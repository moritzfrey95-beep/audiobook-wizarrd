"""Meldungsarten je Benutzer, stummgeschaltete Serien, Wochen-Zusammenfassung und Webhook."""
from datetime import datetime, timedelta, timezone

from abw import db, notifications, notify, push


def setup(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series", "follow", "follow_mute", "notification", "notify_pref"):
        c.execute(f"DELETE FROM {t}")
    sent = []
    monkeypatch.setattr(push, "enqueue", lambda uid, title, body, link, tag: sent.append((uid, title, body)))
    sid = c.execute("INSERT INTO series(name, norm) VALUES('Zyklus Q','zyklusq')").lastrowid
    return c, sid, sent


def test_abgeschaltete_art_kommt_nicht(monkeypatch):
    c, sid, sent = setup(monkeypatch)
    for uid in (1, 2):
        notifications.follow(uid, "series", sid)
    notifications.set_prefs(2, {"epub": False})
    notifications.emit("epub", "E-Book abgelegt", "Band 1", "/serie/1", "epub:1", series_id=sid)
    notifications.emit("announced", "Neuer Band angekündigt", "Band 2", "/serie/1", "ann:2", series_id=sid)
    assert notifications.listing(1)["total"] == 2
    assert [n["kind"] for n in notifications.listing(2)["items"]] == ["announced"]
    assert {u for u, *_ in sent} == {1, 2} and len(sent) == 3
    kinds = {k["kind"]: k["on"] for k in notifications.prefs(2)["kinds"]}
    assert kinds["epub"] is False and kinds["announced"] is True
    notifications.set_prefs(2, {"epub": True})
    assert all(k["on"] for k in notifications.prefs(2)["kinds"])


def test_stumme_serie_meldet_nichts_auch_ueber_den_autor(monkeypatch):
    c, sid, sent = setup(monkeypatch)
    notifications.follow(1, "series", sid)
    notifications.follow(1, "author", "autorina")
    notifications.mute(1, sid)
    notifications.emit("announced", "Neuer Band angekündigt", "x", "/", "a:1", series_id=sid, author_key="autorina")
    assert notifications.listing(1)["total"] == 0
    assert notifications.follows(1)["series"] == [sid]  # bleibt markiert
    notifications.emit("new_series", "Neue Serie von A", "y", "/", "s:1", author_key="autorina")
    assert notifications.listing(1)["total"] == 1  # andere Serien des Autors melden sich weiter
    notifications.mute(1, sid, False)
    notifications.emit("announced", "Neuer Band angekündigt", "z", "/", "a:2", series_id=sid)
    assert notifications.listing(1)["total"] == 2


def test_wochen_zusammenfassung(monkeypatch):
    c, sid, sent = setup(monkeypatch)
    notifications.follow(1, "series", sid)
    notifications.set_prefs(1, digest=True)
    notifications.emit("announced", "Neuer Band angekündigt", "a", "/", "a:1", series_id=sid)
    notifications.emit("available", "Neu im Bestand", "b", "/", "b:1", series_id=sid)
    notifications.emit("available", "Neu im Bestand", "c", "/", "c:1", series_id=sid)
    assert sent == [] and notifications.listing(1)["total"] == 3  # Glocke ja, Push nicht einzeln
    assert notifications.digest() == 0  # noch keine Woche um
    later = datetime.now(timezone.utc) + timedelta(days=7, minutes=1)
    assert notifications.digest(later) == 1
    uid, title, body = sent[0]
    assert uid == 1 and title == "Deine Woche: 3 Meldungen" and body.startswith("Neu im Bestand (2)")
    assert notifications.digest(later + timedelta(days=1)) == 0  # nicht doppelt
    assert notifications.digest(later + timedelta(days=8)) == 0  # leere Woche: nichts
    notifications.set_prefs(1, digest=False)
    notifications.emit("announced", "Neuer Band angekündigt", "d", "/", "d:1", series_id=sid)
    assert len(sent) == 2


def test_webhook_formate():
    assert notify.webhook_kind("https://discord.com/api/webhooks/1/x") == "discord"
    assert notify.webhook_kind("https://hooks.slack.com/services/x") == "slack"
    assert notify.webhook_kind("https://x.webhook.office.com/webhookb2/y") == "teams"
    assert notify.webhook_kind("http://n8n:5678/webhook/abw") == "json"
    d = notify.webhook_payload("discord", "Titel", "Text", "https://abw/x", "books")
    assert d["embeds"][0]["title"] == "Titel" and d["embeds"][0]["url"] == "https://abw/x"
    assert notify.webhook_payload("slack", "T", "M", "", "x") == {"text": "*T*\nM"}
    j = notify.webhook_payload("json", "T", "M", "", "a,b")
    assert j["title"] == "T" and j["tags"] == ["a", "b"] and j["url"] is None


def test_send_geht_auch_nur_an_webhook(monkeypatch):
    from abw import config
    db.init()
    db.set_setting("notify", "1")
    monkeypatch.setattr(config, "NTFY_URL", "")
    monkeypatch.setattr(config, "secret", lambda n: "https://discord.com/api/webhooks/1/x" if n == "WEBHOOK_URL" else "")
    calls = []

    class R:
        status_code = 204
    monkeypatch.setattr(notify.httpx, "post", lambda url, json, timeout: calls.append((url, json)) or R())
    assert notify.send("Neu im Bestand", "Buch", "/pruefen") is True
    assert calls[0][1]["embeds"][0]["title"] == "Neu im Bestand"
    db.set_setting("notify", "0")
    assert notify.send("x", "y") is False and len(calls) == 1
