"""Sprache der Meldungen an die Oberfläche. Der Code schreibt Deutsch; locales/en.json enthält die englischen Fassungen.
Meldungen mit eingesetzten Werten stehen dort als Vorlage mit {0}, {1} …, z. B. "Band {0} fehlt" -> "Volume {0} is missing".
Die Oberfläche schickt ihre Sprache im Header X-ABW-Lang; ohne Header bleibt es Deutsch."""
import json
import re
from contextvars import ContextVar
from pathlib import Path

lang: ContextVar[str] = ContextVar("abw_lang", default="de")
_FILE = Path(__file__).with_name("locales") / "en.json"
_exact: dict[str, str] = {}
_patterns: list[tuple[re.Pattern, str]] = []


def _load() -> None:
    if _exact or not _FILE.is_file():
        return
    data = json.loads(_FILE.read_text(encoding="utf-8"))
    for de, en in data.items():
        if not en:
            continue
        if re.search(r"\{\d+\}", de):
            parts = re.split(r"(\{\d+\})", de)
            rx = "".join(f"(?P<p{p[1:-1]}>.+?)" if re.fullmatch(r"\{\d+\}", p) else re.escape(p) for p in parts)
            _patterns.append((re.compile("^" + rx + "$", re.S), en))
        else:
            _exact[de] = en
    _patterns.sort(key=lambda x: -len(x[0].pattern))  # genauere Vorlagen zuerst


def tr(text: str, to: str | None = None) -> str:
    """Deutsche Meldung in die gewünschte Sprache (Standard: Sprache der laufenden Anfrage)."""
    if (to or lang.get()) != "en" or not isinstance(text, str):
        return text
    _load()
    if text in _exact:
        return _exact[text]
    for rx, en in _patterns:
        m = rx.match(text)
        if m:
            return re.sub(r"\{(\d+)\}", lambda g: m.group("p" + g.group(1)), en)
    return text


def pick(header: str | None) -> str:
    return "en" if (header or "").lower().startswith("en") else "de"
