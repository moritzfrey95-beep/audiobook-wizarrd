"""Benutzerverwaltung: Administrator darf alles; Benutzer dürfen ansehen, hinzufügen, markieren, die automatische Suche
anstossen und ihr Goodreads-Regal verknüpfen, aber weder Prüfen noch Aufgaben noch Auswahl von Hand."""
import os

os.environ["ABW_NO_TASKS"] = "1"

import pytest
from fastapi.testclient import TestClient

from abw import auth, db, search
from abw.api import app


@pytest.fixture
def clients(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("user", "session", "follow", "notification"):
        c.execute(f"DELETE FROM {t}")
    auth._fails.clear()
    monkeypatch.setattr(search, "start_auto", lambda sid: None)
    monkeypatch.setattr(search, "start_book", lambda bid: None)
    with TestClient(app) as admin, TestClient(app) as user:
        assert admin.post("/api/auth/password", json={"user": "max", "password": "ein langes passwort"}).status_code == 200
        assert admin.post("/api/users", json={"name": "gast", "password": "noch ein passwort"}).status_code == 200
        assert user.post("/api/auth/login", json={"user": "gast", "password": "noch ein passwort"}).status_code == 200
        yield admin, user
    for t in ("user", "session", "follow", "notification"):
        c.execute(f"DELETE FROM {t}")


def test_rollen(clients):
    admin, user = clients
    assert admin.get("/api/auth/state").json()["role"] == "admin" and user.get("/api/auth/state").json()["role"] == "user"
    # Benutzer sieht Bibliothek, Entdecken, Kalender, Statistik und sein Konto
    for path in ("/api/series", "/api/authors", "/api/watchlist", "/api/calendar", "/api/stats", "/api/me", "/api/notifications", "/api/follows"):
        assert user.get(path).status_code == 200, path
    # … aber weder Prüfen noch Aufgaben noch Einstellungen
    for path in ("/api/review", "/api/review/count", "/api/tasks", "/api/automation", "/api/settings", "/api/users", "/api/downloads",
                 "/api/cleanup", "/api/convert", "/api/books/1/search", "/api/books/1/ebook/search"):
        assert user.get(path).status_code == 403, path
        assert admin.get(path).status_code != 403, path
    # Anstossen darf er nur die automatische Suche für Serien und Bücher
    assert user.post("/api/series/1/autosearch").status_code == 200
    assert user.post("/api/books/1/autosearch").status_code == 200
    for path, body in (("/api/books/1/grab", {"guid": "x", "title": "x"}), ("/api/books/1/dismiss", None), ("/api/automation", {"auto_search": False}),
                       ("/api/tasks/abs/run", None), ("/api/downloads/1/import", None), ("/api/downloads/1/reject", None),
                       ("/api/series/1/monitor", {"edition_id": 1, "monitored": False}), ("/api/series/1/auto", {"auto": False}),
                       ("/api/series/1/refresh", None), ("/api/series/1/ebooks", None), ("/api/books/1/ebook", None), ("/api/books/1/convert", None),
                       ("/api/cleanup/run", {}), ("/api/users", {"name": "x", "password": "ein langes passwort", "role": "admin"}),
                       ("/api/users/1", {"role": "user"}), ("/api/auth/disable", {"current": "noch ein passwort"})):
        assert user.post(path, json=body).status_code == 403, path
    assert user.delete("/api/users/1").status_code == 403
    # Bewertungen: eigene ansehen, setzen und zurücknehmen
    for path in ("/api/ratings/count", "/api/ratings/open", "/api/ratings/mine", "/api/ratings/tags", "/api/ratings/stats"):
        assert user.get(path).status_code == 200, path
    assert user.post("/api/ratings/book/999999", json={"stars": 4}).status_code == 404  # durchgelassen, Buch fehlt
    assert user.delete("/api/ratings/book/999999").status_code != 403
    assert user.post("/api/ratings/edition/999999", json={"stars": 4}).status_code != 403


def test_benutzer_verwalten(clients):
    admin, user = clients
    users = admin.get("/api/users").json()
    assert [(u["name"], u["role"]) for u in users] == [("max", "admin"), ("gast", "user")]
    gid = users[1]["id"]
    assert admin.post("/api/users", json={"name": "Gast", "password": "ein langes passwort"}).status_code == 400  # Name vergeben
    assert admin.post("/api/users", json={"name": "kurz", "password": "kurz"}).status_code == 400
    assert admin.delete(f"/api/users/{users[0]['id']}").status_code == 409  # sich selbst nicht
    assert admin.post(f"/api/users/{users[0]['id']}", json={"role": "user"}).status_code == 400  # letzter Administrator bleibt
    assert admin.post("/api/auth/disable", json={"current": "ein langes passwort"}).status_code == 409  # erst andere Benutzer löschen
    # Benutzer ändert sein eigenes Passwort, nur mit dem bisherigen
    assert user.post("/api/auth/password", json={"password": "ganz neues passwort", "current": "falsch"}).status_code == 403
    assert user.post("/api/auth/password", json={"password": "ganz neues passwort", "current": "noch ein passwort"}).status_code == 200
    assert user.get("/api/me").json()["user"] == "gast"
    # Administrator setzt ein neues Passwort: die Sitzung des Benutzers endet
    assert admin.post(f"/api/users/{gid}", json={"password": "vom admin gesetzt"}).status_code == 200
    assert user.get("/api/me").status_code == 401
    assert user.post("/api/auth/login", json={"user": "gast", "password": "vom admin gesetzt"}).status_code == 200
    assert admin.delete(f"/api/users/{gid}").status_code == 200
    assert user.get("/api/me").status_code == 401


def test_goodreads_und_meldungen_je_benutzer(clients, monkeypatch):
    from abw import notifications, wishlist
    admin, user = clients
    monkeypatch.setattr(wishlist, "fetch", lambda uid, shelf: [])
    assert user.post("/api/me/goodreads", json={"user": "https://www.goodreads.com/user/show/777-gast", "shelf": "wunsch"}).status_code == 200
    assert user.get("/api/me").json()["goodreads"]["user"] == "777" and admin.get("/api/me").json()["goodreads"]["user"] == ""
    uid = user.get("/api/me").json()["id"]
    assert user.post("/api/follow", json={"kind": "series", "key": "42"}).status_code == 200
    assert user.get("/api/follows").json() == {"series": [42], "authors": []} and admin.get("/api/follows").json()["series"] == []
    assert notifications.emit("announced", "Neuer Band angekündigt", "S, Band 2", "/serie/42", "announced:1", series_id=42) == 1
    assert notifications.emit("announced", "Neuer Band angekündigt", "S, Band 2", "/serie/42", "announced:1", series_id=42) == 0  # nur einmal
    got = user.get("/api/notifications").json()
    assert got["unread"] == 1 and got["items"][0]["link"] == "/serie/42" and admin.get("/api/notifications").json()["total"] == 0
    assert user.post("/api/notifications/read", json={}).status_code == 200 and user.get("/api/notifications").json()["unread"] == 0
    assert uid != 1


def test_alter_einzelbenutzer_wird_administrator():
    db.init()
    c = db.conn()
    c.execute("DELETE FROM user")
    c.execute("INSERT OR REPLACE INTO setting VALUES('auth_user','Chef')")
    c.execute("INSERT OR REPLACE INTO setting VALUES('auth_hash','00$11')")
    c.execute("INSERT OR REPLACE INTO setting VALUES('goodreads_user','174')")
    db.migrate_users(c)
    u = auth.get(1)
    assert (u["name"], u["role"], u["hash"], u["goodreads_user"]) == ("Chef", "admin", "00$11", "174")
    assert not c.execute("SELECT 1 FROM setting WHERE key='auth_hash'").fetchone()
    c.execute("DELETE FROM user")
    db.set_setting("goodreads_user", "")


def test_uebersicht_markiert_und_abstellen(clients):
    from tests.test_notifications import setup
    c, sid, eid = setup()
    _, user = clients
    me = user.get("/api/me").json()["id"]
    assert user.post("/api/follow", json={"kind": "series", "key": str(sid), "on": True}).status_code == 200
    assert user.post("/api/follow", json={"kind": "author", "key": db.norm("Autorin A"), "on": True}).status_code == 200
    got = user.get("/api/follows/overview").json()
    assert [(s["id"], s["name"], s["author"]) for s in got["series"]] == [(sid, "Zyklus Q", "Autorin A")]
    assert [(a["key"], a["name"], a["series"]) for a in got["authors"]] == [(db.norm("Autorin A"), "Autorin A", 1)]
    # Abstellen: weg aus der Übersicht, keine Meldungen mehr, auch nicht für ein selbst hinzugefügtes Serienbuch
    user.post("/api/follow", json={"kind": "series", "key": str(sid), "on": False})
    user.post("/api/follow", json={"kind": "author", "key": db.norm("Autorin A"), "on": False})
    assert user.get("/api/follows/overview").json() == {"series": [], "authors": []}
    c.execute("UPDATE book SET wanted_by=?", (me,))
    from abw import notifications
    assert notifications.book_event("available", "Neu im Bestand", c.execute("SELECT * FROM book").fetchone()) == 0


def test_eigene_einstellung_folgen_beim_hinzufuegen(clients):
    admin, user = clients
    assert user.get("/api/me").json()["follow_on_add"] is True
    assert user.post("/api/me/prefs", json={"follow_on_add": False}).status_code == 200
    assert user.get("/api/me").json()["follow_on_add"] is False and admin.get("/api/me").json()["follow_on_add"] is True
