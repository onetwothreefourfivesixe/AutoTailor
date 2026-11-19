"""Job sources. Each one produces `JobPosting` objects; none of them writes to the database."""

from __future__ import annotations

import httpx

from autotailor import __version__

# Generic client name: no user details are sent to job boards.
USER_AGENT = f"AutoTailor/{__version__} (personal job search tool)"

# Sites whose terms forbid automated access. AutoTailor never fetches these;
# the user pastes the posting text instead.
NO_FETCH_DOMAINS = (
    "linkedin.com",
    "joinhandshake.com",
    "joinrunway.io",
    "indeed.com",
    "glassdoor.com",
)


def make_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def is_no_fetch(host: str) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in NO_FETCH_DOMAINS)
