"""M9: comparing models on saved data. Models are fakes keyed by model name; no network."""

from __future__ import annotations

from datetime import datetime

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from test_m4_resume import CV, CV_TEXT, LIMITS, _r, _store, _verbatim
from typer.testing import CliRunner

from autotailor import compare as cmp
from autotailor import db
from autotailor.chains.tailor_graph import TailorResult
from autotailor.cli import app
from autotailor.config import ModelSpec, Settings, save_settings
from autotailor.models import CriterionScore, FitAssessment, Requirements

BIG, SMALL = "ollama:big:12b", "ollama:small:4b"


def test_parse_model_and_with_model():
    base = ModelSpec(provider="ollama", model="x", context_window=32768)
    spec = cmp.parse_model("ollama:gemma4:12b", base)
    assert (spec.provider, spec.model, spec.context_window) == ("ollama", "gemma4:12b", 32768)
    with pytest.raises(ValueError):
        cmp.parse_model("gemma4")
    s = Settings()
    s2 = cmp.with_model(s, "tailor", spec)
    assert s2.models.for_step("tailor").model == "gemma4:12b" and "tailor" not in s.models.steps


def test_spearman():
    assert cmp.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert cmp.spearman([1, 2, 3], [3, 2, 1]) == -1.0
    assert cmp.spearman([1, 2], [1, 2]) is None


def test_missing_ollama_models(monkeypatch):
    class R:
        def json(self):
            return {"models": [{"name": "small:4b"}, {"name": "nomic-embed-text:latest"}]}

    monkeypatch.setattr("httpx.get", lambda *a, **k: R())
    specs = [cmp.parse_model(SMALL), cmp.parse_model(BIG), cmp.parse_model("ollama:nomic-embed-text"),
             cmp.parse_model("openai:gpt-x")]  # fmt: skip
    assert cmp.missing_ollama_models(specs, "http://localhost:11434") == ["big:12b"]


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "a.db")
    for i, (pid, fit) in enumerate([("lever:a", 0.9), ("lever:b", 0.7), ("lever:c", 0.5)]):
        _store(c, pid, f"Role {i} Intern", fit, company=f"Co{i}")
    return c


def _settings() -> Settings:
    s = Settings()
    s.tailor = LIMITS
    return s


def test_compare_tailor(conn, monkeypatch, tmp_path):
    def fake(posting, profile, cv, settings):
        if settings.models.for_step("tailor").model == "big:12b":
            return TailorResult(_verbatim("exp-lab-detector", "proj-ocr"), [], 1)
        return TailorResult(_r(("ghost", "x")), ["Bullet 1: source_id 'ghost' is not a bullet ID"], 3)

    monkeypatch.setattr("autotailor.chains.tailor_graph.tailor_posting", fake)
    postings = [db.get_posting(conn, "lever:a"), db.get_posting(conn, "lever:b")]
    trials = cmp.compare_tailor(conn, CV, _settings(), [cmp.parse_model(BIG), cmp.parse_model(SMALL)], postings, tmp_path)
    assert [(t.model, t.ok) for t in trials] == [(BIG, True), (BIG, True), (SMALL, False), (SMALL, False)]
    assert trials[2].error.startswith("Bullet 1") and trials[0].metrics["drafts"] == 1
    rows = {r["model"]: r for r in cmp.summarize("tailor", trials)}
    assert rows[BIG]["ok"] == "2/2" and rows[SMALL]["ok"] == "0/2" and rows[SMALL]["avg_drafts"] == 3


def test_compare_score_ranks_against_stored(conn, monkeypatch):
    def fake(postings, profile, settings, max_concurrency=1):
        big = settings.models.for_step("score").model == "big:12b"
        role = int(postings[0].split("Role ")[1][0])
        score = (5 - role) if big else (2 + role)  # big agrees with the stored order, small reverses it
        c = CriterionScore(score=score, reason="r")
        return [FitAssessment(skills=c, experience=c, level=c, interests=c, relevant_bullet_ids=["ghost"])]

    monkeypatch.setattr("autotailor.chains.score_fit.score_fit_batch", fake)
    postings = [db.get_posting(conn, p) for p in ("lever:a", "lever:b", "lever:c")]
    trials = cmp.compare_score(conn, CV, _settings(), [cmp.parse_model(BIG), cmp.parse_model(SMALL)], postings)
    rows = {r["model"]: r for r in cmp.summarize("score", trials)}
    assert rows[BIG]["rank_agreement_with_stored"] == 1.0 and rows[SMALL]["rank_agreement_with_stored"] == -1.0
    assert rows[BIG]["unknown_bullet_ids"] == 3


def test_compare_extract_verdicts(conn, monkeypatch):
    from datetime import date

    from autotailor.config import DegreeLevel, Education

    s = _settings()
    s.profile.education = [Education(level=DegreeLevel.BACHELOR, field="CS", status="in_progress", graduation_date=date(2029, 5, 31))]

    def fake(texts, settings, max_concurrency=1):
        if settings.models.for_step("extract").model == "big:12b":
            return [Requirements()]
        return [ValueError("model returned invalid JSON")]

    monkeypatch.setattr("autotailor.chains.extract_requirements.extract_requirements_batch", fake)
    trials = cmp.compare_extract(conn, s, [cmp.parse_model(BIG), cmp.parse_model(SMALL)], [db.get_posting(conn, "lever:a")])
    assert trials[0].ok and trials[0].metrics["verdict"] == "eligible" and trials[0].metrics["same_verdict"] is True
    assert not trials[1].ok and "invalid JSON" in trials[1].error


def test_compare_answers_uses_the_answer_graph(conn, monkeypatch):
    qid = db.add_question(conn, "lever:a", "Why us? (50 words)", 50)
    db.update_question(conn, qid, points=[{"source_id": "proj-ocr", "point": "Built an OCR pipeline", "why": "x",
                                           "cv_text": CV.bullets()["proj-ocr"].text}])  # fmt: skip

    def model(settings, step):
        name = settings.models.for_step(step).model
        text = "I built an OCR pipeline with OpenCV." if name == "big:12b" else "I built OCR for 400 stores — truly pivotal."
        return RunnableLambda(lambda pv: AIMessage(content=text))

    monkeypatch.setattr("autotailor.llm.get_chat_model", model)
    trials = cmp.compare_answers(conn, CV, _settings(), [cmp.parse_model(BIG), cmp.parse_model(SMALL)], [qid])
    assert trials[0].ok and trials[0].metrics["problems_left"] == 0
    assert not trials[1].ok and trials[1].metrics["problems_left"] >= 2  # invented number, dash, stock phrase


def test_report(conn, tmp_path):
    trials = [cmp.Trial(BIG, "lever:a", "Co0 | Role 0 Intern", True, 12.3, {"drafts": 1, "bullets": 2, "problems": 0,
                                                                            "same_bullets_as_saved": None}, "Trained ..."),
              cmp.Trial(SMALL, "lever:a", "Co0 | Role 0 Intern", False, 4.5, {}, error="Bullet 1: ghost")]  # fmt: skip
    path = cmp.write_report(tmp_path, "tailor", trials, datetime(2026, 11, 21, 12, 0))
    text = path.read_text()
    assert path.name == "20261121-120000-tailor.md" and path.with_suffix(".json").exists()
    assert f"| {BIG} | 1 | 1/1 | 12.3 |" in text and "### Co0 | Role 0 Intern" in text and "error: Bullet 1: ghost" in text


# --- CLI and UI ---------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    s = _settings()
    s.models.default = ModelSpec(provider="ollama", model="small:4b")
    save_settings(s, tmp_path / "config.toml")
    c = db.connect(tmp_path / "data" / "autotailor.db")
    _store(c, "lever:a", "Role 0 Intern", 0.9)
    c.close()
    monkeypatch.setattr("autotailor.compare.missing_ollama_models", lambda specs, url: [])
    monkeypatch.setattr("autotailor.chains.tailor_graph.tailor_posting",
                        lambda posting, profile, cv, settings: TailorResult(_verbatim("proj-ocr", "proj-budget"), [], 1))  # fmt: skip
    return tmp_path


def test_cli_compare(home, monkeypatch):
    runner = CliRunner()
    r = runner.invoke(app, ["compare", "tailor", "-m", BIG, "-n", "1"])
    assert r.exit_code == 0, r.output
    assert f"model: {SMALL}" in r.output and f"model: {BIG}" in r.output and "Report:" in r.output
    assert len(list((home / "data" / "comparisons").glob("*.md"))) == 1
    assert "at least one other model" in runner.invoke(app, ["compare", "tailor"]).output
    assert "Step must be one of" in runner.invoke(app, ["compare", "nope", "-m", BIG]).output
    monkeypatch.setattr("autotailor.compare.missing_ollama_models", lambda specs, url: ["big:12b"])
    assert "ollama pull big:12b" in runner.invoke(app, ["compare", "tailor", "-m", BIG]).output
    monkeypatch.setattr("autotailor.compare.missing_ollama_models", lambda specs, url: [])
    r = runner.invoke(app, ["compare", "tailor", "-m", "openai:gpt-x"], input="n\n")
    assert "will receive posting and CV text" in r.output and r.exit_code == 1


def test_compare_page_renders(home):
    from streamlit.testing.v1 import AppTest

    CliRunner().invoke(app, ["compare", "tailor", "-m", BIG, "-n", "1"])
    at = AppTest.from_string("from autotailor.ui import page_compare\npage_compare.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.dataframe and len(at.dataframe[0].value) == 2
