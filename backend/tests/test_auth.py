"""Anmeldung: ohne Passwort offen, mit Passwort ist jede Schnittstelle ausser /api/auth/ gesperrt."""
import os

os.environ["ABW_NO_TASKS"] = "1"

import pytest
from fastapi.testclient import TestClient

from abw import auth, db
from abw.api import app


@pytest.fixture
def client():
    db.init()
    c = db.conn()
    c.execute("DELETE FROM user")
    c.execute("DELETE FROM session")
    auth._fails.clear()
    with TestClient(app) as tc:
        yield tc
    c.execute("DELETE FROM user")
    c.execute("DELETE FROM session")


def test_ohne_passwort_ist_abw_offen(client):
    assert client.get("/api/auth/state").json() == {"enabled": False, "authenticated": True, "user": None, "role": "admin", "id": 1, "langs": ["en", "de"]}
    assert client.get("/api/review/count").status_code == 200


def test_ohne_passwort_von_aussen_gesperrt(client):
    for headers in ({"X-Forwarded-For": "203.0.113.5"}, {"Host": "abw.example.org"}, {"X-Real-IP": "203.0.113.5", "Host": "192.168.0.10:8790"}):
        assert client.get("/api/review/count", headers=headers).status_code == 403
        assert client.post("/api/auth/password", json={"user": "x", "password": "angreifer passwort"}, headers=headers).status_code == 403
        assert "Passwort" in client.get("/api/auth/state", headers=headers).json()["detail"]
    for host in ("192.168.0.10:8790", "server:8790", "localhost:8790", "server.tail1234.ts.net:8790"):
        assert client.get("/api/review/count", headers={"Host": host}).status_code == 200
    client.post("/api/auth/password", json={"user": "max", "password": "ein langes passwort"})
    assert client.get("/api/review/count", headers={"X-Forwarded-For": "203.0.113.5", "Host": "abw.example.org"}).status_code == 200


def test_mit_passwort_gesperrt_bis_zur_anmeldung(client):
    assert client.post("/api/auth/password", json={"user": "max", "password": "kurz"}).status_code == 400
    assert client.post("/api/auth/password", json={"user": "max", "password": "ein langes passwort"}).status_code == 200
    assert client.get("/api/review/count").status_code == 200  # nach dem Festlegen gleich angemeldet
    stored = auth.find("max")["hash"]
    assert "ein langes passwort" not in stored and "$" in stored
    client.cookies.clear()
    assert client.get("/api/review/count").status_code == 401
    assert client.get("/api/cover/x").status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/auth/state").json() == {"enabled": True, "authenticated": False, "user": None, "role": None, "id": None, "langs": ["en", "de"]}
    assert client.post("/api/auth/login", json={"user": "max", "password": "falsch falsch"}).status_code == 401
    assert client.post("/api/auth/login", json={"user": "Max", "password": "ein langes passwort"}).status_code == 200
    assert client.get("/api/review/count").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/review/count").status_code == 401


def test_passwort_aendern_und_abschalten_nur_mit_bisherigem_passwort(client):
    client.post("/api/auth/password", json={"user": "max", "password": "ein langes passwort"})
    assert client.post("/api/auth/password", json={"user": "max", "password": "noch ein langes", "current": "falsch"}).status_code == 403
    assert client.post("/api/auth/disable", json={"current": "falsch"}).status_code == 403
    fremd = TestClient(app)  # nicht angemeldet: darf weder ändern noch abschalten
    assert fremd.post("/api/auth/password", json={"user": "x", "password": "angreifer passwort", "current": "ein langes passwort"}).status_code == 401
    assert fremd.post("/api/auth/disable", json={"current": "ein langes passwort"}).status_code == 403
    assert client.post("/api/auth/password", json={"user": "max", "password": "noch ein langes", "current": "ein langes passwort"}).status_code == 200
    assert client.post("/api/auth/disable", json={"current": "noch ein langes"}).status_code == 200
    assert not auth.enabled() and client.get("/api/review/count").status_code == 200


def test_sperre_nach_fuenf_fehlversuchen(client):
    client.post("/api/auth/password", json={"user": "max", "password": "ein langes passwort"})
    client.cookies.clear()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"user": "max", "password": "falsch falsch"}).status_code == 401
    assert client.post("/api/auth/login", json={"user": "max", "password": "ein langes passwort"}).status_code == 429
