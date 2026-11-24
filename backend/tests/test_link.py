"""Sprachfassungen mit anderem Titel: nur die einzige offene Serie des Autors, und nur nach Zusatzprüfung automatisch."""
from abw import audible, db


def _series(c, name, lang, asin, author, n_books=1):
    sid = c.execute("INSERT INTO series(name, norm) VALUES(?,?)", (name, db.norm(name))).lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin) VALUES(?,?,?,?,?)", (sid, lang, name, db.norm(name), asin)).lastrowid
    for i in range(n_books):
        c.execute("INSERT INTO book(edition_id, lang, title, author, owned, asin) VALUES(?,?,?,?,1,?)", (eid, lang, f"{name} {i + 1}", author, f"{asin}-{i}"))
    return sid


def _cat(c, author, lang, asin, title, count, first):
    c.execute("INSERT OR REPLACE INTO author_catalog VALUES(?,?,?,?,?,?,?,?)", (db.norm(author), lang, asin, title, count, None, first, "2026"))


def test_unique_remainder_is_suggested_and_confirmed_by_runtime(monkeypatch):
    db.init()
    c = db.conn()
    sid = _series(c, "Lifesteal Test", "en", "SER-EN-1", "Linktest Autor Eins")
    _cat(c, "Linktest Autor Eins", "en", "SER-EN-1", "Lifesteal Test", 5, "EN1")
    _cat(c, "Linktest Autor Eins", "de", "SER-DE-1", "Lebensraub Test", 3, "DE1")
    r = audible.remainder(sid, "de")
    assert r and r["series_asin"] == "SER-DE-1"
    assert [x["likely"] for x in audible.suggestions(sid, "de")] == [True]
    monkeypatch.setattr(audible, "_runtime", lambda lang, asin: {"EN1": 600, "DE1": 700}[asin])
    assert audible.remainder_confirmed(r)
    monkeypatch.setattr(audible, "_runtime", lambda lang, asin: {"EN1": 600, "DE1": 1500}[asin])
    assert audible.remainder_confirmed(r)  # Ergebnis ist gemerkt
    assert not audible.remainder_confirmed({**r, "book_count": 9})  # mehr Bände als das Original


def test_two_open_series_of_one_author_are_never_guessed():
    db.init()
    c = db.conn()
    sid = _series(c, "Bunny Test", "en", "SER-EN-2", "Linktest Autor Zwei")
    _cat(c, "Linktest Autor Zwei", "en", "SER-EN-2", "Bunny Test", 2, "EN2")
    _cat(c, "Linktest Autor Zwei", "en", "SER-EN-3", "Syl Test", 4, "EN3")
    _cat(c, "Linktest Autor Zwei", "de", "SER-DE-3", "Syl Test (German Edition)", 4, "DE3")
    assert audible.remainder(sid, "de") is None
    assert [x["likely"] for x in audible.suggestions(sid, "de")] == [False]


def test_loose_name_ignores_edition_suffix():
    assert audible._loose("System Universe [German Edition]") == audible._loose("System Universe Series")
    assert audible._loose("Ready Player-Serie") == audible._loose("Ready Player")
    assert audible._loose("Syl (German Edition)") != audible._loose("Bookbound Bunny")
