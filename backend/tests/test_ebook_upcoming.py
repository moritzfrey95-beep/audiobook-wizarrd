"""E-Book vorab: Für einen angekündigten Band entsteht der künftige Buchordner mit dem EPUB; das Hörbuch kommt später dazu."""
import json
from pathlib import Path

from abw import config, db, ebook, importer


def test_epub_vorab_und_hoerbuch_spaeter_im_selben_ordner(tmp_path, monkeypatch):
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    root, dl = tmp_path / "lib", tmp_path / "dl"
    (root / "Meine Serie" / "Book 1 - Eins").mkdir(parents=True)
    monkeypatch.setitem(config.LIBRARY_ROOT, "en", root)
    monkeypatch.setattr(config, "IMPORT_ENABLED", True)
    monkeypatch.setattr(config, "USENET_COMPLETE_ROOT", dl)
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Meine Serie','meineserie')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, monitored) VALUES(?,?,?,?,1)", (sid, "en", "Meine Serie", "meineserie")).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, abs_item_id, rel_path) VALUES(?,?,?,?,?,?,1,'x',?)",
              (eid, "en", "A1", "Eins", "1", 1.0, "Meine Serie/Book 1 - Eins"))
    bid = c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, release_date, author) VALUES(?,?,?,?,?,?,0,'2200-01-01','Autorin A')",
                    (eid, "en", "A2", "Zwei", "2", 2.0)).lastrowid
    from abw import search
    book, _ = search.book_ctx(bid)
    assert ebook.upcoming(book) and ebook.blocker(book) is None
    assert [r["id"] for r in c.execute(ebook.UPCOMING_CANDIDATES, {"today": "2026-11-12", "now": "9"})] == [bid]
    src = tmp_path / "zwei.epub"
    src.write_bytes(b"epub-inhalt")
    target = Path(ebook.place(book, src))
    assert target == root / "Meine Serie" / "Book 2 - Zwei" / "Zwei.epub" and target.read_bytes() == b"epub-inhalt"
    meta = json.loads((target.parent / "metadata.json").read_text())
    assert meta["series"] == ["Meine Serie #2"] and meta["asin"] == "A2"
    assert c.execute("SELECT pre_path FROM book WHERE id=?", (bid,)).fetchone()[0] == "Meine Serie/Book 2 - Zwei"
    book, _ = search.book_ctx(bid)
    assert ebook.blocker(book) == "Im Ordner liegt schon ein EPUB"
    # Das Hörbuch erscheint und wird geladen: der Ordner mit dem E-Book ist kein Hindernis, die Dateien kommen dazu
    (dl / "abw-x").mkdir(parents=True)
    (dl / "abw-x" / "zwei.m4b").write_bytes(b"ton" * 100)
    d = {"source": "usenet", "marker": "abw-x"}
    p = importer.plan(book, d)
    assert p["problems"] == [] and p["merge"] and p["shown"] == "Meine Serie/Book 2 - Zwei"
    assert importer.execute(book, d) == str(target.parent)
    assert sorted(f.name for f in target.parent.iterdir()) == ["Zwei.epub", "metadata.json", "zwei.m4b"]
    assert target.read_bytes() == b"epub-inhalt" and not list(target.parent.parent.glob(".*abw-teil"))
    # Ein Ordner, in dem schon ein Hörbuch liegt, bleibt gesperrt
    p = importer.plan(book, d)
    assert any("existiert schon" in x for x in p["problems"]) and not p["merge"]


def test_einzelbuch_landet_unter_standalone_autor(tmp_path, monkeypatch):
    db.init()
    monkeypatch.setitem(config.LIBRARY_ROOT, "en", tmp_path)
    assert importer.target_rel({"edition_id": None, "lang": "en", "title": "Allein / Zu Haus", "author": "Autorin A, Zweiter B"}) == ("Standalone - Autorin A", "Allein Zu Haus")
