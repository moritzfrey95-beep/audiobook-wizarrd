"""Ablage eines geprüften Hörbuchs in die Bibliothek. Legt nur neue Ordner an, überschreibt und löscht nie."""
import functools
import json
import os
import re
import shutil
import subprocess
import threading
from datetime import datetime
from collections import Counter
from pathlib import Path

from . import config, db, plugins, verify

KEEP = verify.AUDIO | {".jpg", ".jpeg", ".png", ".cue", ".nfo", ".opf", ".pdf"}


def clean(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[/\\\0<>|"?*]', " ", name)).strip(" .")


def source_root(source: str) -> Path:
    if src := plugins.get(source):
        return src.root
    return config.TORRENT_COMPLETE_ROOT if source == "audiobookbay" else config.USENET_COMPLETE_ROOT


_ACTIVE: set[str] = set()  # Teil-Ordner, die dieser Prozess gerade füllt
_mine = threading.local()


def _frees_teil(fn):
    """Gibt die Teil-Ordner dieses Aufrufs wieder frei, auch wenn er mit einem Fehler endet."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _mine.dirs = []
        try:
            return fn(*args, **kwargs)
        finally:
            for d in _mine.dirs:
                _ACTIVE.discard(d)
    return wrapper


def carry_metadata(old: Path, new: Path) -> None:
    """metadata.json des ersetzten Buches übernehmen, aber ohne Kapitel: die gehören zu den alten Dateien. Audiobookshelf
    nähme sie sonst für die neuen (Silmarillion 18.11.: Kapitel endeten hinter dem Buch, falsche Reihenfolge)."""
    try:
        data = json.loads(old.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        shutil.copyfile(old, new)
        return
    if isinstance(data, dict):
        data.pop("chapters", None)
    new.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def busy(target: str | Path | None) -> bool:
    """Füllt dieser Prozess gerade den Teil-Ordner dieses Ziels (Ablage oder Ersetzen läuft)?"""
    if not target:
        return False
    t = Path(target)
    return str(t.with_name("." + t.name + ".abw-teil")) in _ACTIVE


def teil_dir(target: Path, lang: str) -> Path:
    """Legt den Teil-Ordner neben dem Ziel an, in den zuerst kopiert wird. Liegt dort noch einer, den kein laufender
    Versuch füllt (Kopieren durch Neustart abgebrochen), wandert er in den Papierkorb; gelöscht wird nichts."""
    tmp = target.with_name("." + target.name + ".abw-teil")
    if str(tmp) in _ACTIVE:
        raise ValueError("Für diesen Band läuft gerade schon eine Ablage")
    if tmp.exists():
        rel = tmp.relative_to(config.LIBRARY_ROOT[lang]) if tmp.is_relative_to(config.LIBRARY_ROOT[lang]) else Path(tmp.name)
        bin_dir = config.LIBRARY_TRASH / datetime.now().strftime("%Y-%m-%d") / lang / "abgebrochen" / f"{rel}-{datetime.now():%H%M%S-%f}"
        bin_dir.parent.mkdir(parents=True, exist_ok=True)
        os.rename(tmp, bin_dir)
    tmp.mkdir(parents=True)
    _ACTIVE.add(str(tmp))
    _mine.dirs.append(str(tmp))
    return tmp


def source_dir(download: dict) -> Path:
    return source_root(download["source"]) / download["marker"]


def target_rel(book: dict) -> tuple[str, str]:
    """Serienordner und Buchordner, in dem ein Band landet (relativ zur Bibliothek)."""
    c = db.conn()
    seq = (book.get("sequence") or "").strip()
    if book.get("edition_id"):
        siblings = [r["rel_path"] for r in c.execute(
            "SELECT rel_path FROM book WHERE edition_id=? AND owned=1 AND rel_path IS NOT NULL", (book["edition_id"],))]
        parents = Counter(str(Path(p).parent) for p in siblings if "/" in p)
        series_dir = parents.most_common(1)[0][0] if parents else clean(book.get("series") or book["title"])
        name = f"Book {seq} - {book['title']}" if seq else book["title"]
        # Mehrere Sprecher-Fassungen derselben Sprache: Sprecher im Ordnernamen, sonst stossen sie zusammen oder sind
        # nicht auseinanderzuhalten. Die ungebundene Hauptfassung nimmt den Sprecher des Buchs laut Audible
        # (Harry Potter DE: Manteuffel neben «Gesprochen von Rufus Beck», 18.11.2026).
        from . import variants
        e = c.execute("SELECT series_id, lang, narr FROM edition WHERE id=?", (book["edition_id"],)).fetchone()
        narr = e and (e["narr"] or next(iter(variants.expected_narrators({k: book.get(k) for k in ("title", "narrator")})), None))
        if narr and c.execute("SELECT 1 FROM edition WHERE series_id=? AND lang=? AND id<>? AND narr IS NOT NULL",
                              (e["series_id"], e["lang"], book["edition_id"])).fetchone():
            name += f" [{narr}]"
        return series_dir, clean(name)
    # Einzelbuch: wie die vorhandenen Einzelbücher unter «Standalone - Autor»
    from . import abs as abs_mod
    author = abs_mod.primary_author(book.get("author"))
    same = [r["rel_path"] for r in c.execute("SELECT rel_path, author FROM book WHERE edition_id IS NULL AND owned=1 AND lang=? AND rel_path LIKE '%/%'", (book["lang"],))
            if db.norm(abs_mod.primary_author(r["author"])) == db.norm(author)]
    parents = Counter(str(Path(p).parent) for p in same)
    return (parents.most_common(1)[0][0] if parents else clean(f"Standalone - {author}")), clean(book["title"])


def plan(book: dict, download: dict) -> dict:
    """Was würde wohin kopiert? Ändert nichts."""
    c = db.conn()
    root = config.LIBRARY_ROOT[book["lang"]]
    # Ein Sammelband, der gerade in Einzelbände aufgeteilt wird («Book 1&2 - …»), versperrt seine Bände nicht
    siblings = [r["rel_path"] for r in c.execute(
        "SELECT rel_path FROM book WHERE edition_id=? AND owned=1 AND rel_path IS NOT NULL"
        " AND id NOT IN (SELECT book_id FROM libsplit_job WHERE state='split')", (book["edition_id"],))]
    series_dir, name = target_rel(book)
    seq = (book.get("sequence") or "").strip()
    target = root / series_dir / name
    # Für diesen Band liegt schon ein Ordner mit dem vorab geladenen E-Book: das Hörbuch kommt dort hinein
    pre = book.get("pre_path")
    if pre and (root / pre).is_dir():
        target = root / pre
        series_dir, name = str(Path(pre).parent), Path(pre).name
    src = source_dir(download)
    files = _files(src)
    problems = []
    if not root.is_dir():
        problems.append("Die Bibliothek ist nicht eingebunden")
    if not files:
        problems.append("Im Download liegen keine Dateien zum Ablegen")
    hard = list(problems)  # Hindernisse, die sich auch von Hand nicht übergehen lassen
    existing = None
    # Ordner ohne Tondateien (nur E-Book, Cover, metadata.json): kein Hindernis, die Dateien werden dazugelegt
    merge = target.is_dir() and not any(p.is_file() and p.suffix.lower() in verify.AUDIO for p in target.rglob("*"))
    if merge:
        clash = sorted({Path(f).name for f in map(str, files)} & {p.name for p in target.iterdir()} - {"metadata.json"})
        if clash:
            problems.append(f"Im vorhandenen Ordner liegt schon eine Datei mit gleichem Namen: {clash[0]}")
            hard.append(problems[-1])
    elif target.exists():
        problems.append("Der Zielordner existiert schon; ABW überschreibt nichts")
        old = [p for p in target.rglob("*") if p.is_file()] if target.is_dir() else []
        existing = {"files": len(old), "bytes": sum(p.stat().st_size for p in old)}
        if not target.is_dir():
            hard.append("Am Ziel liegt eine Datei statt eines Ordners")
    # «Book 2.5» ist ein anderer Band als «Book 2»
    same = [p for p in siblings if seq and re.match(rf"^Book 0*{re.escape(seq)}(?![\d.])", Path(p).name) and root / p != target]
    # Mehrteiliger Band: ein anderer Teil derselben Nummer ist kein Hindernis, ein ganzer Band schon
    from . import variants
    if (part := variants.part_of(book.get("title"))) is not None:
        same = [p for p in same if variants.part_of(Path(p).name) in (None, part)]
    if same:
        problems.append(f"Für Band {seq} gibt es schon einen Ordner: {same[0]}")
        hard.append(problems[-1])
    return {"target": str(target), "shown": f"{series_dir}/{name}", "existing": existing, "merge": merge,
            # Von Hand überschreiben: nur wenn allein der vorhandene Zielordner im Weg steht
            "overwritable": bool(existing) and not hard,
            "new_series_folder": not (root / series_dir).exists(), "files": [str(p.relative_to(src)) for p in files],
            "bytes": sum(p.stat().st_size for p in files), "problems": problems}


def _files(src: Path) -> list[Path]:
    """Dateien, die abgelegt werden. Liegt das Buch in zwei Formaten vor, bleibt nur eine Fassung (bevorzugt M4B)."""
    files = [p for p in sorted(src.rglob("*")) if p.is_file() and p.suffix.lower() in KEEP] if src.is_dir() else []
    dup = verify.duplicate_sets([f for f in files if f.suffix.lower() in verify.AUDIO])
    return [f for f in files if f not in set(dup["drop"])] if dup else files


def quality(folder: Path) -> dict:
    """Kennzahlen eines Buchordners: Dateien, Laufzeit, Tonqualität."""
    files = verify.audio_files(folder)
    if not files:
        raise ValueError(f"Keine Audiodateien in {folder.name}")
    if dup := verify.duplicate_sets(files):
        files = dup["keep"]  # dasselbe Buch in zwei Formaten: nur eine Fassung zählt
    seconds = sum(verify.probe(f)["duration"] for f in files)
    size = sum(f.stat().st_size for f in files)
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(files[0])],
                         capture_output=True, text=True, timeout=120)
    codec = out.stdout.strip().split("\n")[0] or files[0].suffix.lstrip(".").lower()
    kbps = size * 8 / seconds / 1000 if seconds else 0
    kinds = "/".join(sorted({f.suffix.lstrip(".").upper() for f in files}))
    return {"files": len(files), "minutes": round(seconds / 60), "bytes": size, "kbps": round(kbps), "codec": codec,
            # AAC und Opus klingen bei gleicher Datenrate besser als MP3
            "effective": kbps * (1.4 if codec in ("aac", "opus", "vorbis") else 1.0),
            "text": f"{len(files)} {kinds}, {round(seconds / 60) // 60} h {round(seconds / 60) % 60:02d} min, {round(kbps)} kbit/s"}


def compare(book: dict, new_folder: Path) -> dict:
    """Zusatzprüfung für Bände, die schon im Bestand liegen: Ist der neue Band wirklich besser? Ändert nichts."""
    old, new = quality(config.LIBRARY_ROOT[book["lang"]] / book["rel_path"]), quality(new_folder)
    want, source = verify.want_minutes(book)
    if source == "Bestand":
        want = old["minutes"]  # ohne Audible-Laufzeit: gemessene Länge des vorhandenen Buches (doppelte Formate einmal gezählt)
    fits = lambda q: bool(want) and abs(q["minutes"] - want) <= max(5, want * 0.03)
    both = f"Bestand: {old['text']}. Neu: {new['text']}."
    if want and source == "Audible":
        both += f" Audible nennt {want // 60} h {want % 60:02d} min."
    if 0 < (book.get("progress") or 0) < 1 and not book.get("finished"):
        both += f" Du hörst dieses Buch gerade ({round(book['progress'] * 100)} %)."
    if want and not fits(new):
        return {"better": False, "text": f"Die Laufzeit des neuen Bandes passt nicht zu {'Audible' if source == 'Audible' else 'dem Buch im Bestand'}. {both}"}
    from . import integrity
    if book.get("id") and integrity.is_damaged(book["id"]):
        row = db.conn().execute("SELECT detail FROM integrity WHERE book_id=?", (book["id"],)).fetchone()
        return {"better": True, "text": f"Das Buch im Bestand ist beschädigt ({row['detail'][:160]}), der neue Band ist vollständig. {both}"}
    if want and not fits(old):
        return {"better": True, "text": f"Das Buch im Bestand weicht um {abs(old['minutes'] - want)} min von Audible ab, der neue Band passt. {both}"}
    if new["effective"] >= old["effective"] * 1.25:
        return {"better": True, "text": f"Höhere Tonqualität als im Bestand. {both}"}
    return {"better": False, "text": f"Nicht besser als das Buch im Bestand. {both}"}


def replace_plan(book: dict, download: dict) -> dict:
    """Was würde ersetzt? Ändert nichts."""
    root, src = config.LIBRARY_ROOT[book["lang"]], source_dir(download)
    target = root / (book.get("rel_path") or "")
    bin_dir = config.LIBRARY_TRASH / datetime.now().strftime("%Y-%m-%d") / book["lang"] / (book.get("rel_path") or "")
    files = _files(src)
    problems = []
    if not book.get("rel_path") or not target.is_dir():
        problems.append("Der Ordner des Buches im Bestand ist nicht zu finden")
    if not files:
        problems.append("Im Download liegen keine Dateien zum Ablegen")
    if bin_dir.exists():
        problems.append("Im Papierkorb liegt heute schon ein Ordner dieses Buches")
    return {"target": str(target), "shown": book.get("rel_path") or "", "new_series_folder": False, "trash": str(bin_dir),
            "files": [str(p.relative_to(src)) for p in files], "bytes": sum(p.stat().st_size for p in files), "problems": problems}


@_frees_teil
def replace(book: dict, download: dict) -> str:
    """Ersetzt das Buch im Bestand: neuer Ordner wird daneben fertig aufgebaut, der alte wandert in den Papierkorb,
    dann rückt der neue an denselben Pfad. Gelöscht wird nichts."""
    if not config.IMPORT_ENABLED:
        raise PermissionError("Die Ablage ist noch nicht freigeschaltet (ABW_IMPORT_ENABLED)")
    p = replace_plan(book, download)
    if p["problems"]:
        raise ValueError("; ".join(p["problems"]))
    src, target, bin_dir = source_dir(download), Path(p["target"]), Path(p["trash"])
    tmp = teil_dir(target, book["lang"])
    for rel in p["files"]:
        dst = tmp / Path(rel).name
        if dst.exists():
            raise ValueError(f"Doppelter Dateiname im Download: {Path(rel).name}")
        shutil.copyfile(src / rel, dst)
        if dst.stat().st_size != (src / rel).stat().st_size:
            raise IOError(f"Kopie von {Path(rel).name} ist unvollständig")
    if (target / "metadata.json").is_file():  # deine Angaben aus Audiobookshelf bleiben erhalten
        carry_metadata(target / "metadata.json", tmp / "metadata.json")
    else:
        (tmp / "metadata.json").write_text(json.dumps(metadata(book), ensure_ascii=False, indent=2), encoding="utf-8")
    if not any(f.suffix.lower() in (".jpg", ".jpeg", ".png") for f in tmp.iterdir()):
        for cov in (f for f in target.iterdir() if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png")):
            shutil.copyfile(cov, tmp / cov.name)
    bin_dir.parent.mkdir(parents=True, exist_ok=True)
    os.rename(target, bin_dir)
    try:
        os.rename(tmp, target)
    except Exception:
        os.rename(bin_dir, target)  # alten Ordner zurück an seinen Platz
        raise
    return str(target)


def metadata(book: dict) -> dict:
    """metadata.json im Format von Audiobookshelf: damit das Buch mit Serie, Bandnummer und ASIN eingelesen wird."""
    seq = (book.get("sequence") or "").strip()
    series = book.get("series")
    return {
        "title": book["title"], "subtitle": book.get("subtitle"),
        "authors": [a.strip() for a in (book.get("author") or "").split(",") if a.strip()],
        "narrators": [n.strip() for n in (book.get("narrator") or "").split(",") if n.strip()],
        "series": [f"{series} #{seq}" if seq else series] if series else [],
        "publishedYear": (book.get("release_date") or "")[:4] or None, "asin": book.get("asin"),
        "language": {"en": "English", "de": "German"}[book["lang"]],
    }


def _merge_into(book: dict, src: Path, target: Path, files: list[str]) -> str:
    """Hörbuch in einen vorhandenen Ordner ohne Tondateien legen (dort liegt das vorab geladene E-Book).
    Erst vollständig daneben kopieren und prüfen, dann die Dateien einzeln hineinschieben. Überschrieben wird nichts."""
    tmp = teil_dir(target, book["lang"])
    for rel in files:
        dst = tmp / Path(rel).name
        if dst.exists() or (target / dst.name).exists():
            raise ValueError(f"Doppelter Dateiname: {Path(rel).name}")
        shutil.copyfile(src / rel, dst)
        if dst.stat().st_size != (src / rel).stat().st_size:
            raise IOError(f"Kopie von {Path(rel).name} ist unvollständig")
    if not (target / "metadata.json").exists() and not (tmp / "metadata.json").exists():
        (tmp / "metadata.json").write_text(json.dumps(metadata(book), ensure_ascii=False, indent=2), encoding="utf-8")
    for f in sorted(tmp.iterdir()):
        if (target / f.name).exists():
            continue  # metadata.json aus dem Download: die vorhandene bleibt
        os.rename(f, target / f.name)
    shutil.rmtree(tmp, ignore_errors=True)  # enthält höchstens noch die nicht übernommene metadata.json
    return str(target)


@_frees_teil
def execute(book: dict, download: dict, overwrite: bool = False) -> str:
    """Kopiert in einen neuen Ordner und vergleicht die Grössen. Die Quelle bleibt liegen.
    overwrite (nur von Hand): Ein vorhandener Zielordner wandert vorher in den Papierkorb; gelöscht wird nichts."""
    if not config.IMPORT_ENABLED:
        raise PermissionError("Die Ablage ist noch nicht freigeschaltet (ABW_IMPORT_ENABLED)")
    p = plan(book, download)
    if p["problems"] and not (overwrite and p["overwritable"]):
        raise ValueError("; ".join(p["problems"]))
    src, target = source_dir(download), Path(p["target"])
    bin_dir = None
    if p.get("merge"):
        return _merge_into(book, src, target, p["files"])
    if target.exists():
        bin_dir = config.LIBRARY_TRASH / datetime.now().strftime("%Y-%m-%d") / book["lang"] / target.relative_to(config.LIBRARY_ROOT[book["lang"]])
        if bin_dir.exists():
            raise ValueError("Im Papierkorb liegt heute schon ein Ordner dieses Buches")
    tmp = teil_dir(target, book["lang"])
    for rel in p["files"]:
        dst = tmp / Path(rel).name  # flach ablegen, wie die bestehenden Bücher
        if dst.exists():
            raise ValueError(f"Doppelter Dateiname im Download: {Path(rel).name}")
        shutil.copyfile(src / rel, dst)
        if dst.stat().st_size != (src / rel).stat().st_size:
            raise IOError(f"Kopie von {Path(rel).name} ist unvollständig")
    if not (tmp / "metadata.json").exists():
        (tmp / "metadata.json").write_text(json.dumps(metadata(book), ensure_ascii=False, indent=2), encoding="utf-8")
    if bin_dir is None:
        tmp.rename(target)
        return str(target)
    bin_dir.parent.mkdir(parents=True, exist_ok=True)
    os.rename(target, bin_dir)
    try:
        os.rename(tmp, target)
    except Exception:
        os.rename(bin_dir, target)  # alten Ordner zurück an seinen Platz
        raise
    return str(target)
