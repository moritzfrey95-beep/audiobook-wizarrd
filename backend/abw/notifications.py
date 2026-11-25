"""Benachrichtigungen in der Oberfläche. Jeder Benutzer markiert Serien und Autoren (Glocke) und bekommt Meldungen zu
neuen Bänden, erschienenen Hörbüchern, neuen Büchern im Bestand, neuen Serien eines Autors und abgelegten E-Books.
Was jemand selbst hinzufügt, ist automatisch markiert. Jeder Benutzer wählt, welche Meldungsarten er bekommt, kann einzelne
Serien stummschalten (sie bleiben markiert) und Push als Wochen-Zusammenfassung statt einzeln bekommen."""
from datetime import date, datetime, timedelta, timezone

from . import db, push

KEEP_DAYS = 180
KINDS = ("series", "author", "narrator")
# Meldungsarten in der Reihenfolge der Einstellungen. Jede lässt sich je Benutzer abschalten.
NOTE_KINDS = {
    "announced": "Neuer Band angekündigt",
    "new_book": "Neues Hörbuch bei Audible erschienen",
    "released": "Erscheinungstag erreicht",
    "available": "Neu im Bestand",
    "new_series": "Neue Serie eines Autors",
    "narrator": "Neues Hörbuch eines Sprechers",
    "epub": "E-Book abgelegt",
    "recommend": "Empfehlungen anderer Benutzer",
    "rate": "Erinnerung ans Bewerten",
}
DIGEST_DAYS = 7


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def follow(user_id: int, kind: str, key, on: bool = True) -> None:
    if kind not in KINDS:
        raise ValueError("Unbekannte Art")
    c = db.conn()
    if kind == "narrator":
        key = " ".join(str(key).split())  # Sprecher: der Name selbst, wie bei Audible geschrieben
        if len(key) < 3:
            raise ValueError("Bitte den Namen des Sprechers angeben")
    if on:
        c.execute("INSERT OR IGNORE INTO follow(user_id, kind, key, created) VALUES(?,?,?,?)", (user_id, kind, str(key), _now()))
    else:
        c.execute("DELETE FROM follow WHERE user_id=? AND kind=? AND key=?", (user_id, kind, str(key)))


def follows(user_id: int) -> dict:
    out = {"series": [], "authors": [], "narrators": []}
    for r in db.conn().execute("SELECT kind, key FROM follow WHERE user_id=?", (user_id,)):
        if r["kind"] == "series" and r["key"].isdigit():
            out["series"].append(int(r["key"]))
        elif r["kind"] == "author":
            out["authors"].append(r["key"])
        elif r["kind"] == "narrator":
            out["narrators"].append(r["key"])
    return out


def recipients(series_id: int | None = None, author_key: str | None = None, extra=()) -> set[int]:
    """Wer die Serie oder den Autor markiert hat. Wer die Serie stummgeschaltet hat, fällt weg, auch wenn er den Autor markiert hat."""
    c = db.conn()
    out = {int(u) for u in extra if u}
    if series_id:
        out |= {r["user_id"] for r in c.execute("SELECT user_id FROM follow WHERE kind='series' AND key=?", (str(series_id),))}
    if author_key:
        out |= {r["user_id"] for r in c.execute("SELECT user_id FROM follow WHERE kind='author' AND key=?", (author_key,))}
    if series_id:
        out -= {r["user_id"] for r in c.execute("SELECT user_id FROM follow_mute WHERE kind='series' AND key=?", (str(series_id),))}
    return out


def mute(user_id: int, series_id: int, on: bool = True) -> None:
    c = db.conn()
    if on:
        c.execute("INSERT OR IGNORE INTO follow_mute(user_id, kind, key, created) VALUES(?,?,?,?)", (user_id, "series", str(series_id), _now()))
    else:
        c.execute("DELETE FROM follow_mute WHERE user_id=? AND kind='series' AND key=?", (user_id, str(series_id)))


def muted(user_id: int) -> list[int]:
    return [int(r["key"]) for r in db.conn().execute("SELECT key FROM follow_mute WHERE user_id=? AND kind='series'", (user_id,)) if r["key"].isdigit()]


def prefs(user_id: int) -> dict:
    """Welche Meldungsarten der Benutzer bekommt und ob Push als Wochen-Zusammenfassung kommt."""
    rows = {r["key"]: r["value"] for r in db.conn().execute("SELECT key, value FROM notify_pref WHERE user_id=?", (user_id,))}
    return {"kinds": [{"kind": k, "label": label, "on": rows.get("off:" + k) != "1"} for k, label in NOTE_KINDS.items()],
            "digest": rows.get("digest") == "weekly", "muted": muted(user_id)}


def set_prefs(user_id: int, kinds: dict[str, bool] | None = None, digest: bool | None = None) -> None:
    c = db.conn()
    for k, on in (kinds or {}).items():
        if k not in NOTE_KINDS:
            raise ValueError("Unbekannte Meldungsart")
        if on:
            c.execute("DELETE FROM notify_pref WHERE user_id=? AND key=?", (user_id, "off:" + k))
        else:
            c.execute("INSERT OR REPLACE INTO notify_pref(user_id, key, value) VALUES(?,?,'1')", (user_id, "off:" + k))
    if digest is not None:
        if digest:
            c.execute("INSERT OR REPLACE INTO notify_pref(user_id, key, value) VALUES(?,?,?)", (user_id, "digest", "weekly"))
            # Zählt ab jetzt: die erste Zusammenfassung kommt nach einer Woche, nicht sofort mit allem Alten
            c.execute("INSERT OR IGNORE INTO notify_pref(user_id, key, value) VALUES(?,?,?)", (user_id, "digest_at", _now()))
        else:
            c.execute("DELETE FROM notify_pref WHERE user_id=? AND key IN ('digest', 'digest_at')", (user_id,))


def _wants(user_id: int, kind: str) -> tuple[bool, bool]:
    """(Meldung überhaupt, Push sofort) für diesen Benutzer und diese Art."""
    rows = {r["key"]: r["value"] for r in db.conn().execute(
        "SELECT key, value FROM notify_pref WHERE user_id=? AND key IN (?, 'digest')", (user_id, "off:" + kind))}
    return rows.get("off:" + kind) != "1", rows.get("digest") != "weekly"


def emit(kind: str, title: str, body: str, link: str, dedupe: str, cover: str | None = None,
         series_id: int | None = None, author_key: str | None = None, users=()) -> int:
    """Meldung an alle, die die Serie oder den Autor markiert haben (und an die ausdrücklich Genannten).
    Dieselbe Meldung (dedupe) bekommt ein Benutzer nur einmal."""
    c = db.conn()
    n = 0
    for uid in recipients(series_id, author_key, users):
        wanted, push_now = _wants(uid, kind)
        if not wanted:
            continue
        if c.execute("INSERT OR IGNORE INTO notification(user_id, kind, title, body, link, cover, dedupe, created) VALUES(?,?,?,?,?,?,?,?)",
                     (uid, kind, title, body, link, cover, dedupe, _now())).rowcount:
            n += 1
            if push_now:
                push.enqueue(uid, title, body, link, dedupe)
    return n


def book_event(kind: str, title: str, book, dedupe: str | None = None, extra: str = "") -> int:
    """Meldung zu einem Buch: geht an die Markierer seiner Serie und seines Autors. Ein Einzelbuch ohne Serie meldet sich
    zusätzlich bei dem, der es hinzugefügt hat; bei Serienbüchern gilt nur die Markierung, damit Abstellen auch wirkt."""
    from . import abs as abs_mod
    c = db.conn()
    b = dict(book)
    ed = c.execute("SELECT name, series_id FROM edition WHERE id=?", (b.get("edition_id"),)).fetchone() if b.get("edition_id") else None
    author = abs_mod.primary_author(b.get("author"))
    if ed and b.get("sequence"):
        text = f"{ed['name']}, Band {b['sequence']}: {b['title']}"
    elif ed:
        text = f"{ed['name']}: {b['title']}"
    else:
        text = f"{b['title']} ({author})"
    cover = f"/api/cover/{b['abs_item_id']}" if b.get("abs_item_id") else b.get("cover_url")
    link = f"/serie/{ed['series_id']}" if ed else f"/autor/{db.norm(author)}"
    return emit(kind, title, text + (f" ({extra})" if extra else ""), link, dedupe or f"{kind}:{b['id']}", cover,
                series_id=ed["series_id"] if ed else None, author_key=db.norm(author), users=() if ed else (b.get("wanted_by"),))


def released(progress=lambda msg: None) -> str:
    """Hörbücher, deren Erscheinungstermin in den letzten Tagen erreicht wurde und die noch nicht im Bestand liegen.
    Ausserdem räumt der Lauf alte Meldungen weg."""
    c = db.conn()
    today = date.today()
    n = 0
    if c.execute("SELECT 1 FROM follow LIMIT 1").fetchone() or c.execute("SELECT 1 FROM book WHERE wanted_by IS NOT NULL LIMIT 1").fetchone():
        for b in c.execute("SELECT * FROM book WHERE owned=0 AND release_date<=? AND release_date>=?",
                           (today.isoformat(), (today - timedelta(days=5)).isoformat())).fetchall():
            n += book_event("released", "Hörbuch erschienen", b, extra="wird jetzt gesucht")
    c.execute("DELETE FROM notification WHERE created < ?", ((datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).isoformat(),))
    d = digest()
    return f"{n} Meldungen zu erschienenen Hörbüchern" + (f", {d} Wochen-Zusammenfassungen" if d else "")


def digest(now: datetime | None = None) -> int:
    """Wochen-Zusammenfassung per Push für alle, die sie gewählt haben: eine Meldung mit den Meldungen seit der letzten.
    Liefert die Zahl der Benutzer, die eine bekommen haben. Ohne neue Meldungen kommt nichts."""
    c = db.conn()
    now = now or datetime.now(timezone.utc)
    sent = 0
    for r in c.execute("SELECT p.user_id, a.value AS at FROM notify_pref p LEFT JOIN notify_pref a ON a.user_id=p.user_id AND a.key='digest_at'"
                       " WHERE p.key='digest' AND p.value='weekly'").fetchall():
        since = datetime.fromisoformat(r["at"]) if r["at"] else now - timedelta(days=DIGEST_DAYS)
        if now - since < timedelta(days=DIGEST_DAYS):
            continue
        notes = c.execute("SELECT kind, title, body FROM notification WHERE user_id=? AND created>? ORDER BY id DESC",
                          (r["user_id"], since.isoformat())).fetchall()
        c.execute("INSERT OR REPLACE INTO notify_pref(user_id, key, value) VALUES(?,?,?)", (r["user_id"], "digest_at", now.isoformat()))
        if not notes:
            continue
        counts: dict[str, int] = {}
        for x in notes:
            counts[x["title"]] = counts.get(x["title"], 0) + 1
        summary = ", ".join(f"{k} ({v})" if v > 1 else k for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        title = "Deine Woche: 1 Meldung" if len(notes) == 1 else f"Deine Woche: {len(notes)} Meldungen"
        push.enqueue(r["user_id"], title, summary[:300], "/benachrichtigungen", "abw-digest")
        sent += 1
    return sent


def listing(user_id: int, limit: int = 50, offset: int = 0, unread_only: bool = False) -> dict:
    c = db.conn()
    where = "user_id=?" + (" AND read_at IS NULL" if unread_only else "")
    items = [dict(r) for r in c.execute(f"SELECT id, kind, title, body, link, cover, created, read_at FROM notification WHERE {where}"
                                        " ORDER BY id DESC LIMIT ? OFFSET ?", (user_id, limit, offset))]
    total = c.execute(f"SELECT COUNT(*) FROM notification WHERE {where}", (user_id,)).fetchone()[0]
    unread = c.execute("SELECT COUNT(*) FROM notification WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()[0]
    return {"items": items, "total": total, "unread": unread}


def mark_read(user_id: int, ids: list[int] | None = None) -> None:
    c = db.conn()
    if ids is None:
        c.execute("UPDATE notification SET read_at=? WHERE user_id=? AND read_at IS NULL", (_now(), user_id))
    else:
        for i in ids:
            c.execute("UPDATE notification SET read_at=? WHERE user_id=? AND id=? AND read_at IS NULL", (_now(), user_id, i))


def clear(user_id: int) -> None:
    db.conn().execute("DELETE FROM notification WHERE user_id=? AND read_at IS NOT NULL", (user_id,))
