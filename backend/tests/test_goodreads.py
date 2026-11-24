"""Goodreads-Genres als zweite Tag-Quelle."""
import json

from abw import config, db, goodreads, tags

SEARCH = json.dumps([
    {"bookId": "1", "title": "Articles on Inheritance Cycle, Including: Eragon", "author": {"name": "Hephaestus Books"}, "ratingsCount": 79},
    {"bookId": "113436", "title": "Eragon (The Inheritance Cycle #1)", "author": {"name": "Christopher Paolini"}, "ratingsCount": 1996686, "avgRating": "3.97"},
    {"bookId": "3", "title": "A Study Guide for Christopher Paolini's Eragon", "author": {"name": "Gale"}, "ratingsCount": 1},
])
PAGE = ('<html><a href="/genres/art">Art</a><script id="__NEXT_DATA__" type="application/json">{"x":1,"bookGenres":['
        '{"genre":{"name":"Fantasy"}},{"genre":{"name":"Young Adult"}},{"genre":{"name":"Dragons"}},{"genre":{"name":"High Fantasy"}}],'
        '"y":2}</script></html>')


def _fake(monkeypatch, calls):
    def fetch(url, session=None, timeout=60):
        calls.append(url)
        return f"<pre>{SEARCH}</pre>" if "auto_complete" in url else PAGE
    monkeypatch.setattr(goodreads.flaresolverr, "fetch", fetch)
    monkeypatch.setattr(config, "FLARESOLVERR_URL", "http://fs")


def test_treffer_ueber_autor_und_genres_aus_seitendaten(monkeypatch):
    calls = []
    _fake(monkeypatch, calls)
    d = goodreads.lookup("Eragon: Inheritance, Book 1", "Christopher Paolini")
    assert d["book_id"] == "113436" and d["ratings"] == 1996686
    assert d["genres"] == ["Fantasy", "Young Adult", "Dragons", "High Fantasy"]  # nicht «Art» aus dem Menü
    assert calls[0].endswith("q=Eragon")  # zuerst nur der Titel


def test_sync_speichert_und_tags_mischen_goodreads_ein(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("cache", "user_progress", "book"):
        c.execute(f"DELETE FROM {t}")
    bid = c.execute("INSERT INTO book(lang, title, author, asin, owned) VALUES('en','Eragon','Christopher Paolini','B0ERAGON01',1)").lastrowid
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19')", (bid,))
    calls = []
    _fake(monkeypatch, calls)
    assert goodreads.wanted() == [("en", "B0ERAGON01", "Eragon", "Christopher Paolini")]
    assert goodreads.sync().startswith("1 Bücher mit Goodreads-Genres")
    assert goodreads.wanted() == []  # einen Monat zwischengespeichert
    assert goodreads.cached("en", ["B0ERAGON01"]) == {"B0ERAGON01": ["Drachen", "Epic Fantasy", "Young Adult"]}
    # Audible-Daten aus dem Zwischenspeicher, damit tags.of_books nichts abfragt
    c.execute("INSERT INTO cache VALUES('desc:en:B0ERAGON01', ?, '2999-01-01')",
              (json.dumps({"title": "Eragon", "subtitle": None, "summary": "", "ladders": []}),))
    assert tags.of_books("en", ["B0ERAGON01"])["B0ERAGON01"] == {"Drachen", "Epic Fantasy", "Young Adult"}


def test_nicht_gefunden_wird_gemerkt(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("cache", "user_progress", "book"):
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(goodreads.flaresolverr, "fetch", lambda url, session=None, timeout=60: "<pre>[]</pre>")
    monkeypatch.setattr(config, "FLARESOLVERR_URL", "http://fs")
    goodreads.remember_wanted([{"lang": "en", "asin": "B0UNKNOWN1", "title": "Unbekannt", "author": "Niemand"}])
    assert goodreads.sync().startswith("0 Bücher mit Goodreads-Genres, 1 nicht gefunden")
    assert goodreads.wanted() == [] and goodreads.cached("en", ["B0UNKNOWN1"]) == {"B0UNKNOWN1": []}


def test_pseudonym_und_richtiger_band():
    res = [{"bookId": "1", "title": "Amber the Cursed Berserker Book 1", "author": {"name": "V.A. Lewis"}, "ratingsCount": 1128},
           {"bookId": "2", "title": "Amber the Cursed Berserker Book 2", "author": {"name": "V.A. Lewis"}, "ratingsCount": 632}]
    assert goodreads._pick(res, "Azrie, V.A. Lewis", "Amber the Cursed Berserker: Book 2")["bookId"] == "2"
    mol = [{"bookId": "9", "title": "Mother of Learning: ARC 3", "author": {"name": "Domagoj Kurmaić"}, "ratingsCount": 9027}]
    assert goodreads._pick(mol, "Nobody103, Domagoj Kurmaic", "Mother of Learning Arc 3")["bookId"] == "9"
    assert goodreads._pick(mol, "Jemand Anderes", "Mother of Learning Arc 3") is None
