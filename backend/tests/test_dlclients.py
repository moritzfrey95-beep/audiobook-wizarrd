"""Weitere Download-Clients: NZBGet, Transmission, Deluge, Debrid. Antworten nachgebildet, keine echten Dienste."""
import time

import httpx

from abw import config, dlclients, torrent, usenet


def test_nzbget_auftrag_und_stand(monkeypatch):
    calls = []

    def call(self, method, *params):
        calls.append((method, params))
        if method == "append":
            return 17
        if method == "listgroups":
            return [{"NZBID": 17, "Status": "DOWNLOADING", "FileSizeMB": 100, "RemainingSizeMB": 25}]
        if method == "history":
            return [{"NZBID": 18, "Status": "SUCCESS/ALL", "FinalDir": "/dl/abw-2"}, {"NZBID": 19, "Status": "FAILURE/PAR"}]
    monkeypatch.setattr(dlclients.NZBGet, "call", call)
    monkeypatch.setattr(config, "USENET_CLIENT", "nzbget")
    assert usenet.submit(b"<nzb/>", "abw-1-x") == "17"
    assert calls[0][1][0] == "abw-1-x.nzb" and calls[0][1][2] == config.SAB_CATEGORY
    st = usenet.status({"17", "18", "19"})
    assert st["17"]["state"] == "downloading" and abs(st["17"]["progress"] - 0.75) < 0.01
    assert st["18"] == {"state": "arrived", "progress": 1.0, "path": "/dl/abw-2"} and st["19"]["state"] == "failed"


def test_transmission_sitzung_und_stand(monkeypatch):
    seen = []

    class R:
        def __init__(self, code, data=None, sid=""):
            self.status_code, self._d, self.headers = code, data, {"X-Transmission-Session-Id": sid}

        def raise_for_status(self):
            return self

        def json(self):
            return self._d

    def post(url, json, auth, timeout, headers):
        seen.append(headers["X-Transmission-Session-Id"])
        if not headers["X-Transmission-Session-Id"]:
            return R(409, sid="abc")
        if json["method"] == "torrent-add":
            assert json["arguments"]["download-dir"].endswith("/abw-1-x") and json["arguments"]["labels"] == ["abw-1-x"]
            return R(200, {"result": "success", "arguments": {"torrent-added": {"hashString": "h1"}}})
        return R(200, {"result": "success", "arguments": {"torrents": [
            {"hashString": "h1", "percentDone": 1.0, "status": 6, "error": 0, "addedDate": 1, "activityDate": 2, "metadataPercentComplete": 1}]}})
    monkeypatch.setattr(dlclients.httpx, "post", post)
    monkeypatch.setattr(config, "QBIT_URL", "http://tr:9091")
    monkeypatch.setattr(config, "QBIT_SAVE_ROOT", "/downloads")
    t = dlclients.Transmission(user="", password="")
    assert t.url == "http://tr:9091/transmission/rpc"
    assert t.add({"magnet": "magnet:?xt=urn:btih:x"}, "abw-1-x") == "h1" and seen[:2] == ["", "abc"]
    assert t.get("h1", "abw-1-x")["state"] == "done"


def test_deluge_zustaende(monkeypatch):
    d = dlclients.Deluge.__new__(dlclients.Deluge)
    answers = {"a": {"progress": 0, "state": "Downloading", "total_size": 0, "time_added": time.time()},
               "b": {"progress": 50.0, "state": "Downloading", "total_size": 10, "time_added": 1, "time_since_transfer": 5},
               "c": {"progress": 100.0, "state": "Seeding", "total_size": 10, "time_added": 1},
               "e": {"progress": 3.0, "state": "Error", "total_size": 10, "time_added": 1, "message": "Kaputt"}}
    d.call = lambda method, *p: answers.get(p[0])
    assert d.get("a", "")["state"] == "meta"
    b = d.get("b", "")
    assert b["state"] == "downloading" and b["progress"] == 0.5 and time.time() - b["last_activity"] < 10
    assert d.get("c", "")["state"] == "done" and d.get("e", "")["error"] == "Kaputt" and d.get("x", "") is None


class FakeClient:
    def __init__(self, states):
        self.states, self.stopped, self.removed = states, [], []

    def get(self, cid, marker):
        return self.states.get(cid)

    def stop(self, cid):
        self.stopped.append(cid)

    def remove(self, cid, delete):
        self.removed.append((cid, delete))


def test_stand_fuer_alle_clients():
    now = time.time()
    fc = FakeClient({"done": {"state": "done", "progress": 1, "added_on": now},
                     "meta": {"state": "meta", "progress": 0, "added_on": now - 3600},
                     "slow": {"state": "downloading", "progress": 0.4, "added_on": now - 90000, "last_activity": now - 50000},
                     "run": {"state": "downloading", "progress": 0.4, "added_on": now, "last_activity": now}})
    rows = [{"id": i, "client_id": c, "marker": f"abw-{i}"} for i, c in enumerate(("done", "meta", "slow", "run", "weg"))]
    st = torrent.status_generic(rows, fc)
    assert st[0]["state"] == "arrived" and fc.stopped == ["done"]
    assert st[1]["state"] == "failed" and "keine Quelle" in st[1]["error"]
    assert st[2]["state"] == "failed" and "40 %" in st[2]["error"]
    assert st[3] == {"state": "downloading", "progress": 0.4}
    assert st[4]["state"] == "failed"
    assert ("meta", True) in fc.removed and ("slow", True) in fc.removed


def test_debrid_holt_dateien(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "TORRENT_COMPLETE_ROOT", tmp_path)
    real = httpx.Client

    def handler(request):
        return httpx.Response(200, content=b"x" * 1000)
    monkeypatch.setattr(dlclients.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))
    files = [("Buch/01.mp3", "https://cdn/1"), ("../02.mp3", "https://cdn/2")]
    first = dlclients._local("abw-9-x", lambda: files, 2000)
    assert first["state"] == "downloading"
    for _ in range(50):
        if dlclients._local("abw-9-x", lambda: files, 2000)["state"] == "done":
            break
        time.sleep(0.05)
    folder = tmp_path / "abw-9-x"
    assert (folder / "Buch" / "01.mp3").stat().st_size == 1000 and (folder / "02.mp3").is_file()  # «..» fällt weg
    assert not (tmp_path / ".teil-abw-9-x").exists()


def test_eingerichtet_je_client(monkeypatch):
    monkeypatch.setattr(config, "TORRENT_CLIENT", "torbox")
    monkeypatch.setattr(config, "secret", lambda n: "k" if n == "DEBRID_API_KEY" else "")
    assert torrent.client_ready()
    monkeypatch.setattr(config, "TORRENT_CLIENT", "transmission")
    monkeypatch.setattr(config, "QBIT_URL", "http://tr")
    monkeypatch.setattr(config, "QBIT_SAVE_ROOT", "/dl")
    assert torrent.client_ready()
    monkeypatch.setattr(config, "TORRENT_CLIENT", "qbittorrent")
    assert not torrent.client_ready()  # qBittorrent braucht Benutzer
    monkeypatch.setattr(config, "TORRENT_CLIENT", "unsinn")
    assert dlclients.torrent_client() == "qbittorrent"
