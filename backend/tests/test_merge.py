"""Regression: Ein zweiter Audible-Abgleich darf fehlende Bände nicht wieder entfernen."""
from abw import audible, db


def child(asin, seq, date="2024-01-01", runtime=500):
    return {"asin": asin, "sequence": seq, "title": f"Band {seq}", "subtitle": None, "author": "A", "narrator": "N",
            "release_date": date, "runtime_min": runtime, "cover_url": None, "language": "english",
            "content_type": "Product", "format_type": "unabridged"}


def test_second_sync_keeps_missing_books_and_prefers_released_edition():
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('S','s')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", "S", "s")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, abs_item_id) VALUES(?,?,?,?,?,?,1,'x')",
              (eid, "en", "A1", "Band 1", "1", 1.0))
    ed = c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()
    children = [child("A1", "1"), child("A2", "2"), child("A2P", "2", "2200-01-01", None), child("A3", "3", "2200-01-01", None)]
    for _ in range(3):
        audible._merge(c, ed, "SER", children)
        rows = {r["seq"]: r["asin"] for r in c.execute("SELECT seq, asin FROM book WHERE edition_id=? AND owned=0", (eid,))}
        assert rows == {2.0: "A2", 3.0: "A3"}
