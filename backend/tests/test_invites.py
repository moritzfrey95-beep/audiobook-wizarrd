"""Einladungsseite: anlegen, ohne Anmeldung lesen, QR-Code, Ablauf, Löschen."""
from fastapi.testclient import TestClient

from abw import config, db, invites
from abw.api import app


def test_einladung(monkeypatch):
    db.init()
    monkeypatch.setattr(config, "PUBLIC_URL", "https://abw.example.org")
    monkeypatch.setattr(config, "ABS_PUBLIC_URL", "https://abs.example.org")
    inv = invites.create("  Oma  ")
    assert inv["name"] == "Oma" and inv["abw"] == "https://abw.example.org"
    c = TestClient(app)
    r = c.get(f"/api/invite/{inv['code']}")
    assert r.status_code == 200 and r.json()["abs"] == "https://abs.example.org"
    svg = c.get(f"/api/invite/{inv['code']}/qr.svg?what=abw")
    assert svg.status_code == 200 and "<svg" in svg.text
    assert db.conn().execute("SELECT opened FROM invite WHERE code=?", (inv["code"],)).fetchone()["opened"]
    db.conn().execute("UPDATE invite SET expires='2000-01-01' WHERE code=?", (inv["code"],))
    assert c.get(f"/api/invite/{inv['code']}").status_code == 404
    invites.delete(inv["code"])
    assert c.get("/api/invite/gibtsnicht").status_code == 404
