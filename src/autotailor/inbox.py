"""Everything waiting on the user, in one list (M10, the Needs-you page)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from autotailor import db
from autotailor.config import Settings
from autotailor.models import ArtifactKind, Verdict

TOP_UNCLEAR = 15  # unclear verdicts are only worth a look near the top of the ranking


@dataclass
class Need:
    kind: str  # runway_link | answer_choice | unclear | resume_pages | answer_problems | interrupted_run
    title: str
    detail: str
    ref: str  # posting ID, question ID or run ID
    when: str | None = None


def collect(conn: sqlite3.Connection, settings: Settings, outputs: Path, checkpoints: Path | None = None) -> list[Need]:
    from autotailor import matching

    needs: list[Need] = []

    # Runway roles waiting for an employer link (the resolve graph paused on them).
    for r in db.list_runway_roles(conn, "open"):
        p = db.get_posting(conn, r["posting_id"]) if r["posting_id"] else None
        if p is not None and p.status.value == "needs_link":
            needs.append(Need("runway_link", f"{r['company']} | {r['title']}",
                              "Couldn't find it on the employer's job board: paste the posting's link or text.",
                              r["posting_id"], r["seen_at"]))  # fmt: skip

    # Short-answer questions waiting for the draft / points-only choice.
    for q in db.list_questions(conn):
        p = db.get_posting(conn, q["posting_id"])
        name = f"{p.company} | {p.title}" if p else q["posting_id"]
        if q["status"] == "waiting":
            needs.append(Need("answer_choice", q["text"][:90], f"{name}: points suggested; draft an answer or keep the points?",
                              str(q["id"]), q["updated_at"]))  # fmt: skip
        elif q["status"] == "needs_review":
            problems = json.loads(q["problems_json"] or "[]")
            needs.append(Need("answer_problems", q["text"][:90], f"{name}: {len(problems)} problem(s) left in the draft.",
                              str(q["id"]), q["updated_at"]))  # fmt: skip

    # Unclear verdicts near the top of the ranking.
    for p, m in matching.ranked(conn, settings)[:TOP_UNCLEAR]:
        if m.verdict == Verdict.UNCLEAR:
            needs.append(Need("unclear", f"{p.company} | {p.title}",
                              (m.verdict_reasons[0] if m.verdict_reasons else "Requirements unclear.") + " Confirm or override.",
                              p.id))  # fmt: skip

    # Saved resumes longer than a page (in the latest run that has any).
    from autotailor.resume.render import pdf_page_count

    for run in db.list_runs(conn, 5):
        pdfs = [a for a in db.run_artifacts(conn, run["id"]) if a["kind"] == ArtifactKind.RESUME_PDF.value]
        for a in pdfs:
            f = outputs / a["path"]
            if f.exists() and pdf_page_count(f.read_bytes()) > 1:
                p = db.get_posting(conn, a["posting_id"])
                needs.append(Need("resume_pages", f"{p.company} | {p.title}" if p else a["posting_id"],
                                  f"Run {run['id']}: the resume is over one page. Lower tailor.max_bullets or trim it.",
                                  a["posting_id"], a["created_at"]))  # fmt: skip
        if pdfs:
            break

    # Runs stopped part way.
    for r in db.unfinished_runs(conn):
        needs.append(Need("interrupted_run", f"Run {r['id']}", "Stopped part way: resume it to finish.", str(r["id"]),
                          r["started_at"]))  # fmt: skip

    return needs
