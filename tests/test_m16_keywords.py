"""Adding chosen ATS keywords to a finished resume, each exactly once."""

from __future__ import annotations

from langchain_core.runnables import RunnableLambda

from autotailor.config import Settings
from autotailor.resume.keywords import add_keywords, check_bullet, page_text

LAYOUT = {
    "name": "Sam", "contact": "", "contact_items": [],
    "sections": [{"key": "experience", "title": "Experience", "items": [{
        "title": "Data Intern", "organization": "Acme", "location": "", "dates": "", "stack": "",
        "left1": "Acme", "right1": "", "left2": "Data Intern", "right2": "", "stack_on": "left2",
        "bullets": ["Built a Python pipeline that links records across 4 datasets",
                    "Presented findings to the analytics team"]}]}],
    "skills": [{"name": "Languages", "items": "Python, SQL"}, {"name": "Tools", "items": "Git"}],
}  # fmt: skip


class FakeModel:
    """Placements for the plan; for a focused rewrite, `rewrites[term]` (or the bullet unchanged)."""

    def __init__(self, placements, rewrites=None) -> None:  # noqa: ANN001
        self.placements, self.rewrites, self.seen = placements, rewrites or {}, None

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001, ANN003
        def respond(prompt):  # noqa: ANN001
            text = "\n".join(m.content for m in prompt.to_messages())
            if schema.__name__ == "Rewrite":
                term = text.split("Keyword: ", 1)[1].split("\n", 1)[0]
                bullet = text.split("Bullet: ", 1)[1]
                return schema.model_validate({"text": self.rewrites.get(term, bullet)})
            self.seen = text
            return schema.model_validate({"placements": self.placements})

        return RunnableLambda(respond)


def test_each_term_goes_in_once_where_it_fits():
    llm = FakeModel([
        {"term": "ETL", "target": "B1", "text": "Built a Python ETL pipeline that links records across 4 datasets"},
        {"term": "Docker", "target": "S2"},
    ])  # fmt: skip
    r = add_keywords(LAYOUT, ["ETL", "Docker", "python", "etl"], "posting", Settings(), llm=llm)
    assert "B1 [Data Intern, Acme] Built a Python pipeline" in llm.seen and "S2 Tools: Git" in llm.seen
    bullets = r.layout["sections"][0]["items"][0]["bullets"]
    assert bullets[0] == "Built a Python ETL pipeline that links records across 4 datasets"
    assert r.layout["skills"][1]["items"] == "Git, Docker"
    assert [c.term for c in r.changes] == ["ETL", "Docker"] and r.skipped == [{"term": "python", "reason": "already on the resume"}]
    text = page_text(r.layout).lower()
    assert text.count("etl") == 1 and text.count("docker") == 1
    assert LAYOUT["skills"][1]["items"] == "Git"  # the input isn't changed


def test_unsafe_rewrites_go_to_skills_instead():
    llm = FakeModel([
        {"term": "Airflow", "target": "B1", "text": "Built a Python pipeline in Airflow on AWS linking 40 datasets"},  # new facts
        {"term": "Tableau", "target": "B2", "text": "Presented findings in Tableau to the analytics team"},
        {"term": "dashboards", "target": "B2", "text": "Presented findings and dashboards to the analytics team"},  # 2nd in B2
    ])  # fmt: skip
    r = add_keywords(LAYOUT, ["Airflow", "Tableau", "dashboards", "Spark"], "posting", Settings(), llm=llm)
    by_term = {c.term: c for c in r.changes}
    assert by_term["Airflow"].where.startswith("Skills") and "instead" in by_term["Airflow"].note
    assert by_term["Tableau"].where == "Data Intern, Acme"
    assert by_term["dashboards"].where.startswith("Skills") and "already gained a keyword" in by_term["dashboards"].note
    assert by_term["Spark"].where.startswith("Skills") and "didn't place it" in by_term["Spark"].note
    assert r.layout["sections"][0]["items"][0]["bullets"][0] == LAYOUT["sections"][0]["items"][0]["bullets"][0]


def test_check_bullet():
    before = "Built a Python pipeline that links records across 4 datasets"
    assert check_bullet(before, "Built a Python ETL pipeline that links records across 4 datasets", "ETL") is None
    assert "didn't include" in check_bullet(before, before, "ETL")
    assert "numbers" in check_bullet(before, "Built a Python ETL pipeline that links records across 9 datasets", "ETL")
    assert "added words" in check_bullet(before, "Wrote ETL jobs", "ETL")  # a rewrite, not a keyword added
    # Found live: a small model appended a claim along with the keyword.
    ocr = "Built an OCR pipeline with OpenCV and a CRNN model to read price labels from shelf photos"
    assert "vision" in check_bullet(ocr, ocr + " for robotics vision integration", "Robotics")
    assert check_bullet(ocr, ocr.replace("OCR pipeline", "OCR pipeline using Robotics"), "Robotics") is None


def test_api_and_cli(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from test_m4_resume import CV_TEXT, _store
    from typer.testing import CliRunner

    from autotailor import db
    from autotailor.api import TOKEN_HEADER, api_token, create_app
    from autotailor.cli import app
    from autotailor.paths import get_paths
    from test_m12_api import _tailor

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9)
    conn.close()
    monkeypatch.setattr("autotailor.tasks.ensure_worker", lambda home, data: True)
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    c.home = tmp_path  # type: ignore[attr-defined]
    _tailor(c, monkeypatch)
    monkeypatch.setattr("autotailor.llm.get_chat_model", lambda s, step: FakeModel([{"term": "Kubernetes", "target": "S1"}]))

    r = c.post("/api/jobs/lever:a/resume/keywords", json={"terms": ["Kubernetes"]}).json()
    assert r["changes"][0]["term"] == "Kubernetes" and "Kubernetes" in r["layout"]["skills"][0]["items"]
    assert not c.get("/api/jobs/lever:a/resume").json()["layout_edited"]  # a proposal: nothing saved
    assert c.post("/api/jobs/lever:a/resume/keywords", json={"terms": [" "]}).status_code == 422

    out = CliRunner().invoke(app, ["ats", "lever:a", "Kubernetes", "--dry-run"])
    assert out.exit_code == 0 and "+ Kubernetes" in out.output and "Saved" not in out.output
    out = CliRunner().invoke(app, ["ats", "lever:a", "Kubernetes"])
    assert "Saved: 1 page(s)" in out.output
    view = c.get("/api/jobs/lever:a/resume").json()
    assert view["layout_edited"] and "Kubernetes" in view["layout"]["skills"][0]["items"]
    again = CliRunner().invoke(app, ["ats", "lever:a", "Kubernetes"])
    assert "= Kubernetes: already on the resume" in again.output


def test_a_copied_bullet_gets_a_focused_retry_and_skills_go_to_the_closest_group():
    llm = FakeModel(
        [{"term": "data pipelines", "target": "B1", "text": LAYOUT["sections"][0]["items"][0]["bullets"][0]},  # copied
         {"term": "Spark SQL", "target": "B2", "text": LAYOUT["sections"][0]["items"][0]["bullets"][1]}],  # copied, retry fails
        rewrites={"data pipelines": "Built Python data pipelines that link records across 4 datasets"},
    )  # fmt: skip
    r = add_keywords(LAYOUT, ["data pipelines", "Spark SQL"], "posting", Settings(), llm=llm)
    by_term = {c.term: c for c in r.changes}
    assert by_term["data pipelines"].after == "Built Python data pipelines that link records across 4 datasets"
    assert by_term["Spark SQL"].where == "Skills: Languages"  # shares "SQL" with Languages, not the last group


def test_singular_or_plural_counts_and_a_shorter_skill_name_is_replaced():
    from autotailor.resume.fill import _has_phrase

    assert _has_phrase("Built a data pipeline", "data pipelines") and _has_phrase("RAG pipelines", "RAG pipeline")
    assert not _has_phrase("Built a pipe", "pipeline") and _has_phrase("AWS", "aws")
    before = "Built a Python pipeline that links records across 4 datasets"
    assert check_bullet(before, "Built a Python data pipeline that links records across 4 datasets", "data pipelines") is None
    llm = FakeModel([{"term": "Python 3", "target": "S1"}])
    r = add_keywords(LAYOUT, ["Python 3"], "posting", Settings(), llm=llm)
    assert r.layout["skills"][0]["items"] == "Python 3, SQL"  # "Python" became "Python 3", not a second entry


def test_a_plural_keyword_after_a_takes_the_singular():
    from autotailor.resume.keywords import fix_article

    assert fix_article("Architected a multi-agent RAG pipelines on Neo4j", "RAG pipelines") == "Architected a multi-agent RAG pipeline on Neo4j"
    assert fix_article("Built data pipelines for 4 teams", "data pipelines") == "Built data pipelines for 4 teams"
    assert fix_article("Ran a pass", "pass") == "Ran a pass"


def test_saving_gives_no_note_for_the_chosen_keywords():
    import yaml
    from test_m4_resume import CV_TEXT

    from autotailor.cv import MasterCV
    from autotailor.resume.output import layout_warnings

    cv = MasterCV.model_validate(yaml.safe_load(CV_TEXT))
    page = {"sections": [{"items": [{"bullets": ["Built Kubernetes deployments for 9 teams"], "title": "", "organization": "", "stack": ""}]}]}
    notes = layout_warnings(page, cv, allowed=["Kubernetes"])
    assert len(notes) == 1 and "9" in notes[0] and "kubernetes" not in notes[0].lower().split(":")[-1]
