"""M12: `autotailor ui` (both servers, one-time sign-in link) and `ui-login`."""

from __future__ import annotations

import json
import time

from typer.testing import CliRunner

from autotailor import cli
from autotailor.cli import app


def test_login_link_writes_a_one_time_code(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    r = CliRunner().invoke(app, ["ui-login", "--port", "9000"])
    assert r.exit_code == 0
    link = r.output.strip()
    saved = json.loads((tmp_path / "data" / "ui_login.json").read_text())
    assert link == f"http://127.0.0.1:9000/login?code={saved['code']}"
    assert time.time() < saved["expires"] <= time.time() + 600
    assert (tmp_path / "data" / "ui_login.json").stat().st_mode & 0o077 == 0


def test_ui_explains_a_missing_build(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_web_dir", lambda: tmp_path / "web")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/node")
    r = CliRunner().invoke(app, ["ui", "--no-browser"])
    assert r.exit_code == 1 and "npm install && npm run build" in r.output and "--classic" in r.output


def test_ui_needs_node(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: None)
    r = CliRunner().invoke(app, ["ui", "--no-browser"])
    assert r.exit_code == 1 and "Node.js" in r.output
