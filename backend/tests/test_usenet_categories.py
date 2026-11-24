"""Usenet-Suche fragt auch Audio/Other und Audio/Foreign: Treasure Maps führt die meisten deutschen Hörbücher
unter 3050 («Der Hundertjährige, der aus dem Fenster stieg und verschwand» fehlte am 20.11. deshalb)."""
from abw import config, usenet


def test_usenet_search_asks_audiobook_other_and_foreign_but_not_music(monkeypatch):
    seen = []

    class _Client:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            seen.append(params["categories"])

            class R:
                def raise_for_status(self):
                    return self

                def json(self):
                    return []
            return R()

    monkeypatch.setattr(usenet.httpx, "Client", _Client)
    monkeypatch.setattr(config, "USENET_INDEXER_IDS", [18])
    monkeypatch.setattr(config, "secret", lambda k: "x")
    usenet.search({"title": "Der Hundertjährige, der aus dem Fenster stieg und verschwand", "author": "Jonas Jonasson",
                   "series": "Der Hundertjährige", "sequence": "1", "subtitle": ""})
    assert seen and all(set(c) == {3030, 3050, 3060} for c in seen)
