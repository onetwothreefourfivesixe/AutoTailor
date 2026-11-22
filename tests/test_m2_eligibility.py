from datetime import date

import pytest
from typer.testing import CliRunner

from autotailor import db
from autotailor.cli import app
from autotailor.config import (
    DegreeLevel,
    Education,
    EligibilityRules,
    Profile,
    Settings,
    load_settings,
    save_settings,
)
from autotailor.eligibility import assess, passes_gate, quote_found, title_rules_out
from autotailor.models import Evidence, JobPosting, Match, PostingStatus, Requirements, Source, Verdict

B, M, PHD = DegreeLevel.BACHELOR, DegreeLevel.MASTER, DegreeLevel.PHD


def _profile(*edu: Education) -> Profile:
    return Profile(
        education=list(edu)
        or [Education(level=B, field="Data Science & Computer Science", status="in_progress",
                      graduation_date=date(2029, 5, 31))]  # fmt: skip
    )


def _posting(title="Software Engineering Intern", description=None, req=None) -> JobPosting:
    return JobPosting(
        id="lever:1", source=Source.LEVER, source_ref="1", company="Acme", title=title,
        description=description, requirements=req,
        status=PostingStatus.RESOLVED if description else PostingStatus.UNRESOLVED,
    )  # fmt: skip


def _ev(field, quote):
    return Evidence(field=field, quote=quote)


RULES = EligibilityRules()
TEXT = (
    "Currently pursuing a Bachelor’s or Master's degree in Computer Science. "
    "Graduating between December 2027 and June 2029.  Rising seniors graduating in Spring 2028."
)


# --- title pass --------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "PhD Intern, Software Engineer - Interaction Design (2027)",
        "2027 Summer Intern, AI Engineer (Master's Degree)",
        "Summer 2027 MBA Internship - Licensing Intern",
        "AI Researcher, 2027 Graduate U.S.",
        "Systems Engineering, New Grad 2027",
        "Software Engineer, Class of 2028",
    ],
)
def test_title_rules_out(title):
    p = _posting(title)
    assert title_rules_out(p, _profile(), RULES)
    m = assess(p, _profile(), RULES)
    assert m.verdict == Verdict.INELIGIBLE and m.verdict_reasons[0].startswith("Title")


@pytest.mark.parametrize(
    "title",
    [
        "Software Engineering Intern (Summer 2027)",
        "2027 Summer Intern - Human Machine Teaming - 6532",
        "Campus Undergraduate Full-Time Engineer - 2027 AI Engineer I",
        "Scrum Master Intern",
        "Machine Learning Engineering Intern - Summer 2027",
    ],
)
def test_title_does_not_rule_out(title):
    p = _posting(title)
    assert not title_rules_out(p, _profile(), RULES)
    assert assess(p, _profile(), RULES).verdict == Verdict.UNCLEAR  # requirements not read yet


def test_undergraduate_title_excludes_grad_students():
    grad = _profile(Education(level=M, field="CS", status="in_progress", graduation_date=date(2027, 5, 1)))
    assert title_rules_out(_posting("Undergraduate Research Intern"), grad, RULES)
    assert not title_rules_out(_posting("Undergraduate Research Intern"), _profile(), RULES)


def test_title_matching_grad_year_is_fine():
    assert not title_rules_out(_posting("New Grad 2029 Software Engineer"), _profile(), RULES)


# --- requirements pass -------------------------------------------------------


def test_eligible_with_verified_quotes():
    req = Requirements(
        degree_levels=[B, M], enrollment_required=True,
        graduation_earliest=date(2027, 12, 1), graduation_latest=date(2029, 6, 30),
        evidence=[
            _ev("degree_levels", "pursuing a Bachelor's or Master's degree"),  # straight vs curly apostrophe
            _ev("enrollment_required", "Currently pursuing"),
            _ev("graduation_window", "graduating between December 2027 and June 2029"),
        ],
    )  # fmt: skip
    m = assess(_posting(description=TEXT, req=req), _profile(), RULES)
    assert m.verdict == Verdict.ELIGIBLE, m.verdict_reasons
    assert any("Dec 2027 to Jun 2029; you graduate May 2029" in r for r in m.verdict_reasons)


def test_graduation_window_rules_out_with_quote():
    req = Requirements(
        degree_levels=[B], graduation_latest=date(2028, 6, 30),
        evidence=[_ev("degree_levels", "Bachelor’s"), _ev("graduation_window", "Rising seniors graduating in Spring 2028")],
    )  # fmt: skip
    m = assess(_posting(description=TEXT, req=req), _profile(), RULES)
    assert m.verdict == Verdict.INELIGIBLE
    assert 'Posting ("Rising seniors graduating in Spring 2028") requires graduating any time to Jun 2028' in m.verdict_reasons[0]


def test_invented_quote_is_not_trusted():
    req = Requirements(degree_levels=[PHD], evidence=[_ev("degree_levels", "PhD required")])
    m = assess(_posting(description=TEXT, req=req), _profile(), RULES)
    assert m.verdict == Verdict.UNCLEAR
    assert "not trusted" in m.verdict_reasons[0]
    # A requirement with no quote at all is not trusted either.
    m = assess(_posting(description=TEXT, req=Requirements(degree_levels=[PHD])), _profile(), RULES)
    assert m.verdict == Verdict.UNCLEAR


def test_nothing_stated_is_eligible():
    m = assess(_posting(description="Fun job.", req=Requirements()), _profile(), RULES)
    assert m.verdict == Verdict.ELIGIBLE and "states no education" in m.verdict_reasons[0]


def test_graduation_slack():
    req = Requirements(graduation_latest=date(2029, 3, 31), evidence=[_ev("graduation_window", "Rising seniors")])
    p = _posting(description=TEXT, req=req)
    assert assess(p, _profile(), RULES).verdict == Verdict.INELIGIBLE
    assert assess(p, _profile(), EligibilityRules(graduation_window_slack_months=2)).verdict == Verdict.ELIGIBLE


def test_degree_rules():
    req = Requirements(degree_levels=[B], evidence=[_ev("degree_levels", "Bachelor’s")])
    p = _posting(description=TEXT, req=req)
    masters = _profile(Education(level=M, field="CS", status="completed", graduation_date=date(2026, 5, 1)))
    assert assess(p, masters, RULES).verdict == Verdict.ELIGIBLE
    assert assess(p, masters, EligibilityRules(higher_degree_satisfies_lower=False)).verdict == Verdict.INELIGIBLE
    assert assess(p, _profile(), EligibilityRules(in_progress_counts=False)).verdict == Verdict.INELIGIBLE


def test_missing_graduation_date_is_unclear():
    no_date = _profile(Education(level=B, field="CS", status="in_progress"))
    req = Requirements(graduation_latest=date(2029, 6, 30), evidence=[_ev("graduation_window", "Rising seniors")])
    m = assess(_posting(description=TEXT, req=req), no_date, RULES)
    assert m.verdict == Verdict.UNCLEAR and "no graduation date" in m.verdict_reasons[0]


@pytest.mark.parametrize(
    ("majors", "verdict"),
    [
        (["Computer Science"], Verdict.ELIGIBLE),
        (["Statistics", "Data Science"], Verdict.ELIGIBLE),
        (["Mechanical Engineering"], Verdict.UNCLEAR),
        (["Mechanical Engineering or a related field"], Verdict.ELIGIBLE),
    ],
)
def test_majors(majors, verdict):
    req = Requirements(majors=majors, evidence=[_ev("majors", "Computer Science")])
    assert assess(_posting(description=TEXT, req=req), _profile(), RULES).verdict == verdict


def test_quote_found():
    assert quote_found("bachelor's  OR master’s", "Bachelor’s or Master's")
    assert quote_found('"Rising seniors."', TEXT)
    assert not quote_found("", TEXT)
    assert not quote_found("PhD", TEXT)


def test_passes_gate():
    assert passes_gate(Verdict.ELIGIBLE, RULES)
    assert passes_gate(Verdict.UNCLEAR, RULES)
    assert not passes_gate(Verdict.UNCLEAR, EligibilityRules(keep_unclear=False))
    assert not passes_gate(Verdict.INELIGIBLE, RULES)


# --- storage -----------------------------------------------------------------


def test_match_storage_keeps_fit_score(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    db.upsert_posting(conn, _posting(description=TEXT))
    db.set_requirements(conn, "lever:1", Requirements(degree_levels=[B]))
    assert db.get_posting(conn, "lever:1").requirements.degree_levels == [B]

    db.save_match(conn, Match(posting_id="lever:1", verdict=Verdict.ELIGIBLE, fit_score=0.8, fit_reasons=["good"]))
    db.save_match(conn, Match(posting_id="lever:1", verdict=Verdict.UNCLEAR, verdict_reasons=["x"]))
    m = db.get_match(conn, "lever:1")
    assert (m.verdict, m.verdict_reasons, m.fit_score, m.fit_reasons) == (Verdict.UNCLEAR, ["x"], 0.8, ["good"])
    assert list(db.all_matches(conn, Verdict.UNCLEAR)) == ["lever:1"]
    assert db.all_matches(conn, Verdict.ELIGIBLE) == {}


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    s = Settings(profile=_profile())
    save_settings(s, tmp_path / "config.toml")
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    for i, (title, desc) in enumerate(
        [
            ("Software Engineering Intern", "OK-POSTING Bachelor’s"),
            ("PhD Intern, Research", "PHD-POSTING"),
            ("Data Intern", "LATE-POSTING Rising seniors"),
            ("Mystery Intern", None),
        ]
    ):
        db.upsert_posting(conn, _posting(title, desc).model_copy(update={"id": f"lever:{i}", "source_ref": str(i)}))
    conn.close()
    return tmp_path


def test_cli_screen(home, monkeypatch):
    sent: list[str] = []

    def fake_batch(texts, settings, max_concurrency=4, ids=None):
        sent.extend(texts)
        out = []
        for t in texts:
            if "LATE" in t:
                out.append(Requirements(graduation_latest=date(2028, 6, 30), evidence=[_ev("graduation_window", "Rising seniors")]))
            else:
                out.append(Requirements(degree_levels=[B], evidence=[_ev("degree_levels", "Bachelor’s")]))
        return out

    monkeypatch.setattr("autotailor.chains.extract_requirements.extract_requirements_batch", fake_batch)
    runner = CliRunner()
    r = runner.invoke(app, ["screen"])
    assert r.exit_code == 0, r.output
    assert "Screened 4: 1 eligible, 1 unclear, 2 ineligible." in r.output
    assert sorted(sent) == ["LATE-POSTING Rising seniors", "OK-POSTING Bachelor’s"]  # PhD title and no-description skipped

    r = runner.invoke(app, ["jobs", "list", "-v", "eligible"])
    assert "lever:0" in r.output and "lever:2" not in r.output
    r = runner.invoke(app, ["jobs", "show", "lever:2"])
    assert "Eligibility: ineligible" in r.output and "Rising seniors" in r.output

    # A profile change is applied without the model.
    sent.clear()
    s = load_settings(home / "config.toml")
    s.eligibility.graduation_window_slack_months = 12
    save_settings(s, home / "config.toml")
    r = runner.invoke(app, ["screen", "--rejudge"])
    assert "2 eligible" in r.output and not sent

    # Already-read postings are not sent again.
    runner.invoke(app, ["screen"])
    assert not sent


def test_cli_screen_without_chain_uses_titles(home, monkeypatch):
    # Simulate the M2 exercise being unwritten, whatever state the real chain is in.
    def unwritten(*args, **kwargs):
        raise NotImplementedError

    monkeypatch.setattr("autotailor.chains.extract_requirements.extract_requirements_batch", unwritten)
    r = CliRunner().invoke(app, ["screen"])
    assert r.exit_code == 0, r.output
    assert "Using titles only" in r.output
    assert "Screened 4: 0 eligible, 3 unclear, 1 ineligible." in r.output
