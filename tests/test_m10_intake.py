"""M10 step 2: reading pasted and dropped input, previews, and the Add page."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from autotailor import db, intake
from autotailor.chains.extract_posting import PostingFields
from autotailor.cli import app
from autotailor.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "runway_digest.eml"
POSTING = ("Northwind Robotics is hiring a Perception Intern in Pittsburgh, PA for Summer 2027. "
           "You will train detection models in PyTorch and ship them to our warehouse robots. " * 4)  # fmt: skip


def extract(text: str) -> PostingFields:
    return PostingFields(company="Northwind Robotics", title="Perception Intern", location="Pittsburgh, PA")


def _pdf(text: str) -> bytes:
    """A one-page PDF of `text` (as a saved posting would be), via Typst."""
    import typst

    return typst.compile(f"#set page(width: 15cm)\n#text({json.dumps(text)})".encode())


def test_looks_like_email():
    raw = FIXTURE.read_text()
    assert intake.looks_like_email(raw)
    assert not intake.looks_like_email(POSTING)


def test_read_files():
    s = Settings()
    email = intake.read_file("digest.eml", FIXTURE.read_bytes(), extract, s)
    assert email.kind == "email" and len(email.roles) == 3 and email.status == "Runway email, 3 roles"
    txt = intake.read_file("job.txt", POSTING.encode(), extract, s)
    assert txt.kind == "posting" and txt.posting.title == "Perception Intern" and txt.status == "new"
    html = intake.read_file("job.html", f"<html><body><h1>Job</h1><p>{POSTING}</p></body></html>".encode(), extract, s)
    assert html.kind == "posting" and "<p>" not in html.posting.description
    pdf = intake.read_file("job.pdf", _pdf(POSTING), extract, s)
    assert pdf.kind == "posting", pdf.error
    assert "warehouse robots" in pdf.posting.description
    assert intake.read_file("cv.docx", b"x", extract, s).kind == "error"
    assert "too short" in intake.read_file("short.txt", b"hi", extract, s).error


def test_pasted_raw_email_and_unwritten_chain():
    s = Settings()
    assert intake.read_text(FIXTURE.read_text(), extract, s).kind == "email"

    def unwritten(text):
        raise NotImplementedError

    c = intake.read_text(POSTING, unwritten, s)
    assert c.kind == "error" and "M1 extraction chain" in c.error


def test_urls_refuse_no_fetch_sites():
    c = intake.read_urls("https://www.linkedin.com/jobs/view/123\n\n", None, extract)
    assert len(c) == 1 and c[0].kind == "error" and "does not allow automated access" in c[0].error


def test_duplicates_and_save(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    s = Settings()
    first = intake.read_text(POSTING, extract, s)
    intake.mark_duplicates(conn, [first])
    assert first.status == "new"
    report = intake.save(conn, [first, intake.read_file("d.eml", FIXTURE.read_bytes(), extract, s)], s)
    assert len(report.new) == 4 and report.emails == 1 and report.roles == 3

    again = intake.read_text(POSTING + " Apply now.", extract, s)  # different text, same role
    intake.mark_duplicates(conn, [again])
    assert again.status == f"same as {first.posting.id}"
    assert intake.save(conn, [again], s).merged == 1


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    monkeypatch.setattr("autotailor.chains.extract_posting.extract_posting_fields", lambda text, settings: extract(text))
    return tmp_path


def test_add_page_paste_preview_save(home, monkeypatch):
    from streamlit.testing.v1 import AppTest

    queued = []
    monkeypatch.setattr("autotailor.tasks.ensure_worker", lambda h, d: queued.append(1) or True)
    at = AppTest.from_string("from autotailor.ui import page_add\npage_add.render()", default_timeout=30).run()
    assert not at.exception, at.exception
    at.text_area(key="add.text").input(POSTING)
    at = next(b for b in at.button if b.key == "add.read_text").click().run()
    assert not at.exception, at.exception
    preview = at.dataframe[0].value if at.dataframe else None
    assert at.get("arrow_data_frame") or preview is not None
    at = next(b for b in at.button if b.key == "add.save").click().run()
    assert not at.exception, at.exception
    assert any("1 new posting" in s.value for s in at.success)
    conn = db.connect(home / "data" / "autotailor.db")
    assert [p.title for p in db.list_postings(conn, None)] == ["Perception Intern"]
    at = next(b for b in at.button if b.key == "add.screen").click().run()
    assert [r["kind"] for r in __import__("autotailor.tasks", fromlist=["x"]).active(conn)] == ["screen"] and queued
