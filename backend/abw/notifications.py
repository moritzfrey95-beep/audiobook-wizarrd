"""Benachrichtigungen in der Oberfläche. Jeder Benutzer markiert Serien und Autoren (Glocke) und bekommt Meldungen zu
neuen Bänden, erschienenen Hörbüchern, neuen Büchern im Bestand, neuen Serien eines Autors und abgelegten E-Books.
Was jemand selbst hinzufügt, ist automatisch markiert."""
from datetime import date, datetime, timedelta, timezone

from . import db, push

KEEP_DAYS = 180
KINDS = ("series", "author")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def follow(user_id: int, kind: str, key, on: bool = True) -> None:
    if kind not in KINDS:
        raise ValueError("Unbekannte Art")
    c = db.conn()
    if on:
        c.execute("INSERT OR IGNORE INTO follow(user_id, kind, key, created) VALUES(?,?,?,?)", (user_id, kind, str(key), _now()))
    else:
        c.execute("DELETE FROM follow WHERE user_id=? AND kind=? AND key=?", (user_id, kind, str(key)))


def follows(user_id: int) -> dict:
    out = {"series": [], "authors": []}
    for r in db.conn().execute("SELECT kind, key FROM follow WHERE user_id=?", (user_id,)):
        if r["kind"] == "series" and r["key"].isdigit():
            out["series"].append(int(r["key"]))
        elif r["kind"] == "author":
            out["authors"].append(r["key"])
    return out


def recipients(series_id: int | None = None, author_key: str | None = None, extra=()) -> set[int]:
    c = db.conn()
    out = {int(u) for u in extra if u}
    if series_id:
        out |= {r["user_id"] for r in c.execute("SELECT user_id FROM follow WHERE kind='series' AND key=?", (str(series_id),))}
    if author_key:
        out |= {r["user_id"] for r in c.execute("SELECT user_id FROM follow WHERE kind='author' AND key=?", (author_key,))}
    return out


def emit(kind: str, title: str, body: str, link: str, dedupe: str, cover: str | None = None,
         series_id: int | None = None, author_key: str | None = None, users=()) -> int:
    """Meldung an alle, die die Serie oder den Autor markiert haben (und an die ausdrücklich Genannten).
    Dieselbe Meldung (dedupe) bekommt ein Benutzer nur einmal."""
    c = db.conn()
    n = 0
    for uid in recipients(series_id, author_key, users):
        if c.execute("INSERT OR IGNORE INTO notification(user_id, kind, title, body, link, cover, dedupe, created) VALUES(?,?,?,?,?,?,?,?)",
                     (uid, kind, title, body, link, cover, dedupe, _now())).rowcount:
            n += 1
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
    return f"{n} Meldungen zu erschienenen Hörbüchern"


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
