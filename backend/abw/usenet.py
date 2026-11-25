"""Quelle Usenet: Suche über Prowlarr (nur Usenet-Indexer), Übergabe der NZB an SABnzbd."""
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from . import __version__
from . import config, db, scoring

UA = f"AudioBookWizarrd/{__version__}"  # NZBgeek und altHUB sperren Standard-Agenten mit 403
_releases: dict[str, tuple[float, dict]] = {}  # guid -> (Zeit, Roh-Treffer); Download-Adressen nie in die Datenbank
# Hörbuch (3030), Audio/Other (3050) und Audio/Foreign (3060): Treasure Maps führt die meisten deutschen Hörbücher
# unter 3050, NZBFinder fremdsprachige unter 3060. MP3 (3010) und Lossless (3040) bleiben weg: dort liegt fast nur Musik,
# die bei kurzen Titeln («Dune») als unsicherer Treffer durchrutschen würde.
CATEGORIES = [3030, 3050, 3060]
_paused: tuple[float, list[dict]] = (0.0, [])  # (Zeit der Abfrage, gesperrte Indexer)


def configured() -> bool:
    return bool(config.PROWLARR_URL and config.secret("PROWLARR_API_KEY") and config.USENET_INDEXER_IDS)


def sab_configured() -> bool:
    """Ist ein Usenet-Client eingerichtet (SABnzbd oder NZBGet)?"""
    from . import dlclients
    if dlclients.usenet_client() == "nzbget":
        return bool(config.SAB_URL and config.secret("NZBGET_USERNAME"))
    return bool(config.SAB_URL and config.secret("SABNZBD_API_KEY"))


def subtitle_series(subtitle: str, seq: str) -> str | None:
    """Serienname aus dem Untertitel, wenn dort die Bandnummer steht: «System-Apokalypse – Gnadenlos 1»,
    «Ein Dark Fantasy Abenteuer (Das Große Spiel, Buch 8)», «(Fair Quest Buch 1)». Freistehende Striche werden zu Leerzeichen,
    weil ein Suchwort mit «-» davor bei manchen Indexern als Ausschluss gilt; «System-Apokalypse» bleibt (manche Seiten finden nur so)."""
    if not seq.isdigit():
        return None
    sub = re.sub(r"\s*\[[^\]]*\]", "", subtitle)  # englische Fassung in eckigen Klammern
    for part in re.findall(r"\(([^()]*)\)", sub) + [sub]:
        m = re.match(rf"^\s*(.+?)[,\s]+(?:(?:Buch|Band|Teil|Folge|Book)\s*)?0*{int(seq)}\s*$", part, re.I)
        if m:
            name = re.sub(r"\s+", " ", re.sub(r"\s+[-–—]\s+|:", " ", m.group(1))).strip(" ,")
            if len(name) > 2 and not name.isdigit():
                return name
    return None


def queries(book: dict, audio: bool = True) -> list[str]:
    title = re.split(r"[:(\[]| - ", book["title"])[0].strip()
    author = (book.get("author") or "").split(",")[0].strip()
    series = re.sub(r"\s*[(\[][^)\]]*[)\]]|\s+#\S+.*$", "", book.get("series") or "").strip()
    seq = (book.get("sequence") or "").strip()
    out = [f"{author} {title}".strip(), title]
    marker = scoring.edition_marker(book.get("series")) if audio else None  # E-Books haben keinen Sprecher
    if marker:
        out.insert(0, f"{title} {marker}")  # bestimmte Fassung («Narrated by Stephen Fry», «BBC»)
    other = subtitle_series(book.get("subtitle") or "", seq)
    if other and seq.isdigit() and db.norm(other) != db.norm(series):
        out.append(f"{other} {int(seq):02d}")  # deutscher Serienname, wenn Audible die Serie englisch führt
    if series and seq.isdigit():
        out.append(f"{series} {int(seq):02d}")  # Usenet nummeriert zweistellig
        out.append(f"{series} {seq}")
    if series and author:
        out.append(f"{author} {series}")
    return list(dict.fromkeys(q for q in out if len(q) > 2))[:5]


def abb_queries(book: dict) -> list[str]:
    """Die drei Suchbegriffe für AudioBookBay. ABB verfälscht in jedem Titel ein Wort («A Shealers Gift»,
    «Adventuqres on Brad»), darum treffen Titelwörter oft nicht. Der Serienname steht zusätzlich im Beschreibungstext
    und findet die Bände zuverlässig; ohne Bandnummer, die ABB anders schreibt («Book 1», «1», «01»)."""
    q = queries(book)
    series = re.sub(r"\s*[(\[][^)\]]*[)\]]|\s+#\S+.*$", "", book.get("series") or "").strip()
    if len(series) > 2:
        q = [q[0], series] + [x for x in q[1:] if x != series]
    return list(dict.fromkeys(q))[:3]


def slow_queries(book: dict) -> list[str]:
    """Die zwei Suchbegriffe für die langsamen Seiten: der deutsche Serienname zuerst, wenn es einen gibt
    (Themen auf Foren-Seiten heissen nach der Serie, «Eine Faust voller Credits» allein findet nichts)."""
    q = queries(book)
    other = subtitle_series(book.get("subtitle") or "", (book.get("sequence") or "").strip())
    alt = next((x for x in q[2:4] if other and x.startswith(other)), None)
    return [alt, q[0]] if alt else q[:2]


def paused() -> list[dict]:
    """Usenet-Indexer, die Prowlarr gerade gesperrt hat (API-Limit oder Fehler), als [{indexer, until}], until in UTC.
    Prowlarr lässt sie bei der Suche stillschweigend weg und antwortet trotzdem mit 200: ohne diese Abfrage sähe
    «keine Treffer» wie ein echtes Ergebnis aus. Eine Minute zwischengespeichert; ist Prowlarr nicht erreichbar, gilt nichts als gesperrt."""
    global _paused
    if time.time() - _paused[0] < 60:
        return _paused[1]
    out: list[dict] = []
    try:
        with httpx.Client(timeout=20, headers={"X-Api-Key": config.secret("PROWLARR_API_KEY")}) as http:
            status = http.get(config.PROWLARR_URL + "/api/v1/indexerstatus").raise_for_status().json()
            now = datetime.now(timezone.utc)
            locked = {s["indexerId"]: s["disabledTill"] for s in status if s.get("indexerId") in config.USENET_INDEXER_IDS
                      and s.get("disabledTill") and datetime.fromisoformat(s["disabledTill"].replace("Z", "+00:00")) > now}
            if locked:
                names = {i["id"]: i["name"] for i in http.get(config.PROWLARR_URL + "/api/v1/indexer").raise_for_status().json()}
                out = [{"indexer": names.get(i, f"Indexer {i}"), "until": t} for i, t in sorted(locked.items())]
    except Exception:
        out = []
    _paused = (time.time(), out)
    return out


def search(book: dict, enough=None) -> tuple[list[dict], list[str]]:
    """Alle Usenet-Treffer zu einem Buch. Liefert (Treffer, Fehlermeldungen).

    enough(treffer) -> bool: nach jedem Suchbegriff gefragt; True beendet die Suche vorzeitig. Spart Abfragen,
    weil jeder Suchbegriff jeden Indexer einmal fragt und deren API-Kontingent mit Sonarr und Radarr geteilt ist."""
    found: dict[str, dict] = {}
    errors: list[str] = []
    key = config.secret("PROWLARR_API_KEY")
    with httpx.Client(timeout=60, headers={"X-Api-Key": key}) as http:
        for q in queries(book):
            if enough and found and enough(list(found.values())):
                break
            try:
                r = http.get(config.PROWLARR_URL + "/api/v1/search",
                             params={"query": q, "indexerIds": config.USENET_INDEXER_IDS, "categories": CATEGORIES})
                r.raise_for_status()
                rows = r.json()
            except Exception as exc:
                errors.append(f"Suche «{q}»: {type(exc).__name__}")
                continue
            for raw in rows:
                if raw.get("protocol") != "usenet" or raw.get("indexerId") not in config.USENET_INDEXER_IDS or not raw.get("guid"):
                    continue
                _releases[raw["guid"]] = (time.time(), raw)
                found.setdefault(raw["guid"], {
                    "guid": raw["guid"], "source": "usenet", "title": raw["title"], "indexer": raw.get("indexer"),
                    "size": raw.get("size"), "age_days": raw.get("age"), "grabs": raw.get("grabs"), "info_url": raw.get("infoUrl"),
                })
    for g in [g for g, (t, _) in _releases.items() if time.time() - t > 3600]:
        _releases.pop(g, None)
    return list(found.values()), errors


def remember(raw: dict) -> None:
    """Roh-Treffer aus Prowlarr merken, damit fetch_nzb die Download-Adresse findet."""
    _releases[raw["guid"]] = (time.time(), raw)


def known(guid: str) -> bool:
    return guid in _releases


def fetch_nzb(guid: str) -> bytes:
    hit = _releases.get(guid)
    if not hit:
        raise LookupError("Der Treffer ist nicht mehr im Zwischenspeicher. Bitte neu suchen.")
    u = urlsplit(hit[1]["downloadUrl"])
    url = config.PROWLARR_URL + u.path + ("?" + u.query if u.query else "")
    with httpx.Client(timeout=60, follow_redirects=False) as http:
        headers = {"X-Api-Key": config.secret("PROWLARR_API_KEY"), "User-Agent": UA}
        for _ in range(4):
            r = http.get(url, headers=headers)
            if r.status_code in (301, 302, 303, 307, 308):
                url = str(r.next_request.url) if r.next_request else r.headers["location"]
                if not url.startswith(config.PROWLARR_URL):
                    if not url.startswith("https://"):
                        raise ValueError("Unsichere Weiterleitung beim NZB-Abruf")
                    headers = {"User-Agent": UA}  # Schlüssel nie an fremde Server
                continue
            r.raise_for_status()
            data = r.content
            break
        else:
            raise ValueError("Zu viele Weiterleitungen beim NZB-Abruf")
    if len(data) > 16 * 1024 * 1024 or b"<!ENTITY" in data.upper():
        raise ValueError("NZB-Datei ungültig")
    root = ET.fromstring(re.sub(rb"<!DOCTYPE[^>]*>", b"", data, count=1))
    if root.tag.rsplit("}", 1)[-1] != "nzb" or not any(n.tag.rsplit("}", 1)[-1] == "file" for n in root):
        raise ValueError("NZB-Datei ungültig")
    return data


def _sab(mode: str, files=None, **params) -> dict:
    data = {"apikey": config.secret("SABNZBD_API_KEY"), "output": "json", "mode": mode, **params}
    r = httpx.post(config.SAB_URL + "/api", data=data, files=files, timeout=60)
    r.raise_for_status()
    d = r.json()
    if d.get("status") is False or d.get("error"):
        raise RuntimeError(f"SABnzbd lehnt ab: {d.get('error') or 'unbekannter Grund'}")
    return d


def submit(nzb: bytes, marker: str) -> str:
    from . import dlclients
    if dlclients.usenet_client() == "nzbget":
        return dlclients.NZBGet().submit(nzb, marker)
    d = _sab("addfile", files={"name": ("download.nzb", nzb, "application/x-nzb")},
             nzbname=marker, cat=config.SAB_CATEGORY, priority=0)
    ids = d.get("nzo_ids") or []
    if len(ids) != 1:
        raise RuntimeError("SABnzbd hat den Auftrag nicht bestätigt")
    return ids[0]


def status(ids: set[str]) -> dict[str, dict]:
    """Stand der eigenen Aufträge in SABnzbd (oder NZBGet): state queued|downloading|arrived|failed, progress, path, error."""
    from . import dlclients
    if dlclients.usenet_client() == "nzbget":
        return dlclients.NZBGet().status(ids)
    out: dict[str, dict] = {}
    for slot in _sab("queue", limit=500).get("queue", {}).get("slots", []):
        if slot.get("nzo_id") in ids:
            pct = float(slot.get("percentage") or 0)
            out[slot["nzo_id"]] = {"state": "downloading" if pct > 0 or slot.get("status") == "Downloading" else "queued",
                                   "progress": pct / 100}
    for slot in _sab("history", limit=500).get("history", {}).get("slots", []):
        if slot.get("nzo_id") in ids:
            st = slot.get("status")
            if st == "Completed":
                out[slot["nzo_id"]] = {"state": "arrived", "progress": 1.0, "path": slot.get("storage")}
            elif st == "Failed":
                out[slot["nzo_id"]] = {"state": "failed", "error": (slot.get("fail_message") or "Download fehlgeschlagen")[:300]}
            else:
                out[slot["nzo_id"]] = {"state": "downloading", "progress": 0.99}  # Entpacken, Reparieren
    return out
