"""Duplicate detection across sources.

The same role often arrives more than once: from a company board and from a
Runway email, or twice in one board under slightly different text. Two checks,
in order:

1. Same canonical URL (after normalization).
2. Same company and title (normalized), with compatible locations.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_COMPANY_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "co", "company", "bv", "plc",
    "gmbh", "holdings", "lp", "llp", "sa", "ag",
}  # fmt: skip


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", s.lower())


def normalize_company(name: str) -> str:
    words = _words(name.replace(".", ""))  # "B.V." -> "BV", "Inc." -> "Inc"
    while words and words[-1] in _COMPANY_SUFFIXES:
        words.pop()
    return " ".join(words)


def normalize_title(title: str) -> str:
    return " ".join(_words(title))


def dedupe_key(company: str, title: str) -> str:
    return f"{normalize_company(company)}|{normalize_title(title)}"


def normalize_url(url: str | None) -> str | None:
    """Drop scheme, query, fragment and trailing slash; lowercase the host."""
    if not url:
        return None
    u = urlparse(url if "://" in url else f"https://{url}")
    host = u.netloc.lower().removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}" or None


def primary_city(location: str | None) -> str | None:
    """'San Francisco, CA • New York, NY' -> 'san francisco'."""
    if not location:
        return None
    first = re.split(r"[•;|/]", location)[0]
    city = " ".join(_words(first.split(",")[0]))
    return city or None


def locations_compatible(a: str | None, b: str | None) -> bool:
    """Unknown locations are compatible with anything; known ones must share a city."""
    ca, cb = primary_city(a), primary_city(b)
    if ca is None or cb is None:
        return True
    return ca == cb or "remote" in (ca, cb)
