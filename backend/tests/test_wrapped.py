"""Jahresrückblick: Kennzahlen aus dem Hörverlauf, Sprecher, abgeschlossene Serien."""
from datetime import date

from abw import db, wrapped


def test_rueckblick():
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Rueckblick Serie', 'rueckblickserie')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?, 'en', 'Rueckblick Serie', 'rueckblickserieen')", (sid,)).lastrowid
    for i in (1, 2):
        c.execute("INSERT INTO book(edition_id, lang, title, author, narrator, sequence, owned, abs_item_id, runtime_min) VALUES(?, 'en', ?, 'Autor R', 'Sprecher S', ?, 1, ?, 600)",
                  (eid, f"Rueckblick {i}", str(i), f"rb-item-{i}"))
        c.execute("INSERT INTO listen_session(id, abs_user_id, username, item_id, day, started, updated, seconds) VALUES(?,?,?,?,?,?,?,?)",
                  (f"rb-s{i}", "rb-user", "Rb", f"rb-item-{i}", f"2025-0{i}-10", f"2025-0{i}-10T20:00:00+00:00", "x", 7200 * i))
        c.execute("INSERT INTO listen_progress(abs_user_id, item_id, progress, finished, finished_at) VALUES('rb-user', ?, 1, 1, ?)",
                  (f"rb-item-{i}", f"2025-0{i}-12T20:00:00+00:00"))
    try:
        assert wrapped.years("rb-user") == [2025]
        w = wrapped.build(2025, "rb-user", today=date(2026, 1, 5))
        assert w["hours"] == 6.0 and w["finished"] == 2 and w["complete"]
        assert w["top_narrators"][0]["name"] == "Sprecher S" and w["top_books"][0]["title"] == "Rueckblick 2"
        assert w["series_done"][0]["name"] == "Rueckblick Serie" and w["fastest"]["days"] == 3
        # Meistgehört nach Serien: beide Bände zusammen
        assert w["top_series"][0] == {"name": "Rueckblick Serie", "author": "Autor R", "hours": 6.0, "books": 2, "series": True, "item_id": "rb-item-2"}
    finally:
        c.execute("DELETE FROM listen_session WHERE abs_user_id='rb-user'")
        c.execute("DELETE FROM listen_progress WHERE abs_user_id='rb-user'")
        c.execute("DELETE FROM book WHERE edition_id=?", (eid,))
