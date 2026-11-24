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
            if len(re.findall(r"[A-Za-zÄÖÜäöüß]", re.sub(r"\{\d+\}", "", de))) < 2:
                continue  # zu allgemein («{0}: {1}»), würde auch Buchtitel treffen
            parts = re.split(r"(\{\d+\})", de)
            rx = "".join(f"(?P<p{p[1:-1]}>.*?)" if re.fullmatch(r"\{\d+\}", p) else re.escape(p) for p in parts)
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
            # eingesetzte Teile können selbst Meldungen sein («{0}. Abweichung …» mit «9 h, Audible nennt …»)
            return re.sub(r"\{(\d+)\}", lambda g: tr(m.group("p" + g.group(1)), "en"), en)
    # Aus Sätzen zusammengesetzte Meldung («Bestand: … Neu: … Audible nennt …»): Satz für Satz
    parts = re.split(r"(\.\s+|;\s+)", text)
    if len(parts) > 1:
        out = []
        for i, part in enumerate(parts):
            if i % 2 or not part.strip():
                out.append(part)  # Trennzeichen
                continue
            core = part[:-1] if part.endswith(".") and len(part) > 1 else part
            done = tr(core, "en")
            out.append(done + part[len(core):] if done != core else part)
        return "".join(out)
    return text


def pick(header: str | None) -> str:
    return "en" if (header or "").lower().startswith("en") else "de"


# Felder in API-Antworten, die Meldungen enthalten (Aufgaben, Prüfgründe, Trefferbewertung, Quellen, Hörverlauf).
# Titel, Autoren und Serien stehen in anderen Feldern und bleiben unberührt.
FIELDS = {"name", "description", "message", "text", "error", "why", "hint", "progress", "reason", "detail", "note",
          "weekday", "labels", "label", "plan_text", "status_text"}


def tr_tree(obj, to: str | None = None):
    """Übersetzt in verschachtelten Antworten die Werte der Felder aus FIELDS."""
    if (to or lang.get()) != "en":
        return obj
    if isinstance(obj, list):
        return [tr_tree(x, "en") for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in FIELDS and isinstance(v, str):
                out[k] = tr(v, "en")
            elif k in FIELDS and isinstance(v, list) and all(isinstance(x, str) for x in v):
                out[k] = [tr(x, "en") for x in v]
            else:
                out[k] = tr_tree(v, "en")
        return out
    return obj
