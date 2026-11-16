"""SQLite storage for postings, matches, runs and generated files.

Key columns are real columns (for filtering and dedupe); nested data is stored
as JSON produced by the Pydantic models in `autotailor.models`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from autotailor.models import JobPosting, Requirements

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id                TEXT PRIMARY KEY,          -- '<source>:<source_ref>'
    source            TEXT NOT NULL,
    source_ref        TEXT NOT NULL,
    source_url        TEXT,
    canonical_url     TEXT,
    company           TEXT NOT NULL,
    title             TEXT NOT NULL,
    location          TEXT,
    posted_at         TEXT,
    description       TEXT,
    external_score    REAL,
    status            TEXT NOT NULL,
    requirements_json TEXT,
    first_seen_at     TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (source, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_postings_canonical ON postings (canonical_url);
CREATE INDEX IF NOT EXISTS idx_postings_company_title ON postings (company, title);

CREATE TABLE IF NOT EXISTS matches (
    posting_id           TEXT PRIMARY KEY REFERENCES postings (id) ON DELETE CASCADE,
    verdict              TEXT NOT NULL,
    verdict_reasons_json TEXT NOT NULL DEFAULT '[]',
    fit_score            REAL,
    fit_reasons_json     TEXT NOT NULL DEFAULT '[]',
    updated_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,                 -- running | done | failed
    settings_json TEXT NOT NULL,                 -- snapshot of settings used
    counts_json   TEXT NOT NULL DEFAULT '{}'     -- pulled, ineligible, unclear, tailored
);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    posting_id TEXT NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    path       TEXT NOT NULL,                    -- relative to the outputs directory
    model      TEXT,                             -- '<provider>:<model>' that produced it
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_run ON artifacts (run_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    """Open the database, creating the file and schema if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema v{version} is newer than this AutoTailor (v{SCHEMA_VERSION})."
        )
    if version < SCHEMA_VERSION:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    return conn


def upsert_posting(conn: sqlite3.Connection, p: JobPosting) -> None:
    """Insert a posting, or update it if already known.

    On update, fields the new record lacks (None) keep their stored value, so a
    bare re-import never erases a description fetched earlier.
    """
    now = _now()
    conn.execute(
        """
        INSERT INTO postings (id, source, source_ref, source_url, canonical_url, company, title,
                              location, posted_at, description, external_score, status,
                              requirements_json, first_seen_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            source_url        = COALESCE(excluded.source_url, source_url),
            canonical_url     = COALESCE(excluded.canonical_url, canonical_url),
            company           = excluded.company,
            title             = excluded.title,
            location          = COALESCE(excluded.location, location),
            posted_at         = COALESCE(excluded.posted_at, posted_at),
            description       = COALESCE(excluded.description, description),
            external_score    = COALESCE(excluded.external_score, external_score),
            status            = CASE WHEN excluded.description IS NULL AND description IS NOT NULL
                                     THEN status ELSE excluded.status END,
            requirements_json = COALESCE(excluded.requirements_json, requirements_json),
            updated_at        = excluded.updated_at
        """,
        (
            p.id, p.source.value, p.source_ref, p.source_url, p.canonical_url, p.company, p.title,
            p.location, p.posted_at.isoformat() if p.posted_at else None, p.description,
            p.external_score, p.status.value,
            p.requirements.model_dump_json() if p.requirements else None, now, now,
        ),
    )  # fmt: skip
    conn.commit()


def _row_to_posting(row: sqlite3.Row) -> JobPosting:
    return JobPosting(
        id=row["id"],
        source=row["source"],
        source_ref=row["source_ref"],
        source_url=row["source_url"],
        canonical_url=row["canonical_url"],
        company=row["company"],
        title=row["title"],
        location=row["location"],
        posted_at=row["posted_at"],
        description=row["description"],
        external_score=row["external_score"],
        status=row["status"],
        requirements=(
            Requirements.model_validate_json(row["requirements_json"])
            if row["requirements_json"]
            else None
        ),
    )


def get_posting(conn: sqlite3.Connection, posting_id: str) -> JobPosting | None:
    row = conn.execute("SELECT * FROM postings WHERE id = ?", (posting_id,)).fetchone()
    return _row_to_posting(row) if row else None


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 (fixed names)
        for t in ("postings", "matches", "runs", "artifacts")
    }
