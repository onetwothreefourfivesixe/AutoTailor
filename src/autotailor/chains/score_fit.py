"""Grade how well the candidate fits a posting, on four criteria from 1 to 5.

Skills, experience, level and interests, each with a reason, plus what's
missing and the CV bullets most relevant to the posting (FitAssessment). The
weighted fit score is computed in matching.py, not by the model. The profile
is bound once with `.partial` and reused for every posting; invalid output is
retried. Prompt: "score_fit" in structured_prompts.json.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.prompts import ChatPromptTemplate

from autotailor.chains.prompts import load_prompt
from autotailor.config import Settings
from autotailor.llm import get_chat_model, structured
from autotailor.models import FitAssessment

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable


def build_prompt() -> ChatPromptTemplate:
    """The fit-scoring prompt (variables `posting` and `profile`)."""
    return load_prompt("score_fit", FitAssessment)


def build_chain(llm: BaseChatModel, profile: str) -> Runnable:
    """Prompt with the profile bound -> model with structured output, retried up to 3 times."""
    prompt = build_prompt().partial(profile=profile)
    return prompt | structured(llm, FitAssessment).with_retry(stop_after_attempt=3)


def score_fit_batch(
    postings: list[str], profile: str, settings: Settings, max_concurrency: int = 4, ids: list[str] | None = None
) -> list[FitAssessment | Exception]:
    """Score many postings against one profile, concurrently.

    Results line up with `postings`; a failure comes back as its Exception.
    """
    llm = get_chat_model(settings, "score")
    chain = build_chain(llm, profile)
    n = len(postings)  # `ids` (posting IDs) label each call in the AI log
    return chain.batch(
        [{"posting": p} for p in postings],
        config=[{"max_concurrency": max_concurrency, "metadata": {"item": i}} for i in (ids or [None] * n)],
        return_exceptions=True,
    )
