"""Domain models shared by every stage of the pipeline.

These are the shapes the LangChain steps will produce (structured output) and
consume, so their field descriptions double as instructions to the model.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from autotailor.config import DegreeLevel


class Source(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    PASTE = "paste"
    RUNWAY = "runway"


class PostingStatus(StrEnum):
    UNRESOLVED = "unresolved"  # known title/company only; no description yet
    NEEDS_LINK = "needs_link"  # could not find the employer posting; user must supply it
    RESOLVED = "resolved"  # full description available


RequirementField = Literal[
    "degree_levels",
    "undergraduates_only",
    "enrollment_required",
    "graduation_window",
    "majors",
    "work_authorization",
]


class Evidence(BaseModel):
    field: RequirementField = Field(description="Which requirement this quote supports.")
    quote: str = Field(
        description="Exact words copied from the posting, short (one sentence or less)."
    )


class Requirements(BaseModel):
    """What a posting requires of applicants, as the posting states it.

    Every stated requirement needs an Evidence quote. Anything the posting does
    not state stays empty/null: no guessing from the company or the role.
    """

    degree_levels: list[DegreeLevel] = Field(
        default_factory=list,
        description="Degree levels the posting accepts (being pursued or completed). "
        "Empty if not stated.",
    )
    undergraduates_only: bool = Field(
        default=False,
        description="True only if the posting explicitly limits applicants to undergraduates.",
    )
    enrollment_required: bool | None = Field(
        default=None,
        description="True if applicants must be currently enrolled students; null if not stated.",
    )
    graduation_earliest: date | None = Field(
        default=None,
        description="Earliest acceptable graduation date, if a graduation timeframe is stated.",
    )
    graduation_latest: date | None = Field(
        default=None,
        description="Latest acceptable graduation date, if a graduation timeframe is stated.",
    )
    majors: list[str] = Field(
        default_factory=list, description="Fields of study the posting asks for. Empty if not stated."
    )
    work_authorization: str | None = Field(
        default=None,
        description="Work authorization or sponsorship requirement, briefly. Null if not stated.",
    )
    evidence: list[Evidence] = Field(
        default_factory=list,
        description="One quote from the posting for each requirement stated above.",
    )

    def quotes_for(self, field: RequirementField) -> list[str]:
        return [e.quote for e in self.evidence if e.field == field]

    def is_empty(self) -> bool:
        return not (
            self.degree_levels
            or self.undergraduates_only
            or self.enrollment_required is not None
            or self.graduation_earliest
            or self.graduation_latest
            or self.majors
            or self.work_authorization
        )


class JobPosting(BaseModel):
    id: str = Field(description="Stable local ID: '<source>:<source_ref>'.")
    source: Source
    source_ref: str = Field(description="The source's own ID for the role.")
    source_url: str | None = Field(
        default=None, description="Link at the source, shown to the user. Never fetched for Runway."
    )
    canonical_url: str | None = Field(default=None, description="The employer's own posting URL.")
    company: str
    title: str
    location: str | None = None
    posted_at: datetime | None = None
    description: str | None = None
    external_score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Match score reported by the source, if any."
    )
    status: PostingStatus = PostingStatus.UNRESOLVED
    requirements: Requirements | None = None


class Verdict(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNCLEAR = "unclear"


class Match(BaseModel):
    posting_id: str
    verdict: Verdict
    verdict_reasons: list[str] = Field(default_factory=list)
    fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    fit_reasons: list[str] = Field(default_factory=list)


class ArtifactKind(StrEnum):
    RESUME_PDF = "resume_pdf"
    RESUME_DOCX = "resume_docx"
    RESUME_DATA = "resume_data"  # the tailored selection, before rendering
    ANSWER_POINTS = "answer_points"
    ANSWER_DRAFT = "answer_draft"
