"""M5 exercise: fan out over the top X postings in parallel, with checkpoints.

    START --(one Send per job)--> tailor_job  x N, in parallel --> END

`tailor_job` runs `work(job)` for one job; `work` is given to you (it is
`batch.make_worker(...)`, which runs your M4 tailoring graph and turns any
failure into a "failed" JobResult). Your graph decides how jobs are spread
over parallel tasks, how their results come back together, and how a run
picks up where it stopped.

You implement everything marked TODO. Check your work with:

    uv run pytest tests/test_m5_batch_graph.py

Then try a real run: `autotailor tailor`, press Ctrl-C part way, and
`autotailor resume`.

------------------------------------------------------------------------------
LangGraph concepts
------------------------------------------------------------------------------

1. Send (fan-out).  In M4 a conditional edge returned a label. It can also
   return a list of `Send(node_name, payload)`: LangGraph then runs that node
   once per Send, in parallel, and each run receives its own payload instead
   of the graph state:

       from langgraph.types import Send

       def fan_out(state) -> list[Send]:
           return [Send("worker", item) for item in state["items"]]

       builder.add_conditional_edges(START, fan_out, ["worker"])

   The third argument lists the nodes `fan_out` may send to (it draws the
   diagram; there are no labels to map this time).

2. Reducers for parallel writes (fan-in).  All N tasks return their update in
   the same step. Without a reducer, N writes to one key is an error
   (InvalidUpdateError); with `Annotated[list[...], operator.add]`, the lists
   are concatenated. Results arrive in whatever order the tasks finish.

3. Concurrency.  `graph.invoke(input, {"max_concurrency": 2, ...})` caps how
   many tasks run at once (they run on threads).

4. Checkpointer.  `builder.compile(checkpointer=saver)` saves the state after
   each step under a *thread*. If a step stops part way because a task raised
   or the user pressed Ctrl-C, the writes of the tasks that finished are
   saved too:

       config = {"configurable": {"thread_id": "run-7"}}
       graph.invoke(input, config)

   One thread = one conversation/run. Tests use `InMemorySaver`; the CLI uses
   `SqliteSaver` (data/checkpoints.db), so it survives a restart.

5. Resuming.  If a run stops part way, the tasks that finished already have
   their writes saved. `graph.invoke(None, config)` (input None, same thread)
   continues: only the tasks without saved writes run again.

   `graph.get_state(config)` returns a snapshot: `.values` is the state so
   far, `.next` the nodes still to run. Careful: after Ctrl-C, every task may
   have finished while the final checkpoint wasn't written yet, so `.next`
   can be empty for an unfinished run. Ask "does this thread have any saved
   state?" (`.values` non-empty), not "is `.next` non-empty?".

   Don't invoke a thread that has state with new input: that starts a new pass
   on the same thread, and the reducer adds the new results to the old ones.

------------------------------------------------------------------------------
Contract (the tests check these)
------------------------------------------------------------------------------

- build_batch_graph(work, checkpointer) returns a compiled graph with a node
  named "tailor_job", which calls `work(job)` once per job, in parallel.
- run_batch(graph, jobs, thread_id, max_concurrency):
    - a thread with no saved state: run the jobs;
    - a thread with saved state: resume it and ignore `jobs` (pass None when
      resuming). A finished thread just returns its results; nothing runs.
    - no saved state and jobs is None: raise ValueError.
    - returns one JobResult per job, sorted by job["index"].
- If a job raises, run_batch raises. Resuming that thread re-runs only the jobs
  that have no result yet.
"""

from __future__ import annotations

from collections.abc import Callable
import operator
from typing import TYPE_CHECKING, TypedDict, Annotated

from langgraph.graph import START, END, StateGraph
from langgraph.types import Send

from autotailor.batch import Job, JobResult

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.graph.state import CompiledStateGraph


# TODO(you): define the graph's state, e.g. class BatchState(TypedDict): ...
# (TypedDict and Annotated come from `typing`, at runtime; see the M4 lesson.)
class BatchState(TypedDict):
    jobs: list[Job]
    results: Annotated[list[JobResult], operator.add]


def build_batch_graph(
    work: Callable[[Job], JobResult], checkpointer: BaseCheckpointSaver | None = None
) -> CompiledStateGraph:
    """TODO(you): START -> Send per job -> "tailor_job" -> END, compiled with the checkpointer."""
    def tailor_job(job: Job) -> dict:
        return {"results": [work(job)]}
    def fan_out(state) -> list[Send]:
        return [Send("tailor_job", item) for item in state["jobs"]]

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
    """TODO(you): start the run on `thread_id`, or resume it if it has saved state.

    Return its results sorted by job index.
    """
    config = {"max_concurrency": max_concurrency, "configurable": {"thread_id": thread_id}}
    snapshot = graph.get_state(config)
    if snapshot.values:
        final = graph.invoke(None, config)
    elif jobs is None:
        raise ValueError( f"No saved run for {thread_id} to resume, and no jobs given.")
    else:
        final = graph.invoke({"jobs": jobs}, config)
    return sorted(final.get("results", []), key=lambda r: r["index"])
    