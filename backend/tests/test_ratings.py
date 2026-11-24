"""Bewertungen gehörter Bücher und ihr Einfluss auf «Für dich»."""
from fastapi.testclient import TestClient

from abw import api, db, foryou, ratings, tags


def _setup():
    db.init()
    c = db.conn()
    for t in ("rating", "user_progress", "book", "edition", "series", "cache"):
        c.execute(f"DELETE FROM {t}")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Serie','serie')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", "Serie", "serie")).lastrowid
    ids = []
    for n in range(1, 4):
        bid = c.execute("INSERT INTO book(edition_id, lang, title, asin, seq, owned) VALUES(?,?,?,?,?,1)",
                        (eid, "en", f"Band {n}", f"B0SERIE00{n}", float(n))).lastrowid
        ids.append(bid)
    for bid in ids[:2]:
        c.execute("INSERT INTO user_progress VALUES(1,?,1,1,'2026-11-19T00:00:00+00:00')", (bid,))
    c.execute("INSERT INTO user_progress VALUES(1,?,0.5,0,'2026-11-19T00:00:00+00:00')", (ids[2],))
    return c, eid, ids


def test_offen_nur_fertig_gehoerte_ohne_bewertung():
    c, eid, ids = _setup()
    client = TestClient(api.app)
    assert client.get("/api/ratings/count").json() == {"open": 2}
    groups = client.get("/api/ratings/open").json()
    assert [b["id"] for b in groups[0]["books"]] == ids[:2] and groups[0]["series"] == "Serie"
    assert client.post(f"/api/ratings/book/{ids[0]}", json={"stars": 5}).status_code == 200
    assert client.post(f"/api/ratings/book/{ids[1]}", json={"stars": None}).status_code == 200  # nicht bewerten
    assert client.get("/api/ratings/count").json() == {"open": 0}
    mine = client.get("/api/ratings/mine").json()
    assert [(b["id"], b["stars"]) for b in mine] == [(ids[0], 5), (ids[1], None)]
    # Halb gehörte und ungültige Sterne werden abgelehnt
    assert client.post(f"/api/ratings/book/{ids[2]}", json={"stars": 4}).status_code == 404
    assert client.post(f"/api/ratings/book/{ids[0]}", json={"stars": 6}).status_code == 400
    # Zurücknehmen: wieder offen
    client.delete(f"/api/ratings/book/{ids[1]}")
    assert client.get("/api/ratings/count").json() == {"open": 1}


def test_ganze_serie_auf_einmal():
    c, eid, ids = _setup()
    assert TestClient(api.app).post(f"/api/ratings/edition/{eid}", json={"stars": 4}).json() == {"rated": 2}
    assert ratings.stars_of(1) == {ids[0]: 4, ids[1]: 4}


def test_sterne_veraendern_das_seriengewicht():
    c, eid, ids = _setup()
    base = foryou.sources(1)[0]["weight"]
    ratings.set_series(1, eid, 5)
    assert foryou.sources(1)[0]["weight"] > base * 1.4
    c.execute("UPDATE rating SET stars=4")
    assert base < foryou.sources(1)[0]["weight"] < base * 1.3
    # 1–2 Sterne: keine Quelle mehr, sondern Gegensignal
    c.execute("UPDATE rating SET stars=1")
    assert foryou.sources(1) == []
    bad = foryou.dropped(1)
    assert bad and bad[0]["disliked"] and bad[0]["weight"] == -1.0


def test_tags_aus_titel_kategorie_und_beschreibung():
    t = tags.detect("Speedrunning the Multiverse", "A LitRPG Cultivation Adventure",
                    [["Science Fiction & Fantasy", "Fantasy", "Epic"], ["Science Fiction & Fantasy", "Science Fiction", "Cyberpunk"]],
                    "<p>Warring sects and martial techniques. He was <b>reborn</b> into another world.</p>")
    assert {"LitRPG", "Cultivation"} <= t["title"]
    assert t["category"] == {"Epic Fantasy", "LitRPG"}  # Cyberpunk neben Fantasy = LitRPG
    assert {"Isekai", "Cultivation"} <= t["summary"]
    # Schwache Wörter lösen nichts aus
    weak = tags.detect("X", None, [], "He laughed at the bloody, brutal galaxy and the new world.")
    assert not (weak["summary"] & {"Humor", "Grimdark", "Space Opera", "Isekai"})
    # Reine Science Fiction mit Cyberpunk bleibt Cyberpunk
    assert tags.detect("Y", None, [["Science Fiction & Fantasy", "Science Fiction", "Cyberpunk"]], "")["category"] == {"Cyberpunk"}


def test_audible_bewertung_geglaettet():
    assert foryou.rating_bonus(None, None) == 0
    few = foryou.rating_bonus(5.0, 5)
    many = foryou.rating_bonus(4.8, 20000)
    assert 0 < few < many <= foryou.RATING_MAX
    assert foryou.rating_bonus(3.5, 5000) < 0


def test_seriengroesse():
    assert foryou.series_bonus(None) == 0 and foryou.series_bonus(1) < 0 < foryou.series_bonus(2) < foryou.series_bonus(3) < foryou.series_bonus(7)


def _item(n, because, score, tags_=(), present=False, rating=4.0, ratings_=10):
    return {"asin": f"A{n}", "because": [because] if because else [], "score": score, "present": present, "tags": list(tags_),
            "rating": rating, "ratings": ratings_, "explore": False}


def test_abwechslung_hoechstens_drei_je_quellserie_und_mal_was_anderes():
    present = [_item(200 + i, "Gross", 9 - i / 10, present=True) for i in range(10)]  # vorhandene stehen vorn
    items = present + [_item(i, "Gross", 7 - i / 10) for i in range(10)] + [_item(10 + i, f"S{i}", 6.5 - i / 10) for i in range(30)]
    items.append(_item(99, "S0", 1, tags_=("Space Opera",), rating=4.8, ratings_=5000))
    out = foryou.diversify(items, {"LitRPG": 0.8})
    fresh = [r for r in out if not r["present"]]
    assert sum(1 for r in fresh[:20] if r["because"] == ["Gross"]) <= 4
    assert fresh[6]["asin"] == "A99" and fresh[6]["explore"]
    assert [r["asin"] for r in out if r["present"]][:3] == ["A200", "A201", "A202"]


def test_bewertung_verschwindet_mit_dem_buch():
    c, eid, ids = _setup()
    ratings.set_rating(1, ids[0], 5)
    c.execute("DELETE FROM book WHERE id=?", (ids[0],))
    assert ratings.stars_of(1) == {}


def _users(c):
    for uid, name in ((1, "Max"), (7, "Karl"), (8, "Jan")):
        c.execute("INSERT OR IGNORE INTO user(id, name, role, hash, created) VALUES(?,?,?,?,?)", (uid, name, "user", "x", "2026-01-01"))
        c.execute("UPDATE user SET name=? WHERE id=?", (name, uid))


def test_serienansicht_zeigt_meine_und_andere_sterne():
    c, eid, ids = _setup()
    _users(c)
    for uid in (7, 8):
        c.execute("INSERT INTO user_progress VALUES(?,?,1,1,'2026-11-19')", (uid, ids[0]))
    ratings.set_rating(1, ids[0], 5)
    ratings.set_rating(7, ids[0], 3)
    ratings.set_rating(8, ids[0], 4)
    d = ratings.by_book(1)[ids[0]]
    assert d["mine"] == 5 and sorted(o["stars"] for o in d["others"]) == [3, 4]
    assert ratings.summary([5, 3, 4], [5]) == {"avg": 4.0, "count": 3, "mine": 5.0}
    sid = c.execute("SELECT series_id FROM edition WHERE id=?", (eid,)).fetchone()[0]
    s = api.public(api.load(1)[sid], True, 1)
    c.execute("DELETE FROM user WHERE id IN (1, 7, 8)")  # sonst wäre für die übrigen Tests die Anmeldung an
    book = next(b for e in s["editions"] for b in e["books"] if b["id"] == ids[0])
    assert book["rating"]["mine"] == 5 and len(book["rating"]["others"]) == 2
    assert s["rating"]["count"] == 3


def test_statistik():
    c, eid, ids = _setup()
    c.execute("UPDATE book SET author='Jane Doe', narrator='Travis Baldree, Jeff Hays'")
    ratings.set_rating(1, ids[0], 5)
    ratings.set_rating(1, ids[1], 4)
    st = TestClient(api.app).get("/api/ratings/stats").json()
    assert st["rated"] == 2 and st["average"] == 4.5 and st["distribution"]["5"] == 1
    assert st["authors"][0] == {"name": "Jane Doe", "avg": 4.5, "count": 2, "id": None}
    assert {n["name"] for n in st["narrators"]} == {"Travis Baldree", "Jeff Hays"}
    assert st["series"][0]["name"] == "Serie"


def test_meldung_nach_dem_fertighoeren(monkeypatch):
    c, eid, ids = _setup()
    c.execute("DELETE FROM notification")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    c.execute("UPDATE user_progress SET finished=1, progress=1, updated=? WHERE book_id=?", (now, ids[2]))
    assert ratings.remind_finished(c, 1, {ids[0], ids[1]}) == 1
    n = c.execute("SELECT kind, link FROM notification WHERE user_id=1").fetchall()
    assert [(r["kind"], r["link"]) for r in n] == [("rate", f"/bewertungen?buch={ids[2]}")]
    assert ratings.remind_finished(c, 1, {ids[0], ids[1]}) == 0  # nur einmal
    assert ratings.remind_finished(c, 1, set()) == 0  # erster Abgleich: nichts
