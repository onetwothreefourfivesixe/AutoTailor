"""M10 step 1: background tasks and the worker."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autotailor import db, tasks
from autotailor.cli import app

FIXTURE = Path(__file__).parent / "fixtures" / "runway_digest.eml"

SLEEPER = """
import signal, sys, time
def stop(*a):
    print("got Ctrl-C, saving", flush=True); sys.exit(130)
signal.signal(signal.SIGINT, stop)
if hasattr(signal, "SIGBREAK"):  # Windows: the worker stops tasks with Ctrl-Break (as autotailor's CLI does)
    signal.signal(signal.SIGBREAK, stop)
print("working", flush=True)
for i in range(100):
    time.sleep(0.1)
print("finished", flush=True)
"""


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    return tmp_path


def test_queue_claim_and_cancel(home):
    conn = db.connect(home / "data" / "autotailor.db")
    a = tasks.enqueue(conn, "screen")
    b = tasks.enqueue(conn, "tailor", ["--ignore-limits"])
    with pytest.raises(ValueError):
        tasks.enqueue(conn, "rm -rf")
    assert [r["id"] for r in tasks.active(conn)] == [a, b]
    tasks.cancel(conn, b)
    assert tasks.get(conn, b)["status"] == "cancelled"
    assert tasks._claim(conn)["id"] == a and tasks._claim(conn) is None


def test_worker_runs_a_real_cli_task(home):
    (home / "data" / "inbox" / "d.eml").write_bytes(FIXTURE.read_bytes())
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "mail")
    tasks.worker_loop(home, once=True)
    t = tasks.get(conn, tid)
    assert t["status"] == "done", t["log"]
    assert "$ autotailor mail import" in t["log"] and "3 new" in t["log"]
    assert len(db.list_runway_roles(conn)) == 3


def test_failed_task(home):
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "resume", ["999"])
    tasks.worker_loop(home, once=True)
    t = tasks.get(conn, tid)
    assert t["status"] in ("done", "failed")  # 'No unfinished run 999' exits 1
    assert "No run 999" in t["log"]


def test_stop_sends_ctrl_c(home, monkeypatch):
    monkeypatch.setattr(tasks, "argv", lambda task: [sys.executable, "-c", SLEEPER])
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "tailor")
    worker = threading.Thread(target=tasks.worker_loop, args=(home,), kwargs={"once": True})
    worker.start()
    for _ in range(100):
        if "working" in (tasks.get(conn, tid)["log"] or ""):
            break
        time.sleep(0.1)
    tasks.cancel(conn, tid)
    worker.join(timeout=20)
    t = tasks.get(conn, tid)
    output = t["log"].split("\nworking\n", 1)[1]  # after the echoed command line
    assert t["status"] == "cancelled" and "got Ctrl-C, saving" in output and "finished" not in output


CHATTY = r"""
import sys
sys.stdout.buffer.write("start ← ✓ – café\n".encode("utf-8"))  # not all in the Windows code page
sys.stdout.buffer.write(b"bad byte \x90 here\n")              # not even valid UTF-8
sys.stdout.flush()
for i in range(1500):  # far more than a pipe buffer holds, less than the kept log
    print(f"line {i} " + "x" * 60, flush=True)
print("all done", flush=True)
"""


def test_any_output_is_read_and_never_blocks_the_task(home, monkeypatch):
    monkeypatch.setattr(tasks, "argv", lambda task: [sys.executable, "-c", CHATTY])
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "tailor")
    worker = threading.Thread(target=tasks.worker_loop, args=(home,), kwargs={"once": True})
    worker.start()
    worker.join(timeout=60)
    assert not worker.is_alive(), "the task hung: its output wasn't being read"
    t = tasks.get(conn, tid)
    assert t["status"] == "done"
    assert "start ← ✓ – café" in t["log"] and "bad byte" in t["log"] and "all done" in t["log"]


def test_report_progress(home, monkeypatch):
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "tailor")
    tasks.report_progress(1, 4, "nope")  # outside a task: nothing happens
    monkeypatch.setenv("AUTOTAILOR_TASK_ID", str(tid))
    tasks.report_progress(2, 4, "2 resume(s) done")
    t = tasks.get(conn, tid)
    assert (t["done"], t["total"], t["message"]) == (2, 4, "2 resume(s) done")


def test_runs_page_queues_tasks(home, monkeypatch):
    from streamlit.testing.v1 import AppTest

    started = []
    monkeypatch.setattr("autotailor.tasks.ensure_worker", lambda h, d: started.append(h) or True)
    at = AppTest.from_string("from autotailor.ui import page_runs\npage_runs.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    next(b for b in at.button if b.label == "Screen").click().run()
    conn = db.connect(home / "data" / "autotailor.db")
    assert [r["kind"] for r in tasks.active(conn)] == ["screen"] and started


@pytest.mark.parametrize(
    ("text", "argv"),
    [
        ("autotailor rank --rescore", ["rank", "--rescore"]),
        ("tailor lever:abc --force", ["tailor", "lever:abc", "--force"]),
        ("runway resolve", ["runway", "resolve", "--auto"]),
        ("answers run 3", ["answers", "run", "3", "--no-ask"]),
        ('answers add lever:a "Why us? (150 words)"', ["answers", "add", "lever:a", "Why us? (150 words)"]),
        ("config set run.jobs_per_run 5", ["config", "set", "run.jobs_per_run", "5"]),
    ],
)
def test_parse_command(text, argv):
    assert tasks.parse_command(text) == argv


@pytest.mark.parametrize(
    ("text", "why"),
    [("ui", "already running"), ("mail login", "password"), ("rm -rf /", "Unknown command"),
     ("rank; rm -rf /", "Unknown command"), ("jobs", "needs one of"), ("", "Type a command"), ('add "x', "Couldn't read")],
)  # fmt: skip
def test_parse_command_refuses(text, why):
    with pytest.raises(ValueError, match=why):
        tasks.parse_command(text)


def test_command_box_runs_a_typed_command(home):
    conn = db.connect(home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "cli", tasks.parse_command("autotailor runs"))
    tasks.worker_loop(home, once=True)
    t = tasks.get(conn, tid)
    assert t["status"] == "done" and "$ autotailor runs" in t["log"] and "No runs yet" in t["log"]
