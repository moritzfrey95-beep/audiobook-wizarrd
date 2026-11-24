from abw import config, db, ebook


def test_epub_im_buchordner(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LIBRARY_ROOT", {"en": tmp_path, "de": tmp_path / "fehlt"})
    (tmp_path / "Serie/Book 1 - A").mkdir(parents=True)
    (tmp_path / "Serie/Book 1 - A/a.m4b").write_bytes(b"x")
    (tmp_path / "Serie/Book 1 - A/A.EPUB").write_bytes(b"x")
    (tmp_path / "Serie/Book 2 - B").mkdir()
    (tmp_path / "Serie/Book 2 - B/b.m4b").write_bytes(b"x")
    db.init()
    c = db.conn()
    c.execute("DELETE FROM book")
    c.execute("INSERT INTO book(id, lang, title, owned, rel_path) VALUES(1,'en','A',1,'Serie/Book 1 - A')")
    c.execute("INSERT INTO book(id, lang, title, owned, rel_path, epub) VALUES(2,'en','B',1,'Serie/Book 2 - B',1)")
    c.execute("INSERT INTO book(id, lang, title, owned, rel_path, epub) VALUES(3,'de','C',1,'X/Y',1)")
    msg = ebook.scan()
    got = {r["id"]: r["epub"] for r in c.execute("SELECT id, epub FROM book")}
    assert got == {1: 1, 2: 0, 3: 1}  # Buch 3: Bibliothek nicht eingebunden, Stand bleibt
    assert msg.startswith("1 von 2")
