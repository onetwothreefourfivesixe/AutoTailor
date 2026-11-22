from pathlib import Path

import pytest
import yaml
from docx import Document
from typer.testing import CliRunner

from autotailor import db, matching
from autotailor.cli import app
from autotailor.config import Settings, TailorSettings, save_settings
from autotailor.cv import MasterCV
from autotailor.models import (
    CriterionScore,
    FitAssessment,
    JobPosting,
    Match,
    PostingStatus,
    Source,
    TailoredBullet,
    TailoredResume,
    Verdict,
)
from autotailor.resume.layout import build_layout
from autotailor.resume.output import posting_markdown, slug
from autotailor.resume.render import pdf_page_count, render_docx, render_pdf
from autotailor.resume.verify import verify_resume

FIXTURES = Path(__file__).parent / "fixtures"
CV_TEXT = (FIXTURES / "cv_ml_student.yaml").read_text()
CV = MasterCV.model_validate(yaml.safe_load(CV_TEXT))
SRC = CV.bullets()
LIMITS = TailorSettings(min_bullets=2, max_bullets=4)


def _r(*pairs, skills=()):
    return TailoredResume(bullets=[TailoredBullet(source_id=i, text=t) for i, t in pairs], skills=list(skills))


def _verbatim(*ids, skills=()):
    return _r(*((i, SRC[i].text) for i in ids), skills=skills)


# --- verify ------------------------------------------------------------------


def test_verbatim_and_light_rewording_pass():
    assert verify_resume(_verbatim("exp-lab-detector", "proj-ocr", skills=["PyTorch", "c++"]), CV, LIMITS) == []
    reworded = _r(
        ("exp-lab-detector", "Trained a YOLO object detection model in PyTorch on 12k labeled warehouse images (0.81 mAP)"),
        ("exp-lab-cpp", "Cut on-device latency by 35% by porting detector preprocessing to C++"),
    )
    assert verify_resume(reworded, CV, LIMITS) == []


@pytest.mark.parametrize(
    ("bullet", "expected"),
    [
        (("exp-lab-detector", "Trained a YOLO detector in PyTorch on 50k images, reaching 0.81 mAP"), "numbers ['50k']"),
        (("exp-lab-detector", "Trained a YOLO detector in TensorFlow on 12k images, reaching 0.81 mAP"), "tensorflow not in the source"),
        (("exp-lab-labeling", "Built a Python tool used by Google researchers"), "google not in the source"),
        (("ghost", "Did things"), "'ghost' is not a bullet ID"),
        (("proj-ocr", SRC["proj-ocr"].text + " and deployed it across 400 retail stores for regional managers"), "much longer"),
    ],
)
def test_verify_catches(bullet, expected):
    errors = verify_resume(_r(bullet, ("proj-budget", SRC["proj-budget"].text)), CV, LIMITS)
    assert any(expected in e for e in errors), errors


def test_verify_counts_duplicates_and_skills():
    assert any("at least 2" in e for e in verify_resume(_verbatim("proj-ocr"), CV, LIMITS))
    assert any("at most 4" in e for e in verify_resume(_verbatim(*list(SRC)[:5]), CV, LIMITS))
    dup = _verbatim("proj-ocr", "proj-ocr")
    assert any("more than once" in e for e in verify_resume(dup, CV, LIMITS))
    assert any("Rust" in e for e in verify_resume(_verbatim("proj-ocr", "proj-budget", skills=["Rust"]), CV, LIMITS))


def test_users_real_cv_has_no_false_alarms():
    path = Path("cv/master.yaml")
    if not path.exists():
        pytest.skip("no personal CV in this checkout")
    from autotailor.cv import load_master_cv

    cv = load_master_cv(path)
    bullets = cv.bullets()
    r = TailoredResume(bullets=[TailoredBullet(source_id=i, text=b.text) for i, b in bullets.items()])
    assert verify_resume(r, cv, TailorSettings(min_bullets=1, max_bullets=999)) == []


# --- layout and rendering ----------------------------------------------------


def test_layout_selects_entries_and_orders_bullets():
    r = _r(("exp-lab-cpp", "Ported preprocessing to C++, cutting latency by 35%"),
           ("exp-lab-detector", SRC["exp-lab-detector"].text), ("edu-coursework", SRC["edu-coursework"].text),
           skills=["PyTorch", "Python"])  # fmt: skip
    lay = build_layout(r, CV)
    titles = [s["title"] for s in lay["sections"]]
    assert titles == ["Education", "Work Experience"]  # no project bullets chosen
    lab = lay["sections"][1]["items"][0]
    assert (lab["left1"], lab["left2"], lab["right2"]) == ("University Vision Lab", "Research Intern", "Jun. 2026 – Aug. 2026")
    assert lab["bullets"][0].startswith("Ported preprocessing")  # tailored order kept
    assert lay["sections"][0]["items"][0]["bullets"] == [SRC["edu-coursework"].text]
    assert lay["skills"] == [{"name": "Languages", "items": "Python"}, {"name": "Frameworks & Libraries", "items": "PyTorch"}]
    assert build_layout(_verbatim("proj-ocr"), CV)["skills"][0]["items"] == "Python, C++, SQL"  # none chosen: all


def test_render_pdf_and_docx(tmp_path):
    lay = build_layout(_verbatim("exp-lab-detector", "proj-ocr", skills=["C++"]), CV)
    lay["sections"][1]["items"][0]["bullets"].append("Symbols #, $, *, _, [x] and <y> render literally")
    pdf = render_pdf(lay)
    assert pdf[:5] == b"%PDF-" and pdf_page_count(pdf) == 1
    render_docx(lay, tmp_path / "r.docx")
    text = "\n".join(p.text for p in Document(tmp_path / "r.docx").paragraphs)
    assert "Sam Example" in text and "Trained a YOLO-based object detector" in text and "Languages: C++" in text


def test_posting_markdown_and_slug():
    p = JobPosting(id="lever:1", source=Source.LEVER, source_ref="1", company="Acme", title="ML Intern",
                   location="Denver", canonical_url="https://x/1", description="Body.")  # fmt: skip
    other = p.model_copy(update={"id": "lever:2", "location": "Austin", "canonical_url": "https://x/2"})
    md = posting_markdown(p, Match(posting_id="lever:1", verdict=Verdict.ELIGIBLE, verdict_reasons=["ok"]), [other])
    assert "Apply: https://x/1" in md and "- Austin: https://x/2" in md and "Body." in md
    assert slug("Palantir: SWE, Intern (2027)!") == "palantir-swe-intern-2027"


# --- shortlist: one slot per role ---------------------------------------------


def _fit(score: int) -> FitAssessment:
    c = CriterionScore(score=score, reason="r")
    return FitAssessment(skills=c, experience=c, level=c, interests=c, relevant_bullet_ids=["exp-lab-detector"])


def _store(conn, pid, title, fit, company="Acme", location=None):
    p = JobPosting(id=pid, source=Source.LEVER, source_ref=pid.split(":")[1], company=company, title=title,
                   location=location, description=f"{title} at {company}", status=PostingStatus.RESOLVED)  # fmt: skip
    db.upsert_posting(conn, p)
    db.save_match(conn, Match(posting_id=pid, verdict=Verdict.ELIGIBLE))
    db.save_fit(conn, pid, fit, [], _fit(5))
    return p


def test_shortlist_takes_one_posting_per_role(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    _store(conn, "lever:a", "SWE Intern", 0.9, location="Denver")
    _store(conn, "lever:b", "SWE Intern", 0.95, location="Austin")  # same role, other city
    _store(conn, "lever:c", "ML Intern", 0.8)
    _store(conn, "lever:d", "Data Intern", 0.4)  # below min fit
    s = Settings()
    picked = [p.id for p, _ in matching.shortlist(conn, s)]
    assert picked == ["lever:b", "lever:c"]
    rows = matching.ranked(conn, s)
    assert [q.id for q in matching.same_role(db.get_posting(conn, "lever:b"), rows)] == ["lever:a"]
    s.run.jobs_per_run = 1
    assert [p.id for p, _ in matching.shortlist(conn, s)] == ["lever:b"]


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    s = Settings()
    s.tailor = LIMITS
    save_settings(s, tmp_path / "config.toml")
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9, location="Denver")
    _store(conn, "lever:b", "SWE Intern", 0.95, location="Austin")
    _store(conn, "lever:c", "ML Intern", 0.8)
    conn.close()
    return tmp_path


def test_cli_tailor(home, monkeypatch):
    from autotailor.chains.tailor_graph import TailorResult

    seen_profiles: list[str] = []

    def fake_tailor(posting, profile, cv, settings):
        seen_profiles.append(profile)
        if "ML Intern" in posting:
            return TailorResult(_r(("ghost", "x")), ["Bullet 1: source_id 'ghost' is not a bullet ID"], 3)
        return TailorResult(_verbatim("exp-lab-detector", "proj-ocr", skills=["PyTorch"]), [], 2)

    monkeypatch.setattr("autotailor.chains.tailor_graph.tailor_posting", fake_tailor)
    runner = CliRunner()
    r = runner.invoke(app, ["tailor"])
    assert r.exit_code == 0, r.output
    assert "1 tailored, 1 failed" in r.output
    assert "no resume written: the last of 3 drafts" in r.output and "'ghost'" in r.output
    assert "Most relevant CV bullets for this posting (from fit scoring): exp-lab-detector" in seen_profiles[0]

    folders = [f for f in (home / "data" / "outputs").glob("*-run1/*") if f.is_dir()]
    assert [f.name for f in folders] == ["01-acme-swe-intern"]
    files = sorted(x.name for x in folders[0].iterdir())
    assert files == ["posting.md", "sam-example-resume.docx", "sam-example-resume.pdf", "tailored.json"]
    assert "Also posted as:\n- Denver" in (folders[0] / "posting.md").read_text()

    conn = db.connect(home / "data" / "autotailor.db")
    kinds = sorted(a["kind"] for a in db.artifacts_for(conn, "lever:b"))
    assert kinds == ["resume_data", "resume_docx", "resume_pdf"]
    run = conn.execute("SELECT status, counts_json FROM runs").fetchone()
    assert run["status"] == "done" and '"tailored": 1' in run["counts_json"]
    conn.close()

    seen_profiles.clear()
    r = runner.invoke(app, ["tailor"])
    assert "Skipping 1 already tailored" in r.output and len(seen_profiles) == 1  # only the failed one again


def test_cli_tailor_without_graph(home, monkeypatch):
    def unwritten(*a, **k):
        raise NotImplementedError

    monkeypatch.setattr("autotailor.chains.tailor_graph.tailor_posting", unwritten)
    r = CliRunner().invoke(app, ["tailor"])
    assert r.exit_code == 1 and "M4 exercise" in r.output
