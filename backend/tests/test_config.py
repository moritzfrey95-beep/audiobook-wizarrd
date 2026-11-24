"""Einstellungen: gespeicherte Werte gehen vor .env, Zugangsdaten verlassen den Server nicht."""
import pytest
from fastapi.testclient import TestClient

from abw import config, db
from abw.api import app


@pytest.fixture
def client(monkeypatch):
    db.init()
    monkeypatch.setenv("PROWLARR_URL", "http://env-prowlarr:9696")
    monkeypatch.setenv("PROWLARR_API_KEY", "aus-env")
    monkeypatch.setenv("ABS_LIBRARY_EN", "lib-en")
    monkeypatch.delenv("ABS_LIBRARY_DE", raising=False)
    config.reload(db.conn())
    yield TestClient(app)
    db.conn().execute("DELETE FROM setting WHERE key LIKE 'cfg:%'")
    monkeypatch.undo()
    config.reload()


def test_env_dann_datenbank(client):
    f = client.get("/api/config").json()["fields"]
    assert f["PROWLARR_URL"] == {"name": "PROWLARR_URL", "kind": "url", "group": "prowlarr", "source": "env", "value": "http://env-prowlarr:9696"}
    assert f["PROWLARR_API_KEY"]["value"] == "" and f["PROWLARR_API_KEY"]["set"]
    r = client.put("/api/config", json={"values": {"PROWLARR_URL": "http://neu:9696/", "USENET_INDEXER_IDS": "3, 4", "PROWLARR_API_KEY": ""}})
    assert r.status_code == 200 and r.json()["fields"]["PROWLARR_URL"]["source"] == "db"
    assert config.PROWLARR_URL == "http://neu:9696" and config.USENET_INDEXER_IDS == [3, 4]
    assert config.secret("PROWLARR_API_KEY") == "aus-env"  # leerer Text lässt den Schlüssel stehen
    client.put("/api/config", json={"values": {"PROWLARR_API_KEY": "geheim-123"}})
    assert config.secret("PROWLARR_API_KEY") == "geheim-123" and "geheim-123" not in client.get("/api/config").text
    client.put("/api/config", json={"values": {"PROWLARR_URL": None}})
    assert config.PROWLARR_URL == "http://env-prowlarr:9696"


def test_pruefung_der_eingaben(client):
    assert client.put("/api/config", json={"values": {"PROWLARR_URL": "prowlarr:9696"}}).status_code == 400
    assert client.put("/api/config", json={"values": {"USENET_INDEXER_IDS": "a,b"}}).status_code == 400
    assert client.put("/api/config", json={"values": {"LIBRARY_EN_ROOT": "relativ"}}).status_code == 400
    assert client.put("/api/config", json={"values": {"GIBTS_NICHT": "x"}}).status_code == 400


def test_sprachen_folgen_den_bibliotheken(client):
    assert config.LANGS == ["en"] and client.get("/api/auth/state").json()["langs"] == ["en"]
    client.put("/api/config", json={"values": {"ABS_LIBRARY_DE": "lib-de"}})
    assert config.LANGS == ["en", "de"] and config.ABS_LIBRARIES == {"en": "lib-en", "de": "lib-de"}


def test_ordner_pruefen(client, tmp_path):
    r = client.post("/api/config/test", json={"service": "path:LIBRARY_EN_ROOT", "values": {"LIBRARY_EN_ROOT": str(tmp_path)}}).json()
    assert r["ok"]
    r = client.post("/api/config/test", json={"service": "path:LIBRARY_EN_ROOT", "values": {"LIBRARY_EN_ROOT": str(tmp_path / "fehlt")}}).json()
    assert not r["ok"]
    assert client.post("/api/config/test", json={"service": "path:ABS_URL"}).status_code == 400


def test_wizard_bis_audiobookshelf_eingerichtet_ist(client, monkeypatch):
    db.set_setting("setup_done", "0")
    monkeypatch.delenv("ABS_LIBRARY_EN", raising=False)
    config.reload(db.conn())
    assert client.get("/api/setup").json()["needed"]
    assert client.post("/api/setup/done").status_code == 400
    client.put("/api/config", json={"values": {"ABS_URL": "http://abs:80", "ABS_API_KEY": "k", "ABS_LIBRARY_EN": "lib"}})
    r = client.post("/api/setup/done").json()
    assert not r["needed"] and r["abs_ok"] and not r["auto_on"]
