"""Autoren zusammenführen: Schreibweisen zeigen auf den geführten Namen, Markierungen ziehen mit, Trennen geht zurück."""
from fastapi.testclient import TestClient

from abw import abs as abs_mod
from abw import api, authors, db, notifications


def setup():
    db.init()
    c = db.conn()
    for t in ("book", "edition", "series", "follow", "author_alias", "author_catalog", "author_fetch"):
        c.execute(f"DELETE FROM {t}")
    abs_mod.reset_aliases()
    for i, a in enumerate(("J.R.R. Tolkien", "J. R. R. Tolkien", "John Ronald Reuel Tolkien", "Christopher Tolkien", "A. G. Riddle")):
        c.execute("INSERT INTO book(lang, asin, title, owned, author) VALUES('en', ?, ?, 1, ?)", (f"T{i}", f"Buch {i}", a))
    return c


def test_vorschlaege():
    setup()
    pairs = {tuple(sorted((s["a"]["name"], s["b"]["name"]))) for s in authors.suggestions()}
    # «J.R.R.» und «J. R. R.» haben schon denselben Schlüssel und gelten als ein Autor
    assert pairs == {("J.R.R. Tolkien", "John Ronald Reuel Tolkien")}
    assert not any("Christopher" in " ".join(p) for p in pairs)  # anderer Vorname
    assert authors._compatible(["a", "g"], ["andrew", "gerald"]) and not authors._compatible(["a"], ["b"])


def test_zusammenfuehren_und_trennen():
    c = setup()
    alias, target = db.norm("John Ronald Reuel Tolkien"), db.norm("J.R.R. Tolkien")
    notifications.follow(5, "author", alias)
    client = TestClient(api.app)
    r = client.post(f"/api/authors/{alias}/merge", json={"into": target})
    assert r.status_code == 200 and r.json()["name"] == "J.R.R. Tolkien"
    assert abs_mod.primary_author("John Ronald Reuel Tolkien, Rob Inglis - narrator") == "J.R.R. Tolkien"
    assert notifications.follows(5)["authors"] == [target]
    assert [a["name"] for a in client.get(f"/api/authors/{target}").json()["aliases"]] == ["John Ronald Reuel Tolkien"]
    keys = {a["key"]: a["en"] for a in client.get("/api/authors").json()}
    assert alias not in keys and keys[target] == 3
    assert client.post(f"/api/authors/{target}/merge", json={"into": target}).status_code == 400
    assert client.delete(f"/api/authors/alias/{alias}").status_code == 200
    assert abs_mod.primary_author("John Ronald Reuel Tolkien") == "John Ronald Reuel Tolkien"
    assert client.delete(f"/api/authors/alias/{alias}").status_code == 404


def test_keine_ketten():
    setup()
    a, b, t = db.norm("J. R. R. Tolkien"), db.norm("John Ronald Reuel Tolkien"), db.norm("J.R.R. Tolkien")
    authors.merge(a, "J. R. R. Tolkien", "John Ronald Reuel Tolkien")
    authors.merge(b, "John Ronald Reuel Tolkien", "J.R.R. Tolkien")
    assert abs_mod.primary_author("J. R. R. Tolkien") == "J.R.R. Tolkien"
    assert sorted(authors.names_of(t)) == ["J. R. R. Tolkien", "John Ronald Reuel Tolkien"]
    abs_mod.reset_aliases()
    db.conn().execute("DELETE FROM author_alias")
    abs_mod.reset_aliases()
