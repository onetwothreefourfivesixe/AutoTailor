"""Batch runs (M5): tailor resumes for the top X postings in parallel, resumably.

A run is:

1. `make_jobs`: one `Job` per shortlisted posting (posting text plus the
   profile the model sees), in rank order.
2. The batch graph (the M5 exercise, chains/batch_graph.py) fans the jobs out,
   runs `make_worker(...)` on each in parallel, and collects one `JobResult`
   per job. It is compiled with a SQLite checkpointer, keyed by run, so a run
   stopped part way (Ctrl-C, an error) resumes without redoing finished jobs.
3. `save_results` renders the passing resumes (PDF, Word) into the run folder
   and records them; `write_report` writes the run's report.md.

Jobs and results are plain dicts (TypedDicts) because they live in the
checkpoint: plain JSON-like values save and load without surprises.
"""

from __future__ import annotations

import contextvars
import json
import os
import signal
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

import typer

from autotailor import db
from autotailor.config import RunSettings, Settings
from autotailor.cv import MasterCV
from autotailor.models import ArtifactKind, JobPosting, Match, TailoredResume

if TYPE_CHECKING:
    from langgraph.checkpoint.sqlite import SqliteSaver


class Job(TypedDict):
    index: int  # 1-based rank in this run; names the output folder
    posting_id: str
    posting: str  # the posting text the model sees
    profile: str  # the candidate profile the model sees


class JobResult(TypedDict):
    index: int
    posting_id: str
    status: Literal["tailored", "failed"]
    resume: dict | None  # TailoredResume.model_dump(), when tailored
    errors: list[str]  # the last draft's verifier problems, when it failed verification
    attempts: int
    message: str | None  # why it failed, in a sentence
    history: list[dict]  # every draft's bullets and problems, when the tailoring graph keeps them


class Cancelled(Exception):
    """Raised by the worker for jobs not started before the user pressed Ctrl-C.

    A job that raises leaves no result in the checkpoint, so resuming runs it.
    """


def thread_id(run_id: int) -> str:
    """Checkpoint thread for a run. One thread per run: re-invoking a finished thread
    with new input would add to its old results."""
    return f"run-{run_id}"


# --- building jobs ----------------------------------------------------------


def job_profile(base_profile: str, m: Match | None) -> str:
    if m and m.fit and m.fit.relevant_bullet_ids:
        return base_profile + "\n\nMost relevant CV bullets for this posting (from fit scoring): " + ", ".join(
            m.fit.relevant_bullet_ids
        )
    return base_profile


def make_jobs(targets: list[tuple[JobPosting, Match | None]], cv: MasterCV, settings: Settings) -> list[Job]:
    from autotailor import matching

    base = matching.profile_text(cv, settings.profile)
    return [
        Job(index=i, posting_id=p.id, posting=matching.posting_text(p, 12000), profile=job_profile(base, m))
        for i, (p, m) in enumerate(targets, 1)
    ]


# --- the work done per job ---------------------------------------------------


def make_worker(
    cv: MasterCV,
    settings: Settings,
    cancel: threading.Event | None = None,
    tailor: Callable | None = None,
) -> Callable[[Job], JobResult]:
    """The function the batch graph runs once per job: the M4 tailoring graph, with
    failures turned into a `failed` result so one bad posting doesn't stop the run.

    NotImplementedError (exercise not written) and Cancelled still raise.
    """
    if tailor is None:
        from autotailor.chains.tailor_graph import tailor_posting as tailor

    def work(job: Job) -> JobResult:
        if cancel is not None and cancel.is_set():
            raise Cancelled(job["posting_id"])
        base = JobResult(
            index=job["index"], posting_id=job["posting_id"], status="failed",
            resume=None, errors=[], attempts=0, message=None, history=[],
        )  # fmt: skip
        try:
            # A graph invoked inside another graph's node inherits that graph's
            # checkpointer. A fresh context keeps the tailoring graph's drafts out of
            # the batch checkpoint; only finished results are saved there.
            def run():  # noqa: ANN202
                from autotailor.ailog import current_item

                current_item.set(job["posting_id"])  # labels this job's model calls in the AI log
                return tailor(job["posting"], job["profile"], cv, settings)

            r = contextvars.Context().run(run)
        except (NotImplementedError, Cancelled):
            raise
        except Exception as e:  # model errors, missing key, invalid output after retries
            return {**base, "message": f"{type(e).__name__}: {e}"}
        base["history"] = list(getattr(r, "history", []) or [])
        if r.resume is None or r.errors:
            return {
                **base,
                "errors": list(r.errors),
                "attempts": r.attempts,
                "message": f"the last of {r.attempts} drafts still had problems:",
            }
        return {**base, "status": "tailored", "resume": r.resume.model_dump(), "attempts": r.attempts}

    return work


# --- run limits, checkpoints, interrupts --------------------------------------


def limit_message(conn: sqlite3.Connection, run: RunSettings, now: datetime | None = None) -> str | None:
    """Why a new run isn't allowed now, or None. Resuming a run doesn't count as a new one."""
    now = (now or datetime.now()).astimezone()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if (n := db.count_runs_since(conn, today)) >= run.max_runs_per_day:
        return f"{n} runs today already (run.max_runs_per_day = {run.max_runs_per_day})."
    if (n := db.count_runs_since(conn, now - timedelta(days=7))) >= run.max_runs_per_week:
        return f"{n} runs in the last 7 days already (run.max_runs_per_week = {run.max_runs_per_week})."
    return None


@contextmanager
def open_checkpointer(path: Path) -> Iterator[SqliteSaver]:
    """A LangGraph SQLite checkpointer in its own file (data/checkpoints.db)."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    path.parent.mkdir(parents=True, exist_ok=True)
    # Nodes run in worker threads, which save their results through this connection.
    conn = sqlite3.connect(path, check_same_thread=False)
    try:
        yield SqliteSaver(conn)
    finally:
        conn.close()


def has_checkpoint(saver: SqliteSaver, run_id: int) -> bool:
    return saver.get_tuple({"configurable": {"thread_id": thread_id(run_id)}}) is not None


@contextmanager
def interrupt_guard(cancel: threading.Event, in_flight: int) -> Iterator[None]:
    """Ctrl-C (or the terminal closing) stops a run cleanly.

    Jobs not yet started are cancelled; the ones in progress finish, so their
    results reach the checkpoint. A second Ctrl-C quits at once, which loses the
    results of this run's finished jobs too (LangGraph saves them as a batch).
    """
    def handler(signum, frame):  # noqa: ANN001
        if cancel.is_set():
            os._exit(130)  # worker threads would otherwise hold the process open
        cancel.set()
        typer.secho(
            f"\nStopping: waiting for the jobs in progress (up to {in_flight}) to finish so they are saved. "
            "Press Ctrl-C again to quit now and lose this run's progress.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise KeyboardInterrupt

    signals = [signal.SIGINT, signal.SIGTERM] + ([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])
    old = {s: signal.signal(s, handler) for s in signals}
    try:
        yield
    finally:
        for s, h in old.items():
            signal.signal(s, h)


# --- outputs and report -------------------------------------------------------


def run_folder(outputs: Path, run: sqlite3.Row) -> Path:
    """data/outputs/<local start date>-run<N>; stable across resumes."""
    started = datetime.fromisoformat(run["started_at"]).astimezone()
    return outputs / f"{started:%Y-%m-%d}-run{run['id']}"


@dataclass
class ReportRow:
    index: int
    posting: JobPosting | None
    match: Match | None
    result: JobResult
    files: dict[str, Path]  # pdf, docx, posting (relative to the run folder)
    pages: int = 1
    others: int = 0  # same role posted in other places
    warning: str | None = None  # e.g. LaTeX unavailable, Typst used instead


def save_results(
    conn: sqlite3.Connection,
    run_id: int,
    folder: Path,
    outputs: Path,
    results: list[JobResult],
    cv: MasterCV,
    settings: Settings,
    model: str,
) -> list[ReportRow]:
    """Render each passing resume into the run folder and record its files.

    Safe to repeat (e.g. after a resume): a posting already saved in this run is
    not rendered again.
    """
    from autotailor import matching
    from autotailor.resume.output import write_outputs
    from autotailor.resume.render import pdf_page_count

    from autotailor.paths import get_paths
    from autotailor.resume.output import Style

    style = Style.from_settings(settings, get_paths())
    ranked = matching.ranked(conn, settings)
    saved: dict[str, dict[str, Path]] = {}
    for a in db.run_artifacts(conn, run_id):
        saved.setdefault(a["posting_id"], {})[a["kind"]] = outputs / a["path"]

    rows: list[ReportRow] = []
    for r in sorted(results, key=lambda r: r["index"]):
        p = db.get_posting(conn, r["posting_id"])
        m = db.get_match(conn, r["posting_id"])
        others = matching.same_role(p, ranked) if p else []
        row = ReportRow(r["index"], p, m, r, {}, others=len(others))
        if r["status"] == "tailored" and p is not None and r["resume"] is not None:
            done = saved.get(p.id, {})
            if ArtifactKind.RESUME_PDF in done and done[ArtifactKind.RESUME_PDF].exists():
                pdf = done[ArtifactKind.RESUME_PDF]
                row.files = {
                    "pdf": pdf, "docx": done.get(ArtifactKind.RESUME_DOCX, pdf.with_suffix(".docx")),
                    "posting": pdf.parent / "posting.md",
                }  # fmt: skip
                row.pages = pdf_page_count(pdf.read_bytes())
            else:
                resume = TailoredResume.model_validate(r["resume"])
                w = write_outputs(folder, r["index"], p, m, resume, cv, others, style)
                for kind, path in (
                    (ArtifactKind.RESUME_PDF, w.pdf), (ArtifactKind.RESUME_DOCX, w.docx),
                    (ArtifactKind.RESUME_DATA, w.data),
                ):  # fmt: skip
                    db.add_artifact(conn, run_id, p.id, kind.value, str(path.relative_to(outputs)), model)
                row.files = {"pdf": w.pdf, "docx": w.docx, "posting": w.posting}
                row.pages = w.pages
                row.warning = w.warning
                if r.get("history"):  # shown on the posting's Resume tab
                    (w.folder / "attempts.json").write_text(json.dumps(r["history"], indent=1), encoding="utf-8")
        rows.append(row)
    return rows


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _link(label: str, path: Path, folder: Path) -> str:
    return f"[{label}]({path.relative_to(folder).as_posix()})"


def write_report(
    folder: Path,
    run: sqlite3.Row,
    model: str,
    rows: list[ReportRow],
    finished: datetime | None = None,
) -> Path:
    """report.md in the run folder: what was tailored, with links, and what failed and why."""
    folder.mkdir(parents=True, exist_ok=True)
    ok = [r for r in rows if r.result["status"] == "tailored" and r.files]
    bad = [r for r in rows if r not in ok]
    started = datetime.fromisoformat(run["started_at"]).astimezone()
    finished = (finished or datetime.now()).astimezone()
    lines = [
        f"# AutoTailor run {run['id']}",
        "",
        f"Started {started:%Y-%m-%d %H:%M}, finished {finished:%Y-%m-%d %H:%M}. Model: `{model}`.",
        f"{len(ok)} tailored, {len(bad)} not tailored.",
        "",
        "Every resume was checked against the master CV: each bullet comes from a CV bullet, "
        "with no added numbers, tools or names. Read it before sending anyway.",
        "",
    ]
    if ok:
        lines += [
            "## Tailored",
            "",
            "| # | Fit | Company | Role | Location | Resume | Posting |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in ok:
            p, m = r.posting, r.match
            assert p is not None
            fit = f"{m.fit_score:.2f}" if m and m.fit_score is not None else "–"
            where = (p.location or "not stated") + (f" (+{r.others} more)" if r.others else "")
            resume = _link("PDF", r.files["pdf"], folder) + " · " + _link("Word", r.files["docx"], folder)
            if r.pages > 1:
                resume += f" ({r.pages} pages)"
            posting = _link("details", r.files["posting"], folder)
            url = p.canonical_url or p.source_url
            if url:
                posting += f" · [apply]({url})"
            lines.append(
                f"| {r.index} | {fit} | {_cell(p.company)} | {_cell(p.title)} | {_cell(where)} | {resume} | {posting} |"
            )
        lines.append("")
    if bad:
        lines += ["## Not tailored", ""]
        for r in bad:
            name = f"{r.posting.company} | {r.posting.title}" if r.posting else r.result["posting_id"]
            lines.append(f"- **{r.index}. {name}**: {(r.result['message'] or 'no resume was produced').rstrip(':')}.")
            lines += [f"  - {e}" for e in r.result["errors"][:5]]
        lines += [
            "",
            "Retry these with `autotailor tailor <posting id> --force`, or point the `tailor` step at a larger model.",
            "",
        ]
    path = folder / "report.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def counts(rows: list[ReportRow]) -> dict[str, int]:
    tailored = sum(1 for r in rows if r.result["status"] == "tailored" and r.files)
    return {"jobs": len(rows), "tailored": tailored, "failed": len(rows) - tailored}


def parse_counts(run: sqlite3.Row) -> dict[str, int]:
    try:
        return json.loads(run["counts_json"] or "{}")
    except json.JSONDecodeError:
        return {}
