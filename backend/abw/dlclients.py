"""Weitere Download-Clients neben SABnzbd und qBittorrent.

Usenet: NZBGet (JSON-RPC). Torrent: Transmission, Deluge (Web-Oberfläche, JSON-RPC) und die Debrid-Dienste Real-Debrid und
TorBox. Es gilt dieselbe Regel wie bei SABnzbd und qBittorrent: ein Download landet im Ordner <fertige Downloads>/<Marker>.
Bei NZBGet ist das der Ordner der Kategorie, bei Transmission und Deluge der Speicherordner aus Sicht des Clients plus Marker.
Debrid-Dienste laden auf ihren Servern; ABW holt die fertigen Dateien danach selbst in den Ordner der fertigen Torrents.

Ein Torrent-Client liefert je Download ein einheitliches Bild:
  {"progress": 0..1, "state": meta|queued|downloading|done|error|moving, "added_on": Unix-Zeit, "last_activity": Unix-Zeit, "error": Text}
"""
import base64
import logging
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import httpx

from . import config

log = logging.getLogger("abw.dlclients")
TIMEOUT = 60
TORRENT_CLIENTS = ("qbittorrent", "transmission", "deluge", "realdebrid", "torbox")
USENET_CLIENTS = ("sabnzbd", "nzbget")


def usenet_client() -> str:
    c = (config.USENET_CLIENT or "sabnzbd").lower()
    return c if c in USENET_CLIENTS else "sabnzbd"


def torrent_client() -> str:
    c = (config.TORRENT_CLIENT or "qbittorrent").lower()
    return c if c in TORRENT_CLIENTS else "qbittorrent"


def _ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


# ---------- NZBGet ----------

class NZBGet:
    def __init__(self, url: str | None = None, user: str | None = None, password: str | None = None):
        url = (url or config.SAB_URL).rstrip("/")
        self.url = url if url.endswith("/jsonrpc") else url + "/jsonrpc"
        self.auth = (user if user is not None else config.secret("NZBGET_USERNAME"), password if password is not None else config.secret("NZBGET_PASSWORD"))

    def call(self, method: str, *params):
        r = httpx.post(self.url, json={"method": method, "params": list(params), "id": 1}, auth=self.auth, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
        if d.get("error"):
            raise RuntimeError(f"NZBGet lehnt ab: {d['error'].get('message') if isinstance(d['error'], dict) else d['error']}")
        return d.get("result")

    def submit(self, nzb: bytes, marker: str) -> str:
        # Name ohne .nzb wird der Ordnername unter dem Ordner der Kategorie
        nid = self.call("append", marker + ".nzb", base64.b64encode(nzb).decode(), config.SAB_CATEGORY, 0, False, False, "", 0, "SCORE", [])
        if not isinstance(nid, int) or nid <= 0:
            raise RuntimeError("NZBGet hat den Auftrag nicht bestätigt")
        return str(nid)

    def status(self, ids: set[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for g in self.call("listgroups", 0) or []:
            nid = str(g.get("NZBID"))
            if nid in ids:
                total = (g.get("FileSizeHi", 0) << 32) + g.get("FileSizeLo", 0) or g.get("FileSizeMB", 0) * 1048576
                left = (g.get("RemainingSizeHi", 0) << 32) + g.get("RemainingSizeLo", 0) or g.get("RemainingSizeMB", 0) * 1048576
                pct = 1 - left / total if total else 0
                st = str(g.get("Status", ""))
                out[nid] = {"state": "downloading" if pct > 0 or st.startswith(("DOWNLOADING", "PP_", "UNPACKING", "LOADING")) else "queued",
                            "progress": min(pct, 0.99)}
        for h in self.call("history", False) or []:
            nid = str(h.get("NZBID"))
            if nid in ids:
                st = str(h.get("Status", ""))
                if st.startswith("SUCCESS"):
                    out[nid] = {"state": "arrived", "progress": 1.0, "path": h.get("FinalDir") or h.get("DestDir")}
                elif st.startswith(("FAILURE", "DELETED")):
                    out[nid] = {"state": "failed", "error": f"NZBGet: {st}"}
                else:  # WARNING/…: fertig, aber mit Hinweis; die Ankunftsprüfung entscheidet
                    out[nid] = {"state": "arrived", "progress": 1.0, "path": h.get("FinalDir") or h.get("DestDir")}
        return out

    def version(self) -> str:
        return str(self.call("version") or "")


# ---------- Transmission ----------

class Transmission:
    STATUS = {0: "stopped", 1: "queued", 2: "checking", 3: "queued", 4: "downloading", 5: "queued", 6: "seeding"}

    def __init__(self, url: str | None = None, user: str | None = None, password: str | None = None):
        url = (url or config.QBIT_URL).rstrip("/")
        self.url = url if url.endswith("/rpc") else url + "/transmission/rpc"
        u = user if user is not None else config.secret("QBIT_USERNAME")
        self.auth = (u, password if password is not None else config.secret("QBIT_PASSWORD")) if u else None
        self.sid = ""

    def call(self, method: str, **args) -> dict:
        for _ in range(2):
            r = httpx.post(self.url, json={"method": method, "arguments": args}, auth=self.auth, timeout=TIMEOUT,
                           headers={"X-Transmission-Session-Id": self.sid})
            if r.status_code == 409:  # Sitzungskennung holen und noch einmal
                self.sid = r.headers.get("X-Transmission-Session-Id", "")
                continue
            r.raise_for_status()
            d = r.json()
            if d.get("result") != "success":
                raise RuntimeError(f"Transmission lehnt ab: {d.get('result')}")
            return d.get("arguments") or {}
        raise RuntimeError("Transmission: keine Sitzung")

    def add(self, item: dict, marker: str, force: bool = False) -> str:
        args = {"download-dir": f"{config.QBIT_SAVE_ROOT}/{marker}", "labels": [marker], "paused": False}
        if "magnet" in item:
            args["filename"] = item["magnet"]
        else:
            args["metainfo"] = base64.b64encode(item["file"]).decode()
        d = self.call("torrent-add", **args)
        if "torrent-duplicate" in d:
            raise ValueError("Diesen Torrent hat Transmission schon, er wurde also bereits einmal geladen. Bitte einen anderen Treffer wählen.")
        t = d.get("torrent-added") or {}
        if force and t.get("hashString"):
            self.call("torrent-start-now", ids=[t["hashString"]])
        return t.get("hashString") or ""

    def get(self, cid: str, marker: str) -> dict | None:
        fields = ["hashString", "percentDone", "status", "error", "errorString", "addedDate", "activityDate", "metadataPercentComplete"]
        rows = self.call("torrent-get", ids=[cid], fields=fields).get("torrents") or []
        if not rows:
            return None
        t = rows[0]
        if t.get("error"):
            state = "error"
        elif t.get("metadataPercentComplete", 1) < 1:
            state = "meta"
        elif t.get("percentDone", 0) >= 1:
            state = "done"
        else:
            state = "downloading" if t.get("percentDone", 0) > 0 else "queued"
        return {"progress": t.get("percentDone", 0), "state": state, "added_on": t.get("addedDate") or time.time(),
                "last_activity": t.get("activityDate") or 0, "error": t.get("errorString") or ""}

    def stop(self, cid: str) -> None:
        self.call("torrent-stop", ids=[cid])

    def remove(self, cid: str, delete_files: bool) -> None:
        self.call("torrent-remove", ids=[cid], **{"delete-local-data": delete_files})

    def version(self) -> str:
        return str(self.call("session-get", fields=["version"]).get("version", ""))


# ---------- Deluge (Web-Oberfläche) ----------

class Deluge:
    def __init__(self, url: str | None = None, password: str | None = None):
        url = (url or config.QBIT_URL).rstrip("/")
        self.url = url if url.endswith("/json") else url + "/json"
        self.http = httpx.Client(timeout=TIMEOUT)
        self.n = 0
        if not self.call("auth.login", password if password is not None else config.secret("QBIT_PASSWORD")):
            raise RuntimeError("Anmeldung bei Deluge fehlgeschlagen")
        if not self.call("web.connected"):  # Web-Oberfläche mit dem ersten Daemon verbinden
            hosts = self.call("web.get_hosts") or []
            if not hosts:
                raise RuntimeError("Deluge: kein Daemon eingetragen")
            self.call("web.connect", hosts[0][0])

    def call(self, method: str, *params):
        self.n += 1
        r = self.http.post(self.url, json={"method": method, "params": list(params), "id": self.n})
        r.raise_for_status()
        d = r.json()
        if d.get("error"):
            raise RuntimeError(f"Deluge lehnt ab: {(d['error'] or {}).get('message', d['error'])}")
        return d.get("result")

    def add(self, item: dict, marker: str, force: bool = False) -> str:
        opts = {"download_location": f"{config.QBIT_SAVE_ROOT}/{marker}", "add_paused": False}
        if "magnet" in item:
            h = self.call("core.add_torrent_magnet", item["magnet"], opts)
        else:
            h = self.call("core.add_torrent_file", f"{marker}.torrent", base64.b64encode(item["file"]).decode(), opts)
        if not h:
            raise ValueError("Deluge hat den Torrent nicht angenommen (schon vorhanden?). Bitte einen anderen Treffer wählen.")
        if force:
            self.call("core.queue_top", [h])
        return h

    def get(self, cid: str, marker: str) -> dict | None:
        t = self.call("core.get_torrent_status", cid, ["progress", "state", "time_added", "time_since_transfer", "message", "total_size"])
        if not t:
            return None
        p = (t.get("progress") or 0) / 100
        st = t.get("state", "")
        state = ("error" if st == "Error" else "moving" if st == "Moving" else "done" if p >= 1 or st == "Seeding"
                 else "meta" if not t.get("total_size") else "downloading" if p > 0 else "queued")
        since = t.get("time_since_transfer")
        last = time.time() - since if isinstance(since, (int, float)) and since >= 0 else 0
        return {"progress": p, "state": state, "added_on": t.get("time_added") or time.time(), "last_activity": last, "error": t.get("message") or ""}

    def stop(self, cid: str) -> None:
        self.call("core.pause_torrent", [cid])

    def remove(self, cid: str, delete_files: bool) -> None:
        self.call("core.remove_torrent", cid, delete_files)

    def version(self) -> str:
        return str(self.call("daemon.get_version") or "")


# ---------- Debrid: laden auf dem Server des Dienstes, ABW holt die Dateien ----------

_fetching: dict[str, dict] = {}  # Marker -> {"thread", "done", "total", "error"}
_lock = threading.Lock()
DONE_FILE = ".abw-fertig"


def _safe_rel(name: str) -> Path:
    parts = [p for p in re.split(r"[\\/]+", name) if p not in ("", ".", "..")]
    return Path(*parts) if parts else Path("datei")


def _fetch_files(marker: str, files: list[tuple[str, str]]) -> None:
    """Dateien (Name, URL) nach <fertige Torrents>/<Marker> laden; erst in einen Teil-Ordner, am Ende umbenennen."""
    final = config.TORRENT_COMPLETE_ROOT / marker
    part = config.TORRENT_COMPLETE_ROOT / (".teil-" + marker)  # beginnt nicht mit «abw-»: das Aufräumen lässt ihn in Ruhe
    st = _fetching[marker]
    try:
        shutil.rmtree(part, ignore_errors=True)
        with httpx.Client(timeout=httpx.Timeout(60, read=300), follow_redirects=True) as http:
            for name, url in files:
                dest = part / _safe_rel(name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with http.stream("GET", url) as r:
                    r.raise_for_status()
                    with open(dest, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            st["done"] += len(chunk)
        (part / DONE_FILE).write_text("ok")
        if final.exists():
            shutil.rmtree(final)
        part.rename(final)
    except Exception as exc:  # noqa: BLE001
        log.warning("Debrid-Dateien für %s nicht geladen: %s", marker, exc)
        st["error"] = str(exc)[:200]


def _local(marker: str, files_fn, total: int) -> dict:
    """Stand des Holens der Dateien. files_fn liefert [(Name, URL)] und wird nur beim Start aufgerufen."""
    if (config.TORRENT_COMPLETE_ROOT / marker / DONE_FILE).is_file():
        return {"progress": 1.0, "state": "done"}
    with _lock:
        st = _fetching.get(marker)
        if st and st.get("error"):
            err = st["error"]
            _fetching.pop(marker, None)  # beim nächsten Lauf neu versuchen
            return {"progress": 0.99, "state": "downloading", "error": err}
        if not st or not st["thread"].is_alive():
            st = {"done": 0, "total": total, "error": None}
            st["thread"] = threading.Thread(target=_fetch_files, args=(marker, files_fn()), daemon=True, name="abw-debrid")
            _fetching[marker] = st
            st["thread"].start()
    pct = st["done"] / st["total"] if st["total"] else 0
    return {"progress": 0.9 + 0.09 * min(pct, 1), "state": "downloading", "last_activity": time.time()}


class RealDebrid:
    BASE = "https://api.real-debrid.com/rest/1.0"

    def __init__(self, key: str | None = None):
        self.http = httpx.Client(base_url=self.BASE, timeout=TIMEOUT, headers={"Authorization": f"Bearer {key or config.secret('DEBRID_API_KEY')}"})

    def add(self, item: dict, marker: str, force: bool = False) -> str:
        if "magnet" in item:
            r = self.http.post("/torrents/addMagnet", data={"magnet": item["magnet"]})
        else:
            r = self.http.put("/torrents/addTorrent", content=item["file"])
        r.raise_for_status()
        tid = r.json()["id"]
        self.http.post(f"/torrents/selectFiles/{tid}", data={"files": "all"})
        return tid

    def get(self, cid: str, marker: str) -> dict | None:
        r = self.http.get(f"/torrents/info/{cid}")
        if r.status_code == 404:
            return None
        t = r.raise_for_status().json()
        st = t.get("status", "")
        added = _ts(t.get("added") or time.time())
        if st == "waiting_files_selection":
            self.http.post(f"/torrents/selectFiles/{cid}", data={"files": "all"})
            return {"progress": 0, "state": "queued", "added_on": added, "last_activity": time.time()}
        if st in ("magnet_error", "error", "virus", "dead"):
            return {"progress": 0, "state": "error", "added_on": added, "error": f"Real-Debrid: {st}"}
        if st == "magnet_conversion":
            return {"progress": 0, "state": "meta", "added_on": added, "last_activity": 0}
        if st != "downloaded":
            return {"progress": (t.get("progress") or 0) / 100 * 0.9, "state": "downloading" if t.get("progress") else "queued",
                    "added_on": added, "last_activity": time.time()}

        def files():
            out = []
            for link in t.get("links") or []:
                u = self.http.post("/unrestrict/link", data={"link": link}).raise_for_status().json()
                out.append((u.get("filename") or "datei", u["download"]))
            return out
        local = _local(marker, files, int(t.get("bytes") or 0))
        return {**local, "added_on": added, "last_activity": local.get("last_activity", time.time())}

    def stop(self, cid: str) -> None:
        """Nichts zu stoppen: Debrid-Dienste verteilen nicht weiter."""

    def remove(self, cid: str, delete_files: bool) -> None:
        self.http.delete(f"/torrents/delete/{cid}")

    def version(self) -> str:
        u = self.http.get("/user").raise_for_status().json()
        return f"Konto {u.get('username', '?')}, {u.get('type', '')}"


class TorBox:
    BASE = "https://api.torbox.app/v1/api"

    def __init__(self, key: str | None = None):
        self.key = key or config.secret("DEBRID_API_KEY")
        self.http = httpx.Client(base_url=self.BASE, timeout=TIMEOUT, headers={"Authorization": f"Bearer {self.key}"})

    def _data(self, r):
        d = r.raise_for_status().json()
        if not d.get("success", True):
            raise RuntimeError(f"TorBox lehnt ab: {d.get('detail') or d.get('error')}")
        return d.get("data")

    def add(self, item: dict, marker: str, force: bool = False) -> str:
        if "magnet" in item:
            d = self._data(self.http.post("/torrents/createtorrent", data={"magnet": item["magnet"], "name": marker}))
        else:
            d = self._data(self.http.post("/torrents/createtorrent", data={"name": marker},
                                          files={"file": (marker + ".torrent", item["file"], "application/x-bittorrent")}))
        return str(d["torrent_id"])

    def get(self, cid: str, marker: str) -> dict | None:
        r = self.http.get("/torrents/mylist", params={"id": cid, "bypass_cache": "true"})
        if r.status_code == 404:
            return None
        t = self._data(r)
        if not t:
            return None
        added = _ts(t.get("created_at") or time.time())
        st = str(t.get("download_state", ""))
        if "error" in st or st == "failed":
            return {"progress": 0, "state": "error", "added_on": added, "error": f"TorBox: {st}"}
        if not (t.get("download_finished") and t.get("download_present")):
            meta = st.startswith("meta")
            return {"progress": float(t.get("progress") or 0) * 0.9, "state": "meta" if meta else "downloading",
                    "added_on": added, "last_activity": 0 if meta or "stalled" in st else time.time()}

        def files():
            return [(f.get("name") or str(f["id"]),
                     self._data(self.http.get("/torrents/requestdl", params={"token": self.key, "torrent_id": cid, "file_id": f["id"]})))
                    for f in t.get("files") or []]
        local = _local(marker, files, int(t.get("size") or 0))
        return {**local, "added_on": added, "last_activity": local.get("last_activity", time.time())}

    def stop(self, cid: str) -> None:
        """Nichts zu stoppen: Debrid-Dienste verteilen nicht weiter."""

    def remove(self, cid: str, delete_files: bool) -> None:
        self.http.post("/torrents/controltorrent", json={"torrent_id": int(cid), "operation": "delete"})

    def version(self) -> str:
        u = self._data(self.http.get("/user/me"))
        return f"Konto {u.get('email', '?')}"


def torrent(kind: str | None = None):
    kind = kind or torrent_client()
    return {"transmission": Transmission, "deluge": Deluge, "realdebrid": RealDebrid, "torbox": TorBox}[kind]()
