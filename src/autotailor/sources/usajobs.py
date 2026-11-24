"""Federal jobs from USAJOBS, the U.S. government's official job site, through its public API.

The API (https://developer.usajobs.gov) needs a free key, and asks every request to name a
contact email in the User-Agent header; both come from the user (USAJOBS_API_KEY in .env,
sources.usajobs_email in config.toml) and go only to data.usajobs.gov. Searches can be
limited to hiring paths such as students (internships, including Pathways) and recent
graduates, and to keywords such as "policy" or "legal".
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx

from autotailor.models import JobPosting, PostingStatus, Source

API = "https://data.usajobs.gov/api/search"
KEY_ENV = "USAJOBS_API_KEY"
HIRING_PATHS = {"student": "Students (internships, Pathways)", "graduates": "Recent graduates", "public": "Open to the public"}
PAGE_SIZE = 500  # the API's maximum
MAX_PAGES = 4


class USAJobsNotSetUp(RuntimeError):
    """The key or the contact email is missing."""


def api_key() -> str:
    return (os.environ.get(KEY_ENV) or "").strip()


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _description(d: dict[str, Any]) -> str:
    """The posting as plain text: summary, duties, qualifications, and the facts that decide eligibility."""
    details = (d.get("UserArea") or {}).get("Details") or {}
    lines: list[str] = []

    def section(title: str, body: Any) -> None:
        if isinstance(body, list):
            body = "\n".join(f"- {x}" for x in body if str(x).strip())
        if body and str(body).strip():
            lines.extend([title, str(body).strip(), ""])

    section("Summary", details.get("JobSummary"))
    section("Major duties", details.get("MajorDuties"))
    section("Qualifications", d.get("QualificationSummary"))
    section("Education", details.get("Education"))
    section("Requirements", details.get("Requirements"))
    facts = []
    who = (details.get("WhoMayApply") or {}).get("Name")
    if who:
        facts.append(f"Who may apply: {who}")
    paths = [h for h in (d.get("UserArea") or {}).get("Details", {}).get("HiringPath", []) or []]
    if paths:
        facts.append("Hiring paths: " + ", ".join(paths))
    if details.get("SecurityClearance"):
        facts.append(f"Security clearance: {details['SecurityClearance']}")
    pay = (d.get("PositionRemuneration") or [{}])[0]
    if pay.get("MinimumRange"):
        facts.append(f"Pay: ${pay['MinimumRange']}–${pay.get('MaximumRange', '')} {pay.get('Description', '')}".strip())
    schedule = ", ".join(s.get("Name", "") for s in d.get("PositionSchedule") or [] if s.get("Name"))
    if schedule:
        facts.append(f"Schedule: {schedule}")
    if d.get("ApplicationCloseDate"):
        facts.append(f"Applications close: {d['ApplicationCloseDate'][:10]}")
    if details.get("TeleworkEligible") is not None:
        facts.append(f"Telework eligible: {'yes' if details['TeleworkEligible'] else 'no'}")
    section("Details", facts)
    return "\n".join(lines).strip()


def to_posting(item: dict[str, Any]) -> JobPosting | None:
    d = item.get("MatchedObjectDescriptor") or {}
    ref = str(item.get("MatchedObjectId") or d.get("PositionID") or "").strip()
    title = (d.get("PositionTitle") or "").strip()
    if not ref or not title:
        return None
    org, dept = (d.get("OrganizationName") or "").strip(), (d.get("DepartmentName") or "").strip()
    company = org if not dept or dept == org else f"{org} ({dept})"
    url = d.get("PositionURI") or None
    return JobPosting(
        id=f"usajobs:{ref}", source=Source.USAJOBS, source_ref=ref, source_url=url, canonical_url=url,
        company=company or "U.S. Government", title=title, location=d.get("PositionLocationDisplay") or None,
        posted_at=_date(d.get("PublicationStartDate")), description=_description(d) or None,
        status=PostingStatus.RESOLVED,
    )  # fmt: skip


def search(client: httpx.Client, email: str, key: str, keywords: list[str], hiring_paths: list[str],
           days: int = 0) -> list[JobPosting]:  # fmt: skip
    """Postings for each keyword (or all, with none), limited to the hiring paths and to the last
    `days` days (the API allows up to 60; 0 for no limit). Duplicates across keywords are merged."""
    if not email.strip() or not key.strip():
        raise USAJobsNotSetUp(
            "USAJOBS needs a free API key (USAJOBS_API_KEY in .env, from https://developer.usajobs.gov) and your "
            "contact email (sources.usajobs_email), which USAJOBS asks every request to include."
        )
    headers = {"Host": "data.usajobs.gov", "User-Agent": email.strip(), "Authorization-Key": key.strip()}
    found: dict[str, JobPosting] = {}
    for keyword in keywords or [""]:
        for page in range(1, MAX_PAGES + 1):
            params: dict[str, Any] = {"ResultsPerPage": PAGE_SIZE, "Page": page, "Fields": "full"}
            if keyword.strip():
                params["Keyword"] = keyword.strip()
            if hiring_paths:
                params["HiringPath"] = ";".join(hiring_paths)
            if days:
                params["DatePosted"] = min(days, 60)
            r = client.get(API, params=params, headers=headers)
            r.raise_for_status()
            result = r.json().get("SearchResult") or {}
            items = result.get("SearchResultItems") or []
            for item in items:
                p = to_posting(item)
                if p:
                    found.setdefault(p.id, p)
            total = int(result.get("SearchResultCountAll") or 0)
            if len(items) < PAGE_SIZE or page * PAGE_SIZE >= total:
                break
    return list(found.values())
