"""M8 exercise: short answers. Suggest points, let the user opt in, draft, humanize, check.

    START -> suggest -> choose --(points only)--> END
                          |
                       (opt in)
                          v
                        draft -> humanize -> check --(clean, or out of attempts)--> END
                                    ^          |
                                    +----------+  (problems: humanize again with them as feedback)

- suggest: tools.suggest(question_id) -> the 2-5 CV points worth mentioning.
- choose:  interrupt() with the question and the points. The user answers
           {"draft": False}                      keep the points only, or
           {"draft": True, "use": [1, 3], "notes": "..."}
           ("use": 1-based point numbers, default all; "notes": optional extra direction).
- draft:   your writing chain turns the chosen points into an answer.
- humanize: your second chain rewrites it so it doesn't read as AI-written
           (the "humanize" prompt in structured_prompts.json carries the rubric).
- check:   tools.check(...) -> problems: invented numbers or names, over the word
           limit, stock AI phrasing. Problems go back to humanize as feedback.

`tools` (autotailor/answers.py, given):
- tools.suggest(question_id) -> list[dict]          each {"source_id", "point", "why", "cv_text"}
- tools.question(question_id) -> dict               {"question_id", "question", "word_limit", "company", "title"}
- tools.draft_inputs(question_id, chosen, notes) -> dict       variables for your draft prompt
- tools.humanize_inputs(question_id, text, problems) -> dict   variables for the humanize prompt
- tools.check(question_id, text, chosen, notes) -> list[str]   [] when the answer is clean

You implement everything marked TODO, plus the "draft_answer" prompt in
structured_prompts.json, with the variables {question}, {company}, {title},
{word_limit}, {points} and {notes}. Check your work with:

    uv run pytest tests/test_m8_answer_graph.py

Then for real: `autotailor answers add <posting id> "Why do you want to work here? (150 words)"`,
`autotailor answers run`.

------------------------------------------------------------------------------
New this time
------------------------------------------------------------------------------

1. Plain-text output.  Drafting produces prose, not a schema, so there is no
   with_structured_output. A model returns an AIMessage; StrOutputParser turns
   it into a str:

       from langchain_core.output_parsers import StrOutputParser
       chain = prompt | llm | StrOutputParser()
       text = chain.invoke({...})          # -> str

2. Two models.  `draft_llm` and `humanize_llm` may be different models (the
   "tailor" and "humanize" steps in config.toml), so build two chains.

3. Everything else you've seen: an interrupt for the opt-in (M7), a check/retry
   loop with feedback and an attempts limit (M4), a checkpointer and a thread
   per question (M5, M7). Keep `suggest` out of the node that interrupts: it is
   a model call, and that node re-runs on resume.

------------------------------------------------------------------------------
Contract (the tests check these)
------------------------------------------------------------------------------

- build_answer_graph(tools, draft_llm, humanize_llm, checkpointer, max_attempts)
  returns a compiled graph with nodes "suggest", "choose", "draft", "humanize"
  and "check". Input state: {"question_id": int}.
- The interrupt payload is tools.question(...) plus "points" (the suggested list).
- Only the chosen points reach the draft prompt; the user's notes do too.
- Each humanize pass edits the latest text; after a failed check, the next
  humanize prompt includes the problems. At most max_attempts humanize passes.
- run_answer(graph, question_id, decision=None) -> Outcome, like run_resolve:
    - no decision, new thread: run it; no decision, started thread: report it
      without running anything; a decision: resume the waiting thread with it
      (ValueError if it isn't waiting).
    - Outcome.status: "waiting" (payload: the interrupt payload), "points"
      (payload: {"points": [...]}), "drafted" (clean) or "needs_review"
      (problems left after max_attempts). For the last two the payload is
      {"points", "chosen", "text", "problems", "attempts"}.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple, TypedDict

from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from autotailor.chains.prompts import load_prompt

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

    from autotailor.answers import AnswerTools


class Outcome(NamedTuple):
    status: Literal["waiting", "points", "drafted", "needs_review"]
    payload: dict[str, Any]


def thread_id(question_id: int) -> str:
    """One checkpoint thread per question. Given."""
    return f"answer-{question_id}"


class AnswerState(TypedDict, total=False):
    """Everything the graph carries between nodes.

    total=False: the graph starts with only `question_id`; every other key is
    filled in by the node that learns it. Each key has a reader:
    """

    question_id: int  # input; every node passes it to `tools`
    points: list[dict[str, Any]]  # suggest -> choose (shown to the user) and run_answer (payload)
    chosen: list[dict[str, Any]]  # choose -> draft (what to write from) and check (what facts are allowed)
    notes: str  # choose -> draft and check (the user's own words are allowed facts too)
    text: str  # draft -> humanize -> check: always the *latest* version of the answer
    problems: list[str]  # check -> humanize (feedback for the next pass) and run_answer
    attempts: int  # humanize -> check (when to give up)
    outcome: str  # choose/check -> routers and run_answer: "points", "drafted" or "needs_review"
    # No reducers: every key is replaced, never appended to. In particular `text`
    # must be the newest edit only, and `problems` only the latest check's list.


def build_answer_graph(
    tools: AnswerTools,
    draft_llm: BaseChatModel,
    humanize_llm: BaseChatModel,
    checkpointer: BaseCheckpointSaver | None = None,
    max_attempts: int = 3,
) -> CompiledStateGraph:
    """suggest -> choose -> (END | draft -> humanize -> check -> (END | humanize))."""

    # Two plain-text chains, built once (not inside the nodes, which may run many times).
    # prompt -> model -> StrOutputParser: the model returns an AIMessage; the parser keeps
    # just its text. No with_structured_output: the result is prose, not a schema.
    draft_chain = load_prompt("draft_answer") | draft_llm | StrOutputParser()
    humanize_chain = load_prompt("humanize") | humanize_llm | StrOutputParser()

    # --- nodes: each returns only the keys it changes -------------------------------

    def suggest(state: AnswerState) -> dict:
        # A model call, so it lives in its own node: `choose` (which interrupts) re-runs
        # from the top on resume, and anything inside it would run twice.
        return {"points": tools.suggest(state["question_id"])}

    def choose(state: AnswerState) -> dict:
        # The opt-in. First run: interrupt() pauses here and the payload (question + points)
        # goes to whoever invoked the graph. On resume, the same line returns the user's
        # decision instead, and the rest of the node runs.
        payload = {**tools.question(state["question_id"]), "points": state["points"]}
        decision = interrupt(payload)

        if not decision.get("draft"):
            # Opted out: setting `outcome` is what makes the router end the graph here.
            return {"outcome": "points"}

        # "use" is 1-based point numbers; missing or empty means all of them.
        points = state["points"]
        use = decision.get("use") or list(range(1, len(points) + 1))
        chosen = [points[i - 1] for i in use if 1 <= i <= len(points)]
        return {"chosen": chosen, "notes": decision.get("notes") or ""}

    def draft(state: AnswerState) -> dict:
        # tools.draft_inputs formats the chosen points (with their CV text) and the notes
        # into the prompt's variables; the chain returns a str.
        inputs = tools.draft_inputs(state["question_id"], state["chosen"], state.get("notes", ""))
        text = draft_chain.invoke(inputs)
        # Reset the loop's bookkeeping: no problems yet, no humanize passes yet.
        return {"text": text, "problems": [], "attempts": 0}

    def humanize(state: AnswerState) -> dict:
        # Edits the *latest* text (the draft on the first pass, the previous edit after
        # that), with the last check's problems as feedback ("None yet." on the first pass).
        inputs = tools.humanize_inputs(state["question_id"], state["text"], state.get("problems", []))
        text = humanize_chain.invoke(inputs)
        return {"text": text, "attempts": state.get("attempts", 0) + 1}

    def check(state: AnswerState) -> dict:
        # Deterministic, like M4's verify: invented numbers/names, word limit, AI phrasing.
        problems = tools.check(state["question_id"], state["text"], state["chosen"], state.get("notes", ""))
        if not problems:
            return {"problems": [], "outcome": "drafted"}
        if state["attempts"] >= max_attempts:
            # Out of passes: stop, but keep the problems so the user can fix them by hand.
            return {"problems": problems, "outcome": "needs_review"}
        # Leave `outcome` unset: the router sends the graph back to humanize with these.
        return {"problems": problems}

    # --- routing: the same question after choose and after check: "finished yet?" -----

    def after_choose(state: AnswerState) -> str:
        return "end" if state.get("outcome") else "draft"

    def after_check(state: AnswerState) -> str:
        return "end" if state.get("outcome") else "humanize"

    # --- wiring ------------------------------------------------------------------------

    builder = StateGraph(AnswerState)
    builder.add_node("suggest", suggest)
    builder.add_node("choose", choose)
    builder.add_node("draft", draft)
    builder.add_node("humanize", humanize)
    builder.add_node("check", check)

    builder.add_edge(START, "suggest")
    builder.add_edge("suggest", "choose")
    builder.add_conditional_edges("choose", after_choose, {"end": END, "draft": "draft"})
    builder.add_edge("draft", "humanize")
    builder.add_edge("humanize", "check")
    builder.add_conditional_edges("check", after_check, {"end": END, "humanize": "humanize"})

    # The checkpointer is what lets interrupt() pause and a later process resume.
    return builder.compile(checkpointer=checkpointer)


# --- running it ----------------------------------------------------------------------------


def _outcome(values: dict[str, Any], interrupts: Any) -> Outcome:
    """A paused or finished state -> Outcome (same idea as M7's run_resolve)."""
    if interrupts:  # paused at choose: the payload is what choose passed to interrupt()
        return Outcome("waiting", interrupts[0].value)
    if values["outcome"] == "points":
        return Outcome("points", {"points": values["points"]})
    keys = ("points", "chosen", "text", "problems", "attempts")
    return Outcome(values["outcome"], {k: values.get(k) for k in keys})


def run_answer(graph: CompiledStateGraph, question_id: int, decision: dict[str, Any] | None = None) -> Outcome:
    """Start, inspect, or resume the thread for `question_id`."""
    config = {"configurable": {"thread_id": thread_id(question_id)}}
    snapshot = graph.get_state(config)  # reads the checkpoint; runs nothing

    if decision is not None:
        # Resume: interrupt() inside `choose` returns `decision`; the graph then drafts
        # (or stops) and runs to the end or the next pause.
        if not snapshot.interrupts:
            raise ValueError(f"Question {question_id} is not waiting for a decision.")
        result = graph.invoke(Command(resume=decision), config)
    elif snapshot.values:
        # Started before: report where it stands without running any node.
        return _outcome(snapshot.values, snapshot.interrupts)
    else:
        # New: suggest runs, then choose pauses.
        result = graph.invoke({"question_id": question_id}, config)

    # invoke returns the state; when the graph paused it also has "__interrupt__".
    return _outcome(result, result.get("__interrupt__"))
