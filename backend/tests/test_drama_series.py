"""Hörspielserien (bei Audible überwiegend «Performance», z. B. Die drei ???): ihre Folgen zählen als fehlende Bände,
und die Suche zieht für «Hörspiel» keine Punkte ab. Bei Hörbuch-Serien bleibt ein Hörspiel ausgeschlossen."""
from abw import audible, db
from abw.scoring import score


def child(asin, seq, kind):
    return {"asin": asin, "sequence": seq, "title": f"Folge {seq}", "subtitle": None, "author": "A", "narrator": "N",
            "release_date": "2024-01-01", "runtime_min": 60, "cover_url": None, "language": "german",
            "content_type": kind, "format_type": "unabridged"}


def edition(c, norm):
    sid = c.execute("INSERT INTO series(name, norm) VALUES(?,?)", (norm, norm)).lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "de", norm, norm)).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, abs_item_id) VALUES(?,?,?,?,?,?,1,?)",
              (eid, "de", f"{norm}-1", "Folge 1", "1", 1.0, f"i-{norm}"))
    return eid, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()


def missing(c, eid):
    return {r["seq"] for r in c.execute("SELECT seq FROM book WHERE edition_id=? AND owned=0", (eid,))}


def test_hoerspielserie_zaehlt_folgen_als_fehlend():
    db.init()
    c = db.conn()
    eid, ed = edition(c, "drei")
    audible._merge(c, ed, "SER", [child("drei-1", "1", "Performance"), child("x2", "2", "Performance"),
                                  child("x3", "3", "Performance"), child("x4", "4", "Product")])
    assert missing(c, eid) == {2.0, 3.0, 4.0}
    assert c.execute("SELECT drama FROM edition WHERE id=?", (eid,)).fetchone()["drama"] == 1


def test_hoerbuchserie_laesst_hoerspiel_weg():
    db.init()
    c = db.conn()
    eid, ed = edition(c, "buch")
    audible._merge(c, ed, "SER", [child("buch-1", "1", "Product"), child("y2", "2", "Product"),
                                  child("y3", "3", "Product"), child("y3h", "3", "Performance"), child("y4", "4", "Performance")])
    assert missing(c, eid) == {2.0, 3.0}
    assert c.execute("SELECT asin FROM book WHERE edition_id=? AND seq=3", (eid,)).fetchone()["asin"] == "y3"
    assert c.execute("SELECT drama FROM edition WHERE id=?", (eid,)).fetchone()["drama"] == 0


def test_kein_abzug_fuer_hoerspiel_in_hoerspielserie():
    book = dict(title="Und der Karpatenhund", author="Oliver Rohrbeck", series="Die drei ???", sequence="23", lang="de", runtime_min=50)
    rel = {"title": "Die drei Fragezeichen - Folge 23 - Und der Karpatenhund (Hörspiel) German", "size": 60_000_000}
    plain = score(book, rel)
    drama = score({**book, "drama_series": 1}, rel)
    assert "Hörspiel- oder Sonderfassung" in " ".join(r if isinstance(r, str) else str(r) for r in plain["reasons"])
    assert drama["score"] > plain["score"]
