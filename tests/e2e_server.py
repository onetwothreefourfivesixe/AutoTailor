"""Start AutoTailor (API + web app) on a throwaway data folder for the Playwright tests in web/e2e.

Seeds two eligible postings and the test CV, writes a one-time sign-in link to
<home>/login_link.txt, and runs until terminated. Ports: web 8696, API 8796.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
WEB_PORT, API_PORT = 8696, 8796


def main() -> None:
    home = Path(os.environ.get("E2E_HOME") or tempfile.mkdtemp(prefix="autotailor-e2e-"))
    os.environ["AUTOTAILOR_HOME"] = str(home)
    from typer.testing import CliRunner

    from autotailor import db
    from autotailor.cli import app

    CliRunner().invoke(app, ["init"])
    from autotailor.config import Settings, save_settings

    from datetime import date

    from autotailor.config import ModelSpec
    from autotailor.config import Education as Degree

    settings = Settings()
    settings.resume.template = "typst"  # LaTeX would download Tectonic on the first preview
    # A finished setup (the wizard has its own test): local models, a degree, one board.
    settings.models.default = ModelSpec(provider="ollama", model="qwen3-vl:4b-instruct")
    settings.profile.education = [Degree(level="bachelor", field="Computer Science", status="in_progress",
                                         graduation_date=date(2028, 5, 31), school="State University")]  # fmt: skip
    settings.sources.lever = ["acme"]
    save_settings(settings, home / "config.toml")
    (home / "cv" / "master.yaml").write_text((ROOT / "tests" / "fixtures" / "cv_ml_student.yaml").read_text())
    from test_m4_resume import _store

    conn = db.connect(home / "data" / "autotailor.db")
    _store(conn, "lever:e2e-a", "Perception Intern", 0.86, company="Northwind Robotics", location="Pittsburgh, PA")
    _store(conn, "lever:e2e-b", "Data Science Intern", 0.71, company="Acme Analytics", location="Remote")
    # A tailored resume for the first job (Resume tab, viewer and editor).
    from test_m4_resume import CV, _verbatim

    from autotailor.resume.output import Style, write_outputs

    run_id = db.start_run(conn, "{}")
    outputs = home / "data" / "outputs"
    w = write_outputs(outputs / "e2e-run", 1, db.get_posting(conn, "lever:e2e-a"), None,
                      _verbatim("exp-lab-detector", "proj-ocr").model_copy(update={"keywords": ["PyTorch", "Perception", "Robotics"]}),
                      CV, style=Style("typst", fill_page=False))  # fmt: skip
    for kind, path in (("resume_pdf", w.pdf), ("resume_docx", w.docx), ("resume_data", w.data)):
        db.add_artifact(conn, run_id, "lever:e2e-a", kind, str(path.relative_to(outputs)), "test")
    # A Runway role waiting for the employer's link (the Runway page's "Waiting for a link" list).
    from autotailor.models import JobPosting, PostingStatus, Source

    db.upsert_posting(conn, JobPosting(id="runway:e2e-r1", source=Source.RUNWAY, source_ref="e2e-r1", company="Globex",
                                       title="Policy Analyst Intern", status=PostingStatus.NEEDS_LINK))  # fmt: skip
    db.add_runway_role(conn, "e2e-r1", "runway:e2e-r1", "Globex", "Policy Analyst Intern", "Remote", None, 0.8, None)
    conn.commit()
    conn.close()

    from autotailor.api import api_token
    from autotailor.paths import get_paths

    api_token(get_paths(home))
    code = secrets.token_urlsafe(16)
    (home / "data" / "ui_login.json").write_text(json.dumps({"code": code, "expires": time.time() + 3600}))
    (home / "login_link.txt").write_text(f"http://127.0.0.1:{WEB_PORT}/login?code={code}")
    print(f"E2E home: {home}", flush=True)

    env = {**os.environ, "AUTOTAILOR_HOME": str(home)}
    procs = [
        subprocess.Popen([sys.executable, "-m", "autotailor.cli", "api", "--port", str(API_PORT)], env=env),
        subprocess.Popen(
            ["node", str(ROOT / "web" / "build")],
            env={**env, "HOST": "127.0.0.1", "PORT": str(WEB_PORT), "ORIGIN": f"http://127.0.0.1:{WEB_PORT}",
                 "AUTOTAILOR_API_URL": f"http://127.0.0.1:{API_PORT}"},  # fmt: skip
        ),
    ]

    def stop(*_: object) -> None:
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while all(p.poll() is None for p in procs):
        time.sleep(0.5)
    stop()


if __name__ == "__main__":
    main()
