"""Tests for the M3 exercise in src/autotailor/chains/score_fit.py.

As before: "xfail" until implemented, then normal tests ("xpass").
Delete the `pytestmark` line once everything passes.
"""

import os
import re
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

from autotailor.chains.score_fit import build_chain, build_prompt
from autotailor.matching import compute_fit_score
from autotailor.models import CriterionScore, FitAssessment

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="M3 exercise not done yet")

FIXTURES = Path(__file__).parent / "fixtures"
PROFILE = (FIXTURES / "profile_ml_student.txt").read_text()
live = pytest.mark.skipif(not os.environ.get("AUTOTAILOR_LIVE"), reason="set AUTOTAILOR_LIVE=1")


def _answer() -> FitAssessment:
    c = CriterionScore(score=4, reason="ok")
    return FitAssessment(skills=c, experience=c, level=c, interests=c, relevant_bullet_ids=["exp-lab-detector"])


def test_prompt_variables():
    assert sorted(build_prompt().input_variables) == ["posting", "profile"]


def test_prompt_shape():
    messages = build_prompt().format_messages(posting="POSTING-MARKER", profile="PROFILE-MARKER")
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[-1], HumanMessage)
    assert "POSTING-MARKER" in messages[-1].content and "PROFILE-MARKER" in messages[-1].content


class FakeModel:
    """Fails `fail_times` times (like a small model returning invalid output), then answers."""

    def __init__(self, fail_times: int = 0) -> None:
        self.schema = None
        self.calls = 0
        self.fail_times = fail_times
        self.received = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema

        def respond(prompt_value):
            self.calls += 1
            self.received = prompt_value
            if self.calls <= self.fail_times:
                raise ValueError("model returned invalid output")
            return _answer()

        return RunnableLambda(respond)


def test_chain_binds_profile_and_returns_assessment():
    fake = FakeModel()
    result = build_chain(fake, "PROFILE-MARKER").invoke({"posting": "POSTING-MARKER"})
    assert fake.schema is FitAssessment, "use llm.with_structured_output(FitAssessment)"
    assert isinstance(result, FitAssessment)
    sent = "\n".join(m.content for m in fake.received.to_messages())
    assert "POSTING-MARKER" in sent and "PROFILE-MARKER" in sent


def test_chain_retries_failed_output():
    fake = FakeModel(fail_times=1)
    result = build_chain(fake, PROFILE).invoke({"posting": "x"})
    assert isinstance(result, FitAssessment), "wrap the structured model in .with_retry(...)"
    assert fake.calls == 2


@live
def test_live_ranks_matching_posting_above_mismatch():
    from autotailor.chains.score_fit import score_fit_batch
    from autotailor.config import load_settings
    from autotailor.paths import get_paths

    texts = [(FIXTURES / n).read_text() for n in ("sample_posting.txt", "posting_accounting.txt")]
    good, bad = score_fit_batch(texts, PROFILE, load_settings(get_paths().config), max_concurrency=1)
    for name, a in (("perception intern", good), ("accounting intern", bad)):
        assert isinstance(a, FitAssessment), f"{name}: {a!r}"
        print(f"\n{name}: fit {compute_fit_score(a)}\n{a.model_dump_json(indent=1)}")

    assert compute_fit_score(good) - compute_fit_score(bad) >= 0.25
    assert good.skills.score >= 4 and bad.skills.score <= 2
    assert good.level.score >= 4, "a student applying to an internship is a good level fit"
    ids = set(re.findall(r"\[([\w-]+)\]", PROFILE))
    assert good.relevant_bullet_ids and set(good.relevant_bullet_ids) <= ids
    assert "exp-lab-detector" in good.relevant_bullet_ids
    assert bad.missing, "the accounting posting requires things this candidate lacks"
