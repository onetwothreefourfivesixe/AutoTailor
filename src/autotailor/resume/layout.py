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

from pydantic import BaseModel, Field

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
            "bullets": lines, "title": f"{ed.degree} {ed.field}" if ed.field else ed.degree, "organization": ed.school,
            "location": ed.location or "",
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


# --- the final editor ----------------------------------------------------------------------------
# A layout as the resume editor edits it: plain fields per entry, in the user's words. normalize()
# turns it back into a render-ready layout (the four corner fields the templates place).

DEFAULT_TITLES = {"education": "Education", **dict(SECTIONS), "certifications": "Certifications"}


class EditableItem(BaseModel):
    title: str = ""
    organization: str = ""
    location: str = ""
    dates: str = ""
    stack: str = ""
    bullets: list[str] = Field(default_factory=list)


class EditableSection(BaseModel):
    key: str = "custom"  # experience, projects, ... or "custom" for a section the user added
    title: str
    items: list[EditableItem] = Field(default_factory=list)


class EditableContact(BaseModel):
    text: str
    url: str | None = None


class EditableSkillGroup(BaseModel):
    name: str
    items: str


class EditableLayout(BaseModel):
    name: str
    contact_items: list[EditableContact] = Field(default_factory=list)
    sections: list[EditableSection] = Field(default_factory=list)
    skills: list[EditableSkillGroup] = Field(default_factory=list)


def normalize(e: EditableLayout) -> dict[str, Any]:
    """A render-ready layout from an edited one: corner fields from the plain ones, empty
    bullets, entries and sections dropped, and `renamed` set on sections whose title changed
    (so a template keeps the user's title rather than its own)."""
    sections = []
    for s in e.sections:
        items = []
        for it in s.items:
            f = {k: (getattr(it, k) or "").strip() for k in ("title", "organization", "location", "dates", "stack")}
            bullets = [" ".join(b.split()) for b in it.bullets if b.strip()]
            if not (f["title"] or f["organization"] or bullets):
                continue
            if s.key == "certifications":
                corners = {"left1": f["title"], "right1": f["dates"], "left2": f["organization"], "right2": f["location"],
                           "stack_on": "left1"}  # fmt: skip
            elif f["organization"]:
                corners = {"left1": f["organization"], "right1": f["location"], "left2": f["title"], "right2": f["dates"],
                           "stack_on": "left2"}  # fmt: skip
            else:
                corners = {"left1": f["title"], "right1": f["dates"], "left2": "", "right2": f["location"], "stack_on": "left1"}
            items.append({**f, **corners, "bullets": bullets})
        title = s.title.strip() or DEFAULT_TITLES.get(s.key, "Section")
        if items:
            sections.append({"key": s.key, "title": title, "items": items,
                             "renamed": title != DEFAULT_TITLES.get(s.key, title + " ")})  # fmt: skip
    contact = [{"text": c.text.strip(), "url": (c.url or "").strip() or None} for c in e.contact_items if c.text.strip()]
    skills = [{"name": g.name.strip(), "items": g.items.strip()} for g in e.skills if g.name.strip() and g.items.strip()]
    return {"name": e.name.strip(), "contact": " | ".join(c["text"] for c in contact), "contact_items": contact,
            "sections": sections, "skills": skills}  # fmt: skip


def editable(layout: dict[str, Any]) -> dict[str, Any]:
    """The editor's view of a render-ready layout (the reverse of normalize)."""
    return EditableLayout(
        name=layout.get("name", ""),
        contact_items=[EditableContact(**c) for c in layout.get("contact_items", [])],
        sections=[EditableSection(key=s.get("key", "custom"), title=s["title"], items=[
            EditableItem(**{k: i.get(k, "") or "" for k in ("title", "organization", "location", "dates", "stack")},
                         bullets=list(i.get("bullets", []))) for i in s["items"]]) for s in layout.get("sections", [])],
        skills=[EditableSkillGroup(name=g["name"], items=g["items"]) for g in layout.get("skills", [])],
    ).model_dump()  # fmt: skip
