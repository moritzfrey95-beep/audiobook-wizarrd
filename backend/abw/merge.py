"""Doppelte Karten zusammenführen (offen seit 13.11.): dieselbe Audible-Serie in derselben Sprache liegt auf zwei Karten,
weil Audiobookshelf die Serie verschieden nennt («Dune» und «Dune, The Dune Sequence», «Syl» und «Syl [German Edition]»,
«The First Law Trilogy» und «Die Klingen-Saga»).

ABW ordnet Bücher bei jedem Bestandsabgleich nach dem Seriennamen aus Audiobookshelf zu. Damit die Zusammenführung bleibt,
merkt sich ABW den Namen der aufgelösten Fassung als Verweis (`edition_alias`); `abs.get_edition` folgt ihm.
Audiobookshelf selbst wird nicht geändert."""
from datetime import datetime, timezone

from . import db


def _parts(name: str) -> list[str]:
    return [p.strip() for p in (name or "").split(", ") if p.strip()]


def _owned(c, eid: int) -> int:
    return c.execute("SELECT COUNT(*) FROM book WHERE edition_id=? AND owned=1", (eid,)).fetchone()[0]


def plan() -> list[dict]:
    """Gruppen gleicher Audible-Serie und Sprache auf verschiedenen Karten (nur Hauptfassungen).
    Ziel ist die Fassung, deren Name als Teil im Namen der anderen steckt («Dune» in «Dune, The Dune Sequence»),
    sonst die ohne Komma und Zusatz, sonst die mit den meisten Büchern im Bestand."""
    c = db.conn()
    groups = c.execute("SELECT audible_asin, lang FROM edition WHERE audible_asin IS NOT NULL AND variant IS NULL"
                       " GROUP BY audible_asin, lang HAVING COUNT(DISTINCT series_id) > 1").fetchall()
    out = []
    for g in groups:
        eds = [dict(r) | {"owned": _owned(c, r["id"])} for r in c.execute(
            "SELECT e.id, e.series_id, e.name, e.norm, s.name series FROM edition e JOIN series s ON s.id=e.series_id"
            " WHERE e.audible_asin=? AND e.lang=? AND e.variant IS NULL", (g["audible_asin"], g["lang"]))]
        norms = {e["norm"] for e in eds}

        def rank(e):
            named_in_other = any(o is not e and e["norm"] in {db.norm(p) for p in _parts(o["name"])} for o in eds)
            plain = ", " not in e["name"] and "[" not in e["name"] and "(German Edition)" not in e["name"]
            return (named_in_other, plain, e["owned"], -e["id"])

        target = max(eds, key=rank)
        out.append({"asin": g["audible_asin"], "lang": g["lang"], "target": target,
                    "merge": [e for e in eds if e["id"] != target["id"] and e["series_id"] != target["series_id"]],
                    "norms": sorted(norms)})
    return [p for p in out if p["merge"]]


def apply(groups: list[dict] | None = None) -> dict:
    """Führt zusammen: Bücher im Bestand wandern auf die Zielfassung, nur gesuchte Bände ohne laufenden Download
    entfallen (der nächste Audible-Abgleich der Zielfassung legt fehlende Bände neu an), Markierungen und Merkliste
    zeigen auf die Zielserie, der alte Name wird Verweis. Leere Serien verschwinden."""
    c = db.conn()
    groups = plan() if groups is None else groups
    now = datetime.now(timezone.utc).isoformat()
    moved = dropped = 0
    c.execute("BEGIN IMMEDIATE")
    try:
        for g in groups:
            t = g["target"]
            for e in g["merge"]:
                active = "SELECT book_id FROM download WHERE state IN ('queued','downloading','arrived','verified','review','imported')"
                dropped += c.execute(f"DELETE FROM book WHERE edition_id=? AND owned=0 AND id NOT IN ({active})", (e["id"],)).rowcount
                c.execute("DELETE FROM book_search WHERE book_id NOT IN (SELECT id FROM book)")
                moved += c.execute("UPDATE book SET edition_id=? WHERE edition_id=?", (t["id"], e["id"])).rowcount
                c.execute("INSERT OR REPLACE INTO edition_alias(lang, norm, edition_id, created) VALUES(?,?,?,?)",
                          (g["lang"], e["norm"], t["id"], now))
                c.execute("UPDATE edition_alias SET edition_id=? WHERE edition_id=?", (t["id"], e["id"]))
                c.execute("DELETE FROM edition WHERE id=?", (e["id"],))
                if not c.execute("SELECT 1 FROM edition WHERE series_id=?", (e["series_id"],)).fetchone():
                    old, new = str(e["series_id"]), str(t["series_id"])
                    for table in ("follow", "auto_follow"):
                        c.execute(f"INSERT OR IGNORE INTO {table}(user_id, kind, key, created) SELECT user_id, kind, ?, created"
                                  f" FROM {table} WHERE kind='series' AND key=?", (new, old))
                        c.execute(f"DELETE FROM {table} WHERE kind='series' AND key=?", (old,))
                    c.execute("UPDATE watchlist SET series_id=? WHERE series_id=?", (t["series_id"], e["series_id"]))
                    c.execute("DELETE FROM series WHERE id=?", (e["series_id"],))
            c.execute("UPDATE edition SET synced_at=NULL WHERE id=?", (t["id"],))  # beim nächsten Audible-Lauf sofort abgleichen
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise
    return {"groups": len(groups), "moved": moved, "dropped": dropped}
