"""Tests for the batch graph (chains/batch_graph.py): fan-out with Send, checkpoints, resume.

`work` is a fake here: no model is called.
"""

from __future__ import annotations

import threading

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autotailor.batch import Job, JobResult
from autotailor.chains.batch_graph import build_batch_graph, run_batch


def _jobs(n: int) -> list[Job]:
    return [Job(index=i, posting_id=f"p{i}", posting=f"posting {i}", profile="profile") for i in range(1, n + 1)]


class FakeWork:
    """Records calls; fails for the posting IDs in `fail` (until cleared)."""

    def __init__(self, fail: set[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self.fail = set(fail)
        self.lock = threading.Lock()

    def __call__(self, job: Job) -> JobResult:
        with self.lock:
            self.calls.append(job["posting_id"])
        if job["posting_id"] in self.fail:
            raise RuntimeError(f"crash on {job['posting_id']}")
        return JobResult(
            index=job["index"], posting_id=job["posting_id"], status="tailored",
            resume={"bullets": [], "skills": []}, errors=[], attempts=1, message=None,
        )  # fmt: skip


def test_one_task_per_job_results_in_order():
    work = FakeWork()
    graph = build_batch_graph(work, InMemorySaver())
    assert "tailor_job" in graph.get_graph().nodes
    results = run_batch(graph, _jobs(5), "t1", max_concurrency=2)
    assert sorted(work.calls) == ["p1", "p2", "p3", "p4", "p5"]  # each job once
    assert [r["index"] for r in results] == [1, 2, 3, 4, 5]


def test_jobs_run_in_parallel_up_to_the_limit():
    """Two jobs must be running at the same time (a plain loop inside one node can't
    pass this), and never more than max_concurrency."""
    barrier = threading.Barrier(2, timeout=10)
    active = 0
    peak = 0
    lock = threading.Lock()
    inner = FakeWork()

    def work(job: Job) -> JobResult:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            if job["index"] <= 2:
                barrier.wait()  # raises BrokenBarrierError if job 2 never starts alongside job 1
            return inner(job)
        finally:
            with lock:
                active -= 1

    results = run_batch(build_batch_graph(work, InMemorySaver()), _jobs(6), "t2", max_concurrency=2)
    assert len(results) == 6
    assert peak == 2


def test_resume_reruns_only_unfinished_jobs():
    work = FakeWork(fail={"p3"})
    graph = build_batch_graph(work, InMemorySaver())
    with pytest.raises(RuntimeError, match="crash on p3"):
        run_batch(graph, _jobs(4), "t3", max_concurrency=2)
    first = list(work.calls)

    work.fail.clear()
    work.calls.clear()
    results = run_batch(graph, None, "t3", max_concurrency=2)  # resume: no jobs passed
    assert work.calls == ["p3"], f"resume should only redo p3 (first pass ran {first})"
    assert [r["posting_id"] for r in results] == ["p1", "p2", "p3", "p4"]


def test_finished_thread_returns_saved_results():
    work = FakeWork()
    graph = build_batch_graph(work, InMemorySaver())
    first = run_batch(graph, _jobs(3), "t4")
    work.calls.clear()
    again = run_batch(graph, _jobs(3), "t4")  # same thread: nothing runs, nothing is duplicated
    assert work.calls == []
    assert again == first


def test_threads_are_separate_runs():
    work = FakeWork()
    graph = build_batch_graph(work, InMemorySaver())
    run_batch(graph, _jobs(2), "run-a")
    results = run_batch(graph, _jobs(3), "run-b")
    assert len(results) == 3 and len(work.calls) == 5


def test_resume_without_saved_state_is_an_error():
    graph = build_batch_graph(FakeWork(), InMemorySaver())
    with pytest.raises(ValueError):
        run_batch(graph, None, "never-started")
