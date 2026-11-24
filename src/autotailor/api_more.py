"""API routes added for the Svelte frontend (M12): live events, job views, bulk actions,
applications, CV preview and usage, templates, models and comparisons.

Registered by api.create_app; same access rules (token, Host check).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ValidationError

from autotailor import db, tasks
from autotailor.config import Settings
from autotailor.models import ArtifactKind, TailoredResume, Verdict

EVENT_POLL = 0.5  # seconds between checks for task changes (one cheap query)
NEEDS_EVERY = 3.0  # the Needs-you count reads files too: at most this often, or when a task changes
OLLAMA_TAGS_TTL = 15.0  # seconds a list of pulled Ollama models is reused
_OLLAMA_TAGS: dict[str, tuple[float, list[dict] | None]] = {}


class BulkIn(BaseModel):
    ids: list[str]
    action: Literal["tailor", "score", "rescreen", "eligible", "ineligible", "clear_override", "hide", "unhide"]


class EditedBullet(BaseModel):
    source_id: str
    text: str


class ResumeEditIn(BaseModel):
    bullets: list[EditedBullet]
    skills: list[str] = []


class HiddenIn(BaseModel):
    hidden: bool


class ApplicationIn(BaseModel):
    status: str = "applied"
    notes: str | None = None
    applied_at: str | None = None
    run_id: int | None = None


class AnswerEdit(BaseModel):
    answer: str


class CvPreviewIn(BaseModel):
    cv: dict[str, Any] | None = None  # a draft; default: the saved CV
    template: str | None = None  # default: settings.resume.template
    full: bool = False  # the whole CV (every bullet, every page); default: a one-page sample of 12 bullets


class YamlIn(BaseModel):
    text: str
    confirm_delete_used: bool = False


def register(app: FastAPI, _conn, _settings, _paths, _need) -> None:  # noqa: ANN001, C901
    # --- live events ----------------------------------------------------------------------

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-sent events for the whole app: `tasks` (active and recently finished),
        `needs` (count), `runs` (latest run), `postings` (count). Sent when they change."""
        paths = _paths(request)
        # One connection for the life of the stream (opening one costs ~10 ms); used by one
        # worker thread at a time.
        conn = db.connect(paths.db, any_thread=True)
        needs_at = -NEEDS_EVERY
        needs: int | None = None

        def snapshot(tasks_changed: bool) -> dict[str, Any]:
            nonlocal needs_at, needs
            from autotailor import inbox
            from autotailor.config import load_settings

            recent = tasks.recent_summaries(conn, 8)  # without the logs
            if tasks_changed or time.monotonic() - needs_at >= NEEDS_EVERY:
                try:
                    needs = len(inbox.collect(conn, load_settings(paths.config), paths.outputs))
                except Exception:  # an invalid config shouldn't stop the stream
                    needs = None
                needs_at = time.monotonic()
            latest = db.list_runs(conn, 1)
            return {
                "tasks": recent,
                "needs": {"count": needs},
                "runs": {"latest": dict(latest[0]) if latest else None},
                "postings": {"count": db.table_counts(conn)["postings"]},
            }

        async def stream():  # noqa: ANN202
            last: dict[str, str] = {}
            beat = 0.0
            try:
                while not await request.is_disconnected():
                    snap = await asyncio.to_thread(snapshot, False)
                    if json.dumps(snap["tasks"], default=str) != last.get("tasks"):
                        snap = await asyncio.to_thread(snapshot, True)  # a task changed: recount what needs you now
                    for name, value in snap.items():
                        data = json.dumps(value, default=str)
                        if last.get(name) != data:
                            last[name] = data
                            yield f"event: {name}\ndata: {data}\n\n"
                    beat += EVENT_POLL
                    if beat >= 15:
                        beat = 0.0
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(EVENT_POLL)
            finally:
                conn.close()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    # --- jobs -----------------------------------------------------------------------------

    @app.get("/api/jobs")
    def jobs(
        response: Response, conn=Depends(_conn),  # noqa: ANN001
        verdict: str | None = None, q: str | None = None, source: str | None = None,
        min_fit: float | None = None, has_resume: bool | None = None, hidden: bool = False,
        applied: bool | None = None, sort: str = "fit", offset: int = 0, limit: int = 100, cut: bool = False,
        settings: Settings = Depends(_settings),
    ) -> list[dict]:  # fmt: skip
        """The jobs table: one row per posting with its verdict, fit and progress. Total in X-Total-Count.

        Postings below the fit cut-off (run.cut_low_fit) are left out; `cut=true` lists only them.
        Their number, for the Cut view, is in X-Cut-Count. The Hidden view shows hidden ones either way."""
        from autotailor.matching import is_cut

        matches = db.all_matches(conn)
        hidden_set = db.hidden_ids(conn)
        apps = {r["posting_id"]: r["status"] for r in db.list_applications(conn)}
        tailored = {r[0] for r in conn.execute("SELECT DISTINCT posting_id FROM artifacts WHERE kind = ?",
                                               (ArtifactKind.RESUME_PDF.value,))}  # fmt: skip
        rows, cut_count = [], 0
        for p in db.list_postings(conn, None):
            m = matches.get(p.id)
            v = m.verdict.value if m else "not_screened"
            row = {
                "id": p.id, "company": p.company, "title": p.title, "location": p.location, "source": p.source.value,
                "status": p.status.value, "posted_at": p.posted_at.isoformat() if p.posted_at else None,
                "verdict": v, "overridden": bool(m and m.original_verdict), "fit": m.fit_score if m else None,
                "prescore": m.prescore if m else None, "runway_score": p.external_score,
                "tailored": p.id in tailored, "application": apps.get(p.id), "hidden": p.id in hidden_set,
                "cut": is_cut(m, settings.run),
            }  # fmt: skip
            if row["hidden"] != hidden:
                continue
            if not hidden and row["cut"]:
                cut_count += 1
            if not hidden and row["cut"] != cut:
                continue
            if (verdict and v != verdict) or (source and row["source"] != source):
                continue
            if q and q.lower() not in f"{p.company} {p.title} {p.location or ''}".lower():
                continue
            if min_fit is not None and (row["fit"] or 0) < min_fit:
                continue
            if has_resume is not None and row["tailored"] != has_resume:
                continue
            if applied is not None and (row["application"] is not None) != applied:
                continue
            rows.append(row)
        keys = {
            "fit": lambda r: (r["fit"] is not None, r["fit"] or 0, r["prescore"] or 0),
            "posted": lambda r: r["posted_at"] or "",
            "company": lambda r: r["company"].lower(),
        }
        rows.sort(key=keys.get(sort, keys["fit"]), reverse=sort != "company")
        response.headers["X-Total-Count"] = str(len(rows))
        deleted = conn.execute("SELECT COUNT(*) FROM cut_postings").fetchone()[0]
        response.headers["X-Cut-Count"] = str(cut_count + deleted)
        return rows[offset : offset + limit]

    @app.get("/api/jobs/{posting_id}")
    def job(posting_id: str, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        """Everything the job page shows, with the posting text already highlighted."""
        from autotailor import ailog, eligibility
        from autotailor.cv import load_master_cv
        from autotailor.ui.logic import highlight

        p = _need(db.get_posting(conn, posting_id), f"posting {posting_id}")
        m = db.get_match(conn, posting_id)
        quotes = [e.quote for e in p.requirements.evidence] if p.requirements else []
        cited = []
        if m and m.fit and m.fit.relevant_bullet_ids:
            try:
                bullets = load_master_cv(_paths(request).master_cv).bullets()
            except (OSError, ValueError):
                bullets = {}
            cited = [{"id": b, "text": bullets[b].text if b in bullets else None} for b in m.fit.relevant_bullet_ids]
        app_row = db.get_application(conn, posting_id)
        return {
            "posting": p.model_dump(mode="json"),
            "match": m.model_dump(mode="json") if m else None,
            "highlighted": highlight(p.description or "", quotes) if p.description else None,
            "missing_quotes": [q for q in quotes if not eligibility.quote_found(q, p.description or "")],
            "cited_bullets": cited,
            "questions": [{**dict(q), "points": json.loads(q["points_json"] or "[]"),
                           "problems": json.loads(q["problems_json"] or "[]")}
                          for q in db.list_questions(conn, posting_id)],  # fmt: skip
            "runway": dict(r) if (r := db.runway_role_for_posting(conn, posting_id)) else None,
            "application": {**dict(app_row), "events": [dict(e) for e in db.application_events(conn, posting_id)]}
            if app_row else None,
            "hidden": posting_id in db.hidden_ids(conn),
            "ai_calls": len(ailog.calls(conn, posting_id, 1000)),
            "has_resume": any(a["kind"] == ArtifactKind.RESUME_PDF.value for a in db.artifacts_for(conn, posting_id)),
        }

    @app.get("/api/jobs/{posting_id}/resume")
    def job_resume(posting_id: str, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        """The latest tailored resume: bullets beside their CV source, every draft, file links."""
        from autotailor.cv import load_master_cv
        from autotailor.ui.logic import resume_diff

        outputs = _paths(request).outputs
        arts = db.artifacts_for(conn, posting_id)
        data = _need(next((a for a in arts if a["kind"] == ArtifactKind.RESUME_DATA.value), None), "resume for this posting")
        same_run = [a for a in arts if a["run_id"] == data["run_id"]]
        resume = TailoredResume.model_validate_json((outputs / data["path"]).read_text(encoding="utf-8"))
        folder = (outputs / data["path"]).parent
        attempts = folder / "attempts.json"
        fit = json.loads((folder / "fit.json").read_text(encoding="utf-8")) if (folder / "fit.json").exists() else {}
        try:
            cv = load_master_cv(_paths(request).master_cv)
            diff = [vars(r) for r in resume_diff(resume, cv)]
        except (OSError, ValueError):
            cv = None
            diff = [{"source_id": b.source_id, "original": None, "tailored": b.text, "changed": True, "html": b.text}
                    for b in resume.bullets]  # fmt: skip
        files = {a["kind"]: a["path"] for a in same_run}
        from autotailor.resume.render import pdf_page_count

        pdf = outputs / files[ArtifactKind.RESUME_PDF.value] if ArtifactKind.RESUME_PDF.value in files else None
        cv_bullets, cv_skills = [], []
        if cv is not None:  # what the editor may add: every bullet not hidden, and the CV's skills
            hidden, texts = cv.hidden_bullets(), cv.bullets()
            cv_bullets = [{"id": i, "text": texts[i].text, "entry": getattr(e, "title", None) or getattr(e, "school", "")}
                          for i, e in cv.entry_of().items() if i not in hidden]  # fmt: skip
            cv_skills = [{"name": g.name, "items": g.items} for g in cv.skills]
        from autotailor.resume.output import ORIGINAL

        return {
            "edited": (folder / ORIGINAL).exists(), "cv_bullets": cv_bullets, "cv_skills": cv_skills,
            "run_id": data["run_id"], "model": data["model"], "created_at": data["created_at"],
            "pdf": files.get(ArtifactKind.RESUME_PDF.value), "docx": files.get(ArtifactKind.RESUME_DOCX.value),
            "pages": pdf_page_count(pdf.read_bytes()) if pdf and pdf.exists() else None,
            "skills": resume.skills, "bullets": diff,
            "history": json.loads(attempts.read_text(encoding="utf-8")) if attempts.exists() else [],
            "added_to_fill": fit.get("added_to_fill", []), "removed_to_fit": fit.get("removed_to_fit", []),
            "keyword_score": fit.get("keyword_score"), "keywords_matched": fit.get("keywords_matched", []),
            "keywords_missing": fit.get("keywords_missing", []),
        }  # fmt: skip

    def _resume_files(conn, request: Request, posting_id: str) -> dict[str, Path]:  # noqa: ANN001
        outputs = _paths(request).outputs
        arts = db.artifacts_for(conn, posting_id)
        data = _need(next((a for a in arts if a["kind"] == ArtifactKind.RESUME_DATA.value), None), "resume for this posting")
        files = {a["kind"]: outputs / a["path"] for a in arts if a["run_id"] == data["run_id"]}
        data_path = outputs / data["path"]
        return {"data": data_path, "folder": data_path.parent,
                "pdf": files.get(ArtifactKind.RESUME_PDF.value, data_path.with_name("resume.pdf")),
                "docx": files.get(ArtifactKind.RESUME_DOCX.value, data_path.with_name("resume.docx"))}  # fmt: skip

    def _save_resume(conn, request: Request, posting_id: str, resume: TailoredResume, settings: Settings) -> dict:  # noqa: ANN001
        from autotailor.cv import load_master_cv
        from autotailor.resume.output import Style, save_edit

        p = _need(db.get_posting(conn, posting_id), f"posting {posting_id}")
        f = _resume_files(conn, request, posting_id)
        cv = load_master_cv(_paths(request).master_cv)
        try:
            out = save_edit(f["folder"], f["pdf"], f["docx"], f["data"], resume, cv,
                            Style.from_settings(settings, _paths(request)), f"{p.title}\n{p.description or ''}")  # fmt: skip
        except Exception as e:  # LaTeX errors in a custom template
            raise HTTPException(422, f"Couldn't render the resume: {e}") from e
        return {"pages": out.pages, "warnings": out.warnings, "keyword_score": out.keyword_score}

    @app.put("/api/jobs/{posting_id}/resume")
    def edit_resume(posting_id: str, body: ResumeEditIn, request: Request, conn=Depends(_conn),  # noqa: ANN001
                    settings: Settings = Depends(_settings)) -> dict:  # fmt: skip
        """Save hand edits to a tailored resume: bullets (CV bullets, in order, text as edited)
        and skills. The PDF and Word files are rendered again; the tailored version is kept."""
        from autotailor.cv import load_master_cv
        from autotailor.models import TailoredBullet

        cv = load_master_cv(_paths(request).master_cv)
        known, hidden = cv.bullets(), cv.hidden_bullets()
        ids = [b.source_id for b in body.bullets]
        bad = [i for i in ids if i not in known or i in hidden]
        if bad:
            raise HTTPException(422, f"Not bullets that can go on a resume: {', '.join(bad)}")
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "A bullet appears twice.")
        if not ids or any(not b.text.strip() for b in body.bullets):
            raise HTTPException(422, "Every bullet needs text, and the resume needs at least one bullet.")
        skills = {s.lower(): s for g in cv.skills for s in g.items}
        unknown = [s for s in body.skills if s.lower() not in skills]
        if unknown:
            raise HTTPException(422, f"Not in your CV's skills: {', '.join(unknown)}. Add them on the Master CV page first.")
        saved = TailoredResume.model_validate_json(_resume_files(conn, request, posting_id)["data"].read_text(encoding="utf-8"))
        resume = saved.model_copy(update={
            "bullets": [TailoredBullet(source_id=b.source_id, text=" ".join(b.text.split())) for b in body.bullets],
            "skills": [skills[s.lower()] for s in body.skills],
        })  # fmt: skip
        return _save_resume(conn, request, posting_id, resume, settings)

    @app.post("/api/jobs/{posting_id}/resume/restore")
    def restore_resume(posting_id: str, request: Request, conn=Depends(_conn),  # noqa: ANN001
                       settings: Settings = Depends(_settings)) -> dict:  # fmt: skip
        """Undo every hand edit: back to the resume as AutoTailor tailored it."""
        from autotailor.resume.output import ORIGINAL

        f = _resume_files(conn, request, posting_id)
        original = f["folder"] / ORIGINAL
        if not original.exists():
            raise HTTPException(404, "This resume hasn't been edited.")
        out = _save_resume(conn, request, posting_id, TailoredResume.model_validate_json(original.read_text(encoding="utf-8")), settings)
        original.unlink()
        fit_file = f["folder"] / "fit.json"
        if fit_file.exists():
            fit = json.loads(fit_file.read_text(encoding="utf-8"))
            fit["edited"] = False
            fit_file.write_text(json.dumps(fit, indent=1), encoding="utf-8")
        return out

    @app.get("/api/cut")
    def cut_list(conn=Depends(_conn)) -> list[dict]:  # noqa: ANN001
        """Postings deleted to keep the database small (low fit or too old); they aren't fetched again."""
        return [dict(r) for r in db.list_cut(conn)]

    @app.delete("/api/cut/{posting_id}")
    def cut_allow(posting_id: str, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        """Let a deleted posting back in: the next fetch (or adding its link) stores it again."""
        if not db.uncut(conn, posting_id):
            raise HTTPException(404, "No deleted posting with that ID.")
        return {"ok": True}

    @app.put("/api/jobs/{posting_id}/hidden")
    def set_hidden(posting_id: str, body: HiddenIn, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        _need(db.get_posting(conn, posting_id), f"posting {posting_id}")
        db.set_hidden(conn, posting_id, body.hidden)
        return {"hidden": body.hidden}

    @app.post("/api/jobs/bulk")
    def bulk(body: BulkIn, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        """Act on several jobs. Tailor, score and rescreen start a background task; the rest are immediate."""
        ids = [i for i in body.ids if db.get_posting(conn, i)]
        if not ids:
            raise HTTPException(422, "None of those postings exist.")
        if body.action in ("tailor", "score", "rescreen"):
            # Jobs picked by hand are tailored again even if they already have a resume
            # ("Tailor again"); the shortlist buttons still skip finished ones. Scoring picked
            # jobs always scores them (again).
            args = {"tailor": ["tailor", *ids, "--force"], "score": ["rank", *ids],
                    "rescreen": ["screen", *ids, "--reextract"]}[body.action]  # fmt: skip
            tid = tasks.enqueue(conn, "cli", args)
            tasks.ensure_worker(_paths(request).home, _paths(request).data)
            return {"task": dict(tasks.get(conn, tid))}
        for i in ids:
            if body.action in ("eligible", "ineligible"):
                db.set_override(conn, i, Verdict(body.action), "set from the jobs list")
            elif body.action == "clear_override":
                db.clear_override(conn, i)
            else:
                db.set_hidden(conn, i, body.action == "hide")
        return {"updated": len(ids)}

    # --- applications -------------------------------------------------------------------------

    @app.get("/api/applications")
    def applications(conn=Depends(_conn)) -> list[dict]:  # noqa: ANN001
        out = []
        for a in db.list_applications(conn):
            p = db.get_posting(conn, a["posting_id"])
            out.append({**dict(a), "company": p.company if p else "?", "title": p.title if p else a["posting_id"],
                        "location": p.location if p else None, "url": (p.canonical_url or p.source_url) if p else None,
                        "events": [dict(e) for e in db.application_events(conn, a["posting_id"])]})  # fmt: skip
        return out

    @app.put("/api/applications/{posting_id}")
    def put_application(posting_id: str, body: ApplicationIn, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        _need(db.get_posting(conn, posting_id), f"posting {posting_id}")
        run_id = body.run_id
        if run_id is None and db.get_application(conn, posting_id) is None:  # the resume most recently made for it
            arts = [a for a in db.artifacts_for(conn, posting_id) if a["kind"] == ArtifactKind.RESUME_PDF.value]
            run_id = arts[0]["run_id"] if arts else None
        try:
            row = db.set_application(conn, posting_id, body.status, body.notes, body.applied_at, run_id)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        return {**dict(row), "events": [dict(e) for e in db.application_events(conn, posting_id)]}

    @app.delete("/api/applications/{posting_id}")
    def delete_application(posting_id: str, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        db.delete_application(conn, posting_id)
        return {"ok": True}

    # --- questions --------------------------------------------------------------------------

    @app.patch("/api/questions/{question_id}")
    def edit_answer(question_id: int, body: AnswerEdit, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        from autotailor.answers import write_answers

        q = _need(db.get_question(conn, question_id), f"question {question_id}")
        db.update_question(conn, question_id, answer=body.answer, status="drafted", problems=[])
        write_answers(conn, q["posting_id"], _paths(request).outputs)
        return dict(db.get_question(conn, question_id))

    @app.delete("/api/questions/{question_id}")
    def delete_question(question_id: int, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        db.delete_question(conn, question_id)
        return {"ok": True}

    @app.post("/api/questions/{question_id}/suggest", status_code=201)
    def suggest(question_id: int, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        _need(db.get_question(conn, question_id), f"question {question_id}")
        tid = tasks.enqueue(conn, "cli", ["answers", "run", str(question_id), "--no-ask", "--redo"])
        tasks.ensure_worker(_paths(request).home, _paths(request).data)
        return dict(tasks.get(conn, tid))

    # --- CV -------------------------------------------------------------------------------------

    @app.get("/api/cv/usage")
    def cv_usage(request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        from autotailor.ui.logic import bullet_usage

        return {k: [vars(u) for u in v] for k, v in bullet_usage(conn, _paths(request).outputs).items()}

    @app.get("/api/cv/yaml", response_class=PlainTextResponse)
    def cv_yaml(request: Request) -> str:
        f = _paths(request).master_cv
        return f.read_text(encoding="utf-8") if f.exists() else ""

    @app.put("/api/cv/yaml")
    def put_cv_yaml(body: YamlIn, request: Request, conn=Depends(_conn)) -> dict:  # noqa: ANN001
        import yaml

        from autotailor.ui import logic

        try:
            cv = logic.parse_cv_yaml(body.text)
        except yaml.YAMLError as e:
            raise HTTPException(422, f"Not valid YAML: {e}") from e
        except ValidationError as e:
            raise HTTPException(422, json.loads(e.json())) from e
        p = _paths(request)
        old = yaml.safe_load(p.master_cv.read_text(encoding="utf-8")) if p.master_cv.exists() else {}
        usage = logic.bullet_usage(conn, p.outputs)
        used = {i: [vars(u) for u in usage[i]] for i in logic.removed_ids(old or {}, cv) if i in usage}
        if used and not body.confirm_delete_used:
            raise HTTPException(409, {"message": "These bullets were used in past resumes.", "uses": used})
        return logic.save_cv(cv, p.master_cv, p.cv_backups).model_dump(mode="json")

    @app.post("/api/cv/preview")
    def cv_preview(body: CvPreviewIn, request: Request, settings: Settings = Depends(_settings)) -> Response:
        """The saved or a draft CV rendered with a template, as one PNG of all its pages: the whole
        CV (`full`), or the usual resume (bullets marked `base`; without any, a sample of 12)."""
        from autotailor.cv import MasterCV, load_master_cv
        from autotailor.models import TailoredBullet
        from autotailor.resume.layout import build_layout
        from autotailor.resume.render import pdf_page_count, pdf_to_png, render_resume
        from autotailor.ui.logic import fill_missing_ids

        p = _paths(request)
        try:
            cv = MasterCV.model_validate(fill_missing_ids(body.cv)) if body.cv else load_master_cv(p.master_cv)
        except (ValidationError, OSError, ValueError) as e:
            raise HTTPException(422, f"Can't preview this CV: {e}") from e
        bullets = cv.bullets()
        ids = [b.id for e in cv.education for b in e.details] + [b.id for e in cv.entries() for b in e.bullets]
        if not body.full:
            ids = cv.base_bullets() or [b.id for e in cv.entries() for b in e.bullets][:12]
        sample = TailoredResume(bullets=[TailoredBullet(source_id=i, text=bullets[i].text) for i in ids])
        try:
            r = render_resume(build_layout(sample, cv, all_skills=True), body.template or settings.resume.template, p.templates, p.tools)
        except Exception as e:  # LaTeX errors in a custom template
            raise HTTPException(422, str(e)) from e
        headers = {"X-Pages": str(pdf_page_count(r.pdf)), "X-Template": r.template}
        if r.warning:
            headers["X-Warning"] = r.warning.replace("\n", " ")
        return Response(pdf_to_png(r.pdf), media_type="image/png", headers=headers)

    # --- templates -----------------------------------------------------------------------------

    @app.get("/api/templates")
    def templates(request: Request, settings: Settings = Depends(_settings)) -> dict:
        from autotailor.resume.render import BUILTIN_TEMPLATES

        tdir = _paths(request).templates
        mine = sorted(f.name for f in tdir.iterdir()) if tdir.exists() else []
        return {
            "pdf": [{"name": k, "label": v, "builtin": True} for k, v in BUILTIN_TEMPLATES.items()]
            + [{"name": n, "label": f"{n} (yours)", "builtin": False} for n in mine if n.endswith((".tex", ".j2"))],
            "word": [{"name": n, "label": n} for n in mine if n.endswith(".docx")],
            "current": {"pdf": settings.resume.template, "word": settings.resume.word_template},
        }

    @app.post("/api/templates", status_code=201)
    def upload_template(request: Request, file: UploadFile = File(...)) -> dict:
        name = Path(file.filename or "").name
        if not re.fullmatch(r"[\w.-]+\.(tex|j2|docx)", name):
            raise HTTPException(422, "Upload a .tex, .tex.j2 or .docx file (letters, digits, . _ - in the name).")
        tdir = _paths(request).templates
        tdir.mkdir(parents=True, exist_ok=True)
        (tdir / name).write_bytes(file.file.read())
        return {"name": name}

    @app.delete("/api/templates/{name}")
    def delete_template(name: str, request: Request) -> dict:
        f = (_paths(request).templates / Path(name).name)
        if not f.is_file():
            raise HTTPException(404, "No such template.")
        f.unlink()
        return {"ok": True}

    @app.get("/api/templates/builtin/jake", response_class=PlainTextResponse)
    def jake() -> str:
        from autotailor.resume import latex

        return latex.builtin("jake.tex.j2")

    # --- models and comparisons ------------------------------------------------------------------

    @app.get("/api/models")
    def models(settings: Settings = Depends(_settings)) -> dict:
        import httpx

        from autotailor.config import STEPS

        m = settings.models
        url = m.ollama_base_url.rstrip("/")
        cached = _OLLAMA_TAGS.get(url)
        if cached and time.monotonic() - cached[0] < OLLAMA_TAGS_TTL:
            pulled = cached[1]
        else:
            try:
                tags = httpx.get(f"{url}/api/tags", timeout=3).json()["models"]
                pulled = [{"name": t["name"], "size": t["size"], "parameters": t.get("details", {}).get("parameter_size")}
                          for t in tags]  # fmt: skip
            except (httpx.HTTPError, KeyError, ValueError):
                pulled = None
            _OLLAMA_TAGS[url] = (time.monotonic(), pulled)
        names = {p["name"] for p in pulled or []} | {p["name"].removesuffix(":latest") for p in pulled or []}
        steps = []
        for step in [*STEPS, "embeddings"]:
            spec = m.embeddings if step == "embeddings" else m.for_step(step)  # type: ignore[arg-type]
            steps.append({"step": step, "provider": spec.provider, "model": spec.model, "local": spec.is_local,
                          "pulled": (spec.model in names) if spec.is_local and pulled is not None else None})  # fmt: skip
        return {"mode": m.mode(), "steps": steps, "pulled": pulled}

    @app.get("/api/comparisons")
    def comparisons(request: Request) -> list[dict]:
        folder = _paths(request).comparisons
        return [{"id": f.stem, "step": f.stem.rsplit("-", 1)[-1]} for f in sorted(folder.glob("*.md"), reverse=True)] \
            if folder.exists() else []  # fmt: skip

    @app.get("/api/comparisons/{report_id}")
    def comparison(report_id: str, request: Request) -> dict:
        from autotailor import compare as cmp

        folder = _paths(request).comparisons
        md = folder / f"{Path(report_id).name}.md"
        if not md.exists():
            raise HTTPException(404, "No such report.")
        data = md.with_suffix(".json")
        trials = [cmp.Trial(**t) for t in json.loads(data.read_text(encoding="utf-8"))] if data.exists() else []
        step = report_id.rsplit("-", 1)[-1]
        return {"id": report_id, "step": step, "markdown": md.read_text(encoding="utf-8"),
                "summary": cmp.summarize(step, trials) if trials and step in cmp.STEP_OF else [],  # type: ignore[arg-type]
                "trials": [vars(t) for t in trials]}  # fmt: skip
