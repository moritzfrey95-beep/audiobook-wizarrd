"""Backlog: Sprechern folgen, Atom-Feed, Kennzahlen, Mindest-Speicherplatz."""
import pytest

from abw import atomfeed, audible, db, metrics, narrators, notifications, space


def test_narrator_follow_first_run_silent_then_notifies(monkeypatch):
    db.init()
    c = db.conn()
    added = c.execute("INSERT OR IGNORE INTO user(id, name, hash, role, created) VALUES(1, 'admin', 'x', 'admin', '2026-01-01')").rowcount
    notifications.follow(1, "narrator", "  Travis   Baldree ")
    assert notifications.follows(1)["narrators"] == ["Travis Baldree"]
    prod = lambda asin, title, narr: {"asin": asin, "title": title, "authors": [{"name": "Autor X"}], "narrators": [{"name": narr}],
                                      "release_date": "2026-01-01", "language": "english"}
    data = {"products": [prod("B0OLD00001", "Alt", "Travis Baldree"), prod("B0FREMD001", "Fremd", "Jemand Anders")]}
    monkeypatch.setattr(audible, "_get", lambda lang, path, **p: data)
    narrators.check()
    assert not c.execute("SELECT 1 FROM notification WHERE kind='narrator'").fetchone()
    data["products"].append(prod("B0NEU00001", "Neu", "Travis Baldree"))
    narrators.check()
    rows = c.execute("SELECT title, body FROM notification WHERE kind='narrator'").fetchall()
    assert len(rows) == 1 and "Neu" in rows[0]["body"] and rows[0]["title"] == "Neu gelesen von Travis Baldree"
    feed = atomfeed.build(1)
    assert "<feed" in feed and "Neu gelesen von Travis Baldree" in feed
    c.execute("DELETE FROM notification WHERE kind='narrator'")
    c.execute("DELETE FROM follow WHERE kind='narrator'")
    c.execute("DELETE FROM narrator_seen")
    if added:
        c.execute("DELETE FROM user WHERE id=1")
    with pytest.raises(ValueError):
        notifications.follow(1, "narrator", "x")


def test_metrics_and_space(monkeypatch):
    db.init()
    m = metrics.collect()
    assert {"books_owned", "missing_monitored", "downloads_active", "listening_hours_week"} <= set(m)
    assert "abw_books_owned" in metrics.prometheus()
    monkeypatch.setattr(space, "status", lambda: [{"name": "Usenet-Ziel", "free_gb": 3.0, "total_gb": 100.0}])
    monkeypatch.setattr("abw.notify.send", lambda *a, **k: True)
    db.set_setting("min_free_gb", "20")
    with pytest.raises(ValueError, match="Zu wenig Speicherplatz"):
        space.check()
    db.set_setting("min_free_gb", "0")
    space.check()
