"""Write a tailored resume and its posting to the outputs folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from autotailor.cv import MasterCV
from autotailor.models import JobPosting, Match, TailoredResume
from autotailor.resume.layout import build_layout
from autotailor.resume.render import pdf_page_count, render_docx, render_docx_template, render_pdf, render_resume


@dataclass
class Written:
    folder: Path
    pdf: Path
    docx: Path
    data: Path
    posting: Path
    pages: int
    template: str = "typst"
    warning: str | None = None


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


@dataclass
class Style:
    """Which templates to use (from settings.resume) and where they and the tools live."""

    template: str = "typst"
    word_template: str = ""
    templates_dir: Path | None = None
    tools: Path | None = None

    @classmethod
    def from_settings(cls, settings, paths) -> Style:  # noqa: ANN001
        return cls(settings.resume.template, settings.resume.word_template, paths.templates, paths.tools)


def write_outputs(
    root: Path,
    index: int,
    p: JobPosting,
    m: Match | None,
    resume: TailoredResume,
    cv: MasterCV,
    also: list[JobPosting] = (),
    style: Style | None = None,
) -> Written:
    style = style or Style()
    folder = root / f"{index:02d}-{slug(p.company, 30)}-{slug(p.title, 50)}"
    folder.mkdir(parents=True, exist_ok=True)
    name = slug(cv.contact.name, 30)
    layout = build_layout(resume, cv)
    if style.template == "typst" or style.templates_dir is None or style.tools is None:
        pdf, used, warning = render_pdf(layout), "typst", None
    else:
        r = render_resume(layout, style.template, style.templates_dir, style.tools)
        pdf, used, warning = r.pdf, r.template, r.warning
    paths = Written(
        folder=folder,
        pdf=folder / f"{name}-resume.pdf",
        docx=folder / f"{name}-resume.docx",
        data=folder / "tailored.json",
        posting=folder / "posting.md",
        pages=pdf_page_count(pdf),
        template=used,
        warning=warning,
    )
    paths.pdf.write_bytes(pdf)
    if style.word_template and style.templates_dir is not None:
        render_docx_template(layout, style.templates_dir / style.word_template, paths.docx)
    else:
        render_docx(layout, paths.docx)
    paths.data.write_text(resume.model_dump_json(indent=2), encoding="utf-8")
    paths.posting.write_text(posting_markdown(p, m, list(also)), encoding="utf-8")
    return paths
