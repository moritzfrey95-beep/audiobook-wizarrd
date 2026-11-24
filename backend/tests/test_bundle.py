"""Sammelband: Bewertung erkennt ihn, der Download wird in Einzelbände aufgeteilt (echte kleine Tondateien)."""
import json
import shutil
import subprocess

import pytest

from abw import bundle, db, importer, scoring, search

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")
SAO = dict(title="Sword Art Online 15", author="Reki Kawahara", series="Sword Art Online", sequence="15", lang="en", runtime_min=438,
           seq_runtimes={"14": 420, "15": 438, "16": 450})
SIB = ["Sword Art Online 14", "Sword Art Online 16"]


def tone(path, seconds, freq=440):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-b:a", "48k", str(path)], check=True)


def test_bundle_with_wanted_volume_counts_like_a_single_volume():
    r = scoring.score(SAO, {"title": "Sword Art Online 14-16 - Reki Kawahara [M4B]", "size": 829_000_000, "seeders": 1}, SIB)
    assert r["verdict"] == "sicher" and r["bundle"] == [14, 16] and not any(x["points"] < -5 for x in r["reasons"])
    abb = {"title": "Sword Art Online 15 - Reki Kawahara [M4B]", "size": 300_000_000, "seeders": 1}
    assert not any("Seeder" in x["text"] for x in scoring.score(SAO, {**abb, "source": "audiobookbay"}, SIB)["reasons"])
    assert any("Seeder" in x["text"] for x in scoring.score(SAO, {**abb, "source": "andere"}, SIB)["reasons"])
    other = scoring.score(SAO, {"title": "Sword Art Online 1-3 - Reki Kawahara [M4B]", "size": 829_000_000}, SIB)
    assert other["verdict"] == "verworfen" and other["bundle"] is None
    assert scoring.score(SAO, {"title": "Sword Art Online 15 - Reki Kawahara [M4B]", "size": 300_000_000}, SIB)["verdict"] == "sicher"
    assert scoring.span("Foo 2019-2020") is None and scoring.span("Serie 64-128 kbps", "Serie") is None
    assert scoring.span("Wheel.of.Time.Books.01-03") == (1, 3)


def test_boundaries_follow_audible_runtimes():
    assert bundle.by_runtime([600, 600, 30, 900, 900, 1200], [20, 30, 20]) == [(0, 2), (2, 5), (5, 6)]
    assert bundle.by_runtime([3000, 3000], [20, 30]) is None       # keine Grenze in der Nähe
    assert bundle.by_runtime([1200, 1800, 9000], [20, 30, 20]) is None  # letzter Band viel zu lang


def series(runtimes=(1, 1, 1), owned=()):
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Testreihe','testreihe')").lastrowid
    name = f"Testreihe {sid}"
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", name, f"testreihe{sid}")).lastrowid
    ids = [c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, seq, runtime_min, owned) VALUES(?,?,?,?,?,?,?,?)",
                     (eid, "en", f"{name} {n}", "A Author", str(n), n, rt, int(n in owned))).lastrowid
           for n, rt in zip((1, 2, 3), runtimes)]
    return name, ids


def parent(book_id, root, marker):
    c = db.conn()
    did = c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, auto, created, updated, bundle)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?)", (book_id, "usenet", "Testreihe 1-3", f"g-{marker}", marker, "arrived", 0, "2026", "2026", "[1, 3]")).lastrowid
    (root / marker).mkdir(parents=True)
    return dict(c.execute("SELECT * FROM download WHERE id=?", (did,)).fetchone())


def test_split_by_file_name_skips_owned_volume(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    name, ids = series(owned=(1,))
    d = parent(ids[1], tmp_path, "abw-t-name")
    inner = tmp_path / d["marker"] / f"{name} 1-3 [M4B]"
    inner.mkdir()
    for n in (1, 2, 3):
        tone(inner / f"{name} {n:02d} - A Author.mp3", 5)
    search.split_bundle(d)
    c = db.conn()
    assert c.execute("SELECT state FROM download WHERE id=?", (d["id"],)).fetchone()["state"] == "split"
    kids = c.execute("SELECT * FROM download WHERE parent_id=? ORDER BY book_id", (d["id"],)).fetchall()
    assert [k["book_id"] for k in kids] == ids[1:] and all(k["state"] == "arrived" for k in kids)
    assert [p.name for p in (tmp_path / kids[0]["marker"]).iterdir()] == [f"{name} 02 - A Author.mp3"]
    assert len(list(inner.iterdir())) == 3  # die Quelle bleibt unverändert


def test_split_single_file_at_chapter_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    name, ids = series(runtimes=(1, 2, 1))
    d = parent(ids[1], tmp_path, "abw-t-chap")
    parts = []
    for i, sec in enumerate((40, 20, 70, 50, 60)):  # Band 1 = 60 s, Band 2 = 120 s, Band 3 = 60 s
        parts.append(tmp_path / f"p{i}.m4a")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={300 + i * 60}:duration={sec}", "-c:a", "aac", "-b:a", "32k", str(parts[-1])], check=True)
    meta, pos = ";FFMETADATA1\n", 0
    for i, sec in enumerate((40, 20, 70, 50, 60)):
        meta += f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={pos * 1000}\nEND={(pos + sec) * 1000}\ntitle=Kapitel {i + 1}\n"
        pos += sec
    (tmp_path / "meta.txt").write_text(meta)
    (tmp_path / "list.txt").write_text("".join(f"file '{p}'\n" for p in parts))
    whole = tmp_path / d["marker"] / "Sammelband.m4b"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(tmp_path / "list.txt"), "-i", str(tmp_path / "meta.txt"),
                    "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", "-f", "mp4", str(whole)], check=True)
    search.split_bundle(d)
    c = db.conn()
    row = c.execute("SELECT state, checks FROM download WHERE id=?", (d["id"],)).fetchone()
    assert row["state"] == "split", row["checks"]
    kids = c.execute("SELECT * FROM download WHERE parent_id=? ORDER BY book_id", (d["id"],)).fetchall()
    assert len(kids) == 3
    from abw import verify
    got = []
    for k in kids:
        f = next((tmp_path / k["marker"]).iterdir())
        got.append((round(verify.probe(f)["duration"]), len(bundle.chapters(f))))
    assert [g[0] for g in got] == pytest.approx([60, 120, 60], abs=2) and [g[1] for g in got] == [2, 2, 1]


def test_unsplittable_bundle_goes_to_review_and_is_never_imported(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    name, ids = series(runtimes=(60, 60, 60))
    d = parent(ids[1], tmp_path, "abw-t-bad")
    tone(tmp_path / d["marker"] / "alles.mp3", 5)
    search.split_bundle(d)
    c = db.conn()
    row = c.execute("SELECT * FROM download WHERE id=?", (d["id"],)).fetchone()
    assert row["state"] == "review" and "nicht sicher aufteilen" in row["error"]
    assert not c.execute("SELECT 1 FROM download WHERE parent_id=?", (d["id"],)).fetchone()
    with pytest.raises(ValueError):
        search.import_download(d["id"])
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith((".abw", "abw-")) and p.name != d["marker"]] == []


def owned_book(tmp_path, monkeypatch, kbps):
    from abw import config
    lib, trash = tmp_path / "lib", tmp_path / "trash"
    monkeypatch.setattr(config, "LIBRARY_ROOT", {"en": lib, "de": lib})
    monkeypatch.setattr(config, "LIBRARY_TRASH", trash)
    monkeypatch.setattr(config, "IMPORT_ENABLED", True)
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path / "dl")
    name, ids = series(owned=(1,))
    rel = f"{name}/Book 1 - Alt"
    (lib / rel).mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=20", "-b:a", f"{kbps}k", str(lib / rel / "alt.mp3")], check=True)
    (lib / rel / "metadata.json").write_text('{"title": "meins"}')
    c = db.conn()
    c.execute("UPDATE book SET rel_path=? WHERE id=?", (rel, ids[0]))
    d = parent(ids[1], tmp_path / "dl", f"abw-t-own{kbps}")
    for n in (1, 2, 3):
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=500:duration=20", "-b:a", "64k",
                        str(tmp_path / "dl" / d["marker"] / f"{name} {n:02d}.mp3")], check=True)
    return ids, d, lib / rel, trash


def run_checks(monkeypatch):
    from abw import verify
    monkeypatch.setattr(verify, "verify", lambda book, folder, speech=True, release="", siblings=(): {"verdict": "passed", "checks": [], "files": 1, "minutes": 0})
    monkeypatch.setattr(search, "import_download", lambda i: None)
    search.verify_arrived()


def test_owned_volume_from_bundle_is_compared_and_replaced_only_by_hand(tmp_path, monkeypatch):
    ids, d, old, trash = owned_book(tmp_path, monkeypatch, 24)
    run_checks(monkeypatch)
    c = db.conn()
    kid = dict(c.execute("SELECT * FROM download WHERE parent_id=? AND book_id=?", (d["id"], ids[0])).fetchone())
    assert kid["replaces"] == 1 and kid["state"] == "review" and "here Tonqualit" in json.loads(kid["checks"])[0]["text"]
    assert (old / "alt.mp3").exists()  # ohne Knopfdruck bleibt der Bestand unberührt
    with pytest.raises(ValueError):  # die gewöhnliche Ablage ist für diesen Band gesperrt
        importer.execute(search.book_ctx(ids[0])[0], kid)
    monkeypatch.setattr("abw.abs.scan", lambda lang: None)
    search.replace_download(kid["id"])
    assert [p.name for p in sorted(old.iterdir())] == [f"{old.parent.name} 01.mp3", "metadata.json"]
    assert json.loads((old / "metadata.json").read_text()) == {"title": "meins"}
    assert [p.name for p in trash.rglob("*.mp3")] == ["alt.mp3"]


def test_owned_volume_that_is_not_better_is_discarded(tmp_path, monkeypatch):
    ids, d, old, trash = owned_book(tmp_path, monkeypatch, 128)
    run_checks(monkeypatch)
    kid = db.conn().execute("SELECT * FROM download WHERE parent_id=? AND book_id=?", (d["id"], ids[0])).fetchone()
    assert kid["state"] == "rejected" and "Nicht besser" in kid["error"] and (old / "alt.mp3").exists() and not trash.exists()
    with pytest.raises(ValueError):
        search.replace_download(kid["id"])


def test_same_bundle_is_not_downloaded_twice(tmp_path, monkeypatch):
    name, ids = series()
    c = db.conn()
    c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, auto, created, updated, bundle)"
              " VALUES(?,?,?,?,?,?,?,?,?,?)", (ids[0], "usenet", f"{name} 1-3", "g-doppelt", "abw-t-doppelt", "downloading", 1, "2026", "2026", "[1, 3]"))
    with pytest.raises(ValueError, match="Sammelband lädt schon"):
        search.grab(ids[1], {"guid": "g-doppelt", "title": f"{name} 1-3", "source": "usenet"}, auto=True)


def test_hidden_bundle_is_recognised_by_runtime():
    book = {"sequence": "15", "runtime_min": 438, "seq_runtimes": {"13": 400, "14": 420, "15": 438, "16": 450, "17": 900}}
    assert bundle.guess_span(book, (420 + 438 + 450) * 60) == (14, 16)
    assert bundle.guess_span(book, 440 * 60) is None                  # gewöhnlicher Einzelband
    assert bundle.guess_span(book, 2000 * 60) is None                 # passt zu keinem Bereich
    assert bundle.guess_span({**book, "seq_runtimes": {"14": 400, "15": 438, "16": 400}}, 838 * 60) is None  # 14-15 und 15-16: nicht eindeutig
    # mehrdeutig, aber der Release-Name nennt den Bereich («Folge 14 - 15»)
    assert bundle.guess_span({**book, "seq_runtimes": {"14": 400, "15": 438, "16": 400}}, 838 * 60, "Serie - Folge 14 - 15 (Ungekürzt)") == (14, 15)


def test_single_volume_download_that_is_a_bundle_gets_split(tmp_path, monkeypatch):
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    monkeypatch.setattr(bundle, "guess_span", lambda book, seconds, release="": (1, 3))
    name, ids = series()
    c = db.conn()
    did = c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, auto, created, updated)"
                    " VALUES(?,?,?,?,?,?,?,?,?)", (ids[1], "usenet", f"{name} 2", "g-hidden", "abw-t-hidden", "arrived", 1, "2026", "2026")).lastrowid
    (tmp_path / "abw-t-hidden").mkdir()
    for n in (1, 2, 3):
        tone(tmp_path / "abw-t-hidden" / f"{name} {n:02d}.mp3", 5)
    run_checks(monkeypatch)
    row = c.execute("SELECT state, bundle FROM download WHERE id=?", (did,)).fetchone()
    assert row["state"] == "split" and json.loads(row["bundle"]) == [1, 3]
    assert sorted(k["book_id"] for k in c.execute("SELECT book_id FROM download WHERE parent_id=?", (did,))) == ids


def test_pack_with_own_numbering_is_split_by_title(tmp_path, monkeypatch):
    """Wie «Jack Ryan Band 1-19 (Sammelpack)»: BAND_01 ist bei Audible Band 3, dazu ein Zusatzband 1.5."""
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Packtitel','packtitel')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "de", f"Packtitel {sid}", f"packtitel{sid}")).lastrowid
    titles = {"1": "Gnadenlos", "1.5": "Im Sturm", "2": "Die Stunde der Patrioten", "3": "Jagd auf Roter Oktober"}
    ids = {seq: c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, seq, runtime_min, owned) VALUES(?,?,?,?,?,?,?,0)",
                          (eid, "de", t, "Tom Clancy", seq, float(seq), 1)).lastrowid for seq, t in titles.items()}
    d = parent(ids["2"], tmp_path, "abw-t-pack")
    c.execute("UPDATE download SET bundle='[1, 3]' WHERE id=?", (d["id"],))
    d["bundle"] = "[1, 3]"
    extra = tmp_path / d["marker"] / "HB_0001829_BAND_04" / "Tom Clancy - Ziel erfasst"  # bei Audible nicht in der Serie
    extra.mkdir(parents=True)
    tone(extra / "001 - Ziel erfasst.mp3", 30)
    for folder, seq in (("BAND_01", "3"), ("BAND_01.5", "1.5"), ("BAND_02", "2"), ("BAND_03", "1")):
        inner = tmp_path / d["marker"] / f"HB_0001829_{folder}" / f"Tom Clancy - {titles[seq]}"
        inner.mkdir(parents=True)
        for k in (1, 2):
            tone(inner / f"{k:03d} - {titles[seq]}.mp3", 30)
    search.split_bundle(d)
    row = c.execute("SELECT state, checks FROM download WHERE id=?", (d["id"],)).fetchone()
    assert row["state"] == "split" and "nach Titel" in row["checks"] and "HB_0001829_BAND_04" in row["checks"], row["checks"]
    kids = {k["book_id"]: k["marker"] for k in c.execute("SELECT * FROM download WHERE parent_id=?", (d["id"],))}
    assert set(kids) == set(ids.values())  # auch der Zusatzband 1.5
    assert all(titles["3"] in p.name for p in (tmp_path / kids[ids["3"]]).iterdir())  # BAND_01 landet bei Audible-Band 3


def test_auto_import_takes_every_safe_download_but_not_replacements(monkeypatch):
    """Sichere Fälle legt die Automatik ab, auch von Hand gestartete. Ersetzen bleibt ein Knopf."""
    name, ids = series()
    c = db.conn()
    add = lambda book, marker, auto, replaces=0, state="verified": c.execute(
        "INSERT INTO download(book_id, source, release_title, marker, state, auto, created, updated, replaces) VALUES(?,?,?,?,?,?,?,?,?)",
        (book, "usenet", f"{name} x", marker, state, auto, "2026", "2026", replaces)).lastrowid
    manual, automatic, replace, hint = add(ids[0], "abw-t-hand", 0), add(ids[1], "abw-t-auto", 1), add(ids[2], "abw-t-ers", 0, 1), \
        add(ids[2], "abw-t-hint", 1, 0, "review")
    done = []
    monkeypatch.setattr(search, "import_download", lambda i: done.append(i))
    monkeypatch.setattr(db, "setting", lambda key: "0" if key == "auto_import" else None)
    search.verify_arrived()
    assert done == []
    monkeypatch.setattr(db, "setting", lambda key: "1" if key == "auto_import" else None)
    search.verify_arrived()
    assert {manual, automatic} <= set(done) and replace not in done and hint not in done


def test_clear_upgrade_is_replaced_automatically_only_when_everything_is_green(monkeypatch):
    """Eindeutig bessere Fassungen ersetzen den Bestand selbst; Hinweise oder laufendes Hören halten an."""
    ok = lambda n: {"name": n, "status": "ok", "text": ""}
    better = {"name": "Vergleich mit Bestand", "status": "warn", "text": "Höhere Tonqualität"}
    green = [ok("Dateien"), ok("Laufzeit"), ok("Tags"), ok("Ansage"), better]
    assert search.clear_upgrade(green, {})
    assert not search.clear_upgrade([*green[:2], {**ok("Tags"), "status": "warn"}, *green[3:]], {})
    assert not search.clear_upgrade(green[:4], {})
    assert not search.clear_upgrade(green, {"progress": 0.4, "finished": 0})
    assert search.clear_upgrade(green, {"progress": 1.0, "finished": 1})
    name, ids = series(owned=(1, 2))
    c = db.conn()
    add = lambda book, marker, checks, error=None: c.execute(
        "INSERT INTO download(book_id, source, release_title, marker, state, auto, created, updated, replaces, checks, error) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (book, "usenet", f"{name} x", marker, "review", 0, "2026", "2026", 1, json.dumps(checks), error)).lastrowid
    clear, unclear = add(ids[0], "abw-t-up1", green), add(ids[1], "abw-t-up2", [*green[:1], {**ok("Laufzeit"), "status": "warn"}, *green[2:]])
    done = []
    monkeypatch.setattr(search, "replace_download", lambda i: done.append(i) or "x")
    monkeypatch.setattr(db, "setting", lambda key: "1" if key == "auto_import" else None)
    search.verify_arrived()
    assert clear in done and unclear not in done
    monkeypatch.setattr(search, "replace_download", lambda i: (_ for _ in ()).throw(ValueError("Papierkorb belegt")))
    again = add(ids[0], "abw-t-up3", green)
    search.verify_arrived()
    row = c.execute("SELECT state, error FROM download WHERE id=?", (again,)).fetchone()
    assert row["state"] == "review" and row["error"].startswith("Ersetzen nicht möglich")


def test_volume_owned_under_other_series_is_compared_not_filed_again(tmp_path, monkeypatch):
    """Wie Jack Ryan: Audiobookshelf führt den Band unter einer anderen Serie; gleiche ASIN = schon im Bestand."""
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    name, ids = series()
    c = db.conn()
    c.execute("UPDATE book SET asin='B0TWIN0001' WHERE id=?", (ids[0],))
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Andere Reihe','anderereihe')").lastrowid
    other = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", f"Andere {sid}", f"andere{sid}")).lastrowid
    own = c.execute("INSERT INTO book(edition_id, lang, asin, title, sequence, seq, owned, rel_path) VALUES(?,?,?,?,?,?,1,?)",
                    (other, "en", "B0TWIN0001", f"{name} 1", "6", 6.0, f"Andere/Book 6 - {name} 1")).lastrowid
    d = parent(ids[1], tmp_path, "abw-t-twin")
    inner = tmp_path / d["marker"] / f"{name} 1-3"
    inner.mkdir()
    for n in (1, 2, 3):
        tone(inner / f"{name} {n:02d}.mp3", 5)
    search.split_bundle(d)
    kids = {k["book_id"]: k["replaces"] for k in c.execute("SELECT * FROM download WHERE parent_id=?", (d["id"],))}
    assert kids == {own: 1, ids[1]: 0, ids[2]: 0}


def test_title_with_bracketed_original_matches_folder(tmp_path):
    """«Die Gefahren einer Hauptstadt [The Dangers of a Capital]» bei Audible, im Pack nur der deutsche Titel."""
    books = [{"id": 1, "title": "Die Stille des Waldes"}, {"id": 2, "title": "Die Gefahren einer Hauptstadt [The Dangers of a Capital]"}]
    files = []
    for name in ("Tao Wong - Abenteuer in Brad 06 - Die Stille des Waldes (Ungekürzt)", "Tao Wong - Abenteuer in Brad 08 - Die Gefahren einer Hauptstadt (ungekürzt)"):
        (tmp_path / name).mkdir()
        f = tmp_path / name / "01.mp3"
        f.write_bytes(b"x")
        files.append(f)
    groups, unknown = bundle.by_title(tmp_path, files, books)
    assert groups == {1: [files[0]], 2: [files[1]]} and unknown == []


def test_pack_with_gap_in_catalog_is_split_by_title_and_catalog_is_refreshed(tmp_path, monkeypatch):
    """Wie Narnia: Band 4 fehlt bei Audible, Summe der Laufzeiten passt nicht; die Titel in den Ordnernamen entscheiden."""
    from abw import audible
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    synced = []
    monkeypatch.setattr(audible, "sync_edition", lambda eid: synced.append(eid) or "Fehler: offline")
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Luecke','luecke')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "de", f"Luecke {sid}", f"luecke{sid}")).lastrowid
    titles = {1: "Alpha Wald", 2: "Beta Fluss", 3: "Gamma Berg", 5: "Delta See"}
    ids = {n: c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, seq, runtime_min, owned) VALUES(?,?,?,?,?,?,?,0)",
                        (eid, "de", t, "A Author", str(n), float(n), 1)).lastrowid for n, t in titles.items()}
    did = c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, auto, created, updated)"
                    " VALUES(?,?,?,?,?,?,?,?,?)", (ids[2], "usenet", "Luecke Lesung", "g-gap", "abw-t-gap", "arrived", 0, "2026", "2026")).lastrowid
    for t in ("Alpha Wald", "Beta Fluss", "Delta See"):
        (tmp_path / "abw-t-gap" / t).mkdir(parents=True)
        tone(tmp_path / "abw-t-gap" / t / "Kapitel 01.mp3", 100)
    run_checks(monkeypatch)
    row = c.execute("SELECT state, bundle FROM download WHERE id=?", (did,)).fetchone()
    assert synced == [eid]  # Lücke bei Band 4: Serie wurde neu abgefragt
    assert row["state"] == "split" and json.loads(row["bundle"]) == [1, 5]
    assert sorted(k["book_id"] for k in c.execute("SELECT book_id FROM download WHERE parent_id=?", (did,))) == [ids[1], ids[2], ids[5]]


def test_pack_of_other_recording_blocks_the_release_for_the_whole_series():
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Sperre','sperre')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "de", f"Sperre {sid}", f"sperre{sid}")).lastrowid
    a, b = (c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, seq, runtime_min, owned) VALUES(?,?,?,?,?,?,?,0)",
                      (eid, "de", f"Band {n}", "A", str(n), float(n), 100)).lastrowid for n in (1, 2))
    checks = json.dumps([{"name": "Laufzeit", "status": "fail", "text": "Sammelpack", "kind": "pack_other"}])
    c.execute("INSERT INTO download(book_id, source, release_title, guid, marker, state, error, checks, auto, created, updated)"
              " VALUES(?,?,?,?,?,?,?,?,?,?,?)", (a, "usenet", "Pack", "g-pack", "abw-t-sperre", "rejected", "Sammelpack, andere Aufnahme", checks, 0, "2026", "2026"))
    assert search.rejected_reasons(b) == {"g-pack": "Sammelpack, andere Aufnahme"}
    res = [{"guid": "g-pack", "verdict": "sicher", "score": 80, "reasons": []}]
    search._mark_blocked(b, res)
    assert res[0]["verdict"] == "verworfen" and "Sammelpack" in res[0]["reasons"][0]["text"]
    with pytest.raises(ValueError, match="schon geladen"):
        search.grab(b, {"guid": "g-pack", "title": "Pack"})


def _fake_pack(tmp_path, monkeypatch, layout):
    """layout: {Ordner: Minuten je Datei}; ffprobe wird durch die Minuten ersetzt."""
    from abw import verify
    mins = {}
    for folder, parts in layout.items():
        (tmp_path / folder).mkdir(parents=True)
        for k, m in enumerate(parts, 1):
            f = tmp_path / folder / f"Track{k:02d}.mp3"
            f.write_bytes(b"x")
            mins[f] = m
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": mins[f] * 60, "tags": {}})


def test_pack_mit_ordner_je_buch_wird_nie_quer_geschnitten(tmp_path, monkeypatch):
    """17.11. Langdon-Pack: Ordner alphabetisch (Angels, Inferno, Orgin …), Bände fast gleich lang, Titel teils falsch geschrieben."""
    books = [dict(id=i, title=t, sequence=str(i), runtime_min=rt, series="Robert Langdon")
             for i, (t, rt) in enumerate([("Angels and Demons", 1110), ("The Da Vinci Code", 1019), ("The Lost Symbol", 1066)], 1)]
    _fake_pack(tmp_path, monkeypatch, {"Dan.Brown-Angels.and.Demons": [555, 555], "Dan.Brown-Inferno": [500, 533],
                                       "Dan.Brown-Orgin": [533, 533], "Dan.Brown-The.Lost.Symbol": [30]})
    catalog = books + [dict(id=9, title="Inferno", sequence="4", runtime_min=1033), dict(id=10, title="Origin", sequence="5", runtime_min=1091)]
    with pytest.raises(ValueError, match="nennt aber"):  # früher: «Inferno» als Da Vinci Code, «Orgin» als Lost Symbol
        bundle._plan_strict(tmp_path, sorted((tmp_path).rglob("*.mp3"), key=bundle._natural), books, 1, 3, catalog, None, False,
                            lambda fs: sum(bundle.verify.probe(f)["duration"] for f in fs))
    # Seit 22.11.: nach Titel trotzdem aufgeteilt, jeder Ordner bei seinem eigenen Titel; «Lost Symbol» (30 min) nur zur Prüfung
    p = bundle.plan(tmp_path, books, 1, 3, catalog)
    assert {i: {f.parent.name for f, _, _ in fs} for i, fs in p["parts"].items()} == {
        1: {"Dan.Brown-Angels.and.Demons"}, 9: {"Dan.Brown-Inferno"}, 3: {"Dan.Brown-The.Lost.Symbol"}}
    assert p["misfit"] == [3] and "Dan.Brown-Orgin" in p["how"]


def test_pack_aus_cd_ordnern_wird_an_ordnergrenzen_geteilt(tmp_path, monkeypatch):
    books = [dict(id=i, title=f"Band {i}", sequence=str(i), runtime_min=rt, series="X") for i, rt in ((1, 120), (2, 180))]
    _fake_pack(tmp_path, monkeypatch, {"CD1": [30, 30], "CD2": [30, 30], "CD3": [60, 30], "CD4": [60, 30]})
    p = bundle.plan(tmp_path, books, 1, 2)
    assert "Ordner" in p["how"]
    assert {f.parent.name for f, _, _ in p["parts"][1]} == {"CD1", "CD2"} and {f.parent.name for f, _, _ in p["parts"][2]} == {"CD3", "CD4"}


def titled_pack(tmp_path, monkeypatch, runtimes):
    """Drei Bände mit eindeutigen Titeln im Dateinamen, ohne passende Bandnummern (wie «RotE 04 Liveship Traders 01 …»)."""
    monkeypatch.setattr(importer, "source_root", lambda source: tmp_path)
    db.init()
    c = db.conn()
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Elderlings','elderlings')").lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, "en", f"Elderlings {sid}", f"elderlings{sid}")).lastrowid
    titles = ("Assassin's Apprentice", "Royal Assassin", "Ship of Magic")
    ids = [c.execute("INSERT INTO book(edition_id, lang, title, author, sequence, seq, runtime_min, owned) VALUES(?,?,?,?,?,?,?,0)",
                     (eid, "en", t, "Robin Hobb", str(n), n, rt)).lastrowid for n, t, rt in zip((1, 2, 3), titles, runtimes)]
    d = parent(ids[1], tmp_path, f"abw-t-rote{sid}")
    for n, t in zip((1, 2, 3), titles):
        tone(tmp_path / d["marker"] / f"RotE {n + 3:02d} Trilogy 0{n} {t}.mp3", 5)
    return ids, d


def test_pack_by_title_splits_fitting_volumes_and_holds_the_misfit(tmp_path, monkeypatch):
    """Ein Band mit unpassender Laufzeit blockiert nicht mehr den ganzen Sammelband."""
    ids, d = titled_pack(tmp_path, monkeypatch, (1, 1, 30))  # Band 3: 5 s statt 30 min
    run_checks(monkeypatch)
    c = db.conn()
    row = c.execute("SELECT state, checks FROM download WHERE id=?", (d["id"],)).fetchone()
    assert row["state"] == "split" and "einzeln unter" in row["checks"] and "Band 3" in row["checks"], row["checks"]
    kids = {k["book_id"]: dict(k) for k in c.execute("SELECT * FROM download WHERE parent_id=?", (d["id"],))}
    assert set(kids) == set(ids)
    assert [kids[i]["misfit"] for i in ids] == [0, 0, 1]
    assert kids[ids[0]]["state"] == kids[ids[1]]["state"] == "verified"
    assert kids[ids[2]]["state"] == "review" and "Sammelband" in kids[ids[2]]["checks"]  # trotz bestandener Prüfung nur von Hand


def test_pack_by_title_where_no_volume_fits_stays_unsplit(tmp_path, monkeypatch):
    ids, d = titled_pack(tmp_path, monkeypatch, (30, 30, 30))
    search.split_bundle(d)
    c = db.conn()
    assert c.execute("SELECT state FROM download WHERE id=?", (d["id"],)).fetchone()["state"] == "review"
    assert not c.execute("SELECT 1 FROM download WHERE parent_id=?", (d["id"],)).fetchone()


def test_title_split_ignores_common_wrapper_folder(tmp_path, monkeypatch):
    """Robin Hobb 22.11.: alles in «Realm of the Elderlings (Robin Hobb)/», darunter Romane und Kurzgeschichten in eigenen Ordnern."""
    books = [dict(id=i, title=t, sequence=str(i), runtime_min=60, series="Realm of the Elderlings")
             for i, t in ((1, "Assassin's Apprentice"), (2, "Royal Assassin"))]
    wrap = "Realm of the Elderlings (Robin Hobb)"
    _fake_pack(tmp_path, monkeypatch, {f"{wrap}/01 Novels": [], f"{wrap}/02 Short Pieces/01 Homecoming": [10]})
    from abw import verify
    mins = {f: 10 for f in (tmp_path / wrap / "02 Short Pieces").rglob("*.mp3")}
    for n, b in enumerate(books, 1):
        f = tmp_path / wrap / "01 Novels" / f"RotE {n:02d} The Farseer Trilogy {n:02d} {b['title']}.m4b"
        f.write_bytes(b"x")
        mins[f] = 60
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": mins[f] * 60, "tags": {}})
    p = bundle.plan(tmp_path, books, 1, 2)
    assert "nach Titel" in p["how"] and "02 Short Pieces" in p["how"] and p["misfit"] == []
    assert [len(p["parts"][i]) for i in (1, 2)] == [1, 1]
