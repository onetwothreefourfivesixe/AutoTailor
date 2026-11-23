"""A log file for `autotailor ui` (data/ui.log) that outlives its terminal.

The API and web servers print only to the terminal that started them, so when
that terminal closes, the reason the app stopped goes with it. `UiLog` copies
everything they print to a file, line by line with a timestamp and the source,
and records the launcher's own events: start, a server exiting (with its exit
code), Ctrl-C, and on Windows the console being closed, the user logging off,
or the machine shutting down. Each line is flushed as it's written, so even a
hard kill leaves everything up to that moment.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

MAX_BYTES = 5_000_000  # past this, the log is moved to ui.log.1 on the next start

# Windows console control events (wincon.h).
CONSOLE_EVENTS = {
    0: "Ctrl-C",
    1: "Ctrl-Break",
    2: "console window closed",
    5: "user logging off",
    6: "system shutting down",
}


class UiLog:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))  # keep one older file
        self.path = path
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._console_handler = None  # kept alive while installed

    def write(self, source: str, text: str) -> None:
        """Append one line (or several) tagged with the time and where it came from."""
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        lines = text.rstrip("\r\n").splitlines() or [""]
        with self._lock:
            if self._file.closed:
                return
            for line in lines:
                self._file.write(f"{stamp} [{source}] {line}\n")
            self._file.flush()

    def follow(self, source: str, proc: subprocess.Popen) -> None:
        """Copy a child's output to the terminal and to the log until it exits.

        The child must have been started with stdout=PIPE, stderr=STDOUT, text=True.
        """

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                except (OSError, ValueError):  # the terminal is gone; keep logging
                    pass
                self.write(source, line)

        t = threading.Thread(target=pump, name=f"uilog-{source}", daemon=True)
        t.start()
        self._threads.append(t)

    def watch_console(self) -> bool:
        """On Windows, log console close / logoff / shutdown events. True if installed.

        These end every process attached to the console, often before Python can
        run any cleanup, so this handler is the only record of them. It returns
        False, so Windows still handles the event as usual.
        """
        if sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes

        handler_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)

        def on_event(event: int) -> bool:
            self.write("ui", f"received {CONSOLE_EVENTS.get(event, f'console event {event}')}")
            return False

        self._console_handler = handler_type(on_event)
        return bool(ctypes.windll.kernel32.SetConsoleCtrlHandler(self._console_handler, True))

    def close(self, timeout: float = 2.0) -> None:
        """Wait briefly for the children's last output, then close the file."""
        for t in self._threads:
            t.join(timeout=timeout)
        with self._lock:
            self._file.close()
