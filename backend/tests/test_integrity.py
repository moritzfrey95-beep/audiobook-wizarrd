"""Bestandsprüfung: Lücken aus Null-Bytes (unfertige Torrents) erkennen, beschädigte Bücher neu suchen und ersetzen."""
import os
from datetime import date, datetime

import pytest

from abw import config, db, importer, integrity, search, verify

KIB = 1024


def noise(n: int) -> bytes:
    return os.urandom(n)


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series", "book_search", "download", "integrity", "user_progress"):
        c.execute(f"DELETE FROM {t}")
    monkeypatch.setitem(config.LIBRARY_ROOT, "en", tmp_path / "lib")
    monkeypatch.setattr("abw.notify.send", lambda *a, **k: None)
    yield c
    c.execute("DELETE FROM integrity")  # Buch-IDs werden in anderen Tests neu vergeben


def book_in(c, tmp_path, files: dict[str, bytes], title="Code to Zero") -> int:
    folder = tmp_path / "lib" / "Standalone" / title
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return c.execute("INSERT INTO book(lang, asin, title, author, owned, rel_path, runtime_min) VALUES('en','B0',?,'Ken Follett',1,?,584)",
                     (title, f"Standalone/{title}")).lastrowid


def test_null_strecke_ab_128_kib_gilt_als_luecke(tmp_path):
    torn = tmp_path / "a.mp3"
    torn.write_bytes(noise(300 * KIB) + bytes(256 * KIB) + noise(100 * KIB))  # ein fehlendes Torrent-Stück
    z = integrity.zero_runs(torn)
    assert z["longest"] >= 256 * KIB - 16 * KIB and z["total"] == z["longest"]
    short = tmp_path / "b.mp3"
    short.write_bytes(noise(100 * KIB) + bytes(48 * KIB) + noise(100 * KIB))  # in echten Büchern gesehen: höchstens 32 KiB
    assert integrity.zero_runs(short)["longest"] < integrity.MIN_RUN
    tail = tmp_path / "c.mp3"
    tail.write_bytes(noise(50 * KIB) + bytes(1024 * KIB))  # Ende fehlt ganz
    assert integrity.zero_runs(tail)["longest"] >= integrity.MIN_RUN


def id3(pad: int) -> bytes:
    size = 20 + pad  # ein kleiner Frame, dann Polster aus Nullen
    syncsafe = bytes([(size >> 21) & 127, (size >> 14) & 127, (size >> 7) & 127, size & 127])
    return b"ID3\x04\x00\x00" + syncsafe + b"TIT2" + (10).to_bytes(4, "big") + b"\x00\x00" + b"\x03Soul Card" + bytes(pad)


def atom(kind: bytes, body: bytes) -> bytes:
    return (8 + len(body)).to_bytes(4, "big") + kind + body


def test_polster_im_format_ist_keine_luecke(tmp_path):
    """Echte Fehlalarme vom 17.11.: Nullen im ID3-Kopf (Wild Wild Quest) und in «free»/«moov» einer M4B (Soul Blade, Phantom Chamber)."""
    mp3 = tmp_path / "a.mp3"
    mp3.write_bytes(id3(400 * KIB) + noise(300 * KIB))
    assert integrity.zero_runs(mp3)["longest"] == 0
    torn = tmp_path / "b.mp3"
    torn.write_bytes(id3(400 * KIB) + noise(300 * KIB) + bytes(256 * KIB) + noise(100 * KIB))  # Lücke im Ton zählt weiter
    assert integrity.zero_runs(torn)["longest"] >= 240 * KIB
    m4b = tmp_path / "c.m4b"
    m4b.write_bytes(atom(b"ftyp", b"M4B \x00\x00\x02\x00") + atom(b"moov", noise(50 * KIB) + bytes(300 * KIB))
                    + atom(b"free", bytes(270 * KIB)) + atom(b"mdat", noise(600 * KIB)))
    assert integrity.zero_runs(m4b)["longest"] == 0
    hole = tmp_path / "d.m4b"
    hole.write_bytes(atom(b"ftyp", b"M4B \x00\x00\x02\x00") + atom(b"free", bytes(270 * KIB))
                     + atom(b"mdat", noise(300 * KIB) + bytes(256 * KIB) + noise(100 * KIB)))
    assert integrity.zero_runs(hole)["longest"] >= 240 * KIB


def test_ordner_gesund_und_beschaedigt(tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    (ok / "1.mp3").write_bytes(noise(200 * KIB))
    (ok / "cover.jpg").write_bytes(bytes(400 * KIB))  # nur Audiodateien zählen
    r = integrity.check_folder(ok)
    assert r["state"] == "ok" and r["sig"]
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "1.mp3").write_bytes(noise(200 * KIB))
    (bad / "2.mp3").write_bytes(noise(10 * KIB) + bytes(512 * KIB))
    r = integrity.check_folder(bad)
    assert r["state"] == "damaged" and "1 von 2 Dateien" in r["detail"] and "2.mp3" in r["detail"]
    assert integrity.check_folder(tmp_path / "fehlt")["state"] == "missing"


def test_beschaedigtes_buch_wird_gesucht_und_ersetzt(fresh, tmp_path, monkeypatch):
    c = fresh
    bid = book_in(c, tmp_path, {"01.mp3": noise(100 * KIB) + bytes(400 * KIB)})
    today = {"today": date.today().isoformat()}
    assert bid not in [r["id"] for r in c.execute(search.CANDIDATES, today)]  # im Bestand: wird nicht gesucht
    assert integrity.check_book(bid)["state"] == "damaged"
    assert bid in [r["id"] for r in c.execute(search.CANDIDATES, today)]

    # Laden ist erlaubt und läuft als Ersatz für den Bestand
    monkeypatch.setattr(search, "ensure_release", lambda b, r: None)
    monkeypatch.setattr(search.usenet, "fetch_nzb", lambda guid: b"nzb")
    monkeypatch.setattr(search.usenet, "submit", lambda nzb, marker: "nzo1")
    did = search.grab(bid, {"title": "Code to Zero (Unabridged)", "guid": "g1", "source": "usenet", "score": 90})
    assert c.execute("SELECT replaces FROM download WHERE id=?", (did,)).fetchone()["replaces"] == 1

    # Vergleich mit dem Bestand: gleiche Laufzeit, gleiche Qualität, trotzdem besser, weil der Bestand Lücken hat
    q = {"files": 1, "minutes": 584, "bytes": 1, "kbps": 64, "codec": "mp3", "effective": 64, "text": "1 MP3"}
    monkeypatch.setattr(importer, "quality", lambda folder: q)
    book, _ = search.book_ctx(bid)
    cmp = importer.compare(book, tmp_path)
    assert cmp["better"] and "beschädigt" in cmp["text"]

    integrity.forget(bid)  # nach dem Ersetzen
    assert not integrity.is_damaged(bid)


def test_gesundes_buch_bleibt_gesperrt(fresh, tmp_path):
    bid = book_in(fresh, tmp_path, {"01.mp3": noise(300 * KIB)})
    assert integrity.check_book(bid)["state"] == "ok"
    with pytest.raises(ValueError, match="schon im Bestand"):
        search.grab(bid, {"title": "x", "guid": "g", "source": "usenet"})


def test_automatik_schalter_nachtfenster_und_signatur(fresh, tmp_path, monkeypatch):
    c = fresh
    bid = book_in(c, tmp_path, {"01.mp3": noise(300 * KIB)})
    assert "ausgeschaltet" in integrity.auto()
    db.set_setting("auto_integrity", "1")
    try:
        assert not integrity.in_window(datetime(2026, 11, 16, 12, 0)) and integrity.in_window(datetime(2026, 11, 16, 3, 0))
        monkeypatch.setattr(integrity, "in_window", lambda now=None: False)
        assert "Nachtfenster" in integrity.auto()
        monkeypatch.setattr(integrity, "in_window", lambda now=None: True)
        assert "1 Bücher gelesen" in integrity.auto()
        assert "0 Bücher gelesen" in integrity.auto()  # unverändert: nicht noch einmal gelesen
        f = tmp_path / "lib" / "Standalone" / "Code to Zero" / "01.mp3"
        f.write_bytes(noise(100 * KIB) + bytes(300 * KIB))  # Datei ersetzt: neu lesen
        out = integrity.auto()
        assert "1 Bücher gelesen" in out and "beschädigt: Code to Zero" in out
        s = integrity.stats()
        assert s["total"] == 1 and s["checked"] == 1 and [d["book_id"] for d in s["damaged"]] == [bid]
    finally:
        db.set_setting("auto_integrity", "0")


def test_ankunftspruefung_findet_luecken(tmp_path):
    good, torn = tmp_path / "1.mp3", tmp_path / "2.mp3"
    good.write_bytes(noise(100 * KIB))
    torn.write_bytes(noise(100 * KIB) + bytes(256 * KIB))
    assert verify._holes([good]) == "" and verify._holes([good, torn]) == "2.mp3"
