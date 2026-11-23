"""Add a single posting the user found: a URL or pasted text.

Job board URLs (Greenhouse, Lever, Ashby) go through their APIs and need no
model. Other career sites usually embed the posting as schema.org JobPosting
data (for search engines), which is read directly, also without a model; the
visible page text often holds only navigation, with the posting loaded later by
JavaScript. Anything else is turned into plain text and read by the extraction
chain. A fetched page whose text doesn't look like a job posting is refused with a
hint to paste the posting instead (text the user pastes is taken as it is).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
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


# Words nearly every real posting uses; a page of menus and footers has few or none.
_POSTING_MARKERS = (
    "responsibilit", "qualification", "requirement", "duties", "you will", "you'll", "degree",
    "skills", "experience with", "what you", "about the role", "minimum", "preferred", "years of",
    "we are looking", "looking for", "job description", "salary", "pay range", "benefits", "mentor",
    "project", "the team", "our team", "your team", "interns will", "the program", "ideal candidate",
    "you have", "you are", "we offer", "the position", "we are seeking", "candidates", "experience in",
    "knowledge of", "understanding of", "interest in",
)  # fmt: skip


def looks_like_posting(text: str) -> bool:
    """Whether text reads like a job posting rather than a careers site's navigation."""
    low = text.lower().replace("\u2019", "'")
    return sum(m in low for m in _POSTING_MARKERS) >= 3


def _text_id(text: str) -> str:
    normalized = " ".join(text.split()).lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def from_text(text: str, extract: Extractor, url: str | None = None, check: bool = False) -> JobPosting:
    """Build a posting from free text using the extraction chain. With `check` (text fetched
    from a page, not pasted by the user), text that doesn't read like a posting is refused."""
    text = text.strip()
    if len(text) < MIN_TEXT_CHARS:
        raise ValueError(
            f"Only {len(text)} characters of text; that is too short to be a job posting."
        )
    if check and not looks_like_posting(text):
        raise ValueError(
            "That page's text doesn't look like a job posting (no responsibilities, qualifications or "
            "requirements); the site may load the posting with JavaScript. Open it in your "
            "browser, copy the posting's text, and paste that instead."
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
    structured = from_json_ld(r.text, str(r.url))
    if structured is not None:
        return structured
    text = html_to_text(r.text)
    return from_text(text, extract, url=str(r.url), check=True)


def _job_postings(node) -> list[dict]:  # noqa: ANN001
    """schema.org JobPosting objects anywhere in a JSON-LD value (lists, @graph)."""
    if isinstance(node, list):
        return [j for n in node for j in _job_postings(n)]
    if not isinstance(node, dict):
        return []
    kind = node.get("@type")
    if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
        return [node]
    return _job_postings(node.get("@graph", []))


def _location(job: dict) -> str | None:
    places = job.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    out = []
    for place in places:
        a = place.get("address") if isinstance(place, dict) else None
        if isinstance(a, dict):
            parts = [a.get("addressLocality"), a.get("addressRegion")]
            out.append(", ".join(x for x in parts if isinstance(x, str) and x.strip()))
        elif isinstance(a, str):
            out.append(a)
    if str(job.get("jobLocationType", "")).upper() == "TELECOMMUTE":
        out.append("Remote")
    return "; ".join(dict.fromkeys(x for x in out if x)) or None


def from_json_ld(html: str, url: str) -> JobPosting | None:
    """The posting from a page's schema.org JobPosting data, or None if it has none usable."""
    for block in re.findall(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", html, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for job in _job_postings(data):
            title = str(job.get("title") or "").strip()
            description = html_to_text(str(job.get("description") or ""))
            if not title or len(description) < MIN_TEXT_CHARS:
                continue
            org = job.get("hiringOrganization")
            company = (org.get("name") if isinstance(org, dict) else org) or urlparse(url).netloc
            try:
                posted = datetime.fromisoformat(str(job["datePosted"]).replace("Z", "+00:00")) if job.get("datePosted") else None
            except ValueError:
                posted = None
            ref = _text_id(description)
            return JobPosting(
                id=f"paste:{ref}", source=Source.PASTE, source_ref=ref, source_url=url, canonical_url=url,
                company=str(company).strip(), title=title, location=_location(job), posted_at=posted,
                description=description, status=PostingStatus.RESOLVED,
            )  # fmt: skip
    return None
