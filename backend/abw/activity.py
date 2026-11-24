"""Aktivität der letzten 24 Stunden und der letzten 7 Tage, aus den Zeitstempeln der eigenen Tabellen."""
from . import db, plugins

WINDOWS = {"d1": "-1 day", "d7": "-7 days"}
BUILTIN = {"usenet": "Usenet", "audiobookbay": "AudioBookBay"}


def source_name(key: str) -> str:
    src = plugins.get(key)
    return BUILTIN.get(key) or (src.name if src else key)


def _since(col: str) -> str:
    # Zeitstempel stehen als «2026-11-11T22:55:41+00:00» oder «2026-11-11 22:55:41» (beides UTC) in der Datenbank
    return f"replace(substr({col},1,19),'T',' ') >= datetime('now', ?)"


def overview() -> dict:
    c = db.conn()
    out = {}
    for key, span in WINDOWS.items():
        one = lambda sql: c.execute(sql, (span,)).fetchone()[0] or 0
        placed = c.execute("SELECT source, COUNT(*) n, COALESCE(SUM(size),0) s FROM download WHERE state IN ('done','imported') AND "
                           + _since("updated") + " GROUP BY source", (span,)).fetchall()
        started = {r["source"]: r["n"] for r in c.execute(
            "SELECT source, COUNT(*) n FROM download WHERE parent_id IS NULL AND " + _since("created") + " GROUP BY source", (span,))}
        by_placed = {r["source"]: r["n"] for r in placed}
        known = list(BUILTIN) + [s.key for s in plugins.sources()]
        names = known + sorted((set(started) | set(by_placed)) - set(known))
        out[key] = {
            "searched": one("SELECT COUNT(*) FROM book_search WHERE " + _since("last_search")),
            "started": sum(started.values()),
            "placed": sum(by_placed.values()),
            "placed_gb": round(sum(r["s"] for r in placed) / 1e9, 1),
            "failed": one("SELECT COUNT(*) FROM download WHERE state='failed' AND " + _since("updated")),
            "rejected": one("SELECT COUNT(*) FROM download WHERE state='rejected' AND " + _since("updated")),
            "added": one("SELECT COUNT(*) FROM book WHERE owned=1 AND " + _since("added_at")),
            "converted": one("SELECT COUNT(*) FROM convert_job WHERE state='done' AND " + _since("finished")),
            "chapters": one("SELECT COUNT(*) FROM chapter_job WHERE state='done' AND " + _since("at")),
            "ebooks": one("SELECT COUNT(*) FROM ebook_job WHERE state='done' AND " + _since("updated")),
            "sources": [{"source": s, "name": source_name(s), "started": started.get(s, 0), "placed": by_placed.get(s, 0)}
                        for s in names if started.get(s) or by_placed.get(s)],
        }
    return out
