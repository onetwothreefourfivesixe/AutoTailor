"""Turn a tailored resume plus the master CV into a render-ready layout.

Both renderers (PDF and Word) read this one structure, so they always agree on
what appears and in which order:
- Entries marked `hidden` never appear.
- Contact and education always appear. Education shows its chosen detail
  bullets and GPA.
- Experience, projects, activities, volunteer work and awards appear only for
  entries with at least one chosen bullet. With entry_order "relevance", the
  entries in a section are ordered by their first chosen bullet (the tailored
  list is most relevant first); with "cv", they keep the CV's order. Bullets
  within an entry keep the tailored order.
- An entry's `stack` follows its title, the items the posting values first
  (those in the tailored skills or keywords).
- Certifications always appear.
- Skills, under the CV's group names: with all_skills, every CV skill, the
  tailored picks first within each group; otherwise only the tailored picks.
  With no tailored list, every CV skill.

Each section has `key` (experience, projects, ...) and each item, besides the four
corner fields, has title, organization, location, dates, stack and stack_on (the
corner field the title is in), so a template can lay out a section its own way.
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


def _stack(e: Entry, wanted: list[str]) -> str:
    """The entry's stack, items the posting values first (otherwise in the CV's order)."""
    rank = {w: i for i, w in enumerate(wanted)}
    return ", ".join(sorted(e.stack, key=lambda s: rank.get(s.lower(), len(rank))))


def _item(e: Entry, bullets: list[str], wanted: list[str] = ()) -> dict[str, Any]:
    extra = {"title": e.title, "organization": e.organization or "", "location": e.location or "",
             "dates": _dates(e.start, e.end), "stack": _stack(e, list(wanted))}  # fmt: skip
    if e.organization:
        return {"left1": e.organization, "right1": e.location or "", "left2": e.title,
                "right2": _dates(e.start, e.end), "bullets": bullets, "stack_on": "left2", **extra}  # fmt: skip
    return {"left1": e.title, "right1": _dates(e.start, e.end), "left2": "", "right2": e.location or "",
            "bullets": bullets, "stack_on": "left1", **extra}  # fmt: skip


def build_layout(r: TailoredResume, cv: MasterCV, entry_order: str = "cv", all_skills: bool = False) -> dict[str, Any]:
    chosen: dict[str, str] = {}
    for b in r.bullets:  # verified resumes only, so every ID exists
        chosen.setdefault(b.source_id.strip().strip("[]"), b.text.strip())
    order = {sid: i for i, sid in enumerate(chosen)}

    c = cv.contact
    contact_line = " | ".join(x for x in [c.phone, c.email, c.location, *c.links] if x)

    wanted = [s.lower() for s in r.skills]
    hints = list(dict.fromkeys([*wanted, *(k.lower() for k in r.keywords)]))
    education = []
    for ed in cv.education:
        if ed.hidden:
            continue
        details = sorted((b.id for b in ed.details if b.id in chosen), key=order.get)
        lines = [chosen[i] for i in details]
        if ed.gpa:
            lines.append(f"GPA: {ed.gpa}")
        education.append({
            "left1": ed.school, "right1": ed.location or "",
            "left2": f"{ed.degree} {ed.field}" if ed.field else ed.degree,
            "right2": _dates(ed.start, ed.graduation) if ed.start else (ed.graduation or ""),
            "bullets": lines, "title": ed.degree, "organization": ed.school, "location": ed.location or "",
            "dates": _dates(ed.start, ed.graduation) if ed.start else (ed.graduation or ""), "stack": "",
            "stack_on": "left2",
        })  # fmt: skip

    sections = [{"key": "education", "title": "Education", "items": education}]
    for attr, title in SECTIONS:
        ranked = []
        for e in getattr(cv, attr):
            ids = sorted((b.id for b in e.bullets if b.id in chosen), key=order.get)
            if ids and not e.hidden:
                ranked.append((order[ids[0]], _item(e, [chosen[i] for i in ids], hints)))
        if entry_order == "relevance":
            ranked.sort(key=lambda t: t[0])
        if ranked:
            sections.append({"key": attr, "title": title, "items": [item for _, item in ranked]})
    certs = [e for e in cv.certifications if not e.hidden]
    if certs:
        sections.append({
            "key": "certifications",
            "title": "Certifications",
            "items": [
                {**_item(e, [b.text for b in e.bullets], hints), "left1": e.title, "right1": _dates(e.start, e.end),
                 "left2": e.organization or "", "right2": "", "stack_on": "left1"}
                for e in certs
            ],
        })  # fmt: skip

    skills = []
    for g in cv.skills:
        picked = sorted((i for i in g.items if i.lower() in wanted), key=lambda i: wanted.index(i.lower()))
        if not wanted:
            items = g.items
        elif all_skills:
            items = [*picked, *(i for i in g.items if i not in picked)]
        else:
            items = picked
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
