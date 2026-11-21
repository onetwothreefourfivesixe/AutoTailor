"""Write a tailored resume and its posting to the outputs folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from autotailor.cv import MasterCV
from autotailor.models import JobPosting, Match, TailoredResume
from autotailor.resume.layout import build_layout
from autotailor.resume.render import pdf_page_count, render_docx, render_pdf


@dataclass
class Written:
    folder: Path
    pdf: Path
    docx: Path
    data: Path
    posting: Path
    pages: int


def slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "posting"


def posting_markdown(p: JobPosting, m: Match | None, also: list[JobPosting]) -> str:
    lines = [f"# {p.title}", "", f"**{p.company}** | {p.location or 'location not stated'}", ""]
    if p.canonical_url:
        lines += [f"Apply: {p.canonical_url}", ""]
    if also:
        lines.append("Also posted as:")
        lines += [f"- {q.location or 'location not stated'}: {q.canonical_url or q.id}" for q in also]
        lines.append("")
    if m:
        lines += [f"## Eligibility: {m.verdict.value}", ""] + [f"- {r}" for r in m.verdict_reasons] + [""]
        if m.fit_score is not None:
            lines += [f"## Fit: {m.fit_score:.2f}", ""] + [f"- {r}" for r in m.fit_reasons] + [""]
    lines += ["## Posting", "", p.description or "(no description stored)", ""]
    return "\n".join(lines)


def write_outputs(
    root: Path,
    index: int,
    p: JobPosting,
    m: Match | None,
    resume: TailoredResume,
    cv: MasterCV,
    also: list[JobPosting] = (),
) -> Written:
    folder = root / f"{index:02d}-{slug(p.company, 30)}-{slug(p.title, 50)}"
    folder.mkdir(parents=True, exist_ok=True)
    name = slug(cv.contact.name, 30)
    layout = build_layout(resume, cv)
    pdf = render_pdf(layout)
    paths = Written(
        folder=folder,
        pdf=folder / f"{name}-resume.pdf",
        docx=folder / f"{name}-resume.docx",
        data=folder / "tailored.json",
        posting=folder / "posting.md",
        pages=pdf_page_count(pdf),
    )
    paths.pdf.write_bytes(pdf)
    render_docx(layout, paths.docx)
    paths.data.write_text(resume.model_dump_json(indent=2), encoding="utf-8")
    paths.posting.write_text(posting_markdown(p, m, list(also)), encoding="utf-8")
    return paths
