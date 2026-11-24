"""Goodreads-Genres als zweite Tag-Quelle neben Audible (beide kombinieren).

Goodreads sperrt Server-Abrufe nach wenigen Seiten; ABW holt die Seiten deshalb über FlareSolverr (rund 6 s je Buch)
und nur in einer Hintergrund-Aufgabe. «Für dich» und die Topliste lesen ausschliesslich den Zwischenspeicher.

Treffer: Goodreads-Suche als JSON (Autovervollständigung), gleicher Autoren-Nachname, meiste Bewertungen. Die normale
Suchseite taugt nicht (bei «Eragon» steht ein Sammelband mit Wikipedia-Artikeln vorn). Genres aus den Seitendaten
(`__NEXT_DATA__` → `bookGenres`), nicht aus allen Genre-Links: die ersten 27 sind das Seitenmenü.
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

from . import abs as abs_mod
from . import audible, config, db, flaresolverr

MAX_AGE_DAYS = 30
MISS_AGE_DAYS = 14  # nicht gefunden: nach zwei Wochen noch einmal versuchen
PER_RUN = 40  # Bücher je Lauf (rund 4 Minuten)

# Goodreads-Genre -> eigener Tag (Namen wie in tags.RULES/CATEGORY). Nicht aufgeführte Genres zählen nicht.
GENRE: dict[str, str] = {
    "Litrpg": "LitRPG", "Gamelit": "LitRPG", "Progression Fantasy": "Progression Fantasy", "Cultivation": "Cultivation",
    "Xianxia": "Cultivation", "Wuxia": "Cultivation", "Isekai": "Isekai", "Portal Fantasy": "Isekai", "Dungeon Core": "Dungeon Core",
    "Slice Of Life": "Slice of Life", "Cozy": "Cozy", "Cozy Fantasy": "Cozy", "Comedy": "Humor", "Humor": "Humor",
    "Dark Fantasy": "Grimdark", "Grimdark": "Grimdark", "High Fantasy": "Epic Fantasy", "Epic Fantasy": "Epic Fantasy",
    "Space Opera": "Space Opera", "Military Fiction": "Military", "Military Science Fiction": "Military",
    "Post Apocalyptic": "Apocalypse", "Apocalyptic": "Apocalypse", "Urban Fantasy": "Urban Fantasy", "Romantasy": "Romantasy",
    "Paranormal": "Paranormal", "Dragons": "Drachen", "Young Adult": "Young Adult", "Time Travel": "Time Travel",
    "Cyberpunk": "Cyberpunk", "Superheroes": "Superhelden", "Mystery": "Krimi", "Thriller": "Thriller", "Horror": "Horror",
    "Historical Fiction": "Historisch", "Sword and Sorcery": "Sword & Sorcery", "Magic School": "Academy",
    "Role Playing Games": "LitRPG", "Steampunk": "Steampunk", "Hard Science Fiction": "Hard SF",
}


def configured() -> bool:
    return bool(config.FLARESOLVERR_URL)


def _key(lang: str, asin: str) -> str:
    return f"gr:{lang}:{asin}"


def cached(lang: str, asins: list[str]) -> dict[str, list[str]]:
    """Goodreads-Genres (schon als eigene Tags) aus dem Zwischenspeicher; fehlende Bücher fehlen im Ergebnis."""
    c = db.conn()
    out = {}
    for a in asins:
        row = c.execute("SELECT value FROM cache WHERE key=?", (_key(lang, a),)).fetchone()
        if row:
            d = json.loads(row["value"])
            out[a] = sorted({GENRE[g] for g in d.get("genres", []) if g in GENRE})
    return out


def _surnames(author: str) -> set[str]:
    """Nachnamen aller Autoren im Feld («Nobody103, Domagoj Kurmaić» -> beide), damit auch Pseudonyme passen."""
    names = [n.strip() for n in re.split(r",|;|&| and | und ", author or "") if n.strip()]
    return {db.norm(n.split()[-1]) for n in names if db.norm(n.split()[-1])}


def _pick(results: list[dict], author: str, title: str = "") -> dict | None:
    """Treffer mit passendem Autor; bei mehreren Bänden der mit derselben Bandnummer, sonst der mit den meisten Bewertungen."""
    surs = _surnames(author)
    cand = [b for b in results if any(s in db.norm((b.get("author") or {}).get("name", "")) for s in surs)]
    nums = set(re.findall(r"\d+", title))
    return max(cand, key=lambda b: (bool(nums) and nums <= set(re.findall(r"\d+", b.get("title") or "")),
                                    int(b.get("ratingsCount") or 0))) if cand else None


def _search(q: str) -> list[dict]:
    raw = flaresolverr.fetch("https://www.goodreads.com/book/auto_complete?format=json&q=" + quote_plus(q))
    m = re.search(r"\[.*\]", raw, re.S)
    return json.loads(html.unescape(m.group(0))) if m else []


def lookup(title: str, author: str) -> dict:
    """Goodreads-Eintrag zu Titel und Autor: {"book_id", "title", "ratings", "avg", "genres"} oder {"none": True}."""
    head = re.split(r"[:(\[]", title)[0].strip()
    # Zuerst nur der Titel (mit Autor im Suchbegriff findet Goodreads Pseudonyme und manche Bände nicht), dann Titel und Autor
    hit = _pick(_search(head), author, title) or _pick(_search(f"{head} {abs_mod.primary_author(author or '')}"), author, title)
    if not hit:
        return {"none": True}
    genres: list[str] = []
    for _ in range(2):  # manchmal kommt die Seite unvollständig, dann einmal nachladen
        page = flaresolverr.fetch(f"https://www.goodreads.com/book/show/{hit['bookId']}")
        nd = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', page, re.S)
        gm = re.search(r'"bookGenres":(\[.*?\])\s*,\s*"', nd.group(1)) if nd else None
        genres = [g["genre"]["name"] for g in json.loads(gm.group(1))] if gm else []
        if genres:
            break
    d = {"book_id": hit["bookId"], "title": hit.get("title"), "ratings": int(hit.get("ratingsCount") or 0),
         "avg": float(hit.get("avgRating") or 0), "genres": genres}
    if not genres:
        d["none"] = True  # ohne Genres nützt der Eintrag nichts: nach zwei Wochen noch einmal
    return d


def wanted() -> list[tuple[str, str, str, str]]:
    """Bücher, deren Goodreads-Genres fehlen oder veraltet sind: zuerst gehörte (alle Benutzer), dann die Vorschläge, die
    «Für dich» zuletzt vorgemerkt hat. (Sprache, ASIN, Titel, Autor)"""
    c = db.conn()
    now = datetime.now(timezone.utc)
    fresh = {r["key"]: (r["fetched_at"], r["value"]) for r in c.execute("SELECT key, fetched_at, value FROM cache WHERE key LIKE 'gr:%'")}

    def due(lang: str, asin: str) -> bool:
        got = fresh.get(_key(lang, asin))
        if not got:
            return True
        age = MISS_AGE_DAYS if '"none"' in got[1] else MAX_AGE_DAYS
        return got[0] < (now - timedelta(days=age)).isoformat()

    out, seen = [], set()
    for r in c.execute("SELECT DISTINCT b.lang, b.asin, b.title, b.author FROM user_progress p JOIN book b ON b.id=p.book_id"
                       " WHERE (p.finished=1 OR p.progress>=0.25) AND b.asin IS NOT NULL ORDER BY p.updated DESC"):
        if audible.ASIN_OK.match(r["asin"]) and (r["lang"], r["asin"]) not in seen and due(r["lang"], r["asin"]):
            seen.add((r["lang"], r["asin"]))
            out.append((r["lang"], r["asin"], r["title"], r["author"] or ""))
    row = c.execute("SELECT value FROM cache WHERE key='gr:wanted'").fetchone()
    for lang, asin, title, author in json.loads(row["value"]) if row else []:
        if (lang, asin) not in seen and due(lang, asin):
            seen.add((lang, asin))
            out.append((lang, asin, title, author))
    return out


def remember_wanted(items: list[dict]) -> None:
    """«Für dich» merkt sich seine besten Vorschläge, damit die Hintergrund-Aufgabe deren Genres holt."""
    keep = [(i["lang"], i["asin"], i["title"], i.get("author") or "") for i in items if i.get("asin") and i.get("title")]
    db.conn().execute("INSERT OR REPLACE INTO cache VALUES('gr:wanted', ?, ?)", (json.dumps(keep), datetime.now(timezone.utc).isoformat()))


def sync(progress=lambda msg: None) -> str:
    """Hintergrund-Aufgabe: Goodreads-Genres für bis zu 40 Bücher holen."""
    if not configured():
        return "FlareSolverr ist nicht eingerichtet"
    todo = wanted()[:PER_RUN]
    c = db.conn()
    found = miss = errors = 0
    for i, (lang, asin, title, author) in enumerate(todo):
        progress(f"Buch {i + 1} von {len(todo)}: {title[:40]}")
        try:
            d = lookup(title, author)
        except Exception:
            errors += 1
            if errors >= 3:
                break  # Goodreads oder FlareSolverr gestört: nächster Lauf
            continue
        c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (_key(lang, asin), json.dumps(d), datetime.now(timezone.utc).isoformat()))
        found += not d.get("none")
        miss += bool(d.get("none"))
    rest = max(0, len(wanted()))
    return f"{found} Bücher mit Goodreads-Genres, {miss} nicht gefunden" + (f", {errors} Fehler" if errors else "") + f", {rest} offen"
