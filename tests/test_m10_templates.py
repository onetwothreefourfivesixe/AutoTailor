"""M10 step 4: resume templates (Jake's Resume via Tectonic, your own LaTeX and Word templates)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_m4_resume import CV, _verbatim

from autotailor.resume import latex
from autotailor.resume.layout import build_layout
from autotailor.resume.render import pdf_page_count, pdf_to_png, render_docx_template, render_pdf, render_resume

REPO_TOOLS = Path(__file__).resolve().parents[1] / "data" / "tools"
LAYOUT = build_layout(_verbatim("exp-lab-detector", "exp-lab-cpp", "proj-ocr"), CV)


def test_escape():
    assert latex.escape("R&D 100% #1 $5 a_b {x} ~ ^") == r"R\&D 100\% \#1 \$5 a\_b \{x\} \textasciitilde{} \textasciicircum{}"
    assert latex.escape(latex.Raw(r"\textbf{x}")) == r"\textbf{x}"


def test_jake_template_fills_and_escapes():
    tex = latex.render_tex(latex.builtin("jake.tex.j2"), LAYOUT)
    assert r"\textbf{\Huge \scshape Sam Example}" in tex
    assert r"Ported the detector's preprocessing to C++, cutting on-device latency by 35\%" in tex
    assert r"\resumeSubheading" in tex and r"\section{Technical Skills}" in tex
    assert r"\href{mailto:" in tex and "((" not in tex
    assert "MIT License" in tex and "Jake Gutierrez" in tex  # credit kept


def test_custom_template_errors_are_clear(tmp_path):
    import jinja2

    with pytest.raises(jinja2.UndefinedError):
        latex.render_tex(r"\VAR ((( nope )))", LAYOUT)


def test_falls_back_to_typst_when_tectonic_is_unavailable(tmp_path, monkeypatch):
    def unavailable(tex, tools):
        raise latex.LatexUnavailable("Couldn't download Tectonic (offline).")

    monkeypatch.setattr(latex, "compile_pdf", unavailable)
    r = render_resume(LAYOUT, "jake", tmp_path, tmp_path)
    assert r.template == "typst" and "Used the Typst template instead" in r.warning and pdf_page_count(r.pdf) == 1


def test_latex_errors_are_not_hidden(tmp_path, monkeypatch):
    def broken(tex, tools):
        raise latex.LatexError("LaTeX failed:\n! Undefined control sequence.")

    monkeypatch.setattr(latex, "compile_pdf", broken)
    (tmp_path / "mine.tex").write_text(r"\documentclass{article}\begin{document}\oops ((( name )))\end{document}")
    with pytest.raises(latex.LatexError, match="Undefined control sequence"):
        render_resume(LAYOUT, "mine.tex", tmp_path, tmp_path)


def test_word_template(tmp_path):
    from docx import Document

    tpl = Document()
    tpl.add_paragraph("{{ name }}")
    tpl.add_paragraph("{%p for s in sections %}")
    tpl.add_paragraph("{{ s.title }}")
    tpl.add_paragraph("{%p endfor %}")
    tpl.save(tmp_path / "mine.docx")
    render_docx_template(LAYOUT, tmp_path / "mine.docx", tmp_path / "out.docx")
    text = [p.text for p in Document(tmp_path / "out.docx").paragraphs]
    assert text[0] == "Sam Example" and "Work Experience" in text and "Projects" in text


def test_pdf_preview_and_page_count():
    pdf = render_pdf(LAYOUT)
    assert pdf_page_count(pdf) == 1
    assert pdf_to_png(pdf)[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not (REPO_TOOLS / "tectonic").exists(), reason="Tectonic not installed in data/tools")
def test_jake_compiles_with_tectonic(tmp_path):
    r = render_resume(LAYOUT, "jake", tmp_path, REPO_TOOLS)
    assert r.template == "jake" and r.warning is None and pdf_page_count(r.pdf) == 1


def test_tectonic_download_installs_the_binary(tmp_path, monkeypatch):
    """The first LaTeX render downloads Tectonic; the archive's binary ends up in data/tools."""
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"#!/bin/sh\necho tectonic\n"
        info = tarfile.TarInfo("tectonic-0.17.0/tectonic")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    archive = buf.getvalue()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield archive

    import sys

    urls = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("platform.machine", lambda: "x86_64")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("httpx.stream", lambda method, url, **k: urls.append(url) or Stream())
    exe = latex.ensure_tectonic(tmp_path / "tools")
    assert urls[0].endswith("tectonic-0.17.0-x86_64-unknown-linux-musl.tar.gz")
    assert exe == tmp_path / "tools" / "tectonic" and exe.read_bytes().startswith(b"#!/bin/sh")
    if os.name != "nt":  # Windows has no executable bit
        assert exe.stat().st_mode & 0o111


def test_tectonic_download_on_windows_unzips_the_exe(tmp_path, monkeypatch):
    import io
    import sys
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tectonic.exe", b"MZ fake windows binary")
    archive = buf.getvalue()

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield archive

    urls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("httpx.stream", lambda method, url, **k: urls.append(url) or Stream())
    exe = latex.ensure_tectonic(tmp_path / "tools")
    assert urls[0].endswith("tectonic-0.17.0-x86_64-pc-windows-msvc.zip")
    assert exe == tmp_path / "tools" / "tectonic.exe" and exe.read_bytes() == b"MZ fake windows binary"


@pytest.mark.parametrize(
    ("plat", "machine", "target"),
    [
        ("win32", "AMD64", "x86_64-pc-windows-msvc.zip"),
        ("linux", "x86_64", "x86_64-unknown-linux-musl.tar.gz"),
        ("linux", "aarch64", "aarch64-unknown-linux-musl.tar.gz"),
        ("darwin", "arm64", "aarch64-apple-darwin.tar.gz"),
        ("darwin", "x86_64", "x86_64-apple-darwin.tar.gz"),
    ],
)
def test_tectonic_url_matches_the_computer(plat, machine, target):
    assert latex.tectonic_url(plat, machine).endswith(f"tectonic-0.17.0-{target}")


def test_no_tectonic_build_says_how_to_install_it():
    with pytest.raises(latex.LatexUnavailable, match="PATH"):
        latex.tectonic_url("win32", "ARM64")
