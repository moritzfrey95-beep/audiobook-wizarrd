"""Wunschliste aus Goodreads. Goodreads hat die RSS-Adressen Ende Oktober 2026 abgeschaltet; gelesen wird deshalb
das öffentliche Regal-Widget (die 100 zuletzt hinzugefügten Bücher eines Regals) und, für ganze Listen, der CSV-Export
(Goodreads: «My Books», «Import and export»). Jedes Buch wird bei Audible gesucht; Treffer kommen als fehlend in die
Bibliothek und werden mit Vorrang gesucht. Jeder Benutzer verknüpft sein eigenes Regal."""
import csv
import html
import io
import re
from datetime import datetime, timedelta, timezone

import httpx

from . import abs as abs_mod
from . import audible, auth, config, db, library

WIDGET = "https://www.goodreads.com/review/custom_widget/{uid}.js"
RETRY_DAYS = 14  # nicht gefundene Bücher so oft erneut bei Audible suchen
ITEM = re.compile(r'gr_custom_title_.*?<a[^>]*>(.*?)<\\?/a>.*?gr_custom_author_.*?<a[^>]*>(.*?)<\\?/a>', re.S)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_user(text: str) -> str:
    """Benutzernummer aus der Nummer selbst oder aus einer Profil-Adresse («…/user/show/12345-name»)."""
    m = re.search(r"(?:user/show/|review/list/)(\d+)", text or "") or re.fullmatch(r"\s*(\d+)\s*", text or "")
    if not m:
        raise ValueError("Das ist keine Goodreads-Benutzernummer und keine Profil-Adresse")
    return m.group(1)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text.replace("\\/", "/").replace('\\"', '"')))).strip()


def parse_widget(js: str) -> list[dict]:
    return [{"title": t, "author": a} for t, a in ((_clean(t), _clean(a)) for t, a in ITEM.findall(js)) if t and a]


def fetch(uid: str, shelf: str) -> list[dict]:
    """Die 100 zuletzt hinzugefügten Bücher des Regals (mehr gibt das Widget nicht her)."""
    with httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) abw"}) as http:
        r = http.get(WIDGET.format(uid=uid), params={"shelf": shelf, "num_books": 100, "sort": "date_added", "order": "d",
                                                     "show_title": 1, "show_author": 1, "show_cover": 0, "show_rating": 0,
                                                     "show_review": 0, "show_tags": 0})
    if r.status_code == 404:
        raise ValueError("Goodreads kennt diese Benutzernummer nicht")
    r.raise_for_status()
    if "widget_code" not in r.text:
        raise ValueError("Goodreads liefert das Regal nicht. Ist das Profil öffentlich?")
    return parse_widget(r.text)


def parse_csv(text: str, shelf: str) -> list[dict]:
    """Bücher eines Regals aus dem Goodreads-Export."""
    rows = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not rows.fieldnames or not {"Title", "Author"} <= set(rows.fieldnames):
        raise ValueError("Das ist kein Goodreads-Export (Spalten «Title» und «Author» fehlen)")
    want = shelf.strip().lower()
    out = []
    for r in rows:
        shelves = {x.strip().lower() for x in ((r.get("Exclusive Shelf") or "") + "," + (r.get("Bookshelves") or "")).split(",") if x.strip()}
        if (not want or want in shelves) and (r.get("Title") or "").strip():
            out.append({"title": r["Title"].strip(), "author": (r.get("Author") or "").strip()})
    return out


def _key(item: dict) -> str:
    return db.norm(re.sub(r"\([^)]*\)", " ", item["title"])) + "|" + db.norm(item["author"])


def prefs(user_id: int) -> tuple[str, str]:
    """Goodreads-Nummer und Regal eines Benutzers. Ohne Anmeldung gelten die allgemeinen Einstellungen."""
    row = auth.get(user_id)
    if row:
        return row["goodreads_user"], row["goodreads_shelf"] or "to-read"
    return db.setting("goodreads_user"), db.setting("goodreads_shelf") or "to-read"


def set_prefs(user_id: int, uid: str, shelf: str) -> None:
    if auth.get(user_id):
        db.conn().execute("UPDATE user SET goodreads_user=?, goodreads_shelf=? WHERE id=?", (uid, shelf or "to-read", user_id))
    else:
        db.set_setting("goodreads_user", uid)
        db.set_setting("goodreads_shelf", shelf or "to-read")


def owners() -> list[int]:
    return [r["id"] for r in db.conn().execute("SELECT id FROM user ORDER BY id")] or [1]


def remember(items: list[dict], user_id: int = 1) -> int:
    """Neue Einträge vormerken; gesucht werden sie im nächsten Abgleich. Liefert die Anzahl neuer Einträge."""
    c = db.conn()
    new = 0
    for it in items:
        new += c.execute("INSERT OR IGNORE INTO wishlist_item(source, ext_id, title, author, state, checked_at) VALUES(?,?,?,?,'new',?)",
                         (f"gr{user_id}", _key(it), it["title"], it["author"], _now())).rowcount
    return new


def import_csv(text: str, user_id: int = 1) -> dict:
    items = parse_csv(text, prefs(user_id)[1])
    return {"total": len(items), "new": remember(items, user_id)}


def _head(title: str) -> str:
    """Haupttitel ohne Reihenangabe in Klammern und ohne Untertitel."""
    return db.norm(re.split(r"[:(]", title)[0])


def find(item: dict) -> dict | None:
    """Das Hörbuch zu einem Goodreads-Eintrag bei Audible: Haupttitel und Autor müssen stimmen. Englisch zuerst."""
    title = re.sub(r"\([^)]*\)", " ", item["title"]).strip()
    want = _head(item["title"])
    sur = db.norm((abs_mod.primary_author(item["author"]).split() or [""])[-1])
    if not want or not sur:
        return None
    for lang in config.LANGS:
        d = audible._get(lang, "/1.0/catalog/products", title=title, author=item["author"], num_results=10, response_groups=audible.GROUPS)
        for p in d.get("products", []):
            b = audible.brief(p, lang)
            if b["language"] != config.AUDIBLE_LANG[lang] or not b["title"] or not b["asin"]:
                continue
            if _head(b["title"]) == want and sur in db.norm(b["author"]):
                return b
    return None


def sync(progress=lambda msg: None) -> str:
    c = db.conn()
    retry = (datetime.now(timezone.utc) - timedelta(days=RETRY_DAYS)).isoformat()
    seen = None
    todo = []
    any_user = False
    for user_id in owners():
        uid, shelf = prefs(user_id)
        if uid:
            any_user = True
            try:
                items = fetch(uid, shelf)
            except Exception:
                if len(owners()) == 1:
                    raise
                continue  # das Regal eines Benutzers ist nicht lesbar: die anderen trotzdem abgleichen
            seen = (seen or 0) + len(items)
            remember(items, user_id)
        todo += [(user_id, r) for r in c.execute(
            "SELECT * FROM wishlist_item WHERE source=? AND (state='new' OR (state='missing' AND checked_at < ?))", (f"gr{user_id}", retry)).fetchall()]
    if not any_user and not todo:
        return "Keine Goodreads-Wunschliste eingerichtet"
    added = 0
    for n, (user_id, it) in enumerate(todo, 1):
        progress(f"{n} von {len(todo)} Büchern bei Audible gesucht")
        try:
            b = find(dict(it))
        except Exception:
            continue  # Audible gerade nicht erreichbar: beim nächsten Lauf wieder
        state = "missing"
        if b:
            owned = c.execute("SELECT 1 FROM book WHERE asin=? AND owned=1", (b["asin"],)).fetchone()
            state = "owned" if owned else "added"
            if not owned:
                library.add({"asin": b["asin"], "lang": b["lang"], "title": b["title"], "author": b["author"], "series": b["series"],
                             "cover_url": b["cover_url"], "kind": "book"}, user_id)
                added += 1
        c.execute("UPDATE wishlist_item SET asin=?, lang=?, state=?, checked_at=? WHERE source=? AND ext_id=?",
                  (b["asin"] if b else None, b["lang"] if b else None, state, _now(), it["source"], it["ext_id"]))
    head = f"{seen} Bücher in den Goodreads-Regalen gelesen, " if seen is not None else ""
    return f"{head}{len(todo)} bei Audible gesucht, {added} in die Bibliothek aufgenommen"


def overview(user_id: int = 1) -> dict:
    c = db.conn()
    src = f"gr{user_id}"
    uid, shelf = prefs(user_id)
    counts = {r["state"]: r["n"] for r in c.execute("SELECT state, COUNT(*) n FROM wishlist_item WHERE source=? GROUP BY state", (src,))}
    missing = [dict(r) for r in c.execute("SELECT title, author FROM wishlist_item WHERE source=? AND state='missing' ORDER BY title LIMIT 200", (src,))]
    return {"user": uid, "shelf": shelf, "added": counts.get("added", 0), "owned": counts.get("owned", 0), "waiting": counts.get("new", 0), "missing": missing}
