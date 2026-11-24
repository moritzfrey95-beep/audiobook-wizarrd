"""Entdecken «Für dich»: Vorschläge aus dem, was ein Benutzer gehört hat.

Zu den gehörten Serien holt ABW bei Audible die ähnlichen Titel. Jede Serie bringt ihr Gewicht mit: mehr gehörte Bände
zählen mehr, vollständig gehörte Serien am meisten, eine nur angefangene gar nicht. Aus den
Genres derselben Serien entsteht ein Profil, nach dem die Toplisten sortiert werden.
"""
import math
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import abs as abs_mod
from . import audible, config, db, tags

FIT_MIN = 0.15  # ab diesem Anteil gilt ein Toplisten-Titel als passend


def _head(title: str) -> str:
    """Titel ohne Untertitel und Zusätze: «The Hobbit (Dramatised)» und «The Hobbit: …» gelten als gleich."""
    return db.norm(re.split(r"[:(\[]", title)[0])


def finished(user_id: int | None) -> tuple[set[str], set[tuple[str, str]]]:
    """Fertig gehörte Bücher des Benutzers: ASINs und (Titel, Autor), damit auch andere Ausgaben als gehört gelten."""
    if not user_id:
        return set(), set()
    rows = db.conn().execute(
        "SELECT b.asin, b.title, b.author FROM user_progress p JOIN book b ON b.id=p.book_id WHERE p.user_id=? AND p.finished=1",
        (user_id,)).fetchall()
    return ({r["asin"] for r in rows if r["asin"]},
            {(_head(r["title"]), db.norm(abs_mod.primary_author(r["author"]))) for r in rows if r["title"] and r["author"]})


def profile(user_id: int | None) -> dict[str, float]:
    """Anteil je Genre, gewichtet wie die gehörten Serien (siehe `sources`), z. B. {"Fantasy": 0.8, "Science Fiction": 0.3}.

    Je Serie zählt ihr zuletzt gehörter Band; eine nur angefangene Serie zählt nicht."""
    srcs = sources(user_id)
    if not srcs:
        return {}
    count, total = Counter(), sum(s["weight"] for s in srcs)
    for lang in ("en", "de"):
        firsts = {s["books"][0]["asin"]: s["weight"] for s in srcs if s["books"][0]["lang"] == lang}
        for asin, genres in audible.book_genres(lang, list(firsts)).items():
            for g in set(genres):
                count[g] += firsts.get(asin, 0)
    return {g: n / total for g, n in count.items()}


def fit(item: dict, prof: dict[str, float]) -> tuple[float, list[str]]:
    """Wie gut ein Titel zu den Genres passt: Summe der Anteile seiner Genres (höchstens 1) und die passenden Genres."""
    hit = sorted((g for g in item.get("genres") or [] if prof.get(g, 0) >= FIT_MIN), key=lambda g: -prof[g])
    return min(1.0, sum(prof.get(g, 0) for g in item.get("genres") or [])), hit


SOURCE_BOOKS = 40  # höchstens so viele Bücher fragt ABW bei Audible nach Ähnlichem
PER_SERIES = 3  # je Serie die zuletzt gehörten Bände
HALF_LIFE = 180  # Tage: so lange zählt eine Serie, die man nicht mehr hört, noch halb so stark im Zeitanteil


def _age_days(stamp: str | None) -> float:
    try:
        then = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return 365.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - then).total_seconds() / 86400)


DROP_DAYS = 60  # so lange ohne Hören gilt ein angefangener Band als abgebrochen
DROP_BELOW = 0.5  # ... wenn er unter der Hälfte steht
DROP_FACTOR = 0.6  # Serie mit gehörten Bänden, aber abgebrochenem letzten Band: zählt nur noch so viel
DROP_PENALTY = -0.5  # nur angefangen und abgebrochen: Ähnliches rutscht leicht nach unten
DROP_SOURCES = 10
PEER_FACTOR = 1.5  # Gewicht von «Hörer mit ähnlichem Geschmack» neben Audible
PEER_MIN_SHARED = 2  # so viele gemeinsame Serien braucht ein anderer Benutzer mindestens
AUTHOR_BONUS, AUTHOR_MAX = 0.5, 1.0  # je gehörter Serie des Autors, höchstens
NARRATOR_BONUS, NARRATOR_MAX = 0.3, 0.6
DISLIKE = {1: -1.0, 2: -0.5}  # Sterne (gerundet) -> Gegensignal der Serie
TAG_WEIGHT, TAG_MAX = 2.0, 1.5  # Zuschlag aus dem Tag-Profil: Summe der Anteile × 2, höchstens 1,5
TAG_CANDIDATES = 150  # nur so viele Vorschläge bekommen Tags (Audible-Abfragen, ein Monat zwischengespeichert)
SERIES_CANDIDATES = 40  # so viele neue Vorschläge bekommen die Bandzahl ihrer Serie
RATING_PRIOR, RATING_N = 4.3, 100  # Audible-Bewertung: geglättet zum Mittel 4,3 mit 100 gedachten Stimmen
RATING_FACTOR, RATING_MIN, RATING_MAX = 1.5, -0.4, 0.5
DIVERSE_TOP, DIVERSE_PER = 20, 3  # unter den ersten 20 höchstens 3 aus derselben Quellserie
EXPLORE_AT = (7, 14)  # Plätze für «Mal was anderes»


def stars_factor(avg: float) -> float:
    """Sterne -> Faktor fürs Seriengewicht: 5 = 1,5, 4 = 1,2, 3 = 0,9."""
    return 0.9 + (avg - 3) * 0.3


def _names(text: str | None) -> set[str]:
    return {db.norm(n) for n in re.split(r",|;|&| and | und ", text or "") if db.norm(n)}


def _groups(user_id: int) -> list[dict]:
    """Alles, was ein Benutzer angefangen oder gehört hat, je Serie (Fassung) oder Einzelbuch, mit Gewicht.

    - Je gehörtem Band steigt das Gewicht (fertig = 1, ab einem Viertel angehört = ½), abgeflacht: 1 + log2(Anzahl).
    - Nur ein angefangener, nicht fertiger Band einer Serie zählt nicht; ein Einzelbuch zählt nur fertig gehört.
      Solche Gruppen tragen Gewicht 0.
    - Vollständig gehörte Serien (mindestens zwei Bände, jeder vorhandene Band fertig) bekommen +2.
    - Was lange nicht mehr gehört wurde, zählt etwas weniger (Halbwertszeit 180 Tage auf 40 % des Gewichts).
    - Abgebrochen: der zuletzt gehörte Band steht seit 60 Tagen unter der Hälfte. Gehörte Serie: ×0,6;
      sonst wird die Gruppe ein leichtes Gegensignal.
    - Sterne: Durchschnitt der bewerteten Bände. Ab 2,5 Sterne Faktor 0,75–1,5; darunter wird die
      Serie ein Gegensignal (2 Sterne −0,5, 1 Stern −1).
    """
    from . import ratings
    c = db.conn()
    stars = ratings.stars_of(user_id)
    rows = c.execute(
        "SELECT b.id, b.asin, b.lang, b.title, b.author, b.narrator, b.edition_id, e.series_id, e.name series,"
        " p.finished, p.progress, p.updated FROM user_progress p JOIN book b ON b.id=p.book_id LEFT JOIN edition e ON e.id=b.edition_id"
        " WHERE p.user_id=? AND (p.finished=1 OR p.progress > 0) ORDER BY p.updated DESC", (user_id,)).fetchall()
    groups: dict = {}
    for r in rows:
        key = ("e", r["edition_id"]) if r["edition_id"] else ("b", r["id"])
        g = groups.setdefault(key, {"name": r["series"] or r["title"], "series": bool(r["edition_id"]), "edition_id": r["edition_id"],
                                    "series_id": r["series_id"], "finished": 0, "partial": 0, "last": r["updated"],
                                    "dropped": not r["finished"] and (r["progress"] or 0) < DROP_BELOW and _age_days(r["updated"]) > DROP_DAYS,
                                    "books": [], "authors": set(), "narrators": set(), "stars": []})
        if r["id"] in stars:
            g["stars"].append(stars[r["id"]])
        if r["finished"]:
            g["finished"] += 1
        elif (r["progress"] or 0) >= 0.25:
            g["partial"] += 1
        if r["asin"] and audible.ASIN_OK.match(r["asin"]):
            g["books"].append({"asin": r["asin"], "lang": r["lang"]})
        if r["author"]:
            g["authors"].add(db.norm(abs_mod.primary_author(r["author"])))
        g["narrators"] |= _names(r["narrator"])
    out = []
    for g in groups.values():
        n = g["finished"] + 0.5 * g["partial"]
        counts = g["finished"] > 0 or g["partial"] >= 2 if g["series"] else g["finished"] > 0
        complete = False
        if counts and g["series"] and g["finished"] >= 2:
            owned = c.execute("SELECT COUNT(*) FROM book WHERE edition_id=? AND owned=1", (g["edition_id"],)).fetchone()[0]
            done = c.execute("SELECT COUNT(*) FROM book b JOIN user_progress p ON p.book_id=b.id AND p.user_id=? AND p.finished=1"
                             " WHERE b.edition_id=? AND b.owned=1", (user_id, g["edition_id"])).fetchone()[0]
            complete = owned >= 2 and done >= owned
        weight = 0.0
        if counts:
            weight = 1 + math.log2(max(1.0, n)) + (2 if complete else 0)
            weight *= 0.6 + 0.4 * 0.5 ** (_age_days(g["last"]) / HALF_LIFE)
            if g["dropped"]:
                weight *= DROP_FACTOR
        avg = sum(g["stars"]) / len(g["stars"]) if g["stars"] else None
        disliked = avg is not None and avg < 2.5
        if avg is not None and not disliked:
            weight *= stars_factor(avg)
        if disliked:
            counts, weight = False, DISLIKE[max(1, min(2, round(avg)))]
        out.append({**g, "complete": complete, "heard": n, "weight": round(weight, 3), "counts": counts,
                    "stars_avg": avg, "disliked": disliked})
    return out


def sources(user_id: int | None) -> list[dict]:
    """Gehörte Serien und Einzelbücher mit positivem Gewicht, stärkste zuerst, Bücher fürs Audible-Kontingent verteilt."""
    if not user_id:
        return []
    out = sorted((g for g in _groups(user_id) if g["counts"] and g["books"]), key=lambda s: -s["weight"])
    # Kontingent: zuerst je Serie der zuletzt gehörte Band, dann bis zu zwei weitere der stärksten Serien
    out = out[:SOURCE_BOOKS]
    budget = SOURCE_BOOKS - len(out)
    for s in out:
        extra = s["books"][1:PER_SERIES][:max(0, budget)]
        budget -= len(extra)
        s["books"] = s["books"][:1] + extra
    return out


def dropped(user_id: int | None) -> list[dict]:
    """Gegensignale: schlecht bewertete Serien (1–2 Sterne) und nur angefangene, abgebrochene. Ihr Ähnliches rutscht nach unten."""
    if not user_id:
        return []
    groups = _groups(user_id)
    bad = sorted((g for g in groups if g["disliked"] and g["books"]), key=lambda g: g["weight"])
    left = sorted((g for g in groups if not g["counts"] and not g["disliked"] and g["dropped"] and g["books"]),
                  key=lambda g: g["last"] or "", reverse=True)
    return ([{**g, "books": g["books"][:1]} for g in bad[:DROP_SOURCES]]
            + [{**g, "weight": DROP_PENALTY, "books": g["books"][:1]} for g in left[:DROP_SOURCES]])


def peers(user_id: int) -> dict[int, float]:
    """«Hörer mit ähnlichem Geschmack»: andere ABW-Benutzer, die mindestens zwei Serien mit dir teilen.
    Was sie gehört haben und du noch nicht angefangen hast, zählt nach Ähnlichkeit (Kosinus) mal ihrem Gewicht.
    Liefert je Fassung (edition_id) die Summe."""
    c = db.conn()
    mine = {g["series_id"] for g in _groups(user_id) if g["counts"] and g["series_id"]}
    touched = {r[0] for r in c.execute("SELECT DISTINCT e.series_id FROM user_progress p JOIN book b ON b.id=p.book_id"
                                       " JOIN edition e ON e.id=b.edition_id WHERE p.user_id=?", (user_id,))}
    if len(mine) < PEER_MIN_SHARED:
        return {}
    out: dict[int, float] = {}
    for (other,) in c.execute("SELECT DISTINCT user_id FROM user_progress WHERE user_id<>?", (user_id,)).fetchall():
        theirs = [g for g in _groups(other) if g["counts"] and g["series_id"]]
        ids = {g["series_id"] for g in theirs}
        shared = len(mine & ids)
        if shared < PEER_MIN_SHARED:
            continue
        sim = shared / math.sqrt(len(mine) * len(ids))
        for g in theirs:
            if g["series_id"] not in touched:
                out[g["edition_id"]] = out.get(g["edition_id"], 0) + sim * g["weight"] * PEER_FACTOR
    return out


def _peer_item(edition_id: int) -> dict | None:
    """Vorschlag aus dem eigenen Bestand: frühester vorhandener Band der Fassung, in derselben Form wie bei Audible."""
    r = db.conn().execute(
        "SELECT b.asin, b.lang, b.title, b.subtitle, b.author, b.narrator, b.sequence, b.release_date, b.runtime_min, b.cover_url,"
        " e.name series, e.audible_asin FROM book b JOIN edition e ON e.id=b.edition_id WHERE b.edition_id=? AND b.owned=1"
        " ORDER BY b.seq IS NULL, b.seq LIMIT 1", (edition_id,)).fetchone()
    if not r or not r["asin"]:
        return None
    return {"asin": r["asin"], "lang": r["lang"], "title": r["title"], "subtitle": r["subtitle"], "author": r["author"],
            "narrator": r["narrator"], "series": r["series"], "sequence": r["sequence"], "series_asin": r["audible_asin"],
            "release_date": r["release_date"], "runtime_min": r["runtime_min"], "cover_url": r["cover_url"],
            "language": config.AUDIBLE_LANG.get(r["lang"]), "rating": None, "ratings": None, "genres": []}


def known_people(srcs: list[dict]) -> tuple[Counter, Counter]:
    """Autoren und Sprecher der gehörten Serien: je Name, in wie vielen Serien er vorkommt."""
    authors, narrators = Counter(), Counter()
    for s in srcs:
        authors.update(s["authors"])
        narrators.update(s["narrators"])
    return authors, narrators


def people_bonus(item: dict, authors: Counter, narrators: Counter) -> tuple[float, bool, list[str]]:
    """Zuschlag für Autoren und Sprecher, die du schon gehört hast."""
    a = authors.get(db.norm(abs_mod.primary_author(item.get("author") or "")), 0)
    raw = [n.strip() for n in re.split(r",|;|&| and | und ", item.get("narrator") or "") if n.strip()]
    hit = [n for n in raw if narrators.get(db.norm(n))]
    bonus = min(AUTHOR_MAX, AUTHOR_BONUS * a) + min(NARRATOR_MAX, NARRATOR_BONUS * sum(narrators[db.norm(n)] for n in hit))
    return bonus, a > 0, hit


def suggestions(user_id: int | None) -> dict:
    """Serien und Einzelbücher, die zu deinen gehörten Serien passen.

    Quellen: Audible «Hörer mochten auch» zu jeder gehörten Serie (mit ihrem Gewicht, siehe `_groups`), abgebrochene
    Serien als Gegensignal, andere ABW-Benutzer mit ähnlichem Geschmack, dazu Zuschläge für bekannte Autoren und Sprecher
    und das Genreprofil. Was schon in ABW ist (Bestand, bekannte Serie, andere Ausgabe desselben Buchs), trägt «present»
    und kann in der Oberfläche ausgeblendet werden. Die 60 Plätze gelten nur für neue Vorschläge.
    """
    srcs = sources(user_id)
    if not srcs:
        return {"based_on": 0, "complete": 0, "items": []}
    c = db.conn()
    have = {r["asin"] for r in c.execute("SELECT asin FROM book WHERE asin IS NOT NULL")}
    # Andere Ausgaben desselben Buchs (Herr der Ringe als Hörspiel, andere Sprecher): gleicher Titel, gleicher Autor
    owned_titles = {(_head(r["title"]), db.norm(abs_mod.primary_author(r["author"])))
                    for r in c.execute("SELECT title, author FROM book WHERE owned=1 AND title IS NOT NULL AND author IS NOT NULL")}
    known_series = {r["audible_asin"] for r in c.execute("SELECT audible_asin FROM edition WHERE audible_asin IS NOT NULL")}
    groups: dict[str, dict] = {}

    def group(it: dict) -> dict:
        present = (it["asin"] in have or bool(it["series_asin"] and it["series_asin"] in known_series)
                   or (_head(it["title"]), db.norm(abs_mod.primary_author(it["author"]))) in owned_titles)
        g = groups.setdefault(it["series_asin"] or it["asin"], {"item": it, "by": {}, "neg": {}, "peer": 0.0, "present": True})
        g["present"] = g["present"] and present  # vorhanden erst, wenn nichts aus der Gruppe neu ist
        # Von einer Serie den frühesten Band zeigen
        seq, cur = db.parse_seq(it["sequence"]), db.parse_seq(g["item"]["sequence"])
        if seq is not None and (cur is None or seq < cur):
            g["item"] = it
        return g

    for s in srcs + dropped(user_id):
        for src in s["books"]:
            try:
                sims = audible.similar(src["lang"], src["asin"])
            except Exception:
                continue  # ein einzelner Fehler soll die Liste nicht verhindern
            for rank, it in enumerate(sims):
                g = group(it)
                # Je Serie zählt nur ihr bester Platz, sonst zählten mehrere Bände derselben Serie mehrfach
                part = s["weight"] * (1 - rank / 60)  # vordere Plätze bei Audible zählen etwas mehr
                if part < 0:
                    g["neg"][s["name"]] = min(part, g["neg"].get(s["name"], 0))
                elif part > g["by"].get(s["name"], (0, False))[0]:
                    g["by"][s["name"]] = (part, s["complete"])
    for edition_id, w in peers(user_id).items():
        it = _peer_item(edition_id)
        if it:
            group(it)["peer"] += w
    prof = profile(user_id)
    authors, narrators = known_people(srcs)
    out = []
    for g in groups.values():
        if not g["by"] and not g["peer"]:
            continue  # nur aus Gegensignalen: kein Vorschlag
        f, genres = fit(g["item"], prof)
        bonus, author_known, narr = people_bonus(g["item"], authors, narrators)
        rb = rating_bonus(g["item"].get("rating"), g["item"].get("ratings"))
        by = sorted(g["by"].items(), key=lambda kv: -kv[1][0])
        score = sum(v[0] for _, v in by) + sum(g["neg"].values()) + g["peer"] + bonus + f + rb
        out.append({**g["item"], "because": [k for k, _ in by[:3]], "because_complete": [k for k, v in by[:3] if v[1]],
                    "matches": len(by), "fit_genres": genres, "peer": g["peer"] > 0, "author_known": author_known,
                    "narrator_known": narr, "rating_bonus": round(rb, 2), "score": score, "present": g["present"],
                    "tags": [], "fit_tags": [], "series_books": None, "series_min": None, "explore": False})
    out.sort(key=lambda r: -r["score"])
    tprof = tag_profile(srcs)
    add_tags(out[:TAG_CANDIDATES], tprof)
    out.sort(key=lambda r: -r["score"])
    add_series_size([r for r in out if not r["present"]][:SERIES_CANDIDATES])
    out.sort(key=lambda r: (-r["score"], -(r["ratings"] or 0)))
    for r in out:
        r["score"] = round(r["score"], 2)
    out = diversify(out, tprof)
    try:
        from . import goodreads
        goodreads.remember_wanted([r for r in out if not r["present"]][:100])  # deren Genres holt die Hintergrund-Aufgabe
    except Exception:
        pass
    fresh = [r for r in out if not r["present"]][:60]
    # Serienkarten: alle gezeigten neuen Serien tragen ihre Bandzahl, die Reihenfolge bleibt
    add_series_size(fresh, bonus=False)
    cut = out.index(fresh[-1]) + 1 if fresh else len(out)
    top_tags = sorted(tprof.items(), key=lambda kv: -kv[1])[:8]
    return {"based_on": len(srcs), "complete": sum(1 for s in srcs if s["complete"]),
            "rated": sum(1 for s in srcs if s.get("stars_avg") is not None), "disliked": sum(1 for d in dropped(user_id) if d["disliked"]),
            "tag_profile": [{"tag": t, "share": round(v, 2)} for t, v in top_tags], "items": out[:cut]}


def tag_profile(srcs: list[dict]) -> dict[str, float]:
    """Anteil je Unterart-Tag, gewichtet wie die Serien (Sterne eingerechnet). Allgemeine Tags (Epic, Action) zählen nicht."""
    count, total = Counter(), sum(s["weight"] for s in srcs) or 1
    for lang in ("en", "de"):
        firsts = {}
        for s in srcs:
            b = s["books"][0]
            if b["lang"] == lang:
                firsts[b["asin"]] = firsts.get(b["asin"], 0) + s["weight"]
        if not firsts:
            continue
        try:
            found = tags.of_books(lang, list(firsts))
        except Exception:
            continue
        for asin, ts in found.items():
            for t in ts - tags.GENERIC:
                count[t] += firsts.get(asin, 0)
    return {t: n / total for t, n in count.items()}


def add_tags(items: list[dict], tprof: dict[str, float]) -> None:
    """Tags der Vorschläge holen und den Zuschlag aus dem Tag-Profil auf die Punkte rechnen."""
    for lang in ("en", "de"):
        lst = [i for i in items if i["lang"] == lang and i.get("asin") and audible.ASIN_OK.match(i["asin"])]
        if not lst:
            continue
        try:
            found = tags.of_books(lang, [i["asin"] for i in lst])
        except Exception:
            continue
        for i in lst:
            ts = found.get(i["asin"], set())
            special = sorted(ts - tags.GENERIC, key=lambda t: -tprof.get(t, 0))
            i["tags"] = special + sorted(ts & tags.GENERIC)
            i["fit_tags"] = [t for t in special if tprof.get(t, 0) >= FIT_MIN]
            i["tag_bonus"] = round(min(TAG_MAX, TAG_WEIGHT * sum(tprof.get(t, 0) for t in special)), 2)
            i["score"] += i["tag_bonus"]


def series_info(lang: str, series_asin: str) -> dict | None:
    """Einzelbände einer Audible-Serie (ohne Sammelbände) und ihre Gesamtlaufzeit in Minuten,
    je Bandnummer eine Ausgabe; angekündigte Bände ohne Laufzeit zählen nur bei der Anzahl. Zwei Wochen zwischengespeichert."""
    def fetch():
        p = audible._get(lang, f"/1.0/catalog/products/{series_asin}", response_groups="relationships").get("product", {})
        rel = [r for r in p.get("relationships") or [] if r.get("relationship_to_product") == "child"]
        first: dict[str, str] = {}
        for r in rel:
            seq = r.get("sequence")
            if seq and not re.search(r"[-,]", seq) and r.get("asin"):
                first.setdefault(seq, r["asin"])
        mins = sum((x.get("runtime_length_min") or 0) for x in audible.products(lang, list(first.values()))) if first else 0
        return {"books": len(first), "min": mins}
    try:
        return audible._cached(f"seriesinfo:{lang}:{series_asin}", 24 * 14, fetch)
    except Exception:
        return None


def series_size(lang: str, series_asin: str) -> int | None:
    """Anzahl Einzelbände einer Audible-Serie (ohne Sammelbände)."""
    info = series_info(lang, series_asin)
    return info["books"] if info else None


def fill_series_info(items: list[dict], lang_key: str = "lang", asin_key: str = "series_asin") -> None:
    """Bandzahl (`series_books`) und Gesamtlaufzeit (`series_min`) parallel nachtragen, wo sie fehlen."""
    todo = [i for i in items if i.get(asin_key) and i.get(lang_key) and i.get("series_min") is None]
    keys = list({(i[lang_key], i[asin_key]) for i in todo})
    if not keys:
        return
    with ThreadPoolExecutor(8) as ex:
        found = dict(zip(keys, ex.map(lambda k: series_info(*k), keys)))
    for i in todo:
        info = found.get((i[lang_key], i[asin_key]))
        if info:
            i["series_min"] = info["min"]
            if i.get("series_books") is None:
                i["series_books"] = info["books"]


def series_bonus(n: int | None) -> float:
    """Serien, die man länger hören kann, leicht bevorzugen."""
    if not n:
        return 0.0
    return 0.5 if n >= 5 else 0.3 if n >= 3 else 0.1 if n == 2 else -0.2


def add_series_size(items: list[dict], bonus: bool = True) -> None:
    """Bandzahl der Serie eintragen; mit `bonus` fliesst sie auch in die Punktzahl ein."""
    todo = [i for i in items if i.get("series_asin") and i.get("series_books") is None]
    fill_series_info(todo)
    for i in todo:
        if bonus:
            i["score"] += series_bonus(i.get("series_books"))


def rating_bonus(avg: float | None, n: int | None) -> float:
    """Audible-Bewertung als kleiner Zuschlag, geglättet: wenige Stimmen zählen kaum."""
    if not avg or not n:
        return 0.0
    adj = (avg * n + RATING_PRIOR * RATING_N) / (n + RATING_N)
    return max(RATING_MIN, min(RATING_MAX, (adj - RATING_PRIOR) * RATING_FACTOR))


DIVERSE_PENALTY = 2.0  # Abzug je weiterem Titel derselben Quellserie über drei hinaus


def diversify(items: list[dict], tprof: dict[str, float]) -> list[dict]:
    """Abwechslung: je Liste (neue und vorhandene getrennt, damit es auch mit dem Filter
    «Vorhandene ausblenden» stimmt) bekommt ab dem vierten Titel derselben Quellserie jeder weitere einen Abzug.
    Dazu stehen auf Platz 7 und 14 der neuen Vorschläge je ein gut bewerteter Titel aus einer Unterart, die du noch
    kaum hörst («Mal was anderes»)."""
    for present in (False, True):
        seen = Counter()
        for r in [x for x in items if x["present"] == present]:
            key = r["because"][0] if r["because"] else "peer"
            seen[key] += 1
            if seen[key] > DIVERSE_PER:
                r["score"] = round(r["score"] - DIVERSE_PENALTY * (seen[key] - DIVERSE_PER), 2)
    out = sorted(items, key=lambda r: (-r["score"], -(r["ratings"] or 0)))
    fresh = [r for r in out if not r["present"]]
    top = {t for t, v in tprof.items() if v >= FIT_MIN}
    pool = [r for r in fresh[DIVERSE_TOP:] if r["tags"] and not (set(r["tags"]) & top) and set(r["tags"]) - tags.GENERIC
            and (r["rating"] or 0) >= 4.4 and (r["ratings"] or 0) >= 200]
    pool.sort(key=lambda r: -(rating_bonus(r["rating"], r["ratings"]) + r["score"] / 10))
    for pos, r in zip(EXPLORE_AT, pool):
        out.remove(r)
        fresh.remove(r)
        r["explore"] = True
        before = fresh[pos - 1] if pos - 1 < len(fresh) else None  # an diese Stelle der neuen Vorschläge
        out.insert(out.index(before) if before else len(out), r)
        fresh.insert(pos - 1, r)
    return out


def mark_fit(items: list[dict], user_id: int | None) -> list[dict]:
    """Toplisten: jeder Titel trägt, wie gut er zu deinen Genres passt. Sortiert wird in der Oberfläche (abschaltbar)."""
    prof = profile(user_id)
    out = []
    for it in items:
        f, genres = fit(it, prof)
        out.append({**it, "fit": round(f, 2), "fit_genres": genres, "fits": f >= FIT_MIN})
    return out
