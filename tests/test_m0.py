from datetime import date
from importlib import resources

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from autotailor import db
from autotailor.cli import app
from autotailor.config import (
    DegreeLevel,
    Education,
    ModelSpec,
    Settings,
    load_settings,
    save_settings,
    set_value,
)
from autotailor.cv import MasterCV, load_master_cv
from autotailor.llm import model_kwargs
from autotailor.models import Evidence, JobPosting, PostingStatus, Requirements, Source

# --- config ---------------------------------------------------------------


def test_settings_roundtrip(tmp_path):
    s = Settings()
    s.profile.education.append(
        Education(
            level=DegreeLevel.MASTER,
            field="Computer Science",
            status="in_progress",
            graduation_date=date(2027, 5, 15),
        )
    )
    s.run.jobs_per_run = 7
    s.models.steps["tailor"] = ModelSpec(provider="ollama", model="some-local-model")
    path = tmp_path / "config.toml"
    save_settings(s, path)
    assert load_settings(path) == s


def test_missing_config_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.toml") == Settings()


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[run]\njobs_per_runn = 3\n")
    with pytest.raises(ValidationError):
        load_settings(path)


def test_set_value():
    s = set_value(Settings(), "run.jobs_per_run", "12")
    assert s.run.jobs_per_run == 12
    s = set_value(s, "profile.locations", '["NYC", "SF"]')
    assert s.profile.locations == ["NYC", "SF"]
    s = set_value(s, "profile.name", "Ada")
    assert s.profile.name == "Ada"


def test_set_value_rejects_bad_input():
    with pytest.raises(ValidationError):
        set_value(Settings(), "run.jobs_per_run", "0")
    with pytest.raises(ValidationError):
        set_value(Settings(), "run.no_such_key", "1")
    with pytest.raises(KeyError):
        set_value(Settings(), "nope.jobs_per_run", "1")


def test_step_model_falls_back_to_default():
    s = Settings()
    s.models.steps["tailor"] = ModelSpec(provider="ollama", model="m")
    assert s.models.for_step("tailor").is_local
    assert s.models.for_step("extract") == s.models.default
    assert not s.models.default.is_local


def test_model_kwargs_per_provider():
    s = Settings()
    assert model_kwargs(ModelSpec(), s) == {"max_tokens": 16000}
    assert model_kwargs(ModelSpec(provider="openai", model="m"), s) == {"max_tokens": 16000}
    assert model_kwargs(ModelSpec(provider="google_genai", model="m"), s) == {
        "max_output_tokens": 16000
    }
    assert model_kwargs(ModelSpec(provider="ollama", model="m"), s) == {
        "base_url": "http://localhost:11434",
        "num_predict": 16000,
    }


# --- master CV ------------------------------------------------------------


def _example_cv() -> dict:
    text = (resources.files("autotailor.templates") / "master_cv.example.yaml").read_text()
    return yaml.safe_load(text)


def test_example_cv_is_valid():
    cv = MasterCV.model_validate(_example_cv())
    assert "exp-acme-intern-api" in cv.bullets()
    assert len(cv.entries()) == 2


def test_duplicate_ids_rejected():
    data = _example_cv()
    data["projects"][0]["bullets"][0]["id"] = "exp-acme-intern-api"
    with pytest.raises(ValidationError, match="Duplicate IDs"):
        MasterCV.model_validate(data)


def test_invalid_id_rejected():
    data = _example_cv()
    data["projects"][0]["id"] = "Has Spaces"
    with pytest.raises(ValidationError, match="Invalid IDs"):
        MasterCV.model_validate(data)


# --- database -------------------------------------------------------------


def _posting(**kw) -> JobPosting:
    base = dict(
        id="runway:abc", source=Source.RUNWAY, source_ref="abc", company="Acme", title="SWE Intern"
    )
    return JobPosting(**{**base, **kw})


def test_db_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "x" / "a.db")
    p = _posting(
        description="Do things.",
        status=PostingStatus.RESOLVED,
        external_score=0.95,
        requirements=Requirements(
            degree_levels=[DegreeLevel.MASTER],
            evidence=[Evidence(field="degree_levels", quote="Master's required")],
        ),
    )
    db.upsert_posting(conn, p)
    assert db.get_posting(conn, "runway:abc") == p
    assert db.table_counts(conn)["postings"] == 1


def test_reimport_does_not_erase_description(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_posting(conn, _posting(description="Full text.", status=PostingStatus.RESOLVED))
    db.upsert_posting(conn, _posting())  # bare re-import from a later email
    got = db.get_posting(conn, "runway:abc")
    assert got.description == "Full text."
    assert got.status == PostingStatus.RESOLVED
    assert db.table_counts(conn)["postings"] == 1


def test_db_reopen_keeps_data(tmp_path):
    path = tmp_path / "a.db"
    conn = db.connect(path)
    db.upsert_posting(conn, _posting())
    conn.close()
    assert db.table_counts(db.connect(path))["postings"] == 1


# --- CLI ------------------------------------------------------------------


def test_cli_init_set_validate(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    runner = CliRunner()

    r = runner.invoke(app, ["init"])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "config.toml").exists()
    assert (tmp_path / "data" / "autotailor.db").exists()
    load_master_cv(tmp_path / "cv" / "master.yaml")

    r = runner.invoke(app, ["config", "set", "run.jobs_per_run", "5"])
    assert r.exit_code == 0, r.output
    assert load_settings(tmp_path / "config.toml").run.jobs_per_run == 5

    r = runner.invoke(app, ["config", "set", "run.jobs_per_run", "0"])
    assert r.exit_code == 1
    assert load_settings(tmp_path / "config.toml").run.jobs_per_run == 5

    assert runner.invoke(app, ["cv", "validate"]).exit_code == 0

    # init is safe to repeat: it must not overwrite the user's files
    r = runner.invoke(app, ["init"])
    assert "kept" in r.output
    assert load_settings(tmp_path / "config.toml").run.jobs_per_run == 5

    # doctor fails until a profile education entry exists
    assert runner.invoke(app, ["doctor"]).exit_code == 1


def test_doctor_checks_key_for_each_cloud_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    runner = CliRunner()
    runner.invoke(app, ["init"])
    s = Settings()
    s.models.steps["extract"] = ModelSpec(provider="ollama", model="m")
    s.models.steps["score"] = ModelSpec(provider="openai", model="m")
    s.models.steps["tailor"] = ModelSpec(provider="google_genai", model="m")
    save_settings(s, tmp_path / "config.toml")

    out = runner.invoke(app, ["doctor"]).output
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        assert f"FAIL {var}" in out  # humanize still uses the Anthropic default

    # keys are read from the .env file that `init` created
    env = tmp_path / ".env"
    assert "OPENAI_API_KEY=" in env.read_text()
    env.write_text("OPENAI_API_KEY=x\n")
    out = runner.invoke(app, ["doctor"]).output
    assert "ok   OPENAI_API_KEY" in out
    assert "FAIL GOOGLE_API_KEY" in out
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)  # loading set it; clean up


def test_unknown_provider_rejected():
    with pytest.raises(ValidationError):
        ModelSpec(provider="nope", model="m")
