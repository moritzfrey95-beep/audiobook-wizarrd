"""Abgelegte Downloads werden schon beim Bestandsabgleich abgeschlossen, sobald Audiobookshelf das Buch kennt,
nicht erst am Ende einer (womöglich stundenlangen) Ankunftsprüfung."""
import os

os.environ["ABW_NO_TASKS"] = "1"

import pytest

from abw import abs as abs_mod
from abw import config, db


def resp(data):
    return type("R", (), {"raise_for_status": lambda s: s, "json": lambda s: data})()


class FakeAbs:
    items: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path, **kw):
        if path == "/api/me":
            return resp({"mediaProgress": []})
        return resp({"results": self.items, "total": len(self.items)})


def item(iid, asin, title):
    return {"id": iid, "relPath": title, "media": {"numAudioFiles": 1, "duration": 3600, "size": 1,
                                                   "metadata": {"title": title, "asin": asin, "authorName": "A"}}}


@pytest.fixture
def lib(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("download", "book"):
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(abs_mod, "_client", lambda: FakeAbs())
    monkeypatch.setitem(config.ABS_LIBRARIES, "en", "lib-en")
    monkeypatch.setitem(config.ABS_LIBRARIES, "de", "")

    def add(asin, title, state="imported"):
        bid = c.execute("INSERT INTO book(lang, asin, title, owned) VALUES('en',?,?,0)", (asin, title)).lastrowid
        did = c.execute("INSERT INTO download(book_id, source, release_title, marker, state, imported_to, created, updated)"
                        " VALUES(?, 'usenet', 'x', ?, ?, '/library-en/x', datetime('now'), datetime('now'))",
                        (bid, f"abw-{asin}", state)).lastrowid
        return did
    return c, add


def state(c, did):
    return c.execute("SELECT state FROM download WHERE id=?", (did,)).fetchone()["state"]


def test_bekanntes_buch_wird_beim_abgleich_abgeschlossen(lib):
    c, add = lib
    da = add("B000000001", "Angekommen")
    dw = add("B000000002", "Noch nicht eingelesen")
    dv = add("B000000003", "Erst geprüft", state="verified")
    FakeAbs.items = [item("i-1", "B000000001", "Angekommen"), item("i-3", "B000000003", "Erst geprüft")]
    abs_mod.sync()
    assert state(c, da) == "done"
    assert state(c, dw) == "imported"  # Audiobookshelf kennt es noch nicht: wartet weiter
    assert state(c, dv) == "verified"  # nur abgelegte Downloads werden abgeschlossen


class FakeScan:
    calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path, **kw):
        return resp({"folders": [{"fullPath": "/audiobooks/Hörbücher Englisch"}]})

    def post(self, path, json=None):
        self.calls.append((path, json))
        return resp({})


@pytest.fixture
def scanner(monkeypatch, tmp_path):
    FakeScan.calls = []
    monkeypatch.setattr(abs_mod, "_client", lambda: FakeScan())
    monkeypatch.setitem(config.ABS_LIBRARIES, "en", "lib-en")
    monkeypatch.setitem(config.LIBRARY_ROOT, "en", tmp_path)
    monkeypatch.setattr(abs_mod, "_abs_root", {})
    monkeypatch.setattr(abs_mod, "_nudged", {})
    book = tmp_path / "Serie" / "Book 1 - Titel"
    book.mkdir(parents=True)
    (book / "cover.jpg").write_bytes(b"x")
    (book / "Titel.m4b").write_bytes(b"x")
    return book


def test_gezielter_scan_meldet_eine_datei_des_buchs(scanner):
    abs_mod.scan("en", scanner)
    assert FakeScan.calls == [("/api/watcher/update", {"libraryId": "lib-en", "type": "add",
                                                       "path": "/audiobooks/Hörbücher Englisch/Serie/Book 1 - Titel/Titel.m4b"})]


def test_ohne_pfad_oder_ausserhalb_der_bibliothek_ganzer_scan(scanner, tmp_path_factory):
    abs_mod.scan("en")
    abs_mod.scan("en", tmp_path_factory.mktemp("fremd"))
    assert FakeScan.calls == [("/api/libraries/lib-en/scan", None)] * 2


def test_wartende_ablage_wird_nach_15_minuten_erneut_gemeldet(scanner):
    db.init()
    c = db.conn()
    for t in ("download", "book"):
        c.execute(f"DELETE FROM {t}")
    bid = c.execute("INSERT INTO book(lang, asin, title, owned) VALUES('en','B1','Titel',0)").lastrowid
    for marker, ago in (("alt", "-20 minutes"), ("neu", "-5 minutes")):
        c.execute("INSERT INTO download(book_id, source, release_title, marker, state, imported_to, created, updated)"
                  " VALUES(?, 'usenet', 'x', ?, 'imported', ?, datetime('now'), strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now', ?))",
                  (bid, marker, str(scanner), ago))
    assert abs_mod.nudge_waiting() == 1
    assert abs_mod.nudge_waiting() == 0  # nicht bei jedem Abgleich wieder, erst nach weiteren 15 Minuten
    assert [p for p, _ in FakeScan.calls] == ["/api/watcher/update"]
