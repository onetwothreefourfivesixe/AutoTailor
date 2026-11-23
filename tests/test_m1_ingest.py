import json
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from typer.testing import CliRunner

from autotailor import db
from autotailor.chains.extract_posting import PostingFields
from autotailor.cli import app
from autotailor.dedupe import (
    dedupe_key,
    locations_compatible,
    normalize_company,
    normalize_url,
    primary_city,
)
from autotailor.ingest import ingest, title_matches
from autotailor.models import JobPosting, PostingStatus, Source
from autotailor.sources import is_no_fetch
from autotailor.sources.ats import (
    JobRef,
    fetch_ashby,
    fetch_greenhouse,
    fetch_job,
    fetch_lever,
    parse_job_url,
)
from autotailor.sources.paste import NoFetchError, from_text, from_url
from autotailor.text import html_to_text

LEVER_ID = "6ed76ce8-4156-4b60-b120-403538bd66cd"
ASHBY_ID = "8fb1615c-34bf-47c4-a1d1-b7b2f836bbd3"

# Trimmed copies of real response shapes (2026-11-17), with invented content.
GREENHOUSE_JOBS = {
    "jobs": [
        {
            "id": 111,
            "title": "Software Engineering Intern (Summer 2027) ",
            "company_name": "Acme",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/111?gh_jid=111",
            "location": {"name": "San Francisco, CA • New York, NY"},
            "first_published": "2026-11-01T10:00:00-05:00",
            "updated_at": "2026-11-10T10:00:00-05:00",
            "content": "&lt;p&gt;Build things.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
        },
        {
            "id": 222,
            "title": "Account Executive",
            "company_name": "Acme",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/222",
            "location": {"name": "Remote"},
            "first_published": None,
            "updated_at": "2026-11-02T10:00:00Z",
            "content": "&lt;p&gt;Sell things.&lt;/p&gt;",
        },
    ],
    "meta": {"total": 2},
}
LEVER_JOBS = [
    {
        "id": LEVER_ID,
        "text": "Machine Learning Intern",
        "categories": {"location": "Denver, CO", "allLocations": ["Denver, CO"]},
        "hostedUrl": f"https://jobs.lever.co/some-co/{LEVER_ID}",
        "createdAt": 1786469891368,
        "descriptionPlain": "About us.\n",
        "lists": [{"text": "What you'll do", "content": "<li>Train models</li><li>Ship</li>"}],
        "additionalPlain": "Equal opportunity employer.",
    }
]
ASHBY_JOBS = {
    "apiVersion": "1",
    "jobs": [
        {
            "id": ASHBY_ID,
            "title": "Research Intern",
            "location": "San Francisco",
            "isRemote": True,
            "isListed": True,
            "jobUrl": f"https://jobs.ashbyhq.com/labco/{ASHBY_ID}",
            "publishedAt": "2026-11-05T02:20:02.702+00:00",
            "descriptionPlain": "Do research.",
        },
        {"id": "x", "title": "Hidden", "isListed": False, "jobUrl": "u", "descriptionPlain": "x"},
    ],
}


def _handler(request: httpx.Request) -> httpx.Response:
    host, path = request.url.host, request.url.path
    if host == "boards-api.greenhouse.io":
        if path == "/v1/boards/acme/jobs":
            assert request.url.params["content"] == "true"
            return httpx.Response(200, json=GREENHOUSE_JOBS)
        if path == "/v1/boards/acme/jobs/111":
            return httpx.Response(200, json=GREENHOUSE_JOBS["jobs"][0])
    if host == "api.lever.co":
        if path == "/v0/postings/some-co":
            return httpx.Response(200, json=LEVER_JOBS)
        if path == f"/v0/postings/some-co/{LEVER_ID}":
            return httpx.Response(200, json=LEVER_JOBS[0])
    if host == "api.ashbyhq.com" and path == "/posting-api/job-board/labco":
        return httpx.Response(200, json=ASHBY_JOBS)
    if host == "careers.example.com":
        page = "<html><head><style>x{}</style></head><body><h1>Intern</h1><p>" + "Words. " * 60
        return httpx.Response(200, text=page + "</p></body></html>")
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def client():
    with httpx.Client(transport=httpx.MockTransport(_handler)) as c:
        yield c


def _posting(**kw) -> JobPosting:
    base = dict(id="lever:1", source=Source.LEVER, source_ref="1", company="Acme", title="SWE Intern")
    return JobPosting(**{**base, **kw})


# --- text --------------------------------------------------------------------


def test_html_to_text_keeps_structure_and_drops_scripts():
    html = "<script>bad()</script><h2>Role</h2><p>Hello&nbsp;<b>world</b></p><ul><li>A</li><li>B</li></ul>"
    assert html_to_text(html) == "Role\n\nHello world\n\n- A\n\n- B"


# --- job boards --------------------------------------------------------------


def test_greenhouse(client):
    jobs = fetch_greenhouse(client, "acme")
    p = jobs[0]
    assert p.id == "greenhouse:111" and p.company == "Acme"
    assert p.title == "Software Engineering Intern (Summer 2027)"
    assert p.description == "Build things.\n\n- Python"
    assert p.status == PostingStatus.RESOLVED
    assert p.posted_at.year == 2026 and p.posted_at.month == 11
    assert jobs[1].posted_at is not None  # falls back to updated_at


def test_lever(client):
    (p,) = fetch_lever(client, "some-co")
    assert p.id == f"lever:{LEVER_ID}" and p.company == "Some Co"
    assert "What you'll do\n- Train models" in p.description
    assert p.description.endswith("Equal opportunity employer.")
    assert p.posted_at == datetime.fromtimestamp(1786469891.368, tz=UTC)


def test_ashby_skips_unlisted_and_marks_remote(client):
    (p,) = fetch_ashby(client, "labco")
    assert p.location == "San Francisco (remote OK)"
    assert p.company == "Labco"


@pytest.mark.parametrize(
    ("url", "ref"),
    [
        ("https://boards.greenhouse.io/acme/jobs/111?gh_jid=111", JobRef(Source.GREENHOUSE, "acme", "111")),
        ("job-boards.greenhouse.io/acme/jobs/111", JobRef(Source.GREENHOUSE, "acme", "111")),
        (f"https://jobs.lever.co/some-co/{LEVER_ID}/apply", JobRef(Source.LEVER, "some-co", LEVER_ID)),
        (f"https://jobs.ashbyhq.com/labco/{ASHBY_ID}", JobRef(Source.ASHBY, "labco", ASHBY_ID)),
        ("https://example.com/jobs/1", None),
        ("https://boards.greenhouse.io/acme", None),
    ],
)
def test_parse_job_url(url, ref):
    assert parse_job_url(url) == ref


def test_fetch_single_jobs(client):
    assert fetch_job(client, JobRef(Source.GREENHOUSE, "acme", "111")).id == "greenhouse:111"
    assert fetch_job(client, JobRef(Source.LEVER, "some-co", LEVER_ID)).title == "Machine Learning Intern"
    assert fetch_job(client, JobRef(Source.ASHBY, "labco", ASHBY_ID)).title == "Research Intern"
    with pytest.raises(LookupError):
        fetch_job(client, JobRef(Source.ASHBY, "labco", "missing"))


# --- paste / URL -------------------------------------------------------------


def _fake_extract(text: str) -> PostingFields:
    return PostingFields(company="Northwind Robotics", title="SWE Intern", location="Pittsburgh, PA")


def test_from_text_uses_extractor_and_stable_id():
    text = "Northwind is hiring. " * 20
    a = from_text(text, _fake_extract)
    b = from_text("  " + text.upper() + "\n", _fake_extract)  # whitespace/case-insensitive ID
    assert a.id == b.id and a.id.startswith("paste:")
    assert a.company == "Northwind Robotics" and a.description == text.strip()
    with pytest.raises(ValueError, match="too short"):
        from_text("hi", _fake_extract)


def test_from_url_routes_job_boards_without_model(client):
    def no_model(text):
        raise AssertionError("job board URLs must not call the model")

    p = from_url(client, f"jobs.lever.co/some-co/{LEVER_ID}", no_model)
    assert p.source == Source.LEVER


def test_from_url_generic_page_uses_model(client):
    p = from_url(client, "https://careers.example.com/role", _fake_extract)
    assert p.source == Source.PASTE and p.canonical_url == "https://careers.example.com/role"
    assert "Words." in p.description and "x{}" not in p.description


def test_from_url_refuses_no_fetch_sites(client):
    assert is_no_fetch("www.linkedin.com") and is_no_fetch("app.joinrunway.io")
    assert not is_no_fetch("notlinkedin.com.example.org")
    with pytest.raises(NoFetchError):
        from_url(client, "https://www.linkedin.com/jobs/view/123", _fake_extract)


# --- dedupe ------------------------------------------------------------------


def test_normalizers():
    assert normalize_company("Publicis Groupe Holdings B.V.") == "publicis groupe"
    assert normalize_company("Acme, Inc.") == normalize_company("ACME")
    assert dedupe_key("Acme Inc", "SWE  Intern (Summer 2027)") == "acme|swe intern summer 2027"
    assert normalize_url("https://www.Boards.greenhouse.io/acme/jobs/1/?x=1#y") == "boards.greenhouse.io/acme/jobs/1"
    assert primary_city("San Francisco, CA • New York, NY") == "san francisco"
    assert locations_compatible("San Francisco, California", "San Francisco, CA")
    assert locations_compatible(None, "Denver, CO")
    assert not locations_compatible("Denver, CO", "Austin, TX")


def test_ingest_title_filter_and_duplicates(tmp_path):
    conn = db.connect(tmp_path / "a.db")
    a = _posting(id="lever:1", source_ref="1", location="San Francisco, CA")
    same_role_other_source = _posting(
        id="greenhouse:9", source=Source.GREENHOUSE, source_ref="9",
        company="Acme, Inc.", title="SWE intern", location="San Francisco, California",
        description="Full text", canonical_url="https://boards.greenhouse.io/acme/jobs/9",
    )  # fmt: skip
    other_city = _posting(id="lever:2", source_ref="2", location="Austin, TX")
    sales = _posting(id="lever:3", source_ref="3", title="Internal Sales Lead")

    r = ingest(conn, [a, same_role_other_source, other_city, sales], include=["intern"])
    assert r.filtered_out == 1  # "Internal" is not "intern"
    assert r.new == ["lever:1", "lever:2"]
    assert r.merged == 1
    merged = db.get_posting(conn, "lever:1")
    assert merged.description == "Full text" and merged.status == PostingStatus.RESOLVED

    r = ingest(conn, [a, same_role_other_source])
    assert r.refreshed == 1 and r.duplicates == 1 and not r.new
    # The URL now points at lever:1, so a re-import under another ID is a duplicate.
    assert db.find_duplicate(conn, _posting(id="ashby:z", source=Source.ASHBY, source_ref="z",
                                             title="Other", canonical_url="boards.greenhouse.io/acme/jobs/9")) == "lever:1"  # fmt: skip


def test_title_matches():
    assert title_matches("Software Engineering Intern", ["intern"], [])
    assert title_matches("Summer Interns 2027", ["intern"], [])
    assert not title_matches("International Tax Manager", ["intern"], [])
    assert not title_matches("MBA Intern", ["intern"], ["mba"])
    assert title_matches("Anything", [], [])


def test_migration_from_v1_backfills_keys(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.executescript(db._SCHEMA_V1)
    raw.execute(
        "INSERT INTO postings (id, source, source_ref, company, title, status, first_seen_at, updated_at,"
        " canonical_url) VALUES ('lever:1','lever','1','Acme Inc','SWE Intern','unresolved','t','t',"
        " 'https://jobs.lever.co/acme/1/')"
    )
    raw.execute("PRAGMA user_version = 1")
    raw.commit()
    raw.close()

    conn = db.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = conn.execute("SELECT url_key, dedupe_key FROM postings").fetchone()
    assert tuple(row) == ("jobs.lever.co/acme/1", "acme|swe intern")


# --- CLI ---------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    monkeypatch.setattr(
        "autotailor.sources.make_client",
        lambda: httpx.Client(transport=httpx.MockTransport(_handler)),
    )
    assert CliRunner().invoke(app, ["init"]).exit_code == 0
    return tmp_path


def test_cli_fetch_and_list(home):
    runner = CliRunner()
    r = runner.invoke(app, ["fetch"])
    assert r.exit_code == 1 and "No boards configured" in r.output

    runner.invoke(app, ["config", "set", "sources.greenhouse", '["acme", "nope"]'])
    runner.invoke(app, ["config", "set", "sources.title_include", '["intern"]'])
    r = runner.invoke(app, ["fetch"])
    assert r.exit_code == 0, r.output
    assert "greenhouse:acme  2 received, 1 filtered out by title, 1 new" in r.output
    assert "no board named 'nope'" in r.output

    r = runner.invoke(app, ["fetch", "-b", "ashby:labco"])
    assert "1 new" in r.output

    r = runner.invoke(app, ["jobs", "list"])
    assert "greenhouse:111" in r.output and f"ashby:{ASHBY_ID}" in r.output
    r = runner.invoke(app, ["jobs", "show", "greenhouse:111"])
    assert "Build things." in r.output

    assert runner.invoke(app, ["fetch", "-b", "monster:x"]).exit_code == 1


def test_cli_add(home, monkeypatch):
    runner = CliRunner()
    r = runner.invoke(app, ["add", f"https://jobs.lever.co/some-co/{LEVER_ID}"])
    assert r.exit_code == 0 and f"Added lever:{LEVER_ID}" in r.output

    r = runner.invoke(app, ["add", "https://www.linkedin.com/jobs/view/1"])
    assert r.exit_code == 1 and "does not allow automated access" in r.output

    posting = home / "posting.txt"
    posting.write_text("Northwind Robotics is hiring a SWE intern in Pittsburgh. " * 10)

    monkeypatch.setattr(
        "autotailor.chains.extract_posting.extract_posting_fields",
        lambda text, settings: _fake_extract(text),
    )
    r = runner.invoke(app, ["add", "--file", str(posting)])
    assert r.exit_code == 0 and "Added paste:" in r.output
    r = runner.invoke(app, ["add"], input=posting.read_text())
    assert r.exit_code == 0 and "Refreshed paste:" in r.output
