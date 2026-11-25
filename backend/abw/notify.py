"""Meldungen über ntfy und einen Webhook (Discord, Slack, Teams oder ein eigener Empfänger mit JSON).
Ohne Adresse oder bei ausgeschaltetem Schalter passiert nichts."""
import logging

import httpx

from . import config, db

log = logging.getLogger("abw.notify")


def configured() -> bool:
    return bool(config.NTFY_URL and config.NTFY_TOPIC)


def webhook_configured() -> bool:
    return bool(config.secret("WEBHOOK_URL"))


def any_configured() -> bool:
    return configured() or webhook_configured()


def send(title: str, message: str, path: str = "", tags: str = "books", force: bool = False) -> bool:
    """An alle eingerichteten Wege. True, wenn mindestens einer die Meldung angenommen hat."""
    if not force and db.setting("notify") != "1":
        return False
    ok = False
    if configured():
        ok = _ntfy(title, message, path, tags) or ok
    if webhook_configured():
        ok = webhook(title, message, path, tags) or ok
    return ok


def _ntfy(title: str, message: str, path: str, tags: str) -> bool:
    headers = {}
    if config.secret("NTFY_TOKEN"):
        headers["Authorization"] = "Bearer " + config.secret("NTFY_TOKEN")
    try:
        # Titel als JSON, damit Umlaute sicher ankommen
        r = httpx.post(config.NTFY_URL, json={"topic": config.NTFY_TOPIC, "title": title, "message": message, "tags": tags.split(","),
                                              **({"click": config.PUBLIC_URL + path} if config.PUBLIC_URL else {})},
                       headers=headers, timeout=15)
        return r.status_code == 200
    except Exception:
        return False


def webhook_kind(url: str) -> str:
    u = url.lower()
    if "discord.com/api/webhooks" in u or "discordapp.com/api/webhooks" in u:
        return "discord"
    if "hooks.slack.com" in u:
        return "slack"
    if "webhook.office.com" in u or "logic.azure.com" in u or "powerplatform.com" in u:
        return "teams"
    return "json"


def webhook_payload(kind: str, title: str, message: str, link: str, tags: str) -> dict:
    if kind == "discord":
        embed = {"title": title[:256], "description": message[:4000], "color": 0x8B74FF}
        if link:
            embed["url"] = link
        return {"username": "AudioBook Wizarrd", "embeds": [embed]}
    if kind in ("slack", "teams"):
        text = f"*{title}*\n{message}" if kind == "slack" else f"**{title}**\n\n{message}"
        return {"text": text + (f"\n{link}" if link else "")}
    return {"app": "audiobook-wizarrd", "title": title, "message": message, "url": link or None, "tags": [x for x in tags.split(",") if x]}


def webhook(title: str, message: str, path: str = "", tags: str = "books") -> bool:
    url = config.secret("WEBHOOK_URL")
    if not url:
        return False
    link = config.PUBLIC_URL + path if config.PUBLIC_URL else ""
    try:
        r = httpx.post(url, json=webhook_payload(webhook_kind(url), title, message, link, tags), timeout=15)
        if r.status_code >= 300:
            log.warning("Webhook abgelehnt: HTTP %s", r.status_code)
        return r.status_code < 300
    except Exception as exc:
        log.warning("Webhook nicht gesendet: %s", exc)
        return False
