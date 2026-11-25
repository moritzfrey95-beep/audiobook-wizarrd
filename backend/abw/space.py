"""Mindest-Speicherplatz: kein neuer Download, wenn im Download-Ziel oder in der Bibliothek weniger als `min_free_gb` frei ist.
Statt abzubrechen meldet ABW das einmal am Tag."""
import shutil
from datetime import date
from pathlib import Path

from . import config, db

_warned: dict[str, str] = {}


def places() -> list[tuple[str, Path]]:
    out = [("Usenet-Ziel", config.USENET_COMPLETE_ROOT), ("Torrent-Ziel", config.TORRENT_COMPLETE_ROOT),
           ("Bibliothek Englisch", config.LIBRARY_EN_ROOT), ("Bibliothek Deutsch", config.LIBRARY_DE_ROOT)]
    seen, res = set(), []
    for name, p in out:
        if p and Path(p).is_dir():
            try:
                dev = Path(p).stat().st_dev
            except OSError:
                continue
            if dev not in seen:
                seen.add(dev)
                res.append((name, Path(p)))
    return res


def status() -> list[dict]:
    out = []
    for name, p in places():
        try:
            u = shutil.disk_usage(p)
        except OSError:
            continue
        out.append({"name": name, "free_gb": round(u.free / 1e9, 1), "total_gb": round(u.total / 1e9, 1)})
    return out


def low() -> list[str]:
    """Orte mit zu wenig freiem Platz, als Text. Leer, wenn genug frei ist oder die Grenze aus ist (0)."""
    limit = float(db.setting("min_free_gb") or 0)
    if limit <= 0:
        return []
    return [f"{s['name']}: {s['free_gb']:g} GB frei (Grenze {limit:g} GB)" for s in status() if s["free_gb"] < limit]


def check() -> None:
    """Wirft ValueError bei zu wenig Platz und meldet das einmal am Tag."""
    msgs = low()
    if not msgs:
        return
    text = "; ".join(msgs)
    today = date.today().isoformat()
    if _warned.get("day") != today:
        _warned["day"] = today
        from . import notify
        notify.send("Wenig Speicherplatz", f"Neue Downloads pausiert. {text}", "/aufgaben", "warning")
    raise ValueError(f"Zu wenig Speicherplatz, neue Downloads warten: {text}")
