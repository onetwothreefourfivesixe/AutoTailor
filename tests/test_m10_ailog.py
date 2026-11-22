"""M10 step 3: the AI call log, eligibility overrides, and the posting tabs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from test_m4_resume import CV_TEXT, _store
from typer.testing import CliRunner

from autotailor import ailog, db, matching
from autotailor.cli import app
from autotailor.config import Settings
from autotailor.models import Match, Verdict


def _model(tmp_path, replies, step="score", db_name="a.db"):
    handler = ailog.CallLogger(tmp_path / db_name, step, "ollama", "small:4b", True)
    return GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]), callbacks=[handler])


def test_logs_calls_with_item_from_metadata_or_context(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    llm = _model(tmp_path, ["first", "second"])
    llm.invoke("hello", config={"metadata": {"item": "lever:a"}})
    token = ailog.current_item.set("question:7")
    llm.invoke([("system", "be brief"), ("human", "hi")])
    ailog.current_item.reset(token)
    rows = ailog.calls(conn)
    assert [(r["item"], r["response"]) for r in rows] == [("question:7", "second"), ("lever:a", "first")]
    assert '"be brief"' in rows[0]["messages"] and rows[0]["local"] == 1 and rows[0]["step"] == "score"
    assert ailog.calls(conn, "lever:a")[0]["response"] == "first"


def test_keep_prompts_off_and_purge(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    handler = ailog.CallLogger(tmp_path / "a.db", "tailor", "openai", "gpt", False, keep_text=False)
    GenericFakeChatModel(messages=iter([AIMessage(content="secret")]), callbacks=[handler]).invoke("cv text")
    row = ailog.calls(conn)[0]
    assert row["messages"] is None and row["response"] is None and row["ms"] is not None and row["local"] == 0
    old = (datetime.now(UTC) - timedelta(days=20)).isoformat(timespec="seconds")
    conn.execute("UPDATE llm_calls SET at = ?", (old,))
    conn.commit()
    assert ailog.purge(conn, 15) == 1 and ailog.calls(conn) == []


def test_get_chat_model_attaches_the_logger(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    captured = {}
    monkeypatch.setattr("langchain.chat_models.init_chat_model", lambda *a, **k: captured.update(k) or "model")
    from autotailor.llm import get_chat_model

    s = Settings()
    get_chat_model(s, "tailor")
    assert isinstance(captured["callbacks"][0], ailog.CallLogger)
    s.log.enabled = False
    get_chat_model(s, "tailor")
    assert captured["callbacks"] is None


def test_overrides_win_everywhere(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    _store(conn, "lever:a", "SWE Intern", 0.9)
    db.save_match(conn, Match(posting_id="lever:a", verdict=Verdict.INELIGIBLE, verdict_reasons=["needs a PhD"]))
    assert matching.ranked(conn, Settings()) == []
    db.set_override(conn, "lever:a", Verdict.ELIGIBLE, "recruiter said first-years are fine")
    m = db.get_match(conn, "lever:a")
    assert m.verdict == Verdict.ELIGIBLE and m.original_verdict == Verdict.INELIGIBLE
    assert m.verdict_reasons[0].startswith("Overridden by you: recruiter said")
    assert [p.id for p, _ in matching.ranked(conn, Settings())] == ["lever:a"]
    db.save_match(conn, Match(posting_id="lever:a", verdict=Verdict.INELIGIBLE, verdict_reasons=["re-screened"]))
    assert db.get_match(conn, "lever:a").verdict == Verdict.ELIGIBLE  # survives --rejudge
    assert list(db.all_matches(conn, Verdict.ELIGIBLE)) == ["lever:a"]
    db.clear_override(conn, "lever:a")
    assert db.get_match(conn, "lever:a").verdict == Verdict.INELIGIBLE


def test_highlight():
    from autotailor.ui.page_postings import highlight

    out = highlight("Must be a current\nundergraduate <student>.", ["current undergraduate"])
    assert "<mark>current<br>undergraduate</mark>" in out.replace("\n", "<br>") or "<mark>current" in out
    assert "&lt;student&gt;" in out


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9)
    conn.close()
    return tmp_path


def test_posting_tabs_and_override_button(home):
    from streamlit.testing.v1 import AppTest

    _model(home / "data", ["logged"], db_name="autotailor.db").invoke("hi", config={"metadata": {"item": "lever:a"}})
    at = AppTest.from_string("from autotailor.ui import page_postings\npage_postings.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs][:6] == ["Posting", "Eligibility", "Fit", "Resume", "Answers (0)", "AI log (1)"]
    at.selectbox(key="ov.v.lever:a").select("ineligible")
    at.text_input(key="ov.n.lever:a").input("Not my field")
    at = next(b for b in at.button if b.key == "ov.s.lever:a").click().run()
    conn = db.connect(home / "data" / "autotailor.db")
    m = db.get_match(conn, "lever:a")
    assert m.verdict == Verdict.INELIGIBLE and "Not my field" in m.verdict_reasons[0]
