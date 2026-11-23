"""Master CV: the single source of truth for everything a resume may say.

Every bullet has a stable ID. A tailored resume refers to bullets by ID, which
is how the verifier later proves that nothing was invented.

Two optional markers shape tailoring:
- `base: true` on a bullet puts it on your usual resume. Tailoring starts from
  those bullets and changes them for each posting (swapping projects, giving the
  most relevant jobs more bullets) instead of building from nothing.
- `hidden: true` on an entry keeps it in your CV (for fit scoring and short
  answers) but never puts it on a resume, e.g. high school once you're in college.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Bullet(_Model):
    id: str
    text: str
    tags: list[str] = Field(default_factory=list)
    base: bool = Field(default=False, description="On your usual resume: tailoring starts from these bullets.")


class Entry(_Model):
    """One experience, project, activity, volunteer role, award, or certification."""

    id: str
    title: str
    organization: str | None = None
    location: str | None = None
    start: str | None = Field(default=None, description="YYYY-MM, or free text such as 'Summer 2026'.")
    end: str | None = Field(default=None, description="YYYY-MM, 'present', or free text.")
    tags: list[str] = Field(default_factory=list)
    stack: list[str] = Field(
        default_factory=list, description="Tools and technologies used, shown after the title (e.g. Python, PyTorch)."
    )
    hidden: bool = Field(default=False, description="Keep in the CV but never put on a resume.")
    bullets: list[Bullet] = Field(default_factory=list)


class EducationEntry(_Model):
    id: str
    school: str
    degree: str
    field: str | None = None
    location: str | None = None
    start: str | None = None
    graduation: str | None = None
    gpa: str | None = None
    hidden: bool = Field(default=False, description="Keep in the CV but never put on a resume.")
    details: list[Bullet] = Field(default_factory=list)


class SkillGroup(_Model):
    name: str
    items: list[str]


class Contact(_Model):
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list)


class MasterCV(_Model):
    contact: Contact
    education: list[EducationEntry] = Field(default_factory=list)
    experience: list[Entry] = Field(default_factory=list)
    projects: list[Entry] = Field(default_factory=list)
    activities: list[Entry] = Field(default_factory=list)
    volunteer: list[Entry] = Field(default_factory=list)
    awards: list[Entry] = Field(default_factory=list)
    certifications: list[Entry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)

    def entries(self) -> list[Entry]:
        return [
            *self.experience,
            *self.projects,
            *self.activities,
            *self.volunteer,
            *self.awards,
            *self.certifications,
        ]

    def bullets(self) -> dict[str, Bullet]:
        """Every bullet in the CV, keyed by ID."""
        out: dict[str, Bullet] = {}
        for e in self.entries():
            out.update({b.id: b for b in e.bullets})
        for ed in self.education:
            out.update({b.id: b for b in ed.details})
        return out

    def hidden_bullets(self) -> set[str]:
        """IDs of bullets in hidden entries: never put on a resume."""
        out = {b.id for e in self.entries() if e.hidden for b in e.bullets}
        return out | {b.id for ed in self.education if ed.hidden for b in ed.details}

    def base_bullets(self) -> list[str]:
        """IDs of the usual resume's bullets (`base: true`), in CV order. Empty if none are marked."""
        hidden = self.hidden_bullets()
        edu = [b for ed in self.education for b in ed.details]  # education first, as on the page
        rest = [b for e in self.entries() for b in e.bullets]
        return [b.id for b in [*edu, *rest] if b.base and b.id not in hidden]

    def entry_of(self) -> dict[str, Entry | EducationEntry]:
        """Bullet ID -> the entry it belongs to."""
        out: dict[str, Entry | EducationEntry] = {b.id: e for e in self.entries() for b in e.bullets}
        out.update({b.id: ed for ed in self.education for b in ed.details})
        return out

    @model_validator(mode="after")
    def _ids_valid_and_unique(self) -> MasterCV:
        ids: list[str] = []
        for e in self.entries():
            ids.append(e.id)
            ids.extend(b.id for b in e.bullets)
        for ed in self.education:
            ids.append(ed.id)
            ids.extend(b.id for b in ed.details)

        bad = sorted({i for i in ids if not _ID.match(i)})
        if bad:
            raise ValueError(
                f"Invalid IDs (use lowercase letters, digits, '-' and '_'): {', '.join(bad)}"
            )
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"Duplicate IDs: {', '.join(dupes)}")
        return self


def load_master_cv(path: Path) -> MasterCV:
    with path.open(encoding="utf-8") as f:
        return MasterCV.model_validate(yaml.safe_load(f))
