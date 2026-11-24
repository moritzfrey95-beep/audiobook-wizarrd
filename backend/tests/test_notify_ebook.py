from abw import db, ebook


def test_epub_meldung_standard_aus(monkeypatch):
    calls = []
    from abw import notifications
    monkeypatch.setattr(notifications, "book_event", lambda *a, **k: calls.append(a))
    ebook._tell({"owned": 1, "id": 1, "title": "X"})
    assert calls == []
    db.set_setting("notify_ebook", "1")
    ebook._tell({"owned": 1, "id": 1, "title": "X"})
    assert len(calls) == 1
