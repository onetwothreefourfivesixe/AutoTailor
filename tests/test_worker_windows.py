"""The worker must never send Ctrl-C to the UI's console.

On Windows `os.kill(pid, 0)` is not an "is it alive?" check: signal 0 is CTRL_C_EVENT,
so it sends Ctrl-C, which could reach every process in the terminal running the UI
(the API, the web server, the worker and the pipeline) and stop them all.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys

import pytest

from autotailor import tasks


def test_process_alive_for_live_dead_and_unknown_pids():
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert tasks.process_alive(os.getpid())
        assert tasks.process_alive(child.pid)
    finally:
        child.kill()
        child.wait()
    assert not tasks.process_alive(child.pid)
    assert not tasks.process_alive(2**31 - 7)


def test_worker_running_reads_the_pid_file(tmp_path):
    assert not tasks.worker_running(tmp_path)  # no file
    tasks.pid_file(tmp_path).write_text("not a pid")
    assert not tasks.worker_running(tmp_path)
    tasks.pid_file(tmp_path).write_text(str(os.getpid()))
    assert tasks.worker_running(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="os.kill(pid, 0) sends Ctrl-C only on Windows")
def test_windows_liveness_check_never_calls_os_kill(tmp_path, monkeypatch):
    def no_kill(*a):  # noqa: ANN002
        raise AssertionError(f"os.kill{a} would send a console Ctrl-C on Windows")

    monkeypatch.setattr(os, "kill", no_kill)
    tasks.pid_file(tmp_path).write_text(str(os.getpid()))
    assert tasks.worker_running(tmp_path)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console isolation")
def test_windows_worker_is_detached_from_the_ui_console(tmp_path, monkeypatch):
    seen = {}

    class FakePopen:
        pid = 4242

        def __init__(self, cmd, **kw):  # noqa: ANN001, ANN003
            seen.update(kw)

    monkeypatch.setattr(tasks.subprocess, "Popen", FakePopen)
    assert tasks.ensure_worker(tmp_path, tmp_path / "data")
    flags = seen["creationflags"]
    assert flags & subprocess.CREATE_NO_WINDOW and flags & subprocess.CREATE_NEW_PROCESS_GROUP
    assert not seen["start_new_session"]


@pytest.mark.skipif(sys.platform != "win32", reason="Ctrl-Break is Windows-only")
def test_ctrl_break_raises_keyboard_interrupt_like_ctrl_c():
    old = signal.getsignal(signal.SIGBREAK)
    try:
        tasks.ctrl_break_acts_like_ctrl_c()
        assert signal.getsignal(signal.SIGBREAK) is signal.default_int_handler
    finally:
        signal.signal(signal.SIGBREAK, old)


def test_ctrl_break_setup_is_a_no_op_off_windows(monkeypatch):
    monkeypatch.setattr(tasks, "WINDOWS", False)
    tasks.ctrl_break_acts_like_ctrl_c()  # must not touch signals or raise
