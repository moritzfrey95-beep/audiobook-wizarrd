"""Als gehört markieren: Übertrag nach Audiobookshelf mit dem ABW-Schlüssel (eigenes Konto) oder
mit der bei der Anmeldung behaltenen Sitzung (andere Konten), Rücknahme mit alter Hörposition."""
import os

os.environ["ABW_NO_TASKS"] = "1"

import httpx
import pytest
from fastapi.testclient import TestClient

from abw import abs as abs_mod
from abw import auth, config, db, listened, tasks
from abw.api import app

PASS = {"Max": ("a-root", "max abs passwort"), "Bea": ("a-bea", "beas abs passwort")}
TABLES = ("user", "session", "follow", "auto_follow", "user_progress", "mark_undo", "book", "edition", "series")


class Abs:
    """Kleines Audiobookshelf: Konten, Sitzungen mit wechselnden Erneuerungsschlüsseln, Hörstand je Konto."""
    def __init__(self):
        self.progress = {}  # (konto, item) -> dict
        self.refresh = {}  # erneuerungsschlüssel -> konto
        self.access = {"API-KEY": "a-root"}
        self.n = 0
        self.logouts = []

    def _tokens(self, acc):
        self.n += 1
        r, a = f"r{self.n}", f"t{self.n}"
        self.refresh[r] = acc
        self.access[a] = acc
        return r, a

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/login":
            body = __import__("json").loads(req.content)
            acc, pw = PASS.get(body["username"], (None, None))
            if pw != body["password"]:
                return httpx.Response(401)
            r, a = self._tokens(acc)
            return httpx.Response(200, json={"user": {"id": acc, "refreshToken": r, "accessToken": a}})
        if path == "/logout":
            tok = req.headers.get("x-refresh-token")
            self.logouts.append(tok)
            self.refresh.pop(tok, None)
            return httpx.Response(200)
        if path == "/auth/refresh":
            acc = self.refresh.pop(req.headers.get("x-refresh-token"), None)
            if not acc:
                return httpx.Response(401)
            r, a = self._tokens(acc)
            return httpx.Response(200, json={"user": {"id": acc, "refreshToken": r, "accessToken": a}})
        acc = self.access.get(req.headers.get("authorization", "").removeprefix("Bearer "))
        if not acc:
            return httpx.Response(401)
        if path == "/api/me":
            return httpx.Response(200, json={"id": acc, "mediaProgress": []})
        if path.startswith("/api/me/progress/"):
            item = path.rsplit("/", 1)[1]
            p = self.progress.get((acc, item))
            if req.method == "GET":
                return httpx.Response(200, json=p) if p else httpx.Response(404)
            body = __import__("json").loads(req.content)
            p = p or {"currentTime": 0, "progress": 0, "isFinished": False}
            if body.get("isFinished") is True:
                p.update(isFinished=True, progress=1)
            elif body.get("isFinished") is False and p["isFinished"]:
                p.update(isFinished=False, progress=0, currentTime=0)
            if "currentTime" in body:
                p.update(currentTime=body["currentTime"], progress=body["progress"])
            self.progress[(acc, item)] = p
            return httpx.Response(200)
        return httpx.Response(404)


@pytest.fixture
def abs_server(monkeypatch, tmp_path):
    srv = Abs()
    real = httpx.Client

    def client(*a, **kw):
        kw["transport"] = httpx.MockTransport(srv.handle)
        return real(*a, **kw)

    monkeypatch.setattr(httpx, "Client", client)
    monkeypatch.setattr(config, "ABS_URL", "http://abs")
    monkeypatch.setattr(config, "abs_token", lambda: "API-KEY")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(tasks, "request", lambda key: None)
    listened._own.clear()
    listened._access.clear()
    db.init()
    c = db.conn()
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")
    auth._fails.clear()
    c.execute("INSERT INTO series(id, name, norm) VALUES(1,'Cradle','cradle')")
    c.execute("INSERT INTO edition(id, series_id, lang, name, norm) VALUES(1,1,'en','Cradle','cradle')")
    c.execute("INSERT INTO book(id, edition_id, lang, title, author, sequence, seq, abs_item_id, owned, runtime_min) VALUES"
              "(1,1,'en','Unsouled','Will Wight','1',1,'it-1',1,600),(2,1,'en','Soulsmith','Will Wight','2',2,NULL,0,600)")
    yield srv
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")


def setup_users(srv):
    with TestClient(app) as a:
        assert a.post("/api/auth/password", json={"user": "Chef", "password": "ein langes passwort"}).status_code == 200
    c = db.conn()
    c.execute("UPDATE user SET abs_user_id='a-root', abs_name='Max', abs_active=1 WHERE id=1")
    c.execute("INSERT INTO user(id, name, hash, role, created, abs_user_id, abs_name, abs_active) VALUES(2,'Bea','','user','x','a-bea','Bea',1)")


def login(name, pw):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"user": name, "password": pw}).status_code == 200
    return c


def test_eigenes_konto_schreibt_mit_dem_schluessel_und_nimmt_zurueck(abs_server):
    setup_users(abs_server)
    abs_server.progress[("a-root", "it-1")] = {"currentTime": 1234.0, "progress": 0.4, "isFinished": False}
    me = login("Chef", "ein langes passwort")
    assert me.get("/api/listened").json()["can"] is True
    r = me.post("/api/books/1/listened", json={"finished": True})
    assert r.status_code == 200 and r.json()["finished"] is True
    assert abs_server.progress[("a-root", "it-1")]["isFinished"] is True
    row = db.conn().execute("SELECT finished FROM user_progress WHERE user_id=1 AND book_id=1").fetchone()
    assert row["finished"] == 1
    assert db.conn().execute("SELECT 1 FROM follow WHERE user_id=1 AND kind='series' AND key='1'").fetchone()
    r = me.post("/api/books/1/listened", json={"finished": False})
    assert r.json()["finished"] is False
    assert abs_server.progress[("a-root", "it-1")] == {"currentTime": 1234.0, "progress": 0.4, "isFinished": False}
    assert not db.conn().execute("SELECT 1 FROM mark_undo").fetchone()


def test_anderes_konto_braucht_anmeldung_mit_abs_passwort(abs_server):
    setup_users(abs_server)
    # angemeldet ohne Audiobookshelf-Sitzung (z. B. vor dem Update): Hinweis statt Fehler
    db.conn().execute("UPDATE user SET hash=? WHERE id=2", (auth._hash("beas abw passwort"),))
    bea = login("Bea", "beas abw passwort")
    st = bea.get("/api/listened").json()
    assert st["can"] is False and "Audiobookshelf-Passwort" in st["why"]
    assert bea.post("/api/books/1/listened", json={"finished": True}).status_code == 409
    # Anmeldung mit dem Audiobookshelf-Passwort: Sitzung bleibt offen, verschlüsselt gespeichert
    bea = login("Bea", "beas abs passwort")
    stored = db.conn().execute("SELECT abs_session FROM user WHERE id=2").fetchone()["abs_session"]
    assert stored and not stored.startswith("r")
    assert bea.get("/api/listened").json()["can"] is True
    assert bea.post("/api/books/1/listened", json={"finished": True}).json()["finished"] is True
    assert abs_server.progress[("a-bea", "it-1")]["isFinished"] is True
    assert ("a-root", "it-1") not in abs_server.progress
    # neue Anmeldung schliesst die alte Sitzung
    before = len(abs_server.logouts)
    login("Bea", "beas abs passwort")
    assert len(abs_server.logouts) == before + 1


def test_sitzung_erneuern_und_verfallen(abs_server):
    setup_users(abs_server)
    login("Bea", "beas abs passwort")
    assert "1 Audiobookshelf-Sitzungen erneuert" in listened.keep_alive()
    abs_server.refresh.clear()  # in Audiobookshelf abgelaufen oder widerrufen
    listened._access.clear()
    assert "1 abgelaufen" in listened.keep_alive()
    assert listened.status(2)["can"] is False


def test_nicht_im_bestand_und_trennen(abs_server):
    setup_users(abs_server)
    me = login("Chef", "ein langes passwort")
    assert me.post("/api/books/2/listened", json={"finished": True}).status_code == 404
    login("Bea", "beas abs passwort")
    assert "a-bea" in abs_server.refresh.values()
    auth.delete(2)  # Benutzer gelöscht: Sitzung in Audiobookshelf geschlossen
    assert "a-bea" not in abs_server.refresh.values()
