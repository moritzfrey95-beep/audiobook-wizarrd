"""Verbindungstests für Einstellungen und Setup-Wizard. Jeder Test bekommt die Werte direkt, damit man vor dem Speichern prüfen
kann. Ergebnis immer {"ok": bool, "text": str, ...}; Fehler werden als Text gemeldet, nicht geworfen."""
import os
from pathlib import Path

import httpx

TIMEOUT = 10


def _fail(exc: Exception) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return {"ok": False, "text": "Zugang abgelehnt, Schlüssel prüfen" if code in (401, 403) else f"Antwort {code}"}
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return {"ok": False, "text": "Keine Verbindung, Adresse prüfen"}
    return {"ok": False, "text": str(exc)[:200] or exc.__class__.__name__}


def audiobookshelf(url: str, key: str) -> dict:
    """Bibliotheken auflisten; Hörbuch-Bibliotheken mit Ordnern."""
    try:
        r = httpx.get(url.rstrip("/") + "/api/libraries", headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT)
        r.raise_for_status()
        libs = [{"id": x["id"], "name": x["name"], "folders": [f.get("fullPath") for f in x.get("folders", [])]}
                for x in r.json().get("libraries", []) if x.get("mediaType") == "book"]
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": f"{len(libs)} Hörbuch-Bibliotheken gefunden", "libraries": libs}


def prowlarr(url: str, key: str) -> dict:
    """Indexer mit Protokoll (usenet oder torrent)."""
    try:
        r = httpx.get(url.rstrip("/") + "/api/v1/indexer", headers={"X-Api-Key": key}, timeout=TIMEOUT)
        r.raise_for_status()
        idx = [{"id": x["id"], "name": x["name"], "protocol": x.get("protocol"), "enabled": bool(x.get("enable"))} for x in r.json()]
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": f"{len(idx)} Indexer gefunden", "indexers": sorted(idx, key=lambda x: (x["protocol"] or "", x["name"].lower()))}


def sabnzbd(url: str, key: str) -> dict:
    """SABnzbd oder ein Programm mit derselben Schnittstelle (z. B. NZBGet-Proxy, Quasarr)."""
    try:
        r = httpx.get(url.rstrip("/") + "/api", params={"mode": "queue", "output": "json", "apikey": key, "limit": 1}, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
        if data.get("error") or data.get("status") is False:
            return {"ok": False, "text": str(data.get("error") or "Schlüssel abgelehnt")[:200]}
        v = httpx.get(url.rstrip("/") + "/api", params={"mode": "version", "output": "json"}, timeout=TIMEOUT).json().get("version", "")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": f"Verbunden{', Version ' + v if v else ''}"}


def usenet(client: str, url: str, key: str, user: str, password: str) -> dict:
    if (client or "sabnzbd").lower() != "nzbget":
        return sabnzbd(url, key)
    from . import dlclients
    try:
        v = dlclients.NZBGet(url, user, password).version()
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": f"NZBGet verbunden, Version {v}"}


def torrent(client: str, url: str, user: str, password: str, debrid_key: str) -> dict:
    """Gewählten Torrent-Client prüfen: qBittorrent, Transmission, Deluge, Real-Debrid oder TorBox."""
    from . import dlclients
    kind = (client or "qbittorrent").lower()
    if kind not in dlclients.TORRENT_CLIENTS:
        return {"ok": False, "text": "Unbekannter Torrent-Client"}
    if kind == "qbittorrent":
        return qbittorrent(url, user, password)
    try:
        if kind == "transmission":
            v = dlclients.Transmission(url, user, password).version()
        elif kind == "deluge":
            v = dlclients.Deluge(url, password).version()
        elif kind == "realdebrid":
            v = dlclients.RealDebrid(debrid_key).version()
        else:
            v = dlclients.TorBox(debrid_key).version()
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": f"Verbunden: {v}"}


def qbittorrent(url: str, username: str, password: str) -> dict:
    try:
        with httpx.Client(base_url=url.rstrip("/"), timeout=TIMEOUT, headers={"Referer": url}) as c:
            r = c.post("/api/v2/auth/login", data={"username": username, "password": password})
            # qBittorrent bis 5.1 antwortet «Ok.», neuere Versionen leer mit 204; falsche Anmeldung: «Fails.» oder 401
            if r.status_code == 401 or (r.status_code != 204 and r.text.strip() != "Ok.") or not c.cookies:
                return {"ok": False, "text": "Anmeldung abgelehnt, Benutzer und Passwort prüfen"}
            r.raise_for_status()
            v = c.get("/api/v2/app/version").text.strip()
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": f"Verbunden, Version {v}"}


def ntfy(url: str) -> dict:
    try:
        r = httpx.get(url.rstrip("/") + "/v1/health", timeout=TIMEOUT)
        r.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": "ntfy erreichbar"}


def notify(ntfy_url: str, webhook_url: str) -> dict:
    """ntfy auf Erreichbarkeit prüfen; an den Webhook geht eine Testmeldung (anders lässt er sich nicht prüfen)."""
    from . import notify as notify_mod
    if not ntfy_url and not webhook_url:
        return {"ok": False, "text": "Weder ntfy-Adresse noch Webhook eingetragen"}
    parts, ok = [], True
    if ntfy_url:
        r = ntfy(ntfy_url)
        ok = r["ok"]
        parts.append(r["text"])
    if webhook_url:
        kind = notify_mod.webhook_kind(webhook_url)
        try:
            r = httpx.post(webhook_url, json=notify_mod.webhook_payload(kind, "AudioBook Wizarrd", "Testmeldung: Der Webhook funktioniert.", "", "mage"),
                           timeout=TIMEOUT)
            r.raise_for_status()
            parts.append(f"Webhook ({kind}) hat die Testmeldung angenommen")
        except Exception as exc:  # noqa: BLE001
            ok = False
            parts.append("Webhook: " + _fail(exc)["text"])
    return {"ok": ok, "text": ". ".join(parts)}


def flaresolverr(url: str) -> dict:
    try:
        r = httpx.post(url, json={"cmd": "sessions.list"}, timeout=TIMEOUT)
        r.raise_for_status()
        if r.json().get("status") != "ok":
            return {"ok": False, "text": "Unerwartete Antwort"}
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return {"ok": True, "text": "FlareSolverr erreichbar"}


def folder(path: str, write: bool = False) -> dict:
    """Ordner aus Sicht von ABW (im Container): vorhanden, lesbar, bei Bedarf beschreibbar."""
    p = Path(path or "")
    if not path or not p.is_dir():
        return {"ok": False, "text": "Ordner nicht gefunden. In Docker muss er eingebunden sein (volumes)."}
    if not os.access(p, os.R_OK):
        return {"ok": False, "text": "Ordner nicht lesbar"}
    if write and not os.access(p, os.W_OK):
        return {"ok": False, "text": "ABW darf hier nicht schreiben"}
    return {"ok": True, "text": "Ordner beschreibbar" if write else "Ordner lesbar"}
