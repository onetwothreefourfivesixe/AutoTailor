"""Fill the page, and measure keyword coverage, after a resume passes verification.

Fill: a tailored resume should use its whole page, not stop halfway. After the
model's verified draft is rendered, unused CV bullets are added (verbatim, so
they need no re-verification) until the page is full, in this order:

- relevance to the posting: the posting's keywords (from the tailoring model),
  the bullets fit scoring cited, and plain word overlap with the posting;
- spread across sections: one bullet from each section in turn (experience,
  projects, activities, ...), so every section grows rather than one;
- entries already on the resume first (a bullet there costs one or two lines; a
  new entry also costs its heading);
- bullets from the usual resume (`base: true`) before all others: the page is
  filled with the rest of the usual resume first, then with other CV bullets.
  Hidden entries are never used.

The page count from real renders decides: the result is the most bullets that
still fit on one page. A draft that is already over a page is trimmed from its
least relevant end instead (education lines are kept), and then only bullets of
entries already on the page are tried in the space left: a new entry for one
short bullet reads as filler. A text-position estimate from the PDF picks the
first guess, so it usually takes two or three renders.

Coverage: the share of the posting's keywords that appear in the final resume,
the "match score" ATS-optimization guides use (matched / total).
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from autotailor.cv import MasterCV
from autotailor.models import TailoredBullet, TailoredResume
from autotailor.resume.render import pdf_page_count

SECTION_ORDER = ("experience", "projects", "activities", "volunteer", "awards", "education")
CHARS_PER_LINE = 105  # letter page, 0.5in margins, 9.5-10pt text
MAX_RENDERS = 8
_STOP = set(
    "the and for with you your our are will this that from have has into who what about their they them "
    "can all any our more most such also other able work team role including include using use well new "
    "strong experience skills ability knowledge year years internship intern".split()
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9+#.\-]*[a-z0-9+#]|[a-z0-9]", text.lower()) if len(w) > 2 and w not in _STOP}


def _has_phrase(text: str, phrase: str) -> bool:
    """Whole-word, case-insensitive; punctuation and hyphens between words are ignored."""
    words = [re.escape(w) for w in re.findall(r"[a-z0-9+#.]+", phrase.lower())]
    if not words:
        return False
    pattern = r"(?<![a-z0-9])" + r"[\s\-/,.]*".join(words) + r"(?![a-z0-9])"
    return re.search(pattern, text.lower()) is not None


# --- which bullets to add -------------------------------------------------------------


def _entry_sections(cv: MasterCV) -> dict[str, tuple[str, str]]:
    """Bullet ID -> (section, entry ID)."""
    out: dict[str, tuple[str, str]] = {}
    for section in SECTION_ORDER:
        if section == "education":
            for e in cv.education:
                for b in e.details if not e.hidden else []:
                    out[b.id] = (section, e.id)
        else:
            for e in getattr(cv, section):
                for b in e.bullets if not e.hidden else []:
                    out[b.id] = (section, e.id)
    return out


def rank_candidates(
    resume: TailoredResume, cv: MasterCV, posting_text: str, cited: list[str] | None = None
) -> list[str]:
    """Unused CV bullets, in the order they should be added to fill the page."""
    bullets = cv.bullets()
    where = _entry_sections(cv)
    used = {b.source_id for b in resume.bullets}
    on_page_entries = {where[i][1] for i in used if i in where}
    posting_words = _words(posting_text)
    keywords = [k for k in resume.keywords if k.strip()]
    cited = cited or []
    base = set(cv.base_bullets())

    def score(bid: str) -> float:
        text = bullets[bid].text
        s = 3.0 * sum(_has_phrase(text, k) for k in keywords)
        overlap = _words(text) & posting_words
        s += len(overlap) / math.sqrt(max(1, len(_words(text))))
        if bid in cited:
            s += 2.0 + (len(cited) - cited.index(bid)) * 0.1
        if where[bid][1] in on_page_entries:
            s += 1.0
        return s

    def round_robin(ids: list[str]) -> list[str]:
        per_section: dict[str, list[tuple[float, str]]] = {}
        for bid in ids:
            per_section.setdefault(where[bid][0], []).append((score(bid), bid))
        for lst in per_section.values():
            lst.sort(key=lambda t: -t[0])
        sections = sorted(per_section, key=lambda s: -per_section[s][0][0])
        order: list[str] = []
        while any(per_section[s] for s in sections):
            for s in sections:  # one from each section in turn
                if per_section[s]:
                    order.append(per_section[s].pop(0)[1])
        return order

    unused = [bid for bid in where if bid not in used and bid in bullets]
    return round_robin([b for b in unused if b in base]) + round_robin([b for b in unused if b not in base])


# --- how much room is left -------------------------------------------------------------


def free_lines(pdf: bytes) -> float:
    """Roughly how many more text lines fit below the content on page 1 (estimate only)."""
    try:
        from pypdf import PdfReader

        page = PdfReader(io.BytesIO(pdf)).pages[0]
        height = float(page.mediabox.height)
        ys: list[float] = []

        def visit(text, cm, tm, font, size):  # noqa: ANN001
            if text.strip():
                ys.append(tm[5] * cm[3] + cm[5] if cm[3] else tm[5])

        page.extract_text(visitor_text=visit)
        ys = [y for y in ys if 0 < y < height]
        if len(ys) < 5:
            return 0.0
        distinct = sorted({round(y, 1) for y in ys}, reverse=True)
        gaps = sorted(a - b for a, b in zip(distinct, distinct[1:], strict=False) if 6 < a - b < 30)
        line = gaps[len(gaps) // 2] if gaps else 12.0
        bottom_margin = 0.45 * 72
        return max(0.0, (min(ys) - bottom_margin) / line)
    except Exception:  # an unreadable PDF just means no estimate
        return 0.0


def _lines(text: str) -> int:
    return max(1, math.ceil(len(text) / CHARS_PER_LINE))


def _guess(cv: MasterCV, resume: TailoredResume, candidates: list[str], room: float) -> int:
    """How many candidates should fit in `room` lines."""
    bullets = cv.bullets()
    where = _entry_sections(cv)
    entries = {where[b.source_id][1] for b in resume.bullets if b.source_id in where}
    used, n = 0.0, 0
    for bid in candidates:
        cost = _lines(bullets[bid].text) + (0 if where[bid][1] in entries else 2)
        if used + cost > room:
            break
        used += cost
        entries.add(where[bid][1])
        n += 1
    return n


# --- the fill loop --------------------------------------------------------------------------


@dataclass
class FillResult:
    resume: TailoredResume
    pdf: bytes
    pages: int
    added: list[str] = field(default_factory=list)  # bullet IDs added to fill the page
    removed: list[str] = field(default_factory=list)  # bullet IDs dropped to fit one page
    renders: int = 0


def _with(resume: TailoredResume, cv: MasterCV, ids: list[str]) -> TailoredResume:
    bullets = cv.bullets()
    extra = [TailoredBullet(source_id=i, text=bullets[i].text) for i in ids]
    return resume.model_copy(update={"bullets": [*resume.bullets, *extra]})


def fill_page(
    resume: TailoredResume,
    cv: MasterCV,
    candidates: list[str],
    render: Callable[[TailoredResume], bytes],
    min_bullets: int = 1,
    max_renders: int = MAX_RENDERS,
) -> FillResult:
    """The most bullets that fit on one page: trim an over-long draft, or add candidates
    (in order) to a short one. `render` turns a resume into PDF bytes."""
    pdf = render(resume)
    renders = 1
    pages = pdf_page_count(pdf)

    where = _entry_sections(cv)
    removed: list[str] = []
    while pages > 1 and len(resume.bullets) > min_bullets and renders < max_renders:
        # The least relevant bullet that isn't an education line (coursework stays).
        cut = next((i for i in range(len(resume.bullets) - 1, -1, -1)
                    if where.get(resume.bullets[i].source_id, ("",))[0] != "education"), len(resume.bullets) - 1)  # fmt: skip
        removed.append(resume.bullets[cut].source_id)
        resume = resume.model_copy(update={"bullets": [b for i, b in enumerate(resume.bullets) if i != cut]})
        pdf = render(resume)
        renders += 1
        pages = pdf_page_count(pdf)
    candidates = [c for c in candidates if c not in removed]
    if pages > 1 or not candidates:
        return FillResult(resume, pdf, pages, removed=removed, renders=renders)
    # After a trim the page usually has a few lines left: shorter bullets may still fit.

    # Add as many candidates as fit. lo: known to fit; hi: known to overflow.
    best_resume, best_pdf, lo, hi = resume, pdf, 0, None
    if removed:  # the trimmed bullet didn't fit, so only shorter ones in entries on the page might
        bullets = cv.bullets()
        limit = min(len(bullets[i].text) for i in removed)
        on_page = {where[b.source_id][1] for b in resume.bullets if b.source_id in where}
        candidates = [c for c in candidates if len(bullets[c].text) < limit and where[c][1] in on_page]
        if not candidates:
            return FillResult(resume, pdf, 1, removed=removed, renders=renders)
    n = min(len(candidates), max(1, _guess(cv, resume, candidates, free_lines(pdf))))
    while renders < max_renders:
        trial = _with(resume, cv, candidates[:n])
        trial_pdf = render(trial)
        renders += 1
        if pdf_page_count(trial_pdf) == 1:
            best_resume, best_pdf, lo = trial, trial_pdf, n
            if lo == len(candidates):
                break
            if hi is None:  # still room: guess again from the new render
                extra = _guess(cv, trial, candidates[lo:], free_lines(trial_pdf))
                n = lo + max(1, extra)
                n = min(n, len(candidates))
            else:
                n = (lo + hi) // 2
        else:
            hi = n
            n = (lo + hi) // 2
        if hi is not None and hi - lo <= 1:
            break
        if n <= lo:
            n = lo + 1
    added = list(candidates[:lo])

    # Top-up: the next bullet in order may not fit while a shorter one, in an entry that's
    # already on the page (no new heading), still would. Try the cheapest few that remain.
    bullets = cv.bullets()
    rest = [c for c in candidates[lo:] if c not in added]
    while rest and renders < max_renders:
        entries = {where[b.source_id][1] for b in best_resume.bullets if b.source_id in where}
        rest = [c for c in rest if where[c][1] in entries]
        if not rest:
            break
        rest.sort(key=lambda c: (_lines(bullets[c].text), len(bullets[c].text)))
        pick = rest.pop(0)
        trial = _with(best_resume, cv, [pick])
        trial_pdf = render(trial)
        renders += 1
        if pdf_page_count(trial_pdf) == 1:
            best_resume, best_pdf = trial, trial_pdf
            added.append(pick)
        elif _lines(bullets[pick].text) == 1:
            break  # even the cheapest kind of bullet doesn't fit: the page is full
    return FillResult(best_resume, best_pdf, 1, added=added, removed=removed, renders=renders)


# --- keyword coverage -----------------------------------------------------------------------


@dataclass
class Coverage:
    score: float | None  # matched / total, 0-1; None when there are no keywords
    matched: list[str]
    missing: list[str]


MAX_KEYWORDS = 20


def keyword_coverage(keywords: list[str], layout: dict, posting_text: str | None = None) -> Coverage:
    """Which of the posting's keywords appear anywhere on the rendered resume.

    With `posting_text`, keywords that aren't in the posting are dropped (a model sometimes
    adds its own, or the candidate's), and at most MAX_KEYWORDS are kept, most important first.
    """
    seen: set[str] = set()
    unique = []
    for k in (k.strip() for k in keywords):
        if k and k.lower() not in seen:  # the model may repeat a term in another case
            seen.add(k.lower())
            unique.append(k)
    if posting_text is not None:
        unique = [k for k in unique if _has_phrase(posting_text, k)]
    keywords = unique[:MAX_KEYWORDS]
    parts = [layout.get("name", "")]
    for section in layout.get("sections", []):
        for item in section.get("items", []):
            parts += [item.get(k, "") for k in ("left1", "right1", "left2", "right2")]
            parts += item.get("bullets", [])
    parts += [f"{g['name']} {g['items']}" for g in layout.get("skills", [])]
    text = "\n".join(str(p) for p in parts)
    matched = [k for k in keywords if _has_phrase(text, k)]
    missing = [k for k in keywords if k not in matched]
    return Coverage(round(len(matched) / len(keywords), 2) if keywords else None, matched, missing)
