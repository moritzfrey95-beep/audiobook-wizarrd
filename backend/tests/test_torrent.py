import hashlib

from abw import db, torrent

INFO = b"d6:lengthi12e4:name8:book.m4b12:piece lengthi16384e6:pieces20:" + b"x" * 20 + b"e"
FILE = b"d8:announce14:http://tracker13:creation datei1700000000e4:info" + INFO + b"e"


def test_infohash_aus_datei_und_magnet():
    h = hashlib.sha1(INFO).hexdigest()
    assert torrent.info_hash({"file": FILE}) == h
    assert torrent.info_hash({"magnet": f"magnet:?xt=urn:btih:{h.upper()}&dn=x"}) == h
    assert torrent.info_hash({"magnet": "magnet:?xt=urn:btih:AAAQEAYEAUDAOCAJBIFQYDIOB4IBCEQT&dn=x"}) == bytes(range(20)).hex()
    assert torrent.info_hash({"file": b"kaputt"}) is None


class FakeQbit:
    def __init__(self, t):
        self.t, self.calls = t, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def by_hash(self, h):
        return self.t if self.t and self.t["hash"] == h else None

    def take_over(self, t, marker):
        self.calls.append(("take_over", marker))

    def add(self, item, marker):
        self.calls.append(("add", marker))


def test_vorhandener_torrent_wird_uebernommen_wenn_frei(monkeypatch):
    db.init()
    c = db.conn()
    c.execute("DELETE FROM download")
    for marker, state in (("abw-1-alt", "rejected"), ("abw-2-laeuft", "review")):
        c.execute("INSERT INTO download(book_id, source, release_title, marker, state, created, updated) VALUES(1,'audiobookbay','x',?,?,'t','t')",
                  (marker, state))
    h = hashlib.sha1(INFO).hexdigest()
    monkeypatch.setattr(torrent, "fetch", lambda guid: {"file": FILE})
    q = FakeQbit({"hash": h, "tags": "abw-1-alt", "progress": 1})
    monkeypatch.setattr(torrent, "Qbit", lambda: q)
    assert torrent.submit("g", "abw-3-neu") == h
    assert q.calls == [("take_over", "abw-3-neu")]

    q.t["tags"] = "abw-2-laeuft"  # Dateien werden noch gebraucht: nicht wegnehmen
    import pytest
    with pytest.raises(ValueError, match="noch läuft"):
        torrent.submit("g", "abw-4-neu")


def test_deutscher_serienname_aus_dem_untertitel():
    from abw.usenet import queries, subtitle_series
    b = dict(title="Eine Faust voller Credits", subtitle="System-Apokalypse – Gnadenlos 1", author="Tao Wong, Craig Hamilton",
             series="System Apocalypse - Relentless [German Edition]", sequence="1")
    from abw.usenet import slow_queries
    assert queries(b)[2] == "System-Apokalypse Gnadenlos 01"
    assert slow_queries(b) == ["System-Apokalypse Gnadenlos 01", "Tao Wong Eine Faust voller Credits"]
    assert subtitle_series("Ein Dark Fantasy LitRPG Abenteuer (Das Große Spiel, Buch 8)", "8") == "Das Große Spiel"
    assert subtitle_series("Eine VR-MMORPG LitRPG-Trilogie (Eternal Online 3) [A LitRPG Adventure (Eternal Online, Book 3)]", "3") == "Eternal Online"
    assert subtitle_series("A LitRPG Adventure", "2") is None
    # gleicher Name wie die Serie: keine zusätzliche Suche
    assert len(queries(dict(title="Wolf", subtitle="Cradle, Book 3", author="Will Wight", series="Cradle", sequence="3"))) == 5
    assert "Cradle 03" in queries(dict(title="Wolf", subtitle="Cradle, Book 3", author="Will Wight", series="Cradle", sequence="3"))


def test_englischer_zusatz_in_eckigen_klammern_zaehlt_nicht_zum_titel():
    from abw import scoring
    from abw.usenet import queries
    b = dict(title="Drifter der Dungeonwelt [Drifter of the Dungeon World]", subtitle="System-Apokalypse – Gnadenlos 2",
             author="Tao Wong, Craig Hamilton", series="System Apocalypse - Relentless [German Edition]", sequence="2", lang="de", runtime_min=None)
    r = scoring.score(b, {"title": "Tao Wong, Craig Hamilton - System-Apokalypse - Gnadenlos 02 - Drifter der Dungeonwelt (Ungekürzt) [German]", "size": None})
    assert any(x["text"] == "Titel passt" for x in r["reasons"])
    assert queries(b)[1] == "Drifter der Dungeonwelt"


def test_abb_sucht_auch_den_seriennamen():
    """ABB verfälscht ein Wort im Titel («A Shealers Gift»); der Serienname ohne Nummer findet den Band trotzdem."""
    from abw import usenet
    q = usenet.abb_queries({"title": "A Healer's Gift", "author": "Tao Wong", "series": "Adventures on Brad", "sequence": "1"})
    assert len(q) == 3 and "Adventures on Brad" in q[:2]
    assert usenet.abb_queries({"title": "Dune", "author": "Frank Herbert", "series": "", "sequence": ""}) == ["Frank Herbert Dune", "Dune"]
