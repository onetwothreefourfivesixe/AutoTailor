"""Store postings from any source: title filter, duplicate check, then save."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from autotailor import db
from autotailor.models import JobPosting


@dataclass
class IngestReport:
    received: int = 0
    filtered_out: int = 0
    too_old: int = 0
    cut_before: int = 0  # deleted earlier (low fit or too old); not added again
    new: list[str] = field(default_factory=list)
    refreshed: int = 0  # already stored under the same ID
    merged: int = 0  # duplicates that added missing details to a stored posting
    duplicates: int = 0  # duplicates with nothing new

    def summary(self) -> str:
        return (
            f"{self.received} received, {self.filtered_out} filtered out by title, "
            + (f"{self.too_old} too old, " if self.too_old else "")
            + (f"{self.cut_before} cut before, " if self.cut_before else "")
            + f"{len(self.new)} new, {self.refreshed} refreshed, "
            f"{self.merged + self.duplicates} duplicates ({self.merged} added details)"
        )


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term.strip())}s?\b", re.IGNORECASE)


def is_recent(p: JobPosting, max_age_days: int, keep_undated: bool = True, now: datetime | None = None) -> bool:
    """Whether a posting is within the date cutoff (always, when max_age_days is 0)."""
    if max_age_days <= 0:
        return True
    if p.posted_at is None:
        return keep_undated
    posted = p.posted_at if p.posted_at.tzinfo else p.posted_at.replace(tzinfo=UTC)
    return posted >= (now or datetime.now(UTC)) - timedelta(days=max_age_days)


def title_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    if any(_term_pattern(t).search(title) for t in exclude if t.strip()):
        return False
    terms = [t for t in include if t.strip()]
    return not terms or any(_term_pattern(t).search(title) for t in terms)


def ingest(
    conn: sqlite3.Connection,
    postings: Iterable[JobPosting],
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    max_age_days: int = 0,
    keep_undated: bool = True,
) -> IngestReport:
    """Save postings, skipping filtered titles and ones older than the date cutoff
    (max_age_days; 0 for none), and folding duplicates into stored ones."""
    report = IngestReport()
    now = datetime.now(UTC)
    for p in postings:
        report.received += 1
        if not title_matches(p.title, include or [], exclude or []):
            report.filtered_out += 1
            continue
        if not is_recent(p, max_age_days, keep_undated, now):
            report.too_old += 1
            continue
        if db.was_cut(conn, p):
            report.cut_before += 1
            continue
        existing_id = db.find_duplicate(conn, p)
        if existing_id:
            if db.fill_missing(conn, existing_id, p):
                report.merged += 1
            else:
                report.duplicates += 1
            continue
        if db.get_posting(conn, p.id):
            report.refreshed += 1
        else:
            report.new.append(p.id)
        db.upsert_posting(conn, p)
    return report
