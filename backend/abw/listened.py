"""Bücher in ABW als gehört markieren und den Stand nach Audiobookshelf übertragen.

Audiobookshelf setzt den Hörstand nur für das angemeldete Konto selbst (/api/me/progress), auch ein Administrator kann das
für andere nicht. Deshalb:
- Das Konto, mit dessen Schlüssel ABW liest (meist der Administrator), schreibt mit diesem Schlüssel.
- Jedes andere verknüpfte Konto braucht eine eigene Sitzung. Meldet sich jemand in ABW mit seinem Audiobookshelf-Passwort an,
  behält ABW den Erneuerungsschlüssel dieser Sitzung, verschlüsselt mit einem Schlüssel aus dem Datenordner. Das Passwort
  speichert ABW nie. Audiobookshelf tauscht den Erneuerungsschlüssel bei jeder Verwendung aus (30 Tage gültig); die
  tägliche Aufgabe «sessions_keep» hält die Sitzungen so am Leben.

«Nicht gehört» nimmt die Markierung zurück. Hatte das Buch vorher eine Hörposition, kommt sie zurück; sonst steht es
wieder auf Anfang (so macht es Audiobookshelf selbst)."""
import os
import time
from datetime import datetime, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken

from . import abs as abs_mod
from . import config, db

KEY_FILE = "abs-sitzungen.key"
ACCESS_SECONDS = 45 * 60  # Audiobookshelf gibt Zugangsschlüssel für eine Stunde aus
_own: dict[str, str] = {}
_access: dict[int, tuple[str, float]] = {}  # Benutzer -> (Zugangsschlüssel, gültig bis); spart Erneuerungen (Ratenlimit)


class NeedLogin(Exception):
    """Für dieses Konto hat ABW keine gültige Audiobookshelf-Sitzung."""


def _fernet() -> Fernet:
    path = config.DATA_DIR / KEY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return Fernet(path.read_bytes().strip())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def own_abs_id() -> str:
    """Konto in Audiobookshelf, zu dem der Schlüssel von ABW gehört (einmal nachgefragt)."""
    if "id" not in _own:
        with abs_mod._client() as http:
            _own["id"] = http.get("/api/me").raise_for_status().json()["id"]
    return _own["id"]


def _logout(refresh: str) -> None:
    try:
        with httpx.Client(base_url=config.ABS_URL, timeout=20) as http:
            http.post("/logout", headers={"x-refresh-token": refresh})
    except httpx.HTTPError:
        pass


def _stored(user_id: int) -> str | None:
    r = db.conn().execute("SELECT abs_session FROM user WHERE id=?", (user_id,)).fetchone()
    if not r or not r["abs_session"]:
        return None
    try:
        return _fernet().decrypt(r["abs_session"].encode()).decode()
    except InvalidToken:
        return None


def remember(user_id: int, refresh: str) -> None:
    """Neue Sitzung nach einer Anmeldung mit dem Audiobookshelf-Passwort. Eine ältere Sitzung wird geschlossen."""
    old = _stored(user_id)
    db.conn().execute("UPDATE user SET abs_session=?, abs_session_at=? WHERE id=?",
                      (_fernet().encrypt(refresh.encode()).decode(), _now(), user_id))
    if old and old != refresh:
        _logout(old)


def forget(user_id: int) -> None:
    """Sitzung verwerfen (Verknüpfung gelöst, Benutzer gelöscht) und in Audiobookshelf schliessen."""
    old = _stored(user_id)
    _access.pop(user_id, None)
    db.conn().execute("UPDATE user SET abs_session=NULL, abs_session_at=NULL WHERE id=?", (user_id,))
    if old:
        _logout(old)


def _uses_key(row) -> bool:
    """Benutzer schreibt mit dem Schlüssel von ABW: sein Konto ist das von ABW, oder Benutzer 1 ohne Verknüpfung."""
    if not row["abs_user_id"]:
        return row["id"] == 1
    try:
        return row["abs_user_id"] == own_abs_id()
    except httpx.HTTPError:
        return False


def _refresh(user_id: int, refresh: str) -> str:
    """Neuer Zugangsschlüssel; der Erneuerungsschlüssel wird dabei ausgetauscht und neu gespeichert."""
    try:
        with httpx.Client(base_url=config.ABS_URL, timeout=30) as http:
            r = http.post("/auth/refresh", headers={"x-refresh-token": refresh})
    except httpx.HTTPError as exc:
        raise ConnectionError("Audiobookshelf ist gerade nicht erreichbar") from exc
    if r.status_code in (401, 403):
        _access.pop(user_id, None)
        db.conn().execute("UPDATE user SET abs_session=NULL, abs_session_at=NULL WHERE id=?", (user_id,))
        raise NeedLogin("Die Audiobookshelf-Sitzung ist abgelaufen. Melde dich einmal ab und mit deinem Audiobookshelf-Passwort wieder an.")
    if r.status_code == 429:
        raise ConnectionError("Audiobookshelf nimmt gerade keine Anmeldungen an. Bitte in einigen Minuten erneut versuchen.")
    user = r.raise_for_status().json().get("user") or {}
    _access[user_id] = (user["accessToken"], time.time() + ACCESS_SECONDS)
    if user.get("refreshToken"):
        db.conn().execute("UPDATE user SET abs_session=?, abs_session_at=? WHERE id=?",
                          (_fernet().encrypt(user["refreshToken"].encode()).decode(), _now(), user_id))
    return user["accessToken"]


def _client_for(user_id: int) -> httpx.Client:
    row = db.conn().execute("SELECT * FROM user WHERE id=?", (user_id,)).fetchone()
    if row is None and user_id == 1:  # ABW ohne Anmeldung
        return abs_mod._client()
    if row is None:
        raise LookupError("Benutzer nicht gefunden")
    if _uses_key(row):
        return abs_mod._client()
    if not row["abs_user_id"] or row["abs_active"] == 0:
        raise NeedLogin("Dein ABW-Konto ist mit keinem aktiven Audiobookshelf-Konto verknüpft.")
    cached = _access.get(user_id)
    if cached and cached[1] > time.time():
        token = cached[0]
    else:
        refresh = _stored(user_id)
        if not refresh:
            raise NeedLogin("Melde dich einmal ab und mit deinem Audiobookshelf-Passwort wieder an, dann kann ABW deinen Hörstand setzen.")
        token = _refresh(user_id, refresh)
    return httpx.Client(base_url=config.ABS_URL, timeout=60, headers={"Authorization": f"Bearer {token}"})


def status(user_id: int) -> dict:
    """Kann der Benutzer markieren? «why» erklärt, was fehlt."""
    row = db.conn().execute("SELECT * FROM user WHERE id=?", (user_id,)).fetchone()
    if row is None:
        return {"can": user_id == 1, "why": None if user_id == 1 else "Benutzer nicht gefunden"}
    if _uses_key(row):
        return {"can": True, "why": None}
    if not row["abs_user_id"] or row["abs_active"] == 0:
        return {"can": False, "why": "Dein ABW-Konto ist mit keinem aktiven Audiobookshelf-Konto verknüpft."}
    if not row["abs_session"]:
        return {"can": False, "why": "Melde dich einmal ab und mit deinem Audiobookshelf-Passwort wieder an, dann kannst du Bücher als gehört markieren."}
    return {"can": True, "why": None}


def mark(user_id: int, book_id: int, finished: bool) -> dict:
    """Buch als gehört oder nicht gehört markieren, in Audiobookshelf und gleich auch in ABW."""
    c = db.conn()
    b = c.execute("SELECT id, title, abs_item_id FROM book WHERE id=? AND owned=1", (book_id,)).fetchone()
    if not b or not b["abs_item_id"]:
        raise LookupError("Das Buch liegt nicht in Audiobookshelf")
    item = b["abs_item_id"]
    with _client_for(user_id) as http:
        r = http.get(f"/api/me/progress/{item}")
        prev = r.json() if r.status_code == 200 else None
        if r.status_code not in (200, 404):
            r.raise_for_status()
        if finished:
            if not (prev and prev.get("isFinished")):
                c.execute("INSERT OR REPLACE INTO mark_undo(user_id, book_id, cur_time, progress, created) VALUES(?,?,?,?,?)",
                          (user_id, book_id, (prev or {}).get("currentTime") or 0, (prev or {}).get("progress") or 0, _now()))
                http.patch(f"/api/me/progress/{item}", json={"isFinished": True}).raise_for_status()
        else:
            undo = c.execute("SELECT cur_time, progress FROM mark_undo WHERE user_id=? AND book_id=?", (user_id, book_id)).fetchone()
            if prev and prev.get("isFinished"):
                http.patch(f"/api/me/progress/{item}", json={"isFinished": False}).raise_for_status()
                if undo and (undo["cur_time"] or 0) > 0:
                    http.patch(f"/api/me/progress/{item}",
                               json={"currentTime": undo["cur_time"], "progress": undo["progress"]}).raise_for_status()
        after = http.get(f"/api/me/progress/{item}")
        now = after.json() if after.status_code == 200 else {}
    if not finished:
        c.execute("DELETE FROM mark_undo WHERE user_id=? AND book_id=?", (user_id, book_id))
    c.execute("INSERT OR REPLACE INTO user_progress(user_id, book_id, progress, finished, updated) VALUES(?,?,?,?,?)",
              (user_id, book_id, now.get("progress"), 1 if now.get("isFinished") else 0, _now()))
    if now.get("isFinished"):
        abs_mod.auto_mark(c, user_id)
        from . import toread
        toread.on_finished(c, user_id, {book_id})
    return {"finished": bool(now.get("isFinished")), "progress": now.get("progress")}


def keep_alive(progress=lambda msg: None) -> str:
    """Gespeicherte Sitzungen einmal am Tag erneuern, damit sie nicht nach 30 Tagen ohne Markierung verfallen."""
    rows = db.conn().execute("SELECT id, name FROM user WHERE abs_session IS NOT NULL").fetchall()
    ok = lost = 0
    for r in rows:
        refresh = _stored(r["id"])
        if not refresh:
            continue
        progress(f"Sitzung von {r['name']}")
        try:
            _refresh(r["id"], refresh)
            ok += 1
        except NeedLogin:
            lost += 1
    return f"{ok} Audiobookshelf-Sitzungen erneuert" + (f", {lost} abgelaufen (Anmeldung nötig)" if lost else "")
