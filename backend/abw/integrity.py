"""Bestandsprüfung: liegen im Bestand beschädigte Hörbücher?

Unfertige Torrents legen ihre Dateien in voller Grösse an und füllen fehlende Stücke mit Nullen. Laufzeit und Tags stimmen
dann, Audiobookshelf und die Laufzeitprüfung merken nichts, beim Hören fehlen aber Minuten. Erkennbar ist das an langen
Strecken aus Null-Bytes, die in komprimiertem Ton nie vorkommen. Die Prüfung liest jede Datei einmal ganz, ändert nichts
und läuft nur nachts. Ein beschädigtes Buch wird wie ein fehlender Band gesucht; die neue Fassung ersetzt es nach der
Ankunftsprüfung, das alte geht in den Papierkorb.
"""
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, verify

BLOCK = 16 * 1024
ZERO = bytes(BLOCK)
MIN_RUN = 8 * BLOCK  # ab 128 KiB Nullen am Stück gilt eine Datei als beschädigt (kleinstes Torrent-Stück: 256 KiB)
WINDOW = (1, 7)  # nur zwischen 01 und 07 Uhr Ortszeit
BUDGET_S = 9 * 60  # pro Lauf höchstens so lange lesen; die Aufgabe läuft alle 10 Minuten


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def padding(path: Path) -> list[tuple[int, int]]:
    """Bereiche, in denen Nullen zum Format gehören: der ID3v2-Kopf einer MP3 (Platzhalter für spätere Tags) und bei
    MP4/M4B alle Atome ausser den Tondaten «mdat» (`free`/`skip`, Polster in `moov`). Liefert (Anfang, Ende) in Bytes."""
    try:
        with open(path, "rb") as f:
            head = f.read(10)
            if head[:3] == b"ID3" and len(head) == 10:
                s = head[6:10]
                return [(0, 10 + ((s[0] & 127) << 21 | (s[1] & 127) << 14 | (s[2] & 127) << 7 | (s[3] & 127)))]
            if path.suffix.lower() not in (".m4b", ".m4a", ".mp4", ".aac") or head[4:8] != b"ftyp":
                return []
            end, pos, out = path.stat().st_size, 0, []
            while pos + 8 <= end and len(out) < 1000:
                f.seek(pos)
                hd = f.read(16)
                size, kind = int.from_bytes(hd[:4], "big"), hd[4:8]
                if size == 1:
                    size = int.from_bytes(hd[8:16], "big")
                elif size == 0:
                    size = end - pos
                if size < 8:
                    break  # kaputter Kopf: nichts ausnehmen
                if kind != b"mdat":
                    out.append((pos, min(end, pos + size)))
                pos += size
            return out
    except OSError:
        return []


def zero_runs(path: Path) -> dict:
    """Längste Null-Strecke und Summe aller Null-Strecken ab MIN_RUN, in Bytes. Liest die Datei einmal.
    Nullen in Polsterbereichen des Formats (siehe padding) zählen nicht: sie sind kein fehlendes Torrent-Stück."""
    skip = padding(path)
    longest = total = run = 0
    pos = 0  # Anfang des aktuellen Blocks

    def counted(start: int, length: int) -> int:
        return length - sum(max(0, min(start + length, b) - max(start, a)) for a, b in skip)

    def close(end: int) -> None:
        nonlocal longest, total, run
        if run >= MIN_RUN:
            n = counted(end - run, run) if skip else run
            if n >= MIN_RUN:
                total += n
                longest = max(longest, n)
        run = 0

    with open(path, "rb", buffering=0) as f:
        while chunk := f.read(64 * BLOCK):
            for i in range(0, len(chunk), BLOCK):
                part = chunk[i:i + BLOCK]
                if len(part) == BLOCK and part == ZERO:
                    run += BLOCK
                else:
                    close(pos + i)
            pos += len(chunk)
    close(pos)
    return {"longest": longest, "total": total}


def signature(files: list[Path]) -> str:
    """Ändert sich, sobald eine Datei ersetzt, gekürzt oder hinzugefügt wird: dann wird neu geprüft."""
    st = [f.stat() for f in files]
    return f"{len(files)}:{sum(s.st_size for s in st)}:{max((s.st_mtime_ns for s in st), default=0)}"


def check_folder(folder: Path) -> dict:
    """Ergebnis für einen Buchordner: state ok/damaged/missing, Text und die betroffenen Dateien."""
    if not folder.is_dir():
        return {"state": "missing", "detail": "Ordner nicht gefunden", "sig": ""}
    files = verify.audio_files(folder)
    if not files:
        return {"state": "missing", "detail": "Keine Audiodateien im Ordner", "sig": ""}
    sig = signature(files)
    bad, zeros, size = [], 0, 0
    for f in files:
        z = zero_runs(f)
        size += f.stat().st_size
        if z["longest"] >= MIN_RUN:
            bad.append(f.name)
            zeros += z["total"]
    if not bad:
        return {"state": "ok", "detail": f"{len(files)} Dateien ohne Lücken", "sig": sig}
    share = zeros / size * 100 if size else 0
    names = ", ".join(bad[:3]) + (f" und {len(bad) - 3} weitere" if len(bad) > 3 else "")
    return {"state": "damaged", "sig": sig,
            "detail": f"{len(bad)} von {len(files)} Dateien mit Lücken aus Null-Bytes ({zeros / 1e6:.1f} MB, {share:.1f} %): {names}"}


def folder_of(book) -> Path:
    return config.LIBRARY_ROOT[book["lang"]] / book["rel_path"]


def check_book(book_id: int) -> dict:
    """Ein Buch sofort prüfen und das Ergebnis speichern."""
    c = db.conn()
    b = c.execute("SELECT id, lang, rel_path, title FROM book WHERE id=? AND owned=1 AND rel_path IS NOT NULL", (book_id,)).fetchone()
    if not b:
        raise LookupError("Buch nicht im Bestand")
    res = check_folder(folder_of(b))
    c.execute("INSERT INTO integrity(book_id, checked_at, sig, state, detail) VALUES(?,?,?,?,?)"
              " ON CONFLICT(book_id) DO UPDATE SET checked_at=excluded.checked_at, sig=excluded.sig, state=excluded.state, detail=excluded.detail",
              (book_id, _now(), res["sig"], res["state"], res["detail"]))
    if res["state"] == "damaged":
        # sofort suchen, nicht erst nach der Wartezeit eines früheren Suchlaufs
        c.execute("UPDATE book_search SET next_search=NULL WHERE book_id=?", (book_id,))
        from . import notify
        notify.send("Beschädigt im Bestand", f"{b['title']}: {res['detail']}"[:200], "/aufgaben", "warning")
    return res


def damaged_ids() -> set[int]:
    return {r["book_id"] for r in db.conn().execute("SELECT book_id FROM integrity WHERE state='damaged'")}


def is_damaged(book_id: int) -> bool:
    return bool(db.conn().execute("SELECT 1 FROM integrity WHERE book_id=? AND state='damaged'", (book_id,)).fetchone())


def forget(book_id: int) -> None:
    """Nach dem Ersetzen: das Buch wird bei der nächsten Nachtprüfung neu gelesen."""
    db.conn().execute("DELETE FROM integrity WHERE book_id=?", (book_id,))


# Noch nie geprüft zuerst, zuletzt hinzugefügte vorn (dort sind Torrent-Teilimporte am wahrscheinlichsten)
PENDING = ("SELECT b.id, b.lang, b.rel_path, b.title, i.sig FROM book b LEFT JOIN integrity i ON i.book_id=b.id"
           " WHERE b.owned=1 AND b.rel_path IS NOT NULL ORDER BY i.checked_at IS NOT NULL, i.checked_at, b.added_at DESC")


def in_window(now: datetime | None = None) -> bool:
    h = (now or datetime.now()).hour
    return WINDOW[0] <= h < WINDOW[1]


def auto(progress=lambda msg: None) -> str:
    c = db.conn()
    if db.setting("auto_integrity") != "1":
        return "Bestandsprüfung ist ausgeschaltet"
    if not in_window():
        return f"Wartet auf das Nachtfenster ({WINDOW[0]:02d}–{WINDOW[1]:02d} Uhr)"
    start, checked, found = time.monotonic(), 0, []
    for b in c.execute(PENDING).fetchall():
        if time.monotonic() - start > BUDGET_S or not in_window():
            break
        folder = folder_of(b)
        try:
            files = verify.audio_files(folder) if folder.is_dir() else []
            if b["sig"] and files and signature(files) == b["sig"]:
                # unverändert seit der letzten Prüfung: nur den Zeitpunkt nachführen
                c.execute("UPDATE integrity SET checked_at=? WHERE book_id=?", (_now(), b["id"]))
                continue
        except OSError:
            pass
        progress(f"Liest «{b['title']}»")
        try:
            res = check_book(b["id"])
        except Exception as exc:
            c.execute("INSERT OR REPLACE INTO integrity(book_id, checked_at, sig, state, detail) VALUES(?,?,?,?,?)",
                      (b["id"], _now(), "", "error", str(exc)[:300]))
            continue
        checked += 1
        if res["state"] == "damaged":
            found.append(b["title"])
    s = stats()
    text = f"{checked} Bücher gelesen ({s['checked']} von {s['total']} geprüft)"
    if found:
        text += f", beschädigt: {', '.join(found)[:300]}"
    return text


def stats() -> dict:
    c = db.conn()
    total = c.execute("SELECT COUNT(*) FROM book WHERE owned=1 AND rel_path IS NOT NULL").fetchone()[0]
    rows = {r["state"]: r["n"] for r in c.execute(
        "SELECT i.state, COUNT(*) n FROM integrity i JOIN book b ON b.id=i.book_id WHERE b.owned=1 GROUP BY i.state")}
    damaged = [dict(r) for r in c.execute(
        "SELECT b.id book_id, b.title, b.lang, e.name series, e.series_id, i.detail, i.checked_at,"
        " (SELECT state FROM download d WHERE d.book_id=b.id ORDER BY d.id DESC LIMIT 1) download_state"
        " FROM integrity i JOIN book b ON b.id=i.book_id LEFT JOIN edition e ON e.id=b.edition_id"
        " WHERE i.state='damaged' AND b.owned=1 ORDER BY b.title")]
    return {"total": total, "checked": sum(rows.values()), "ok": rows.get("ok", 0), "errors": rows.get("error", 0) + rows.get("missing", 0),
            "damaged": damaged}
