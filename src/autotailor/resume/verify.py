"""Check that a tailored resume says nothing the master CV doesn't.

Every bullet must name a real CV bullet as its source, and its rephrased text
may not introduce numbers, or names/technical terms, that the source bullet
(with its job or project title) and the CV's skills don't already contain.
Small wording changes are allowed (a posting's term for what the bullet already
says), but a bullet rewritten in the model's own words is rejected and the
CV's text is sent back to copy: the candidate's wording is the point.

The messages are written for the model: on a retry they are sent back as
feedback, so each one says what is wrong and how to fix it.
"""

from __future__ import annotations

import difflib
import re

from autotailor.config import TailorSettings
from autotailor.cv import Entry, MasterCV
from autotailor.models import TailoredResume

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*\s*[kKmMbB]?\b\+?%?")
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.\-/&]*")


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[,\s]", "", m.group()).lower().rstrip("+%") for m in _NUMBER.finditer(text)}


def _words(text: str) -> list[str]:
    return [w.rstrip(".-/") for w in _WORD.findall(text)]


def _is_term(word: str, first: bool) -> bool:
    """Words that look like names or technologies: digits, symbols, or capitals not
    explained by being the first word."""
    if any(c.isdigit() for c in word) or any(c in word for c in "+#&"):
        return True
    caps = sum(c.isupper() for c in word)
    return caps > 1 or (caps == 1 and not first and word[0].isupper())


def _terms(text: str) -> set[str]:
    out = set()
    for i, w in enumerate(_words(text)):
        if w and not w[0].isdigit() and _is_term(w, first=i == 0):
            out.add(w.lower())
    return out


def _vocabulary(text: str) -> set[str]:
    """Lowercase words in `text`, plus the parts of hyphenated or slashed words, so
    'YOLO-based' in a source allows 'YOLO' in the rewrite."""
    out = set()
    for w in _words(text):
        w = w.lower()
        out.add(w)
        out.update(part for part in re.split(r"[-/]", w) if part)
    return out


def _parents(cv: MasterCV) -> dict[str, str]:
    """Bullet ID -> text of the entry it belongs to (title, organization)."""
    out: dict[str, str] = {}
    entries: list[Entry] = cv.entries()
    for e in entries:
        for b in e.bullets:
            out[b.id] = f"{e.title} {e.organization or ''} {' '.join(e.stack)}"
    for ed in cv.education:
        for b in ed.details:
            out[b.id] = f"{ed.school} {ed.degree} {ed.field or ''}"
    return out


MIN_SIMILARITY = 0.75  # word level; the rewrites that read worse than the source scored 0.35-0.69


def similarity(a: str, b: str) -> float:
    """How much of the wording two bullets share, 0-1 (word level, ignoring case)."""
    return difflib.SequenceMatcher(None, _words(a.lower()), _words(b.lower()), autojunk=False).ratio()


def _norm_skill(s: str) -> str:
    return " ".join(s.lower().split())


def verify_resume(r: TailoredResume, cv: MasterCV, limits: TailorSettings) -> list[str]:
    """Problems with the draft, as instructions to fix them. An empty list means it passed."""
    errors: list[str] = []
    bullets = cv.bullets()
    parents = _parents(cv)
    skill_items = [item for g in cv.skills for item in g.items]
    skill_words = set().union(*(_vocabulary(item) for item in skill_items)) if skill_items else set()

    n = len(r.bullets)
    if n < limits.min_bullets:
        errors.append(f"Only {n} bullets. Include at least {limits.min_bullets} relevant CV bullets.")
    if n > limits.max_bullets:
        errors.append(f"{n} bullets. Use at most {limits.max_bullets} so the resume fits on one page.")

    hidden = cv.hidden_bullets()
    seen: set[str] = set()
    for i, b in enumerate(r.bullets, 1):
        sid = b.source_id.strip().strip("[]")
        if sid not in bullets:
            errors.append(
                f"Bullet {i}: source_id '{b.source_id}' is not a bullet ID from the profile. "
                "Use only IDs shown in [brackets]."
            )
            continue
        if sid in seen:
            errors.append(f"Bullet {i}: '{sid}' is used more than once. Use each CV bullet at most once.")
        seen.add(sid)
        if sid in hidden:
            errors.append(f"Bullet {i}: '{sid}' belongs to an entry hidden from resumes. Choose another bullet.")
            continue

        source = bullets[sid].text
        if similarity(b.text, source) < MIN_SIMILARITY:
            errors.append(
                f"Bullet {i} ({sid}): reworded too much. Copy it as written, changing at most a few "
                f"words to the posting's terms: \"{source}\""
            )
        new_numbers = _numbers(b.text) - _numbers(source)
        if new_numbers:
            errors.append(
                f"Bullet {i} ({sid}): the numbers {sorted(new_numbers)} are not in the source bullet. "
                "Keep the source's figures exactly and add no new ones."
            )
        allowed = _vocabulary(f"{source} {parents.get(sid, '')}") | skill_words
        new_terms = sorted(
            t for t in _terms(b.text)
            if t not in allowed and not all(part in allowed for part in re.split(r"[-/]", t) if part)
        )
        if new_terms:
            errors.append(
                f"Bullet {i} ({sid}): {', '.join(new_terms)} not in the source bullet or the CV's "
                "skills. Do not add tools, names or claims the source does not state."
            )
        if len(b.text) > max(len(source) * limits.length_growth, len(source) + 40):
            errors.append(
                f"Bullet {i} ({sid}): much longer than its source ({len(b.text)} vs {len(source)} "
                "characters). Rephrase or trim; do not add content."
            )
        if not b.text.strip():
            errors.append(f"Bullet {i} ({sid}): empty text.")

    known_skills = {_norm_skill(s) for s in skill_items}
    unknown = [s for s in r.skills if _norm_skill(s) not in known_skills]
    if unknown:
        errors.append(
            f"Skills {unknown} are not in the CV's skills section. List only skills the CV contains."
        )
    return errors
