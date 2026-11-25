"""Kennzahlen für Home Assistant und Prometheus: fehlende Bände, laufende Downloads, nächste Erscheinung, Hörzeit.
Abruf mit API-Schlüssel (Kopf `X-Api-Key` oder `Authorization: Bearer abw_…`)."""
from datetime import date, datetime, timedelta, timezone

from . import db


def collect() -> dict:
    c = db.conn()
    today = date.today().isoformat()
    one = lambda sql, *a: c.execute(sql, a).fetchone()[0] or 0
    missing = one("SELECT COUNT(*) FROM book b JOIN edition e ON e.id=b.edition_id WHERE b.owned=0 AND e.monitored=1"
                  " AND (b.release_date IS NULL OR b.release_date<=?)", today)
    upcoming = c.execute("SELECT b.title, b.release_date, e.name series FROM book b LEFT JOIN edition e ON e.id=b.edition_id"
                         " WHERE b.owned=0 AND b.release_date>? ORDER BY b.release_date LIMIT 1", (today,)).fetchone()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    states = {r["state"]: r["n"] for r in c.execute("SELECT state, COUNT(*) n FROM download GROUP BY state")}
    return {
        "books_owned": one("SELECT COUNT(*) FROM book WHERE owned=1"),
        "series": one("SELECT COUNT(*) FROM series"),
        "missing_monitored": missing,
        "upcoming": one("SELECT COUNT(*) FROM book WHERE owned=0 AND release_date>?", today),
        "downloads_active": sum(states.get(s, 0) for s in ("queued", "downloading", "arrived")),
        "review_open": states.get("review", 0) + states.get("verified", 0),
        "listening_hours_week": round(one("SELECT SUM(seconds) FROM listen_session WHERE day>=?", week) / 3600, 1),
        "next_release": dict(upcoming) if upcoming else None,
    }


def prometheus() -> str:
    m = collect()
    help_ = {"books_owned": "Hörbücher im Bestand", "series": "Serien in der Bibliothek", "missing_monitored": "Fehlende erschienene Bände überwachter Fassungen",
             "upcoming": "Angekündigte Bände", "downloads_active": "Laufende Downloads und Prüfungen", "review_open": "Fälle im Tab Prüfen",
             "listening_hours_week": "Hörstunden aller Benutzer in den letzten 7 Tagen"}
    out = []
    for k, text in help_.items():
        out += [f"# HELP abw_{k} {text}", f"# TYPE abw_{k} gauge", f"abw_{k} {m[k]}"]
    if m["next_release"]:
        d = datetime.fromisoformat(m["next_release"]["release_date"][:10]).replace(tzinfo=timezone.utc)
        out += ["# HELP abw_next_release_timestamp Nächste Erscheinung (Unix-Zeit)", "# TYPE abw_next_release_timestamp gauge",
                f"abw_next_release_timestamp {int(d.timestamp())}"]
    return "\n".join(out) + "\n"
