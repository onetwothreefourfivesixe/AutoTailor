"""Short answers: suggest points, opt in to a draft, humanize, check.

    START -> suggest -> choose --(points only)--> END
                          |
                       (opt in)
                          v
                        draft -> humanize -> check --(clean, or out of attempts)--> END
                                    ^          |
                                    +----------+  (problems: humanize again with them as feedback)

- suggest: tools.suggest(question_id), the 2-5 CV points worth mentioning.
- choose:  interrupt() with the question and the points. The answer is
           {"draft": False} (keep the points) or
           {"draft": True, "use": [1, 3], "notes": "..."} (1-based point numbers, default all).
- draft:   the "draft_answer" prompt turns the chosen points into prose.
- humanize: the "humanize" prompt removes the usual signs of AI writing.
- check:   tools.check(...): invented numbers or names, the word limit, stock
           AI phrasing. Problems go back to humanize as feedback.

Both writing chains return plain text (StrOutputParser) and may use different
models (the `tailor` and `humanize` steps). One checkpoint thread per question.
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
    """One checkpoint thread per question."""
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
        # Deterministic: invented numbers or names, the word limit, AI phrasing.
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
    """A paused or finished state -> Outcome."""
    if interrupts:  # paused at choose: the payload is what choose passed to interrupt()
        return Outcome("waiting", interrupts[0].value)
    if values["outcome"] == "points":
        return Outcome("points", {"points": values["points"]})
    keys = ("points", "chosen", "text", "problems", "attempts")
    return Outcome(values["outcome"], {k: values.get(k) for k in keys})


def run_answer(graph: CompiledStateGraph, question_id: int, decision: dict[str, Any] | None = None) -> Outcome:
    """Start, inspect, or resume the thread for `question_id`.

    Status "waiting" (payload: the question and points), "points" (payload: the points),
    "drafted" or "needs_review" (payload: points, chosen, text, problems, attempts).
    """
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
