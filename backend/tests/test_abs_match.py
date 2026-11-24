"""Von ABW abgelegte Bücher werden in Audiobookshelf gematcht: einmal je Buch, alles von Audible ausser Serie und ASIN."""
import json
import os

os.environ["ABW_NO_TASKS"] = "1"

import pytest

from abw import abs as abs_mod
from abw import db


def resp(data, code=200):
    return type("R", (), {"status_code": code, "raise_for_status": lambda s: s, "json": lambda s: data})()


class FakeAbs:
    calls: list = []
    answer: dict = {}  # item -> Antwort auf den Match
    series: dict = {}  # item -> Serien vor dem Match
    fail_patch = False
    tags: dict = {}  # item -> Tags in Audiobookshelf

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, path, **kw):
        item = path.split("/")[3]
        if item in self.answer and self.answer[item][1] == 404:
            return resp({}, 404)
        return resp({"media": {"tags": self.tags.get(item, []), "metadata": {"asin": "B0ORIGINAL", "series": [
            {"id": "s1", "name": n, "sequence": q} for n, q in self.series.get(item, [("Serie", "1")])]}}})

    def post(self, path, json=None):
        self.calls.append(("post", path, json))
        data, code = self.answer.get(path.split("/")[3], ({"updated": True}, 200))
        return resp(data, code)

    def patch(self, path, json=None):
        if self.fail_patch:
            raise ConnectionError("Audiobookshelf weg")
        self.calls.append(("patch", path, json))
        return resp({"updated": True})


@pytest.fixture
def lib(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("download", "book"):
        c.execute(f"DELETE FROM {t}")
    FakeAbs.calls, FakeAbs.answer, FakeAbs.series, FakeAbs.fail_patch, FakeAbs.tags = [], {}, {}, False, {}
    for t in ("user", "abs_tag", "watchlist", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setattr(abs_mod, "_client", lambda: FakeAbs())

    def add(item, asin, lang="en", imported=True, owned=1):
        bid = c.execute("INSERT INTO book(lang, asin, title, abs_item_id, owned) VALUES(?,?,?,?,?)",
                        (lang, asin, f"Buch {item}", item, owned)).lastrowid
        c.execute("INSERT INTO download(book_id, source, release_title, marker, state, imported_to, created, updated)"
                  " VALUES(?, 'usenet', 'x', ?, 'done', ?, datetime('now'), datetime('now'))", (bid, f"abw-{item}", "/library-en/x" if imported else None))
        return bid
    return c, add


def test_ueberschreibt_alles_und_setzt_serie_und_asin_zurueck(lib):
    c, add = lib
    en = add("i-en", "B000000001")
    de = add("i-de", "B000000002", lang="de")
    add("i-alt", "B000000003", imported=False)  # war schon vor ABW im Bestand
    add("i-ohne", "0")  # Platzhalter statt ASIN
    FakeAbs.series["i-de"] = [("The First Law", "9"), ("Zweite", None)]
    assert abs_mod.match_imported() == "2 gematcht"
    assert ("post", "/api/items/i-de/match", {"provider": "audible.de", "asin": "B0ORIGINAL", "overrideCover": True, "overrideDetails": True}) in FakeAbs.calls
    assert ("post", "/api/items/i-en/match", {"provider": "audible", "asin": "B0ORIGINAL", "overrideCover": True, "overrideDetails": True}) in FakeAbs.calls
    assert ("patch", "/api/items/i-de/media", {"metadata": {"series": [{"name": "The First Law", "sequence": "9"}, {"name": "Zweite", "sequence": None}],
                                                            "asin": "B0ORIGINAL"}}) in FakeAbs.calls
    assert all(c.execute("SELECT abs_match FROM book WHERE id=?", (b,)).fetchone()[0][:2] == "20" for b in (en, de))
    FakeAbs.calls.clear()
    assert abs_mod.match_imported() == "Nichts zu matchen"
    assert FakeAbs.calls == []


def test_ohne_serie_bleibt_ohne_serie(lib):
    c, add = lib
    add("i-1", "B000000001")
    FakeAbs.series["i-1"] = []
    abs_mod.match_imported()
    assert FakeAbs.calls[-1] == ("patch", "/api/items/i-1/media", {"metadata": {"series": [], "asin": "B0ORIGINAL"}})


def test_kein_treffer_wird_vermerkt_und_nicht_wiederholt(lib):
    c, add = lib
    b = add("i-1", "B000000001")
    FakeAbs.answer["i-1"] = ({"warning": "No audible match found"}, 200)
    assert abs_mod.match_imported() == "0 gematcht, 1 ohne Treffer"
    assert c.execute("SELECT abs_match FROM book WHERE id=?", (b,)).fetchone()[0] == "Fehler: No audible match found"
    assert not [x for x in FakeAbs.calls if x[0] == "patch"]
    assert abs_mod.match_imported() == "Nichts zu matchen"


def test_abbruch_nach_match_wird_beim_naechsten_lauf_zurueckgesetzt(lib):
    c, add = lib
    b = add("i-1", "B000000001")
    FakeAbs.series["i-1"] = [("Drachenmagus", "4")]
    FakeAbs.fail_patch = True
    with pytest.raises(ConnectionError):
        abs_mod.match_imported()
    saved = c.execute("SELECT abs_match FROM book WHERE id=?", (b,)).fetchone()[0]
    assert saved.startswith("offen: ") and json.loads(saved[7:])["series"] == [{"name": "Drachenmagus", "sequence": "4"}]
    FakeAbs.fail_patch, FakeAbs.calls = False, []
    FakeAbs.series["i-1"] = []  # Audible hat die Serie entfernt
    assert abs_mod.match_imported() == "1 gematcht"
    assert FakeAbs.calls == [("patch", "/api/items/i-1/media", {"metadata": {"series": [{"name": "Drachenmagus", "sequence": "4"}], "asin": "B0ORIGINAL"}})]


def test_stapel_und_rest(lib, monkeypatch):
    c, add = lib
    monkeypatch.setattr(abs_mod, "MATCH_BATCH", 2)
    for i in range(3):
        add(f"i-{i}", f"B00000000{i}")
    assert abs_mod.match_imported() == "2 gematcht, 1 warten auf den nächsten Lauf"
    assert abs_mod.match_imported() == "1 gematcht"


def test_verschwundener_eintrag_bleibt_offen(lib):
    c, add = lib
    b = add("i-weg", "B000000001")
    FakeAbs.answer["i-weg"] = ({}, 404)
    assert abs_mod.match_imported() == "0 gematcht, 1 warten auf den nächsten Lauf"
    assert c.execute("SELECT abs_match FROM book WHERE id=?", (b,)).fetchone()[0] is None


def test_tag_gewuenscht_von_benutzer(lib):
    c, add = lib
    lou = c.execute("INSERT INTO user(name, hash, role, created) VALUES('Lou', 'x', 'user', datetime('now'))").lastrowid
    c.execute("UPDATE user SET abs_user_id='abs-lou' WHERE id=?", (lou,))
    boss = c.execute("INSERT INTO user(name, hash, role, created) VALUES('Max', 'x', 'admin', datetime('now'))").lastrowid
    c.execute("UPDATE user SET abs_user_id='abs-max' WHERE id=?", (boss,))
    b = add("i-1", "B000000001")
    add("i-2", "B000000002")
    add("i-3", "B000000003")
    c.execute("UPDATE book SET wanted_by=? WHERE id=?", (lou, b))
    c.execute("UPDATE book SET wanted_by=? WHERE abs_item_id='i-2'", (boss,))  # Administratoren bekommen keinen Tag
    FakeAbs.tags["i-1"] = ["Fantasy"]
    assert abs_mod.match_imported() == "3 gematcht"
    tagged = [x for x in FakeAbs.calls if x[0] == "patch" and "tags" in x[2]]
    assert tagged == [("patch", "/api/items/i-1/media", {"tags": ["Fantasy", "Gewünscht von Lou"]})]


def test_tag_auch_ohne_treffer_und_nicht_doppelt(lib):
    c, add = lib
    lou = c.execute("INSERT INTO user(name, hash, role, created) VALUES('Lou', 'x', 'user', datetime('now'))").lastrowid
    c.execute("UPDATE user SET abs_user_id='abs-lou' WHERE id=?", (lou,))
    b = add("i-1", "B000000001")
    add("i-2", "B000000002")
    c.execute("UPDATE book SET wanted_by=? WHERE id IN (?, ?)", (lou, b, b + 1))
    FakeAbs.answer["i-1"] = ({"warning": "No audible match found"}, 200)
    FakeAbs.tags["i-2"] = ["Gewünscht von Lou"]
    assert abs_mod.match_imported() == "1 gematcht, 1 ohne Treffer"
    tagged = [x for x in FakeAbs.calls if x[0] == "patch" and "tags" in x[2]]
    assert tagged == [("patch", "/api/items/i-1/media", {"tags": ["Gewünscht von Lou"]})]


def test_ganze_serie_und_nachtragen(lib):
    c, add = lib
    lou = c.execute("INSERT INTO user(name, hash, role, abs_user_id, created) VALUES('Lou', 'x', 'user', 'abs-lou', datetime('now'))").lastrowid
    test = c.execute("INSERT INTO user(name, hash, role, created) VALUES('testnutzer', 'x', 'user', datetime('now'))").lastrowid
    sid = c.execute("INSERT INTO series(name, norm, added_at) VALUES('Poirot', 'poirot', datetime('now'))").lastrowid
    de = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?, 'de', 'Poirot', 'poirot')", (sid,)).lastrowid
    en = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?, 'en', 'Poirot', 'poirot')", (sid,)).lastrowid
    wished = add("i-1", "B000000001")
    other = add("i-2", "B000000002", imported=False)  # schon vor ABW im Bestand
    english = add("i-3", "B000000003", imported=False)
    single = add("i-4", "B000000004", imported=False)  # Einzelbuch ohne Serie
    c.execute("UPDATE book SET edition_id=? WHERE id IN (?, ?)", (de, wished, other))
    c.execute("UPDATE book SET edition_id=? WHERE id=?", (en, english))
    c.execute("UPDATE book SET wanted_by=? WHERE id=?", (lou, wished))
    c.execute("UPDATE book SET wanted_by=? WHERE id=?", (test, single))  # Konto ohne Audiobookshelf: kein Tag
    c.execute("UPDATE book SET abs_match='2026' WHERE id=?", (wished,))
    assert abs_mod.tag_wishes() == "3 Einträge getaggt"
    tagged = sorted(x[1] for x in FakeAbs.calls if x[0] == "patch")
    assert tagged == ["/api/items/i-1/media", "/api/items/i-2/media", "/api/items/i-3/media"]
    FakeAbs.calls.clear()
    assert abs_mod.tag_wishes() == "Nichts zu taggen"  # von Hand entfernt bleibt entfernt
    assert FakeAbs.calls == []


def test_nachtragen_in_staffeln(lib, monkeypatch):
    c, add = lib
    monkeypatch.setattr(abs_mod, "TAG_BATCH", 2)
    lou = c.execute("INSERT INTO user(name, hash, role, abs_user_id, created) VALUES('Lou', 'x', 'user', 'abs-lou', datetime('now'))").lastrowid
    for i in range(3):
        b = add(f"i-{i}", f"B00000000{i}", imported=False)
        c.execute("UPDATE book SET wanted_by=? WHERE id=?", (lou, b))
    assert abs_mod.tag_wishes() == "2 Einträge getaggt, 1 warten auf den nächsten Lauf"
    assert abs_mod.tag_wishes() == "1 Einträge getaggt"


def test_statistik_gewuenscht_von(lib):
    from abw import api
    c, add = lib
    lou = c.execute("INSERT INTO user(name, hash, role, abs_user_id, created) VALUES('Lou', 'x', 'user', 'abs-lou', datetime('now'))").lastrowid
    sid = c.execute("INSERT INTO series(name, norm, added_at) VALUES('Poirot', 'poirot', datetime('now'))").lastrowid
    de = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?, 'de', 'Poirot', 'poirot')", (sid,)).lastrowid
    a, b = add("i-1", "B000000001"), add("i-2", "B000000002")
    add("i-3", "B000000003", owned=0)  # fehlt noch: zählt nicht
    c.execute("UPDATE book SET edition_id=?, runtime_min=90 WHERE id IN (?, ?, ?)", (de, a, b, a + 2))
    c.execute("UPDATE book SET wanted_by=? WHERE id=?", (lou, a))
    single = add("i-4", "B000000004")
    c.execute("UPDATE book SET wanted_by=?, author='Agatha Christie', runtime_min=60 WHERE id=?", (lou, single))
    assert api.wished_stats(c, {sid: {"name": "Hercule Poirot"}}) == [{"name": "Lou", "books": 3, "hours": 4, "series": 1, "items": [
        {"kind": "series", "id": sid, "key": str(sid), "name": "Hercule Poirot", "books": 2},
        {"kind": "single", "id": None, "key": "agathachristie", "name": "Agatha Christie – Einzelbücher", "books": 1}]}]
    c.execute("UPDATE book SET wanted=datetime('now') WHERE id=?", (a + 2,))  # fehlender, aber gewünschter Band zählt im Filter
    assert api.library_wished() == {"series": {sid: ["Lou"]}, "singles": {"agathachristie": ["Lou"]}}
