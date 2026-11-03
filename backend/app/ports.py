"""Which port the server listens on, and how the launcher picks a free one."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

DEFAULT_PORT = 8000
# The launcher scans DEFAULT_PORT..DEFAULT_PORT + PORT_SCAN_SPAN, and the
# browser extension probes the same range, so keep the two in step.
PORT_SCAN_SPAN = 100

# Returned by /api/identity so probes can tell AppTracker from other servers.
APP_ID = "apptracker"


def port_file(data_dir: Path) -> Path:
    """Where a running launcher records the port it bound, once it is healthy.

    The update helper follows this instead of guessing: the relaunched build
    may land on a different port than the one it replaced.
    """
    return data_dir / "server.port"


def _env_port(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def server_port() -> int:
    """The port this process serves on. The launcher exports the one it bound."""
    return _env_port("APPTRACKER_PORT", DEFAULT_PORT)


def preferred_port() -> int:
    """The port the launcher was asked for, before any busy-port fallback.

    Relaunches should start from this one so a one-off fallback doesn't
    become the new preference (and from there, the Start on Login entry).
    """
    return _env_port("APPTRACKER_PREFERRED_PORT", server_port())


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Bind the way uvicorn will. On POSIX it sets SO_REUSEADDR, so a port
        # whose old connections are still in TIME_WAIT after a quick restart is
        # usable; without it we would wrongly skip to the next port. On Windows
        # SO_REUSEADDR would let us bind over a live listener, so leave it off.
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def is_port_free(port: int, host: str = "127.0.0.1") -> bool:
    """True when nothing is listening on `port` for loopback or all interfaces.

    Windows lets 127.0.0.1 bind even while another program holds 0.0.0.0 on
    the same port, so checking the loopback address alone misses servers like
    Docker's published ports. Test both.
    """
    return _can_bind(host, port) and _can_bind("0.0.0.0", port)


def find_free_port(
    start: int = DEFAULT_PORT,
    end: int | None = None,
    host: str = "127.0.0.1",
) -> int | None:
    """The first free port in start..end (inclusive), or None if all are busy."""
    if end is None:
        end = start + PORT_SCAN_SPAN
    for port in range(start, end + 1):
        if is_port_free(port, host):
            return port
    return None
