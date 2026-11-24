"""Push aufs Handy: nur Benutzer mit eingeschaltetem Gerät bekommen etwas, viele Meldungen werden zusammengefasst,
abgemeldete Geräte verschwinden."""
import base64
import os

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from abw import db, notifications, push


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def device(n: int) -> dict:
    """Anmeldung wie vom Browser: echter Geräteschlüssel, damit die Verschlüsselung wirklich läuft."""
    pub = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return {"endpoint": f"https://push.example/{n}", "keys": {"p256dh": b64(pub), "auth": b64(os.urandom(16))}}


class Resp:
    def __init__(self, status):
        self.status_code, self.text, self.content, self.reason, self.headers = status, "", b"", "", {}


def setup(monkeypatch, status=201):
    db.init()
    c = db.conn()
    for t in ("push_sub", "notification", "follow"):
        c.execute(f"DELETE FROM {t}")
    sent = []

    def post(url, data=None, headers=None, timeout=None, **kw):
        sent.append({"url": url, "data": data, "headers": headers})
        return Resp(status)
    monkeypatch.setattr("requests.post", post)
    return c, sent


def test_schluessel_bleibt_gleich():
    key = push.public_key()
    assert len(base64.urlsafe_b64decode(key + "=")) == 65 and push.public_key() == key


def test_anmelden_pruefen_und_abmelden(monkeypatch):
    c, _ = setup(monkeypatch)
    for bad in ({"endpoint": "http://x/1", "keys": {"p256dh": "a", "auth": "b"}}, {"endpoint": "https://x/1", "keys": {}}):
        try:
            push.subscribe(1, bad)
            assert False, "ungültige Anmeldung angenommen"
        except ValueError:
            pass
    push.subscribe(1, device(1), "Handy")
    push.subscribe(1, device(1), "Handy")  # zweimal dasselbe Gerät: ein Eintrag
    assert [d["agent"] for d in push.devices(1)] == ["Handy"] and not push.has_devices(2)
    push.unsubscribe(2, "https://push.example/1")  # fremdes Gerät bleibt
    assert push.has_devices(1)
    push.unsubscribe(1, "https://push.example/1")
    assert not push.has_devices(1)


def test_senden_verschluesselt_mit_vapid(monkeypatch):
    c, sent = setup(monkeypatch)
    push.subscribe(1, device(1))
    push.subscribe(1, device(2))
    assert push.deliver(1, [{"title": "T", "body": "B", "url": "/serie/1", "tag": "x"}]) == 2
    assert {s["url"] for s in sent} == {"https://push.example/1", "https://push.example/2"}
    h = sent[0]["headers"]
    assert h["authorization"].startswith("vapid t=") and h["content-encoding"] == "aes128gcm" and b"/serie/1" not in sent[0]["data"]
    assert all(d["last_ok"] for d in push.devices(1))


def test_viele_meldungen_eine_sammelmeldung(monkeypatch):
    c, sent = setup(monkeypatch)
    push.subscribe(1, device(1))
    got = []
    monkeypatch.setattr(push, "_send", lambda sub, payload: got.append(payload) or True)
    push.deliver(1, [{"title": "T", "body": f"Band {i}", "url": "/", "tag": str(i)} for i in range(2)])
    assert [g["body"] for g in got] == ["Band 0", "Band 1"]
    got.clear()
    push.deliver(1, [{"title": "T", "body": f"Band {i}", "url": "/", "tag": str(i)} for i in range(6)])
    assert len(got) == 1 and got[0]["title"] == "6 neue Meldungen" and got[0]["url"] == "/benachrichtigungen"


def test_abgemeldetes_geraet_verschwindet(monkeypatch):
    c, sent = setup(monkeypatch, status=410)
    push.subscribe(1, device(1))
    assert push.deliver(1, [{"title": "T", "body": "B", "url": "/", "tag": "x"}]) == 0
    assert not push.has_devices(1)


def test_fehler_zaehlen_bis_zum_entfernen(monkeypatch):
    c, sent = setup(monkeypatch, status=500)
    push.subscribe(1, device(1))
    for i in range(push.MAX_FAILS):
        assert push.has_devices(1)
        push.deliver(1, [{"title": "T", "body": "B", "url": "/", "tag": "x"}])
    assert not push.has_devices(1)


def test_meldung_geht_nur_an_benutzer_mit_geraet(monkeypatch):
    c, sent = setup(monkeypatch)
    queued = []
    monkeypatch.setattr(push._queue, "put", queued.append)
    monkeypatch.setattr(push, "_worker", type("W", (), {"is_alive": lambda self: True})())
    push.subscribe(1, device(1))
    notifications.follow(1, "author", "autora")
    notifications.follow(2, "author", "autora")
    for _ in range(2):  # dieselbe Meldung nur einmal
        notifications.emit("new_series", "Neue Serie", "Text", "/autor/autora", "d1", author_key="autora")
    assert queued == [(1, {"title": "Neue Serie", "body": "Text", "url": "/autor/autora", "tag": "d1"})]
    assert notifications.listing(2)["total"] == 1
