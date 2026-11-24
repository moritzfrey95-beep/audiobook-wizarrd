"""«To Read»: was ein Benutzer als Nächstes hört (nur Hörbücher).

Ein Eintrag ist eine ganze Serie (genauer: eine Fassung) oder ein Einzelbuch. Eine Serie zeigt immer den nächsten Band,
den der Benutzer noch nicht fertig gehört hat; damit wandert der Eintrag beim Hören von selbst weiter.
- Hat jemand einen Band fertig gehört und folgt in der Fassung ein weiterer, kommt die Serie automatisch oben auf die Liste.
  Wer einen Serien-Eintrag entfernt, bekommt ihn nicht automatisch zurück (Tabelle toread_skip), von Hand aber jederzeit.
- Fertig gehörte Einzelbücher und Serien ohne weiteren Band fallen weg.
- Die Seite zeigt eine Zeile je Serie mit Fortschritt über die ganze Serie, geteilt in «Als Nächstes» (vorhanden; zuoberst
  die zuletzt gehörte Serie, sonst eigene Reihenfolge) und «Wartet» (noch nicht im Bestand: angekündigt, gesucht, am Laden).
ABW schreibt dafür nichts nach Audiobookshelf.
"""
from datetime import datetime, timedelta, timezone

from . import config, db

KINDS = ("edition", "book")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(kind: str, ref: int) -> str:
    return f"{kind[0]}{ref}"


def _check(kind: str, ref: int) -> None:
    c = db.conn()
    if kind not in KINDS:
        raise ValueError("Unbekannte Art")
    table = "edition" if kind == "edition" else "book"
    if not c.execute(f"SELECT 1 FROM {table} WHERE id=?", (ref,)).fetchone():
        raise LookupError("Nicht gefunden")


def keys(user_id: int) -> list[str]:
    return [_key(r["kind"], r["ref_id"]) for r in db.conn().execute("SELECT kind, ref_id FROM toread WHERE user_id=? ORDER BY pos", (user_id,))]


def add(user_id: int, kind: str, ref: int, top: bool = False, source: str = "manual") -> bool:
    """Eintrag ans Ende (oder an den Anfang) setzen. Liefert False, wenn er schon auf der Liste steht."""
    _check(kind, ref)
    c = db.conn()
    lo, hi = c.execute("SELECT MIN(pos), MAX(pos) FROM toread WHERE user_id=?", (user_id,)).fetchone()
    pos = ((lo or 0) - 1) if top else ((hi or 0) + 1)
    new = c.execute("INSERT OR IGNORE INTO toread(user_id, kind, ref_id, pos, source, added) VALUES(?,?,?,?,?,?)",
                    (user_id, kind, ref, pos, source, _now())).rowcount
    if source == "manual":
        c.execute("DELETE FROM toread_skip WHERE user_id=? AND edition_id=?", (user_id, ref if kind == "edition" else -1))
    return bool(new)


def remove(user_id: int, kind: str, ref: int, remember: bool = True) -> None:
    c = db.conn()
    c.execute("DELETE FROM toread WHERE user_id=? AND kind=? AND ref_id=?", (user_id, kind, ref))
    if remember and kind == "edition":
        c.execute("INSERT OR IGNORE INTO toread_skip(user_id, edition_id, created) VALUES(?,?,?)", (user_id, ref, _now()))
        se = _Ctx(user_id).eds.get(ref)
        nxt = next_of(se[1]) if se else None
        if nxt:
            c.execute("INSERT OR REPLACE INTO toread_dismiss(user_id, edition_id, book_id, created) VALUES(?,?,?,?)",
                      (user_id, ref, nxt[0]["id"], _now()))


def reorder(user_id: int, order: list[str]) -> None:
    """Neue Reihenfolge als Liste von Schlüsseln («e12», «b40»). Nicht genannte Einträge behalten ihren Platz dahinter."""
    c = db.conn()
    rows = c.execute("SELECT kind, ref_id FROM toread WHERE user_id=? ORDER BY pos", (user_id,)).fetchall()
    have = [_key(r["kind"], r["ref_id"]) for r in rows]
    seen = [k for k in dict.fromkeys(order) if k in have]
    final = seen + [k for k in have if k not in seen]
    by_key = {_key(r["kind"], r["ref_id"]): r for r in rows}
    c.executemany("UPDATE toread SET pos=? WHERE user_id=? AND kind=? AND ref_id=?",
                  [(i, user_id, by_key[k]["kind"], by_key[k]["ref_id"]) for i, k in enumerate(final)])


def _numbered(e: dict) -> list:
    """Nummerierte Bände einer Fassung in Reihenfolge (Sammelbände wie «1-3» haben keine Zahl und zählen nicht)."""
    return [(b, st) for b, st in zip(e["_books"], e["_states"]) if b["seq"] is not None]


def next_of(e: dict) -> tuple | None:
    """Nächster Band: der erste nicht fertig gehörte nach dem letzten fertig gehörten, sonst der erste nicht fertig gehörte.
    Liefert (Buch, Zustand, Anzahl offener Bände ab hier) oder None."""
    prog = e["_prog"]
    nb = _numbered(e)
    done = [b["seq"] for b, _ in nb if prog.get(b["id"], (0, False))[1]]
    last = max(done, default=None)
    rest = [(b, st) for b, st in nb if not prog.get(b["id"], (0, False))[1] and (last is None or b["seq"] > last)]
    if not rest:
        return None
    b, st = rest[0]
    return b, st, len(rest)


def _book_out(b, st: str, prog: dict) -> dict:
    p = prog.get(b["id"], (None, False))
    rd = b["release_date"]
    return {"id": b["id"], "title": b["title"], "sequence": b["sequence"], "author": b["author"], "narrator": b["narrator"],
            "runtime_min": b["runtime_min"], "cover": f"/api/cover/{b['abs_item_id']}" if b["abs_item_id"] else b["cover_url"],
            "abs_item_id": b["abs_item_id"], "state": st, "release_date": None if rd and rd >= "2100" else rd,
            "progress": p[0], "finished": p[1]}


def _stats(books: list, prog: dict) -> dict:
    """Fortschritt über die ganze Serie: Bände x von y und Anteil nach Stunden (angekündigte Bände zählen nicht)."""
    total = done = 0.0
    n_done = 0
    for b in books:
        mins = b["runtime_min"] or b["audible_min"] or 0
        p, fin = prog.get(b["id"], (None, False))
        total += mins
        done += mins if fin else mins * (p or 0)
        n_done += 1 if fin else 0
    return {"done": n_done, "total": len(books), "pct": round(100 * done / total) if total else None,
            "hours_done": round(done / 60), "hours_total": round(total / 60), "min_left": round(total - done)}


PACE_DAYS = 30


def pace(user_id: int) -> float | None:
    """Hörminuten pro Tag in den letzten 30 Tagen (Hörverlauf aus Audiobookshelf); None unter einer Stunde insgesamt."""
    c = db.conn()
    row = c.execute("SELECT abs_user_id FROM user WHERE id=?", (user_id,)).fetchone()
    abs_id = row["abs_user_id"] if row else None
    if not abs_id and user_id == 1:
        try:
            from . import listened
            abs_id = listened.own_abs_id()
        except Exception:
            return None
    if not abs_id:
        return None
    since = (datetime.now(timezone.utc) - timedelta(days=PACE_DAYS)).date().isoformat()
    sec = c.execute("SELECT COALESCE(SUM(seconds), 0) FROM listen_session WHERE abs_user_id=? AND day>=?", (abs_id, since)).fetchone()[0]
    return sec / 60 / PACE_DAYS if sec >= 3600 else None


class _Ctx:
    """Einmal geladener Stand für eine Seite: Serien mit Fortschritt, Downloads, wann was gehört wurde."""

    def __init__(self, user_id: int):
        from . import api
        c = db.conn()
        self.series = api.load(user_id)
        self.eds = {e["id"]: (s, e) for s in self.series.values() for e in s["editions"]}
        self.prog = api.progress_of(user_id)
        self.heard = {r["book_id"]: r["updated"] or "" for r in c.execute(
            "SELECT book_id, updated FROM user_progress WHERE user_id=? AND (finished=1 OR progress>0)", (user_id,))}
        self.today = datetime.now(timezone.utc).date().isoformat()
        self.dl = api.own_downloads()

    def entry(self, kind: str, ref: int) -> dict | None:
        """Eine Zeile (Serie mit nächstem Band oder Einzelbuch); None, wenn nichts mehr offen ist."""
        from . import api
        prog = self.prog
        if kind == "edition":
            se = self.eds.get(ref)
            nxt = next_of(se[1]) if se else None
            if not nxt:
                return None
            s, e = se
            b, st, left = nxt
            books = [bk for bk, bst in _numbered(e) if bst != "upcoming"]
            return {"key": _key(kind, ref), "kind": kind, "series_id": s["id"], "series": e["name"], "author": s["author"],
                    "cover": s["cover"], "lang": e["lang"], "variant": e["variant"], "left": left, "stats": _stats(books, prog),
                    "last": max((self.heard.get(bk["id"], "") for bk, _ in _numbered(e)), default=""), "book": _book_out(b, st, prog)}
        b = db.conn().execute("SELECT b.*, e.series_id, e.name series FROM book b LEFT JOIN edition e ON e.id=b.edition_id WHERE b.id=?",
                              (ref,)).fetchone()
        if not b or prog.get(b["id"], (0, False))[1]:
            return None
        out = _book_out(b, api.book_state(b, self.today, self.dl), prog)
        return {"key": _key(kind, ref), "kind": kind, "series_id": b["series_id"], "series": b["series"], "author": b["author"],
                "cover": out["cover"], "lang": b["lang"], "variant": None, "left": 1, "stats": _stats([b], prog),
                "last": self.heard.get(b["id"], ""), "book": out}


def overview(user_id: int) -> dict:
    """Eine Zeile je Serie: «Als Nächstes» (nächster Band im Bestand) und «Wartet» (fehlt noch).
    Zuoberst die zuletzt gehörte Serie, danach die eigene Reihenfolge. Darunter Empfehlungen anderer Benutzer und
    Vorschläge. Entfernt dabei Einträge, die erledigt sind."""
    c = db.conn()
    ctx = _Ctx(user_id)
    entries, gone = [], []
    for r in c.execute("SELECT * FROM toread WHERE user_id=? ORDER BY pos", (user_id,)).fetchall():
        x = ctx.entry(r["kind"], r["ref_id"])
        if not x:
            gone.append(r)
            continue
        x["source"] = r["source"]
        entries.append(x)
    for r in gone:  # fertig gehört: weg, ohne Sperre (kommt mit einem neuen Band automatisch wieder)
        c.execute("DELETE FROM toread WHERE user_id=? AND kind=? AND ref_id=?", (user_id, r["kind"], r["ref_id"]))
    ready = [x for x in entries if x["book"]["state"] == "owned"]
    waiting = sorted((x for x in entries if x["book"]["state"] != "owned"), key=lambda x: x["book"]["release_date"] or "9999")
    latest = max(ready, key=lambda x: x["last"], default=None)
    if latest and latest["last"]:
        ready.remove(latest)
        ready.insert(0, latest)
    for x in ready:
        x["pinned"] = x is latest and bool(latest["last"])
    hours = round(sum(x["book"]["runtime_min"] or 0 for x in ready) / 60)
    # Ungefähr fertig: Resthörzeit jeder Serie in der Reihenfolge der Liste, beim Tempo der letzten 30 Tage
    per_day = pace(user_id)
    cum = 0
    for x in ready:
        cum += x["stats"]["min_left"]
        x["eta"] = (datetime.now(timezone.utc) + timedelta(days=cum / per_day)).date().isoformat() if per_day else None
    listed = {x["key"] for x in entries}
    recs = recommended(user_id, ctx, listed)
    return {"next": ready, "waiting": waiting, "hours": hours, "pace_h": round(per_day / 60, 1) if per_day else None,
            "abs_url": config.ABS_PUBLIC_URL, "recommended": recs,
            "suggested": suggestions(user_id, ctx, listed | {x["key"] for x in recs})}


# ---------- Weiterempfehlen ----------

def place(user_id: int, kind: str, ref: int, top: bool) -> None:
    """Auf die Liste setzen, zuoberst oder zuunterst; steht der Eintrag schon darauf, wird er dorthin verschoben."""
    c = db.conn()
    if not add(user_id, kind, ref, top=top):
        lo, hi = c.execute("SELECT MIN(pos), MAX(pos) FROM toread WHERE user_id=?", (user_id,)).fetchone()
        c.execute("UPDATE toread SET pos=? WHERE user_id=? AND kind=? AND ref_id=?",
                  ((lo or 0) - 1 if top else (hi or 0) + 1, user_id, kind, ref))


def _target(book_id: int) -> tuple[str, int]:
    """Empfohlen wird die Serie (Fassung) eines Buchs, ein Buch ohne Serie für sich."""
    b = db.conn().execute("SELECT id, edition_id FROM book WHERE id=?", (book_id,)).fetchone()
    if not b:
        raise LookupError("Buch nicht gefunden")
    return ("edition", b["edition_id"]) if b["edition_id"] else ("book", b["id"])


REC_MODES = ("admins", "all", "custom", "off")


def users(user_id: int) -> list[dict]:
    """An wen man empfehlen kann. Der Administrator legt das unter Einstellungen fest (rec_mode):
    «admins» = Administratoren an alle, Benutzer nur an Administratoren; «all» = alle an alle;
    «custom» = nach der Tabelle rec_allow; «off» = niemand."""
    c = db.conn()
    mode = db.setting("rec_mode")
    me = c.execute("SELECT role FROM user WHERE id=?", (user_id,)).fetchone()
    if mode == "off":
        return []
    if mode == "custom":
        return [dict(r) for r in c.execute("SELECT u.id, u.name FROM rec_allow a JOIN user u ON u.id=a.to_user"
                                           " WHERE a.from_user=? AND u.id<>? ORDER BY u.name COLLATE NOCASE", (user_id, user_id))]
    only_admins = mode == "admins" and bool(me) and me["role"] != "admin"
    return [dict(r) for r in c.execute("SELECT id, name FROM user WHERE id<>?" + (" AND role='admin'" if only_admins else "")
                                       + " ORDER BY name COLLATE NOCASE", (user_id,))]


def rules() -> dict:
    """Einstellung fürs Weiterempfehlen (nur Administrator): Modus und erlaubte Paare für «custom»."""
    c = db.conn()
    return {"mode": db.setting("rec_mode"),
            "users": [dict(r) for r in c.execute("SELECT id, name, role FROM user ORDER BY name COLLATE NOCASE")],
            "allow": [[r[0], r[1]] for r in c.execute("SELECT from_user, to_user FROM rec_allow ORDER BY 1, 2")]}


def set_rules(mode: str, allow: list[list[int]] | None = None) -> None:
    if mode not in REC_MODES:
        raise ValueError("Unbekannte Einstellung fürs Weiterempfehlen")
    c = db.conn()
    db.set_setting("rec_mode", mode)
    if allow is not None:
        ids = {r[0] for r in c.execute("SELECT id FROM user")}
        c.execute("DELETE FROM rec_allow")
        c.executemany("INSERT OR IGNORE INTO rec_allow VALUES(?,?)",
                      [(a, b) for a, b in allow if a != b and a in ids and b in ids])


def heard(user_id: int) -> list[dict]:
    """Gehörte Serien (und Einzelbücher), zuletzt gehörte zuerst, eine Zeile je Serie, mit Anzahl
    gehörter Bände und den Benutzern, denen die Serie schon empfohlen wurde."""
    c = db.conn()
    sent: dict[str, list[str]] = {}
    for r in c.execute("SELECT t.kind, t.ref_id, u.name FROM toread_rec t JOIN user u ON u.id=t.to_user WHERE t.from_user=? ORDER BY u.name",
                       (user_id,)):
        sent.setdefault(_key(r["kind"], r["ref_id"]), []).append(r["name"])
    seen: dict[str, dict] = {}
    for r in c.execute("SELECT b.id, b.title, b.sequence, b.author, b.lang, b.abs_item_id, b.cover_url, b.edition_id, e.series_id, e.name series,"
                       " p.updated finished_at FROM user_progress p JOIN book b ON b.id=p.book_id LEFT JOIN edition e ON e.id=b.edition_id"
                       " WHERE p.user_id=? AND p.finished=1 ORDER BY p.updated DESC", (user_id,)):
        key = _key("edition", r["edition_id"]) if r["edition_id"] else _key("book", r["id"])
        if key in seen:
            seen[key]["count"] += 1
            continue
        seen[key] = {"id": r["id"], "count": 1, "title": r["title"], "sequence": r["sequence"], "author": r["author"], "lang": r["lang"],
                    "series": r["series"], "series_id": r["series_id"], "finished_at": r["finished_at"], "key": key,
                    "cover": f"/api/cover/{r['abs_item_id']}" if r["abs_item_id"] else r["cover_url"], "sent_to": sent.get(key, [])}
    out = list(seen.values())
    # Umfang der ganzen Serie: erschienene nummerierte Bände und ihre Laufzeit, je Nummer einmal
    eds = [int(x["key"][1:]) for x in out if x["key"][0] == "e"]
    size: dict[int, dict[float, int]] = {}
    today = datetime.now(timezone.utc).date().isoformat()
    for i in range(0, len(eds), 500):
        part = eds[i:i + 500]
        for r in c.execute(f"SELECT edition_id, sequence, release_date, runtime_min, audible_min FROM book WHERE edition_id IN ({','.join('?' * len(part))})", part):
            seq = db.parse_seq(r["sequence"])
            if seq is None or (r["release_date"] and r["release_date"] > today):
                continue
            mins = r["runtime_min"] or r["audible_min"] or 0
            per = size.setdefault(r["edition_id"], {})
            per[seq] = max(per.get(seq, 0), mins)
    for x in out:
        per = size.get(int(x["key"][1:])) if x["key"][0] == "e" else None
        x["series_books"] = len(per) if per else None
        x["series_hours"] = round(sum(per.values()) / 60) if per else None
    cands = users(user_id)
    for x in out:
        why = blocked(x["key"], [u["id"] for u in cands])
        x["blocked"] = [{"id": u["id"], "name": u["name"], "why": why[u["id"]]} for u in cands if u["id"] in why]
    return out


def _series_of(kind: str, ref: int) -> int | None:
    if kind != "edition":
        return None
    r = db.conn().execute("SELECT series_id FROM edition WHERE id=?", (ref,)).fetchone()
    return r["series_id"] if r else None


def blocked(key: str, user_ids: list[int]) -> dict[int, str]:
    """Wem man nicht empfehlen kann: «heard» = hat aus der Serie (egal welche Fassung) schon etwas
    gehört oder angefangen, «listed» = steht schon auf seiner Liste «To Read»."""
    kind, ref = _parse(key)
    if not user_ids:
        return {}
    c = db.conn()
    sid = _series_of(kind, ref)
    qs = ",".join("?" * len(user_ids))
    if sid:
        books = "SELECT b.id FROM book b JOIN edition e ON e.id=b.edition_id WHERE e.series_id=?"
        listed_q = (f"SELECT t.user_id FROM toread t JOIN edition e ON t.kind='edition' AND e.id=t.ref_id WHERE e.series_id=? AND t.user_id IN ({qs})",
                    (sid, *user_ids))
        arg = (sid,)
    else:
        books = "SELECT ?"
        listed_q = (f"SELECT user_id FROM toread WHERE kind='book' AND ref_id=? AND user_id IN ({qs})", (ref, *user_ids))
        arg = (ref,)
    out = {r[0]: "listed" for r in c.execute(*listed_q)}
    for r in c.execute(f"SELECT DISTINCT user_id FROM user_progress WHERE book_id IN ({books}) AND (finished=1 OR progress>0.01)"
                       f" AND user_id IN ({qs})", (*arg, *user_ids)):
        out[r[0]] = "heard"
    return out


def recommend(user_id: int, book_id: int, to: list[int]) -> int:
    """Serie eines gehörten Buchs an andere Benutzer empfehlen; sie erscheint dort unter der Liste «To Read».
    Liefert die Anzahl Empfänger."""
    from . import auth, notifications
    kind, ref = _target(book_id)
    c = db.conn()
    me = auth.get(user_id)
    name = me["name"] if me else "Jemand"
    valid = {u["id"] for u in users(user_id)}
    valid -= set(blocked(_key(kind, ref), sorted(valid)))  # schon gehört oder auf seiner Liste: nicht empfehlen
    b = c.execute("SELECT b.title, b.abs_item_id, b.cover_url, e.name series FROM book b LEFT JOIN edition e ON e.id=b.edition_id WHERE b.id=?",
                  (book_id,)).fetchone()
    what = b["series"] or b["title"]
    n = 0
    for uid in dict.fromkeys(to):
        if uid not in valid:
            continue
        c.execute("INSERT INTO toread_rec(from_user, to_user, kind, ref_id, created, state) VALUES(?,?,?,?,?,'open')"
                  " ON CONFLICT(from_user, to_user, kind, ref_id) DO UPDATE SET state='open', created=excluded.created",
                  (user_id, uid, kind, ref, _now()))
        n += 1
        try:
            notifications.emit("recommend", f"{name} empfiehlt dir «{what}»", "Unter «To Read» entscheiden: oben, unten oder nicht auf die Liste.",
                               "/to-read", f"rec:{user_id}:{_key(kind, ref)}:{_now()[:10]}",
                               f"/api/cover/{b['abs_item_id']}" if b["abs_item_id"] else b["cover_url"], users=(uid,))
        except Exception:
            pass  # die Meldung darf die Empfehlung nicht verhindern
    return n


def recommended(user_id: int, ctx: _Ctx | None = None, listed: set[str] = frozenset()) -> list[dict]:
    """Offene Empfehlungen an den Benutzer, je Serie eine Zeile mit allen Empfehlenden. Was schon auf der Liste steht
    oder nichts Offenes mehr hat, erscheint nicht."""
    rows = db.conn().execute("SELECT t.kind, t.ref_id, u.name FROM toread_rec t JOIN user u ON u.id=t.from_user"
                             " WHERE t.to_user=? AND t.state='open' ORDER BY t.created DESC", (user_id,)).fetchall()
    if not rows:
        return []
    ctx = ctx or _Ctx(user_id)
    out: dict[str, dict] = {}
    for r in rows:
        key = _key(r["kind"], r["ref_id"])
        if key in listed:
            continue
        if key not in out:
            x = ctx.entry(r["kind"], r["ref_id"])
            if not x:
                continue
            out[key] = {**x, "source": "rec", "from": []}
        if r["name"] not in out[key]["from"]:
            out[key]["from"].append(r["name"])
    return list(out.values())


def decide_rec(user_id: int, key: str, action: str) -> None:
    """Empfehlung annehmen (zuoberst oder zuunterst auf die Liste) oder ablehnen."""
    kind, ref = _parse(key)
    if action not in ("top", "bottom", "no"):
        raise ValueError("Unbekannte Auswahl")
    if action != "no":
        place(user_id, kind, ref, action == "top")
    db.conn().execute("UPDATE toread_rec SET state=? WHERE to_user=? AND kind=? AND ref_id=? AND state='open'",
                      ("declined" if action == "no" else "accepted", user_id, kind, ref))


def _parse(key: str) -> tuple[str, int]:
    if len(key) < 2 or key[0] not in "eb" or not key[1:].isdigit():
        raise ValueError("Unbekannter Eintrag")
    return ("edition" if key[0] == "e" else "book"), int(key[1:])


# ---------- Vorschläge unter der Liste ----------

HIGH = 4.5  # Schnitt der eigenen Sterne für «hoch bewertet» (Sterne gehen bis 5)
MOSTLY = 0.8  # Anteil gehörter Bände für «fast fertig gehört»


def suggestions(user_id: int, ctx: _Ctx | None = None, skip: set[str] = frozenset(), limit: int = 30) -> list[dict]:
    """Serien mit einem nächsten, schon erschienenen Band, die der Benutzer hoch bewertet (Schnitt ab 4,5 Sternen) oder zu
    mindestens 80 % gehört hat. «Nicht auf die Liste» blendet eine Serie aus, bis ein anderer Band der nächste ist."""
    from . import ratings
    ctx = ctx or _Ctx(user_id)
    c = db.conn()
    stars = ratings.stars_of(user_id)
    dismissed = {r["edition_id"]: r["book_id"] for r in c.execute("SELECT edition_id, book_id FROM toread_dismiss WHERE user_id=?", (user_id,))}
    out = []
    for eid, (s, e) in ctx.eds.items():
        key = _key("edition", eid)
        if key in skip:
            continue
        nxt = next_of(e)
        if not nxt or nxt[1] == "upcoming" or dismissed.get(eid) == nxt[0]["id"]:
            continue
        x = ctx.entry("edition", eid)
        st = x["stats"]
        if not st["done"]:
            continue
        mine = [stars[b["id"]] for b, _ in _numbered(e) if b["id"] in stars]
        avg = sum(mine) / len(mine) if mine else None
        why = []
        if avg is not None and avg >= HIGH:
            why.append(f"{avg:.1f} Sterne".replace(".", ","))
        if st["total"] and st["done"] / st["total"] >= MOSTLY:
            why.append(f"{st['done']} von {st['total']} Bänden gehört")
        if not why:
            continue
        out.append({**x, "source": "suggest", "why": why, "stars": avg})
    out.sort(key=lambda x: (x["book"]["release_date"] or "", x["stars"] or 0), reverse=True)  # neue Bände zuerst
    return out[:limit]


def decide_suggestion(user_id: int, edition_id: int, action: str) -> None:
    if action not in ("top", "bottom", "no"):
        raise ValueError("Unbekannte Auswahl")
    if action != "no":
        place(user_id, "edition", edition_id, action == "top")
        return
    ctx = _Ctx(user_id)
    se = ctx.eds.get(edition_id)
    nxt = next_of(se[1]) if se else None
    db.conn().execute("INSERT OR REPLACE INTO toread_dismiss(user_id, edition_id, book_id, created) VALUES(?,?,?,?)",
                      (user_id, edition_id, nxt[0]["id"] if nxt else None, _now()))


def on_finished(c, user_id: int, book_ids) -> int:
    """Nach dem Fertighören: Serie mit weiterem Band oben einreihen (ausser der Benutzer hat sie entfernt).
    Liefert die Anzahl neu eingereihter Serien."""
    from . import api
    ids = set(book_ids)
    if not ids:
        return 0
    eds = {r["edition_id"] for r in c.execute(
        f"SELECT DISTINCT edition_id FROM book WHERE id IN ({','.join('?' * len(ids))}) AND edition_id IS NOT NULL", tuple(ids))}
    skip = {r[0] for r in c.execute("SELECT edition_id FROM toread_skip WHERE user_id=?", (user_id,))}
    eds -= skip
    if not eds:
        return 0
    found = {e["id"]: e for s in api.load(user_id).values() for e in s["editions"] if e["id"] in eds}
    n = 0
    for eid, e in found.items():
        if next_of(e):
            n += add(user_id, "edition", eid, top=True, source="auto")
    return n


def resolve(asin: str, lang: str, kind: str) -> tuple[str, int] | None:
    """Welcher Eintrag gehört zu einem Titel aus Entdecken? Serie (über die Serien-ASIN oder die Fassung des Buchs)
    oder Einzelbuch. None, wenn ABW den Titel noch nicht kennt."""
    c = db.conn()
    if kind == "series":
        e = c.execute("SELECT id FROM edition WHERE audible_asin=? ORDER BY lang<>?, variant IS NOT NULL, base_id IS NOT NULL LIMIT 1",
                      (asin, lang)).fetchone()
        return ("edition", e["id"]) if e else None
    b = c.execute("SELECT id, edition_id FROM book WHERE asin=? ORDER BY edition_id IS NULL, owned DESC LIMIT 1", (asin,)).fetchone()
    if not b:
        return None
    return ("edition", b["edition_id"]) if b["edition_id"] else ("book", b["id"])


def from_discover(user_id: int, item: dict) -> dict:
    """Knopf «Zu To Read» unter Entdecken: kennt ABW den Titel, kommt seine Serie (oder das Einzelbuch)
    auf die Liste; sonst wird er wie mit «Hinzufügen» in die Bibliothek aufgenommen und danach eingereiht."""
    from . import library
    asin = (item.get("asin") or "").strip()
    lang = item.get("lang") if item.get("lang") in ("en", "de") else config.main_lang()
    kind = "series" if item.get("kind") == "series" else "book"
    if not asin:
        raise ValueError("Die Audible-Nummer fehlt")
    ref = resolve(asin, lang, kind)
    added_to_library = False
    if not ref:
        db.conn().execute("INSERT OR IGNORE INTO toread_pending(user_id, asin, created) VALUES(?,?,?)", (user_id, asin, _now()))
        res = library.add({**item, "asin": asin, "lang": lang, "kind": kind}, user_id)
        added_to_library = True
        if not res.get("ok"):
            return {"ok": True, "pending": True, "message": res.get("message") or "Vorgemerkt; kommt auf To Read, sobald der Titel in der Bibliothek ist."}
        ref = resolve(asin, lang, kind)
        if not ref and res.get("series_id"):
            e = db.conn().execute("SELECT id FROM edition WHERE series_id=? ORDER BY lang<>?, variant IS NOT NULL LIMIT 1",
                                  (res["series_id"], lang)).fetchone()
            ref = ("edition", e["id"]) if e else None
        if not ref and res.get("book_id"):
            ref = ("book", res["book_id"])
        if not ref:
            return {"ok": True, "pending": True, "message": "Vorgemerkt; kommt auf To Read, sobald der Titel in der Bibliothek ist."}
    db.conn().execute("DELETE FROM toread_pending WHERE user_id=? AND asin=?", (user_id, asin))
    add(user_id, ref[0], ref[1])
    return {"ok": True, "key": _key(*ref), "library": added_to_library}


def after_process(asin: str, lang: str, kind: str) -> None:
    """Nach der Aufnahme in die Bibliothek: vorgemerkte «Zu To Read» einlösen."""
    c = db.conn()
    users = [r[0] for r in c.execute("SELECT user_id FROM toread_pending WHERE asin=?", (asin,))]
    ref = resolve(asin, lang, kind) if users else None
    if not ref:
        return
    for u in users:
        add(u, ref[0], ref[1])
    c.execute("DELETE FROM toread_pending WHERE asin=?", (asin,))


def asins(user_id: int) -> list[str]:
    """Audible-Nummern, die auf der Liste stehen (Serien-ASIN und alle Bände der Serie), für die Knöpfe unter Entdecken."""
    c = db.conn()
    out = {r[0] for r in c.execute(
        "SELECT b.asin FROM toread t JOIN book b ON (t.kind='book' AND b.id=t.ref_id) OR (t.kind='edition' AND b.edition_id=t.ref_id)"
        " WHERE t.user_id=? AND b.asin IS NOT NULL", (user_id,))}
    out |= {r[0] for r in c.execute("SELECT e.audible_asin FROM toread t JOIN edition e ON e.id=t.ref_id"
                                     " WHERE t.user_id=? AND t.kind='edition' AND e.audible_asin IS NOT NULL", (user_id,))}
    out |= {r[0] for r in c.execute("SELECT asin FROM toread_pending WHERE user_id=?", (user_id,))}
    return sorted(out)


# ---------- Drei Serien aus Entdecken unter der Liste ----------

PICKS = 3


def _genre(item: dict) -> str | None:
    """Hauptgenre eines Vorschlags: erster spezifischer Tag, sonst erster Tag, sonst Audible-Genre."""
    from . import tags
    t = item.get("tags") or []
    return next((x for x in t if x not in tags.GENERIC), None) or (t[0] if t else None) or ((item.get("fit_genres") or [None])[0])


def picks(user_id: int) -> list[dict]:
    """Drei neue Serien aus «Für dich» in drei verschiedenen Genres, eine davon «Mal was anderes». Ohne Gehörtes,
    ohne Titel auf der Liste und ohne mit «Nicht auf die Liste» Abgelehntes."""
    from . import api, foryou
    c = db.conn()
    items = api._annotate(foryou.suggestions(user_id)["items"], user_id)
    on_list = set(asins(user_id))
    gone = {r[0] for r in c.execute("SELECT asin FROM toread_pick_dismiss WHERE user_id=?", (user_id,))}
    heard_series = {r[0] for r in c.execute("SELECT DISTINCT e.audible_asin FROM user_progress p JOIN book b ON b.id=p.book_id"
                                            " JOIN edition e ON e.id=b.edition_id WHERE p.user_id=? AND (p.finished=1 OR p.progress>0.01)"
                                            " AND e.audible_asin IS NOT NULL", (user_id,))}

    def ok(i: dict) -> bool:
        keys = {i["asin"], i.get("series_asin")} - {None}
        return not (i["heard"] or keys & on_list or keys & gone or (i.get("series_asin") in heard_series))

    pool = [i for i in items if ok(i)]
    out: list[dict] = []
    used: set[str] = set()
    explore = next((i for i in pool if i.get("explore")), None)
    if explore:
        out.append(explore)
        used.add(_genre(explore) or "")
    for i in pool:
        if len(out) >= PICKS:
            break
        g = _genre(i)
        if i in out or not g or g in used:
            continue
        out.append(i)
        used.add(g)
    out.sort(key=lambda i: bool(i.get("explore")))  # «Mal was anderes» zuletzt
    foryou.fill_series_info(out)  # Bandzahl und Gesamtdauer auch für Vorschläge hinter den ersten 60
    return [{"asin": i["asin"], "lang": i["lang"], "title": i["title"], "author": i["author"], "series": i.get("series"),
             "series_asin": i.get("series_asin"), "series_id": i.get("series_id"), "series_books": i.get("series_books"),
             "series_min": i.get("series_min"), "cover_url": i.get("cover_url"), "genre": _genre(i), "explore": bool(i.get("explore")),
             "because": i.get("because") or [], "rating": i.get("rating"), "in_library": i.get("in_library", False)} for i in out]


def decide_pick(user_id: int, item: dict, action: str) -> dict:
    """Zuoberst oder zuunterst: wie «Zu To Read» unter Entdecken (fehlt der Titel, kommt er zuerst in die Bibliothek),
    dann an die gewünschte Stelle. «Nicht auf die Liste»: dieser Vorschlag kommt nicht wieder."""
    if action not in ("top", "bottom", "no"):
        raise ValueError("Unbekannte Auswahl")
    asin = (item.get("asin") or "").strip()
    if not asin:
        raise ValueError("Die Audible-Nummer fehlt")
    if action == "no":
        c = db.conn()
        for a in {asin, item.get("series_asin")} - {None, ""}:
            c.execute("INSERT OR IGNORE INTO toread_pick_dismiss(user_id, asin, created) VALUES(?,?,?)", (user_id, a, _now()))
        return {"ok": True}
    res = from_discover(user_id, item)
    if res.get("key") and action == "top":
        kind, ref = _parse(res["key"])
        place(user_id, kind, ref, True)
    return res
