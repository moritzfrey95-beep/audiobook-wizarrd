"""Meldungen auf Englisch, wenn die Oberfläche Englisch ist; Vorlagen mit eingesetzten Werten."""
from abw import i18n


def test_vorlagen_und_genaue_treffer(monkeypatch):
    monkeypatch.setattr(i18n, "_exact", {"Bitte anmelden.": "Please sign in."})
    monkeypatch.setattr(i18n, "_patterns", [])
    import re
    i18n._patterns.append((re.compile(r"^Audible ist gerade nicht erreichbar: (?P<p0>.+?)$", re.S), "Audible is not reachable right now: {0}"))
    assert i18n.tr("Bitte anmelden.", "en") == "Please sign in."
    assert i18n.tr("Bitte anmelden.", "de") == "Bitte anmelden."
    assert i18n.tr("Audible ist gerade nicht erreichbar: Zeitüberschreitung", "en") == "Audible is not reachable right now: Zeitüberschreitung"
    assert i18n.tr("Unbekannt", "en") == "Unbekannt"
    assert i18n.pick("en-GB") == "en" and i18n.pick(None) == "de"


def test_fehler_der_schnittstelle_auf_englisch(monkeypatch):
    from fastapi.testclient import TestClient
    from abw import db
    from abw.api import app
    db.init()
    monkeypatch.setattr(i18n, "_exact", {"Unbekannter Dienst": "Unknown service"})
    c = TestClient(app)
    assert c.post("/api/config/test", json={"service": "x"}, headers={"X-ABW-Lang": "en"}).json()["detail"] == "Unknown service"
    assert c.post("/api/config/test", json={"service": "x"}).json()["detail"] == "Unbekannter Dienst"


def test_meldungen_uebersetzt():
    from abw import i18n
    assert i18n.tr("Neuer Band angekündigt", "en") == "New volume announced"
    assert i18n.tr("The Expanse, Band 10: Leviathan Falls (erscheint am 01.02.2027)", "en") == "The Expanse, volume 10: Leviathan Falls (out on 01.02.2027)"
    assert i18n.tr("Dune, Band 2: Dune Messiah", "en") == "Dune, volume 2: Dune Messiah"
    assert i18n.tr("Alex empfiehlt dir «Red Rising»", "en") == "Alex recommends “Red Rising” to you"
    assert i18n.tr("Neue Serie von Brandon Sanderson", "en") == "New series by Brandon Sanderson"
