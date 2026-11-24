"""Anmeldung und Benutzer. Solange kein Benutzer angelegt ist, bleibt ABW offen (Heimnetz, Tailscale) und jeder Besucher
handelt als Administrator. Es gibt zwei Rollen: «admin» darf alles, «user» darf ansehen, hinzufügen, die automatische
Suche für Bücher und Serien anstossen und sein Goodreads-Regal verknüpfen.
Gespeichert wird nur ein scrypt-Hash des Passworts und von jeder Sitzung nur der Hash des Sitzungsschlüssels.
Ein Benutzer kann mit einem Konto in Audiobookshelf verknüpft sein. Dann gelten dessen Benutzername und Passwort auch hier:
ABW fragt bei der Anmeldung Audiobookshelf und speichert das Passwort nicht. Ein eigenes ABW-Passwort gilt daneben weiter."""
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timezone

from . import abs as abs_mod
from . import db

COOKIE = "abw_session"
SESSION_DAYS = 30
MIN_LENGTH = 10
WINDOW = 15 * 60  # Sekunden
MAX_PER_IP, MAX_TOTAL = 5, 30  # Fehlversuche je Fenster
ROLES = ("admin", "user")
OPEN_USER = {"id": 1, "name": None, "role": "admin"}  # ohne Anmeldung: alles gehört Benutzer 1
_fails: list[tuple[float, str]] = []
_lock = threading.Lock()
_DUMMY = "00" * 16 + "$" + "0" * 64


def enabled() -> bool:
    return bool(db.conn().execute("SELECT 1 FROM user LIMIT 1").fetchone())


def _scrypt(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def _hash(password: str) -> str:
    if len(password) < MIN_LENGTH:
        raise ValueError(f"Das Passwort braucht mindestens {MIN_LENGTH} Zeichen")
    salt = secrets.token_bytes(16)
    return salt.hex() + "$" + _scrypt(password, salt)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _public(row) -> dict:
    return {"id": row["id"], "name": row["name"], "role": row["role"]}


def find(name: str):
    """Benutzer zum eingegebenen Namen: ABW-Name, sonst der Benutzername des verknüpften Audiobookshelf-Kontos."""
    c = db.conn()
    name = (name or "").strip()
    return (c.execute("SELECT * FROM user WHERE name=?", (name,)).fetchone()
            or c.execute("SELECT * FROM user WHERE abs_user_id IS NOT NULL AND abs_name=? COLLATE NOCASE", (name,)).fetchone())


def get(user_id: int):
    return db.conn().execute("SELECT * FROM user WHERE id=?", (user_id,)).fetchone()


def users() -> list[dict]:
    return [{**_public(r), "created": r["created"], "goodreads": bool(r["goodreads_user"]), "has_password": "$" in r["hash"],
             "abs_user_id": r["abs_user_id"], "abs_name": r["abs_name"] if r["abs_user_id"] else None,
             "abs_active": bool(r["abs_user_id"]) and r["abs_active"] != 0}
            for r in db.conn().execute("SELECT * FROM user ORDER BY role, name")]


def password_ok(name: str, password: str, keep_session: bool = False):
    """Liefert den Benutzer, wenn Name und Passwort stimmen, sonst None. Rechnet immer (gleiche Antwortzeit).
    Stimmt das ABW-Passwort nicht (oder gibt es keins) und ist der Benutzer mit Audiobookshelf verknüpft, entscheidet
    Audiobookshelf. Ist es nicht erreichbar, kommt ConnectionError."""
    row = find(name)
    stored = row["hash"] if row and "$" in row["hash"] else _DUMMY
    salt, want = stored.split("$", 1)
    good = hmac.compare_digest(_scrypt(password, bytes.fromhex(salt)), want)
    if good and row:
        return row
    if row and row["abs_user_id"] and row["abs_active"] != 0 and password:
        if keep_session:
            from . import listened
            keep = lambda refresh: listened.remember(row["id"], refresh)
            return row if abs_mod.login_ok(row["abs_name"] or row["name"], password, row["abs_user_id"], keep=keep) else None
        return row if abs_mod.login_ok(row["abs_name"] or row["name"], password, row["abs_user_id"]) else None
    return None


def has_password(user_id: int) -> bool:
    row = get(user_id)
    return bool(row) and "$" in row["hash"]


def link_abs(user_id: int, abs_user: dict | None) -> None:
    """Benutzer mit einem Audiobookshelf-Konto verknüpfen (oder mit None trennen)."""
    c = db.conn()
    row = get(user_id)
    if not row:
        raise LookupError("Benutzer nicht gefunden")
    if abs_user is None:
        if "$" not in row["hash"]:
            raise ValueError(f"«{row['name']}» hat kein eigenes ABW-Passwort. Setze zuerst eines, sonst kann er sich nicht mehr anmelden.")
        c.execute("UPDATE user SET abs_user_id=NULL, abs_name=NULL, abs_active=NULL WHERE id=?", (user_id,))
    else:
        other = c.execute("SELECT name FROM user WHERE abs_user_id=? AND id<>?", (abs_user["id"], user_id)).fetchone()
        if other:
            raise ValueError(f"Dieses Audiobookshelf-Konto ist schon mit «{other['name']}» verknüpft")
        clash = c.execute("SELECT name FROM user WHERE name=? AND id<>?", (abs_user["username"], user_id)).fetchone()
        if clash:
            raise ValueError(f"Der Name «{abs_user['username']}» gehört in ABW schon einem anderen Benutzer")
        c.execute("UPDATE user SET abs_user_id=?, abs_name=?, abs_active=? WHERE id=?",
                  (abs_user["id"], abs_user["username"], 1 if abs_user.get("isActive", True) else 0, user_id))
    c.execute("DELETE FROM user_progress WHERE user_id=?", (user_id,))
    if abs_user is None or abs_user["id"] != row["abs_user_id"]:
        _forget(user_id)


def _forget(user_id: int) -> None:
    from . import listened
    listened.forget(user_id)
    db.conn().execute("DELETE FROM mark_undo WHERE user_id=?", (user_id,))


def import_abs(abs_user: dict) -> tuple[int, bool]:
    """Audiobookshelf-Konto übernehmen: gleicher Benutzername, Passwort bleibt bei Audiobookshelf. Gibt es in ABW schon
    einen unverknüpften Benutzer mit diesem Namen, wird er verknüpft. Liefert (Benutzer, neu angelegt)."""
    if not enabled():
        raise ValueError("Lege zuerst dein eigenes Passwort fest; damit wirst du Administrator.")
    c = db.conn()
    row = c.execute("SELECT id FROM user WHERE abs_user_id=?", (abs_user["id"],)).fetchone()
    if row:
        return row["id"], False
    name = (abs_user.get("username") or "").strip()
    if not name:
        raise ValueError("Das Konto hat keinen Benutzernamen")
    row = c.execute("SELECT * FROM user WHERE name=?", (name,)).fetchone()
    if row and row["abs_user_id"]:
        raise ValueError(f"Der Name «{name}» ist in ABW schon mit einem anderen Audiobookshelf-Konto verknüpft")
    if row:
        link_abs(row["id"], abs_user)
        return row["id"], False
    uid = c.execute("INSERT INTO user(name, hash, role, created, abs_user_id, abs_name, abs_active) VALUES(?,?,?,?,?,?,?)",
                    (name, "", "user", datetime.now(timezone.utc).isoformat(), abs_user["id"], name,
                     1 if abs_user.get("isActive", True) else 0)).lastrowid
    return uid, True


def create(name: str, password: str, role: str = "user") -> int:
    name = (name or "").strip()
    if not name:
        raise ValueError("Der Benutzername fehlt")
    if role not in ROLES:
        raise ValueError("Unbekannte Rolle")
    if find(name):
        raise ValueError("Diesen Benutzernamen gibt es schon")
    c = db.conn()
    first = not enabled()
    # Der erste Benutzer wird Administrator Nummer 1 und übernimmt, was bisher ohne Anmeldung eingerichtet war
    return c.execute("INSERT INTO user(id, name, hash, role, goodreads_user, goodreads_shelf, created) VALUES(?,?,?,?,?,?,?)",
                     (1 if first else None, name, _hash(password), "admin" if first else role,
                      db.setting("goodreads_user") if first else "", (db.setting("goodreads_shelf") if first else "") or "to-read",
                      datetime.now(timezone.utc).isoformat())).lastrowid


def set_password(user_id: int, password: str, name: str | None = None, keep_token: str | None = None) -> None:
    """Neues Passwort (und auf Wunsch neuer Name). Alle Sitzungen des Benutzers enden, ausser der genannten."""
    c = db.conn()
    row = get(user_id)
    if not row:
        raise LookupError("Benutzer nicht gefunden")
    name = (name or "").strip() or row["name"]
    other = find(name)
    if other and other["id"] != user_id:
        raise ValueError("Diesen Benutzernamen gibt es schon")
    c.execute("UPDATE user SET name=?, hash=? WHERE id=?", (name, _hash(password), user_id))
    c.execute("DELETE FROM session WHERE user_id=? AND token_hash<>?", (user_id, _digest(keep_token) if keep_token else ""))


def admins() -> int:
    return db.conn().execute("SELECT COUNT(*) FROM user WHERE role='admin'").fetchone()[0]


def set_role(user_id: int, role: str) -> None:
    row = get(user_id)
    if not row:
        raise LookupError("Benutzer nicht gefunden")
    if role not in ROLES:
        raise ValueError("Unbekannte Rolle")
    if row["role"] == "admin" and role != "admin" and admins() <= 1:
        raise ValueError("Es muss mindestens ein Administrator bleiben")
    db.conn().execute("UPDATE user SET role=? WHERE id=?", (role, user_id))


def delete(user_id: int) -> None:
    row = get(user_id)
    if not row:
        raise LookupError("Benutzer nicht gefunden")
    if row["role"] == "admin" and admins() <= 1:
        raise ValueError("Der letzte Administrator lässt sich nicht löschen")
    _forget(user_id)
    c = db.conn()
    for table in ("session", "follow", "notification", "user_progress", "auto_follow", "rating", "toread", "toread_skip", "toread_pending", "toread_dismiss", "toread_pick_dismiss"):
        c.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    c.execute("DELETE FROM wishlist_item WHERE source=?", (f"gr{user_id}",))
    c.execute("DELETE FROM toread_rec WHERE from_user=? OR to_user=?", (user_id, user_id))
    c.execute("DELETE FROM rec_allow WHERE from_user=? OR to_user=?", (user_id, user_id))
    c.execute("DELETE FROM user WHERE id=?", (user_id,))


def disable() -> None:
    """Anmeldung abschalten: nur möglich, solange der Administrator der einzige Benutzer ist."""
    c = db.conn()
    if c.execute("SELECT COUNT(*) FROM user").fetchone()[0] > 1:
        raise ValueError("Lösche zuerst die anderen Benutzer")
    c.execute("DELETE FROM user")
    c.execute("DELETE FROM session")
    c.execute("DELETE FROM user_progress WHERE user_id<>1")


def blocked(ip: str) -> bool:
    now = time.time()
    with _lock:
        _fails[:] = [f for f in _fails if now - f[0] < WINDOW]
        return len(_fails) >= MAX_TOTAL or sum(1 for f in _fails if f[1] == ip) >= MAX_PER_IP


def fail(ip: str) -> None:
    with _lock:
        _fails.append((time.time(), ip))


def open_session(user_id: int, agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    c = db.conn()
    c.execute("DELETE FROM session WHERE expires < ?", (time.time(),))
    c.execute("INSERT INTO session(token_hash, created, expires, agent, user_id) VALUES(?,?,?,?,?)",
              (_digest(token), time.time(), time.time() + SESSION_DAYS * 86400, agent[:200], user_id))
    return token


def login(name: str, password: str, ip: str, agent: str = "") -> str:
    """Prüft die Anmeldung und liefert den Sitzungsschlüssel. PermissionError bei Sperre, ValueError bei falschen Angaben."""
    if blocked(ip):
        raise PermissionError("Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
    row = password_ok(name, password, keep_session=True)
    if not row:
        fail(ip)
        raise ValueError("Benutzername oder Passwort stimmt nicht")
    return open_session(row["id"], agent)


def session_user(token: str | None) -> dict | None:
    """Der angemeldete Benutzer zu einem Sitzungsschlüssel."""
    if not token:
        return None
    row = db.conn().execute("SELECT u.* FROM session s JOIN user u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires > ?",
                            (_digest(token), time.time())).fetchone()
    return _public(row) if row else None


def session_ok(token: str | None) -> bool:
    return session_user(token) is not None


def logout(token: str | None) -> None:
    if token:
        db.conn().execute("DELETE FROM session WHERE token_hash=?", (_digest(token),))
