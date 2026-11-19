"""Store postings from any source: title filter, duplicate check, then save."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field

from autotailor import db
from autotailor.models import JobPosting


@dataclass
class IngestReport:
    received: int = 0
    filtered_out: int = 0
    new: list[str] = field(default_factory=list)
    refreshed: int = 0  # already stored under the same ID
    merged: int = 0  # duplicates that added missing details to a stored posting
    duplicates: int = 0  # duplicates with nothing new

    def summary(self) -> str:
        return (
            f"{self.received} received, {self.filtered_out} filtered out by title, "
            f"{len(self.new)} new, {self.refreshed} refreshed, "
            f"{self.merged + self.duplicates} duplicates ({self.merged} added details)"
        )


def _term_pattern(term: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(term.strip())}s?\b", re.IGNORECASE)


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
) -> IngestReport:
    """Save postings, skipping filtered titles and folding duplicates into stored ones."""
    report = IngestReport()
    for p in postings:
        report.received += 1
        if not title_matches(p.title, include or [], exclude or []):
            report.filtered_out += 1
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
