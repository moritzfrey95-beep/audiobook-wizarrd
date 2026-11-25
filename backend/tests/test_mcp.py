"""MCP-Server: Handshake, Werkzeugliste, Aufrufe, Schreiben nur nach Freischaltung."""
import json

from fastapi.testclient import TestClient

from abw import audible, db, library
from abw.api import app


def rpc(c, method, params=None, rid=1):
    return c.post("/api/mcp", json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}).json()


def test_mcp(monkeypatch):
    db.init()
    c = TestClient(app)
    init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    assert init["result"]["serverInfo"]["name"] == "audiobook-wizarrd"
    assert c.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    db.set_setting("mcp_write", "0")
    names = {t["name"] for t in rpc(c, "tools/list")["result"]["tools"]}
    assert "find_series" in names and "add_wish" not in names
    stats = rpc(c, "tools/call", {"name": "library_stats", "arguments": {}})["result"]
    assert not stats["isError"] and "books_owned" in json.loads(stats["content"][0]["text"])
    assert rpc(c, "tools/call", {"name": "add_wish", "arguments": {"asin": "B0X", "lang": "en"}})["error"]["code"] == -32602
    bad = rpc(c, "tools/call", {"name": "series_missing", "arguments": {"series_id": 999999}})["result"]
    assert bad["isError"]
    monkeypatch.setattr(audible, "search", lambda q, lang: [{"asin": "B0SUCHE001", "title": "Treffer", "author": "A"}])
    hits = json.loads(rpc(c, "tools/call", {"name": "search_audible", "arguments": {"query": "x"}})["result"]["content"][0]["text"])
    assert hits[0]["asin"] == "B0SUCHE001" and hits[0]["owned"] is False
    db.set_setting("mcp_write", "1")
    added = []
    monkeypatch.setattr(library, "add", lambda item, uid: added.append(item) or {"ok": True})
    r = rpc(c, "tools/call", {"name": "add_wish", "arguments": {"asin": "B0SUCHE001", "lang": "en"}})["result"]
    assert not r["isError"] and added[0]["asin"] == "B0SUCHE001"
    r = rpc(c, "tools/call", {"name": "add_wish", "arguments": {"asin": "B0SUCHE001"}})["result"]
    assert r["isError"] and r["content"][0]["text"] == "Angabe fehlt: lang"
    db.set_setting("mcp_write", "0")
    assert rpc(c, "gibtsnicht")["error"]["code"] == -32601
