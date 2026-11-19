"""Tests for the M2 exercise in src/autotailor/chains/extract_requirements.py.

As in M1: "xfail" until implemented, then normal tests (shown as "xpass").
Delete the `pytestmark` line once everything passes.
"""

import os
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

from autotailor.chains.extract_requirements import build_chain, build_prompt
from autotailor.config import DegreeLevel
from autotailor.eligibility import quote_found
from autotailor.models import Requirements

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="M2 exercise not done yet")

FIXTURES = Path(__file__).parent / "fixtures"
TODAY = date(2026, 11, 17)
live = pytest.mark.skipif(not os.environ.get("AUTOTAILOR_LIVE"), reason="set AUTOTAILOR_LIVE=1")


def test_prompt_takes_text_and_today():
    assert sorted(build_prompt().input_variables) == ["text", "today"]


def test_prompt_shape():
    messages = build_prompt().format_messages(text="UNIQUE-POSTING-MARKER", today="2031-02-03")
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[-1], HumanMessage)
    assert "UNIQUE-POSTING-MARKER" in messages[-1].content
    assert any("2031-02-03" in m.content for m in messages), "today's date must reach the model"


class FakeModel:
    def __init__(self) -> None:
        self.schema = None
        self.received = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema

        def respond(prompt_value):
            self.received = prompt_value
            return Requirements(degree_levels=[DegreeLevel.BACHELOR])

        return RunnableLambda(respond)


def test_chain_wiring():
    fake = FakeModel()
    result = build_chain(fake).invoke({"text": "UNIQUE-POSTING-MARKER", "today": "2031-02-03"})
    assert fake.schema is Requirements, "use llm.with_structured_output(Requirements)"
    assert isinstance(result, Requirements)
    assert "UNIQUE-POSTING-MARKER" in fake.received.to_messages()[-1].content


def _live_extract(name: str) -> tuple[Requirements, str]:
    from autotailor.chains.extract_requirements import extract_requirements
    from autotailor.config import load_settings
    from autotailor.paths import get_paths

    text = (FIXTURES / name).read_text()
    req = extract_requirements(text, load_settings(get_paths().config), today=TODAY)
    print(f"\n{name}: {req.model_dump_json(indent=1)}")
    missing = [e.quote for e in req.evidence if not quote_found(e.quote, text)]
    assert not missing, f"quotes not found in the posting: {missing}"
    return req, text


@live
def test_live_explicit_window():
    req, _ = _live_extract("sample_posting.txt")
    assert set(req.degree_levels) == {DegreeLevel.BACHELOR, DegreeLevel.MASTER}
    assert req.enrollment_required is True
    assert req.graduation_earliest and (req.graduation_earliest.year, req.graduation_earliest.month) == (2027, 12)
    assert req.graduation_latest and (req.graduation_latest.year, req.graduation_latest.month) == (2029, 6)
    assert {e.field for e in req.evidence} >= {"degree_levels", "graduation_window"}


@live
def test_live_rising_seniors():
    req, _ = _live_extract("posting_rising_seniors.txt")
    assert req.degree_levels == [DegreeLevel.BACHELOR]
    assert req.graduation_latest and req.graduation_latest.year == 2028 and req.graduation_latest.month <= 6
    assert req.work_authorization
    assert "time-series" not in " ".join(req.majors).lower()  # "a plus" is not a requirement


@live
def test_live_nothing_stated():
    req, _ = _live_extract("posting_no_requirements.txt")
    assert req.is_empty(), "nothing in this posting is an education requirement"
