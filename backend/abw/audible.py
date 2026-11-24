"""Öffentliche Audible-Katalog-API: Serienbände, Autorenkatalog, Toplisten."""
import json
from collections import Counter
import re
import time
from datetime import date, datetime, timedelta, timezone

import httpx

from . import abs as abs_mod
from . import config, db

GROUPS = "product_attrs,contributors,series,media,rating"
_http = httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) abw"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(lang: str, path: str, **params) -> dict:
    time.sleep(config.AUDIBLE_DELAY)
    for attempt in range(3):
        r = _http.get(config.MARKETPLACE[lang] + path, params=params)
        if r.status_code in (429, 500, 502, 503):
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()
    r.raise_for_status()
    return {}


def _cover(p: dict) -> str | None:
    imgs = p.get("product_images") or {}
    return imgs.get("500") or next(iter(imgs.values()), None)


def _names(lst) -> str:
    return ", ".join(x["name"] for x in (lst or []))


def brief(p: dict, lang: str) -> dict:
    ser = (p.get("series") or [{}])[0]
    rating = ((p.get("rating") or {}).get("overall_distribution") or {})
    return {
        "asin": p.get("asin"), "lang": lang, "title": p.get("title"), "subtitle": p.get("subtitle"),
        "author": _names(p.get("authors")), "narrator": _names(p.get("narrators")),
        "series": ser.get("title"), "sequence": ser.get("sequence"), "series_asin": ser.get("asin"),
        "release_date": p.get("release_date"), "runtime_min": p.get("runtime_length_min"),
        "cover_url": _cover(p), "language": p.get("language"),
        "rating": round(rating.get("average_rating") or 0, 1) or None, "ratings": rating.get("num_ratings"),
        "genres": genres_of(p),
    }


def genres_of(p: dict) -> list[str]:
    """Genres eines Produkts auf der zweiten Ebene («Fantasy», nicht «Science Fiction & Fantasy»).
    Audible.de liefert dieselben englischen Namen, darum lassen sich beide Sprachen vergleichen."""
    out = []
    for lad in p.get("category_ladders") or []:
        nodes = lad.get("ladder") or []
        if lad.get("root") == "Genres" and nodes:
            name = nodes[min(1, len(nodes) - 1)]["name"]
            if name not in out:
                out.append(name)
    return out


def products(lang: str, asins: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(asins), 50):
        d = _get(lang, "/1.0/catalog/products", asins=",".join(asins[i:i + 50]), response_groups=GROUPS)
        out += d.get("products", [])
    return out


# ---------- Serien ----------

ASIN_OK = re.compile(r"^[A-Z0-9]{10}$")  # Bestand trägt teils Platzhalter wie «0» oder «ignore»


def _title_keys(title: str | None) -> set[str]:
    """Vergleichsschlüssel eines Titels ohne Zusätze in Klammern: ganz und, bei «Reihe: Titel», der hintere Teil."""
    t = re.sub(r"[\[(][^\])]*[\])]", " ", title or "")
    return {k for k in (db.norm(t), db.norm(t.split(":")[-1])) if len(k) > 3}


def lookup_book(lang: str, title: str, author: str | None) -> dict | None:
    """Buch ohne brauchbare ASIN über Titel und Autor finden. Nur ein Treffer in der Sprache der Fassung, dessen Titel
    gleich ist und der denselben Autor nennt, zählt; sonst lieber keiner."""
    keys, who = _title_keys(title), db.norm(abs_mod.primary_author(author))
    if not keys:
        return None
    q = {"title": title, "author": abs_mod.primary_author(author)} if author else {"title": title}
    d = _get(lang, "/1.0/catalog/products", **q, num_results=10, response_groups="series,product_attrs,contributors,media")
    for p in d.get("products") or []:
        if p.get("language") != config.AUDIBLE_LANG[lang] or not keys & _title_keys(p.get("title")):
            continue
        if who and who != "unbekannt" and who not in {db.norm(a["name"]) for a in p.get("authors") or []}:
            continue
        return p
    return None


def find_series_asin(lang: str, name: str, books: list) -> tuple[str | None, str | None]:
    """Audible-Serie zu einer Fassung über ihre Bücher. Liefert (Serien-ASIN, None) oder, wenn keine Serie zu finden ist,
    (None, Art): «single» = Audible kennt die Bücher, führt sie aber in keiner Serie, «absent» = kein Buch bei Audible."""
    key = db.norm(name)
    fallback, known = None, False
    real = [b for b in books if b["asin"] and ASIN_OK.match(b["asin"])]
    for b in real[:6]:
        p = _get(lang, f"/1.0/catalog/products/{b['asin']}", response_groups="series").get("product", {})
        known = known or bool(p)
        for s in p.get("series") or []:
            if db.norm(s.get("title")) == key:
                return s["asin"], None
            fallback = fallback or s.get("asin")
    if fallback:
        return fallback, None
    # Platzhalter statt ASIN: über Titel und Autor suchen (höchstens drei Bücher, damit grosse Ordner nicht ausufern)
    for b in [b for b in books if b not in real][:3]:
        p = lookup_book(lang, b["title"], b["author"])
        known = known or bool(p)
        for s in (p or {}).get("series") or []:
            if db.norm(s.get("title")) == key:
                return s["asin"], None
            fallback = fallback or s.get("asin")
    if fallback:
        return fallback, None
    return None, "single" if known else "absent"


def series_children(lang: str, series_asin: str) -> tuple[str | None, list[dict]]:
    p = _get(lang, f"/1.0/catalog/products/{series_asin}", response_groups="relationships,product_attrs").get("product", {})
    rel = [r for r in p.get("relationships") or [] if r.get("relationship_to_product") == "child"]
    seqs = {r["asin"]: r.get("sequence") for r in rel}
    out = []
    for prod in products(lang, list(seqs)):
        if not prod.get("title"):
            continue
        b = brief(prod, lang)
        b["sequence"] = seqs.get(prod["asin"]) or b["sequence"]
        b["content_type"] = prod.get("content_type")
        b["format_type"] = prod.get("format_type")
        out.append(b)
    return p.get("title"), out


NO_SERIES = {
    "single": "Audible führt diese Bücher in keiner Serie (Einzelbücher)",
    "absent": "Keines dieser Bücher ist bei Audible zu finden",
}


def sync_edition(edition_id: int) -> str:
    c = db.conn()
    ed = c.execute("SELECT * FROM edition WHERE id=?", (edition_id,)).fetchone()
    lang = ed["lang"]
    try:
        asin = ed["audible_asin"]
        if not asin:
            books = c.execute("SELECT asin, title, author FROM book WHERE edition_id=? AND owned=1 ORDER BY seq IS NULL, seq",
                              (edition_id,)).fetchall()
            asin, kind = find_series_asin(lang, ed["name"], books) if books else (None, "absent")
            if not asin:
                c.execute("UPDATE edition SET kind=? WHERE id=?", (kind, edition_id))
                raise LookupError(NO_SERIES[kind])
        _, children = series_children(lang, asin)
        _merge(c, ed, asin, children)
        c.execute("UPDATE edition SET audible_asin=?, synced_at=?, sync_error=NULL, kind=NULL WHERE id=?", (asin, _now(), edition_id))
        return "ok"
    except Exception as exc:  # Fehler pro Serie festhalten, Lauf geht weiter
        c.execute("UPDATE edition SET synced_at=?, sync_error=? WHERE id=?", (_now(), str(exc)[:300], edition_id))
        return f"Fehler: {exc}"


def pick(cands: list[dict]) -> dict:
    """Eine Ausgabe je Band: ungekürzt vor gekürzt, erschienen vor angekündigt, dann die längste."""
    today = date.today().isoformat()
    return sorted(cands, key=lambda x: (x.get("format_type") == "abridged", (x["release_date"] or "9") > today, -(x["runtime_min"] or 0)))[0]


def _merge(c, ed, series_asin: str, children: list[dict]) -> None:
    lang = ed["lang"]
    want = config.AUDIBLE_LANG[lang]
    books = c.execute("SELECT * FROM book WHERE edition_id=?", (ed["id"],)).fetchall()
    # Nur vorhandene Bücher: fehlende Bände laufen unten durch die Auswahl, sonst fielen sie beim zweiten Abgleich weg
    # Ein Sammelband im Bestand, den ABW aufteilt, deckt keine Bände ab: seine Bände kommen als eigene Einträge
    split = {r["book_id"] for r in c.execute("SELECT book_id FROM libsplit_job WHERE state IN ('split','done','partial')")}
    by_asin = {b["asin"]: b for b in books if b["asin"] and b["owned"] and b["id"] not in split}
    owned_seqs = {b["seq"] for b in books if b["owned"] and b["seq"] is not None and b["id"] not in split}
    # Mehrteilige Bände («Light Bringer (1 of 3)»): jeder Teil zählt für sich; ein ganzer Band im Bestand deckt alle Teile ab
    from . import variants
    part = variants.part_of
    owned_parts = {(b["seq"], part(b["title"])) for b in books if b["owned"] and b["seq"] is not None and b["id"] not in split
                   and part(b["title"]) is not None}
    owned_whole = {b["seq"] for b in books if b["owned"] and b["seq"] is not None and b["id"] not in split and part(b["title"]) is None}
    groups: dict[tuple, list[dict]] = {}
    # Hörspielserie: bei Audible überwiegend Hörspiele («Performance»). Dann zählen ihre Hörspiel-Folgen als Bände,
    # sonst nicht (bei Hörbuch-Serien sind Hörspiele nur eine zweite Fassung desselben Bandes).
    numbered = [ch for ch in children if ch.get("language") == want and db.parse_seq(ch["sequence"]) is not None]
    drama = bool(numbered) and sum(ch.get("content_type") == "Performance" for ch in numbered) * 2 > len(numbered)
    if "variant" in ed.keys() and ed["variant"] in ("BBC-Hörspiel", "Hörspiel", "GraphicAudio"):
        drama = True  # Hörspiel-Fassung einer Hörbuch-Serie: ihre Folgen sind die Bände
    c.execute("UPDATE edition SET drama=? WHERE id=?", (int(drama), ed["id"]))
    kinds = (None, "Product", "Performance") if drama else (None, "Product")
    belongs = variants.merge_filter(c, ed)
    for ch in children:
        seq = db.parse_seq(ch["sequence"])
        if ch["asin"] in by_asin:
            b = by_asin[ch["asin"]]
            c.execute("UPDATE book SET release_date=?, cover_url=?, sequence=COALESCE(sequence, ?), seq=COALESCE(seq, ?),"
                      " narrator=COALESCE(narrator, ?), audible_min=? WHERE id=?",
                      (ch["release_date"], ch["cover_url"], ch["sequence"], seq, ch["narrator"], ch["runtime_min"], b["id"]))
            if b["owned"] and seq is not None:
                owned_seqs.add(seq)
                if part(b["title"]) is None:
                    owned_whole.add(seq)
            m = re.match(r"^\s*(\d+)\s*-\s*(\d+)\s*$", ch["sequence"] or "")
            if b["owned"] and m and int(m.group(2)) - int(m.group(1)) < 50:  # vorhandener Sammelband deckt seine Bände ab
                owned_seqs.update(float(n) for n in range(int(m.group(1)), int(m.group(2)) + 1))
                owned_whole.update(float(n) for n in range(int(m.group(1)), int(m.group(2)) + 1))
            continue
        if seq is None or ch.get("language") != want or ch.get("content_type") not in kinds:
            continue  # Sammelbände ("1-3"), fremde Sprache, Hörspiele (ausser in Hörspielserien)
        if not belongs(ch):
            continue  # gehört zu einer anderen Fassung derselben Sprache (anderer Sprecher, Hörspiel)
        groups.setdefault((seq, part(ch["title"])), []).append(ch)
    # Gibt es den Band auch ganz, zählen seine Teile nicht extra
    groups = {k: v for k, v in groups.items() if k[1] is None or (k[0], None) not in groups}
    today = date.today().isoformat()
    keep = set()
    for (seq, p), cands in groups.items():
        if (p is None and seq in owned_seqs) or (p is not None and (seq in owned_whole or (seq, p) in owned_parts)):
            continue  # andere Ausgabe desselben Bandes (oder Teils) ist vorhanden
        ch = pick(cands)
        keep.add(ch["asin"])
        vals = (ch["title"], ch["subtitle"], ch["author"], ch["narrator"], ch["sequence"], seq,
                ch["release_date"], ch["runtime_min"], ch["cover_url"])
        old = variants.pick_row(c.execute("SELECT id, asin, title FROM book WHERE edition_id=? AND owned=0 AND (asin=? OR seq=?)",
                                          (ed["id"], ch["asin"], seq)), ch["asin"], ch["title"])
        if old:
            c.execute("UPDATE book SET asin=?, title=?, subtitle=?, author=?, narrator=?, sequence=?, seq=?, release_date=?,"
                      " runtime_min=?, cover_url=?, audible_min=? WHERE id=?", (ch["asin"], *vals, ch["runtime_min"], old["id"]))
        else:
            c.execute("INSERT INTO book(edition_id, lang, asin, title, subtitle, author, narrator, sequence, seq,"
                      " release_date, runtime_min, cover_url, owned, audible_min) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
                      (ed["id"], lang, ch["asin"], *vals, ch["runtime_min"]))
            new_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            if ed["synced_at"]:  # nicht beim ersten Abgleich einer Serie: da ist jeder Band «neu»
                from . import notifications, notify
                upcoming = (ch["release_date"] or "") > today
                when = "" if not upcoming else "Termin offen" if ch["release_date"] >= "2100" else "erscheint am " + ".".join(reversed(ch["release_date"].split("-")))
                notifications.book_event("announced" if upcoming else "new_book", "Neuer Band angekündigt" if upcoming else "Neues Hörbuch erschienen",
                                         {"id": new_id, "edition_id": ed["id"], "title": ch["title"], "sequence": ch["sequence"], "author": ch["author"],
                                          "cover_url": ch["cover_url"]}, extra=when)
                if upcoming and owned_seqs:  # neu angekündigt in einer Serie, die du hast
                    notify.send("Neuer Band angekündigt", f"{ed['name']}, Band {ch['sequence']}: {ch['title']} ({when})", f"/serie/{ed['series_id']}", "calendar")
    busy = {r["book_id"] for r in c.execute("SELECT book_id FROM download WHERE state IN ('queued','downloading','arrived','verified','review','imported')")}
    for b in c.execute("SELECT id, asin, wanted FROM book WHERE edition_id=? AND owned=0", (ed["id"],)).fetchall():
        # Band mit laufendem Download bleibt, bis der Download fertig ist; von Hand gewünschte Bände bleiben immer
        if b["asin"] not in keep and b["id"] not in busy and not b["wanted"]:
            c.execute("DELETE FROM book WHERE id=?", (b["id"],))


def sync_series(progress=lambda msg: None, max_age_days: float = 3) -> str:
    c = db.conn()
    limit = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    ids = [r["id"] for r in c.execute(
        "SELECT id FROM edition WHERE synced_at IS NULL OR synced_at < ? OR (audible_asin IS NULL AND kind IS NULL)"  # ungeprüfte sofort
        " ORDER BY synced_at IS NOT NULL, synced_at", (limit,))]
    errors = 0
    for i, eid in enumerate(ids, 1):
        if sync_edition(eid) != "ok":
            errors += 1
        if i % 5 == 0 or i == len(ids):
            progress(f"{i} von {len(ids)} Serien abgeglichen")
    return f"{len(ids)} Serien abgeglichen, {errors} ohne Audible-Treffer"


# ---------- Autoren ----------

def author_series(lang: str, author: str) -> list[dict]:
    """Alle Serien eines Autors in einer Sprache (Katalogabfrage nach Autor, Shop der Sprache)."""
    want, key = config.AUDIBLE_LANG[lang], db.norm(author)
    found: dict[str, dict] = {}
    for page in range(6):
        d = _get(lang, "/1.0/catalog/products", author=author, num_results=50, page=page,
                 response_groups="contributors,product_attrs,series,media")
        prods = d.get("products", [])
        for p in prods:
            if p.get("language") != want or p.get("content_type") not in (None, "Product"):
                continue
            if key not in {db.norm(a["name"]) for a in p.get("authors") or []}:
                continue
            for s in (p.get("series") or [])[:1]:
                e = found.setdefault(s["asin"], {"series_asin": s["asin"], "series_title": s["title"], "seqs": set(),
                                                 "cover_url": None, "first_asin": None, "low": 1e9})
                seq = db.parse_seq(s.get("sequence"))
                if seq is None:
                    continue
                e["seqs"].add(seq)
                if seq < e["low"]:
                    e.update(low=seq, cover_url=_cover(p), first_asin=p["asin"])
        if len(prods) < 50:
            break
    return [e for e in found.values() if e["seqs"]]


def sync_authors(progress=lambda msg: None, max_age_days: float = 7) -> str:
    c = db.conn()
    limit = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    authors = {}
    for r in c.execute("SELECT DISTINCT author FROM book WHERE author IS NOT NULL AND ((edition_id IS NOT NULL AND owned=1) OR wanted IS NOT NULL"
                       " OR edition_id IN (SELECT id FROM edition WHERE link_source='added'))"):
        a = abs_mod.primary_author(r["author"])
        authors.setdefault(db.norm(a), a)
    done = {(r["author_norm"], r["lang"]) for r in c.execute("SELECT author_norm, lang FROM author_fetch WHERE fetched_at >= ?", (limit,))}
    todo = [(k, a, lang) for k, a in sorted(authors.items()) for lang in config.LANGS if (k, lang) not in done and k]
    for i, (key, author, lang) in enumerate(todo, 1):
        try:
            rows = author_series(lang, author)
        except Exception:
            continue
        c.execute("BEGIN IMMEDIATE")
        before = {r["series_asin"] for r in c.execute("SELECT series_asin FROM author_catalog WHERE author_norm=? AND lang=?", (key, lang))}
        known = c.execute("SELECT 1 FROM author_fetch WHERE author_norm=? AND lang=?", (key, lang)).fetchone()
        c.execute("DELETE FROM author_catalog WHERE author_norm=? AND lang=?", (key, lang))
        for e in rows:
            c.execute("INSERT OR REPLACE INTO author_catalog VALUES(?,?,?,?,?,?,?,?)",
                      (key, lang, e["series_asin"], e["series_title"], len(e["seqs"]), e["cover_url"], e["first_asin"], _now()))
            if known and e["series_asin"] not in before:  # nicht bei der ersten Abfrage eines Autors
                from . import notifications
                notifications.emit("new_series", "Neue Serie von " + author,
                                   f"{e['series_title']} ({'Deutsch' if lang == 'de' else 'Englisch'}, {len(e['seqs'])} {'Band' if len(e['seqs']) == 1 else 'Bände'})",
                                   f"/autor/{key}", f"new_series:{lang}:{e['series_asin']}", e["cover_url"], author_key=key)
        c.execute("INSERT OR REPLACE INTO author_fetch VALUES(?,?,?)", (key, lang, _now()))
        c.execute("COMMIT")
        if i % 5 == 0 or i == len(todo):
            progress(f"{i} von {len(todo)} Autorenabfragen")
    linked = auto_link()
    return f"{len(todo)} Autorenabfragen, {linked} Sprachfassungen automatisch verknüpft"


def _loose(title: str | None) -> str:
    """Vergleichsschlüssel ohne Zusätze wie «[German Edition]» oder «-Serie»."""
    import re
    t = re.sub(r"[\[(][^\])]*(edition|ausgabe)[^\])]*[\])]", " ", (title or "").lower())
    return db.norm(re.sub(r"-(serie|reihe)\b", " ", t))


def _runtime(lang: str, asin: str | None) -> int | None:
    if not asin:
        return None
    return (_get(lang, f"/1.0/catalog/products/{asin}", response_groups="media").get("product") or {}).get("runtime_length_min")


def remainder(series_id: int, lang: str) -> dict | None:
    """Sprachfassung mit anderem Titel: Hat der Autor in beiden Sprachen genau eine Serie, die noch keine zweite Fassung hat,
    gehören die beiden sehr wahrscheinlich zusammen. Liefert den Katalogeintrag der anderen Sprache oder None."""
    c = db.conn()
    eds = c.execute("SELECT * FROM edition WHERE series_id=? AND variant IS NULL", (series_id,)).fetchall()
    if len(eds) != 1 or eds[0]["lang"] == lang or not eds[0]["audible_asin"]:
        return None
    own = eds[0]
    authors = {db.norm(abs_mod.primary_author(r["author"])) for r in c.execute(
        "SELECT DISTINCT author FROM book WHERE edition_id=? AND author IS NOT NULL", (own["id"],))}
    if len(authors) != 1:
        return None
    # frei = Katalogserie, die an keiner Serie mit zwei Fassungen hängt
    free = ("SELECT a.* FROM author_catalog a WHERE a.author_norm=? AND a.lang=? AND NOT EXISTS (SELECT 1 FROM edition e WHERE e.audible_asin=a.series_asin"
            " AND e.lang=a.lang AND (SELECT COUNT(*) FROM edition x WHERE x.series_id=e.series_id AND x.variant IS NULL)>1)")
    author = next(iter(authors))
    mine = c.execute(free, (author, own["lang"])).fetchall()
    theirs = c.execute(free, (author, lang)).fetchall()
    if len(mine) != 1 or len(theirs) != 1 or mine[0]["series_asin"] != own["audible_asin"]:
        return None
    # dieselbe Audible-Serie steht mehrfach im Bestand (z. B. «Serie #1&2»): nur die mit dem Katalognamen zählt
    same = c.execute("SELECT COUNT(*) FROM edition WHERE audible_asin=? AND lang=?", (own["audible_asin"], own["lang"])).fetchone()[0]
    if same > 1 and db.norm(mine[0]["series_title"]) != own["norm"]:
        return None
    return {**dict(theirs[0]), "own_count": mine[0]["book_count"], "own_first": mine[0]["first_asin"], "own_lang": own["lang"]}


def remainder_confirmed(r: dict) -> bool:
    """Zusatzprüfung vor dem automatischen Verknüpfen: Die Übersetzung hat nicht mehr Bände als das Original (Englisch),
    und der erste Band ist ähnlich lang (deutsche Fassungen sind meist etwas länger)."""
    en_count, de_count = (r["own_count"], r["book_count"]) if r["own_lang"] == "en" else (r["book_count"], r["own_count"])
    if de_count > en_count:
        return False
    c = db.conn()
    key = f"linkcheck:{r['own_first']}:{r['first_asin']}"
    row = c.execute("SELECT value FROM cache WHERE key=?", (key,)).fetchone()
    if row:
        return row["value"] == "1"
    try:
        a, b = _runtime(r["own_lang"], r["own_first"]), _runtime(r["lang"], r["first_asin"])
    except Exception:
        return False  # ohne Antwort von Audible nicht raten und nichts merken
    en, de = (a, b) if r["own_lang"] == "en" else (b, a)
    ok = bool(en and de and 0.85 <= de / en <= 1.5)
    c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, "1" if ok else "0", _now()))
    return ok


def auto_link() -> int:
    """Serie desselben Autors in der anderen Sprache als zweite Fassung anhängen: bei gleichem Namen (auch mit Zusatz wie
    «[German Edition]») oder, bei anderem Titel, wenn sie die einzige offene Serie des Autors ist und die Zusatzprüfung besteht."""
    c = db.conn()
    n = 0
    rows = c.execute(
        "SELECT s.id, s.name, s.norm, GROUP_CONCAT(DISTINCT e.lang) langs FROM series s JOIN edition e ON e.series_id=s.id AND e.variant IS NULL"
        " GROUP BY s.id HAVING COUNT(*)=1").fetchall()
    for s in rows:
        other = "de" if s["langs"] == "en" else "en"
        try:
            cands = candidates(s["id"], other)
            hit = next((x for x in cands if db.norm(x["series_title"]) == s["norm"]), None) \
                or next((x for x in cands if _loose(x["series_title"]) and _loose(x["series_title"]) == _loose(s["name"])), None)
            source = "auto"
            if not hit:
                r = remainder(s["id"], other)
                if r and remainder_confirmed(r):
                    hit, source = r, "auto-titel"
            if hit:
                link(s["id"], other, hit["series_asin"], hit["series_title"], source)
                n += 1
        except ValueError:
            continue  # inzwischen anders verknüpft
    return n


# ---------- Sprachpaare zum Bestätigen ----------

PAIR_MIN = 40  # ab dieser Punktzahl steht ein Paar in der Vorschlagsliste


def _pair_data(c) -> dict[int, dict]:
    """Fassungen, die allein auf ihrer Karte stehen und eine Audible-Serie haben: Autor, Laufzeit je Band und gesamt."""
    out = {}
    rows = c.execute("SELECT e.* FROM edition e WHERE e.audible_asin IS NOT NULL"
                     " AND e.variant IS NULL AND (SELECT COUNT(*) FROM edition x WHERE x.series_id=e.series_id AND x.variant IS NULL)=1").fetchall()
    for e in rows:
        bl = c.execute("SELECT seq, sequence, runtime_min, owned, author, cover_url, abs_item_id FROM book WHERE edition_id=?", (e["id"],)).fetchall()
        authors = {db.norm(abs_mod.primary_author(b["author"])) for b in bl if b["author"]}
        rt = {}
        for b in bl:
            if b["seq"] is not None and b["runtime_min"] and "-" not in (b["sequence"] or ""):
                rt.setdefault(b["seq"], b["runtime_min"])
        cover = next((b for b in sorted(bl, key=lambda b: (not b["owned"], b["seq"] is None, b["seq"] or 0))), None)
        out[e["id"]] = {"e": e, "authors": authors - {"unbekannt"}, "rt": rt, "total": sum(rt.values()),
                        "owned": sum(b["owned"] for b in bl), "count": len(rt),
                        "cover": cover and (f"/api/cover/{cover['abs_item_id']}" if cover["abs_item_id"] else cover["cover_url"])}
    return out


def pair_score(de: dict, en: dict) -> tuple[int, list[str]]:
    """Wie gut passen eine deutsche und eine englische Fassung zusammen? Punkte 0 bis 100 und die Gründe dafür.
    Übersetzungen sind meist etwas länger als das Original (Verhältnis 0,85 bis 1,5); geteilte Bände passen nur gesamt."""
    score, why = 0, []
    hint = re.search(r"\[([^\]]+)\]", de["e"]["name"])
    if hint and "edition" not in hint.group(1).lower() and _loose(hint.group(1)) == _loose(en["e"]["name"]):
        score += 60
        why.append(f"Originaltitel «{hint.group(1)}» steht im deutschen Namen")
    common = sorted(set(de["rt"]) & set(en["rt"]))
    ok = [s for s in common if 0.85 <= de["rt"][s] / en["rt"][s] <= 1.5]
    total_ok = bool(de["total"] and en["total"] and 0.85 <= de["total"] / en["total"] <= 1.5)
    hours = f"({round(de['total'] / 60)} h zu {round(en['total'] / 60)} h)"
    split = en["count"] >= 2 and de["count"] >= 1.3 * en["count"]
    if split and total_ok and len(ok) < 0.5 * len(common):
        score += 55  # deutsche Ausgabe in mehr Teile geteilt: Bände passen einzeln nicht, zusammen schon
        why.append(f"deutsch in {de['count']} statt {en['count']} Teile geteilt, Gesamtlaufzeit passt {hours}")
    elif len(common) >= 2:
        # Anteil gleich langer Bände, gewichtet mit der Abdeckung der deutschen Fassung (Übersetzungen hinken oft hinterher)
        score += round(70 * len(ok) / len(common) * min(1, len(common) / de["count"]))
        if de["count"] == en["count"]:
            score += 10
        why.append(f"{len(ok)} von {len(common)} Bänden gleich lang (Übersetzung 0,85- bis 1,5-mal)")
        if total_ok and de["count"] == en["count"]:
            why.append(f"Gesamtlaufzeit passt {hours}")
    else:
        if ok:
            score += 15
            why.append("Band 1 gleich lang")
        if total_ok and min(de["count"], en["count"]) >= 2:
            score += 30
            why.append(f"Gesamtlaufzeit passt {hours}")
    return max(0, min(100, score)), why


def pair_suggestions(limit_per: int = 3) -> list[dict]:
    """Deutsche und englische Fassung desselben Autors auf getrennten Karten, die wahrscheinlich zusammengehören.
    Abgelehnte Paare kommen nicht wieder."""
    c = db.conn()
    data = _pair_data(c)
    rejected = {r["key"] for r in c.execute("SELECT key FROM cache WHERE key LIKE 'pairno:%'")}
    by_de: dict[int, list] = {}
    for d in (x for x in data.values() if x["e"]["lang"] == "de" and x["authors"]):
        for e in (x for x in data.values() if x["e"]["lang"] == "en" and x["authors"] & d["authors"]):
            if f"pairno:{d['e']['id']}:{e['e']['id']}" in rejected:
                continue
            score, why = pair_score(d, e)
            if score >= PAIR_MIN:
                by_de.setdefault(d["e"]["id"], []).append((score, why, e))
    side = lambda x: {"edition_id": x["e"]["id"], "series_id": x["e"]["series_id"], "name": x["e"]["name"], "lang": x["e"]["lang"],
                      "owned": x["owned"], "count": x["count"], "hours": round(x["total"] / 60), "cover": x["cover"]}
    out = []
    for did, hits in by_de.items():
        hits.sort(key=lambda h: -h[0])
        for score, why, e in hits[:limit_per]:
            out.append({"score": score, "why": why, "de": side(data[did]), "en": side(e), "rank": hits.index((score, why, e))})
    return sorted(out, key=lambda p: (-max(h[0] for h in by_de[p["de"]["edition_id"]]), p["de"]["name"].lower(), -p["score"]))


def reject_pair(de_edition: int, en_edition: int) -> None:
    db.conn().execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (f"pairno:{de_edition}:{en_edition}", "1", _now()))


def link_editions(a: int, b: int) -> int:
    """Zwei Fassungen verschiedener Sprache auf eine Karte legen. Es bleibt die Karte mit mehr vorhandenen Büchern;
    Markierungen (Glocke), Merkliste und Automatik-Einstellung der anderen Karte ziehen mit. Liefert die Serie."""
    c = db.conn()
    ea, eb = (c.execute("SELECT * FROM edition WHERE id=?", (x,)).fetchone() for x in (a, b))
    if not ea or not eb or ea["lang"] == eb["lang"]:
        raise ValueError("Zwei Fassungen in verschiedenen Sprachen nötig")
    for e in (ea, eb):
        if c.execute("SELECT COUNT(*) FROM edition WHERE series_id=? AND variant IS NULL", (e["series_id"],)).fetchone()[0] != 1:
            raise ValueError(f"«{e['name']}» hat inzwischen schon eine zweite Fassung")
    owned = lambda e: c.execute("SELECT COUNT(*) FROM book WHERE edition_id=? AND owned=1", (e["id"],)).fetchone()[0]
    keep, move = (ea, eb) if owned(ea) >= owned(eb) else (eb, ea)
    old, new = move["series_id"], keep["series_id"]
    c.execute("BEGIN IMMEDIATE")
    try:
        c.execute("UPDATE edition SET series_id=?, link_source='manual' WHERE id=?", (new, move["id"]))
        c.execute("UPDATE edition SET series_id=? WHERE series_id=?", (new, old))  # weitere Fassungen derselben Karte
        from . import variants
        variants.move_series_refs(c, old, new)
        c.execute("DELETE FROM series WHERE id=?", (old,))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return new


def candidates(series_id: int, lang: str) -> list[dict]:
    c = db.conn()
    authors = {db.norm(abs_mod.primary_author(r["author"])) for r in c.execute(
        "SELECT DISTINCT b.author FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=? AND b.author IS NOT NULL", (series_id,))}
    if not authors:
        return []
    q = ",".join("?" * len(authors))
    return [dict(r) for r in c.execute(
        f"SELECT DISTINCT series_asin, series_title, book_count, cover_url FROM author_catalog WHERE lang=? AND author_norm IN ({q})"
        " AND series_asin NOT IN (SELECT audible_asin FROM edition WHERE audible_asin IS NOT NULL AND series_id=?)"
        " ORDER BY book_count DESC", (lang, *authors, series_id))]


def suggestions(series_id: int, lang: str) -> list[dict]:
    """Kandidaten für die Oberfläche; die wahrscheinlich passende Serie steht vorn und ist gekennzeichnet."""
    r = remainder(series_id, lang)
    out = [{**x, "likely": bool(r and x["series_asin"] == r["series_asin"])} for x in candidates(series_id, lang)]
    return sorted(out, key=lambda x: not x["likely"])


def link(series_id: int, lang: str, series_asin: str, title: str, source: str) -> None:
    """Sprachfassung an eine Serie hängen. Besteht sie schon als eigene Serie, werden beide zusammengeführt."""
    c = db.conn()
    key = db.norm(title) or title.lower()
    ex = c.execute("SELECT * FROM edition WHERE lang=? AND (audible_asin=? OR norm=?) AND variant IS NULL", (lang, series_asin, key)).fetchone()
    if c.execute("SELECT 1 FROM edition WHERE series_id=? AND lang=? AND variant IS NULL", (series_id, lang)).fetchone():
        raise ValueError("Diese Serie hat schon eine Fassung in dieser Sprache")
    if ex:
        old = ex["series_id"]
        c.execute("UPDATE edition SET series_id=?, audible_asin=COALESCE(audible_asin, ?) WHERE id=?", (series_id, series_asin, ex["id"]))
        c.execute("DELETE FROM series WHERE id=? AND id NOT IN (SELECT series_id FROM edition)", (old,))
        eid = ex["id"]
    else:
        eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, link_source) VALUES(?,?,?,?,?,?)",
                        (series_id, lang, title, key, series_asin, source)).lastrowid
    sync_edition(eid)


def _authors(c, where: str, arg: int) -> set[str]:
    return {db.norm(abs_mod.primary_author(r["author"])) for r in c.execute(f"SELECT author FROM book WHERE {where}", (arg,))
            if r["author"]} - {"unbekannt", ""}


def _join_same_card(c, eid: int, sid: int, target: int) -> int:
    """Neu aufgenommene Serie heisst wie eine Karte, die in dieser Sprache schon eine Hauptfassung hat. Gleicher Autor:
    andere Lesung derselben Serie (Harry Potter, Jim Dale, 19.11.2026), sie kommt als Sprecher-Fassung auf diese Karte.
    Anderer Autor oder kein Sprecher erkennbar: eigene Karte bleibt. Liefert die Karte der Fassung."""
    from . import variants
    if not (_authors(c, "edition_id=?", eid) & _authors(c, "edition_id IN (SELECT id FROM edition WHERE series_id=?)", target)):
        return sid
    narrs = Counter(n for r in c.execute("SELECT narrator FROM book WHERE edition_id=?", (eid,))
                    if (n := (r["narrator"] or "").split(",")[0].strip()))
    if not narrs:
        return sid
    narr = narrs.most_common(1)[0][0]
    if c.execute("SELECT 1 FROM edition WHERE series_id=? AND lang=? AND variant=?", (target, c.execute(
            "SELECT lang FROM edition WHERE id=?", (eid,)).fetchone()[0], narr)).fetchone():
        return sid  # Fassung dieses Sprechers gibt es dort schon: nicht zweimal
    c.execute("UPDATE edition SET series_id=?, variant=?, narr=? WHERE id=?", (target, narr, narr, eid))
    variants.move_series_refs(c, sid, target)
    c.execute("DELETE FROM series WHERE id=? AND id NOT IN (SELECT series_id FROM edition)", (sid,))
    return target


def add_series(lang: str, series_asin: str, title: str, source: str = "added") -> int:
    """Serie aus dem Audible-Katalog in die Bibliothek aufnehmen: überwacht, alle Bände als fehlend, Suche mit Vorrang.
    Gibt es die Fassung schon, wird sie nur überwacht. Liefert die Serie."""
    c = db.conn()
    key = db.norm(title) or title.lower()
    ex = c.execute("SELECT * FROM edition WHERE lang=? AND audible_asin=? ORDER BY variant IS NOT NULL", (lang, series_asin)).fetchone() \
        or c.execute("SELECT * FROM edition WHERE lang=? AND norm=?", (lang, key)).fetchone()
    if ex:
        eid, sid = ex["id"], ex["series_id"]
        c.execute("UPDATE edition SET monitored=1, audible_asin=COALESCE(audible_asin, ?) WHERE id=?", (series_asin, eid))
    else:
        s = c.execute("SELECT id FROM series WHERE norm=?", (key,)).fetchone()
        same = None
        if s and c.execute("SELECT 1 FROM edition WHERE series_id=? AND lang=? AND variant IS NULL", (s["id"], lang)).fetchone():
            same, s, skey = s["id"], None, key + ":" + lang  # gleichnamige Karte hat schon eine Hauptfassung: erst eigene Karte
        else:
            skey = key
        sid = s["id"] if s else c.execute("INSERT INTO series(name, norm, added_at) VALUES(?,?,?)", (title, skey, _now())).lastrowid
        eid = c.execute("INSERT INTO edition(series_id, lang, name, norm, audible_asin, monitored, link_source) VALUES(?,?,?,?,?,1,?)",
                        (sid, lang, title, key, series_asin, source)).lastrowid
    if not ex or not ex["synced_at"]:
        sync_edition(eid)
    if not ex and same:
        sid = _join_same_card(c, eid, sid, same)
    c.execute("UPDATE series SET prio=?, auto=NULL WHERE id=?", (_now(), sid))
    return sid


# ---------- Entdecken ----------

LISTS = {"bestseller": "BestSellers", "neu": "-ReleaseDate"}

def discover(kind: str, lang: str, max_age_h: float = 12) -> list[dict]:
    c = db.conn()
    key = f"discover2:{kind}:{lang}"  # «2»: seit 13.11. mit Genres
    row = c.execute("SELECT value, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
    if row and row["fetched_at"] > (datetime.now(timezone.utc) - timedelta(hours=max_age_h)).isoformat():
        return json.loads(row["value"])
    out, today = [], date.today().isoformat()
    for page in range(3):
        d = _get(lang, "/1.0/catalog/products", num_results=50, page=page, products_sort_by=LISTS[kind],
                 response_groups=GROUPS + ",category_ladders")
        for p in d.get("products", []):
            b = brief(p, lang)
            if b["language"] != config.AUDIBLE_LANG[lang] or not b["title"]:
                continue
            if (b["release_date"] or "9") > today:
                continue  # Vorbestellungen gehören in den Kalender, nicht in die Toplisten
            out.append(b)
        if len(out) >= 60:
            break
    c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, json.dumps(out), _now()))
    return out


def search(q: str, lang: str, max_age_h: float = 1) -> list[dict]:
    """Freie Suche im Audible-Katalog (Titel, Autor, Serie, Sprecher), beste Treffer zuerst.
    Nur Ausgaben in der Sprache des Marktplatzes, angekündigte Bände eingeschlossen."""
    q = " ".join(q.split())[:120]
    if len(q) < 2:
        return []
    c = db.conn()
    key = f"search:{lang}:{q.casefold()}"
    row = c.execute("SELECT value, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
    if row and row["fetched_at"] > (datetime.now(timezone.utc) - timedelta(hours=max_age_h)).isoformat():
        return json.loads(row["value"])
    d = _get(lang, "/1.0/catalog/products", keywords=q, num_results=50, products_sort_by="Relevance", response_groups=GROUPS)
    out = []
    for p in d.get("products", []):
        b = brief(p, lang)
        if b["language"] == config.AUDIBLE_LANG[lang] and b["title"] and b["asin"]:
            out.append(b)
    c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, json.dumps(out), _now()))
    return out


# ---------- Für dich ----------

def _cached(key: str, max_age_h: float, fetch):
    c = db.conn()
    row = c.execute("SELECT value, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
    if row and row["fetched_at"] > (datetime.now(timezone.utc) - timedelta(hours=max_age_h)).isoformat():
        return json.loads(row["value"])
    value = fetch()
    c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, json.dumps(value), _now()))
    return value


def similar(lang: str, asin: str) -> list[dict]:
    """«Hörer mochten auch» zu einem Buch, nur Ausgaben in der Sprache des Marktplatzes (zwei Wochen zwischengespeichert)."""
    def fetch():
        d = _get(lang, f"/1.0/catalog/products/{asin}/sims", num_results=30, response_groups=GROUPS + ",category_ladders")
        return [b for p in d.get("similar_products", []) if (b := brief(p, lang))["language"] == config.AUDIBLE_LANG[lang]
                and b["title"] and b["asin"]]
    return _cached(f"sims:{lang}:{asin}", 24 * 14, fetch)


def book_genres(lang: str, asins: list[str]) -> dict[str, list[str]]:
    """Genres zu Büchern aus dem Bestand, je Buch einen Monat zwischengespeichert."""
    c = db.conn()
    limit = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    out, todo = {}, []
    for a in asins:
        row = c.execute("SELECT value FROM cache WHERE key=? AND fetched_at > ?", (f"genres:{lang}:{a}", limit)).fetchone()
        if row:
            out[a] = json.loads(row["value"])
        else:
            todo.append(a)
    for i in range(0, len(todo), 50):
        d = _get(lang, "/1.0/catalog/products", asins=",".join(todo[i:i + 50]), response_groups="category_ladders")
        got = {p["asin"]: genres_of(p) for p in d.get("products", []) if p.get("asin")}
        for a in todo[i:i + 50]:
            out[a] = got.get(a, [])
            c.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (f"genres:{lang}:{a}", json.dumps(out[a]), _now()))
    return out
