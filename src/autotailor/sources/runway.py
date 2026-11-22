"""Runway notification emails: find them in a message, and read the roles out of them.

Runway's terms forbid automated access to its site, so this module never makes
a request. Everything comes from the email the user received:

- the role's title, company, location, match score and "posted about N hours ago";
- the Runway job ID, decoded offline from the "View Role" link (a Postmark
  tracking URL wrapping app.joinrunway.io/job/<id>). The link itself is never
  opened: that would be automated access, and it would count as a click;
- the company's web domain, from the logo's file name
  (cdn.joinrunway.io/logo/<domain>.jpg). The image is never loaded.

The email has no job description and no employer link; resolving a role to the
employer's posting is a separate step (autotailor/resolve.py).
"""

from __future__ import annotations

import email
import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

from autotailor.models import JobPosting, PostingStatus, Source

RUNWAY_JOB_URL = "https://app.joinrunway.io/job/{id}"
_JOB_ID = re.compile(r"app\.joinrunway\.io/job/([A-Za-z0-9_-]+)")
_SCORE = re.compile(r"(\d{1,3})\s*%\s*Match", re.I)
_POSTED = re.compile(
    r"Posted\s+(?:about\s+|over\s+|almost\s+)?(less than a|an?|\d+)\s+(minute|hour|day|week|month)s?\s+ago", re.I
)
_UNITS = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1),
          "week": timedelta(weeks=1), "month": timedelta(days=30)}  # fmt: skip


@dataclass
class RunwayRole:
    job_id: str
    title: str
    company: str
    location: str | None
    match_score: float | None  # 0-1
    posted_at: datetime | None
    company_domain: str | None

    @property
    def runway_url(self) -> str:
        return RUNWAY_JOB_URL.format(id=self.job_id)

    def to_posting(self) -> JobPosting:
        """A placeholder posting: known title and company, no description yet."""
        return JobPosting(
            id=f"runway:{self.job_id}", source=Source.RUNWAY, source_ref=self.job_id,
            source_url=self.runway_url, company=self.company, title=self.title, location=self.location,
            posted_at=self.posted_at, external_score=self.match_score, status=PostingStatus.UNRESOLVED,
        )  # fmt: skip


@dataclass
class RunwayDigest:
    message_id: str
    sent_at: datetime | None
    subject: str
    roles: list[RunwayRole] = field(default_factory=list)


# --- finding Runway mail -----------------------------------------------------------


def parse_message(raw: bytes) -> EmailMessage:
    return email.message_from_bytes(raw, policy=policy.default)  # type: ignore[return-value]


def sender(msg: EmailMessage) -> str:
    return parseaddr(str(msg.get("From", "")))[1].lower()


def _html(msg: EmailMessage) -> str:
    body = msg.get_body(("html", "plain"))
    return body.get_content() if body is not None else ""


def runway_messages(msg: EmailMessage, allowed: list[str]) -> list[EmailMessage]:
    """The Runway emails in `msg`: the message itself if Runway sent it, or ones forwarded
    inside it (as an attachment, or inline with the original From line quoted)."""
    allowed_l = [a.lower() for a in allowed]
    if sender(msg) in allowed_l:
        return [msg]
    found = []
    for part in msg.walk():
        if part.get_content_type() == "message/rfc822":
            inner = part.get_content()
            if isinstance(inner, EmailMessage):
                found += runway_messages(inner, allowed)
    if found:
        return found
    # Inline forward: the original headers are quoted in the body ("From: Ford from Runway <hello@...>").
    text = _html(msg)
    if any(re.search(rf"From:.{{0,200}}{re.escape(a)}", text, re.I | re.S) for a in allowed_l) and _JOB_ID.search(
        unquote(text)
    ):
        return [msg]
    return []


# --- reading roles -------------------------------------------------------------------


class _Items(HTMLParser):
    """Flattens the HTML into ('logo', src, alt), ('text', s) and ('link', href, text) items."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple] = []
        self._href: str | None = None
        self._link_text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        a = dict(attrs)
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag == "img" and "/logo/" in (a.get("src") or ""):
            self.items.append(("logo", a.get("src") or "", a.get("alt") or ""))
        elif tag == "a":
            self._href, self._link_text = a.get("href") or "", []

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in ("style", "script", "head") and self._skip:
            self._skip -= 1
        elif tag == "a" and self._href is not None:
            self.items.append(("link", self._href, " ".join(self._link_text).strip()))
            self._href = None

    def handle_data(self, data):  # noqa: ANN001
        if self._skip:
            return
        text = " ".join(data.replace("‌", " ").replace(" ", " ").split())
        if not text:
            return
        if self._href is not None:
            self._link_text.append(text)
        else:
            self.items.append(("text", text))


def job_id_from_link(href: str) -> str | None:
    """'https://track.pstmrk.it/3s/app.joinrunway.io%2Fjob%2Fcmwe9...%3Futm...' -> 'cmwe9...'. Offline."""
    m = _JOB_ID.search(unquote(href))
    return m.group(1) if m else None


def _domain(logo_src: str) -> str | None:
    name = urlparse(logo_src).path.rsplit("/", 1)[-1]
    name = re.sub(r"\.(jpe?g|png|gif|webp|svg)$", "", name, flags=re.I)
    return name.lower() if "." in name else None


def posted_at(text: str, sent: datetime | None) -> datetime | None:
    m = _POSTED.search(text)
    if not m or sent is None:
        return None
    n = m.group(1).lower()
    count = 0 if n.startswith("less") else 1 if n in ("a", "an") else int(n)
    return sent - count * _UNITS[m.group(2).lower()]


def _role(texts: list[str], logo: tuple | None, job_id: str, sent: datetime | None) -> RunwayRole | None:
    content = [t for t in texts if not _SCORE.search(t) and not _POSTED.search(t)]
    if len(content) < 2:
        return None
    title, where = content[-2], content[-1]
    company, _, location = where.partition("•")
    score = next((int(m.group(1)) / 100 for t in texts if (m := _SCORE.search(t))), None)
    posted = next((p for t in texts if (p := posted_at(t, sent))), None)
    return RunwayRole(
        job_id=job_id, title=title.strip(), company=company.strip(), location=location.strip() or None,
        match_score=score, posted_at=posted, company_domain=_domain(logo[1]) if logo else None,
    )  # fmt: skip


def parse_digest(msg: EmailMessage) -> RunwayDigest:
    """Every role in one Runway notification email."""
    try:
        sent = parsedate_to_datetime(str(msg["Date"])).astimezone(UTC) if msg["Date"] else None
    except (TypeError, ValueError):
        sent = None
    parser = _Items()
    parser.feed(_html(msg))
    roles: list[RunwayRole] = []
    seen: set[str] = set()
    texts: list[str] = []
    logo: tuple | None = None
    for item in parser.items:
        if item[0] == "logo":
            texts, logo = [], item
        elif item[0] == "text":
            texts.append(item[1])
        elif item[0] == "link":
            job_id = job_id_from_link(item[1])
            if job_id and job_id not in seen and (role := _role(texts, logo, job_id, sent)):
                roles.append(role)
                seen.add(job_id)
            if job_id:
                texts, logo = [], None
    message_id = str(msg.get("Message-ID") or "").strip()
    if not message_id:  # stable stand-in, so the same file isn't imported twice
        message_id = "<sha256-" + hashlib.sha256(msg.as_bytes()).hexdigest()[:32] + ">"
    return RunwayDigest(message_id=message_id, sent_at=sent, subject=str(msg.get("Subject", "")), roles=roles)
