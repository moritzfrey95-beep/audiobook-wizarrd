"""MP3 zu M4B: viele Dateien eines Buchs zu einer M4B mit Kapiteln zusammenfassen.

Ablauf: M4B als Teil-Datei im Buchordner bauen, gegen die Originale prüfen (Laufzeit, Kapitelzahl, abspielbar),
erst dann die Originale in den Papierkorb ausserhalb der Bibliothek verschieben. Gelöscht wird hier nichts.
"""
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config, db, verify

SOURCES = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".wma"}
COPYABLE = {"aac"}  # schon AAC: ohne Neukodierung übernehmen
progress_now: dict = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def natural(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def folder_of(book: dict) -> Path:
    return config.LIBRARY_ROOT[book["lang"]] / book["rel_path"]


def sources(folder: Path) -> list[Path]:
    return sorted((p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SOURCES and not p.name.startswith(".")), key=natural)


def trash_root() -> Path:
    return config.LIBRARY_TRASH


def plan(book: dict) -> dict:
    """Was würde passieren? Ändert nichts."""
    problems = []
    folder = folder_of(book) if book.get("rel_path") else None
    files = sources(folder) if folder and folder.is_dir() else []
    if not folder or not folder.is_dir():
        problems.append("Der Buchordner ist nicht erreichbar")
    elif len(files) < 2:
        problems.append("Das Buch besteht nicht aus mehreren Audiodateien")
    elif any(p.suffix.lower() in (".m4b",) for p in folder.rglob("*") if p.is_file() and not p.name.startswith(".")):
        problems.append("Im Ordner liegt schon eine M4B-Datei neben den Einzeldateien")
    elif len({p.suffix.lower() for p in files}) > 1:
        problems.append("Die Dateien haben verschiedene Formate")
    name = importer_clean(book["title"]) + ".m4b"
    return {"folder": str(folder) if folder else "", "files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "target": name, "problems": problems}


def importer_clean(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[/\\\0<>|"?*:]', " ", name)).strip(" .")[:150]


def _esc(text: str) -> str:
    return re.sub(r"([=;#\\\n])", r"\\\1", text)


def _bitrate(kbps: float) -> int:
    want = max(48, min(128, kbps * 0.8))
    return min((48, 64, 80, 96, 112, 128), key=lambda b: abs(b - want))


def convert(book: dict, report=lambda pct: None) -> dict:
    """Führt die Umwandlung aus. Wirft bei jedem Zweifel einen Fehler und lässt die Originale unberührt."""
    p = plan(book)
    if p["problems"]:
        raise ValueError("; ".join(p["problems"]))
    folder = Path(p["folder"])
    files = sources(folder)
    infos = []
    for f in files:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", "-select_streams", "a:0", str(f)],
                             capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            raise ValueError(f"{f.name} ist nicht lesbar")
        d = json.loads(out.stdout)
        st = (d.get("streams") or [{}])[0]
        infos.append({"dur": float(d["format"].get("duration") or 0), "codec": st.get("codec_name"), "ch": int(st.get("channels") or 2),
                      "br": float(d["format"].get("bit_rate") or 0) / 1000, "title": (d["format"].get("tags") or {}).get("title")})
    total = sum(i["dur"] for i in infos)
    if total < 60 or any(i["dur"] <= 0 for i in infos):
        raise ValueError("Mindestens eine Datei hat keine lesbare Laufzeit")
    copy = all(i["codec"] in COPYABLE for i in infos)
    kbps = _bitrate(sum(i["br"] * i["dur"] for i in infos) / total)
    channels = 1 if all(i["ch"] == 1 for i in infos) else 2

    target = folder / p["target"]
    part = folder / (".abw-teil-" + p["target"])
    if target.exists() or part.exists():
        raise ValueError("Die Zieldatei oder ein unfertiger Versuch existiert schon")
    cover = next((c for c in (folder / "cover.jpg", folder / "cover.png") if c.is_file()), None)
    with tempfile.TemporaryDirectory() as tmp:
        lst, meta = Path(tmp) / "liste.txt", Path(tmp) / "meta.txt"
        lst.write_text("".join("file '" + str(f).replace("'", "'\\''") + "'\n" for f in files), encoding="utf-8")
        lines = [";FFMETADATA1", f"title={_esc(book['title'])}", f"album={_esc(book['title'])}",
                 f"artist={_esc(book.get('author') or '')}", f"album_artist={_esc(book.get('author') or '')}", "genre=Audiobook"]
        pos = 0.0
        generic = len({i["title"] for i in infos if i["title"]}) <= 1  # alle Dateien tragen denselben Titel: dann zählt der Dateiname
        for f, i in zip(files, infos):
            name = f.stem if generic or not i["title"] else i["title"]
            lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(pos * 1000)}", f"END={int((pos + i['dur']) * 1000)}", f"title={_esc(name)}"]
            pos += i["dur"]
        meta.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cmd = ["nice", "-n", "19", "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(meta)]
        if cover:
            cmd += ["-i", str(cover)]
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        cmd += ["-c:a", "copy"] if copy else ["-c:a", "aac", "-b:a", f"{kbps}k", "-ac", str(channels), "-threads", "2"]
        if cover:
            cmd += ["-map", "2:v", "-c:v", "copy", "-disposition:v", "attached_pic"]
        cmd += ["-movflags", "+faststart", "-f", "ipod", "-progress", "pipe:1", str(part)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for line in proc.stdout:
            if line.startswith("out_time_ms="):
                try:
                    report(min(0.99, int(line.split("=")[1]) / 1e6 / total))
                except ValueError:
                    pass
        err = proc.stderr.read()
        if proc.wait() != 0:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"ffmpeg ist gescheitert: {err.strip()[-200:]}")

    # Prüfung gegen die Originale, bevor irgendetwas verschoben wird
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_chapters", str(part)],
                             capture_output=True, text=True, timeout=120)
        d = json.loads(out.stdout or "{}")
        got = float((d.get("format") or {}).get("duration") or 0)
        if abs(got - total) > max(5, total * 0.005):
            raise ValueError(f"Laufzeit der M4B weicht ab ({round(got)} s statt {round(total)} s)")
        if len(d.get("chapters") or []) != len(files):
            raise ValueError("Die Kapitelzahl stimmt nicht mit der Zahl der Dateien überein")
        if not verify.decodes(part, False) or not verify.decodes(part, True):
            raise ValueError("Die M4B lässt sich am Anfang oder Ende nicht abspielen")
    except Exception:
        part.unlink(missing_ok=True)
        raise

    # Originale in den Papierkorb (gleiche Platte, darum nur Umbenennen), dann die M4B an ihren Platz
    bin_dir = trash_root() / datetime.now().strftime("%Y-%m-%d") / book["lang"] / book["rel_path"]
    bin_dir.mkdir(parents=True, exist_ok=False)
    moved = []
    try:
        for f in files:
            dst = bin_dir / f.relative_to(folder)
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(f, dst)
            moved.append((f, dst))
        os.rename(part, target)
    except Exception:
        for f, dst in moved:  # alles zurück an den alten Platz
            os.rename(dst, f)
        part.unlink(missing_ok=True)
        raise
    for sub in sorted((d for d in folder.rglob("*") if d.is_dir()), key=lambda d: -len(d.parts)):
        if not any(sub.iterdir()):
            sub.rmdir()  # leere CD-Unterordner
    return {"files": len(files), "src_bytes": p["bytes"], "out_bytes": target.stat().st_size, "minutes": round(total / 60),
            "mode": "übernommen" if copy else f"{kbps} kbit/s", "trash": str(bin_dir)}


def run_job(book_id: int) -> dict:
    from . import abs as abs_mod
    c = db.conn()
    b = c.execute("SELECT b.*, e.name series FROM book b LEFT JOIN edition e ON e.id=b.edition_id WHERE b.id=?", (book_id,)).fetchone()
    if not b or not b["owned"]:
        raise LookupError("Buch nicht im Bestand")
    book = dict(b)
    jid = c.execute("INSERT INTO convert_job(book_id, state, started) VALUES(?,?,?)", (book_id, "running", _now())).lastrowid
    progress_now.update(book_id=book_id, title=book["title"], pct=0.0)
    try:
        res = convert(book, lambda pct: progress_now.update(pct=pct))
        c.execute("UPDATE convert_job SET state='done', finished=?, message=?, src_files=?, src_bytes=?, out_bytes=? WHERE id=?",
                  (_now(), f"{res['files']} Dateien zu einer M4B, {res['minutes'] // 60} h {res['minutes'] % 60:02d} min, Ton {res['mode']}",
                   res["files"], res["src_bytes"], res["out_bytes"], jid))
        c.execute("UPDATE book SET num_files=1 WHERE id=?", (book_id,))
        try:
            abs_mod.scan(book["lang"], config.LIBRARY_ROOT[book["lang"]] / book["rel_path"] if book.get("rel_path") else None)
        except Exception:
            pass
        return res
    except Exception as exc:
        state = "skipped" if isinstance(exc, ValueError) else "failed"
        c.execute("UPDATE convert_job SET state=?, finished=?, message=? WHERE id=?", (state, _now(), str(exc)[:300], jid))
        raise
    finally:
        progress_now.clear()


def pending_sql() -> str:
    # Bücher mit mehreren Dateien, die noch nie versucht wurden
    return ("SELECT b.id FROM book b WHERE b.owned=1 AND b.num_files>1 AND b.rel_path IS NOT NULL"
            " AND b.id NOT IN (SELECT book_id FROM convert_job)")


def auto(progress=lambda msg: None) -> str:
    c = db.conn()
    n = c.execute(f"SELECT COUNT(*) FROM ({pending_sql()})").fetchone()[0]
    if db.setting("auto_convert") != "1":
        return f"Automatische Umwandlung ist ausgeschaltet, {n} Bücher warten"
    if os.getloadavg()[0] > (os.cpu_count() or 4) * 1.5:
        return f"Rechner ausgelastet, Umwandlung wartet ({n} Bücher offen)"
    row = c.execute(pending_sql() + " ORDER BY b.size LIMIT 1").fetchone()
    if not row:
        return "Nichts umzuwandeln"
    title = c.execute("SELECT title FROM book WHERE id=?", (row["id"],)).fetchone()["title"]
    progress(f"Wandelt «{title}» um")
    try:
        res = run_job(row["id"])
        return f"«{title}»: {res['files']} Dateien zu einer M4B ({n - 1} Bücher offen)"
    except ValueError as exc:
        return f"«{title}» übersprungen: {exc}"


def overview() -> dict:
    c = db.conn()
    jobs = [dict(r) for r in c.execute(
        "SELECT j.*, b.title, b.lang, e.name series, e.series_id FROM convert_job j JOIN book b ON b.id=j.book_id"
        " LEFT JOIN edition e ON e.id=b.edition_id ORDER BY j.id DESC LIMIT 30")]
    pend = c.execute(f"SELECT COUNT(*) n, COALESCE(SUM(size),0) s FROM book WHERE id IN ({pending_sql()})").fetchone()
    counts = {r["state"]: r["n"] for r in c.execute("SELECT state, COUNT(*) n FROM convert_job GROUP BY state")}
    bin_items = []
    root = trash_root()
    for day in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        files = [f for f in day.rglob("*") if f.is_file()]
        bin_items.append({"day": day.name, "files": len(files), "bytes": sum(f.stat().st_size for f in files)})
    return {"pending": pend["n"], "pending_bytes": pend["s"], "counts": counts, "jobs": jobs, "running": dict(progress_now) or None,
            "trash": bin_items, "auto": db.setting("auto_convert") == "1", "keep_days": 14}


def purge(older_than_days: int = 14) -> dict:
    """Originale endgültig löschen, die länger als die Frist im Papierkorb liegen. Nur auf Knopfdruck."""
    import shutil
    from datetime import timedelta
    limit = (datetime.now() - timedelta(days=older_than_days)).strftime("%Y-%m-%d")
    root, n, size = trash_root(), 0, 0
    for day in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        if day.name > limit or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name):
            continue
        files = [f for f in day.rglob("*") if f.is_file()]
        n += len(files)
        size += sum(f.stat().st_size for f in files)
        shutil.rmtree(day)
    return {"removed": n, "bytes": size}
