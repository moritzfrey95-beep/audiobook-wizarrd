from fastapi.testclient import TestClient

from abw import api, audible, db, foryou


def _prod(asin, title, series=None, seq=None, genres=("Fantasy",), lang="english"):
    p = {"asin": asin, "title": title, "language": lang, "authors": [{"name": "A"}],
         "category_ladders": [{"root": "Genres", "ladder": [{"id": "1", "name": "Science Fiction & Fantasy"}, {"id": "2", "name": g}]} for g in genres]}
    if series:
        p["series"] = [{"asin": series, "title": series, "sequence": seq}]
    return p


def _setup(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("cache", "watchlist", "user_progress", "book", "edition"):
        c.execute(f"DELETE FROM {t}")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Eigene','eigene')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin) VALUES(?,?,?,?,?)", (sid, "en", "Eigene", "eigene", "SOWN")).lastrowid
    for i, (asin, fin, prog) in enumerate([("B0HEARD001", 1, 1.0), ("B0HEARD002", 0, 0.5), ("B0NOTYET01", 0, 0.1)]):
        bid = c.execute("INSERT INTO book(lang, title, asin, owned, edition_id) VALUES('en',?,?,1,?)", (f"Gehört {i}", asin, eid if i == 0 else None)).lastrowid
        c.execute("INSERT INTO user_progress(user_id, book_id, progress, finished, updated) VALUES(1,?,?,?,?)", (bid, prog, fin, f"2026-11-1{i}"))
    sims = {
        "B0HEARD001": [_prod("B0NEW00002", "Neu 2", "SNEW", "2"), _prod("B0OWNSER01", "Eigene 5", "SOWN", "5"), _prod("B0HEARD002", "schon da")],
        "B0HEARD002": [_prod("B0NEW00001", "Neu 1", "SNEW", "1"), _prod("B0SOLO0001", "Einzeln", genres=("Romance",))],
    }
    calls = []

    def fake(lang, path, **params):
        calls.append(path)
        if path.endswith("/sims"):
            return {"similar_products": sims.get(path.split("/")[-2], [])}
        if "asins" in params:  # Genres der gehörten Bücher
            return {"products": [_prod(a, a) for a in params["asins"].split(",")]}
        top = [_prod("B0TOP00001", "Krimi", genres=("Thriller & Suspense",)), _prod("B0TOP00002", "Drache")]
        return {"products": [{**p, "release_date": "2026-01-01"} for p in top]}

    monkeypatch.setattr(audible, "_get", fake)
    return calls


def test_fuer_dich_zaehlt_serien_aus_gehoerten_buechern(monkeypatch):
    calls = _setup(monkeypatch)
    c = db.conn()
    c.execute("UPDATE user_progress SET finished=1, progress=1 WHERE book_id=(SELECT id FROM book WHERE asin='B0HEARD002')")
    out = TestClient(api.app).get("/api/discover/foryou").json()
    assert out["based_on"] == 2  # 10 % angehört zählt nicht
    items = {i["asin"]: i for i in out["items"]}
    # Serie SNEW kam bei beiden gehörten Büchern vor: steht vorn, mit dem frühesten Band
    assert out["items"][0]["asin"] == "B0NEW00001" and out["items"][0]["matches"] == 2
    assert set(out["items"][0]["because"]) == {"Gehört 1", "Eigene"}
    # Bestand und Serien, die schon in ABW sind, kommen mit, aber als vorhanden markiert
    assert items["B0OWNSER01"]["present"] and items["B0HEARD002"]["present"] and items["B0HEARD002"]["in_library"]
    assert not items["B0SOLO0001"]["present"] and not items["B0NEW00001"]["present"]
    assert items["B0HEARD002"]["heard"] and not items["B0NEW00001"]["heard"]
    n = len(calls)
    TestClient(api.app).get("/api/discover/foryou")
    assert len(calls) == n  # zweiter Aufruf aus dem Zwischenspeicher


def test_nur_angefangenes_einzelbuch_zaehlt_nicht(monkeypatch):
    _setup(monkeypatch)
    out = foryou.suggestions(1)
    assert out["based_on"] == 1  # «Gehört 1» ist nur halb gehört und hat keine Serie
    got = {i["asin"] for i in out["items"]}
    assert "B0NEW00002" in got and not got & {"B0NEW00001", "B0SOLO0001"}  # nur Vorschläge aus der gehörten Serie


def _serie(c, name, n_owned, n_finished, n_partial=0, updated="2026-11-19"):
    sid = c.execute("INSERT INTO series(name, norm) VALUES(?,?)", (name, db.norm(name))).lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", name, db.norm(name))).lastrowid
    for i in range(n_owned):
        bid = c.execute("INSERT INTO book(lang, title, asin, owned, edition_id, seq) VALUES('en',?,?,1,?,?)",
                        (f"{name} {i + 1}", f"B0{name[:3].upper()}{i:05d}", eid, i + 1)).lastrowid
        if i < n_finished:
            c.execute("INSERT INTO user_progress VALUES(1,?,1,1,?)", (bid, updated))
        elif i < n_finished + n_partial:
            c.execute("INSERT INTO user_progress VALUES(1,?,0.5,0,?)", (bid, updated))
    return eid


def test_gewicht_je_gehoertem_band_und_ganz_gehoerte_serie_vorn(monkeypatch):
    _setup(monkeypatch)
    c = db.conn()
    c.execute("DELETE FROM user_progress")
    _serie(c, "Angefangen", 5, 0, 1)  # nur ein Band angefangen: zählt nicht
    _serie(c, "Einer", 5, 1)
    _serie(c, "Vier", 8, 4)
    _serie(c, "Ganz", 3, 3)
    w = {s["name"]: s for s in foryou.sources(1)}
    assert "Angefangen" not in w
    assert w["Einer"]["weight"] < w["Vier"]["weight"] < w["Ganz"]["weight"]
    assert w["Ganz"]["complete"] and not w["Vier"]["complete"]
    # zwei angefangene Bände derselben Serie zählen
    _serie(c, "Zwei halb", 4, 0, 2)
    assert "Zwei halb" in {s["name"] for s in foryou.sources(1)}


def test_lange_nicht_gehoert_zaehlt_weniger(monkeypatch):
    _setup(monkeypatch)
    c = db.conn()
    c.execute("DELETE FROM user_progress")
    _serie(c, "Frisch", 5, 2, updated="2026-11-19T00:00:00+00:00")
    _serie(c, "Alt", 5, 2, updated="2024-01-01T00:00:00+00:00")
    w = {s["name"]: s["weight"] for s in foryou.sources(1)}
    assert w["Alt"] < w["Frisch"]


def test_toplisten_tragen_passende_genres(monkeypatch):
    _setup(monkeypatch)
    got = {i["asin"]: i for i in TestClient(api.app).get("/api/discover/bestseller/en").json()}
    assert got["B0TOP00002"]["fits"] and got["B0TOP00002"]["fit_genres"] == ["Fantasy"]
    assert not got["B0TOP00001"]["fits"]


def test_ohne_hoerverlauf_keine_vorschlaege(monkeypatch):
    _setup(monkeypatch)
    db.conn().execute("DELETE FROM user_progress")
    assert foryou.suggestions(1) == {"based_on": 0, "complete": 0, "items": []}
    assert foryou.profile(1) == {}


def test_gehoerte_titel_sind_markiert(monkeypatch):
    _setup(monkeypatch)
    got = {i["asin"]: i for i in TestClient(api.app).get("/api/discover/bestseller/en").json()}
    assert not got["B0TOP00002"]["heard"]
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, asin, owned) VALUES('en','Drache: Eine Geschichte','A','B0ANDERE01',1)").lastrowid
    c.execute("INSERT INTO user_progress(user_id, book_id, progress, finished, updated) VALUES(1,?,1,1,'2026-11-13')", (bid,))
    got = {i["asin"]: i for i in TestClient(api.app).get("/api/discover/bestseller/en").json()}
    assert got["B0TOP00002"]["heard"]  # andere Ausgabe, gleicher Titel und Autor
    assert not got["B0TOP00001"]["heard"]


def test_abgebrochene_serie_zaehlt_weniger_und_zieht_aehnliches_runter(monkeypatch):
    _setup(monkeypatch)
    c = db.conn()
    c.execute("DELETE FROM user_progress")
    _serie(c, "Laufend", 5, 2, updated="2026-11-19T00:00:00+00:00")
    eid = _serie(c, "Liegen", 5, 2, updated="2026-11-19T00:00:00+00:00")
    w0 = {s["name"]: s["weight"] for s in foryou.sources(1)}
    # Band 3 seit Monaten bei 20 %: abgebrochen
    bid = c.execute("SELECT id FROM book WHERE edition_id=? AND seq=3", (eid,)).fetchone()[0]
    c.execute("INSERT INTO user_progress VALUES(1,?,0.2,0,'2026-01-01T00:00:00+00:00')", (bid,))
    c.execute("UPDATE user_progress SET updated='2025-12-01T00:00:00+00:00' WHERE book_id IN (SELECT id FROM book WHERE edition_id=?) AND finished=1", (eid,))
    w = {s["name"]: s["weight"] for s in foryou.sources(1)}
    assert w["Liegen"] < w0["Liegen"] * 0.7
    # Nur angefangen und abgebrochen: Gegensignal, aber keine Quelle
    _serie(c, "Weg", 3, 0, 1, updated="2026-01-01T00:00:00+00:00")
    c.execute("UPDATE user_progress SET progress=0.2 WHERE finished=0 AND book_id IN (SELECT id FROM book WHERE title LIKE 'Weg%')")
    assert "Weg" not in {s["name"] for s in foryou.sources(1)}
    assert [d["name"] for d in foryou.dropped(1)] == ["Weg"] and foryou.dropped(1)[0]["weight"] < 0


def test_hoerer_mit_aehnlichem_geschmack(monkeypatch):
    _setup(monkeypatch)
    c = db.conn()
    c.execute("DELETE FROM user_progress")
    for name in ("Aaa", "Bbb", "Ccc", "Ddd"):
        _serie(c, name, 3, 0)
    ids = {r["name"]: r["id"] for r in c.execute("SELECT id, name FROM edition")}
    books = {name: [r[0] for r in c.execute("SELECT id FROM book WHERE edition_id=? ORDER BY seq", (ids[name],))] for name in ids}

    def hoert(user, name, n):
        for bid in books[name][:n]:
            c.execute("INSERT INTO user_progress VALUES(?,?,1,1,'2026-11-19')", (user, bid))
    hoert(1, "Aaa", 2); hoert(1, "Bbb", 1)
    hoert(7, "Aaa", 3); hoert(7, "Bbb", 1); hoert(7, "Ccc", 3)  # teilt A und B mit mir, hat C ganz gehört
    hoert(8, "Ddd", 3)  # teilt nichts
    got = foryou.peers(1)
    assert set(got) == {ids["Ccc"]} and got[ids["Ccc"]] > 0
    out = {i["asin"]: i for i in foryou.suggestions(1)["items"]}
    first_c = c.execute("SELECT asin FROM book WHERE edition_id=? AND seq=1", (ids["Ccc"],)).fetchone()[0]
    assert out[first_c]["peer"] and out[first_c]["present"]


def test_bekannte_autoren_und_sprecher_bekommen_zuschlag(monkeypatch):
    _setup(monkeypatch)
    c = db.conn()
    c.execute("UPDATE book SET author='Jane Doe', narrator='Travis Baldree' WHERE asin='B0HEARD001'")
    srcs = foryou.sources(1)
    authors, narrators = foryou.known_people(srcs)
    b, author, narr = foryou.people_bonus({"author": "Jane Doe", "narrator": "Travis Baldree, Someone Else"}, authors, narrators)
    assert author and narr == ["Travis Baldree"] and b == foryou.AUTHOR_BONUS + foryou.NARRATOR_BONUS
    assert foryou.people_bonus({"author": "X", "narrator": "Y"}, authors, narrators) == (0, False, [])


def test_bandzahl_ohne_zuschlag(monkeypatch):
    """Serienkarten (20.11.): weitere Serien bekommen ihre Bandzahl, ohne dass sich die Punktzahl ändert."""
    monkeypatch.setattr(foryou, "series_info", lambda lang, asin: {"books": 7, "min": 600})
    a = {"lang": "en", "series_asin": "S1", "series_books": None, "score": 1.0}
    b = {"lang": "en", "series_asin": "S2", "series_books": None, "score": 1.0}
    single = {"lang": "en", "series_asin": None, "series_books": None, "score": 1.0}
    foryou.add_series_size([a])
    foryou.add_series_size([a, b, single], bonus=False)
    assert a["series_books"] == 7 and a["score"] == 1.0 + foryou.series_bonus(7)
    assert b["series_books"] == 7 and b["score"] == 1.0
    assert single["series_books"] is None
    assert a["series_min"] == 600 and b["series_min"] == 600


def test_serienumfang_mit_laufzeit(monkeypatch):
    """Bandzahl und Gesamtlaufzeit (20.11.): Sammelbände zählen nicht, je Bandnummer nur eine Ausgabe."""
    rel = [{"relationship_to_product": "child", "asin": a, "sequence": s}
           for a, s in [("A1", "1"), ("A1b", "1"), ("A2", "2"), ("A13", "1-3"), ("A3", "3")]]
    monkeypatch.setattr(foryou.audible, "_get", lambda lang, path, **kw: {"product": {"relationships": rel}})
    asked = []

    def products(lang, asins):
        asked.extend(asins)
        return [{"asin": a, "runtime_length_min": {"A1": 600, "A2": 720}.get(a)} for a in asins]
    monkeypatch.setattr(foryou.audible, "products", products)
    assert foryou.series_info("en", "S9") == {"books": 3, "min": 1320}
    assert sorted(asked) == ["A1", "A2", "A3"]
    assert foryou.series_size("en", "S9") == 3  # zwischengespeichert
    assert len(asked) == 3
