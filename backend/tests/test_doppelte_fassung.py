"""Silmarillion 18.11.: Buch in zwei Formaten im Ordner, Soll-Laufzeit aus Audiobookshelf statt Audible, alte Kapitel."""
import json
from pathlib import Path

from abw import diagnose, importer, verify

H = 3600


def _fake_probe(monkeypatch, lengths: dict[str, float]):
    monkeypatch.setattr(verify, "probe", lambda f: {"duration": lengths[Path(f).name], "tags": {}})


def _silmarillion(tmp_path: Path) -> tuple[Path, dict]:
    src = tmp_path / "dl"
    (src / "M4B files").mkdir(parents=True)
    (src / "MP3 files").mkdir()
    lengths = {}
    for i, h in enumerate((4.66, 4.78, 5.58), 1):
        (src / "M4B files" / f"Volume {i}.m4b").write_bytes(b"x")
        lengths[f"Volume {i}.m4b"] = h * H
    for i in range(28):
        (src / "MP3 files" / f"{i:02d}.mp3").write_bytes(b"x")
        lengths[f"{i:02d}.mp3"] = 15.0 * H / 28
    (src / "map.jpg").write_bytes(b"x")
    return src, lengths


def test_zwei_formate_gleicher_laenge_zaehlen_einmal(tmp_path, monkeypatch):
    src, lengths = _silmarillion(tmp_path)
    _fake_probe(monkeypatch, lengths)
    dup = verify.duplicate_sets(verify.audio_files(src))
    assert dup and {f.suffix for f in dup["keep"]} == {".m4b"} and len(dup["drop"]) == 28
    assert "3 M4B" in dup["text"] and "28 MP3" in dup["text"]


def test_ablage_nimmt_nur_m4b(tmp_path, monkeypatch):
    src, lengths = _silmarillion(tmp_path)
    _fake_probe(monkeypatch, lengths)
    files = importer._files(src)
    assert sorted(f.suffix for f in files) == [".jpg", ".m4b", ".m4b", ".m4b"]


def test_verschiedene_laengen_sind_kein_doppel(tmp_path, monkeypatch):
    src = tmp_path / "dl"
    src.mkdir()
    (src / "a.m4b").write_bytes(b"x")
    (src / "bonus.mp3").write_bytes(b"x")
    _fake_probe(monkeypatch, {"a.m4b": 15 * H, "bonus.mp3": 0.5 * H})
    assert verify.duplicate_sets(verify.audio_files(src)) is None
    assert len(importer._files(src)) == 2


def test_soll_laufzeit_zuerst_audible():
    assert verify.want_minutes({"owned": 1, "runtime_min": 1791, "audible_min": 900}) == (900, "Audible")
    assert verify.want_minutes({"owned": 1, "runtime_min": 1791, "audible_min": None}) == (1791, "Bestand")
    assert verify.want_minutes({"owned": 0, "runtime_min": 900}) == (900, "Audible")


def test_text_nennt_die_quelle():
    assert "Audible nennt" in diagnose.runtime_finding(900, 900)["text"]
    f = diagnose.runtime_finding(1164, 1791, source="Bestand")
    assert "im Bestand sind es" in f["text"] and "Audible" not in f["text"]


def test_ersetzen_uebernimmt_metadata_ohne_kapitel(tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps({"title": "The Silmarillion", "asin": "B016N9U1XW", "chapters": [{"start": 0, "end": 1, "title": "Start"}]}))
    importer.carry_metadata(old, new)
    data = json.loads(new.read_text())
    assert data["title"] == "The Silmarillion" and data["asin"] == "B016N9U1XW" and "chapters" not in data


def test_ablage_laeuft_wird_erkannt(tmp_path):
    target = tmp_path / "Book 0 - The Silmarillion"
    assert not importer.busy(target)
    tmp = importer.teil_dir(target, "en")
    try:
        assert importer.busy(str(target))
    finally:
        importer._ACTIVE.discard(str(tmp))
    assert not importer.busy(target) and not importer.busy(None)
