"""Bewertungen gehörter Bücher, 1 bis 5 Sterne.

Bewerten lässt sich jedes Buch, das der Benutzer fertig gehört hat. «Nicht bewerten» speichert eine Zeile ohne Sterne,
damit das Buch nicht mehr unter «Offen» erscheint. Die Sterne fliessen in «Für dich» ein (siehe `foryou`).
"""
from collections import defaultdict
from datetime import datetime, timezone

from . import audible, db, tags

BOOK_COLS = ("b.id, b.asin, b.lang, b.title, b.author, b.narrator, b.sequence, b.seq, b.cover_url, b.abs_item_id,"
             " b.edition_id, e.series_id, e.name series")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _book(r) -> dict:
    d = dict(r)
    d["cover"] = f"/api/cover/{d['abs_item_id']}" if d.get("abs_item_id") else d.get("cover_url")
    return d


def stars_of(user_id: int) -> dict[int, int]:
    """Sterne je Buch (nur bewertete, ohne «nicht bewerten»)."""
    return {r["book_id"]: r["stars"] for r in db.conn().execute(
        "SELECT book_id, stars FROM rating WHERE user_id=? AND stars IS NOT NULL", (user_id,))}


def open_count(user_id: int) -> int:
    return db.conn().execute(
        "SELECT COUNT(*) FROM user_progress p WHERE p.user_id=? AND p.finished=1"
        " AND NOT EXISTS (SELECT 1 FROM rating r WHERE r.user_id=p.user_id AND r.book_id=p.book_id)", (user_id,)).fetchone()[0]


def open_list(user_id: int) -> list[dict]:
    """Fertig gehörte, noch nicht bewertete Bücher, nach Serie gruppiert; zuletzt gehörte Serie zuerst."""
    rows = db.conn().execute(
        f"SELECT {BOOK_COLS}, p.updated finished_at FROM user_progress p JOIN book b ON b.id=p.book_id"
        " LEFT JOIN edition e ON e.id=b.edition_id WHERE p.user_id=? AND p.finished=1"
        " AND NOT EXISTS (SELECT 1 FROM rating r WHERE r.user_id=p.user_id AND r.book_id=p.book_id)"
        " ORDER BY p.updated DESC", (user_id,)).fetchall()
    groups: dict = {}
    for r in rows:
        key = f"e{r['edition_id']}" if r["edition_id"] else f"b{r['id']}"
        g = groups.setdefault(key, {"key": key, "edition_id": r["edition_id"], "series_id": r["series_id"], "series": r["series"],
                                    "last": r["finished_at"], "books": []})
        g["books"].append(_book(r))
    for g in groups.values():
        g["books"].sort(key=lambda b: (b["seq"] is None, b["seq"] or 0, b["title"]))
    return list(groups.values())


def mine(user_id: int) -> list[dict]:
    """Alle Bewertungen des Benutzers, auch «nicht bewerten» (stars = None)."""
    rows = db.conn().execute(
        f"SELECT {BOOK_COLS}, r.stars, r.updated rated_at FROM rating r JOIN book b ON b.id=r.book_id"
        " LEFT JOIN edition e ON e.id=b.edition_id WHERE r.user_id=? ORDER BY r.stars IS NULL, r.stars DESC, r.updated DESC",
        (user_id,)).fetchall()
    return [_book(r) for r in rows]


def _check(stars: int | None) -> None:
    if stars is not None and not 1 <= stars <= 5:
        raise ValueError("Sterne müssen zwischen 1 und 5 liegen")


def set_rating(user_id: int, book_id: int, stars: int | None) -> None:
    """Bewerten oder mit stars=None als «nicht bewerten» markieren. Nur fertig gehörte Bücher."""
    _check(stars)
    c = db.conn()
    if not c.execute("SELECT 1 FROM user_progress WHERE user_id=? AND book_id=? AND finished=1", (user_id, book_id)).fetchone():
        raise LookupError("Dieses Buch hast du nicht fertig gehört")
    c.execute("INSERT OR REPLACE INTO rating(user_id, book_id, stars, updated) VALUES(?,?,?,?)", (user_id, book_id, stars, _now()))


def set_series(user_id: int, edition_id: int, stars: int | None) -> int:
    """Alle offenen, fertig gehörten Bände einer Fassung gleich bewerten. Liefert die Anzahl."""
    _check(stars)
    c = db.conn()
    ids = [r[0] for r in c.execute(
        "SELECT b.id FROM user_progress p JOIN book b ON b.id=p.book_id WHERE p.user_id=? AND p.finished=1 AND b.edition_id=?"
        " AND NOT EXISTS (SELECT 1 FROM rating r WHERE r.user_id=p.user_id AND r.book_id=p.book_id)", (user_id, edition_id))]
    now = _now()
    c.executemany("INSERT OR REPLACE INTO rating(user_id, book_id, stars, updated) VALUES(?,?,?,?)", [(user_id, i, stars, now) for i in ids])
    return len(ids)


def clear(user_id: int, book_id: int) -> None:
    """Bewertung zurücknehmen: das Buch erscheint wieder unter «Offen»."""
    db.conn().execute("DELETE FROM rating WHERE user_id=? AND book_id=?", (user_id, book_id))


def top_by_tag(user_id: int) -> list[dict]:
    """Meine Topliste je Tag: bewertete Bücher je Tag, beste zuerst. Tags aus Audible (Titel, Kategorie, Beschreibung)."""
    books = [b for b in mine(user_id) if b["stars"]]
    by_lang = defaultdict(list)
    for b in books:
        if b["asin"] and audible.ASIN_OK.match(b["asin"]):
            by_lang[b["lang"]].append(b["asin"])
    found: dict[tuple[str, str], set[str]] = {}
    for lang, asins in by_lang.items():
        for a, t in tags.of_books(lang, asins).items():
            found[(lang, a)] = t
    out: dict[str, list[dict]] = defaultdict(list)
    for b in books:
        for t in found.get((b["lang"], b["asin"]), set()):
            out[t].append(b)
    res = []
    for t, lst in out.items():
        lst.sort(key=lambda b: (-b["stars"], b["rated_at"]))
        res.append({"tag": t, "generic": t in tags.GENERIC, "count": len(lst),
                    "average": round(sum(b["stars"] for b in lst) / len(lst), 2), "books": lst})
    # Unterarten vor den allgemeinen Tags, dann nach Anzahl
    res.sort(key=lambda r: (r["generic"], -r["count"], -r["average"]))
    untagged = [b for b in books if not found.get((b["lang"], b["asin"]))]
    if untagged:
        res.append({"tag": "Ohne Tag", "generic": True, "count": len(untagged),
                    "average": round(sum(b["stars"] for b in untagged) / len(untagged), 2), "books": untagged})
    return res


def by_book(user_id: int | None) -> dict[int, dict]:
    """Je Buch: meine Sterne (oder «nicht bewerten») und die Sterne der anderen Benutzer, für die Serienansicht."""
    out: dict[int, dict] = {}
    for r in db.conn().execute("SELECT r.book_id, r.user_id, r.stars, u.name FROM rating r JOIN user u ON u.id=r.user_id ORDER BY u.name"):
        d = out.setdefault(r["book_id"], {"mine": None, "skipped": False, "others": []})
        if r["user_id"] == user_id:
            d["mine"], d["skipped"] = r["stars"], r["stars"] is None
        elif r["stars"]:
            d["others"].append({"name": r["name"], "stars": r["stars"]})
    return out


def summary(stars: list[int], mine: list[int]) -> dict | None:
    """Schnitt aller Bewertungen und meiner, für die Serienkarte."""
    if not stars:
        return None
    return {"avg": round(sum(stars) / len(stars), 1), "count": len(stars), "mine": round(sum(mine) / len(mine), 1) if mine else None}


def stats(user_id: int, everyone: bool = False) -> dict:
    """Statistik: Verteilung der Sterne, beste Autoren, Sprecher und Serien (mindestens zwei bewertete Bücher)."""
    from . import abs as abs_mod
    c = db.conn()
    where, args = ("", ()) if everyone else (" AND r.user_id=?", (user_id,))
    rows = c.execute(
        "SELECT r.stars, b.author, b.narrator, e.series_id, e.name series FROM rating r JOIN book b ON b.id=r.book_id"
        f" LEFT JOIN edition e ON e.id=b.edition_id WHERE 1=1{where}", args).fetchall()
    rated = [r for r in rows if r["stars"]]
    dist = {n: sum(1 for r in rated if r["stars"] == n) for n in range(1, 6)}

    def best(key_of, min_n: int = 2, limit: int = 10) -> list[dict]:
        acc: dict[str, list] = defaultdict(list)
        extra: dict[str, int | None] = {}
        for r in rated:
            for k, x in key_of(r):
                acc[k].append(r["stars"])
                extra[k] = x
        res = [{"name": k, "avg": round(sum(v) / len(v), 2), "count": len(v), "id": extra[k]} for k, v in acc.items() if len(v) >= min_n]
        res.sort(key=lambda d: (-d["avg"], -d["count"]))
        return res[:limit]

    split = lambda t: [n.strip() for n in (t or "").replace(";", ",").split(",") if n.strip()]  # noqa: E731
    return {
        "rated": len(rated), "skipped": len(rows) - len(rated), "open": open_count(user_id) if not everyone else None,
        "average": round(sum(r["stars"] for r in rated) / len(rated), 2) if rated else None, "distribution": dist,
        "authors": best(lambda r: [(abs_mod.primary_author(r["author"]), None)] if r["author"] else []),
        "narrators": best(lambda r: [(n, None) for n in split(r["narrator"])]),
        "series": best(lambda r: [(r["series"], r["series_id"])] if r["series_id"] else []),
    }


def remind_finished(c, user_id: int, before: set[int]) -> int:
    """Nach dem Abgleich: Meldung «Wie war …?» für Bücher, die der Benutzer seit dem letzten Abgleich fertig gehört hat
   . Nicht beim ersten Abgleich eines Kontos und nur, wenn das Fertighören höchstens zwei Tage her ist."""
    from datetime import timedelta
    from . import notifications
    if not before:
        return 0
    since = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    rows = c.execute(
        f"SELECT {BOOK_COLS} FROM user_progress p JOIN book b ON b.id=p.book_id LEFT JOIN edition e ON e.id=b.edition_id"
        " WHERE p.user_id=? AND p.finished=1 AND p.updated>=?"
        " AND NOT EXISTS (SELECT 1 FROM rating r WHERE r.user_id=p.user_id AND r.book_id=p.book_id)", (user_id, since)).fetchall()
    n = 0
    for r in rows:
        if r["id"] in before:
            continue
        name = f"{r['series']}, Band {r['sequence']}: {r['title']}" if r["series"] and r["sequence"] else r["title"]
        n += notifications.emit("rate", "Wie war es?", f"{name}. Jetzt mit Sternen bewerten.", f"/bewertungen?buch={r['id']}",
                                f"rate:{r['id']}", _book(r)["cover"], users=(user_id,))
    return n
