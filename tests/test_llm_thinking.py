"""The thinking check that picks a structured-output method each model supports."""

from __future__ import annotations

import pytest
from langchain_anthropic import ChatAnthropic

from autotailor.llm import structured, supports_thinking
from autotailor.models import Requirements


def claude(model: str, **kw) -> ChatAnthropic:
    return ChatAnthropic(model=model, api_key="test-key", max_tokens=1024, **kw)


@pytest.mark.parametrize(
    "model",
    ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-2", "claude-opus-5"],
)
def test_models_that_think_by_default(model):
    assert supports_thinking(claude(model))


@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5"])
def test_older_models_think_only_when_asked(model):
    assert not supports_thinking(claude(model))
    assert supports_thinking(claude(model, thinking={"type": "adaptive"}))


def test_thinking_switched_off_explicitly():
    assert not supports_thinking(claude("claude-sonnet-5-5", thinking={"type": "between_tools"}))


def test_non_anthropic_models_report_no_thinking():
    class FakeOpenAI:
        model_name = "gpt-x"

    assert not supports_thinking(FakeOpenAI())


class Recorder:
    """Records the arguments `structured` passes to with_structured_output."""

    def __init__(self, model: str, thinking: dict | None = None):
        self.model, self.thinking, self.kwargs = model, thinking, None

    def with_structured_output(self, schema, **kwargs):
        self.kwargs = kwargs
        return self


class ChatAnthropic_(Recorder):
    pass


ChatAnthropic_.__name__ = "ChatAnthropic"  # looks like the real class to supports_thinking


def test_thinking_model_uses_native_json_schema():
    rec = ChatAnthropic_("claude-sonnet-5-5")
    structured(rec, Requirements)
    assert rec.kwargs == {"method": "json_schema"}


def test_non_thinking_model_keeps_provider_default():
    rec = Recorder("gpt-x")
    structured(rec, Requirements)
    assert rec.kwargs == {}


def test_real_thinking_model_builds_without_forced_tool_warning(recwarn):
    structured(claude("claude-sonnet-5-5"), Requirements)
    assert not [w for w in recwarn if "forced tool calling" in str(w.message)]
