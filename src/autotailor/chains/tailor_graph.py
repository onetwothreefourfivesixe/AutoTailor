"""Tailor a resume to one posting: draft, verify, retry.

    START -> draft -> verify --(passed, or out of attempts)--> END
               ^         |
               +---------+  (problems found: redraft with them as feedback)

`draft` asks the model for a TailoredResume: which CV bullets to use, starting
from the usual resume (bullets marked `base: true`) and changing it for this
posting, in the candidate's own wording (a posting's term may be swapped in),
plus the most relevant skills (prompt "tailor" in structured_prompts.json).
`verify` runs verify_resume, which rejects anything the CV doesn't say: unknown
or hidden bullet IDs, new numbers, added tools or names, heavy rewording,
bloated bullets, too few or too many bullets. Its messages go back into the
next draft's prompt. AutoTailor only renders a resume that passes.

The final state has `resume` (the last draft), `errors` (its problems, [] if it
passed), `attempts`, and `history`: one entry per draft with the bullets it
chose and the problems found, shown on the posting's Resume tab.
"""

from __future__ import annotations

import difflib
import operator
from typing import TYPE_CHECKING, Annotated, NamedTuple, TypedDict

from langgraph.graph import END, START, StateGraph

from autotailor.chains.prompts import load_prompt
from autotailor.config import Settings, TailorSettings
from autotailor.cv import MasterCV
from autotailor.llm import get_chat_model
from autotailor.matching import BASE_MARK
from autotailor.models import TailoredBullet, TailoredResume
from autotailor.resume.verify import verify_resume

WORDING = {
    "light": "Copy each bullet's text as written. Where the bullet already says something the posting "
    "names with a different word, you may use the posting's word instead (\"made charts\" -> \"data "
    "visualization\"). Change only those few words; keep the rest, including the sentence structure, exactly.",
    "off": "Copy each bullet's text exactly as written in the profile, word for word.",
}


def effective_limits(limits: TailorSettings, cv: MasterCV) -> TailorSettings:
    """Bullet limits for this CV: a usual resume longer than max_bullets raises the maximum,
    so starting from it is never an error."""
    base = len(cv.base_bullets())
    if base + 4 <= limits.max_bullets:
        return limits
    return limits.model_copy(update={"max_bullets": base + 4})


def _known_id(sid: str, bullets: dict) -> str:
    """A mistyped bullet ID mapped to the real one when that's unambiguous: the right name
    with the wrong section prefix ('exp-calhacks-pipeline' for 'proj-calhacks-pipeline'),
    or a near-identical spelling. Verification still checks the text against that bullet."""
    if sid in bullets:
        return sid
    tail = sid.split("-", 1)[-1]
    same_tail = [i for i in bullets if i.split("-", 1)[-1] == tail]
    if len(same_tail) == 1:
        return same_tail[0]
    close = difflib.get_close_matches(sid, list(bullets), n=2, cutoff=0.9)
    return close[0] if len(close) == 1 else sid


def clean_draft(resume: TailoredResume, cv: MasterCV, rewording: str) -> TailoredResume:
    """Small fixes that need no retry: mistyped IDs, the (usual) mark copied into the text,
    skills the CV doesn't list (dropped: the resume shows only CV skills anyway), and with
    rewording off, every bullet's text set to the CV's (so it is word for word)."""
    bullets = cv.bullets()
    fixed = []
    for b in resume.bullets:
        text = b.text.strip()
        if text.startswith(BASE_MARK):
            text = text[len(BASE_MARK):].strip()
        sid = _known_id(b.source_id.strip().strip("[]"), bullets)
        if rewording == "off" and sid in bullets:
            text = bullets[sid].text
        fixed.append(b.model_copy(update={"source_id": sid, "text": text}))
    known = {" ".join(s.lower().split()): s for g in cv.skills for s in g.items}
    skills = list(dict.fromkeys(known[k] for k in (" ".join(s.lower().split()) for s in resume.skills) if k in known))
    return resume.model_copy(update={"bullets": fixed, "skills": skills})

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph


class TailorState(TypedDict, total=False):
    posting: str  # input: the posting text
    profile: str  # input: the candidate profile with [bullet-id] markers
    resume: TailoredResume | None  # the latest draft
    errors: list[str]  # the latest draft's problems ([] when it passed)
    attempts: int  # drafts made so far
    history: Annotated[list[dict], operator.add]  # every draft: {"attempt", "bullets", "errors"}


def build_graph(llm: BaseChatModel, cv: MasterCV, limits: TailorSettings) -> CompiledStateGraph:
    """The draft -> verify -> (retry | END) graph for one posting."""
    limits = effective_limits(limits, cv)
    prompt = load_prompt("tailor", TailoredResume).partial(
        min_bullets=str(limits.min_bullets), max_bullets=str(limits.max_bullets), wording=WORDING[limits.rewording]
    )
    draft_chain = prompt | llm.with_structured_output(TailoredResume)

    def draft(state: TailorState) -> dict:
        errors = state.get("errors", [])
        feedback = "\n".join(errors) if errors else ""
        resume = draft_chain.invoke({"posting": state["posting"], "profile": state["profile"], "feedback": feedback})
        resume = clean_draft(resume, cv, limits.rewording)
        return {"resume": resume, "attempts": state.get("attempts", 0) + 1}

    def verify(state: TailorState) -> dict:
        errors = verify_resume(state["resume"], cv, limits)
        entry = {
            "attempt": state["attempts"],
            "bullets": [b.source_id for b in state["resume"].bullets],
            "errors": errors,
        }
        return {"errors": errors, "history": [entry]}  # history appends (list reducer)

    def route(state: TailorState) -> str:
        if not state["errors"] or state["attempts"] >= limits.max_attempts:
            return "done"
        return "retry"

    graph = StateGraph(TailorState)
    graph.add_node("draft", draft)
    graph.add_node("verify", verify)
    graph.add_edge(START, "draft")
    graph.add_edge("draft", "verify")
    graph.add_conditional_edges("verify", route, {"retry": "draft", "done": END})
    return graph.compile()


class TailorResult(NamedTuple):
    resume: TailoredResume | None
    errors: list[str]
    attempts: int
    history: list[dict] = []  # one entry per draft
    note: str | None = None  # set when the usual resume was used because no draft passed


FALLBACK_NOTE = "No draft passed verification, so this is your usual resume, reordered and filled for the posting."


def usual_resume(cv: MasterCV, last: TailoredResume | None = None) -> TailoredResume | None:
    """The usual resume (bullets marked `base`), word for word, keeping the last draft's skills
    and keywords; None if no bullet is marked."""
    ids = cv.base_bullets()
    if not ids:
        return None
    bullets = cv.bullets()
    chosen = [b.source_id for b in (last.bullets if last else []) if b.source_id in ids]
    order = list(dict.fromkeys([*chosen, *ids]))  # the draft's picks (most relevant) first
    return TailoredResume(
        bullets=[TailoredBullet(source_id=i, text=bullets[i].text) for i in order],
        skills=list(last.skills) if last else [], keywords=list(last.keywords) if last else [],
    )  # fmt: skip


def tailor_posting(posting: str, profile: str, cv: MasterCV, settings: Settings) -> TailorResult:
    """Run the graph for one posting with the `tailor` step's model. If no draft passes and
    the CV marks a usual resume, that is used instead (with `note` saying so)."""
    graph = build_graph(get_chat_model(settings, "tailor"), cv, settings.tailor)
    final = graph.invoke({"posting": posting, "profile": profile})
    history = [dict(h) for h in final.get("history", [])]
    resume, errors, attempts = final.get("resume"), list(final.get("errors", [])), final.get("attempts", 0)
    if resume is None or errors:
        fallback = usual_resume(cv, resume)
        if fallback is not None and not verify_resume(fallback, cv, effective_limits(settings.tailor, cv)):
            history.append({"attempt": attempts + 1, "bullets": [b.source_id for b in fallback.bullets],
                            "errors": [], "note": FALLBACK_NOTE})  # fmt: skip
            return TailorResult(fallback, [], attempts, history, FALLBACK_NOTE)
    return TailorResult(resume, errors, attempts, history)
