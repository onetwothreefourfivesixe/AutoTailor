"""M5 plumbing: jobs, the worker, run limits, outputs, the run report, and the CLI.

The batch graph itself is the user's exercise (test_m5_batch_graph.py); here it
is replaced by a plain loop so these tests don't depend on it.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from test_m4_resume import CV, CV_TEXT, LIMITS, _r, _store, _verbatim
from typer.testing import CliRunner

from autotailor import batch, db
from autotailor.chains.tailor_graph import TailorResult
from autotailor.cli import app
from autotailor.config import RunSettings, Settings, save_settings


def _settings() -> Settings:
    s = Settings()
    s.tailor = LIMITS
    return s


def _add_run(conn, when: datetime, status: str = "done") -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, status, settings_json) VALUES (?, ?, '{}')",
        (when.astimezone(UTC).isoformat(timespec="seconds"), status),
    )
    conn.commit()
    return cur.lastrowid


def fake_tailor(posting, profile, cv, settings):
    if "ML Intern" in posting:
        return TailorResult(_r(("ghost", "x")), ["Bullet 1: source_id 'ghost' is not a bullet ID"], 3)
    if "Broken" in posting:
        raise ValueError("model returned invalid JSON")
    return TailorResult(_verbatim("exp-lab-detector", "proj-ocr", skills=["PyTorch"]), [], 2)


# --- jobs and the worker -------------------------------------------------------


def test_make_jobs_in_rank_order_with_fit_hint(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    a = _store(conn, "lever:a", "SWE Intern", 0.9)
    c = _store(conn, "lever:c", "ML Intern", 0.8)
    jobs = batch.make_jobs([(a, db.get_match(conn, a.id)), (c, None)], CV, _settings())
    assert [(j["index"], j["posting_id"]) for j in jobs] == [(1, "lever:a"), (2, "lever:c")]
    assert jobs[0]["profile"].endswith("(from fit scoring): exp-lab-detector")
    assert "from fit scoring" not in jobs[1]["profile"]
    assert "SWE Intern" in jobs[0]["posting"]


def _job(text: str, i: int = 1) -> batch.Job:
    return batch.Job(index=i, posting_id=f"p{i}", posting=text, profile="profile")


def test_worker_results():
    work = batch.make_worker(CV, _settings(), tailor=fake_tailor)
    ok = work(_job("SWE Intern"))
    assert ok["status"] == "tailored" and ok["attempts"] == 2 and ok["resume"]["skills"] == ["PyTorch"]

    unverified = work(_job("ML Intern"))
    assert unverified["status"] == "failed" and unverified["resume"] is None
    assert "the last of 3 drafts" in unverified["message"] and "'ghost'" in unverified["errors"][0]

    broken = work(_job("Broken"))
    assert broken["status"] == "failed" and "invalid JSON" in broken["message"]


def test_worker_raises_for_unwritten_graph_and_after_cancel():
    def unwritten(*a):
        raise NotImplementedError

    with pytest.raises(NotImplementedError):
        batch.make_worker(CV, _settings(), tailor=unwritten)(_job("x"))
    cancel = threading.Event()
    work = batch.make_worker(CV, _settings(), cancel, tailor=fake_tailor)
    assert work(_job("SWE Intern"))["status"] == "tailored"
    cancel.set()
    with pytest.raises(batch.Cancelled):
        work(_job("SWE Intern"))


# --- run limits ------------------------------------------------------------------


def test_run_limits(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    now = datetime(2026, 11, 21, 15, 0).astimezone()
    limits = RunSettings(max_runs_per_day=2, max_runs_per_week=3)
    assert batch.limit_message(conn, limits, now) is None
    _add_run(conn, now - timedelta(days=3))
    _add_run(conn, now - timedelta(hours=1))
    _add_run(conn, now - timedelta(minutes=5), status="failed")  # failed runs don't count
    assert batch.limit_message(conn, limits, now) is None
    _add_run(conn, now - timedelta(minutes=1), status="interrupted")
    assert "2 runs today" in batch.limit_message(conn, limits, now)
    tomorrow = now + timedelta(days=1)
    assert "3 runs in the last 7 days" in batch.limit_message(conn, limits, tomorrow)
    assert batch.limit_message(conn, limits, now + timedelta(days=8)) is None


# --- outputs and report ------------------------------------------------------------


def test_save_results_and_report(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    _store(conn, "lever:a", "SWE Intern", 0.9, location="Denver")
    _store(conn, "lever:b", "SWE Intern", 0.95, location="Austin")
    _store(conn, "lever:c", "ML Intern", 0.8)
    run_id = _add_run(conn, datetime.now(), status="running")
    run = db.get_run(conn, run_id)
    outputs = tmp_path / "outputs"
    folder = batch.run_folder(outputs, run)
    assert folder.name.endswith(f"-run{run_id}")

    work = batch.make_worker(CV, _settings(), tailor=fake_tailor)
    results = [work(_job("ML Intern", 2)), work(_job("SWE Intern", 1))]
    results[0]["posting_id"], results[1]["posting_id"] = "lever:c", "lever:b"

    rows = batch.save_results(conn, run_id, folder, outputs, results, CV, _settings(), "ollama:test")
    assert [r.index for r in rows] == [1, 2]
    assert batch.counts(rows) == {"jobs": 2, "tailored": 1, "failed": 1}
    assert rows[0].files["pdf"].exists() and rows[0].others == 1
    assert len(db.run_artifacts(conn, run_id)) == 3

    again = batch.save_results(conn, run_id, folder, outputs, results, CV, _settings(), "ollama:test")
    assert len(db.run_artifacts(conn, run_id)) == 3  # nothing rendered twice
    assert again[0].files["pdf"] == rows[0].files["pdf"]

    report = batch.write_report(folder, run, "ollama:test", rows).read_text()
    assert "1 tailored, 1 not tailored" in report
    assert "| 1 | 0.95 | Acme | SWE Intern | Austin (+1 more) |" in report
    assert "[PDF](01-acme-swe-intern/sam-example-resume.pdf)" in report
    assert "**2. Acme | ML Intern**: the last of 3 drafts still had problems." in report
    assert "  - Bullet 1: source_id 'ghost'" in report


# --- CLI -----------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    save_settings(_settings(), tmp_path / "config.toml")
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9, location="Denver")
    _store(conn, "lever:b", "SWE Intern", 0.95, location="Austin")
    _store(conn, "lever:c", "ML Intern", 0.8)
    conn.close()
    monkeypatch.setattr("autotailor.chains.tailor_graph.tailor_posting", fake_tailor)
    return tmp_path


class LoopGraph:
    """Stands in for the user's batch graph: runs the jobs in a loop. `stop_after`
    simulates Ctrl-C after that many jobs; the results so far count as saved."""

    saved: dict[str, list] = {}

    def __init__(self, stop_after: int | None = None) -> None:
        self.stop_after = stop_after

    def install(self, monkeypatch) -> None:
        monkeypatch.setattr("autotailor.chains.batch_graph.build_batch_graph", lambda work, saver: work)
        monkeypatch.setattr("autotailor.chains.batch_graph.run_batch", self.run)
        monkeypatch.setattr("autotailor.batch.has_checkpoint", lambda saver, run_id: True)

    def run(self, work, jobs, thread_id, max_concurrency=2):
        if jobs is None:
            jobs = self.saved[thread_id]
        self.saved[thread_id] = jobs
        out = []
        for i, j in enumerate(jobs):
            if self.stop_after is not None and i == self.stop_after:
                raise KeyboardInterrupt
            out.append(work(j))
        return out


def test_cli_tailor_writes_report_and_lists_run(home, monkeypatch):
    LoopGraph().install(monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["tailor"])
    assert r.exit_code == 0, r.output
    assert "1 tailored, 1 failed" in r.output and "Report:" in r.output
    report = next((home / "data" / "outputs").glob("*-run1/report.md"))
    assert "SWE Intern" in report.read_text()

    r = runner.invoke(app, ["runs"])
    assert "done" in r.output and "1 tailored, 1 not" in r.output and "report.md" in r.output


def test_cli_interrupt_then_resume(home, monkeypatch):
    LoopGraph(stop_after=1).install(monkeypatch)
    runner = CliRunner()
    r = runner.invoke(app, ["tailor"])
    assert r.exit_code == 130
    assert "autotailor resume" in r.output
    conn = db.connect(home / "data" / "autotailor.db")
    assert db.get_run(conn, 1)["status"] == "interrupted"
    conn.close()

    r = runner.invoke(app, ["tailor"])  # a new run is allowed, and points at the unfinished one
    assert "Unfinished run(s) 1" in r.output  # (this one stops part way too: run 2)

    LoopGraph().install(monkeypatch)
    r = runner.invoke(app, ["resume", "1"])
    assert r.exit_code == 0, r.output
    conn = db.connect(home / "data" / "autotailor.db")
    assert db.get_run(conn, 1)["status"] == "done"
    conn.close()
    assert "Resuming run 2" in runner.invoke(app, ["resume"]).output  # default: latest unfinished
    assert runner.invoke(app, ["resume"]).output.strip() == "No unfinished runs."


def test_cli_run_limit(home, monkeypatch):
    LoopGraph().install(monkeypatch)
    runner = CliRunner()
    runner.invoke(app, ["config", "set", "run.max_runs_per_day", "1"])
    assert runner.invoke(app, ["tailor"]).exit_code == 0
    r = runner.invoke(app, ["tailor", "--force"])
    assert r.exit_code == 1 and "Run limit reached: 1 runs today" in r.output
    assert runner.invoke(app, ["tailor", "--force", "--ignore-limits"]).exit_code == 0


def test_cli_without_batch_graph_runs_one_at_a_time(home):
    r = CliRunner().invoke(app, ["tailor"])
    assert r.exit_code == 0, r.output
    assert "batch graph is not written yet" in r.output and "1 tailored, 1 failed" in r.output
