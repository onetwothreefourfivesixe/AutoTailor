"""M12: API routes for the Svelte frontend."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_m4_resume import CV_TEXT, _store, _verbatim
from typer.testing import CliRunner

from autotailor import db, tasks
from autotailor.api import TOKEN_HEADER, api_token, create_app
from autotailor.chains.tailor_graph import TailorResult
from autotailor.cli import app as cli
from autotailor.paths import get_paths


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(cli, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9, location="Denver")
    _store(conn, "lever:b", "ML Intern", 0.7)
    conn.close()
    monkeypatch.setattr("autotailor.tasks.ensure_worker", lambda home, data: True)
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    c.home = tmp_path  # type: ignore[attr-defined]
    return c


def _tailor(client, monkeypatch):
    from test_m5_batch import LoopGraph

    history = [{"attempt": 1, "bullets": ["exp-lab-detector", "proj-ocr"], "errors": []}]
    monkeypatch.setattr("autotailor.chains.tailor_graph.tailor_posting",
                        lambda *a: TailorResult(_verbatim("exp-lab-detector", "proj-ocr"), [], 1, history))  # fmt: skip
    LoopGraph().install(monkeypatch)
    r = CliRunner().invoke(cli, ["tailor"])
    assert r.exit_code == 0, r.output


def test_jobs_list_filters_and_total(client):
    r = client.get("/api/jobs")
    assert r.headers["X-Total-Count"] == "2" and [j["id"] for j in r.json()] == ["lever:a", "lever:b"]
    assert [j["id"] for j in client.get("/api/jobs?min_fit=0.8").json()] == ["lever:a"]
    assert client.get("/api/jobs?q=denver").json()[0]["id"] == "lever:a"
    assert [j["id"] for j in client.get("/api/jobs?sort=company&limit=1&offset=1").json()] == ["lever:b"]


def test_bulk_actions(client):
    assert client.post("/api/jobs/bulk", json={"ids": ["lever:b"], "action": "ineligible"}).json() == {"updated": 1}
    assert client.get("/api/jobs?verdict=ineligible").json()[0]["overridden"] is True
    client.post("/api/jobs/bulk", json={"ids": ["lever:a"], "action": "hide"})
    assert [j["id"] for j in client.get("/api/jobs").json()] == ["lever:b"]
    assert [j["id"] for j in client.get("/api/jobs?hidden=true").json()] == ["lever:a"]
    t = client.post("/api/jobs/bulk", json={"ids": ["lever:b", "nope:1"], "action": "tailor"}).json()["task"]
    s = client.post("/api/jobs/bulk", json={"ids": ["lever:b"], "action": "score"}).json()["task"]
    assert s["args_json"] == '["rank", "lever:b"]'
    assert t["args_json"] == '["tailor", "lever:b", "--force"]'  # picked by hand: redo even if tailored
    assert client.post("/api/jobs/bulk", json={"ids": ["nope:1"], "action": "hide"}).status_code == 422


def test_job_detail_and_resume(client, monkeypatch):
    _tailor(client, monkeypatch)
    j = client.get("/api/jobs/lever:a").json()
    assert j["posting"]["title"] == "SWE Intern" and j["has_resume"] and j["cited_bullets"][0]["id"] == "exp-lab-detector"
    assert j["cited_bullets"][0]["text"].startswith("Trained a YOLO")
    r = client.get("/api/jobs/lever:a/resume").json()
    assert r["pages"] == 1 and r["pdf"].endswith(".pdf")
    assert [b["source_id"] for b in r["bullets"]][:2] == ["exp-lab-detector", "proj-ocr"]  # the model's draft first
    assert set(r["added_to_fill"]) == {b["source_id"] for b in r["bullets"][2:]}  # then bullets added to fill the page
    assert r["history"][0]["attempt"] == 1 and r["bullets"][0]["changed"] is False
    assert client.get("/api/jobs/lever:zzz/resume").status_code == 404


def test_applications_and_shortlist(client, monkeypatch):
    _tailor(client, monkeypatch)
    a = client.put("/api/applications/lever:a", json={"status": "applied", "notes": "via site"}).json()
    assert a["run_id"] == 1 and [e["status"] for e in a["events"]] == ["applied"]
    a = client.put("/api/applications/lever:a", json={"status": "interview"}).json()
    assert a["notes"] == "via site" and [e["status"] for e in a["events"]] == ["applied", "interview"]
    assert client.put("/api/applications/lever:a", json={"status": "ghosted"}).status_code == 422
    apps = client.get("/api/applications").json()
    assert apps[0]["company"] == "Acme" and apps[0]["status"] == "interview"
    assert "lever:a" not in [s["id"] for s in client.get("/api/overview").json()["shortlist"]]
    assert client.get("/api/jobs?applied=true").json()[0]["application"] == "interview"
    client.delete("/api/applications/lever:a")
    assert client.get("/api/applications").json() == []


def test_questions_edit_delete_suggest(client):
    q = client.post("/api/questions", json={"posting_id": "lever:a", "text": "Why us?"}).json()
    t = client.post(f"/api/questions/{q['id']}/suggest").json()
    assert t["args_json"] == f'["answers", "run", "{q["id"]}", "--no-ask", "--redo"]'
    e = client.patch(f"/api/questions/{q['id']}", json={"answer": "Because robots."}).json()
    assert e["answer"] == "Because robots." and e["status"] == "drafted"
    assert any((client.home / "data" / "outputs" / "answers").glob("*.md"))
    client.delete(f"/api/questions/{q['id']}")
    assert client.get("/api/questions").json() == []


def test_cv_yaml_usage_and_preview(client, monkeypatch):
    _tailor(client, monkeypatch)
    usage = client.get("/api/cv/usage").json()
    assert usage["exp-lab-detector"][0]["run_id"] == 1
    text = client.get("/api/cv/yaml").text
    assert "exp-lab-detector" in text
    r = client.put("/api/cv/yaml", json={"text": text.replace("id: exp-lab-detector", "id: exp-lab-renamed")})
    assert r.status_code == 409 and "exp-lab-detector" in r.json()["detail"]["uses"]
    assert client.put("/api/cv/yaml", json={"text": "contact: ["}).status_code == 422
    png = client.post("/api/cv/preview", json={"template": "typst"})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG" and png.headers["X-Pages"] == "1"
    cv = client.get("/api/cv").json()
    cv["contact"]["name"] = "Draft Name"
    assert client.post("/api/cv/preview", json={"cv": cv, "template": "typst"}).status_code == 200


def test_templates(client):
    t = client.get("/api/templates").json()
    assert [x["name"] for x in t["pdf"]][:3] == ["jake", "jake-compact", "typst"] and t["current"]["pdf"] == "jake"
    assert client.post("/api/templates", files={"file": ("mine.tex", b"\\documentclass{article}")}).json() == {"name": "mine.tex"}
    assert client.post("/api/templates", files={"file": ("../evil.sh", b"x")}).status_code == 422
    assert "mine.tex" in [x["name"] for x in client.get("/api/templates").json()["pdf"]]
    client.delete("/api/templates/mine.tex")
    assert "MIT License" in client.get("/api/templates/builtin/jake").text


def test_models_and_comparisons(client, monkeypatch):
    class R:
        def json(self):
            return {"models": [{"name": "qwen3-vl:4b-instruct", "size": 3_300_000_000, "details": {"parameter_size": "4B"}}]}

    monkeypatch.setattr("httpx.get", lambda *a, **k: R())
    m = client.get("/api/models").json()
    assert {s["step"] for s in m["steps"]} >= {"extract", "tailor", "embeddings"} and m["pulled"][0]["parameters"] == "4B"
    assert client.get("/api/comparisons").json() == []
    assert client.get("/api/comparisons/nope").status_code == 404


def test_events_stream_first_snapshot(client, monkeypatch):
    import autotailor.api_more as more

    monkeypatch.setattr(more, "EVENT_POLL", 0.05)
    conn = db.connect(client.home / "data" / "autotailor.db")
    tasks.enqueue(conn, "screen")
    with client.stream("GET", "/api/events") as r:
        seen, text = set(), ""
        for line in r.iter_lines():
            text += line + "\n"
            if line.startswith("event: "):
                seen.add(line[7:])
            if {"tasks", "needs", "runs", "postings"} <= seen and line.startswith("data: "):
                break
    assert '"kind": "screen"' in text and '"count": 2' in text


def test_edit_and_restore_a_tailored_resume(client, monkeypatch):
    from test_m4_resume import SRC

    _tailor(client, monkeypatch)
    before = client.get("/api/jobs/lever:a/resume").json()
    assert before["edited"] is False and any(b["id"] == "proj-budget" for b in before["cv_bullets"])
    assert before["cv_skills"] and client.get("/api/postings/lever:a/resume.png?scale=3").status_code == 200

    edit = {"bullets": [{"source_id": "proj-budget", "text": SRC["proj-budget"].text},
                        {"source_id": "exp-lab-detector", "text": SRC["exp-lab-detector"].text + " for 12 robots"}],
            "skills": ["pytorch"]}  # fmt: skip
    r = client.put("/api/jobs/lever:a/resume", json=edit).json()
    assert r["pages"] == 1 and any("12" in w for w in r["warnings"])  # a number the CV doesn't have: a note, not an error
    after = client.get("/api/jobs/lever:a/resume").json()
    assert after["edited"] and [b["source_id"] for b in after["bullets"]] == ["proj-budget", "exp-lab-detector"]
    assert after["skills"] == ["PyTorch"]

    assert client.put("/api/jobs/lever:a/resume", json={**edit, "skills": ["Rust"]}).status_code == 422
    assert client.put("/api/jobs/lever:a/resume", json={"bullets": [{"source_id": "nope", "text": "x"}]}).status_code == 422
    dup = {"bullets": [edit["bullets"][0], edit["bullets"][0]]}
    assert client.put("/api/jobs/lever:a/resume", json=dup).status_code == 422

    assert client.post("/api/jobs/lever:a/resume/restore").status_code == 200
    restored = client.get("/api/jobs/lever:a/resume").json()
    assert not restored["edited"] and [b["source_id"] for b in restored["bullets"]] == [b["source_id"] for b in before["bullets"]]
    assert client.post("/api/jobs/lever:a/resume/restore").status_code == 404


def test_low_fit_and_old_postings_are_deleted_but_protected_ones_stay(client, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from autotailor import matching
    from autotailor.config import Settings
    conn = db.connect(client.home / "data" / "autotailor.db")
    _store(conn, "lever:low", "Accounting Intern", 0.3)
    _store(conn, "lever:low-applied", "Sales Intern", 0.2)
    db.set_application(conn, "lever:low-applied", "applied")
    _store(conn, "lever:old", "Old Intern", 0.95)
    long_ago = (datetime.now(UTC) - timedelta(days=60)).isoformat(timespec="seconds")
    conn.execute("UPDATE postings SET posted_at = ? WHERE id = 'lever:old'", (long_ago,))
    conn.commit()

    s = Settings()
    s.run.cut_action = "hide"  # hidden, not deleted: out of the list, shown in the Cut view
    assert matching.prune(conn, s.model_copy(update={"sources": s.sources.model_copy(update={"delete_expired": False})})) == {"low_fit": 0, "too_old": 0}
    r = client.get("/api/jobs")
    ids = [j["id"] for j in r.json()]
    assert "lever:low" not in ids and r.headers["X-Cut-Count"] == "2"
    assert {j["id"] for j in client.get("/api/jobs?cut=true").json()} == {"lever:low", "lever:low-applied"}

    counts = matching.prune(conn, Settings())  # the defaults: delete low fit and too old
    assert counts == {"low_fit": 1, "too_old": 1}
    assert db.get_posting(conn, "lever:low") is None and db.get_posting(conn, "lever:old") is None
    assert db.get_posting(conn, "lever:low-applied") is not None  # applied: never deleted
    assert db.get_posting(conn, "lever:a") is not None  # 0.9 fit, recent
    assert conn.execute("SELECT COUNT(*) FROM embeddings WHERE key = 'posting:lever:low'").fetchone()[0] == 0

    deleted = {d["id"]: d for d in client.get("/api/cut").json()}
    assert deleted["lever:low"]["reason"] == "low_fit" and deleted["lever:old"]["reason"] == "too_old"
    assert client.get("/api/jobs").headers["X-Cut-Count"] == "3"  # 2 deleted + the applied one, hidden
    from autotailor.models import JobPosting, Source

    again = JobPosting(id="lever:low", source=Source.LEVER, source_ref="low", company="Acme", title="Accounting Intern")
    assert db.was_cut(conn, again)
    assert client.delete("/api/cut/lever:low").status_code == 200 and not db.was_cut(conn, again)
    assert client.delete("/api/cut/lever:low").status_code == 404
    db.compact(conn)
    conn.close()


def test_final_editor_edits_the_whole_page(client, monkeypatch):
    _tailor(client, monkeypatch)
    view = client.get("/api/jobs/lever:a/resume").json()
    page = view["layout"]
    assert not view["layout_edited"] and page["name"] and page["sections"][0]["key"] == "education"
    work = next(s for s in page["sections"] if s["key"] == "experience")

    # Everything on the page is editable: header, section titles, entries, bullets (own words), skills.
    page["name"] = "Sam Q. Example"
    page["contact_items"].append({"text": "sam.dev", "url": "https://sam.dev"})
    work["title"] = "Research"
    work["items"][0]["dates"] = "Summer 2026"
    work["items"][0]["bullets"] = ["Wrote a brand-new bullet about 7 robots", work["items"][0]["bullets"][0], "   "]
    page["sections"].append({"key": "custom", "title": "Interests", "items": [{"title": "Chess, climbing", "bullets": []}]})
    page["skills"] = [{"name": "Languages", "items": "Python, C++"}]

    r = client.post("/api/jobs/lever:a/resume/preview", json=page)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.headers["X-Pages"] == "1"
    assert not client.get("/api/jobs/lever:a/resume").json()["layout_edited"]  # a preview saves nothing

    saved = client.put("/api/jobs/lever:a/resume/layout", json=page).json()
    assert saved["pages"] == 1 and any("7" in w for w in saved["warnings"])  # a new number: a note
    after = client.get("/api/jobs/lever:a/resume").json()
    assert after["edited"] and after["layout_edited"] and after["layout"]["name"] == "Sam Q. Example"
    work2 = next(s for s in after["layout"]["sections"] if s["key"] == "experience")
    assert work2["title"] == "Research" and work2["items"][0]["dates"] == "Summer 2026"
    assert work2["items"][0]["bullets"][0].startswith("Wrote a brand-new bullet") and len(work2["items"][0]["bullets"]) == 2
    assert after["layout"]["sections"][-1]["title"] == "Interests"

    bad = {**page, "sections": []}
    assert client.put("/api/jobs/lever:a/resume/layout", json=bad).status_code == 422

    assert client.post("/api/jobs/lever:a/resume/restore").status_code == 200
    back = client.get("/api/jobs/lever:a/resume").json()
    assert not back["edited"] and back["layout"]["name"] != "Sam Q. Example"


def test_normalize_places_the_corners():
    from autotailor.resume.layout import EditableLayout, editable, normalize

    e = EditableLayout(name=" Sam ", contact_items=[{"text": "a@b.c", "url": "mailto:a@b.c"}, {"text": " "}], sections=[
        {"key": "experience", "title": "Work Experience", "items": [
            {"title": "Intern", "organization": "Acme", "location": "Denver", "dates": "2026", "bullets": ["Did a thing"]}]},
        {"key": "projects", "title": "Things I Built", "items": [{"title": "Bot", "dates": "2025", "stack": "Python"}, {}]},
    ], skills=[{"name": "Languages", "items": "Python"}, {"name": "Empty", "items": " "}])  # fmt: skip
    out = normalize(e)
    job, proj = out["sections"][0]["items"][0], out["sections"][1]["items"][0]
    assert (job["left1"], job["right1"], job["left2"], job["right2"]) == ("Acme", "Denver", "Intern", "2026")
    assert (proj["left1"], proj["right1"], proj["stack_on"]) == ("Bot", "2025", "left1")
    assert len(out["sections"][1]["items"]) == 1 and out["contact"] == "a@b.c" and len(out["skills"]) == 1
    assert out["sections"][0]["renamed"] is False and out["sections"][1]["renamed"] is True
    assert normalize(EditableLayout(**editable(out))) == out  # round trip


def test_jobs_sort_by_any_column_both_ways_with_blanks_last(client):
    from autotailor.api_more import sort_rows

    def row(i, fit=None, location=None, posted=None, app=None, tailored=False, verdict="eligible"):
        return {"id": i, "company": f"C{i}", "title": f"T{i}", "fit": fit, "prescore": None, "location": location,
                "posted_at": posted, "application": app, "tailored": tailored, "status": "resolved",
                "verdict": verdict, "source": "lever"}  # fmt: skip

    rows = [row("a", 0.5, "Denver", "2026-11-01"), row("b", None, None, None, tailored=True, verdict="unclear"),
            row("c", 0.9, "Austin", "2026-11-20", app="interview")]  # fmt: skip
    ids = lambda rs: [r["id"] for r in rs]  # noqa: E731
    assert ids(sort_rows(rows, "fit")) == ["c", "a", "b"]  # best first, unscored last
    assert ids(sort_rows(rows, "fit", "asc")) == ["a", "c", "b"]  # unscored still last
    assert ids(sort_rows(rows, "location")) == ["c", "a", "b"] and ids(sort_rows(rows, "location", "desc")) == ["a", "c", "b"]
    assert ids(sort_rows(rows, "posted")) == ["c", "a", "b"]
    assert ids(sort_rows(rows, "progress")) == ["c", "b", "a"]  # interview, resume ready, new
    assert ids(sort_rows(rows, "verdict")) == ["c", "a", "b"]  # eligible (best fit first), then unclear
    assert ids(sort_rows(rows, "nonsense")) == ids(sort_rows(rows, "fit"))

    r = client.get("/api/jobs?sort=company&dir=desc")
    assert [j["company"] for j in r.json()] == sorted([j["company"] for j in r.json()], reverse=True)
