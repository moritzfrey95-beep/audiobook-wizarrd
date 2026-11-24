"""Hinzufügen: Serie oder Buch kommt als fehlend in die Bibliothek und wird mit Vorrang gesucht."""
from datetime import date

import pytest

from abw import abs as abs_mod
from abw import audible, db, library, notifications, search


def child(asin, seq, date_="2024-01-01"):
    return {"asin": asin, "sequence": seq, "title": f"Band {seq}", "subtitle": None, "author": "Autorin A", "narrator": "N",
            "release_date": date_, "runtime_min": 500, "cover_url": None, "language": "english", "content_type": "Product", "format_type": "unabridged"}


@pytest.fixture
def fresh(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series", "watchlist", "follow", "notification", "book_search", "download", "user"):
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(library, "kick", lambda s, b: None)
    monkeypatch.setattr(audible, "series_children", lambda lang, asin: ("Neue Serie", [child("N1", "1"), child("N2", "2"), child("N3", "3", "2200-01-01")]))
    return c


def candidates(c):
    return [r["title"] for r in c.execute(search.CANDIDATES + " ORDER BY COALESCE(b.wanted, s.prio) IS NULL, q.last_search IS NOT NULL,"
                                          " COALESCE(b.wanted, s.prio) DESC, q.last_search", {"today": date.today().isoformat()})]


def test_serie_hinzufuegen_ueberwacht_und_sucht_zuerst(fresh):
    c = fresh
    old = c.execute("INSERT INTO series(name, norm) VALUES('Alt','alt')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, monitored, link_source) VALUES(?,?,?,?,1,'manual')", (old, "en", "Alt", "alt")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned) VALUES(?,?,?,?,?,?,0)", (eid, "en", "ALT2", "Alter Band", "2", 2.0))
    res = library.add({"asin": "SERNEU", "lang": "en", "title": "Neue Serie", "kind": "series"}, 7)
    assert res["ok"] and res["series_id"]
    e = c.execute("SELECT * FROM edition WHERE audible_asin='SERNEU'").fetchone()
    assert e["monitored"] == 1 and e["link_source"] == "added" and e["series_id"] == res["series_id"]
    assert notifications.follows(7)["series"] == [res["series_id"]]  # was man hinzufügt, ist markiert
    # die zwei erschienenen Bände der neuen Serie stehen vor dem alten fehlenden Band; der angekündigte wird nicht gesucht
    assert candidates(c) == ["Band 1", "Band 2", "Alter Band"]
    assert library.listing()[0]["status"] == "searching" and "0 von 2" in library.listing()[0]["text"]
    # der Bestandsabgleich räumt eine hinzugefügte Serie ohne vorhandene Bücher nicht weg
    abs_mod.cleanup(c)
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=?", (e["id"],)).fetchone()[0] == 3
    # entfernen: nichts geladen, also verschwindet die Serie wieder; ein fremder Benutzer darf das nicht
    with pytest.raises(PermissionError):
        library.remove("SERNEU", {"id": 8, "role": "user"})
    library.remove("SERNEU", {"id": 7, "role": "user"})
    assert not c.execute("SELECT 1 FROM edition WHERE audible_asin='SERNEU'").fetchone() and candidates(c) == ["Alter Band"]


def test_buch_einer_serie_bringt_die_ganze_serie(fresh, monkeypatch):
    c = fresh
    monkeypatch.setattr(audible, "products", lambda lang, asins: [{"asin": "N2", "title": "Band 2", "language": "english", "authors": [{"name": "Autorin A"}],
                                                                  "series": [{"asin": "SERNEU", "title": "Neue Serie", "sequence": "2"}]}])
    res = library.add({"asin": "N2", "lang": "en", "title": "Band 2", "kind": "book"}, 1)
    assert res["series_id"] and not res["book_id"]
    assert c.execute("SELECT COUNT(*) FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=?", (res["series_id"],)).fetchone()[0] == 3


def test_einzelbuch_wird_gesucht_und_bei_ankunft_erkannt(fresh, monkeypatch):
    c = fresh
    monkeypatch.setattr(audible, "products", lambda lang, asins: [{"asin": "SOLO1", "title": "Allein", "language": "english", "authors": [{"name": "Autorin A"}],
                                                                  "release_date": "2020-01-01", "runtime_length_min": 300}])
    res = library.add({"asin": "SOLO1", "lang": "en", "kind": "book"}, 3)
    assert res["book_id"] and candidates(c) == ["Allein"]
    assert library.listing()[0]["status"] == "searching"
    item = {"id": "abs-1", "relPath": "Standalone - Autorin A/Allein", "addedAt": 1,
            "media": {"metadata": {"title": "Allein", "asin": "SOLO1", "authorName": "Autorin A"}, "duration": 18000, "numAudioFiles": 1, "size": 5}}
    abs_mod._upsert_item(c, "en", item, None)
    rows = c.execute("SELECT id, owned FROM book WHERE asin='SOLO1'").fetchall()
    assert [(r["id"], r["owned"]) for r in rows] == [(res["book_id"], 1)] and candidates(c) == []
    assert library.listing()[0]["status"] == "owned"


def test_reiner_ebook_ordner_ist_kein_vorhandenes_hoerbuch(fresh):
    c = fresh
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Zyklus Q','zyklusq')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, monitored) VALUES(?,?,?,?,1)", (sid, "en", "Zyklus Q", "zyklusq")).lastrowid
    bid = c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, release_date) VALUES(?,?,?,?,?,?,0,'2200-01-01')",
                    (eid, "en", "S4", "Band 4", "4", 4.0)).lastrowid
    abs_mod._ebook_only(c, "en", {"id": "abs-e", "relPath": "S/Book 4 - Band 4",
                                  "media": {"ebookFormat": "epub", "numAudioFiles": 0, "metadata": {"title": "Band 4", "seriesName": "Zyklus Q #4"}}})
    b = c.execute("SELECT owned, epub, pre_path, abs_item_id FROM book WHERE id=?", (bid,)).fetchone()
    assert (b["owned"], b["epub"], b["pre_path"], b["abs_item_id"]) == (0, 1, "S/Book 4 - Band 4", None)


def test_ohne_folgen_beim_hinzufuegen(fresh):
    """Einstellung im Entdecken: abgeschaltet wird die Serie aufgenommen, aber nicht markiert."""
    c = fresh
    c.execute("INSERT INTO user(id, name, hash, role, created, follow_on_add) VALUES(9, 'gast', '-', 'user', datetime('now'), 0)")
    res = library.add({"asin": "SERNEU", "lang": "en", "title": "Neue Serie", "kind": "series"}, 9)
    assert res["ok"] and res["series_id"] and notifications.follows(9)["series"] == []
    # derselbe Eintrag, von einem Benutzer mit Folgen hinzugefügt: der wird markiert
    c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(10, 'max2', '-', 'user', datetime('now'))")
    library.add({"asin": "SERNEU", "lang": "en", "kind": "series"}, 10)
    assert notifications.follows(10)["series"] == [res["series_id"]] and notifications.follows(9)["series"] == []


def test_serien_gehoerter_autoren(fresh, monkeypatch):
    """Fertig gehörtes Buch bringt alle Serien des Autors in der Sprache des Buchs, nur mit Schalter, nur einmal."""
    c = fresh
    for t in ("author_catalog", "author_auto", "user_progress", "listen_progress"):
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO user(id, name, hash, created, abs_user_id) VALUES(3,'ada','x','2026','abs-ada')")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Alt','alt')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, monitored) VALUES(?,?,?,?,?,0)", (sid, "en", "Alt", "alt", "SERALT")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned, abs_item_id) VALUES(?,?,?,?,1,'it1')", (eid, "en", "Alt 1", "Autorin A"))
    for asin, title in (("SERALT", "Alt"), ("SERNEU", "Neue Serie"), ("SERKURZ", "Neue Serie (gekürzt)"), ("SERHS", "Neue Serie [Hörspiel]")):
        c.execute("INSERT INTO author_catalog VALUES('autorin','en',?,?,3,NULL,NULL,'2026')", (asin, title))
    c.execute("INSERT INTO author_catalog VALUES('autorin','de','SERDE','Neue Serie DE',3,NULL,NULL,'2026')")
    assert library.finished_authors() == "Ausgeschaltet"
    db.set_setting("auto_author_series", "1")
    assert library.finished_authors() == "Keine neuen Serien gehörter Autoren"  # noch nichts fertig gehört
    c.execute("INSERT INTO listen_progress(abs_user_id, item_id, finished) VALUES('abs-ada','it1',1)")
    assert library.finished_authors().startswith("3 von 3")
    w = c.execute("SELECT * FROM watchlist ORDER BY asin").fetchall()
    assert [(r["asin"], r["user_id"], r["state"], r["author"]) for r in w] == [("SERDE", 3, "done", "Autorin A"), ("SERHS", 3, "done", "Autorin A"), ("SERNEU", 3, "done", "Autorin A")]
    assert c.execute("SELECT monitored FROM edition WHERE id=?", (eid,)).fetchone()[0] == 0  # vorhandene Serie unverändert
    assert c.execute("SELECT 1 FROM edition WHERE audible_asin='SERDE' AND lang='de'").fetchone()  # immer beide Sprachen
    library.remove("SERNEU", {"id": 3, "role": "user"})
    library.remove("SERHS", {"id": 3, "role": "user"})
    library.remove("SERDE", {"id": 3, "role": "user"})
    assert library.finished_authors() == "Keine neuen Serien gehörter Autoren"  # entfernt bleibt entfernt
    db.set_setting("auto_author_series", "0")


def test_gehoerter_autor_andere_sprache_auf_dieselbe_karte(fresh, monkeypatch):
    """Englisch gehört: die deutsche Fassung der vorhandenen Serie hängt an derselben Karte, der Eintrag zeigt dorthin."""
    c = fresh
    for t in ("author_catalog", "author_auto", "user_progress", "listen_progress"):
        c.execute(f"DELETE FROM {t}")
    de = lambda a, s: {**child(a, s), "language": "german"}
    monkeypatch.setattr(audible, "series_children", lambda lang, asin: ("Saga X", [de("D1", "1"), de("D2", "2")]))
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Saga X','x')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, monitored) VALUES(?,?,?,?,?,1)", (sid, "en", "Saga X", "x", "SXEN")).lastrowid
    bid = c.execute("INSERT INTO book(edition_id, lang, title, author, owned) VALUES(?,?,?,?,1)", (eid, "en", "X 1", "Autorin A")).lastrowid
    c.execute("INSERT INTO user(id, name, hash, created) VALUES(1,'max','x','2026')")
    c.execute("INSERT INTO user_progress(user_id, book_id, finished) VALUES(1,?,1)", (bid,))
    c.execute("INSERT INTO author_catalog VALUES('autorin','en','SXEN','Saga X',3,NULL,NULL,'2026')")
    c.execute("INSERT INTO author_catalog VALUES('autorin','de','SXDE','Saga X [German Edition]',3,NULL,NULL,'2026')")
    db.set_setting("auto_author_series", "1")
    assert library.finished_authors() == "1 von 1 Serien gehörter Autoren aufgenommen, 1 Sprachfassungen verknüpft"
    assert c.execute("SELECT series_id FROM edition WHERE audible_asin='SXDE'").fetchone()[0] == sid
    assert c.execute("SELECT series_id FROM watchlist WHERE asin='SXDE'").fetchone()[0] == sid
    assert library.listing()[0]["text"] == "1 von 3 Bänden im Bestand"
    db.set_setting("auto_author_series", "0")


def test_gleichnamige_serie_gleicher_autor_wird_fassung(fresh, monkeypatch):
    """Harry Potter 19.11.2026: Karte «Harry Potter» hat schon eine englische Hauptfassung; die Audible-Serie «Harry Potter»
    mit Jim Dale kommt als Sprecher-Fassung auf diese Karte statt auf eine zweite gleichnamige Karte."""
    c = fresh
    dale = lambda a, s: {**child(a, s), "author": "J. K. Rowling", "narrator": "Jim Dale"}
    monkeypatch.setattr(audible, "series_children", lambda lang, asin: ("Harry Potter", [dale("D1", "1"), dale("D2", "2")]))
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Harry Potter','harrypotter')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, link_source) VALUES(?,?,?,?,?,'manual')",
                    (sid, "en", "Hogwarts Library", "hogwartslibrary", "HL")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned) VALUES(?,?,?,?,1)", (eid, "en", "Quidditch", "J. K. Rowling"))
    got = audible.add_series("en", "HPDALE", "Harry Potter", "auto")
    e = c.execute("SELECT * FROM edition WHERE audible_asin='HPDALE'").fetchone()
    assert got == sid and e["series_id"] == sid and e["variant"] == "Jim Dale" and e["narr"] == "Jim Dale"
    assert c.execute("SELECT COUNT(*) FROM series WHERE name='Harry Potter'").fetchone()[0] == 1


def test_gleichnamige_serie_anderer_autor_eigene_karte(fresh, monkeypatch):
    c = fresh
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Neue Serie','neueserie')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin) VALUES(?,?,?,?,?)", (sid, "en", "Neue Serie", "neueserie", "ALT")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned) VALUES(?,?,?,?,1)", (eid, "en", "X", "Jemand Anderes"))
    got = audible.add_series("en", "SERNEU2", "Neue Serie")
    e = c.execute("SELECT * FROM edition WHERE audible_asin='SERNEU2'").fetchone()
    assert got != sid and e["series_id"] == got and e["variant"] is None


def test_hinzugefuegtes_ist_ohne_bestand_sichtbar(fresh, monkeypatch):
    """Fehler 20.11.: Ein hinzugefügtes Einzelbuch stand nirgends, und Autoren ohne Bestand fehlten in der Autorenliste."""
    from abw import api
    c = fresh
    c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(18, 'Bige', 'x', 'user', '2026-11-20')")
    c.execute("INSERT INTO book(edition_id, lang, asin, title, author, owned, wanted, wanted_by) VALUES(NULL,'de','EINZEL','Der Schwarzbrenner','Jonas Jonasson',0,'2026-11-20T16:45:00',18)")
    c.execute("INSERT INTO book(edition_id, lang, asin, title, author, owned) VALUES(NULL,'de','ALTEINZEL','Nicht gewünscht','Jemand Anders',0)")
    monkeypatch.setattr(api, "auto_on", lambda: True)
    groups = api.single_groups(with_books=True)
    assert list(groups) == [db.norm("Jonas Jonasson")]
    g = groups[db.norm("Jonas Jonasson")]
    assert [(b["title"], b["state"], b["added_by"]) for b in g["books"]] == [("Der Schwarzbrenner", "searching", "Bige")]
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Hundert','hundert')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, monitored, link_source) VALUES(?,?,?,?,1,'added')", (sid, "de", "Hundert", "hundert")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, author, sequence, seq, owned) VALUES(?,?,?,?,?,?,?,0)", (eid, "de", "H1", "Band 1", "Serien Autorin", "1", 1.0))
    keys = {a["key"]: a for a in api.authors()}
    assert db.norm("Jonas Jonasson") in keys and keys[db.norm("Jonas Jonasson")]["en"] == 0
    assert db.norm("Jemand Anders") not in keys
    if db.norm("Serien Autorin") in keys:  # Serienautor kommt aus dem ersten Band
        assert keys[db.norm("Serien Autorin")]["series_count"] == 1


def test_einzelbuecher_als_karte_je_autor(fresh, monkeypatch):
    """Einzelbücher stehen als Karte «Autor – Einzelbücher» in der Serienansicht, vorhandene und
    hinzugefügte zusammen, je Sprache gezählt. Bücher mit Serie gehören nicht dazu."""
    from abw import api
    c = fresh
    monkeypatch.setattr(api, "auto_on", lambda: False)
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned, runtime_min, added_at) VALUES(NULL,'en','Pillars','Ken Follett',1,600,'2026-01-01')")
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned, runtime_min, added_at) VALUES(NULL,'de','Die Tore der Welt','Ken Follett',1,1200,'2026-02-01')")
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned, wanted) VALUES(NULL,'de','Kälteschock','Ken Follett',0,'2026-11-20')")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Kingsbridge','kingsbridge')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", "Kingsbridge", "kingsbridge")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, title, author, owned) VALUES(?,?,?,?,1)", (eid, "en", "Serienband", "Ken Follett"))
    g = api.single_groups()[db.norm("Ken Follett")]
    assert g["name"] == "Ken Follett – Einzelbücher" and g["owned"] == 2 and g["total"] == 3 and g["hours"] == 30
    assert g["langs"] == [{"lang": "en", "owned": 1, "total": 1, "upcoming": 0}, {"lang": "de", "owned": 1, "total": 2, "upcoming": 0}]
    assert g["flags"] == ["incomplete", "single"] and g["primary"] == "incomplete" and g["missing"] == 1
    assert "books" not in g and g["added_at"] == "2026-11-20"
