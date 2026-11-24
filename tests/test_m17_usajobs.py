"""Federal jobs from the USAJOBS API, and the law/policy/politics catalog fields."""

from __future__ import annotations

import httpx
import pytest
from typer.testing import CliRunner

from autotailor.sources import usajobs


def _item(n: int, title: str = "Student Trainee (Policy Analyst)") -> dict:
    return {
        "MatchedObjectId": str(800000 + n),
        "MatchedObjectDescriptor": {
            "PositionID": f"DOJ-{n}", "PositionTitle": title, "PositionURI": f"https://www.usajobs.gov/job/{800000 + n}",
            "PositionLocationDisplay": "Washington, District of Columbia", "OrganizationName": "Office of Legal Policy",
            "DepartmentName": "Department of Justice", "PublicationStartDate": "2026-11-20T00:00:00.0000",
            "ApplicationCloseDate": "2026-12-05T23:59:59.9970", "QualificationSummary": "Enrolled in a bachelor's program.",
            "PositionSchedule": [{"Name": "Part-time"}], "PositionRemuneration": [{"MinimumRange": "20.00", "MaximumRange": "25.00", "Description": "Per Hour"}],
            "UserArea": {"Details": {"JobSummary": "Support policy research on criminal justice.", "MajorDuties": ["Draft memos", "Track legislation"],
                                     "Education": "Current students only.", "Requirements": "U.S. citizenship required.",
                                     "WhoMayApply": {"Name": "Students"}, "HiringPath": ["student"], "TeleworkEligible": False}},
        },
    }  # fmt: skip


class FakeAPI:
    def __init__(self, total: int, page_size: int = usajobs.PAGE_SIZE) -> None:
        self.total, self.page_size, self.requests = total, page_size, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("Authorization-Key") != "k3y":
            return httpx.Response(401)
        page = int(request.url.params["Page"])
        start = (page - 1) * self.page_size
        items = [_item(n, f"Student Trainee {n}") for n in range(start, min(start + self.page_size, self.total))]
        return httpx.Response(200, json={"SearchResult": {"SearchResultCountAll": self.total, "SearchResultItems": items}})


def test_a_posting_has_what_screening_and_tailoring_need():
    p = usajobs.to_posting(_item(1))
    assert p.id == "usajobs:800001" and p.source.value == "usajobs"
    assert p.company == "Office of Legal Policy (Department of Justice)" and p.location.startswith("Washington")
    assert p.canonical_url == "https://www.usajobs.gov/job/800001" and p.posted_at.year == 2026
    for text in ("Support policy research", "- Draft memos", "U.S. citizenship required", "Who may apply: Students",
                 "Pay: $20.00–$25.00 Per Hour", "Applications close: 2026-12-05"):  # fmt: skip
        assert text in p.description
    assert usajobs.to_posting({"MatchedObjectDescriptor": {}}) is None


def test_search_pages_filters_and_merges_keywords():
    api = FakeAPI(total=3, page_size=2)
    old = usajobs.PAGE_SIZE
    usajobs.PAGE_SIZE = 2
    try:
        with httpx.Client(transport=httpx.MockTransport(api)) as c:
            found = usajobs.search(c, "me@example.com", "k3y", ["policy", "legal"], ["student", "graduates"], days=90)
    finally:
        usajobs.PAGE_SIZE = old
    assert len(found) == 3  # the same 3 postings from both keywords, merged
    r = api.requests[0]
    assert r.headers["User-Agent"] == "me@example.com" and r.url.host == "data.usajobs.gov"
    assert r.url.params["Keyword"] == "policy" and r.url.params["HiringPath"] == "student;graduates"
    assert r.url.params["DatePosted"] == "60"  # the API's maximum
    assert len(api.requests) == 4  # 2 pages per keyword


def test_search_needs_a_key_and_email():
    with httpx.Client(transport=httpx.MockTransport(FakeAPI(1))) as c:
        with pytest.raises(usajobs.USAJobsNotSetUp, match="developer.usajobs.gov"):
            usajobs.search(c, "", "k3y", [], [])
        with pytest.raises(httpx.HTTPStatusError):
            usajobs.search(c, "me@example.com", "wrong", [], [])


def test_fetch_uses_usajobs_even_without_job_boards(tmp_path, monkeypatch):
    from autotailor import db
    from autotailor.cli import app

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    runner = CliRunner()
    runner.invoke(app, ["init"])
    for key, value in (("sources.usajobs", "true"), ("sources.usajobs_email", '"me@example.com"'),
                       ("sources.max_age_days", "0")):  # fmt: skip
        assert runner.invoke(app, ["config", "set", key, value]).exit_code == 0
    api = FakeAPI(total=2)
    monkeypatch.setattr("autotailor.sources.make_client", lambda: httpx.Client(transport=httpx.MockTransport(api)))
    monkeypatch.setenv(usajobs.KEY_ENV, "wrong")
    r = runner.invoke(app, ["fetch"])
    assert r.exit_code == 1 and "API key was refused" in r.output
    monkeypatch.setenv(usajobs.KEY_ENV, "k3y")
    r = runner.invoke(app, ["fetch"])
    assert r.exit_code == 0, r.output
    assert "usajobs  2 received" in r.output and "2 new" in r.output
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    assert db.get_posting(conn, "usajobs:800000").title.startswith("Student Trainee")


def test_key_is_saved_and_reported_but_never_returned(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from autotailor.api import TOKEN_HEADER, api_token, create_app
    from autotailor.cli import app
    from autotailor.paths import get_paths

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.delenv(usajobs.KEY_ENV, raising=False)
    CliRunner().invoke(app, ["init"])
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    assert c.get("/api/setup/status").json()["keys"]["usajobs"] is False
    r = c.put("/api/setup/keys", json={"provider": "usajobs", "key": "abc123"})
    assert r.status_code == 200 and "abc123" not in r.text
    assert "USAJOBS_API_KEY=abc123" in (tmp_path / ".env").read_text()
    assert c.get("/api/setup/status").json()["keys"]["usajobs"] is True
    monkeypatch.delenv(usajobs.KEY_ENV, raising=False)


def test_law_and_policy_fields():
    from autotailor.sources.discover import catalog, match_industries

    assert match_industries("law") == ["legal"]
    assert match_industries("pre-law and public policy") == ["legal", "policy and government"]
    assert "politics and advocacy" in match_industries("political campaigns and civil rights")
    assert "legal" not in match_industries("enterprise SaaS")
    names = {b["name"] for b in catalog() if "legal" in b["industries"]}
    assert {"Harvey", "Everlaw", "Legora", "DISCO"} <= names
    assert "Goodwin" not in names and "Relativity Space" not in names  # namesakes of a law firm / Relativity
