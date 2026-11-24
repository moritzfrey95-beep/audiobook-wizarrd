"""Sammelband aus einer Datei im Bestand: erkennen, sicher planen, aufteilen, Original erst nach der Ablage in den Papierkorb."""
import shutil
import subprocess

import pytest

from abw import abs as abs_mod
from abw import audible, config, db, libsplit

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def child(asin, seq, runtime):
    return {"asin": asin, "sequence": seq, "title": f"Band {seq}", "subtitle": None, "author": "A Author", "narrator": "N",
            "release_date": "2024-01-01", "runtime_min": runtime, "cover_url": None, "language": "english",
            "content_type": "Product", "format_type": "unabridged"}


def test_series_with_ampersand_is_a_bundle_not_its_own_series():
    assert abs_mod.split_series("Legend of the Arch Magus #9&10") == ("Legend of the Arch Magus", "9-10")
    assert abs_mod.split_series("Star Wars #53, Star Wars: The Last Jedi") == ("Star Wars", "53")
    assert libsplit.span_of({"sequence": "9-10", "title": "x", "rel_path": "S/Book 9-10 - x"}) == (9, 10)
    assert libsplit.span_of({"sequence": "11", "title": "Pack 6", "rel_path": "S/Book 11&12 - Pack 6"}) == (11, 12)
    assert libsplit.span_of({"sequence": "1", "title": "Dual Sword God, Books 1-2", "rel_path": "S/Book 1 - x", "series": "Dual Sword God"}) == (1, 2)
    assert libsplit.span_of({"sequence": "2", "title": "Band 2", "rel_path": "S/Book 2 - Band 2"}) is None


def test_chapter_titles_name_the_volumes():
    chs = [(0, 1, "Opening Credits"), (1, 2, "Volume 11 : Chapter 1"), (2, 3, "Volume 11 : Epilogue"),
           (3, 4, "Volume 12 : Chapter 1"), (4, 5, "End Credits")]
    assert libsplit._by_titles(chs, 11, 12) == [0, 3]
    assert libsplit._by_titles(chs, 11, 13) is None  # Band 13 fehlt
    assert libsplit._by_titles([(0, 1, "Book 2"), (1, 2, "Book 1")], 1, 2) is None  # falsche Reihenfolge


def test_runtime_boundary_must_be_close():
    chs = [(0, 600, ""), (600, 1200, ""), (1200, 1800, ""), (1800, 2400, "")]
    assert libsplit._by_runtime(chs, [20, 20]) == [0, 2]
    assert libsplit._by_runtime(chs, [16, 24]) is None  # nächste Grenze 4 min daneben: zu unsicher


def setup_bundle(tmp_path, monkeypatch, runtimes, chapters):
    """Serie mit Audible-ASIN, ein Sammelband «Book 1-2» aus einer M4B mit Kapiteln (Sekunden) im Bestand."""
    lib, trash, dl = tmp_path / "lib", tmp_path / "trash", tmp_path / "dl"
    dl.mkdir()
    monkeypatch.setattr(config, "LIBRARY_ROOT", {"en": lib, "de": lib})
    monkeypatch.setattr(config, "LIBRARY_TRASH", trash)
    monkeypatch.setattr(config, "USENET_COMPLETE_ROOT", dl)
    monkeypatch.setattr(audible, "series_children", lambda lang, asin: ("S", [child("P12", "1-2", sum(runtimes)),
                                                                              *(child(f"A{i + 1}", str(i + 1), rt) for i, rt in enumerate(runtimes))]))
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Packreihe','packreihe')").lastrowid
    name = f"Packreihe {sid}"
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin) VALUES(?,?,?,?,?)", (sid, "en", name, f"packreihe{sid}", "SER")).lastrowid
    rel = f"{name}/Book 1-2 - {name}: Publisher's Pack"
    (lib / rel).mkdir(parents=True)
    parts, meta, pos = [], ";FFMETADATA1\n", 0
    for i, sec in enumerate(chapters):
        parts.append(tmp_path / f"p{i}.m4a")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + i * 60}:duration={sec}", "-c:a", "aac", "-b:a", "32k", str(parts[-1])], check=True)
        meta += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={pos * 1000}\nEND={(pos + sec) * 1000}\ntitle={i + 1:03d}\n"
        pos += sec
    (tmp_path / "meta.txt").write_text(meta)
    (tmp_path / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "list.txt"), "-i", str(tmp_path / "meta.txt"),
                    "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", "-f", "mp4", str(lib / rel / "pack.m4b")], check=True)
    (lib / rel / "cover.jpg").write_bytes(b"jpg")
    bid = c.execute("INSERT INTO book(edition_id, lang, asin, title, author, sequence, seq, owned, abs_item_id, num_files, rel_path)"
                    " VALUES(?,?,?,?,?,?,?,1,?,1,?)", (eid, "en", "P12", f"{name}: Publisher's Pack", "A Author", "1-2", None, f"abs-{sid}", rel)).lastrowid
    return bid, eid, lib / rel, trash, dl


@needs_ffmpeg
def test_bundle_is_split_and_trashed_after_all_volumes_are_filed(tmp_path, monkeypatch):
    bid, eid, folder, trash, dl = setup_bundle(tmp_path, monkeypatch, [1, 2], [40, 20, 70, 50])  # Band 1 = 60 s, Band 2 = 120 s
    book = next(b for b in libsplit.candidates() if b["id"] == bid)
    assert libsplit.run_one(book) == "split"
    c = db.conn()
    job = c.execute("SELECT * FROM libsplit_job WHERE book_id=?", (bid,)).fetchone()
    kids = c.execute("SELECT d.*, b.seq, b.asin FROM download d JOIN book b ON b.id=d.book_id WHERE d.parent_id=? ORDER BY b.seq", (job["parent_id"],)).fetchall()
    assert [(k["seq"], k["asin"], k["state"]) for k in kids] == [(1.0, "A1", "arrived"), (2.0, "A2", "arrived")]
    from abw import bundle, verify
    for k, want in zip(kids, (60, 120)):
        files = sorted(p.name for p in (dl / k["marker"]).iterdir())
        assert files == ["Band %d.m4b" % k["seq"], "cover.jpg"]
        assert round(verify.probe(dl / k["marker"] / files[0])["duration"]) == pytest.approx(want, abs=2)
    # Der Serienabgleich darf die beiden Bände nicht wieder entfernen, obwohl der Sammelband sie enthält
    audible._merge(c, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone(), "SER", audible.series_children("en", "SER")[1])
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=? AND owned=0 AND seq IN (1, 2)", (eid,)).fetchone()[0] == 2
    # Die Ablage sieht den Ordner «Book 1-2» nicht als vorhandenen Band 1
    from abw import importer, search
    book1, _ = search.book_ctx(kids[0]["book_id"])
    assert not importer.plan(book1, dict(kids[0]))["problems"]
    libsplit.finish()
    assert folder.exists()  # Bände noch in Prüfung: Original bleibt
    c.execute("UPDATE download SET state='done' WHERE parent_id=?", (job["parent_id"],))
    monkeypatch.setattr(abs_mod, "scan", lambda lang: None)
    assert libsplit.finish() == 1
    assert not folder.exists() and [p.name for p in trash.rglob("*.m4b")] == ["pack.m4b"]
    assert c.execute("SELECT state FROM libsplit_job WHERE book_id=?", (bid,)).fetchone()["state"] == "done"


@needs_ffmpeg
def test_unclear_bundle_is_left_alone(tmp_path, monkeypatch):
    bid, eid, folder, trash, dl = setup_bundle(tmp_path, monkeypatch, [10, 10], [60, 60, 60])  # 3 min statt 20 min
    book = next(b for b in libsplit.candidates() if b["id"] == bid)
    assert libsplit.run_one(book) == "unsafe"
    c = db.conn()
    job = c.execute("SELECT * FROM libsplit_job WHERE book_id=?", (bid,)).fetchone()
    assert "keine sichere Grenze" in job["message"] and job["parent_id"] is None
    assert list(dl.iterdir()) == [] and (folder / "pack.m4b").exists()
    assert bid not in [b["id"] for b in libsplit.candidates()]  # nicht bei jedem Lauf neu
    libsplit.finish()
    assert (folder / "pack.m4b").exists()


@needs_ffmpeg
def test_volume_already_owned_alone_is_not_cut_again(tmp_path, monkeypatch):
    bid, eid, folder, trash, dl = setup_bundle(tmp_path, monkeypatch, [1, 2], [40, 20, 70, 50])
    c = db.conn()
    c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, abs_item_id, num_files, rel_path) VALUES(?,?,?,?,?,?,1,?,1,?)",
              (eid, "en", "A1", "Band 1", "1", 1.0, f"abs-one-{bid}", "x/Book 1 - Band 1"))
    book = next(b for b in libsplit.candidates() if b["id"] == bid)
    assert libsplit.run_one(book) == "split"
    job = c.execute("SELECT * FROM libsplit_job WHERE book_id=?", (bid,)).fetchone()
    kids = c.execute("SELECT b.seq FROM download d JOIN book b ON b.id=d.book_id WHERE d.parent_id=?", (job["parent_id"],)).fetchall()
    assert [k["seq"] for k in kids] == [2.0] and "Band 1 liegt schon einzeln im Bestand" in job["message"]
