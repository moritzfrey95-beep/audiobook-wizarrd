"""Erweiterungen: zusätzliche Quellen, die nicht zum Lieferumfang gehören.

Usenet und AudioBookBay laufen über Prowlarr und sind fest eingebaut. Weitere Quellen kommen als Erweiterung aus dem
Ordner ABW_PLUGINS_DIR (Standard /plugins). Jedes Python-Modul oder Paket dort wird beim Start geladen und muss eine
Funktion register(plugins) anbieten, die mit plugins.add_source(...) Quellen anmeldet.

Eine Quelle ist ein Objekt mit diesen Angaben:
  key, name            Kennung (in der Datenbank gespeichert) und Anzeigename
  order                Rang bei gleich guten sicheren Treffern (Usenet 0, AudioBookBay 1, Standard 5)
  root                 Ordner der fertigen Downloads aus Sicht von ABW (Path)
  configured()         ist die Quelle eingerichtet?
  active_for(book)     für diesen Band durchsuchen? (optional, sonst configured())
  search(queries)      -> (Treffer, Fehlermeldungen); Treffer wie bei Usenet mit guid, title, size, source=key
  refresh(guid)        Download-Angaben eines gespeicherten Treffers frisch holen
  submit(guid, marker) Download starten, Ordnername = marker; gibt die Kennung beim Download-Client zurück
  status(rows)         {download.id: {"state": queued|downloading|arrived|failed, "progress", "error"}}
  can_start()          kann gerade ein Download gestartet werden?
  diagnose()           optional: Liste von (Name, eingerichtet, Beschreibung) für die Einstellungen
Erweiterte Quellen werden nur gefragt, solange Usenet und AudioBookBay nichts Sicheres gefunden haben.
"""
import importlib
import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("abw.plugins")
_sources: dict[str, object] = {}
_loaded = False
errors: list[str] = []


def add_source(source) -> None:
    if source.key in ("usenet", "audiobookbay"):
        raise ValueError(f"Kennung {source.key} ist fest eingebaut")
    _sources[source.key] = source


def load(folder: str | None = None) -> None:
    """Erweiterungen einmal laden. Fehler halten ABW nicht auf, sie stehen in errors."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    root = Path(folder or os.environ.get("ABW_PLUGINS_DIR", "/plugins"))
    if not root.is_dir():
        return
    sys.path.insert(0, str(root))
    for entry in sorted(root.iterdir()):
        name = entry.stem if entry.suffix == ".py" else entry.name
        if name.startswith((".", "_")) or not (entry.suffix == ".py" or (entry / "__init__.py").is_file()):
            continue
        try:
            mod = importlib.import_module(name)
            mod.register(sys.modules[__name__])
            log.info("Erweiterung %s geladen", name)
        except Exception as exc:  # noqa: BLE001 - eine kaputte Erweiterung darf ABW nicht stoppen
            errors.append(f"{name}: {exc}")
            log.exception("Erweiterung %s nicht geladen", name)


def sources() -> list:
    load()
    return sorted(_sources.values(), key=lambda s: getattr(s, "order", 5))


def get(key: str | None):
    load()
    return _sources.get(key or "")


def active_for(src, book: dict) -> bool:
    f = getattr(src, "active_for", None)
    return f(book) if f else src.configured()


def reset() -> None:
    """Nur für Tests."""
    global _loaded
    _sources.clear()
    errors.clear()
    _loaded = False
