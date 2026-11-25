"""Qualitäts-Upgrade: schwache Bücher erkennen, nur deutlich bessere sichere Treffer laden."""
from abw import db, search, upgrade


def _book(c, title, size, runtime, audible=None):
    sid = c.execute("INSERT INTO series(name, norm, added_at) VALUES(?,?,'2026-01-01')", (title, db.norm(title))).lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", title, db.norm(title) + "en")).lastrowid
    return c.execute("INSERT INTO book(edition_id, lang, title, owned, size, runtime_min, audible_min, rel_path) VALUES(?,?,?,1,?,?,?,?)",
                     (eid, "en", title, size, runtime, audible, title)).lastrowid


def test_candidates_and_better_release(monkeypatch):
    db.init()
    c = db.conn()
    weak = _book(c, "Upgrade Schwach", 60 * 600 * 24_000 // 8, 600)   # 24 kbit/s
    good = _book(c, "Upgrade Gut", 60 * 600 * 64_000 // 8, 600)       # 64 kbit/s
    short = _book(c, "Upgrade Kurz", 60 * 400 * 64_000 // 8, 400, 500)
    ids = {x["id"]: x for x in upgrade.candidates()}
    assert ids[weak]["reason"] == "weak" and ids[short]["reason"] == "short" and good not in ids
    book = {"size": 60 * 600 * 24_000 // 8, "runtime_min": 600}
    same = {"verdict": "sicher", "size": 60 * 600 * 26_000 // 8, "guid": "a"}
    better = {"verdict": "sicher", "size": 60 * 600 * 64_000 // 8, "guid": "b", "title": "Besser", "score": 90}
    unsure = {"verdict": "unsicher", "size": 10**10, "guid": "c"}
    assert upgrade.better_release(book, [unsure, same, better]) is better
    assert upgrade.better_release(book, [same]) is None

    grabbed = []
    monkeypatch.setattr(search, "guided", lambda bid, early_stop=False: {"results": [better]})
    monkeypatch.setattr(search, "grab", lambda bid, rel, auto=False, upgrade=False: grabbed.append((bid, upgrade)) or 1)
    db.set_setting("auto_upgrade", "0")
    assert "ausgeschaltet" in upgrade.run()
    r = upgrade.search_one(weak)
    assert r["result"] == "started" and grabbed == [(weak, True)]
    row = c.execute("SELECT attempts, next_search FROM upgrade_search WHERE book_id=?", (weak,)).fetchone()
    assert row["attempts"] == 1 and row["next_search"]

    # Knopf «Suchen» und Automatik gleichzeitig: der zweite Lauf überschreibt «started» nicht mit einem Fehler
    def busy(bid, rel, auto=False, upgrade=False):
        raise ValueError("Für dieses Buch läuft schon ein Download")
    monkeypatch.setattr(search, "grab", busy)
    calls = iter([None, {"id": 1}])
    monkeypatch.setattr(search, "active_download", lambda bid: next(calls))
    assert upgrade.search_one(weak)["result"] == "skipped"
    assert c.execute("SELECT last_result FROM upgrade_search WHERE book_id=?", (weak,)).fetchone()["last_result"] == "started"
    c.execute("DELETE FROM book WHERE id IN (?,?,?)", (weak, good, short))
    c.execute("DELETE FROM upgrade_search")
