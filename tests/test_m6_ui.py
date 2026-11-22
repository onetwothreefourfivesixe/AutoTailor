"""M6: the UI's logic (saving, IDs, usage, diffs) and a headless smoke test of each page."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest
from test_m4_resume import CV, CV_TEXT, _r
from test_m5_batch import LoopGraph, home  # noqa: F401  (fixture)
from typer.testing import CliRunner

from autotailor import db
from autotailor.cli import app
from autotailor.config import load_settings
from autotailor.cv import load_master_cv
from autotailor.ui import logic

# --- IDs and saving ----------------------------------------------------------------


def test_new_ids_are_unique_and_valid():
    taken = {"exp-acme-robotics"}
    assert logic.new_id("exp", "Acme Robotics, Inc.", taken) == "exp-acme-robotics-inc"
    assert logic.new_id("exp", "Acme Robotics", taken) == "exp-acme-robotics-2"
    assert logic.new_id("exp", "!!!", taken) == "exp-item"


def test_fill_missing_ids_keeps_existing_and_skips_kept():
    data = CV.model_dump(mode="json")
    data["projects"].append({"title": "Chess Engine", "bullets": [{"text": "Wrote a chess engine in Rust"}]})
    data["projects"][0]["bullets"].append({"id": None, "text": "Added a new bullet"})
    first_id = data["projects"][0]["id"]
    logic.fill_missing_ids(data, keep={"proj-chess-engine"})
    new = data["projects"][-1]
    assert new["id"] == "proj-chess-engine-2"  # 'proj-chess-engine' is reserved by a past run
    assert new["bullets"][0]["id"] == "proj-chess-engine-2-wrote-a-chess"
    assert data["projects"][0]["bullets"][-1]["id"].startswith(first_id + "-added-a-new")
    assert data["projects"][0]["id"] == first_id


def test_save_cv_validates_backs_up_and_round_trips(tmp_path):
    path, backups = tmp_path / "master.yaml", tmp_path / "backups"
    path.write_text(CV_TEXT)
    bad = CV.model_dump(mode="json")
    bad["projects"][0]["id"] = bad["experience"][0]["id"]  # duplicate ID
    with pytest.raises(ValidationError):
        logic.save_cv(bad, path, backups)
    assert path.read_text() == CV_TEXT and not backups.exists()

    logic.save_cv(CV.model_dump(mode="json"), path, backups)
    assert load_master_cv(path) == CV
    assert len(list(backups.iterdir())) == 1
    text = path.read_text()
    assert "null" not in text and "[]" not in text  # tidy, like a hand-written file


def test_save_settings_checked(tmp_path):
    path, backups = tmp_path / "config.toml", tmp_path / "b"
    data = load_settings(path).model_dump(mode="json")
    data["run"]["jobs_per_run"] = 0  # must be >= 1
    with pytest.raises(ValidationError):
        logic.save_settings_checked(data, path, backups)
    assert not path.exists()
    data["run"]["jobs_per_run"] = 4
    logic.save_settings_checked(data, path, backups)
    assert load_settings(path).run.jobs_per_run == 4


def test_resume_diff_marks_reworded_words():
    rows = logic.resume_diff(
        _r(("proj-ocr", CV.bullets()["proj-ocr"].text), ("exp-lab-cpp", "Ported detector <preprocessing> to C++"), ("gone", "x")),
        CV,
    )
    assert [r.changed for r in rows] == [False, True, True]
    assert "<ins>" in rows[1].html and "&lt;preprocessing&gt;" in rows[1].html
    assert rows[2].original is None


# --- history and usage (needs a real run) ----------------------------------------------


@pytest.fixture
def ran(home, monkeypatch):  # noqa: F811
    LoopGraph().install(monkeypatch)
    r = CliRunner().invoke(app, ["tailor"])
    assert r.exit_code == 0, r.output
    return home


def test_bullet_usage_and_run_outputs(ran):
    conn = db.connect(ran / "data" / "autotailor.db")
    usage = logic.bullet_usage(conn, ran / "data" / "outputs")
    assert [(u.run_id, u.posting_id) for u in usage["exp-lab-detector"]] == [(1, "lever:b")]
    outs = logic.run_outputs(conn, 1, ran / "data" / "outputs")
    assert [(o.index, o.company, o.pdf.exists()) for o in outs] == [(1, "Acme", True)]
    assert logic.run_model(db.get_run(conn, 1)) == "anthropic:claude-opus-5-5"
    old = CV.model_dump(mode="json")
    new = CV.model_dump(mode="json")
    new["experience"][0]["bullets"] = [b for b in new["experience"][0]["bullets"] if b["id"] != "exp-lab-detector"]
    assert logic.removed_ids(old, new) == {"exp-lab-detector"}


# --- pages (headless) ----------------------------------------------------------------------


def _page(module: str) -> AppTest:
    at = AppTest.from_string(f"from autotailor.ui import {module}\n{module}.render()", default_timeout=30)
    return at.run()


@pytest.mark.parametrize("module", ["page_overview", "page_settings", "page_cv", "page_runs", "page_postings"])
def test_page_renders(ran, module):
    at = _page(module)
    assert not at.exception, at.exception


def test_app_navigation_renders(ran):
    from importlib import resources

    at = AppTest.from_file(str(resources.files("autotailor.ui") / "app.py"), default_timeout=30).run()
    assert not at.exception, at.exception
    assert at.title[0].value == "AutoTailor"


def test_settings_page_saves(ran):
    at = _page("page_settings")
    at.number_input(key="set.jobs").set_value(4)
    at.text_area(key="set.interests").input("Robotics\nML infrastructure")
    next(b for b in at.button if b.label == "Save settings").click().run()
    assert not at.exception, at.exception
    s = load_settings(ran / "config.toml")
    assert s.run.jobs_per_run == 4 and s.profile.role_interests == ["Robotics", "ML infrastructure"]
    assert any((ran / "data" / "config_backups").iterdir())


def _save_cv(at: AppTest) -> AppTest:
    return next(b for b in at.button if b.label == "Save CV").click().run()


def test_cv_page_edits_contact_and_adds_entry(ran):
    at = _page("page_cv")
    at.text_input(key="cv.contact.name").input("Sam Q. Example")
    next(b for b in at.button if b.key == "cv.projects.add").click().run()
    new_title = next(t for t in at.text_input if t.key.startswith("cv.projects.") and t.key.endswith(".title") and not t.value)
    new_title.input("Chess Engine").run()
    at = _save_cv(at)
    assert not at.exception, at.exception
    assert any("Saved" in s.value for s in at.success), [e.value for e in at.error]
    cv = load_master_cv(ran / "cv" / "master.yaml")
    assert cv.contact.name == "Sam Q. Example"
    assert cv.projects[-1].id == "proj-chess-engine" and cv.projects[-1].title == "Chess Engine"
    assert len(list((ran / "data" / "cv_backups").iterdir())) == 1


def test_cv_page_asks_before_deleting_a_used_bullet(ran):
    at = _page("page_cv")
    data = yaml.safe_load(CV_TEXT)
    for e in data["experience"]:
        e["bullets"] = [b for b in e["bullets"] if b["id"] != "exp-lab-detector"]  # used by run 1
    at.text_area(key="cv.raw").input(yaml.safe_dump(data, sort_keys=False))
    save_yaml = lambda a: next(b for b in a.button if b.key == "cv.raw.save").click().run()  # noqa: E731
    at = save_yaml(at)
    assert any("used in past resumes" in w.value and "run 1, Acme | SWE Intern" in w.value for w in at.warning)
    assert "exp-lab-detector" in load_master_cv(ran / "cv" / "master.yaml").bullets()  # not yet

    at = save_yaml(at)  # same deletion again: confirmed
    assert not at.exception, at.exception
    assert "exp-lab-detector" not in load_master_cv(ran / "cv" / "master.yaml").bullets()


def test_cv_page_rejects_invalid_yaml(ran):
    at = _page("page_cv")
    at.text_area(key="cv.raw").input("contact: [not, a, mapping]")
    at = next(b for b in at.button if b.key == "cv.raw.save").click().run()
    assert at.error and "Not saved" in at.error[0].value
    assert load_master_cv(ran / "cv" / "master.yaml") == CV
