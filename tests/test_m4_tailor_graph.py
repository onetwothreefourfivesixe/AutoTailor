"""Tests for the M4 exercise in src/autotailor/chains/tailor_graph.py.

As before: "xfail" until implemented, then normal tests ("xpass").
Delete the `pytestmark` line once everything passes.
"""

import os
from pathlib import Path

import pytest
import yaml
from langchain_core.runnables import RunnableLambda

from autotailor.chains.tailor_graph import build_graph
from autotailor.config import TailorSettings
from autotailor.cv import MasterCV
from autotailor.models import TailoredBullet, TailoredResume

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="M4 exercise not done yet")

FIXTURES = Path(__file__).parent / "fixtures"
CV = MasterCV.model_validate(yaml.safe_load((FIXTURES / "cv_ml_student.yaml").read_text()))
PROFILE = (FIXTURES / "profile_ml_student.txt").read_text()
LIMITS = TailorSettings(min_bullets=2, max_bullets=5, max_attempts=3)
live = pytest.mark.skipif(not os.environ.get("AUTOTAILOR_LIVE"), reason="set AUTOTAILOR_LIVE=1")

GOOD = TailoredResume(
    bullets=[
        TailoredBullet(source_id="exp-lab-detector", text="Trained a YOLO-based object detector in PyTorch on 12k labeled warehouse images, reaching 0.81 mAP"),
        TailoredBullet(source_id="proj-ocr", text="Built an OCR pipeline with OpenCV and a CRNN model to read shelf price labels"),
    ],
    skills=["PyTorch", "Python"],
)
BAD = TailoredResume(
    bullets=[TailoredBullet(source_id="made-up-bullet", text="Led a team of 40 engineers")],
    skills=["Kubernetes"],
)


class ScriptedModel:
    """Returns the given drafts in order (repeating the last), and records every prompt."""

    def __init__(self, *drafts: TailoredResume) -> None:
        self.drafts = list(drafts)
        self.prompts: list[str] = []
        self.schema = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema

        def respond(prompt_value):
            self.prompts.append("\n".join(m.content for m in prompt_value.to_messages()))
            return self.drafts[min(len(self.prompts), len(self.drafts)) - 1]

        return RunnableLambda(respond)


def _run(model):
    return build_graph(model, CV, LIMITS).invoke({"posting": "POSTING-MARKER", "profile": "PROFILE-MARKER"})


def test_graph_has_draft_and_verify_nodes():
    nodes = set(build_graph(ScriptedModel(GOOD), CV, LIMITS).get_graph().nodes)
    assert {"draft", "verify"} <= nodes


def test_good_first_draft_ends_after_one_attempt():
    model = ScriptedModel(GOOD)
    final = _run(model)
    assert model.schema is TailoredResume, "use llm.with_structured_output(TailoredResume)"
    assert final["resume"] == GOOD and final["errors"] == [] and final["attempts"] == 1
    assert "POSTING-MARKER" in model.prompts[0] and "PROFILE-MARKER" in model.prompts[0]


def test_retry_sends_verifier_feedback():
    model = ScriptedModel(BAD, GOOD)
    final = _run(model)
    assert final["attempts"] == 2 and final["errors"] == [] and final["resume"] == GOOD
    assert "made-up-bullet" in model.prompts[1], "the second prompt must include the first draft's errors"
    assert "made-up-bullet" not in model.prompts[0]


def test_gives_up_after_max_attempts():
    model = ScriptedModel(BAD)
    final = _run(model)
    assert final["attempts"] == LIMITS.max_attempts == len(model.prompts)
    assert final["errors"] and final["resume"] == BAD


@live
def test_live_tailors_sample_posting():
    from autotailor.chains.tailor_graph import tailor_posting
    from autotailor.config import load_settings
    from autotailor.paths import get_paths

    settings = load_settings(get_paths().config)
    settings.tailor = TailorSettings(min_bullets=3, max_bullets=6)
    posting = (FIXTURES / "sample_posting.txt").read_text()
    result = tailor_posting(posting, PROFILE, CV, settings)
    print(f"\nattempts: {result.attempts}\nerrors: {result.errors}")
    if result.resume:
        print(result.resume.model_dump_json(indent=1))
    assert result.errors == [], "the final draft must pass verification"
    ids = [b.source_id for b in result.resume.bullets]
    assert "exp-lab-detector" in ids
    assert "proj-budget" not in ids, "the budgeting app is irrelevant to a perception internship"


@pytest.mark.xfail(reason="M10 exercise: keep a history of every draft (see the end of tailor_graph.py)", strict=False)
def test_history_keeps_every_draft():
    bad = TailoredResume(bullets=[TailoredBullet(source_id="ghost", text="Invented")], skills=[])
    model = ScriptedModel(bad, GOOD)
    final = _run(model)
    history = final["history"]
    assert [h["attempt"] for h in history] == [1, 2]
    assert history[0]["bullets"] == ["ghost"] and history[0]["errors"]
    assert history[1]["errors"] == [] and history[1]["bullets"] == [b.source_id for b in GOOD.bullets]
