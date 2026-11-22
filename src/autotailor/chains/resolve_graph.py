"""M7 exercise: a graph that pauses to ask you something, and picks up where it left off.

A Runway email has no description and no employer link, so each role must be
found on the employer's own site. Usually AutoTailor can do that alone (the
company's job board has the same title). When it can't, only you can help:
open the Runway link in your browser and paste the employer's URL or the text.
That may happen now, in an hour, or after a restart, in the terminal or the UI.

    START -> match --(found)--> END
               |
            (not found)
               v
              ask  <------------------+      ask:   interrupt() with a question; the
               |                      |             graph stops and saves itself
               v                      |
             apply --(answer failed: error)      apply: use the answer
               |
          (resolved or skipped)
               v
              END

`resolver` (autotailor/resolve.py, given) does the real work:
- resolver.auto_match(posting_id) -> bool        True if it found the posting itself
- resolver.question(posting_id) -> dict          what to ask (company, title, Runway link, ...)
- resolver.apply_answer(posting_id, answer) -> str | None
      answer is {"url": ...}, {"text": ...} or {"skip": True}; returns None if
      it worked, else an error message to show the user next time you ask

You implement everything marked TODO. Check your work with:

    uv run pytest tests/test_m7_resolve_graph.py

Then for real: `autotailor mail import` (with a Runway .eml in data/inbox/),
`autotailor runway resolve`.

------------------------------------------------------------------------------
LangGraph concepts
------------------------------------------------------------------------------

1. interrupt().  Inside a node, `value = interrupt(payload)` stops the graph.
   The checkpointer saves where it stopped. `graph.invoke(...)` returns, and
   its result has an "__interrupt__" key holding the Interrupt objects; each
   one's `.value` is the payload you passed (here: the question).

       from langgraph.types import interrupt

       def ask(state):
           answer = interrupt({"question": "Which URL?"})
           return {"answer": answer}

   A checkpointer and a thread_id are required: the pause lives in the
   checkpoint, so it survives the process ending.

2. Resuming with Command.  Later (another process even), continue the same
   thread with the answer:

       from langgraph.types import Command
       graph.invoke(Command(resume={"url": "..."}), config)

   Inside `ask`, `interrupt(...)` now *returns* that answer.

3. The node re-runs from its start.  On resume LangGraph does not jump back
   into the middle of `ask`: it runs `ask` again from the top, and this time
   `interrupt()` returns the answer instead of stopping. So anything in the
   node before interrupt() runs twice. Keep side effects (network, database)
   out of the node that interrupts; that's why `match` and `apply` are separate
   nodes from `ask`.

4. Looking at a paused thread.  `snapshot = graph.get_state(config)`:
   `snapshot.interrupts` is a tuple of pending Interrupt objects (empty if the
   thread isn't waiting); `snapshot.values` is the state so far (empty dict if
   the thread never ran).

------------------------------------------------------------------------------
Contract (the tests check these)
------------------------------------------------------------------------------

- build_resolve_graph(resolver, checkpointer) returns a compiled graph with
  nodes "match", "ask" and "apply". Input state: {"posting_id": str}.
- resolver.auto_match runs once per thread (not again on resume).
- When asking after a failed answer, the question includes "error": the
  message apply_answer returned.
- run_resolve(graph, posting_id, answer=None) -> Outcome:
    - no answer, thread never run: run it. Outcome("resolved"|"skipped", None),
      or Outcome("waiting", question) if it paused;
    - no answer, thread paused: Outcome("waiting", the pending question),
      without running anything;
    - no answer, thread finished: its outcome, without running anything;
    - an answer: resume the paused thread with it (ValueError if the thread
      isn't waiting) and return the new Outcome.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph

    from autotailor.resolve import Resolver


class Outcome(NamedTuple):
    status: Literal["resolved", "skipped", "waiting"]
    question: dict[str, Any] | None  # set when waiting


def thread_id(posting_id: str) -> str:
    """One checkpoint thread per role. Given."""
    return f"resolve-{posting_id}"


# TODO(you): define the graph's state, e.g. class ResolveState(TypedDict): ...
# Think about what `ask` needs to know on a retry, and what tells you the result.
class ResolveState(TypedDict):
    posting_id: str
    answer: dict | None
    error: str | None
    outcome: str | None

def build_resolve_graph(resolver: Resolver, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph: 
    """TODO(you): match -> (END | ask -> apply -> (END | ask)), compiled with the checkpointer."""
    def match(state: ResolveState):
        if resolver.auto_match(state["posting_id"]):
            return {"outcome": "resolved"}
        return {}

    def ask(state: ResolveState):
        question = resolver.question(state["posting_id"])
        if state.get("error"):
            question = {**question, "error": state["error"]}
        answer = interrupt(question)
        return {"answer": answer}

    def apply(state: ResolveState):
        error = resolver.apply_answer(state["posting_id"], state["answer"])
        if error:
            return {"error": error, "answer": None}           # outcome stays unset, so the graph goes back to ask
        status = "skipped" if state["answer"].get("skip") else "resolved"
        return {"error": None, "outcome": status}

    def route(state: ResolveState):
        return "done" if state.get("outcome") else "ask"


    builder = StateGraph(ResolveState)

    builder.add_node("match", match)
    builder.add_node("ask", ask)
    builder.add_node("apply", apply)

    builder.add_edge(START, "match")
    builder.add_edge("ask", "apply")
    builder.add_conditional_edges("match", route, {"done": END, "ask": "ask"})
    builder.add_conditional_edges("apply", route, {"done": END, "ask": "ask"})

    return builder.compile(checkpointer=checkpointer)


def _outcome(values: dict[str, Any], interrupts: Any) -> Outcome:
    """Turn a final or paused state into an Outcome."""
    if interrupts:  # paused at ask: the pending question is the interrupt's payload
        return Outcome("waiting", interrupts[0].value)
    return Outcome(values["outcome"], None)


def run_resolve(graph: CompiledStateGraph, posting_id: str, answer: dict[str, Any] | None = None) -> Outcome:
    """Start, inspect, or resume the thread for `posting_id` (see the contract)."""
    config = {"configurable": {"thread_id": thread_id(posting_id)}}
    snapshot = graph.get_state(config)  # reads the checkpoint; runs nothing

    if answer is not None:
        # Resume: interrupt() inside `ask` returns `answer`, then the graph carries on.
        if not snapshot.interrupts:
            raise ValueError(f"{posting_id} is not waiting for an answer.")
        result = graph.invoke(Command(resume=answer), config)
    elif snapshot.values:
        return _outcome(snapshot.values, snapshot.interrupts)
    else:
        result = graph.invoke({"posting_id": posting_id}, config)

    return _outcome(result, result.get("__interrupt__"))

