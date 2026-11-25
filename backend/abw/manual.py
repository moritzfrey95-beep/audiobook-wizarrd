"""Handimport: Hörbuch-Ordner, die nicht über ABW geladen wurden, einem Buch zuordnen und wie einen Download prüfen.

Quellen sind der Eingangsordner (`ABW_IMPORT_ROOT`, nur lesend eingebunden) und Ordner in den Download-Zielen,
die zu keinem ABW-Download gehören. Beim Übernehmen wird der Ordner in einen eigenen `abw-<buch>-<zufall>`-Ordner
im Usenet-Ziel kopiert und als angekommener Download eingetragen. Danach laufen Ankunftsprüfung, Vergleich mit dem
Bestand und Ablage wie bei jedem Download. Der Quellordner bleibt unverändert; die Kopie räumt «Aufräumen» später weg.
"""
import os
import re
import secrets
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, plugins, scoring, verify

SKIP = re.compile(r"^(?:\.abw|@eaDir|#recycle|\.Trash|abw-|incomplete$|_UNPACK_|_FAILED_|\.usenet$)", re.I)
OPAQUE = re.compile(r"^[a-z]{2,4}-[0-9a-f]{8}-[0-9a-f-]{20,}$", re.I)  # Ordner fremder Werkzeuge mit Kennung statt Titel
CD = re.compile(r"^(?:(?:cd|disc|disk|teil|part|tl)\s*[-_.]?\s*\d+\b|\d{1,2}$)", re.I)
MAX_DEPTH = 3
_cache: dict = {"at": 0.0, "items": []}
_jobs: dict[str, dict] = {}  # Pfad -> Stand der Kopie


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def roots() -> list[tuple[str, Path]]:
    out = []
    imp = getattr(config, "IMPORT_ROOT", None)
    if imp and Path(imp).is_dir():
        out.append(("Eingang", Path(imp)))
    out += [("Usenet", config.USENET_COMPLETE_ROOT), ("Torrent", config.TORRENT_COMPLETE_ROOT)]
    out += [(s.name, s.root) for s in plugins.sources()]
    seen, res = set(), []
    for name, p in out:
        if p and Path(p).is_dir() and str(p) not in seen:
            seen.add(str(p))
            res.append((name, Path(p)))
    return res


def _audio_here(d: Path) -> list[Path]:
    try:
        return [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in verify.AUDIO]
    except OSError:
        return []


def _subdirs(d: Path) -> list[Path]:
    try:
        return sorted(p for p in d.iterdir() if p.is_dir() and not SKIP.match(p.name))
    except OSError:
        return []


def _book_dirs(d: Path, depth: int) -> list[Path]:
    """Buchordner unterhalb von d: ein Ordner mit Audiodateien, oder einer, dessen Unterordner nur CDs/Teile sind."""
    out = []
    for sub in _subdirs(d):
        kids = _subdirs(sub)
        parts = [k for k in kids if _audio_here(k)]  # Unterordner ohne Ton («Cover», «Scans») zählen nicht
        if _audio_here(sub) or (parts and all(CD.match(k.name) for k in parts)):
            out.append(sub)
        elif depth < MAX_DEPTH and kids:
            out += _book_dirs(sub, depth + 1)
    return out


def _size(d: Path) -> tuple[int, int]:
    total = files = 0
    for p, _, names in os.walk(d):
        for n in names:
            if Path(n).suffix.lower() in verify.AUDIO:
                files += 1
                try:
                    total += os.path.getsize(os.path.join(p, n))
                except OSError:
                    pass
    return total, files


def label(d: Path) -> str:
    """Name für die Zuordnung: der Ordnername, bei Kennungen («hbo-1eeda4de-…») Album/Titel und Autor aus den Tags der ersten Datei."""
    if not OPAQUE.match(d.name):
        return d.name if not CD.match(d.name) else d.parent.name
    first = next((Path(p) / n for p, _, names in sorted(os.walk(d)) for n in sorted(names) if Path(n).suffix.lower() in verify.AUDIO), None)
    if not first:
        return d.name
    try:
        tags = {k.lower(): v for k, v in (verify.probe(first).get("tags") or {}).items()}
    except Exception:  # noqa: BLE001
        tags = {}
    text = " - ".join(x for x in (tags.get("artist") or tags.get("album_artist"), tags.get("album") or tags.get("title")) if x)
    if text:
        return text
    return first.parent.name if first.parent != d else first.stem


def _books() -> list[dict]:
    rows = db.conn().execute(
        "SELECT b.id, b.title, b.author, b.sequence, b.lang, b.owned, e.name series, e.series_id FROM book b"
        " LEFT JOIN edition e ON e.id=b.edition_id").fetchall()
    return [{**dict(r), "_t": scoring.significant(r["title"]), "_s": scoring.significant(r["series"] or ""),
             "_a": scoring.significant(r["author"] or "")} for r in rows]


def suggest(name: str, books: list[dict], n: int = 3) -> list[dict]:
    """Passende Bücher zum Ordnernamen: Titelwörter, Serie, Autor und Bandnummer. Fehlende Bände vor vorhandenen."""
    have = scoring.significant(name)
    nums = scoring.numbers(name) | {x.lstrip("0") for x in re.findall(r"\b\d{1,3}\b", scoring.plain(name))}
    scored = []
    for b in books:
        if not b["_t"]:
            continue
        cov = scoring.coverage(b["_t"], have)
        ser = scoring.coverage(b["_s"], have) if b["_s"] else 0
        if cov < 0.6 and not (ser >= 0.8 and scoring.seq_key(b["sequence"]) in nums):
            continue
        pts = round(60 * cov) + (20 if b["_a"] and b["_a"] & have else 0) + (10 if ser >= 0.8 else 0)
        if b["sequence"] and scoring.seq_key(b["sequence"]) in nums:
            pts += 15
        pts -= 10 if b["owned"] else 0
        scored.append((pts, b))
    scored.sort(key=lambda x: -x[0])
    return [{"book_id": b["id"], "title": b["title"], "series": b["series"], "series_id": b["series_id"], "sequence": b["sequence"],
             "lang": b["lang"], "owned": bool(b["owned"]), "points": p} for p, b in scored[:n]]


def folders(refresh: bool = False) -> list[dict]:
    """Ordner, die sich von Hand übernehmen lassen, neueste zuerst. Fünf Minuten zwischengespeichert."""
    if not refresh and time.time() - _cache["at"] < 300:
        items = _cache["items"]
    else:
        c = db.conn()
        known = {r["marker"] for r in c.execute("SELECT marker FROM download")}
        books, items = _books(), []
        for name, root in roots():
            for d in _book_dirs(root, 1):
                if d.name in known:
                    continue
                size, files = _size(d)
                if not files:
                    continue
                try:
                    mtime = datetime.fromtimestamp(d.stat().st_mtime, timezone.utc).isoformat()
                except OSError:
                    mtime = None
                shown = label(d)
                items.append({"path": str(d), "root": name, "name": shown, "rel": str(d.relative_to(root)), "bytes": size, "files": files,
                              "modified": mtime, "suggestions": suggest(shown, books)})
        items.sort(key=lambda x: x["modified"] or "", reverse=True)
        _cache.update(at=time.time(), items=items)
    hidden = {r["path"] for r in db.conn().execute("SELECT path FROM manual_hidden")}
    return [{**x, "job": _jobs.get(x["path"])} for x in items if x["path"] not in hidden]


def hide(path: str) -> None:
    db.conn().execute("INSERT OR IGNORE INTO manual_hidden(path, hidden_at) VALUES(?,?)", (path, _now()))


def _inside_roots(path: Path) -> bool:
    p = path.resolve()
    return any(p.is_relative_to(r.resolve()) and p != r.resolve() for _, r in roots())


def start(path: str, book_id: int) -> dict:
    """Ordner kopieren und als angekommenen Download eintragen. Die Kopie läuft im Hintergrund."""
    src = Path(path)
    if not src.is_dir() or not _inside_roots(src):
        raise ValueError("Dieser Ordner liegt nicht in einem Eingangs- oder Download-Ordner")
    dest_root = config.USENET_COMPLETE_ROOT
    if not dest_root or not Path(dest_root).is_dir():
        raise ValueError("Das Usenet-Ziel ist nicht eingerichtet; dorthin kopiert der Handimport")
    c = db.conn()
    book = c.execute("SELECT id, title, owned FROM book WHERE id=?", (book_id,)).fetchone()
    if not book:
        raise LookupError("Buch nicht gefunden")
    if c.execute("SELECT 1 FROM download WHERE book_id=? AND state IN ('queued','downloading','arrived','verified','review')", (book_id,)).fetchone():
        raise ValueError("Für dieses Buch läuft schon ein Download oder eine Prüfung")
    if (_jobs.get(path) or {}).get("running"):
        raise ValueError("Dieser Ordner wird gerade schon kopiert")
    marker = f"abw-{book_id}-{secrets.token_hex(4)}"
    _jobs[path] = {"running": True, "book_id": book_id, "text": "Kopiert", "download_id": None}

    def work():
        try:
            shutil.copytree(src, Path(dest_root) / marker, ignore=shutil.ignore_patterns("@eaDir", "#recycle", ".DS_Store"))
            size, _ = _size(Path(dest_root) / marker)
            did = db.conn().execute(
                "INSERT INTO download(book_id, source, indexer, release_title, marker, size, state, progress, auto, created, updated, replaces)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (book_id, "usenet", "Handimport", src.name, marker, size, "arrived", 1, 0, _now(), _now(), 1 if book["owned"] else None)).lastrowid
            _jobs[path] = {"running": False, "book_id": book_id, "download_id": did,
                           "text": "Kopiert, wird geprüft (Tab «Prüfen»)" + (", danach mit dem Bestand verglichen" if book["owned"] else "")}
            from . import tasks
            tasks.request("verify")
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(Path(dest_root) / marker, ignore_errors=True)
            _jobs[path] = {"running": False, "book_id": book_id, "download_id": None, "text": f"Kopieren fehlgeschlagen: {exc}"[:300], "error": True}
    threading.Thread(target=work, daemon=True, name="abw-handimport").start()
    return _jobs[path]
