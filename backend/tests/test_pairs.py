"""Ungeprüfte Fassungen zuordnen oder als Einzelbuch kennzeichnen; Sprachpaare vorschlagen und zusammenlegen."""
from abw import audible, db


def _ed(c, name, lang, asin, author, runtimes, owned=True):
    sid = c.execute("INSERT INTO series(name, norm) VALUES(?,?)", (name, db.norm(name) + ":" + lang)).lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin) VALUES(?,?,?,?,?)",
                    (sid, lang, name, db.norm(name), asin)).lastrowid
    for i, rt in enumerate(runtimes, 1):
        c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, seq, runtime_min, owned) VALUES(?,?,?,?,?,?,?,?)",
                  (eid, lang, f"{name} {i}", author, str(i), float(i), rt, int(owned)))
    return sid, eid


def _data(eid):
    return audible._pair_data(db.conn())[eid]


def test_translation_with_same_runtimes_scores_high():
    db.init()
    c = db.conn()
    _, de = _ed(c, "Paar Rad Zeit", "de", "P-DE-1", "Paar Autor Eins", [600, 700, 800])
    _, en = _ed(c, "Paar Wheel Time", "en", "P-EN-1", "Paar Autor Eins", [500, 600, 700])
    _, other = _ed(c, "Paar Andere", "en", "P-EN-2", "Paar Autor Eins", [200, 1500, 300])
    good, why = audible.pair_score(_data(de), _data(en))
    bad, _ = audible.pair_score(_data(de), _data(other))
    assert good >= 70 and "3 von 3" in why[0]
    assert bad < audible.PAIR_MIN


def test_original_title_in_brackets_and_split_editions():
    db.init()
    c = db.conn()
    _, de = _ed(c, "Der Paar-Test [Pair Test]", "de", "P-DE-3", "Paar Autor Zwei", [100])
    _, en = _ed(c, "Pair Test", "en", "P-EN-3", "Paar Autor Zwei", [900])
    score, why = audible.pair_score(_data(de), _data(en))
    assert score >= 60 and "Originaltitel" in why[0]
    # deutsch in doppelt so viele Teile geteilt: einzeln zu kurz, zusammen passend
    _, de2 = _ed(c, "Paar Geteilt", "de", "P-DE-4", "Paar Autor Drei", [300, 320, 310, 330])
    _, en2 = _ed(c, "Pair Split", "en", "P-EN-4", "Paar Autor Drei", [580, 600])
    score, why = audible.pair_score(_data(de2), _data(en2))
    assert score >= audible.PAIR_MIN and "geteilt" in why[0]


def test_suggestions_hide_rejected_and_link_moves_follows():
    db.init()
    c = db.conn()
    sde, de = _ed(c, "Paar Link DE", "de", "P-DE-5", "Paar Autor Vier", [600, 700])
    sen, en = _ed(c, "Paar Link EN", "en", "P-EN-5", "Paar Autor Vier", [550, 650], owned=False)
    c.execute("INSERT INTO follow VALUES(1,'series',?, '2026')", (str(sen),))
    assert any(p["de"]["edition_id"] == de and p["en"]["edition_id"] == en for p in audible.pair_suggestions())
    sid = audible.link_editions(de, en)
    assert sid == sde  # die Karte mit den vorhandenen Büchern bleibt
    assert {r["lang"] for r in c.execute("SELECT lang FROM edition WHERE series_id=?", (sid,))} == {"de", "en"}
    assert c.execute("SELECT 1 FROM series WHERE id=?", (sen,)).fetchone() is None
    assert c.execute("SELECT 1 FROM follow WHERE kind='series' AND key=?", (str(sde),)).fetchone()
    assert not any(p["de"]["edition_id"] == de for p in audible.pair_suggestions())
    _, de2 = _ed(c, "Paar Nein DE", "de", "P-DE-6", "Paar Autor Fuenf", [600, 700])
    _, en2 = _ed(c, "Paar Nein EN", "en", "P-EN-6", "Paar Autor Fuenf", [550, 650])
    audible.reject_pair(de2, en2)
    assert not any(p["de"]["edition_id"] == de2 for p in audible.pair_suggestions())


def _fake_get(products: dict, search: list):
    def get(lang, path, **params):
        if path == "/1.0/catalog/products":
            return {"products": search}
        return {"product": products.get(path.rsplit("/", 1)[-1], {})}
    return get


def test_unknown_edition_becomes_single_absent_or_found(monkeypatch):
    db.init()
    c = db.conn()
    _, eid = _ed(c, "Ohne Serie Test", "de", None, "Einzel Autor", [300])
    c.execute("UPDATE book SET asin='B0EINZEL01' WHERE edition_id=?", (eid,))
    monkeypatch.setattr(audible, "_get", _fake_get({"B0EINZEL01": {"asin": "B0EINZEL01", "series": []}}, []))
    assert audible.sync_edition(eid).startswith("Fehler")
    assert c.execute("SELECT kind FROM edition WHERE id=?", (eid,)).fetchone()["kind"] == "single"
    # Platzhalter-ASIN «0» und kein Suchtreffer: nicht bei Audible
    _, eid2 = _ed(c, "Nicht Da Test", "en", None, "Fehlt Autor", [300])
    c.execute("UPDATE book SET asin='0' WHERE edition_id=?", (eid2,))
    monkeypatch.setattr(audible, "_get", _fake_get({}, []))
    audible.sync_edition(eid2)
    assert c.execute("SELECT kind FROM edition WHERE id=?", (eid2,)).fetchone()["kind"] == "absent"
    # Suche nach Titel und Autor findet das Buch samt Serie; ein fremder Titel zählt nicht
    hit = {"asin": "B0GEFUNDEN", "title": "Nicht Da Test 1", "language": "english", "authors": [{"name": "Fehlt Autor"}],
           "series": [{"asin": "SER-FOUND", "title": "Nicht Da Test"}]}
    wrong = {**hit, "title": "Ganz anderes Buch", "series": [{"asin": "SER-WRONG", "title": "Falsch"}]}
    monkeypatch.setattr(audible, "_get", _fake_get({}, [wrong, hit]))
    books = c.execute("SELECT * FROM book WHERE edition_id=?", (eid2,)).fetchall()
    assert audible.find_series_asin("en", "Nicht Da Test", books) == ("SER-FOUND", None)
    monkeypatch.setattr(audible, "_get", _fake_get({}, [wrong, {**hit, "language": "german"}]))
    assert audible.find_series_asin("en", "Nicht Da Test", books) == (None, "absent")


def test_owned_omnibus_covers_its_volumes(monkeypatch):
    db.init()
    c = db.conn()
    _, eid = _ed(c, "Omnibus Test", "en", None, "Omni Autor", [900])
    c.execute("UPDATE book SET asin='B0OMNIBUS1' WHERE edition_id=?", (eid,))
    kids = [{"asin": "B0OMNIBUS1", "sequence": "1-3"}, *({"asin": f"B0OMNIVOL{i}", "sequence": str(i)} for i in (1, 2, 3))]
    monkeypatch.setattr(audible, "find_series_asin", lambda *a: ("SER-OMNI", None))
    monkeypatch.setattr(audible, "series_children", lambda lang, asin: ("Omnibus Test", [
        {**k, "title": f"Omni {k['sequence']}", "subtitle": None, "author": "Omni Autor", "narrator": None, "release_date": "2020-01-01",
         "runtime_min": 300, "cover_url": None, "language": "english", "content_type": "Product", "format_type": "unabridged"} for k in kids]))
    assert audible.sync_edition(eid) == "ok"
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=? AND owned=0", (eid,)).fetchone()[0] == 0
