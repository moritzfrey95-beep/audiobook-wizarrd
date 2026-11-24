"""Nutzer aus Audiobookshelf: übernehmen und verknüpfen, Anmeldung mit dem Audiobookshelf-Passwort,
Hörfortschritt pro Benutzer, gehörte Bücher markieren Serie und Autor."""
import os

os.environ["ABW_NO_TASKS"] = "1"

import pytest
from fastapi.testclient import TestClient

from abw import abs as abs_mod
from abw import auth, db, tasks
from abw.api import app

ABS = [
    {"id": "a-root", "username": "Max", "type": "root", "isActive": True, "last_seen": None, "own": True},
    {"id": "a-bea", "username": "Bea", "type": "user", "isActive": True, "last_seen": None, "own": False},
    {"id": "a-gast", "username": "gast", "type": "user", "isActive": True, "last_seen": None, "own": False},
    {"id": "a-alt", "username": "Alt", "type": "user", "isActive": False, "last_seen": None, "own": False},
]
PASS = {"a-bea": "beas abs passwort", "a-root": "max abs passwort", "a-gast": "gast abs passwort"}
TABLES = ("user", "session", "follow", "notification", "user_progress", "auto_follow", "book", "edition", "series")


class FakeAbs:
    """Antwortet wie Audiobookshelf auf /api/me, /api/users und /api/users/<id>."""
    progress: dict = {}
    users = ABS

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path, **kw):
        if path == "/api/me":
            data = {"id": "a-root", "mediaProgress": self.progress.get("a-root", [])}
        elif path == "/api/users":
            data = {"users": [u for u in self.users]}
        else:
            data = {"mediaProgress": self.progress.get(path.rsplit("/", 1)[1], [])}
        return type("R", (), {"raise_for_status": lambda s: s, "json": lambda s: data})()


def entry(item, progress, finished=False):
    return {"libraryItemId": item, "episodeId": None, "progress": progress, "isFinished": finished, "lastUpdate": 1794000000000}


@pytest.fixture
def admin(monkeypatch):
    db.init()
    c = db.conn()
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")
    auth._fails.clear()
    FakeAbs.progress, FakeAbs.users = {}, ABS
    monkeypatch.setattr(abs_mod, "users", lambda: [dict(u) for u in FakeAbs.users])
    monkeypatch.setattr(abs_mod, "_client", lambda: FakeAbs())
    monkeypatch.setattr(abs_mod, "login_ok", lambda name, pw, uid, keep=None: PASS.get(uid) == pw and any(u["id"] == uid and u["username"] == name for u in ABS))
    monkeypatch.setattr(tasks, "request", lambda key: None)
    c.execute("INSERT INTO series(id, name, norm) VALUES(1,'Cradle','cradle'),(2,'Dune','dune')")
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm) VALUES(1,1,'en','Cradle','cradle'),(2,2,'en','Dune','dune')")
    c.execute("INSERT INTO book(id, edition_id, lang, title, author, sequence, seq, abs_item_id, owned, runtime_min) VALUES"
              "(1,1,'en','Unsouled','Will Wight','1',1,'it-1',1,600),(2,1,'en','Soulsmith','Will Wight','2',2,'it-2',1,600),"
              "(3,2,'en','Dune','Frank Herbert','1',1,'it-3',1,1200),(4,NULL,'en','Solo','Jane Doe',NULL,NULL,'it-4',1,300)")
    with TestClient(app) as a:
        assert a.post("/api/auth/password", json={"user": "Chef", "password": "ein langes passwort"}).status_code == 200
        yield a
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")


def login(name, password):
    c = TestClient(app)
    return c, c.post("/api/auth/login", json={"user": name, "password": password})


def test_liste_und_alle_importieren(admin):
    assert admin.post("/api/users", json={"name": "Gast", "password": "lokales passwort"}).status_code == 200
    rows = {a["username"]: a for a in admin.get("/api/abs/users").json()}
    assert rows["Max"]["skip_all"] and not rows["Bea"]["linked"] and rows["gast"]["same_name"]["name"] == "Gast"
    r = admin.post("/api/abs/users/import", json={}).json()
    # Bea neu, «gast» mit dem bestehenden Benutzer verknüpft; das eigene Konto und das gesperrte bleiben aussen vor
    assert r == {"created": ["Bea"], "linked": ["gast"], "errors": []}
    users = {u["name"]: u for u in admin.get("/api/users").json()}
    assert set(users) == {"Chef", "Bea", "Gast"}
    assert users["Bea"]["role"] == "user" and users["Bea"]["abs_name"] == "Bea" and not users["Bea"]["has_password"]
    assert users["Gast"]["abs_name"] == "gast" and users["Gast"]["has_password"]
    # Zweiter Lauf legt nichts doppelt an
    assert admin.post("/api/abs/users/import", json={}).json() == {"created": [], "linked": [], "errors": []}


def test_anmeldung_mit_audiobookshelf_passwort(admin):
    admin.post("/api/abs/users/import", json={"ids": ["a-bea"]})
    assert login("Bea", "falsch und lang genug")[1].status_code == 401
    c, r = login("bea", PASS["a-bea"])
    assert r.status_code == 200 and c.get("/api/auth/state").json()["role"] == "user"
    me = c.get("/api/me").json()
    assert me["abs_name"] == "Bea" and me["has_password"] is False
    # Kein eigenes ABW-Passwort: geändert wird in Audiobookshelf
    assert c.post("/api/auth/password", json={"password": "ein neues passwort", "current": PASS["a-bea"]}).status_code == 400
    # Benutzer dürfen die Nutzerübernahme nicht bedienen
    assert c.get("/api/abs/users").status_code == 403 and c.post("/api/abs/users/import", json={}).status_code == 403
    assert c.post("/api/users/1/abs", json={"abs_user_id": "a-bea"}).status_code == 403


def test_verknuepfen_beide_passwoerter_und_trennen(admin):
    assert admin.post("/api/users/1/abs", json={"abs_user_id": "a-root"}).status_code == 200
    assert login("Chef", "ein langes passwort")[1].status_code == 200      # ABW-Passwort gilt weiter
    assert login("Chef", PASS["a-root"])[1].status_code == 200            # Audiobookshelf-Passwort auch
    c, r = login("Max", PASS["a-root"])                                  # und der Name aus Audiobookshelf
    assert r.status_code == 200 and c.get("/api/auth/state").json()["user"] == "Chef"
    assert login("Max", PASS["a-bea"])[1].status_code == 401
    # Dasselbe Konto lässt sich nicht zweimal verknüpfen
    uid = admin.post("/api/users", json={"name": "zwei", "password": "lokales passwort"}).json()["id"]
    assert admin.post(f"/api/users/{uid}/abs", json={"abs_user_id": "a-root"}).status_code == 409
    assert admin.post("/api/users/1/abs", json={}).status_code == 200
    assert login("Chef", PASS["a-root"])[1].status_code == 401
    # Ohne eigenes Passwort lässt sich nicht trennen (sonst wäre der Zugang weg)
    admin.post("/api/abs/users/import", json={"ids": ["a-bea"]})
    bea = next(u["id"] for u in admin.get("/api/users").json() if u["name"] == "Bea")
    assert admin.post(f"/api/users/{bea}/abs", json={}).status_code == 409


def test_audiobookshelf_nicht_erreichbar(admin, monkeypatch):
    admin.post("/api/abs/users/import", json={"ids": ["a-bea"]})
    def down(*a, **kw):
        raise ConnectionError("Audiobookshelf ist gerade nicht erreichbar")
    monkeypatch.setattr(abs_mod, "login_ok", down)
    assert login("Bea", PASS["a-bea"])[1].status_code == 503
    assert login("Chef", "ein langes passwort")[1].status_code == 200  # der Administrator kommt mit dem ABW-Passwort trotzdem hinein


def test_fortschritt_pro_benutzer_und_markieren(admin):
    admin.post("/api/abs/users/import", json={"ids": ["a-bea"]})
    bea_id = next(u["id"] for u in admin.get("/api/users").json() if u["name"] == "Bea")
    FakeAbs.progress = {"a-root": [entry("it-3", 1, True)], "a-bea": [entry("it-1", 1, True), entry("it-2", 0.4), entry("it-4", 1, True), entry("weg", 1, True)]}
    abs_mod.sync_progress()
    bea, _ = login("Bea", PASS["a-bea"])
    books = {b["title"]: b for b in bea.get("/api/series/1").json()["editions"][0]["books"]}
    assert books["Unsouled"]["finished"] and books["Soulsmith"]["progress"] == 0.4 and not books["Soulsmith"]["finished"]
    assert bea.get("/api/series/1").json()["listened"] == 1 and bea.get("/api/series/2").json()["listened"] == 0
    assert bea.get("/api/stats").json()["totals"]["en"]["finished"] == 2
    # Administrator ohne Verknüpfung sieht den Stand des Kontos, mit dem ABW liest
    assert admin.get("/api/series/2").json()["listened"] == 1 and admin.get("/api/series/1").json()["listened"] == 0
    # Fertig gehört: Serie und Autor markiert, auch beim Einzelbuch der Autor; nur Angehörtes zählt nicht
    assert bea.get("/api/follows").json() == {"series": [1], "authors": ["janedoe", "willwight"]} or \
        sorted(bea.get("/api/follows").json()["authors"]) == ["janedoe", "willwight"]
    assert bea.get("/api/follows").json()["series"] == [1]
    assert admin.get("/api/follows").json() == {"series": [2], "authors": ["frankherbert"]}
    # Entfernt Bea eine Markierung, setzt der nächste Abgleich sie nicht wieder
    bea.post("/api/follow", json={"kind": "series", "key": "1", "on": False})
    abs_mod.sync_progress()
    assert bea.get("/api/follows").json()["series"] == []
    # Neues fertig gehörtes Buch markiert weiter
    FakeAbs.progress["a-bea"].append(entry("it-3", 1, True))
    abs_mod.sync_progress()
    assert bea.get("/api/follows").json()["series"] == [2]
    # Nach dem Verknüpfen gilt für den Administrator sein eigenes Konto
    assert db.conn().execute("SELECT COUNT(*) FROM user_progress WHERE user_id=?", (bea_id,)).fetchone()[0] == 4


def test_gesperrtes_konto_verliert_zugang(admin):
    admin.post("/api/abs/users/import", json={"ids": ["a-bea"]})
    bea, r = login("Bea", PASS["a-bea"])
    assert r.status_code == 200
    FakeAbs.users = [dict(u, isActive=u["id"] != "a-bea") for u in ABS]
    abs_mod.sync_progress()
    assert bea.get("/api/series").status_code == 401
    assert login("Bea", PASS["a-bea"])[1].status_code == 401
    users = {u["name"]: u for u in admin.get("/api/users").json()}
    assert users["Bea"]["abs_active"] is False
    FakeAbs.users = ABS
    abs_mod.sync_progress()
    assert login("Bea", PASS["a-bea"])[1].status_code == 200


def test_statistik_mittel_der_fassungen(admin):
    """Statistik: Autoren und Serien zählen mit der mittleren Laufzeit der Fassungen, nicht der Summe."""
    c = db.conn()
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm) VALUES(3,1,'de','Cradle','cradle')")
    c.execute("INSERT INTO book(id, edition_id, lang, title, author, sequence, seq, abs_item_id, owned, runtime_min) VALUES"
              "(5,3,'de','Unsouled DE','Will Wight','1',1,'it-5',1,700),(6,3,'de','Soulsmith DE','Will Wight','2',2,'it-6',1,700)")
    s = admin.get("/api/stats").json()
    authors = {a["name"]: a["hours"] for a in s["top_authors"]}
    assert authors["Will Wight"] == 22  # (1200 + 1400) / 2 min, nicht 43 h
    assert authors["Frank Herbert"] == 20 and authors["Jane Doe"] == 5
    series = {x["name"]: x for x in s["longest_series"]}
    assert series["Cradle"]["hours"] == 22 and series["Cradle"]["editions"] == 2
    assert series["Dune"]["hours"] == 20 and series["Dune"]["editions"] == 1


def test_autoren_und_serien_mit_mittel_der_fassungen(admin):
    """Seite Autoren und Sortierung nach Laufzeit (20.11.): ebenfalls Mittel der Fassungen."""
    c = db.conn()
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm) VALUES(3,1,'de','Cradle','cradle')")
    c.execute("INSERT INTO book(id, edition_id, lang, title, author, sequence, seq, abs_item_id, owned, runtime_min) VALUES"
              "(5,3,'de','Unsouled DE','Will Wight','1',1,'it-5',1,700),(6,3,'de','Soulsmith DE','Will Wight','2',2,'it-6',1,700)")
    authors = {a["name"]: a for a in admin.get("/api/authors").json()}
    assert authors["Will Wight"]["hours"] == 22 and authors["Will Wight"]["en"] == 2 and authors["Will Wight"]["de"] == 2
    cradle = next(s for s in admin.get("/api/series").json() if s["name"] == "Cradle")
    assert cradle["hours"] == 22 and cradle["hours_editions"] == 2


def test_statistik_fasst_schreibweisen_zusammen(admin):
    """«Pirateaba» und «pirateaba» zählen in der Statistik als ein Autor (20.11.)."""
    db.conn().execute("INSERT INTO book(id, lang, title, author, abs_item_id, owned, runtime_min) VALUES"
                      "(7,'en','A','Pirateaba','it-7',1,600),(8,'en','B','Pirateaba','it-8',1,600),(9,'en','C','pirateaba','it-9',1,600)")
    authors = {a["name"]: a["hours"] for a in admin.get("/api/stats").json()["top_authors"]}
    assert authors["Pirateaba"] == 30 and "pirateaba" not in authors
