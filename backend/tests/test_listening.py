"""Hörverlauf: Sitzungen aus Audiobookshelf lesen, unplausible Hörzeit kürzen, Wochen-, Monats- und Jahresrückblick,
nur für Administratoren."""
import os

os.environ["ABW_NO_TASKS"] = "1"

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from abw import abs as abs_mod
from abw import auth, db, listening
from abw.api import app

TABLES = ("listen_session", "listen_progress", "user", "session", "book")


def ms(y, m, d, h=8):
    return int(datetime(y, m, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def sess(sid, user, name, item, day, h, seconds, *, wall=None, start=0.0, moved=None, duration=36000):
    t = ms(*day, h)
    return {"id": sid, "userId": user, "user": {"id": user, "username": name}, "libraryItemId": item, "libraryId": "lib-en",
            "mediaType": "book", "displayTitle": f"Titel {item} (Unabridged)", "displayAuthor": "will wight, Jemand",
            "date": date(*day).isoformat(), "startedAt": t, "updatedAt": t + int((wall if wall is not None else seconds) * 1000),
            "timeListening": seconds, "startTime": start, "currentTime": start + (moved if moved is not None else seconds),
            "duration": duration, "deviceInfo": {"clientName": "Absorb", "manufacturer": "samsung", "model": "S24"}}


SESSIONS = [
    sess("s1", "u-mo", "Max", "it-1", (2026, 11, 10), 6, 3600),
    sess("s2", "u-mo", "Max", "it-1", (2026, 11, 11), 6, 1800),
    sess("s3", "u-mo", "Max", "it-2", (2026, 11, 12), 20, 7200),
    sess("s4", "u-jo", "Jan", "it-2", (2026, 11, 12), 21, 900),
    sess("s5", "u-mo", "Max", "it-1", (2026, 11, 3), 6, 600),  # Vorwoche
    # Audiobookshelf zählt 1200 h in einer Sitzung, die zwei Stunden dauerte
    sess("s6", "u-mo", "Max", "it-3", (2026, 10, 20), 6, 4_320_000, wall=7200, moved=7000),
    {"id": "share", "userId": None, "mediaType": "book", "startedAt": ms(2026, 11, 10)},  # geteilter Link ohne Konto
    {**sess("pod", "u-mo", "Max", "it-9", (2026, 11, 10), 9, 600), "mediaType": "podcast"},
]
PROGRESS = {
    "u-mo": [{"libraryItemId": "it-2", "progress": 1, "isFinished": True, "finishedAt": ms(2026, 11, 12, 22), "lastUpdate": ms(2026, 11, 12, 22)},
             {"libraryItemId": "it-1", "progress": 0.4, "isFinished": False, "lastUpdate": ms(2026, 11, 11)},
             {"libraryItemId": "it-3", "progress": 0.3, "isFinished": False, "lastUpdate": ms(2026, 10, 20)}],
    "u-jo": [],
}


class FakeAbs:
    pages: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path, params=None, **kw):
        if path == "/api/sessions":
            n = params["itemsPerPage"]
            rows = sorted(SESSIONS, key=lambda s: -(s.get("updatedAt") or s["startedAt"]))
            FakeAbs.pages.append(params["page"])
            data = {"sessions": rows[params["page"] * n:(params["page"] + 1) * n], "numPages": -(-len(rows) // n)}
        elif path == "/api/users":
            data = {"users": [{"id": "u-mo", "username": "Max"}, {"id": "u-jo", "username": "Jan"}]}
        else:
            data = {"mediaProgress": PROGRESS[path.rsplit("/", 1)[1]]}
        return type("R", (), {"raise_for_status": lambda s: s, "json": lambda s: data})()


@pytest.fixture
def synced(monkeypatch):
    db.init()
    c = db.conn()
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(abs_mod, "_client", lambda: FakeAbs())
    monkeypatch.setattr(listening.config, "ABS_LIBRARIES", {"en": "lib-en", "de": "lib-de"})
    c.execute("INSERT INTO book(id, lang, title, author, abs_item_id, owned) VALUES(1,'en','Unsouled','Will Wight','it-1',1)")
    FakeAbs.pages = []
    assert listening.sync() == "6 Hörsitzungen gelesen, Fortschritt von 2 Konten"
    yield c
    for t in TABLES:
        c.execute(f"DELETE FROM {t}")


def test_plausibel():
    assert listening.plausible(3600, 3700, 36000, 3500) == 3600
    assert listening.plausible(4_320_000, 152_640, 65_880, 65_880) == 65_880  # nie mehr als das Buch lang ist
    assert listening.plausible(7200, 1800, 36000, 1800) == 2100  # nie mehr als verstrichen
    assert listening.plausible(36000, 40000, 50000, 600) == 4200  # Position lief kaum weiter: höchstens eine Stunde mehr


def test_zeitraeume():
    today = date(2026, 11, 13)
    assert listening.period_range("week", 0, today)[:2] == (date(2026, 11, 9), date(2026, 11, 15))
    assert listening.period_range("week", 0, today)[2] == "KW 46 · 9.11.–15.11.2026"
    assert listening.period_range("week", -45, today)[2] == "KW 1 · 29.12.2025–4.1.2026"
    assert listening.period_range("month", -11, today) == (date(2025, 12, 1), date(2025, 12, 31), "Dezember 2025")
    assert listening.period_range("month", 0, today)[1] == date(2026, 11, 30)
    assert listening.period_range("year", -1, today) == (date(2025, 1, 1), date(2025, 12, 31), "2025")


def test_abgleich(synced):
    rows = {r["id"]: r for r in synced.execute("SELECT * FROM listen_session")}
    assert set(rows) == {"s1", "s2", "s3", "s4", "s5", "s6"}  # ohne geteilte Links und Podcasts
    assert rows["s6"]["raw_seconds"] == 4_320_000 and rows["s6"]["seconds"] == 7500
    assert rows["s1"]["device"] == "Absorb · samsung S24" and rows["s1"]["lang"] == "en"
    # Zweiter Lauf hört auf, sobald bekannte Sitzungen erreicht sind
    FakeAbs.pages = []
    listening.sync()
    assert FakeAbs.pages == [0]


def test_woche(synced):
    o = listening.overview("week", 0, "", date(2026, 11, 13))
    s = o["summary"]
    assert o["range"]["label"] == "KW 46 · 9.11.–15.11.2026" and o["range"]["days"] == 5
    assert (s["seconds"], s["prev_seconds"], s["sessions"], s["books"], s["days"], s["listeners"]) == (13500, 600, 4, 2, 3, 2)
    assert s["finished"] == 1 and s["streak"] == 3 and s["best_day"] == {"day": "2026-11-12", "seconds": 8100}
    assert [u["name"] for u in o["users"]] == ["Max", "Jan"] and o["users"][0]["seconds"] == 12600
    assert [t["seconds"] for t in o["timeline"]] == [0, 3600, 1800, 8100, 0, 0, 0]
    assert o["timeline"][0]["label"] == "Mo"
    assert o["books"][0]["item_id"] == "it-2" and o["books"][0]["listeners"] == ["Jan", "Max"]
    # Titel aus dem Bestand, sonst aus der Sitzung ohne «(Unabridged)»; erster Autor
    titles = {b["item_id"]: (b["title"], b["author"]) for b in o["books"]}
    assert titles == {"it-1": ("Unsouled", "Will Wight"), "it-2": ("Titel it-2", "Will Wight")}
    assert o["authors"] == [{"name": "Will Wight", "seconds": 13500}]
    assert o["heat"][1][7] == 3600 and sum(o["heat"][1]) == 3600  # Dienstag 6 Uhr UTC = 7 Uhr Schweizer Zeit


def test_ein_konto_und_liegengeblieben(synced):
    o = listening.overview("month", 0, "u-mo", date(2026, 11, 13))
    assert o["user"] == "u-mo" and o["summary"]["seconds"] == 13200 and o["summary"]["listeners"] == 1
    assert {b["item_id"]: (b["progress"], b["finished"]) for b in o["books"]} == {"it-1": (0.4, False), "it-2": (1, True)}
    assert [f["item_id"] for f in o["finished"]] == ["it-2"]
    assert o["stale"] == []  # it-3 zuletzt am 20.10., noch keine 30 Tage her
    later = listening.overview("month", 0, "u-mo", date(2026, 12, 1))
    assert [(x["item_id"], x["progress"]) for x in later["stale"]] == [("it-3", 0.3)] and later["summary"]["seconds"] == 0
    oct_ = listening.overview("month", -1, "u-mo", date(2026, 11, 13))
    assert oct_["summary"]["seconds"] == 7500 and oct_["summary"]["corrected"] == 1
    assert oct_["history"][0]["raw_seconds"] == 4_320_000
    year = listening.overview("year", 0, "u-mo", date(2026, 11, 13))
    assert year["summary"]["best_slot"] == {"key": "2026-11", "seconds": 13200} and len(year["timeline"]) == 12


def test_benutzer_sehen_nur_eigenes_konto(synced, monkeypatch):
    auth._fails.clear()
    with TestClient(app) as admin, TestClient(app) as user:
        assert admin.post("/api/auth/password", json={"user": "max", "password": "ein langes passwort"}).status_code == 200
        assert admin.post("/api/users", json={"name": "gast", "password": "noch ein passwort"}).status_code == 200
        assert user.post("/api/auth/login", json={"user": "gast", "password": "noch ein passwort"}).status_code == 200
        r = admin.get("/api/listening", params={"period": "year"})
        assert r.status_code == 200 and r.json()["range"]["period"] == "year"
        assert admin.get("/api/listening", params={"period": "decade"}).status_code == 400
        # Ohne verknüpftes Audiobookshelf-Konto: nichts, auch nicht mit fremdem Konto als Parameter
        for params in ({}, {"user": "u-mo"}):
            r = user.get("/api/listening", params={"period": "month", **params})
            assert r.status_code == 200 and r.json()["users"] == [] and r.json()["summary"]["seconds"] == 0 and r.json()["history"] == []
        synced.execute("UPDATE user SET abs_user_id='u-jo' WHERE name='gast'")
        synced.commit()
        o = user.get("/api/listening", params={"period": "month", "user": "u-mo"}).json()
        assert o["user"] == "u-jo" and [u["name"] for u in o["users"]] == ["Jan"]
        assert o["summary"]["seconds"] == 900 and o["summary"]["listeners"] == 1 and {h["name"] for h in o["history"]} == {"Jan"}
        assert [b["listeners"] for b in o["books"]] == [["Jan"]]
