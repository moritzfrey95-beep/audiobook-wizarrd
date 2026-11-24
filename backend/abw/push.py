"""Push-Meldungen aufs Handy (Web Push). Jedes Gerät, auf dem ein Benutzer «Push einschalten» gedrückt hat, bekommt seine
Meldungen aus der Glocke als Benachrichtigung, auch wenn ABW geschlossen ist. Der Schlüssel (VAPID) entsteht beim ersten
Gebrauch im Datenordner. Kommen viele Meldungen auf einmal (z. B. ein grosser Abgleich), geht pro Gerät nur eine Sammelmeldung."""
import base64
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone

from . import config, db

log = logging.getLogger("abw.push")
KEY_FILE = config.DATA_DIR / "vapid_private.pem"
GATHER_SECONDS = 4  # so lange sammeln, bevor gesendet wird
MAX_SINGLE = 3  # mehr Meldungen pro Benutzer auf einmal: eine Sammelmeldung
MAX_FAILS = 5  # danach gilt das Gerät als weg
_queue: queue.Queue = queue.Queue()
_worker: threading.Thread | None = None
_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _vapid():
    from py_vapid import Vapid
    with _lock:
        if not KEY_FILE.exists():
            v = Vapid()
            v.generate_keys()
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            v.save_key(str(KEY_FILE))
            KEY_FILE.chmod(0o600)
        return Vapid.from_file(str(KEY_FILE))


def public_key() -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    raw = _vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def subscribe(user_id: int, sub: dict, agent: str = "") -> None:
    keys = sub.get("keys") or {}
    if not str(sub.get("endpoint", "")).startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError("Ungültige Anmeldung für Push")
    db.conn().execute("INSERT OR REPLACE INTO push_sub(endpoint, user_id, p256dh, auth, agent, created, fails) VALUES(?,?,?,?,?,?,0)",
                      (sub["endpoint"], user_id, keys["p256dh"], keys["auth"], agent[:200], _now()))


def unsubscribe(user_id: int, endpoint: str) -> None:
    db.conn().execute("DELETE FROM push_sub WHERE user_id=? AND endpoint=?", (user_id, endpoint))


def devices(user_id: int) -> list[dict]:
    return [dict(r) for r in db.conn().execute("SELECT endpoint, agent, created, last_ok FROM push_sub WHERE user_id=? ORDER BY created", (user_id,))]


def has_devices(user_id: int) -> bool:
    return bool(db.conn().execute("SELECT 1 FROM push_sub WHERE user_id=? LIMIT 1", (user_id,)).fetchone())


def enqueue(user_id: int, title: str, body: str, link: str, tag: str) -> None:
    """Meldung vormerken. Ohne Gerät des Benutzers passiert nichts."""
    global _worker
    if not has_devices(user_id):
        return
    _queue.put((user_id, {"title": title, "body": body, "url": link or "/benachrichtigungen", "tag": tag}))
    with _lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_run, name="abw-push", daemon=True)
            _worker.start()


def _run() -> None:
    while True:
        first = _queue.get()
        time.sleep(GATHER_SECONDS)
        batch = [first]
        while True:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        by_user: dict[int, list[dict]] = {}
        for uid, msg in batch:
            by_user.setdefault(uid, []).append(msg)
        for uid, msgs in by_user.items():
            try:
                deliver(uid, msgs)
            except Exception:
                log.exception("Push an Benutzer %s fehlgeschlagen", uid)


def _unread(user_id: int) -> int:
    return db.conn().execute("SELECT COUNT(*) FROM notification WHERE user_id=? AND read_at IS NULL", (user_id,)).fetchone()[0]


def deliver(user_id: int, msgs: list[dict]) -> int:
    """Meldungen an alle Geräte des Benutzers. Liefert die Zahl der Geräte, die angenommen haben."""
    if len(msgs) > MAX_SINGLE:
        msgs = [{"title": f"{len(msgs)} neue Meldungen", "body": " · ".join(m["body"] for m in msgs[:4]) + (" …" if len(msgs) > 4 else ""),
                 "url": "/benachrichtigungen", "tag": "abw-sammel"}]
    unread = _unread(user_id)
    ok = 0
    for sub in db.conn().execute("SELECT * FROM push_sub WHERE user_id=?", (user_id,)).fetchall():
        good = all([_send(sub, {**m, "unread": unread}) for m in msgs])
        ok += good
    return ok


def _send(sub, payload: dict) -> bool:
    from pywebpush import WebPushException, webpush
    c = db.conn()
    info = {"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}}
    try:
        webpush(info, json.dumps(payload), vapid_private_key=_vapid(), vapid_claims={"sub": _subject()}, ttl=24 * 3600, timeout=15)
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else 0
        if status in (404, 410):  # Gerät hat Push abgemeldet oder die App wurde entfernt
            c.execute("DELETE FROM push_sub WHERE endpoint=?", (sub["endpoint"],))
        else:
            _failed(sub["endpoint"])
        log.warning("Push abgelehnt (%s): %s", status, exc)
        return False
    except Exception as exc:
        _failed(sub["endpoint"])
        log.warning("Push nicht gesendet: %s", exc)
        return False
    c.execute("UPDATE push_sub SET last_ok=?, fails=0 WHERE endpoint=?", (_now(), sub["endpoint"]))
    return True


def _failed(endpoint: str) -> None:
    c = db.conn()
    c.execute("UPDATE push_sub SET fails=fails+1 WHERE endpoint=?", (endpoint,))
    c.execute("DELETE FROM push_sub WHERE endpoint=? AND fails>=?", (endpoint, MAX_FAILS))


def _subject() -> str:
    url = config.PUBLIC_URL
    return url if url.startswith("https://") else "mailto:abw@localhost"
