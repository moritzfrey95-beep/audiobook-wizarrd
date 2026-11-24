"""Automatik hört auf, sobald ein sicherer Einzelband-Treffer da ist (Indexer-Kontingent schonen, Entscheid 16.11.)."""
from abw import config, db, search, usenet, torrent

SAFE = {"guid": "s1", "source": "usenet", "title": "Joe Abercrombie - Half a King", "indexer": "X", "size": 300_000_000}


class _Resp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        return self

    def json(self):
        return self.rows


class _Client:
    """Prowlarr-Ersatz: liefert je Suchbegriff die Treffer aus by_query und merkt sich die Begriffe."""
    def __init__(self, by_query, asked):
        self.by_query, self.asked = by_query, asked

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None):
        self.asked.append(params["query"])
        return _Resp([{**r, "protocol": "usenet", "indexerId": 18} for r in self.by_query.get(params["query"], [])])


def _prowlarr(monkeypatch, by_query):
    asked: list[str] = []
    monkeypatch.setattr(usenet.httpx, "Client", lambda **kw: _Client(by_query, asked))
    monkeypatch.setattr(config, "USENET_INDEXER_IDS", [18])
    monkeypatch.setattr(config, "secret", lambda k: "x")
    return asked


def _book():
    return {"title": "Half a King", "author": "Joe Abercrombie", "series": "Shattered Sea", "sequence": "1", "subtitle": ""}


def test_usenet_search_stops_after_the_query_that_was_enough(monkeypatch):
    qs = usenet.queries(_book())
    assert len(qs) >= 3
    asked = _prowlarr(monkeypatch, {qs[0]: [SAFE]})
    found, _ = usenet.search(_book(), enough=lambda f: any(r["guid"] == "s1" for r in f))
    assert asked == qs[:1] and [r["guid"] for r in found] == ["s1"]


def test_usenet_search_without_enough_asks_every_query(monkeypatch):
    qs = usenet.queries(_book())
    asked = _prowlarr(monkeypatch, {qs[0]: [SAFE]})
    usenet.search(_book())
    assert asked == qs


def _setup(monkeypatch, hits):
    db.init()
    c = db.conn()
    # mit Laufzeit, sonst ist der Treffer nur unsicher
    bid = c.execute("INSERT INTO book(lang, title, author, owned, runtime_min) VALUES('en','Half a King','Joe Abercrombie',0,600)").lastrowid
    seen = []

    def fake(book, enough=None):
        seen.append(enough)
        return [dict(h) for h in hits], []
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", fake)
    monkeypatch.setattr(usenet, "paused", lambda: [])
    torrent_calls = []
    monkeypatch.setattr(torrent, "configured", lambda: True)
    monkeypatch.setattr(torrent, "search", lambda q: (torrent_calls.append(q) or [], []))
    return c, bid, seen, torrent_calls


def test_guided_by_hand_searches_everything(monkeypatch):
    c, bid, seen, torrent_calls = _setup(monkeypatch, [SAFE])
    res = search.guided(bid)
    assert seen == [None] and len(torrent_calls) == 1 and res["results"][0]["verdict"] == "sicher"


def test_automatic_search_skips_further_sources_after_a_safe_hit(monkeypatch):
    c, bid, seen, torrent_calls = _setup(monkeypatch, [SAFE])
    res = search.guided(bid, early_stop=True)
    assert seen[0] is not None and seen[0]([SAFE]) and torrent_calls == []
    assert res["results"][0]["verdict"] == "sicher"


def test_failed_or_declined_hit_is_not_enough(monkeypatch):
    c, bid, seen, torrent_calls = _setup(monkeypatch, [SAFE])
    c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
              (bid, "usenet", "R", "s1", "abw-test-early", "failed", "2026", "2026"))
    search.guided(bid, early_stop=True)
    assert not seen[0]([SAFE]) and len(torrent_calls) == 1
