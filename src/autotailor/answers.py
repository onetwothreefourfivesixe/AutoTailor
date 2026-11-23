"""Short-answer questions (M8): suggest points from the CV, then (opt-in) a drafted answer.

`AnswerTools` is what the answer graph (chains/answer_graph.py)
calls for everything that isn't drafting or humanizing:

- `suggest`: the 2-5 CV experiences worth mentioning (a model call, checked:
  points must cite real CV bullet IDs);
- `question`, `draft_inputs`, `humanize_inputs`: the values each prompt needs;
- `check`: the facts-and-style check run on every humanized draft. The answer
  may only use numbers and names found in the chosen CV bullets, their entries,
  the CV skills, your profile, the posting and the question; it must fit the
  word limit; and it must avoid the stock AI phrasing the humanizer removes.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from autotailor import db
from autotailor.config import Settings
from autotailor.cv import MasterCV
from autotailor.models import AnswerPoints
from autotailor.resume.verify import _numbers, _parents, _vocabulary, _words

# --- questions ---------------------------------------------------------------------------

_LIMIT_WORDS = re.compile(r"(\d{2,4})\s*(?:-|\s)?\s*words?\b|\bwords?\s*(?:limit|max(?:imum)?)\s*:?\s*(\d{2,4})", re.I)
_LIMIT_CHARS = re.compile(r"(\d{2,5})\s*(?:-|\s)?\s*char(?:acter)?s?\b", re.I)
WORDS_PER_CHAR = 1 / 6  # a rough conversion for character limits


def parse_question(text: str) -> tuple[str, int | None]:
    """'Why us? (150 words max)' -> ('Why us? (150 words max)', 150). Character limits are
    converted to an approximate word limit."""
    text = " ".join(text.split())
    if m := _LIMIT_WORDS.search(text):
        return text, int(m.group(1) or m.group(2))
    if m := _LIMIT_CHARS.search(text):
        return text, max(10, int(int(m.group(1)) * WORDS_PER_CHAR))
    return text, None


def split_questions(text: str) -> list[str]:
    """Questions in a file: separated by blank lines (a question may span several lines)."""
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", text) if block.strip()]


# --- the fact and style check -----------------------------------------------------------------

STOCK_PHRASES = [
    "delve", "tapestry", "testament to", "pivotal", "underscore", "vibrant", "showcas", "seamless",
    "cutting-edge", "realm", "landscape of", "deeply passionate", "thrilled", "not only", "in summary",
    "ultimately,", "overall,", "a wide range of", "plays a vital role", "fostering", "navigat",
]  # fmt: skip
_SENTENCE_START = re.compile(r"(?:^|[.!?]\s+|\n+)([A-Za-z][\w'-]*)")


def _sentence_starts(text: str) -> set[str]:
    return {m.group(1).lower() for m in _SENTENCE_START.finditer(text)}


def _prose_terms(text: str) -> set[str]:
    """Capitalized or technical words that aren't just starting a sentence (or 'I')."""
    starts = _sentence_starts(text)
    out = set()
    for w in _words(text):
        lw = w.lower()
        if not w or w[0].isdigit() or lw in ("i", "i'm", "i've", "i'd", "i'll"):
            continue
        technical = any(c.isdigit() for c in w) or any(c in w for c in "+#&")
        caps = sum(c.isupper() for c in w)
        if technical or caps > 1 or (caps == 1 and w[0].isupper() and lw not in starts):
            out.add(lw)
    return out


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def check_answer(text: str, allowed_text: str, word_limit: int | None) -> list[str]:
    """Problems with an answer, written as instructions for the next edit."""
    problems: list[str] = []
    new_numbers = sorted(_numbers(text) - _numbers(allowed_text))
    if new_numbers:
        problems.append(f"Remove the numbers {new_numbers}: they are not in the chosen experiences or the posting.")
    vocab = _vocabulary(allowed_text)
    new_terms = sorted(t for t in _prose_terms(text) if t not in vocab and not all(p in vocab for p in re.split(r"[-/]", t) if p))
    if new_terms:
        problems.append(f"Remove or replace {new_terms}: these names or terms are not in the chosen experiences, "
                        "the CV skills, the profile or the posting.")  # fmt: skip
    n = word_count(text)
    if word_limit and n > word_limit:
        problems.append(f"It is {n} words; the limit is {word_limit}. Cut to {word_limit} words or fewer.")
    if "—" in text or " – " in text:
        problems.append("Replace the dashes (— or –) with commas, periods or parentheses.")
    low = text.lower()
    stock = [p for p in STOCK_PHRASES if p in low]
    if stock:
        problems.append(f"Rephrase without these stock phrases: {stock}.")
    if re.search(r"\*\*|^#|^\s*[-*•]\s", text, re.M):
        problems.append("Write plain paragraphs: no bold, headings or bullet points.")
    return problems


# --- tools for the graph --------------------------------------------------------------------------


class AnswerTools:
    """The non-writing steps the answer graph calls. One SQLite connection: not thread-safe."""

    def __init__(self, conn: sqlite3.Connection, cv: MasterCV, settings: Settings, suggest_llm: Any = None) -> None:
        self.conn, self.cv, self.settings, self._llm = conn, cv, settings, suggest_llm

    def _q(self, question_id: int) -> sqlite3.Row:
        row = db.get_question(self.conn, question_id)
        if row is None:
            raise LookupError(f"No question {question_id}")
        return row

    def _posting(self, question_id: int):  # noqa: ANN202
        return db.get_posting(self.conn, self._q(question_id)["posting_id"])

    def question(self, question_id: int) -> dict[str, Any]:
        q, p = self._q(question_id), self._posting(question_id)
        return {
            "question_id": question_id, "question": q["text"], "word_limit": q["word_limit"],
            "company": p.company if p else "?", "title": p.title if p else "?",
        }  # fmt: skip

    def suggest(self, question_id: int) -> list[dict[str, Any]]:
        """2-5 points from the CV for this question (model call). Unknown bullet IDs are dropped."""
        from autotailor import matching
        from autotailor.chains.prompts import load_prompt

        q, p = self._q(question_id), self._posting(question_id)
        llm = self._llm
        if llm is None:
            from autotailor.llm import get_chat_model

            llm = get_chat_model(self.settings, "score")
        from autotailor.llm import structured

        chain = load_prompt("suggest_points", AnswerPoints) | structured(llm, AnswerPoints).with_retry(
            stop_after_attempt=3
        )
        result: AnswerPoints = chain.invoke({
            "question": q["text"], "company": p.company, "title": p.title,
            "posting": matching.posting_text(p, 6000), "profile": matching.profile_text(self.cv, self.settings.profile),
        })  # fmt: skip
        bullets = self.cv.bullets()
        points, seen = [], set()
        for pt in result.points:
            sid = pt.source_id.strip().strip("[]")
            if sid in bullets and sid not in seen:
                seen.add(sid)
                points.append({"source_id": sid, "point": pt.point, "why": pt.why, "cv_text": bullets[sid].text})
        db.update_question(self.conn, question_id, points=points)
        return points

    def draft_inputs(self, question_id: int, chosen: list[dict[str, Any]], notes: str = "") -> dict[str, Any]:
        """Values for the draft prompt: {question}, {company}, {title}, {word_limit}, {points}, {notes}."""
        info = self.question(question_id)
        parents = _parents(self.cv)
        points = "\n".join(
            f"- {pt['point']} (from: {parents.get(pt['source_id'], '').strip()}; CV bullet: \"{pt['cv_text']}\")" for pt in chosen
        )
        return {
            "question": info["question"], "company": info["company"], "title": info["title"],
            "word_limit": info["word_limit"] or "none given; keep it under 200 words",
            "points": points or "(none chosen)", "notes": notes.strip() or "None.",
        }  # fmt: skip

    def humanize_inputs(self, question_id: int, text: str, problems: list[str]) -> dict[str, Any]:
        """Values for the humanize prompt: {text}, {feedback}, {word_limit}."""
        limit = self._q(question_id)["word_limit"]
        return {
            "text": text,
            "feedback": "\n".join(f"- {p}" for p in problems) if problems else "None yet.",
            "word_limit": limit or "none given; keep it under 200 words",
        }

    def allowed_text(self, question_id: int, chosen: list[dict[str, Any]], notes: str = "") -> str:
        """Everything the answer may draw facts from."""
        q, p = self._q(question_id), self._posting(question_id)
        parents = _parents(self.cv)
        prof = self.settings.profile
        parts = [q["text"], notes, p.company, p.title, p.description or "", self.cv.contact.name]
        parts += [f"{pt['cv_text']} {pt['point']} {parents.get(pt['source_id'], '')}" for pt in chosen]
        parts += [" ".join(g.items) + " " + g.name for g in self.cv.skills]
        parts += [f"{e.level.value} {e.field} {e.school or ''}" for e in prof.education]
        parts += [f"{e.school} {e.degree} {e.field or ''}" for e in self.cv.education]
        parts += prof.role_interests
        return "\n".join(parts)

    def check(self, question_id: int, text: str, chosen: list[dict[str, Any]], notes: str = "") -> list[str]:
        return check_answer(text, self.allowed_text(question_id, chosen, notes), self._q(question_id)["word_limit"])


def save_outcome(conn: sqlite3.Connection, question_id: int, out: Any, model: str) -> None:
    """Store what the answer graph produced (an Outcome) on the question."""
    status, p = out.status, out.payload
    if status == "waiting":
        db.update_question(conn, question_id, status="waiting", points=p.get("points", []))
    elif status == "points":
        db.update_question(conn, question_id, status="points", points=p["points"], answer=None, problems=[])
    else:
        db.update_question(conn, question_id, status=status, points=p["points"], answer=p["text"],
                           problems=p["problems"], model=model)  # fmt: skip


# --- output ---------------------------------------------------------------------------------------


def answers_markdown(conn: sqlite3.Connection, posting_id: str) -> str:
    p = db.get_posting(conn, posting_id)
    lines = [f"# Application answers: {p.title}, {p.company}" if p else f"# Application answers: {posting_id}", ""]
    for q in db.list_questions(conn, posting_id):
        limit = f" (limit {q['word_limit']} words)" if q["word_limit"] else ""
        lines += [f"## {q['text']}", ""]
        if q["answer"]:
            lines += [q["answer"], "", f"*{word_count(q['answer'])} words{limit}. Drafted by `{q['model']}`; edit before sending.*", ""]
            problems = json.loads(q["problems_json"] or "[]")
            if problems:
                lines += ["Still to fix by hand:"] + [f"- {x}" for x in problems] + [""]
        points = json.loads(q["points_json"] or "[]")
        if points:
            lines += ["Points to draw on:"] + [f"- {pt['point']} (`{pt['source_id']}`)" for pt in points] + [""]
        if not q["answer"] and not points:
            lines += ["(not started)", ""]
    return "\n".join(lines)


def write_answers(conn: sqlite3.Connection, posting_id: str, outputs: Path) -> Path:
    """data/outputs/answers/<company>-<title>.md, rewritten whenever a question changes."""
    from autotailor.resume.output import slug

    p = db.get_posting(conn, posting_id)
    name = f"{slug(p.company, 30)}-{slug(p.title, 50)}" if p else slug(posting_id, 60)
    path = outputs / "answers" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(answers_markdown(conn, posting_id), encoding="utf-8")
    return path
