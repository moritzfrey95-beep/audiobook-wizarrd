from datetime import datetime, timedelta, timezone

from abw import activity, db


def _ts(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def test_fenster_1_und_7_tage():
    db.init()
    c = db.conn()
    for t in ("download", "book_search", "convert_job", "chapter_job", "book"):
        c.execute(f"DELETE FROM {t}")
    rows = [("usenet", "done", 0.2), ("usenet", "done", 3), ("audiobookbay", "failed", 0.5), ("audiobookbay", "done", 9), ("beispiel", "rejected", 6)]
    for i, (src, state, age) in enumerate(rows):
        c.execute("INSERT INTO download(book_id, source, release_title, size, marker, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
                  (i + 1, src, "x", 2_000_000_000, f"m{i}", state, _ts(age), _ts(age)))
    c.execute("INSERT INTO book_search(book_id, last_search) VALUES(1, ?)", (_ts(0.1),))
    c.execute("INSERT INTO book_search(book_id, last_search) VALUES(2, ?)", (_ts(5),))
    # Schreibweise ohne «T», wie sie datetime('now') liefert
    c.execute("INSERT INTO chapter_job(book_id, state, at) VALUES(1, 'done', datetime('now','-2 hours'))")
    c.execute("INSERT INTO book(lang, title, owned, added_at) VALUES('en', 'A', 1, ?)", (_ts(0.5),))
    a = activity.overview()
    assert (a["d1"]["placed"], a["d7"]["placed"]) == (1, 2)
    assert (a["d1"]["started"], a["d7"]["started"]) == (2, 4)
    assert (a["d1"]["failed"], a["d7"]["failed"]) == (1, 1)
    assert (a["d1"]["rejected"], a["d7"]["rejected"]) == (0, 1)
    assert (a["d1"]["searched"], a["d7"]["searched"]) == (1, 2)
    assert a["d1"]["chapters"] == 1 and a["d1"]["added"] == 1
    assert a["d7"]["placed_gb"] == 4.0
    assert {s["source"]: (s["started"], s["placed"]) for s in a["d7"]["sources"]} == {"usenet": (2, 2), "audiobookbay": (1, 0), "beispiel": (1, 0)}
