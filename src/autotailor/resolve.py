"""Resolve Runway roles to the employer's own posting (M7).

A Runway email gives a title, company and location but no description and no
employer link. `Resolver` does the two concrete jobs; the resolve graph
(chains/resolve_graph.py) decides when to do which and when to pause and ask
the user:

- `auto_match`: find the company's public job board (Greenhouse, Lever, Ashby)
  and the same role on it. Boards come from: one learned earlier for this
  company, the boards in config.toml, then slugs guessed from the company name
  and its web domain. Only employer boards are contacted, never Runway.
- `apply_answer`: use what the user found: the employer's posting URL, or the
  posting text. Runway links are refused; the user opens those in a browser.

Either way the Runway placeholder is replaced by the employer's posting (under
the employer's ID), keeping Runway's match score.
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from autotailor import db
from autotailor.config import Settings
from autotailor.dedupe import locations_compatible, normalize_company, normalize_title
from autotailor.models import JobPosting, PostingStatus, Source
from autotailor.sources import is_no_fetch
from autotailor.sources.ats import fetch_board

ATS = (Source.GREENHOUSE, Source.ASHBY, Source.LEVER)
TITLE_SIMILARITY = 0.9  # fuzzy title match threshold, when no exact title match

Answer = dict[str, Any]  # {"url": str} | {"text": str} | {"skip": True}


def slug_guesses(company: str, domain: str | None) -> list[str]:
    """'Muon Space', 'muonspace.com' -> ['muonspace', 'muon-space', 'muon']."""
    words = normalize_company(company).split()
    out = []
    if domain:
        out.append(re.sub(r"[^a-z0-9-]", "", domain.lower().split(".")[0]))
    if words:
        out += ["".join(words), "-".join(words)]
        if len(words) > 1 and len(words[0]) > 3:
            out.append(words[0])
    return [s for s in dict.fromkeys(out) if s]


def best_match(role: JobPosting, board: list[JobPosting]) -> JobPosting | None:
    """The board posting for the same role: same title (exact after normalizing, else very
    similar) and a compatible location. None when unsure."""
    want = normalize_title(role.title)
    here = [q for q in board if locations_compatible(q.location, role.location)]
    exact = [q for q in here if normalize_title(q.title) == want]
    if exact:
        return exact[0]
    scored = sorted(
        ((difflib.SequenceMatcher(a=want, b=normalize_title(q.title)).ratio(), q) for q in here),
        key=lambda t: t[0],
        reverse=True,
    )
    if scored and scored[0][0] >= TITLE_SIMILARITY and (len(scored) == 1 or scored[1][0] < scored[0][0]):
        return scored[0][1]
    return None


class Resolver:
    """The concrete steps the resolve graph calls. Not thread-safe (one SQLite connection)."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: httpx.Client,
        settings: Settings,
        extract: Callable[[str], Any] | None = None,
        fetch: Callable[[httpx.Client, Source, str], list[JobPosting]] | None = None,
    ) -> None:
        self.conn, self.client, self.settings = conn, client, settings
        self.extract, self.fetch = extract, fetch or fetch_board
        self._boards: dict[tuple[Source, str], list[JobPosting] | None] = {}

    # --- automatic -------------------------------------------------------------

    def _board(self, source: Source, slug: str) -> list[JobPosting] | None:
        key = (source, slug)
        if key not in self._boards:
            try:
                self._boards[key] = self.fetch(self.client, source, slug) or None
            except (httpx.HTTPError, ValueError):
                self._boards[key] = None
        return self._boards[key]

    def candidate_boards(self, p: JobPosting, domain: str | None) -> list[tuple[Source, str]]:
        key = normalize_company(p.company)
        out: list[tuple[Source, str]] = []
        if learned := db.learned_board(self.conn, key):
            out.append((Source(learned[0]), learned[1]))
        guesses = slug_guesses(p.company, domain)
        configured = self.settings.sources
        for source in ATS:
            for slug in getattr(configured, source.value):
                if slug.lower() in guesses or normalize_company(slug.replace("-", " ")) == key:
                    out.append((source, slug))
        out += [(source, slug) for slug in guesses for source in ATS]
        return list(dict.fromkeys(out))

    def auto_match(self, posting_id: str) -> bool:
        """Find the role on the employer's job board. True if resolved (or already was)."""
        p = db.get_posting(self.conn, posting_id)
        if p is None:
            return False
        if p.description:
            self._mark(posting_id, "resolved", posting_id)
            return True
        role = db.runway_role_for_posting(self.conn, posting_id)
        domain = role["company_domain"] if role else None
        for source, slug in self.candidate_boards(p, domain):
            board = self._board(source, slug)
            if not board:
                continue
            match = best_match(p, board)
            if match:
                db.learn_board(self.conn, normalize_company(p.company), source.value, slug)
                self._replace(p, match)
                return True
        db.set_status(self.conn, posting_id, PostingStatus.NEEDS_LINK)
        return False

    # --- the user's answer -------------------------------------------------------------

    def question(self, posting_id: str) -> dict[str, Any]:
        """What to show the user when asking for the posting."""
        p = db.get_posting(self.conn, posting_id)
        role = db.runway_role_for_posting(self.conn, posting_id)
        return {
            "posting_id": posting_id,
            "company": p.company if p else (role["company"] if role else "?"),
            "title": p.title if p else (role["title"] if role else "?"),
            "location": p.location if p else None,
            "runway_url": p.source_url if p else None,
            "hint": "Open the Runway link in your browser, follow it to the employer's posting, and paste that "
            "page's URL (or the posting text).",
        }

    def apply_answer(self, posting_id: str, answer: Answer) -> str | None:
        """Use the user's answer. Returns None on success, else what went wrong (to show them)."""
        p = db.get_posting(self.conn, posting_id)
        if p is None:
            return f"{posting_id} is no longer stored."
        if answer.get("skip"):
            self._skip(p)
            return None
        try:
            if url := (answer.get("url") or "").strip():
                host = urlparse(url if "://" in url else f"https://{url}").netloc
                if is_no_fetch(host):
                    return (f"That link is on {host}, which AutoTailor doesn't open. Open it in your browser, follow "
                            "it to the employer's own posting, and paste that URL (or the posting text).")  # fmt: skip
                from autotailor.sources.paste import from_url

                found = from_url(self.client, url, self._extractor())
            elif text := (answer.get("text") or "").strip():
                from autotailor.sources.paste import from_text

                found = from_text(text, self._extractor())
            else:
                return "Give a URL, the posting text, or skip."
        except httpx.HTTPStatusError as e:
            return f"The page returned HTTP {e.response.status_code}."
        except (httpx.HTTPError, ValueError) as e:
            return str(e)
        self._replace(p, found)
        return None

    def _extractor(self) -> Callable[[str], Any]:
        if self.extract is None:
            from autotailor.chains.extract_posting import extract_posting_fields

            self.extract = lambda text: extract_posting_fields(text, self.settings)
        return self.extract

    # --- storage ---------------------------------------------------------------------

    def _replace(self, placeholder: JobPosting, found: JobPosting) -> None:
        """Store the employer's posting (keeping Runway's score) and drop the placeholder."""
        found = found.model_copy(update={"external_score": found.external_score or placeholder.external_score})
        if found.id == placeholder.id:
            db.upsert_posting(self.conn, found)
            self._mark(placeholder.id, "resolved", placeholder.id)
            return
        existing = db.find_duplicate(self.conn, found) or (found.id if db.get_posting(self.conn, found.id) else None)
        if existing and existing != placeholder.id:
            db.fill_missing(self.conn, existing, found)
            db.fill_missing(self.conn, existing, placeholder)
            target = existing
        else:
            db.upsert_posting(self.conn, found)
            target = found.id
        self._mark(placeholder.id, "resolved", target)
        if target != placeholder.id:
            db.delete_posting(self.conn, placeholder.id)

    def _skip(self, p: JobPosting) -> None:
        self._mark(p.id, "skipped", None)
        if p.source == Source.RUNWAY:
            db.delete_posting(self.conn, p.id)

    def _mark(self, posting_id: str, status: str, new_posting_id: str | None) -> None:
        role = db.runway_role_for_posting(self.conn, posting_id)
        if role:
            db.update_runway_role(self.conn, role["job_id"], new_posting_id, status)


def open_roles(conn: sqlite3.Connection) -> list[str]:
    """Posting IDs of Runway roles not yet resolved or skipped."""
    return [r["posting_id"] for r in db.list_runway_roles(conn, "open") if r["posting_id"]]
