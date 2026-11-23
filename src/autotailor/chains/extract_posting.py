"""Read a job posting's basic facts (company, title, location, ...) from its text.

Used for postings added as pasted text, files, or pages that aren't a supported
job board. The prompt is "extract_posting" in structured_prompts.json.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from autotailor.chains.prompts import load_prompt
from autotailor.config import Settings
from autotailor.llm import get_chat_model

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable


class PostingFields(BaseModel):
    """Basic facts about a job posting."""

    company: str = Field(description="The hiring company's name, as the posting states it.")
    title: str = Field(description="The job title, exactly as written in the posting.")
    location: str | None = Field(
        default=None,
        description="Where the job is based, e.g. 'New York, NY'. Null if not stated.",
    )
    workplace: Literal["onsite", "hybrid", "remote"] | None = Field(
        default=None, description="Work arrangement, if the posting states it. Null otherwise."
    )
    employment_type: Literal["internship", "full_time", "part_time", "contract", "other"] | None = (
        Field(default=None, description="Type of employment, if stated. Null otherwise.")
    )


def build_prompt() -> ChatPromptTemplate:
    """The extraction prompt: a system message, then a human message with {text}."""
    return load_prompt("extract_posting", PostingFields)


def build_chain(llm: BaseChatModel) -> Runnable:
    """Prompt -> model with structured output; invoke with {"text": ...} to get PostingFields."""
    return build_prompt() | llm.with_structured_output(PostingFields)


def extract_posting_fields(text: str, settings: Settings) -> PostingFields:
    """Run the chain with the model configured for the `extract` step."""
    llm = get_chat_model(settings, "extract")
    return build_chain(llm).invoke({"text": text})
