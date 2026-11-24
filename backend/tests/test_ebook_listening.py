"""E-Books zu Serien, die gerade gehört werden: zuerst gesucht, gehörter Band und folgende vorn."""
from datetime import datetime, timedelta, timezone

from abw import config, db, ebook


def _setup(monkeypatch, updated=None):
    db.init()
    c = db.conn()
    for t in ("user_progress", "ebook_search", "ebook_job", "book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(config, "IMPORT_ENABLED", True)
    updated = updated or datetime.now(timezone.utc).isoformat()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('S','s')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", "S", "s")).lastrowid
    ids = {}
    for n in range(1, 6):
        ids[n] = c.execute("INSERT INTO book(edition_id, lang, title, seq, owned, rel_path, epub) VALUES(?,?,?,?,1,?,?)",
                           (eid, "en", f"B{n}", float(n), f"S/{n}", 1 if n == 4 else 0)).lastrowid
    ids[6] = c.execute("INSERT INTO book(edition_id, lang, title, seq, owned, release_date) VALUES(?,?,?,?,0,'2200-01-01')",
                       (eid, "en", "B6", 6.0)).lastrowid
    ids[7] = c.execute("INSERT INTO book(edition_id, lang, title, seq, owned, release_date) VALUES(?,?,?,?,0,'2020-01-01')",
                       (eid, "en", "B7", 7.0)).lastrowid  # erschienen, fehlt: kein Ordner, kein EPUB
    c.execute("INSERT INTO user_progress VALUES(3,?,0.01,0,?)", (ids[3], updated))  # anderer Benutzer fängt Band 3 an
    return c, ids


def test_gehoerte_serie_zuerst_ab_dem_gehoerten_band(monkeypatch):
    c, ids = _setup(monkeypatch)
    # Band 3 und die folgenden (4 hat schon ein EPUB), dann der angekündigte, dann die früheren
    assert ebook.listening_candidates(10) == [ids[3], ids[5], ids[6], ids[1], ids[2]]
    assert ebook.listening_candidates() == [ids[3], ids[5], ids[6]]
    # Wartezeit nach erfolgloser Suche gilt weiter
    later = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    c.execute("INSERT INTO ebook_search(book_id, attempts, next_search) VALUES(?,1,?)", (ids[5], later))
    assert ebook.listening_candidates(10) == [ids[3], ids[6], ids[1], ids[2]]


def test_lange_nicht_gehoert_zaehlt_nicht(monkeypatch):
    _setup(monkeypatch, updated="2026-01-01T00:00:00+00:00")
    assert ebook.listening_candidates(10) == []


def test_automatik_sucht_gehoerte_serie_vor_den_anderen(monkeypatch):
    c, ids = _setup(monkeypatch)
    for k in ("auto_ebook", "auto_ebook_upcoming"):
        db.set_setting(k, "0")
    db.set_setting("auto_ebook_listening", "1")
    asked = []
    monkeypatch.setattr(ebook, "request", lambda book_id, auto=False: asked.append(book_id) or {"started": False})
    msg = ebook.auto()
    assert asked == [ids[3], ids[5], ids[6]] and msg.startswith("3 Bände gerade gehörter Serien")
    db.set_setting("auto_ebook_listening", "0")
    assert ebook.auto() == "Alle E-Book-Schalter sind aus"
