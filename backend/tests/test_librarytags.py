"""Filter nach Genre und Tag in der Bibliothek."""
import json

from abw import db, librarytags, tags


def _desc(c, lang, asin, title, ladders, summary=""):
    c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,'2999-01-01')",
              (f"desc:{lang}:{asin}", json.dumps({"title": title, "subtitle": None, "summary": summary, "ladders": ladders})))


def _setup():
    db.init()
    c = db.conn()
    for t in ("cache", "book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    librarytags._memo["value"] = None
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Cradle','cradle')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,'en','Cradle','cradle')", (sid,)).lastrowid
    for i, a in enumerate(["B0CRADLE01", "B0CRADLE02", "B0CRADLE03", "B0CRADLE04"], 1):
        c.execute("INSERT INTO book(edition_id, lang, title, author, asin, seq, owned) VALUES(?,'en',?,'Will Wight',?,?,1)", (eid, f"Band {i}", a, i))
    c.execute("INSERT INTO book(lang, title, author, asin, owned) VALUES('de','Der Hobbit','J.R.R. Tolkien','B0HOBBIT01',1)")
    return c, sid


def test_genres_und_tags_aus_den_ersten_baenden():
    c, sid = _setup()
    fantasy = ["Science Fiction & Fantasy", "Fantasy", "Epic"]
    _desc(c, "en", "B0CRADLE01", "Unsouled", [fantasy], "a cultivation story")
    _desc(c, "en", "B0CRADLE02", "Soulsmith", [fantasy, ["Science Fiction & Fantasy", "Science Fiction"]], "cultivation again")
    _desc(c, "en", "B0CRADLE03", "Blackflame", [fantasy], "he joins an academy")
    _desc(c, "en", "B0CRADLE04", "Skysworn", [["Krimi"]], "")  # vierter Band zählt nicht
    _desc(c, "de", "B0HOBBIT01", "Der Hobbit", [["Literatur & Belletristik", "Belletristik"]])
    out = librarytags.of_library()
    # Science Fiction nur in 1 von 3 Bänden, Academy nur in einer Beschreibung: zählen nicht
    assert out["series"][sid] == {"genres": ["Fantasy"], "tags": ["Cultivation", "Epic Fantasy"]}  # Science-Fiction nur in einem Band
    assert out["singles"]["jrrtolkien"] == {"genres": ["Belletristik"], "tags": []}  # deutsche Kategorie von Audible.de


def test_sync_holt_nur_fehlende(monkeypatch):
    c, _ = _setup()
    _desc(c, "en", "B0CRADLE01", "Unsouled", [])
    calls = []
    monkeypatch.setattr(tags, "fetch", lambda lang, asins: calls.append((lang, list(asins))) or {})
    assert librarytags.sync() == "3 Bücher bei Audible geholt, 0 offen"
    assert sorted(calls) == [("de", ["B0HOBBIT01"]), ("en", ["B0CRADLE02", "B0CRADLE03"])]


def test_ergebnis_wird_gespeichert_und_wieder_gelesen(monkeypatch):
    c, sid = _setup()
    _desc(c, "en", "B0CRADLE01", "Unsouled", [["Science Fiction & Fantasy", "Fantasy"]])
    monkeypatch.setattr(tags, "fetch", lambda lang, asins: {})
    librarytags.sync()
    librarytags._memo["value"] = None  # wie nach einem Neustart
    monkeypatch.setattr(librarytags, "_build", lambda: (_ for _ in ()).throw(AssertionError("rechnet neu")))
    assert librarytags.of_library()["series"][sid]["genres"] == ["Fantasy"]  # Serien-ID wieder als Zahl


def test_englische_und_deutsche_genres_gleich_benannt():
    assert librarytags._genres([["Mystery, Thriller & Suspense", "Mystery"], ["Literatur & Belletristik", "Krimis"]]) == {"Krimi"}
    assert librarytags._genres([["Science Fiction & Fantasy", "Science Fiction"]]) == {"Science-Fiction"}
