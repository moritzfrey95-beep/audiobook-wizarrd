"""Einstellbare Ordnerstruktur beim Ablegen."""
from fastapi.testclient import TestClient

from abw import config, db, folders, importer
from test_variants import book, series

MISTBORN = {"author": "Brandon Sanderson", "series": "Mistborn", "seq": "2", "title": "The Well of Ascension"}


def test_schema_mit_platzhaltern_und_wegfallenden_teilen():
    assert folders.render(folders.SERIES_DEFAULT, MISTBORN) == ("Mistborn", "Book 2 - The Well of Ascension")
    assert folders.render(folders.SERIES_DEFAULT, {**MISTBORN, "seq": ""}) == ("Mistborn", "The Well of Ascension")
    s = "{author}/{series}/<Book {seq}/>{title}"
    assert folders.render(s, MISTBORN) == ("Brandon Sanderson/Mistborn/Book 2", "The Well of Ascension")
    assert folders.render(s, {**MISTBORN, "seq": ""}) == ("Brandon Sanderson/Mistborn", "The Well of Ascension")
    # Unzulässige Zeichen aus Titeln werden ersetzt, nie neue Ordnerebenen
    assert folders.render("{author}/{title}", {"author": "A/B", "title": 'Was? "Nein"'}) == ("A B", "Was Nein")


def test_ungueltige_schemata():
    assert folders.problem("{autor}/{title}")
    assert folders.problem("{series}/<Book {seq}/{title}")
    assert folders.problem("{title}/{series}")
    assert folders.problem("../{series}/{title}")
    assert folders.problem("/abs/{series}/{title}")
    assert folders.problem("{author}/{title}")  # Serie ohne {series} und {seq}
    assert folders.problem("{author}/{title}", single=True) is None
    assert all(folders.problem(a) is None and folders.problem(b, single=True) is None for a, b in folders.PRESETS.values())


def test_bandnummer_erkennen():
    p = folders.volume_prefix("{author}/{series}/<{seq} - >{title}", "2")
    assert p.match("2 - X") and p.match("02 - X") and not p.match("21 - X") and not p.match("2.5 - X")
    assert folders.volume_prefix("{series}/{series} {seq} - {title}", "2") is None


def test_ablage_nach_schema(monkeypatch):
    db.init()
    c = db.conn()
    monkeypatch.setattr(config, "FOLDER_SERIES", "{author}/{series}/<Book {seq} - >{title}")
    monkeypatch.setattr(config, "FOLDER_SINGLE", "{author}/{title}")
    _, eid = series(c, "Schema Neu")
    bk = {"edition_id": eid, "sequence": "1", "title": "Erster", "series": "Schema Neu", "author": "Autorin A, Sprecher B", "lang": "en"}
    assert importer.target_rel(bk) == ("Autorin A/Schema Neu", "Book 1 - Erster")
    assert importer.target_rel({"edition_id": None, "lang": "en", "title": "Allein", "author": "Niemand Sonst"}) == ("Niemand Sonst", "Allein")
    # Vorhandene Bände in einem anderen Ordner: der neue kommt dazu
    book(c, eid, 1, "Erster", "N", path="Alte Ablage/Book 1 - Erster")
    assert importer.target_rel({**bk, "sequence": "2", "title": "Zweiter"}) == ("Alte Ablage", "Book 2 - Zweiter")


def test_eigener_ordner_je_band(monkeypatch):
    db.init()
    c = db.conn()
    monkeypatch.setattr(config, "FOLDER_SERIES", "{author}/{series}/<Book {seq}/>{title}")
    _, eid = series(c, "Je Band")
    book(c, eid, 1, "Erster", "N", path="Autorin A/Je Band/Book 1/Erster")
    bk = {"edition_id": eid, "series": "Je Band", "author": "Autorin A", "lang": "en"}
    # Nicht in den Ordner von Band 1, sondern daneben
    assert importer.target_rel({**bk, "sequence": "2", "title": "Zweiter"}) == ("Autorin A/Je Band/Book 2", "Zweiter")
    assert importer.target_rel({**bk, "sequence": "", "title": "Kurzgeschichte"}) == ("Autorin A/Je Band", "Kurzgeschichte")
    assert importer.same_volume("Book 1", "1") and not importer.same_volume("Book 12", "1")


def test_schema_speichern_und_vorschau(monkeypatch):
    db.init()
    from abw import api
    client = TestClient(api.app)
    r = client.post("/api/config/folders", json={"series": "{author}/{series}/<Book {seq} - >{title}", "single": "{author}/{title}"})
    assert r.json()["ok"] and r.json()["examples"][0] == "Brandon Sanderson/Mistborn/Book 2 - The Well of Ascension"
    assert set(r.json()["presets"]) == set(folders.PRESETS)
    assert not client.post("/api/config/folders", json={"series": "{autor}/{title}", "single": "{title}"}).json()["ok"]
    assert client.put("/api/config", json={"values": {"FOLDER_SERIES": "{title}"}}).status_code == 400
    try:
        assert client.put("/api/config", json={"values": {"FOLDER_SERIES": "{author}/{series}/<{seq} - >{title}"}}).status_code == 200
        assert config.FOLDER_SERIES == "{author}/{series}/<{seq} - >{title}"
    finally:
        config.save(db.conn(), {"FOLDER_SERIES": None})
    assert config.FOLDER_SERIES == folders.SERIES_DEFAULT
