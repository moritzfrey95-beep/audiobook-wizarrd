import zipfile

import pytest

from abw import cleanup, config, db, ebook

BOOK = {"id": 1, "lang": "en", "title": "Dungeon Crawler Carl", "author": "Matt Dinniman", "series": "Dungeon Crawler Carl", "sequence": "1"}
SIBLINGS = ["Carl's Doomsday Scenario", "The Butcher's Masquerade", "This Inevitable Ruin"]


def hit(title, size=1_200_000):
    return {"guid": title, "title": title, "size": size, "grabs": 0}


@pytest.mark.parametrize("title, verdict", [
    ("Matt Dinniman - [Dungeon Crawler Carl 01] - Dungeon Crawler Carl (epub)", "sicher"),
    ("Matt Dinniman - Dungeon Crawler Carl", "sicher"),
    ("Matt Dinniman - [Dungeon Crawler Carl 01] - Dungeon Crawler Carl - Victorys Price Returns (epub)", "möglich"),  # fremde Wörter: die Prüfung nach dem Download entscheidet
    ("Matt Dinniman - [Dungeon Crawler Carl 05] - The Butchers Masquerade (epub)", "unsicher"),  # anderer Band
    ("This.Inevitable.Ruin-Dungeon.Crawler.Carl.Book.VII-Matt.Dinniman.epub.mobi", "unsicher"),
    ("Dungeon Crawler Carl", "möglich"),  # weder Autor noch Bandnummer
    ("Matt Dinniman - Dungeon Crawler Carl 01 (azw3)", "verworfen"),
    ("Matt Dinniman - Dungeon Crawler Carl 01 - Unabridged MP3", "verworfen"),
    ("Matt Dinniman - Dungeon Crawler Carl Books 1-7 (epub)", "unsicher"),
    ("Matt Dinniman - Dungeon Crawler Carl German (epub)", "verworfen"),
    ("Brandon Sanderson - Mistborn 01 (epub)", "verworfen"),
])
def test_bewertung(title, verdict):
    assert ebook.score(BOOK, hit(title), SIBLINGS)["verdict"] == verdict


def test_gleicher_titel_in_beiden_sprachen_ist_nie_sicher():
    de = {**BOOK, "lang": "de", "same_title_other_lang": True}
    assert ebook.score(de, hit("Matt Dinniman - Dungeon Crawler Carl 01 (epub)"), [])["verdict"] == "möglich"
    ok = {"title": "Dungeon Crawler Carl", "authors": ["Matt Dinniman"], "language": ""}
    assert "keine Sprache" in ebook.mismatch(de, ok, [])  # ohne Sprachangabe im EPUB bleibt es unentschieden
    assert "andere" in ebook.mismatch(de, {**ok, "language": "en"}, []) and ebook.mismatch(de, {**ok, "language": "de"}, []) is None
    assert ebook.score(de, hit("Matt Dinniman - Dungeon Crawler Carl 01 German (epub)"), [])["verdict"] == "sicher"


def make_epub(path, title, author="Matt Dinniman", lang="en"):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                                             '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                                        f"<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:language>{lang}</dc:language></metadata></package>")
    return path


def test_pruefung_des_inhalts(tmp_path):
    ok = ebook.read_meta(make_epub(tmp_path / "a.epub", "Dungeon Crawler Carl"))
    assert ok == {"title": "Dungeon Crawler Carl", "authors": ["Matt Dinniman"], "language": "en"}
    assert ebook.mismatch(BOOK, ok, SIBLINGS) is None
    other = ebook.read_meta(make_epub(tmp_path / "b.epub", "The Butcher's Masquerade (Dungeon Crawler Carl Book 5)"))
    assert "anderer Band" in ebook.mismatch(BOOK, other, SIBLINGS)
    assert "Sprache" in ebook.mismatch(BOOK, {**ok, "language": "de-de"}, SIBLINGS)
    assert "Autor" in ebook.mismatch(BOOK, {**ok, "authors": ["Jemand Anders"]}, SIBLINGS)
    assert "Titel" in ebook.mismatch(BOOK, {**ok, "title": "Mistborn"}, SIBLINGS)
    # EPUB ohne Autor: nur der Titel zählt, fremde Wörter heissen anderes Buch («Conan The Marauder» für Seth Rings «Marauder»)
    marauder = {"id": 2, "lang": "en", "title": "Marauder", "author": "Seth Ring", "series": "The Tower Series", "sequence": "8"}
    assert "keinen Autor" in ebook.mismatch(marauder, {"title": "Conan The Marauder", "authors": [], "language": "en"}, [])
    assert ebook.mismatch(marauder, {"title": "Marauder (The Tower Series Book 8)", "authors": [], "language": "en"}, []) is None
    assert ebook.mismatch(BOOK, {**ok, "authors": []}, SIBLINGS) is None
    (tmp_path / "kaputt.epub").write_bytes(b"kein zip")
    with pytest.raises(ValueError):
        ebook.read_meta(tmp_path / "kaputt.epub")


def setup(tmp_path, monkeypatch):
    lib, dl = tmp_path / "lib", tmp_path / "dl"
    (lib / "DCC/Book 1 - Dungeon Crawler Carl").mkdir(parents=True)
    (lib / "DCC/Book 1 - Dungeon Crawler Carl/a.m4b").write_bytes(b"audio")
    dl.mkdir()
    monkeypatch.setattr(config, "LIBRARY_ROOT", {"en": lib, "de": lib})
    monkeypatch.setattr(config, "USENET_COMPLETE_ROOT", dl)
    monkeypatch.setattr(config, "TORRENT_COMPLETE_ROOT", tmp_path / "x")
    monkeypatch.setattr(config, "IMPORT_ENABLED", True)
    monkeypatch.setattr("abw.abs.scan", lambda lang: None)
    db.init()
    c = db.conn()
    for t in ("book", "ebook_job", "ebook_search", "download", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO book(id, lang, title, author, sequence, owned, rel_path) VALUES(1,'en','Dungeon Crawler Carl','Matt Dinniman','1',1,'DCC/Book 1 - Dungeon Crawler Carl')")
    return lib / "DCC/Book 1 - Dungeon Crawler Carl", dl, c


def job(c, marker, state="arrived"):
    c.execute("INSERT INTO ebook_job(book_id, release_title, guid, marker, state, created, updated) VALUES(1,'R',?,?,?,'2026','2026')", (marker, marker, state))
    return dict(c.execute("SELECT * FROM ebook_job WHERE marker=?", (marker,)).fetchone())


def test_ablage_legt_nur_eine_neue_datei_an(tmp_path, monkeypatch):
    folder, dl, c = setup(tmp_path, monkeypatch)
    (dl / "abw-ebook-1-a").mkdir()
    make_epub(dl / "abw-ebook-1-a/falsch.epub", "Mistborn", "Brandon Sanderson")
    make_epub(dl / "abw-ebook-1-a/richtig.epub", "Dungeon Crawler Carl")
    assert ebook.finish(job(c, "abw-ebook-1-a")) == "done"
    assert sorted(p.name for p in folder.iterdir()) == ["Dungeon Crawler Carl.epub", "a.m4b"]
    assert (folder / "a.m4b").read_bytes() == b"audio"
    assert c.execute("SELECT epub FROM book WHERE id=1").fetchone()[0] == 1
    # Zweiter Versuch: im Ordner liegt schon ein EPUB, nichts wird überschrieben
    before = (folder / "Dungeon Crawler Carl.epub").read_bytes()
    (dl / "abw-ebook-1-b").mkdir()
    make_epub(dl / "abw-ebook-1-b/neu.epub", "Dungeon Crawler Carl", lang="en-US")
    assert ebook.finish(job(c, "abw-ebook-1-b")) == "failed"
    assert (folder / "Dungeon Crawler Carl.epub").read_bytes() == before
    assert ebook.blocker(dict(c.execute("SELECT * FROM book WHERE id=1").fetchone())) == "Im Ordner liegt schon ein EPUB"


def test_falsches_epub_wird_verworfen_und_aufraeumen_laesst_laufende_liegen(tmp_path, monkeypatch):
    folder, dl, c = setup(tmp_path, monkeypatch)
    (dl / "abw-ebook-1-c").mkdir()
    make_epub(dl / "abw-ebook-1-c/x.epub", "The Butcher's Masquerade")
    (dl / "abw-ebook-1-d").mkdir()
    job(c, "abw-ebook-1-d", "arrived")
    assert {x["marker"] for x in cleanup.candidates()} == {"abw-ebook-1-c"}  # c ist unbekannt, d wird noch gebraucht
    assert ebook.finish(job(c, "abw-ebook-1-c")) == "rejected"
    assert [p.name for p in folder.iterdir()] == ["a.m4b"]
    assert ebook.states()[1]["state"] == "loading"  # d läuft noch


def test_serienname_als_haupttitel_und_gleiche_titel_brauchen_die_bandnummer():
    sw = {"id": 2, "lang": "en", "title": "Star Wars: Alphabet Squadron", "author": "Alexander Freed", "series": "Star Wars", "sequence": "42"}
    sib = ["Star Wars: Victory's Price", "Star Wars: Shadow Fall"]
    assert ebook.score(sw, hit("Alexander Freed - [Star Wars Alphabet Squadron 03] - Victorys Price (retail) (epub)"), sib)["verdict"] == "unsicher"
    assert ebook.score(sw, hit("Alexander Freed - Star Wars Alphabet Squadron (retail) (epub)"), sib)["verdict"] == "sicher"
    merc = {"id": 3, "lang": "en", "title": "Reborn as a Space Mercenary", "author": "Ryuto", "series": "Reborn as a Space Mercenary", "sequence": "13"}
    same = ["Reborn as a Space Mercenary"] * 3
    assert ebook.score(merc, hit("Ryuto - Reborn as a Space Mercenary (epub)"), same)["verdict"] == "möglich"
    plain = {"title": "Reborn as a Space Mercenary", "authors": ["Ryuto"], "language": "en"}
    assert "Bandnummer" in ebook.mismatch(merc, plain, same, seq_ok=False)  # welcher Band es ist, bleibt offen
    assert ebook.mismatch(merc, plain, same, seq_ok=True) is None
    assert ebook.score(merc, hit("Ryuto - Reborn as a Space Mercenary 13 (epub)"), same)["verdict"] == "sicher"
    assert ebook.score(merc, hit("Ryuto - Reborn as a Space Mercenary 03 (epub)"), same)["verdict"] == "unsicher"
    assert "anderer Band" in ebook.mismatch(merc, {"title": "Reborn as a Space Mercenary: Volume 3", "authors": ["Ryuto"], "language": "en"}, same)
    assert ebook.mismatch(merc, {"title": "Reborn as a Space Mercenary: Volume 13", "authors": ["Ryuto"], "language": "en"}, same) is None


def test_von_hand_ueberschreiben_legt_den_alten_ordner_in_den_papierkorb(tmp_path, monkeypatch):
    from abw import importer
    lib, dl, trash = tmp_path / "lib", tmp_path / "dl", tmp_path / "lib/.abw-papierkorb"
    old = lib / "HP/Book 2 - Kammer"
    old.mkdir(parents=True)
    (old / "alt.mp3").write_bytes(b"alt")
    (dl / "abw-7-x").mkdir(parents=True)
    (dl / "abw-7-x/neu.m4b").write_bytes(b"neu!")
    monkeypatch.setattr(config, "LIBRARY_ROOT", {"en": lib, "de": lib})
    monkeypatch.setattr(config, "LIBRARY_TRASH", trash)
    monkeypatch.setattr(config, "USENET_COMPLETE_ROOT", dl)
    monkeypatch.setattr(config, "IMPORT_ENABLED", True)
    db.init()
    c = db.conn()
    c.execute("DELETE FROM book")
    c.execute("DELETE FROM edition")
    c.execute("DELETE FROM series")
    c.execute("INSERT INTO series(id, name, norm) VALUES(5, 'HP', 'hp')")
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm) VALUES(5, 5, 'en', 'HP', 'hp')")
    c.execute("INSERT INTO book(id, edition_id, lang, title, sequence, owned, rel_path) VALUES(8, 5, 'en', 'Halbband', '2.5', 1, 'HP/Book 2.5 - Halbband')")
    book = {"id": 7, "edition_id": 5, "lang": "en", "title": "Kammer", "sequence": "2", "series": "HP"}
    d = {"source": "usenet", "marker": "abw-7-x"}
    p = importer.plan(book, d)
    assert p["overwritable"] and p["existing"] == {"files": 1, "bytes": 3}
    assert p["problems"] == ["Der Zielordner existiert schon; ABW überschreibt nichts"]  # «Book 2.5» stört Band 2 nicht
    with pytest.raises(ValueError):
        importer.execute(book, d)  # ohne ausdrückliches Überschreiben bleibt alles, wie es ist
    assert (old / "alt.mp3").read_bytes() == b"alt"
    importer.execute(book, d, overwrite=True)
    assert sorted(f.name for f in old.iterdir()) == ["metadata.json", "neu.m4b"]
    moved = list(trash.rglob("alt.mp3"))
    assert len(moved) == 1 and moved[0].read_bytes() == b"alt" and moved[0].parent.name == "Book 2 - Kammer"


def test_ganzer_titel_ersetzt_die_bandnummer_und_torrent_suchbegriffe():
    jedi = {"id": 4, "lang": "en", "title": "Star Wars Jedi: Battle Scars", "author": "Sam Maggs", "series": "Star Wars", "sequence": "26"}
    sib = ["Star Wars Jedi: Fallen Order - Dark Temple"]
    assert "Bandnummer" in ebook.mismatch(jedi, {"title": "Star Wars Jedi", "authors": ["Sam Maggs"], "language": "en"}, sib, seq_ok=False)
    assert ebook.mismatch(jedi, {"title": "Star Wars Jedi: Battle Scars", "authors": ["Sam Maggs"], "language": "en"}, sib, seq_ok=False) is None
    assert ebook.score(jedi, hit("Sam Maggs - Star Wars Jedi Battle Scars (epub)"), sib)["verdict"] == "sicher"
    gs = {"id": 5, "lang": "en", "title": "Goblin Slayer, Vol. 12", "author": "Kumo Kagyu, Noboru Kannatuki", "series": "Goblin Slayer", "sequence": "12"}
    assert ebook.torrent_queries(gs) == ["Goblin Slayer", "Goblin Slayer kagyu"]
    assert ebook.torrent_queries(jedi) == ["Star Wars Jedi", "Star Wars Jedi maggs"]


def test_titelwoerter_muessen_beieinander_stehen():
    rr = {"id": 6, "lang": "en", "title": "Red Rising", "author": "Pierce Brown", "series": "Red Rising Saga", "sequence": "1"}
    assert ebook.score(rr, hit("Red Moon Rising - Peter Moore"), [])["verdict"] == "unsicher"
    assert ebook.score(rr, hit("Red Rising - Pierce Brown"), [])["verdict"] == "sicher"
    assert ebook.score(rr, hit("Red Rising"), [])["verdict"] == "möglich"


def test_unpassendes_epub_wird_dort_abgelegt_wo_es_passt(tmp_path, monkeypatch):
    folder, dl, c = setup(tmp_path, monkeypatch)
    lib = folder.parent.parent
    for i, (title, seq) in enumerate([("The Butcher's Masquerade", "5"), ("Carl's Doomsday Scenario", "2")], start=2):
        (lib / f"DCC/Book {seq} - {title}").mkdir()
        c.execute("INSERT INTO book(id, lang, title, author, sequence, owned, rel_path) VALUES(?,?,?,?,?,1,?)",
                  (i, "en", title, "Matt Dinniman", seq, f"DCC/Book {seq} - {title}"))
    (lib / "DCC/Book 2 - Carl's Doomsday Scenario/alt.epub").write_bytes(b"schon da")
    (dl / "abw-ebook-1-e").mkdir()
    make_epub(dl / "abw-ebook-1-e/a.epub", "The Butcher's Masquerade (Dungeon Crawler Carl Book 5)")
    make_epub(dl / "abw-ebook-1-e/b.epub", "Carl's Doomsday Scenario")  # dort liegt schon ein EPUB
    make_epub(dl / "abw-ebook-1-e/c.epub", "The Butcher's Masquerade", author="Jemand Anders")
    make_epub(dl / "abw-ebook-1-e/d.epub", "Mistborn", "Brandon Sanderson")
    assert ebook.finish(job(c, "abw-ebook-1-e")) == "rejected"  # zum gesuchten Band 1 passt keines
    assert [p.name for p in folder.iterdir()] == ["a.m4b"]
    assert [p.name for p in (lib / "DCC/Book 5 - The Butcher's Masquerade").iterdir()] == ["The Butcher's Masquerade.epub"]
    assert [p.name for p in (lib / "DCC/Book 2 - Carl's Doomsday Scenario").iterdir()] == ["alt.epub"]
    rows = {r["book_id"]: (r["state"], r["error"]) for r in c.execute("SELECT book_id, state, error FROM ebook_job")}
    assert rows[2] == ("done", None) and "Passte aber zu: «The Butcher's Masquerade»" in rows[1][1]
    assert c.execute("SELECT epub FROM book WHERE id=2").fetchone()[0] == 1


def _job(c, marker, manual):
    c.execute("INSERT INTO ebook_job(book_id, release_title, guid, marker, state, created, updated, source, seq_ok, manual)"
              " VALUES(1,'R','g-' || ?,?,'arrived','2026','2026','usenet',0,?)", (marker, marker, manual))
    return dict(c.execute("SELECT * FROM ebook_job WHERE marker=?", (marker,)).fetchone())


def test_von_hand_gewaehltes_epub_wird_trotz_abweichung_abgelegt(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    c = db.conn()
    for marker in ("abw-ebook-1-auto", "abw-ebook-1-hand"):
        (tmp_path / "dl" / marker).mkdir()
        make_epub(tmp_path / "dl" / marker / "x.epub", "Carl im Kerker", author="Jemand Anders")
    assert ebook.finish(_job(c, "abw-ebook-1-auto", 0)) == "rejected"  # die Automatik bleibt streng
    assert ebook.finish(_job(c, "abw-ebook-1-hand", 1)) == "done"
    row = c.execute("SELECT error, imported_to FROM ebook_job WHERE marker='abw-ebook-1-hand'").fetchone()
    assert "Von Hand gewählt" in row["error"] and row["imported_to"].endswith("Dungeon Crawler Carl.epub")


def test_von_hand_gewaehlt_mit_mehreren_epubs_raet_nicht(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    c = db.conn()
    d = tmp_path / "dl" / "abw-ebook-1-viele"
    d.mkdir()
    make_epub(d / "a.epub", "Etwas Anderes", author="Jemand Anders")
    make_epub(d / "b.epub", "Noch Etwas", author="Jemand Anders")
    assert ebook.finish(_job(c, "abw-ebook-1-viele", 1)) == "rejected"


def test_handauswahl_startet_den_gewaehlten_treffer(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    sent = []
    monkeypatch.setattr(ebook.usenet, "known", lambda guid: True)
    monkeypatch.setattr(ebook.usenet, "fetch_nzb", lambda guid: b"nzb")
    monkeypatch.setattr(ebook.usenet, "submit", lambda nzb, marker: sent.append(marker) or "nzo1")
    r = ebook.grab(1, {"guid": "g-hand", "title": "Matt Dinniman - Dungeon Crawler Carl Books 1-7 (epub)", "source": "usenet", "indexer": "X", "size": 5})
    assert r["started"] and len(sent) == 1
    job = db.conn().execute("SELECT manual, state, auto FROM ebook_job WHERE guid='g-hand'").fetchone()
    assert (job["manual"], job["state"], job["auto"]) == (1, "queued", 0)
    with pytest.raises(ValueError, match="lädt schon"):
        ebook.grab(1, {"guid": "g-zwei", "title": "x", "source": "usenet"})
