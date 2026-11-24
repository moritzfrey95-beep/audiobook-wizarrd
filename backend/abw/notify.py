"""Meldungen über ntfy. Ohne Adresse oder bei ausgeschaltetem Schalter passiert nichts."""
import httpx

from . import config, db


def configured() -> bool:
    return bool(config.NTFY_URL and config.NTFY_TOPIC)


def send(title: str, message: str, path: str = "", tags: str = "books", force: bool = False) -> bool:
    if not configured() or (not force and db.setting("notify") != "1"):
        return False
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore") or "AudioBook Wizarrd", "Tags": tags}
    if config.secret("NTFY_TOKEN"):
        headers["Authorization"] = "Bearer " + config.secret("NTFY_TOKEN")
    if config.PUBLIC_URL:
        headers["Click"] = config.PUBLIC_URL + path
    try:
        # Titel als JSON, damit Umlaute sicher ankommen
        r = httpx.post(config.NTFY_URL, json={"topic": config.NTFY_TOPIC, "title": title, "message": message, "tags": tags.split(","),
                                              **({"click": config.PUBLIC_URL + path} if config.PUBLIC_URL else {})},
                       headers={k: v for k, v in headers.items() if k == "Authorization"}, timeout=15)
        return r.status_code == 200
    except Exception:
        return False
