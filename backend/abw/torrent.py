"""Quelle AudioBookBay: Suche über Prowlarr (Torrent-Indexer), Download über qBittorrent in einen eigenen Ordner."""
import base64
import hashlib
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx

from . import config

_releases: dict[str, tuple[float, dict]] = {}
META_TIMEOUT_MIN = 20     # ohne Metadaten gilt ein Torrent als tot
STALL_TIMEOUT_H = 12      # so lange ohne Fortschritt, dann aufgeben


def configured() -> bool:
    return bool(config.PROWLARR_URL and config.secret("PROWLARR_API_KEY") and config.TORRENT_INDEXER_IDS
                and config.QBIT_URL and config.secret("QBIT_USERNAME") and config.QBIT_SAVE_ROOT)


def search(queries: list[str]) -> tuple[list[dict], list[str]]:
    found: dict[str, dict] = {}
    errors: list[str] = []
    with httpx.Client(timeout=90, headers={"X-Api-Key": config.secret("PROWLARR_API_KEY")}) as http:
        for q in queries[:3]:  # die Seite ist langsam; drei Anfragen genügen
            try:
                r = http.get(config.PROWLARR_URL + "/api/v1/search",
                             params={"query": q, "indexerIds": config.TORRENT_INDEXER_IDS, "categories": [3030]})
                r.raise_for_status()
                rows = r.json()
            except Exception as exc:
                errors.append(f"AudioBookBay «{q}»: {type(exc).__name__}")
                continue
            for raw in rows:
                if raw.get("protocol") != "torrent" or not raw.get("guid"):
                    continue
                _releases[raw["guid"]] = (time.time(), raw)
                found.setdefault(raw["guid"], {
                    "guid": raw["guid"], "source": "audiobookbay", "title": raw["title"], "indexer": raw.get("indexer"),
                    "size": raw.get("size"), "age_days": raw.get("age"), "grabs": raw.get("grabs"),
                    "seeders": raw.get("seeders"), "info_url": raw.get("infoUrl"),
                })
    for g in [g for g, (t, _) in _releases.items() if time.time() - t > 3600]:
        _releases.pop(g, None)
    return list(found.values()), errors


def remember(raw: dict) -> None:
    """Roh-Treffer aus Prowlarr merken, damit fetch die Download-Adresse findet."""
    _releases[raw["guid"]] = (time.time(), raw)


def known(guid: str) -> bool:
    return guid in _releases


def fetch(guid: str) -> dict:
    """Magnet-Link oder Torrent-Datei zum Treffer holen: {'magnet': ...} oder {'file': bytes}."""
    hit = _releases.get(guid)
    if not hit:
        raise LookupError("Der Treffer ist nicht mehr im Zwischenspeicher. Bitte neu suchen.")
    raw = hit[1]
    if (raw.get("magnetUrl") or "").startswith("magnet:"):
        return {"magnet": raw["magnetUrl"]}
    u = urlsplit(raw["downloadUrl"])
    url = config.PROWLARR_URL + u.path + ("?" + u.query if u.query else "")
    r = httpx.get(url, headers={"X-Api-Key": config.secret("PROWLARR_API_KEY")}, timeout=90, follow_redirects=False)
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("location", "")
        if loc.startswith("magnet:"):
            return {"magnet": loc}
        raise ValueError("Unerwartete Weiterleitung beim Abruf des Torrents")
    r.raise_for_status()
    if not r.content.startswith(b"d"):
        raise ValueError("Die Antwort ist keine Torrent-Datei")
    return {"file": r.content}


def _bspan(data: bytes, i: int) -> int:
    """Ende des bencodierten Werts ab Position i."""
    c = data[i:i + 1]
    if c == b"i":
        return data.index(b"e", i) + 1
    if c in (b"l", b"d"):
        i += 1
        while data[i:i + 1] != b"e":
            i = _bspan(data, i)
        return i + 1
    colon = data.index(b":", i)
    return colon + 1 + int(data[i:colon])


def info_hash(item: dict) -> str | None:
    """Infohash (v1, hex klein) aus Magnet-Link oder Torrent-Datei; None, wenn er sich nicht bestimmen lässt."""
    try:
        if "magnet" in item:
            for xt in parse_qs(urlsplit(item["magnet"]).query).get("xt", []):
                h = xt.rsplit(":", 1)[-1] if xt.lower().startswith("urn:btih:") else ""
                if re.fullmatch(r"[0-9a-fA-F]{40}", h):
                    return h.lower()
                if re.fullmatch(r"[A-Za-z2-7]{32}", h):
                    return base64.b32decode(h.upper()).hex()
            return None
        data, i = item["file"], 1
        while data[i:i + 1] != b"e":  # Schlüssel und Werte des äusseren Wörterbuchs
            key_end = _bspan(data, i)
            key = data[data.index(b":", i) + 1:key_end]
            val_end = _bspan(data, key_end)
            if key == b"info":
                return hashlib.sha1(data[key_end:val_end]).hexdigest()
            i = val_end
    except (ValueError, IndexError):
        pass
    return None


def busy(markers: list[str]) -> bool:
    """Gehört einer dieser Ordner zu einem Download, der noch läuft oder dessen Dateien noch gebraucht werden?"""
    from . import db
    if not markers:
        return False
    q = ",".join("?" * len(markers))
    c = db.conn()
    return bool(c.execute(f"SELECT 1 FROM download WHERE marker IN ({q}) AND state IN ('queued','downloading','arrived','verified','review','split')", markers).fetchone()
                or c.execute(f"SELECT 1 FROM ebook_job WHERE marker IN ({q}) AND state IN ('queued','downloading','arrived')", markers).fetchone())


class Qbit:
    def __init__(self):
        self.http = httpx.Client(base_url=config.QBIT_URL, timeout=60, headers={"Referer": config.QBIT_URL})
        r = self.http.post("/api/v2/auth/login", data={"username": config.secret("QBIT_USERNAME"), "password": config.secret("QBIT_PASSWORD")})
        if r.status_code not in (200, 204) or "fail" in r.text.lower() or not self.http.cookies:  # neuere Versionen antworten leer mit 204
            raise RuntimeError("Anmeldung bei qBittorrent fehlgeschlagen")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.http.close()

    def add(self, item: dict, marker: str) -> None:
        data = {"savepath": f"{config.QBIT_SAVE_ROOT}/{marker}", "category": config.QBIT_CATEGORY, "tags": marker,
                "autoTMM": "false", "useDownloadPath": "false"}
        if "magnet" in item:
            r = self.http.post("/api/v2/torrents/add", data={**data, "urls": item["magnet"]})
        else:
            r = self.http.post("/api/v2/torrents/add", data=data, files={"torrents": ("abw.torrent", item["file"], "application/x-bittorrent")})
        failed = "fails" in r.text.lower()
        try:  # neuere Versionen antworten mit {"added_torrent_ids": [...], "failure_count": n}
            failed = int(r.json().get("failure_count", 0)) > 0
        except Exception:
            pass
        if r.status_code == 409:  # derselbe Torrent liegt schon in qBittorrent
            raise ValueError("Diesen Torrent hat qBittorrent schon, er wurde also bereits einmal geladen. Bitte einen anderen Treffer wählen.")
        if r.status_code not in (200, 202, 204) or failed:
            raise RuntimeError(f"qBittorrent hat den Torrent nicht angenommen ({r.text[:80]})")

    def by_tag(self, marker: str) -> dict | None:
        rows = self.http.get("/api/v2/torrents/info", params={"tag": marker}).raise_for_status().json()
        return rows[0] if rows else None

    def by_hash(self, h: str) -> dict | None:
        rows = self.http.get("/api/v2/torrents/info", params={"hashes": h}).raise_for_status().json()
        return rows[0] if rows else None

    def take_over(self, t: dict, marker: str) -> None:
        """Vorhandenen Torrent für einen neuen Download übernehmen: Dateien wandern in den neuen Ordner, das Etikett wechselt."""
        old = [x.strip() for x in (t.get("tags") or "").split(",") if x.strip()]
        self.http.post("/api/v2/torrents/setLocation", data={"hashes": t["hash"], "location": f"{config.QBIT_SAVE_ROOT}/{marker}"}).raise_for_status()
        if old:
            self.http.post("/api/v2/torrents/removeTags", data={"hashes": t["hash"], "tags": ",".join(old)})
        self.http.post("/api/v2/torrents/addTags", data={"hashes": t["hash"], "tags": marker}).raise_for_status()
        if t.get("progress", 0) < 1:
            self.http.post("/api/v2/torrents/start", data={"hashes": t["hash"]})

    def stop(self, h: str) -> None:
        self.http.post("/api/v2/torrents/stop", data={"hashes": h}).raise_for_status()

    def remove(self, h: str, delete_files: bool, marker: str = "") -> None:
        self.http.post("/api/v2/torrents/delete", data={"hashes": h, "deleteFiles": "true" if delete_files else "false"}).raise_for_status()
        if marker:
            self.http.post("/api/v2/torrents/deleteTags", data={"tags": marker})


def submit(guid: str, marker: str, force: bool = False) -> str:
    """force: sofort starten, auch wenn die Warteschlange von qBittorrent voll ist (für kleine E-Books)."""
    item = fetch(guid)
    h = info_hash(item)
    with Qbit() as q:
        t = q.by_hash(h) if h else None
        if t:  # schon einmal geladen (z. B. für einen verworfenen Download): übernehmen statt erneut hinzufügen
            old = [x.strip() for x in (t.get("tags") or "").split(",") if x.strip()]
            if busy(old):
                raise ValueError("Dieser Torrent gehört zu einem Download, der noch läuft oder geprüft wird")
            q.take_over(t, marker)
            return t["hash"]
        q.add(item, marker)
        for _ in range(10):
            t = q.by_tag(marker)
            if t:
                if force:
                    q.http.post("/api/v2/torrents/setForceStart", data={"hashes": t["hash"], "value": "true"})
                return t["hash"]
            time.sleep(1)
    raise RuntimeError("qBittorrent hat den Torrent nicht bestätigt")


def status(rows: list[dict]) -> dict[int, dict]:
    """Stand je Download-Zeile. Fertige Torrents werden sofort gestoppt (Entscheid: nicht weiter verteilen)."""
    out: dict[int, dict] = {}
    now = time.time()
    with Qbit() as q:
        for r in rows:
            t = q.by_tag(r["marker"])
            if not t:
                out[r["id"]] = {"state": "failed", "error": "Der Torrent ist in qBittorrent nicht mehr vorhanden"}
                continue
            age_min = (now - t.get("added_on", now)) / 60
            idle_h = (now - max(t.get("last_activity") or 0, t.get("added_on") or 0)) / 3600
            if t.get("state") == "moving":  # übernommener Torrent: Dateien wandern noch in den neuen Ordner
                out[r["id"]] = {"state": "downloading", "progress": t.get("progress", 0)}
            elif t.get("progress", 0) >= 1:
                q.stop(t["hash"])
                out[r["id"]] = {"state": "arrived", "progress": 1.0}
            elif t.get("state") in ("metaDL", "forcedMetaDL") and age_min > META_TIMEOUT_MIN:
                q.remove(t["hash"], True, r["marker"])
                out[r["id"]] = {"state": "failed", "error": f"Tot: nach {META_TIMEOUT_MIN} Minuten keine Quelle gefunden"}
            elif t.get("state") in ("error", "missingFiles"):
                q.remove(t["hash"], True, r["marker"])
                out[r["id"]] = {"state": "failed", "error": "qBittorrent meldet einen Fehler"}
            elif idle_h > STALL_TIMEOUT_H:
                q.remove(t["hash"], True, r["marker"])
                pct = f"{int(t.get('progress', 0) * 1000) / 10:g}"  # abgerundet: 99,6 % statt «100 %»
                out[r["id"]] = {"state": "failed", "error": f"Abgebrochen: {STALL_TIMEOUT_H} Stunden ohne Fortschritt bei {pct} %"}
            else:
                out[r["id"]] = {"state": "downloading" if t.get("progress", 0) > 0 else "queued", "progress": t.get("progress", 0)}
    return out


def forget(marker: str) -> None:
    """Torrent aus qBittorrent austragen, Dateien bleiben liegen (das Aufräumen verschiebt sie)."""
    with Qbit() as q:
        t = q.by_tag(marker)
        if t:
            q.remove(t["hash"], False, marker)
