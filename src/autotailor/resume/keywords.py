"""Add chosen ATS keywords to a finished resume, each exactly once.

The user picks keywords (usually ones the keyword match reports as missing) that are true
for them. The `tailor` step's model places each one (prompt "add_keywords"): reworded into
the one bullet whose experience involves it, or, when no bullet does, into a skills group.

Every placement is checked before it is used. A reworded bullet must contain the term, keep
most of its words, and add no numbers, names or tools other than the term's own; one that
fails goes into the skills section instead, so the term is still added, once. Terms already
on the page are skipped. The result is a proposal: the caller shows it for review (or
saves it, from the command line).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from autotailor.config import Settings
from autotailor.resume.fill import _has_phrase, _phrase_pattern
from autotailor.resume.verify import _numbers, _terms, _vocabulary, similarity

MIN_SIMILARITY = 0.7  # a bullet gains a term, it isn't rewritten
MAX_GROWTH = 60  # characters a bullet may grow by


class Placement(BaseModel):
    term: str = Field(description="The keyword, exactly as given.")
    target: str = Field(description="Where it goes: a bullet number like 'B3', or a skills group like 'S1'.")
    text: str | None = Field(
        default=None,
        description="For a bullet: the whole bullet rewritten to include the term once, every other fact kept as "
        "it was. Leave empty for a skills group.",
    )


class KeywordPlan(BaseModel):
    placements: list[Placement] = Field(description="One placement per keyword.")


class Rewrite(BaseModel):
    text: str = Field(description="The bullet with the keyword worked in once, every other fact kept.")


@dataclass
class Change:
    term: str
    where: str  # "Research Intern, TokenWorks" or "Skills: Languages"
    before: str
    after: str
    note: str = ""  # e.g. why a bullet edit went to skills instead


@dataclass
class KeywordResult:
    layout: dict[str, Any]
    changes: list[Change] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)  # {term, reason}


def page_text(layout: dict[str, Any]) -> str:
    parts = [layout.get("name", "")]
    for s in layout.get("sections", []):
        parts.append(s.get("title", ""))
        for it in s.get("items", []):
            parts += [it.get(k, "") or "" for k in ("title", "organization", "location", "dates", "stack")]
            parts += it.get("bullets", [])
    parts += [f"{g['name']}: {g['items']}" for g in layout.get("skills", [])]
    return "\n".join(parts)


def _bullets(layout: dict[str, Any]) -> list[tuple[int, int, int, str, str]]:
    """(section, item, bullet index, text, entry label) for every bullet on the page."""
    out = []
    for si, s in enumerate(layout.get("sections", [])):
        for ii, it in enumerate(s.get("items", [])):
            label = ", ".join(x for x in (it.get("title"), it.get("organization")) if x) or s.get("title", "")
            for bi, text in enumerate(it.get("bullets", [])):
                out.append((si, ii, bi, text, label))
    return out


def fix_article(text: str, term: str) -> str:
    """'a multi-agent RAG pipelines' -> 'a multi-agent RAG pipeline': a plural keyword right after
    "a"/"an" (with up to two words between) takes the singular, so the sentence stays grammatical."""
    last = term.split()[-1] if term.split() else ""
    if len(last) <= 3 or not last.lower().endswith("s") or last.lower().endswith("ss"):
        return text
    head = re.escape(term[: -1])  # the term without its final "s"
    return re.sub(rf"(\b(?:a|an)\s+(?:[\w-]+\s+){{0,2}}{head})s\b", r"\1", text, flags=re.I)


def check_bullet(before: str, after: str, term: str) -> str | None:
    """Why a reworded bullet can't be used, or None if it can."""
    if not _has_phrase(after, term):
        return "the rewrite didn't include the term"
    if len(re.findall(_phrase_pattern(term) or "$^", after.lower())) > 1:
        return "the rewrite used the term more than once"
    if similarity(after, before) < MIN_SIMILARITY:
        return "the rewrite changed the bullet too much"
    if len(after) > len(before) + len(term) + MAX_GROWTH:
        return "the rewrite made the bullet much longer"
    allowed = _vocabulary(before) | _vocabulary(term)
    if _numbers(after) - _numbers(before) - _numbers(term):
        return "the rewrite added numbers"
    new = [t for t in _terms(after) if t not in allowed and not all(p in allowed for p in re.split(r"[-/]", t) if p)]
    if new:
        return f"the rewrite added {', '.join(sorted(new))}"
    return None


def _best_group(skills: list[dict], term: str) -> int:
    """The skills group sharing the most words with the term ("Apache Spark" -> the one listing
    Spark); the last group when none does."""
    words = set(re.findall(r"[a-z0-9+#]+", term.lower()))
    scores = [len(words & set(re.findall(r"[a-z0-9+#]+", f"{g['name']} {g['items']}".lower()))) for g in skills]
    best = max(scores, default=0)
    return scores.index(best) if best else len(skills) - 1


def _add_to_skills(layout: dict[str, Any], term: str, group: int | None) -> Change:
    skills = layout.setdefault("skills", [])
    if not skills:
        skills.append({"name": "Skills", "items": ""})
    if group is None or not 0 <= group < len(skills):
        group = _best_group(skills, term)
    g = skills[group]
    before = g["items"]
    items = [i.strip() for i in before.split(",") if i.strip()]
    words = set(re.findall(r"[a-z0-9+#]+", term.lower()))
    # A shorter name for the same thing ("Spark" for "Apache Spark") is replaced, not repeated.
    same = next((n for n, i in enumerate(items) if set(re.findall(r"[a-z0-9+#]+", i.lower())) < words), None)
    if same is not None:
        items[same] = term
    else:
        items.append(term)
    g["items"] = ", ".join(items)
    return Change(term, f"Skills: {g['name']}", before, g["items"])


def plan_prompt_inputs(layout: dict[str, Any], terms: list[str], posting: str) -> dict[str, str]:
    bullets = "\n".join(f"B{n} [{label}] {text}" for n, (_, _, _, text, label) in enumerate(_bullets(layout), 1))
    skills = "\n".join(f"S{n} {g['name']}: {g['items']}" for n, g in enumerate(layout.get("skills", []), 1))
    return {"terms": "\n".join(f"- {t}" for t in terms), "bullets": bullets or "(none)", "skills": skills or "(none)",
            "posting": posting[:4000]}  # fmt: skip


def add_keywords(layout: dict[str, Any], terms: list[str], posting: str, settings: Settings, llm: Any = None) -> KeywordResult:
    """A copy of `layout` (render-ready) with each term added once, and what changed."""
    import copy

    layout = copy.deepcopy(layout)
    result = KeywordResult(layout)
    wanted: list[str] = []
    for t in (" ".join(t.split()) for t in terms):
        if not t or t.lower() in {w.lower() for w in wanted}:
            continue
        if _has_phrase(page_text(layout), t):
            result.skipped.append({"term": t, "reason": "already on the resume"})
        else:
            wanted.append(t)
    if not wanted:
        return result

    from autotailor.chains.prompts import load_prompt

    if llm is None:
        from autotailor.llm import get_chat_model

        llm = get_chat_model(settings, "tailor")
    chain = load_prompt("add_keywords", KeywordPlan) | llm.with_structured_output(KeywordPlan)
    plan: KeywordPlan = chain.invoke(plan_prompt_inputs(layout, wanted, posting))

    rewrite = load_prompt("insert_keyword", Rewrite) | llm.with_structured_output(Rewrite)
    bullets = _bullets(layout)
    by_term = {p.term.strip().lower(): p for p in reversed(plan.placements)}  # first placement per term wins
    edited: set[tuple[int, int, int]] = set()
    for term in wanted:
        p = by_term.get(term.lower())
        target = (p.target.strip().upper() if p else "")
        note = ""
        m = re.fullmatch(r"B(\d+)", target)
        if m and p and p.text and 1 <= int(m.group(1)) <= len(bullets):
            si, ii, bi, _, label = bullets[int(m.group(1)) - 1]
            current = layout["sections"][si]["items"][ii]["bullets"][bi]  # may already hold another term
            new = " ".join(p.text.split())
            why = "that bullet already gained a keyword" if (si, ii, bi) in edited else check_bullet(current, new, term)
            if why == "the rewrite didn't include the term":
                # Small models often copy the bullet unchanged; one focused request per bullet works better.
                try:
                    new = " ".join(rewrite.invoke({"bullet": current, "term": term}).text.split())
                    why = check_bullet(current, new, term)
                except Exception:  # invalid output: fall back to skills
                    pass
            if why is None:
                new = fix_article(new, term)
                layout["sections"][si]["items"][ii]["bullets"][bi] = new
                edited.add((si, ii, bi))
                result.changes.append(Change(term, label, current, new))
                continue
            note = f"Added to skills instead: {why}."
        s = re.fullmatch(r"S(\d+)", target)
        change = _add_to_skills(layout, term, int(s.group(1)) - 1 if s else None)
        change.note = note or ("" if p else "The model didn't place it; added to skills.")
        result.changes.append(change)
    return result
