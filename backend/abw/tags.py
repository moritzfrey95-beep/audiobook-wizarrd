"""Eigene Tags (Unterarten) zu Büchern: LitRPG, Cultivation, Isekai ….

Audible ordnet fast alles, was hier gehört wird, unter «Fantasy» ein. Die Tags kommen aus drei Quellen:
- Titel und Untertitel («A LitRPG Adventure»),
- der dritten Ebene der Audible-Kategorien («Epic», «Action & Adventure», «Dragons & Mythical Creatures»),
- der Verlagsbeschreibung (Stichwörter, deutsch und englisch).
Je Buch einen Monat zwischengespeichert. Dazu kommen Goodreads-Genres (`goodreads.py`).
"""
import html
import json
import re
from datetime import datetime, timedelta, timezone

from . import audible, db

# Tag: Muster (klein geschrieben, ohne HTML). Wortgrenzen bewusst, damit «classic» nicht «class» trifft.
RULES: dict[str, list[str]] = {
    "LitRPG": [r"\blit ?rpg\b", r"\bgame ?lit\b", r"\bgamelit\b", r"\blevel(?:s|ed|ing)? up\b", r"\bstat (?:points|screen|sheet)s?\b",
               r"\bskill points?\b", r"\bthe system\b", r"\bsystem (?:messages?|notifications?|interface)\b", r"\bblue boxes\b",
               r"\bvrmmo\b", r"\bmmorpg\b", r"\bvirtual reality game\b"],
    "Progression Fantasy": [r"\bprogression fantasy\b", r"\bprogression\b", r"\bweak[- ]to[- ]strong\b", r"\bclimb(?:s|ing)? the ranks\b"],
    "Cultivation": [r"\bcultivat(?:ion|or|ors|e|es|ing)\b", r"\bxianxia\b", r"\bwuxia\b", r"\bqi\b", r"\bsects?\b", r"\bdao\b",
                    r"\bmartial arts?\b", r"\bkultivier"],
    "Isekai": [r"\bisekai\b", r"\breincarnat", r"\breborn\b", r"\btransported to\b", r"\banother world\b",
               r"\bsummoned to\b", r"\bportal fantasy\b", r"\bwiedergeboren\b", r"\banderen welt\b"],
    "Dungeon Core": [r"\bdungeon core\b", r"\bdungeon heart\b"],
    "Dungeon Crawl": [r"\bdungeon crawl", r"\bdungeon div", r"\btower\b.*\bfloors?\b", r"\bfloors? of the (?:tower|dungeon)\b"],
    "Base Building": [r"\bbase[- ]building\b", r"\bkingdom[- ]building\b", r"\btown[- ]building\b", r"\bsettlement\b",
                      r"\bbuild(?:s|ing)? (?:a|his|her|their) (?:town|village|kingdom|base|empire)\b"],
    "Crafting": [r"\bcrafting\b", r"\bcrafter\b", r"\bblacksmith", r"\balchem", r"\benchant(?:er|ing|ment)", r"\bsmith(?:ing)?\b"],
    "Summoner": [r"\bsummoner\b", r"\bsummoning\b", r"\bnecromancer\b", r"\bbeast tamer\b", r"\bmonster tamer\b"],
    "Apocalypse": [r"\bapocalyp", r"\bend of the world\b", r"\bsystem (?:arrived|came|integration)\b", r"\bweltuntergang"],
    "Academy": [r"\bacadem(?:y|ies)\b", r"\bmagic school\b", r"\buniversity\b", r"\bschool of magic\b", r"\bakademie\b"],
    "Monster MC": [r"\breincarnated as an? (?:\w+ )?(?:monster|slime|spider|ant|dragon|goblin|skeleton)\b",
                   r"\bmonster evolution\b", r"\bmonster mc\b"],
    "Time Loop": [r"\btime loop\b", r"\bloop(?:s|ing)? back\b", r"\bzeitschleife\b"],
    "Cozy": [r"\bcozy\b", r"\bcosy\b", r"\bslice of life\b", r"\blow[- ]stakes\b", r"\bheartwarming\b", r"\bgemütlich"],
    "Humor": [r"\bhilarious\b", r"\bcomedy\b", r"\bcomedic\b", r"\bhumou?r(?:ous)?\b", r"\blaugh[- ]out[- ]loud\b"],
    "Grimdark": [r"\bgrimdark\b", r"\bdark fantasy\b"],
    "Harem": [r"\bharem\b"],
    "Space Opera": [r"\bspace opera\b", r"\bstarship\b", r"\bgalactic empire\b"],
}
# Dritte Ebene der Audible-Kategorien -> Tag (Audible.de liefert teils deutsche Namen)
CATEGORY: dict[str, str] = {
    "Epic": "Epic Fantasy", "Episch": "Epic Fantasy", "Action & Adventure": "Action", "Dragons & Mythical Creatures": "Drachen",
    "Space Opera": "Space Opera", "Military": "Military", "Militär": "Military", "Post-Apocalyptic": "Apocalypse",
    "Humorous": "Humor", "Humor": "Humor", "Dark Fantasy": "Grimdark", "Sword & Sorcery": "Sword & Sorcery",
    "Urban": "Urban Fantasy", "Paranormal": "Paranormal", "Romantic": "Romantasy", "Gaming": "LitRPG",
    "LitRPG": "LitRPG", "Cyberpunk": "Cyberpunk", "Hard Science Fiction": "Hard SF", "Time Travel": "Time Travel",
    "Alien Invasion": "Aliens", "First Contact": "Aliens", "Galactic Empire": "Space Opera", "Dystopian": "Dystopie",
    "Historical": "Historisch", "Coming of Age": "Coming of Age", "Superhero": "Superhelden",
}
# Muster, die nur in Titel/Untertitel zählen (in Beschreibungen zu häufig)
TITLE_ONLY = {"Progression Fantasy": [r"\bprogression\b"], "Summoner": [r"\bnecromancer\b"]}
# Allgemeine Tags: gut für die Topliste, zu breit für Empfehlungen
GENERIC = {"Epic Fantasy", "Action", "Drachen"}


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", text or ""))).strip().lower()


def detect(title: str | None, subtitle: str | None, ladders: list[list[str]], summary: str | None) -> dict[str, set[str]]:
    """Tags je Quelle: {"title": {...}, "category": {...}, "summary": {...}}."""
    head, body = clean(f"{title or ''} {subtitle or ''}"), clean(summary)
    out = {"title": set(), "category": set(), "summary": set()}
    for tag, pats in RULES.items():
        weak = set(TITLE_ONLY.get(tag, []))
        if any(re.search(p, head) for p in pats):
            out["title"].add(tag)
        if any(re.search(p, body) for p in pats if p not in weak):
            out["summary"].add(tag)
    fantasy = any(len(lad) > 1 and lad[1] == "Fantasy" for lad in ladders)
    for lad in ladders:
        for name in lad[2:]:
            if name == "Cyberpunk":
                # Audible legt LitRPG unter Science Fiction › Cyberpunk ab, meist zusammen mit Fantasy (Analyse 20.11.)
                out["category"].add("LitRPG" if fantasy else "Cyberpunk")
            elif name in CATEGORY:
                out["category"].add(CATEGORY[name])
    return out


def fetch(lang: str, asins: list[str]) -> dict[str, dict]:
    """Titel, Untertitel, Kategorien und Beschreibung je ASIN, einen Monat zwischengespeichert."""
    c = db.conn()
    limit = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    out, todo = {}, []
    for a in dict.fromkeys(asins):
        row = c.execute("SELECT value FROM cache WHERE key=? AND fetched_at > ?", (f"desc:{lang}:{a}", limit)).fetchone()
        if row:
            out[a] = json.loads(row["value"])
        else:
            todo.append(a)
    now = datetime.now(timezone.utc).isoformat()
    for i in range(0, len(todo), 50):
        d = audible._get(lang, "/1.0/catalog/products", asins=",".join(todo[i:i + 50]),
                         response_groups="product_attrs,product_desc,product_extended_attrs,category_ladders")
        got = {p["asin"]: p for p in d.get("products", []) if p.get("asin")}
        for a in todo[i:i + 50]:
            p = got.get(a) or {}
            out[a] = {"title": p.get("title"), "subtitle": p.get("subtitle"), "summary": clean(p.get("publisher_summary"))[:4000],
                      "ladders": [[n["name"] for n in lad.get("ladder") or []] for lad in p.get("category_ladders") or [] if lad.get("root") == "Genres"]}
            c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (f"desc:{lang}:{a}", json.dumps(out[a]), now))
    return out


def of_books(lang: str, asins: list[str], use_summary: bool = True) -> dict[str, set[str]]:
    """Tags je ASIN (alle Quellen zusammen): Audible (Titel, Kategorie, Beschreibung) und Goodreads-Genres aus dem
    Zwischenspeicher (beide kombinieren; geholt werden sie nur von der Hintergrund-Aufgabe)."""
    from . import goodreads
    gr = goodreads.cached(lang, asins)
    out = {}
    for a, d in fetch(lang, asins).items():
        t = detect(d["title"], d["subtitle"], d["ladders"], d["summary"] if use_summary else "")
        out[a] = t["title"] | t["category"] | t["summary"] | set(gr.get(a, []))
    return out
