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

Windows has no signals between processes, only console events, so three things
differ there. Each task runs in its own process group and is stopped with
Ctrl-Break, which AutoTailor's commands treat as Ctrl-C (see
`ctrl_break_acts_like_ctrl_c`). The worker gets its own hidden console, so a
Ctrl-C or a closed window in the terminal running the UI doesn't reach it (the
equivalent of `start_new_session` elsewhere). And "is the worker alive?" asks
Windows about the process: `os.kill(pid, 0)` there is not a check but sends
Ctrl-C (signal 0 is CTRL_C_EVENT), which can reach every process in the console.
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
WINDOWS = sys.platform == "win32"
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
    # UTF-8 both ways. Otherwise Windows decodes the output with the locale code page
    # (cp1252), a character outside it kills the reader thread, and the task then blocks
    # forever on a full output pipe.
    env = {**os.environ, "AUTOTAILOR_TASK_ID": str(task["id"]), "AUTOTAILOR_HOME": str(home),
           "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1", "TERM": "dumb"}  # fmt: skip
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        # Windows: its own process group, so Ctrl-Break can be sent to this task alone.
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0,
    )  # fmt: skip
    conn.execute("UPDATE tasks SET pid = ? WHERE id = ?", (proc.pid, task["id"]))
    conn.commit()
    _append_log(conn, task["id"], f"$ autotailor {' '.join(args)}\n")

    import threading

    lines: list[str] = []
    lock = threading.Lock()

    def reader() -> None:
        # Must keep draining until the task exits: if it stops, the task blocks on a full pipe.
        assert proc.stdout is not None
        while True:
            try:
                line = proc.stdout.readline()
            except (OSError, ValueError) as e:  # pipe broken: record it, the task can't be heard any more
                with lock:
                    lines.append(f"\n[output lost: {type(e).__name__}: {e}]\n")
                return
            if not line:
                return
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
            # Same as Ctrl-C: finished work is kept. Windows can't send SIGINT; Ctrl-Break to the
            # task's own process group does the same job there (see ctrl_break_acts_like_ctrl_c).
            proc.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)
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


def process_alive(pid: int) -> bool:
    """Whether a process with this ID is running. Never signals it."""
    if not WINDOWS:
        try:
            os.kill(pid, 0)  # POSIX: signal 0 only checks
            return True
        except OSError:
            return False
    # Windows: os.kill(pid, 0) would send Ctrl-C (CTRL_C_EVENT == 0), so ask Windows instead.
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    process_query_limited_information, still_active = 0x1000, 259
    handle = k32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
    finally:
        k32.CloseHandle(handle)


def worker_running(data: Path) -> bool:
    try:
        pid = int(pid_file(data).read_text())
    except (OSError, ValueError):
        return False
    return process_alive(pid)


def ctrl_break_acts_like_ctrl_c() -> None:
    """Windows: make Ctrl-Break stop this process the way Ctrl-C does (KeyboardInterrupt).

    The worker stops a task with Ctrl-Break (Windows can't send it Ctrl-C alone), and
    Python's default for Ctrl-Break is to end the process at once, losing finished work.
    Called when a CLI command starts; does nothing elsewhere or off the main thread.
    """
    if WINDOWS:
        try:
            signal.signal(signal.SIGBREAK, signal.default_int_handler)
        except ValueError:  # not the main thread
            pass


def ensure_worker_for_queue(conn: sqlite3.Connection, home: Path, data: Path) -> bool:
    """Start the worker if tasks are waiting and none is running. True if it was started.

    A worker normally starts when a task is queued. Tasks queued for a worker that
    then died (or before the app restarted) would otherwise wait forever.
    """
    if conn.execute("SELECT 1 FROM tasks WHERE status = 'queued' LIMIT 1").fetchone() is None:
        return False
    return ensure_worker(home, data)


def ensure_worker(home: Path, data: Path) -> bool:
    """Start the worker in the background if it isn't running. True if it was started."""
    if worker_running(data):
        return False
    data.mkdir(parents=True, exist_ok=True)
    log = (data / "worker.log").open("a")
    proc = subprocess.Popen(
        [sys.executable, "-m", "autotailor.cli", "worker"],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        env={**os.environ, "AUTOTAILOR_HOME": str(home)},
        # Detached from the UI's terminal, so Ctrl-C there or closing it doesn't stop a running
        # task. POSIX: a new session. Windows: its own hidden console and process group.
        start_new_session=not WINDOWS,
        creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if WINDOWS else 0,
    )  # fmt: skip
    pid_file(data).write_text(str(proc.pid))
    return True
