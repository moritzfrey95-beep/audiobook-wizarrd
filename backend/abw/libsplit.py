"""Sammelbände im Bestand aufteilen: ein Buch aus einer einzigen Datei, das mehrere Bände enthält (Publisher's Pack,
«Books 1-3», Serienangabe «#1&2»), wird an Kapitelgrenzen in Einzelbände geschnitten.

Aufgeteilt wird nur bei sicherem Plan: 1) die Kapiteltitel nennen die Bände («Volume 12: Chapter 1»), oder 2) jede
Grenze liegt höchstens 3 Minuten neben der Laufzeit laut Audible. Jeder Band läuft danach als eigener Download durch
Ankunftsprüfung und Ablage. Sind alle Bände abgelegt, wandert der Sammelband in den Papierkorb der Bibliothek.
Unsichere Fälle bleiben unverändert und stehen mit Grund im Tab «Prüfen».
"""
import json
import os
import re
import secrets
import shutil
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import audible, bundle, config, db, scoring, verify

HINT = re.compile(r"publisher'?s pack|box(?:ed)? ?set|omnibus|sammelband", re.I)  # nur im Titel; Bandbereiche erkennt span_of
VOLUME = re.compile(r"\b(?:book|volume|vol|band|buch)\.?\s*0*(\d{1,3})\b", re.I)
EXACT = 180  # Sekunden: so nahe muss eine Grenze ohne Kapiteltitel an der Laufzeit laut Audible liegen
RETRY_DAYS = 7  # unsichere Fälle nach dieser Zeit neu planen (Audible oder Bestand können sich geändert haben)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def span_of(book: dict) -> tuple[int, int] | None:
    """Bandbereich aus Serienangabe («1-2»), Titel («Books 1-3») oder Ordnername («Book 1&2 - …»)."""
    m = re.match(r"^\s*(\d+)\s*[-&]\s*(\d+)\s*$", book.get("sequence") or "")
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo, hi) if 0 < lo < hi <= lo + 20 else None
    folder = Path(book.get("rel_path") or "").name
    m = re.match(r"^Book 0*(\d+)(?:-|\s*&\s*)0*(\d+)\b", folder)  # «Book 1-2 - …», nicht «Book 1 - 12 Miles Below»
    if m and 0 < int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + 20:
        return int(m.group(1)), int(m.group(2))
    return scoring.span(book["title"], book.get("series") or "")


def candidates() -> list[dict]:
    """Bücher aus einer Datei, die nach Sammelband aussehen und deren Serie Audible kennt. Liest nur die Datenbank."""
    limit = (datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)).isoformat()
    rows = db.conn().execute(
        "SELECT b.*, e.name series, e.audible_asin series_asin FROM book b JOIN edition e ON e.id=b.edition_id"
        " WHERE b.owned=1 AND b.num_files=1 AND b.rel_path IS NOT NULL AND e.audible_asin IS NOT NULL"
        " AND b.id NOT IN (SELECT book_id FROM libsplit_job WHERE state<>'unsafe' OR at>?) ORDER BY b.id", (limit,)).fetchall()
    return [dict(r) for r in rows if span_of(dict(r)) or HINT.search(r["title"])]


def _audible_bands(book: dict) -> dict[int, dict]:
    """Ganzzahlige Bände der Serie laut Audible, einer je Nummer (gleiche Auswahl wie beim Serienabgleich)."""
    _, children = audible.series_children(book["lang"], book["series_asin"])
    want = config.AUDIBLE_LANG[book["lang"]]
    groups: dict[int, list[dict]] = {}
    for ch in children:
        k = scoring.seq_key(ch["sequence"])
        if k and k.isdigit() and ch.get("language") == want and ch.get("content_type") in (None, "Product"):
            groups.setdefault(int(k), []).append(ch)
    return {n: audible.pick(cands) for n, cands in groups.items()}


def _audio(book: dict) -> Path:
    path = config.LIBRARY_ROOT[book["lang"]] / book["rel_path"]
    files = verify.audio_files(path) if path.is_dir() else [path] if path.is_file() else []
    if len(files) != 1:
        raise ValueError(f"Erwartet wird genau eine Audiodatei, gefunden: {len(files)}")
    return files[0]


def _by_titles(chs: list[tuple[float, float, str]], lo: int, hi: int) -> list[int] | None:
    """Erstes Kapitel jedes Bandes, wenn die Kapiteltitel die Bände lo bis hi der Reihe nach nennen."""
    first: dict[int, int] = {}
    last = lo - 1
    for i, (_, _, title) in enumerate(chs):
        nums = {int(n) for n in VOLUME.findall(title)}
        if len(nums) > 1:
            return None
        if not nums:
            continue
        n = nums.pop()
        if n < last or not lo <= n <= hi:
            return None
        if n != last:
            if n != last + 1:
                return None
            first[n] = i
            last = n
    if last != hi or first.get(lo) is None:
        return None
    first[lo] = 0  # Vorspann («Opening Credits») gehört zum ersten Band
    return [first[n] for n in range(lo, hi + 1)]


def _by_runtime(chs: list[tuple[float, float, str]], runtimes: list[int]) -> list[int] | None:
    """Erstes Kapitel jedes Bandes nach Laufzeit laut Audible; jede Grenze höchstens EXACT Sekunden daneben."""
    cuts = bundle.by_runtime([e - s for s, e, _ in chs], runtimes)
    if not cuts:
        return None
    starts = [chs[a][0] for a, _ in cuts] + [chs[-1][1]]
    if any(abs(starts[i + 1] - starts[i] - rt * 60) > EXACT for i, rt in enumerate(runtimes)):
        return None
    return [a for a, _ in cuts]


def plan(book: dict) -> dict:
    """Ändert nichts. Liefert Bandbereich, Bände laut Audible, Schnittpunkte und Begründung; wirft ValueError, wenn unsicher."""
    src = _audio(book)
    total = verify.probe(src)["duration"]
    chs = bundle._chapters(src)
    if len(chs) < 2:
        raise ValueError("Die Datei hat keine Kapitel, an denen sich schneiden liesse")
    bands = _audible_bands(book)
    span = span_of(book)
    if not span:
        # Nur ein Hinweis im Titel: Bereich aus den Kapiteltiteln, sonst ab der eigenen Bandnummer über die Gesamtlaufzeit
        nums = sorted({int(n) for _, _, t in chs for n in VOLUME.findall(t)})
        if len(nums) >= 2 and nums == list(range(nums[0], nums[-1] + 1)):
            span = (nums[0], nums[-1])
        elif (k := scoring.seq_key(book.get("sequence"))) and k.isdigit():
            n = int(k)
            fits = [hi for hi in range(n + 1, n + 7) if all(m in bands for m in range(n, hi + 1))
                    and abs(sum(bands[m]["runtime_min"] or 0 for m in range(n, hi + 1)) * 60 - total) <= max(600, total * 0.03)]
            span = (n, fits[0]) if len(fits) == 1 else None
        if not span:
            raise ValueError("Sieht nach Sammelband aus, aber welche Bände er enthält, ist nicht erkennbar")
    lo, hi = span
    absent = [n for n in range(lo, hi + 1) if n not in bands or not bands[n].get("runtime_min")]
    if absent:
        raise ValueError(f"Band {absent[0]} ist bei Audible nicht bekannt oder ohne Laufzeit")
    runtimes = [bands[n]["runtime_min"] for n in range(lo, hi + 1)]
    starts, how = _by_titles(chs, lo, hi), "nach den Kapiteltiteln"
    if starts:
        bounds = [chs[a][0] for a in starts] + [total]
        off = [abs(bounds[i + 1] - bounds[i] - rt * 60) for i, rt in enumerate(runtimes)]
        if any(o > max(600, rt * 60 * 0.05) for o, rt in zip(off, runtimes)):
            starts = None  # Titel und Laufzeiten widersprechen sich
    if not starts:
        starts, how = _by_runtime(chs, runtimes), "nach Laufzeit laut Audible"
    if not starts:
        sums = round(sum(runtimes))
        raise ValueError(f"{len(chs)} Kapitel, {round(total / 60)} min: keine sichere Grenze zu den Bänden {lo}–{hi}"
                         f" (Audible zusammen {sums} min)")
    ends = [chs[a][0] for a in starts[1:]] + [total]
    parts = [{"seq": lo + i, "asin": bands[lo + i]["asin"], "title": bands[lo + i]["title"], "runtime_min": bands[lo + i]["runtime_min"],
              "start": chs[a][0] if i else 0.0, "end": ends[i]} for i, a in enumerate(starts)]
    return {"lo": lo, "hi": hi, "how": how, "file": str(src), "parts": parts, "bands": {n: bands[n] for n in range(lo, hi + 1)}}


def _band_row(c, book: dict, part: dict, ch: dict) -> tuple[int | None, str | None]:
    """Buch-Eintrag für einen Band: vorhandenen fehlenden nehmen oder anlegen. None, wenn der Band schon einzeln da ist."""
    have = c.execute("SELECT id FROM book WHERE owned=1 AND id<>? AND ((edition_id=? AND seq=?) OR (asin IS NOT NULL AND asin=?))",
                     (book["id"], book["edition_id"], float(part["seq"]), part["asin"])).fetchone()
    if have:
        return None, "liegt schon einzeln im Bestand"
    row = c.execute("SELECT id FROM book WHERE edition_id=? AND owned=0 AND (asin=? OR seq=?)",
                    (book["edition_id"], part["asin"], float(part["seq"]))).fetchone()
    if row:
        if c.execute("SELECT 1 FROM download WHERE book_id=? AND state IN ('queued','downloading','arrived','verified','review','imported')",
                     (row["id"],)).fetchone():
            return None, "lädt schon"
        return row["id"], None
    return c.execute("INSERT INTO book(edition_id, lang, asin, title, subtitle, author, narrator, sequence, seq, release_date,"
                     " runtime_min, cover_url, owned) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)",
                     (book["edition_id"], book["lang"], ch["asin"], ch["title"], ch.get("subtitle"), ch.get("author") or book.get("author"),
                      ch.get("narrator") or book.get("narrator"), str(part["seq"]), float(part["seq"]), ch.get("release_date"),
                      ch["runtime_min"], ch.get("cover_url"))).lastrowid, None


def split(book: dict, p: dict) -> dict:
    """Schneidet je Band einen Ordner abw-<Buch>-<Zufall> in den Download-Ordner und legt Downloads an, die normal
    geprüft und abgelegt werden. Der Sammelband selbst bleibt bis dahin unverändert liegen."""
    c = db.conn()
    root = config.USENET_COMPLETE_ROOT
    src = Path(p["file"])
    covers = [f for f in sorted(src.parent.iterdir()) if f.is_file() and f.suffix.lower() in bundle.COVER][:1] if src.parent != config.LIBRARY_ROOT[book["lang"]] else []
    todo, skipped = [], []
    for part in p["parts"]:
        bid, why = _band_row(c, book, part, p["bands"][part["seq"]])
        if bid is None:
            skipped.append(f"Band {part['seq']} {why}")
        else:
            todo.append((bid, part))
    if not todo:
        return {"children": [], "skipped": skipped}
    made, children = [], []
    try:
        for bid, part in todo:
            marker = f"abw-{bid}-{secrets.token_hex(4)}"
            tmp = root / f".{marker}.abw-teil"
            tmp.mkdir()
            made.append(tmp)
            name = re.sub(r'[/\\\0<>|"?*:]', " ", part["title"]).strip(" .") or f"Band {part['seq']}"
            bundle._cut(src, tmp / f"{name}{src.suffix.lower()}", part["start"], part["end"], part["title"])
            for cov in covers:
                shutil.copyfile(cov, tmp / cov.name)
            children.append({"book_id": bid, "marker": marker, "tmp": tmp, "seq": part["seq"]})
        for ch in children:
            ch.pop("tmp").rename(root / ch["marker"])
    except Exception:
        for tmp in made:  # nur die eben angelegten Teil-Ordner entfernen
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"children": children, "skipped": skipped}


def _job(c, book: dict, state: str, message: str, parent_id: int | None = None) -> None:
    c.execute("INSERT OR REPLACE INTO libsplit_job(book_id, title, rel_path, lang, state, message, parent_id, at) VALUES(?,?,?,?,?,?,?,?)",
              (book["id"], book["title"], book["rel_path"], book["lang"], state, message[:500], parent_id, _now()))


def run_one(book: dict) -> str:
    c = db.conn()
    try:
        p = plan(book)
    except ValueError as exc:
        _job(c, book, "unsafe", str(exc))
        return "unsafe"
    except Exception as exc:  # z. B. Audible nicht erreichbar: beim nächsten Lauf neu
        _job(c, book, "unsafe", f"Fehler: {exc}"[:300])
        c.execute("UPDATE libsplit_job SET at=? WHERE book_id=?", ((datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS - 1)).isoformat(), book["id"]))
        return "error"
    try:
        res = split(book, p)
    except Exception as exc:  # Schneiden fehlgeschlagen: nichts angelegt, Sammelband unverändert
        _job(c, book, "unsafe", f"Schneiden fehlgeschlagen: {exc}"[:300])
        return "error"
    text = f"Band {p['lo']}–{p['hi']} {p['how']} geschnitten"
    if res["skipped"]:
        text += "; " + ", ".join(res["skipped"])
    if not res["children"]:
        _job(c, book, "skipped", f"Nicht aufgeteilt: {', '.join(res['skipped'])}. Der Sammelband bleibt im Bestand.")
        return "skipped"
    parent = c.execute("INSERT INTO download(book_id, source, release_title, marker, state, progress, auto, created, updated, bundle, checks)"
                       " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (book["id"], "library", f"Bestand: {book['rel_path']}", f"abw-lib-{book['id']}-{secrets.token_hex(4)}", "split", 1, 1,
                        _now(), _now(), json.dumps([p["lo"], p["hi"]]),
                        json.dumps([{"name": "Aufteilen", "status": "ok", "text": text}]))).lastrowid
    for ch in res["children"]:
        c.execute("INSERT INTO download(book_id, source, release_title, marker, state, progress, auto, created, updated, parent_id, replaces)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                  (ch["book_id"], "library", f"Aus Sammelband: {book['title']}", ch["marker"], "arrived", 1, 1, _now(), _now(), parent))
    _job(c, book, "split", text, parent)
    return "split"


def _trash(book: dict) -> str:
    """Sammelband in den Papierkorb der Bibliothek (gleiche Platte, darum nur umbenennen). Gelöscht wird nichts."""
    root = config.LIBRARY_ROOT[book["lang"]]
    path = root / book["rel_path"]
    if not path.exists():
        return "Der Sammelband liegt nicht mehr im Bestand"
    bin_dir = config.LIBRARY_TRASH / date.today().isoformat() / book["lang"] / book["rel_path"]
    if bin_dir.exists():
        raise ValueError("Im Papierkorb liegt heute schon ein Ordner dieses Sammelbands")
    bin_dir.parent.mkdir(parents=True, exist_ok=True)
    os.rename(path, bin_dir)
    return f"Sammelband in den Papierkorb verschoben ({bin_dir.relative_to(config.LIBRARY_TRASH)})"


def finish(progress=lambda msg: None) -> int:
    """Aufgeteilte Sammelbände abschliessen: alle Bände abgelegt → Original in den Papierkorb; einer gescheitert → Original bleibt."""
    c = db.conn()
    moved = 0
    for j in c.execute("SELECT * FROM libsplit_job WHERE state='split'").fetchall():
        kids = [r["state"] for r in c.execute("SELECT state FROM download WHERE parent_id=?", (j["parent_id"],))]
        if any(s in ("queued", "downloading", "arrived", "verified", "review", "imported") for s in kids):
            continue
        book = {"id": j["book_id"], "title": j["title"], "rel_path": j["rel_path"], "lang": j["lang"]}
        if kids and all(s == "done" for s in kids):
            progress(f"Legt den Sammelband «{j['title'][:60]}» in den Papierkorb")
            try:
                text = _trash(book)
            except Exception as exc:
                c.execute("UPDATE libsplit_job SET message=?, at=? WHERE book_id=?", (f"Alle Bände abgelegt, Papierkorb nicht möglich: {exc}"[:500], _now(), j["book_id"]))
                continue
            c.execute("UPDATE libsplit_job SET state='done', message=?, at=? WHERE book_id=?", (f"{j['message']}. Alle Bände abgelegt. {text}"[:500], _now(), j["book_id"]))
            moved += 1
            try:
                from . import abs as abs_mod
                abs_mod.scan(j["lang"])
            except Exception:
                pass
        else:
            bad = sum(s not in ("done",) for s in kids)
            c.execute("UPDATE libsplit_job SET state='partial', message=?, at=? WHERE book_id=?",
                      (f"{j['message']}. {bad} von {len(kids)} Bänden nicht abgelegt (Prüfung durchgefallen oder verworfen); der Sammelband bleibt im Bestand."[:500],
                       _now(), j["book_id"]))
    return moved


def auto(progress=lambda msg: None) -> str:
    """Zyklische Aufgabe: abschliessen, dann höchstens drei neue Sammelbände planen und, wenn sicher, aufteilen."""
    moved = finish(progress)
    todo = candidates()
    res = {"split": 0, "unsafe": 0, "skipped": 0, "error": 0}
    for book in todo[:3]:
        progress(f"Prüft den Sammelband «{book['title'][:60]}»")
        res[run_one(book)] += 1
    text = f"{res['split']} aufgeteilt, {res['unsafe'] + res['error']} unsicher, {res['skipped']} übersprungen, {max(0, len(todo) - 3)} offen"
    return text + (f", {moved} Sammelbände in den Papierkorb" if moved else "")


def overview() -> list[dict]:
    c = db.conn()
    out = []
    for j in c.execute("SELECT j.*, b.abs_item_id, b.cover_url FROM libsplit_job j LEFT JOIN book b ON b.id=j.book_id ORDER BY j.at DESC").fetchall():
        kids = [dict(r) for r in c.execute("SELECT d.id, d.state, b.title, b.sequence FROM download d JOIN book b ON b.id=d.book_id WHERE d.parent_id=?"
                                           " ORDER BY b.seq", (j["parent_id"],))] if j["parent_id"] else []
        out.append({**dict(j), "children": kids})
    return out
