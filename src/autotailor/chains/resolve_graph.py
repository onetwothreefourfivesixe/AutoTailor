"""Match a Runway role to the employer's posting, and ask the user when that fails.

    START -> match --(found)--> END
               |
            (not found)
               v
              ask  <------------------+      ask: interrupt() with the question; the
               |                      |           graph pauses and is saved
               v                      |
             apply --(answer failed: error)
               |
          (resolved or skipped)
               v
              END

`match` tries the employer's job board (Resolver.auto_match). `ask` pauses the
graph with interrupt(), passing the role's details; the answer ({"url": ...},
{"text": ...} or {"skip": True}) arrives later through Command(resume=...),
possibly from another process (the CLI, the UI, the API), and `apply` uses it.
A failed answer loops back to `ask` with the error. Side effects stay out of
`ask`, because a node that interrupts runs again from the top on resume.
One checkpoint thread per role.
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
    """One checkpoint thread per role."""
    return f"resolve-{posting_id}"


class ResolveState(TypedDict, total=False):
    posting_id: str  # input
    answer: dict | None  # what interrupt() returned: {"url"}, {"text"} or {"skip"}
    error: str | None  # why the last answer failed, shown with the next question
    outcome: str | None  # "resolved" or "skipped"; unset while there's more to do


def build_resolve_graph(resolver: Resolver, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """match -> (END | ask -> apply -> (END | ask)), compiled with the checkpointer."""

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
            return {"error": error, "answer": None}  # outcome stays unset: back to ask
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
    """Start, inspect, or resume the thread for `posting_id`.

    No answer and a new thread: run it. No answer and a started thread: report where it
    stands (the pending question, or the outcome) without running anything. An answer:
    resume the waiting thread with it (ValueError if it isn't waiting).
    """
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

