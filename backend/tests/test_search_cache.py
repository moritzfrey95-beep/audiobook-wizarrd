"""Gespeicherte Suche: zweiter Aufruf fragt die Quellen nicht neu, gesperrte Treffer werden trotzdem markiert."""
from abw import db, search, usenet, torrent


def test_stored_results_are_returned_without_searching_again(monkeypatch):
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned) VALUES('en','Half a King','Joe Abercrombie',0)").lastrowid
    calls = []
    rel = {"guid": "g1", "source": "usenet", "title": "Joe Abercrombie - Half a King", "indexer": "X", "size": 300_000_000}
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: (calls.append(1) or [dict(rel)], []))
    monkeypatch.setattr(torrent, "configured", lambda: False)

    first = search.guided(bid)
    again = search.guided(bid, fresh=False)
    assert len(calls) == 1 and again["cached"] and not first["cached"]
    assert again["results"][0]["guid"] == "g1" and again["results"][0]["verdict"] == first["results"][0]["verdict"] != "verworfen"

    c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
              (bid, "usenet", "R", "g1", "abw-test-cache", "failed", "2026", "2026"))
    assert search.guided(bid, fresh=False)["results"][0]["verdict"] == "verworfen"
    assert search.guided(bid, fresh=True) and len(calls) == 2


def test_dismiss_declines_unsure_hits_until_a_new_one_appears(monkeypatch):
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned) VALUES('en','Half a King','Joe Abercrombie',0)").lastrowid
    hits = [{"guid": "u1", "source": "usenet", "title": "Joe Abercrombie - Half a King", "indexer": "X", "size": 300_000_000}]
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: ([dict(h) for h in hits], []))
    monkeypatch.setattr(torrent, "configured", lambda: False)
    assert search.guided(bid)["results"][0]["verdict"] == "unsicher"
    before = search.guided(bid)["results"][0]["score"]
    c.execute("INSERT INTO book_search(book_id, attempts, last_search, last_result, hits, unsure) VALUES(?,1,'2026','unsure',1,1)", (bid,))
    assert search.dismiss(bid) == 1
    # Abgelehnt heisst seit 13.11.: 5 Punkte Abzug, kein hartes Aus (gespeichert und nach neuer Suche)
    for res in (search.guided(bid, fresh=False), search.guided(bid)):
        r = res["results"][0]
        assert r["score"] == before - 5 and r["declined"] and r["reasons"][0]["points"] == -5
        assert not res["listed"]  # bringt den Band nicht zurück unter «Treffer wählen»
    assert search.open_unsure(search.guided(bid)["results"]) == 0
    hits.append({"guid": "u2", "source": "usenet", "title": "Half a King - Joe Abercrombie", "indexer": "X", "size": 300_000_000})
    res = search.guided(bid)["results"]
    assert {r["guid"]: bool(r.get("declined")) for r in res} == {"u1": True, "u2": False} and search.open_unsure(res) == 1


def test_declined_hit_is_never_sure_and_drops_below_threshold():
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned) VALUES('en','X','Y',0)").lastrowid
    c.execute("INSERT INTO book_reject(book_id, guid, at) VALUES(?,?,?)", (bid, "a", "2026"))
    c.execute("INSERT INTO book_reject(book_id, guid, at) VALUES(?,?,?)", (bid, "b", "2026"))
    rs = [{"guid": "a", "verdict": "sicher", "score": 90, "reasons": []}, {"guid": "b", "verdict": "unsicher", "score": 42, "reasons": []}]
    search._mark_blocked(bid, rs)
    assert [(r["verdict"], r["score"]) for r in rs] == [("unsicher", 85), ("verworfen", 37)]


def test_listed_band_without_unsure_hits_can_still_be_dismissed(monkeypatch):
    """Band steht unter «Treffer wählen», die gespeicherte Suche zeigt aber keinen unsicheren Treffer mehr (Sword Art Online 15)."""
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned) VALUES('en','Sword Art Online 15','Reki Kawahara',0)").lastrowid
    hits = [{"guid": "e1", "source": "usenet", "title": "Sword Art Online 15 Reki Kawahara EPUB", "indexer": "X", "size": 2_000_000}]
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: ([dict(h) for h in hits], []))
    monkeypatch.setattr(torrent, "configured", lambda: False)
    listed = lambda: c.execute("SELECT last_result FROM book_search WHERE book_id=?", (bid,)).fetchone()["last_result"] == "unsure"
    mark = lambda: c.execute("INSERT OR REPLACE INTO book_search(book_id, attempts, last_search, next_search, last_result, hits, unsure)"
                             " VALUES(?,1,'2026','2026','unsure',1,1)", (bid,))
    mark()
    c.execute("INSERT INTO book_result(book_id, searched_at, payload) VALUES(?,?,?)",
              (bid, "2026", '{"results": [{"guid": "e1", "title": "Sword Art Online 15 Reki Kawahara EPUB", "verdict": "verworfen", "score": 0, "reasons": []}]}'))
    stored = search.guided(bid, fresh=False)
    assert stored["listed"] and not any(r["verdict"] == "unsicher" for r in stored["results"])
    assert search.dismiss(bid) == 0 and not listed()
    # Eine frische Suche ohne unsicheren Treffer nimmt den Band von selbst aus der Liste
    mark()
    out = search.guided(bid)
    assert all(r["verdict"] == "verworfen" for r in out["results"]) and not out["listed"] and not listed()


def test_search_carries_current_book_details(monkeypatch):
    """Kopf der Trefferauswahl: Sprecher, Sprache und Erscheinungsdatum kommen auch bei gespeicherter Suche frisch mit."""
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, narrator, sequence, release_date, owned)"
                    " VALUES('en','Half a King','Joe Abercrombie','Ben Elliot','1','2014-07-08',0)").lastrowid
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: ([], []))
    monkeypatch.setattr(torrent, "configured", lambda: False)
    search.guided(bid)
    c.execute("UPDATE book SET narrator='Neu' WHERE id=?", (bid,))
    info = search.guided(bid, fresh=False)["book"]
    assert info["narrator"] == "Neu" and info["lang"] == "en" and info["release_date"] == "2014-07-08" and info["sequence"] == "1"


def test_hidden_hit_is_marked_skipped_by_automation_and_expires(monkeypatch):
    """«1 Monat ausblenden»: Treffer bleibt in der Antwort (markiert), die Automatik startet ihn nicht, nach Ablauf wieder normal."""
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned, runtime_min) VALUES('en','Half a King','Joe Abercrombie',0,600)").lastrowid
    hits = [{"guid": "h1", "source": "usenet", "title": "Joe Abercrombie - Half a King", "indexer": "X", "size": 300_000_000}]
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: ([dict(h) for h in hits], []))
    monkeypatch.setattr(torrent, "configured", lambda: False)
    monkeypatch.setattr(search, "active_download", lambda book_id: None)
    grabbed = []
    monkeypatch.setattr(search, "grab", lambda book_id, r, auto=False: grabbed.append(r["guid"]))
    row = c.execute("SELECT id, title, sequence FROM book WHERE id=?", (bid,)).fetchone()
    assert search.guided(bid)["results"][0]["verdict"] == "sicher"

    until = search.hide(bid, "h1")
    for res in (search.guided(bid, fresh=False), search.guided(bid)):
        assert res["results"][0]["hidden_until"] == until
    assert search.auto_one(row)["result"] == "none" and not grabbed
    assert search.best_sure(search.guided(bid, fresh=False)["results"]) is None  # gleiche Wahl im Suchlauf auto_run

    c.execute("UPDATE book_hide SET until='2000-01-01T00:00:00+00:00' WHERE book_id=?", (bid,))
    assert "hidden_until" not in search.guided(bid, fresh=False)["results"][0]
    assert search.auto_one(row)["result"] == "started" and grabbed == ["h1"]
    search.unhide(bid, "h1")
    assert not c.execute("SELECT 1 FROM book_hide WHERE book_id=?", (bid,)).fetchone()


def test_hidden_unsure_hit_does_not_list_the_band(monkeypatch):
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned) VALUES('en','Half a King','Joe Abercrombie',0)").lastrowid
    monkeypatch.setattr(usenet, "configured", lambda: True)
    monkeypatch.setattr(usenet, "search", lambda book, enough=None: ([{"guid": "u1", "source": "usenet", "title": "Joe Abercrombie - Half a King",
                                                                        "indexer": "X", "size": 300_000_000}], []))
    monkeypatch.setattr(torrent, "configured", lambda: False)
    assert search.open_unsure(search.guided(bid)["results"]) == 1
    search.hide(bid, "u1")
    assert search.open_unsure(search.guided(bid)["results"]) == 0
