"""Render a resume layout (resume/layout.py) to PDF (Typst) and Word (python-docx)."""

from __future__ import annotations

import json
import re
from importlib import resources
from pathlib import Path
from typing import Any


def render_pdf(layout: dict[str, Any]) -> bytes:
    import typst

    template = (resources.files("autotailor.templates") / "resume.typ").read_bytes()
    return typst.compile(template, sys_inputs={"data": json.dumps(layout, ensure_ascii=False)})


def pdf_page_count(pdf: bytes) -> int:
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
            two_columns(e["left1"], e["right1"], bold=True)
            if e["left2"] or e["right2"]:
                two_columns(e["left2"], e["right2"], italic=True, size=9.5)
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
