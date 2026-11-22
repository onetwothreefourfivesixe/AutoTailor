"""M10 step 6: the Needs-you list."""

from __future__ import annotations

import pytest
from test_m4_resume import CV_TEXT, _store
from typer.testing import CliRunner

from autotailor import db, inbox
from autotailor.cli import app
from autotailor.config import Settings
from autotailor.models import Match, PostingStatus, Verdict


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9)
    _store(conn, "lever:b", "ML Intern", 0.8)
    db.save_match(conn, Match(posting_id="lever:b", verdict=Verdict.UNCLEAR, verdict_reasons=["Requirements not read yet."]))
    q = db.add_question(conn, "lever:a", "Why us?", 100)
    db.update_question(conn, q, status="waiting", points=[])
    run = db.start_run(conn, "{}")
    db.set_run_status(conn, run, "interrupted")
    db.add_runway_role(conn, "rw1", "runway:rw1", "Acme", "Intern", None, None, 0.9, None)
    from autotailor.models import JobPosting, Source

    db.upsert_posting(conn, JobPosting(id="runway:rw1", source=Source.RUNWAY, source_ref="rw1", company="Acme",
                                       title="Intern", status=PostingStatus.NEEDS_LINK))  # fmt: skip
    conn.close()
    return tmp_path


def test_collect(home):
    conn = db.connect(home / "data" / "autotailor.db")
    kinds = sorted(n.kind for n in inbox.collect(conn, Settings(), home / "data" / "outputs"))
    assert kinds == ["answer_choice", "interrupted_run", "runway_link", "unclear"]


def test_page_override_and_resume(home, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr("autotailor.tasks.ensure_worker", lambda h, d: True)
    at = AppTest.from_string("from autotailor.ui import page_needs\npage_needs.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    assert any("Unclear eligibility" in s.value for s in at.subheader)
    at = next(b for b in at.button if b.key == "need.eligible.lever:b").click().run()
    conn = db.connect(home / "data" / "autotailor.db")
    assert db.get_match(conn, "lever:b").verdict == Verdict.ELIGIBLE
    next(b for b in at.button if b.key.startswith("need.resume.")).click().run()
    from autotailor import tasks

    assert [t["kind"] for t in tasks.active(conn)] == ["resume"]
