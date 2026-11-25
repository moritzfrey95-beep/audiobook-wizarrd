"""Serie vom Autor beendet: seltener bei Audible prüfen, Flag «ended», Markierung fällt bei neuem Band weg."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from abw import api, audible, db


def child(asin, seq, date="2024-01-01"):
    return {"asin": asin, "sequence": seq, "title": f"Band {seq}", "subtitle": None, "author": "Autorin A", "narrator": "N",
            "release_date": date, "runtime_min": 500, "cover_url": None, "language": "english", "content_type": "Product", "format_type": "unabridged"}


def setup():
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series", "follow", "notification"):
        c.execute(f"DELETE FROM {t}")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Zyklus E','zykluse')").lastrowid
    old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, monitored, synced_at) VALUES(?,?,?,?,?,1,?)",
                    (sid, "en", "Zyklus E", "zykluse", "SER", old)).lastrowid
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, abs_item_id, author) VALUES(?,?,?,?,?,?,1,'x','Autorin A')",
              (eid, "en", "A1", "Band 1", "1", 1.0))
    return c, sid, eid


def test_beendet_flag_und_schalter():
    c, sid, eid = setup()
    client = TestClient(api.app)
    assert "ended" not in api.load()[sid]["flags"]
    assert client.post(f"/api/series/{sid}/ended", json={"ended": True}).status_code == 200
    s = api.load()[sid]
    assert s["ended"] and s["flags"][0] == "ended" and "complete" in s["flags"]
    assert client.post("/api/series/999999/ended", json={"ended": True}).status_code == 404
    client.post(f"/api/series/{sid}/ended", json={"ended": False})
    assert not api.load()[sid]["ended"]


def test_beendete_serie_seltener_geprueft(monkeypatch):
    c, sid, eid = setup()
    seen = []
    monkeypatch.setattr(audible, "sync_edition", lambda e: seen.append(e) or "ok")
    audible.sync_series()
    assert seen == [eid]  # zehn Tage alt: fällig
    seen.clear()
    c.execute("UPDATE series SET ended='2026-01-01' WHERE id=?", (sid,))
    audible.sync_series()
    assert seen == []  # beendet: erst nach 30 Tagen
    c.execute("UPDATE edition SET synced_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(days=31)).isoformat(), eid))
    audible.sync_series()
    assert seen == [eid]


def test_neuer_band_hebt_beendet_auf():
    c, sid, eid = setup()
    c.execute("UPDATE series SET ended='2026-01-01' WHERE id=?", (sid,))
    ed = c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()
    audible._merge(c, ed, "SER", [child("A1", "1")])
    assert c.execute("SELECT ended FROM series WHERE id=?", (sid,)).fetchone()[0] == "2026-01-01"
    audible._merge(c, ed, "SER", [child("A1", "1"), child("A2", "2", "2200-01-01")])
    assert c.execute("SELECT ended FROM series WHERE id=?", (sid,)).fetchone()[0] is None
