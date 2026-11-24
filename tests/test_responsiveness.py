"""Faster feedback: Ollama over IPv4, light task lists, and tasks that say why they failed."""

from __future__ import annotations

import sys
import threading

import pytest

from autotailor import db, tasks
from autotailor.config import ModelSettings


@pytest.mark.parametrize(
    ("given", "used"),
    [
        ("http://localhost:11434", "http://127.0.0.1:11434"),
        ("http://localhost:11434/", "http://127.0.0.1:11434/"),
        ("http://localhost", "http://127.0.0.1"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        ("http://gpu-box.lan:11434", "http://gpu-box.lan:11434"),
    ],
)
def test_ollama_localhost_becomes_ipv4(given, used):
    """Windows tries ::1 first for `localhost` and waits ~2 s when Ollama isn't there."""
    assert ModelSettings(ollama_base_url=given).ollama_base_url == used


def test_ollama_default_is_ipv4():
    assert ModelSettings().ollama_base_url == "http://127.0.0.1:11434"


RICH_TRACEBACK = """$ autotailor resume 1
Resuming run 1 (started 2026-11-23T20:40:05+00:00) ...
┌───────────────────── Traceback (most recent call last) ─────────────────────┐
│ C:\\Users\\x\\subprocess.py:1553 in _execute_child                              │
│ > 1553 │   │   │   │   hp, ht, pid, tid = _winapi.CreateProcess(executable, │
└─────────────────────────────────────────────────────────────────────────────┘
OSError: [WinError 193] %1 is not a valid Win32 application
"""


@pytest.mark.parametrize(
    ("log", "reason"),
    [
        (RICH_TRACEBACK, "OSError: [WinError 193] %1 is not a valid Win32 application"),
        ("working\nError: Run limit reached: 3 runs today.\n\n", "Error: Run limit reached: 3 runs today."),
        ("fetching\nsomething odd happened\n", "something odd happened"),
        ("", None),
        ("│   │\n└──┘\n", None),
    ],
)
def test_failure_reason_finds_the_error_line(log, reason):
    assert tasks.failure_reason(log) == reason


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    (tmp_path / "data").mkdir()
    return tmp_path


def run_one(home, monkeypatch, code: str) -> dict:
    monkeypatch.setattr(tasks, "argv", lambda task: [sys.executable, "-c", code])
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "tailor")
    worker = threading.Thread(target=tasks.worker_loop, args=(home,), kwargs={"once": True})
    worker.start()
    worker.join(timeout=60)
    return dict(tasks.get(conn, tid))


def test_a_failed_task_records_why(home, monkeypatch):
    t = run_one(home, monkeypatch, "import sys; print('working'); sys.exit('Error: the CV has no bullets')")
    assert t["status"] == "failed"
    assert t["message"] == "Error: the CV has no bullets"
    assert t["updated_at"]


def test_a_finished_task_keeps_its_progress_message(home, monkeypatch):
    t = run_one(home, monkeypatch, "print('ok')")
    assert t["status"] == "done" and t["message"] is None and t["updated_at"]


def test_task_summaries_leave_out_the_logs(home):
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "screen")
    tasks._append_log(conn, tid, "x" * 10_000)
    [s] = tasks.recent_summaries(conn, 5)
    assert s["id"] == tid and "log" not in s and s["updated_at"]
    assert set(s) == set(tasks.SUMMARY_COLUMNS)
