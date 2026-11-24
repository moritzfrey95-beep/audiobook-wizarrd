from fastapi.testclient import TestClient

from abw import api, audible, db


def test_audible_suche_filtert_sprache_mischt_und_markiert_bestand(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("cache", "watchlist", "book"):
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO book(lang, title, asin, owned) VALUES('en','Monsters 1','B0EN000001',1)")
    calls = []

    def fake(lang, path, **params):
        calls.append((lang, params))
        lng = "english" if lang == "en" else "german"
        return {"products": [
            {"asin": f"B0{lang.upper()}000001", "title": "Monsters 1", "language": lng, "authors": [{"name": "Travis Deverell"}],
             "series": [{"asin": "S1", "title": "Monsters", "sequence": "1"}]},
            {"asin": f"B0{lang.upper()}000002", "title": "Monsters 2", "language": lng, "release_date": "2200-01-01"},
            {"asin": "B0FOREIGN1", "title": "Fremde Sprache", "language": "spanish"},
        ]}

    monkeypatch.setattr(audible, "_get", fake)
    client = TestClient(api.app)
    r = client.get("/api/discover/search", params={"q": "  he who  fights ", "lang": "all"})
    assert r.status_code == 200
    got = r.json()
    assert [(i["asin"], i["lang"]) for i in got] == [("B0EN000001", "en"), ("B0DE000001", "de"), ("B0EN000002", "en"), ("B0DE000002", "de")]
    assert got[0]["in_library"] and not got[1]["in_library"]
    assert calls[0][1]["keywords"] == "he who fights" and calls[0][1]["products_sort_by"] == "Relevance"
    # Zweite gleiche Suche kommt aus dem Zwischenspeicher
    client.get("/api/discover/search", params={"q": "He Who Fights", "lang": "de"})
    assert len([x for x in calls if "keywords" in x[1]]) == 2  # dazu je Sprache einmal der Serienumfang (20.11.)
    assert client.get("/api/discover/search", params={"q": "x", "lang": "en"}).json() == []
    assert client.get("/api/discover/search", params={"q": "abc", "lang": "fr"}).status_code == 404
