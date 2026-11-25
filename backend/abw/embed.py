"""Metadaten in die Dateitags schreiben: Audiobookshelf bettet Titel, Autor, Sprecher, Serie, Beschreibung, Cover und Kapitel
in die Audiodateien ein (Werkzeug «Metadaten einbetten»). So tragen die Dateien selbst die richtigen Angaben, auch für
andere Player und für eine spätere Neu-Einrichtung. Nur für Bücher, die ABW abgelegt und mit Audible gematcht hat.

Ablauf je Buch: Einbetten anstossen («gesendet»), nach einer Wartezeit das Buch in Audiobookshelf neu einlesen lassen und
die Tags der ersten Datei mit Titel und Autor vergleichen («ok»). Passt es nicht, ein zweiter Versuch, dann «Fehler».
Audiobookshelf bettet nacheinander ein; je Lauf gehen deshalb nur wenige Bücher los."""
from datetime import datetime, timedelta, timezone

from . import abs as abs_mod
from . import db

BATCH = 3  # gleichzeitig offene Aufträge
WAIT_MIN = 10  # nach dem Anstossen bis zum Neu-Einlesen
SCAN_WAIT_MIN = 2  # nach dem Neu-Einlesen bis zum Vergleich
SENT, SCANNED, OK, FAIL = "gesendet", "eingelesen", "ok", "Fehler"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def candidates_sql() -> str:
    """Gematcht (abs_match ist ein Zeitpunkt), noch nicht eingebettet."""
    return ("SELECT b.* FROM book b WHERE b.owned=1 AND b.abs_item_id IS NOT NULL AND b.abs_match LIKE '2%'"
            " AND b.embedded IS NULL ORDER BY b.id DESC")


def _norm(s: str | None) -> str:
    return db.norm(s or "")


def tags_match(item: dict) -> tuple[bool, str]:
    """Stimmen die Tags der ersten Audiodatei mit Titel und Autor in Audiobookshelf überein?"""
    media = item.get("media") or {}
    md = media.get("metadata") or {}
    files = media.get("audioFiles") or []
    if not files:
        return False, "Keine Audiodatei"
    tags = files[0].get("metaTags") or {}
    title = md.get("title") or ""
    authors = [a.get("name", "") for a in md.get("authors") or []] or [md.get("authorName") or ""]
    if _norm(tags.get("tagTitle")) != _norm(title) and _norm(tags.get("tagAlbum")) != _norm(title):
        return False, f"Titel in der Datei «{tags.get('tagTitle') or '–'}», erwartet «{title}»"
    artist = _norm((tags.get("tagArtist") or "") + " " + (tags.get("tagAlbumArtist") or ""))
    if authors[0] and _norm(authors[0]) not in artist:
        return False, f"Autor in der Datei «{tags.get('tagArtist') or '–'}», erwartet «{authors[0]}»"
    return True, "Tags passen"


def _send(http, b) -> None:
    r = http.post(f"/api/tools/item/{b['abs_item_id']}/embed-metadata", params={"forceEmbedChapters": 1})
    if r.status_code == 400 and "already in queue" in (getattr(r, "text", "") or ""):
        return  # läuft schon
    r.raise_for_status()


def _state(b) -> tuple[str, int, datetime | None]:
    """(Zustand, Versuch, Zeitpunkt) aus «gesendet 1 2026-…»."""
    parts = (b["embedded"] or "").split(" ")
    try:
        return parts[0], int(parts[1]), datetime.fromisoformat(parts[2])
    except (IndexError, ValueError):
        return parts[0], 0, None


def _set(c, book_id: int, value: str) -> None:
    c.execute("UPDATE book SET embedded=? WHERE id=?", (value[:200], book_id))


def auto(progress=lambda msg: None, now: datetime | None = None) -> str:
    if db.setting("auto_embed") != "1":
        return "Ausgeschaltet"
    c = db.conn()
    now = now or _now()
    checked, sent, failed = 0, 0, 0
    with abs_mod._client() as http:
        for b in c.execute("SELECT * FROM book WHERE embedded LIKE ? OR embedded LIKE ?", (SENT + " %", SCANNED + " %")).fetchall():
            state, tries, at = _state(b)
            item = f"/api/items/{b['abs_item_id']}"
            if state == SENT:  # eingebettet? Audiobookshelf die Datei neu lesen lassen
                if at and now - at < timedelta(minutes=WAIT_MIN):
                    continue
                if http.post(f"{item}/scan").status_code == 404:
                    _set(c, b["id"], f"{FAIL}: Eintrag fehlt in Audiobookshelf")
                    failed += 1
                    continue
                _set(c, b["id"], f"{SCANNED} {tries} {now.isoformat()}")
                continue
            if at and now - at < timedelta(minutes=SCAN_WAIT_MIN):
                continue
            r = http.get(item)
            if r.status_code == 404:
                _set(c, b["id"], f"{FAIL}: Eintrag fehlt in Audiobookshelf")
                failed += 1
                continue
            ok, why = tags_match(r.raise_for_status().json())
            if ok:
                _set(c, b["id"], f"{OK} {now.isoformat()}")
                checked += 1
            elif tries < 2:
                try:
                    _send(http, b)
                    _set(c, b["id"], f"{SENT} {tries + 1} {now.isoformat()}")
                except Exception as exc:  # noqa: BLE001
                    _set(c, b["id"], f"{FAIL}: {exc}")
                    failed += 1
            else:
                _set(c, b["id"], f"{FAIL}: {why}")
                failed += 1
        busy = c.execute("SELECT COUNT(*) FROM book WHERE embedded LIKE ? OR embedded LIKE ?", (SENT + " %", SCANNED + " %")).fetchone()[0]
        for b in c.execute(candidates_sql() + " LIMIT ?", (max(0, BATCH - busy),)).fetchall():
            progress(f"Einbetten: {b['title']}")
            try:
                _send(http, b)
            except Exception as exc:  # noqa: BLE001 - ein Buch scheitert, die anderen laufen weiter
                _set(c, b["id"], f"{FAIL}: {exc}")
                failed += 1
                continue
            _set(c, b["id"], f"{SENT} 1 {now.isoformat()}")
            sent += 1
    left = c.execute("SELECT COUNT(*) FROM (" + candidates_sql() + ")").fetchone()[0]
    return f"{sent} angestossen, {checked} geprüft, {failed} fehlgeschlagen, {left} offen"


def stats() -> dict:
    c = db.conn()
    row = c.execute("SELECT SUM(embedded LIKE 'ok %') ok, SUM(embedded LIKE 'Fehler%') fail, SUM(embedded LIKE 'gesendet %' OR embedded LIKE 'eingelesen %') busy"
                    " FROM book WHERE owned=1").fetchone()
    return {"ok": row["ok"] or 0, "failed": row["fail"] or 0, "busy": row["busy"] or 0,
            "open": c.execute("SELECT COUNT(*) FROM (" + candidates_sql() + ")").fetchone()[0]}
