"""Aufräumen der Download-Reste. Verschiebt in einen Papierkorb; endgültig gelöscht wird nur auf ausdrücklichen Knopfdruck."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, db, plugins, torrent

TRASH = ".abw-papierkorb"
FINISHED = {"done": "Abgelegt und im Bestand", "failed": "Download defekt", "rejected": "Verworfen oder durchgefallen",
            "split": "Sammelband, in Einzelbände aufgeteilt"}
ACTIVE = ("queued", "downloading", "arrived", "verified", "review", "imported")


def roots() -> list[Path]:
    return [config.USENET_COMPLETE_ROOT, config.TORRENT_COMPLETE_ROOT] + [s.root for s in plugins.sources()]


def _root_name(root: Path) -> str:
    if root == config.TORRENT_COMPLETE_ROOT:
        return "AudioBookBay"
    if root == config.USENET_COMPLETE_ROOT:
        return "Usenet"
    return " / ".join(s.name for s in plugins.sources() if s.root == root) or root.name


def _size(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return sum(p.stat().st_size for p in files), len(files)


def candidates() -> list[dict]:
    """Ordner abw-*, deren Download abgeschlossen ist oder zu denen es keinen Download mehr gibt."""
    c = db.conn()
    out = []
    folders = [(root, p) for root in roots() if root.is_dir() for p in root.iterdir() if p.is_dir() and p.name.startswith("abw-")]
    for root, folder in sorted(folders, key=lambda x: x[1].name):
        d = c.execute("SELECT d.state, d.release_title, d.updated, b.title FROM download d LEFT JOIN book b ON b.id=d.book_id WHERE d.marker=?",
                      (folder.name,)).fetchone() or c.execute(
            "SELECT j.state, j.release_title, j.updated, b.title FROM ebook_job j LEFT JOIN book b ON b.id=j.book_id WHERE j.marker=?",
            (folder.name,)).fetchone()
        if d and d["state"] in ACTIVE:
            continue  # wird noch gebraucht
        size, files = _size(folder)
        out.append({"marker": folder.name, "bytes": size, "files": files, "root": str(root),
                    "source": _root_name(root),
                    "title": (d["title"] or d["release_title"]) if d else folder.name,
                    "reason": FINISHED.get(d["state"], d["state"]) if d else "Kein Download mehr dazu bekannt",
                    "finished": d["updated"] if d else None})
    return out


def run(markers: list[str] | None = None, min_age_hours: float = 0) -> dict:
    """Verschiebt die Ordner in den Papierkorb (gleiche Platte, darum sofort). Prüft jeden Ordner direkt vorher neu."""
    limit = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    moved, size = 0, 0
    for cand in candidates():
        root = Path(cand["root"])
        day = root / TRASH / datetime.now().strftime("%Y-%m-%d")
        if markers is not None and cand["marker"] not in markers:
            continue
        if min_age_hours and (cand["finished"] or "") > limit:
            continue
        day.mkdir(parents=True, exist_ok=True)
        target = day / cand["marker"]
        if target.exists():
            continue
        if root == config.TORRENT_COMPLETE_ROOT and torrent.configured():
            try:
                torrent.forget(cand["marker"])  # erst aus qBittorrent austragen, sonst vermisst es die Dateien
            except Exception:
                continue
        (root / cand["marker"]).rename(target)
        moved += 1
        size += cand["bytes"]
    return {"moved": moved, "bytes": size}


def trash() -> list[dict]:
    out = []
    for base in (r / TRASH for r in roots()):
        for day in sorted(p for p in base.iterdir() if p.is_dir()) if base.is_dir() else []:
            for folder in sorted(p for p in day.iterdir() if p.is_dir()):
                size, files = _size(folder)
                out.append({"day": day.name, "marker": folder.name, "bytes": size, "files": files, "base": str(base)})
    return out


def purge(older_than_days: int) -> dict:
    """Endgültig löschen, was länger als die Frist im Papierkorb liegt. Nur Ordner abw-* im Papierkorb."""
    limit = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d")
    removed, size = 0, 0
    for item in trash():
        if item["day"] > limit or not item["marker"].startswith("abw-"):
            continue
        shutil.rmtree(Path(item["base"]) / item["day"] / item["marker"])
        removed += 1
        size += item["bytes"]
    for base in (r / TRASH for r in roots()):
        for day in list(base.iterdir()) if base.is_dir() else []:
            if day.is_dir() and not any(day.iterdir()):
                day.rmdir()
    return {"removed": removed, "bytes": size}


def auto(progress=lambda msg: None) -> str:
    if db.setting("auto_cleanup") != "1":
        n = len(candidates())
        return f"Automatisches Aufräumen ist ausgeschaltet, {n} Ordner warten" if n else "Nichts aufzuräumen"
    r = run(min_age_hours=24)
    return f"{r['moved']} Ordner in den Papierkorb verschoben ({round(r['bytes'] / 1e6)} MB)" if r["moved"] else "Nichts aufzuräumen"
