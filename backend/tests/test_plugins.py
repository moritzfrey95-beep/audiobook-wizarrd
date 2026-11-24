"""Erweiterungen: zusätzliche Quelle aus einem Ordner laden, suchen, starten, verfolgen, ablegen."""
import textwrap

import pytest

from abw import activity, cleanup, db, importer, plugins, search, torrent, usenet

PLUGIN = textwrap.dedent('''
    from pathlib import Path

    class Quelle:
        key, name, order = "beispiel", "Beispielquelle", 3

        def __init__(self, root):
            self.root = Path(root)
            self.calls = []

        def configured(self):
            return True

        def active_for(self, book):
            return book["lang"] == "en"

        def search(self, queries):
            self.calls.append(("search", queries))
            return [{"guid": "b1", "source": self.key, "title": "Joe Abercrombie - Half a King", "indexer": "Beispiel",
                     "size": 300_000_000}], []

        def refresh(self, guid):
            self.calls.append(("refresh", guid))

        def submit(self, guid, marker):
            self.calls.append(("submit", guid, marker))
            return "client-1"

        def status(self, rows):
            return {r["id"]: {"state": "downloading", "progress": 0.5} for r in rows}

        def can_start(self):
            return True

    def register(abw):
        import os
        abw.add_source(Quelle(os.environ["BEISPIEL_ROOT"]))
''')


@pytest.fixture
def quelle(tmp_path, monkeypatch):
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "beispiel_quelle.py").write_text(PLUGIN)
    (folder / "kaputt.py").write_text("raise RuntimeError('geht nicht')\n")
    monkeypatch.setenv("BEISPIEL_ROOT", str(tmp_path / "fertig"))
    plugins.reset()
    plugins.load(str(folder))
    yield plugins.get("beispiel")
    plugins.reset()


def test_laden_und_fehler_merken(quelle):
    assert quelle and [s.key for s in plugins.sources()] == ["beispiel"]
    assert plugins.errors and plugins.errors[0].startswith("kaputt:")
    with pytest.raises(ValueError):
        plugins.add_source(type("X", (), {"key": "usenet"})())


def test_suche_start_und_verfolgung(quelle, monkeypatch):
    db.init()
    c = db.conn()
    bid = c.execute("INSERT INTO book(lang, title, author, owned) VALUES('en','Half a King','Joe Abercrombie',0)").lastrowid
    monkeypatch.setattr(usenet, "configured", lambda: False)
    monkeypatch.setattr(torrent, "configured", lambda: False)
    res = search.guided(bid)
    assert {"key": "beispiel", "name": "Beispielquelle", "active": True} in res["sources"]
    assert res["results"][0]["source"] == "beispiel" and res["can_start"]
    assert quelle.calls[0][0] == "search"

    did = search.grab(bid, res["results"][0])
    assert ("refresh", "b1") in quelle.calls and quelle.calls[-1][:2] == ("submit", "b1")
    row = c.execute("SELECT source, client_id FROM download WHERE id=?", (did,)).fetchone()
    assert tuple(row) == ("beispiel", "client-1")
    search.track()
    assert c.execute("SELECT state, progress FROM download WHERE id=?", (did,)).fetchone()["progress"] == 0.5

    assert importer.source_root("beispiel") == quelle.root
    assert quelle.root in cleanup.roots()
    assert activity.source_name("beispiel") == "Beispielquelle"


def test_nicht_fuer_diesen_band(quelle, monkeypatch):
    db.init()
    bid = db.conn().execute("INSERT INTO book(lang, title, author, owned) VALUES('de','Ein Buch','Jemand',0)").lastrowid
    monkeypatch.setattr(usenet, "configured", lambda: False)
    monkeypatch.setattr(torrent, "configured", lambda: False)
    res = search.guided(bid)
    assert {"key": "beispiel", "name": "Beispielquelle", "active": False} in res["sources"] and not quelle.calls
