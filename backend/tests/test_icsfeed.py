"""Kalender-Abo: Filter nach Sprache und Umfang, gültiges iCalendar, Zugang nur mit dem Schlüssel; API-Schlüssel."""
from fastapi.testclient import TestClient

from abw import api, auth, db, icsfeed, notifications

E = [
    {"series_id": 1, "series": "Zyklus Q", "lang": "de", "monitored": True, "title": "Der lange Weg; Teil, zwei", "sequence": "3",
     "release_date": "2027-02-01", "author": "Autorin A", "asin": "B0DE"},
    {"series_id": 2, "series": "Other Saga", "lang": "en", "monitored": False, "title": "Book", "sequence": "1",
     "release_date": "2027-03-05", "author": "Writer B", "asin": "B0EN"},
    {"series_id": 3, "series": "Ohne Termin", "lang": "de", "monitored": True, "title": "X", "sequence": "2",
     "release_date": None, "author": "Autorin A", "asin": "B0X"},
]


def test_filter():
    db.init()
    db.conn().execute("DELETE FROM follow")
    assert icsfeed.select(E, 7, "all", "followed") == []
    notifications.follow(7, "author", db.norm("Writer B"))
    assert [e["asin"] for e in icsfeed.select(E, 7, "all", "followed")] == ["B0EN"]
    assert [e["asin"] for e in icsfeed.select(E, 7, "de", "followed")] == []
    assert [e["asin"] for e in icsfeed.select(E, 7, "all", "monitored")] == ["B0DE"]  # ohne Termin fehlt
    assert [e["asin"] for e in icsfeed.select(E, 7, "all", "all")] == ["B0DE", "B0EN"]


def test_ics_gueltig():
    text = icsfeed.build(E[:2])
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "DTSTART;VALUE=DATE:20270201" in text and "DTEND;VALUE=DATE:20270202" in text
    assert "SUMMARY:Zyklus Q\\, Band 3: Der lange Weg\; Teil\\, zwei" in text
    assert "UID:B0EN@audiobook-wizarrd" in text
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    long = icsfeed._fold("SUMMARY:" + "ä" * 80)
    assert all(len(p.encode()) <= 75 for p in long.split("\r\n")) and long.replace("\r\n ", "") == "SUMMARY:" + "ä" * 80


def test_zugang_nur_mit_schluessel(monkeypatch):
    db.init()
    c = TestClient(api.app)
    monkeypatch.setattr(api, "calendar", lambda: E)
    tok = auth.feed_token(1)
    assert auth.feed_token(1) == tok
    r = c.get(f"/api/calendar.ics?token={tok}&scope=all&lang=de")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "B0DE@" in r.text and "B0EN@" not in r.text
    assert c.get("/api/calendar.ics?token=falsch").status_code == 401
    neu = auth.feed_token(1, renew=True)
    assert neu != tok and c.get(f"/api/calendar.ics?token={tok}").status_code == 401
    assert c.get(f"/api/calendar.ics?token={neu}&scope=nix").status_code == 400


def test_api_schluessel(monkeypatch):
    db.init()
    c = TestClient(api.app)
    monkeypatch.setattr(auth, "enabled", lambda: True)
    db.conn().execute("INSERT OR IGNORE INTO user(id, name, hash, role, created) VALUES(42, 'leser', 'x', 'user', '2026-01-01')")
    key = auth.create_api_key(42, "Skript")
    assert key.startswith("abw_") and auth.api_keys(42)[0]["hint"] == key[-4:]
    assert c.get("/api/notifications").status_code == 401
    assert c.get("/api/notifications", headers={"X-Api-Key": key}).status_code == 200
    assert c.get("/api/notifications", headers={"Authorization": "Bearer " + key}).status_code == 200
    assert c.get("/api/tasks", headers={"X-Api-Key": key}).status_code == 403  # Rechte wie der Benutzer
    assert c.get("/api/notifications", headers={"X-Api-Key": "abw_falsch"}).status_code == 401
    assert auth.api_keys(42)[0]["last_used"]
    assert auth.delete_api_key(42, auth.api_keys(42)[0]["id"])
    assert c.get("/api/notifications", headers={"X-Api-Key": key}).status_code == 401
