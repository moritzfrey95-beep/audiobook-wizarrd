"""Gesperrter Usenet-Indexer (API-Limit in Prowlarr): «keine Treffer» ist dann nicht endgültig."""
from datetime import datetime, timedelta, timezone

from abw import config, db, search, usenet, torrent


def _setup(monkeypatch, paused):
    db.init()
    c = db.conn()
    c.execute("DELETE FROM book_search")
    bid = c.execute("INSERT INTO book(lang, title, author, owned, wanted) VALUES('en','Half a King','Joe Abercrombie',0,1)").lastrowid
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "sab_configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: ([], []))
    monkeypatch.setattr(usenet, "paused", lambda: paused)
    monkeypatch.setattr(torrent, "configured", lambda: False)
    db.set_setting("auto_search", "1")
    return c, bid


def _row(c, bid):
    return c.execute("SELECT attempts, next_search, last_result FROM book_search WHERE book_id=?", (bid,)).fetchone()


def test_no_hits_while_indexer_paused_retries_after_the_lock(monkeypatch):
    until = datetime.now(timezone.utc) + timedelta(hours=3)
    c, bid = _setup(monkeypatch, [{"indexer": "altHUB", "until": until.isoformat().replace("+00:00", "Z")}])
    assert search.guided(bid)["paused"][0]["indexer"] == "altHUB"
    search.auto_run()
    r = _row(c, bid)
    assert r["last_result"] == "none" and r["attempts"] == 0  # Wartezeit wird nicht hochgezählt
    assert abs(datetime.fromisoformat(r["next_search"]) - (until + timedelta(minutes=10))) < timedelta(seconds=5)


def test_no_hits_without_lock_waits_as_before(monkeypatch):
    c, bid = _setup(monkeypatch, [])
    search.auto_run()
    r = _row(c, bid)
    assert r["attempts"] == 1 and datetime.fromisoformat(r["next_search"]) > datetime.now(timezone.utc) + timedelta(hours=23)


def test_long_lock_never_waits_longer_than_the_normal_pause(monkeypatch):
    until = datetime.now(timezone.utc) + timedelta(days=5)
    c, bid = _setup(monkeypatch, [{"indexer": "altHUB", "until": until.isoformat()}])
    search.auto_run()
    assert datetime.fromisoformat(_row(c, bid)["next_search"]) < datetime.now(timezone.utc) + timedelta(days=1, minutes=1)


def test_paused_reads_prowlarr_status_for_usenet_indexers_only(monkeypatch):
    later = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    class Resp:
        def __init__(self, data): self.data = data
        def raise_for_status(self): return self
        def json(self): return self.data

    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url):
            if url.endswith("/indexerstatus"):
                return Resp([{"indexerId": 20, "disabledTill": later}, {"indexerId": 19, "disabledTill": past},
                             {"indexerId": 5, "disabledTill": later}])
            return Resp([{"id": 19, "name": "NZBgeek"}, {"id": 20, "name": "altHUB"}, {"id": 5, "name": "Internet Archive"}])

    monkeypatch.setattr(config, "USENET_INDEXER_IDS", [18, 19, 20])
    monkeypatch.setattr(usenet.httpx, "Client", Client)
    monkeypatch.setattr(usenet, "_paused", (0.0, []))
    assert usenet.paused() == [{"indexer": "altHUB", "until": later}]

    class Down(Client):
        def get(self, url): raise OSError("Prowlarr weg")
    monkeypatch.setattr(usenet.httpx, "Client", Down)
    monkeypatch.setattr(usenet, "_paused", (0.0, []))
    assert usenet.paused() == []
