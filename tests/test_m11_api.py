"""M11: the local HTTP API."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_m4_resume import CV_TEXT, _store
from typer.testing import CliRunner

from autotailor import db, tasks
from autotailor.api import TOKEN_HEADER, api_token, create_app
from autotailor.chains.extract_posting import PostingFields
from autotailor.cli import app as cli
from autotailor.models import Match, Verdict
from autotailor.paths import get_paths

FIXTURE = Path(__file__).parent / "fixtures" / "runway_digest.eml"
POSTING = "Northwind Robotics is hiring a Perception Intern in Pittsburgh, PA. You will train detection models. " * 4


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(cli, ["init"])
    (tmp_path / "cv" / "master.yaml").write_text(CV_TEXT)
    conn = db.connect(tmp_path / "data" / "autotailor.db")
    _store(conn, "lever:a", "SWE Intern", 0.9)
    _store(conn, "lever:b", "ML Intern", 0.7)
    conn.close()
    monkeypatch.setattr("autotailor.tasks.ensure_worker", lambda home, data: True)
    monkeypatch.setattr("autotailor.chains.extract_posting.extract_posting_fields",
                        lambda text, settings: PostingFields(company="Northwind Robotics", title="Perception Intern",
                                                             location="Pittsburgh, PA"))  # fmt: skip
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    c.home = tmp_path  # type: ignore[attr-defined]
    return c


def test_access_rules(client):
    assert TestClient(client.app).get("/api/health").json() == {"ok": True}  # no token needed
    assert TestClient(client.app).get("/api/postings").status_code == 401
    assert TestClient(client.app).get("/api/postings", headers={TOKEN_HEADER: "wrong"}).status_code == 401
    r = client.get("/api/postings", headers={"host": "evil.example.com"})
    assert r.status_code == 403  # DNS rebinding: a site pointing its own name at 127.0.0.1
    assert client.get("/api/postings").status_code == 200
    assert (client.home / "data" / "api_token").stat().st_mode & 0o077 == 0  # owner-only


def test_postings_detail_and_override(client):
    rows = client.get("/api/postings").json()
    assert [r["id"] for r in rows] == ["lever:a", "lever:b"] and rows[0]["fit"] == 0.9
    assert client.get("/api/postings?q=ml").json()[0]["id"] == "lever:b"
    d = client.get("/api/postings/lever:a").json()
    assert d["posting"]["title"] == "SWE Intern" and d["match"]["verdict"] == "eligible"
    assert client.get("/api/postings/nope:1").status_code == 404
    m = client.put("/api/postings/lever:a/override", json={"verdict": "ineligible", "note": "not my field"}).json()
    assert m["verdict"] == "ineligible" and m["original_verdict"] == "eligible"
    assert client.get("/api/postings?verdict=ineligible").json()[0]["overridden"] is True
    client.delete("/api/postings/lever:a/override")
    assert client.get("/api/postings/lever:a").json()["match"]["verdict"] == "eligible"


def test_overview_and_needs(client):
    conn = db.connect(client.home / "data" / "autotailor.db")
    db.save_match(conn, Match(posting_id="lever:b", verdict=Verdict.UNCLEAR))
    o = client.get("/api/overview").json()
    assert o["counts"]["postings"] == 2 and o["counts"]["unclear"] == 1 and "tailor" in o["models"]
    assert [n["kind"] for n in client.get("/api/needs").json()] == ["unclear"]


def test_tasks(client):
    t = client.post("/api/tasks", json={"command": "autotailor rank --rescore"}).json()
    assert t["kind"] == "cli" and t["args_json"] == '["rank", "--rescore"]' and t["status"] == "queued"
    assert client.post("/api/tasks", json={"command": "ui"}).status_code == 422
    assert client.post("/api/tasks", json={"kind": "nope"}).status_code == 422
    assert client.post(f"/api/tasks/{t['id']}/cancel").json()["status"] == "cancelled"
    assert [x["id"] for x in client.get("/api/tasks").json()] == [t["id"]]


def test_task_events_stream(client):
    conn = db.connect(client.home / "data" / "autotailor.db")
    tid = tasks.enqueue(conn, "cli", ["runs"])
    tasks.worker_loop(client.home, once=True)
    body = client.get(f"/api/tasks/{tid}/events").text
    assert "event: progress" in body and '"status": "done"' in body and "No runs yet" in body


def test_files_stay_inside_outputs(client):
    out = client.home / "data" / "outputs" / "x"
    out.mkdir(parents=True)
    (out / "a.md").write_text("hello")
    assert client.get("/api/files/x/a.md").text == "hello"
    assert client.get("/api/files/../autotailor.db").status_code == 404
    assert client.get("/api/files/%2e%2e/api_token").status_code == 404


def test_intake_preview_and_save(client):
    r = client.post("/api/intake/preview", data={"text": POSTING, "urls": "https://www.linkedin.com/jobs/view/1"},
                    files=[("files", ("digest.eml", FIXTURE.read_bytes(), "message/rfc822"))]).json()  # fmt: skip
    kinds = [c["kind"] for c in r["candidates"]]
    assert kinds == ["error", "posting", "email"] and len(r["candidates"][2]["roles"]) == 3
    saved = client.post("/api/intake/save", json={"batch": r["batch"], "keep": [0, 1, 2],
                                                   "edits": {"1": {"title": "Perception Intern (Summer)"}}}).json()  # fmt: skip
    assert len(saved["new"]) == 4 and saved["emails"] == 1
    titles = [p["title"] for p in client.get("/api/postings").json()]
    assert "Perception Intern (Summer)" in titles
    assert client.post("/api/intake/save", json={"batch": r["batch"], "keep": [1]}).status_code == 404  # used up


def test_settings_and_cv(client):
    s = client.get("/api/settings").json()
    s["run"]["jobs_per_run"] = 0
    assert client.put("/api/settings", json=s).status_code == 422
    s["run"]["jobs_per_run"] = 4
    assert client.put("/api/settings", json=s).json()["run"]["jobs_per_run"] == 4
    cv = client.get("/api/cv").json()
    cv["projects"].append({"title": "Chess Engine", "bullets": [{"text": "Wrote a chess engine"}]})
    new = client.put("/api/cv", json={"cv": cv}).json()
    assert new["projects"][-1]["id"] == "proj-chess-engine"
    bad = dict(new, experience=[{**new["experience"][0], "id": new["projects"][0]["id"]}])
    assert client.put("/api/cv", json={"cv": bad}).status_code == 422


def test_questions_and_runway_go_through_tasks(client):
    q = client.post("/api/questions", json={"posting_id": "lever:a", "text": "Why us? (120 words)"}).json()
    assert q["word_limit"] == 120
    assert client.post(f"/api/questions/{q['id']}/decision", json={"draft": True}).status_code == 409  # not waiting
    conn = db.connect(client.home / "data" / "autotailor.db")
    db.update_question(conn, q["id"], status="waiting", points=[])
    t = client.post(f"/api/questions/{q['id']}/decision", json={"draft": True, "use": [1, 3], "notes": "lab"}).json()
    assert t["args_json"] == f'["answers", "decide", "{q["id"]}", "--draft", "--use", "1,3", "--notes", "lab"]'
    t = client.post("/api/runway/runway:x/answer", json={"url": "https://boards.greenhouse.io/acme/jobs/1"}).json()
    assert '"runway", "answer", "runway:x", "--url"' in t["args_json"]
    assert client.get("/api/ai-calls").json() == []


def test_cli_runway_answer_and_answers_decide(client, monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda

    from autotailor.models import AnswerPoint, AnswerPoints

    runner = CliRunner()
    (client.home / "data" / "inbox" / "d.eml").write_bytes(FIXTURE.read_bytes())
    runner.invoke(cli, ["mail", "import"])
    monkeypatch.setattr("autotailor.resolve.fetch_board", lambda *a: [])
    r = runner.invoke(cli, ["runway", "answer", "runway:rwjob0001aaaa", "--skip"])
    assert r.exit_code == 0, r.output
    assert "skipped" in r.output
    assert runner.invoke(cli, ["runway", "answer", "runway:rwjob0002bbbb"]).exit_code == 1  # needs one answer

    class Model(RunnableLambda):
        def __init__(self):
            super().__init__(lambda pv: AIMessage(content="I built an OCR pipeline with OpenCV."))

        def with_structured_output(self, schema, **kw):
            return RunnableLambda(lambda pv: AnswerPoints(points=[AnswerPoint(source_id="proj-ocr", point="Built OCR", why="x")]))

    monkeypatch.setattr("autotailor.llm.get_chat_model", lambda settings, step: Model())
    conn = db.connect(client.home / "data" / "autotailor.db")
    qid = db.add_question(conn, "lever:a", "Why us?", 100)
    assert runner.invoke(cli, ["answers", "run", str(qid), "--no-ask"]).exit_code == 0
    assert db.get_question(conn, qid)["status"] == "waiting"
    r = runner.invoke(cli, ["answers", "decide", str(qid), "--draft", "--use", "1"])
    assert r.exit_code == 0, r.output
    assert "I built an OCR pipeline" in r.output and db.get_question(conn, qid)["status"] == "drafted"
