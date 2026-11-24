"""Benachrichtigungen: nur wer eine Serie oder einen Autor markiert hat, bekommt die Meldung, und jede nur einmal."""
from abw import abs as abs_mod
from abw import audible, db, notifications
from datetime import datetime, timezone


def child(asin, seq, date="2024-01-01"):
    return {"asin": asin, "sequence": seq, "title": f"Band {seq}", "subtitle": None, "author": "Autorin A", "narrator": "N",
            "release_date": date, "runtime_min": 500, "cover_url": "http://c/x.jpg", "language": "english", "content_type": "Product", "format_type": "unabridged"}


def setup():
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series", "follow", "notification"):
        c.execute(f"DELETE FROM {t}")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Zyklus Q','zyklusq')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, synced_at) VALUES(?,?,?,?,'2026-01-01')", (sid, "en", "Zyklus Q", "zyklusq")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, abs_item_id, author) VALUES(?,?,?,?,?,?,1,'x','Autorin A')",
              (eid, "en", "A1", "Band 1", "1", 1.0))
    return c, sid, eid


def test_neuer_band_geht_an_markierer_von_serie_und_autor():
    c, sid, eid = setup()
    notifications.follow(1, "series", sid)
    notifications.follow(2, "author", db.norm("Autorin A"))
    ed = c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()
    for _ in range(2):  # der zweite Abgleich meldet nichts noch einmal
        audible._merge(c, ed, "SER", [child("A1", "1"), child("A2", "2", "2200-01-01")])
    for uid in (1, 2):
        got = notifications.listing(uid)
        assert got["total"] == 1 and got["unread"] == 1
        n = got["items"][0]
        assert (n["kind"], n["title"], n["link"], n["cover"]) == ("announced", "Neuer Band angekündigt", f"/serie/{sid}", "http://c/x.jpg")
        assert "Zyklus Q, Band 2: Band 2" in n["body"] and "Termin offen" in n["body"]
    assert notifications.listing(3)["total"] == 0


def test_erster_abgleich_einer_serie_meldet_nichts():
    c, sid, eid = setup()
    notifications.follow(1, "series", sid)
    c.execute("UPDATE edition SET synced_at=NULL WHERE id=?", (eid,))
    audible._merge(c, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone(), "SER", [child("A1", "1"), child("A2", "2")])
    assert notifications.listing(1)["total"] == 0


def test_neu_im_bestand_und_erschienen():
    c, sid, eid = setup()
    notifications.follow(1, "series", sid)
    today = datetime.now(timezone.utc)
    bid = c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, release_date, author) VALUES(?,?,?,?,?,?,0,?,'Autorin A')",
                    (eid, "en", "A2", "Band 2", "2", 2.0, today.date().isoformat())).lastrowid
    assert "1 Meldungen" in notifications.released() and "0 Meldungen" in notifications.released()
    item = {"id": "abs-2", "relPath": "S/Book 2 - Band 2", "addedAt": today.timestamp() * 1000,
            "media": {"metadata": {"title": "Band 2", "asin": "A2", "authorName": "Autorin A", "seriesName": "Zyklus Q #2"}, "duration": 600, "numAudioFiles": 1}}
    abs_mod._upsert_item(c, "en", item, None)
    abs_mod._upsert_item(c, "en", item, None)
    kinds = [n["kind"] for n in notifications.listing(1)["items"]]
    assert kinds == ["available", "released"] and c.execute("SELECT owned FROM book WHERE id=?", (bid,)).fetchone()[0] == 1
    notifications.mark_read(1)
    notifications.clear(1)
    assert notifications.listing(1)["total"] == 0
