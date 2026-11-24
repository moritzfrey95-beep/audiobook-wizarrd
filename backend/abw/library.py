"""Hinzufügen: Was unter Entdecken, beim Autor oder über Goodreads hinzugefügt wird, kommt als fehlend in die Bibliothek
und wird wie alle fehlenden Bände gesucht, aber zuerst. Eine Serie wird überwacht; ein Buch, das zu einer Serie gehört,
bringt die ganze Serie mit; ein Buch ohne Serie steht als Einzelbuch auf der Suchliste.
Die Tabelle «watchlist» hält fest, wer was hinzugefügt hat."""
import re
from datetime import datetime, timezone

from . import audible, config, db, notifications

ACTIVE = "('queued','downloading','arrived','verified','review','imported')"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_book(b: dict, user_id: int) -> tuple[int, bool]:
    """Einzelbuch ohne Serie auf die Suchliste setzen. Liefert (Buch, schon im Bestand)."""
    c = db.conn()
    row = c.execute("SELECT id, owned FROM book WHERE asin=? ORDER BY owned DESC LIMIT 1", (b["asin"],)).fetchone()
    if row:
        if not row["owned"]:
            c.execute("UPDATE book SET wanted=COALESCE(wanted, ?), wanted_by=COALESCE(wanted_by, ?) WHERE id=?", (_now(), user_id, row["id"]))
        return row["id"], bool(row["owned"])
    bid = c.execute("INSERT INTO book(edition_id, lang, asin, title, subtitle, author, narrator, release_date, runtime_min, cover_url, owned,"
                    " wanted, wanted_by) VALUES(NULL,?,?,?,?,?,?,?,?,?,0,?,?)",
                    (b["lang"], b["asin"], b["title"], b.get("subtitle"), b.get("author"), b.get("narrator"), b.get("release_date"),
                     b.get("runtime_min"), b.get("cover_url"), _now(), user_id)).lastrowid
    return bid, False


def is_bundle(b: dict) -> bool:
    """Sammelband laut Audible («1-5», «Complete Series», «Box Set»): Gesucht werden dann die Einzelbände der Serie,
    denn die Quellen führen meist nur diese («System School: The Complete Series»)."""
    from . import libsplit
    return bool(libsplit.span_of({"title": b["title"], "sequence": b.get("sequence"), "series": b.get("series")})
                or libsplit.HINT.search(b["title"]) or re.search(r"\bcomplete (?:series|collection|saga|trilogy)\b", b["title"], re.I))


def requested_by(book_ids) -> dict[int, list[str]]:
    """Welche Benutzer (nicht Administratoren) diese Bücher hinzugefügt haben: das Buch selbst (Entdecken, Goodreads)
    oder seine Serie. Für die farbige Markierung unter «Prüfen»."""
    ids = sorted({int(i) for i in book_ids})
    if not ids:
        return {}
    c = db.conn()
    marks = ",".join("?" * len(ids))
    rows = c.execute(
        f"SELECT b.id, u.id uid, u.name FROM book b JOIN user u ON u.id=b.wanted_by AND u.role!='admin' WHERE b.id IN ({marks})"
        f" UNION SELECT b.id, u.id, u.name FROM book b JOIN watchlist w ON w.kind!='series' AND w.asin=b.asin"
        f" JOIN user u ON u.id=w.user_id AND u.role!='admin' WHERE b.id IN ({marks})"
        f" UNION SELECT b.id, u.id, u.name FROM book b JOIN edition e ON e.id=b.edition_id"
        f" JOIN watchlist w ON w.kind='series' AND w.series_id=e.series_id JOIN user u ON u.id=w.user_id AND u.role!='admin'"
        f" WHERE b.id IN ({marks}) ORDER BY 3", ids * 3).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        if r["name"] not in out.setdefault(r["id"], []):
            out[r["id"]].append(r["name"])
    return out


def wishers(book_ids) -> dict[int, list[str]]:
    """Wer diese Bücher gewünscht hat, für den Tag «Gewünscht von» in Audiobookshelf: Benutzer (nicht Administratoren,
    nur mit Audiobookshelf-Konto), die das Buch, irgendein Buch derselben Serie (jede Fassung) oder die Serie selbst
    hinzugefügt haben. Die ganze Serie zählt; Einzelbücher ohne Serie nur sich selbst."""
    ids = sorted({int(i) for i in book_ids})
    if not ids:
        return {}
    who = "JOIN user u ON u.id={} AND u.role!='admin' AND u.abs_user_id IS NOT NULL"
    rows = db.conn().execute(
        "WITH wish(name, bid, sid) AS ("
        f" SELECT u.name, b.id, e.series_id FROM book b {who.format('b.wanted_by')} LEFT JOIN edition e ON e.id=b.edition_id"
        f" UNION SELECT u.name, b.id, e.series_id FROM watchlist w JOIN book b ON w.kind!='series' AND w.asin=b.asin"
        f" {who.format('w.user_id')} LEFT JOIN edition e ON e.id=b.edition_id"
        f" UNION SELECT u.name, NULL, w.series_id FROM watchlist w {who.format('w.user_id')} WHERE w.kind='series')"
        " SELECT DISTINCT t.id, wish.name FROM book t LEFT JOIN edition te ON te.id=t.edition_id"
        " JOIN wish ON wish.bid=t.id OR (wish.sid IS NOT NULL AND wish.sid=te.series_id)"
        f" WHERE t.id IN ({','.join('?' * len(ids))}) ORDER BY 2", ids).fetchall()
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r[0], []).append(r[1])
    return out


def follows_on_add(user_id: int) -> bool:
    """Einstellung im Entdecken: beim Hinzufügen der Serie folgen (Standard ja)."""
    row = db.conn().execute("SELECT follow_on_add FROM user WHERE id=?", (user_id,)).fetchone()
    return not row or row["follow_on_add"] != 0


def process(asin: str) -> dict:
    """Einen Eintrag in die Bibliothek aufnehmen. Liefert den neuen Stand des Eintrags."""
    c = db.conn()
    w = c.execute("SELECT * FROM watchlist WHERE asin=?", (asin,)).fetchone()
    if not w:
        raise LookupError("Eintrag nicht gefunden")
    lang = w["lang"] if w["lang"] in ("en", "de") else config.main_lang()
    user_id = w["user_id"] or 1
    series_id = book_id = None
    try:
        if w["kind"] == "series":
            series_id = audible.add_series(lang, asin, w["title"] or asin)
        else:
            prods = audible.products(lang, [asin])
            if not prods or not prods[0].get("title"):
                raise LookupError("Audible kennt diesen Titel nicht (mehr)")
            b = audible.brief(prods[0], lang)
            if b["series_asin"] and b["series"] and (db.parse_seq(b["sequence"]) is not None or is_bundle(b)):
                series_id = audible.add_series(lang, b["series_asin"], b["series"])
            else:
                book_id, _ = add_book(b, user_id)
    except Exception as exc:
        c.execute("UPDATE watchlist SET state='error', message=? WHERE asin=?", (str(exc)[:300], asin))
        raise
    if series_id and follows_on_add(user_id):
        notifications.follow(user_id, "series", series_id)
    c.execute("UPDATE watchlist SET state='done', message=NULL, series_id=?, book_id=? WHERE asin=?", (series_id, book_id, asin))
    from . import toread
    toread.after_process(asin, lang, w["kind"])
    return {"series_id": series_id, "book_id": book_id}


def kick(series_id: int | None, book_id: int | None) -> None:
    """Die Suche sofort anstossen: über die Automatik, wenn sie läuft, sonst als einzelne automatische Suche."""
    from . import search, tasks
    if db.setting("auto_search") == "1":
        tasks.request("auto")
    elif series_id:
        search.start_auto(series_id)
    elif book_id:
        search.start_book(book_id)


def add(item: dict, user_id: int) -> dict:
    """Eintrag anlegen und gleich aufnehmen. Scheitert die Aufnahme (Audible nicht erreichbar), holt die Aufgabe
    «Hinzugefügtes aufnehmen» sie nach."""
    c = db.conn()
    asin = (item.get("asin") or "").strip()
    if not asin:
        raise ValueError("Die Audible-Nummer fehlt")
    kind = "series" if item.get("kind") == "series" else "book"
    old = c.execute("SELECT state FROM watchlist WHERE asin=?", (asin,)).fetchone()
    if not old:
        c.execute("INSERT INTO watchlist(asin, lang, title, author, series, cover_url, kind, added_at, user_id, state) VALUES(?,?,?,?,?,?,?,?,?,'pending')",
                  (asin, item.get("lang"), item.get("title"), item.get("author"), item.get("series"), item.get("cover_url"), kind, _now(), user_id))
    elif old["state"] == "done":
        res = dict(c.execute("SELECT series_id, book_id FROM watchlist WHERE asin=?", (asin,)).fetchone())
        if res["series_id"] and follows_on_add(user_id):
            notifications.follow(user_id, "series", res["series_id"])  # schon von jemand anderem hinzugefügt: mit markieren
        return {"ok": True, **res}
    try:
        res = process(asin)
    except Exception as exc:
        return {"ok": False, "pending": True, "message": f"Vorgemerkt. Die Aufnahme in die Bibliothek wird nachgeholt ({exc})."[:300]}
    kick(res["series_id"], res["book_id"])
    return {"ok": True, **res}


def pending(progress=lambda msg: None) -> str:
    """Aufgabe: Einträge aufnehmen, die noch nicht in der Bibliothek stehen (ältere Merkliste, Audible war nicht erreichbar)."""
    c = db.conn()
    rows = c.execute("SELECT asin, title FROM watchlist WHERE state IS NULL OR state IN ('pending','error') ORDER BY added_at").fetchall()
    done = 0
    for i, r in enumerate(rows, 1):
        progress(f"{i} von {len(rows)}: «{r['title'] or r['asin']}»")
        try:
            process(r["asin"])
            done += 1
        except Exception:
            continue
    if done and db.setting("auto_search") == "1":
        from . import tasks
        tasks.request("auto")
    return f"{done} von {len(rows)} Einträgen in die Bibliothek aufgenommen" if rows else "Nichts aufzunehmen"


SKIP = re.compile(r"gekürzt|\babridged", re.I)  # Hörspiele und andere Sprecher ja


def finished_authors(progress=lambda msg: None) -> str:
    """Aufgabe: Hat irgendein Benutzer ein Buch fertig gehört, kommen alle Serien dieses Autors
    aus dem Autorenkatalog in beiden Sprachen in die Bibliothek, wie über «Hinzufügen» (immer Deutsch und
    Englisch). Gekürzte Fassungen nicht. Danach hängt «auto_link» die Sprachfassungen an dieselbe Karte, wo das eindeutig ist. «author_auto» hält fest, was so aufgenommen wurde: Wer eine Serie wieder
    entfernt, bekommt sie nicht nochmals."""
    if db.setting("auto_author_series") != "1":
        return "Ausgeschaltet"
    from . import abs as abs_mod
    c = db.conn()
    links = {r["abs_user_id"]: r["id"] for r in c.execute("SELECT id, abs_user_id FROM user WHERE abs_user_id IS NOT NULL")}
    heard: dict[str, tuple[int, str]] = {}  # Autor -> Benutzer, der ein Buch fertig gehört hat, Name
    for r in c.execute("SELECT b.author, b.lang, p.user_id, NULL abs_user_id FROM user_progress p JOIN book b ON b.id=p.book_id"
                       " WHERE p.finished=1 AND b.author IS NOT NULL UNION ALL"
                       " SELECT b.author, b.lang, NULL, l.abs_user_id FROM listen_progress l JOIN book b ON b.abs_item_id=l.item_id"
                       " WHERE l.finished=1 AND b.author IS NOT NULL"):
        name = abs_mod.primary_author(r["author"])
        key = db.norm(name)
        if key:
            heard.setdefault(key, (r["user_id"] or links.get(r["abs_user_id"]) or 1, name))
    todo = []
    for author, (user_id, name) in sorted(heard.items()):
        for a in c.execute("SELECT * FROM author_catalog WHERE author_norm=? ORDER BY lang, book_count DESC", (author,)):
            lang = a["lang"]
            if SKIP.search(a["series_title"]) or c.execute("SELECT 1 FROM author_auto WHERE lang=? AND series_asin=?", (lang, a["series_asin"])).fetchone():
                continue
            if c.execute("SELECT 1 FROM edition WHERE lang=? AND (audible_asin=? OR norm=?)",
                         (lang, a["series_asin"], db.norm(a["series_title"]) or a["series_title"].lower())).fetchone() \
                    or c.execute("SELECT 1 FROM watchlist WHERE asin=?", (a["series_asin"],)).fetchone():
                continue  # schon in der Bibliothek oder hinzugefügt; ob überwacht, bleibt wie es ist
            todo.append((a, author, name, lang, user_id))
    done = 0
    for i, (a, author, name, lang, user_id) in enumerate(todo, 1):
        progress(f"{i} von {len(todo)}: «{a['series_title']}»")
        c.execute("INSERT INTO watchlist(asin, lang, title, author, series, cover_url, kind, added_at, user_id, state) VALUES(?,?,?,?,?,?,'series',?,?,'pending')",
                  (a["series_asin"], lang, a["series_title"], name, a["series_title"], a["cover_url"], _now(), user_id))
        c.execute("INSERT OR IGNORE INTO author_auto VALUES(?,?,?,?)", (lang, a["series_asin"], author, _now()))
        try:
            process(a["series_asin"])  # scheitert Audible, holt «Hinzugefügtes aufnehmen» es nach
            done += 1
        except Exception:
            continue
    linked = 0
    if done:
        linked = audible.auto_link()
        # zusammengelegte Karten: Einträge zeigen auf die Karte, an der die Fassung jetzt hängt
        c.execute("UPDATE watchlist SET series_id=(SELECT e.series_id FROM edition e WHERE e.audible_asin=watchlist.asin AND e.lang=watchlist.lang LIMIT 1)"
                  " WHERE kind='series' AND EXISTS (SELECT 1 FROM edition e WHERE e.audible_asin=watchlist.asin AND e.lang=watchlist.lang)")
    if done and db.setting("auto_search") == "1":
        from . import tasks
        tasks.request("auto")
    return f"{done} von {len(todo)} Serien gehörter Autoren aufgenommen" + (f", {linked} Sprachfassungen verknüpft" if linked else "") if todo else "Keine neuen Serien gehörter Autoren"


def remove(asin: str, user: dict) -> None:
    """Eintrag entfernen. Was dadurch neu in die Bibliothek kam und wovon noch nichts geladen ist, verschwindet wieder;
    Bücher im Bestand und laufende Downloads bleiben unberührt."""
    c = db.conn()
    w = c.execute("SELECT * FROM watchlist WHERE asin=?", (asin,)).fetchone()
    if not w:
        return
    if user["role"] != "admin" and (w["user_id"] or 1) != user["id"]:
        raise PermissionError("Diesen Eintrag hat jemand anderes hinzugefügt")
    c.execute("BEGIN IMMEDIATE")
    try:
        if w["book_id"]:
            busy = c.execute(f"SELECT 1 FROM download WHERE book_id=? AND state IN {ACTIVE}", (w["book_id"],)).fetchone()
            b = c.execute("SELECT owned, edition_id FROM book WHERE id=?", (w["book_id"],)).fetchone()
            if b and not b["owned"] and not b["edition_id"] and not busy:
                for table in ("book_search", "book_result", "book_reject", "book_hide", "ebook_search"):
                    c.execute(f"DELETE FROM {table} WHERE book_id=?", (w["book_id"],))
                c.execute("DELETE FROM book WHERE id=?", (w["book_id"],))
            elif b:
                c.execute("UPDATE book SET wanted=NULL WHERE id=?", (w["book_id"],))
        if w["series_id"] and w["kind"] == "series":
            e = c.execute("SELECT id FROM edition WHERE series_id=? AND audible_asin=? AND link_source='added'", (w["series_id"], asin)).fetchone()
            if e and not c.execute("SELECT 1 FROM book WHERE edition_id=? AND (owned=1 OR pre_path IS NOT NULL)", (e["id"],)).fetchone() \
                    and not c.execute(f"SELECT 1 FROM download d JOIN book b ON b.id=d.book_id WHERE b.edition_id=? AND d.state IN {ACTIVE}", (e["id"],)).fetchone():
                for table in ("book_search", "book_result", "book_reject", "book_hide", "ebook_search"):
                    c.execute(f"DELETE FROM {table} WHERE book_id IN (SELECT id FROM book WHERE edition_id=?)", (e["id"],))
                c.execute("DELETE FROM book WHERE edition_id=?", (e["id"],))
                c.execute("DELETE FROM edition WHERE id=?", (e["id"],))
                c.execute("DELETE FROM series WHERE id=? AND id NOT IN (SELECT series_id FROM edition)", (w["series_id"],))
                c.execute("DELETE FROM follow WHERE kind='series' AND key=? AND key NOT IN (SELECT CAST(id AS TEXT) FROM series)", (str(w["series_id"]),))
        c.execute("DELETE FROM watchlist WHERE asin=?", (asin,))
        c.execute("COMMIT")
    except Exception:
        c.execute("ROLLBACK")
        raise


def listing() -> list[dict]:
    """Alles Hinzugefügte mit seinem Stand in der Bibliothek."""
    c = db.conn()
    names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM user")}
    out = []
    for w in c.execute("SELECT * FROM watchlist ORDER BY added_at DESC").fetchall():
        d = {**dict(w), "user": names.get(w["user_id"] or 1), "status": "pending", "text": w["message"] or "Wird in die Bibliothek aufgenommen"}
        if w["state"] == "error":
            d["status"] = "error"
        if w["state"] == "done" and w["series_id"]:
            r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(b.owned),0) own, COALESCE(SUM(b.owned=0 AND COALESCE(b.release_date,'')>date('now')),0) up"
                          " FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=?", (w["series_id"],)).fetchone()
            total = r["n"] - r["up"]
            d["status"] = "owned" if total and r["own"] >= total else "searching"
            d["text"] = f"{r['own']} von {total} Bänden im Bestand" + (f", {r['up']} angekündigt" if r["up"] else "")
            if not c.execute("SELECT 1 FROM series WHERE id=?", (w["series_id"],)).fetchone():
                d.update(status="error", text="Die Serie gibt es in der Bibliothek nicht mehr", series_id=None)
        elif w["state"] == "done" and w["book_id"]:
            b = c.execute("SELECT owned, release_date FROM book WHERE id=?", (w["book_id"],)).fetchone()
            dl = c.execute(f"SELECT state FROM download WHERE book_id=? AND state IN {ACTIVE}", (w["book_id"],)).fetchone()
            if not b:
                d.update(status="error", text="Das Buch gibt es in der Bibliothek nicht mehr", book_id=None)
            elif b["owned"]:
                d.update(status="owned", text="Im Bestand")
            elif dl:
                d.update(status="downloading" if dl["state"] in ("queued", "downloading") else "review",
                         text="Lädt herunter" if dl["state"] in ("queued", "downloading") else "Heruntergeladen, wird geprüft und abgelegt")
            elif (b["release_date"] or "") > datetime.now().date().isoformat():
                d.update(status="upcoming", text="Noch nicht erschienen")
            else:
                d.update(status="searching", text="Wird gesucht")
        out.append(d)
    return out
