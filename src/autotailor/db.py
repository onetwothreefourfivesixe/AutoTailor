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
from autotailor.models import FitAssessment, JobPosting, Match, PostingStatus, Requirements, Verdict

SCHEMA_VERSION = 8

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
    status        TEXT NOT NULL,                 -- running | interrupted | done | failed
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


# v3: matching (M3). Pre-rank score and full fit assessment per match, and a
# cache of embedding vectors keyed by what was embedded and with which model.
_SCHEMA_V3 = """
ALTER TABLE matches ADD COLUMN prescore REAL;
ALTER TABLE matches ADD COLUMN fit_json TEXT;
CREATE TABLE IF NOT EXISTS embeddings (
    key        TEXT NOT NULL,                    -- e.g. 'posting:<id>', 'cv:<bullet id>', 'interests'
    model      TEXT NOT NULL,
    text_hash  TEXT NOT NULL,                    -- recompute when the embedded text changes
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,                    -- float32, little-endian
    created_at TEXT NOT NULL,
    PRIMARY KEY (key, model)
);
"""

# Each step upgrades the schema from version N-1 to version N.
_SCHEMA_V4 = """
CREATE TABLE IF NOT EXISTS mail_messages (
    message_id  TEXT PRIMARY KEY,
    origin      TEXT NOT NULL,                  -- drop_folder | imap | smtp
    sender      TEXT,
    subject     TEXT,
    sent_at     TEXT,
    roles       INTEGER NOT NULL DEFAULT 0,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runway_roles (
    job_id         TEXT PRIMARY KEY,            -- Runway's job ID, decoded offline from the email link
    posting_id     TEXT,                        -- the stored posting for it; NULL once skipped
    company        TEXT NOT NULL,
    title          TEXT NOT NULL,
    location       TEXT,
    company_domain TEXT,                        -- from the logo file name in the email (never fetched)
    match_score    REAL,
    message_id     TEXT,
    status         TEXT NOT NULL DEFAULT 'open', -- open | resolved | skipped
    seen_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runway_posting ON runway_roles (posting_id);
CREATE TABLE IF NOT EXISTS company_boards (
    company_key TEXT PRIMARY KEY,               -- normalize_company(name)
    source      TEXT NOT NULL,
    slug        TEXT NOT NULL,
    learned_at  TEXT NOT NULL
);
"""

_SCHEMA_V5 = """
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  TEXT NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    word_limit  INTEGER,
    status      TEXT NOT NULL DEFAULT 'new',   -- new | waiting | points | drafted | failed
    points_json TEXT,                          -- suggested points (AnswerPoint list)
    answer      TEXT,                          -- the humanized draft, when drafted
    problems_json TEXT NOT NULL DEFAULT '[]',  -- fact-check problems left in the last draft
    model       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_posting ON questions (posting_id);
"""

_SCHEMA_V6 = """
CREATE TABLE IF NOT EXISTS tasks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,                 -- fetch | screen | rank | tailor | resume | resolve | mail | answers | run
    args_json     TEXT NOT NULL DEFAULT '[]',    -- extra CLI arguments
    status        TEXT NOT NULL DEFAULT 'queued',-- queued | running | done | failed | cancelled
    done          INTEGER,                       -- progress, when the task reports it
    total         INTEGER,
    message       TEXT,
    log           TEXT NOT NULL DEFAULT '',
    pid           INTEGER,
    cancel        INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);
CREATE TABLE IF NOT EXISTS llm_calls (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    step       TEXT,
    provider   TEXT,
    model      TEXT,
    local      INTEGER,
    item       TEXT,                             -- posting ID or 'question:<id>' the call was for
    task_id    INTEGER,
    messages   TEXT,                             -- the prompt, as JSON [{role, content}]
    response   TEXT,
    error      TEXT,
    ms         INTEGER,
    tokens_in  INTEGER,
    tokens_out INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_item ON llm_calls (item);
CREATE INDEX IF NOT EXISTS idx_llm_calls_at ON llm_calls (at);
CREATE TABLE IF NOT EXISTS overrides (
    posting_id TEXT PRIMARY KEY REFERENCES postings (id) ON DELETE CASCADE,
    verdict    TEXT NOT NULL,                    -- eligible | ineligible
    note       TEXT,
    created_at TEXT NOT NULL
);
"""

_SCHEMA_V7 = """
ALTER TABLE postings ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS applications (
    posting_id  TEXT PRIMARY KEY REFERENCES postings (id) ON DELETE CASCADE,
    status      TEXT NOT NULL,                   -- applied | heard_back | interview | offer | rejected | withdrawn
    applied_at  TEXT NOT NULL,
    run_id      INTEGER,                         -- the run whose resume was sent, if any
    notes       TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    posting_id  TEXT NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    status      TEXT NOT NULL,
    note        TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_events ON application_events (posting_id);
"""

# Postings deleted to keep the database small (below the fit cut-off, or too old). Only this
# small record stays, so fetching or pasting the same posting again doesn't bring it back.
_SCHEMA_V8 = """
CREATE TABLE IF NOT EXISTS cut_postings (
    id            TEXT PRIMARY KEY,
    url_key       TEXT,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    canonical_url TEXT,
    fit_score     REAL,
    reason        TEXT NOT NULL,                 -- low_fit | too_old
    cut_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cut_url_key ON cut_postings (url_key);
"""

_MIGRATIONS = {
    1: (_SCHEMA_V1, None),
    2: (_SCHEMA_V2, _backfill_v2),
    3: (_SCHEMA_V3, None),
    4: (_SCHEMA_V4, None),
    5: (_SCHEMA_V5, None),
    6: (_SCHEMA_V6, None),
    7: (_SCHEMA_V7, None),
    8: (_SCHEMA_V8, None),
}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path, *, any_thread: bool = False) -> sqlite3.Connection:
    """Open the database, creating or upgrading the schema as needed.

    `any_thread`: allow use from a thread other than the one that opened it (the API opens a
    connection per request, and its framework may run the route on another thread; the
    connection is still used by one thread at a time)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # The UI, the background worker and the task it runs share this file: wait for locks
    # instead of failing, and use the write-ahead log so readers don't block the writer.
    conn = sqlite3.connect(path, timeout=15, check_same_thread=not any_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema v{version} is newer than this AutoTailor (v{SCHEMA_VERSION})."
        )
    if version < SCHEMA_VERSION:
        _migrate(conn)
    return conn


def _statements(script: str) -> list[str]:
    """Split a schema script into single statements (executescript would commit between them)."""
    out, buf = [], ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring the schema up to date, safely when several processes open the file at once
    (the API serves concurrent requests; the worker and its task open it too).

    The write lock is taken first and the version read again under it, so whoever comes
    second waits and then finds nothing to do. Each upgrade step runs in one transaction:
    a failure leaves the database at the previous version, never half-upgraded.
    """
    conn.commit()
    previous = conn.isolation_level
    conn.isolation_level = None  # manage the transaction explicitly
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            for target in range(version + 1, SCHEMA_VERSION + 1):
                script, backfill = _MIGRATIONS[target]
                for statement in _statements(script):
                    conn.execute(statement)
                if backfill:
                    backfill(conn)
                conn.execute(f"PRAGMA user_version = {target}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.isolation_level = previous


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


_MATCH_SQL = (
    "SELECT m.*, o.verdict AS o_verdict, o.note AS o_note FROM matches m "
    "LEFT JOIN overrides o ON o.posting_id = m.posting_id"
)


def _row_to_match(row: sqlite3.Row) -> Match:
    """A stored match, with the user's override (if any) applied: overrides win everywhere
    (ranking, shortlist, UI) and survive `screen --rejudge`, which only rewrites `matches`."""
    reasons = json.loads(row["verdict_reasons_json"])
    verdict, original = row["verdict"], None
    keys = row.keys()
    if "o_verdict" in keys and row["o_verdict"]:
        verdict, original = row["o_verdict"], row["verdict"]
        reasons = [f"Overridden by you: {row['o_note'] or 'no note'} (was {original})."] + reasons
    return Match(
        posting_id=row["posting_id"],
        verdict=verdict,
        original_verdict=original,
        verdict_reasons=reasons,
        prescore=row["prescore"],
        fit_score=row["fit_score"],
        fit_reasons=json.loads(row["fit_reasons_json"]),
        fit=FitAssessment.model_validate_json(row["fit_json"]) if row["fit_json"] else None,
    )


def save_prescore(conn: sqlite3.Connection, posting_id: str, prescore: float) -> None:
    """Store the embedding pre-rank score. The posting must already have a match row."""
    conn.execute(
        "UPDATE matches SET prescore = ?, updated_at = ? WHERE posting_id = ?",
        (prescore, _now(), posting_id),
    )
    conn.commit()


def save_fit(
    conn: sqlite3.Connection,
    posting_id: str,
    fit_score: float,
    fit_reasons: list[str],
    fit: FitAssessment,
) -> None:
    """Store a fit score. The posting must already have a match row."""
    conn.execute(
        "UPDATE matches SET fit_score = ?, fit_reasons_json = ?, fit_json = ?, updated_at = ?"
        " WHERE posting_id = ?",
        (fit_score, json.dumps(fit_reasons), fit.model_dump_json(), _now(), posting_id),
    )
    conn.commit()


def get_embedding(
    conn: sqlite3.Connection, key: str, model: str, text_hash: str
) -> list[float] | None:
    """Cached vector, or None if missing or computed from different text."""
    import array

    row = conn.execute(
        "SELECT text_hash, vector FROM embeddings WHERE key = ? AND model = ?", (key, model)
    ).fetchone()
    if row is None or row["text_hash"] != text_hash:
        return None
    vec = array.array("f")
    vec.frombytes(row["vector"])
    return vec.tolist()


def put_embedding(
    conn: sqlite3.Connection, key: str, model: str, text_hash: str, vector: list[float]
) -> None:
    import array

    conn.execute(
        """
        INSERT INTO embeddings (key, model, text_hash, dim, vector, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (key, model) DO UPDATE SET
            text_hash = excluded.text_hash, dim = excluded.dim,
            vector = excluded.vector, created_at = excluded.created_at
        """,
        (key, model, text_hash, len(vector), array.array("f", vector).tobytes(), _now()),
    )


def get_match(conn: sqlite3.Connection, posting_id: str) -> Match | None:
    row = conn.execute(_MATCH_SQL + " WHERE m.posting_id = ?", (posting_id,)).fetchone()
    return _row_to_match(row) if row else None


def all_matches(conn: sqlite3.Connection, verdict: Verdict | None = None) -> dict[str, Match]:
    out = {r["posting_id"]: _row_to_match(r) for r in conn.execute(_MATCH_SQL).fetchall()}
    return {k: m for k, m in out.items() if verdict is None or m.verdict == verdict}


def start_run(conn: sqlite3.Connection, settings_json: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (started_at, status, settings_json) VALUES (?, 'running', ?)",
        (_now(), settings_json),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, counts: dict[str, int]) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = ?, status = ?, counts_json = ? WHERE id = ?",
        (_now(), status, json.dumps(counts), run_id),
    )
    conn.commit()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def list_runs(conn: sqlite3.Connection, limit: int | None = 20) -> list[sqlite3.Row]:
    sql = "SELECT * FROM runs ORDER BY id DESC"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql).fetchall()


def unfinished_runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Runs that were interrupted or never finished (e.g. the process was killed), newest first."""
    return conn.execute(
        "SELECT * FROM runs WHERE status IN ('running', 'interrupted') ORDER BY id DESC"
    ).fetchall()


def count_runs_since(conn: sqlite3.Connection, since: datetime) -> int:
    """Runs started at or after `since` that count toward the run limits (failed ones don't)."""
    return conn.execute(
        "SELECT COUNT(*) FROM runs WHERE started_at >= ? AND status != 'failed'",
        (since.astimezone(UTC).isoformat(timespec="seconds"),),
    ).fetchone()[0]


def set_run_status(conn: sqlite3.Connection, run_id: int, status: str) -> None:
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    conn.commit()


def run_artifacts(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM artifacts WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()


def add_artifact(
    conn: sqlite3.Connection, run_id: int, posting_id: str, kind: str, path: str, model: str | None
) -> None:
    conn.execute(
        "INSERT INTO artifacts (run_id, posting_id, kind, path, model, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, posting_id, kind, path, model, _now()),
    )
    conn.commit()


def artifacts_for(conn: sqlite3.Connection, posting_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM artifacts WHERE posting_id = ? ORDER BY created_at DESC, id DESC", (posting_id,)
    ).fetchall()


def set_override(conn: sqlite3.Connection, posting_id: str, verdict: Verdict, note: str | None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO overrides (posting_id, verdict, note, created_at) VALUES (?, ?, ?, ?)",
        (posting_id, verdict.value, (note or "").strip() or None, _now()),
    )
    conn.commit()


def clear_override(conn: sqlite3.Connection, posting_id: str) -> None:
    conn.execute("DELETE FROM overrides WHERE posting_id = ?", (posting_id,))
    conn.commit()


def delete_posting(conn: sqlite3.Connection, posting_id: str) -> None:
    """Remove a posting and its match (e.g. a Runway placeholder merged into the employer's posting)."""
    conn.execute("DELETE FROM postings WHERE id = ?", (posting_id,))
    conn.execute("DELETE FROM embeddings WHERE key = ?", (f"posting:{posting_id}",))
    conn.commit()


def set_status(conn: sqlite3.Connection, posting_id: str, status: PostingStatus) -> None:
    conn.execute("UPDATE postings SET status = ?, updated_at = ? WHERE id = ?", (status.value, _now(), posting_id))
    conn.commit()


# --- mail and Runway (M7) ------------------------------------------------------


def mail_seen(conn: sqlite3.Connection, message_id: str) -> bool:
    return conn.execute("SELECT 1 FROM mail_messages WHERE message_id = ?", (message_id,)).fetchone() is not None


def record_mail(
    conn: sqlite3.Connection, message_id: str, origin: str, sender: str | None, subject: str | None,
    sent_at: datetime | None, roles: int,
) -> None:  # fmt: skip
    conn.execute(
        "INSERT OR REPLACE INTO mail_messages (message_id, origin, sender, subject, sent_at, roles, imported_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (message_id, origin, sender, subject, sent_at.isoformat() if sent_at else None, roles, _now()),
    )
    conn.commit()


def runway_role(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runway_roles WHERE job_id = ?", (job_id,)).fetchone()


def runway_role_for_posting(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runway_roles WHERE posting_id = ?", (posting_id,)).fetchone()


def add_runway_role(
    conn: sqlite3.Connection, job_id: str, posting_id: str, company: str, title: str, location: str | None,
    company_domain: str | None, match_score: float | None, message_id: str | None, status: str = "open",
) -> None:  # fmt: skip
    conn.execute(
        "INSERT OR IGNORE INTO runway_roles (job_id, posting_id, company, title, location, company_domain, "
        "match_score, message_id, status, seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (job_id, posting_id, company, title, location, company_domain, match_score, message_id, status, _now()),
    )
    conn.commit()


def update_runway_role(conn: sqlite3.Connection, job_id: str, posting_id: str | None, status: str) -> None:
    conn.execute("UPDATE runway_roles SET posting_id = ?, status = ? WHERE job_id = ?", (posting_id, status, job_id))
    conn.commit()


def list_runway_roles(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM runway_roles", ()
    if status:
        sql, args = sql + " WHERE status = ?", (status,)
    return conn.execute(sql + " ORDER BY seen_at DESC, job_id", args).fetchall()


def learned_board(conn: sqlite3.Connection, company_key: str) -> tuple[str, str] | None:
    row = conn.execute("SELECT source, slug FROM company_boards WHERE company_key = ?", (company_key,)).fetchone()
    return (row["source"], row["slug"]) if row else None


def learn_board(conn: sqlite3.Connection, company_key: str, source: str, slug: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO company_boards (company_key, source, slug, learned_at) VALUES (?, ?, ?, ?)",
        (company_key, source, slug, _now()),
    )
    conn.commit()


# --- short-answer questions (M8) ---------------------------------------------------


def add_question(conn: sqlite3.Connection, posting_id: str, text: str, word_limit: int | None) -> int:
    now = _now()
    cur = conn.execute(
        "INSERT INTO questions (posting_id, text, word_limit, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (posting_id, text, word_limit, now, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_question(conn: sqlite3.Connection, question_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()


def list_questions(conn: sqlite3.Connection, posting_id: str | None = None) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM questions", ()
    if posting_id:
        sql, args = sql + " WHERE posting_id = ?", (posting_id,)
    return conn.execute(sql + " ORDER BY posting_id, id", args).fetchall()


def update_question(conn: sqlite3.Connection, question_id: int, **fields) -> None:  # noqa: ANN003
    """Set any of: status, points (list), answer, problems (list), model."""
    cols = {"status": "status", "answer": "answer", "model": "model", "points": "points_json", "problems": "problems_json"}
    sets, args = [], []
    for k, v in fields.items():
        sets.append(f"{cols[k]} = ?")
        args.append(json.dumps(v) if k in ("points", "problems") else v)
    conn.execute(f"UPDATE questions SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*args, _now(), question_id))  # noqa: S608
    conn.commit()


def delete_question(conn: sqlite3.Connection, question_id: int) -> None:
    conn.execute("DELETE FROM questions WHERE id = ?", (question_id,))
    conn.commit()


# --- hidden postings and applications (M12) -------------------------------------------

APPLICATION_STATUSES = ("applied", "heard_back", "interview", "offer", "rejected", "withdrawn")


def set_hidden(conn: sqlite3.Connection, posting_id: str, hidden: bool) -> None:
    conn.execute("UPDATE postings SET hidden = ? WHERE id = ?", (int(hidden), posting_id))
    conn.commit()


def hidden_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM postings WHERE hidden = 1")}


def get_application(conn: sqlite3.Connection, posting_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM applications WHERE posting_id = ?", (posting_id,)).fetchone()


def list_applications(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM applications ORDER BY applied_at DESC").fetchall()


def application_events(conn: sqlite3.Connection, posting_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM application_events WHERE posting_id = ? ORDER BY id", (posting_id,)).fetchall()


def set_application(
    conn: sqlite3.Connection, posting_id: str, status: str, notes: str | None = None,
    applied_at: str | None = None, run_id: int | None = None,
) -> sqlite3.Row:  # fmt: skip
    """Record or update an application; each status change is kept as an event."""
    if status not in APPLICATION_STATUSES:
        raise ValueError(f"status must be one of {', '.join(APPLICATION_STATUSES)}")
    now = _now()
    old = get_application(conn, posting_id)
    if old is None:
        conn.execute(
            "INSERT INTO applications (posting_id, status, applied_at, run_id, notes, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (posting_id, status, applied_at or now, run_id, notes or "", now),
        )
    else:
        conn.execute(
            "UPDATE applications SET status = ?, notes = COALESCE(?, notes), applied_at = COALESCE(?, applied_at), "
            "run_id = COALESCE(?, run_id), updated_at = ? WHERE posting_id = ?",
            (status, notes, applied_at, run_id, now, posting_id),
        )
    if old is None or old["status"] != status:
        conn.execute("INSERT INTO application_events (posting_id, status, note, at) VALUES (?, ?, ?, ?)",
                     (posting_id, status, notes, now))  # fmt: skip
    conn.commit()
    return get_application(conn, posting_id)


def delete_application(conn: sqlite3.Connection, posting_id: str) -> None:
    conn.execute("DELETE FROM application_events WHERE posting_id = ?", (posting_id,))
    conn.execute("DELETE FROM applications WHERE posting_id = ?", (posting_id,))
    conn.commit()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 (fixed names)
        for t in ("postings", "matches", "runs", "artifacts")
    }


# --- cutting postings (keeps the database small) ------------------------------------------------


def protected_ids(conn: sqlite3.Connection) -> set[str]:
    """Postings never cut: ones with a resume, an application, questions or a verdict override."""
    out: set[str] = set()
    for table in ("artifacts", "applications", "questions", "overrides"):
        out |= {r[0] for r in conn.execute(f"SELECT DISTINCT posting_id FROM {table}")}  # noqa: S608 (fixed names)
    return out


def cut_postings(conn: sqlite3.Connection, cuts: dict[str, tuple[str, float | None]]) -> int:
    """Delete postings {id: (reason, fit score)} with everything stored for them, keeping a
    small record of each so they aren't fetched again. Returns how many were deleted."""
    n = 0
    for pid, (reason, fit) in cuts.items():
        row = conn.execute("SELECT * FROM postings WHERE id = ?", (pid,)).fetchone()
        if row is None:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO cut_postings (id, url_key, company, title, location, canonical_url, fit_score,"
            " reason, cut_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, row["url_key"], row["company"], row["title"], row["location"], row["canonical_url"], fit, reason, _now()),
        )
        conn.execute("DELETE FROM embeddings WHERE key = ?", (f"posting:{pid}",))
        conn.execute("UPDATE runway_roles SET posting_id = NULL, status = 'skipped' WHERE posting_id = ?", (pid,))
        conn.execute("DELETE FROM postings WHERE id = ?", (pid,))  # matches etc. go with it (ON DELETE CASCADE)
        n += 1
    conn.commit()
    return n


def was_cut(conn: sqlite3.Connection, p: JobPosting) -> bool:
    """Whether this posting (same ID or same link) was cut before."""
    if conn.execute("SELECT 1 FROM cut_postings WHERE id = ?", (p.id,)).fetchone():
        return True
    key = normalize_url(p.canonical_url)
    return bool(key and conn.execute("SELECT 1 FROM cut_postings WHERE url_key = ?", (key,)).fetchone())


def list_cut(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM cut_postings ORDER BY cut_at DESC, company").fetchall()


def uncut(conn: sqlite3.Connection, posting_id: str) -> bool:
    """Forget a cut, so the posting can be fetched or added again."""
    cur = conn.execute("DELETE FROM cut_postings WHERE id = ?", (posting_id,))
    conn.commit()
    return cur.rowcount > 0


def compact(conn: sqlite3.Connection) -> None:
    """Give the space of deleted rows back to the disk (SQLite keeps it otherwise)."""
    conn.commit()
    conn.execute("VACUUM")
