"""Eligibility gate: can the user apply to this posting at all?

Two passes feed one verdict:

1. Title pass (no model). Titles such as "PhD Intern", "(Master's Degree)" or
   "New Grad 2027" settle many cases before any description is read.
2. Requirements pass. The `Requirements` extracted from the description (M2
   chain) are compared with the profile in `config.toml`.

Verdicts: INELIGIBLE if any stated requirement clearly fails; UNCLEAR if
something needed is missing or untrustworthy; ELIGIBLE otherwise.

A requirement only counts against the user if its quote is actually found in
the posting. A model that invents "PhD required" downgrades the verdict to
UNCLEAR rather than silently dropping a posting the user could apply to.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

from autotailor.config import DegreeLevel, EligibilityRules, Profile
from autotailor.models import JobPosting, Match, RequirementField, Requirements, Verdict

DEGREE_RANK: dict[DegreeLevel, int] = {
    DegreeLevel.ASSOCIATE: 1,
    DegreeLevel.BACHELOR: 2,
    DegreeLevel.MASTER: 3,
    DegreeLevel.MBA: 3,
    DegreeLevel.PHD: 4,
}
UNDERGRAD_LEVELS = {DegreeLevel.ASSOCIATE, DegreeLevel.BACHELOR}
_LEVEL_NAMES = {
    DegreeLevel.ASSOCIATE: "associate's",
    DegreeLevel.BACHELOR: "bachelor's",
    DegreeLevel.MASTER: "master's",
    DegreeLevel.MBA: "MBA",
    DegreeLevel.PHD: "PhD",
    DegreeLevel.OTHER: "other degree",
}
_ORDER = {Verdict.INELIGIBLE: 0, Verdict.UNCLEAR: 1, Verdict.ELIGIBLE: 2}


@dataclass(frozen=True)
class Finding:
    verdict: Verdict
    reason: str
    field: RequirementField | None = None  # None: came from the title or the profile


def _levels(levels: set[DegreeLevel]) -> str:
    return " or ".join(sorted(_LEVEL_NAMES[lv] for lv in levels)) or "none"


def _fmt(d: date) -> str:
    return d.strftime("%b %Y")


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


# --- profile helpers ---------------------------------------------------------


def profile_levels(profile: Profile, rules: EligibilityRules) -> set[DegreeLevel]:
    """Degree levels the user holds, plus those in progress if the rules count them."""
    return {
        e.level
        for e in profile.education
        if e.status == "completed" or (e.status == "in_progress" and rules.in_progress_counts)
    }


def profile_graduation(profile: Profile) -> date | None:
    """Graduation date that postings care about: the soonest in-progress degree, else the latest completed."""
    in_progress = [e.graduation_date for e in profile.education if e.status == "in_progress" and e.graduation_date]
    if in_progress:
        return min(in_progress)
    done = [e.graduation_date for e in profile.education if e.graduation_date]
    return max(done) if done else None


def is_enrolled(profile: Profile) -> bool:
    return any(e.status == "in_progress" for e in profile.education)


# --- evidence ----------------------------------------------------------------


def _norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return " ".join(s.split())


def quote_found(quote: str, text: str) -> bool:
    """True if the quote appears in the text, ignoring case, whitespace and curly quotes."""
    q = _norm(quote).strip(" .\"'")
    return bool(q) and q in _norm(text)


def verified_fields(req: Requirements, text: str) -> set[RequirementField]:
    """Fields with at least one quote, all of which appear in the posting text."""
    out: set[RequirementField] = set()
    fields = {e.field for e in req.evidence}
    for f in fields:
        quotes = req.quotes_for(f)
        if quotes and all(quote_found(q, text) for q in quotes):
            out.add(f)
    return out


def _quote(req: Requirements, field: RequirementField) -> str:
    quotes = req.quotes_for(field)
    return f' ("{quotes[0]}")' if quotes else ""


# --- shared checks -----------------------------------------------------------


def _degree_finding(
    accepted: set[DegreeLevel],
    undergrad_only: bool,
    profile: Profile,
    rules: EligibilityRules,
    source: str,
    field: RequirementField | None,
) -> Finding:
    have = profile_levels(profile, rules)
    if not profile.education:
        return Finding(Verdict.UNCLEAR, f"{source} asks for {_levels(accepted)}; your profile lists no education.", field)
    if not have:  # only in-progress degrees, and the rules don't count them
        return Finding(
            Verdict.INELIGIBLE,
            f"{source} asks for {_levels(accepted)}; you have no completed degree "
            "(in-progress degrees don't count under your eligibility rules).",
            field,
        )
    if undergrad_only:
        if any(e.level in UNDERGRAD_LEVELS and e.status == "in_progress" for e in profile.education):
            return Finding(Verdict.ELIGIBLE, f"{source} is for undergraduates; you are one.", field)
        return Finding(Verdict.INELIGIBLE, f"{source} is for undergraduates only; you are not a current undergraduate.", field)
    if have & accepted:
        return Finding(Verdict.ELIGIBLE, f"{source} accepts {_levels(accepted)}; you have {_levels(have & accepted)}.", field)
    ranks = [DEGREE_RANK[lv] for lv in accepted if lv in DEGREE_RANK]
    if not ranks:
        return Finding(Verdict.UNCLEAR, f"{source} asks for an unrecognized degree type.", field)
    if rules.higher_degree_satisfies_lower and any(DEGREE_RANK.get(lv, 0) > max(ranks) for lv in have):
        return Finding(Verdict.ELIGIBLE, f"{source} asks for {_levels(accepted)}; your {_levels(have)} is higher.", field)
    return Finding(Verdict.INELIGIBLE, f"{source} asks for {_levels(accepted)}; you have {_levels(have)}.", field)


def _graduation_finding(
    earliest: date | None,
    latest: date | None,
    profile: Profile,
    rules: EligibilityRules,
    source: str,
    field: RequirementField | None,
) -> Finding:
    window = f"{_fmt(earliest) if earliest else 'any time'} to {_fmt(latest) if latest else 'any time'}"
    grad = profile_graduation(profile)
    if grad is None:
        return Finding(Verdict.UNCLEAR, f"{source} requires graduating {window}; your profile has no graduation date.", field)
    slack = rules.graduation_window_slack_months
    lo = _add_months(earliest, -slack) if earliest else None
    hi = _add_months(latest, slack) if latest else None
    if (lo and grad < lo) or (hi and grad > hi):
        return Finding(Verdict.INELIGIBLE, f"{source} requires graduating {window}; you graduate {_fmt(grad)}.", field)
    return Finding(Verdict.ELIGIBLE, f"{source} requires graduating {window}; you graduate {_fmt(grad)}.", field)


# --- pass 1: title -----------------------------------------------------------

_TITLE_DEGREES: list[tuple[re.Pattern[str], DegreeLevel]] = [
    (re.compile(r"\bph\.?\s?d\b|\bdoctoral\b", re.I), DegreeLevel.PHD),
    (re.compile(r"\bmba\b", re.I), DegreeLevel.MBA),
    (re.compile(r"\bmaster(?:'s|’s|s)\b|\bmaster\s+degree\b", re.I), DegreeLevel.MASTER),
]
_TITLE_UNDERGRAD = re.compile(r"\bundergrad(?:uate)?s?\b", re.I)
_TITLE_GRAD_YEAR = [
    re.compile(r"\b(20\d\d)\s+(?:new\s+)?grad(?:uate)?s?\b", re.I),  # "2027 Graduate", "2027 New Grad"
    re.compile(r"\bnew\s+grad(?:uate)?s?\W{0,3}(?:class\s+of\s+)?(20\d\d)\b", re.I),  # "New Grad 2027"
    re.compile(r"\bclass\s+of\s+(20\d\d)\b", re.I),
    re.compile(r"\bgraduating\s+(?:in\s+)?(?:[a-z]+\s+)?(20\d\d)\b", re.I),
]


def title_findings(title: str, profile: Profile, rules: EligibilityRules) -> list[Finding]:
    findings: list[Finding] = []
    levels = {lv for pattern, lv in _TITLE_DEGREES if pattern.search(title)}
    if levels:
        findings.append(_degree_finding(levels, False, profile, rules, "Title", None))
    elif _TITLE_UNDERGRAD.search(title):
        findings.append(_degree_finding(UNDERGRAD_LEVELS, True, profile, rules, "Title", None))

    for pattern in _TITLE_GRAD_YEAR:
        m = pattern.search(title)
        if m:
            year = int(m.group(1))
            findings.append(
                _graduation_finding(date(year, 1, 1), date(year, 12, 31), profile, rules, f"Title (class of {year})", None)
            )
            break
    return findings


# --- pass 2: requirements ----------------------------------------------------

_MAJOR_STOPWORDS = {
    "a", "an", "and", "or", "of", "in", "the", "related", "field", "fields", "degree",
    "similar", "equivalent", "science", "sciences", "studies", "technical", "other", "major",
}  # fmt: skip


def _major_tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in _MAJOR_STOPWORDS}


def requirement_findings(req: Requirements, profile: Profile, rules: EligibilityRules) -> list[Finding]:
    findings: list[Finding] = []
    if req.degree_levels or req.undergraduates_only:
        accepted = set(req.degree_levels) or UNDERGRAD_LEVELS
        source = "Posting" + _quote(req, "undergraduates_only" if req.undergraduates_only else "degree_levels")
        field: RequirementField = "undergraduates_only" if req.undergraduates_only else "degree_levels"
        findings.append(_degree_finding(accepted, req.undergraduates_only, profile, rules, source, field))

    if req.enrollment_required:
        source = "Posting" + _quote(req, "enrollment_required")
        if is_enrolled(profile):
            findings.append(Finding(Verdict.ELIGIBLE, f"{source} requires current enrollment; you are enrolled.", "enrollment_required"))
        else:
            findings.append(Finding(Verdict.INELIGIBLE, f"{source} requires current enrollment; you are not enrolled.", "enrollment_required"))

    if req.graduation_earliest or req.graduation_latest:
        source = "Posting" + _quote(req, "graduation_window")
        findings.append(
            _graduation_finding(req.graduation_earliest, req.graduation_latest, profile, rules, source, "graduation_window")
        )

    if req.majors:
        asked = ", ".join(req.majors)
        mine = [e.field for e in profile.education]
        broad = any("related" in m.lower() for m in req.majors) or any(
            "related" in q.lower() for q in req.quotes_for("majors")
        )
        want = set().union(*(_major_tokens(m) for m in req.majors))
        have = set().union(*(_major_tokens(m) for m in mine)) if mine else set()
        if want & have:
            findings.append(Finding(Verdict.ELIGIBLE, f"Majors asked: {asked}; yours matches.", "majors"))
        elif broad:
            findings.append(Finding(Verdict.ELIGIBLE, f"Majors asked: {asked} (related fields accepted).", "majors"))
        else:
            findings.append(Finding(Verdict.UNCLEAR, f"Majors asked: {asked}; check whether {', '.join(mine) or 'yours'} fits.", "majors"))

    if req.work_authorization:
        findings.append(
            Finding(Verdict.ELIGIBLE, f"Work authorization (not checked): {req.work_authorization}.", "work_authorization")
        )
    return findings


def _distrust_unverified(findings: list[Finding], verified: set[RequirementField]) -> list[Finding]:
    out = []
    for f in findings:
        if f.verdict == Verdict.INELIGIBLE and f.field is not None and f.field not in verified:
            f = Finding(Verdict.UNCLEAR, f"{f.reason} Its quote was not found in the posting, so it is not trusted.", f.field)
        out.append(f)
    return out


# --- verdict -----------------------------------------------------------------


def assess(posting: JobPosting, profile: Profile, rules: EligibilityRules) -> Match:
    """Combine the title pass and the stored requirements into one verdict."""
    findings = title_findings(posting.title, profile, rules)
    req = posting.requirements
    if req is not None:
        req_findings = requirement_findings(req, profile, rules)
        if req_findings:
            verified = verified_fields(req, posting.description or "")
            findings += _distrust_unverified(req_findings, verified)
        elif not findings:
            findings.append(Finding(Verdict.ELIGIBLE, "The posting states no education or graduation requirements."))
    elif not any(f.verdict == Verdict.INELIGIBLE for f in findings):
        findings.append(Finding(Verdict.UNCLEAR, "Requirements not read yet."))

    findings.sort(key=lambda f: _ORDER[f.verdict])
    verdict = findings[0].verdict if findings else Verdict.UNCLEAR
    return Match(posting_id=posting.id, verdict=verdict, verdict_reasons=[f.reason for f in findings])


def title_rules_out(posting: JobPosting, profile: Profile, rules: EligibilityRules) -> bool:
    """True if the title alone makes the posting ineligible (no need to read the description)."""
    return any(f.verdict == Verdict.INELIGIBLE for f in title_findings(posting.title, profile, rules))


def passes_gate(verdict: Verdict, rules: EligibilityRules) -> bool:
    return verdict == Verdict.ELIGIBLE or (verdict == Verdict.UNCLEAR and rules.keep_unclear)
