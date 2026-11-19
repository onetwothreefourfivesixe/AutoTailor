"""Tests for the M1 exercise in src/autotailor/chains/extract_posting.py.

While build_prompt() and build_chain() raise NotImplementedError these show as
"xfail" (expected failure). Once implemented they run as normal tests: a wrong
implementation fails, a right one shows as "xpass". When everything passes,
delete the `pytestmark` line below so they count as ordinary tests.
"""

import os
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda

from autotailor.chains.extract_posting import PostingFields, build_chain, build_prompt

# pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="M1 exercise not done yet")

SAMPLE = (Path(__file__).parent / "fixtures" / "sample_posting.txt").read_text()


def test_prompt_takes_only_text():
    assert build_prompt().input_variables == ["text"]


def test_prompt_is_system_then_human_with_the_posting():
    messages = build_prompt().format_messages(text="UNIQUE-POSTING-MARKER")
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[-1], HumanMessage)
    assert "UNIQUE-POSTING-MARKER" in messages[-1].content
    assert "UNIQUE-POSTING-MARKER" not in messages[0].content


class FakeModel:
    """Stands in for a chat model: records what the chain sends, returns a fixed answer."""

    def __init__(self) -> None:
        self.schema = None
        self.received = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema

        def respond(prompt_value):
            self.received = prompt_value
            return PostingFields(company="Northwind Robotics", title="SWE Intern")

        return RunnableLambda(respond)


def test_chain_formats_prompt_and_returns_posting_fields():
    fake = FakeModel()
    result = build_chain(fake).invoke({"text": "UNIQUE-POSTING-MARKER"})

    assert fake.schema is PostingFields, "use llm.with_structured_output(PostingFields)"
    assert isinstance(result, PostingFields)
    sent = fake.received.to_messages()  # the prompt must run before the model
    assert "UNIQUE-POSTING-MARKER" in sent[-1].content


@pytest.mark.skipif(not os.environ.get("AUTOTAILOR_LIVE"), reason="set AUTOTAILOR_LIVE=1")
def test_live_model_reads_sample_posting():
    """Calls the real model configured for the `extract` step (may cost money)."""
    from autotailor.chains.extract_posting import extract_posting_fields
    from autotailor.config import load_settings
    from autotailor.paths import get_paths

    fields = extract_posting_fields(SAMPLE, load_settings(get_paths().config))
    print(f"\n{fields!r}")
    assert "northwind" in fields.company.lower()
    assert "intern" in fields.title.lower()
    assert fields.location and "pittsburgh" in fields.location.lower()
    assert fields.employment_type == "internship"
    assert fields.workplace == "hybrid"
