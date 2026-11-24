"""Doppelte Karten derselben Audible-Serie zusammenführen; der nächste Bestandsabgleich hebt das nicht wieder auf."""
from abw import abs as abs_mod
from abw import db, merge


def setup():
    db.init()
    c = db.conn()
    for t in ("edition_alias", "follow", "book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO series(id, name, norm) VALUES(1,'Dune','dune'),(2,'Dune, The Dune Sequence','dunedunesequence'),(3,'Andere','andere')")
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm, audible_asin) VALUES"
              "(1,1,'en','Dune','dune','S1'),(2,2,'en','Dune, The Dune Sequence','dunedunesequence','S1'),(3,3,'en','Andere','andere','S2')")
    c.execute("INSERT INTO book(id, edition_id, lang, title, sequence, seq, owned, abs_item_id) VALUES"
              "(1,1,'en','Dune','1',1,1,'i1'),(2,2,'en','Dune Messiah','2',2,1,'i2'),(3,2,'en','Children of Dune','3',3,0,NULL)")
    c.execute("INSERT INTO follow(user_id, kind, key, created) VALUES(1,'series','2','x')")
    return c


def test_plan_and_apply():
    c = setup()
    p = merge.plan()
    assert len(p) == 1 and p[0]["target"]["id"] == 1 and [e["id"] for e in p[0]["merge"]] == [2]
    r = merge.apply(p)
    assert r == {"groups": 1, "moved": 1, "dropped": 1}
    assert [b["edition_id"] for b in c.execute("SELECT edition_id FROM book ORDER BY id")] == [1, 1]
    assert not c.execute("SELECT 1 FROM series WHERE id=2").fetchone()
    assert c.execute("SELECT 1 FROM follow WHERE kind='series' AND key='1'").fetchone()
    # Audiobookshelf nennt die Serie weiter «Dune, The Dune Sequence»: das Buch bleibt auf der Karte «Dune»
    assert abs_mod.get_edition(c, "en", "Dune, The Dune Sequence") == 1
    assert merge.plan() == []
