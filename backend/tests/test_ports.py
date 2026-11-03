"""Port selection: a busy preferred port falls through to the next free one."""
from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager

from app import ports


@contextmanager
def _listening(host: str = "127.0.0.1", port: int = 0) -> Iterator[int]:
    """Hold a listening socket for the duration of the block; yields its port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, port))
        sock.listen()
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _free_run(length: int) -> int:
    """A port p where p..p+length-1 are all currently free."""
    for _ in range(50):
        with _listening() as base:
            pass
        if all(ports.is_port_free(base + i) for i in range(length)):
            return base
    raise RuntimeError("could not find a run of free ports")


def test_free_port_is_reported_free():
    port = _free_run(1)
    assert ports.is_port_free(port)
    assert ports.find_free_port(port, port + 5) == port


def test_busy_port_moves_to_next():
    start = _free_run(3)
    with _listening(port=start):
        assert not ports.is_port_free(start)
        assert ports.find_free_port(start, start + 5) == start + 1


def test_skips_several_busy_ports():
    start = _free_run(4)
    with _listening(port=start), _listening(port=start + 1), _listening(port=start + 2):
        assert ports.find_free_port(start, start + 5) == start + 3


def test_port_held_on_all_interfaces_counts_as_busy():
    """Windows lets 127.0.0.1 bind over a 0.0.0.0 listener; we must not."""
    start = _free_run(2)
    with _listening(host="0.0.0.0", port=start):
        assert not ports.is_port_free(start)
        assert ports.find_free_port(start, start + 5) == start + 1


class _RecordingSocket:
    def __init__(self, *_a):
        self.options: list[tuple[int, int, int]] = []
        _RecordingSocket.last = self

    def setsockopt(self, *args):
        self.options.append(args)

    def bind(self, _addr):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def test_posix_probe_uses_reuseaddr_like_uvicorn(monkeypatch):
    """Otherwise ports in TIME_WAIT after a quick restart look busy."""
    monkeypatch.setattr(ports.sys, "platform", "linux")
    monkeypatch.setattr(ports.socket, "socket", _RecordingSocket)
    assert ports.is_port_free(8000)
    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) in _RecordingSocket.last.options


def test_windows_probe_does_not_use_reuseaddr(monkeypatch):
    """On Windows SO_REUSEADDR would bind over a live listener."""
    monkeypatch.setattr(ports.sys, "platform", "win32")
    monkeypatch.setattr(ports.socket, "socket", _RecordingSocket)
    assert ports.is_port_free(8000)
    assert _RecordingSocket.last.options == []


def test_returns_none_when_range_is_exhausted():
    start = _free_run(2)
    with _listening(port=start), _listening(port=start + 1):
        assert ports.find_free_port(start, start + 1) is None


def test_default_scan_range_is_preferred_plus_span(monkeypatch):
    seen: list[int] = []
    monkeypatch.setattr(ports, "is_port_free", lambda p, host="127.0.0.1": seen.append(p) or False)
    assert ports.find_free_port(8000) is None
    assert seen[0] == 8000
    assert seen[-1] == 8000 + ports.PORT_SCAN_SPAN


def test_server_port_reads_launcher_env(monkeypatch):
    monkeypatch.setenv("APPTRACKER_PORT", "8003")
    assert ports.server_port() == 8003
    monkeypatch.setenv("APPTRACKER_PORT", "junk")
    assert ports.server_port() == ports.DEFAULT_PORT
    monkeypatch.delenv("APPTRACKER_PORT")
    assert ports.server_port() == ports.DEFAULT_PORT


def test_preferred_port_falls_back_to_server_port(monkeypatch):
    monkeypatch.delenv("APPTRACKER_PREFERRED_PORT", raising=False)
    monkeypatch.setenv("APPTRACKER_PORT", "8001")
    assert ports.preferred_port() == 8001
    monkeypatch.setenv("APPTRACKER_PREFERRED_PORT", "8000")
    assert ports.preferred_port() == 8000


def test_identity_endpoint(client):
    body = client.get("/api/identity").json()
    assert body["app"] == ports.APP_ID
    assert body["version"]


def test_cors_rejects_arbitrary_extension_and_web_origins(client):
    """Our extension relies on host_permissions, so no extension origin is allowed."""
    for origin in ("chrome-extension://" + "a" * 32, "http://evil.example"):
        res = client.options(
            "/api/identity",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert "access-control-allow-origin" not in res.headers, origin


def test_cors_allows_the_frontend_dev_server(client):
    origin = "http://localhost:5173"
    res = client.options(
        "/api/identity",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert res.headers.get("access-control-allow-origin") == origin
