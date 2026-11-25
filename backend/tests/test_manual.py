"""Handimport: Buchordner finden, Bücher vorschlagen, Ordner kopieren und als Download eintragen."""
import time
from pathlib import Path

from abw import config, db, manual


def _audio(p: Path, name="01.mp3"):
    p.mkdir(parents=True, exist_ok=True)
    (p / name).write_bytes(b"\0" * 100)


def test_folders_suggest_and_start(tmp_path, monkeypatch):
    db.init()
    c = db.conn()
    imp, usenet = tmp_path / "import", tmp_path / "usenet"
    _audio(imp / "Audiobooks" / "Brandon Sanderson - Hand Import Test 2")
    _audio(imp / "Audiobooks" / "Mehrteilig Handtest" / "CD1")
    _audio(imp / "Audiobooks" / "Mehrteilig Handtest" / "CD2")
    _audio(imp / ".abw-intern" / "nicht zeigen")
    usenet.mkdir()
    monkeypatch.setattr(config, "IMPORT_ROOT", imp, raising=False)
    monkeypatch.setattr(config, "USENET_COMPLETE_ROOT", usenet)
    monkeypatch.setattr(config, "TORRENT_COMPLETE_ROOT", tmp_path / "fehlt")
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Hand Import Test', 'handimporttest')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?, 'en', 'Hand Import Test', 'handimporttesten')", (sid,)).lastrowid
    b1 = c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, owned) VALUES(?, 'en', 'Hand Import Test', 'Brandon Sanderson', '1', 0)", (eid,)).lastrowid
    b2 = c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, owned) VALUES(?, 'en', 'Hand Import Test 2', 'Brandon Sanderson', '2', 0)", (eid,)).lastrowid
    try:
        items = {x["name"]: x for x in manual.folders(refresh=True)}
        assert set(items) == {"Brandon Sanderson - Hand Import Test 2", "Mehrteilig Handtest"}
        assert items["Brandon Sanderson - Hand Import Test 2"]["suggestions"][0]["book_id"] == b2
        job = manual.start(items["Brandon Sanderson - Hand Import Test 2"]["path"], b2)
        for _ in range(50):
            if not manual._jobs[items["Brandon Sanderson - Hand Import Test 2"]["path"]]["running"]:
                break
            time.sleep(0.05)
        row = c.execute("SELECT * FROM download WHERE book_id=?", (b2,)).fetchone()
        assert row["state"] == "arrived" and row["indexer"] == "Handimport" and row["replaces"] is None
        assert (usenet / row["marker"] / "01.mp3").is_file()
        assert job is not None
        manual.hide(items["Mehrteilig Handtest"]["path"])
        assert [x["name"] for x in manual.folders()] == ["Brandon Sanderson - Hand Import Test 2"]
        try:
            manual.start(str(tmp_path), b1)
            raise AssertionError("Ordner ausserhalb der Wurzeln darf nicht gehen")
        except ValueError:
            pass
    finally:
        c.execute("DELETE FROM download WHERE book_id IN (?,?)", (b1, b2))
        c.execute("DELETE FROM book WHERE id IN (?,?)", (b1, b2))
        c.execute("DELETE FROM manual_hidden")
