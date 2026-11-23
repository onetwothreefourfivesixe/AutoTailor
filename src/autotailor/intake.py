"""Reading pasted and dropped input into candidates, previewed before saving (M10, Add page).

Everything ends in the existing import code: job URLs and text through
sources/paste.py, emails through mail.import_raw. Nothing is stored until
`save` is called with the candidates the user kept.
"""

from __future__ import annotations

import io
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from autotailor import db
from autotailor.config import Settings
from autotailor.models import JobPosting
from autotailor.text import html_to_text

TEXT_SUFFIXES = (".txt", ".md")
HTML_SUFFIXES = (".html", ".htm")
_HEADER = re.compile(r"^(From|To|Subject|Date|Received|Return-Path|Delivered-To|MIME-Version|Message-ID):", re.M | re.I)


@dataclass
class Candidate:
    source: str  # what the user gave: a URL, "pasted text", or a file name
    kind: str  # "posting" | "email" | "error"
    posting: JobPosting | None = None
    raw_email: bytes | None = None
    roles: list[Any] = field(default_factory=list)  # RunwayRole, for emails
    duplicate_of: str | None = None
    error: str | None = None

    @property
    def status(self) -> str:
        if self.kind == "error":
            return "can't read"
        if self.kind == "email":
            return f"Runway email, {len(self.roles)} roles" if self.roles else "not a Runway email"
        return f"same as {self.duplicate_of}" if self.duplicate_of else "new"


def looks_like_email(text: str) -> bool:
    """A raw message (Gmail "Show original" > copy), not just the visible text."""
    head = text.lstrip()[:3000]
    return len(_HEADER.findall(head)) >= 3


def _email(source: str, raw: bytes, settings: Settings) -> Candidate:
    from autotailor.sources import runway

    msgs = runway.runway_messages(runway.parse_message(raw), settings.mail.allowed_senders)
    roles = [r for m in msgs for r in runway.parse_digest(m).roles]
    return Candidate(source, "email", raw_email=raw, roles=roles)


def _text(source: str, text: str, extract: Callable[[str], Any], url: str | None = None) -> Candidate:
    from autotailor.sources.paste import from_text

    try:
        return Candidate(source, "posting", posting=from_text(text, extract, url=url))
    except Exception as e:  # too short, model errors
        return Candidate(source, "error", error=str(e))


def read_urls(text: str, client: httpx.Client, extract: Callable[[str], Any]) -> list[Candidate]:
    from autotailor.sources.paste import NoFetchError, from_url

    out = []
    for url in [u.strip() for u in text.splitlines() if u.strip()]:
        try:
            out.append(Candidate(url, "posting", posting=from_url(client, url, extract)))
        except NoFetchError as e:
            out.append(Candidate(url, "error", error=str(e)))
        except httpx.HTTPStatusError as e:
            out.append(Candidate(url, "error", error=f"HTTP {e.response.status_code}"))
        except Exception as e:  # network, model
            out.append(Candidate(url, "error", error=f"{type(e).__name__}: {e}"))
    return out


def read_text(text: str, extract: Callable[[str], Any], settings: Settings) -> Candidate:
    if looks_like_email(text):
        return _email("pasted email", text.encode(), settings)
    return _text("pasted text", text, extract)


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def read_file(name: str, data: bytes, extract: Callable[[str], Any], settings: Settings) -> Candidate:
    low = name.lower()
    try:
        if low.endswith(".eml"):
            return _email(name, data, settings)
        if low.endswith(".pdf"):
            text = pdf_text(data)
            if len(text.strip()) < 50:
                return Candidate(name, "error", error="No text in this PDF (a scan?). Paste the text instead.")
            return _text(name, text, extract)
        if low.endswith(HTML_SUFFIXES):
            return _text(name, html_to_text(data.decode("utf-8", "replace")), extract)
        if low.endswith(TEXT_SUFFIXES):
            return _text(name, data.decode("utf-8", "replace"), extract)
    except Exception as e:  # corrupt PDF, bad encoding
        return Candidate(name, "error", error=f"{type(e).__name__}: {e}")
    return Candidate(name, "error", error="Use .eml, .pdf, .html, .txt or .md.")


def mark_duplicates(conn: sqlite3.Connection, cands: list[Candidate]) -> None:
    for c in cands:
        if c.posting is not None:
            c.duplicate_of = db.find_duplicate(conn, c.posting) or (c.posting.id if db.get_posting(conn, c.posting.id) else None)


@dataclass
class SaveReport:
    new: list[str] = field(default_factory=list)
    merged: int = 0
    emails: int = 0
    roles: int = 0

    def summary(self) -> str:
        parts = [f"{len(self.new)} new posting(s)"]
        if self.merged:
            parts.append(f"{self.merged} added details to stored ones")
        if self.emails:
            parts.append(f"{self.emails} Runway email(s) with {self.roles} roles")
        return ", ".join(parts)


def save(conn: sqlite3.Connection, cands: list[Candidate], settings: Settings) -> SaveReport:
    """Store the kept candidates. Postings go through the same dedupe as fetching (no title filter:
    you chose these); emails through the Runway import."""
    from autotailor import mail

    report = SaveReport()
    for c in cands:
        if c.kind == "posting" and c.posting is not None:
            existing = db.find_duplicate(conn, c.posting)
            if existing:
                db.fill_missing(conn, existing, c.posting)
                report.merged += 1
            else:
                if db.get_posting(conn, c.posting.id) is None:
                    report.new.append(c.posting.id)
                db.upsert_posting(conn, c.posting)
        elif c.kind == "email" and c.raw_email is not None and c.roles:
            r = mail.import_raw(conn, c.raw_email, "ui", settings)
            report.emails += r.messages - r.already
            report.roles += len(r.new)
            report.new += r.new
    return report
