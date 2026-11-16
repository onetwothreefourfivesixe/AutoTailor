"""Domain models shared by every stage of the pipeline.

These are the shapes the LangChain steps will produce (structured output) and
consume, so their field descriptions double as instructions to the model.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

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


class Requirements(BaseModel):
    """What a posting requires of applicants. Every claim needs quoted evidence."""

    degree_levels: list[DegreeLevel] = Field(
        default_factory=list,
        description="Degree levels the posting accepts. Empty if not stated.",
    )
    undergraduates_only: bool = Field(
        default=False, description="True only if the posting explicitly excludes graduate students."
    )
    enrollment_required: bool | None = Field(
        default=None,
        description="True if the applicant must be a current student; None if not stated.",
    )
    graduation_earliest: date | None = Field(
        default=None, description="Earliest acceptable graduation date, if stated."
    )
    graduation_latest: date | None = Field(
        default=None, description="Latest acceptable graduation date, if stated."
    )
    majors: list[str] = Field(default_factory=list)
    work_authorization: str | None = None
    evidence: dict[str, str] = Field(
        default_factory=dict,
        description="Field name -> exact quote from the posting supporting that field.",
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
