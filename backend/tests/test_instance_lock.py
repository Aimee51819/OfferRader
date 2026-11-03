"""Single-instance lock: a second launcher on the same data dir must back off."""
from __future__ import annotations

import subprocess
import sys

from app.instance_lock import InstanceLock


def test_second_holder_is_refused_until_release(tmp_path):
    path = tmp_path / "apptracker.lock"
    first, second = InstanceLock(path), InstanceLock(path)

    assert first.acquire()
    assert not second.acquire()

    first.release()
    assert second.acquire()
    second.release()


def test_other_process_is_refused_while_held(tmp_path):
    path = tmp_path / "apptracker.lock"
    lock = InstanceLock(path)
    assert lock.acquire()
    try:
        probe = (
            "import sys; from app.instance_lock import InstanceLock; "
            f"sys.exit(0 if InstanceLock(__import__('pathlib').Path({str(path)!r})).acquire() else 3)"
        )
        result = subprocess.run([sys.executable, "-c", probe], cwd=".", timeout=30)
        assert result.returncode == 3
    finally:
        lock.release()


def test_lock_is_freed_when_the_holder_exits(tmp_path):
    path = tmp_path / "apptracker.lock"
    holder = (
        "from pathlib import Path; from app.instance_lock import InstanceLock; "
        f"assert InstanceLock(Path({str(path)!r})).acquire()"
    )
    subprocess.run([sys.executable, "-c", holder], cwd=".", timeout=30, check=True)
    lock = InstanceLock(path)
    assert lock.acquire()
    lock.release()


def test_waiting_launch_takes_over_when_the_holder_exits(tmp_path, monkeypatch):
    """A holder whose server died must not lock out new launches."""
    import threading

    import launcher

    monkeypatch.setattr(launcher, "_find_running_instance", lambda _start: None)
    path = tmp_path / "apptracker.lock"
    holder, waiter = InstanceLock(path), InstanceLock(path)
    assert holder.acquire()
    threading.Timer(0.5, holder.release).start()

    assert launcher._wait_for_other_launch(8000, waiter, timeout_s=10.0) is None
    assert waiter.held
    waiter.release()


def test_waiting_launch_hands_off_to_a_serving_instance(tmp_path, monkeypatch):
    import launcher

    monkeypatch.setattr(
        launcher, "_find_running_instance", lambda _start: "http://127.0.0.1:8001"
    )
    path = tmp_path / "apptracker.lock"
    holder, waiter = InstanceLock(path), InstanceLock(path)
    assert holder.acquire()
    try:
        assert launcher._wait_for_other_launch(8000, waiter, 5.0) == "http://127.0.0.1:8001"
        assert not waiter.held
    finally:
        holder.release()
