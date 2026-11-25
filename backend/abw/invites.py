"""Einladungsseite für neue Mitglieder: App installieren, anmelden, Wünsche in ABW. Mit QR-Codes zum Abfotografieren.

Der Administrator legt eine Einladung mit Namen an und gibt den Link weiter. Die Seite ist ohne Anmeldung lesbar,
zeigt aber nur die Adressen von ABW und Audiobookshelf und den Namen; sie legt kein Konto an (Konten kommen aus
Audiobookshelf). Einladungen laufen nach 30 Tagen ab und lassen sich jederzeit löschen.
"""
import io
import secrets
from datetime import datetime, timedelta, timezone

from . import config, db

DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(name: str, user_id: int | None = None) -> dict:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise ValueError("Bitte einen Namen angeben")
    code = secrets.token_urlsafe(9)
    db.conn().execute("INSERT INTO invite(code, name, created, expires, created_by) VALUES(?,?,?,?,?)",
                      (code, name, _now().isoformat(), (_now() + timedelta(days=DAYS)).isoformat(), user_id))
    return get(code, admin=True)


def listing() -> list[dict]:
    return [dict(r) | {"expired": r["expires"] < _now().isoformat()} for r in
            db.conn().execute("SELECT code, name, created, expires, opened FROM invite ORDER BY created DESC")]


def delete(code: str) -> None:
    db.conn().execute("DELETE FROM invite WHERE code=?", (code,))


def urls() -> dict:
    return {"abw": (config.PUBLIC_URL or "").rstrip("/"), "abs": (config.ABS_PUBLIC_URL or config.ABS_URL or "").rstrip("/")}


def get(code: str, admin: bool = False) -> dict:
    r = db.conn().execute("SELECT * FROM invite WHERE code=?", (code,)).fetchone()
    if not r or (r["expires"] < _now().isoformat() and not admin):
        raise LookupError("Diese Einladung gibt es nicht mehr. Bitte frag nach einem neuen Link.")
    if not admin and not r["opened"]:
        db.conn().execute("UPDATE invite SET opened=? WHERE code=?", (_now().isoformat(), code))
    return {"code": r["code"], "name": r["name"], "expires": r["expires"], **urls()}


def qr_svg(text: str) -> str:
    import segno
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=6, border=2, dark="#111111", light="#ffffff", xmldecl=False)
    return buf.getvalue().decode("utf-8")
