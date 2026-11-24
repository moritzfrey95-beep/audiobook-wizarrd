import pytest

from abw import audible, db, wishlist

WIDGET = r"""var widget_code = '  <div class=\"gr_custom_container_\">\n <h2 class=\"gr_custom_header_\"><a href=\"https://x\">js<\/a><\/h2>\n
<div class=\"gr_custom_each_container_\">\n <div class=\"gr_custom_title_\">\n <a rel=\"nofollow\" href=\"https://www.goodreads.com/review/show/1\">The Way of Kings (The Stormlight Archive, #1)<\/a>\n <\/div>\n <div class=\"gr_custom_author_\">\n by <a rel=\"nofollow\" href=\"https://x\">Brandon Sanderson<\/a>\n <\/div>\n <\/div>\n
<div class=\"gr_custom_each_container_\">\n <div class=\"gr_custom_title_\">\n <a href=\"https://x\">Gibt Es &amp; Nicht<\/a>\n <\/div>\n <div class=\"gr_custom_author_\">\n by <a href=\"https://x\">Nie Mand<\/a>\n <\/div>\n <\/div>'"""
CSV = 'Book Id,Title,Author,Exclusive Shelf,Bookshelves\n1,Schon Da,Brandon Sanderson,to-read,"to-read, fantasy"\n2,Gelesen,Jemand,read,\n'


def test_benutzernummer_aus_adresse():
    assert wishlist.parse_user("12345") == "12345"
    assert wishlist.parse_user("https://www.goodreads.com/user/show/12345-max") == "12345"
    with pytest.raises(ValueError):
        wishlist.parse_user("max")


def test_widget_und_export_lesen():
    assert wishlist.parse_widget(WIDGET) == [{"title": "The Way of Kings (The Stormlight Archive, #1)", "author": "Brandon Sanderson"},
                                             {"title": "Gibt Es & Nicht", "author": "Nie Mand"}]
    assert wishlist.parse_csv(CSV, "to-read") == [{"title": "Schon Da", "author": "Brandon Sanderson"}]
    with pytest.raises(ValueError):
        wishlist.parse_csv("a,b\n1,2\n", "to-read")


def test_abgleich_nimmt_treffer_in_die_bibliothek_auf(monkeypatch):
    db.init()
    c = db.conn()
    for t in ("wishlist_item", "watchlist", "user", "book", "edition", "series"):
        c.execute(f"DELETE FROM {t}")
    c.execute("INSERT INTO book(lang, title, asin, owned) VALUES('en','Schon Da','B0SCHONDA1',1)")
    db.set_setting("goodreads_user", "12345")
    monkeypatch.setattr(wishlist, "fetch", lambda uid, shelf: wishlist.parse_widget(WIDGET))
    assert wishlist.import_csv(CSV) == {"total": 1, "new": 1}
    calls = []
    prod = lambda asin, title: {"asin": asin, "title": title, "language": "english", "authors": [{"name": "Brandon Sanderson"}]}

    def fake(lang, path, **params):
        if "asins" in params:  # Aufnahme in die Bibliothek fragt das Buch selbst ab
            return {"products": [prod(params["asins"], "The Way of Kings")]}
        calls.append(params["title"])
        if lang != "en":
            return {}
        return {"products": {"The Way of Kings": [prod("B0WRONG001", "The Way of Kings Companion"), prod("B003P2WO5E", "The Way of Kings")],
                             "Schon Da": [prod("B0SCHONDA1", "Schon Da")]}.get(params["title"], [])}
    monkeypatch.setattr(audible, "_get", fake)
    msg = wishlist.sync()
    assert "2 Bücher in den Goodreads-Regalen" in msg and "3 bei Audible gesucht" in msg and "1 in die Bibliothek" in msg
    assert [(r["asin"], r["state"]) for r in c.execute("SELECT asin, state FROM watchlist")] == [("B003P2WO5E", "done")]
    # Buch ohne Serie: steht als fehlendes Einzelbuch auf der Suchliste
    b = c.execute("SELECT owned, wanted, wanted_by, edition_id FROM book WHERE asin='B003P2WO5E'").fetchone()
    assert b["owned"] == 0 and b["wanted"] and b["wanted_by"] == 1 and b["edition_id"] is None
    o = wishlist.overview()
    assert (o["added"], o["owned"], o["waiting"], [m["title"] for m in o["missing"]]) == (1, 1, 0, ["Gibt Es & Nicht"])
    n = len(calls)
    c.execute("DELETE FROM watchlist")
    wishlist.sync()  # schon geprüfte Einträge werden nicht erneut gesucht, entfernte Einträge nicht wieder angelegt
    assert len(calls) == n and not c.execute("SELECT 1 FROM watchlist").fetchone()
    db.set_setting("goodreads_user", "")


def test_sammelband_wunsch_wird_zur_serie():
    from abw.library import is_bundle
    assert is_bundle({"title": "System School: The Complete Series", "sequence": "1-5", "series": "The System School"})
    assert is_bundle({"title": "The Expanse Box Set", "sequence": None, "series": "The Expanse"})
    assert is_bundle({"title": "Cradle: The Complete Series", "sequence": None, "series": "Cradle"})
    assert not is_bundle({"title": "The Accidental Summoning", "sequence": "1", "series": "The System School"})
    assert not is_bundle({"title": "Standalone", "sequence": None, "series": None})
