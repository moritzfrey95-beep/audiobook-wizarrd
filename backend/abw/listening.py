"""Hörverlauf aller Audiobookshelf-Konten: wer wann was gehört hat, als Wochen-, Monats- und Jahresrückblick.
Quelle sind die Hörsitzungen von Audiobookshelf (nur lesend). Nur für Administratoren."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import abs as abs_mod
from . import config, db

TZ = ZoneInfo("Europe/Zurich")
PAGE = 500
STALE_DAYS = 30  # angefangen, aber so lange nicht mehr weitergehört: «liegengeblieben»
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
WEEKDAYS = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def _ms(ms) -> str | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(TZ)


def plausible(seconds: int, wall: float, duration: float | None, moved: float | None) -> int:
    """Audiobookshelf zählt bei manchen Sitzungen viel zu viel Hörzeit (Beispiel: 1203 h in einer Sitzung, die 42 h dauerte,
    bei einem Buch von 18 h). Höchstens die verstrichene Zeit, die Buchlänge und der Sprung der Position plus eine Stunde zählen."""
    cap = wall + 300
    if duration:
        cap = min(cap, duration)
    if moved is not None:
        cap = min(cap, abs(moved) + 3600)
    return max(0, min(seconds, int(cap)))


def _row(s: dict, langs: dict) -> tuple | None:
    if s.get("mediaType") != "book" or not s.get("startedAt") or not s.get("userId"):  # geteilte Links haben kein Konto
        return None
    dev = s.get("deviceInfo") or {}
    device = dev.get("deviceName") or " ".join(x for x in (dev.get("manufacturer"), dev.get("model")) if x) or dev.get("browserName") or ""
    client = dev.get("clientName") or s.get("mediaPlayer") or ""
    started = _ms(s["startedAt"])
    raw = int(s.get("timeListening") or 0)
    wall = ((s.get("updatedAt") or s["startedAt"]) - s["startedAt"]) / 1000
    moved = s["currentTime"] - s["startTime"] if s.get("currentTime") is not None and s.get("startTime") is not None else None
    return (s["id"], s.get("userId"), (s.get("user") or {}).get("username"), s.get("libraryItemId"), langs.get(s.get("libraryId")),
            s.get("displayTitle") or "", s.get("displayAuthor") or "", s.get("date") or _local(started).date().isoformat(),
            started, _ms(s.get("updatedAt") or s["startedAt"]), plausible(raw, wall, s.get("duration"), moved), raw,
            s.get("startTime"), s.get("currentTime"), s.get("duration"), " · ".join(x for x in (client, device) if x))


def sync(progress=lambda msg: None) -> str:
    """Neue und geänderte Hörsitzungen holen (neueste zuerst, bis bekannte erreicht sind), dazu je Konto Fortschritt und Abschlussdatum."""
    c = db.conn()
    last = c.execute("SELECT MAX(updated) FROM listen_session").fetchone()[0]
    stop = (datetime.fromisoformat(last) - timedelta(days=2)).timestamp() * 1000 if last else None
    langs = {v: k for k, v in config.ABS_LIBRARIES.items() if v}
    rows, prog = [], {}
    with abs_mod._client() as http:
        page = 0
        while True:
            progress(f"Hörsitzungen, Seite {page + 1}")
            data = http.get("/api/sessions", params={"itemsPerPage": PAGE, "page": page, "sort": "updatedAt", "desc": 1}).raise_for_status().json()
            got = data.get("sessions") or []
            rows += [r for r in (_row(s, langs) for s in got) if r]
            page += 1
            if not got or page >= (data.get("numPages") or 0) or (stop and min(s.get("updatedAt") or 0 for s in got) < stop):
                break
        for u in http.get("/api/users").raise_for_status().json().get("users", []):
            progress(f"Fortschritt von {u['username']}")
            mp = http.get(f"/api/users/{u['id']}").raise_for_status().json().get("mediaProgress") or []
            prog[u["id"]] = (u["username"], [(u["id"], p["libraryItemId"], p.get("progress"), 1 if p.get("isFinished") else 0,
                                              _ms(p.get("finishedAt")), _ms(p.get("lastUpdate")))
                                             for p in mp if p.get("libraryItemId") and not p.get("episodeId")])
    c.execute("BEGIN IMMEDIATE")
    c.executemany("INSERT OR REPLACE INTO listen_session(id, abs_user_id, username, item_id, lang, title, author, day, started, updated,"
                  " seconds, raw_seconds, pos_start, pos_end, duration, device) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    for uid, (name, entries) in prog.items():
        c.execute("UPDATE listen_session SET username=? WHERE abs_user_id=?", (name, uid))
        c.execute("DELETE FROM listen_progress WHERE abs_user_id=?", (uid,))
        c.executemany("INSERT OR REPLACE INTO listen_progress(abs_user_id, item_id, progress, finished, finished_at, updated) VALUES(?,?,?,?,?,?)", entries)
    c.execute("COMMIT")
    return f"{len(rows)} Hörsitzungen gelesen, Fortschritt von {len(prog)} Konten"


# ---------- Auswertung ----------

def period_range(period: str, offset: int, today: date | None = None) -> tuple[date, date, str]:
    """Erster und letzter Tag (einschliesslich) der Woche, des Monats oder Jahres; offset 0 = laufend, -1 = davor."""
    today = today or datetime.now(TZ).date()
    if period == "week":
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=offset)
        end = start + timedelta(days=6)
        first = f"{start.day}.{start.month}." + ("" if start.year == end.year else str(start.year))
        return start, end, f"KW {start.isocalendar()[1]} · {first}–{end.day}.{end.month}.{end.year}"
    if period == "month":
        m = today.year * 12 + today.month - 1 + offset
        start = date(m // 12, m % 12 + 1, 1)
        nxt = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        return start, nxt - timedelta(days=1), f"{MONTHS[start.month - 1]} {start.year}"
    if period == "year":
        y = today.year + offset
        return date(y, 1, 1), date(y, 12, 31), str(y)
    raise ValueError(period)


def _streak(days: set[str]) -> int:
    best = run = 0
    prev = None
    for d in sorted(date.fromisoformat(x) for x in days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best, prev = max(best, run), d
    return best


def _current_streak(days: set[str], today: date) -> int:
    d = today if today.isoformat() in days else today - timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n, d = n + 1, d - timedelta(days=1)
    return n


def overview(period: str = "week", offset: int = 0, user: str = "", today: date | None = None, only: str | None = None) -> dict:
    """`only`: nur dieses Audiobookshelf-Konto (Benutzer ohne Adminrechte); "" heisst ohne verknüpftes Konto, also leer."""
    today = today or datetime.now(TZ).date()
    offset = min(offset, 0)
    start, end, label = period_range(period, offset, today)
    length = (end - start).days + 1
    pstart = period_range(period, offset - 1, today)[0]
    c = db.conn()
    abw = {r["abs_user_id"]: r["id"] for r in c.execute("SELECT id, abs_user_id FROM user WHERE abs_user_id IS NOT NULL")}
    books = {r["abs_item_id"]: r for r in c.execute("SELECT abs_item_id, title, author, lang, runtime_min, edition_id FROM book WHERE abs_item_id IS NOT NULL")}

    # «pirateaba» und «Pirateaba» sind derselbe Autor: die Schreibweise aus dem Bestand gilt, sonst die zuerst gesehene
    spelling: dict[str, str] = {}
    for b in books.values():
        if b["author"]:
            spelling.setdefault(abs_mod.primary_author(b["author"]).casefold(), abs_mod.primary_author(b["author"]))

    def meta(item, title, author):
        b = books.get(item)
        name = abs_mod.primary_author(b["author"] if b and b["author"] else author) or "Unbekannt"
        return {"item_id": item, "title": b["title"] if b else title.removesuffix(" (Unabridged)"),
                "author": spelling.setdefault(name.casefold(), name)}

    # Konten: alle mit mindestens einer Sitzung, Kennzahlen für diesen und den vorigen Zeitraum
    names = {r["abs_user_id"]: r["username"] for r in c.execute("SELECT abs_user_id, username FROM listen_session GROUP BY abs_user_id")}
    per = {u: {"seconds": 0, "prev_seconds": 0, "books": set(), "days": set(), "sessions": 0} for u in names}
    for r in c.execute("SELECT abs_user_id, item_id, day, seconds FROM listen_session WHERE day BETWEEN ? AND ?", (pstart.isoformat(), end.isoformat())):
        p = per[r["abs_user_id"]]
        if r["day"] < start.isoformat():
            p["prev_seconds"] += r["seconds"]
        elif r["seconds"] > 0:
            p["seconds"] += r["seconds"]
            p["books"].add(r["item_id"])
            p["days"].add(r["day"])
            p["sessions"] += 1
    lastseen = {r["abs_user_id"]: r["m"] for r in c.execute("SELECT abs_user_id, MAX(updated) m FROM listen_session WHERE seconds>0 GROUP BY abs_user_id")}
    finished_in = Counter(r["abs_user_id"] for r in c.execute(
        "SELECT abs_user_id, finished_at FROM listen_progress WHERE finished=1 AND finished_at IS NOT NULL")
        if start <= _local(r["finished_at"]).date() <= end)
    users = sorted(({"abs_user_id": u, "name": names[u], "abw_id": abw.get(u), "seconds": p["seconds"], "prev_seconds": p["prev_seconds"],
                     "books": len(p["books"]), "days": len(p["days"]), "sessions": p["sessions"], "finished": finished_in[u],
                     "last": lastseen.get(u)} for u, p in per.items()), key=lambda x: x["name"].lower())
    users.sort(key=lambda x: x["last"] or "", reverse=True)  # ohne Hören im Zeitraum: zuletzt aktive zuerst
    users.sort(key=lambda x: -x["seconds"])

    if only is not None:
        users = [u for u in users if u["abs_user_id"] == only]
        who = only
    else:
        who = user if user in names else ""
    cond, args = ("AND abs_user_id=?", (who,)) if who or only is not None else ("", ())
    sess = [dict(r) for r in c.execute(f"SELECT * FROM listen_session WHERE day BETWEEN ? AND ? AND seconds>0 {cond} ORDER BY started DESC",
                                       (start.isoformat(), end.isoformat(), *args))]

    # Zeitverlauf: Tage (Woche, Monat) oder Monate (Jahr)
    if period == "year":
        keys = [f"{start.year}-{m:02d}" for m in range(1, 13)]
        slot = lambda s: s["day"][:7]
        labels = [MONTHS[m][:3] for m in range(12)]
    else:
        keys = [(start + timedelta(days=i)).isoformat() for i in range(length)]
        slot = lambda s: s["day"]
        labels = [WEEKDAYS[(start + timedelta(days=i)).weekday()][:2] if period == "week" else str(i + 1) for i in range(length)]
    timeline = Counter()
    heat = [[0] * 24 for _ in range(7)]
    by_book: dict[str, dict] = {}
    authors, devices, langs, per_day = Counter(), Counter(), Counter(), Counter()
    for s in sess:
        timeline[slot(s)] += s["seconds"]
        per_day[s["day"]] += s["seconds"]
        t = _local(s["started"])
        heat[t.weekday()][t.hour] += s["seconds"]
        m = meta(s["item_id"], s["title"], s["author"])
        b = by_book.setdefault(s["item_id"], {**m, "seconds": 0, "sessions": 0, "first": s["started"], "last": s["started"], "listeners": set()})
        b["seconds"] += s["seconds"]
        b["sessions"] += 1
        b["first"] = min(b["first"], s["started"])
        b["last"] = max(b["last"], s["started"])
        b["listeners"].add(s["username"])
        authors[m["author"]] += s["seconds"]
        devices[s["device"] or "Unbekannt"] += s["seconds"]
        lb = books.get(s["item_id"])
        langs[(lb["lang"] if lb else None) or s["lang"] or "?"] += s["seconds"]

    prog = {}
    if who:
        prog = {r["item_id"]: dict(r) for r in c.execute("SELECT * FROM listen_progress WHERE abs_user_id=?", (who,))}
    top_books = sorted(by_book.values(), key=lambda b: -b["seconds"])[:25]
    for b in top_books:
        p = prog.get(b["item_id"])
        b["progress"] = p["progress"] if p else None
        b["finished"] = bool(p and p["finished"])
        b["listeners"] = sorted(b["listeners"], key=str.lower)

    # Beendet im Zeitraum
    fin = []
    for r in c.execute(f"SELECT * FROM listen_progress WHERE finished=1 AND finished_at IS NOT NULL {cond}", args):
        if start <= _local(r["finished_at"]).date() <= end:
            title = c.execute("SELECT title, author FROM listen_session WHERE item_id=? LIMIT 1", (r["item_id"],)).fetchone()
            fin.append({**meta(r["item_id"], title["title"] if title else "", title["author"] if title else ""),
                        "finished_at": r["finished_at"], "name": names.get(r["abs_user_id"], "?")})
    fin.sort(key=lambda x: x["finished_at"], reverse=True)

    # Liegengeblieben: angefangen, nicht beendet, seit 30 Tagen keine Sitzung (nur für ein Konto, unabhängig vom Zeitraum)
    stale = []
    if who:
        lastplay = {r["item_id"]: r for r in c.execute(
            "SELECT item_id, MAX(started) last, title, author FROM listen_session WHERE abs_user_id=? AND seconds>0 GROUP BY item_id", (who,))}
        cutoff = datetime.combine(today, datetime.min.time(), TZ) - timedelta(days=STALE_DAYS)
        for item, p in prog.items():
            lp = lastplay.get(item)
            if not p["finished"] and lp and (p["progress"] or 0) >= 0.02 and (p["progress"] or 0) < 0.97 and datetime.fromisoformat(lp["last"]) < cutoff:
                stale.append({**meta(item, lp["title"], lp["author"]), "progress": p["progress"], "last": lp["last"]})
        stale.sort(key=lambda x: x["last"], reverse=True)

    total = sum(s["seconds"] for s in sess)
    prev = sum(u["prev_seconds"] for u in users if not who and only is None or u["abs_user_id"] == who)
    longest = max(sess, key=lambda s: s["seconds"], default=None)
    best_day = max(per_day.items(), key=lambda x: x[1], default=None)
    best_slot = max(timeline.items(), key=lambda x: x[1], default=None)
    wd = [sum(row) for row in heat]
    hours = [sum(heat[d][h] for d in range(7)) for h in range(24)]
    alldays = {r["day"] for r in c.execute(f"SELECT DISTINCT day FROM listen_session WHERE seconds>0 {cond}", args)}
    return {
        "range": {"period": period, "offset": offset, "start": start.isoformat(), "end": end.isoformat(), "label": label,
                  "days": min(length, max(0, (today - start).days + 1))},
        "users": users, "user": who,
        "summary": {
            "seconds": total, "prev_seconds": prev, "sessions": len(sess), "books": len(by_book), "days": len(per_day),
            "finished": len(fin), "listeners": len({s["abs_user_id"] for s in sess}),
            "corrected": sum(1 for s in sess if s["raw_seconds"] is not None and s["raw_seconds"] > s["seconds"] + 60),
            "streak": _streak(set(per_day)), "current_streak": _current_streak(alldays, today) if offset == 0 else None,
            "longest": {"seconds": longest["seconds"], "started": longest["started"], **meta(longest["item_id"], longest["title"], longest["author"])} if longest else None,
            "best_day": {"day": best_day[0], "seconds": best_day[1]} if best_day else None,
            "best_slot": {"key": best_slot[0], "seconds": best_slot[1]} if best_slot and period == "year" else None,
            "weekday": WEEKDAYS[wd.index(max(wd))] if total else None,
            "hour": hours.index(max(hours)) if total else None,
            "lang": dict(langs),
        },
        "timeline": [{"key": k, "label": lab, "seconds": timeline[k]} for k, lab in zip(keys, labels)],
        "heat": heat,
        "books": top_books,
        "authors": [{"name": k, "seconds": v} for k, v in authors.most_common(10)],
        "devices": [{"name": k, "seconds": v} for k, v in devices.most_common(6)],
        "finished": fin,
        "stale": stale[:20],
        "history": [{**meta(s["item_id"], s["title"], s["author"]), "id": s["id"], "name": s["username"], "started": s["started"],
                     "updated": s["updated"], "seconds": s["seconds"], "pos_start": s["pos_start"], "pos_end": s["pos_end"],
                     "duration": s["duration"], "device": s["device"], "raw_seconds": s["raw_seconds"]} for s in sess[:300]],
        "history_total": len(sess),
    }
