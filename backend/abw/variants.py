"""Fassungen innerhalb einer Sprache: andere Sprecher, Hörspiel, gekürzte Ausgabe.

Eine Fassung ist eine eigene Zeile in `edition`, hängt an derselben Serie und steht auf der Karte unter der Sprache,
so wie Deutsch und Englisch. Spalten:
- `variant`: Name der zusätzlichen Fassung («Rob Inglis», «BBC-Hörspiel», «Gekürzt»). NULL = Hauptfassung der Sprache.
- `narr`: Sprecher, an den die Fassung gebunden ist (Audible-Abgleich, Suche, Ankunftsprüfung, Ordnername).
- `base_id`: Hauptfassung, aus deren Audiobookshelf-Serie die Bücher dieser Sprecher-Fassung kommen.
- `vlock`: 1 = von Hand getrennt, ABW ordnet hier nichts mehr automatisch zu.

Zwei Wege führen zu einer Fassung:
1. Aufteilen: In einer Serie liegen mehrere Bände doppelt mit verschiedenen Sprechern (Herr der Ringe: Serkis und
   Inglis). Die grösste Sprechergruppe bleibt die Hauptfassung, jede weitere mit mindestens zwei Büchern wird eine Fassung.
2. Zusammenlegen: Eine eigene Karte, deren Name eine Fassung meint («BBC Radio's The Lord of the Rings»,
   «Harry Potter - Gesprochen von Rufus Beck», «… [Hörspiel]», «… (abridged)», «… - Phil Dragash»), wandert als Fassung
   zur gleichnamigen Serie.
Hörbuchdateien und Audiobookshelf bleiben dabei unverändert; es ändert sich nur die Zuordnung in ABW."""
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from . import db

DRAMA_LABELS = {"BBC-Hörspiel", "Hörspiel", "GraphicAudio", "Full Cast"}


def narr_key(narrator: str | None) -> str:
    """Vergleichsschlüssel des ersten Sprechers: «Andy Serkis, Tolkien» -> «andyserkis»."""
    first = next((p.strip() for p in re.split(r",|&|\bund\b|\band\b", narrator or "") if p.strip()), "")
    return db.norm(first)


def narr_keys(narrator: str | None) -> set[str]:
    """Alle Sprecher eines Eintrags (Audible nennt bei Hörspielen viele)."""
    return {k for p in re.split(r",|&|\bund\b|\band\b", narrator or "") if (k := db.norm(p.strip()))}


def surname(narrator: str | None) -> str:
    first = next((p.strip() for p in (narrator or "").split(",") if p.strip()), "")
    words = re.findall(r"[^\W\d_]+", first.lower())
    return words[-1] if words else ""


def label(e) -> str | None:
    """Anzeigename der Fassung unter der Sprache, z. B. «Andy Serkis»; None für eine Hauptfassung ohne Bindung."""
    return e["variant"] or e["narr"] or None


# ---------- Bestandsabgleich ----------

PART = re.compile(r"[(\[]\s*(?:part\s+|teil\s+)?(\d+)\s+(?:of|von)\s+(\d+)\s*[)\]]|\b(?:part|teil)\s+(\d+)\s+(?:of|von)\s+(\d+)\b", re.I)


def part_of(title: str | None) -> int | None:
    """Teil eines auf mehrere Ausgaben verteilten Bandes: «Light Bringer (1 of 3) [Dramatized Adaptation]» -> 1."""
    m = PART.search(title or "")
    if not m:
        return None
    n, of = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(3)), int(m.group(4)))
    return n if 1 <= n <= of <= 20 else None


def pick_row(rows, asin: str | None, title: str | None):
    """Unter den fehlenden Einträgen mit gleicher ASIN oder Bandnummer den richtigen: zuerst die ASIN, sonst derselbe Teil."""
    rows = list(rows)
    hit = next((r for r in rows if asin and r["asin"] == asin), None)
    if hit is not None:
        return hit
    part = part_of(title)
    return next((r for r in rows if part_of(r["title"]) == part), None)

DRAMA_KEY = "hoerspiel"


def is_drama(title: str | None, narrator: str | None) -> bool:
    """Hörspiel- oder Full-Cast-Ausgabe: «Red Rising (Part 1 of 2) (Dramatized Adaptation)», Sprecher «Full Cast»."""
    from . import scoring
    return bool(scoring.DRAMA.search(scoring.plain(title or ""))) or "full cast" in (narrator or "").lower()


def cluster_key(title: str | None, narrator: str | None) -> str:
    return DRAMA_KEY if is_drama(title, narrator) else narr_key(narrator)


def title_core(title: str | None, series: str | None) -> str:
    """Titel ohne Seriennamen und ohne Klammern, zum Erkennen desselben Buchs in zwei Fassungen."""
    from . import titles
    t = titles.display(title or "", series)
    t = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", t)
    return db.norm(t.split(":")[0]) if len(db.norm(t.split(":")[0])) >= 4 else db.norm(t)


def same_title(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or (min(len(a), len(b)) >= 6 and (a in b or b in a)))


def route(c, edition_id: int | None, narrator: str | None, title: str | None = None) -> int | None:
    """Buch aus der Audiobookshelf-Serie einer Hauptfassung: gehört es nach Sprecher (oder als Hörspiel) zu einer ihrer
    Fassungen, dorthin."""
    if not edition_id:
        return edition_id
    key = cluster_key(title, narrator)
    if not key:
        return edition_id
    for v in c.execute("SELECT id, narr FROM edition WHERE base_id=?", (edition_id,)):
        if (narr_key(v["narr"]) if v["narr"] else DRAMA_KEY) == key:
            return v["id"]
    return edition_id


def split_plan(c, e) -> dict | None:
    """Soll die Fassung aufgeteilt werden? Bedingung: Eine zweite Sprechergruppe (oder die Hörspiele) mit mindestens zwei
    Büchern hat mindestens einen Band mit demselben Titel wie die Hauptgruppe. Andere Bücher unter derselben Nummer
    (Halo, Book of Dust) sind keine Fassung. Liefert {'keep', 'keep_name', 'parts': {Schlüssel: [Bücher]}, 'names'}."""
    cl: dict[str, list] = {}
    for b in c.execute("SELECT * FROM book WHERE edition_id=? AND owned=1", (e["id"],)):
        if (k := cluster_key(b["title"], b["narrator"])):
            cl.setdefault(k, []).append(b)
    big = {k: bl for k, bl in cl.items() if len(bl) >= 2}
    if len(big) < 2:
        return None
    core = {k: {(b["seq"], title_core(b["title"], e["name"])) for b in bl if b["seq"] is not None} for k, bl in big.items()}

    def twins(a, b):
        return any(sa == sb and same_title(ta, tb) for sa, ta in core[a] for sb, tb in core[b])
    # Hauptgruppe: die grösste Sprechergruppe; Hörspiele werden nie Hauptfassung
    keys = sorted(big, key=lambda k: (k == DRAMA_KEY, -len(big[k]), -sum(b["runtime_min"] or 0 for b in big[k]), k))
    # erste Gruppe, die überhaupt Doppel hat (bei His Dark Materials hat Book of Dust keine, Ruth Wilson schon)
    keep, parts = next(((k, p) for k in keys if k != DRAMA_KEY and (p := {o: big[o] for o in keys if o != k and twins(k, o)})), (None, None))
    if not parts:
        return None
    name = lambda k: "Hörspiel" if k == DRAMA_KEY else Counter(b["narrator"].split(",")[0].strip() for b in big[k]).most_common(1)[0][0]
    return {"keep": keep, "keep_name": name(keep), "parts": parts, "names": {k: name(k) for k in [keep, *parts]}}


def apply_split(c, e, plan: dict) -> list[int]:
    """Aufteilung umsetzen: Hauptfassung bekommt ihren Sprecher als Namen, je weitere Gruppe eine Fassung mit deren
    Büchern (vorhandene und gesuchte). Downloads hängen an den Büchern und ziehen mit."""
    # Name der Hauptfassung nur, wenn ihr Sprecher wirklich die meisten ihrer Bücher liest (bei His Dark Materials nicht)
    rest = [b for b in c.execute("SELECT narrator, title FROM book WHERE edition_id=? AND owned=1", (e["id"],))]
    moved = sum(len(b) for b in plan["parts"].values())
    mine = sum(cluster_key(b["title"], b["narrator"]) == plan["keep"] for b in rest)
    c.execute("UPDATE edition SET narr=? WHERE id=?", (plan["keep_name"] if mine * 2 > len(rest) - moved else None, e["id"]))
    made = []
    for k, books in plan["parts"].items():
        nm = plan["names"][k]
        narr = None if k == DRAMA_KEY else nm
        ex = c.execute("SELECT id FROM edition WHERE base_id=? AND variant=?", (e["id"], nm)).fetchone()
        vid = ex["id"] if ex else c.execute(
            "INSERT INTO edition(series_id, lang, name, norm, audible_asin, monitored, link_source, variant, narr, base_id, drama)"
            " VALUES(?,?,?,?,?,?,'variant',?,?,?,?)",
            (e["series_id"], e["lang"], e["name"], f"{e['norm']}~{k}", e["audible_asin"], e["monitored"], nm, narr, e["id"],
             1 if k == DRAMA_KEY else None)).lastrowid
        ids = [b["id"] for b in books]
        c.execute(f"UPDATE book SET edition_id=? WHERE id IN ({','.join('?' * len(ids))})", (vid, *ids))
        for b in c.execute("SELECT id, title, narrator FROM book WHERE edition_id=? AND owned=0", (e["id"],)).fetchall():
            if cluster_key(b["title"], b["narrator"]) == k:
                c.execute("UPDATE book SET edition_id=? WHERE id=?", (vid, b["id"]))
        made.append(vid)
    return made


def merge_filter(c, ed):
    """Welche Audible-Einträge gehören beim Abgleich zu dieser Fassung? Sprecher-Fassung: nur ihr Sprecher.
    Hörspiel-Fassung: nur Hörspiele. Hauptfassung mit weiteren Fassungen: alles ausser deren Sprechern und Hörspielen."""
    keys = ed.keys()
    variant, narr, base = (ed["variant"] if "variant" in keys else None), (ed["narr"] if "narr" in keys else None), \
        (ed["base_id"] if "base_id" in keys else None)
    if variant and narr:
        k = narr_key(narr)
        return lambda ch: k in narr_keys(ch.get("narrator"))
    if base:
        return lambda ch: is_drama(ch.get("title"), ch.get("narrator")) or ch.get("content_type") == "Performance"
    if variant:
        return lambda ch: True
    others = c.execute("SELECT narr, base_id FROM edition WHERE series_id=? AND lang=? AND id<>? AND variant IS NOT NULL",
                       (ed["series_id"], ed["lang"], ed["id"])).fetchall()
    if not others:
        return lambda ch: True
    ks = {narr_key(o["narr"]) for o in others if o["narr"]}
    drama = any(not o["narr"] and o["base_id"] == ed["id"] for o in others)
    return lambda ch: not (ks & narr_keys(ch.get("narrator"))) and not (drama and is_drama(ch.get("title"), ch.get("narrator")))


# Kennzeichen einer Fassung im Seriennamen: (Muster, Bezeichnung oder None = Sprecher aus der Gruppe «n»)
MARKERS = [
    (re.compile(r"^(?P<base>.+?)\s*[(\[]\s*(?:narrated|read|performed)\s+by\s+(?P<n>[^)\]]+)[)\]]\s*$", re.I), None),
    (re.compile(r"^(?P<base>.+?)\s*[(\[]\s*(?:gelesen|gesprochen)\s+von\s+(?P<n>[^)\]]+)[)\]]\s*$", re.I), None),
    (re.compile(r"^(?P<base>.+?)\s+[-–]\s+(?:gelesen|gesprochen)\s+von\s+(?P<n>.+)$", re.I), None),
    (re.compile(r"^(?P<base>.+?)\s+[-–]\s+(?:narrated|read)\s+by\s+(?P<n>.+)$", re.I), None),
    (re.compile(r"^BBC\s+Radio(?:\s*['’]s)?\s+(?P<base>.+)$", re.I), "BBC-Hörspiel"),
    (re.compile(r"^(?P<base>.+?)\s*[(\[][^)\]]*\bBBC\b[^)\]]*[)\]]\s*$", re.I), "BBC-Hörspiel"),
    (re.compile(r"^(?P<base>.+?)\s*[(\[]\s*graphic\s*audio[^)\]]*[)\]]\s*$", re.I), "GraphicAudio"),
    (re.compile(r"^(?P<base>.+?)\s*[(\[]\s*full[\s-]*cast[^)\]]*[)\]]\s*$", re.I), "Full Cast"),
    (re.compile(r"^(?P<base>.+?)\s*[(\[]\s*(?:hörspiel|hoerspiel|radio\s*(?:drama|play)|full\s*cast|dramati[sz]ed)[^)\]]*[)\]]\s*$", re.I), "Hörspiel"),
    (re.compile(r"^(?P<base>.+?)\s+[-–]\s+(?:das\s+)?hörspiel\s*$", re.I), "Hörspiel"),
    (re.compile(r"^(?P<base>.+?)\s*[(\[]\s*(?:abridged|gekürzt)\s*[)\]]\s*$", re.I), "Gekürzt"),
]
DASH_NAME = re.compile(r"^(?P<base>.+?)\s+[-–]\s+(?P<n>[^-–]+?)\s*$")


def card_marker(c, e) -> dict | None:
    """Meint der Name dieser Karte eine bestimmte Fassung? Liefert {'base', 'variant', 'narr'} oder None."""
    name = (e["name"] or "").strip()
    for rx, lab in MARKERS:
        m = rx.match(name)
        if m:
            n = m.groupdict().get("n")
            n = n.strip() if n else None
            return {"base": m.group("base").strip(" -–:"), "variant": lab or n, "narr": n}
    # «Serie - Sprecher» nur, wenn der Zusatz wirklich der Sprecher der meisten Bücher ist («Ken Follett - Die Nadel» nicht)
    m = DASH_NAME.match(name)
    if m:
        keys = [narr_key(b["narrator"]) for b in c.execute("SELECT narrator FROM book WHERE edition_id=? AND owned=1", (e["id"],))]
        k = db.norm(m.group("n"))
        if keys and k and keys.count(k) * 2 >= len(keys):
            return {"base": m.group("base").strip(), "variant": m.group("n").strip(), "narr": m.group("n").strip()}
    return None


def card_target(c, e, base: str):
    """Hauptfassung derselben Sprache mit dem Namen ohne Zusatz, auf einer anderen Karte."""
    key = db.norm(base)
    if not key:
        return None
    return c.execute("SELECT * FROM edition WHERE lang=? AND norm=? AND variant IS NULL AND id<>? AND series_id<>?",
                     (e["lang"], key, e["id"], e["series_id"])).fetchone()


def move_series_refs(c, old: int, new: int) -> None:
    """Glocke, Merkliste und Automatik-Einstellung einer aufgelösten Karte gehen an die Karte, die bleibt."""
    for t in ("follow", "auto_follow"):
        c.execute(f"INSERT OR IGNORE INTO {t}(user_id, kind, key, created) SELECT user_id, kind, ?, created FROM {t}"
                  " WHERE kind='series' AND key=?", (str(new), str(old)))
        c.execute(f"DELETE FROM {t} WHERE kind='series' AND key=?", (str(old),))
    c.execute("UPDATE watchlist SET series_id=? WHERE series_id=?", (new, old))
    o = c.execute("SELECT auto, prio FROM series WHERE id=?", (old,)).fetchone()
    if o:
        c.execute("UPDATE series SET auto=COALESCE(auto, ?), prio=COALESCE(prio, ?) WHERE id=?", (o["auto"], o["prio"], new))


def apply_card(c, e, mk: dict, target) -> None:
    if mk["narr"]:
        c.execute("UPDATE edition SET narr=? WHERE id=?", (mk["narr"], e["id"]))
    if target is None:
        return  # keine Hauptserie gefunden: Karte bleibt, ist aber an den Sprecher gebunden
    old = e["series_id"]
    c.execute("UPDATE edition SET series_id=?, variant=? WHERE id=?", (target["series_id"], mk["variant"], e["id"]))
    if mk["variant"] in DRAMA_LABELS:
        c.execute("UPDATE edition SET drama=1 WHERE id=? AND drama IS NULL", (e["id"],))
    if not c.execute("SELECT 1 FROM edition WHERE series_id=?", (old,)).fetchone():
        move_series_refs(c, old, target["series_id"])
        c.execute("DELETE FROM series WHERE id=?", (old,))


def main_narr(c, edition_id: int) -> str:
    """Sprecher der meisten vorhandenen Bücher einer Fassung (Schlüssel), sonst leer."""
    keys = Counter(narr_key(b["narrator"]) for b in c.execute("SELECT narrator FROM book WHERE edition_id=? AND owned=1", (edition_id,)))
    k, n = keys.most_common(1)[0] if keys else ("", 0)
    return k if k and n * 2 > sum(keys.values()) else ""


# Einzelbuch, das ein Hörspiel einer Serie ist: «Der Herr der Ringe. Das Hörspiel», «Narnia (Hörspiel)»
SOLO_DRAMA = re.compile(r"^(?P<base>.+?)\s*(?:[.:\-–]\s*(?:das\s+|the\s+)?(?:hörspiel|hoerspiel|radio\s*drama|full[\s-]*cast(?:\s+edition)?)"
                        r"|[(\[]\s*(?:hörspiel|hoerspiel|radio\s*drama|dramati[sz]ed[^)\]]*|full[\s-]*cast[^)\]]*)[)\]])\s*$", re.I)


def solo_target(c, lang: str, title: str | None):
    """Hauptfassung der Serie, zu der ein Hörspiel ohne Serie in Audiobookshelf gehört (gleicher Name in derselben Sprache)."""
    m = SOLO_DRAMA.match((title or "").strip())
    if not m or not db.norm(m.group("base")):
        return None
    return c.execute("SELECT * FROM edition WHERE lang=? AND norm=? AND variant IS NULL AND COALESCE(vlock,0)=0",
                     (lang, db.norm(m.group("base")))).fetchone()


def solo_edition(c, main) -> int:
    """Hörspiel-Fassung einer Hauptfassung (anlegen, falls es sie noch nicht gibt)."""
    ex = c.execute("SELECT id FROM edition WHERE base_id=? AND variant='Hörspiel'", (main["id"],)).fetchone()
    if ex:
        return ex["id"]
    return c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, link_source, variant, base_id, drama)"
                     " VALUES(?,?,?,?,?,'variant','Hörspiel',?,1)",
                     (main["series_id"], main["lang"], main["name"], f"{main['norm']}~{DRAMA_KEY}", main["audible_asin"], main["id"])).lastrowid


def plan_all(c) -> dict:
    """Was würde zugeordnet? Ändert nichts. {'split': [(Fassung, Plan)], 'cards': [(Fassung, Kennzeichen, Ziel)]}."""
    out = {"split": [], "cards": []}
    for e in c.execute("SELECT * FROM edition WHERE variant IS NULL AND base_id IS NULL AND COALESCE(vlock,0)=0").fetchall():
        mk = card_marker(c, e)
        if mk and c.execute("SELECT 1 FROM edition WHERE series_id=? AND id<>?", (e["series_id"], e["id"])).fetchone():
            continue  # Karte ist schon mit einer anderen Sprache verknüpft: nicht auseinanderreissen (Harry Potter, 18.11.)
        if mk:
            t = card_target(c, e, mk["base"])
            if t is not None and mk["narr"] and narr_key(mk["narr"]) == main_narr(c, t["id"]):
                out.setdefault("same", []).append((e, mk, t))  # gleicher Sprecher wie die Hauptkarte: keine andere Fassung
                continue
            if t is not None or (mk["narr"] and not e["narr"]):
                out["cards"].append((e, mk, t))
            continue
        p = split_plan(c, e)
        if p:
            out["split"].append((e, p))
    return out


def assign(c) -> str:
    """Nach dem Bestandsabgleich: neue Fassungen erkennen und zuordnen. Läuft in der Transaktion des Abgleichs."""
    p = plan_all(c)
    for e, mk, t in p["cards"]:
        apply_card(c, e, mk, t)
    for e, sp in p["split"]:
        apply_split(c, e, sp)
    solo = 0
    for b in c.execute("SELECT id, lang, title FROM book WHERE edition_id IS NULL AND owned=1").fetchall():
        if (t := solo_target(c, b["lang"], b["title"])) is not None:
            c.execute("UPDATE book SET edition_id=? WHERE id=?", (solo_edition(c, t), b["id"]))
            solo += 1
    return f"{len(p['split'])} aufgeteilt, {len(p['cards'])} Karten zugeordnet, {solo} Hörspiele ohne Serie"


def detach(c, edition_id: int) -> None:
    """Von Hand trennen. Aufgeteilte Sprecher-Fassung: Bücher zurück in die Hauptfassung, die dann nicht mehr aufgeteilt
    wird. Zusammengelegte Karte: wieder eigene Karte, ohne erneutes Zusammenlegen."""
    e = c.execute("SELECT * FROM edition WHERE id=?", (edition_id,)).fetchone()
    if e["base_id"]:
        # Hörspiele ohne Serie in Audiobookshelf werden wieder Einzelbücher, die übrigen gehen zurück in die Hauptfassung
        for b in c.execute("SELECT id, seq, title FROM book WHERE edition_id=?", (edition_id,)).fetchall():
            solo = b["seq"] is None and SOLO_DRAMA.match(b["title"] or "")
            c.execute("UPDATE book SET edition_id=? WHERE id=?", (None if solo else e["base_id"], b["id"]))
        c.execute("DELETE FROM edition WHERE id=?", (edition_id,))
        c.execute("UPDATE edition SET vlock=1, narr=CASE WHEN EXISTS(SELECT 1 FROM edition WHERE base_id=?) THEN narr ELSE NULL END WHERE id=?",
                  (e["base_id"], e["base_id"]))
    else:
        c.execute("UPDATE edition SET variant=NULL, vlock=1 WHERE id=?", (edition_id,))


# ---------- Suche und Prüfung ----------

def context(c, edition_id: int | None) -> dict:
    """Angaben zur Fassung für Suche und Ankunftsprüfung: gewünschter Sprecher, Sprecher der übrigen Fassungen derselben
    Sprache, Name der Fassung."""
    if not edition_id:
        return {}
    e = c.execute("SELECT * FROM edition WHERE id=?", (edition_id,)).fetchone()
    if not e:
        return {}
    others = [r["narr"] for r in c.execute("SELECT narr FROM edition WHERE series_id=? AND lang=? AND id<>? AND narr IS NOT NULL",
                                           (e["series_id"], e["lang"], edition_id)) if narr_key(r["narr"]) != narr_key(e["narr"])]
    return {"want_narr": e["narr"], "other_narrs": others, "variant": e["variant"]}


def book_context(c, b) -> dict:
    """Wie context(), aber mit dem Sprecher des gesuchten Buchs selbst, wenn er bekannt ist (Audible nennt ihn je Ausgabe;
    beim Ersetzen ist es der Sprecher des vorhandenen Buchs). Der Sprecher der Fassung gilt nur, wenn das Buch keinen hat."""
    ctx = context(c, b["edition_id"])
    if not ctx:
        return ctx
    own = next((p.strip() for p in (b["narrator"] or "").split(",") if p.strip()), "")
    if own and not is_drama(b["title"], b["narrator"]) and (ctx["want_narr"] or ctx["other_narrs"]):
        ctx["want_narr"] = own
        ctx["other_narrs"] = [o for o in ctx["other_narrs"] if narr_key(o) != narr_key(own)]
    return ctx


# «Gelesen von Felix von Manteuffel», «narrated by Andy Serkis»: ausdrücklich genannter Sprecher (bis drei Namensteile)
_NAME = r"(?:[A-ZÄÖÜ][\w'’-]*\.?|von|van|de|der|du|la)"
# «gelesen von», «read for you by», bei Lübbe auch «Es liest …»
NAMED = re.compile(r"(?:(?i:gelesen|gesprochen|erzählt|read|narrated|performed)(?i:\s+for\s+you)?\s+(?i:von|by)|(?i:es\s+(?:liest|lesen|spricht|sprechen)))\s+("
                   + _NAME + r"(?:\s+" + _NAME + r"){0,2})")


def _fold(word: str) -> str:
    """Schreibweise für den unscharfen Vergleich: ohne Akzente, Doppelbuchstaben einfach («Mantäufel» ~ «Manteuffel»)."""
    w = unicodedata.normalize("NFKD", word.lower().replace("ß", "ss"))
    return re.sub(r"(.)\1+", r"\1", "".join(ch for ch in w if not unicodedata.combining(ch)))


def _words(text: str) -> set[str]:
    return {_fold(w) for w in re.findall(r"[^\W\d_]+", text or "")}


def name_in(name: str | None, words: set[str]) -> bool:
    """Steht der Nachname in den (gefalteten) Wörtern? Ab fünf Buchstaben darf die Spracherkennung leicht danebenliegen."""
    s = _fold(surname(name))
    if len(s) < 5:
        return bool(s) and s in words
    return any(abs(len(w) - len(s)) <= 2 and SequenceMatcher(None, s, w).ratio() >= 0.8 for w in words)


# Wörter, die in der Ansage auf einen Namen folgen und nicht dazugehören: «Read by Peter Berkrot Chapter One»
_AFTER_NAME = {"chapter", "kapitel", "part", "teil", "prologue", "prolog", "recap", "previously", "summary", "dedication",
               "copyright", "for", "this", "edited", "produced", "and", "und", "with", "mit", "book", "buch", "the", "spring", "star"}


def similarity(heard: str, name: str) -> float:
    """Ähnlichkeit zweier Namen (0..1), gefaltet und ohne Leerzeichen. Hörfehler der Spracherkennung («Travis Bouldery»,
    «Felix von Mantäufel») liegen über 0,7, verschiedene Sprecher («Rufus Beck» / «Daniel Brühl») unter 0,4
    (gemessen an rund 1300 Ankünften, 18.11.2026)."""
    f = lambda n: "".join(_fold(w) for w in re.findall(r"[^\W\d_]+", n))
    return SequenceMatcher(None, f(heard), f(name)).ratio()


def _shares_name(heard: str, name: str) -> bool:
    """Vor- oder Nachname ähnlich. Whisper verhört meist nur einen Teil (Bill Nye für Bill Nighy, Ida für Aida),
    bei Joachim Kerzel gegen Tobias Kluckert passt keiner."""
    h, n = heard.split(), name.split()
    return similarity(h[0], n[0]) >= 0.5 or similarity(h[-1], n[-1]) >= 0.5


def named_narrators(text: str) -> list[str]:
    """Ausdrücklich genannte Sprecher («gelesen von …», «narrated by …») ohne angehängte Füllwörter. Ein Name ganz am
    Textende kann abgeschnitten sein (die Ansage endet nach 75 Sekunden) und zählt nicht, ebenso ein Vorname allein."""
    out, text = [], text or ""
    for m in NAMED.finditer(text):
        raw = m.group(1).split(". ")[0]
        words = raw.rstrip(".").split()
        while words and words[-1].lower().strip(".") in _AFTER_NAME:
            words.pop()
        cut = m.end() >= len(text.rstrip()) and not raw.endswith(".")
        if len(words) > 1 and not cut:
            out.append(" ".join(words))
    return out


def expected_narrators(book: dict) -> list[str]:
    """Gesuchte Sprecher: der der Fassung, sonst alle laut Audible. Hörspiele und Ensemble-Produktionen (mehr als vier
    Sprecher, z. B. die neue deutsche Harry-Potter-Fassung mit 14) haben keinen einzelnen."""
    if book.get("want_narr"):
        return [book["want_narr"]]
    names = [p.strip() for p in (book.get("narrator") or "").split(",") if p.strip()]
    return [] if is_drama(book.get("title"), book.get("narrator")) or len(names) > 4 else names


def narrator_finding(book: dict, text: str) -> dict | None:
    """Sprecher im Treffer, in den Tags oder in der Ansage: passt er zum gesuchten (Fassung oder Audible)?
    Liefert {'status': ok|warn, 'text'} oder None, wenn nichts Verwertbares genannt ist."""
    wants = expected_narrators(book)
    if not wants:
        return None
    want = ", ".join(wants[:3])
    words = _words(text)
    named = named_narrators(text)
    best = lambda n: max(similarity(n, w) for w in wants)
    if any(name_in(w, words) for w in wants) or any(best(n) >= 0.7 for n in named):
        return {"status": "ok", "text": f"Sprecher passt ({want})"}
    other = next((o for o in book.get("other_narrs") or [] if name_in(o, words)), None) \
        or next((n for n in named if best(n) < 0.4 or not any(_shares_name(n, w) for w in wants)), None)
    if other:
        what = "gesucht ist die Fassung von" if book.get("want_narr") else "Audible nennt"
        return {"status": "warn", "text": f"Genannt ist {other}, {what} {want}. Wahrscheinlich eine andere Aufnahme"}
    return None


def narrator_check(book: dict, sources: list[tuple[str, str]]) -> dict | None:
    """Wie narrator_finding, über mehrere Quellen (stärkste zuerst: Ansage, Tags, Releasename); die erste Quelle mit
    einer Aussage gilt und steht im Text."""
    for label, text in sources:
        if (nf := narrator_finding(book, text)):
            return {**nf, "text": f"{nf['text']}, laut {label}"}
    return None
