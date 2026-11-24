"""E-Books: merkt je Buch, ob in seinem Ordner ein EPUB liegt, und holt fehlende EPUBs über Usenet."""
import json
import os
import re
import secrets
import shutil
import threading
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import config, db, importer, scoring, torrent, usenet


def has_epub(path) -> bool:
    if path.is_file():
        return path.suffix.lower() == ".epub"
    try:
        with os.scandir(path) as it:
            return any(f.name.lower().endswith(".epub") and f.is_file() for f in it)
    except OSError:
        return False


def scan(progress=lambda msg: None) -> str:
    c = db.conn()
    found = total = 0
    for lang, root in config.LIBRARY_ROOT.items():
        if not root.is_dir():
            continue  # Bibliothek nicht eingebunden: bisherigen Stand behalten
        rows = c.execute("SELECT id, rel_path, epub FROM book WHERE owned=1 AND lang=? AND rel_path IS NOT NULL", (lang,)).fetchall()
        for i, r in enumerate(rows):
            flag = 1 if has_epub(root / r["rel_path"]) else 0
            if flag != (r["epub"] or 0):
                c.execute("UPDATE book SET epub=? WHERE id=?", (flag, r["id"]))
            found += flag
            total += 1
            if i % 250 == 0:
                progress(f"{lang.upper()}: {i} von {len(rows)} Ordnern geprüft")
    # Noch fehlende Bände mit vorab geladenem E-Book: gilt, solange das EPUB in seinem Ordner liegt
    for r in c.execute("SELECT id, lang, pre_path FROM book WHERE owned=0 AND pre_path IS NOT NULL").fetchall():
        root = config.LIBRARY_ROOT[r["lang"]]
        if root.is_dir() and not has_epub(root / r["pre_path"]):
            c.execute("UPDATE book SET epub=0, pre_path=NULL WHERE id=?", (r["id"],))
    c.execute("UPDATE book SET epub=0 WHERE owned=0 AND epub=1 AND pre_path IS NULL")
    return f"{found} von {total} Büchern haben ein EPUB im Ordner"


# ---------- EPUB über Usenet suchen, laden, prüfen und in den Buchordner legen ----------
# Quellen: zuerst Usenet, sonst EBookBay und Internet Archive (Torrent über qBittorrent).
# Abgelegt wird nur eine neue Datei im Ordner des Hörbuchs; vorhandene Dateien werden nie überschrieben oder gelöscht.
# Die Bewertung ist bewusst locker: ein EPUB ist klein, und ob es wirklich passt, zeigt die Prüfung nach dem Download.
CATEGORY = 7020  # Newznab: Bücher / E-Books
SAFE = 60
POSSIBLE = 35  # ab hier wird ein Treffer versuchsweise geladen; die Prüfung des EPUB entscheidet
ACTIVE = ("queued", "downloading", "arrived")
OTHER_FORMAT = re.compile(r"\b(mobi|azw3?|pdf|cbz|cbr|kf8|lit|djvu|docx?|rtf|txt)\b", re.I)
AUDIO = re.compile(r"\b(mp3|m4b|m4a|flac|audiobook|audio ?book|horbuch|hoerbuch|unabridged|ungekurzt|kbps)\b", re.I)
MAX_BYTES = 200 * 1024 * 1024
MAX_TORRENT_BYTES = 700 * 1024 * 1024  # Einträge im Internet Archive enthalten oft mehrere Formate
TRIES = 3  # so viele Treffer je Buch nacheinander versuchen, wenn die Prüfung einen verwirft
_lock = threading.Lock()  # eine Suche gleichzeitig: schont die Abfragegrenzen der Indexer


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


NOISE = frozenset("epub retail ebook ebooks v1 v2 v3 v4 v5 nzb novel roman litrpg edition ausgabe german deutsch ger english eng "
                  "light ln series serie reihe saga trilogy trilogie chronicles chroniken".split())


def _head(title: str, series: str | None = None) -> set[str]:
    """Kennwörter des Haupttitels, ohne Untertitel und Klammerzusatz. Ist der Haupttitel nur der Serienname
    («Star Wars: Alphabet Squadron»), zählt der ganze Titel."""
    head = scoring.significant(re.split(r"[:(]| - ", title)[0])
    if series and head <= scoring.significant(series):
        return scoring.significant(re.sub(r"\([^)]*\)", " ", title)) or head
    return head


def _nums(text: str, series: str = "") -> set[str]:
    """Alle Bandnummern im Text: ausdrückliche («Book 8», «#4») und frei stehende ein- bis dreistellige Zahlen."""
    free = {m.lstrip("0") or "0" for w in re.split(r"[^a-z0-9.]+", scoring.plain(text)) if (m := w) and re.fullmatch(r"\d{1,3}", w)}
    return free | scoring.numbers(text, series)


def _known(word: str, known: set[str]) -> bool:
    """Wort bekannt, auch in gebeugter Form («Monde» und «Monden»)."""
    return word in known or (len(word) >= 5 and any(len(k) >= 5 and (k.startswith(word[:5])) for k in known))


def _extra(book: dict, text: str) -> set[str]:
    """Kennwörter im Text, die weder zu Titel, Untertitel, Autor noch Serie gehören. Viele davon = anderes Buch."""
    known = scoring.significant(" ".join(str(book.get(k) or "") for k in ("title", "subtitle", "author", "series"))) | NOISE
    body = re.sub(r"\[[^\]]*\]", " ", text)  # «[Serie 03]» nennt nur die Reihe
    return {w for w in scoring.significant(body) if len(w) > 2 and not _known(w, known)}


def _surnames(author: str | None) -> set[str]:
    from . import abs as abs_mod
    name = abs_mod.primary_author(author)
    w = [x for x in scoring.words(name) if len(x) > 1]
    return {w[-1]} if w and name != "Unbekannt" else set()


def _other_volume(book: dict, siblings: list[str], have: set[str]) -> str | None:
    """Nennt der Text den Titel eines anderen Bandes der Serie?"""
    own = _head(book["title"], book.get("series"))
    series = scoring.significant(book.get("series"))
    for s in siblings:
        h = _head(s, book.get("series"))
        if h and h <= have and not h <= own and not h <= series:  # ein Band, der wie die Serie heisst, sagt nichts
            return s
    return None


def score(book: dict, hit: dict, siblings: list[str] = ()) -> dict:
    """Bewertung eines Treffers als EPUB zu diesem Buch: 0 bis 100 Punkte mit Begründung.
    sicher = alles bestätigt; möglich = Titel passt, nichts spricht dagegen (die Prüfung nach dem Download entscheidet)."""
    rel = hit["title"]
    p = scoring.plain(rel)
    have = set(scoring.words(rel))
    reasons: list[dict] = []
    add = lambda pts, text: reasons.append({"points": pts, "text": text})
    out = lambda verdict, pts: {**hit, "score": max(0, min(100, pts)), "verdict": verdict, "reasons": reasons}
    if AUDIO.search(p):
        add(-100, "Das ist ein Hörbuch, kein E-Book")
        return out("verworfen", 0)
    if OTHER_FORMAT.search(p) and "epub" not in have:
        add(-100, "Anderes Format, kein EPUB")
        return out("verworfen", 0)
    size = hit.get("size") or 0
    if size and (size < 20_000 or size > (MAX_TORRENT_BYTES if hit.get("source") == "torrent" else MAX_BYTES)):
        add(-100, "Dateigrösse passt nicht zu einem EPUB")
        return out("verworfen", 0)
    foreign = next((v for k, v in scoring.FOREIGN.items() if k in have and len(k) > 2), None)
    if foreign:
        add(-100, f"Andere Sprache ({foreign})")
        return out("verworfen", 0)
    if book["lang"] == "en" and have & {"german", "deutsch", "ger"}:
        add(-100, "Deutsche Ausgabe, gesucht ist die englische")
        return out("verworfen", 0)
    own = _head(book["title"], book.get("series"))
    if not own or not own <= have:
        add(-100, "Titel passt nicht")
        return out("verworfen", 0)
    pts = 50
    add(50, "Titel passt")
    # «Red Rising» ist nicht «Red Moon Rising»: die Titelwörter müssen beieinander stehen
    seq_words = [w for w in scoring.words(rel) if w not in scoring.STOP]
    want = [w for w in scoring.words(re.split(r"[:(]| - ", book["title"])[0]) if w not in scoring.STOP and not w.isdigit()]
    together = not want or any(seq_words[i:i + len(want)] == want for i in range(len(seq_words))) or set(want) != own
    if not together:
        pts -= 30
        add(-30, "Die Titelwörter stehen nicht beieinander")
    author_ok = bool(_surnames(book.get("author")) & have)
    if author_ok:
        pts += 25
        add(25, "Autor passt")
    seq = scoring.seq_key(book.get("sequence"))
    nums = _nums(rel, book.get("series") or "")
    seq_ok = bool(seq and seq in nums)
    wrong_number = bool(seq and nums and not seq_ok)
    if seq_ok:
        pts += 15
        add(15, "Bandnummer passt")
    elif wrong_number:
        pts -= 40
        add(-40, "Andere Bandnummer")
    # Tragen mehrere Bände denselben Haupttitel, entscheidet allein die Bandnummer
    shared = bool(seq) and any(_head(s, book.get("series")) == own for s in siblings) and not (_full(book) > own and _full(book) <= have)
    if shared and not seq_ok and not wrong_number:
        pts -= 30
        add(-30, "Mehrere Bände tragen diesen Titel, die Bandnummer fehlt")
    extra = _extra(book, rel)
    if len(extra) >= 2:
        pts -= 30
        add(-30, "Nennt weitere Wörter, die nicht zum Buch gehören (" + ", ".join(sorted(extra)[:4]) + ")")
    other = _other_volume(book, list(siblings), have)
    if other:
        pts -= 50
        add(-50, f"Nennt den Titel eines anderen Bandes («{other}»)")
    multi = bool(scoring.is_multi(p) or scoring.span(rel, book.get("series") or ""))
    if multi:
        pts -= 40
        add(-40, "Sammelband")
    if "epub" in have:
        pts += 10
        add(10, "Als EPUB gekennzeichnet")
    lang_unclear = book["lang"] == "de" and bool(book.get("same_title_other_lang")) and not have & {"german", "deutsch", "ger"}
    if lang_unclear:
        pts -= 30
        add(-30, "Gleicher Titel wie die englische Ausgabe, Sprache nicht erkennbar")
    safe = (pts >= SAFE and (author_ok or seq_ok) and not lang_unclear and not wrong_number and not other
            and len(extra) < 2 and (seq_ok or not shared) and not multi and together)
    # «möglich»: der Titel passt und nichts spricht klar dagegen. Autor, Band und Sprache klärt die Prüfung des EPUB.
    possible = not wrong_number and not other and not multi and pts >= POSSIBLE
    hit = {**hit, "seq_ok": seq_ok}
    return out("sicher" if safe else "möglich" if possible else "unsicher", pts)


RANK = {"sicher": 0, "möglich": 1, "unsicher": 2, "verworfen": 3}


def torrent_ready() -> bool:
    return bool(config.EBOOK_TORRENT_INDEXER_IDS and config.QBIT_URL and config.secret("QBIT_USERNAME") and config.QBIT_SAVE_ROOT)


def _query(http, book: dict, siblings, queries: list[str], indexers: list[int], protocol: str, found: dict, errors: list[str]) -> None:
    for q in queries:
        try:
            r = http.get(config.PROWLARR_URL + "/api/v1/search", params={"query": q, "indexerIds": indexers, "categories": [CATEGORY]})
            r.raise_for_status()
            rows = r.json()
        except Exception as exc:
            errors.append(f"Suche «{q}» ({'Usenet' if protocol == 'usenet' else 'EBookBay/Internet Archive'}): {type(exc).__name__}")
            continue
        for raw in rows:
            if raw.get("protocol") != protocol or raw.get("indexerId") not in indexers or not raw.get("guid"):
                continue
            (usenet if protocol == "usenet" else torrent).remember(raw)
            found.setdefault(raw["guid"], {"guid": raw["guid"], "title": raw["title"], "indexer": raw.get("indexer"), "source": protocol,
                                           "size": raw.get("size"), "grabs": raw.get("grabs") or raw.get("seeders") or 0})


def _full(book: dict) -> set[str]:
    """Kennwörter des ganzen Titels mit Untertitel («Star Wars Jedi: Battle Scars»)."""
    return scoring.significant(re.sub(r"\([^)]*\)", " ", book["title"]))


def torrent_queries(book: dict) -> list[str]:
    """EBookBay findet nur über den blossen Titel, das Internet Archive besser mit Autor."""
    title = re.split(r"[:(]| - ", book["title"])[0].strip()
    if _head(title, book.get("series")) != _head(book["title"], book.get("series")):
        title = re.sub(r"\([^)]*\)", " ", book["title"]).replace(":", " ").strip()
    title = re.sub(r",?\s*\b(vol(ume)?\.?|book|band|teil)\s*(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b.*$", "", title, flags=re.I).strip() or title
    sur = " ".join(_surnames(book.get("author")))
    return list(dict.fromkeys([title, f"{title} {sur}".strip()]))


def search(book: dict, siblings: list[str] = (), sources: tuple = ("usenet", "torrent"), everything: bool = False) -> tuple[list[dict], list[str]]:
    """Alle EPUB-Treffer, beste zuerst. Zuerst Usenet; nur ohne ladbaren Treffer auch EBookBay und Internet Archive
    (die sind langsam). everything=True (Auswahl von Hand) fragt immer alle Quellen und zeigt auch schon gescheiterte
    Treffer, als verworfen markiert. Liefert (Treffer, Fehlermeldungen)."""
    found: dict[str, dict] = {}
    errors: list[str] = []
    from . import search as search_mod
    tried = {r["guid"] for r in db.conn().execute("SELECT guid FROM ebook_job WHERE book_id=? AND state IN ('failed','rejected')", (book["id"],))}
    hid = search_mod.hidden(book["id"])  # von dir für einen Monat ausgeblendet: nur bei der Auswahl von Hand sichtbar
    def _one(h: dict) -> dict:
        r = score(book, h, siblings)
        if h["guid"] in hid:
            r["hidden_until"] = hid[h["guid"]]
        if h["guid"] in tried:
            r["reasons"].append({"points": -100, "text": "Schon geladen: das EPUB passte nicht oder war defekt"})
            r.update(verdict="verworfen", score=0)
        return r
    rank = lambda: sorted((_one(h) for h in found.values() if everything or (h["guid"] not in tried and h["guid"] not in hid)),
                          key=lambda h: (RANK[h["verdict"]], h["source"] != "usenet", -h["score"], -h["grabs"]))
    loadable = lambda hits: any(h["verdict"] in ("sicher", "möglich") for h in hits)
    with httpx.Client(timeout=60, headers={"X-Api-Key": config.secret("PROWLARR_API_KEY")}) as http:
        if "usenet" in sources and usenet.configured():
            _query(http, book, siblings, usenet.queries(book, audio=False)[:3], config.USENET_INDEXER_IDS, "usenet", found, errors)
        hits = rank()
        if "torrent" in sources and torrent_ready() and (everything or not loadable(hits)):
            http.timeout = httpx.Timeout(170)  # das Internet Archive braucht oft über eine Minute
            for q in torrent_queries(book):
                _query(http, book, siblings, [q], config.EBOOK_TORRENT_INDEXER_IDS, "torrent", found, errors)
                hits = rank()
                if loadable(hits) and not everything:
                    break
    return hits, errors


def upcoming(book: dict) -> bool:
    """Angekündigter Band, dessen Hörbuch noch nicht erschienen ist: das E-Book wird vorab geladen."""
    return not book["owned"] and bool(book.get("edition_id")) and (book.get("release_date") or "") > date.today().isoformat()


def folder_of(book: dict) -> Path:
    if book["owned"]:
        return config.LIBRARY_ROOT[book["lang"]] / book["rel_path"]
    # künftiger Buchordner: dorthin legt die Ablage später auch das Hörbuch
    return config.LIBRARY_ROOT[book["lang"]] / (book.get("pre_path") or "/".join(importer.target_rel(book)))


def blocker(book: dict) -> str | None:
    """Warum für dieses Buch kein EPUB geladen werden kann. None = möglich."""
    if not upcoming(book):
        if not book["owned"] or not book.get("rel_path"):
            return "Das Hörbuch liegt nicht im Bestand"
        if not folder_of(book).is_dir():
            return "Der Ordner des Hörbuchs ist nicht zu finden"
    elif not config.LIBRARY_ROOT[book["lang"]].is_dir():
        return "Die Bibliothek ist nicht eingebunden"
    if has_epub(folder_of(book)):
        return "Im Ordner liegt schon ein EPUB"
    q = ",".join("?" * len(ACTIVE))
    if db.conn().execute(f"SELECT 1 FROM ebook_job WHERE book_id=? AND state IN ({q})", (book["id"], *ACTIVE)).fetchone():
        return "Für dieses Buch lädt schon ein EPUB"
    return None


def request_quick(book_id: int) -> dict:
    """Für den Knopf: Usenet sofort durchsuchen; ohne Treffer laufen die langsamen Quellen im Hintergrund weiter."""
    r = request(book_id, sources=("usenet",))
    if r["started"] or r.get("blocked") or not torrent_ready():
        return r

    def work():
        try:
            request(book_id, sources=("torrent",))
        except Exception:
            pass
    threading.Thread(target=work, daemon=True, name="abw-ebook-torrent").start()
    return {"started": False, "pending": True, "message": "Auf Usenet ist nichts Passendes. EBookBay und Internet Archive werden im Hintergrund "
            "durchsucht (das dauert einige Minuten); ein passender Treffer wird von selbst geladen."}


def request(book_id: int, auto: bool = False, sources: tuple = ("usenet", "torrent")) -> dict:
    """Sucht das EPUB zu einem Buch und startet den Download des besten ladbaren Treffers."""
    from . import search as search_mod
    if not ((usenet.configured() and usenet.sab_configured()) or torrent_ready()):
        return {"started": False, "message": "Keine Quelle für E-Books eingerichtet (Prowlarr mit SABnzbd oder qBittorrent fehlt)"}
    book, siblings = search_mod.book_ctx(book_id)
    why = blocker(book)
    if why:
        return {"started": False, "blocked": True, "message": why}
    c = db.conn()
    with _lock:
        hits, errors = search(book, siblings, sources)
        best = next((h for h in hits if h["verdict"] in ("sicher", "möglich")), None)  # nach Rang sortiert: sicher zuerst
        unsure = sum(1 for h in hits if h["verdict"] == "unsicher")
        prev = c.execute("SELECT attempts FROM ebook_search WHERE book_id=?", (book_id,)).fetchone()
        attempts = (prev["attempts"] if prev else 0) + 1
        result, message = "none", "Kein passendes EPUB zu finden (Usenet, EBookBay, Internet Archive)"
        if unsure and not best:
            message = f"Kein brauchbarer Treffer ({unsure} mit anderem Band oder Sammelband, bester: «{hits[0]['title'][:80]}»)"
        if errors and not hits:
            result, message = "error", "Die Suche ist fehlgeschlagen: " + "; ".join(errors)[:200]
        if best:
            marker = f"abw-ebook-{book_id}-{secrets.token_hex(4)}"
            try:
                if best["source"] == "torrent":
                    nzo = torrent.submit(best["guid"], marker, force=True)  # nicht hinter den Hörbüchern anstehen
                else:
                    nzo = usenet.submit(usenet.fetch_nzb(best["guid"]), marker)
            except Exception as exc:
                result, message = "error", f"Der Download liess sich nicht starten: {exc}"[:300]
            else:
                c.execute("INSERT INTO ebook_job(book_id, indexer, release_title, guid, size, score, marker, client_id, state, auto, created, updated,"
                          " source, seq_ok) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (book_id, best.get("indexer"), best["title"], best["guid"], best.get("size"), best["score"], marker, nzo,
                           "queued", int(auto), _now(), _now(), best["source"], int(best["seq_ok"])))
                result = "started"
                message = f"EPUB lädt von {best.get('indexer') or best['source']}: «{best['title'][:100]}» ({best['score']} Punkte, {best['verdict']})"
        # Ohne Treffer erst nach 7, 14, 21, höchstens 28 Tagen wieder; nach einem Fehler schon nach einer Stunde
        wait = timedelta(hours=1) if result == "error" else timedelta(days=min(28, 7 * attempts))
        c.execute("INSERT OR REPLACE INTO ebook_search(book_id, attempts, last_search, next_search, last_result, message) VALUES(?,?,?,?,?,?)",
                  (book_id, attempts, _now(), (datetime.now(timezone.utc) + wait).isoformat(), result, message))
    return {"started": result == "started", "message": message}


def guided(book_id: int) -> dict:
    """Auswahl von Hand: alle EPUB-Treffer aller Quellen mit Punktzahl und Begründung, beste zuerst."""
    from . import search as search_mod
    book, siblings = search_mod.book_ctx(book_id)
    sources = [{"key": "usenet", "name": "Usenet", "active": usenet.configured()},
               {"key": "torrent", "name": "EBookBay, Internet Archive", "active": torrent_ready()}]
    why = blocker(book)
    with _lock:
        hits, errors = search(book, siblings, everything=True)
    can = (usenet.configured() and usenet.sab_configured()) or torrent_ready()
    return {"book": search_mod.book_info(book), "results": hits, "errors": errors,
            "queries": list(dict.fromkeys(usenet.queries(book, audio=False)[:3] + torrent_queries(book))),
            "can_start": bool(can) and not why, "blocked": why, "searched_at": _now(), "cached": False, "listed": False, "sources": sources}


def grab(book_id: int, picked: dict) -> dict:
    """Startet einen von Hand gewählten Treffer. Die Wahl gilt: Titel und Band prüft ABW danach nicht mehr streng."""
    from . import search as search_mod
    book, siblings = search_mod.book_ctx(book_id)
    why = blocker(book)
    if why:
        raise ValueError(why)
    guid = picked["guid"]
    source = "torrent" if picked.get("source") == "torrent" else "usenet"
    if not (torrent if source == "torrent" else usenet).known(guid):
        raise LookupError("Der Treffer ist nicht mehr bekannt. Bitte neu suchen.")
    hit = score(book, {"guid": guid, "title": picked["title"], "indexer": picked.get("indexer"), "source": source, "size": picked.get("size"), "grabs": 0}, siblings)
    marker = f"abw-ebook-{book_id}-{secrets.token_hex(4)}"
    nzo = torrent.submit(guid, marker, force=True) if source == "torrent" else usenet.submit(usenet.fetch_nzb(guid), marker)
    c = db.conn()
    c.execute("INSERT INTO ebook_job(book_id, indexer, release_title, guid, size, score, marker, client_id, state, auto, created, updated,"
              " source, seq_ok, manual) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
              (book_id, hit.get("indexer"), hit["title"], guid, hit.get("size"), hit["score"], marker, nzo, "queued", 0, _now(), _now(),
               source, int(hit.get("seq_ok", False))))
    return {"started": True, "message": f"EPUB lädt von {hit.get('indexer') or source}: «{hit['title'][:100]}» (von Hand gewählt)"}


def read_meta(path: Path) -> dict:
    """Titel, Autor und Sprache aus dem EPUB. Wirft ValueError, wenn die Datei kein gültiges EPUB ist."""
    if not zipfile.is_zipfile(path):
        raise ValueError("Die Datei ist kein EPUB")
    with zipfile.ZipFile(path) as z:
        try:
            box = z.read("META-INF/container.xml")
            if b"<!ENTITY" in box.upper():
                raise ValueError("Das EPUB ist beschädigt")
            root = next(e.get("full-path") for e in ET.fromstring(box).iter() if e.tag.rsplit("}", 1)[-1] == "rootfile")
            if z.getinfo(root).file_size > 5_000_000:
                raise ValueError("Das EPUB ist beschädigt")
            opf = z.read(root)
        except (KeyError, StopIteration, ET.ParseError):
            raise ValueError("Das EPUB ist beschädigt (Inhaltsangabe fehlt)")
        if b"<!ENTITY" in opf.upper():
            raise ValueError("Das EPUB ist beschädigt")
        if "META-INF/rights.xml" in z.namelist():  # Adobe-Kopierschutz: auf keinem Gerät lesbar
            raise ValueError("Das EPUB ist kopiergeschützt")
    try:
        tree = ET.fromstring(opf)
    except ET.ParseError:
        raise ValueError("Das EPUB ist beschädigt (Inhaltsangabe unlesbar)")
    get = lambda tag: [(e.text or "").strip() for e in tree.iter() if e.tag.rsplit("}", 1)[-1] == tag and (e.text or "").strip()]
    return {"title": (get("title") or [""])[0], "authors": get("creator"), "language": ((get("language") or [""])[0]).lower()}


def mismatch(book: dict, meta: dict, siblings: list[str] = (), seq_ok: bool = True) -> str | None:
    """Passt das geladene EPUB zum Hörbuch? Liefert den Grund, wenn nicht.
    seq_ok: Der Treffer nannte schon die richtige Bandnummer."""
    have = set(scoring.words(meta["title"]))
    own = _head(book["title"], book.get("series"))
    if not own or not own <= have:
        return f"Das EPUB trägt einen anderen Titel («{meta['title'][:80]}»)"
    seq = scoring.seq_key(book.get("sequence"))
    nums = _nums(meta["title"], book.get("series") or "") - _nums(book["title"])
    if _other_volume(book, list(siblings), have) or (seq and nums and seq not in nums):
        return f"Das EPUB ist ein anderer Band («{meta['title'][:80]}»)"
    whole = _full(book) > own and _full(book) <= have  # der ganze Titel samt Untertitel steht im EPUB
    if seq and not seq_ok and seq not in nums and not whole and any(_head(s, book.get("series")) == own for s in siblings):
        return "Mehrere Bände tragen diesen Titel, und weder Treffer noch EPUB nennen die Bandnummer"
    lang = meta["language"][:2]
    want = {"en": "en", "de": "de"}[book["lang"]]
    if not lang and book["lang"] == "de" and book.get("same_title_other_lang"):
        return "Die deutsche und die englische Ausgabe heissen gleich, und das EPUB nennt keine Sprache"
    if lang and lang not in (want, "un") and lang.isalpha():
        return f"Das EPUB ist in einer anderen Sprache ({meta['language']})"
    names = set(scoring.words(" ".join(meta["authors"])))
    sur = _surnames(book.get("author"))
    if names and sur and not sur & names:
        return f"Das EPUB nennt einen anderen Autor ({', '.join(meta['authors'])[:80]})"
    if not names:
        # Ohne Autor muss der Titel genügen: fremde Wörter («Conan The Marauder» für «Marauder») heissen anderes Buch
        extra = scoring.significant(meta["title"]) - _full(book) - scoring.significant(book.get("series") or "") - _FILLER
        if extra:
            return f"Das EPUB nennt keinen Autor, und sein Titel enthält fremde Wörter («{meta['title'][:80]}»)"
    return None


_FILLER = {"book", "books", "volume", "vol", "band", "teil", "novel", "roman", "edition", "unabridged", "ungekürzt", "series", "reihe",
           "litrpg", "saga", "chronicles", "ebook", "epub"}


def place(book: dict, src: Path) -> str:
    """Kopiert das EPUB als neue Datei in den Ordner des Hörbuchs. Überschreibt nichts."""
    if not config.IMPORT_ENABLED:
        raise PermissionError("Die Ablage ist noch nicht freigeschaltet (ABW_IMPORT_ENABLED)")
    folder = folder_of(book)
    pre = upcoming(book)
    if pre:
        # Band ist noch nicht erschienen: den künftigen Buchordner anlegen, mit Angaben für Audiobookshelf
        root = config.LIBRARY_ROOT[book["lang"]]
        if not root.is_dir():
            raise ValueError("Die Bibliothek ist nicht eingebunden")
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / "metadata.json").exists():
            (folder / "metadata.json").write_text(json.dumps(importer.metadata(book), ensure_ascii=False, indent=2), encoding="utf-8")
        db.conn().execute("UPDATE book SET pre_path=? WHERE id=?", (str(folder.relative_to(root)), book["id"]))
    if not folder.is_dir():
        raise ValueError("Der Ordner des Hörbuchs ist nicht zu finden")
    if has_epub(folder):
        raise ValueError("Im Ordner liegt schon ein EPUB")
    target = folder / (importer.clean(book["title"]) + ".epub")
    tmp = folder / ("." + target.name + ".abw-teil")
    if target.exists() or tmp.exists():
        raise ValueError("Die Zieldatei existiert schon; ABW überschreibt nichts")
    shutil.copyfile(src, tmp)
    if tmp.stat().st_size != src.stat().st_size:
        tmp.unlink()
        raise IOError("Die Kopie des EPUB ist unvollständig")
    tmp.rename(target)
    return str(target)


def fits_elsewhere(meta: dict, exclude: int) -> dict | None:
    """Passt dieses EPUB zu einem anderen Hörbuch im Bestand, dem noch eines fehlt? Nur bei eindeutiger Zuordnung:
    Titel, Autor und Sprache müssen stimmen; tragen mehrere Bände den Titel, muss das EPUB die Bandnummer nennen."""
    from . import search as search_mod
    have = set(scoring.words(meta["title"]))
    names = set(scoring.words(" ".join(meta["authors"])))
    lang = meta["language"][:2]
    if not have or not names or lang not in ("en", "de"):
        return None  # ohne Autor oder Sprache im EPUB bleibt die Zuordnung unsicher
    fit = []
    for r in db.conn().execute("SELECT b.id, b.title, b.author, e.name series FROM book b LEFT JOIN edition e ON e.id=b.edition_id"
                               " WHERE b.owned=1 AND COALESCE(b.epub,0)=0 AND b.rel_path IS NOT NULL AND b.lang=? AND b.id<>?", (lang, exclude)).fetchall():
        head = _head(r["title"], r["series"])
        if not head or not head <= have or not _surnames(r["author"]) & names:
            continue
        book, siblings = search_mod.book_ctx(r["id"])
        if mismatch(book, meta, siblings, seq_ok=False) is None and blocker(book) is None:
            fit.append((len(_full(book) & have), book))
    if not fit:
        return None
    best = max(n for n, _ in fit)
    top = [b for n, b in fit if n == best]
    return top[0] if len(top) == 1 else None  # mehrere gleich gute Kandidaten: lieber nicht raten


def _tell(book: dict) -> None:
    if db.setting("notify_ebook") != "1":
        return  # Meldungen zu E-Books abgeschaltet (Standard)
    try:
        from . import notifications
        notifications.book_event("epub", "E-Book vorab abgelegt" if not book["owned"] else "E-Book abgelegt", book,
                                 extra="das Hörbuch ist noch nicht erschienen" if not book["owned"] else "")
    except Exception:
        pass  # eine Meldung darf die Ablage nie stören


def finish(job: dict) -> str:
    """Angekommenen Download prüfen und das passende EPUB ablegen. Liefert den neuen Zustand.
    Ein EPUB, das nicht zum gesuchten Buch passt, wird vor dem Verwerfen gegen den übrigen Bestand geprüft und
    dort abgelegt, wo es eindeutig passt und noch keines liegt (anderer Band, Sammel-Download)."""
    from . import abs as abs_mod
    from . import search as search_mod
    c = db.conn()
    end = lambda state, error=None, target=None: c.execute(
        "UPDATE ebook_job SET state=?, error=?, imported_to=?, updated=? WHERE id=?", (state, error, target, _now(), job["id"])) and state
    try:
        book, siblings = search_mod.book_ctx(job["book_id"])
    except LookupError:
        book, siblings = None, []
    src = (config.TORRENT_COMPLETE_ROOT if job.get("source") == "torrent" else config.USENET_COMPLETE_ROOT) / job["marker"]
    files = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() == ".epub") if src.is_dir() else []
    if not files:
        return end("rejected", "Im Download liegt kein EPUB")
    why = "Kein EPUB im Download passt" if book else "Das Buch gibt es im Bestand nicht mehr"
    target, failed, others, langs = None, None, [], set()
    valid = []
    for f in files[:40]:
        try:
            if f.stat().st_size > MAX_BYTES:
                raise ValueError("Das EPUB ist ungewöhnlich gross")
            meta = read_meta(f)
        except (ValueError, OSError, zipfile.BadZipFile) as exc:
            why = str(exc)
            continue
        valid.append(f)
        miss = mismatch(book, meta, siblings, bool(job.get("seq_ok"))) if book else why
        if miss is None and target is None and failed is None:
            try:
                target = place(book, f)
            except (ValueError, PermissionError, OSError) as exc:
                failed = f"Ablage nicht möglich: {exc}"[:300]
                continue
            c.execute("UPDATE book SET epub=1 WHERE id=?", (book["id"],))
            langs.add((book["lang"], target))
            _tell(book)
            continue
        if miss is not None:
            why = miss
        try:
            other = fits_elsewhere(meta, job["book_id"])
            if other:
                where = place(other, f)
                c.execute("UPDATE book SET epub=1 WHERE id=?", (other["id"],))
                # eigener Eintrag, damit Statistik und Verlauf das zusätzlich abgelegte EPUB kennen
                c.execute("INSERT INTO ebook_job(book_id, indexer, release_title, guid, size, score, marker, state, imported_to, auto, created, updated,"
                          " source, seq_ok) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                          (other["id"], job.get("indexer"), job["release_title"], None, f.stat().st_size, job.get("score"),
                           f"{job['marker']}-b{other['id']}", "done", where, job.get("auto") or 0, _now(), _now(), job.get("source")))
                others.append(other["title"])
                langs.add((other["lang"], where))
        except (ValueError, PermissionError, OSError):
            pass
    for lang, where in langs:
        try:
            abs_mod.scan(lang, where)
        except Exception:
            pass  # Audiobookshelf liest das EPUB beim nächsten eigenen Scan ein
    note = (" Passte aber zu: " + ", ".join(f"«{t}»" for t in others[:5]) + " (dort abgelegt).") if others else ""
    # Von Hand gewählt: liegt genau ein gültiges EPUB im Download, gilt die Wahl auch gegen die Prüfung
    if job.get("manual") and book and not target and not failed and not others and len(valid) == 1:
        try:
            target = place(book, valid[0])
        except (ValueError, PermissionError, OSError) as exc:
            failed = f"Ablage nicht möglich: {exc}"[:300]
        else:
            c.execute("UPDATE book SET epub=1 WHERE id=?", (book["id"],))
            try:
                abs_mod.scan(book["lang"], target)
            except Exception:
                pass
            return end("done", f"Von Hand gewählt und abgelegt. Hinweis der Prüfung: {why}"[:400], target)
    if target:
        return end("done", note.strip() or None, target)
    return end("failed" if failed else "rejected", ((failed or why) + note)[:400])


def track(progress=lambda msg: None) -> str:
    """Stand der EPUB-Downloads aus SABnzbd holen; angekommene prüfen und ablegen."""
    c = db.conn()
    rows = [dict(r) for r in c.execute("SELECT * FROM ebook_job WHERE state IN ('queued','downloading','arrived')")]
    if not rows:
        return "Keine laufenden E-Book-Downloads"
    waiting = [r for r in rows if r["state"] != "arrived"]
    st: dict[int, dict] = {}
    nzb = [r for r in waiting if r.get("source") != "torrent"]
    if nzb and usenet.sab_configured():
        by_id = usenet.status({r["client_id"] for r in nzb})
        st.update({r["id"]: by_id[r["client_id"]] for r in nzb if r["client_id"] in by_id})
    tor = [r for r in waiting if r.get("source") == "torrent"]
    if tor and torrent_ready():
        st.update(torrent.status(tor))
    for r in waiting:
        s = st.get(r["id"])
        if s:
            r["state"] = s["state"]
            c.execute("UPDATE ebook_job SET state=?, error=?, updated=? WHERE id=?", (s["state"], s.get("error"), _now(), r["id"]))
    result = [finish(r) for r in rows if r["state"] == "arrived"]
    # Verworfen oder defekt: den nächsten Treffer versuchen, höchstens TRIES je Buch und Tag
    again = 0
    for r in rows:
        now = c.execute("SELECT state FROM ebook_job WHERE id=?", (r["id"],)).fetchone()["state"]
        if now in ("failed", "rejected") and not r.get("manual") and c.execute(
                "SELECT COUNT(*) FROM ebook_job WHERE book_id=? AND replace(substr(created,1,19),'T',' ') >= datetime('now','-1 day')",
                (r["book_id"],)).fetchone()[0] < TRIES:
            try:
                again += request(r["book_id"], auto=bool(r["auto"]))["started"]
            except Exception:
                pass
    return (f"{len(rows)} E-Book-Downloads, {result.count('done')} abgelegt, {len(result) - result.count('done')} verworfen"
            + (f", {again} mit dem nächsten Treffer neu gestartet" if again else ""))


AUTO_BATCH = 3
AUTO_CANDIDATES = (
    "SELECT b.id FROM book b LEFT JOIN ebook_search q ON q.book_id=b.id"
    " WHERE b.owned=1 AND COALESCE(b.epub,0)=0 AND b.rel_path IS NOT NULL"
    " AND EXISTS (SELECT 1 FROM download d WHERE d.book_id=b.id AND d.state='done')"  # von ABW abgelegte Hörbücher
    " AND NOT EXISTS (SELECT 1 FROM ebook_job j WHERE j.book_id=b.id AND j.state IN ('queued','downloading','arrived'))"
    " AND (q.next_search IS NULL OR q.next_search<=?)")


# Angekündigte Bände überwachter Serien, deren Hörbuch noch nicht erschienen ist: Bände mit Termin zuerst, die nächsten vorn
UPCOMING_CANDIDATES = (
    "SELECT b.id FROM book b JOIN edition e ON e.id=b.edition_id JOIN series s ON s.id=e.series_id LEFT JOIN ebook_search q ON q.book_id=b.id"
    " WHERE b.owned=0 AND COALESCE(b.epub,0)=0 AND b.release_date>:today AND COALESCE(s.auto,1)=1"
    " AND COALESCE(e.monitored, (SELECT COUNT(*) FROM book o WHERE o.edition_id=e.id AND o.owned=1) > 0)"
    " AND NOT EXISTS (SELECT 1 FROM ebook_job j WHERE j.book_id=b.id AND j.state IN ('queued','downloading','arrived'))"
    " AND (q.next_search IS NULL OR q.next_search<=:now)")


LISTENING_DAYS = 14  # so lange nach dem letzten Hören gilt eine Serie als «wird gerade gehört»


def listening_candidates(limit: int = AUTO_BATCH) -> list[int]:
    """Bände der Serien, die irgendein Benutzer gerade hört: vorhandene Bände ohne EPUB und, wenn die
    Ablage eingeschaltet ist, angekündigte Bände derselben Fassung. Die zuletzt gehörte Serie zuerst, darin der gehörte Band
    und die folgenden vor den früheren. Die Wartezeit nach erfolgloser Suche gilt weiter."""
    c = db.conn()
    since = (datetime.now(timezone.utc) - timedelta(days=LISTENING_DAYS)).isoformat()
    heard = c.execute(
        "SELECT b.edition_id, MAX(p.updated) last, MAX(CASE WHEN p.finished=0 THEN b.seq END) cur FROM user_progress p JOIN book b ON b.id=p.book_id"
        " WHERE b.edition_id IS NOT NULL AND p.progress>0 AND p.updated>=? GROUP BY b.edition_id ORDER BY last DESC", (since,)).fetchall()
    today, now, out = date.today().isoformat(), _now(), []
    for h in heard:
        rows = c.execute(
            "SELECT b.id, b.seq FROM book b LEFT JOIN ebook_search q ON q.book_id=b.id WHERE b.edition_id=? AND COALESCE(b.epub,0)=0"
            " AND ((b.owned=1 AND b.rel_path IS NOT NULL) OR (b.owned=0 AND b.release_date>? AND ?=1))"
            " AND NOT EXISTS (SELECT 1 FROM ebook_job j WHERE j.book_id=b.id AND j.state IN ('queued','downloading','arrived'))"
            " AND (q.next_search IS NULL OR q.next_search<=?)",
            (h["edition_id"], today, int(bool(config.IMPORT_ENABLED)), now)).fetchall()
        cur = h["cur"] if h["cur"] is not None else -1
        rows = sorted(rows, key=lambda r: (r["seq"] is None, (r["seq"] or 0) < cur, r["seq"] or 0))
        out += [r["id"] for r in rows if r["id"] not in out]
        if len(out) >= limit:
            break
    return out[:limit]


def auto(progress=lambda msg: None) -> str:
    """Holt das EPUB zu Serien, die gerade gehört werden (zuerst), zu neu abgelegten Hörbüchern und vorab zu angekündigten
    Bänden. Jeder Teil hat seinen Schalter."""
    c = db.conn()
    ids = []
    if db.setting("auto_ebook_listening") == "1":
        ids += listening_candidates()
    listening = len(ids)
    if db.setting("auto_ebook") == "1":
        ids += [r["id"] for r in c.execute(AUTO_CANDIDATES + " ORDER BY q.last_search IS NOT NULL, q.last_search LIMIT ?", (_now(), AUTO_BATCH))
                if r["id"] not in ids]
    new = len(ids) - listening
    if db.setting("auto_ebook_upcoming") == "1" and config.IMPORT_ENABLED:
        ids += [r["id"] for r in c.execute(UPCOMING_CANDIDATES + " ORDER BY q.last_search IS NOT NULL, b.release_date>='2100', b.release_date, q.last_search LIMIT :n",
                                           {"today": date.today().isoformat(), "now": _now(), "n": AUTO_BATCH}) if r["id"] not in ids]
    if all(db.setting(k) != "1" for k in ("auto_ebook", "auto_ebook_upcoming", "auto_ebook_listening")):
        return "Alle E-Book-Schalter sind aus"
    started = 0
    for i, book_id in enumerate(ids):
        progress(f"Buch {i + 1} von {len(ids)}")
        try:
            started += request(book_id, auto=True)["started"]
        except Exception:
            continue
    return (f"{listening} Bände gerade gehörter Serien, {new} neue Hörbücher und {len(ids) - new - listening} angekündigte Bände gesucht,"
            f" {started} EPUBs gestartet")


def request_series(series_id: int) -> int:
    """Sucht im Hintergrund EPUBs für alle vorhandenen Bände einer Serie ohne EPUB. Liefert die Anzahl."""
    ids = [r["id"] for r in db.conn().execute(
        "SELECT b.id FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=? AND b.owned=1 AND COALESCE(b.epub,0)=0"
        " AND b.rel_path IS NOT NULL AND NOT EXISTS (SELECT 1 FROM ebook_job j WHERE j.book_id=b.id AND j.state IN ('queued','downloading','arrived'))"
        " ORDER BY e.lang DESC, b.seq LIMIT 60", (series_id,))]

    def work():
        for book_id in ids:
            try:
                request(book_id)
            except Exception:
                continue
    if ids:
        threading.Thread(target=work, daemon=True, name="abw-ebook-serie").start()
    return len(ids)


def states() -> dict[int, dict]:
    """Je Buch: läuft ein EPUB-Download, und was ergab die letzte Suche?"""
    c = db.conn()
    out = {r["book_id"]: {"state": "none" if r["last_result"] == "none" else "error" if r["last_result"] == "error" else None,
                          "message": r["message"]} for r in c.execute("SELECT book_id, last_result, message FROM ebook_search")}
    for r in c.execute("SELECT book_id, state, error FROM ebook_job ORDER BY id"):
        if r["state"] in ACTIVE:
            out[r["book_id"]] = {"state": "loading", "message": "EPUB lädt"}
        elif r["state"] in ("failed", "rejected") and out.get(r["book_id"], {}).get("state") != "loading":
            out[r["book_id"]] = {"state": "error", "message": r["error"] or "Download fehlgeschlagen"}
        elif r["state"] == "done":
            out.pop(r["book_id"], None)
    return out
