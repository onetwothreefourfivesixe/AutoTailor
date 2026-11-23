"""Write a tailored resume and its posting to the outputs folder."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    added: list[str] = field(default_factory=list)  # bullet IDs added to fill the page
    removed: list[str] = field(default_factory=list)  # bullet IDs dropped to fit one page
    keyword_score: float | None = None
    keywords_missing: list[str] = field(default_factory=list)


def slug(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:limit].rstrip("-") or "posting"


def posting_markdown(p: JobPosting, m: Match | None, also: list[JobPosting], coverage=None) -> str:  # noqa: ANN001
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
    if coverage is not None and coverage.score is not None:
        lines += [f"## Keyword match: {round(coverage.score * 100)}%", ""]
        lines += [f"On the resume: {', '.join(coverage.matched) or 'none'}", ""]
        if coverage.missing:
            lines += [f"Not on the resume: {', '.join(coverage.missing)}. Add one to your CV only if it's true for you.", ""]
    lines += ["## Posting", "", p.description or "(no description stored)", ""]
    return "\n".join(lines)


@dataclass
class Style:
    """Which templates to use (from settings.resume) and where they and the tools live."""

    template: str = "typst"
    word_template: str = ""
    templates_dir: Path | None = None
    tools: Path | None = None
    fill_page: bool = True
    min_bullets: int = 1
    entry_order: str = "relevance"
    all_skills: bool = True

    @classmethod
    def from_settings(cls, settings, paths) -> Style:  # noqa: ANN001
        return cls(settings.resume.template, settings.resume.word_template, paths.templates, paths.tools,
                   settings.tailor.fill_page, settings.tailor.min_bullets, settings.resume.entry_order,
                   settings.resume.skills == "all")  # fmt: skip

    def layout(self, r: TailoredResume, cv: MasterCV) -> dict:
        return build_layout(r, cv, self.entry_order, self.all_skills)


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
    import json

    from autotailor.resume.fill import fill_page, keyword_coverage, rank_candidates

    style = style or Style()
    folder = root / f"{index:02d}-{slug(p.company, 30)}-{slug(p.title, 50)}"
    folder.mkdir(parents=True, exist_ok=True)
    name = slug(cv.contact.name, 30)
    used, warning = "typst", None

    def render(r: TailoredResume) -> bytes:
        nonlocal used, warning
        layout = style.layout(r, cv)
        if style.template == "typst" or style.templates_dir is None or style.tools is None:
            used = "typst"
            return render_pdf(layout)
        out = render_resume(layout, style.template, style.templates_dir, style.tools)
        used, warning = out.template, out.warning
        return out.pdf

    if style.fill_page:
        cited = m.fit.relevant_bullet_ids if m and m.fit else []
        candidates = rank_candidates(resume, cv, f"{p.title}\n{p.description or ''}", cited)
        filled = fill_page(resume, cv, candidates, render, min_bullets=style.min_bullets)
        resume, pdf, added, removed = filled.resume, filled.pdf, filled.added, filled.removed
    else:
        pdf, added, removed = render(resume), [], []
    layout = style.layout(resume, cv)
    coverage = keyword_coverage(resume.keywords, layout, f"{p.title}\n{p.description or ''}")
    paths = Written(
        folder=folder,
        pdf=folder / f"{name}-resume.pdf",
        docx=folder / f"{name}-resume.docx",
        data=folder / "tailored.json",
        posting=folder / "posting.md",
        pages=pdf_page_count(pdf),
        template=used,
        warning=warning,
        added=added,
        removed=removed,
        keyword_score=coverage.score,
        keywords_missing=coverage.missing,
    )
    paths.pdf.write_bytes(pdf)
    if style.word_template and style.templates_dir is not None:
        render_docx_template(layout, style.templates_dir / style.word_template, paths.docx)
    else:
        render_docx(layout, paths.docx)
    paths.data.write_text(resume.model_dump_json(indent=2), encoding="utf-8")
    (folder / "fit.json").write_text(json.dumps({
        "added_to_fill": added, "removed_to_fit": removed, "pages": paths.pages,
        "keyword_score": coverage.score, "keywords_matched": coverage.matched, "keywords_missing": coverage.missing,
    }, indent=1), encoding="utf-8")  # fmt: skip
    paths.posting.write_text(posting_markdown(p, m, list(also), coverage), encoding="utf-8")
    return paths
