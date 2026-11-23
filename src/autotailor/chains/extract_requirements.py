"""Read a posting's education and graduation requirements, with quoted evidence.

Each stated requirement comes with a quote from the posting; eligibility.py
trusts a requirement only if its quotes are found in the text. Today's date is
passed in so phrases like "graduating next year" resolve to dates. Prompt and
few-shot examples: "extract_requirements" in structured_prompts.json.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate

from autotailor.chains.prompts import load_prompt
from autotailor.config import Settings
from autotailor.llm import get_chat_model, structured
from autotailor.models import Requirements

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable


def build_prompt() -> ChatPromptTemplate:
    """The requirements prompt (variables `text` and `today`)."""
    return load_prompt("extract_requirements", Requirements)


def build_chain(llm: BaseChatModel) -> Runnable:
    """Prompt -> model with structured output; returns Requirements."""
    return build_prompt() | structured(llm, Requirements)


def _inputs(text: str, today: date | None) -> dict[str, str]:
    return {"text": text, "today": (today or date.today()).isoformat()}


def extract_requirements(text: str, settings: Settings, today: date | None = None) -> Requirements:
    """Extract one posting's requirements with the `extract` step's model."""
    llm = get_chat_model(settings, "extract")
    return build_chain(llm).invoke(_inputs(text, today))


def extract_requirements_batch(
    texts: list[str], settings: Settings, today: date | None = None, max_concurrency: int = 4,
    ids: list[str] | None = None,
) -> list[Requirements | Exception]:
    """Extract many postings concurrently.

    Results line up with `texts`. A posting that fails comes back as its
    Exception instead of stopping the whole batch (`return_exceptions=True`).
    """
    llm = get_chat_model(settings, "extract")
    chain = build_chain(llm)
    n = len(texts)  # `ids` (posting IDs) label each call in the AI log
    return chain.batch(
        [_inputs(t, today) for t in texts],
        config=[{"max_concurrency": max_concurrency, "metadata": {"item": i}} for i in (ids or [None] * n)],
        return_exceptions=True,
    )
