"""SQLite storage for postings, matches, runs and generated files.

Key columns are real columns (for filtering and dedupe); nested data is stored
as JSON produced by the Pydantic models in `autotailor.models`.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from autotailor.dedupe import dedupe_key, locations_compatible, normalize_url
from autotailor.models import JobPosting, Match, PostingStatus, Requirements, Verdict

SCHEMA_VERSION = 2

_SCHEMA_V1 = """
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

# v2: normalized keys for duplicate detection (see autotailor.dedupe).
_SCHEMA_V2 = """
ALTER TABLE postings ADD COLUMN url_key TEXT;
ALTER TABLE postings ADD COLUMN dedupe_key TEXT;
CREATE INDEX IF NOT EXISTS idx_postings_url_key ON postings (url_key);
CREATE INDEX IF NOT EXISTS idx_postings_dedupe_key ON postings (dedupe_key);
"""


def _backfill_v2(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT id, canonical_url, company, title FROM postings").fetchall()
    for r in rows:
        conn.execute(
            "UPDATE postings SET url_key = ?, dedupe_key = ? WHERE id = ?",
            (normalize_url(r["canonical_url"]), dedupe_key(r["company"], r["title"]), r["id"]),
        )


# Each step upgrades the schema from version N-1 to version N.
_MIGRATIONS = {
    1: (_SCHEMA_V1, None),
    2: (_SCHEMA_V2, _backfill_v2),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    """Open the database, creating or upgrading the schema as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema v{version} is newer than this AutoTailor (v{SCHEMA_VERSION})."
        )
    for target in range(version + 1, SCHEMA_VERSION + 1):
        script, backfill = _MIGRATIONS[target]
        conn.executescript(script)
        if backfill:
            backfill(conn)
        conn.execute(f"PRAGMA user_version = {target}")
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
                              requirements_json, first_seen_at, updated_at, url_key, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            updated_at        = excluded.updated_at,
            url_key           = COALESCE(excluded.url_key, url_key),
            dedupe_key        = excluded.dedupe_key
        """,
        (
            p.id, p.source.value, p.source_ref, p.source_url, p.canonical_url, p.company, p.title,
            p.location, p.posted_at.isoformat() if p.posted_at else None, p.description,
            p.external_score, p.status.value,
            p.requirements.model_dump_json() if p.requirements else None, now, now,
            normalize_url(p.canonical_url), dedupe_key(p.company, p.title),
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


def find_duplicate(conn: sqlite3.Connection, p: JobPosting) -> str | None:
    """ID of an already-stored posting that is the same role as `p`, if any.

    A posting stored under `p.id` itself is not a duplicate; it is the same
    record being refreshed.
    """
    if conn.execute("SELECT 1 FROM postings WHERE id = ?", (p.id,)).fetchone():
        return None
    url_key = normalize_url(p.canonical_url)
    if url_key:
        row = conn.execute(
            "SELECT id FROM postings WHERE url_key = ? ORDER BY first_seen_at LIMIT 1", (url_key,)
        ).fetchone()
        if row:
            return row["id"]
    rows = conn.execute(
        "SELECT id, location FROM postings WHERE dedupe_key = ? ORDER BY first_seen_at",
        (dedupe_key(p.company, p.title),),
    ).fetchall()
    for row in rows:
        if locations_compatible(row["location"], p.location):
            return row["id"]
    return None


def fill_missing(conn: sqlite3.Connection, existing_id: str, p: JobPosting) -> bool:
    """Copy fields from duplicate `p` into the stored posting, only where it has none.

    Returns True if anything was filled in.
    """
    existing = get_posting(conn, existing_id)
    if existing is None:
        raise LookupError(existing_id)
    fields = ("canonical_url", "location", "posted_at", "description", "external_score")
    if not any(getattr(existing, f) is None and getattr(p, f) is not None for f in fields):
        return False
    conn.execute(
        """
        UPDATE postings SET
            status         = CASE WHEN description IS NULL AND ? IS NOT NULL THEN ? ELSE status END,
            canonical_url  = COALESCE(canonical_url, ?),
            url_key        = COALESCE(url_key, ?),
            location       = COALESCE(location, ?),
            posted_at      = COALESCE(posted_at, ?),
            description    = COALESCE(description, ?),
            external_score = COALESCE(external_score, ?),
            updated_at     = ?
        WHERE id = ?
        """,
        (
            p.description, PostingStatus.RESOLVED.value,
            p.canonical_url, normalize_url(p.canonical_url), p.location,
            p.posted_at.isoformat() if p.posted_at else None, p.description, p.external_score,
            _now(), existing_id,
        ),
    )  # fmt: skip
    conn.commit()
    return True


def list_postings(
    conn: sqlite3.Connection,
    limit: int | None = 50,
    status: PostingStatus | None = None,
    source: str | None = None,
) -> list[JobPosting]:
    """Most recently posted first; postings without a date sort by when they were first seen."""
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status.value)
    if source:
        where.append("source = ?")
        args.append(source)
    sql = "SELECT * FROM postings"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY COALESCE(posted_at, first_seen_at) DESC LIMIT ?"
    return [_row_to_posting(r) for r in conn.execute(sql, (*args, limit or -1)).fetchall()]


def set_requirements(conn: sqlite3.Connection, posting_id: str, req: Requirements | None) -> None:
    """Store (or clear) the requirements extracted for a posting, replacing any earlier ones."""
    conn.execute(
        "UPDATE postings SET requirements_json = ?, updated_at = ? WHERE id = ?",
        (req.model_dump_json() if req else None, _now(), posting_id),
    )
    conn.commit()


def save_match(conn: sqlite3.Connection, m: Match) -> None:
    """Store an eligibility verdict. Fit fields are only written when set, so a
    re-check of eligibility never erases an earlier fit score."""
    conn.execute(
        """
        INSERT INTO matches (posting_id, verdict, verdict_reasons_json, fit_score, fit_reasons_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (posting_id) DO UPDATE SET
            verdict              = excluded.verdict,
            verdict_reasons_json = excluded.verdict_reasons_json,
            fit_score            = COALESCE(excluded.fit_score, fit_score),
            fit_reasons_json     = CASE WHEN excluded.fit_score IS NULL THEN fit_reasons_json
                                        ELSE excluded.fit_reasons_json END,
            updated_at           = excluded.updated_at
        """,
        (
            m.posting_id, m.verdict.value, json.dumps(m.verdict_reasons), m.fit_score,
            json.dumps(m.fit_reasons), _now(),
        ),
    )  # fmt: skip
    conn.commit()


def _row_to_match(row: sqlite3.Row) -> Match:
    return Match(
        posting_id=row["posting_id"],
        verdict=row["verdict"],
        verdict_reasons=json.loads(row["verdict_reasons_json"]),
        fit_score=row["fit_score"],
        fit_reasons=json.loads(row["fit_reasons_json"]),
    )


def get_match(conn: sqlite3.Connection, posting_id: str) -> Match | None:
    row = conn.execute("SELECT * FROM matches WHERE posting_id = ?", (posting_id,)).fetchone()
    return _row_to_match(row) if row else None


def all_matches(conn: sqlite3.Connection, verdict: Verdict | None = None) -> dict[str, Match]:
    sql, args = "SELECT * FROM matches", ()
    if verdict:
        sql, args = sql + " WHERE verdict = ?", (verdict.value,)
    return {r["posting_id"]: _row_to_match(r) for r in conn.execute(sql, args).fetchall()}


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 (fixed names)
        for t in ("postings", "matches", "runs", "artifacts")
    }
