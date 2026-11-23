"""M8 plumbing: question parsing, the facts-and-style check, AnswerTools, the CLI and the UI page.

The answer graph has its own tests (test_m8_answer_graph.py). Models are fakes.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from test_m4_resume import CV, CV_TEXT, _store
from typer.testing import CliRunner

from autotailor import db
from autotailor.answers import AnswerTools, check_answer, parse_question, split_questions, word_count
from autotailor.chains.prompts import load_prompt
from autotailor.cli import app
from autotailor.config import Settings
from autotailor.models import AnswerPoint, AnswerPoints

# --- questions -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "limit"),
    [
        ("Why do you want to work at Acme? (150 words)", 150),
        ("Describe a project you are proud of. 200 word limit", 200),
        ("What excites you about robotics? Word limit: 100", 100),
        ("Tell us about yourself (max 600 characters)", 100),
        ("Anything else we should know?", None),
    ],
)
def test_parse_question(text, limit):
    assert parse_question(text) == (text, limit)


def test_split_questions():
    text = "Why us?\n(150 words)\n\n\nDescribe a project.\n\n  \nAnything else?"
    assert split_questions(text) == ["Why us? (150 words)", "Describe a project.", "Anything else?"]


# --- the check ----------------------------------------------------------------------------

ALLOWED = (
    "Trained a YOLO-based object detector in PyTorch on 12k labeled warehouse images, reaching 0.81 mAP. "
    "Research Intern University Vision Lab. Acme Robotics ML Intern. Python C++ SQL PyTorch OpenCV."
)


def test_clean_prose_passes():
    text = ("I trained a YOLO detector in PyTorch on 12k warehouse images and reached 0.81 mAP. "
            "That work at the University Vision Lab is why the ML Intern role at Acme Robotics appeals to me. "
            "My time there taught me to check data before trusting a model.")  # fmt: skip
    assert check_answer(text, ALLOWED, 150) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I trained a detector on 50k images.", "Remove the numbers ['50k']"),
        ("I trained a detector with TensorFlow at the lab.", "['tensorflow']"),
        ("I worked with Google on detectors.", "['google']"),
        ("I trained a detector — and it worked.", "dashes"),
        ("My work is a testament to my curiosity.", "stock phrases: ['testament to']"),
        ("- trained a detector\n- built a tool", "no bold, headings or bullet points"),
    ],
)
def test_check_catches(text, expected):
    problems = check_answer(text, ALLOWED, 150)
    assert any(expected in p for p in problems), problems


def test_word_limit():
    text = "I like robots. " * 20
    assert word_count(text) == 60
    assert any("It is 60 words; the limit is 50" in p for p in check_answer(text, ALLOWED, 50))
    assert check_answer(text, ALLOWED, None) == []


# --- tools -------------------------------------------------------------------------------------


class FakeSuggestLLM(RunnableLambda):
    """Stands in for any chat model: structured output gives fixed points; as plain text it echoes."""

    def __init__(self) -> None:
        super().__init__(lambda pv: AIMessage(content="I like robots."))
        self.prompt = None

    def with_structured_output(self, schema, **kw):
        def respond(pv):
            self.prompt = "\n".join(str(m.content) for m in pv.to_messages())
            return AnswerPoints(points=[
                AnswerPoint(source_id="[exp-lab-detector]", point="Trained a detector in PyTorch", why="ML"),
                AnswerPoint(source_id="ghost", point="Invented", why="x"),
                AnswerPoint(source_id="exp-lab-detector", point="Duplicate", why="x"),
                AnswerPoint(source_id="proj-ocr", point="Built an OCR pipeline", why="shipping"),
            ])  # fmt: skip

        return RunnableLambda(respond)


@pytest.fixture
def tools(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    _store(conn, "lever:a", "ML Intern", 0.9, company="Acme Robotics")
    qid = db.add_question(conn, "lever:a", "Why do you want to work here? (120 words)", 120)
    llm = FakeSuggestLLM()
    return AnswerTools(conn, CV, Settings(), suggest_llm=llm), qid, llm


def test_suggest_keeps_only_real_bullets(tools):
    t, qid, llm = tools
    points = t.suggest(qid)
    assert [p["source_id"] for p in points] == ["exp-lab-detector", "proj-ocr"]
    assert points[0]["cv_text"].startswith("Trained a YOLO-based")
    assert "Why do you want to work here?" in llm.prompt and "[exp-lab-detector]" in llm.prompt  # profile with IDs
    stored = json.loads(db.get_question(t.conn, qid)["points_json"])
    assert stored == points


def test_inputs_and_check_use_the_chosen_points(tools):
    t, qid, _ = tools
    chosen = t.suggest(qid)[:1]
    inputs = t.draft_inputs(qid, chosen, "mention the lab")
    assert inputs["word_limit"] == 120 and inputs["company"] == "Acme Robotics" and inputs["notes"] == "mention the lab"
    assert "Research Intern University Vision Lab" in inputs["points"] and "12k labeled" in inputs["points"]
    assert t.humanize_inputs(qid, "text", [])["feedback"] == "None yet."
    assert "- too long" in t.humanize_inputs(qid, "text", ["too long"])["feedback"]

    ok = "At the University Vision Lab I trained a YOLO detector in PyTorch that reached 0.81 mAP."
    assert t.check(qid, ok, chosen) == []
    assert t.check(qid, "I used OpenCV.", chosen) == []  # a CV skill: allowed even though that bullet wasn't chosen
    assert any("['35']" in p for p in t.check(qid, "I cut latency by 35%.", chosen))  # that bullet wasn't chosen


def test_prompts_load():
    assert sorted(load_prompt("suggest_points", AnswerPoints).input_variables) == ["company", "posting", "profile", "question", "title"]
    assert sorted(load_prompt("humanize").input_variables) == ["feedback", "text", "word_limit"]


# --- CLI and UI --------------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "ML Intern", 0.9, company="Acme Robotics")
    conn.close()
    monkeypatch.setattr("autotailor.llm.get_chat_model", lambda settings, step: FakeSuggestLLM())
    return tmp_path


def test_cli_add_list_and_suggest(home):
    runner = CliRunner()
    qfile = home / "q.txt"
    qfile.write_text("Why Acme? (150 words)\n\nDescribe a project you're proud of.\n")
    r = runner.invoke(app, ["answers", "add", "lever:a", "--file", str(qfile)])
    assert r.exit_code == 0, r.output
    assert "[150 words]" in r.output
    r = runner.invoke(app, ["answers", "run", "--no-ask"])
    assert r.exit_code == 0, r.output
    assert "2 points suggested; waiting for your decision" in r.output
    md = next((home / "data" / "outputs" / "answers").glob("*.md")).read_text()
    assert "## Why Acme? (150 words)" in md and "Built an OCR pipeline (`proj-ocr`)" in md
    assert "waiting" in runner.invoke(app, ["answers", "list"]).output
    assert runner.invoke(app, ["answers", "add", "nope:1", "Why?"]).exit_code == 1


def test_answers_page_renders(home):
    from streamlit.testing.v1 import AppTest

    conn = db.connect(home / "data" / "autotailor.db")
    q = db.add_question(conn, "lever:a", "Why Acme?", None)
    db.update_question(conn, q, status="drafted", answer="I like robots.", points=[], problems=["too short?"])
    conn.close()
    at = AppTest.from_string("from autotailor.ui import page_answers\npage_answers.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.text_area(key=f"ans{q}").value == "I like robots."
    assert any("too short?" in w.value for w in at.warning)
