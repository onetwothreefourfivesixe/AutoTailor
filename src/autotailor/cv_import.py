"""Turn an existing resume or CV (PDF, Word, text, Markdown, or AutoTailor YAML) into a master CV.

The text is read locally (pypdf, python-docx); a model then sorts it into the
master CV's sections. Bullets must be copied word for word, so the result is
checked against the source: any bullet that isn't found in it is reported for
the user to look at before saving. Nothing is saved here; the caller shows the
draft for review and saves it through the usual validated path.
"""

from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from autotailor.config import Settings

MIN_CHARS = 200


class ImportedBullet(BaseModel):
    text: str = Field(description="The bullet, copied word for word from the document.")


class ImportedEntry(BaseModel):
    title: str = Field(description="Role, project or award name, as written.")
    organization: str | None = Field(default=None, description="Company, school or group, as written. Null if none.")
    location: str | None = None
    start: str | None = Field(default=None, description="Start date as written, e.g. 'Aug. 2026'.")
    end: str | None = Field(default=None, description="End date as written, or 'present'.")
    bullets: list[ImportedBullet] = Field(default_factory=list)


class ImportedEducation(BaseModel):
    school: str
    degree: str = Field(description="e.g. 'B.A.', 'B.S. Computer Science', 'High School Diploma'.")
    field: str | None = Field(default=None, description="Major or field of study, if separate from the degree.")
    location: str | None = None
    start: str | None = None
    graduation: str | None = Field(default=None, description="Graduation date as written, e.g. 'Expected May 2029'.")
    gpa: str | None = None
    details: list[ImportedBullet] = Field(default_factory=list, description="Coursework, honors, etc., word for word.")


class ImportedSkillGroup(BaseModel):
    name: str = Field(description="The group's heading, e.g. 'Languages'; 'Skills' if the document has no groups.")
    items: list[str]


class ImportedContact(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    links: list[str] = Field(default_factory=list, description="LinkedIn, GitHub, website, as written.")


class ImportedCV(BaseModel):
    """A resume sorted into sections. Every bullet copied word for word; nothing added."""

    contact: ImportedContact
    education: list[ImportedEducation] = Field(default_factory=list)
    experience: list[ImportedEntry] = Field(default_factory=list, description="Jobs, internships, research positions.")
    projects: list[ImportedEntry] = Field(default_factory=list)
    activities: list[ImportedEntry] = Field(default_factory=list, description="Leadership, clubs, extracurriculars.")
    volunteer: list[ImportedEntry] = Field(default_factory=list)
    awards: list[ImportedEntry] = Field(default_factory=list)
    certifications: list[ImportedEntry] = Field(default_factory=list)
    skills: list[ImportedSkillGroup] = Field(default_factory=list)


# --- reading the file ------------------------------------------------------------------


def document_text(name: str, data: bytes) -> str:
    """Plain text of a resume file. Raises ValueError for unreadable or unsupported files."""
    low = name.lower()
    if low.endswith(".pdf"):
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
        if len(text.strip()) < MIN_CHARS:
            raise ValueError("This PDF has almost no text in it (a scan or an image?). Try a Word or text version.")
        return text
    if low.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(data))
        parts = [p.text for p in doc.paragraphs]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts)
    if low.endswith((".txt", ".md")):
        return data.decode("utf-8", "replace")
    raise ValueError("Use a .pdf, .docx, .txt, .md, or an AutoTailor .yaml file.")


# --- checking the result -------------------------------------------------------------------


def _norm(s: str) -> str:
    s = s.replace("’", "'").replace("“", '"').replace("”", '"').replace("–", "-").replace("—", "-")
    return re.sub(r"[\s•●▪◦·]+", " ", s).strip().lower()


def unverified_bullets(cv: dict[str, Any], source: str) -> list[str]:
    """IDs of bullets whose text isn't in the source document (ignoring spacing, case and
    bullet symbols). These may have been reworded by the model and need a look."""
    text = _norm(source)
    out = []
    for section in ("education", "experience", "projects", "activities", "volunteer", "awards", "certifications"):
        for e in cv.get(section) or []:
            for b in (e.get("details") if section == "education" else e.get("bullets")) or []:
                if _norm(b["text"])[:-1] not in text:  # allow a trailing period difference
                    out.append(b["id"])
    return out


# --- a profile from the CV ------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _graduation(text: str | None) -> date | None:
    """'Expected May 2029' -> 2029-05-31; '2028' -> 2028-05-31."""
    if not text:
        return None
    year = re.search(r"(19|20)\d\d", text)
    if not year:
        return None
    month = next((n for m, n in _MONTHS.items() if re.search(rf"\b{m}", text.lower())), 5)
    import calendar

    y = int(year.group())
    return date(y, month, calendar.monthrange(y, month)[1])


_DATE = re.compile(
    r"(?:expected|anticipated|graduat\w*)?[\s:]*(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+)?(?:19|20)\d\d"
    r"(?!\s*[-–—]\s*(?:19|20)\d\d)", re.I)  # fmt: skip


def _date_near(text: str, school: str, window: int = 250) -> str | None:
    """A graduation date written just after the school's name, for when the model leaves it out."""
    i = text.lower().find(school.lower().strip()) if school.strip() else -1
    if i < 0:
        return None
    m = _DATE.search(text[i : i + len(school) + window])
    return " ".join(m.group().split()) if m else None


def _level(degree: str) -> str | None:
    d = degree.lower()
    if "high school" in d or "diploma" in d and "bachelor" not in d:
        return None
    for level, marks in (("phd", ("ph.d", "phd", "doctor")), ("mba", ("mba",)), ("master", ("master", "m.s", "m.a", "m.eng", "ms ")),
                         ("associate", ("associate", "a.a", "a.s")), ("bachelor", ("bachelor", "b.a", "b.s", "b.eng", "ba ", "bs ", "bsc"))):  # fmt: skip
        if any(m in d + " " for m in marks):
            return level
    return "other"


_UNIT = re.compile(r"^\s*(college|school|faculty|department|division|institute)\s+of\b", re.I)


def _field(e: dict[str, Any]) -> str:
    """The major: the model's `field` unless it's a college or department name; otherwise
    whatever follows 'in' or the abbreviation in the degree ('B.S. in Data Science')."""
    field = (e.get("field") or "").strip()
    if field and not _UNIT.match(field):
        return field
    degree = (e.get("degree") or "").strip()
    m = re.search(r"\bin\s+(.+)$", degree) or re.search(r"^(?:[A-Z][A-Za-z]{0,4}\.){1,3}\s*(.+)$", degree)
    return m.group(1).strip(" ,") if m else ""


def profile_from_cv(cv: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Suggested `profile.education` entries from the CV's education (degrees only)."""
    today = today or date.today()
    out = []
    for e in cv.get("education") or []:
        level = _level(e.get("degree") or "")
        if level is None:
            continue
        grad = _graduation(e.get("graduation"))
        out.append({
            "level": level,
            "field": _field(e),
            "status": "in_progress" if (grad and grad > today) or "expected" in (e.get("graduation") or "").lower() else "completed",
            "graduation_date": grad.isoformat() if grad else None,
            "school": e.get("school"),
        })  # fmt: skip
    return out


# --- the model step ----------------------------------------------------------------------------


def parse_cv(text: str, settings: Settings, llm: Any = None) -> dict[str, Any]:
    """The document sorted into a master-CV dict with generated IDs (not yet validated or saved)."""
    from autotailor.chains.prompts import load_prompt
    from autotailor.ui.logic import fill_missing_ids

    if llm is None:
        from autotailor.llm import get_chat_model

        llm = get_chat_model(settings, "extract")
    chain = load_prompt("import_cv", ImportedCV) | llm.with_structured_output(ImportedCV).with_retry(stop_after_attempt=2)
    imported: ImportedCV = chain.invoke({"document": text[:30000]})
    data = imported.model_dump(mode="json")
    for section in ("experience", "projects", "activities", "volunteer", "awards", "certifications"):
        for e in data[section]:
            e["id"] = None
            e["tags"] = []
            # The imported resume is the user's usual one: its bullets start as the base.
            e["bullets"] = [{"id": None, "text": b["text"].strip(), "tags": [], "base": True} for b in e["bullets"] if b["text"].strip()]
    for e in data["education"]:
        e["graduation"] = e.get("graduation") or _date_near(text, e.get("school") or "")
        e["id"] = None
        e["details"] = [{"id": None, "text": b["text"].strip(), "tags": [], "base": True} for b in e["details"] if b["text"].strip()]
    data["skills"] = [g for g in data["skills"] if g["items"]]
    return fill_missing_ids(data)
