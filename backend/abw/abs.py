"""Bestand aus Audiobookshelf lesen (nur lesend)."""
import json
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import httpx

from . import config, db, library


def _client() -> httpx.Client:
    return httpx.Client(base_url=config.ABS_URL, timeout=60,
                        headers={"Authorization": f"Bearer {config.abs_token()}"})


def _iso(ms) -> str | None:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat() if ms else None


def split_series(series_name: str | None) -> tuple[str, str | None] | None:
    """'Name #3, Andere #1' -> ('Name', '3'). Nur die erste Serie zählt."""
    if not series_name:
        return None
    # Audiobookshelf hängt mehrere Serien mit Komma aneinander: "Star Wars #53, Star Wars: The Last Jedi"
    # Sammelband «Name #1&2» zählt als «1-2», nicht als eigene Serie «Name #1&2»
    m = re.match(r"^(.*?)\s+#\s*([\w.\-]+(?:\s*&\s*[\w.\-]+)*)\s*(?:,\s.*)?$", series_name.strip())
    return (m.group(1).strip(), re.sub(r"\s*&\s*", "-", m.group(2))) if m else (series_name.strip(), None)


ROLE = re.compile(r"\s+-\s+(translator|editor|introduction|foreword|afterword|illustrator|contributor|adapter|übersetzer|herausgeber)\b.*$", re.I)


def primary_author(name: str | None) -> str:
    """Erster Name ohne Rollenangabe wie 'X - translator'."""
    parts = [p.strip() for p in (name or "").split(",") if p.strip()]
    real = [p for p in parts if not ROLE.search(p)]
    return (real[0] if real else ROLE.sub("", parts[0]) if parts else "") or "Unbekannt"


def sync(progress=lambda msg: None) -> str:
    c = db.conn()
    seen: set[str] = set()
    counts = {}
    with _client() as http:
        prog = {p["libraryItemId"]: p for p in http.get("/api/me").raise_for_status().json().get("mediaProgress", [])}
        for lang, lib in config.ABS_LIBRARIES.items():
            if not lib:
                continue
            page, n = 0, 0
            while True:
                r = http.get(f"/api/libraries/{lib}/items", params={"limit": 500, "page": page}).raise_for_status().json()
                items = r.get("results", [])
                c.execute("BEGIN IMMEDIATE")
                for it in items:
                    if it.get("isMissing"):
                        continue
                    if not it["media"].get("numAudioFiles") and it["media"].get("ebookFormat"):
                        _ebook_only(c, lang, it)  # nur ein E-Book im Ordner: das Hörbuch fehlt noch
                        continue
                    _upsert_item(c, lang, it, prog.get(it["id"]))
                    seen.add(it["id"])
                    n += 1
                c.execute("COMMIT")
                progress(f"{lang.upper()}: {n} von {r.get('total', '?')} Büchern gelesen")
                page += 1
                if len(items) < 500:
                    break
            counts[lang] = n
    if not seen:
        raise RuntimeError("Audiobookshelf lieferte keine Bücher; Bestand bleibt unverändert")
    c.execute("BEGIN IMMEDIATE")
    for row in c.execute("SELECT id, abs_item_id, asin, edition_id FROM book WHERE owned=1").fetchall():
        if row["abs_item_id"] in seen:
            continue
        ed = c.execute("SELECT audible_asin FROM edition WHERE id=?", (row["edition_id"],)).fetchone() if row["edition_id"] else None
        if ed and ed["audible_asin"] and row["asin"]:
            c.execute("UPDATE book SET owned=0, abs_item_id=NULL, size=NULL, num_files=NULL, progress=NULL, finished=0 WHERE id=?", (row["id"],))
        else:
            c.execute("DELETE FROM book WHERE id=?", (row["id"],))
    from . import variants
    variants.assign(c)  # andere Sprecher, Hörspiel, gekürzt: als Fassung unter der Sprache
    cleanup(c)
    # Abgelegte Downloads abschliessen, sobald Audiobookshelf das Buch kennt; nicht erst am Ende der Ankunftsprüfung,
    # die bei vielen Sammelband-Bänden stundenlang laufen kann
    c.execute("UPDATE download SET state='done', updated=? WHERE state='imported' AND book_id IN (SELECT id FROM book WHERE owned=1)",
              (datetime.now(timezone.utc).isoformat(),))
    c.execute("COMMIT")
    try:
        nudge_waiting()
    except Exception:
        pass
    return ", ".join(f"{k.upper()} {v} Bücher" for k, v in counts.items())


def cleanup(c) -> None:
    # Fassungen aus dem Bestand, von denen kein Buch mehr vorhanden ist (z. B. umbenannte Serie), samt ihrer nur gesuchten Bände
    gone = "SELECT id FROM edition WHERE link_source IN ('abs','variant') AND id NOT IN (SELECT edition_id FROM book WHERE edition_id IS NOT NULL AND owned=1)"
    c.execute(f"UPDATE download SET state='rejected', error='Die Serie gibt es im Bestand nicht mehr', updated=datetime('now')"
              f" WHERE state IN ('queued','downloading','arrived','verified','review') AND book_id IN (SELECT id FROM book WHERE edition_id IN ({gone}))")
    c.execute(f"DELETE FROM book_search WHERE book_id IN (SELECT id FROM book WHERE edition_id IN ({gone}))")
    c.execute(f"DELETE FROM book WHERE owned=0 AND edition_id IN ({gone})")
    c.execute("DELETE FROM edition WHERE link_source IN ('abs','variant') AND id NOT IN (SELECT edition_id FROM book WHERE edition_id IS NOT NULL)")
    c.execute("DELETE FROM series WHERE id NOT IN (SELECT series_id FROM edition)")


def find_edition(c, lang: str, key: str):
    """Fassung zum Seriennamen, auch über den Verweis einer zusammengeführten Karte."""
    return (c.execute("SELECT id FROM edition WHERE lang=? AND norm=?", (lang, key)).fetchone()
            or c.execute("SELECT e.id FROM edition_alias a JOIN edition e ON e.id=a.edition_id WHERE a.lang=? AND a.norm=?",
                         (lang, key)).fetchone())


def get_edition(c, lang: str, name: str) -> int:
    key = db.norm(name) or name.lower()
    ed = find_edition(c, lang, key)
    if ed:
        return ed["id"]
    s = c.execute("SELECT id FROM series WHERE norm=?", (key,)).fetchone()
    sid = s["id"] if s else c.execute(
        "INSERT INTO series(name, norm, added_at) VALUES(?,?,?)",
        (name, key, datetime.now(timezone.utc).isoformat())).lastrowid
    return c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)",
                     (sid, lang, name, key)).lastrowid


def _ebook_only(c, lang: str, it: dict) -> None:
    """Eintrag ohne Tondateien (E-Book vorab oder von Hand abgelegt): zählt nicht als vorhandenes Hörbuch.
    Gehört er zu einem fehlenden Band, merkt sich ABW den Ordner; das Hörbuch kommt später dort hinein."""
    md = it["media"]["metadata"]
    ser = split_series(md.get("seriesName"))
    if not ser or it["media"].get("ebookFormat") != "epub" or not it.get("relPath"):
        return
    ed = find_edition(c, lang, db.norm(ser[0]) or ser[0].lower())
    seq = db.parse_seq(ser[1])
    if not ed or (not md.get("asin") and seq is None):
        return
    c.execute("UPDATE book SET epub=1, pre_path=COALESCE(pre_path, ?) WHERE id=(SELECT id FROM book WHERE edition_id=? AND owned=0"
              " AND ((asin IS NOT NULL AND asin=?) OR (seq IS NOT NULL AND seq=?)) LIMIT 1)", (it["relPath"], ed["id"], md.get("asin") or None, seq))


def _upsert_item(c, lang: str, it: dict, prog: dict | None) -> None:
    media = it["media"]
    md = media["metadata"]
    ser = split_series(md.get("seriesName"))
    edition_id = get_edition(c, lang, ser[0]) if ser else None
    from . import variants
    edition_id = variants.route(c, edition_id, md.get("narratorName"), md.get("title"))  # Sprecher-Fassung derselben Serie
    if not edition_id and (t := variants.solo_target(c, lang, md.get("title"))) is not None:
        edition_id = variants.solo_edition(c, t)  # Hörspiel ohne Serie: Fassung «Hörspiel» der gleichnamigen Serie
    sequence = ser[1] if ser else None
    vals = dict(
        edition_id=edition_id, lang=lang, asin=(md.get("asin") or None), title=md.get("title") or "Ohne Titel",
        subtitle=md.get("subtitle"), author=md.get("authorName"), narrator=md.get("narratorName"),
        sequence=sequence, seq=db.parse_seq(sequence),
        runtime_min=round((media.get("duration") or 0) / 60) or None,
        abs_item_id=it["id"], owned=1, size=media.get("size") or it.get("size"),
        num_files=media.get("numAudioFiles"), num_chapters=media.get("numChapters"), added_at=_iso(it.get("addedAt")),
        rel_path=it.get("relPath"), progress=(prog or {}).get("progress"), finished=1 if (prog or {}).get("isFinished") else 0,
    )
    row = c.execute("SELECT id, edition_id FROM book WHERE abs_item_id=?", (it["id"],)).fetchone()
    arrived = not row  # neu im Bestand (für die Benachrichtigung)
    if row and row["edition_id"] and row["edition_id"] != edition_id:
        # Einmal einer Fassung zugeordnet (Sprecher, Hörspiel ohne Serie): bleibt dort, auch wenn Audiobookshelf es anders führt
        cur = c.execute("SELECT base_id FROM edition WHERE id=? AND link_source='variant'", (row["edition_id"],)).fetchone()
        if cur and (edition_id is None or cur["base_id"] == edition_id):
            edition_id = vals["edition_id"] = row["edition_id"]
    if not row and not edition_id:
        # Von Hand hinzugefügtes Einzelbuch ist angekommen: gleiche ASIN, sonst gleicher Titel in derselben Sprache
        row = (c.execute("SELECT id FROM book WHERE edition_id IS NULL AND owned=0 AND asin IS NOT NULL AND asin=? LIMIT 1", (vals["asin"],)).fetchone()
               or c.execute("SELECT id FROM book WHERE edition_id IS NULL AND owned=0 AND lang=? AND title=? COLLATE NOCASE LIMIT 1", (lang, vals["title"])).fetchone())
    if not row and edition_id and (vals["asin"] or vals["seq"] is not None):
        # Bisher nur aus Audible bekannter Band ist jetzt angekommen.
        # Mehrteiliger Band («Light Bringer (1 of 3)»): nur derselbe Teil
        row = variants.pick_row(c.execute(
            "SELECT id, asin, title FROM book WHERE edition_id=? AND owned=0 AND ((asin IS NOT NULL AND asin=?) OR (seq IS NOT NULL AND seq=?))",
            (edition_id, vals["asin"], vals["seq"])), vals["asin"], vals["title"])
    if row and edition_id and (vals["asin"] or vals["seq"] is not None):
        # Dasselbe Buch stand noch als fehlend in der Serie: Eintrag auflösen, Downloads umhängen
        twin = variants.pick_row(c.execute(
            "SELECT id, asin, title FROM book WHERE edition_id=? AND owned=0 AND id<>? AND ((asin IS NOT NULL AND asin=?) OR (seq IS NOT NULL AND seq=?))",
            (edition_id, row["id"], vals["asin"], vals["seq"])), vals["asin"], vals["title"])
        if twin:
            c.execute("UPDATE download SET book_id=? WHERE book_id=?", (row["id"], twin["id"]))
            c.execute("DELETE FROM book_search WHERE book_id=?", (twin["id"],))
            c.execute("DELETE FROM book WHERE id=?", (twin["id"],))
    if row:
        c.execute("UPDATE book SET " + ", ".join(f"{k}=:{k}" for k in vals) + ", pre_path=NULL WHERE id=:id",
                  {**vals, "id": row["id"]})
    else:
        vals["release_date"] = f"{md['publishedYear']}-01-01" if (md.get("publishedYear") or "").isdigit() else None
        row = {"id": c.execute(f"INSERT INTO book({', '.join(vals)}) VALUES({', '.join(':' + k for k in vals)})", vals).lastrowid}
    if arrived and (vals["added_at"] or "") >= (datetime.now(timezone.utc) - timedelta(days=3)).isoformat():
        # nur frisch dazugekommene Bücher melden, nicht den ganzen Bestand beim ersten Abgleich
        from . import notifications
        b = c.execute("SELECT * FROM book WHERE id=?", (row["id"],)).fetchone()
        notifications.book_event("available", "Neu im Bestand", b)


_abs_root: dict[str, str] = {}  # Sprache -> Bibliotheksordner aus Sicht von Audiobookshelf
_nudged: dict[int, datetime] = {}  # Download -> letzter erneuter Anstoss
NUDGE_AFTER = timedelta(minutes=15)
MEDIA = (".m4b", ".m4a", ".mp3", ".aac", ".ogg", ".opus", ".flac", ".wav", ".wma", ".epub", ".pdf")


def _first_file(path: Path) -> Path | None:
    """Eine Datei des Buchs: der Ordner-Wächter von Audiobookshelf nimmt Dateien, nicht Ordner."""
    if path.is_file():
        return path
    files = sorted(f for f in path.iterdir() if f.is_file() and not f.name.startswith("."))
    return next((f for f in files if f.suffix.lower() in MEDIA), files[0] if files else None)


def scan(lang: str, path: str | Path | None = None) -> None:
    """Audiobookshelf neu einlesen lassen. Mit `path` (Buchordner oder Datei aus Sicht von ABW) nur dieses Buch über den
    Ordner-Wächter (`/api/watcher/update`, wenige Sekunden); sonst oder wenn das nicht geht, die ganze Bibliothek
    (auf dem NAS 15 bis 45 Minuten, und ein zweiter Anstoss während eines laufenden Scans wird verworfen)."""
    lib = config.ABS_LIBRARIES[lang]
    with _client() as http:
        if path is not None:
            try:
                f = _first_file(Path(path))
                if f is not None:
                    rel = f.relative_to(config.LIBRARY_ROOT[lang]).as_posix()
                    if lang not in _abs_root:
                        _abs_root[lang] = http.get(f"/api/libraries/{lib}").raise_for_status().json()["folders"][0]["fullPath"].rstrip("/")
                    http.post("/api/watcher/update", json={"libraryId": lib, "path": f"{_abs_root[lang]}/{rel}", "type": "add"}).raise_for_status()
                    return
            except (ValueError, OSError, KeyError, IndexError, httpx.HTTPError):
                pass  # dann eben die ganze Bibliothek
        http.post(f"/api/libraries/{lib}/scan").raise_for_status()


def nudge_waiting() -> int:
    """Abgelegte Bücher, die Audiobookshelf nach 15 Minuten noch nicht (oder nur als «fehlend») kennt, erneut melden."""
    c = db.conn()
    now = datetime.now(timezone.utc)
    n = 0
    for r in c.execute("SELECT d.id, d.imported_to, d.updated, b.lang FROM download d JOIN book b ON b.id=d.book_id"
                       " WHERE d.state='imported' AND d.imported_to IS NOT NULL").fetchall():
        try:
            since = datetime.fromisoformat(r["updated"])
        except (TypeError, ValueError):
            continue
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        last = _nudged.get(r["id"], since)
        if now - last < NUDGE_AFTER:
            continue
        _nudged[r["id"]] = now
        try:
            scan(r["lang"], r["imported_to"])
            n += 1
        except Exception:
            pass
    return n


def scan_all(progress=lambda msg: None) -> str:
    """Rückfallebene: beide Bibliotheken komplett einlesen lassen."""
    done = []
    for lang, lib in config.ABS_LIBRARIES.items():
        if lib:
            scan(lang)
            done.append(lang.upper())
    return "Scan angestossen: " + ", ".join(done)


PROVIDER = {"en": "audible", "de": "audible.de"}
MATCH_BATCH = 40  # je Lauf, damit der erste Nachhol-Lauf die anderen Aufgaben nicht lange aufhält


PENDING = "offen: "  # vor dem Match gesicherte Serien und ASIN; bleibt stehen, falls das Zurücksetzen abbricht
TODO = "(b.abs_match IS NULL OR b.abs_match LIKE 'offen: %')"


WISH_TAG = "Gewünscht von "


TAG_BATCH = 100  # Einträge je Lauf beim Nachtragen


def _tag_wishers(http, item: str, names: list[str] | None) -> bool:
    """Tag «Gewünscht von <Name>» für jeden Benutzer, der das Buch oder seine Serie gewünscht hat (Idee aus Spokarr).
    Beim Match erst danach, weil der Match mit overrideDetails die Tags ersetzt.
    Vorhandene Tags bleiben stehen. Gesetzte Tags merkt sich ABW (abs_tag), damit ein in Audiobookshelf
    von Hand entfernter Tag nicht wiederkommt. False, wenn es den Eintrag nicht mehr gibt."""
    if not names:
        return True
    r = http.get(item)
    if r.status_code == 404:
        return False
    tags = list((r.raise_for_status().json().get("media") or {}).get("tags") or [])
    want = [WISH_TAG + n for n in names]
    new = [t for t in want if t not in tags]
    if new:
        http.patch(f"{item}/media", json={"tags": tags + new}).raise_for_status()
    db.conn().executemany("INSERT OR IGNORE INTO abs_tag(item, tag) VALUES(?, ?)", [(item.rsplit("/", 1)[1], t) for t in want])
    return True


def _wish_tags_due() -> dict[str, list[str]]:
    """{Audiobookshelf-Eintrag: noch nie gesetzte Namen} für alle vorhandenen Bücher."""
    c = db.conn()
    rows = c.execute("SELECT id, abs_item_id FROM book WHERE owned=1 AND abs_item_id IS NOT NULL").fetchall()
    item_of = {r["id"]: r["abs_item_id"] for r in rows}
    done = {(r[0], r[1]) for r in c.execute("SELECT item, tag FROM abs_tag")}
    out: dict[str, list[str]] = {}
    for bid, names in library.wishers(item_of).items():
        for n in names:
            if (item_of[bid], WISH_TAG + n) not in done and n not in out.setdefault(item_of[bid], []):
                out[item_of[bid]].append(n)
    return {k: v for k, v in out.items() if v}


def tag_wishes(progress=lambda msg: None) -> str:
    """Trägt den Tag «Gewünscht von <Name>» bei allen vorhandenen Büchern gewünschter Serien nach, auch bei
    Büchern, die schon vor ABW im Bestand waren. Je Tag und Eintrag nur einmal."""
    due = _wish_tags_due()
    if not due:
        return "Nichts zu taggen"
    todo = sorted(due.items())[:TAG_BATCH]
    done = 0
    with _client() as http:
        for i, (item, names) in enumerate(todo, 1):
            progress(f"{i} von {len(todo)}")
            if _tag_wishers(http, f"/api/items/{item}", names):
                done += 1
    left = len(due) - len(todo)
    return f"{done} Einträge getaggt" + (f", {left} warten auf den nächsten Lauf" if left else "")


def match_imported(progress=lambda msg: None) -> str:
    """Von ABW abgelegte Bücher in Audiobookshelf mit Audible abgleichen («Match» über die ASIN), je Buch einmal.
    Audible überschreibt alles (Cover, Titel, Autoren, Beschreibung, Verlag, Genres …) ausser Serie und ASIN:
    Die setzt ABW danach auf den Stand vor dem Match zurück, weil Audible Serien verbiegt
    (z. B. «The First Law» → «Die Klingen-Saga», «Drachenmagus» ohne Serie)."""
    c = db.conn()
    rows = c.execute(
        "SELECT DISTINCT b.id, b.abs_item_id, b.asin, b.lang, b.title, b.abs_match FROM book b JOIN download d ON d.book_id=b.id"
        f" WHERE d.imported_to IS NOT NULL AND b.owned=1 AND b.abs_item_id IS NOT NULL AND length(b.asin)=10 AND {TODO}"
        " ORDER BY b.id DESC LIMIT ?", (MATCH_BATCH,)).fetchall()
    if not rows:
        return "Nichts zu matchen"
    done, failed = 0, 0
    wishers = library.wishers([b["id"] for b in rows])
    with _client() as http:
        for i, b in enumerate(rows, 1):
            progress(f"{i} von {len(rows)}: {b['title']}")
            item = f"/api/items/{b['abs_item_id']}"
            if b["abs_match"]:  # letzter Versuch brach nach dem Match ab: nur noch zurücksetzen
                keep = json.loads(b["abs_match"][len(PENDING):])
            else:
                r = http.get(item)
                if r.status_code == 404:  # Eintrag gibt es nicht mehr; der nächste Bestandsabgleich räumt auf
                    continue
                md = r.raise_for_status().json()["media"]["metadata"]
                keep = {"series": [{"name": se["name"], "sequence": se.get("sequence")} for se in md.get("series") or []],
                        "asin": md.get("asin") or b["asin"]}
                c.execute("UPDATE book SET abs_match=? WHERE id=?", (PENDING + json.dumps(keep, ensure_ascii=False), b["id"]))
                res = http.post(f"{item}/match", json={"provider": PROVIDER[b["lang"]], "asin": keep["asin"],
                                                       "overrideCover": True, "overrideDetails": True}).raise_for_status().json()
                if res.get("warning"):  # nichts geändert
                    failed += 1
                    c.execute("UPDATE book SET abs_match=? WHERE id=?", (f"Fehler: {res['warning']}"[:200], b["id"]))
                    _tag_wishers(http, item, wishers.get(b["id"]))
                    continue
            http.patch(f"{item}/media", json={"metadata": keep}).raise_for_status()
            _tag_wishers(http, item, wishers.get(b["id"]))
            done += 1
            c.execute("UPDATE book SET abs_match=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), b["id"]))
    left = c.execute(
        "SELECT count(DISTINCT b.id) FROM book b JOIN download d ON d.book_id=b.id WHERE d.imported_to IS NOT NULL AND b.owned=1"
        f" AND b.abs_item_id IS NOT NULL AND length(b.asin)=10 AND {TODO}").fetchone()[0]
    return f"{done} gematcht" + (f", {failed} ohne Treffer" if failed else "") + (f", {left} warten auf den nächsten Lauf" if left else "")


def cover(item_id: str) -> tuple[bytes, str]:
    with _client() as http:
        r = http.get(f"/api/items/{item_id}/cover", params={"width": 400, "format": "webp"})
        r.raise_for_status()
        return r.content, r.headers.get("content-type", "image/webp")


# ---------- Nutzer aus Audiobookshelf: Anmeldung, Hörfortschritt, Markierungen ----------

def login_ok(username: str, password: str, abs_user_id: str, keep=None) -> bool:
    """Prüft Benutzername und Passwort bei Audiobookshelf. ABW speichert das Passwort nicht.
    Mit «keep» (Funktion) bleibt die Sitzung offen und keep(refresh_token) bekommt ihren Erneuerungsschlüssel,
    damit ABW den Hörstand dieses Kontos setzen kann (Audiobookshelf erlaubt das nur dem Konto selbst).
    ConnectionError, wenn Audiobookshelf nicht antwortet."""
    try:
        with httpx.Client(base_url=config.ABS_URL, timeout=20) as http:
            r = http.post("/login", json={"username": username, "password": password}, headers={"x-return-tokens": "true"})
            if r.status_code in (400, 401, 403):
                return False
            if r.status_code == 429:
                raise ConnectionError("Audiobookshelf nimmt gerade keine Anmeldungen an (zu viele Versuche). Bitte in einigen Minuten erneut versuchen.")
            user = r.raise_for_status().json().get("user") or {}
            ok = user.get("id") == abs_user_id and user.get("isActive", True) and not user.get("isLocked")
            if ok and keep and user.get("refreshToken"):
                try:
                    keep(user["refreshToken"])
                    return True
                except Exception:
                    pass  # Sitzung nicht gespeichert: wie bisher schliessen, die Anmeldung gilt trotzdem
            try:  # die für die Prüfung eröffnete Sitzung gleich wieder schliessen
                http.post("/logout", headers={"x-refresh-token": user.get("refreshToken") or ""})
            except httpx.HTTPError:
                pass
            return bool(ok)
    except httpx.HTTPError as exc:
        raise ConnectionError("Audiobookshelf ist gerade nicht erreichbar; die Anmeldung mit dem Audiobookshelf-Passwort geht im Moment nicht.") from exc


def users() -> list[dict]:
    """Konten in Audiobookshelf (braucht den Schlüssel eines Administrators) und das Konto, mit dem ABW selbst liest."""
    with _client() as http:
        me = http.get("/api/me").raise_for_status().json()
        rows = http.get("/api/users").raise_for_status().json().get("users", [])
    return [{"id": u["id"], "username": u["username"], "type": u.get("type"), "isActive": bool(u.get("isActive", True)),
             "last_seen": _iso(u.get("lastSeen")), "own": u["id"] == me["id"]} for u in rows]


def _entries(media_progress: list) -> list[tuple]:
    return [(p["libraryItemId"], p.get("progress"), 1 if p.get("isFinished") else 0, _iso(p.get("lastUpdate")))
            for p in media_progress or [] if p.get("libraryItemId") and not p.get("episodeId")]


def sync_progress(progress=lambda msg: None) -> str:
    """Hörfortschritt je Benutzer: jeder verknüpfte Benutzer bekommt den Stand seines Audiobookshelf-Kontos.
    Benutzer 1 ohne Verknüpfung (und ABW ohne Anmeldung) sieht den Stand des Kontos, mit dem ABW liest.
    Hat jemand ein Buch fertig gehört, werden Serie und Autor für ihn markiert (einmal; wer die Markierung entfernt, behält das)."""
    c = db.conn()
    linked = c.execute("SELECT id, name, abs_user_id FROM user WHERE abs_user_id IS NOT NULL").fetchall()
    first = c.execute("SELECT abs_user_id FROM user WHERE id=1").fetchone()
    data: dict[int, list[tuple]] = {}
    state: dict[int, tuple] = {}
    with _client() as http:
        known = {}
        if linked:
            known = {u["id"]: u for u in http.get("/api/users").raise_for_status().json().get("users", [])}
        for u in linked:
            a = known.get(u["abs_user_id"])
            if not a or not a.get("isActive", True):
                state[u["id"]] = (None, 0)
                continue
            state[u["id"]] = (a["username"], 1)
            progress(f"Hörfortschritt von {u['name']}")
            data[u["id"]] = _entries(http.get(f"/api/users/{a['id']}").raise_for_status().json().get("mediaProgress"))
        if not first or not first["abs_user_id"]:
            data[1] = _entries(http.get("/api/me").raise_for_status().json().get("mediaProgress"))
    books = {r["abs_item_id"]: r["id"] for r in c.execute("SELECT id, abs_item_id FROM book WHERE abs_item_id IS NOT NULL")}
    c.execute("BEGIN IMMEDIATE")
    for uid, (name, active) in state.items():
        if active:
            c.execute("UPDATE user SET abs_name=?, abs_active=1 WHERE id=?", (name, uid))
        else:
            # Konto in Audiobookshelf gesperrt oder gelöscht: kein Zugang mehr über dessen Passwort, offene Sitzungen enden
            c.execute("UPDATE user SET abs_active=0 WHERE id=?", (uid,))
            c.execute("DELETE FROM session WHERE user_id=? AND user_id IN (SELECT id FROM user WHERE hash NOT LIKE '%$%')", (uid,))
    marked = 0
    finished_before = {uid: {r[0] for r in c.execute("SELECT book_id FROM user_progress WHERE user_id=? AND finished=1", (uid,))}
                       for uid in data}
    had_rows = {uid: bool(c.execute("SELECT 1 FROM user_progress WHERE user_id=? LIMIT 1", (uid,)).fetchone()) for uid in data}
    for uid, entries in data.items():
        c.execute("DELETE FROM user_progress WHERE user_id=?", (uid,))
        c.executemany("INSERT OR REPLACE INTO user_progress(user_id, book_id, progress, finished, updated) VALUES(?,?,?,?,?)",
                      [(uid, books[item], p, fin, upd) for item, p, fin, upd in entries if item in books])
        marked += auto_mark(c, uid)
    c.execute("COMMIT")
    from . import ratings, toread
    for uid in data:
        if had_rows[uid]:
            try:
                ratings.remind_finished(c, uid, finished_before[uid] or {-1})
            except Exception:
                pass  # eine Meldung darf den Abgleich nicht stören
            try:  # neu fertig gehört: Serie mit weiterem Band auf «To Read» (nicht beim ersten Abgleich eines Kontos)
                now_done = {r[0] for r in c.execute("SELECT book_id FROM user_progress WHERE user_id=? AND finished=1", (uid,))}
                toread.on_finished(c, uid, now_done - finished_before[uid])
            except Exception:
                pass
    n = len(data)
    gone = sum(1 for v in state.values() if not v[1])
    return (f"Hörfortschritt von {n} {'Konto' if n == 1 else 'Konten'} gelesen" + (f", {marked} Serien und Autoren neu markiert" if marked else "")
            + (f", {gone} verknüpfte Konten in Audiobookshelf gesperrt oder gelöscht" if gone else ""))


def auto_mark(c, user_id: int) -> int:
    """Serie und Autor jedes fertig gehörten Buchs markieren. Jede Markierung setzt ABW nur einmal."""
    now = datetime.now(timezone.utc).isoformat()
    want: set[tuple[str, str]] = set()
    for b in c.execute("SELECT b.author, e.series_id FROM user_progress p JOIN book b ON b.id=p.book_id"
                       " LEFT JOIN edition e ON e.id=b.edition_id WHERE p.user_id=? AND p.finished=1", (user_id,)):
        if b["series_id"]:
            want.add(("series", str(b["series_id"])))
        key = db.norm(primary_author(b["author"])) if b["author"] else ""
        if key:
            want.add(("author", key))
    done = {(r["kind"], r["key"]) for r in c.execute("SELECT kind, key FROM auto_follow WHERE user_id=?", (user_id,))}
    n = 0
    for kind, key in sorted(want - done):
        c.execute("INSERT OR IGNORE INTO auto_follow(user_id, kind, key, created) VALUES(?,?,?,?)", (user_id, kind, key, now))
        n += c.execute("INSERT OR IGNORE INTO follow(user_id, kind, key, created) VALUES(?,?,?,?)", (user_id, kind, key, now)).rowcount
    return n
