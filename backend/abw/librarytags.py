"""Genre und Tags je Karte der Serienansicht, für die Filter in der Bibliothek.

Je Fassung zählen die ersten drei Bände mit ASIN (vorhandene zuerst), bei «Autor – Einzelbücher» alle Bücher.
Die Angaben kommen aus dem Zwischenspeicher von `tags.fetch` (Audible: Kategorien, Titel, Beschreibung) und den
Goodreads-Genres. Geholt wird nur von der Hintergrund-Aufgabe, die Seite selbst fragt Audible nie ab.
"""
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import abs as abs_mod
from . import db, goodreads, tags

PER_EDITION = 3
PER_RUN = 1000  # ASINs je Lauf (20 Audible-Abfragen)


def _books() -> dict[tuple[str, object], list[tuple[str, str]]]:
    """(Art, Schlüssel) -> [(Sprache, ASIN)]: Art «series» mit Serien-ID, «single» mit Schlüssel der Einzelbuch-Karte."""
    c = db.conn()
    out: dict[tuple[str, object], list[tuple[str, str]]] = defaultdict(list)
    for r in c.execute("""SELECT series_id, lang, asin FROM (
            SELECT e.series_id, b.lang, b.asin, row_number() OVER (PARTITION BY b.edition_id ORDER BY b.owned DESC, b.seq IS NULL, b.seq) rn
            FROM book b JOIN edition e ON e.id=b.edition_id WHERE length(b.asin)=10) WHERE rn<=?""", (PER_EDITION,)):
        out[("series", r["series_id"])].append((r["lang"], r["asin"]))
    for r in c.execute("SELECT lang, asin, author FROM book WHERE edition_id IS NULL AND (owned=1 OR wanted IS NOT NULL) AND length(asin)=10"):
        key = db.norm(abs_mod.primary_author(r["author"] or "") or "Unbekannt") or "unbekannt"
        out[("single", key)].append((r["lang"], r["asin"]))
    return out


def _cached(lang: str, asins: list[str]) -> dict[str, dict]:
    """Audible-Angaben aus dem Zwischenspeicher, auch abgelaufene (die Aufgabe frischt sie auf)."""
    c = db.conn()
    out = {}
    for i in range(0, len(asins), 500):
        part = asins[i:i + 500]
        keys = [f"desc:{lang}:{a}" for a in part]
        for r in c.execute(f"SELECT key, value FROM cache WHERE key IN ({','.join('?' * len(keys))})", keys):
            out[r["key"].split(":", 2)[2]] = json.loads(r["value"])
    return out


# Genre-Namen deutsch. Audible.com liefert englische, Audible.de teils deutsche Namen
# (Auswertung 22.11.); beide kommen auf denselben deutschen Namen. Unbekannte Namen bleiben, wie Audible sie liefert.
GENRE_NAME = {
    # englisch
    "Science Fiction": "Science-Fiction", "Science Fiction & Fantasy": "Science-Fiction & Fantasy",
    "Literature & Fiction": "Belletristik", "Genre Fiction": "Unterhaltungsliteratur", "Action & Adventure": "Action & Abenteuer",
    "Thriller & Suspense": "Thriller & Spannung", "Mystery, Thriller & Suspense": "Krimis & Thriller", "Mystery": "Krimi",
    "Crime Fiction": "Krimi", "Fairy Tales, Folk Tales & Myths": "Märchen & Mythen", "Classics": "Klassiker",
    "Historical Fiction": "Historische Romane", "Historical": "Historisch", "Growing Up & Facts of Life": "Erwachsenwerden",
    "Romance": "Liebesromane", "Fantasy Romance": "Romantasy", "Romantic Comedy": "Romantische Komödie",
    "World Literature": "Weltliteratur", "Anthologies & Short Stories": "Anthologien & Kurzgeschichten",
    "Comedy & Humor": "Humor", "Literary History & Criticism": "Literaturgeschichte & -kritik",
    "Teen & Young Adult": "Jugendbücher", "Children's Audiobooks": "Kinderhörbücher", "Paranormal": "Übernatürliches",
    "Christianity": "Christentum", "Animals & Nature": "Tiere & Natur", "Biographies & Memoirs": "Biografien & Erinnerungen",
    "Ancient": "Antike", "Contemporary": "Gegenwart", "Philosophy": "Philosophie", "Royalty": "Königshäuser",
    "Audio Performances & Dramatizations": "Hörspiele & Lesungen", "Science": "Wissenschaft",
    "Geography & Cultures": "Geografie & Kulturen", "Music & Performing Arts": "Musik & darstellende Kunst",
    "Religious Studies": "Religionswissenschaft", "Spirituality": "Spiritualität", "Holidays & Celebrations": "Feiertage & Festlichkeiten",
    # deutsch von Audible.de, auf dieselben Namen
    "Literatur & Belletristik": "Belletristik", "Krimis": "Krimi", "Detektive & Spannung": "Krimi",
    "Auf- & Heranwachsen": "Erwachsenwerden", "Kinder": "Kinderhörbücher", "Jugendliche & Heranwachsende": "Jugendbücher",
    "Romantik": "Liebesromane", "Unterhaltung & Darstellende Künste": "Musik & darstellende Kunst",
}


def _genres(ladders: list[list[str]]) -> set[str]:
    """Zweite Ebene der Audible-Kategorien wie `audible.genres_of` («Fantasy», nicht «Science Fiction & Fantasy»)."""
    return {GENRE_NAME.get(n, n) for lad in ladders if lad for n in [lad[min(1, len(lad) - 1)]]}


def _merge(per_book: list[tuple[set[str], set[str], set[str]]]) -> dict:
    """Genres und Tags einer Karte aus ihren Büchern (Genres, sichere Tags, Tags aus der Beschreibung).
    Ein Genre oder ein Tag aus der Beschreibung zählt, wenn es mindestens die Hälfte der Bücher trägt (bei einem Buch
    dieses eine); das häufigste Genre zählt immer. Tags aus Titel, Kategorie und Goodreads zählen schon bei einem Buch."""
    n = len(per_book)
    need = max(1, (n + 1) // 2)
    g = Counter(x for gs, _, _ in per_book for x in gs)
    genres = {x for x, k in g.items() if k >= need}
    if g and not genres:
        genres = {g.most_common(1)[0][0]}
    sure = {x for _, ts, _ in per_book for x in ts}
    loose = Counter(x for _, _, ls in per_book for x in ls)
    return {"genres": sorted(genres), "tags": sorted(sure | {x for x, k in loose.items() if k >= need})}


KEY = "librarytags:2"  # neue Nummer, wenn sich das Ergebnis ändert (2: Genres deutsch)
_memo: dict = {"value": None}


def of_library() -> dict:
    """{"series": {id: {"genres", "tags"}}, "singles": {Schlüssel: {...}}}. Die Berechnung dauert einige Sekunden
    (Tag-Muster über alle Beschreibungen); darum rechnet sie die Aufgabe vorab und legt sie im Zwischenspeicher ab.
    Die Seite rechnet nur, wenn noch nichts gespeichert ist."""
    if _memo["value"] is None:
        row = db.conn().execute("SELECT value FROM cache WHERE key=?", (KEY,)).fetchone()
        if row:
            v = json.loads(row["value"])
            v["series"] = {int(k): x for k, x in v["series"].items()}
            _memo["value"] = v
        else:
            _store(_build())
    return _memo["value"]


def _store(value: dict) -> None:
    db.conn().execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (KEY, json.dumps(value), datetime.now(timezone.utc).isoformat()))
    _memo["value"] = value


def _build() -> dict:
    cards = _books()
    by_lang: dict[str, set[str]] = defaultdict(set)
    for items in cards.values():
        for lang, a in items:
            by_lang[lang].add(a)
    desc = {lang: _cached(lang, sorted(a)) for lang, a in by_lang.items()}
    gr = {lang: goodreads.cached(lang, sorted(a)) for lang, a in by_lang.items()}
    out: dict[str, dict] = {"series": {}, "singles": {}}
    for (kind, key), items in cards.items():
        per_book = []
        for lang, a in dict.fromkeys(items):
            d = desc[lang].get(a)
            if not d:
                continue
            t = tags.detect(d["title"], d["subtitle"], d["ladders"], d["summary"])
            per_book.append((_genres(d["ladders"]), t["title"] | t["category"] | set(gr[lang].get(a, [])), t["summary"]))
        if per_book:
            m = _merge(per_book)
            if m["genres"] or m["tags"]:
                out["series" if kind == "series" else "singles"][key] = m
    return out


def sync(progress=lambda msg: None) -> str:
    """Hintergrund-Aufgabe: fehlende oder über einen Monat alte Audible-Angaben holen, höchstens PER_RUN je Lauf."""
    c = db.conn()
    limit = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fresh = {r["key"] for r in c.execute("SELECT key FROM cache WHERE key LIKE 'desc:%' AND fetched_at > ?", (limit,))}
    todo: dict[str, list[str]] = defaultdict(list)
    for items in _books().values():
        for lang, a in items:
            if f"desc:{lang}:{a}" not in fresh and a not in todo[lang]:
                todo[lang].append(a)
    total = sum(len(v) for v in todo.values())
    done = 0
    for lang, asins in todo.items():
        part = asins[:PER_RUN - done]
        if not part:
            break
        progress(f"{len(part)} Bücher ({lang.upper()}) bei Audible")
        tags.fetch(lang, part)
        done += len(part)
    _store(_build())  # auch ohne Abfragen: neue Serien und Goodreads-Genres seit dem letzten Lauf
    return f"{done} Bücher bei Audible geholt, {total - done} offen"
