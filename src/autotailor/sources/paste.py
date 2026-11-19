"""Add a single posting the user found: a URL or pasted text.

Job board URLs (Greenhouse, Lever, Ashby) go through their APIs and need no
model. Anything else is turned into plain text and read by the extraction chain.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from autotailor.chains.extract_posting import PostingFields
from autotailor.models import JobPosting, PostingStatus, Source
from autotailor.sources import is_no_fetch
from autotailor.sources.ats import fetch_job, parse_job_url
from autotailor.text import html_to_text

Extractor = Callable[[str], PostingFields]

MIN_TEXT_CHARS = 200


class NoFetchError(Exception):
    """The URL is on a site AutoTailor must not fetch automatically."""


def _text_id(text: str) -> str:
    normalized = " ".join(text.split()).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def from_text(text: str, extract: Extractor, url: str | None = None) -> JobPosting:
    """Build a posting from free text using the extraction chain."""
    text = text.strip()
    if len(text) < MIN_TEXT_CHARS:
        raise ValueError(
            f"Only {len(text)} characters of text; that is too short to be a job posting."
        )
    fields = extract(text)
    ref = _text_id(text)
    return JobPosting(
        id=f"paste:{ref}",
        source=Source.PASTE,
        source_ref=ref,
        source_url=url,
        canonical_url=url,
        company=fields.company.strip(),
        title=fields.title.strip(),
        location=fields.location,
        description=text,
        status=PostingStatus.RESOLVED,
    )


def from_url(client: httpx.Client, url: str, extract: Extractor) -> JobPosting:
    """Fetch one posting by URL: via the job board API if possible, else the page itself."""
    if not urlparse(url).scheme:
        url = f"https://{url}"
    ref = parse_job_url(url)
    if ref:
        return fetch_job(client, ref)

    host = urlparse(url).netloc
    if is_no_fetch(host):
        raise NoFetchError(
            f"{host} does not allow automated access. Open the posting in your browser, "
            "copy the text, and add it with --file or by piping it in."
        )
    r = client.get(url)
    r.raise_for_status()
    text = html_to_text(r.text)
    return from_text(text, extract, url=str(r.url))
