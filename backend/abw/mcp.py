"""MCP-Server (Model Context Protocol) für KI-Assistenten wie Claude: Was fehlt in Serie X? Was erscheint bald?
Meldungen lesen, im Audible-Katalog suchen, und auf Wunsch Bücher hinzufügen.

Übertragung: «Streamable HTTP» unter POST /api/mcp (JSON-RPC 2.0, Antwort als JSON). Anmeldung mit einem API-Schlüssel
(`Authorization: Bearer abw_…`), die Rechte sind die des Benutzers. Standard nur lesend; Werkzeuge, die etwas ändern,
schaltet ein Administrator unter Einstellungen frei (`mcp_write`).
"""
import json
from datetime import date, timedelta

from . import __version__, audible, db, library, metrics, notifications

PROTOCOL = "2025-06-18"

TOOLS = {
    "find_series": {
        "description": "Serien der Bibliothek nach Name oder Autor suchen. Liefert je Serie die Sprachfassungen mit Bänden im Bestand, fehlenden und angekündigten Bänden.",
        "schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Teil des Serien- oder Autornamens"}}, "required": ["query"]},
    },
    "series_missing": {
        "description": "Fehlende und angekündigte Bände einer Serie (id aus find_series), mit Erscheinungsdatum und Stand.",
        "schema": {"type": "object", "properties": {"series_id": {"type": "integer"}}, "required": ["series_id"]},
    },
    "upcoming": {
        "description": "Angekündigte Hörbücher der nächsten Tage, standardmässig nur markierte Serien und Autoren des Benutzers.",
        "schema": {"type": "object", "properties": {"days": {"type": "integer", "default": 60}, "only_followed": {"type": "boolean", "default": True}}},
    },
    "notifications": {
        "description": "Die neuesten Meldungen des Benutzers (neu im Bestand, angekündigt, erschienen, Empfehlungen).",
        "schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}, "unread_only": {"type": "boolean", "default": False}}},
    },
    "library_stats": {
        "description": "Kennzahlen: Bücher im Bestand, fehlende Bände, laufende Downloads, offene Prüfungen, Hörstunden der Woche, nächste Erscheinung.",
        "schema": {"type": "object", "properties": {}},
    },
    "search_audible": {
        "description": "Im Audible-Katalog suchen (Titel, Autor, Serie). Liefert ASIN, Sprache und ob es schon im Bestand ist.",
        "schema": {"type": "object", "properties": {"query": {"type": "string"}, "lang": {"type": "string", "enum": ["en", "de"], "default": "en"}}, "required": ["query"]},
    },
    "add_wish": {
        "description": "Buch oder Serie aus dem Audible-Katalog (ASIN aus search_audible) zur Bibliothek hinzufügen; ABW sucht es dann mit Vorrang. Ändert Daten.",
        "schema": {"type": "object", "properties": {"asin": {"type": "string"}, "lang": {"type": "string", "enum": ["en", "de"]},
                                                   "kind": {"type": "string", "enum": ["book", "series"], "default": "book"}, "title": {"type": "string"}},
                   "required": ["asin", "lang"]},
        "write": True,
    },
}


def writable() -> bool:
    return db.setting("mcp_write") == "1"


def tool_list() -> list[dict]:
    return [{"name": k, "description": v["description"], "inputSchema": v["schema"],
             "annotations": {"readOnlyHint": not v.get("write"), "destructiveHint": False}}
            for k, v in TOOLS.items() if not v.get("write") or writable()]


def _series_brief(s: dict) -> dict:
    eds = []
    for e in s["editions"]:
        eds.append({"lang": e["lang"], "name": e["name"], "monitored": bool(e["monitored"]), "owned": e["owned"],
                    "missing": e["missing"], "upcoming": e["upcoming"], "next_release": e.get("next_release")})
    return {"id": s["id"], "name": s["name"], "author": s.get("author"), "editions": eds}


def call(name: str, args: dict, user: dict, load, calendar) -> object:
    uid = user["id"]
    if name == "find_series":
        q = db.norm(args.get("query") or "")
        if len(q) < 2:
            raise ValueError("Suchtext zu kurz")
        hits = [s for s in load(uid).values() if q in db.norm(s["name"]) or q in db.norm(s.get("author") or "")]
        return [_series_brief(s) for s in hits[:20]]
    if name == "series_missing":
        s = load(uid).get(int(args["series_id"]))
        if not s:
            raise ValueError("Serie nicht gefunden")
        out = []
        for e in s["editions"]:
            for b, st in zip(e["_books"], e["_states"]):
                if st != "owned":
                    out.append({"lang": e["lang"], "edition": e["name"], "sequence": b["sequence"], "title": b["title"],
                                "release_date": b["release_date"], "state": st, "asin": b["asin"]})
        return {"series": s["name"], "books": out}
    if name == "upcoming":
        until = (date.today() + timedelta(days=int(args.get("days") or 60))).isoformat()
        f = notifications.follows(uid)
        series, authors = set(f["series"]), set(f["authors"])
        rows = [e for e in calendar() if e["release_date"] and e["release_date"] <= until]
        if args.get("only_followed", True):
            rows = [e for e in rows if e["series_id"] in series or db.norm(e.get("author") or "") in authors]
        return [{k: e[k] for k in ("release_date", "series", "sequence", "title", "author", "lang", "series_id")} for e in rows[:100]]
    if name == "notifications":
        res = notifications.listing(uid, limit=min(int(args.get("limit") or 20), 100), unread_only=bool(args.get("unread_only")))
        return [{k: n[k] for k in ("kind", "title", "body", "created", "read_at")} for n in res["items"]]
    if name == "library_stats":
        return metrics.collect()
    if name == "search_audible":
        lang = args.get("lang") or "en"
        owned = {r["asin"] for r in db.conn().execute("SELECT asin FROM book WHERE owned=1 AND asin IS NOT NULL")}
        return [{"asin": b["asin"], "lang": lang, "title": b["title"], "author": b["author"], "series": b.get("series"),
                 "sequence": b.get("sequence"), "release_date": b.get("release_date"), "owned": b["asin"] in owned}
                for b in audible.search(args["query"], lang)[:15]]
    if name == "add_wish":
        if not writable():
            raise PermissionError("Ändernde Werkzeuge sind in ABW nicht freigeschaltet")
        return library.add({"asin": args["asin"], "lang": args["lang"], "kind": args.get("kind") or "book", "title": args.get("title")}, uid)
    raise LookupError(f"Unbekanntes Werkzeug: {name}")


def _ok(rid, result) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": text}}


def handle(msg: dict, user: dict, load, calendar) -> dict | None:
    """Eine JSON-RPC-Nachricht. Benachrichtigungen (ohne id) bekommen keine Antwort."""
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if rid is None:
        return None
    if method == "initialize":
        return _ok(rid, {"protocolVersion": params.get("protocolVersion") or PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "audiobook-wizarrd", "version": __version__},
                         "instructions": "Hörbuch-Bibliothek AudioBook Wizarrd: Serien, fehlende und angekündigte Bände, Meldungen."})
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": tool_list()})
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS or (TOOLS[name].get("write") and not writable()):
            return _err(rid, -32602, f"Unbekanntes Werkzeug: {name}")
        try:
            data = call(name, params.get("arguments") or {}, user, load, calendar)
            return _ok(rid, {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}], "isError": False})
        except KeyError as exc:  # Pflichtangabe fehlt
            return _ok(rid, {"content": [{"type": "text", "text": f"Angabe fehlt: {exc.args[0]}"}], "isError": True})
        except Exception as exc:  # noqa: BLE001 - Fehler gehen als Werkzeug-Ergebnis an das Modell
            return _ok(rid, {"content": [{"type": "text", "text": str(exc)[:500]}], "isError": True})
    return _err(rid, -32601, f"Methode nicht unterstützt: {method}")
