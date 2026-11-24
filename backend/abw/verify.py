"""Ankunftsprüfung: Ist das heruntergeladene Hörbuch das richtige, vollständig und in der richtigen Sprache?"""
import json
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, scoring

PARALLEL = 6  # gleichzeitige ffprobe-Aufrufe
FILE_PART = re.compile(r"[(\[]\s*(?:teil|part|track|cd|disc|kapitel|chapter)\s*\d+\s*[)\]]", re.I)
FILLER = {"folge", "band", "buch", "teil", "book", "volume", "vol", "part", "episode", "chapter", "kapitel"}

AUDIO = {".m4b", ".m4a", ".mp3", ".flac", ".ogg", ".opus", ".aac", ".wma", ".mp4"}
LEFTOVER = {".rar", ".par2", ".zip", ".7z", ".r00", ".001"}
LANG_NAME = {"en": "Englisch", "de": "Deutsch"}
_model = None
_model_lock = threading.Lock()


def audio_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in AUDIO)


def want_minutes(book: dict) -> tuple[int, str]:
    """Soll-Laufzeit und ihre Quelle. Bei Büchern im Bestand ist `runtime_min` die Dauer aus Audiobookshelf (ein doppelter
    Ordner verdoppelt sie), daher zuerst die Laufzeit laut Audible. Fehlt sie, gilt der Bestand und heisst auch so."""
    if book.get("audible_min"):
        return book["audible_min"], "Audible"
    return book.get("runtime_min") or 0, "Bestand" if book.get("owned") else "Audible"


FORMAT = {".m4b": "M4B", ".m4a": "M4B", ".aac": "M4B", ".mp4": "M4B", ".mp3": "MP3", ".flac": "FLAC",
          ".ogg": "OGG", ".opus": "OGG", ".wma": "WMA"}


def duplicate_sets(files: list[Path], seconds=None) -> dict | None:
    """Liegt dasselbe Buch in zwei Formaten im Ordner (z. B. 3 M4B und 28 MP3, beide 15 h)? Dann zählt nur eine Fassung,
    bevorzugt M4B. Liefert {'keep', 'drop', 'text'} oder None. seconds: Datei -> Dauer."""
    seconds = seconds or (lambda f: probe(f)["duration"])
    groups: dict[str, list[Path]] = {}
    for f in files:
        groups.setdefault(FORMAT.get(f.suffix.lower(), f.suffix.lower()), []).append(f)
    if len(groups) < 2:
        return None
    dur = {}
    for k, fs in groups.items():
        try:
            dur[k] = sum(seconds(f) for f in fs)
        except Exception:
            return None  # unlesbare Datei: das meldet die Prüfung selbst
    keep = sorted(groups, key=lambda k: (k != "M4B", -dur[k]))[0]
    twins = [k for k in groups if k != keep and dur[keep] >= 1800 and abs(dur[k] - dur[keep]) <= max(300, dur[keep] * 0.05)]
    if not twins:
        return None
    hm = lambda s: f"{round(s / 60) // 60} h {round(s / 60) % 60:02d} min"
    parts = " und ".join(f"{len(groups[k])} {k} ({hm(dur[k])})" for k in [keep, *twins])
    return {"keep": [f for k, fs in groups.items() if k not in twins for f in fs],
            "drop": [f for k in twins for f in groups[k]],
            "text": f"Das Buch liegt doppelt im Ordner: {parts}. Gezählt und abgelegt wird nur {keep}."}


_probed: dict[tuple, dict] = {}  # (Pfad, Grösse, Änderungszeit) -> Ergebnis; Sammelband-Erkennung und Prüfung messen jede Datei nur einmal


def probe(path: Path) -> dict:
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _probed:
        if len(_probed) > 20000:
            _probed.clear()
        _probed[key] = _probe(path)
    return _probed[key]


def _probe(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise ValueError(out.stderr.strip()[:200] or "nicht lesbar")
    fmt = json.loads(out.stdout).get("format", {})
    return {"duration": float(fmt.get("duration") or 0), "tags": {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}}


def decodes(path: Path, from_end: bool) -> bool:
    """30 Sekunden vom Anfang oder Ende dekodieren: findet abgeschnittene und defekte Dateien."""
    pos = ["-sseof", "-30"] if from_end else ["-t", "30"]
    out = subprocess.run(["ffmpeg", "-v", "error", "-nostats", "-progress", "pipe:1", *pos, "-i", str(path), "-f", "null", "-"],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0:
        return False
    if "Error" not in out.stderr:
        return True
    return from_end and _last_frame_only(out.stdout, out.stderr, path)


def _last_frame_only(progress: str, errors: str, path: Path) -> bool:
    """Nur ein unvollständiger Rest-Frame ganz am Ende (oft vor dem ID3v1-Tag): kein abgeschnittener Download.
    Die drei ??? (VOLDiES, OMA, MOD) wurden deswegen verworfen, obwohl bis auf 0,03 s alles dekodierte (21.11.)."""
    lines = [l for l in errors.splitlines() if l.strip()]
    if not lines or len(lines) > 2 or not all("Header missing" in l or "Invalid data found" in l for l in lines):
        return False
    done = [int(v) for k, _, v in (l.partition("=") for l in progress.splitlines()) if k == "out_time_us" and v.isdigit()]
    try:
        whole = probe(path)["duration"]
    except Exception:
        return False
    return bool(done) and done[-1] / 1e6 >= min(30.0, whole) - 0.5


def speech_available() -> bool:
    """Spracherkennung ist optional: das schlanke Image kommt ohne faster-whisper."""
    import importlib.util
    return importlib.util.find_spec("faster_whisper") is not None


def intro(path: Path, seconds: int = 75) -> dict:
    """Gesprochene Ansage am Anfang erkennen: Sprache und Wortlaut."""
    global _model
    wav = Path("/tmp") / f"abw-intro-{threading.get_ident()}.wav"
    try:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", str(seconds), "-i", str(path), "-ac", "1", "-ar", "16000", str(wav)],
                       check=True, capture_output=True, timeout=300)
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                _model = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=4,
                                      download_root=config.WHISPER_CACHE or None)
            import wave

            import numpy as np
            with wave.open(str(wav), "rb") as w:  # roh einlesen, ohne den Umweg über PyAV
                samples = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            segments, info = _model.transcribe(samples, beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments)
        return {"language": info.language, "probability": info.language_probability, "text": text}
    finally:
        wav.unlink(missing_ok=True)


def verify(book: dict, folder: Path, speech: bool = True, release: str = "", siblings: list[str] = ()) -> dict:
    """Liefert {'verdict': passed|review|rejected, 'checks': [{name, status ok|warn|fail, text}], 'files': n}."""
    checks: list[dict] = []
    add = lambda name, status, text: checks.append({"name": name, "status": status, "text": text})
    if not folder.is_dir():
        add("Dateien", "fail", "Der Download-Ordner ist nicht vorhanden")
        return _result(checks, 0)
    files = audio_files(folder)
    if not files:
        add("Dateien", "fail", "Keine Audiodateien im Download")
        return _result(checks, 0)
    if any(p.suffix.lower() in LEFTOVER for p in folder.rglob("*") if p.is_file()):
        add("Dateien", "warn", "Im Ordner liegen noch Archivteile, der Download ist vielleicht nicht vollständig entpackt")

    total, tags, broken = 0.0, {}, []

    def _try(f: Path):
        try:
            return probe(f)
        except Exception as exc:
            return exc
    # Das NAS antwortet langsam auf einzelne Zugriffe: parallel ist ffprobe bei vielen Dateien rund fünfmal schneller (18.11.)
    with ThreadPoolExecutor(PARALLEL) as ex:
        for f, info in zip(files, ex.map(_try, files)):
            if isinstance(info, Exception):
                broken.append(f"{f.name}: {info}")
            else:
                total += info["duration"]
                tags = tags or info["tags"]
    if broken:
        add("Dateien", "fail", f"{len(broken)} von {len(files)} Dateien sind nicht lesbar ({broken[0][:120]})")
    elif not decodes(files[0], False) or not decodes(files[-1], True):
        add("Dateien", "fail", "Anfang oder Ende lässt sich nicht abspielen, die Datei ist beschädigt oder abgeschnitten")
    elif holes := _holes(files):
        add("Dateien", "fail", f"{holes} enthält Lücken aus Null-Bytes, der Download ist unvollständig")
    else:
        add("Dateien", "ok", f"{len(files)} {'Datei' if len(files) == 1 else 'Dateien'}, alle lesbar")
    if not broken and (dup := duplicate_sets(files)):
        total = sum(probe(f)["duration"] for f in dup["keep"])
        add("Doppelt", "warn", dup["text"])

    minutes, (want, source) = round(total / 60), want_minutes(book)
    if want:
        from . import diagnose
        f = diagnose.runtime_finding(minutes, want, release, list((book.get("seq_runtimes") or {}).values()), source)
        checks.append({"name": "Laufzeit", "status": f["status"], "text": f["text"], "kind": f["kind"], "volumes": f["volumes"]})
    else:
        add("Laufzeit", "warn", f"{minutes // 60} h {minutes % 60:02d} min, bei Audible ist keine Laufzeit hinterlegt")

    tagged = " ".join(tags.get(k, "") for k in ("title", "album", "artist", "album_artist"))
    import re
    title = scoring.significant(re.split(r"[:(]| - ", book["title"])[0])
    surnames = {w[-1] for a in (book.get("author") or "").split(",") if (w := scoring.words(a))}
    have = set(scoring.words(tagged))
    want = scoring.seq_key(book.get("sequence"))
    # Nummer, die zum Titel gehört («Dungeon Diving 103» ist Band 3), ist kein Widerspruch
    from . import variants
    tags = {**tags, **{k: variants.PART.sub(" ", tags.get(k, "")) for k in ("title", "album")}}  # «(1 of 3)» ist keine Bandnummer
    if len(files) > 1:  # «173 - Dämon der Rache (Teil 01)» im Titel der ersten Datei ist der Dateiteil, nicht Band 1 (21.11.)
        tags["title"] = FILE_PART.sub(" ", tags.get("title", ""))
    tag_nums = scoring.numbers(" ".join(tags.get(k, "") for k in ("title", "album")), book.get("series") or "") \
        - scoring.title_numbers(book["title"], book.get("series") or "")
    tag_span = scoring.span(" ".join(tags.get(k, "") for k in ("title", "album")), book.get("series") or "")
    if tag_span and want and want.isdigit() and tag_span[0] <= int(want) <= tag_span[1]:
        tag_nums = set()  # Datei aus einem Sammelband: "Serie 14-16" in den Tags ist kein Widerspruch
    # Titel eines anderen Bandes der Serie in Titel/Album, der eigene fehlt («Origin (Disc 02)» im Ordner von «The Lost Symbol», 17.11.)
    # Klammern zählen nicht: «(Das Große Spiel 6)» ist die Serie, nicht der gleichnamige Band 1
    named_tags = set(scoring.words(re.sub(r"[(\[][^)\]]*[)\]]", " ", " ".join(tags.get(k, "") for k in ("title", "album")))))
    plain_own = title | scoring.significant(book.get("series") or "")
    # ganzer Titel ohne Klammern und Füllwörter: «Folge 240: und die schwarze Rose» ist nicht bloss «Folge»
    key = lambda s: scoring.significant(re.sub(r"\([^)]*\)", " ", s)) - FILLER
    other = next((s for s in siblings if (h := key(s)) and not h <= plain_own and h <= named_tags), None)
    if want and tag_nums and want not in tag_nums:
        add("Tags", "fail", f"Die Datei nennt Band {', '.join(sorted(tag_nums))}, gesucht ist Band {want} ({(tags.get('album') or tags.get('title') or '')[:60]})")
        checks[-1]["kind"] = "band"
    elif other and scoring.coverage(title, named_tags) < 0.8:
        add("Tags", "fail", f"Die Tags nennen einen anderen Band der Serie («{other[:60]}»): {(tags.get('album') or tags.get('title') or '')[:60]}")
    elif not tagged.strip():
        add("Tags", "warn", "Die Datei trägt weder Titel noch Autor")
    elif scoring.coverage(title, have) >= 0.8 and (not surnames or surnames & have):
        add("Tags", "ok", f"Titel und Autor in den Tags passen ({(tags.get('album') or tags.get('title') or '')[:60]})")
    elif scoring.coverage(title, have) >= 0.8 or surnames & have:
        add("Tags", "warn", f"Tags passen nur teilweise: {tagged.strip()[:100]}")
    else:
        add("Tags", "warn", f"Tags nennen etwas anderes: {tagged.strip()[:100]}")

    heard_text = ""
    if speech and not broken and not speech_available():
        add("Ansage", "ok", "Spracherkennung nicht installiert, Ansage nicht geprüft")
    elif speech and not broken:
        try:
            sp = intro(files[0])
            heard_text = sp["text"]
            lang, wanted = sp["language"], book["lang"]
            heard = set(scoring.words(sp["text"]))
            named = scoring.coverage(title, heard) >= 0.6 or bool(surnames & heard)
            quote = sp["text"].strip()[:140]
            if lang != wanted and sp["probability"] >= 0.8:
                add("Ansage", "fail", f"Gesprochen wird {LANG_NAME.get(lang, lang)}, gesucht ist {LANG_NAME[wanted]}. Gehört: «{quote}»")
            elif lang != wanted:
                add("Ansage", "warn", f"Sprache unsicher erkannt ({LANG_NAME.get(lang, lang)}). Gehört: «{quote}»")
            elif named:
                add("Ansage", "ok", f"{LANG_NAME[wanted]}, Titel oder Autor werden genannt. Gehört: «{quote}»")
            else:
                add("Ansage", "ok", f"{LANG_NAME[wanted]}; Titel wird in den ersten 75 Sekunden nicht genannt. Gehört: «{quote}»")
        except Exception as exc:
            add("Ansage", "warn", f"Spracherkennung nicht möglich: {str(exc)[:120]}")
    # Falscher Band im Tag, aber Ansage nennt den gesuchten Band und die Laufzeit passt: der Tag irrt
    # («Judicator Jane 5» mit Album-Tag «Judicator Jane 4», Ansage «Book 5», Laufzeit auf die Minute, 20.11.)
    tag_fail = next((c for c in checks if c["name"] == "Tags" and c.get("kind") == "band"), None)
    runtime_ok = any(c["name"] == "Laufzeit" and c["status"] == "ok" for c in checks)
    if tag_fail and runtime_ok and heard_text:
        spoken = spoken_numbers(heard_text, book.get("series") or "")
        if want in spoken and not spoken & tag_nums:
            tag_fail["status"] = "warn"
            tag_fail["text"] += f". Die Ansage nennt aber Band {want} und die Laufzeit passt, der Tag ist falsch"
    # Sprecher aus Ansage, Tags und Releasename gegen die gesuchte Fassung bzw. den Audible-Sprecher
    from . import variants
    tagged_narr = " ".join(tags.get(k, "") for k in ("artist", "album_artist", "composer", "comment", "description", "narrator", "performer"))
    if (nf := variants.narrator_check(book, [("Ansage", heard_text), ("Tags", tagged_narr), ("Releasename", release)])):
        add("Sprecher", nf["status"], nf["text"])
    return _result(checks, len(files), minutes)


SPOKEN = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
                                         "sixteen seventeen eighteen nineteen twenty".split())}
SPOKEN |= {w: str(i) for i, w in enumerate("null eins zwei drei vier funf sechs sieben acht neun zehn elf zwolf".split())}
SPOKEN |= {"ein": "1", "erster": "1", "erste": "1", "zweiter": "2", "zweite": "2", "dritter": "3", "dritte": "3"}


def spoken_numbers(text: str, series: str = "") -> set[str]:
    """Bandnummern aus der Ansage; Whisper schreibt sie mal als Ziffer («Book 5»), mal als Wort («Book Two», «Band drei»)."""
    t = " ".join(SPOKEN.get(w, w) for w in scoring.words(text))
    return scoring.numbers(t, series)


def _holes(files: list[Path]) -> str:
    """Name der ersten Datei mit einer Null-Strecke (unfertiger Torrent), sonst leer."""
    from . import integrity
    if len(files) == 1:
        return files[0].name if integrity.zero_runs(files[0])["longest"] >= integrity.MIN_RUN else ""
    # mehrere Leser zugleich holen vom NAS gut dreimal so viel (10 → 33 MB/s, 18.11.)
    with ThreadPoolExecutor(4) as ex:
        for f, z in zip(files, ex.map(integrity.zero_runs, files)):
            if z["longest"] >= integrity.MIN_RUN:
                return f.name
    return ""


def _result(checks: list[dict], files: int, minutes: int = 0) -> dict:
    states = {c["status"] for c in checks}
    verdict = "rejected" if "fail" in states else "review" if "warn" in states else "passed"
    # Tags allein sind kein Grund für die Prüfliste, wenn alles andere stimmt
    if verdict == "review" and all(c["status"] == "ok" or c["name"] == "Tags" for c in checks) \
            and any(c["name"] == "Ansage" and "genannt." in c["text"] and c["status"] == "ok" for c in checks):
        verdict = "passed"
    return {"verdict": verdict, "checks": checks, "files": files, "minutes": minutes}
