import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from functools import lru_cache

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel

from . import __version__
from . import abs as abs_mod
from . import activity, audible, auth, chapters, cleanup, config, convert, db, ebook, foryou, i18n, importer, integrity, libsplit, library, librarytags, listened, listening, notifications, notify, plugins, push, ratings, search, setup, tasks, titles, toread, torrent, usenet, variants, verify, wishlist

app = FastAPI(title="AudioBook Wizarrd", version=__version__)
PRIORITY = ["downloading", "review", "failed", "searching", "incomplete", "upcoming", "complete", "unknown", "single", "absent", "unmonitored"]


@app.on_event("startup")
def startup() -> None:
    db.init()
    config.reload(db.conn())  # in der Oberfläche gespeicherte Einstellungen
    if db.setting("setup_done") != "1" and setup_complete():
        db.set_setting("setup_done", "1")  # schon eingerichtete Installation (aus .env): kein Wizard
    if os.environ.get("ABW_NO_TASKS") != "1":
        tasks.start()


def open_date(d: str | None) -> bool:
    """Audible führt angekündigte Bände ohne Termin mit dem Jahr 2200."""
    return bool(d) and d >= "2100"


def auto_on() -> bool:
    return db.setting("auto_search") == "1"


def book_state(b, today: str, dl: dict | None = None) -> str:
    if b["owned"]:
        return "owned"
    own = (dl or {}).get(b["id"])
    if own:
        return "downloading" if own["state"] in ("queued", "downloading") else "review"
    if (b["release_date"] or "") > today:
        return "upcoming"
    return "missing"


def own_downloads() -> dict:
    return {r["book_id"]: dict(r) for r in db.conn().execute(
        "SELECT book_id, state, progress, release_title, source FROM download"
        " WHERE state IN ('queued','downloading','arrived','verified','review','imported')")}


def cover_of(b) -> str | None:
    if not b:
        return None
    return f"/api/cover/{b['abs_item_id']}" if b["abs_item_id"] else b["cover_url"]


def progress_of(user_id: int | None) -> dict:
    """Hörfortschritt eines Benutzers: Buch -> (Anteil, fertig gehört)."""
    if not user_id:
        return {}
    return {r["book_id"]: (r["progress"], bool(r["finished"])) for r in db.conn().execute(
        "SELECT book_id, progress, finished FROM user_progress WHERE user_id=?", (user_id,))}


def _mean_editions(eds: list[dict]) -> float:
    """Mittlere Laufzeit (Minuten) der Fassungen einer Serie, die Bände mit Laufzeit im Bestand haben."""
    mins = [sum(b["runtime_min"] or 0 for b in e["_books"] if b["owned"]) for e in eds]
    mins = [m for m in mins if m]
    return sum(mins) / len(mins) if mins else 0


def series_weights(c) -> dict[int, int]:
    """Je Serie die Anzahl Fassungen mit Laufzeit im Bestand. Ein Band zählt bei Autoren mit 1 / dieser Zahl, damit
    eine Serie mit der mittleren Laufzeit ihrer Fassungen eingeht."""
    return {r[0]: r[1] for r in c.execute("SELECT e.series_id, COUNT(DISTINCT b.edition_id) FROM book b JOIN edition e ON e.id=b.edition_id"
                                          " WHERE b.owned=1 AND b.runtime_min>0 GROUP BY e.series_id")}


def load(user_id: int | None = None) -> dict:
    """Alle Serien mit Fassungen, Büchern und berechnetem Status. Mit Benutzer: samt seinem Hörfortschritt."""
    c = db.conn()
    prog = progress_of(user_id)
    today = date.today().isoformat()
    dl = own_downloads()
    have_twin = {r["id"] for r in c.execute(db.HAVE_TWIN)}
    auto = auto_on()
    series_auto = {r["id"]: r["auto"] != 0 for r in c.execute("SELECT id, auto FROM series")}
    stars_all, stars_mine = defaultdict(list), defaultdict(list)  # Bewertungen
    for r in c.execute("SELECT book_id, user_id, stars FROM rating WHERE stars IS NOT NULL"):
        stars_all[r["book_id"]].append(r["stars"])
        if r["user_id"] == user_id:
            stars_mine[r["book_id"]].append(r["stars"])
    books = defaultdict(list)
    for b in c.execute("SELECT * FROM book WHERE edition_id IS NOT NULL ORDER BY seq IS NULL, seq, title"):
        books[b["edition_id"]].append(b)
    editions = defaultdict(list)
    for e in c.execute("SELECT * FROM edition ORDER BY lang DESC, variant IS NOT NULL, base_id IS NULL, variant, id"):  # en vor de, Hauptfassung, aufgeteilte, zugeordnete Fassungen
        bl = books.get(e["id"], [])
        # Dasselbe Hörbuch liegt schon im Bestand (andere Serie oder doppelter Audible-Eintrag): gilt als vorhanden
        states = ["owned" if not b["owned"] and b["id"] in have_twin else book_state(b, today, dl) for b in bl]
        # Doppelte Ausgaben desselben Buchs («Solo» und «Solo: Expanded Edition») als ein Band zeigen und zählen
        bl, states, also = titles.collapse(bl, states, e["name"])
        monitored = bool(e["monitored"]) if e["monitored"] is not None else any(b["owned"] for b in bl)
        if auto and monitored and series_auto.get(e["series_id"], True):
            # Die Automatik sucht diese Bände
            states = ["searching" if st in ("missing", "failed", "searching") and b["id"] not in dl else st for b, st in zip(bl, states)]
        cnt = Counter(states)
        owned = cnt["owned"]
        upcoming = cnt["upcoming"]
        missing = len(bl) - owned - upcoming
        monitored = bool(e["monitored"]) if e["monitored"] is not None else owned > 0
        flags = set()
        if not monitored:
            flags.add("unmonitored")
        else:
            for st in ("downloading", "review", "failed", "searching"):
                if cnt[st]:
                    flags.add(st)
            if missing:
                flags.add("incomplete")
            if upcoming:
                flags.add("upcoming")
            if not e["audible_asin"]:
                flags.add(e["kind"] if e["kind"] in ("single", "absent") else "unknown")
            elif not missing and not upcoming:
                flags.add("complete")
        nxt = min((b["release_date"] for b, s in zip(bl, states) if s == "upcoming" and not open_date(b["release_date"])), default=None)
        editions[e["series_id"]].append({
            "id": e["id"], "lang": e["lang"], "name": e["name"], "audible_asin": e["audible_asin"],
            "variant": variants.label(e), "extra": bool(e["variant"]), "drama": bool(e["drama"]),
            "monitored": monitored, "link_source": e["link_source"], "synced_at": e["synced_at"],
            "sync_error": e["sync_error"], "owned": owned, "total": owned + missing, "missing": missing,
            "upcoming": upcoming, "next_release": nxt, "flags": sorted(flags, key=PRIORITY.index),
            "_books": bl, "_states": states, "_also": also, "_prog": prog,
        })
    out = {}
    for s in c.execute("SELECT * FROM series"):
        eds = editions.get(s["id"], [])
        if not eds:
            continue
        mon = [e for e in eds if e["monitored"]]
        flags = set().union(*[e["flags"] for e in mon]) if mon else {"unmonitored"}
        if "complete" in flags and not all("complete" in e["flags"] for e in mon):
            flags.discard("complete")
        allb = [b for e in eds for b in e["_books"]]
        owned_books = [b for b in allb if b["owned"]]
        first = next((b for e in sorted(eds, key=lambda e: -e["owned"]) for b in e["_books"] if b["owned"]), None) or (allb[0] if allb else None)
        authors = Counter(abs_mod.primary_author(b["author"]) for b in (owned_books or allb) if b["author"])
        main = max(eds, key=lambda e: (not e["extra"], e["owned"]))
        author = authors.most_common(1)[0][0] if authors else "Unbekannt"
        out[s["id"]] = {
            "id": s["id"], "name": main["name"], "author": author, "author_key": db.norm(author),
            "cover": cover_of(first), "flags": sorted(flags, key=PRIORITY.index),
            "primary": min(flags, key=PRIORITY.index),
            "added_at": max((b["added_at"] or "" for b in owned_books), default=""),
            "next_release": min((e["next_release"] for e in mon if e["next_release"]), default=None),
            "missing": sum(e["missing"] for e in mon),
            # Mittel der Fassungen mit Laufzeit im Bestand, nicht die Summe
            "hours": round(_mean_editions(eds) / 60),
            "hours_editions": sum(1 for e in eds if any(b["owned"] and b["runtime_min"] for b in e["_books"])),
            "listened": sum(1 for b in owned_books if prog.get(b["id"], (0, False))[1]), "owned": len(owned_books),
            "auto": series_auto.get(s["id"], True),
            "rating": ratings.summary([x for b in allb for x in stars_all.get(b["id"], [])], [x for b in allb for x in stars_mine.get(b["id"], [])]),
            "editions": eds,
        }
    return out


def public(s: dict, with_books: bool = False, user_id: int | None = None) -> dict:
    dl = own_downloads() if with_books else {}
    eb = ebook.states() if with_books else {}
    rated = ratings.by_book(user_id) if with_books else {}
    eds = []
    for e in s["editions"]:
        d = {k: v for k, v in e.items() if not k.startswith("_")}
        if with_books:
            d["books"] = [{
                "id": b["id"], "asin": b["asin"], "title": titles.display(b["title"], e["name"]), "full_title": b["title"],
                "also": [{"title": o["title"], "owned": bool(o["owned"]), "abs_item_id": o["abs_item_id"]} for o in e["_also"].get(b["id"], [])],
                "subtitle": b["subtitle"], "author": b["author"],
                "narrator": b["narrator"], "sequence": b["sequence"],
                "release_date": None if open_date(b["release_date"]) else b["release_date"],
                "runtime_min": b["runtime_min"], "cover": cover_of(b), "state": st, "abs_item_id": b["abs_item_id"],
                "size": b["size"], "num_files": b["num_files"], "progress": e["_prog"].get(b["id"], (None, False))[0],
                "finished": e["_prog"].get(b["id"], (None, False))[1],
                "added_at": b["added_at"], "download": dl.get(b["id"]), "epub": bool(b["epub"]), "ebook": eb.get(b["id"]),
                "rating": rated.get(b["id"]),
                "status_from": "abw" if b["id"] in dl or (auto_on() and s["auto"] and e["monitored"]) else None,
            } for b, st in zip(e["_books"], e["_states"])]
        eds.append(d)
    return {**s, "editions": eds}


@app.get("/api/series")
def series_list(request: Request):
    return [{**public(s), "sort": db.norm(s["name"])} for s in sorted(load(request.state.user["id"]).values(), key=lambda s: db.norm(s["name"]))]


def single_groups(user_id: int | None = None, with_books: bool = False) -> dict[str, dict]:
    """Einzelbücher ohne Serie (vorhanden oder hinzugefügt) als eine Karte je Autor, «Autor – Einzelbücher».
    Nur für die Anzeige zusammengestellt, keine Serie in der Datenbank: Abgleich mit Audiobookshelf und
    Audible, Suche, Statistik und Empfehlungen bleiben unberührt."""
    c = db.conn()
    today, dl, auto = date.today().isoformat(), own_downloads(), auto_on()
    prog = progress_of(user_id)
    users = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM user")}
    stars_all, stars_mine = defaultdict(list), defaultdict(list)
    for r in c.execute("SELECT book_id, user_id, stars FROM rating WHERE stars IS NOT NULL"):
        stars_all[r["book_id"]].append(r["stars"])
        if r["user_id"] == user_id:
            stars_mine[r["book_id"]].append(r["stars"])
    rated = ratings.by_book(user_id) if with_books else {}
    eb = ebook.states() if with_books else {}
    rows: dict[str, list] = defaultdict(list)
    names: dict[str, str] = {}
    for b in c.execute("SELECT * FROM book WHERE edition_id IS NULL AND (owned=1 OR wanted IS NOT NULL) ORDER BY title"):
        author = abs_mod.primary_author(b["author"] or "") or "Unbekannt"
        key = db.norm(author) or "unbekannt"
        st = book_state(b, today, dl)
        if auto and st in ("missing", "failed") and b["id"] not in dl:
            st = "searching"  # die eigene Automatik sucht hinzugefügte Einzelbücher
        rows[key].append((b, st))
        names.setdefault(key, author)
    out = {}
    for key, items in rows.items():
        owned = [b for b, st in items if b["owned"]]
        states = Counter(st for _, st in items)
        flags = {"single"} | {f for f in ("downloading", "review", "failed", "searching", "upcoming") if states[f]}
        if states["missing"]:
            flags.add("incomplete")
        langs = []
        for lang in ("en", "de"):
            mine = [(b, st) for b, st in items if b["lang"] == lang]
            if mine:
                langs.append({"lang": lang, "owned": sum(1 for b, _ in mine if b["owned"]), "total": len(mine),
                              "upcoming": sum(1 for _, st in mine if st == "upcoming")})
        name = f"{names[key]} – Einzelbücher"
        g = {
            "key": key, "name": name, "sort": db.norm(name), "author": names[key], "author_key": key,
            "cover": cover_of(owned[0] if owned else items[0][0]), "flags": sorted(flags, key=PRIORITY.index),
            "primary": min(flags, key=PRIORITY.index),
            "added_at": max(((b["added_at"] if b["owned"] else b["wanted"]) or "" for b, _ in items), default=""),
            "next_release": min((b["release_date"] for b, st in items if st == "upcoming" and not open_date(b["release_date"])), default=None),
            "missing": states["missing"] + states["searching"] + states["failed"],
            "hours": round(sum(b["runtime_min"] or 0 for b in owned) / 60),
            "listened": sum(1 for b in owned if prog.get(b["id"], (0, False))[1]), "owned": len(owned), "total": len(items),
            "langs": langs, "titles": [b["title"] for b, _ in items],  # für die Suche der Serienansicht
            "rating": ratings.summary([x for b, _ in items for x in stars_all.get(b["id"], [])], [x for b, _ in items for x in stars_mine.get(b["id"], [])]),
        }
        if with_books:
            g["books"] = [{
                "id": b["id"], "asin": b["asin"], "title": b["title"], "full_title": b["title"], "also": [],
                "subtitle": b["subtitle"], "author": b["author"], "narrator": b["narrator"], "sequence": None, "lang": b["lang"],
                "release_date": None if open_date(b["release_date"]) else b["release_date"],
                "runtime_min": b["runtime_min"], "cover": cover_of(b), "state": st, "abs_item_id": b["abs_item_id"],
                "size": b["size"], "num_files": b["num_files"], "progress": prog.get(b["id"], (None, False))[0],
                "finished": prog.get(b["id"], (None, False))[1], "added_at": b["added_at"] if b["owned"] else b["wanted"],
                "added_by": None if b["owned"] else users.get(b["wanted_by"]),
                "download": dl.get(b["id"]), "epub": bool(b["epub"]), "ebook": eb.get(b["id"]), "rating": rated.get(b["id"]),
                "status_from": "abw" if b["id"] in dl or (auto and not b["owned"]) else None,
            } for b, st in items]
        out[key] = g
    return out


@app.get("/api/library/tags")
def library_tags():
    """Genre und Tags je Serie und Einzelbuch-Karte für die Filter der Serienansicht."""
    return librarytags.of_library()


@app.get("/api/singles")
def singles_list(request: Request):
    """Einzelbuch-Karten je Autor für die Serienansicht (früher nur die hinzugefügten, fehlenden Einzelbücher)."""
    return sorted(single_groups(me(request)["id"]).values(), key=lambda g: g["sort"])


@app.get("/api/singles/{key}")
def singles_detail(key: str, request: Request):
    g = single_groups(me(request)["id"], with_books=True).get(key)
    if not g:
        raise HTTPException(404, "Keine Einzelbücher von diesem Autor")
    return {**g, "abs_url": config.ABS_PUBLIC_URL, "can_mark": listened.status(me(request)["id"])}


@app.get("/api/series/{sid}")
def series_detail(sid: int, request: Request):
    s = load(request.state.user["id"]).get(sid)
    if not s:
        raise HTTPException(404, "Serie nicht gefunden")
    d = public(s, True, request.state.user["id"])
    have = {e["lang"] for e in s["editions"] if not e["extra"]}
    d["candidates"] = {lang: audible.suggestions(sid, lang)[:8] for lang in config.LANGS if lang not in have}
    d["abs_url"] = config.ABS_PUBLIC_URL
    d["can_mark"] = listened.status(request.state.user["id"])
    return d


class Monitor(BaseModel):
    edition_id: int
    monitored: bool


@app.post("/api/series/{sid}/monitor")
def monitor(sid: int, body: Monitor):
    db.conn().execute("UPDATE edition SET monitored=? WHERE id=? AND series_id=?", (int(body.monitored), body.edition_id, sid))
    return {"ok": True}


class Link(BaseModel):
    lang: str
    series_asin: str
    title: str


@app.post("/api/series/{sid}/link")
def link(sid: int, body: Link):
    if body.lang not in ("en", "de"):
        raise HTTPException(400, "Unbekannte Sprache")
    try:
        audible.link(sid, body.lang, body.series_asin, body.title, "manual")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@app.post("/api/series/{sid}/unlink/{edition_id}")
def unlink(sid: int, edition_id: int):
    c = db.conn()
    e = c.execute("SELECT * FROM edition WHERE id=? AND series_id=?", (edition_id, sid)).fetchone()
    if not e:
        raise HTTPException(404, "Fassung nicht gefunden")
    if e["variant"]:
        variants.detach(c, edition_id)  # Fassung derselben Sprache: nicht wieder automatisch zuordnen
        if e["base_id"]:
            return {"ok": True}
    if c.execute("SELECT 1 FROM book WHERE edition_id=? AND owned=1", (edition_id,)).fetchone():
        new = c.execute("INSERT INTO series(name, norm, added_at) VALUES(?,?,?)",
                        (e["name"], e["norm"] + ":" + e["lang"], datetime.now(timezone.utc).isoformat())).lastrowid
        c.execute("UPDATE edition SET series_id=? WHERE id=?", (new, edition_id))
    else:
        c.execute("DELETE FROM book WHERE edition_id=?", (edition_id,))
        c.execute("DELETE FROM edition WHERE id=?", (edition_id,))
    return {"ok": True}


class Pair(BaseModel):
    de: int
    en: int


@app.get("/api/pairs")
def pairs():
    """Vorschläge: deutsche und englische Fassung auf getrennten Karten, die wahrscheinlich zusammengehören."""
    return audible.pair_suggestions()


@app.post("/api/pairs/link")
def pairs_link(body: Pair):
    try:
        return {"ok": True, "series_id": audible.link_editions(body.de, body.en)}
    except ValueError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/pairs/reject")
def pairs_reject(body: Pair):
    audible.reject_pair(body.de, body.en)
    return {"ok": True}


@app.post("/api/series/{sid}/refresh")
def refresh(sid: int):
    for e in db.conn().execute("SELECT id FROM edition WHERE series_id=?", (sid,)).fetchall():
        audible.sync_edition(e["id"])
    return {"ok": True}


# ---------- Anmeldung ----------
# Ist ein Passwort gesetzt, verlangt jede Schnittstelle ausser /api/auth/ eine gültige Sitzung (auch die Cover).

LOCAL_HOST = re.compile(r"^(localhost|\[[0-9a-f:]+\]|\d{1,3}(\.\d{1,3}){3}|[a-z0-9-]+|.+\.(ts\.net|local|lan|home|fritz\.box))(:\d+)?$", re.I)
NO_PASSWORD = "ABW ist von aussen erst erreichbar, wenn ein Passwort gesetzt ist. Lege es im Heimnetz oder über Tailscale unter Einstellungen fest."


def external(request: Request) -> bool:
    """Kommt die Anfrage über einen Reverse Proxy oder eine öffentliche Adresse? Im Zweifel ja."""
    h = request.headers
    return any(k in h for k in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "x-real-ip", "forwarded", "via")) \
        or not LOCAL_HOST.match(h.get("host", ""))


# Benutzer mit der Rolle «user» dürfen ansehen, hinzufügen, markieren, die automatische Suche für Serien und Bücher
# anstossen und ihr Goodreads-Regal verknüpfen. Alles andere (Prüfen, Aufgaben, Schalter, Auswahl von Hand) ist dem Administrator vorbehalten.
USER_READ = re.compile(r"^/api/(series(/\d+(/autosearch)?)?|authors(/[^/]+)?|discover/.+|watchlist|calendar|stats|listening|cover/.+|notifications|follows(/overview)?|me|push|listened"
                       r"|books/\d+/autosearch|ratings/(count|open|mine|tags|stats)|toread(/keys|/asins|/heard|/users|/picks)?)$")
USER_WRITE = re.compile(r"^/api/(series/\d+/autosearch|books/\d+/autosearch|watchlist(/[^/]+)?|follow|notifications/(read|clear)|push/(subscribe|unsubscribe|test)"
                        r"|me/goodreads(/csv|/sync)?|me/prefs|books/\d+/listened|ratings/(book|edition)/\d+|toread(/order|/discover|/picks/decide|/recommend|/rec/[eb]\d+|/suggest/\d+|/(book|edition)/\d+)?)$")


@app.exception_handler(StarletteHTTPException)
async def translated_error(request: Request, exc: StarletteHTTPException):
    """Fehlermeldungen in der Sprache der Oberfläche."""
    detail = i18n.tr(exc.detail) if isinstance(exc.detail, str) else exc.detail
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    request.state.user = dict(auth.OPEN_USER)
    i18n.lang.set(i18n.pick(request.headers.get("x-abw-lang")))
    if path.startswith("/api/"):
        on = auth.enabled()
        # Ohne Passwort ist ABW nur direkt im Heimnetz offen. Von aussen geht dann gar nichts, auch nicht das Festlegen des Passworts.
        if not on and external(request):
            return JSONResponse({"detail": i18n.tr(NO_PASSWORD)}, status_code=403)
        if on:
            user = auth.session_user(request.cookies.get(auth.COOKIE))
            request.state.user = user
            if not path.startswith("/api/auth/"):
                if not user:
                    return JSONResponse({"detail": i18n.tr("Bitte anmelden.")}, status_code=401)
                if user["role"] != "admin" and not (USER_READ if request.method in ("GET", "HEAD") else USER_WRITE).match(path):
                    return JSONResponse({"detail": i18n.tr("Das darf nur ein Administrator.")}, status_code=403)
    return await call_next(request)


def me(request: Request) -> dict:
    return request.state.user


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "")


def _set_cookie(request: Request, response: Response, token: str) -> None:
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=https, path="/")


@app.get("/api/auth/state")
def auth_state(request: Request):
    on = auth.enabled()
    u = me(request)
    return {"enabled": on, "authenticated": bool(u), "user": u["name"] if u else None, "role": u["role"] if u else None,
            "id": u["id"] if u else None, "langs": config.LANGS}


class Login(BaseModel):
    user: str
    password: str


@app.post("/api/auth/login")
def auth_login(body: Login, request: Request, response: Response):
    try:
        token = auth.login(body.user, body.password, _ip(request), request.headers.get("user-agent", ""))
    except PermissionError as exc:
        raise HTTPException(429, str(exc))
    except ValueError as exc:
        raise HTTPException(401, str(exc))
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))
    _set_cookie(request, response, token)
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


class Password(BaseModel):
    user: str = ""
    password: str
    current: str = ""


@app.post("/api/auth/password")
def auth_password(body: Password, request: Request, response: Response):
    """Erstes Passwort festlegen (legt den Administrator an) oder das eigene ändern (angemeldet, mit dem bisherigen Passwort)."""
    agent = request.headers.get("user-agent", "")
    try:
        if not auth.enabled():
            uid = auth.create(body.user, body.password, "admin")
        else:
            u = me(request)
            if not u:
                raise HTTPException(401, "Bitte anmelden.")
            if auth.blocked(_ip(request)):
                raise HTTPException(403, "Zu viele Fehlversuche. Bitte in 15 Minuten erneut versuchen.")
            if not auth.has_password(u["id"]):
                raise HTTPException(400, "Du meldest dich mit deinem Audiobookshelf-Passwort an. Ändern kannst du es in Audiobookshelf.")
            if not auth.password_ok(u["name"], body.current):
                auth.fail(_ip(request))
                raise HTTPException(403, "Das bisherige Passwort stimmt nicht")
            uid = u["id"]
            auth.set_password(uid, body.password, body.user or None)
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc))
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))
    _set_cookie(request, response, auth.open_session(uid, agent))
    return {"ok": True}


class Current(BaseModel):
    current: str


@app.post("/api/auth/disable")
def auth_disable(body: Current, request: Request, response: Response):
    if not auth.enabled():
        return {"ok": True}
    u = me(request)
    try:
        ok = bool(u) and u["role"] == "admin" and auth.password_ok(u["name"], body.current)
    except ConnectionError as exc:
        raise HTTPException(503, str(exc))
    if not ok:
        raise HTTPException(403, "Das Passwort stimmt nicht")
    try:
        auth.disable()
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


# ---------- Benutzer (nur Administrator) ----------

@app.get("/api/users")
def user_list():
    return auth.users()


class NewUser(BaseModel):
    name: str
    password: str
    role: str = "user"


@app.post("/api/users")
def user_create(body: NewUser):
    if not auth.enabled():
        raise HTTPException(409, "Lege zuerst dein eigenes Passwort fest; damit wirst du Administrator.")
    try:
        return {"id": auth.create(body.name, body.password, body.role)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class EditUser(BaseModel):
    password: str | None = None
    role: str | None = None


@app.post("/api/users/{user_id}")
def user_edit(user_id: int, body: EditUser):
    try:
        if body.role:
            auth.set_role(user_id, body.role)
        if body.password:
            auth.set_password(user_id, body.password)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.delete("/api/users/{user_id}")
def user_delete(user_id: int, request: Request):
    if user_id == me(request)["id"]:
        raise HTTPException(409, "Den eigenen Zugang kannst du nicht löschen")
    try:
        auth.delete(user_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


# ---------- Nutzer aus Audiobookshelf übernehmen und verknüpfen (nur Administrator) ----------

def _abs_users() -> list[dict]:
    try:
        return abs_mod.users()
    except Exception as exc:
        raise HTTPException(502, f"Audiobookshelf liefert die Nutzer nicht: {exc}")


@app.get("/api/abs/users")
def abs_user_list():
    """Konten in Audiobookshelf und womit sie in ABW verknüpft sind."""
    mine = auth.users()
    by_abs = {u["abs_user_id"]: u for u in mine if u["abs_user_id"]}
    by_name = {u["name"].lower(): u for u in mine if not u["abs_user_id"]}
    first_linked = any(u["id"] == 1 and u["abs_user_id"] for u in mine)
    out = []
    for a in _abs_users():
        u = by_abs.get(a["id"])
        same = by_name.get(a["username"].lower())
        out.append({**a, "linked": {"id": u["id"], "name": u["name"]} if u else None,
                    "same_name": {"id": same["id"], "name": same["name"]} if same and not u else None,
                    # Das Konto, mit dem ABW liest, gehört dem Betreiber: nicht blind als zweiten Zugang anlegen
                    "skip_all": bool(a["own"] and not u and not first_linked and not same)})
    return sorted(out, key=lambda a: a["username"].lower())


class AbsImport(BaseModel):
    ids: list[str] | None = None  # ohne Angabe: alle aktiven, noch nicht verknüpften Konten


@app.post("/api/abs/users/import")
def abs_user_import(body: AbsImport):
    rows = abs_user_list()
    if body.ids is None:
        pick = [a for a in rows if a["isActive"] and not a["linked"] and not a["skip_all"]]
    else:
        pick = [a for a in rows if a["id"] in set(body.ids)]
    created, linked, errors = [], [], []
    for a in pick:
        try:
            _, new = auth.import_abs(a)
            (created if new else linked).append(a["username"])
        except ValueError as exc:
            errors.append(f"{a['username']}: {exc}")
    if created or linked:
        tasks.request("listening")
    return {"created": created, "linked": linked, "errors": errors}


class AbsLink(BaseModel):
    abs_user_id: str | None = None  # ohne Angabe: Verknüpfung trennen


@app.post("/api/users/{user_id}/abs")
def user_abs_link(user_id: int, body: AbsLink):
    try:
        a = None
        if body.abs_user_id:
            a = next((x for x in _abs_users() if x["id"] == body.abs_user_id), None)
            if not a:
                raise HTTPException(404, "Dieses Konto gibt es in Audiobookshelf nicht")
        auth.link_abs(user_id, a)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    tasks.request("listening")
    return {"ok": True}


# ---------- Eigenes Konto: Goodreads, Markierungen, Benachrichtigungen ----------

@app.get("/api/me")
def me_info(request: Request):
    u = me(request)
    row = auth.get(u["id"]) if auth.enabled() else None
    return {"id": u["id"], "user": u["name"], "role": u["role"], "auth_enabled": auth.enabled(), "session_days": auth.SESSION_DAYS,
            "min_length": auth.MIN_LENGTH, "goodreads": wishlist.overview(u["id"]),
            "abs_name": row["abs_name"] if row and row["abs_user_id"] else None, "has_password": bool(row) and "$" in row["hash"],
            "abs_url": config.ABS_PUBLIC_URL or config.ABS_URL, "follow_on_add": library.follows_on_add(u["id"])}


class Prefs(BaseModel):
    follow_on_add: bool


@app.post("/api/me/prefs")
def me_prefs(body: Prefs, request: Request):
    """Persönliche Einstellungen, die jeder Benutzer selbst setzt."""
    db.conn().execute("UPDATE user SET follow_on_add=? WHERE id=?", (int(body.follow_on_add), me(request)["id"]))
    return {"follow_on_add": body.follow_on_add}


class Goodreads(BaseModel):
    user: str
    shelf: str = "to-read"


@app.post("/api/me/goodreads")
def me_goodreads(body: Goodreads, request: Request):
    uid_me = me(request)["id"]
    try:
        uid = wishlist.parse_user(body.user) if body.user.strip() else ""
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    shelf = body.shelf.strip() or "to-read"
    wishlist.set_prefs(uid_me, uid, shelf)
    if uid:
        try:
            wishlist.fetch(uid, shelf)  # gleich prüfen, ob Goodreads das Regal herausgibt
        except Exception as exc:
            raise HTTPException(400, f"Gespeichert, aber das Regal lässt sich nicht lesen: {exc}")
        tasks.request("wishlist")
    return wishlist.overview(uid_me)


class Csv(BaseModel):
    text: str


@app.post("/api/me/goodreads/csv")
def me_goodreads_csv(body: Csv, request: Request):
    """Goodreads-Export einlesen: Bücher des eingestellten Regals vormerken und den Abgleich anstossen."""
    if len(body.text) > 20_000_000:
        raise HTTPException(413, "Die Datei ist zu gross")
    uid = me(request)["id"]
    try:
        r = wishlist.import_csv(body.text, uid)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if r["new"]:
        tasks.request("wishlist")
    return {**r, **wishlist.overview(uid)}


@app.post("/api/me/goodreads/sync")
def me_goodreads_sync():
    tasks.request("wishlist")
    return {"ok": True}


@app.get("/api/follows")
def follow_list(request: Request):
    return notifications.follows(me(request)["id"])


@app.get("/api/follows/overview")
def follow_overview(request: Request):
    """Was der Benutzer markiert hat, mit Namen und Cover, für die Übersicht unter Benachrichtigungen."""
    c = db.conn()
    f = notifications.follows(me(request)["id"])
    data = load()
    series = [{"id": s["id"], "name": s["name"], "author": s["author"], "author_key": s["author_key"], "cover": s["cover"]}
              for s in (data.get(sid) for sid in f["series"]) if s]
    want = set(f["authors"])
    names: dict[str, str] = {}
    for r in c.execute("SELECT DISTINCT author FROM book WHERE author IS NOT NULL"):
        name = abs_mod.primary_author(r["author"])
        if db.norm(name) in want:
            names.setdefault(db.norm(name), name)
    authors = [{"key": k, "name": names.get(k, k), "series": sum(1 for s in data.values() if db.norm(s["author"]) == k)} for k in want]
    return {"series": sorted(series, key=lambda s: db.norm(s["name"])), "authors": sorted(authors, key=lambda a: db.norm(a["name"]))}


class Follow(BaseModel):
    kind: str
    key: str
    on: bool = True


@app.post("/api/follow")
def follow_set(body: Follow, request: Request):
    try:
        notifications.follow(me(request)["id"], body.kind, body.key, body.on)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.get("/api/notifications")
def notification_list(request: Request, limit: int = 50, offset: int = 0, unread: int = 0):
    out = notifications.listing(me(request)["id"], max(1, min(limit, 200)), max(0, offset), bool(unread))
    for n in out["items"]:  # gespeichert wird Deutsch, angezeigt in der Sprache der Oberfläche
        n["title"], n["body"] = i18n.tr(n["title"]), i18n.tr(n["body"])
    return out


class Read(BaseModel):
    ids: list[int] | None = None  # ohne Angabe: alle


@app.post("/api/notifications/read")
def notification_read(body: Read, request: Request):
    notifications.mark_read(me(request)["id"], body.ids)
    return {"ok": True}


@app.post("/api/notifications/clear")
def notification_clear(request: Request):
    notifications.clear(me(request)["id"])
    return {"ok": True}


@app.get("/api/push")
def push_state(request: Request):
    return {"key": push.public_key(), "devices": push.devices(me(request)["id"])}


class PushSub(BaseModel):
    endpoint: str
    keys: dict[str, str] = {}


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSub, request: Request):
    try:
        push.subscribe(me(request)["id"], body.model_dump(), request.headers.get("user-agent", ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


class PushEnd(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: PushEnd, request: Request):
    push.unsubscribe(me(request)["id"], body.endpoint)
    return {"ok": True}


@app.post("/api/push/test")
def push_test(request: Request):
    uid = me(request)["id"]
    if not push.has_devices(uid):
        raise HTTPException(409, "Auf keinem Gerät ist Push eingeschaltet.")
    if not push.deliver(uid, [{"title": "AudioBook Wizarrd", "body": "Testmeldung: Push funktioniert auf diesem Gerät.", "url": "/benachrichtigungen", "tag": "abw-test"}]):
        raise HTTPException(502, "Der Push-Dienst hat die Meldung nicht angenommen.")
    return {"ok": True}


# ---------- Einstellungen ----------

# ---------- Einstellungen der Verbindungen (in der Datenbank, vor .env) ----------

def _config_view() -> dict:
    out = {}
    for name, f in config.FIELDS.items():
        value = config.raw(name)
        item = {"name": name, "kind": f.kind, "group": f.group, "source": config.source(name)}
        # Zugangsdaten verlassen den Server nie, die Oberfläche sieht nur, ob einer gesetzt ist
        item.update({"value": "", "set": bool(value)} if f.kind == "secret" else {"value": value})
        out[name] = item
    return {"fields": out, "langs": config.LANGS, "speech": verify.speech_available(), "plugins": plugins.errors}


def setup_complete() -> bool:
    """Mindestens Audiobookshelf mit Schlüssel und einer Bibliothek."""
    return bool(config.ABS_URL and config.abs_token() and any(config.ABS_LIBRARIES.values()))


@app.get("/api/setup")
def setup_state():
    return {"needed": db.setting("setup_done") != "1", "abs_ok": setup_complete(), "auth": auth.enabled(),
            "auto_on": any(db.setting(k) == "1" for k in ("auto_search", "auto_import", "auto_ebook"))}


@app.post("/api/setup/done")
def setup_done():
    if not setup_complete():
        raise HTTPException(400, "Audiobookshelf ist noch nicht eingerichtet (Adresse, Schlüssel und mindestens eine Bibliothek)")
    db.set_setting("setup_done", "1")
    return setup_state()


@app.get("/api/config")
def config_get():
    return _config_view()


class ConfigSave(BaseModel):
    values: dict[str, str | None]


@app.put("/api/config")
def config_put(body: ConfigSave):
    """Werte speichern. null setzt einen Wert auf .env zurück; ein leerer Text bei Zugangsdaten lässt sie unverändert."""
    vals = {}
    for name, value in body.values.items():
        f = config.FIELDS.get(name)
        if not f:
            raise HTTPException(400, f"Unbekannte Einstellung {name}")
        if f.kind == "secret" and value == "":
            continue
        if value is not None:
            value = value.strip()
            if f.kind == "url" and value and not re.match(r"^https?://", value):
                raise HTTPException(400, f"{name}: Adresse muss mit http:// oder https:// beginnen")
            if f.kind == "ids" and value and not re.fullmatch(r"\d+(\s*,\s*\d+)*", value):
                raise HTTPException(400, f"{name}: nur Zahlen mit Komma getrennt")
            if f.kind == "path" and value and not value.startswith("/"):
                raise HTTPException(400, f"{name}: Pfad muss mit / beginnen")
        vals[name] = value
    config.save(db.conn(), vals)
    return _config_view()


class ConfigTest(BaseModel):
    service: str
    values: dict[str, str] = {}


@app.post("/api/config/test")
def config_test(body: ConfigTest):
    """Verbindung prüfen, mit den übergebenen Werten; fehlende nimmt der Test aus den gespeicherten Einstellungen."""
    v = lambda name: body.values.get(name) or config.raw(name)
    tests = {
        "abs": lambda: setup.audiobookshelf(v("ABS_URL"), v("ABS_API_KEY")),
        "prowlarr": lambda: setup.prowlarr(v("PROWLARR_URL"), v("PROWLARR_API_KEY")),
        "sabnzbd": lambda: setup.sabnzbd(v("SAB_URL"), v("SABNZBD_API_KEY")),
        "qbit": lambda: setup.qbittorrent(v("QBIT_URL"), v("QBIT_USERNAME"), v("QBIT_PASSWORD")),
        "notify": lambda: setup.ntfy(v("NTFY_URL")),
        "extra": lambda: setup.flaresolverr(v("FLARESOLVERR_URL")),
    }
    if body.service.startswith("path:"):
        name = body.service[5:]
        if config.FIELDS.get(name, None) is None or config.FIELDS[name].kind != "path":
            raise HTTPException(400, "Unbekannter Ordner")
        r = setup.folder(v(name), write=name in ("LIBRARY_EN_ROOT", "LIBRARY_DE_ROOT", "LIBRARY_TRASH"))
        return {**r, "text": i18n.tr(r["text"])}
    if body.service not in tests:
        raise HTTPException(400, "Unbekannter Dienst")
    r = tests[body.service]()
    return {**r, "text": i18n.tr(r["text"])}


@app.get("/api/settings")
def settings():
    src = lambda name, ok, hint: {"name": name, "ok": bool(ok), "hint": hint}
    return {
        "users": auth.users(),
        "sources": [
            src("Audiobookshelf", config.abs_token(), "Bestand, Nutzer, Hörfortschritt, Cover"),
            src("Usenet (Prowlarr)", usenet.configured(), "Suche nach Hörbüchern und E-Books"),
            src("SABnzbd", usenet.sab_configured(), "Download aus dem Usenet"),
            src("AudioBookBay (qBittorrent)", torrent.configured(), "Torrent-Suche und Download"),
            src("EBookBay, Internet Archive", ebook.torrent_ready(), "E-Books als Torrent"),
            *[src(*d) for s in plugins.sources() for d in (s.diagnose() if hasattr(s, "diagnose") else [(s.name, s.configured(), "Erweiterung")])],
            *[src("Erweiterung", False, e) for e in plugins.errors],
            src("ntfy", notify.configured() and config.secret("NTFY_TOKEN"), "Meldungen aufs Handy" if notify.configured() and config.secret("NTFY_TOKEN") else "Adresse, Thema oder Schlüssel fehlt"),
            src("Ablage in die Bibliothek", config.IMPORT_ENABLED, "ABW darf Bücher in die Bibliothek legen"),
        ],
    }


# ---------- Suche und Downloads ----------

@app.get("/api/books/{book_id}/search")
def book_search(book_id: int, fresh: int = 0):
    """Ohne fresh=1 kommt die zuletzt gespeicherte Suche zurück, falls es eine gibt."""
    try:
        return search.guided(book_id, fresh=bool(fresh))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


class Grab(BaseModel):
    source: str = "usenet"
    guid: str
    title: str
    indexer: str | None = None
    size: int | None = None
    score: int | None = None
    reasons: list = []


@app.post("/api/books/{book_id}/grab")
def book_grab(book_id: int, body: Grab):
    try:
        return {"download_id": search.grab(book_id, body.model_dump())}
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Der Download liess sich nicht starten: {exc}")


class HideHit(BaseModel):
    guid: str


@app.post("/api/books/{book_id}/hide")
def book_hide(book_id: int, body: HideHit):
    """Treffer einen Monat ausblenden; gilt für Hörbuch und EPUB desselben Bandes."""
    return {"until": search.hide(book_id, body.guid)}


@app.post("/api/books/{book_id}/unhide")
def book_unhide(book_id: int, body: HideHit):
    search.unhide(book_id, body.guid)
    return {"ok": True}


@app.post("/api/books/{book_id}/dismiss")
def book_dismiss(book_id: int):
    return {"declined": search.dismiss(book_id)}


@app.get("/api/downloads")
def download_list():
    return search.downloads()


@app.get("/api/review")
def review():
    """Alles, was eine Entscheidung braucht, getrennt von dem, was nur läuft oder erledigt ist."""
    c = db.conn()
    covers = {r["id"]: cover_of(r) for r in c.execute(
        "SELECT id, abs_item_id, cover_url FROM book WHERE id IN (SELECT book_id FROM download UNION SELECT book_id FROM book_search)")}
    decide, active, history = [], [], []
    for d in search.downloads(300):
        d["cover"] = covers.get(d["book_id"])
        if d["state"] in ("verified", "review"):
            # Ein anderer Sprecher erklärt meist auch die übrigen Abweichungen, darum zuerst
            warn = sorted((x for x in d["checks"] if x["status"] != "ok"), key=lambda x: x["name"] != "Sprecher")
            narr_ok = next((x["text"] for x in d["checks"] if x["name"] == "Sprecher" and x["status"] == "ok"), "")
            problems = (d["plan"] or {}).get("problems") or []
            d["why"] = (problems[0] if problems else d["error"] or (f"{warn[0]['name']}: {warn[0]['text']}" + (f". {narr_ok}" if narr_ok and warn[0]["name"] == "Laufzeit" else "") if warn
                        else "Prüfung bestanden. Die automatische Ablage ist ausgeschaltet." if db.setting("auto_import") != "1"
                        else "Prüfung bestanden. Wird beim nächsten Prüflauf automatisch abgelegt."))
            d["kind"] = "blocked" if problems else "upgrade" if d.get("replaces") else "hint" if d["state"] == "review" else "ready"
            if d["kind"] == "blocked" and (d["plan"] or {}).get("overwritable") and not d.get("replaces"):
                d["kind"] = "exists"
                d["why"] = "Im Bestand liegt dort schon ein Ordner. «Überschreiben» legt ihn in den Papierkorb und den neuen Download an seine Stelle."
            if d["kind"] == "upgrade":
                d["why"] = d["error"] or next((x["text"] for x in d["checks"] if x["name"] == "Vergleich mit Bestand"), d["why"])
            if importer.busy((d["plan"] or {}).get("target")):
                d["kind"] = "filing"
                d["why"] = "Wird gerade abgelegt. Die Automatik kopiert den Band in die Bibliothek, das dauert bei grossen Büchern einige Minuten."
            decide.append(d)
        elif d["state"] in ("queued", "downloading", "arrived", "imported"):
            active.append(d)
        else:
            history.append(d)
    order = {"filing": 0, "ready": 0, "upgrade": 1, "hint": 1, "exists": 1, "blocked": 2}
    decide.sort(key=lambda d: order[d["kind"]])
    unsure = search.auto_overview()["unsure"]
    for u in unsure:
        u["cover"] = covers.get(u["book_id"])
    bundles = libsplit.overview()
    for b in bundles:
        b["cover"] = cover_of(b)
    # Von Benutzern hinzugefügte Bücher farbig hervorheben
    who = library.requested_by([d["book_id"] for d in decide + active + history] + [u["book_id"] for u in unsure])
    for x in decide + active + history + unsure:
        x["users"] = who.get(x["book_id"], [])
    return {"decide": decide, "unsure": unsure, "active": active, "history": history[:100], "bundles": bundles,
            "counts": {"decide": len(decide), "unsure": len(unsure), "active": len(active), "history": len(history), "bundles": len(bundles)}}


@app.post("/api/libsplit/{book_id}/retry")
def libsplit_retry(book_id: int):
    """Unsicheren oder übersprungenen Sammelband beim nächsten Lauf neu planen."""
    c = db.conn()
    if not c.execute("DELETE FROM libsplit_job WHERE book_id=? AND state IN ('unsafe','skipped')", (book_id,)).rowcount:
        raise HTTPException(409, "Dieser Sammelband wird gerade aufgeteilt oder ist schon fertig")
    tasks.request("libsplit")
    return {"ok": True}


@app.get("/api/review/count")
def review_count():
    c = db.conn()
    decide = [r[0] for r in c.execute("SELECT book_id FROM download WHERE state IN ('verified','review')")]
    unsure = [r["book_id"] for r in search.unsure_books("b.id book_id")]
    who = library.requested_by(decide + unsure)
    # Abzeichen = «Entscheiden» + «Treffer wählen»; «users» färbt es, wenn etwas davon Benutzern gehört
    return {"open": len(decide) + len(unsure), "decide": len(decide), "unsure": len(unsure), "users": sum(1 for b in decide + unsure if b in who)}


def _dl_action(fn, download_id: int):
    try:
        return {"ok": True, "result": fn(download_id)}
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/downloads/{download_id}/import")
def download_import(download_id: int):
    return _dl_action(search.import_download, download_id)


@app.post("/api/downloads/{download_id}/overwrite")
def download_overwrite(download_id: int):
    return _dl_action(search.overwrite_download, download_id)


@app.post("/api/downloads/{download_id}/replace")
def download_replace(download_id: int):
    return _dl_action(search.replace_download, download_id)


@app.post("/api/downloads/{download_id}/reject")
def download_reject(download_id: int):
    return _dl_action(search.reject_download, download_id)


@app.post("/api/downloads/{download_id}/recheck")
def download_recheck(download_id: int):
    return _dl_action(search.recheck_download, download_id)


@app.post("/api/books/{book_id}/autosearch")
def book_autosearch_start(book_id: int):
    """Automatische Suche für ein einzelnes Buch: lädt nur einen sicheren Treffer."""
    try:
        search.start_book(book_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@app.get("/api/books/{book_id}/autosearch")
def book_autosearch_state(book_id: int):
    return search.book_auto_state(book_id)


@app.post("/api/series/{sid}/autosearch")
def autosearch_start(sid: int):
    search.start_auto(sid)
    return {"ok": True}


@app.get("/api/series/{sid}/autosearch")
def autosearch_state(sid: int):
    return search.auto_state(sid) or {"running": False, "total": 0, "done": 0, "items": []}


class SeriesAuto(BaseModel):
    auto: bool


@app.post("/api/series/{sid}/auto")
def series_auto_set(sid: int, body: SeriesAuto):
    db.conn().execute("UPDATE series SET auto=? WHERE id=?", (int(body.auto), sid))
    return {"ok": True}


@app.get("/api/automation")
def automation():
    return search.auto_overview()


class Automation(BaseModel):
    auto_search: bool | None = None
    auto_import: bool | None = None
    auto_cleanup: bool | None = None
    auto_convert: bool | None = None
    auto_chapters: bool | None = None
    notify: bool | None = None
    auto_ebook: bool | None = None
    auto_ebook_upcoming: bool | None = None
    auto_ebook_listening: bool | None = None
    notify_ebook: bool | None = None
    auto_author_series: bool | None = None
    auto_integrity: bool | None = None


@app.post("/api/automation")
def automation_set(body: Automation):
    if body.auto_search is not None:
        db.set_setting("auto_search", "1" if body.auto_search else "0")
        if body.auto_search:
            tasks.request("auto")
    if body.auto_import is not None:
        db.set_setting("auto_import", "1" if body.auto_import else "0")
    for key in ("auto_cleanup", "auto_convert", "auto_chapters", "notify", "auto_ebook", "auto_ebook_upcoming", "auto_ebook_listening", "notify_ebook", "auto_author_series", "auto_integrity"):
        if getattr(body, key) is not None:
            db.set_setting(key, "1" if getattr(body, key) else "0")
    if body.auto_author_series:
        tasks.request("author_series")
    return search.auto_overview()


@app.post("/api/books/{book_id}/integrity")
def integrity_check(book_id: int):
    """Ein Buch im Bestand sofort auf Lücken prüfen (liest alle Dateien, kann bei grossen Büchern eine Minute dauern)."""
    try:
        res = integrity.check_book(book_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    if res["state"] == "damaged":
        tasks.request("auto")
    return res


# ---------- E-Books ----------

@app.post("/api/books/{book_id}/ebook")
def ebook_request(book_id: int):
    """EPUB zu einem vorhandenen Hörbuch auf Usenet suchen und den besten sicheren Treffer laden."""
    try:
        return ebook.request_quick(book_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.get("/api/books/{book_id}/ebook/search")
def ebook_search(book_id: int):
    """Alle EPUB-Treffer aller Quellen zum Auswählen von Hand."""
    try:
        return ebook.guided(book_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/books/{book_id}/ebook/grab")
def ebook_grab(book_id: int, body: Grab):
    try:
        return ebook.grab(book_id, body.model_dump())
    except (ValueError, LookupError) as exc:
        raise HTTPException(409, str(exc))
    except Exception as exc:
        raise HTTPException(502, f"Der Download liess sich nicht starten: {exc}")


@app.post("/api/series/{sid}/ebooks")
def ebook_request_series(sid: int):
    return {"total": ebook.request_series(sid)}


# ---------- Umwandlung, Kapitel, Meldungen ----------

@app.get("/api/convert")
def convert_overview():
    return {**convert.overview(), "chapters": chapters.overview()}


@app.get("/api/books/{book_id}/convert")
def convert_plan(book_id: int):
    try:
        book, _ = search.book_ctx(book_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return convert.plan(book)


_convert_lock = __import__("threading").Lock()


@app.post("/api/books/{book_id}/convert")
def convert_start(book_id: int):
    """Ein einzelnes Buch sofort umwandeln (läuft im Hintergrund, eines nach dem anderen)."""
    def work():
        with _convert_lock:
            try:
                convert.run_job(book_id)
            except Exception:
                pass  # steht mit Grund in der Auftragsliste
    __import__("threading").Thread(target=work, daemon=True).start()
    return {"ok": True}


@app.post("/api/convert/purge")
def convert_purge():
    try:
        return convert.purge(14)
    except OSError as exc:
        raise HTTPException(409, f"Löschen nicht möglich: {exc}")


@app.post("/api/notify/test")
def notify_test():
    if not notify.configured():
        raise HTTPException(409, "ntfy ist nicht eingerichtet (Adresse und Thema fehlen)")
    if not notify.send("AudioBook Wizarrd", "Testmeldung: Die Benachrichtigungen funktionieren.", "/", "mage", force=True):
        raise HTTPException(502, "ntfy hat die Meldung nicht angenommen. Stimmen Thema und Token?")
    return {"ok": True}


# ---------- Aufräumen ----------

@app.get("/api/cleanup")
def cleanup_overview():
    return {"candidates": cleanup.candidates(), "trash": cleanup.trash(), "keep_days": 14}


class CleanupRun(BaseModel):
    markers: list[str] | None = None


@app.post("/api/cleanup/run")
def cleanup_run(body: CleanupRun):
    try:
        return cleanup.run(body.markers)
    except OSError as exc:
        raise HTTPException(409, f"Verschieben nicht möglich: {exc}")


@app.post("/api/cleanup/purge")
def cleanup_purge():
    try:
        return cleanup.purge(14)
    except OSError as exc:
        raise HTTPException(409, f"Löschen nicht möglich: {exc}")


# ---------- Autoren ----------

@app.get("/api/authors")
def authors():
    c = db.conn()
    data = load()
    out: dict[str, dict] = {}
    weight = series_weights(c)
    for b in c.execute("SELECT b.author, b.lang, b.edition_id, b.runtime_min, e.series_id FROM book b LEFT JOIN edition e ON e.id=b.edition_id"
                       " WHERE b.owned=1 AND b.author IS NOT NULL"):
        name = abs_mod.primary_author(b["author"])
        a = out.setdefault(db.norm(name), {"key": db.norm(name), "name": name, "en": 0, "de": 0, "minutes": 0, "series": []})
        a[b["lang"]] += 1
        a["minutes"] += (b["runtime_min"] or 0) / weight.get(b["series_id"], 1)
    # Autoren hinzugefügter Bücher und Serien ohne Bestand gehören auch dazu (Fehler 20.11.: Biges Jonasson fehlte)
    for r in c.execute("SELECT author FROM book WHERE wanted IS NOT NULL AND owned=0 AND author IS NOT NULL"):
        name = abs_mod.primary_author(r["author"])
        out.setdefault(db.norm(name), {"key": db.norm(name), "name": name, "en": 0, "de": 0, "minutes": 0, "series": []})
    for s in data.values():
        if s["author"] and db.norm(s["author"]) not in out:
            out[db.norm(s["author"])] = {"key": db.norm(s["author"]), "name": s["author"], "en": 0, "de": 0, "minutes": 0, "series": []}
        a = out.get(db.norm(s["author"]))
        if a:
            a["series"].append({"id": s["id"], "name": s["name"], "cover": s["cover"], "owned": s["owned"], "primary": s["primary"]})
    more = Counter(r["author_norm"] for r in c.execute(
        "SELECT author_norm FROM author_catalog WHERE series_asin NOT IN (SELECT audible_asin FROM edition WHERE audible_asin IS NOT NULL)"))
    res = []
    for a in out.values():
        if not a["key"]:
            continue
        a["series"].sort(key=lambda x: -x["owned"])
        a["series_count"] = len(a["series"])
        a["more_series"] = more.get(a["key"], 0)
        a["hours"] = round(a.pop("minutes") / 60)
        a["series"] = a["series"][:3]
        res.append(a)
    return sorted(res, key=lambda a: -(a["en"] + a["de"]))


@app.get("/api/authors/{key}")
def author_detail(key: str, request: Request):
    c = db.conn()
    data = load(me(request)["id"])
    prog = progress_of(me(request)["id"])
    mine = [public(s) for s in data.values() if db.norm(s["author"]) == key]
    if not mine and not c.execute("SELECT 1 FROM author_catalog WHERE author_norm=?", (key,)).fetchone():
        # Autor nur eines hinzugefügten Einzelbuchs
        if not any(db.norm(abs_mod.primary_author(r["author"])) == key for r in c.execute("SELECT author FROM book WHERE edition_id IS NULL AND author IS NOT NULL")):
            raise HTTPException(404, "Autor nicht gefunden")
    linked = {r["audible_asin"]: r["series_id"] for r in c.execute("SELECT audible_asin, series_id FROM edition WHERE audible_asin IS NOT NULL")}
    watch = {r["asin"] for r in c.execute("SELECT asin FROM watchlist")}
    catalog = [{**dict(r), "series_id": linked.get(r["series_asin"]), "watched": r["series_asin"] in watch}
               for r in c.execute("SELECT * FROM author_catalog WHERE author_norm=? ORDER BY book_count DESC", (key,))]
    singles = [{"id": b["id"], "title": b["title"], "lang": b["lang"], "cover": cover_of(b), "runtime_min": b["runtime_min"],
                "finished": prog.get(b["id"], (None, False))[1], "progress": prog.get(b["id"], (None, False))[0], "owned": bool(b["owned"])}
               for b in c.execute("SELECT * FROM book WHERE (owned=1 OR wanted IS NOT NULL) AND edition_id IS NULL AND author IS NOT NULL ORDER BY title")
               if db.norm(abs_mod.primary_author(b["author"])) == key]
    name = mine[0]["author"] if mine else next((abs_mod.primary_author(r["author"]) for r in c.execute("SELECT author FROM book WHERE author IS NOT NULL")
                                                if db.norm(abs_mod.primary_author(r["author"])) == key), key)
    fetched = c.execute("SELECT MIN(fetched_at) f FROM author_fetch WHERE author_norm=?", (key,)).fetchone()["f"]
    return {"key": key, "name": name, "series": sorted(mine, key=lambda s: -s["owned"]), "catalog": catalog,
            "singles": singles, "catalog_fetched": fetched}


# ---------- Entdecken, Merkliste, Kalender ----------

def _annotate(items: list[dict], user_id: int | None = None) -> list[dict]:
    """Bestand, Serie, Merkliste und ob der Benutzer den Titel schon fertig gehört hat."""
    c = db.conn()
    asins, titles = foryou.finished(user_id)
    owned = {r["asin"] for r in c.execute("SELECT asin FROM book WHERE owned=1 AND asin IS NOT NULL")}
    linked = {r["audible_asin"]: r["series_id"] for r in c.execute("SELECT audible_asin, series_id FROM edition WHERE audible_asin IS NOT NULL")}
    watch = {r["asin"] for r in c.execute("SELECT asin FROM watchlist")}
    return [{**i, "in_library": i["asin"] in owned, "series_id": linked.get(i.get("series_asin")),
             "watched": i["asin"] in watch or bool(i.get("series_asin") and i["series_asin"] in watch),
             "heard": i["asin"] in asins or (foryou._head(i.get("title") or ""), db.norm(abs_mod.primary_author(i.get("author") or ""))) in titles}
            for i in items]


@app.get("/api/discover/{kind}/{lang}")
def discover(kind: str, lang: str, request: Request):
    """Toplisten von Audible; jeder Titel trägt, wie gut er zu den Genres des Benutzers passt."""
    if kind not in audible.LISTS or lang not in ("en", "de"):
        raise HTTPException(404, "Liste nicht vorhanden")
    try:
        items = [dict(i) for i in audible.discover(kind, lang)]
        foryou.fill_series_info(items)  # Bandzahl und Gesamtlaufzeit der Serie
        return foryou.mark_fit(_annotate(items, me(request)["id"]), me(request)["id"])
    except Exception as exc:
        raise HTTPException(502, f"Audible ist gerade nicht erreichbar: {exc}")


# ---------- Bewertungen ----------

class Rate(BaseModel):
    stars: int | None = None  # None = «nicht bewerten»


@app.get("/api/ratings/count")
def ratings_count(request: Request):
    return {"open": ratings.open_count(me(request)["id"])}


@app.get("/api/ratings/open")
def ratings_open(request: Request):
    return ratings.open_list(me(request)["id"])


@app.get("/api/ratings/mine")
def ratings_mine(request: Request):
    return ratings.mine(me(request)["id"])


@app.get("/api/ratings/tags")
def ratings_tags(request: Request):
    try:
        return ratings.top_by_tag(me(request)["id"])
    except Exception as exc:
        raise HTTPException(502, f"Audible ist gerade nicht erreichbar: {exc}")


@app.get("/api/ratings/stats")
def ratings_stats(request: Request, scope: str = "me"):
    return ratings.stats(me(request)["id"], everyone=scope == "all")


@app.post("/api/ratings/book/{book_id}")
def ratings_set(book_id: int, body: Rate, request: Request):
    try:
        ratings.set_rating(me(request)["id"], book_id, body.stars)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


class Mark(BaseModel):
    finished: bool


@app.get("/api/listened")
def listened_status(request: Request):
    return listened.status(me(request)["id"])


@app.post("/api/books/{book_id}/listened")
def listened_mark(book_id: int, body: Mark, request: Request):
    """Als gehört oder nicht gehört markieren, wird nach Audiobookshelf übertragen."""
    try:
        return listened.mark(me(request)["id"], book_id, body.finished)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except listened.NeedLogin as exc:
        raise HTTPException(409, str(exc))
    except (ConnectionError, httpx.HTTPError) as exc:
        raise HTTPException(503, f"Audiobookshelf hat den Hörstand nicht übernommen: {exc}")


@app.delete("/api/ratings/book/{book_id}")
def ratings_clear(book_id: int, request: Request):
    ratings.clear(me(request)["id"], book_id)
    return {"ok": True}


@app.post("/api/ratings/edition/{edition_id}")
def ratings_series(edition_id: int, body: Rate, request: Request):
    try:
        return {"rated": ratings.set_series(me(request)["id"], edition_id, body.stars)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class ToRead(BaseModel):
    kind: str  # edition (ganze Serie) oder book
    id: int


class ToReadOrder(BaseModel):
    keys: list[str]


@app.get("/api/toread")
def toread_overview(request: Request):
    return toread.overview(me(request)["id"])


@app.get("/api/toread/keys")
def toread_keys(request: Request):
    return toread.keys(me(request)["id"])


@app.get("/api/toread/asins")
def toread_asins(request: Request):
    return toread.asins(me(request)["id"])


class Recommend(BaseModel):
    book_id: int
    to: list[int]


class Decide(BaseModel):
    action: str  # top, bottom oder no


@app.get("/api/toread/heard")
def toread_heard(request: Request):
    return toread.heard(me(request)["id"])


@app.get("/api/toread/users")
def toread_users(request: Request):
    return toread.users(me(request)["id"])


class RecRules(BaseModel):
    mode: str
    allow: list[list[int]] | None = None


@app.get("/api/recommend/rules")
def recommend_rules():
    return toread.rules()


@app.put("/api/recommend/rules")
def recommend_rules_save(body: RecRules):
    try:
        toread.set_rules(body.mode, body.allow)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return toread.rules()


@app.post("/api/toread/recommend")
def toread_recommend(body: Recommend, request: Request):
    try:
        return {"sent": toread.recommend(me(request)["id"], body.book_id, body.to)}
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/toread/rec/{key}")
def toread_rec_decide(key: str, body: Decide, request: Request):
    try:
        toread.decide_rec(me(request)["id"], key, body.action)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True}


@app.post("/api/toread/suggest/{edition_id}")
def toread_suggest_decide(edition_id: int, body: Decide, request: Request):
    try:
        toread.decide_suggestion(me(request)["id"], edition_id, body.action)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True}


@app.post("/api/toread")
def toread_add(body: ToRead, request: Request):
    try:
        return {"added": toread.add(me(request)["id"], body.kind, body.id)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@app.delete("/api/toread/{kind}/{ref_id}")
def toread_remove(kind: str, ref_id: int, request: Request):
    toread.remove(me(request)["id"], kind, ref_id)
    return {"ok": True}


@app.put("/api/toread/order")
def toread_order(body: ToReadOrder, request: Request):
    toread.reorder(me(request)["id"], body.keys)
    return {"ok": True}


@app.get("/api/discover/foryou")
def discover_foryou(request: Request):
    """Was Audible zu den zuletzt gehörten Büchern des angemeldeten Benutzers vorschlägt."""
    try:
        out = foryou.suggestions(me(request)["id"])
    except Exception as exc:
        raise HTTPException(502, f"Audible ist gerade nicht erreichbar: {exc}")
    return {**out, "items": _annotate(out["items"], me(request)["id"])}


@app.get("/api/discover/search")
def discover_search(request: Request, q: str = "", lang: str = "all"):
    """Freie Suche im Audible-Katalog; «all» fragt Audible.com und Audible.de."""
    langs = tuple(config.LANGS) if lang == "all" else (lang,)
    if any(l not in ("en", "de") for l in langs):
        raise HTTPException(404, "Sprache nicht vorhanden")
    try:
        found = [audible.search(q, l) for l in langs]
    except Exception as exc:
        raise HTTPException(502, f"Audible ist gerade nicht erreichbar: {exc}")
    # Bei beiden Sprachen abwechselnd mischen, damit die besten Treffer beider Marktplätze oben stehen
    out, seen = [], set()
    for i in range(max((len(f) for f in found), default=0)):
        for f in found:
            if i < len(f) and (f[i]["asin"], f[i]["lang"]) not in seen:
                seen.add((f[i]["asin"], f[i]["lang"]))
                out.append(dict(f[i]))
    foryou.fill_series_info(out)
    return _annotate(out, me(request)["id"])


@app.get("/api/discover/authors")
def discover_authors(request: Request):
    """Serien deiner meistgehörten Autoren, die noch nicht in der Bibliothek sind."""
    c = db.conn()
    weight = Counter()
    names = {}
    prog = progress_of(me(request)["id"])
    for b in c.execute("SELECT id, author FROM book WHERE owned=1 AND author IS NOT NULL"):
        n = abs_mod.primary_author(b["author"])
        names[db.norm(n)] = n
        weight[db.norm(n)] += 1 + 2 * prog.get(b["id"], (None, False))[1]
    watch = {r["asin"] for r in c.execute("SELECT asin FROM watchlist")}
    rows = c.execute(
        "SELECT * FROM author_catalog WHERE book_count >= 2 AND series_asin NOT IN (SELECT audible_asin FROM edition WHERE audible_asin IS NOT NULL)").fetchall()
    out = [{**dict(r), "author": names.get(r["author_norm"], ""), "score": weight.get(r["author_norm"], 0),
            "watched": r["series_asin"] in watch} for r in rows if r["author_norm"] in names]
    out = sorted(out, key=lambda r: (-r["score"], -r["book_count"]))[:120]
    foryou.fill_series_info(out)  # Gesamtlaufzeit der Serie
    return out


class Watch(BaseModel):
    asin: str
    lang: str | None = None
    title: str | None = None
    author: str | None = None
    series: str | None = None
    cover_url: str | None = None
    kind: str = "book"


@app.get("/api/watchlist")
def watchlist(request: Request):
    """Alles Hinzugefügte mit seinem Stand. «mine» zeigt, ob der Eintrag vom angemeldeten Benutzer stammt."""
    u = me(request)
    return [{**w, "mine": (w["user_id"] or 1) == u["id"], "removable": u["role"] == "admin" or (w["user_id"] or 1) == u["id"]}
            for w in library.listing()]


@app.post("/api/watchlist")
def watch_add(w: Watch, request: Request):
    """Hinzufügen: als fehlend in die Bibliothek aufnehmen und mit Vorrang suchen."""
    try:
        return library.add(w.model_dump(), me(request)["id"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/watchlist/{asin}")
def watch_del(asin: str, request: Request):
    try:
        library.remove(asin, me(request))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return {"ok": True}


class PickDecide(Watch):
    series_asin: str | None = None
    action: str


@app.get("/api/toread/picks")
def toread_picks(request: Request):
    """Drei Serien aus «Für dich» für unter die Liste (verschiedene Genres, eine «Mal was anderes»)."""
    try:
        return toread.picks(me(request)["id"])
    except Exception as exc:
        raise HTTPException(502, f"Audible ist gerade nicht erreichbar: {exc}")


@app.post("/api/toread/picks/decide")
def toread_picks_decide(body: PickDecide, request: Request):
    try:
        d = body.model_dump()
        return toread.decide_pick(me(request)["id"], d, d.pop("action"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/toread/discover")
def toread_discover(body: Watch, request: Request):
    """«Zu To Read» unter Entdecken: Serie oder Buch einreihen, wenn nötig vorher in die Bibliothek aufnehmen."""
    try:
        return toread.from_discover(me(request)["id"], body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/calendar")
def calendar():
    out = []
    for s in load().values():
        for e in s["editions"]:
            for b, st in zip(e["_books"], e["_states"]):
                if st == "upcoming":
                    out.append({"series_id": s["id"], "series": e["name"], "lang": e["lang"], "monitored": e["monitored"],
                                "title": titles.display(b["title"], e["name"]), "sequence": b["sequence"],
                                "release_date": None if open_date(b["release_date"]) else b["release_date"],
                                "cover": cover_of(b), "author": abs_mod.primary_author(b["author"]), "asin": b["asin"]})
    return sorted(out, key=lambda x: x["release_date"] or "9999")


# ---------- Aufgaben, Statistik ----------

@app.get("/api/tasks")
def task_list():
    return tasks.overview()


@app.post("/api/tasks/{key}/run")
def task_run(key: str):
    if key not in tasks.TASKS:
        raise HTTPException(404, "Aufgabe nicht vorhanden")
    tasks.request(key)
    return {"ok": True}


@app.get("/api/stats")
def stats(request: Request):
    c = db.conn()
    data = load()
    tot = {}
    for lang in ("en", "de"):
        # «Gehört» zählt für den angemeldeten Benutzer
        r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(b.runtime_min),0) m, COALESCE(SUM(b.size),0) s, COALESCE(SUM(p.finished),0) f, COALESCE(SUM(b.epub),0) eb,"
                      " COALESCE(SUM(CASE WHEN p.finished=1 THEN b.runtime_min END),0) fm FROM book b"
                      " LEFT JOIN user_progress p ON p.book_id=b.id AND p.user_id=? WHERE b.owned=1 AND b.lang=?", (me(request)["id"], lang)).fetchone()
        tot[lang] = {"books": r["n"], "hours": round(r["m"] / 60), "gb": round(r["s"] / 1e9), "finished": r["f"], "epub": r["eb"],
                     "finished_hours": round(r["fm"] / 60),
                     "series": sum(1 for s in data.values() for e in s["editions"] if e["lang"] == lang and e["owned"]),
                     "missing": sum(e["missing"] for s in data.values() for e in s["editions"] if e["lang"] == lang and e["monitored"]),
                     "upcoming": sum(e["upcoming"] for s in data.values() for e in s["editions"] if e["lang"] == lang and e["monitored"])}
    status = Counter(s["primary"] for s in data.values())
    added = [dict(r) for r in c.execute(
        "SELECT substr(added_at,1,7) month, lang, COUNT(*) n FROM book WHERE owned=1 AND added_at IS NOT NULL GROUP BY 1,2 ORDER BY 1")]
    # Autoren und Serien: eine Serie zählt mit der mittleren Laufzeit ihrer Fassungen, nicht mit
    # der Summe. Jeder Band einer Serie wiegt darum 1 / Anzahl Fassungen mit Laufzeit im Bestand; Einzelbücher ganz.
    weight = series_weights(c)
    rows = c.execute("SELECT b.author, b.narrator, b.runtime_min, e.series_id FROM book b"
                     " LEFT JOIN edition e ON e.id=b.edition_id WHERE b.owned=1").fetchall()
    authors = Counter()
    narrators = Counter()
    spelled: dict[str, Counter] = {}  # «Pirateaba» und «pirateaba» sind dieselbe Person: nach db.norm zusammenfassen

    def person(name: str) -> str:
        n = abs_mod.primary_author(name)
        spelled.setdefault(db.norm(n), Counter())[n] += 1
        return db.norm(n)
    for b in rows:
        mins = b["runtime_min"] or 0
        if b["author"]:
            authors[person(b["author"])] += mins / weight.get(b["series_id"], 1) / 60
        if b["narrator"]:
            narrators[person(b["narrator"])] += mins / 60  # Sprecher je Fassung verschieden: Summe
    files = c.execute("SELECT SUM(num_files=1) single, SUM(num_files>1) multi FROM book WHERE owned=1").fetchone()
    longest = sorted(data.values(), key=lambda s: -s["hours"])[:10]
    return {
        "totals": tot, "series_total": len(data), "status": [{"key": k, "n": status[k]} for k in PRIORITY if status[k]],
        "singles": c.execute("SELECT COUNT(*) FROM book WHERE owned=1 AND edition_id IS NULL").fetchone()[0],
        "added": added, "files": {"single": files["single"] or 0, "multi": files["multi"] or 0},
        "top_authors": [{"name": spelled[k].most_common(1)[0][0], "hours": round(v)} for k, v in authors.most_common(10)],
        "top_narrators": [{"name": spelled[k].most_common(1)[0][0], "hours": round(v)} for k, v in narrators.most_common(10)],
        "longest_series": [{"id": s["id"], "name": s["name"], "hours": s["hours"], "editions": s["hours_editions"]} for s in longest],
        "without_audible": sum(1 for s in data.values() if "unknown" in s["flags"]),
        "activity": activity.overview(),
        "wished": wished_stats(c, data) if me(request)["role"] == "admin" else None,
    }


def _wished_books(c, owned_only: bool):
    """Bücher mit Wünschenden (wie der Tag «Gewünscht von»): [(Buch, Namen)] mit Serie und Einzelbuch-Schlüssel."""
    rows = {r["id"]: r for r in c.execute(
        "SELECT b.id, b.runtime_min, b.author, e.series_id FROM book b LEFT JOIN edition e ON e.id=b.edition_id"
        " WHERE b.owned=1" + ("" if owned_only else " OR b.wanted IS NOT NULL"))}
    return [(rows[bid], names) for bid, names in library.wishers(rows).items()]


def _single_key(author: str | None) -> str:
    return db.norm(abs_mod.primary_author(author or "") or "Unbekannt") or "unbekannt"  # wie single_groups


def wished_stats(c, data: dict | None = None) -> list[dict]:
    """Wer welchen Teil des Bestands gewünscht hat, wie der Tag «Gewünscht von» in Audiobookshelf (ganze Serie,
    nur Benutzer mit Audiobookshelf-Konto). Bücher und Stunden je Fassung, Serien als Karten, dazu die Liste der
    Serien und Einzelbuch-Karten zum Aufklappen."""
    data = data if data is not None else load()
    authors = {}
    per: dict[str, dict] = {}
    for b, names in _wished_books(c, owned_only=True):
        for n in names:
            x = per.setdefault(n, {"books": 0, "minutes": 0, "series": Counter(), "singles": Counter()})
            x["books"] += 1
            x["minutes"] += b["runtime_min"] or 0
            if b["series_id"]:
                x["series"][b["series_id"]] += 1
            else:
                key = _single_key(b["author"])
                authors.setdefault(key, abs_mod.primary_author(b["author"] or "") or "Unbekannt")
                x["singles"][key] += 1
    out = []
    for n, x in per.items():
        items = [{"kind": "series", "id": sid, "key": str(sid), "name": data[sid]["name"] if sid in data else f"Serie {sid}", "books": k}
                 for sid, k in x["series"].items()]
        items += [{"kind": "single", "id": None, "key": key, "name": f"{authors[key]} – Einzelbücher", "books": k} for key, k in x["singles"].items()]
        out.append({"name": n, "books": x["books"], "hours": round(x["minutes"] / 60), "series": len(x["series"]),
                    "items": sorted(items, key=lambda i: (-i["books"], db.norm(i["name"])))})
    return sorted(out, key=lambda x: (-x["books"], x["name"]))


@app.get("/api/library/wished")
def library_wished():
    """Wer welche Serie und Einzelbuch-Karte gewünscht hat, für den Filter der Serienansicht. Nur Administratoren
    (steht nicht in USER_READ)."""
    series: dict[int, set] = defaultdict(set)
    singles: dict[str, set] = defaultdict(set)
    for b, names in _wished_books(db.conn(), owned_only=False):
        (series[b["series_id"]] if b["series_id"] else singles[_single_key(b["author"])]).update(names)
    return {"series": {k: sorted(v) for k, v in series.items()}, "singles": {k: sorted(v) for k, v in singles.items()}}


@app.get("/api/listening")
def listening_overview(request: Request, period: str = "week", offset: int = 0, user: str = ""):
    """Hörverlauf: Administratoren sehen alle Konten, Benutzer nur ihr eigenes verknüpftes Audiobookshelf-Konto."""
    if period not in ("week", "month", "year"):
        raise HTTPException(400, "Zeitraum week, month oder year")
    u = me(request)
    if u["role"] == "admin":
        return listening.overview(period, offset, user)
    row = db.conn().execute("SELECT abs_user_id FROM user WHERE id=?", (u["id"],)).fetchone()
    return listening.overview(period, offset, only=(row["abs_user_id"] if row else None) or "")


# ---------- Cover und Oberfläche ----------

@lru_cache(maxsize=512)
def _cover(item_id: str):
    return abs_mod.cover(item_id)


@app.get("/api/cover/{item_id}")
def cover(item_id: str):
    try:
        data, ctype = _cover(item_id)
    except Exception:
        raise HTTPException(404, "Kein Cover")
    return Response(data, media_type=ctype, headers={"Cache-Control": "public, max-age=604800"})


if (config.STATIC_DIR / "assets").exists():
    app.mount("/assets", StaticFiles(directory=config.STATIC_DIR / "assets"), name="assets")


@app.get("/{path:path}")
def spa(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    f = config.STATIC_DIR / path
    if path and f.is_file() and config.STATIC_DIR in f.resolve().parents:
        if path == "sw.js":  # Service Worker immer frisch, damit neue Fassungen sofort greifen
            return FileResponse(f, media_type="text/javascript", headers={"Cache-Control": "no-cache"})
        if path.endswith(".webmanifest"):
            return FileResponse(f, media_type="application/manifest+json")
        return FileResponse(f)
    index = config.STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(503, "Oberfläche ist nicht gebaut")
    return FileResponse(index)
