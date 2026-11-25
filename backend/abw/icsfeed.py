"""Kalender-Abo (iCalendar): angekündigte Bände als ganztägige Termine, für Kalender-Apps (Google, Apple, Outlook, Thunderbird).
Der Link trägt einen Schlüssel des Benutzers, weil Kalender-Apps keine Anmeldung mitschicken. Filter: Sprache und Umfang
(gefolgte Serien und Autoren, überwachte Fassungen oder alles). Bände ohne festen Termin fehlen, ein Kalender braucht ein Datum."""
from datetime import date, datetime, timedelta, timezone

from . import config, db, notifications

SCOPES = ("followed", "monitored", "all")
LANGS = ("all", "de", "en")


def select(entries: list[dict], user_id: int, lang: str = "all", scope: str = "followed") -> list[dict]:
    """Einträge aus /api/calendar nach Sprache und Umfang filtern; nur mit festem Termin."""
    if lang not in LANGS or scope not in SCOPES:
        raise ValueError("Unbekannter Filter")
    f = notifications.follows(user_id)
    series, authors = set(f["series"]), set(f["authors"])
    out = []
    for e in entries:
        if not e.get("release_date") or (lang != "all" and e["lang"] != lang):
            continue
        if scope == "monitored" and not e.get("monitored"):
            continue
        if scope == "followed" and e["series_id"] not in series and db.norm(e.get("author") or "") not in authors:
            continue
        out.append(e)
    return out


def _esc(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Zeilen über 75 Byte umbrechen (RFC 5545), ohne ein UTF-8-Zeichen zu zerschneiden."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur)
            cur = b""
        cur += b
    parts.append(cur)
    return "\r\n ".join(p.decode("utf-8") for p in parts)


def build(entries: list[dict], name: str = "AudioBook Wizarrd", lang_ui: str = "de") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    vol = "volume" if lang_ui == "en" else "Band"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AudioBook Wizarrd//Release calendar//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", "X-WR-CALNAME:" + _esc(name), "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H"]
    for e in entries:
        try:
            day = date.fromisoformat(e["release_date"][:10])
        except ValueError:
            continue
        head = f"{e['series']}, {vol} {e['sequence']}" if e.get("sequence") else e["series"]
        summary = f"{head}: {e['title']}" if e.get("title") and e["title"] != e["series"] else head
        desc = " · ".join(x for x in (e.get("author"), "DE" if e["lang"] == "de" else "EN") if x)
        uid = f"{e.get('asin') or (str(e['series_id']) + '-' + e['lang'] + '-' + (e.get('sequence') or e['title']))}@audiobook-wizarrd"
        lines += ["BEGIN:VEVENT", "UID:" + _esc(uid), "DTSTAMP:" + stamp, "DTSTART;VALUE=DATE:" + day.strftime("%Y%m%d"),
                  "DTEND;VALUE=DATE:" + (day + timedelta(days=1)).strftime("%Y%m%d"), "SUMMARY:" + _esc(summary),
                  "DESCRIPTION:" + _esc(desc), "TRANSP:TRANSPARENT"]
        if config.PUBLIC_URL:
            lines.append("URL:" + config.PUBLIC_URL + f"/serie/{e['series_id']}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"
