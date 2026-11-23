"""Render a resume layout (resume/layout.py) to PDF (Typst) and Word (python-docx)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


def render_pdf(layout: dict[str, Any]) -> bytes:
    import typst

    template = (resources.files("autotailor.templates") / "resume.typ").read_bytes()
    return typst.compile(template, sys_inputs={"data": json.dumps(layout, ensure_ascii=False)})


def render_png(layout: dict[str, Any], ppi: int = 110) -> bytes:
    """The first page as a PNG, for previews in the UI."""
    import typst

    template = (resources.files("autotailor.templates") / "resume.typ").read_bytes()
    out = typst.compile(template, format="png", ppi=ppi, sys_inputs={"data": json.dumps(layout, ensure_ascii=False)})
    return out[0] if isinstance(out, list) else out


def pdf_page_count(pdf: bytes) -> int:
    """Pages in a PDF. pypdf reads compressed object streams too (Tectonic's output has them)."""
    try:
        import io

        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception:  # not a readable PDF: count page objects directly
        return len(re.findall(rb"/Type\s*/Page\b(?!s)", pdf))


def render_docx(layout: dict[str, Any], path: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Inches, Pt

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(0.5)
    sec.top_margin = sec.bottom_margin = Inches(0.45)
    right_edge = sec.page_width - sec.left_margin - sec.right_margin
    style = doc.styles["Normal"]
    style.font.name, style.font.size = "Times New Roman", Pt(10)
    style.paragraph_format.space_after = Pt(0)

    def para(align=None, space_before=0):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(space_before)
        if align is not None:
            p.alignment = align
        return p

    def two_columns(left: str, right: str, bold=False, italic=False, size=10):
        p = para()
        p.paragraph_format.tab_stops.add_tab_stop(right_edge, WD_TAB_ALIGNMENT.RIGHT)
        run = p.add_run(f"{left}\t{right}")
        run.bold, run.italic, run.font.size = bold, italic, Pt(size)

    def rule_below(p):
        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in {"w:val": "single", "w:sz": "4", "w:space": "1", "w:color": "000000"}.items():
            bottom.set(qn(k), v)
        borders.append(bottom)
        p._p.get_or_add_pPr().append(borders)

    def heading(title: str):
        p = para(space_before=6)
        run = p.add_run(title)
        run.font.small_caps, run.font.size = True, Pt(11.5)
        rule_below(p)

    name = para(WD_ALIGN_PARAGRAPH.CENTER).add_run(layout["name"])
    name.font.size, name.font.small_caps = Pt(22), True
    para(WD_ALIGN_PARAGRAPH.CENTER).add_run(layout["contact"]).font.size = Pt(9)

    for s in layout["sections"]:
        heading(s["title"])
        for e in s["items"]:
            stack = f" | {e['stack']}" if e.get("stack") else ""
            two_columns(e["left1"] + (stack if e.get("stack_on") == "left1" else ""), e["right1"], bold=True)
            if e["left2"] or e["right2"]:
                two_columns(e["left2"] + (stack if e.get("stack_on") == "left2" else ""), e["right2"], italic=True, size=9.5)
            for b in e["bullets"]:
                p = doc.add_paragraph(style="List Bullet")
                p.paragraph_format.space_after = Pt(0)
                p.add_run(b).font.size = Pt(9.5)

    if layout["skills"]:
        heading("Skills")
        for g in layout["skills"]:
            p = para()
            label = p.add_run(f"{g['name']}: ")
            label.bold, label.font.size = True, Pt(9.5)
            p.add_run(g["items"]).font.size = Pt(9.5)

    doc.save(path)


# --- templates (M10) ---------------------------------------------------------------------------

BUILTIN_TEMPLATES = {
    "jake": "Jake's Resume (LaTeX)",
    "jake-compact": "Jake's Resume, compact (LaTeX: 10pt, tech stack after titles, one-line leadership)",
    "typst": "AutoTailor classic (Typst)",
}
_BUILTIN_TEX = {"jake": "jake.tex.j2", "jake-compact": "jake-compact.tex.j2"}


@dataclass
class Rendered:
    pdf: bytes
    template: str  # what was actually used
    warning: str | None = None


def render_resume(layout: dict[str, Any], template: str, templates_dir: Path, tools: Path) -> Rendered:
    """The resume PDF with the chosen template. A LaTeX template that can't be used because
    Tectonic is unavailable (e.g. offline on first use) falls back to Typst, with a warning;
    a template with errors raises LatexError so it gets fixed rather than silently replaced."""
    from autotailor.resume import latex

    if template == "typst":
        return Rendered(render_pdf(layout), "typst")
    source = latex.builtin(_BUILTIN_TEX[template]) if template in _BUILTIN_TEX else (templates_dir / template).read_text(encoding="utf-8")
    tex = latex.render_tex(source, layout)
    try:
        return Rendered(latex.compile_pdf(tex, tools), template)
    except latex.LatexUnavailable as e:
        return Rendered(render_pdf(layout), "typst", f"{e} Used the Typst template instead.")


def pdf_to_png(pdf: bytes, scale: float = 1.6, all_pages: bool = True, gap: int = 16) -> bytes:
    """A PDF as one PNG (pypdfium2), for previews of any template: every page, stacked
    top to bottom with a gap between them (or only the first page)."""
    import io

    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(pdf)
    try:
        count = len(doc) if all_pages else 1
        images = [doc[i].render(scale=scale).to_pil() for i in range(count)]
    finally:
        doc.close()
    if len(images) == 1:
        sheet = images[0]
    else:
        width = max(im.width for im in images)
        sheet = Image.new("RGB", (width, sum(im.height for im in images) + gap * (len(images) - 1)), (200, 203, 209))
        y = 0
        for im in images:
            sheet.paste(im, (0, y))
            y += im.height + gap
    buf = io.BytesIO()
    sheet.save(buf, format="PNG")
    return buf.getvalue()


def render_docx_template(layout: dict[str, Any], template: Path, path: Path) -> None:
    """Fill your own Word template (docxtpl: {{ name }}, {% for s in sections %} ...)."""
    from docxtpl import DocxTemplate

    doc = DocxTemplate(str(template))
    doc.render(layout)
    doc.save(str(path))
