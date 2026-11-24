"""Markierung unter «Prüfen»: Bücher, die ein Benutzer (nicht der Administrator) hinzugefügt hat."""
import os

os.environ["ABW_NO_TASKS"] = "1"

from abw import db, library


def test_benutzer_buch_und_serie():
    db.init()
    c = db.conn()
    for t in ("user", "watchlist", "book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(1,'max','x','admin','t'),(2,'bea','x','user','t'),(3,'kurt','x','user','t')")
    c.execute("INSERT INTO series(id, name, norm) VALUES(7,'S','s')")
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm) VALUES(70,7,'en','S','s')")
    ins = "INSERT INTO book(id, edition_id, lang, asin, title, owned, wanted_by) VALUES(?,?,?,?,?,0,?)"
    c.execute(ins, (1, None, "en", "A1", "Einzel von Bea", 2))
    c.execute(ins, (2, None, "en", "A2", "Einzel vom Admin", 1))
    c.execute(ins, (3, 70, "en", "A3", "Band der Serie", None))
    c.execute(ins, (4, None, "de", "A4", "Über Goodreads", None))
    c.execute(ins, (5, None, "de", "A5", "Niemand", None))
    w = "INSERT INTO watchlist(asin, kind, added_at, user_id, series_id) VALUES(?,?,'t',?,?)"
    c.execute(w, ("S7", "series", 3, 7))
    c.execute(w, ("S8", "series", 1, 7))  # Admin zählt nicht
    c.execute(w, ("A4", "book", 2, None))
    try:
        assert library.requested_by([1, 2, 3, 4, 5]) == {1: ["bea"], 3: ["kurt"], 4: ["bea"]}
        assert library.requested_by([]) == {}
    finally:
        for t in ("user", "watchlist", "book", "edition", "series"):
            c.execute(f"DELETE FROM {t}")
