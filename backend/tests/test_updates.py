"""Updater: version ordering, the offline status endpoint, and staging hygiene."""
from __future__ import annotations

import pytest

from app.services import updater
from app.version import __version__, is_newer, version_tuple


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.0.0", (1, 0, 0)),
        ("v1.0.1", (1, 0, 1)),
        ("V2.10.3", (2, 10, 3)),
        ("1.2.3-beta.1", (1, 2, 3)),
        ("1.2.3+build9", (1, 2, 3)),
        ("nonsense", (0,)),
        ("", (0,)),
    ],
)
def test_version_tuple_parses(raw, expected):
    assert version_tuple(raw) == expected


def test_newer_compares_numerically_not_lexically():
    # "v1.10.0" < "v1.9.0" as strings, which is the bug this guards against.
    assert is_newer("v1.10.0", "1.9.0")
    assert not is_newer("v1.9.0", "1.10.0")


def test_same_version_is_not_newer():
    assert not is_newer(__version__, __version__)
    assert not is_newer(f"v{__version__}", __version__)


def test_unparseable_tag_never_looks_like_an_update():
    assert not is_newer("latest", "1.0.0")
    assert not is_newer("", "1.0.0")


def test_status_endpoint_reports_unsupported_from_source(client):
    """Running from source (not frozen) must not offer to swap a binary."""
    body = client.get("/api/updates").json()
    assert body["current_version"] == __version__
    assert body["supported"] is False
    assert body["update_available"] is False
    assert "source" in (body["reason"] or "")


def test_check_from_source_does_not_call_github(client, monkeypatch):
    def explode(*_a, **_kw):  # pragma: no cover - must never run
        raise AssertionError("checked GitHub while running from source")

    monkeypatch.setattr(updater, "_get_json", explode)
    body = client.post("/api/updates/check").json()
    assert body["supported"] is False


def test_install_without_a_download_is_rejected(client):
    res = client.post("/api/updates/install")
    assert res.status_code == 400
    assert "waiting" in res.json()["detail"]


def test_find_staged_discards_builds_that_are_not_newer(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path)
    old = tmp_path / f"AppTracker-{__version__}.exe"
    old.write_bytes(b"stale")

    assert updater.find_staged() is None
    assert not old.exists(), "a build we already run should be cleaned up"


def test_find_staged_returns_the_highest_version(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path)
    for version in ("9.0.1", "9.0.10", "9.0.2"):
        (tmp_path / f"AppTracker-{version}.exe").write_bytes(b"x")

    found = updater.find_staged()
    assert found is not None
    assert found[0] == "9.0.10"


def test_install_relaunches_on_the_preferred_port(client, tmp_path, monkeypatch):
    """A busy-port fallback must not become the relaunched instance's preference."""
    monkeypatch.setenv("APPTRACKER_PREFERRED_PORT", "8000")
    monkeypatch.setenv("APPTRACKER_PORT", "8001")
    monkeypatch.setattr(updater, "find_staged", lambda: ("9.9.9", tmp_path / "x.exe"))
    calls: list[int] = []
    monkeypatch.setattr(
        updater, "restart_for_update", lambda port: calls.append(port) or True
    )

    assert client.post("/api/updates/install").status_code == 200
    assert calls == [8000]


def test_swap_helper_follows_the_port_file_not_a_fixed_port(tmp_path, monkeypatch):
    """The relaunch can land on any free port; the helper must find it there."""
    from app.config import settings as app_settings
    from app.ports import APP_ID, port_file

    staged = tmp_path / "AppTracker-9.9.9.exe"
    staged.write_bytes(b"new")
    monkeypatch.setattr(updater, "staging_dir", lambda: tmp_path)
    monkeypatch.setattr(updater, "installed_exe", lambda: tmp_path / "AppTracker.exe")
    monkeypatch.setattr(updater.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **kw: None)

    assert updater.apply_staged_update(8000, staged=staged)
    script = (tmp_path / "apply-update.ps1").read_text(encoding="utf-8")
    assert "'--port','8000'" in script
    assert f"$portFile = '{port_file(app_settings.data_path)}'" in script
    assert "/api/identity" in script and f'"{APP_ID}"' in script
    assert "127.0.0.1:8000" not in script
