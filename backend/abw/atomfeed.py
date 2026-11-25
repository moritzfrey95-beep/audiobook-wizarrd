"""Atom-Feed je Benutzer: seine Meldungen (neu im Bestand, angekündigt, erschienen, neue Serien, Sprecher) für Feed-Reader.
Der Link trägt denselben Schlüssel wie das Kalender-Abo, weil Feed-Reader keine Anmeldung mitschicken."""
from xml.sax.saxutils import escape

from . import config, db, i18n

KINDS = ("available", "announced", "new_book", "released", "new_series", "narrator", "epub")


def build(user_id: int, lang: str = "de", limit: int = 50) -> str:
    rows = db.conn().execute(
        f"SELECT id, kind, title, body, link, created FROM notification WHERE user_id=? AND kind IN ({','.join('?' * len(KINDS))})"
        " ORDER BY id DESC LIMIT ?", (user_id, *KINDS, limit)).fetchall()
    base = (config.PUBLIC_URL or "").rstrip("/")
    updated = rows[0]["created"] if rows else "2026-01-01T00:00:00+00:00"
    tr = lambda s: escape(i18n.tr(s or "", lang))
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           f"<title>{tr('AudioBook Wizarrd: Neuigkeiten')}</title>", f"<id>urn:abw:feed:{user_id}</id>", f"<updated>{escape(updated)}</updated>"]
    if base:
        out.append(f'<link href="{escape(base)}/benachrichtigungen"/>')
    for r in rows:
        out += ["<entry>", f"<id>urn:abw:note:{r['id']}</id>", f"<title>{tr(r['title'])}: {tr(r['body'])}</title>",
                f"<updated>{escape(r['created'])}</updated>", f"<summary>{tr(r['body'])}</summary>",
                f"<category term=\"{escape(r['kind'])}\"/>"]
        if base and r["link"]:
            out.append(f'<link href="{escape(base + r["link"])}"/>')
        out.append("</entry>")
    out.append("</feed>")
    return "\n".join(out)
