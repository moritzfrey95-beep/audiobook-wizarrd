"""Jahresrückblick im Stil eines «Wrapped»: das Hörjahr eines Kontos als Folge von Karten.

Grundlage ist der Hörverlauf (`listening.overview` für das Jahr), ergänzt um Sprecher, abgeschlossene Serien, Tempo je
Buch und den Vergleich mit dem Vorjahr. Alles wird aus den gespeicherten Sitzungen berechnet, nichts wird geschrieben.
"""
from collections import Counter
from datetime import date, datetime

from . import abs as abs_mod
from . import db, listening

MONTHS = listening.MONTHS


def years(abs_user: str | None) -> list[int]:
    cond, args = ("WHERE abs_user_id=? AND seconds>0", (abs_user,)) if abs_user else ("WHERE seconds>0", ())
    return [int(r[0]) for r in db.conn().execute(f"SELECT DISTINCT substr(day, 1, 4) FROM listen_session {cond} ORDER BY 1 DESC", args)]


def build(year: int, abs_user: str | None, today: date | None = None) -> dict:
    """abs_user None = alle Konten zusammen (nur Administratoren)."""
    today = today or datetime.now(listening.TZ).date()
    offset = year - today.year
    if offset > 0:
        raise ValueError("Dieses Jahr hat noch nicht angefangen")
    ov = listening.overview("year", offset, user=abs_user or "", today=today, only=abs_user)
    prev = listening.overview("year", offset - 1, user=abs_user or "", today=today, only=abs_user)["summary"]["seconds"]
    c = db.conn()
    cond, args = ("AND abs_user_id=?", (abs_user,)) if abs_user else ("", ())
    start, end = f"{year}-01-01", f"{year}-12-31"
    sess = c.execute(f"SELECT item_id, seconds, day, started FROM listen_session WHERE day BETWEEN ? AND ? AND seconds>0 {cond}",
                     (start, end, *args)).fetchall()
    books = {r["abs_item_id"]: r for r in c.execute(
        "SELECT b.abs_item_id, b.title, b.author, b.narrator, b.sequence, b.runtime_min, b.edition_id, e.name series, e.series_id"
        " FROM book b LEFT JOIN edition e ON e.id=b.edition_id WHERE b.abs_item_id IS NOT NULL")}

    # Sprecher: Hörzeit je Sprecher (erster genannter), wie bei Autoren
    narr = Counter()
    first_play: dict[str, str] = {}
    for s in sess:
        b = books.get(s["item_id"])
        if b and b["narrator"]:
            narr[b["narrator"].split(",")[0].strip()] += s["seconds"]
        first_play[s["item_id"]] = min(first_play.get(s["item_id"], s["started"]), s["started"])

    # Meistgehört nach Serien: Hörzeit je Serie, Bücher ohne Serie zählen einzeln
    ser: dict = {}
    for x in sess:
        b = books.get(x["item_id"])
        if not b:
            continue
        key = ("s", b["series_id"]) if b["series_id"] else ("b", x["item_id"])
        e = ser.setdefault(key, {"name": b["series"] if b["series_id"] else b["title"], "author": abs_mod.primary_author(b["author"] or ""),
                                 "seconds": 0, "items": Counter(), "series": bool(b["series_id"])})
        e["seconds"] += x["seconds"]
        e["items"][x["item_id"]] += x["seconds"]
    top_series = [{"name": e["name"], "author": e["author"], "hours": round(e["seconds"] / 3600, 1), "books": len(e["items"]),
                   "series": e["series"], "item_id": e["items"].most_common(1)[0][0]}
                  for e in sorted(ser.values(), key=lambda e: -e["seconds"])[:5]]

    # Tempo: beendete Bücher, Tage vom ersten Hören bis zum Ende (nur wenn der Anfang im Verlauf liegt)
    fin = ov["finished"]
    pace = []
    all_first = {r["item_id"]: r["f"] for r in c.execute(f"SELECT item_id, MIN(started) f FROM listen_session WHERE seconds>0 {cond} GROUP BY item_id", args)}
    for f in fin:
        st = all_first.get(f["item_id"])
        if st:
            days = max(1, (listening._local(f["finished_at"]).date() - listening._local(st).date()).days + 1)
            b = books.get(f["item_id"])
            pace.append({"item_id": f["item_id"], "title": f["title"], "days": days, "hours": round((b["runtime_min"] or 0) / 60, 1) if b else None})
    pace.sort(key=lambda x: (x["days"], -(x["hours"] or 0)))

    # Abgeschlossene Serien: im Jahr den letzten vorhandenen Band einer Serie beendet, und alle ihre Bände sind beendet
    finished_items = {r["item_id"] for r in c.execute(f"SELECT item_id FROM listen_progress WHERE finished=1 {cond}", args)}
    done_series = []
    for f in fin:
        b = books.get(f["item_id"])
        if not b or not b["edition_id"]:
            continue
        own = [r["abs_item_id"] for r in c.execute("SELECT abs_item_id FROM book WHERE edition_id=? AND owned=1 AND abs_item_id IS NOT NULL", (b["edition_id"],))]
        if len(own) >= 2 and all(i in finished_items for i in own) and b["series_id"] not in {d["series_id"] for d in done_series}:
            done_series.append({"series_id": b["series_id"], "name": b["series"], "books": len(own), "item_id": f["item_id"]})

    s = ov["summary"]
    best_month = s["best_slot"]
    top = ov["books"][:5]
    return {
        "year": year, "complete": year < today.year, "user": abs_user,
        "users": [{"abs_user_id": u["abs_user_id"], "name": u["name"]} for u in ov["users"]] if abs_user is None else [],
        "hours": round(s["seconds"] / 3600, 1), "prev_hours": round(prev / 3600, 1), "days": s["days"], "sessions": s["sessions"],
        "books": s["books"], "finished": s["finished"], "streak": s["streak"],
        "top_books": [{"item_id": b["item_id"], "title": b["title"], "author": b["author"], "hours": round(b["seconds"] / 3600, 1)} for b in top],
        "top_series": top_series,
        "top_authors": [{"name": a["name"], "hours": round(a["seconds"] / 3600, 1)} for a in ov["authors"][:5]],
        "top_narrators": [{"name": n, "hours": round(v / 3600, 1)} for n, v in narr.most_common(5)],
        "best_month": {"name": MONTHS[int(best_month["key"][5:7]) - 1], "hours": round(best_month["seconds"] / 3600, 1)} if best_month else None,
        "months": [{"label": t["label"], "hours": round(t["seconds"] / 3600, 1)} for t in ov["timeline"]],
        "weekday": s["weekday"], "hour": s["hour"],
        "longest": {"title": s["longest"]["title"], "hours": round(s["longest"]["seconds"] / 3600, 1), "day": s["longest"]["started"][:10]} if s["longest"] else None,
        "best_day": {"day": s["best_day"]["day"], "hours": round(s["best_day"]["seconds"] / 3600, 1)} if s["best_day"] else None,
        "fastest": pace[0] if pace else None,
        "avg_days": round(sum(p["days"] for p in pace) / len(pace)) if pace else None,
        "series_done": done_series[:6],
        "langs": {k: round(v / 3600, 1) for k, v in s["lang"].items()},
        "authors_total": len({abs_mod.primary_author((books.get(x["item_id"]) or {"author": ""})["author"] or "") for x in sess if books.get(x["item_id"])}),
    }
