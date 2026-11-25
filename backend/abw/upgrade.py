"""Qualitäts-Upgrade: Bücher im Bestand mit schwacher Tonqualität erkennen und eine bessere Fassung nachladen.

Schwach heisst: mittlere Datenrate (Grösse durch Laufzeit) unter der Schwelle `upgrade_kbps`. Gefunden wird wie bei
fehlenden Bänden (gleiche Quellen und Bewertung), geladen werden nur sichere Treffer, die mindestens ein Viertel
mehr Daten je Minute mitbringen. Der Download ersetzt den Bestand nicht direkt: Die Ankunftsprüfung vergleicht ihn
mit dem vorhandenen Buch, und nur eindeutig bessere Fassungen ersetzen es (alt in den Papierkorb), sonst entscheidet
der Administrator im Tab «Prüfen».

Bücher, die kürzer sind als bei Audible, stehen in der Liste, werden aber nicht automatisch gesucht: oft ist es
eine andere Aufnahme (anderer Sprecher), die nicht ersetzt werden soll.
"""
from datetime import datetime, timedelta, timezone

from . import db, search, usenet

WAIT_DAYS = [1, 3, 7, 14, 30]  # nach erfolgloser Suche
BETTER = 1.25  # Treffer braucht so viel mehr Daten je Minute wie der Bestand


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def kbps(size: int | None, minutes: int | None) -> float:
    return size * 8 / (minutes * 60) / 1000 if size and minutes else 0.0


def candidates(limit: int | None = None) -> list[dict]:
    """Bücher im Bestand mit schwacher Datenrate oder deutlich kürzer als bei Audible, schwächste zuerst."""
    floor = int(db.setting("upgrade_kbps") or 40)
    rows = db.conn().execute(
        "SELECT b.id, b.title, b.lang, b.sequence, b.size, b.runtime_min, b.audible_min, e.name series, e.series_id,"
        " u.attempts, u.last_search, u.next_search, u.last_result, u.text"
        " FROM book b LEFT JOIN edition e ON e.id=b.edition_id LEFT JOIN upgrade_search u ON u.book_id=b.id"
        " WHERE b.owned=1 AND b.size>0 AND b.runtime_min>0").fetchall()
    out = []
    for r in rows:
        rate = kbps(r["size"], r["runtime_min"])
        short = bool(r["audible_min"]) and r["runtime_min"] < r["audible_min"] - max(10, r["audible_min"] * 0.05)
        if rate >= floor and not short:
            continue
        reason = "weak" if rate < floor else "short"
        text = (f"{round(rate)} kbit/s, Schwelle {floor}" if reason == "weak"
                else f"{r['audible_min'] - r['runtime_min']} min kürzer als bei Audible")
        out.append({**dict(r), "kbps": round(rate), "reason": reason, "reason_text": text})
    out.sort(key=lambda x: (x["reason"] != "weak", x["kbps"]))
    return out[:limit] if limit else out


def _due(c, book_id: int) -> bool:
    row = c.execute("SELECT next_search FROM upgrade_search WHERE book_id=?", (book_id,)).fetchone()
    return not row or not row["next_search"] or row["next_search"] <= _now()


def better_release(book: dict, results: list[dict]) -> dict | None:
    """Bester sicherer Einzelband, der spürbar mehr Daten je Minute hat als das Buch im Bestand."""
    have = kbps(book["size"], book["runtime_min"])
    for r in results:
        if r.get("verdict") != "sicher" or r.get("bundle") or r.get("declined") or r.get("hidden_until"):
            continue
        if kbps(r.get("size"), book["runtime_min"]) >= have * BETTER:
            return r
    return None


def search_one(book_id: int, auto: bool = True) -> dict:
    """Bessere Fassung für ein Buch suchen und, wenn eindeutig, laden. Hält das Ergebnis in `upgrade_search` fest."""
    c = db.conn()
    book, _ = search.book_ctx(book_id)
    if not book["owned"]:
        raise ValueError("Dieses Buch ist nicht im Bestand")
    attempts = (c.execute("SELECT attempts FROM upgrade_search WHERE book_id=?", (book_id,)).fetchone() or {"attempts": 0})["attempts"]
    if search.active_download(book_id):
        result, text = "skipped", "Für dieses Buch läuft schon ein Download"
    else:
        try:
            found = search.guided(book_id, early_stop=False)
            best = better_release(book, found["results"])
            if best:
                search.grab(book_id, best, auto=auto, upgrade=True)
                result, text = "started", f"Geladen: {best['title']} ({best['score']} Punkte), wird nach der Ankunft mit dem Bestand verglichen"
            else:
                result, text = "none", f"Kein sicherer Treffer mit besserer Qualität ({len(found['results'])} Treffer)"
        except Exception as exc:  # noqa: BLE001 - ein Buch darf den Lauf nicht abbrechen
            if search.active_download(book_id):  # während der Suche hat jemand anderes geladen (Knopf «Suchen» und Automatik gleichzeitig)
                result, text = "skipped", "Für dieses Buch läuft schon ein Download"
            else:
                result, text = "error", str(exc)[:200]
    if result == "skipped" and (prev := c.execute("SELECT last_result FROM upgrade_search WHERE book_id=?", (book_id,)).fetchone()) and prev["last_result"] == "started":
        return {"book_id": book_id, "result": result, "text": text}
    wait = timedelta(hours=1) if result in ("error", "skipped") else timedelta(days=WAIT_DAYS[min(attempts, len(WAIT_DAYS) - 1)])
    c.execute("INSERT OR REPLACE INTO upgrade_search(book_id, attempts, last_search, next_search, last_result, text) VALUES(?,?,?,?,?,?)",
              (book_id, attempts + (result in ("none", "started")), _now(), (datetime.now(timezone.utc) + wait).isoformat(), result, text))
    return {"book_id": book_id, "result": result, "text": text}


def run(progress=lambda msg: None) -> str:
    """Aufgabe: je Lauf bis zu drei fällige Bücher mit schwacher Datenrate."""
    if db.setting("auto_upgrade") != "1":
        return "Qualitäts-Upgrade ist ausgeschaltet"
    if not (usenet.configured() and usenet.sab_configured()):
        return "Usenet oder SABnzbd ist nicht eingerichtet"
    c = db.conn()
    due = [b for b in candidates() if b["reason"] == "weak" and _due(c, b["id"])][:3]
    if not due:
        return "Kein Buch fällig"
    res: dict[str, int] = {}
    for i, b in enumerate(due, 1):
        progress(f"{i} von {len(due)}: «{b['title']}»")
        r = search_one(b["id"])
        res[r["result"]] = res.get(r["result"], 0) + 1
    return f"{len(due)} Bücher gesucht: {res.get('started', 0)} bessere Fassungen geladen, {res.get('none', 0)} ohne besseren Treffer"


def overview() -> dict:
    items = candidates()
    return {"enabled": db.setting("auto_upgrade") == "1", "kbps": int(db.setting("upgrade_kbps") or 40),
            "weak": sum(x["reason"] == "weak" for x in items), "short": sum(x["reason"] == "short" for x in items),
            "items": items[:200]}
