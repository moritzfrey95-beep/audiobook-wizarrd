"""Geführte und automatische Suche, Start von Downloads und deren Verfolgung."""
import json
import secrets
import threading
from datetime import date, datetime, timedelta, timezone

from pathlib import Path

from . import bundle, config, db, importer, integrity, plugins, scoring, torrent, usenet, verify

SOURCE_ORDER = {"usenet": 0, "audiobookbay": 1}  # Erweiterungen danach, nach ihrem «order»


def source_rank(key: str) -> int:
    src = plugins.get(key)
    return SOURCE_ORDER.get(key, getattr(src, "order", 5) if src else 9)


def can_start() -> bool:
    return usenet.sab_configured() or torrent.configured() or any(s.can_start() for s in plugins.sources())

ACTIVE = ("queued", "downloading", "arrived", "verified", "review", "imported")
BLOCKING = ("failed", "rejected")  # diese Releases werden nicht noch einmal angeboten
DECLINED_PENALTY = 5  # Abzug für einen Treffer, den du mit «Kein Treffer passt» schon abgelehnt hast
HIDE_DAYS = 30  # «1 Monat ausblenden»: so lange fehlt ein Treffer in der Auswahl und die Automatik lässt ihn liegen
_auto: dict[int, dict] = {}  # Serien-ID -> Stand der automatischen Suche


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def book_ctx(book_id: int) -> tuple[dict, list[str]]:
    c = db.conn()
    b = c.execute("SELECT b.*, e.name series, CASE WHEN e.drama=1 OR e.variant IN ('BBC-Hörspiel','Hörspiel','GraphicAudio') THEN 1 ELSE e.drama END drama_series FROM book b LEFT JOIN edition e ON e.id=b.edition_id WHERE b.id=?", (book_id,)).fetchone()
    if not b:
        raise LookupError("Buch nicht gefunden")
    siblings = [r["title"] for r in c.execute("SELECT title FROM book WHERE edition_id=? AND id<>?", (b["edition_id"], book_id))] if b["edition_id"] else []
    book = dict(b)
    if b["edition_id"]:
        # Laufzeiten der ganzzahligen Bände: für Sammelbände, die mehrere Bände enthalten
        book["seq_runtimes"] = {k: r["runtime_min"] for r in c.execute(
            "SELECT sequence, COALESCE(audible_min, runtime_min) runtime_min FROM book WHERE edition_id=? AND COALESCE(audible_min, runtime_min)>0",
            (b["edition_id"],))
            if (k := scoring.seq_key(r["sequence"])) and k.isdigit()}
        # Titel der ganzzahligen Bände: Sammelbände, die ihre Bände nur mit Titeln nennen
        book["seq_titles"] = {k: r["title"] for r in c.execute("SELECT sequence, title FROM book WHERE edition_id=?", (b["edition_id"],))
                              if (k := scoring.seq_key(r["sequence"])) and k.isdigit()}
        # Gleicher Titel in der anderen Sprachfassung der Serie: ohne Sprachkennzeichen ist ein Treffer nie sicher
        head = lambda t: " ".join(sorted(scoring.significant(t.split(":")[0].split("(")[0])))
        others = c.execute("SELECT b.title FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=(SELECT series_id FROM edition WHERE id=?)"
                           " AND e.lang<>?", (b["edition_id"], b["lang"])).fetchall()
        book["same_title_other_lang"] = head(b["title"]) in {head(o["title"]) for o in others}
        from . import variants
        book.update(variants.book_context(c, b))  # Sprecher-Fassung: gewünschter und andere Sprecher
    return book, siblings


def book_info(book: dict) -> dict:
    """Angaben zum gesuchten Band für den Kopf der Trefferauswahl, immer frisch aus der Datenbank."""
    return {k: book.get(k) for k in ("id", "title", "author", "narrator", "sequence", "series", "lang", "release_date", "runtime_min")}


def hidden(book_id: int) -> dict[str, str]:
    """Von dir ausgeblendete Treffer dieses Bandes, deren Monat noch läuft: guid -> bis wann."""
    return {r["guid"]: r["until"] for r in db.conn().execute("SELECT guid, until FROM book_hide WHERE book_id=? AND until>?", (book_id, _now()))}


def hide(book_id: int, guid: str) -> str:
    """Treffer für einen Monat ausblenden: nicht in der Liste, nie automatisch gestartet."""
    until = (datetime.now(timezone.utc) + timedelta(days=HIDE_DAYS)).isoformat()
    db.conn().execute("INSERT OR REPLACE INTO book_hide(book_id, guid, until) VALUES(?,?,?)", (book_id, guid, until))
    return until


def unhide(book_id: int, guid: str) -> None:
    db.conn().execute("DELETE FROM book_hide WHERE book_id=? AND guid=?", (book_id, guid))


def rejected_reasons(book_id: int) -> dict[str, str]:
    """Treffer (GUID), die für diesen Band nicht mehr geladen werden sollen, mit dem Grund. Gescheitert oder durchgefallen gilt je Band;
    ein Sammelpack, das nach Länge und Aufbau eine andere Aufnahme ist, gilt für die ganze Serie (er passt auf alle ihre Bände nicht)."""
    c = db.conn()
    out = {r["guid"]: r["error"] or "Download ist gescheitert oder bei der Prüfung durchgefallen"
           for r in c.execute("SELECT guid, error FROM download WHERE book_id=? AND state IN ('failed','rejected') AND guid IS NOT NULL ORDER BY id", (book_id,))}
    for r in c.execute("SELECT d.guid, d.error FROM download d JOIN book b ON b.id=d.book_id WHERE d.state='rejected' AND d.guid IS NOT NULL"
                       " AND d.checks LIKE '%\"kind\": \"pack_other\"%' AND b.edition_id=(SELECT edition_id FROM book WHERE id=?) ORDER BY d.id", (book_id,)):
        out[r["guid"]] = r["error"]
    return out


def _mark_blocked(book_id: int, results: list[dict]) -> None:
    """Gescheiterte Treffer gelten als verworfen, von dir abgelehnte bekommen einen Abzug (beide bleiben sichtbar)."""
    c = db.conn()
    failed = rejected_reasons(book_id)
    declined = {r["guid"] for r in c.execute("SELECT guid FROM book_reject WHERE book_id=?", (book_id,))}
    hid = hidden(book_id)
    for r in results:
        r.pop("declined", None)
        r.pop("hidden_until", None)
        if r["guid"] in hid:
            r["hidden_until"] = hid[r["guid"]]
        if r["verdict"] == "verworfen" and r["score"] == 0:
            continue
        if r["guid"] in failed:
            r.update(verdict="verworfen", score=0)
            r["reasons"].insert(0, {"points": -100, "text": "Schon einmal geladen und verworfen: " + (failed[r["guid"]] or "")[:240]})
        elif r["guid"] in declined:
            # Kein hartes Aus mehr, nur ein Abzug. Automatisch gestartet wird er trotzdem nie.
            r["declined"] = True
            r["score"] = max(0, r["score"] - DECLINED_PENALTY)
            r["reasons"].insert(0, {"points": -DECLINED_PENALTY, "text": "Von dir schon einmal abgelehnt"})
            if r["verdict"] == "sicher":
                r["verdict"] = "unsicher"
            if r["verdict"] == "unsicher" and r["score"] < 40:  # gleiche Grenze wie in scoring.score
                r["verdict"] = "verworfen"


def open_unsure(results: list[dict]) -> int:
    """Unsichere Treffer, die noch niemand abgelehnt oder ausgeblendet hat: nur sie bringen einen Band unter «Treffer wählen»."""
    return sum(1 for r in results if r["verdict"] == "unsicher" and not r.get("declined") and not r.get("hidden_until"))


def _listed(book_id: int) -> bool:
    """Steht der Band unter «Treffer wählen»?"""
    return bool(db.conn().execute("SELECT 1 FROM book_search WHERE book_id=? AND last_result='unsure'", (book_id,)).fetchone())


def dismiss(book_id: int) -> int:
    """«Kein Treffer passt»: alle derzeit unsicheren Treffer dieses Bandes ablehnen. Der Band wird weiter gesucht,
    erscheint aber erst wieder unter «Treffer wählen», wenn ein neuer unsicherer Treffer auftaucht."""
    c = db.conn()
    row = c.execute("SELECT payload FROM book_result WHERE book_id=?", (book_id,)).fetchone()
    results = json.loads(row["payload"])["results"] if row else []
    n = 0
    for r in results:
        if r["verdict"] == "unsicher":
            n += c.execute("INSERT OR IGNORE INTO book_reject(book_id, guid, at) VALUES(?,?,?)", (book_id, r["guid"], _now())).rowcount
    c.execute("UPDATE book_search SET last_result='none', unsure=0 WHERE book_id=?", (book_id,))
    return n


def best_sure(results: list[dict]) -> dict | None:
    """Treffer für die Automatik: der beste sichere, den du nicht ausgeblendet hast."""
    return next((r for r in results if r["verdict"] == "sicher" and not r.get("hidden_until")), None)


def _sure_single(book: dict, siblings: list[str], results: list[dict], blocked: set[str]) -> bool:
    """Gibt es schon einen sicheren Einzelband-Treffer, der weder gescheitert noch von dir abgelehnt ist?"""
    for r in results:
        if r["guid"] in blocked:
            continue
        s = scoring.score(book, r, siblings)
        if s["verdict"] == "sicher" and not s.get("bundle"):
            return True
    return False


def guided(book_id: int, fresh: bool = True, early_stop: bool = False) -> dict:
    """Alle Treffer der eingeschalteten Quellen mit Punktzahl und Begründung, beste zuerst.

    fresh=False zeigt die zuletzt gespeicherte Suche, falls es eine gibt (ohne die Quellen neu zu fragen).
    early_stop=True (Automatik): aufhören, sobald ein sicherer Einzelband-Treffer da ist,
    also keine weiteren Suchbegriffe und Quellen. Die Suche von Hand zeigt weiter alle Treffer.
    """
    book, siblings = book_ctx(book_id)
    c = db.conn()
    if not fresh:
        row = c.execute("SELECT searched_at, payload FROM book_result WHERE book_id=?", (book_id,)).fetchone()
        if row:
            out = json.loads(row["payload"])
            for r in out["results"]:  # mit den heutigen Regeln neu bewerten, ohne die Quellen zu fragen
                r.update(scoring.score(book, r, siblings))
            _mark_blocked(book_id, out["results"])
            out["results"].sort(key=lambda r: ({"sicher": 0, "unsicher": 1, "verworfen": 2}[r["verdict"]],
                                             bool(r.get("bundle")) if r["verdict"] == "sicher" else 0, -r["score"]))
            return {**out, "book": book_info(book), "searched_at": row["searched_at"], "cached": True, "listed": _listed(book_id),
                    "can_start": can_start()}
    sources = [{"key": "usenet", "name": "Usenet", "active": usenet.configured()},
               {"key": "audiobookbay", "name": "AudioBookBay", "active": torrent.configured()}]
    sources += [{"key": s.key, "name": s.name, "active": plugins.active_for(s, book)} for s in plugins.sources()]
    enough = None
    if early_stop:
        blocked = set(rejected_reasons(book_id)) | {r["guid"] for r in c.execute("SELECT guid FROM book_reject WHERE book_id=?", (book_id,))} | set(hidden(book_id))
        enough = lambda found: _sure_single(book, siblings, found, blocked)
    results, errors = usenet.search(book, enough=enough) if usenet.configured() else ([], ["Usenet ist nicht eingerichtet"])
    paused = usenet.paused() if usenet.configured() else []
    if torrent.configured() and not (enough and enough(results)):
        more, errs = torrent.search(usenet.abb_queries(book))
        results += more
        errors += errs
    # Erweiterte Quellen (meist langsame Seiten) nur fragen, wenn bis hier nichts Sicheres gefunden ist
    hid = hidden(book_id)
    def sure() -> bool:
        return any(scoring.score(book, r, siblings)["verdict"] == "sicher" for r in results if r["guid"] not in hid)
    q = usenet.slow_queries(book)
    for src in plugins.sources():
        if plugins.active_for(src, book) and not sure():
            try:
                more, errs = src.search(q)
            except Exception as exc:  # noqa: BLE001 - eine Erweiterung darf die Suche nicht abbrechen
                more, errs = [], [f"{src.name}: {exc}"]
            results += more
            errors += errs
    for r in results:
        r.update(scoring.score(book, r, siblings))
    _mark_blocked(book_id, results)
    order = {"sicher": 0, "unsicher": 1, "verworfen": 2}
    # bei sicheren Treffern: Einzelband vor Sammelband, dann die Quellenreihenfolge
    results.sort(key=lambda r: (order[r["verdict"]], bool(r.get("bundle")) if r["verdict"] == "sicher" else 0, source_rank(r["source"]) if r["verdict"] == "sicher" else 0,
                                -r["score"], r.get("age_days") or 0))
    out = {"book": book_info(book),
           "results": results, "errors": errors, "sources": sources, "queries": usenet.queries(book), "paused": paused}
    # Treffer aufbewahren (ohne Download-Adressen), damit sie später sofort wieder da sind
    c.execute("INSERT OR REPLACE INTO book_result(book_id, searched_at, payload) VALUES(?,?,?)", (book_id, _now(), json.dumps(out)))
    # Liste «Treffer wählen» nachführen: zeigt die frische Suche keinen unsicheren Treffer mehr, fällt der Band heraus
    unsure = open_unsure(results)
    if unsure:
        c.execute("UPDATE book_search SET unsure=? WHERE book_id=? AND last_result='unsure'", (unsure, book_id))
    elif not errors:
        c.execute("UPDATE book_search SET last_result='none', unsure=0 WHERE book_id=? AND last_result='unsure'", (book_id,))
    return {**out, "searched_at": _now(), "cached": False, "listed": _listed(book_id),
            "can_start": can_start()}


def ensure_release(book: dict, release: dict) -> None:
    """Die Download-Adresse eines gespeicherten Treffers liegt nur kurz im Speicher: bei Bedarf frisch holen."""
    source, guid = release.get("source") or "usenet", release["guid"]
    try:
        if source == "usenet" and not usenet.known(guid):
            usenet.search(book)
        elif source == "audiobookbay" and not torrent.known(guid):
            torrent.search(usenet.abb_queries(book))
        elif plugins.get(source):
            plugins.get(source).refresh(guid)
    except Exception as exc:
        raise LookupError(f"Der Treffer liess sich bei der Quelle nicht mehr abrufen ({exc}). Bitte neu suchen.")
    if (source == "usenet" and not usenet.known(guid)) or (source == "audiobookbay" and not torrent.known(guid)):
        raise LookupError("Diesen Treffer gibt es bei der Quelle nicht mehr. Bitte neu suchen.")


def active_download(book_id: int):
    q = ",".join("?" * len(ACTIVE))
    return db.conn().execute(f"SELECT * FROM download WHERE book_id=? AND state IN ({q})", (book_id, *ACTIVE)).fetchone()


def grab(book_id: int, release: dict, auto: bool = False) -> int:
    book, _ = book_ctx(book_id)
    damaged = bool(book["owned"]) and integrity.is_damaged(book_id)  # Ersatz für ein beschädigtes Buch im Bestand
    if book["owned"] and not damaged:
        raise ValueError("Dieses Buch ist schon im Bestand")
    have = db.conn().execute("SELECT o.title, o.sequence, e.name FROM book_twin t JOIN book o ON o.id=t.twin_id LEFT JOIN edition e ON e.id=o.edition_id"
                             " WHERE t.id=? AND o.owned=1", (book_id,)).fetchone()
    if have:
        where = f"«{have['name']}», Band {have['sequence']}" if have["name"] and have["sequence"] else f"«{have['title']}»"
        raise ValueError(f"Dieses Hörbuch liegt schon im Bestand ({where}). Audible führt es doppelt.")
    if active_download(book_id) or db.conn().execute(f"SELECT 1 FROM ({db.BUSY_TWIN}) WHERE id=?", (book_id,)).fetchone():
        raise ValueError("Für dieses Buch läuft schon ein Download")
    if release.get("guid") and (why := rejected_reasons(book_id).get(release["guid"])):
        raise ValueError(f"Dieser Treffer wurde schon geladen und bei der Prüfung verworfen, ein zweiter Download bringt dasselbe: {why[:300]}")
    # Ein Sammelband passt auf mehrere Bände: derselbe Treffer wird nur einmal geladen und dann aufgeteilt
    if release.get("guid") and db.conn().execute(
            "SELECT 1 FROM download WHERE guid=? AND bundle IS NOT NULL AND state IN ('queued','downloading','arrived','review')", (release["guid"],)).fetchone():
        raise ValueError("Dieser Sammelband lädt schon und wird danach aufgeteilt")
    marker = f"abw-{book_id}-{secrets.token_hex(4)}"
    source = release.get("source") or "usenet"
    ensure_release(book, release)
    if source == "audiobookbay":
        nzo = torrent.submit(release["guid"], marker)
    elif plugins.get(source):
        nzo = plugins.get(source).submit(release["guid"], marker)
    else:
        nzo = usenet.submit(usenet.fetch_nzb(release["guid"]), marker)
    span = scoring.score(book, release, _)["bundle"]  # selbst bestimmen, nicht der Oberfläche glauben
    return db.conn().execute(
        "INSERT INTO download(book_id, source, indexer, release_title, guid, size, score, reasons, marker, client_id, state,"
        " progress, auto, created, updated, bundle, replaces) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (book_id, source, release.get("indexer"), release["title"], release["guid"], release.get("size"), release.get("score"),
         json.dumps(release.get("reasons") or []), marker, nzo, "queued", 0, int(auto), _now(), _now(),
         json.dumps(span) if span else None, 1 if damaged else None)).lastrowid


def track(progress=lambda msg: None) -> str:
    """Stand der laufenden Downloads aus SABnzbd holen."""
    c = db.conn()
    rows = c.execute("SELECT * FROM download WHERE state IN ('queued','downloading')").fetchall()
    if not rows:
        return "Keine laufenden Downloads"
    st: dict = {}
    nzb = [r for r in rows if r["source"] == "usenet"]
    if nzb and usenet.sab_configured():
        by_id = usenet.status({r["client_id"] for r in nzb})
        st.update({r["id"]: by_id[r["client_id"]] for r in nzb if r["client_id"] in by_id})
    tor = [dict(r) for r in rows if r["source"] == "audiobookbay"]
    if tor and torrent.configured():
        st.update(torrent.status(tor))
    for src in plugins.sources():
        own = [dict(r) for r in rows if r["source"] == src.key]
        if own and src.configured():
            try:
                st.update(src.status(own))
            except Exception:  # noqa: BLE001 - Client gerade nicht erreichbar: beim nächsten Lauf wieder
                pass
    done = failed = 0
    for r in rows:
        s = st.get(r["id"])
        if not s:
            continue
        c.execute("UPDATE download SET state=?, progress=?, path=COALESCE(?, path), error=?, updated=? WHERE id=?",
                  (s["state"], s.get("progress", r["progress"]), s.get("path"), s.get("error"), _now(), r["id"]))
        done += s["state"] == "arrived"
        failed += s["state"] == "failed"
    return f"{len(rows)} laufende Downloads, {done} angekommen, {failed} fehlgeschlagen"


def downloads(limit: int = 100) -> list[dict]:
    rows = db.conn().execute(
        "SELECT d.*, b.title book_title, b.sequence, b.lang, b.owned, e.name series, e.series_id FROM download d JOIN book b ON b.id=d.book_id"
        " LEFT JOIN edition e ON e.id=b.edition_id ORDER BY d.id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = {**dict(r), "reasons": json.loads(r["reasons"] or "[]"), "checks": json.loads(r["checks"] or "[]"), "plan": None,
             "import_enabled": config.IMPORT_ENABLED}
        if r["state"] in ("verified", "review"):
            try:
                book, _ = book_ctx(r["book_id"])
                d["plan"] = importer.plan(book, d)
                if r["replaces"]:
                    d["plan"] = importer.replace_plan(book, d)
                if r["bundle"]:
                    d["plan"]["problems"].insert(0, "Sammelband: nicht aufgeteilt, als einzelner Band wird er nicht abgelegt")
                    d["plan"]["overwritable"] = False
            except Exception as exc:
                d["plan"] = {"problems": [str(exc)[:200]], "files": [], "bytes": 0, "shown": "", "target": "", "new_series_folder": False}
        if r["state"] == "split":
            # Ergebnis der Aufteilung: Zustand jedes entstandenen Einzelbands
            d["children"] = [dict(k) for k in db.conn().execute(
                "SELECT d.id, d.state, d.error, b.title, b.sequence FROM download d JOIN book b ON b.id=d.book_id WHERE d.parent_id=? ORDER BY b.seq, d.id", (r["id"],))]
        kind = next((x.get("kind") for x in d["checks"] if x.get("name") == "Laufzeit" and x.get("kind")), None)
        if kind:
            d["finding"] = kind
        out.append(d)
    return out


def auto_file() -> int:
    """Sichere Fälle ablegen bzw. ersetzen. Läuft vor, während und nach der Prüfung, damit nichts auf einen langen Durchlauf wartet."""
    if db.setting("auto_import") != "1":
        return 0
    c, auto = db.conn(), 0
    # Sicher = Prüfung ohne Hinweis bestanden; gilt auch für von Hand gestartete Downloads.
    # Bände, die einen vorhandenen ersetzen würden, und Sammelbände bleiben beim Knopf.
    for r in c.execute("SELECT id FROM download WHERE state='verified' AND bundle IS NULL AND COALESCE(replaces, 0)=0").fetchall():
        try:
            import_download(r["id"])
            auto += 1
        except Exception as exc:  # z. B. Zielordner existiert: ab in die Prüfliste
            c.execute("UPDATE download SET state='review', error=?, updated=? WHERE id=?", (f"Ablage nicht möglich: {exc}"[:300], _now(), r["id"]))
    # Eindeutig bessere Fassungen ersetzen den Bestand automatisch (alt geht in den Papierkorb)
    for r in c.execute("SELECT id, book_id, checks FROM download WHERE state='review' AND replaces=1 AND bundle IS NULL AND error IS NULL").fetchall():
        book, _ = book_ctx(r["book_id"])
        if not clear_upgrade(json.loads(r["checks"] or "[]"), book):
            continue
        try:
            replace_download(r["id"])
            auto += 1
        except Exception as exc:  # bleibt unter «Entscheiden», wird nicht jede Minute neu versucht
            c.execute("UPDATE download SET error=?, updated=? WHERE id=?", (f"Ersetzen nicht möglich: {exc}"[:300], _now(), r["id"]))
    return auto


def verify_arrived(progress=lambda msg: None) -> str:
    """Angekommene Downloads prüfen. Durchgefallene gelten als falsch und werden neu gesucht."""
    c = db.conn()
    auto = auto_file()
    rows = c.execute("SELECT * FROM download WHERE state='arrived'").fetchall()
    res = {"passed": 0, "review": 0, "rejected": 0}
    for r in rows:
        # Sammelband-Erkennung je Download direkt vor seiner Prüfung, nicht vorab für alle: sonst wartet das erste Buch,
        # bis jeder angekommene Download gemessen ist. Die Laufzeiten bleiben für die Prüfung zwischengespeichert.
        if r["bundle"] is None and r["parent_id"] is None:
            hidden_bundle(dict(r))
            r = c.execute("SELECT * FROM download WHERE id=?", (r["id"],)).fetchone()
        if r["bundle"] is not None:
            progress(f"Teilt den Sammelband «{r['release_title'][:60]}» auf")
            split_bundle(dict(r))
            rows += c.execute("SELECT * FROM download WHERE parent_id=? AND state='arrived'", (r["id"],)).fetchall()  # Einzelbände gleich mitprüfen
            continue
        book, siblings = book_ctx(r["book_id"])
        progress(f"Prüft «{book['title']}»")
        v = verify.verify(book, importer.source_dir(dict(r)), release=r["release_title"] or "", siblings=siblings)
        state = {"passed": "verified", "review": "review", "rejected": "rejected"}[v["verdict"]]
        error = next((x["text"] for x in v["checks"] if x["status"] == "fail"), None)
        if r["replaces"] and v["verdict"] != "rejected":
            # Band liegt schon im Bestand: Zusatzprüfung, ob der neue wirklich besser ist. Eindeutige Fälle ersetzt auto_file.
            try:
                cmp = importer.compare(book, importer.source_dir(dict(r)))
            except Exception as exc:
                cmp = {"better": False, "text": f"Vergleich mit dem Bestand nicht möglich: {exc}"[:300]}
            if v["verdict"] != "passed":
                cmp = {"better": False, "text": "Die Ankunftsprüfung ist nicht einwandfrei, das Buch im Bestand bleibt. " + cmp["text"]}
            v["checks"].append({"name": "Vergleich mit Bestand", "status": "warn" if cmp["better"] else "fail", "text": cmp["text"]})
            state, error = ("review", None) if cmp["better"] else ("rejected", cmp["text"])
            v["verdict"] = "review" if cmp["better"] else "rejected"
        elif r["misfit"]:
            # Aus einem Sammelband nach Titel herausgelöst, obwohl die Laufzeit nicht passt: nie automatisch ablegen oder verwerfen,
            # sondern einzeln unter «Entscheiden»
            v["checks"].append({"name": "Sammelband", "status": "warn", "text": "Nach Titel aus dem Sammelband herausgelöst, die Laufzeit passt "
                                "nicht zu Audible. Bitte von Hand ablegen oder verwerfen."})
            state, v["verdict"] = "review", "review"
        c.execute("UPDATE download SET state=?, checks=?, verified_at=?, updated=?, error=? WHERE id=?",
                  (state, json.dumps(v["checks"]), _now(), _now(), error, r["id"]))
        res[v["verdict"]] += 1
        if v["verdict"] != "rejected":
            auto += auto_file()  # gleich ablegen, nicht erst nach dem ganzen Durchlauf
        still = c.execute("SELECT state FROM download WHERE id=?", (r["id"],)).fetchone()["state"]
        if v["verdict"] == "review" and still == "review":
            from . import notify
            notify.send("Bitte prüfen", f"{book['title']}: {next((x['text'] for x in v['checks'] if x['status'] == 'warn'), 'Prüfung mit Hinweisen')[:160]}", "/pruefen", "eyes")
    auto += auto_file()
    # Abgelegte Bücher abschliessen, sobald Audiobookshelf sie kennt
    if c.execute("SELECT 1 FROM download WHERE state='imported'").fetchone():
        from . import abs as abs_mod
        try:
            abs_mod.sync()
        except Exception:
            # Ein abgebrochener Abgleich darf keine offene Transaktion hinterlassen, sonst scheitert das UPDATE darunter
            if c.in_transaction:
                c.execute("ROLLBACK")
    c.execute("UPDATE download SET state='done', updated=? WHERE state='imported' AND book_id IN (SELECT id FROM book WHERE owned=1)", (_now(),))
    if auto and not rows:
        return f"{auto} automatisch abgelegt"
    if not rows:
        return "Nichts zu prüfen"
    return f"{len(rows)} geprüft: {res['passed']} bestanden, {res['review']} zur Prüfliste, {res['rejected']} durchgefallen, {auto} automatisch abgelegt"


def clear_upgrade(checks: list[dict], book: dict) -> bool:
    """Eindeutig besser: jeder Prüfpunkt ohne Hinweis, der Vergleich spricht für den neuen Band, und das Buch wird gerade
    nicht gehört (sonst entscheidet der Administrator, ob der Hörfortschritt das Ersetzen überstehen muss)."""
    base = [x for x in checks if x["name"] != "Vergleich mit Bestand"]
    cmp = [x for x in checks if x["name"] == "Vergleich mit Bestand"]
    listening = 0 < (book.get("progress") or 0) < 1 and not book.get("finished") or bool(book.get("id") and db.conn().execute(
        "SELECT 1 FROM user_progress WHERE book_id=? AND progress>0 AND progress<1 AND finished=0", (book["id"],)).fetchone())
    return bool(base) and all(x["status"] == "ok" for x in base) and len(cmp) == 1 and cmp[0]["status"] == "warn" and not listening


def ensure_catalog(book: dict) -> bool:
    """Fehlen im Katalog Bandnummern zwischen 1 und der höchsten, wird die Serie bei Audible neu abgefragt (höchstens alle 12 Stunden).
    Liefert True, wenn nachgeladen wurde."""
    from . import audible
    c = db.conn()
    if not book.get("edition_id"):
        return False
    nums = {int(k) for k in (scoring.seq_key(r["sequence"]) for r in c.execute("SELECT sequence FROM book WHERE edition_id=?", (book["edition_id"],))) if k and k.isdigit()}
    if not nums or all(n in nums for n in range(1, max(nums) + 1)):
        return False
    ed = c.execute("SELECT synced_at FROM edition WHERE id=?", (book["edition_id"],)).fetchone()
    if ed and ed["synced_at"] and datetime.fromisoformat(ed["synced_at"]) > datetime.now(timezone.utc) - timedelta(hours=12):
        return False
    return audible.sync_edition(book["edition_id"]) == "ok"


def _span_by_title(book: dict, files: list, root) -> tuple[int, int] | None:
    """Ohne passende Summe der Audible-Laufzeiten: tragen die Dateien die Titel der Bände, und passt jede Gruppe zur Laufzeit ihres Bandes,
    ist es ein Sammelband mit diesen Bänden."""
    books = bundle.catalog(book)
    titled = bundle.by_title(root, files, books) if len(files) > 1 else None
    if not titled:
        return None
    by_id = {b["id"]: b for b in books}
    if not all(bundle.fits(by_id[i], sum(verify.probe(f)["duration"] for f in fs)) for i, fs in titled[0].items()):
        return None
    seqs = [int(k) for i in titled[0] if (k := scoring.seq_key(by_id[i]["sequence"])) and k.isdigit()]
    return (min(seqs), max(seqs)) if len(seqs) >= 2 else None


def hidden_bundle(d: dict) -> None:
    """Als einzelner Band gestartet, angekommen ist ein Sammelband (oft eine einzige Datei mit allen Bänden):
    passt die Dauer eindeutig zu aufeinanderfolgenden Bänden, wird der Download als Sammelband markiert und aufgeteilt.
    Passt die Summe nicht (Lücke im Katalog, Band fehlt bei Audible), entscheiden die Titel in den Dateinamen."""
    try:
        book, _ = book_ctx(d["book_id"])
        root = importer.source_dir(d)
        files = verify.audio_files(root)
        seconds = sum(verify.probe(f)["duration"] for f in files)
        if files and seconds >= (book.get("runtime_min") or 0) * 60 * 1.5 and ensure_catalog(book):
            book, _ = book_ctx(d["book_id"])  # Katalog war lückenhaft und ist jetzt frisch
    except Exception:
        return  # die Ankunftsprüfung meldet, was mit den Dateien nicht stimmt
    span = bundle.guess_span(book, seconds, d.get("release_title") or "") if files else None
    if not span and files and seconds >= (book.get("runtime_min") or 0) * 60 * 1.5:
        try:
            span = _span_by_title(book, files, root)
        except Exception:
            span = None
    if span:
        db.conn().execute("UPDATE download SET bundle=?, updated=? WHERE id=?", (json.dumps(list(span)), _now(), d["id"]))


def split_bundle(d: dict) -> None:
    """Sammelband in Einzelbände aufteilen: je fehlendem Band ein eigener Download, der normal geprüft und abgelegt wird.
    Lässt er sich nicht sicher aufteilen, bleibt er in der Prüfliste stehen und wird nie als ein Band abgelegt."""
    c = db.conn()
    lo, hi = json.loads(d["bundle"])
    try:
        book, _ = book_ctx(d["book_id"])
        res = bundle.split(d, book, importer.source_root(d["source"]))
    except Exception as exc:
        text = f"Sammelband Band {lo}–{hi} liess sich nicht sicher aufteilen: {exc}"[:400]
        c.execute("UPDATE download SET state='review', checks=?, error=?, verified_at=?, updated=? WHERE id=?",
                  (json.dumps([{"name": "Aufteilen", "status": "fail", "text": text}]), text, _now(), _now(), d["id"]))
        from . import notify
        notify.send("Bitte prüfen", text[:200], "/pruefen", "eyes")
        return
    for ch in res["children"]:
        b = ch["book"]
        c.execute("INSERT INTO download(book_id, source, indexer, release_title, guid, size, score, reasons, marker, state, progress, auto,"
                  " created, updated, parent_id, replaces, misfit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (b["id"], d["source"], d["indexer"], d["release_title"], d["guid"], None, d["score"], d["reasons"], ch["marker"],
                   "arrived", 1, d["auto"], _now(), _now(), d["id"], int(ch["replaces"]), int(ch.get("misfit", False))))
    seqs = lambda own: ", ".join(scoring.seq_key(ch["book"]["sequence"]) for ch in res["children"] if ch["replaces"] == own)
    text = f"Sammelband Band {lo}–{hi} aufgeteilt ({res['how']})"
    if seqs(False):
        text += f": Band {seqs(False)}"
    if seqs(True):
        text += f"; schon im Bestand, wird mit dem Bestand verglichen: Band {seqs(True)}"
    if res["skipped"]:
        text += f"; schon unterwegs: Band {', '.join(map(str, res['skipped']))}"
    c.execute("UPDATE download SET state='split', checks=?, error=NULL, verified_at=?, updated=? WHERE id=?",
              (json.dumps([{"name": "Aufteilen", "status": "ok", "text": text}]), _now(), _now(), d["id"]))


def _download(download_id: int) -> dict:
    r = db.conn().execute("SELECT * FROM download WHERE id=?", (download_id,)).fetchone()
    if not r:
        raise LookupError("Download nicht gefunden")
    return dict(r)


def overwrite_download(download_id: int) -> str:
    """Von Hand ablegen, obwohl der Zielordner schon existiert: der vorhandene Ordner geht in den Papierkorb."""
    return import_download(download_id, overwrite=True)


def import_download(download_id: int, overwrite: bool = False) -> str:
    from . import abs as abs_mod
    d = _download(download_id)
    if d["state"] not in ("verified", "review"):
        raise ValueError("Dieser Download ist nicht bereit für die Ablage")
    if d.get("bundle"):
        raise ValueError("Ein Sammelband wird nicht als einzelner Band abgelegt")
    if d.get("replaces"):
        raise ValueError("Dieses Buch liegt schon im Bestand; möglich ist nur «Bestand ersetzen»")
    book, _ = book_ctx(d["book_id"])
    target = importer.execute(book, d, overwrite=overwrite)
    db.conn().execute("UPDATE download SET state='imported', imported_to=?, updated=? WHERE id=?", (target, _now(), download_id))
    try:
        abs_mod.scan(book["lang"], target)
    except Exception:
        pass  # das Buch liegt richtig; Audiobookshelf findet es beim nächsten eigenen Einlesen
    from . import notify
    where = f"{book['series']}, Band {book['sequence']}" if book.get("series") and book.get("sequence") else (book.get("series") or "Einzelbuch")
    notify.send("Neu im Bestand", f"{book['title']} ({where})", "/pruefen", "white_check_mark")
    return target


def replace_download(download_id: int) -> str:
    """Bestand ersetzen: nur von Hand, nach bestandener Ankunftsprüfung und Vergleich. Das alte Buch geht in den Papierkorb."""
    from . import abs as abs_mod
    d = _download(download_id)
    if not d.get("replaces") or d["state"] != "review":
        raise ValueError("Dieser Download ist nicht zum Ersetzen vorgesehen")
    book, _ = book_ctx(d["book_id"])
    target = importer.replace(book, d)
    db.conn().execute("UPDATE download SET state='imported', imported_to=?, updated=? WHERE id=?", (target, _now(), download_id))
    integrity.forget(book["id"])  # die neue Fassung wird bei der nächsten Nachtprüfung gelesen
    try:
        abs_mod.scan(book["lang"], target)
    except Exception:
        pass
    return target


def reject_download(download_id: int) -> None:
    d = _download(download_id)
    if d["state"] not in ("verified", "review", "arrived"):
        raise ValueError("Dieser Download lässt sich nicht mehr verwerfen")
    db.conn().execute("UPDATE download SET state='rejected', error='Von Hand verworfen', updated=? WHERE id=?", (_now(), download_id))


def recheck_download(download_id: int) -> None:
    d = _download(download_id)
    if d["state"] not in ("verified", "review", "rejected"):
        raise ValueError("Dieser Download lässt sich nicht neu prüfen")
    db.conn().execute("UPDATE download SET state='arrived', checks=NULL, error=NULL, updated=? WHERE id=?", (_now(), download_id))


def auto_one(b) -> dict:
    """Automatische Suche für ein Buch: den besten sicheren Treffer starten. Unsichere Treffer kommen unter «Treffer wählen»."""
    item = {"book_id": b["id"], "title": b["title"], "sequence": b["sequence"]}
    try:
        if active_download(b["id"]):
            item.update(result="skipped", text="Download läuft schon")
        else:
            res = guided(b["id"], early_stop=True)
            best = best_sure(res["results"])
            unsure = open_unsure(res["results"])
            c = db.conn()
            if best:
                grab(b["id"], best, auto=True)
                item.update(result="started", text=f"Gestartet: {best['title']} ({best['score']} Punkte)")
            elif unsure:
                item.update(result="unsure", text=f"{unsure} unsichere Treffer; sie stehen im Tab «Prüfen» zur Auswahl von Hand")
                # in die Liste «Treffer wählen» eintragen, ohne den Suchplan der Automatik zu verändern
                c.execute("INSERT INTO book_search(book_id, attempts, last_search, last_result, hits, unsure) VALUES(?,1,?,'unsure',?,?)"
                          " ON CONFLICT(book_id) DO UPDATE SET last_search=excluded.last_search, last_result='unsure', hits=excluded.hits, unsure=excluded.unsure",
                          (b["id"], _now(), len(res["results"]), unsure))
            else:
                item.update(result="none", text="Kein passender Treffer")
    except Exception as exc:
        item.update(result="error", text=str(exc)[:200])
    return item


_book_auto: dict[int, dict] = {}  # Buch -> Stand der einzelnen automatischen Suche


def start_book(book_id: int) -> None:
    """Automatische Suche für ein einzelnes Buch im Hintergrund."""
    b = db.conn().execute("SELECT id, title, sequence, owned FROM book WHERE id=?", (book_id,)).fetchone()
    if not b:
        raise LookupError("Buch nicht gefunden")
    if b["owned"] and not integrity.is_damaged(book_id):
        raise ValueError("Dieses Buch ist schon im Bestand")
    if _book_auto.get(book_id, {}).get("running"):
        return
    _book_auto[book_id] = {"running": True, "result": None, "text": "Sucht in allen Quellen"}

    def work():
        try:
            item = auto_one(db.conn().execute("SELECT id, title, sequence FROM book WHERE id=?", (book_id,)).fetchone())
            _book_auto[book_id] = {"running": False, "result": item["result"], "text": item["text"]}
        except Exception as exc:
            _book_auto[book_id] = {"running": False, "result": "error", "text": str(exc)[:200]}
    threading.Thread(target=work, daemon=True, name="abw-buchsuche").start()


def book_auto_state(book_id: int) -> dict:
    return _book_auto.get(book_id) or {"running": False, "result": None, "text": ""}


def auto_series(series_id: int) -> None:
    """Automatische Suche für eine Serie: je fehlendem Band den besten sicheren Treffer starten."""
    c = db.conn()
    today = date.today().isoformat()
    books = c.execute(
        "SELECT b.id, b.title, b.sequence FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=? AND b.owned=0"
        " AND (b.release_date IS NULL OR b.release_date<=?) AND COALESCE(e.monitored, (SELECT COUNT(*) FROM book o WHERE o.edition_id=e.id AND o.owned=1) > 0)"
        f" AND NOT {db.B_HAS_TWIN}"
        " ORDER BY b.seq", (series_id, today)).fetchall()
    state = _auto[series_id] = {"running": True, "total": len(books), "done": 0, "items": []}
    try:
        for b in books:
            state["items"].append(auto_one(b))
            state["done"] += 1
    finally:
        state["running"] = False


def start_auto(series_id: int) -> None:
    if _auto.get(series_id, {}).get("running"):
        return
    _auto[series_id] = {"running": True, "total": 0, "done": 0, "items": []}
    threading.Thread(target=auto_series, args=(series_id,), daemon=True).start()


def auto_state(series_id: int) -> dict | None:
    return _auto.get(series_id)


# ---------- Automatik über alle Serien ----------

WAIT_DAYS = [1, 2, 4, 7]  # Wartezeit nach erfolgloser Suche, danach wöchentlich

# Gesucht wird, was in einer überwachten Fassung fehlt, und jedes von Hand hinzugefügte Einzelbuch (wanted)
CANDIDATES = (
    "SELECT b.id, b.title FROM book b LEFT JOIN edition e ON e.id=b.edition_id LEFT JOIN series s ON s.id=e.series_id"
    " LEFT JOIN book_search q ON q.book_id=b.id"
    " WHERE ((b.owned=0 AND (b.release_date IS NULL OR b.release_date<=:today) AND COALESCE(s.auto, 1)=1"
    " AND (b.wanted IS NOT NULL OR (e.id IS NOT NULL"
    " AND COALESCE(e.monitored, (SELECT COUNT(*) FROM book o WHERE o.edition_id=e.id AND o.owned=1) > 0))))"
    # beschädigt im Bestand (Bestandsprüfung): wird gesucht wie ein fehlender Band und ersetzt das alte
    " OR (b.owned=1 AND b.id IN (SELECT book_id FROM integrity WHERE state='damaged')))"
    " AND b.id NOT IN (SELECT book_id FROM download WHERE state IN ('queued','downloading','arrived','verified','review','imported'))"
    # dasselbe Hörbuch kann unter zwei Serien oder doppelt bei Audible stehen: nicht noch einmal laden
    f" AND NOT {db.B_HAS_TWIN} AND NOT {db.B_TWIN_BUSY}"
)


def auto_run(progress=lambda msg: None) -> str:
    """Suchlauf: fällige fehlende Bände suchen, sichere Treffer sofort laden."""
    if db.setting("auto_search") != "1":
        return "Automatik ist ausgeschaltet"
    if not (usenet.configured() and usenet.sab_configured()):
        return "Usenet oder SABnzbd ist nicht eingerichtet"
    c = db.conn()
    now = _now()
    # Nach fehlgeschlagenem oder durchgefallenem Download sofort wieder suchen
    c.execute("UPDATE book_search SET next_search=? WHERE book_id IN (SELECT book_id FROM download WHERE state IN ('failed','rejected') AND updated > COALESCE(book_search.last_search, ''))", (now,))
    rows = c.execute(CANDIDATES + " AND (q.next_search IS NULL OR q.next_search<=:now) ORDER BY"
                     # Hinzugefügtes (Entdecken, Goodreads) zuerst, das zuletzt Hinzugefügte vorn
                     " COALESCE(b.wanted, s.prio) IS NULL, q.last_search IS NOT NULL, COALESCE(b.wanted, s.prio) DESC, q.last_search LIMIT :n",
                     {"today": date.today().isoformat(), "now": now, "n": int(db.setting("auto_batch"))}).fetchall()
    res = {"started": 0, "unsure": 0, "none": 0, "error": 0}
    for i, b in enumerate(rows, 1):
        progress(f"{i} von {len(rows)}: «{b['title']}»")
        attempts = (c.execute("SELECT attempts FROM book_search WHERE book_id=?", (b["id"],)).fetchone() or {"attempts": 0})["attempts"]
        hits = unsure = 0
        until = None  # Ende einer Indexer-Sperre, falls «keine Treffer» deswegen nicht endgültig ist
        try:
            found = guided(b["id"], early_stop=True)
            hits = len(found["results"])
            unsure = open_unsure(found["results"])
            best = best_sure(found["results"])
            if best:
                grab(b["id"], best, auto=True)
                result = "started"
            else:
                result = "unsure" if unsure else "none"
                if found["errors"] and not hits:
                    result = "error"
                elif found.get("paused"):
                    until = max(p["until"] for p in found["paused"])
        except Exception:
            result = "error"
        res[result] = res.get(result, 0) + 1
        if until:
            # Ein Indexer war gesperrt: kurz nach Ende der Sperre neu suchen, ohne die Wartezeit hochzuzählen
            nxt = datetime.fromisoformat(until.replace("Z", "+00:00")) + timedelta(minutes=10)
            nxt = min(nxt, datetime.now(timezone.utc) + timedelta(days=WAIT_DAYS[min(attempts, len(WAIT_DAYS) - 1)]))
            tries = attempts
        else:
            wait = timedelta(hours=1) if result in ("error", "skipped") else timedelta(days=WAIT_DAYS[min(attempts, len(WAIT_DAYS) - 1)])
            nxt, tries = datetime.now(timezone.utc) + wait, attempts + 1
        c.execute("INSERT OR REPLACE INTO book_search(book_id, attempts, last_search, next_search, last_result, hits, unsure) VALUES(?,?,?,?,?,?,?)",
                  (b["id"], tries, _now(), nxt.isoformat(), result, hits, unsure))
    if not rows:
        return "Kein Band fällig"
    return f"{len(rows)} Bände gesucht: {res['started']} gestartet, {res['unsure']} mit unsicheren Treffern, {res['none']} ohne Treffer, {res['error']} Fehler"


def unsure_books(fields: str = "b.id book_id, b.title, b.sequence, b.lang, e.name series, e.series_id, q.unsure, q.last_search") -> list[dict]:
    """Fehlende Bände mit nur unsicheren Treffern (Register «Treffer wählen»)."""
    return [dict(r) for r in db.conn().execute(
        f"SELECT {fields} FROM book_search q JOIN book b ON b.id=q.book_id"
        " LEFT JOIN edition e ON e.id=b.edition_id WHERE q.last_result='unsure' AND (b.owned=0 OR b.id IN (SELECT book_id FROM integrity WHERE state='damaged'))"
        f" AND NOT {db.B_HAS_TWIN}"
        " AND b.id NOT IN (SELECT book_id FROM download WHERE state IN ('queued','downloading','arrived','verified','review','imported'))"
        " ORDER BY q.last_search DESC LIMIT 200")]


def auto_overview() -> dict:
    c = db.conn()
    p = {"today": date.today().isoformat()}
    total = c.execute("SELECT COUNT(*) FROM (" + CANDIDATES + ")", p).fetchone()[0]
    due = c.execute("SELECT COUNT(*) FROM (" + CANDIDATES + " AND (q.next_search IS NULL OR q.next_search<=:now))", {**p, "now": _now()}).fetchone()[0]
    never = c.execute("SELECT COUNT(*) FROM (" + CANDIDATES + " AND q.book_id IS NULL)", p).fetchone()[0]
    unsure = unsure_books()
    from . import notify
    return {"auto_search": db.setting("auto_search") == "1", "auto_import": db.setting("auto_import") == "1",
            "auto_convert": db.setting("auto_convert") == "1", "auto_chapters": db.setting("auto_chapters") == "1",
            "notify": db.setting("notify") == "1", "notify_configured": notify.configured(),
            "auto_cleanup": db.setting("auto_cleanup") == "1", "auto_ebook": db.setting("auto_ebook") == "1",
            "auto_ebook_upcoming": db.setting("auto_ebook_upcoming") == "1", "auto_ebook_listening": db.setting("auto_ebook_listening") == "1", "notify_ebook": db.setting("notify_ebook") == "1", "auto_author_series": db.setting("auto_author_series") == "1",
            "auto_integrity": db.setting("auto_integrity") == "1", "integrity": integrity.stats(),
            "batch": int(db.setting("auto_batch")), "open": total, "due": due, "never": never, "unsure": unsure,
            "import_enabled": config.IMPORT_ENABLED}
