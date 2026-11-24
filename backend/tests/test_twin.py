"""Audible führt dasselbe Hörbuch manchmal doppelt (zwei ASIN, zwei Bandnummern): nur einmal laden."""
import pytest

from abw import db, search


def _book(c, **kw):
    cols = ", ".join(kw)
    return c.execute(f"INSERT INTO book({cols}) VALUES({', '.join('?' * len(kw))})", tuple(kw.values())).lastrowid


def test_duplicate_audible_entry_counts_as_owned_and_is_not_grabbed():
    db.init()
    c = db.conn()
    base = dict(lang="en", title="Star Wars Jedi: Battle Scars", narrator="Sean Kenin Elias-Reyes")
    have = _book(c, **base, asin="B0TWINAAAA", runtime_min=628, owned=1, sequence="26")
    twin = _book(c, **base, asin="B0TWINBBBB", runtime_min=629, owned=0, sequence="25")
    other_len = _book(c, **base, asin="TWINC", runtime_min=700, owned=0)                       # andere Fassung
    other_voice = _book(c, **{**base, "narrator": "Someone Else"}, asin="TWIND", runtime_min=628, owned=0)
    other_lang = _book(c, **{**base, "lang": "de"}, asin="TWINE", runtime_min=628, owned=0)
    _book(c, lang="en", title="Platzhalter A", asin="0", runtime_min=100, owned=1)
    placeholder = _book(c, lang="en", title="Platzhalter B", asin="0", runtime_min=300, owned=0)
    ids = {r["id"] for r in c.execute(db.HAVE_TWIN)}
    assert placeholder not in ids
    assert twin in ids and not ids & {have, other_len, other_voice, other_lang}
    with pytest.raises(ValueError, match="schon im Bestand"):
        search.grab(twin, {"guid": "x", "title": "x", "source": "audiobookbay"})


def test_twin_of_a_running_download_is_busy():
    db.init()
    c = db.conn()
    a = _book(c, lang="en", title="Shadow of the Sith Test", asin="BUSYA", runtime_min=1032, owned=0)
    b = _book(c, lang="en", title="Shadow of the Sith Test", asin="BUSYB", runtime_min=1033, owned=0)
    c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, created, updated) VALUES(?,?,?,?,?,?,?,?)",
              (a, "usenet", "R", "gb", "abw-test-twin", "downloading", "2026", "2026"))
    assert b in {r["id"] for r in c.execute(db.BUSY_TWIN)}
    with pytest.raises(ValueError, match="läuft schon"):
        search.grab(b, {"guid": "y", "title": "y", "source": "usenet"})
