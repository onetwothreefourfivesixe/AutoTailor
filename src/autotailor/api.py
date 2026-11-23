"""Local HTTP API (M11): everything the UI does, for any frontend (Svelte later) or a browser extension.

Start it with `autotailor api` (127.0.0.1:8765). Interactive docs: /docs.

Security, because it listens on this machine where websites you visit can also
send requests to it:

- every request needs the token in data/api_token, sent as the header
  `X-AutoTailor-Token` (or `?token=` for links such as file downloads);
- the Host header must be localhost or 127.0.0.1 (blocks DNS rebinding);
- CORS is closed except for the origins in `api.cors_origins` (e.g. the
  Svelte dev server).

Anything that calls a model or the network for long runs as a background task
(tasks.py), exactly like the UI's buttons: POST /api/tasks, then follow
/api/tasks/{id}/events (server-sent events) or poll /api/tasks/{id}.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError

from autotailor import ailog, db, tasks
from autotailor.config import STEPS, Settings, load_settings
from autotailor.models import Verdict
from autotailor.paths import Paths, get_paths

TOKEN_HEADER = "X-AutoTailor-Token"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "testserver"}


# --- access ----------------------------------------------------------------------------------


def api_token(paths: Paths) -> str:
    """The token clients must send; created on first use, kept in data/api_token (git-ignored)."""
    f = paths.data / "api_token"
    if not f.exists():
        paths.data.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(32))
        f.chmod(0o600)
    return f.read_text().strip()


def create_app(home: Path | None = None, cors_origins: list[str] | None = None) -> FastAPI:
    paths = get_paths(home)
    token = api_token(paths)
    app = FastAPI(title="AutoTailor", version="1", summary="Local API. Everything stays on this machine.")
    if cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["*"],
                           allow_headers=[TOKEN_HEADER, "Content-Type"])  # fmt: skip

    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001, ANN202
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Only localhost may use this API."}, status_code=403)
        if request.method == "OPTIONS" or request.url.path in ("/docs", "/openapi.json", "/api/health"):
            return await call_next(request)
        sent = request.headers.get(TOKEN_HEADER) or request.query_params.get("token")
        if not sent or not secrets.compare_digest(sent, token):
            return JSONResponse({"detail": f"Missing or wrong {TOKEN_HEADER} (see data/api_token)."}, status_code=401)
        return await call_next(request)

    app.state.paths = paths
    _routes(app)
    from autotailor import api_more

    api_more.register(app, _conn, _settings, _paths, _need)
    from autotailor import api_setup

    api_setup.register(app, _conn, _settings, _paths, _need)
    return app


def _paths(request: Request) -> Paths:
    return request.app.state.paths


def _conn(request: Request) -> Iterator[Any]:
    conn = db.connect(_paths(request).db, any_thread=True)
    try:
        yield conn
    finally:
        conn.close()


def _settings(request: Request) -> Settings:
    try:
        return load_settings(_paths(request).config)
    except ValidationError as e:
        raise HTTPException(500, f"config.toml is invalid: {e}") from e


def _need(value: Any, what: str) -> Any:
    if value is None:
        raise HTTPException(404, f"No {what}.")
    return value


def _row(r: Any) -> dict[str, Any]:
    return dict(r) if r is not None else None


# --- request bodies ------------------------------------------------------------------------------


class OverrideIn(BaseModel):
    verdict: Literal["eligible", "ineligible"]
    note: str = ""


class TaskIn(BaseModel):
    kind: str | None = None  # a button: fetch, screen, rank, tailor, ...
    args: list[str] = []
    command: str | None = None  # or a typed command, as in the UI's command box


class IntakeSave(BaseModel):
    batch: str
    keep: list[int]  # indexes of the previewed candidates to save
    edits: dict[int, dict[str, str]] = {}  # index -> {company, title, location}


class QuestionIn(BaseModel):
    posting_id: str
    text: str
    word_limit: int | None = None


class DecisionIn(BaseModel):
    draft: bool
    use: list[int] = []
    notes: str = ""


class RunwayAnswerIn(BaseModel):
    url: str | None = None
    text: str | None = None
    skip: bool = False


class CvIn(BaseModel):
    cv: dict[str, Any]
    confirm_delete_used: bool = False


# --- routes -----------------------------------------------------------------------------------------

_previews: dict[str, tuple[float, list[Any]]] = {}  # intake previews, kept 30 minutes
PREVIEW_TTL = 30 * 60


def _routes(app: FastAPI) -> None:  # noqa: C901 (one place lists every route)
    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    # overview, needs, models

    @app.get("/api/overview")
    def overview(request: Request, conn=Depends(_conn), settings: Settings = Depends(_settings)) -> dict:  # noqa: ANN001
        from autotailor import inbox, matching

        matches = db.all_matches(conn)
        counts = db.table_counts(conn)
        cut = {i for i, m in matches.items() if matching.is_cut(m, settings.run)}
        return {
            "counts": {**counts, **{v.value: sum(m.verdict == v for i, m in matches.items() if i not in cut) for v in Verdict},
                       "not_screened": counts["postings"] - len(matches), "cut": len(cut)},
            "mode": settings.models.mode(),
            "models": {s: {"provider": (m := settings.models.for_step(s)).provider, "model": m.model, "local": m.is_local}
                       for s in STEPS},
            "shortlist": [{"id": p.id, "company": p.company, "title": p.title, "fit": m.fit_score}
                          for p, m in matching.shortlist(conn, settings)],
            "needs": len(inbox.collect(conn, settings, _paths(request).outputs)),
            "worker_running": tasks.worker_running(_paths(request).data),
        }  # fmt: skip

    @app.get("/api/needs")
    def needs(request: Request, conn=Depends(_conn), settings: Settings = Depends(_settings)) -> list[dict]:  # noqa: ANN001
        from dataclasses import asdict

        from autotailor import inbox

        return [asdict(n) for n in inbox.collect(conn, settings, _paths(request).outputs)]

    # postings

    @app.get("/api/postings")
    def postings(
        conn=Depends(_conn),  # noqa: ANN001
        verdict: str | None = None, q: str | None = None, limit: int = Query(200, le=2000),
    ) -> list[dict]:  # fmt: skip
        matches = db.all_matches(conn)
        out = []
        for p in db.list_postings(conn, None):
            m = matches.get(p.id)
            v = m.verdict.value if m else "not_screened"
            if verdict and v != verdict:
                continue
            if q and q.lower() not in f"{p.company} {p.title}".lower():
                continue
            out.append({"id": p.id, "company": p.company, "title": p.title, "location": p.location,
                        "source": p.source.value, "status": p.status.value, "verdict": v,
                        "overridden": bool(m and m.original_verdict), "fit": m.fit_score if m else None,
                        "prescore": m.prescore if m else None})  # fmt: skip
        out.sort(key=lambda r: (r["fit"] is not None, r["fit"] or 0, r["prescore"] or 0), reverse=True)
        return out[:limit]

    @app.get("/api/postings/{posting_id}")
    def posting(posting_id: str, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        p = _need(db.get_posting(conn, posting_id), f"posting {posting_id}")
        m = db.get_match(conn, posting_id)
        return {
            "posting": p.model_dump(mode="json"),
            "match": m.model_dump(mode="json") if m else None,
            "artifacts": [_row(a) for a in db.artifacts_for(conn, posting_id)],
            "questions": [_row(q) for q in db.list_questions(conn, posting_id)],
            "runway": _row(db.runway_role_for_posting(conn, posting_id)),
        }

    @app.put("/api/postings/{posting_id}/override")
    def set_override(posting_id: str, body: OverrideIn, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        _need(db.get_posting(conn, posting_id), f"posting {posting_id}")
        db.set_override(conn, posting_id, Verdict(body.verdict), body.note)
        return db.get_match(conn, posting_id).model_dump(mode="json") if db.get_match(conn, posting_id) else {}

    @app.delete("/api/postings/{posting_id}/override")
    def clear_override(posting_id: str, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        db.clear_override(conn, posting_id)
        return {"ok": True}

    @app.get("/api/postings/{posting_id}/resume.png")
    def resume_png(posting_id: str, request: Request, conn=Depends(_conn), scale: float = 1.6) -> Response:  # noqa: ANN001
        from autotailor.models import ArtifactKind
        from autotailor.resume.render import pdf_to_png

        pdf = next((a for a in db.artifacts_for(conn, posting_id) if a["kind"] == ArtifactKind.RESUME_PDF.value), None)
        f = _paths(request).outputs / _need(pdf, "resume for this posting")["path"]
        if not f.exists():
            raise HTTPException(404, "The resume file is missing.")
        scale = min(max(scale, 1.0), 3.0)  # 3 is sharp enough for a full-screen view
        return Response(pdf_to_png(f.read_bytes(), scale=scale), media_type="image/png", headers={"Cache-Control": "no-store"})

    # runs and files

    @app.get("/api/runs")
    def runs(conn=Depends(_conn)) -> list[dict]:  # noqa: ANN001
        from autotailor import batch
        from autotailor.ui.logic import run_model

        return [{**{k: r[k] for k in ("id", "started_at", "finished_at", "status")}, "counts": batch.parse_counts(r),
                 "model": run_model(r)} for r in db.list_runs(conn, None)]  # fmt: skip

    @app.get("/api/runs/{run_id}")
    def run(run_id: int, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        from autotailor import batch
        from autotailor.ui.logic import run_outputs

        r = _need(db.get_run(conn, run_id), f"run {run_id}")
        outputs = _paths(request).outputs
        folder = batch.run_folder(outputs, r)
        report = folder / "report.md"
        rel = lambda f: str(f.relative_to(outputs)) if f else None  # noqa: E731
        return {
            "run": {k: r[k] for k in ("id", "started_at", "finished_at", "status")}, "counts": batch.parse_counts(r),
            "report": report.read_text(encoding="utf-8") if report.exists() else None,
            "outputs": [{"index": o.index, "posting_id": o.posting_id, "company": o.company, "title": o.title,
                         "pdf": rel(o.pdf), "docx": rel(o.docx), "data": rel(o.data), "model": o.model}
                        for o in run_outputs(conn, run_id, outputs)],
        }  # fmt: skip

    @app.get("/api/files/{rel_path:path}")
    def file(rel_path: str, request: Request) -> FileResponse:
        """A file under data/outputs (resumes, reports, answers). Nothing outside it."""
        root = _paths(request).outputs.resolve()
        f = (root / rel_path).resolve()
        if not f.is_relative_to(root) or not f.is_file():
            raise HTTPException(404, "No such file.")
        return FileResponse(f, filename=f.name)

    # tasks

    @app.get("/api/commands")
    def commands() -> list[dict]:
        """What the command line accepts (AutoTailor's own commands, never a shell)."""
        return tasks.command_help()

    @app.get("/api/tasks")
    def task_list(conn=Depends(_conn), limit: int = 20) -> list[dict]:  # noqa: ANN001
        return [{k: v for k, v in dict(r).items() if k != "log"} for r in tasks.recent(conn, limit)]

    @app.post("/api/tasks", status_code=201)
    def task_start(body: TaskIn, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        try:
            if body.command:
                tid = tasks.enqueue(conn, "cli", tasks.parse_command(body.command))
            elif body.kind:
                tid = tasks.enqueue(conn, body.kind, body.args)
            else:
                raise ValueError("Give `kind` (and `args`) or `command`.")
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        tasks.ensure_worker(_paths(request).home, _paths(request).data)
        return _row(tasks.get(conn, tid))

    @app.get("/api/tasks/{task_id}")
    def task(task_id: int, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        return _row(_need(tasks.get(conn, task_id), f"task {task_id}"))

    @app.post("/api/tasks/{task_id}/cancel")
    def task_cancel(task_id: int, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        _need(tasks.get(conn, task_id), f"task {task_id}")
        tasks.cancel(conn, task_id)
        return _row(tasks.get(conn, task_id))

    @app.get("/api/tasks/{task_id}/events")
    async def task_events(task_id: int, request: Request) -> StreamingResponse:
        """Server-sent events: `progress` (status, done, total, message) and `log` (new output),
        until the task finishes."""
        path = _paths(request).db

        async def stream():  # noqa: ANN202
            sent, last = 0, None
            while True:
                conn = db.connect(path)
                t = tasks.get(conn, task_id)
                conn.close()
                if t is None:
                    yield "event: error\ndata: {}\n\n"
                    return
                state = {k: t[k] for k in ("status", "done", "total", "message")}
                if state != last:
                    yield f"event: progress\ndata: {json.dumps(state)}\n\n"
                    last = state
                if len(t["log"]) > sent:
                    yield f"event: log\ndata: {json.dumps(t['log'][sent:])}\n\n"
                    sent = len(t["log"])
                if t["status"] not in ("queued", "running"):
                    return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(1)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # adding jobs and emails

    @app.post("/api/intake/preview")
    def intake_preview(
        request: Request, settings: Settings = Depends(_settings), conn=Depends(_conn),  # noqa: ANN001
        urls: str = Form(""), text: str = Form(""), files: list[UploadFile] = File(default=[]),
    ) -> dict:  # fmt: skip
        """Read links, text (or a raw email) and files; nothing is saved yet. Returns a batch ID for /save."""
        from autotailor import intake
        from autotailor.chains.extract_posting import extract_posting_fields
        from autotailor.sources import make_client

        def extract(t: str):  # noqa: ANN202
            return extract_posting_fields(t, settings)

        cands = []
        if urls.strip():
            with make_client() as client:
                cands += intake.read_urls(urls, client, extract)
        if text.strip():
            cands.append(intake.read_text(text, extract, settings))
        for f in files:
            cands.append(intake.read_file(f.filename or "file", f.file.read(), extract, settings))
        intake.mark_duplicates(conn, cands)
        now = time.time()
        for k in [k for k, (t, _) in _previews.items() if now - t > PREVIEW_TTL]:
            del _previews[k]
        batch_id = secrets.token_urlsafe(8)
        _previews[batch_id] = (now, cands)
        return {"batch": batch_id, "candidates": [
            {"index": i, "kind": c.kind, "status": c.status, "source": c.source, "error": c.error,
             "company": c.posting.company if c.posting else None, "title": c.posting.title if c.posting else None,
             "location": c.posting.location if c.posting else None,
             "roles": [{"title": r.title, "company": r.company, "match_score": r.match_score} for r in c.roles]}
            for i, c in enumerate(cands)]}  # fmt: skip

    @app.post("/api/intake/save")
    def intake_save(body: IntakeSave, settings: Settings = Depends(_settings), conn=Depends(_conn)) -> dict:  # noqa: ANN001
        from dataclasses import asdict

        from autotailor import intake

        _, cands = _need(_previews.pop(body.batch, None), "preview with that batch ID (they expire after 30 minutes)")
        keep = []
        for i in body.keep:
            if not 0 <= i < len(cands) or cands[i].kind == "error":
                continue
            c = cands[i]
            if c.posting is not None and i in body.edits:
                e = body.edits[i]
                c.posting = c.posting.model_copy(update={k: (e[k].strip() or None if k == "location" else e[k].strip())
                                                         for k in ("company", "title", "location") if k in e})  # fmt: skip
            keep.append(c)
        return asdict(intake.save(conn, keep, settings))

    # AI log

    @app.get("/api/ai-calls")
    def ai_calls(conn=Depends(_conn), item: str | None = None, step: str | None = None, limit: int = Query(100, le=1000)) -> list[dict]:  # noqa: ANN001, E501
        return [_row(r) for r in ailog.calls(conn, item, limit, step)]

    # settings and CV

    @app.get("/api/settings")
    def get_settings(settings: Settings = Depends(_settings)) -> dict:
        return settings.model_dump(mode="json")

    @app.put("/api/settings")
    def put_settings(body: dict[str, Any], request: Request) -> dict:
        from autotailor.ui.logic import save_settings_checked

        try:
            s = save_settings_checked(body, _paths(request).config, _paths(request).config_backups)
        except ValidationError as e:
            raise HTTPException(422, json.loads(e.json())) from e
        return s.model_dump(mode="json")

    @app.get("/api/cv")
    def get_cv(request: Request) -> dict:
        from autotailor.cv import load_master_cv

        try:
            return load_master_cv(_paths(request).master_cv).model_dump(mode="json")
        except FileNotFoundError as e:
            raise HTTPException(404, "No master CV yet: run `autotailor init`, then fill in cv/master.yaml.") from e
        except (OSError, ValueError) as e:
            raise HTTPException(500, f"Can't read the master CV: {e}") from e

    @app.put("/api/cv")
    def put_cv(body: CvIn, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        """Validate and save the CV (new entries/bullets get IDs). Deleting bullets that past
        resumes used is refused with 409 and the uses, unless confirm_delete_used is true."""
        import yaml

        from autotailor.ui import logic

        p = _paths(request)
        old = yaml.safe_load(p.master_cv.read_text(encoding="utf-8")) if p.master_cv.exists() else {}
        usage = logic.bullet_usage(conn, p.outputs)
        used = {i: [vars(u) for u in usage[i]] for i in logic.removed_ids(old or {}, body.cv) if i in usage}
        if used and not body.confirm_delete_used:
            raise HTTPException(409, {"message": "These bullets were used in past resumes.", "uses": used})
        try:
            cv = logic.save_cv(body.cv, p.master_cv, p.cv_backups, keep_ids=set(usage) | logic.all_ids(old or {}))
        except ValidationError as e:
            raise HTTPException(422, json.loads(e.json())) from e
        return cv.model_dump(mode="json")

    # Runway roles

    @app.get("/api/runway")
    def runway_roles(conn=Depends(_conn), status: str | None = None) -> list[dict]:  # noqa: ANN001
        return [_row(r) for r in db.list_runway_roles(conn, status)]

    @app.post("/api/runway/{posting_id}/answer", status_code=201)
    def runway_answer(posting_id: str, body: RunwayAnswerIn, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        args = ["runway", "answer", posting_id] + (
            ["--skip"] if body.skip else ["--url", body.url] if body.url else ["--text", body.text or ""]
        )
        tid = tasks.enqueue(conn, "cli", args)
        tasks.ensure_worker(_paths(request).home, _paths(request).data)
        return _row(tasks.get(conn, tid))

    # short answers

    @app.get("/api/questions")
    def questions(conn=Depends(_conn), posting_id: str | None = None) -> list[dict]:  # noqa: ANN001
        return [{**_row(q), "points": json.loads(q["points_json"] or "[]"), "problems": json.loads(q["problems_json"] or "[]")}
                for q in db.list_questions(conn, posting_id)]  # fmt: skip

    @app.post("/api/questions", status_code=201)
    def add_question(body: QuestionIn, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        from autotailor.answers import parse_question

        _need(db.get_posting(conn, body.posting_id), f"posting {body.posting_id}")
        text, limit = parse_question(body.text)
        return _row(db.get_question(conn, db.add_question(conn, body.posting_id, text, body.word_limit or limit)))

    @app.post("/api/questions/{question_id}/decision", status_code=201)
    def decide(question_id: int, body: DecisionIn, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        q = _need(db.get_question(conn, question_id), f"question {question_id}")
        if q["status"] != "waiting":
            raise HTTPException(409, f"Question {question_id} isn't waiting for a decision (it is {q['status']}).")
        args = ["answers", "decide", str(question_id), "--draft" if body.draft else "--points-only"]
        if body.use:
            args += ["--use", ",".join(str(i) for i in body.use)]
        if body.notes:
            args += ["--notes", body.notes]
        tid = tasks.enqueue(conn, "cli", args)
        tasks.ensure_worker(_paths(request).home, _paths(request).data)
        return _row(tasks.get(conn, tid))
