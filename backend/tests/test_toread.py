"""«To Read»: Liste pro Benutzer, nächster Band, automatisch einreihen, fertig gehört fällt weg."""
from fastapi.testclient import TestClient

from abw import api, db, toread


def _setup():
    db.init()
    c = db.conn()
    for t in ("toread", "toread_skip", "toread_pending", "toread_pick_dismiss", "rating", "user_progress", "download", "book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Serie','serie')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, monitored) VALUES(?,?,?,?,1)", (sid, "en", "Serie", "serie")).lastrowid
    ids = []
    for n in range(1, 5):
        owned = 1 if n < 4 else 0  # Band 4 fehlt noch
        ids.append(c.execute("INSERT INTO book(edition_id, lang, title, asin, sequence, seq, owned, abs_item_id, runtime_min, release_date)"
                             " VALUES(?,?,?,?,?,?,?,?,?,?)", (eid, "en", f"Band {n}", f"B0SERIE00{n}", str(n), float(n), owned,
                                                            f"item{n}" if owned else None, 600, "2020-01-01")).lastrowid)
    # Sammelband zählt nicht als nächster Band
    c.execute("INSERT INTO book(edition_id, lang, title, asin, sequence, seq, owned) VALUES(?,?,?,?,?,?,1)",
              (eid, "en", "Box 1-3", "B0SERIEBOX", "1-3", None))
    single = c.execute("INSERT INTO book(edition_id, lang, title, asin, owned, abs_item_id) VALUES(NULL,'en','Einzel','B0EINZEL01',1,'itemS')").lastrowid
    return c, eid, ids, single


def test_serie_zeigt_naechsten_band_und_wandert_weiter():
    c, eid, ids, single = _setup()
    client = TestClient(api.app)
    assert client.post("/api/toread", json={"kind": "edition", "id": eid}).json() == {"added": True}
    assert client.post("/api/toread", json={"kind": "book", "id": single}).json() == {"added": True}
    o = client.get("/api/toread").json()
    assert [x["book"]["id"] for x in o["next"]] == [ids[0], single] and o["next"][0]["left"] == 4 and o["hours"] == 10
    # Band 1 und 2 fertig gehört: Eintrag zeigt Band 3
    for bid in ids[:2]:
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (bid,))
    assert toread.overview(1)["next"][0]["book"]["id"] == ids[2]
    # Band 3 angefangen: Serie bleibt eine Zeile, Fortschritt über die ganze Serie nach Stunden (4 Bände à 10 h)
    c.execute("INSERT INTO user_progress VALUES(1,?,0.4,0,'2026-11-20T00:00:00+00:00')", (ids[2],))
    o = toread.overview(1)
    x = o["next"][0]
    assert x["book"]["id"] == ids[2] and x["stats"] == {"done": 2, "total": 4, "pct": 60, "hours_done": 24, "hours_total": 40, "min_left": 960}
    assert x["author"] is None or isinstance(x["author"], str)
    # Band 3 fertig: Band 4 fehlt noch, also «Wartet»
    c.execute("UPDATE user_progress SET finished=1, progress=1 WHERE book_id=?", (ids[2],))
    o = toread.overview(1)
    assert [x["book"]["id"] for x in o["waiting"]] == [ids[3]] and o["waiting"][0]["book"]["state"] != "owned"
    # Einzelbuch fertig gehört: fällt weg
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-20T00:00:00+00:00')", (single,))
    o = toread.overview(1)
    assert o["next"] == [] and toread.keys(1) == [f"e{eid}"]


def test_reihenfolge():
    c, eid, ids, single = _setup()
    toread.add(1, "edition", eid)
    toread.add(1, "book", single)
    client = TestClient(api.app)
    assert client.put("/api/toread/order", json={"keys": [f"b{single}", "x9"]}).status_code == 200
    assert [x["key"] for x in toread.overview(1)["next"]] == [f"b{single}", f"e{eid}"]


def test_fertig_gehoert_reiht_serie_oben_ein_ausser_entfernt():
    c, eid, ids, single = _setup()
    toread.add(1, "book", single)
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
    assert toread.on_finished(c, 1, {ids[0]}) == 1
    o = toread.overview(1)
    assert o["next"][0]["key"] == f"e{eid}" and o["next"][0]["source"] == "auto" and o["next"][0]["book"]["id"] == ids[1]
    # Von Hand entfernt: kommt beim nächsten Fertighören nicht zurück
    TestClient(api.app).delete(f"/api/toread/edition/{eid}")
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[1],))
    assert toread.on_finished(c, 1, {ids[1]}) == 0 and toread.keys(1) == [f"b{single}"]
    # Von Hand wieder hinzugefügt: Sperre weg
    toread.add(1, "edition", eid)
    assert not c.execute("SELECT 1 FROM toread_skip").fetchone()


def test_letzter_band_gehoert_keine_automatik():
    c, eid, ids, single = _setup()
    c.execute("DELETE FROM book WHERE id=?", (ids[3],))
    for bid in ids[:3]:
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (bid,))
    assert toread.on_finished(c, 1, {ids[2]}) == 0


def test_unbekannt_und_falsche_art():
    _setup()
    client = TestClient(api.app)
    assert client.post("/api/toread", json={"kind": "edition", "id": 999999}).status_code == 404
    assert client.post("/api/toread", json={"kind": "foo", "id": 1}).status_code == 400


def test_benutzerrechte():
    assert api.USER_READ.match("/api/toread") and api.USER_READ.match("/api/toread/keys")
    assert api.USER_WRITE.match("/api/toread") and api.USER_WRITE.match("/api/toread/order")
    assert api.USER_WRITE.match("/api/toread/edition/5") and api.USER_WRITE.match("/api/toread/book/7")


def test_zuletzt_gehoerte_serie_zuoberst():
    c, eid, ids, single = _setup()
    toread.add(1, "book", single)
    toread.add(1, "edition", eid)
    o = toread.overview(1)
    assert [x["key"] for x in o["next"]] == [f"b{single}", f"e{eid}"] and not any(x["pinned"] for x in o["next"])
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-20T00:00:00+00:00')", (ids[0],))
    o = toread.overview(1)
    assert [x["key"] for x in o["next"]] == [f"e{eid}", f"b{single}"] and o["next"][0]["pinned"]
    assert "continue" not in o


def test_entdecken_bekannter_titel_setzt_serie():
    c, eid, ids, single = _setup()
    c.execute("UPDATE edition SET audible_asin='B0SERIENAS' WHERE id=?", (eid,))
    client = TestClient(api.app)
    r = client.post("/api/toread/discover", json={"asin": "B0SERIE002", "lang": "en", "title": "Band 2"}).json()
    assert r == {"ok": True, "key": f"e{eid}", "library": False}
    assert client.post("/api/toread/discover", json={"asin": "B0SERIENAS", "lang": "en", "kind": "series"}).json()["key"] == f"e{eid}"
    assert client.post("/api/toread/discover", json={"asin": "B0EINZEL01", "lang": "en"}).json()["key"] == f"b{single}"
    assert toread.keys(1) == [f"e{eid}", f"b{single}"]
    assert {"B0SERIE001", "B0SERIENAS", "B0EINZEL01"} <= set(client.get("/api/toread/asins").json())


def test_entdecken_neuer_titel_wird_aufgenommen(monkeypatch):
    from abw import library
    c, eid, ids, single = _setup()
    c.execute("DELETE FROM watchlist")

    def fake_process(asin):  # Audible-Aufnahme: legt das Buch in die vorhandene Serie
        c.execute("INSERT INTO book(edition_id, lang, title, asin, seq, owned) VALUES(?,?,?,?,?,0)", (eid, "en", "Band 5", asin, 5.0))
        c.execute("UPDATE watchlist SET state='done' WHERE asin=?", (asin,))
        return {"series_id": None, "book_id": None}
    monkeypatch.setattr(library, "process", fake_process)
    monkeypatch.setattr(library, "kick", lambda *a: None)
    r = toread.from_discover(1, {"asin": "B0NEU00005", "lang": "en", "title": "Band 5"})
    assert r == {"ok": True, "key": f"e{eid}", "library": True} and not c.execute("SELECT 1 FROM toread_pending").fetchone()


def test_entdecken_audible_weg_wird_nachgeholt(monkeypatch):
    from abw import library
    c, eid, ids, single = _setup()
    c.execute("DELETE FROM watchlist")
    monkeypatch.setattr(library, "process", lambda asin: (_ for _ in ()).throw(ConnectionError("503")))
    r = toread.from_discover(1, {"asin": "B0NEU00006", "lang": "en", "title": "Band 6"})
    assert r["pending"] and toread.keys(1) == [] and "B0NEU00006" in toread.asins(1)
    # Später aufgenommen: «Hinzugefügtes aufnehmen» ruft process, das löst die Vormerkung ein
    c.execute("INSERT INTO book(edition_id, lang, title, asin, seq, owned) VALUES(?,?,?,?,?,0)", (eid, "en", "Band 6", "B0NEU00006", 6.0))
    toread.after_process("B0NEU00006", "en", "book")
    assert toread.keys(1) == [f"e{eid}"] and not c.execute("SELECT 1 FROM toread_pending").fetchone()


def test_entdecken_rechte():
    assert api.USER_WRITE.match("/api/toread/discover") and api.USER_READ.match("/api/toread/asins")


def _users(c):
    c.execute("DELETE FROM user")
    c.execute("DELETE FROM notification")
    c.execute("DELETE FROM toread_rec")
    c.execute("DELETE FROM toread_dismiss")
    for uid, name in ((1, "Max"), (2, "Ada")):
        c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(?,?,'x','admin','2026-01-01')", (uid, name))


def test_weiterempfehlen_und_entscheiden():
    c, eid, ids, single = _setup()
    _users(c)
    try:
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-20T00:00:00+00:00')", (single,))
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-18T00:00:00+00:00')", (ids[1],))
        h = toread.heard(1)  # eine Zeile je Serie, zuletzt gehörter Band bestimmt Platz und Buch
        assert [(x["id"], x["count"]) for x in h] == [(single, 1), (ids[0], 2)] and h[1]["key"] == f"e{eid}"
        assert toread.users(1) == [{"id": 2, "name": "Ada"}]
        assert toread.recommend(1, ids[0], [2, 2, 1, 99]) == 1  # nur gültige andere Benutzer, je einmal
        assert toread.heard(1)[1]["sent_to"] == ["Ada"]
        assert c.execute("SELECT COUNT(*) FROM notification WHERE user_id=2 AND kind='recommend'").fetchone()[0] == 1
        recs = toread.overview(2)["recommended"]
        assert [(x["key"], x["from"]) for x in recs] == [(f"e{eid}", ["Max"])] and recs[0]["book"]["id"] == ids[0]
        toread.add(2, "book", single)
        toread.decide_rec(2, f"e{eid}", "top")
        o = toread.overview(2)
        assert o["recommended"] == [] and [x["key"] for x in o["next"]] == [f"e{eid}", f"b{single}"]
        # Ablehnen blendet aus
        assert toread.recommend(1, single, [2]) == 0  # steht auf Ada' Liste: nicht empfehlen
        toread.remove(2, "book", single)
        assert toread.recommend(1, single, [2]) == 1
        assert [x["key"] for x in toread.overview(2)["recommended"]] == [f"b{single}"]
        toread.decide_rec(2, f"b{single}", "no")
        assert toread.overview(2)["recommended"] == []
    finally:
        c.execute("DELETE FROM user")


def test_vorschlaege_hoch_bewertet_oder_fast_fertig():
    c, eid, ids, single = _setup()
    c.execute("DELETE FROM toread_dismiss")
    c.execute("UPDATE book SET owned=1, abs_item_id='item4' WHERE id=?", (ids[3],))
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
    assert toread.suggestions(1) == []  # 1 von 4 gehört, nicht bewertet
    # Hoch bewertet
    c.execute("INSERT INTO rating VALUES(1,?,5,'2026-11-19')", (ids[0],))
    s = toread.suggestions(1)
    assert [x["key"] for x in s] == [f"e{eid}"] and s[0]["why"] == ["5,0 Sterne"] and s[0]["book"]["id"] == ids[1]
    # 3 von 4 gehört (75 %) und nur 3 Sterne: kein Vorschlag
    c.execute("UPDATE rating SET stars=3")
    for bid in ids[1:3]:
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (bid,))
    assert toread.suggestions(1) == []
    # Neuer Band 5 erschienen, 4 von 5 gehört (80 %): Vorschlag mit Band 5
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-20T00:00:00+00:00')", (ids[3],))
    b5 = c.execute("INSERT INTO book(edition_id, lang, title, asin, sequence, seq, owned, abs_item_id, runtime_min) VALUES(?,?,?,?,?,?,1,'item5',600)",
                   (eid, "en", "Band 5", "B0SERIE005", "5", 5.0)).lastrowid
    s = toread.suggestions(1)
    assert s[0]["book"]["id"] == b5 and s[0]["why"] == ["4 von 5 Bänden gehört"]
    # Nicht auf die Liste: weg, bis ein anderer Band der nächste ist
    toread.decide_suggestion(1, eid, "no")
    assert toread.suggestions(1) == []
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-20T00:00:00+00:00')", (b5,))
    c.execute("INSERT INTO book(edition_id, lang, title, asin, sequence, seq, owned, abs_item_id) VALUES(?,?,?,?,?,?,1,'item6')",
              (eid, "en", "Band 6", "B0SERIE006", "6", 6.0))
    assert [x["book"]["title"] for x in toread.suggestions(1)] == ["Band 6"]
    # Oben auf die Liste: steht dann nicht mehr bei den Vorschlägen
    toread.decide_suggestion(1, eid, "top")
    o = toread.overview(1)
    assert o["suggested"] == [] and o["next"][0]["key"] == f"e{eid}"


def test_rechte_weiterempfehlen():
    for p in ("/api/toread/heard", "/api/toread/users"):
        assert api.USER_READ.match(p)
    for p in ("/api/toread/recommend", "/api/toread/rec/e5", "/api/toread/rec/b7", "/api/toread/suggest/5"):
        assert api.USER_WRITE.match(p)


def test_benutzer_empfehlen_nur_dem_admin():
    c, eid, ids, single = _setup()
    _users(c)
    try:
        c.execute("UPDATE user SET role='user' WHERE id=2")
        c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(3,'Karl','x','user','2026-01-01')")
        c.execute("INSERT INTO user_progress VALUES(2,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
        assert toread.users(2) == [{"id": 1, "name": "Max"}]
        assert [u["name"] for u in toread.users(1)] == ["Ada", "Karl"]
        assert toread.recommend(2, ids[0], [3, 1]) == 1  # an Karl nicht erlaubt
        assert [r[0] for r in c.execute("SELECT to_user FROM toread_rec WHERE from_user=2")] == [1]
    finally:
        c.execute("DELETE FROM user")


def test_ungefaehr_fertig_nach_hoertempo():
    from datetime import datetime, timedelta, timezone
    today = datetime.now(timezone.utc).date()  # wie toread: UTC, sonst schlägt der Test zwischen 0 und 1 Uhr fehl
    c, eid, ids, single = _setup()
    _users(c)
    try:
        c.execute("UPDATE user SET abs_user_id='absM' WHERE id=1")
        c.execute("DELETE FROM listen_session")
        c.execute("INSERT INTO listen_session(id, abs_user_id, day, started, updated, seconds) VALUES('s1','absM',?,?,?,?)",
                  (today.isoformat(), "x", "x", 30 * 2 * 3600))  # 2 h pro Tag über 30 Tage
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
        toread.add(1, "edition", eid)   # Rest 3 Bände à 10 h = 30 h -> 15 Tage
        toread.add(1, "book", single)   # ohne Laufzeit: gleiches Datum
        o = toread.overview(1)
        assert o["pace_h"] == 2.0
        first = next(x for x in o["next"] if x["key"] == f"e{eid}")
        assert first["eta"] == (today + timedelta(days=15)).isoformat()
        c.execute("DELETE FROM listen_session")
        assert toread.overview(1)["next"][0]["eta"] is None and toread.overview(1)["pace_h"] is None
    finally:
        c.execute("DELETE FROM user")
        c.execute("DELETE FROM listen_session")


def test_empfehlen_nicht_an_wer_gehoert_oder_auf_liste():
    c, eid, ids, single = _setup()
    _users(c)
    try:
        c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(3,'Karl','x','user','2026-01-01')")
        c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(4,'Jan','x','user','2026-01-01')")
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
        # Ada hat Band 2 angefangen (zählt als gehört), Karl hat die Serie auf der Liste, Jan ist frei
        c.execute("INSERT INTO user_progress VALUES(2,?,0.3,0,'2026-11-19T00:00:00+00:00')", (ids[1],))
        toread.add(3, "edition", eid)
        row = toread.heard(1)[0]
        assert {b["name"]: b["why"] for b in row["blocked"]} == {"Ada": "heard", "Karl": "listed"}
        assert toread.recommend(1, ids[0], [2, 3, 4]) == 1
        assert [r[0] for r in c.execute("SELECT to_user FROM toread_rec")] == [4]
    finally:
        c.execute("DELETE FROM user")


def test_drei_vorschlaege_aus_entdecken(monkeypatch):
    from abw import foryou
    c, eid, ids, single = _setup()
    c.execute("UPDATE edition SET audible_asin='B0SERIENAS' WHERE id=?", (eid,))
    c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))

    def it(asin, tag, explore=False, series_asin=None):
        return {"asin": asin, "lang": "en", "title": asin, "author": "A", "series": "S" + asin, "series_asin": series_asin or "S" + asin,
                "cover_url": None, "tags": [tag], "explore": explore, "because": ["X"], "rating": 4.5}
    items = [it("A1", "LitRPG"), it("A2", "LitRPG"), it("H1", "Krimi", series_asin="B0SERIENAS"),  # gleiches Genre, gehörte Serie
             it("A3", "Isekai"), it("A4", "Cultivation"), it("E1", "Romantasy", explore=True)]
    monkeypatch.setattr(foryou, "suggestions", lambda uid: {"items": items})
    p = toread.picks(1)
    assert [x["asin"] for x in p] == ["A1", "A3", "E1"] and p[-1]["explore"] and len({x["genre"] for x in p}) == 3
    toread.decide_pick(1, {"asin": "A1", "series_asin": "SA1"}, "no")
    assert [x["asin"] for x in toread.picks(1)] == ["A2", "A3", "E1"]  # nächster LitRPG rückt nach


def test_empfehlungsrechte_einstellbar():
    c, eid, ids, single = _setup()
    _users(c)
    try:
        c.execute("UPDATE user SET role='user' WHERE id=2")
        c.execute("INSERT INTO user(id, name, hash, role, created) VALUES(3,'Karl','x','user','2026-01-01')")
        from abw import auth
        client = TestClient(api.app, cookies={"abw_session": auth.open_session(1)})
        assert client.get("/api/recommend/rules").json()["mode"] == "admins"
        # alle an alle
        assert client.put("/api/recommend/rules", json={"mode": "all"}).status_code == 200
        assert [u["name"] for u in toread.users(2)] == ["Karl", "Max"]
        # nach Tabelle: Ada nur an Karl, Max an niemanden; Paare mit sich selbst oder unbekannten Benutzern fallen weg
        r = client.put("/api/recommend/rules", json={"mode": "custom", "allow": [[2, 3], [2, 2], [2, 99]]}).json()
        assert r["allow"] == [[2, 3]]
        assert toread.users(2) == [{"id": 3, "name": "Karl"}] and toread.users(1) == []
        c.execute("INSERT INTO user_progress VALUES(2,?,1,1,'2026-11-19T00:00:00+00:00')", (ids[0],))
        assert toread.recommend(2, ids[0], [1, 3]) == 1
        # aus: niemand
        client.put("/api/recommend/rules", json={"mode": "off"})
        assert toread.users(1) == [] and toread.users(2) == []
        assert client.put("/api/recommend/rules", json={"mode": "xyz"}).status_code == 400
        assert not api.USER_READ.match("/api/recommend/rules")
    finally:
        c.execute("DELETE FROM user")
        c.execute("DELETE FROM rec_allow")
        c.execute("DELETE FROM session")
        c.execute("DELETE FROM setting WHERE key='rec_mode'")
