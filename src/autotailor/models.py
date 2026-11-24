"""Domain models shared by every stage of the pipeline.

These are the shapes the LangChain steps will produce (structured output) and
consume, so their field descriptions double as instructions to the model.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from autotailor.config import DegreeLevel


def _bare_id(raw: str) -> str:
    """Models often copy a CV bullet ID with its brackets ("[exp-1]"); keep just the ID."""
    return raw.strip().strip("[]").strip()


class Source(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"
    PASTE = "paste"
    RUNWAY = "runway"
    USAJOBS = "usajobs"


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


class CriterionScore(BaseModel):
    score: int = Field(ge=1, le=5, description="1 (poor) to 5 (excellent), per the rubric.")
    reason: str = Field(description="One or two sentences citing the posting and the candidate's experience.")


class FitAssessment(BaseModel):
    """How well the candidate fits one posting. Produced by the M3 fit-scoring chain."""

    skills: CriterionScore = Field(
        description="Does the candidate have the skills and tools the posting requires?"
    )
    experience: CriterionScore = Field(
        description="How relevant is the candidate's past work and projects to this role's actual work?"
    )
    level: CriterionScore = Field(
        description="Does the role's expected experience level suit the candidate (e.g. an internship "
        "for a student, not '5+ years')?"
    )
    interests: CriterionScore = Field(
        description="How closely does the role match the candidate's stated role interests?"
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Required qualifications the candidate clearly lacks. Empty if none.",
    )
    relevant_bullet_ids: list[str] = Field(
        default_factory=list,
        description="IDs (in [brackets] in the profile) of the 3-8 CV bullets most relevant to this "
        "posting, best first. Only IDs that appear in the profile.",
    )

    @field_validator("relevant_bullet_ids")
    @classmethod
    def _bare_ids(cls, v: list[str]) -> list[str]:
        return [_bare_id(x) for x in v]


class TailoredBullet(BaseModel):
    source_id: str = Field(description="ID of the master-CV bullet this is based on, copied exactly.")
    text: str = Field(
        description="The bullet, lightly rephrased toward the posting's wording. Same facts, same "
        "numbers, nothing added that the source bullet does not say."
    )

    @field_validator("source_id")
    @classmethod
    def _bare_source_id(cls, v: str) -> str:
        return _bare_id(v)


class TailoredResume(BaseModel):
    """A one-page resume tailored to one posting, built only from master-CV bullets."""

    bullets: list[TailoredBullet] = Field(
        description="The CV bullets to include, most relevant first. Entries (jobs, projects) "
        "appear on the resume only if at least one of their bullets is chosen."
    )
    skills: list[str] = Field(
        default_factory=list,
        description="Skills from the CV's skills section most relevant to the posting, most "
        "relevant first. Only skills that appear in the CV.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="The posting's 10-20 most important keywords (hard skills, tools, methods, "
        "qualifications), copied with the posting's exact wording, most important first. Used to "
        "measure how well the resume covers the posting; list them whether or not the CV has them.",
    )


class AnswerPoint(BaseModel):
    source_id: str = Field(description="ID of the CV bullet this point draws on, copied exactly without brackets.")
    point: str = Field(
        description="What to say, in one sentence: the experience and what it shows, using only facts "
        "the bullet states."
    )
    why: str = Field(description="Why it answers this question, in a few words.")


class AnswerPoints(BaseModel):
    """Experiences from the CV worth mentioning in a short answer. Produced by the suggest step (M8)."""

    points: list[AnswerPoint] = Field(
        description="2-5 points, most useful first. Each from a different CV bullet."
    )


class Match(BaseModel):
    posting_id: str
    verdict: Verdict
    verdict_reasons: list[str] = Field(default_factory=list)
    prescore: float | None = Field(
        default=None, description="Embedding similarity to the CV (cheap pre-rank), roughly 0-1."
    )
    fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    fit_reasons: list[str] = Field(default_factory=list)
    fit: FitAssessment | None = None
    original_verdict: Verdict | None = Field(
        default=None, description="Set when the user overrode the verdict: what the rules and model decided."
    )


class ArtifactKind(StrEnum):
    RESUME_PDF = "resume_pdf"
    RESUME_DOCX = "resume_docx"
    RESUME_DATA = "resume_data"  # the tailored selection, before rendering
    ANSWER_POINTS = "answer_points"
    ANSWER_DRAFT = "answer_draft"
