"""Sprechern folgen: neue Hörbücher eines Sprechers bei Audible melden, wie bei Autoren und Serien.

Je markiertem Sprecher fragt die Aufgabe täglich Audible.com und Audible.de nach seinen neuesten Titeln. Beim ersten
Lauf gilt alles Gefundene als bekannt (keine Meldungsflut), danach meldet jeder neue Titel einmal an alle, die den
Sprecher markiert haben. Der Name muss so geschrieben sein wie bei Audible; verglichen wird ohne Akzente und Grossschreibung.
"""
from datetime import datetime, timezone

from . import audible, db, notifications


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def followed() -> dict[str, list[int]]:
    """Sprechername -> Benutzer, die ihn markiert haben (gleiche Namen in anderer Schreibweise zusammen)."""
    out: dict[str, list[int]] = {}
    names: dict[str, str] = {}
    for r in db.conn().execute("SELECT user_id, key FROM follow WHERE kind='narrator'"):
        k = db.norm(r["key"])
        names.setdefault(k, r["key"])
        out.setdefault(names[k], []).append(r["user_id"])
    return out


def titles(name: str, lang: str) -> list[dict]:
    """Neueste Titel, die dieser Sprecher liest (nur Treffer, bei denen er wirklich unter den Sprechern steht)."""
    d = audible._get(lang, "/1.0/catalog/products", narrator=name, num_results=50, products_sort_by="-ReleaseDate",
                     response_groups="contributors,product_attrs,media")
    want = db.norm(name)
    out = []
    for p in d.get("products", []):
        if any(db.norm(n.get("name")) == want for n in p.get("narrators") or []):
            out.append(audible.brief(p, lang))
    return out


def check(progress=lambda msg: None) -> str:
    c = db.conn()
    who = followed()
    if not who:
        return "Kein Sprecher markiert"
    new = 0
    for i, (name, users) in enumerate(who.items(), 1):
        progress(f"{i} von {len(who)}: {name}")
        key = db.norm(name)
        first = not c.execute("SELECT 1 FROM narrator_seen WHERE narrator=? LIMIT 1", (key,)).fetchone()
        for lang in ("en", "de"):
            try:
                found = titles(name, lang)
            except Exception:  # noqa: BLE001 - Audible kurz weg: beim nächsten Lauf
                continue
            for b in found:
                if not b["asin"] or not c.execute("INSERT OR IGNORE INTO narrator_seen(narrator, asin, seen_at) VALUES(?,?,?)",
                                                  (key, b["asin"], _now())).rowcount or first:
                    continue
                when = (b.get("release_date") or "")[:10]
                upcoming = when > datetime.now().date().isoformat()
                text = f"{b['title']} ({b['author']})" + (f", erscheint am {when[8:10]}.{when[5:7]}.{when[:4]}" if upcoming and when else "")
                new += notifications.emit("narrator", f"Neu gelesen von {name}", text, f"/autor/{db.norm(b['author'] or '')}",
                                          f"narrator:{key}:{b['asin']}", b.get("cover_url"), users=users)
    return f"{len(who)} Sprecher geprüft, {new} Meldungen"
