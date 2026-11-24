"""Seiten über FlareSolverr lesen (löst die Cloudflare-Prüfung). Optional: ohne FLARESOLVERR_URL ist es aus."""
import threading
from urllib.parse import urlsplit

import httpx

from . import config

_lock = threading.Lock()  # FlareSolverr arbeitet eine Seite nach der anderen ab


def configured() -> bool:
    return bool(config.FLARESOLVERR_URL)


def fetch(url: str, session: str | None = None, timeout: int = 60) -> str:
    """Seite über FlareSolverr holen (löst die Cloudflare-Prüfung)."""
    body = {"cmd": "request.get", "url": url, "maxTimeout": timeout * 1000}
    if session:
        body["session"] = session
    with _lock:
        r = httpx.post(config.FLARESOLVERR_URL, json=body, timeout=timeout + 15)
    r.raise_for_status()
    data = r.json()
    sol = data.get("solution") or {}
    if data.get("status") != "ok" or sol.get("status") != 200 or not isinstance(sol.get("response"), str):
        raise ConnectionError(f"Seite nicht lesbar ({sol.get('status') or data.get('message', '')[:60]})")
    if urlsplit(sol.get("url") or url).hostname != urlsplit(url).hostname:
        raise PermissionError("Weiterleitung auf eine andere Seite (Anmeldung abgelaufen?)")
    return sol["response"]
