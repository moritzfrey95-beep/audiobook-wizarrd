"""Verbindungstest qBittorrent: alte («Ok.») und neue Antwort (204 leer) gelten als angemeldet."""
import httpx
import pytest

from abw import setup


def _client(login):
    real = httpx.Client

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v2/auth/login":
            return login()
        return httpx.Response(200, text="v5.2.3")

    return lambda **kw: real(transport=httpx.MockTransport(handler), **kw)


OK_COOKIE = {"set-cookie": "SID=abc; path=/"}


@pytest.mark.parametrize("login, ok", [
    (lambda: httpx.Response(200, text="Ok.", headers=OK_COOKIE), True),
    (lambda: httpx.Response(204, headers=OK_COOKIE), True),
    (lambda: httpx.Response(200, text="Fails."), False),
    (lambda: httpx.Response(401, text="Unauthorized"), False),
])
def test_qbit_login(monkeypatch, login, ok):
    monkeypatch.setattr(setup.httpx, "Client", _client(login))
    r = setup.qbittorrent("http://qbit:8090", "u", "p")
    assert r["ok"] is ok
    if ok:
        assert "v5.2.3" in r["text"]
