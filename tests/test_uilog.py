"""data/ui.log: the record `autotailor ui` leaves behind when its terminal is gone."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from autotailor import uilog
from autotailor.uilog import UiLog

LINE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d \[(\w+)\] (.*)$")


def entries(log: UiLog) -> list[tuple[str, str]]:
    out = []
    for line in log.path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        assert m, f"not a log line: {line!r}"
        out.append((m[1], m[2]))
    return out


def test_lines_are_timestamped_and_tagged(tmp_path):
    log = UiLog(tmp_path / "data" / "ui.log")
    log.write("ui", "starting")
    log.write("api", "one\ntwo\n")
    assert entries(log) == [("ui", "starting"), ("api", "one"), ("api", "two")]
    log.close()
    log.write("ui", "after close is ignored")
    assert len(entries(log)) == 3


def test_appends_across_starts_and_rotates_when_large(tmp_path, monkeypatch):
    path = tmp_path / "ui.log"
    first = UiLog(path)
    first.write("ui", "first start")
    first.close()
    second = UiLog(path)
    second.write("ui", "second start")
    second.close()
    assert [t for _, t in entries(second)] == ["first start", "second start"]

    monkeypatch.setattr(uilog, "MAX_BYTES", 10)
    third = UiLog(path)
    third.write("ui", "third start")
    third.close()
    assert [t for _, t in entries(third)] == ["third start"]
    assert "second start" in (tmp_path / "ui.log.1").read_text(encoding="utf-8")


def test_follow_captures_a_child_until_it_exits(tmp_path, capsys):
    log = UiLog(tmp_path / "ui.log")
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", "import sys; print('listening'); print('boom: café', file=sys.stderr); sys.exit(3)"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", bufsize=1,
        env={"PYTHONIOENCODING": "utf-8", "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", "")},
    )  # fmt: skip
    log.follow("api", proc)
    assert proc.wait(timeout=30) == 3
    log.close()
    assert entries(log) == [("api", "listening"), ("api", "boom: café")]
    assert "listening" in capsys.readouterr().out  # still shown in the terminal


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console events")
def test_console_events_are_logged_on_windows(tmp_path):
    log = UiLog(tmp_path / "ui.log")
    log.watch_console()  # may report False when pytest has no console; the callback still works
    assert log._console_handler(2) == 0  # lets Windows carry on with its default handling
    log.close()
    assert entries(log) == [("ui", "received console window closed")]


FAKE_WEB = """\
import http.server, os, sys, threading
srv = http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), http.server.SimpleHTTPRequestHandler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
print("fake web listening", flush=True)
threading.Event().wait(4)
print("fake web crashing", flush=True)
os._exit(3)
"""


def free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_ui_logs_a_server_that_stops(tmp_path, monkeypatch):
    """The real `ui` launcher and API, with a stand-in web server that crashes after 4 s."""
    import shutil

    from typer.testing import CliRunner

    from autotailor import cli

    web = tmp_path / "web"
    (web / "build").mkdir(parents=True)
    (web / "build" / "index.js").write_text(FAKE_WEB)  # run by "node", which is Python here
    monkeypatch.setattr(cli, "_web_dir", lambda: web)
    real_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: sys.executable if name == "node" else real_which(name, *a, **k))
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    cli.init()

    r = CliRunner().invoke(cli.app, ["ui", "--no-browser", "--port", str(free_port()), "--api-port", str(free_port())])

    assert r.exit_code == 1, r.output
    log = (tmp_path / "data" / "ui.log").read_text(encoding="utf-8")
    for expected in ("[ui] starting", "[web] fake web listening", "[api] AutoTailor API", "[ui] running",
                     "[web] fake web crashing", "[ui] web server exited with code 3; stopping the other",
                     "[ui] api server stopped", "[ui] stopped"):  # fmt: skip
        assert expected in log, f"{expected!r} missing from:\n{log}"


def test_console_watch_is_a_no_op_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    log = UiLog(tmp_path / "ui.log")
    assert log.watch_console() is False
    log.close()
