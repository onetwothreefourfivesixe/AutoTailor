"""Turn a tailored resume plus the master CV into a render-ready layout.

Both renderers (PDF and Word) read this one structure, so they always agree on
what appears and in which order:
- Contact and education always appear. Education shows its chosen detail
  bullets and GPA.
- Experience, projects, activities, volunteer work and awards appear only for
  entries with at least one chosen bullet. Entries keep the CV's order; bullets
  within an entry keep the tailored order (most relevant first).
- Certifications always appear.
- Skills: the tailored list, grouped under the CV's skill-group names. With no
  tailored list, every CV skill.
"""

from __future__ import annotations

from typing import Any

from autotailor.cv import Entry, MasterCV
from autotailor.models import TailoredResume

SECTIONS = [
    ("experience", "Work Experience"),
    ("projects", "Projects"),
    ("activities", "Leadership & Activities"),
    ("volunteer", "Volunteer Experience"),
    ("awards", "Awards"),
]


def _dates(start: str | None, end: str | None) -> str:
    if start and end:
        return f"{start} – {end.capitalize() if end.lower() == 'present' else end}"
    return start or end or ""


def _item(e: Entry, bullets: list[str]) -> dict[str, Any]:
    if e.organization:
        return {"left1": e.organization, "right1": e.location or "", "left2": e.title,
                "right2": _dates(e.start, e.end), "bullets": bullets}  # fmt: skip
    return {"left1": e.title, "right1": _dates(e.start, e.end), "left2": "", "right2": e.location or "",
            "bullets": bullets}  # fmt: skip


def build_layout(r: TailoredResume, cv: MasterCV) -> dict[str, Any]:
    chosen: dict[str, str] = {}
    for b in r.bullets:  # verified resumes only, so every ID exists
        chosen.setdefault(b.source_id.strip().strip("[]"), b.text.strip())
    order = {sid: i for i, sid in enumerate(chosen)}

    c = cv.contact
    contact_line = " | ".join(x for x in [c.phone, c.email, c.location, *c.links] if x)

    education = []
    for ed in cv.education:
        details = sorted((b.id for b in ed.details if b.id in chosen), key=order.get)
        lines = [chosen[i] for i in details]
        if ed.gpa:
            lines.append(f"GPA: {ed.gpa}")
        education.append({
            "left1": ed.school, "right1": ed.location or "",
            "left2": f"{ed.degree} {ed.field}" if ed.field else ed.degree,
            "right2": _dates(ed.start, ed.graduation) if ed.start else (ed.graduation or ""),
            "bullets": lines,
        })  # fmt: skip

    sections = [{"title": "Education", "items": education}]
    for attr, title in SECTIONS:
        items = []
        for e in getattr(cv, attr):
            ids = sorted((b.id for b in e.bullets if b.id in chosen), key=order.get)
            if ids:
                items.append(_item(e, [chosen[i] for i in ids]))
        if items:
            sections.append({"title": title, "items": items})
    if cv.certifications:
        sections.append({
            "title": "Certifications",
            "items": [
                {"left1": e.title, "right1": _dates(e.start, e.end),
                 "left2": e.organization or "", "right2": "", "bullets": [b.text for b in e.bullets]}
                for e in cv.certifications
            ],
        })  # fmt: skip

    wanted = [s.lower() for s in r.skills]
    skills = []
    for g in cv.skills:
        items = g.items if not wanted else sorted(
            (i for i in g.items if i.lower() in wanted), key=lambda i: wanted.index(i.lower())
        )
        if items:
            skills.append({"name": g.name, "items": ", ".join(items)})

    return {"name": c.name, "contact": contact_line, "contact_items": contact_items(c), "sections": sections,
            "skills": skills}  # fmt: skip


def contact_items(c) -> list[dict[str, str | None]]:  # noqa: ANN001
    """Contact details with a link where one makes sense (for templates that link them)."""
    out: list[dict[str, str | None]] = []
    if c.phone:
        out.append({"text": c.phone, "url": None})
    if c.email:
        out.append({"text": c.email, "url": f"mailto:{c.email}"})
    if c.location:
        out.append({"text": c.location, "url": None})
    for link in c.links:
        url = link if "://" in link else f"https://{link}"
        out.append({"text": link.removeprefix("https://").removeprefix("http://"), "url": url})
    return out
