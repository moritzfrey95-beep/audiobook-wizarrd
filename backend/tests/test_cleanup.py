import os
from datetime import datetime, timedelta

from abw import cleanup, config, db


def test_only_finished_or_unknown_downloads_are_moved_and_purge_respects_age(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USENET_COMPLETE_ROOT", tmp_path)
    monkeypatch.setattr(config, "TORRENT_COMPLETE_ROOT", tmp_path / "kein-torrent-ordner")
    db.init()
    c = db.conn()
    c.execute("DELETE FROM download")
    for marker, state in (("abw-1-done", "done"), ("abw-2-busy", "verified"), ("abw-3-bad", "rejected")):
        (tmp_path / marker).mkdir()
        (tmp_path / marker / "a.m4b").write_bytes(b"x" * 10)
        c.execute("INSERT INTO download(book_id, source, release_title, marker, state, created, updated) VALUES(1,'usenet','R',?,?,'2026','2026')", (marker, state))
    (tmp_path / "abw-9-orphan").mkdir()
    (tmp_path / "hbo-fremd").mkdir()  # fremder Ordner, wird nie angefasst

    assert {x["marker"] for x in cleanup.candidates()} == {"abw-1-done", "abw-3-bad", "abw-9-orphan"}
    assert cleanup.run()["moved"] == 3
    assert (tmp_path / "abw-2-busy").is_dir() and (tmp_path / "hbo-fremd").is_dir() and not (tmp_path / "abw-1-done").exists()
    assert len(cleanup.trash()) == 3

    assert cleanup.purge(14)["removed"] == 0  # heute verschoben, bleibt
    today = tmp_path / cleanup.TRASH / datetime.now().strftime("%Y-%m-%d")
    old = tmp_path / cleanup.TRASH / (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    os.rename(today, old)
    assert cleanup.purge(14)["removed"] == 3 and cleanup.trash() == []
