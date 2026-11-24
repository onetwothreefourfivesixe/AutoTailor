"""First-run setup: CV import from documents, the setup status, API keys, suggested boards."""

from __future__ import annotations

import io
import json
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from test_m4_resume import CV_TEXT
from typer.testing import CliRunner

from autotailor import cv_import
from autotailor.api import TOKEN_HEADER, api_token, create_app
from autotailor.cli import app as cli
from autotailor.config import Settings
from autotailor.cv import MasterCV
from autotailor.paths import get_paths

RESUME = """Sam Example
sam@example.com | 555-0100 | github.com/samex
EDUCATION
State University, B.S. Computer Science, Expected May 2028
EXPERIENCE
Research Intern, University Vision Lab, Jun. 2025 - Aug. 2025
- Trained a YOLO-based object detector in PyTorch on 12k labeled warehouse images, reaching 0.81 mAP
- Built a Python tool to label and visualize camera and depth-sensor data, used by 6 researchers
PROJECTS
Shelf-Label Reader
- Built an OCR pipeline with OpenCV and a CRNN model to read price labels from shelf photos
SKILLS
Languages: Python, C++, SQL
"""


def fake_model(reword: bool = False):
    """Returns a fixed ImportedCV; with `reword`, one bullet is not copied word for word."""

    class M:
        def with_structured_output(self, schema, **kw):
            def answer(_pv):
                return cv_import.ImportedCV(
                    contact=cv_import.ImportedContact(name="Sam Example", email="sam@example.com", links=["github.com/samex"]),
                    education=[cv_import.ImportedEducation(school="State University", degree="B.S.", field="Computer Science",
                                                           graduation="Expected May 2028")],
                    experience=[cv_import.ImportedEntry(title="Research Intern", organization="University Vision Lab",
                                                        start="Jun. 2025", end="Aug. 2025", bullets=[
                        cv_import.ImportedBullet(text="Trained a YOLO-based object detector in PyTorch on 12k labeled warehouse images, reaching 0.81 mAP"),
                        cv_import.ImportedBullet(text="Built a Python labeling tool loved by researchers" if reword else
                                                 "Built a Python tool to label and visualize camera and depth-sensor data, used by 6 researchers"),
                    ])],
                    projects=[cv_import.ImportedEntry(title="Shelf-Label Reader", bullets=[
                        cv_import.ImportedBullet(text="Built an OCR pipeline with OpenCV and a CRNN model to read price labels from shelf photos")])],
                    skills=[cv_import.ImportedSkillGroup(name="Languages", items=["Python", "C++", "SQL"])],
                )  # fmt: skip

            return RunnableLambda(answer)

    return M()


def test_parse_gives_a_valid_cv_with_ids_and_flags_reworded_bullets():
    cv = cv_import.parse_cv(RESUME, Settings(), llm=fake_model())
    parsed = MasterCV.model_validate(cv)
    assert parsed.experience[0].id == "exp-university-vision-lab" and len(parsed.bullets()) == 3
    assert cv_import.unverified_bullets(cv, RESUME) == []
    reworded = cv_import.parse_cv(RESUME, Settings(), llm=fake_model(reword=True))
    assert cv_import.unverified_bullets(reworded, RESUME) == [reworded["experience"][0]["bullets"][1]["id"]]


def test_profile_from_cv():
    cv = {"education": [{"school": "State U", "degree": "B.S.", "field": "Computer Science", "graduation": "Expected May 2028"},
                        {"school": "Lincoln High", "degree": "High School Diploma", "graduation": "June 2024"}]}  # fmt: skip
    edu = cv_import.profile_from_cv(cv, today=date(2026, 11, 22))
    assert edu == [{"level": "bachelor", "field": "Computer Science", "status": "in_progress",
                    "graduation_date": "2028-05-31", "school": "State U"}]  # fmt: skip


def test_document_text_reads_pdf_and_docx():
    import typst
    from docx import Document

    pdf = typst.compile(f"#set page(width: 18cm)\n#text({json.dumps(RESUME)})".encode())
    assert "YOLO-based object detector" in cv_import.document_text("cv.pdf", pdf)
    doc = Document()
    for line in RESUME.splitlines():
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    assert "Shelf-Label Reader" in cv_import.document_text("cv.docx", buf.getvalue())
    with pytest.raises(ValueError):
        cv_import.document_text("cv.pages", b"x")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    CliRunner().invoke(cli, ["init"])
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    c.home = tmp_path  # type: ignore[attr-defined]
    return c


def test_status_on_a_fresh_install(client):
    st = client.get("/api/setup/status").json()
    assert st["cv"] == "template" and not st["complete"] and not st["dismissed"]
    assert st["checks"] == {"settings": True, "cv": False, "profile": False, "models": False, "sources": False}
    assert st["missing_keys"] == ["anthropic"]  # the default model is Claude
    client.post("/api/setup/finish")
    assert client.get("/api/setup/status").json()["dismissed"] is True


def test_keys_are_saved_to_env_and_never_returned(client):
    r = client.put("/api/setup/keys", json={"provider": "google_genai", "key": "AIzaTESTKEY123"}).json()
    assert {k: r["keys"][k] for k in ("anthropic", "openai", "google_genai")} == {"anthropic": False, "openai": False, "google_genai": True}
    env = (client.home / ".env").read_text()
    assert "GOOGLE_API_KEY=AIzaTESTKEY123" in env and env.count("GOOGLE_API_KEY") == 1  # replaced, not appended
    assert (client.home / ".env").stat().st_mode & 0o077 == 0
    assert "AIzaTESTKEY123" not in client.get("/api/setup/status").text
    assert client.put("/api/setup/keys", json={"provider": "openai", "key": "has spaces in it"}).status_code == 422
    os.environ.pop("GOOGLE_API_KEY", None)


def test_cv_import_endpoint(client, monkeypatch):
    monkeypatch.setattr("autotailor.llm.get_chat_model", lambda settings, step: fake_model())
    r = client.post("/api/setup/cv", files={"file": ("resume.txt", RESUME.encode(), "text/plain")}).json()
    assert r["unverified"] == [] and r["education"][0]["level"] == "bachelor" and r["cv"]["contact"]["name"] == "Sam Example"
    saved = client.put("/api/cv", json={"cv": r["cv"], "confirm_delete_used": True})
    assert saved.status_code == 200
    assert client.get("/api/setup/status").json()["cv"] == "ready"
    y = client.post("/api/setup/cv", files={"file": ("master.yaml", CV_TEXT.encode(), "text/yaml")}).json()
    assert y["cv"]["contact"]["name"] == "Sam Example" and y["unverified"] == []
    assert client.post("/api/setup/cv", files={"file": ("cv.txt", b"too short", "text/plain")}).status_code == 422


def test_suggested_boards(client):
    boards = client.get("/api/setup/boards").json()["boards"]
    stripe = {"name": "Stripe", "ats": "greenhouse", "slug": "stripe", "industries": ["fintech"], "following": False}
    assert len(boards) > 250 and stripe in boards


def test_ui_initialises_a_new_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda name: None)  # stop before starting servers
    CliRunner().invoke(cli, ["ui", "--no-browser"])
    assert (tmp_path / "config.toml").exists() and (tmp_path / "cv" / "master.yaml").exists()


def test_field_is_the_major_not_the_college():
    from autotailor.cv_import import _field

    assert _field({"field": "Computer Science", "degree": "B.A."}) == "Computer Science"
    assert _field({"field": "College of Computing, Data Science, and Society", "degree": "B.A. in Data Science"}) == "Data Science"
    assert _field({"field": "School of Engineering", "degree": "B.S. Electrical Engineering"}) == "Electrical Engineering"
    assert _field({"field": "College of Letters and Science", "degree": "Bachelor of Arts"}) == ""


def test_graduation_date_is_found_in_the_text_when_the_model_leaves_it_out():
    from autotailor.cv_import import _date_near

    text = "EDUCATION\nUniversity of California, Berkeley Berkeley, CA\nB.A. Data Science Expected May 2029\nCoursework: CS 61A"
    assert _date_near(text, "University of California, Berkeley") == "Expected May 2029"
    assert _date_near("State University\nB.S. Biology Aug. 2022 - 2026", "State University") == "2026"
    assert _date_near(text, "MIT") is None
