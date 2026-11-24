"""Umwandlung an echten kleinen Tondateien: M4B entsteht, Originale landen im Papierkorb, nichts geht verloren."""
import shutil
import subprocess

import pytest

from abw import config, convert

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def tone(path, seconds, freq):
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-b:a", "64k", str(path)], check=True)


def make_book(tmp_path, monkeypatch, n=3):
    lib, trash = tmp_path / "lib", tmp_path / "trash"
    folder = lib / "Serie" / "Book 1 - Test"
    folder.mkdir(parents=True)
    for i in range(1, n + 1):
        tone(folder / f"{i:02d} - Kapitel {i}.mp3", 25, 300 + i * 50)
    (folder / "metadata.json").write_text("{}")
    monkeypatch.setattr(config, "LIBRARY_ROOT", {"en": lib, "de": lib})
    monkeypatch.setattr(config, "LIBRARY_TRASH", trash)
    return dict(title="Test: Buch", author="A", lang="en", rel_path="Serie/Book 1 - Test"), folder, trash


def test_convert_builds_m4b_and_moves_originals_to_trash(tmp_path, monkeypatch):
    book, folder, trash = make_book(tmp_path, monkeypatch)
    res = convert.convert(book)
    assert res["files"] == 3
    assert sorted(p.name for p in folder.iterdir()) == ["Test Buch.m4b", "metadata.json"]
    kept = sorted(p.name for p in trash.rglob("*.mp3"))
    assert kept == ["01 - Kapitel 1.mp3", "02 - Kapitel 2.mp3", "03 - Kapitel 3.mp3"]
    out = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-print_format", "json", str(folder / "Test Buch.m4b")], capture_output=True, text=True)
    assert out.stdout.count('"title"') == 3 and "Kapitel 2" in out.stdout


def test_single_file_or_existing_m4b_is_refused(tmp_path, monkeypatch):
    book, folder, trash = make_book(tmp_path, monkeypatch, n=2)
    (folder / "schon-da.m4b").write_bytes(b"x")
    with pytest.raises(ValueError):
        convert.convert(book)
    assert len(list(folder.glob("*.mp3"))) == 2 and not trash.exists()
