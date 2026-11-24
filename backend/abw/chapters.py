"""Kapitel ergänzen: Bücher aus einer einzigen Datei ohne Kapitelmarken bekommen die Kapitel von Audible.

Die Audiodatei wird nicht verändert. Die Kapitel werden über die Schnittstelle von Audiobookshelf gesetzt.
"""
from datetime import datetime, timezone

import httpx

from . import abs as abs_mod
from . import config, db

REGION = {"en": "us", "de": "de"}


def candidates_sql() -> str:
    return ("SELECT id FROM book WHERE owned=1 AND num_files=1 AND COALESCE(num_chapters, 0) <= 1 AND asin IS NOT NULL AND length(asin)=10"
            " AND abs_item_id IS NOT NULL AND id NOT IN (SELECT book_id FROM chapter_job)")


def fetch(asin: str, lang: str) -> dict | None:
    r = httpx.get(f"https://api.audnex.us/books/{asin}/chapters", params={"region": REGION[lang]}, timeout=30)
    if r.status_code in (400, 404):
        return None
    r.raise_for_status()
    return r.json()


def apply(book: dict) -> str:
    """Setzt die Kapitel, wenn die Laufzeit bei Audible zur Datei passt. Liefert einen Satz zum Ergebnis."""
    data = fetch(book["asin"], book["lang"])
    chapters = (data or {}).get("chapters") or []
    if len(chapters) < 2:
        raise ValueError("Audible hat zu dieser Ausgabe keine Kapitel")
    with abs_mod._client() as http:
        item = http.get(f"/api/items/{book['abs_item_id']}", params={"expanded": 1}).raise_for_status().json()
        media = item["media"]
        if len(media.get("chapters") or []) > 1:
            raise ValueError("In Audiobookshelf sind schon Kapitel vorhanden")
        duration = float(media.get("duration") or 0)
        want = (data.get("runtimeLengthMs") or 0) / 1000
        if not duration or abs(duration - want) > 60:
            raise ValueError(f"Laufzeit passt nicht zu Audible ({round(duration / 60)} min statt {round(want / 60)} min)")
        out = []
        for i, ch in enumerate(chapters):
            start = ch["startOffsetMs"] / 1000
            end = (chapters[i + 1]["startOffsetMs"] / 1000) if i + 1 < len(chapters) else duration
            if start >= duration:
                break
            out.append({"id": i, "start": round(start, 3), "end": round(min(end, duration), 3), "title": ch.get("title") or f"Kapitel {i + 1}"})
        http.post(f"/api/items/{book['abs_item_id']}/chapters", json={"chapters": out}).raise_for_status()
    return f"{len(out)} Kapitel von Audible gesetzt"


def auto(progress=lambda msg: None) -> str:
    c = db.conn()
    n = c.execute(f"SELECT COUNT(*) FROM ({candidates_sql()})").fetchone()[0]
    if db.setting("auto_chapters") != "1":
        return f"Kapitel ergänzen ist ausgeschaltet, {n} Bücher kämen in Frage"
    done = skipped = 0
    for row in c.execute(candidates_sql() + " LIMIT 15").fetchall():
        b = dict(c.execute("SELECT * FROM book WHERE id=?", (row["id"],)).fetchone())
        progress(f"Kapitel für «{b['title']}»")
        try:
            msg, state = apply(b), "done"
            done += 1
        except ValueError as exc:
            msg, state = str(exc), "skipped"
            skipped += 1
        except Exception as exc:
            msg, state = f"Fehler: {exc}"[:200], "failed"
            skipped += 1
        c.execute("INSERT OR REPLACE INTO chapter_job(book_id, state, message, at) VALUES(?,?,?,?)",
                  (b["id"], state, msg, datetime.now(timezone.utc).isoformat()))
    return f"{done} Bücher mit Kapiteln ergänzt, {skipped} übersprungen, {max(0, n - done - skipped)} offen"


def overview() -> dict:
    c = db.conn()
    return {"pending": c.execute(f"SELECT COUNT(*) FROM ({candidates_sql()})").fetchone()[0],
            "counts": {r["state"]: r["n"] for r in c.execute("SELECT state, COUNT(*) n FROM chapter_job GROUP BY state")},
            "recent": [dict(r) for r in c.execute("SELECT j.*, b.title FROM chapter_job j JOIN book b ON b.id=j.book_id ORDER BY j.at DESC LIMIT 15")]}
