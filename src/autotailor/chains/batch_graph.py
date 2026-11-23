"""Tailor many postings in parallel, and resume a run that stopped part way.

    START --(one Send per job)--> tailor_job x N, in parallel --> END

Each job's JobResult is appended to `results` (a list reducer, so parallel
writes combine). The graph is compiled with a checkpointer and each run uses
its own thread, so a run stopped by Ctrl-C or an error resumes without redoing
finished jobs. `run_batch` resumes when the thread has saved values (after
Ctrl-C, `next` can be empty even though the run isn't finished).
"""

from __future__ import annotations

import operator
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from autotailor.batch import Job, JobResult

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph


class BatchState(TypedDict):
    jobs: list[Job]
    results: Annotated[list[JobResult], operator.add]


def build_batch_graph(
    work: Callable[[Job], JobResult], checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """START -> one Send per job -> "tailor_job" -> END, compiled with the checkpointer."""
    def tailor_job(job: Job) -> dict:
        return {"results": [work(job)]}

    def fan_out(state: BatchState) -> list[Send]:
        return [Send("tailor_job", job) for job in state["jobs"]]

    builder = StateGraph(BatchState)
    builder.add_node("tailor_job", tailor_job)
    builder.add_conditional_edges(START, fan_out, ["tailor_job"])
    builder.add_edge("tailor_job", END)

    return builder.compile(checkpointer=checkpointer)


def run_batch(
    graph: CompiledStateGraph,
    jobs: list[Job] | None,
    thread_id: str,
    max_concurrency: int = 2,
) -> list[JobResult]:
    """Start the run on `thread_id`, or resume it if it has saved state.

    Returns its results sorted by job index.
    """
    config = {"max_concurrency": max_concurrency, "configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if snapshot.values:
        final = graph.invoke(None, config)
    elif jobs is None:
        raise ValueError(f"No saved run for {thread_id} to resume, and no jobs given.")
    else:
        final = graph.invoke({"jobs": jobs}, config)
    return sorted(final.get("results", []), key=lambda r: r["index"])
    