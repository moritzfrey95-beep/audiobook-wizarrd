"""Metadaten einbetten: nur gematchte ABW-Bücher, höchstens drei offen, Neu-Einlesen und Tag-Vergleich, zweiter Versuch, Fehler."""
from datetime import datetime, timedelta, timezone

from abw import abs as abs_mod
from abw import db, embed


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d = status, data or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)
        return self

    def json(self):
        return self._d


class FakeABS:
    def __init__(self, tags):
        self.tags, self.calls = tags, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, params=None, json=None):
        self.calls.append(("post", url))
        return Resp()

    def get(self, url, params=None):
        self.calls.append(("get", url))
        item = url.rsplit("/", 1)[1]
        return Resp(data={"media": {"metadata": {"title": "Band " + item, "authors": [{"name": "Autorin A"}]},
                                    "audioFiles": [{"metaTags": self.tags(item)}]}})


def setup(monkeypatch, tags):
    db.init()
    c = db.conn()
    c.execute("DELETE FROM book")
    for i in range(1, 6):
        match = "2026-11-01T00:00:00+00:00" if i != 5 else "Fehler: x"
        c.execute("INSERT INTO book(lang, asin, title, owned, abs_item_id, abs_match) VALUES('en', ?, ?, 1, ?, ?)", (f"A{i}", f"Band i{i}", f"i{i}", match))
    db.set_setting("auto_embed", "1")
    fake = FakeABS(tags)
    monkeypatch.setattr(abs_mod, "_client", lambda: fake)
    return c, fake


def states(c):
    return {r["abs_item_id"]: (r["embedded"] or "").split(" ")[0] for r in c.execute("SELECT abs_item_id, embedded FROM book")}


def test_ablauf_bis_ok(monkeypatch):
    c, fake = setup(monkeypatch, lambda item: {"tagTitle": "Band " + item, "tagArtist": "Autorin A"})
    t0 = datetime.now(timezone.utc)
    embed.auto(now=t0)
    st = states(c)
    assert sum(v == "gesendet" for v in st.values()) == 3 and st["i5"] == ""  # höchstens drei, ungematchte nie
    embed.auto(now=t0 + timedelta(minutes=5))  # noch zu früh
    assert sum(v == "gesendet" for v in states(c).values()) == 3
    embed.auto(now=t0 + timedelta(minutes=11))
    assert sum(v == "eingelesen" for v in states(c).values()) == 3
    embed.auto(now=t0 + timedelta(minutes=14))
    st = states(c)
    assert sum(v == "ok" for v in st.values()) == 3 and sum(v == "gesendet" for v in st.values()) == 1
    assert embed.stats() == {"ok": 3, "failed": 0, "busy": 1, "open": 0}


def test_falsche_tags_zweiter_versuch_dann_fehler(monkeypatch):
    c, fake = setup(monkeypatch, lambda item: {"tagTitle": "Irgendwas", "tagArtist": "Autorin A"})
    c.execute("DELETE FROM book WHERE abs_item_id<>'i1'")
    t = datetime.now(timezone.utc)
    for step, want in ((0, "gesendet"), (11, "eingelesen"), (14, "gesendet"), (25, "eingelesen"), (28, "Fehler:")):
        embed.auto(now=t + timedelta(minutes=step))
        assert states(c)["i1"] == want, step
    assert "Titel in der Datei «Irgendwas»" in c.execute("SELECT embedded FROM book").fetchone()[0]


def test_aus_heisst_aus(monkeypatch):
    c, fake = setup(monkeypatch, lambda item: {})
    db.set_setting("auto_embed", "0")
    assert embed.auto() == "Ausgeschaltet" and fake.calls == []


def test_tags_match():
    item = {"media": {"metadata": {"title": "Dune", "authors": [{"name": "Frank Herbert"}]},
                      "audioFiles": [{"metaTags": {"tagTitle": "Dune", "tagAlbumArtist": "Frank Herbert"}}]}}
    assert embed.tags_match(item)[0]
    item["media"]["audioFiles"][0]["metaTags"]["tagAlbumArtist"] = "X"
    assert not embed.tags_match(item)[0]
