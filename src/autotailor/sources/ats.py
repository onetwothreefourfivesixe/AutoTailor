"""Public job board APIs: Greenhouse, Lever and Ashby.

All three publish open JSON endpoints for a company's listed jobs; no login or
API key is involved. A board is identified by the company's slug on that ATS,
e.g. `figma` in https://boards.greenhouse.io/figma.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from autotailor.models import JobPosting, PostingStatus, Source
from autotailor.text import clean_text, html_to_text

ATS_SOURCES = (Source.GREENHOUSE, Source.LEVER, Source.ASHBY)


@dataclass(frozen=True)
class JobRef:
    """A single posting identified from its URL."""

    source: Source
    slug: str
    job_id: str


def _company_from_slug(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug).strip().title()


def _dt(value: str | int | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):  # Lever: epoch milliseconds
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --- Greenhouse ------------------------------------------------------------

_GH_API = "https://boards-api.greenhouse.io/v1/boards/{slug}"


def _greenhouse_posting(slug: str, job: dict) -> JobPosting:
    content = html_to_text(html.unescape(job.get("content") or ""))
    return JobPosting(
        id=f"greenhouse:{job['id']}",
        source=Source.GREENHOUSE,
        source_ref=str(job["id"]),
        source_url=job.get("absolute_url"),
        canonical_url=job.get("absolute_url"),
        company=job.get("company_name") or _company_from_slug(slug),
        title=job["title"].strip(),
        location=(job.get("location") or {}).get("name") or None,
        posted_at=_dt(job.get("first_published") or job.get("updated_at")),
        description=content or None,
        status=PostingStatus.RESOLVED if content else PostingStatus.UNRESOLVED,
    )


def fetch_greenhouse(client: httpx.Client, slug: str) -> list[JobPosting]:
    r = client.get(_GH_API.format(slug=slug) + "/jobs", params={"content": "true"})
    r.raise_for_status()
    return [_greenhouse_posting(slug, j) for j in r.json().get("jobs", [])]


def fetch_greenhouse_job(client: httpx.Client, slug: str, job_id: str) -> JobPosting:
    r = client.get(_GH_API.format(slug=slug) + f"/jobs/{job_id}")
    r.raise_for_status()
    return _greenhouse_posting(slug, r.json())


# --- Lever -----------------------------------------------------------------

_LEVER_API = "https://api.lever.co/v0/postings/{slug}"


def _lever_posting(slug: str, job: dict) -> JobPosting:
    parts = [job.get("descriptionPlain") or ""]
    for section in job.get("lists") or []:
        parts.append(f"{section.get('text', '').strip()}\n{html_to_text(section.get('content', ''))}")
    parts.append(job.get("additionalPlain") or "")
    description = clean_text("\n\n".join(p for p in parts if p and p.strip())) or None

    cats = job.get("categories") or {}
    locations = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
    url = job.get("hostedUrl")
    return JobPosting(
        id=f"lever:{job['id']}",
        source=Source.LEVER,
        source_ref=job["id"],
        source_url=url,
        canonical_url=url,
        company=_company_from_slug(slug),
        title=job["text"].strip(),
        location=" • ".join(locations) or None,
        posted_at=_dt(job.get("createdAt")),
        description=description,
        status=PostingStatus.RESOLVED if description else PostingStatus.UNRESOLVED,
    )


def fetch_lever(client: httpx.Client, slug: str) -> list[JobPosting]:
    r = client.get(_LEVER_API.format(slug=slug), params={"mode": "json"})
    r.raise_for_status()
    return [_lever_posting(slug, j) for j in r.json()]


def fetch_lever_job(client: httpx.Client, slug: str, job_id: str) -> JobPosting:
    r = client.get(_LEVER_API.format(slug=slug) + f"/{job_id}", params={"mode": "json"})
    r.raise_for_status()
    return _lever_posting(slug, r.json())


# --- Ashby -----------------------------------------------------------------

_ASHBY_API = "https://api.ashbyhq.com/posting-api/job-board/{slug}"


def _ashby_posting(slug: str, job: dict) -> JobPosting:
    description = clean_text(job.get("descriptionPlain") or "") or (
        html_to_text(job.get("descriptionHtml") or "") or None
    )
    location = job.get("location") or None
    if job.get("isRemote") and location and "remote" not in location.lower():
        location = f"{location} (remote OK)"
    url = job.get("jobUrl")
    return JobPosting(
        id=f"ashby:{job['id']}",
        source=Source.ASHBY,
        source_ref=job["id"],
        source_url=url,
        canonical_url=url,
        company=_company_from_slug(slug),
        title=job["title"].strip(),
        location=location,
        posted_at=_dt(job.get("publishedAt")),
        description=description or None,
        status=PostingStatus.RESOLVED if description else PostingStatus.UNRESOLVED,
    )


def fetch_ashby(client: httpx.Client, slug: str) -> list[JobPosting]:
    r = client.get(_ASHBY_API.format(slug=slug))
    r.raise_for_status()
    jobs = r.json().get("jobs", [])
    return [_ashby_posting(slug, j) for j in jobs if j.get("isListed", True)]


def fetch_ashby_job(client: httpx.Client, slug: str, job_id: str) -> JobPosting:
    # Ashby's public API has no single-job endpoint, so read the board and pick one.
    for p in fetch_ashby(client, slug):
        if p.source_ref == job_id:
            return p
    raise LookupError(f"Job {job_id} is not listed on the Ashby board '{slug}'.")


# --- dispatch --------------------------------------------------------------

_BOARD_FETCHERS = {
    Source.GREENHOUSE: fetch_greenhouse,
    Source.LEVER: fetch_lever,
    Source.ASHBY: fetch_ashby,
}
_JOB_FETCHERS = {
    Source.GREENHOUSE: fetch_greenhouse_job,
    Source.LEVER: fetch_lever_job,
    Source.ASHBY: fetch_ashby_job,
}


def fetch_board(client: httpx.Client, source: Source, slug: str) -> list[JobPosting]:
    return _BOARD_FETCHERS[source](client, slug)


def fetch_job(client: httpx.Client, ref: JobRef) -> JobPosting:
    return _JOB_FETCHERS[ref.source](client, ref.slug, ref.job_id)


_UUID = r"[0-9a-fA-F-]{36}"


def parse_job_url(url: str) -> JobRef | None:
    """Recognize a posting URL on one of the supported job boards."""
    u = urlparse(url if "://" in url else f"https://{url}")
    host, path = u.netloc.lower(), u.path

    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") or host.endswith(
        ".greenhouse.io"
    ):
        m = re.match(r"^/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", path)
        if m:
            return JobRef(Source.GREENHOUSE, m.group(1), m.group(2))
    if host == "jobs.lever.co":
        m = re.match(rf"^/([\w.-]+)/({_UUID})", path)
        if m:
            return JobRef(Source.LEVER, m.group(1), m.group(2))
    if host == "jobs.ashbyhq.com":
        m = re.match(rf"^/([^/]+)/({_UUID})", path)
        if m:
            return JobRef(Source.ASHBY, m.group(1), m.group(2))
    return None
