"""Einstellungen. Grundwerte kommen aus Umgebungsvariablen (.env), in der Oberfläche gespeicherte Werte aus der Datenbank
(Tabelle setting, Schlüssel «cfg:<NAME>») und gehen vor. reload() setzt die Modulwerte neu; aufgerufen beim Start und nach
jedem Speichern. Keine Zugangsdaten im Code.
"""
import os
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(os.environ.get("ABW_DATA_DIR", "./data"))
DB_PATH = DATA_DIR / "abw.db"
STATIC_DIR = Path(os.environ.get("ABW_STATIC_DIR", "../frontend/dist"))

MARKETPLACE = {"en": "https://api.audible.com", "de": "https://api.audible.de"}
AUDIBLE_LANG = {"en": "english", "de": "german"}


@dataclass(frozen=True)
class Field:
    env: str                # Umgebungsvariable mit dem Grundwert
    kind: str = "str"       # str, url, ids, path, bool, float, secret, scheme
    default: str = ""
    group: str = ""         # Abschnitt in den Einstellungen


FIELDS: dict[str, Field] = {
    "ABS_URL": Field("ABS_URL", "url", "http://localhost:13378", "abs"),
    "ABS_PUBLIC_URL": Field("ABS_PUBLIC_URL", "url", "", "abs"),
    "ABS_API_KEY": Field("ABS_API_KEY", "secret", "", "abs"),
    "ABS_LIBRARY_EN": Field("ABS_LIBRARY_EN", "str", "", "abs"),
    "ABS_LIBRARY_DE": Field("ABS_LIBRARY_DE", "str", "", "abs"),
    "LIBRARY_EN_ROOT": Field("LIBRARY_EN_ROOT", "path", "/library-en", "abs"),
    "LIBRARY_DE_ROOT": Field("LIBRARY_DE_ROOT", "path", "/library-de", "abs"),
    "LIBRARY_TRASH": Field("LIBRARY_TRASH", "path", "/library-trash", "abs"),
    "IMPORT_ENABLED": Field("ABW_IMPORT_ENABLED", "bool", "0", "abs"),
    # Ordnerstruktur beim Ablegen (siehe folders.py)
    "FOLDER_SERIES": Field("ABW_FOLDER_SERIES", "scheme", "{series}/<Book {seq} - >{title}", "folders"),
    "FOLDER_SINGLE": Field("ABW_FOLDER_SINGLE", "scheme", "Standalone - {author}/{title}", "folders"),
    "PROWLARR_URL": Field("PROWLARR_URL", "url", "", "prowlarr"),
    "PROWLARR_API_KEY": Field("PROWLARR_API_KEY", "secret", "", "prowlarr"),
    "USENET_INDEXER_IDS": Field("USENET_INDEXER_IDS", "ids", "", "prowlarr"),
    "TORRENT_INDEXER_IDS": Field("TORRENT_INDEXER_IDS", "ids", "", "prowlarr"),
    "EBOOK_TORRENT_INDEXER_IDS": Field("EBOOK_TORRENT_INDEXER_IDS", "ids", "", "prowlarr"),
    "USENET_CLIENT": Field("ABW_USENET_CLIENT", "str", "sabnzbd", "sabnzbd"),  # sabnzbd oder nzbget
    "SAB_URL": Field("SABNZBD_URL", "url", "", "sabnzbd"),
    "SABNZBD_API_KEY": Field("SABNZBD_API_KEY", "secret", "", "sabnzbd"),
    "NZBGET_USERNAME": Field("NZBGET_USERNAME", "secret", "", "sabnzbd"),
    "NZBGET_PASSWORD": Field("NZBGET_PASSWORD", "secret", "", "sabnzbd"),
    "SAB_CATEGORY": Field("SABNZBD_CATEGORY", "str", "audiobooks", "sabnzbd"),
    "USENET_COMPLETE_ROOT": Field("USENET_COMPLETE_ROOT", "path", "/usenet-complete", "sabnzbd"),
    "TORRENT_CLIENT": Field("ABW_TORRENT_CLIENT", "str", "qbittorrent", "qbit"),  # qbittorrent, transmission, deluge, realdebrid, torbox
    "QBIT_URL": Field("QBIT_URL", "url", "", "qbit"),
    "QBIT_USERNAME": Field("QBIT_USERNAME", "secret", "", "qbit"),
    "QBIT_PASSWORD": Field("QBIT_PASSWORD", "secret", "", "qbit"),
    "QBIT_SAVE_ROOT": Field("QBIT_SAVE_ROOT", "path", "", "qbit"),
    "QBIT_CATEGORY": Field("QBIT_CATEGORY", "str", "abw", "qbit"),
    "DEBRID_API_KEY": Field("DEBRID_API_KEY", "secret", "", "qbit"),  # Real-Debrid oder TorBox
    "TORRENT_COMPLETE_ROOT": Field("TORRENT_COMPLETE_ROOT", "path", "/torrent-complete", "qbit"),
    "NTFY_URL": Field("NTFY_URL", "url", "", "notify"),
    "NTFY_TOPIC": Field("NTFY_TOPIC", "str", "", "notify"),
    "NTFY_TOKEN": Field("NTFY_TOKEN", "secret", "", "notify"),
    "WEBHOOK_URL": Field("ABW_WEBHOOK_URL", "secret", "", "notify"),  # Discord, Slack, Teams oder eigener Empfänger (JSON)
    "PUBLIC_URL": Field("ABW_PUBLIC_URL", "url", "", "notify"),
    "FLARESOLVERR_URL": Field("FLARESOLVERR_URL", "url", "", "extra"),  # optional: Goodreads und Erweiterungen
    "AUDIBLE_DELAY": Field("AUDIBLE_DELAY", "float", "0.35", "extra"),
    "WHISPER_MODEL": Field("WHISPER_MODEL", "str", "small", "extra"),
    "WHISPER_CACHE": Field("WHISPER_CACHE", "path", "", "extra"),  # Ordner mit schon geladenem Modell
    "IMPORT_ROOT": Field("ABW_IMPORT_ROOT", "path", "/import", "extra"),  # Eingangsordner für den Handimport (nur lesend)
}
PREFIX = "cfg:"
_stored: dict[str, str] = {}  # in der Datenbank gespeicherte Werte


def _env(f: Field) -> str:
    if f.kind == "secret":
        path = os.environ.get(f.env + "_FILE", "")
        if path and Path(path).is_file():
            return Path(path).read_text().strip()
        return os.environ.get(f.env, "")
    return os.environ.get(f.env, f.default)


def raw(name: str) -> str:
    """Wert als Text: gespeichert vor Umgebung vor Grundwert."""
    f = FIELDS[name]
    return _stored[name] if name in _stored else _env(f)


def source(name: str) -> str:
    """Woher der Wert kommt: «db», «env» oder «default»."""
    f = FIELDS[name]
    if name in _stored:
        return "db"
    if (f.kind == "secret" and _env(f)) or os.environ.get(f.env) is not None:
        return "env"
    return "default"


def _typed(name: str, text: str):
    kind = FIELDS[name].kind
    if kind == "url":
        return text.strip().rstrip("/")
    if kind == "ids":
        return [int(x) for x in text.split(",") if x.strip().isdigit()]
    if kind == "path":
        return Path(text) if text.strip() else None
    if kind == "bool":
        return text.strip().lower() in ("1", "true", "ja", "yes", "on")
    if kind == "scheme":  # ungültiges Schema (etwa von Hand in .env): Grundschema, damit die Ablage nicht scheitert
        from . import folders
        return text.strip() if not folders.problem(text, single=name == "FOLDER_SINGLE") else FIELDS[name].default
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            return float(FIELDS[name].default)
    return text.strip()


def reload(conn=None) -> None:
    """Gespeicherte Werte aus der Datenbank lesen und alle Modulwerte neu setzen."""
    _stored.clear()
    if conn is not None:
        try:
            for r in conn.execute("SELECT key, value FROM setting WHERE key LIKE ?", (PREFIX + "%",)):
                name = r[0][len(PREFIX):]
                if name in FIELDS:
                    _stored[name] = r[1]
        except Exception:  # noqa: BLE001 - Tabelle fehlt noch: dann nur Umgebung
            pass
    g = globals()
    for name, f in FIELDS.items():
        if f.kind != "secret":
            g[name] = _typed(name, raw(name))
    # Pfade ohne Wert behalten ihren Grundwert, damit Path-Rechnungen nicht an None scheitern
    for name in ("LIBRARY_EN_ROOT", "LIBRARY_DE_ROOT", "LIBRARY_TRASH", "USENET_COMPLETE_ROOT", "TORRENT_COMPLETE_ROOT"):
        if g[name] is None:
            g[name] = Path(FIELDS[name].default)
    g["QBIT_SAVE_ROOT"] = str(g["QBIT_SAVE_ROOT"] or "").rstrip("/")
    g["WHISPER_CACHE"] = str(g["WHISPER_CACHE"] or "")
    g["ABS_LIBRARIES"] = {"en": g["ABS_LIBRARY_EN"], "de": g["ABS_LIBRARY_DE"]}  # Sprache -> Bibliotheks-ID
    g["LIBRARY_ROOT"] = {"en": g["LIBRARY_EN_ROOT"], "de": g["LIBRARY_DE_ROOT"]}
    # Sprachen in Gebrauch: die mit einer Bibliothek. Ohne jede Bibliothek (frische Installation) beide, damit
    # Entdecken und Suche im Audible-Katalog schon gehen.
    g["LANGS"] = [lang for lang in ("en", "de") if g["ABS_LIBRARIES"][lang]] or ["en", "de"]


def main_lang() -> str:
    return LANGS[0]


def save(conn, values: dict[str, str | None]) -> None:
    """Werte speichern; None oder "" bei einem Schlüssel löscht den gespeicherten Wert (dann gilt wieder .env)."""
    for name, value in values.items():
        if name not in FIELDS:
            raise KeyError(name)
        if value is None:
            conn.execute("DELETE FROM setting WHERE key=?", (PREFIX + name,))
        else:
            conn.execute("INSERT OR REPLACE INTO setting(key, value) VALUES(?,?)", (PREFIX + name, str(value)))
    reload(conn)


def secret(name: str) -> str:
    """Zugangsdaten: gespeichert, sonst aus einer eingebundenen Datei (<NAME>_FILE) oder der Variable selbst."""
    if name in FIELDS:
        return raw(name)
    path = os.environ.get(name + "_FILE", "")
    return Path(path).read_text().strip() if path and Path(path).exists() else ""


def abs_token() -> str:
    return secret("ABS_API_KEY")


reload()
