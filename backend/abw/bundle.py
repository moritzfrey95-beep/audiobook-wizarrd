"""Sammelband aufteilen: ein Download mit mehreren Bänden wird zu einem Ordner je Band.

Zuordnung in dieser Reihenfolge: 1) Bandnummer im Ordner- oder Dateinamen, 2) Laufzeiten von Audible entlang der
Dateien oder Kapitel. Passt nichts sicher, wird nicht aufgeteilt. Die Quelle bleibt unverändert liegen.
"""
import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from . import db, scoring, verify

COVER = {".jpg", ".jpeg", ".png"}


def _natural(path: Path) -> list:
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", str(path))]


def _chapters(path: Path) -> list[tuple[float, float, str]]:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", str(path)],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        return []
    return [(float(ch["start_time"]), float(ch["end_time"]), (ch.get("tags") or {}).get("title") or "")
            for ch in json.loads(out.stdout).get("chapters", [])]


def chapters(path: Path) -> list[tuple[float, float]]:
    return [(s, e) for s, e, _ in _chapters(path)]


def bands(book: dict, lo: int, hi: int, strict: bool = True) -> list[dict]:
    """Die Bände lo bis hi derselben Fassung, einer je Nummer. Fehlt einer im Katalog, lässt sich nicht aufteilen."""
    rows = db.conn().execute("SELECT b.*, e.name series FROM book b JOIN edition e ON e.id=b.edition_id WHERE b.edition_id=?"
                             " ORDER BY b.id=? DESC, b.owned DESC, b.id", (book["edition_id"], book["id"])).fetchall()
    by_seq: dict[int, dict] = {}
    for r in rows:
        k = scoring.seq_key(r["sequence"])
        if k and k.isdigit():
            by_seq.setdefault(int(k), dict(r))
    missing = [n for n in range(lo, hi + 1) if n not in by_seq]
    if missing and strict:
        raise ValueError(f"Band {missing[0]} ist im Audible-Katalog dieser Serie nicht bekannt")
    return [by_seq[n] for n in range(lo, hi + 1) if n in by_seq]


def _band_in(text: str, series: str, lo: int, hi: int) -> int | None:
    t = re.sub(r"\d+\s*(?:-|to|bis|&)\s*\d+", " ", text.replace("–", "-"))  # die Angabe "14-16" selbst zählt nicht
    nums = {int(n) for n in scoring.numbers(t, series) if n.isdigit() and lo <= int(n) <= hi}
    return nums.pop() if len(nums) == 1 else None


def by_name(src: Path, files: list[Path], series: str, lo: int, hi: int) -> dict[int, list[Path]] | None:
    """Jede Datei trägt (im Ordner oder im Namen) genau eine Bandnummer, und jeder Band hat Dateien."""
    groups: dict[int, list[Path]] = {}
    for f in files:
        rel = f.relative_to(src)
        parts = [*rel.parts[:-1], rel.stem]  # äusserster Ordner zuerst: "Book 1/01.mp3" gehört zu Band 1
        n = next((b for part in parts if (b := _band_in(part, series, lo, hi)) is not None), None)
        if n is None:
            return None
        groups.setdefault(n, []).append(f)
    return groups if set(groups) == set(range(lo, hi + 1)) else None


def by_runtime(units: list[float], runtimes: list[int]) -> list[tuple[int, int]] | None:
    """units: Dauer der Dateien oder Kapitel in Sekunden. Liefert je Band (erste, letzte+1) Einheit.
    Die Grenze liegt dort, wo die aufsummierte Dauer der Laufzeit laut Audible am nächsten kommt."""
    if len(units) < len(runtimes) or not all(runtimes):
        return None
    starts = [0.0]
    for u in units:
        starts.append(starts[-1] + u)
    cuts, pos = [], 0
    for want in runtimes[:-1]:
        target = starts[pos] + want * 60
        nxt = min(range(pos + 1, len(units)), key=lambda i: abs(starts[i] - target), default=None)
        if nxt is None or abs(starts[nxt] - target) > max(600, want * 60 * 0.05):
            return None
        cuts.append((pos, nxt))
        pos = nxt
    if abs(starts[-1] - starts[pos] - runtimes[-1] * 60) > max(600, runtimes[-1] * 60 * 0.05):
        return None
    return cuts + [(pos, len(units))]


def guess_span(book: dict, seconds: float, release: str = "") -> tuple[int, int] | None:
    """Als einzelner Band geladen, aber viel länger: enthält der Download aufeinanderfolgende Bände der Serie?
    Liefert den Bereich mit dem gesuchten Band, dessen Laufzeiten laut Audible zusammen zur Dauer passen
    (höchstens 10 min oder 3 % daneben), aber nur, wenn genau ein Bereich passt. Passen mehrere (Band 1 und 9 fast
    gleich lang: 1–8 und 2–9), entscheidet der Release-Name, wenn er genau eines der Bereichsenden nennt."""
    want = scoring.seq_key(book.get("sequence"))
    rts = {int(k): v for k, v in (book.get("seq_runtimes") or {}).items()}
    if not (want and want.isdigit() and book.get("runtime_min")) or seconds < book["runtime_min"] * 60 * 1.5:
        return None
    n, fits = int(want), []
    for lo in range(max(1, n - 10), n + 1):
        for hi in range(max(lo + 1, n), lo + 21):
            if not all(m in rts for m in range(lo, hi + 1)):
                break  # Lücke im Katalog: ein grösserer Bereich hätte sie auch
            if abs(sum(rts[m] for m in range(lo, hi + 1)) * 60 - seconds) <= max(600, seconds * 0.03):
                fits.append((lo, hi))
    if len(fits) > 1 and release:
        named = {n.lstrip("0") for n in re.findall(r"\d+", scoring.plain(release))}
        fits = [f for f in fits if {str(f[0]), str(f[1])} <= named]
    return fits[0] if len(fits) == 1 else None


def catalog(book: dict) -> list[dict]:
    """Alle Bände derselben Fassung (auch Zusatzbände wie 1.5), für die Zuordnung nach Titel."""
    return [dict(r) for r in db.conn().execute("SELECT b.*, e.name series FROM book b JOIN edition e ON e.id=b.edition_id WHERE b.edition_id=?"
                                               " ORDER BY b.seq", (book["edition_id"],))]


def fits(book: dict, seconds: float) -> bool:
    """Dauer passt zur Laufzeit laut Audible (höchstens 10 min oder 5 % daneben); ohne Laufzeit nicht prüfbar."""
    rt = (book.get("runtime_min") or 0) * 60
    return not rt or abs(seconds - rt) <= max(600, rt * 0.05)


def by_title(src: Path, files: list[Path], books: list[dict]) -> tuple[dict[int, list[Path]], list[str]] | None:
    """Jede Datei trägt (im Ordner- oder Dateinamen) den Titel genau eines Bandes. Für Sammelpacks, deren eigene
    Nummerierung nicht zu Audible passt («BAND_01» = Audible-Band 4). Ein ganzer Ordner ohne bekannten Titel
    (Buch, das Audible in der Serie nicht führt) wird ausgelassen; liefert (Gruppen, ausgelassene Ordner)."""
    # Zusätze nach «:», «(» oder «[» zählen nicht («Die Gefahren einer Hauptstadt [The Dangers of a Capital]»)
    titles = [(b, scoring.significant(re.split(r"[:(\[]", b["title"])[0])) for b in books]
    titles = [(b, t) for b, t in titles if t]
    groups: dict[int, list[Path]] = {}
    known, unknown = set(), set()
    # Gemeinsame Hülle-Ordner zählen nicht als Ordner eines Buches («Realm of the Elderlings (Robin Hobb)/01 Novels/…» neben
    # «…/02 Short Pieces/…»): sonst gelten Romane und Kurzgeschichten als ein Ordner mit bekannten und unbekannten Dateien
    base = src / Path(os.path.commonpath([f.parent.relative_to(src) for f in files]))
    for f in files:
        rel = f.relative_to(base)
        top = rel.parts[0] if len(rel.parts) > 1 else None
        full = f.relative_to(src)
        have = set(scoring.words(" ".join([*full.parts[:-1], full.stem])))
        hits = [(len(t), b["id"]) for b, t in titles if t <= have]
        if not hits:
            if top is None:
                return None
            unknown.add(top)
            continue
        best = max(hits)
        if [h for h in hits if h[0] == best[0] and h[1] != best[1]]:
            return None  # zwei Bände passen gleich gut
        known.add(top)
        groups.setdefault(best[1], []).append(f)
    if known & unknown:
        return None  # ein Ordner mit bekannten und unbekannten Dateien: Zuordnung unklar
    return (groups, sorted(unknown)) if len(groups) >= 2 else None


def plan(src: Path, wanted: list[dict], lo: int, hi: int, books: list[dict] | None = None, titles_only: bool = False) -> dict:
    """Ändert nichts. Liefert {'how': Text, 'books': [Band], 'parts': {Buch-ID: [(Datei, Start, Ende)]}, 'misfit': [Buch-ID]};
    Start/Ende None = ganze Datei. Zuordnungen nach Nummer oder Laufzeit gelten nur, wenn jede Gruppe zur Laufzeit laut Audible passt.
    Nach Titel gilt das auch, mit einer Ausnahme (Anlass Robin Hobb «Realm of the Elderlings»): passt kein
    anderer Weg, aber ist jeder Titel eindeutig und passt mindestens ein Band, wird trotzdem aufgeteilt. Die Bände mit unpassender
    Laufzeit stehen in 'misfit' und kommen einzeln unter «Entscheiden», statt den ganzen Sammelband zu blockieren."""
    files = sorted(verify.audio_files(src), key=_natural) if src.is_dir() else []
    if not files:
        raise ValueError("Keine Audiodateien im Download")
    seconds: dict[Path, float] = {}
    dur = lambda fs: sum(seconds.setdefault(f, verify.probe(f)["duration"]) for f in fs)
    books = books or wanted
    by_id = {b["id"]: b for b in books}
    titled = by_title(src, files, books) if len(files) > 1 else None
    partial = None
    if titled:
        groups, unknown = titled
        chosen = [b for b in books if b["id"] in groups]
        misfit = [b["id"] for b in chosen if not fits(b, dur(groups[b["id"]]))]
        how = "nach Titel im Datei- oder Ordnernamen"
        if unknown:
            how += f"; nicht im Audible-Katalog der Serie, bleibt im Sammelband: {', '.join(unknown)[:200]}"
        res = {"how": how, "books": chosen, "parts": {b["id"]: [(f, None, None) for f in groups[b["id"]]] for b in chosen}, "misfit": misfit}
        if not misfit:
            return res
        if len(misfit) < len(chosen):
            partial = res
    try:
        return _plan_strict(src, files, wanted, lo, hi, books, titled, titles_only, dur)
    except ValueError:
        if not partial:
            raise
    seqs = ", ".join(str(scoring.seq_key(by_id[i]["sequence"]) or by_id[i]["title"]) for i in partial["misfit"])
    partial["how"] += f"; Laufzeit passt nicht zu Audible, einzeln unter «Entscheiden»: Band {seqs}"
    return partial


def _plan_strict(src: Path, files: list[Path], wanted: list[dict], lo: int, hi: int, books: list[dict], titled, titles_only: bool, dur) -> dict:
    """Aufteilen nach Nummer, Kapiteln oder Laufzeit; jeder Band muss zur Laufzeit laut Audible passen."""
    series = wanted[0].get("series") or ""
    whole = lambda b: [(f, None, None) for f in groups[b["id"]]]
    if titles_only:
        raise ValueError("Die Dateien tragen keine Bandtitel, die zur Laufzeit laut Audible passen; nach Nummer oder Laufzeit lässt sich bei "
                         "Lücken im Katalog nicht sicher aufteilen")
    named = by_name(src, files, series, lo, hi)
    if named:
        groups = {wanted[n - lo]["id"]: fs for n, fs in named.items()}
        if all(fits(b, dur(groups[b["id"]])) for b in wanted):
            return {"how": "nach Bandnummer im Datei- oder Ordnernamen", "books": wanted, "parts": {b["id"]: whole(b) for b in wanted}}
    runtimes = [b.get("runtime_min") or 0 for b in wanted]
    if not all(runtimes):
        raise ValueError("Die Dateinamen nennen keine passenden Bandnummern oder Titel, und bei Audible fehlt die Laufzeit eines Bandes")
    if len(files) == 1:
        chs = chapters(files[0])
        cuts = by_runtime([e - s for s, e in chs], runtimes)
        if not cuts:
            raise ValueError(f"Eine einzige Datei mit {len(chs)} Kapiteln: die Kapitelgrenzen passen nicht zu den Laufzeiten der Bände {lo}–{hi}")
        return {"how": "an den Kapitelgrenzen nach Laufzeit laut Audible", "books": wanted,
                "parts": {wanted[i]["id"]: [(files[0], chs[a][0], chs[b - 1][1])] for i, (a, b) in enumerate(cuts)}}
    tops = {f.relative_to(src).parts[0] for f in files if len(f.relative_to(src).parts) > 1}
    if len(tops) >= 2:
        # Ein Ordner je Buch: nur an Ordnergrenzen schneiden. Quer über die Ordner nach Laufzeit zu schneiden legte am 17.11.
        # Teile von «Origin» und «Inferno» als «The Lost Symbol» ab, weil die Ordner alphabetisch statt nach Band sortiert waren.
        folders: dict[str, list[Path]] = {}
        for f in files:
            rel = f.relative_to(src)
            folders.setdefault(rel.parts[0] if len(rel.parts) > 1 else "", []).append(f)
        units = list(folders.values())
        cuts = by_runtime([dur(fs) for fs in units], runtimes)
        if not cuts:
            raise ValueError(f"{len(units)} Ordner. Weder Bandnummer noch Titel in den Ordnernamen passen sicher, und die Laufzeiten der Ordner "
                             f"passen in ihrer Reihenfolge nicht zu den Bänden {lo}–{hi}")
        # Bände einer Serie sind oft fast gleich lang: Nennt ein Ordner den Titel eines anderen Bandes, ist die Reihenfolge falsch
        heads = [(b, scoring.significant(re.split(r"[:(\[]", b["title"])[0])) for b in books]
        names = list(folders)
        for i, (a, z) in enumerate(cuts):
            for name in names[a:z]:
                have = set(scoring.words(name))
                clash = next((b for b, h in heads if h and h <= have and b["id"] != wanted[i]["id"]), None)
                if clash:
                    raise ValueError(f"Nach Laufzeit käme der Ordner «{name[:60]}» zu Band {wanted[i].get('sequence')}, er nennt aber "
                                     f"«{clash['title'][:60]}». Nicht aufgeteilt.")
        return {"how": "nach Laufzeit laut Audible entlang der Ordner", "books": wanted,
                "parts": {wanted[i]["id"]: [(f, None, None) for fs in units[a:b] for f in fs] for i, (a, b) in enumerate(cuts)}}
    cuts = by_runtime([dur([f]) for f in files], runtimes)
    if not cuts:
        why = "Bandnummern oder Titel in den Namen passen nicht zu den Laufzeiten laut Audible, und auch" if named or titled else "Ohne Bandnummer oder Titel im Namen:"
        raise ValueError(f"{len(files)} Dateien. {why} die Laufzeiten der Dateien passen nicht zu den Bänden {lo}–{hi}")
    return {"how": "nach Laufzeit laut Audible entlang der Dateien", "books": wanted,
            "parts": {wanted[i]["id"]: [(f, None, None) for f in files[a:b]] for i, (a, b) in enumerate(cuts)}}


def _cut(src: Path, dst: Path, start: float, end: float, title: str) -> None:
    """Schneidet ohne Neukodierung und schreibt die Kapitel dieses Bandes neu, beginnend bei null."""
    esc = lambda t: re.sub(r"([=;#\\\n])", r"\\\1", t)
    meta = ";FFMETADATA1\n" + "".join(
        f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={round((s - start) * 1000)}\nEND={round((e - start) * 1000)}\ntitle={esc(t)}\n"
        for s, e, t in _chapters(src) if s >= start - 0.5 and e <= end + 0.5)
    note = dst.with_name(dst.name + ".kapitel.txt")
    note.write_text(meta, encoding="utf-8")
    try:
        out = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-i", str(note),
                              "-map", "0:a", "-map", "0:v?", "-c", "copy", "-map_metadata", "0", "-map_chapters", "1",
                              "-metadata", f"title={title}", "-metadata", f"album={title}", str(dst)],
                             capture_output=True, text=True, timeout=3600)
    finally:
        note.unlink(missing_ok=True)
    if out.returncode != 0:
        raise IOError(f"Schneiden fehlgeschlagen: {out.stderr.strip()[:200]}")
    got = verify.probe(dst)["duration"]
    if abs(got - (end - start)) > 5:
        raise IOError(f"Geschnittener Teil ist {round(got / 60)} statt {round((end - start) / 60)} Minuten lang")


def split(download: dict, book: dict, root: Path) -> dict:
    """Legt je fehlendem Band einen eigenen Ordner abw-<Buch>-<Zufall> neben dem Download an.
    Liefert {'how', 'children': [{'book', 'marker'}], 'skipped': [Bandnummern, die schon unterwegs sind]}.
    Bände, die schon im Bestand liegen, werden ebenfalls herausgelöst (replaces=True) und später mit dem Bestand verglichen."""
    lo, hi = json.loads(download["bundle"])
    src = root / download["marker"]
    wanted = bands(book, lo, hi, strict=False)
    gaps = len(wanted) < hi - lo + 1  # Lücke im Katalog: nur nach Titel aufteilen, nie nach Nummer oder Laufzeit
    p = plan(src, wanted, lo, hi, catalog(book), titles_only=gaps)
    c = db.conn()
    covers = [f for f in sorted(src.rglob("*")) if f.is_file() and f.suffix.lower() in COVER][:1]
    made: list[Path] = []
    children, skipped = [], []
    try:
        for b in p["books"]:
            n = scoring.seq_key(b["sequence"]) or b["sequence"] or b["title"]
            parts = p["parts"][b["id"]]
            misfit = b["id"] in p.get("misfit", ())
            if not b["owned"]:
                # Dasselbe Hörbuch liegt unter einer anderen Serie im Bestand (gleiche ASIN): mit diesem vergleichen, nicht neu ablegen
                twin = c.execute("SELECT o.*, e.name series FROM book_twin t JOIN book o ON o.id=t.twin_id LEFT JOIN edition e ON e.id=o.edition_id"
                                 " WHERE t.id=? AND o.owned=1 AND o.rel_path IS NOT NULL LIMIT 1", (b["id"],)).fetchone()
                if twin:
                    b = dict(twin)
            busy = c.execute("SELECT 1 FROM download WHERE book_id=? AND id<>? AND state IN ('queued','downloading','arrived','verified','review','imported')",
                             (b["id"], download["id"])).fetchone()
            if busy or (b["owned"] and not b["rel_path"]):
                skipped.append(n)
                continue
            marker = f"abw-{b['id']}-{secrets.token_hex(4)}"
            tmp = root / f".{marker}.abw-teil"
            tmp.mkdir()
            made.append(tmp)
            for i, (f, start, end) in enumerate(parts, 1):
                if start is None:
                    dst = tmp / f.name
                    if dst.exists():
                        dst = tmp / f"{i:03d} - {f.name}"
                    shutil.copyfile(f, dst)
                    if dst.stat().st_size != f.stat().st_size:
                        raise IOError(f"Kopie von {f.name} ist unvollständig")
                else:
                    name = re.sub(r'[/\\\0<>|"?*:]', " ", b["title"]).strip(" .") or f"Band {n}"
                    _cut(f, tmp / f"{name}{f.suffix.lower()}", start, end, b["title"])
            for cov in covers:
                if not (tmp / cov.name).exists():
                    shutil.copyfile(cov, tmp / cov.name)
            children.append({"book": b, "marker": marker, "tmp": tmp, "replaces": bool(b["owned"]), "misfit": misfit})
        if not children:
            raise ValueError("Alle Bände dieses Sammelbands sind schon unterwegs")
        for ch in children:
            ch.pop("tmp").rename(root / ch["marker"])
    except Exception:
        for tmp in made:  # nur die eigenen, eben angelegten Teil-Ordner wieder entfernen
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"how": p["how"], "children": children, "skipped": skipped}
