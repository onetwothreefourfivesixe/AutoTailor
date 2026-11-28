"""Filling the page with relevant CV bullets, and the keyword match score."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_m4_resume import CV, _r, _verbatim

from autotailor.cv import load_master_cv
from autotailor.models import TailoredBullet, TailoredResume
from autotailor.resume.fill import fill_page, keyword_coverage, rank_candidates
from autotailor.resume.layout import build_layout
from autotailor.resume.render import pdf_page_count, render_pdf

REAL_CV = Path(__file__).resolve().parents[1] / "cv" / "master.yaml"
POSTING = "Perception intern: train object detection models in PyTorch, build OCR pipelines with OpenCV, write C++ and Rust."


def test_candidates_skip_used_bullets_and_take_turns_across_sections():
    resume = _verbatim("exp-lab-detector").model_copy(update={"keywords": ["OCR", "OpenCV"]})
    order = rank_candidates(resume, CV, POSTING)
    assert "exp-lab-detector" not in order
    assert set(order) == set(CV.bullets()) - {"exp-lab-detector"}
    first_two_sections = {_section(order[0]), _section(order[1])}
    assert len(first_two_sections) == 2, order  # one from each section in turn
    assert order.index("proj-ocr") < order.index("proj-budget")  # keyword hits rank higher


def _section(bid: str) -> str:
    for section in ("experience", "projects", "activities", "education"):
        entries = CV.education if section == "education" else getattr(CV, section, [])
        for e in entries:
            if any(b.id == bid for b in (e.details if section == "education" else e.bullets)):
                return section
    return "?"


class FakeRenderer:
    """'Pages' = 1 while the resume has at most `capacity` bullets."""

    def __init__(self, capacity: int) -> None:
        self.capacity, self.calls = capacity, 0

    def __call__(self, r: TailoredResume) -> bytes:
        self.calls += 1
        pages = 1 if len(r.bullets) <= self.capacity else 2
        return b"%PDF" + b"/Type /Page " * pages


def test_fill_adds_until_the_page_is_full():
    start = _verbatim("exp-lab-detector", "proj-ocr")
    candidates = rank_candidates(start, CV, POSTING)
    render = FakeRenderer(capacity=4)
    out = fill_page(start, CV, candidates, render)
    assert len(out.resume.bullets) == 4 and out.pages == 1 and out.added == candidates[:2]
    assert out.resume.bullets[2].text == CV.bullets()[candidates[0]].text  # verbatim from the CV
    assert render.calls <= 8


def test_fill_trims_an_overlong_draft():
    start = _verbatim("exp-lab-detector", "proj-ocr", "proj-budget", "exp-lab-cpp")
    out = fill_page(start, CV, ["exp-lab-labeling"], FakeRenderer(capacity=2), min_bullets=1)
    assert [b.source_id for b in out.resume.bullets] == ["exp-lab-detector", "proj-ocr"]
    assert out.removed == ["exp-lab-cpp", "proj-budget"] and out.added == []


class SpaceRenderer:
    """'Pages' = 1 while the bullets' total length fits `room` characters."""

    def __init__(self, room: int) -> None:
        self.room = room

    def __call__(self, r: TailoredResume) -> bytes:
        pages = 1 if sum(len(b.text) for b in r.bullets) <= self.room else 2
        return b"%PDF" + b"/Type /Page " * pages


def test_after_trimming_shorter_bullets_still_fill_the_gap():
    bullets = CV.bullets()
    start = _verbatim("exp-lab-detector", "exp-lab-labeling")  # the second is long and won't fit
    short = "exp-lab-cpp"  # shorter, and in the same job: no new heading needed
    assert len(bullets[short].text) < len(bullets["exp-lab-labeling"].text)
    room = len(bullets["exp-lab-detector"].text) + len(bullets[short].text) + 1
    other_entry = "proj-budget"  # also short, but would start a new entry after a trim
    out = fill_page(start, CV, [other_entry, short], SpaceRenderer(room), min_bullets=1)
    assert out.removed == ["exp-lab-labeling"] and out.added == [short] and out.pages == 1


def test_fill_adds_everything_when_it_all_fits():
    start = _verbatim("exp-lab-detector")
    candidates = rank_candidates(start, CV, POSTING)
    out = fill_page(start, CV, candidates, FakeRenderer(capacity=100))
    assert out.added == candidates and out.pages == 1


def _render_typst(r: TailoredResume) -> bytes:
    return render_pdf(build_layout(r, CV_REAL))


CV_REAL = load_master_cv(REAL_CV) if REAL_CV.exists() else None


@pytest.mark.skipif(CV_REAL is None, reason="needs a full CV in cv/master.yaml")
def test_real_render_fills_one_page_without_spilling():
    bullets = CV_REAL.bullets()
    first = [b.id for e in CV_REAL.experience[:2] for b in e.bullets[:2]]
    start = TailoredResume(bullets=[TailoredBullet(source_id=i, text=bullets[i].text) for i in first])
    candidates = rank_candidates(start, CV_REAL, "Software engineering intern: Python, machine learning, data pipelines")
    out = fill_page(start, CV_REAL, candidates, _render_typst)
    assert out.pages == 1 and len(out.added) > 3
    rest = [c for c in candidates if c not in out.added]
    if rest:  # the next bullet in ranking order would spill onto a second page
        more = out.resume.model_copy(update={"bullets": [*out.resume.bullets, TailoredBullet(
            source_id=rest[0], text=bullets[rest[0]].text)]})  # fmt: skip
        assert pdf_page_count(_render_typst(more)) == 2
    assert out.renders <= 8


def test_keyword_coverage():
    layout = build_layout(_r(("exp-lab-detector", CV.bullets()["exp-lab-detector"].text), skills=["PyTorch", "C++"]), CV)
    cov = keyword_coverage(["PyTorch", "object detector", "C++", "Kubernetes", "pytorch"], layout)
    assert cov.matched == ["PyTorch", "object detector", "C++"] and cov.missing == ["Kubernetes"]
    assert cov.score == 0.75
    assert keyword_coverage([], layout).score is None
    # Keywords that aren't in the posting are dropped; at most 20 are kept.
    cov = keyword_coverage(["PyTorch", "HIPAA compliance", "Kubernetes"], layout, posting_text="We use PyTorch and Kubernetes.")
    assert cov.matched == ["PyTorch"] and cov.missing == ["Kubernetes"]
    many = [f"skill{i}" for i in range(30)]
    assert len(keyword_coverage(many, layout, " ".join(many)).missing) == 20


def test_outputs_record_fill_and_coverage(tmp_path):
    import json

    from autotailor.models import JobPosting, PostingStatus, Source
    from autotailor.resume.output import Style, write_outputs

    p = JobPosting(id="lever:x", source=Source.LEVER, source_ref="x", company="Acme", title="Perception Intern",
                   description=POSTING, status=PostingStatus.RESOLVED)  # fmt: skip
    resume = _verbatim("exp-lab-detector").model_copy(update={"keywords": ["PyTorch", "OCR", "Rust", "Golang"]})
    w = write_outputs(tmp_path, 1, p, None, resume, CV, style=Style("typst"))
    fit = json.loads((w.folder / "fit.json").read_text())
    assert fit["pages"] == 1 and set(fit["added_to_fill"]) == set(CV.bullets()) - {"exp-lab-detector"}
    assert fit["keywords_missing"] == ["Rust"] and fit["keyword_score"] == 0.67
    assert "## Keyword match: 67%" in w.posting.read_text() and "Not on the resume: Rust" in w.posting.read_text()
    saved = TailoredResume.model_validate_json(w.data.read_text())
    assert len(saved.bullets) == len(CV.bullets())  # the small sample CV fits entirely

    off = write_outputs(tmp_path / "off", 1, p, None, resume, CV, style=Style("typst", fill_page=False))
    assert json.loads((off.folder / "fit.json").read_text())["added_to_fill"] == []


def test_previews_show_every_page():
    import io

    import typst
    from PIL import Image

    from autotailor.resume.render import pdf_to_png

    two = typst.compile(b"#set page(width: 10cm, height: 8cm)\nfirst\n#pagebreak()\nsecond")
    one_page = Image.open(io.BytesIO(pdf_to_png(two, scale=1, all_pages=False)))
    both = Image.open(io.BytesIO(pdf_to_png(two, scale=1)))
    assert both.height > 2 * one_page.height and both.width == one_page.width


@pytest.mark.skipif(CV_REAL is None, reason="needs a full CV in cv/master.yaml")
def test_whole_cv_preview_has_every_page(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autotailor.api import TOKEN_HEADER, api_token, create_app
    from autotailor.cli import app
    from autotailor.paths import get_paths

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(REAL_CV.read_text())
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    whole = c.post("/api/cv/preview", json={"template": "typst", "full": True})
    sample = c.post("/api/cv/preview", json={"template": "typst"})
    assert int(whole.headers["X-Pages"]) > 1 and int(sample.headers["X-Pages"]) <= int(whole.headers["X-Pages"])


def test_top_up_adds_a_shorter_bullet_when_the_next_one_does_not_fit():
    bullets = CV.bullets()
    start = _verbatim("exp-lab-detector")
    same_entry = ["exp-lab-labeling", "exp-lab-cpp"]  # the rest of that job's bullets
    shortest = min(same_entry, key=lambda c: len(bullets[c].text))
    longest_first = sorted(same_entry, key=lambda c: -len(bullets[c].text))
    room = len(bullets["exp-lab-detector"].text) + len(bullets[shortest].text)
    out = fill_page(start, CV, longest_first, SpaceRenderer(room))
    assert out.added == [shortest] and out.pages == 1 and out.renders <= 8


def test_usual_resume_hidden_entries_stack_and_order():
    import yaml

    from autotailor.config import Profile
    from autotailor.cv import MasterCV
    from autotailor.matching import profile_text

    data = yaml.safe_load((Path(__file__).parent / "fixtures" / "cv_ml_student.yaml").read_text())
    lab, (shelf, budget) = data["experience"][0], data["projects"]
    lab["stack"] = ["PyTorch", "C++", "YOLO"]
    lab["bullets"][0]["base"] = True
    lab["bullets"][1]["base"] = True
    data["activities"] = [{"id": "act-old", "title": "Old Club", "hidden": True, "bullets": [{"id": "act-old-1", "text": "Ran a club"}]}]
    cv = MasterCV.model_validate(data)
    assert cv.hidden_bullets() == {"act-old-1"}
    assert cv.base_bullets() == [lab["bullets"][0]["id"], lab["bullets"][1]["id"]]

    text = profile_text(cv, Profile(), for_resume=True)
    assert "(usual)" in text and "stack: PyTorch, C++, YOLO" in text and "[act-old-1]" not in text
    assert "[act-old-1]" in profile_text(cv, Profile())  # fit scoring and answers still see it

    start = TailoredResume(bullets=[TailoredBullet(source_id="proj-ocr", text=CV.bullets()["proj-ocr"].text)])
    ranked = rank_candidates(start, cv, POSTING)
    assert "act-old-1" not in ranked

    # Entries follow the tailored order; the stack puts the posting's terms first; all skills stay.
    r = TailoredResume(bullets=[TailoredBullet(source_id=i, text=cv.bullets()[i].text) for i in ("proj-budget", "proj-ocr", "exp-lab-detector")],
                       skills=["C++"], keywords=["YOLO"])  # fmt: skip
    layout = build_layout(r, cv, "relevance", all_skills=True)
    projects = next(s for s in layout["sections"] if s["key"] == "projects")["items"]
    assert [p["title"] for p in projects] == [budget["title"], shelf["title"]]
    lab_item = next(s for s in layout["sections"] if s["key"] == "experience")["items"][0]
    assert lab_item["stack"] == "C++, YOLO, PyTorch"
    in_cv_order = next(s for s in build_layout(r, cv, "cv")["sections"] if s["key"] == "projects")["items"]
    assert [p["title"] for p in in_cv_order] == [shelf["title"], budget["title"]]
    assert not any(s["key"] == "activities" for s in layout["sections"])
    assert sum(len(g["items"].split(", ")) for g in layout["skills"]) == sum(len(g.items) for g in cv.skills)


def test_previews_can_render_from_many_threads_at_once():
    """PDFium isn't thread-safe; without the lock in pdf_to_png this crashed the API (segfault)."""
    from concurrent.futures import ThreadPoolExecutor

    import typst

    from autotailor.resume.render import pdf_to_png

    pdf = typst.compile(b"#set page(width: 10cm, height: 8cm)\nfirst\n#pagebreak()\nsecond")
    with ThreadPoolExecutor(max_workers=8) as pool:
        pngs = list(pool.map(lambda _: pdf_to_png(pdf, scale=1), range(32)))
    assert len({len(p) for p in pngs}) == 1 and pngs[0].startswith(b"\x89PNG")
