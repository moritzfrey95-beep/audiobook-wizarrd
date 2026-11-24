import pytest

from abw.verify import _result


def c(name, status, text=""):
    return {"name": name, "status": status, "text": text}


def test_all_ok_passes():
    assert _result([c("Dateien", "ok"), c("Laufzeit", "ok")], 1)["verdict"] == "passed"


def test_any_fail_rejects():
    assert _result([c("Dateien", "ok"), c("Laufzeit", "fail")], 1)["verdict"] == "rejected"


def test_warning_goes_to_review():
    assert _result([c("Dateien", "ok"), c("Laufzeit", "warn")], 1)["verdict"] == "review"


def test_tag_warning_alone_passes_when_intro_names_the_book():
    checks = [c("Dateien", "ok"), c("Laufzeit", "ok"), c("Tags", "warn"), c("Ansage", "ok", "Englisch, Titel oder Autor werden genannt. Gehört")]
    assert _result(checks, 1)["verdict"] == "passed"


def _tag_check(monkeypatch, tmp_path, book, album, siblings):
    from abw import verify
    (tmp_path / "Track01.mp3").write_bytes(b"x")
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": book["runtime_min"] * 60, "tags": {"album": album, "artist": "Dan Brown"}})
    monkeypatch.setattr(verify, "decodes", lambda f, end: True)
    monkeypatch.setattr(verify, "_holes", lambda files: "")
    r = verify.verify(book, tmp_path, speech=False, siblings=siblings)
    return next(x for x in r["checks"] if x["name"] == "Tags")


LANGDON = dict(id=1, lang="en", title="The Lost Symbol", author="Dan Brown", series="Robert Langdon", sequence="3", runtime_min=1066)


def test_tags_mit_anderem_band_der_serie_fallen_durch(monkeypatch, tmp_path):
    """17.11.: «Origin (Disc 02)» lag als «The Lost Symbol» im Bestand, die Prüfung warnte nur."""
    t = _tag_check(monkeypatch, tmp_path, LANGDON, "Origin (Disc 02)", ["Angels and Demons", "Origin", "Inferno"])
    assert t["status"] == "fail" and "Origin" in t["text"]


@pytest.mark.parametrize("book, album, siblings", [
    (LANGDON, "The Lost Symbol (Disc 02)", ["Origin", "Inferno"]),  # eigener Titel
    (dict(LANGDON, title="Die Pflicht eines Nachkommen, Buch 6", series="Das Große Spiel", author="Tom Elliot", sequence="6"),
     "Die Pflicht eines Nachkommen (Das Große Spiel 6)", ["Das Große Spiel"]),  # Serie in Klammern, nicht Band 1
    (dict(LANGDON, title="Die drei ??? und die verlorene Zeit", series="Die drei ???", author="Kari Erlhoff", sequence="215"),
     "Folge 215: und die verlorene Zeit", ["Folge 240: und die schwarze Rose"]),  # «Folge» allein ist kein Titel
    (dict(LANGDON, title="Children of Dune", series="Dune", author="Frank Herbert", sequence="3"), "Dune Chronicles 3", ["Dune", "Dune Messiah"]),
])
def test_tags_ohne_fremden_band_bleiben(monkeypatch, tmp_path, book, album, siblings):
    assert _tag_check(monkeypatch, tmp_path, book, album, siblings)["status"] != "fail"


JANE = dict(id=7605, lang="en", title="Judicator Jane 5", author="Brian Rouleau", series="Judicator Jane", sequence="5", runtime_min=943)


def _jane(monkeypatch, tmp_path, minutes, heard):
    from abw import verify
    (tmp_path / "jj5.m4b").write_bytes(b"x")
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": minutes * 60, "tags": {"album": "Judicator Jane 4", "artist": "Brian Rouleau"}})
    monkeypatch.setattr(verify, "decodes", lambda f, end: True)
    monkeypatch.setattr(verify, "_holes", lambda files: "")
    monkeypatch.setattr(verify, "intro", lambda f: {"language": "en", "probability": 0.99, "text": heard})
    return verify.verify(JANE, tmp_path)


def test_falscher_band_im_tag_zaehlt_nicht_wenn_ansage_und_laufzeit_passen(monkeypatch, tmp_path):
    """20.11.: Judicator Jane 5 verworfen, weil der Album-Tag «Judicator Jane 4» lautet; Ansage sagt «Book 5»."""
    r = _jane(monkeypatch, tmp_path, 944, "This is Audible. Judicator Jane, Book 5. Written by Brian Roulot, narrated by Angela Clark.")
    tags = next(x for x in r["checks"] if x["name"] == "Tags")
    assert tags["status"] == "warn" and r["verdict"] != "rejected"


def test_falscher_band_im_tag_bleibt_fail_wenn_ansage_ihn_bestaetigt(monkeypatch, tmp_path):
    r = _jane(monkeypatch, tmp_path, 944, "This is Audible. Judicator Jane, Book Four. Written by Brian Roulot.")
    assert r["verdict"] == "rejected"


def test_falscher_band_im_tag_bleibt_fail_wenn_laufzeit_nicht_passt(monkeypatch, tmp_path):
    r = _jane(monkeypatch, tmp_path, 700, "This is Audible. Judicator Jane, Book 5. Written by Brian Roulot.")
    assert r["verdict"] == "rejected"


def test_gesprochene_zahlwoerter():
    from abw.verify import spoken_numbers
    assert spoken_numbers("Judicator Jane, Book Two Written by", "Judicator Jane") == {"2"}
    assert spoken_numbers("Das Große Spiel, Band drei", "Das Große Spiel") == {"3"}


DAEMON = dict(id=6878, lang="de", title="Dämon der Rache", author="Ben Nevis", series="Die drei ???", sequence="173", runtime_min=70)


def test_dateiteil_im_titel_ist_keine_bandnummer(monkeypatch, tmp_path):
    """21.11.: «173 - Dämon der Rache (Teil 01)» im Titel der ersten Datei galt als Band 1."""
    from abw import verify
    for i in (1, 2):
        (tmp_path / f"{i:02d}.mp3").write_bytes(b"x")
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": 35 * 60, "tags": {
        "title": "173 - Dämon der Rache (Teil 01)", "album": "173/Dämon der Rache", "artist": "Die Drei ???"}})
    monkeypatch.setattr(verify, "decodes", lambda f, end: True)
    monkeypatch.setattr(verify, "_holes", lambda files: "")
    tags = next(x for x in verify.verify(DAEMON, tmp_path, speech=False)["checks"] if x["name"] == "Tags")
    assert tags["status"] != "fail"


def _ffmpeg(monkeypatch, err, done_us, duration=1001.04):
    from abw import verify
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, f"out_time_us={done_us}\nprogress=end\n", err))
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": duration, "tags": {}})
    return verify


HEADER = "[mp3float @ 0x1] Header missing\n[aist#0:0/mp3 @ 0x2] Error submitting packet to decoder: Invalid data found when processing input\n"


def test_rest_frame_am_ende_ist_kein_schaden(monkeypatch, tmp_path):
    """21.11.: Die drei ??? verworfen, obwohl nur 0,03 s vor dem ID3v1-Tag fehlten."""
    assert _ffmpeg(monkeypatch, HEADER, 29974943).decodes(tmp_path / "x.mp3", True)


def test_fehler_am_anfang_bleibt_schaden(monkeypatch, tmp_path):
    assert not _ffmpeg(monkeypatch, HEADER, 29974943).decodes(tmp_path / "x.mp3", False)


def test_abgebrochenes_ende_bleibt_schaden(monkeypatch, tmp_path):
    assert not _ffmpeg(monkeypatch, HEADER, 12_000_000).decodes(tmp_path / "x.mp3", True)


def test_viele_fehler_am_ende_bleiben_schaden(monkeypatch, tmp_path):
    assert not _ffmpeg(monkeypatch, HEADER * 3, 29974943).decodes(tmp_path / "x.mp3", True)



def test_ohne_spracherkennung_wird_die_ansage_uebersprungen(monkeypatch, tmp_path):
    """Schlankes Image ohne faster-whisper: kein Hinweis, der die Ablage aufhält."""
    from abw import verify
    monkeypatch.setattr(verify, "speech_available", lambda: False)
    monkeypatch.setattr(verify, "intro", lambda f: (_ for _ in ()).throw(AssertionError("darf nicht laufen")))
    (tmp_path / "jj5.m4b").write_bytes(b"x")
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": 944 * 60, "tags": {"album": "Judicator Jane 5", "artist": "Brian Rouleau"}})
    monkeypatch.setattr(verify, "decodes", lambda f, end: True)
    monkeypatch.setattr(verify, "_holes", lambda files: "")
    r = verify.verify(JANE, tmp_path)
    ansage = [c for c in r["checks"] if c["name"] == "Ansage"]
    assert ansage and ansage[0]["status"] == "ok" and "nicht installiert" in ansage[0]["text"]
