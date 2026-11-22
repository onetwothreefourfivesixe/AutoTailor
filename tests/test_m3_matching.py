import json
import re
import sqlite3
from datetime import date
from importlib import resources

import pytest
import yaml
from langchain_core.embeddings import Embeddings
from typer.testing import CliRunner

from autotailor import db, matching
from autotailor.chains import prompts
from autotailor.cli import app
from autotailor.config import DegreeLevel, Education, Profile, Settings, save_settings
from autotailor.cv import MasterCV
from autotailor.models import (
    CriterionScore,
    FitAssessment,
    JobPosting,
    Match,
    PostingStatus,
    Source,
    Verdict,
)

VOCAB = ["python", "pytorch", "vision", "model", "api", "test", "accounting", "excel", "audit", "intern"]


class KeywordEmbeddings(Embeddings):
    """Deterministic stand-in for a real embedding model: counts vocabulary words."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _vec(self, text: str) -> list[float]:
        words = re.findall(r"[a-z]+", text.lower())
        return [float(words.count(w)) + 0.01 for w in VOCAB]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.extend(texts)
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def _cv() -> MasterCV:
    text = (resources.files("autotailor.templates") / "master_cv.example.yaml").read_text()
    return MasterCV.model_validate(yaml.safe_load(text))


def _settings() -> Settings:
    s = Settings()
    s.profile = Profile(
        role_interests=["Machine learning intern"],
        education=[Education(level=DegreeLevel.BACHELOR, field="Computer Science",
                             status="in_progress", graduation_date=date(2029, 5, 31))],
    )  # fmt: skip
    return s


def _posting(pid: str, title: str, description: str) -> JobPosting:
    return JobPosting(
        id=pid, source=Source.PASTE, source_ref=pid.split(":")[1], company="Co", title=title,
        description=description, status=PostingStatus.RESOLVED,
    )  # fmt: skip


ML = _posting("paste:ml", "ML Intern", "Train a vision model in PyTorch and Python. Write test code.")
ACCT = _posting("paste:acct", "Accounting Intern", "Accounting close, Excel reconciliations, audit support.")


def _assessment(*scores: int, ids=()) -> FitAssessment:
    s = [CriterionScore(score=x, reason="r") for x in scores]
    return FitAssessment(skills=s[0], experience=s[1], level=s[2], interests=s[3], relevant_bullet_ids=list(ids))


# --- embeddings and prescore -------------------------------------------------


def test_cv_chunks_cover_bullets_and_skills():
    chunks = matching.cv_chunks(_cv())
    assert chunks["cv:exp-acme-intern-api"].startswith("Software Engineering Intern, Acme Corp: Built a REST API")
    assert "cv:edu-example-ms-coursework" in chunks
    assert chunks["cv:skills:Languages"] == "Skills, Languages: Python, TypeScript, SQL"


def test_embeddings_are_cached_and_refreshed(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    emb = KeywordEmbeddings()
    first = matching.embed_cached(conn, emb, "m", {"a": "python", "b": "excel"}, "q: ")
    assert emb.calls == ["q: python", "q: excel"]
    assert abs(float(first["a"] @ first["a"]) - 1.0) < 1e-5  # unit length

    emb.calls.clear()
    again = matching.embed_cached(conn, emb, "m", {"a": "python", "b": "excel"}, "q: ")
    assert emb.calls == [] and (again["a"] == first["a"]).all()

    matching.embed_cached(conn, emb, "m", {"a": "python audit", "b": "excel"}, "q: ")
    assert emb.calls == ["q: python audit"]  # only the changed text
    matching.embed_cached(conn, emb, "other-model", {"a": "python audit"}, "q: ")
    assert emb.calls[-1] == "q: python audit"  # cached per model


def test_prescore_prefers_matching_posting(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    scores = matching.prescore_postings(conn, [ML, ACCT], _cv(), _settings(), KeywordEmbeddings())
    assert scores["paste:ml"] > scores["paste:acct"]


# --- fit scores --------------------------------------------------------------


def test_profile_text():
    s = _settings()
    text = matching.profile_text(_cv(), s.profile)
    assert text.startswith("Role interests: Machine learning intern")
    assert "- bachelor in Computer Science (in progress, expected May 2029)" in text
    assert "## Software Engineering Intern | Acme Corp | 2026-06 – 2026-08" in text
    assert "- [exp-acme-intern-api] Built a REST API" in text
    assert "- Languages: Python, TypeScript, SQL" in text


def test_compute_fit_score():
    assert matching.compute_fit_score(_assessment(5, 5, 5, 5)) == 1.0
    assert matching.compute_fit_score(_assessment(1, 1, 1, 1)) == 0.0
    assert matching.compute_fit_score(_assessment(5, 1, 3, 3)) == pytest.approx(0.35 + 0.075 + 0.075)
    assert sum(matching.FIT_WEIGHTS.values()) == pytest.approx(1.0)


def test_clean_assessment_drops_unknown_ids():
    a = _assessment(3, 3, 3, 3, ids=["[exp-acme-intern-api]", "made-up", "exp-acme-intern-api", "proj-example-model"])
    cleaned, dropped = matching.clean_assessment(a, _cv())
    assert cleaned.relevant_bullet_ids == ["exp-acme-intern-api", "proj-example-model"]
    assert dropped == ["made-up"]


def test_fit_reasons():
    a = _assessment(4, 3, 5, 2).model_copy(update={"missing": ["CPA track"]})
    assert matching.fit_reasons(a) == [
        "Skills 4/5: r", "Experience 3/5: r", "Level 5/5: r", "Interests 2/5: r", "Missing: CPA track",
    ]  # fmt: skip


# --- storage -----------------------------------------------------------------


def test_migration_v2_to_v3(tmp_path):
    path = tmp_path / "v2.db"
    raw = sqlite3.connect(path)
    raw.executescript(db._SCHEMA_V1)
    raw.executescript(db._SCHEMA_V2)
    raw.execute("PRAGMA user_version = 2")
    raw.commit()
    raw.close()
    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION  # upgrades all the way
    cols = {r[1] for r in conn.execute("PRAGMA table_info(matches)")}
    assert {"prescore", "fit_json"} <= cols


def test_fit_storage_survives_rejudge(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_posting(conn, ML)
    db.save_match(conn, Match(posting_id=ML.id, verdict=Verdict.ELIGIBLE))
    db.save_prescore(conn, ML.id, 0.71)
    fit = _assessment(4, 4, 5, 3, ids=["exp-acme-intern-api"])
    db.save_fit(conn, ML.id, 0.8, ["good"], fit)
    db.save_match(conn, Match(posting_id=ML.id, verdict=Verdict.UNCLEAR, verdict_reasons=["re-screened"]))
    m = db.get_match(conn, ML.id)
    assert (m.verdict, m.prescore, m.fit_score, m.fit) == (Verdict.UNCLEAR, 0.71, 0.8, fit)


# --- prompt loader -----------------------------------------------------------


def test_loader_fills_any_variables(monkeypatch):
    data = {
        "score_fit": {
            "system": ["Score {fairly}."],
            "human": "PROFILE:\n{profile}\n\nPOSTING:\n{posting}",
            "examples": [
                {"profile": ["line 1", "line 2"], "posting": "a {json} posting", "answer": {"x": 1}},
            ],
        }
    }
    monkeypatch.setattr(prompts, "_load_file", lambda: data)
    prompt = prompts.load_prompt("score_fit")
    assert sorted(prompt.input_variables) == ["posting", "profile"]
    msgs = prompt.format_messages(profile="P", posting="Q")
    assert msgs[0].content == "Score {fairly}."
    assert msgs[1].content == "PROFILE:\nline 1\nline 2\n\nPOSTING:\na {json} posting"
    assert json.loads(msgs[2].content) == {"x": 1}
    assert msgs[3].content == "PROFILE:\nP\n\nPOSTING:\nQ"

    data["score_fit"]["examples"][0].pop("profile")
    with pytest.raises(ValueError, match="lacks \\['profile'\\]"):
        prompts.load_prompt("score_fit")


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    s = _settings()
    s.run.jobs_per_run = 1
    s.run.min_fit_score = 0.5
    save_settings(s, tmp_path / "config.toml")
    monkeypatch.setattr(matching, "get_embeddings", lambda settings: KeywordEmbeddings())
    return tmp_path


def _store(home, *postings_and_verdicts):
    conn = db.connect(home / "data" / "autotailor.db")
    for p, v in postings_and_verdicts:
        db.upsert_posting(conn, p)
        db.save_match(conn, Match(posting_id=p.id, verdict=v))
    conn.close()


def test_cli_rank_and_shortlist(home, monkeypatch):
    phd = _posting("paste:phd", "PhD Intern", "Python vision model research.")
    _store(home, (ML, Verdict.ELIGIBLE), (ACCT, Verdict.UNCLEAR), (phd, Verdict.INELIGIBLE))

    scored: list[str] = []

    def fake_score(postings, profile, settings, max_concurrency=4, ids=None):
        assert "[exp-acme-intern-api]" in profile
        scored.extend(postings)
        return [
            _assessment(5, 4, 5, 4, ids=["exp-acme-intern-api", "bogus"]) if "PyTorch" in t
            else _assessment(1, 1, 4, 1)
            for t in postings
        ]  # fmt: skip

    monkeypatch.setattr("autotailor.chains.score_fit.score_fit_batch", fake_score)
    runner = CliRunner()
    r = runner.invoke(app, ["rank"])
    assert r.exit_code == 0, r.output
    assert len(scored) == 2  # the ineligible PhD posting is not ranked
    assert "ignored unknown CV bullet IDs ['bogus']" in r.output
    lines = [line for line in r.output.splitlines() if "  paste:" in line]  # ranking rows only
    assert "paste:ml" in lines[0] and "paste:acct" in lines[1]
    assert lines[0].lstrip().startswith("0.88")  # 0.35*1 + 0.35*0.75 + 0.15*1 + 0.15*0.75 = 0.875
    assert "1 postings meet the minimum fit score" in r.output

    r = runner.invoke(app, ["shortlist"])
    assert "1. 0.88  paste:ml" in r.output
    r = runner.invoke(app, ["jobs", "show", "paste:ml"])
    assert "Fit score: 0.88" in r.output and "Most relevant CV bullets: exp-acme-intern-api" in r.output

    scored.clear()
    runner.invoke(app, ["rank"])
    assert not scored  # already scored; --rescore forces it
    runner.invoke(app, ["rank", "--rescore"])
    assert len(scored) == 2


def test_cli_rank_without_chain(home, monkeypatch):
    _store(home, (ML, Verdict.ELIGIBLE), (ACCT, Verdict.ELIGIBLE))

    def unwritten(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr("autotailor.chains.score_fit.score_fit_batch", unwritten)
    r = CliRunner().invoke(app, ["rank"])
    assert r.exit_code == 0, r.output
    assert "Ranking by embedding similarity only" in r.output
    lines = [line for line in r.output.splitlines() if "  paste:" in line]
    assert "paste:ml" in lines[0]
    assert "0 postings meet the minimum fit score" in r.output


def test_cli_rank_survives_model_errors(home, monkeypatch):
    _store(home, (ML, Verdict.ELIGIBLE))

    def no_key(*args, **kwargs):
        raise ValueError("API key required")

    monkeypatch.setattr("autotailor.chains.score_fit.score_fit_batch", no_key)
    r = CliRunner().invoke(app, ["rank"])
    assert r.exit_code == 0, r.output
    assert "Could not run the fit-scoring model" in r.output and "API key required" in r.output
    assert "paste:ml" in r.output


def test_cli_rank_needs_eligible_postings(home):
    r = CliRunner().invoke(app, ["rank"])
    assert r.exit_code == 1 and "No eligible postings" in r.output


# --- model modes and embedding providers -------------------------------------


def test_models_mode():
    from autotailor.config import EmbeddingSpec, ModelSpec

    s = Settings()
    assert s.models.mode() == "mixed"  # cloud chat default + local embeddings
    s.models.embeddings = EmbeddingSpec(provider="openai", model="text-embedding-3-small")
    assert s.models.mode() == "cloud"
    local = ModelSpec(provider="ollama", model="m")
    s.models.default = local
    s.models.embeddings = EmbeddingSpec()
    assert s.models.mode() == "local"
    s.models.steps["tailor"] = ModelSpec(provider="google_genai", model="g")
    assert s.models.mode() == "mixed"


def test_embedding_prefixes():
    from autotailor.config import EmbeddingSpec

    assert EmbeddingSpec().prefixes == ("search_query: ", "search_document: ")
    assert EmbeddingSpec(provider="openai", model="text-embedding-3-small").prefixes == ("", "")
    assert EmbeddingSpec(query_prefix="", document_prefix="doc: ").prefixes == ("", "doc: ")
    assert EmbeddingSpec(provider="openai", model="x").cache_key == "openai:x"


@pytest.mark.parametrize(
    ("provider", "model", "cls"),
    [
        ("ollama", "nomic-embed-text", "OllamaEmbeddings"),
        ("openai", "text-embedding-3-small", "OpenAIEmbeddings"),
        ("google_genai", "gemini-embedding-001", "GoogleGenerativeAIEmbeddings"),
    ],
)
def test_get_embeddings_per_provider(monkeypatch, tmp_path, provider, model, cls):
    from autotailor.config import EmbeddingSpec

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    s = Settings()
    s.models.embeddings = EmbeddingSpec(provider=provider, model=model)
    assert type(matching.get_embeddings(s)).__name__ == cls


def test_doctor_reports_mode_and_checks_ollama(tmp_path, monkeypatch):
    import httpx

    from autotailor.config import EmbeddingSpec, ModelSpec

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["init"])
    s = _settings()
    s.models.default = ModelSpec(provider="ollama", model="qwen3:8b")
    save_settings(s, tmp_path / "config.toml")
    monkeypatch.setattr(
        httpx, "get",
        lambda url, timeout: httpx.Response(200, json={"models": [{"name": "nomic-embed-text:latest"}]},
                                            request=httpx.Request("GET", url)),
    )  # fmt: skip
    out = runner.invoke(app, ["doctor"]).output
    assert "all local: no posting or CV text leaves this machine" in out
    assert "ok   ollama model nomic-embed-text" in out
    assert "FAIL ollama model qwen3:8b: run `ollama pull qwen3:8b`" in out
    assert "API_KEY" not in out  # nothing in the cloud, so no keys needed

    s.models.embeddings = EmbeddingSpec(provider="openai", model="text-embedding-3-small")
    s.models.default = ModelSpec(provider="openai", model="gpt-x")
    save_settings(s, tmp_path / "config.toml")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = runner.invoke(app, ["doctor"]).output
    assert "cloud only" in out and "FAIL OPENAI_API_KEY" in out and "Ollama" not in out
