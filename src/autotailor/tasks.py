"""Background tasks (M10): the UI queues work, a worker process runs it.

A task is one CLI command (`screen`, `tailor`, ...) run by the worker as a
subprocess, so the UI and the terminal share one code path: run limits,
checkpoints, Ctrl-C handling and `resume` behave the same. The worker:

- takes the oldest queued task, runs `python -m autotailor.cli <args>` with
  AUTOTAILOR_TASK_ID set, and appends its output to the task's log;
- stops a task by sending it SIGINT (exactly what Ctrl-C does), so a stopped
  tailoring run keeps its finished resumes and can be resumed;
- exits after `IDLE_EXIT` seconds with nothing to do; the UI starts it again.

Commands report progress with `report_progress(done, total, message)`, which
does nothing outside a task.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from autotailor import db

IDLE_EXIT = 30 * 60
POLL = 1.0
LOG_LIMIT = 200_000  # characters of output kept per task

# kind -> CLI arguments. Everything a task can run is listed here.
COMMANDS: dict[str, list[str]] = {
    "fetch": ["fetch"],
    "mail": ["mail", "import"],
    "resolve": ["runway", "resolve", "--auto"],
    "screen": ["screen"],
    "rank": ["rank"],
    "tailor": ["tailor"],
    "prune": ["prune"],
    "resume": ["resume"],
    "answers": ["answers", "run", "--no-ask"],
    "run": ["run"],
    "cli": [],  # a command typed in the UI; its arguments are the whole command (see parse_command)
}
LABELS = {
    "fetch": "Fetch job boards", "mail": "Import Runway emails", "resolve": "Match Runway roles",
    "screen": "Screen eligibility", "rank": "Rank", "tailor": "Tailor resumes", "resume": "Resume a run",
    "answers": "Suggest answer points", "prune": "Delete low-fit and old postings", "run": "Whole pipeline", "cli": "autotailor",
}  # fmt: skip


# --- commands typed in the UI ----------------------------------------------------------------

# What the UI's command box may run. Each entry: allowed subcommands (None = no subcommand),
# and flags added so the command never waits for keyboard input (the worker has none).
ALLOWED: dict[str, dict[str | None, list[str]]] = {
    "fetch": {None: []}, "add": {None: []}, "screen": {None: []}, "rank": {None: []},
    "shortlist": {None: []}, "tailor": {None: []}, "prune": {None: []}, "discover": {None: []}, "resume": {None: []}, "runs": {None: []}, "run": {None: []},
    "models": {None: []}, "doctor": {None: []}, "compare": {None: []},
    "jobs": {"list": [], "show": []},
    "runway": {"list": [], "resolve": ["--auto"], "answer": []},
    "mail": {"import": [], "pull": []},
    "answers": {"add": [], "list": [], "show": [], "run": ["--no-ask"], "remove": [], "decide": []},
    "config": {"show": [], "set": []},
    "cv": {"validate": []},
}  # fmt: skip
NOT_HERE = {
    "ui": "the UI is already running",
    "worker": "the worker is started for you",
    "init": "run it once from a terminal",
    "mail login": "it asks for a password: run it in a terminal",
    "mail listen": "it runs until stopped: run it in a terminal",
}


def command_help() -> list[dict[str, str]]:
    """The commands the UI's command line accepts, each with the first line of its help."""
    import typer.main

    from autotailor.cli import app

    group = typer.main.get_command(app)
    out = []
    for top, subs in sorted(ALLOWED.items()):
        cmd = group.commands.get(top)  # type: ignore[attr-defined]
        if cmd is None:
            continue
        names = [top] if None in subs else [f"{top} {s}" for s in subs if s]
        for name in names:
            sub = getattr(cmd, "commands", {}).get(name.split(" ", 1)[1]) if " " in name else cmd
            text = (getattr(sub, "help", None) or "").strip().splitlines()
            out.append({"command": name, "help": text[0] if text else ""})
    return out


def parse_command(text: str) -> list[str]:
    """'autotailor tailor lever:abc --force' -> ['tailor', 'lever:abc', '--force'].

    Only AutoTailor's own commands are accepted (never a shell), and interactive ones get
    their non-interactive flag (e.g. `runway resolve` runs with --auto). Raises ValueError
    with the reason otherwise."""
    import shlex

    try:
        args = shlex.split(text.strip())
    except ValueError as e:
        raise ValueError(f"Couldn't read that: {e}.") from e
    if args and args[0] == "autotailor":
        args = args[1:]
    if not args:
        raise ValueError("Type a command, e.g. `rank --rescore` or `tailor lever:abc --force`.")
    top = args[0]
    sub = args[1] if len(args) > 1 and not args[1].startswith("-") else None
    for key in (f"{top} {sub}", top):
        if key in NOT_HERE:
            raise ValueError(f"`{key}` isn't available here: {NOT_HERE[key]}.")
    if top not in ALLOWED:
        raise ValueError(f"Unknown command `{top}`. Available: {', '.join(sorted(ALLOWED))}.")
    subs = ALLOWED[top]
    if None in subs:
        extra = subs[None]
    elif sub in subs:
        extra = subs[sub]
    else:
        raise ValueError(f"`{top}` needs one of: {', '.join(s for s in subs if s)}.")
    return args + [f for f in extra if f not in args]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- the queue -----------------------------------------------------------------------


def enqueue(conn: sqlite3.Connection, kind: str, args: list[str] | None = None) -> int:
    if kind not in COMMANDS:
        raise ValueError(f"Unknown task kind: {kind}")
    cur = conn.execute(
        "INSERT INTO tasks (kind, args_json, created_at) VALUES (?, ?, ?)", (kind, json.dumps(args or []), _now())
    )
    conn.commit()
    return int(cur.lastrowid)


def get(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()


def recent(conn: sqlite3.Connection, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()


def active(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tasks WHERE status IN ('queued', 'running') ORDER BY id").fetchall()


def cancel(conn: sqlite3.Connection, task_id: int) -> None:
    """Queued: dropped. Running: the worker sends it Ctrl-C."""
    conn.execute("UPDATE tasks SET status = 'cancelled', finished_at = ? WHERE id = ? AND status = 'queued'",
                 (_now(), task_id))  # fmt: skip
    conn.execute("UPDATE tasks SET cancel = 1 WHERE id = ? AND status = 'running'", (task_id,))
    conn.commit()


def _claim(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM tasks WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    n = conn.execute(
        "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued'", (_now(), row["id"])
    ).rowcount
    conn.commit()
    return get(conn, row["id"]) if n else None


def _append_log(conn: sqlite3.Connection, task_id: int, text: str) -> None:
    conn.execute(
        "UPDATE tasks SET log = substr(log || ?, -?) WHERE id = ?", (text, LOG_LIMIT, task_id)
    )
    conn.commit()


# --- progress from inside a task ---------------------------------------------------------------


def current_task_id() -> int | None:
    v = os.environ.get("AUTOTAILOR_TASK_ID")
    return int(v) if v and v.isdigit() else None


def report_progress(done: int | None, total: int | None, message: str | None = None, db_path: Path | None = None) -> None:
    """Record progress for the running task (no-op when not running as a task). Thread-safe:
    opens its own connection."""
    tid = current_task_id()
    if tid is None:
        return
    from autotailor.paths import get_paths

    try:
        conn = db.connect(db_path or get_paths().db)
        conn.execute("UPDATE tasks SET done = ?, total = ?, message = COALESCE(?, message) WHERE id = ?",
                     (done, total, message, tid))  # fmt: skip
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass  # progress is best effort


# --- the worker ------------------------------------------------------------------------------------


def argv(task: sqlite3.Row) -> list[str]:
    """The command line a task runs."""
    return [sys.executable, "-m", "autotailor.cli", *COMMANDS[task["kind"]], *json.loads(task["args_json"])]


def run_task(conn: sqlite3.Connection, task: sqlite3.Row, home: Path) -> str:
    """Run one task to completion as a subprocess. Returns its final status."""
    cmd = argv(task)
    args = cmd[3:] if cmd[1:3] == ["-m", "autotailor.cli"] else cmd
    env = {**os.environ, "AUTOTAILOR_TASK_ID": str(task["id"]), "AUTOTAILOR_HOME": str(home),
           "PYTHONUNBUFFERED": "1", "NO_COLOR": "1", "TERM": "dumb"}  # fmt: skip
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env, text=True, bufsize=1,
    )  # fmt: skip
    conn.execute("UPDATE tasks SET pid = ? WHERE id = ?", (proc.pid, task["id"]))
    conn.commit()
    _append_log(conn, task["id"], f"$ autotailor {' '.join(args)}\n")

    import threading

    lines: list[str] = []
    lock = threading.Lock()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                lines.append(line)

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    interrupted = False
    while proc.poll() is None:
        time.sleep(POLL)
        with lock:
            chunk, lines[:] = "".join(lines), []
        if chunk:
            _append_log(conn, task["id"], chunk)
        row = get(conn, task["id"])
        if row and row["cancel"] and not interrupted:
            proc.send_signal(signal.SIGINT)  # same as Ctrl-C: finished work is kept
            interrupted = True
            _append_log(conn, task["id"], "\n[Stop requested: finishing the jobs in progress ...]\n")
    t.join(timeout=5)
    with lock:
        if lines:
            _append_log(conn, task["id"], "".join(lines))
    status = "cancelled" if interrupted else ("done" if proc.returncode == 0 else "failed")
    conn.execute("UPDATE tasks SET status = ?, finished_at = ?, pid = NULL WHERE id = ?", (status, _now(), task["id"]))
    conn.commit()
    return status


def worker_loop(home: Path, once: bool = False, idle_exit: float = IDLE_EXIT) -> None:
    from autotailor.paths import Paths

    paths = Paths(home=home)
    conn = db.connect(paths.db)
    # Tasks left 'running' by a worker that died can't be resumed as tasks.
    conn.execute("UPDATE tasks SET status = 'failed', message = 'worker stopped', finished_at = ? "
                 "WHERE status = 'running'", (_now(),))  # fmt: skip
    conn.commit()
    idle_since = time.monotonic()
    try:
        while True:
            task = _claim(conn)
            if task is None:
                if once or time.monotonic() - idle_since > idle_exit:
                    return
                time.sleep(POLL)
                continue
            run_task(conn, task, home)
            idle_since = time.monotonic()
    finally:
        conn.close()


# --- starting the worker from the UI ----------------------------------------------------------------


def pid_file(data: Path) -> Path:
    return data / "worker.pid"


def worker_running(data: Path) -> bool:
    try:
        pid = int(pid_file(data).read_text())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def ensure_worker(home: Path, data: Path) -> bool:
    """Start the worker in the background if it isn't running. True if it was started."""
    if worker_running(data):
        return False
    data.mkdir(parents=True, exist_ok=True)
    log = (data / "worker.log").open("a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "autotailor.cli", "worker"],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env={**os.environ, "AUTOTAILOR_HOME": str(home)}, start_new_session=True,
    )  # fmt: skip
    pid_file(data).write_text(str(proc.pid))
    return True
