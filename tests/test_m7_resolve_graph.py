"""M7 exercise tests: the resolve graph (interrupt, resume with Command, inspecting a paused thread).

Expected to fail (shown as `x`) until chains/resolve_graph.py is written. The
resolver is a fake: no network, no database.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from autotailor.chains.resolve_graph import Outcome, build_resolve_graph, run_resolve

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="M7 exercise not written yet")


class FakeResolver:
    def __init__(self, auto: bool = False, good_url: str = "https://boards.greenhouse.io/acme/jobs/1") -> None:
        self.auto, self.good_url = auto, good_url
        self.calls: list[tuple] = []

    def auto_match(self, posting_id):
        self.calls.append(("auto_match", posting_id))
        return self.auto

    def question(self, posting_id):
        self.calls.append(("question", posting_id))
        return {"posting_id": posting_id, "company": "Acme", "title": "SWE Intern", "runway_url": "https://app.joinrunway.io/job/x"}

    def apply_answer(self, posting_id, answer):
        self.calls.append(("apply_answer", posting_id, dict(answer)))
        if answer.get("skip") or answer.get("url") == self.good_url or answer.get("text"):
            return None
        return "That's a joinrunway.io link, which AutoTailor doesn't open."

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)


def _graph(resolver, saver=None):
    return build_resolve_graph(resolver, saver or InMemorySaver())


def test_nodes():
    nodes = _graph(FakeResolver()).get_graph().nodes
    assert {"match", "ask", "apply"} <= set(nodes)


def test_auto_match_needs_no_question():
    r = FakeResolver(auto=True)
    out = run_resolve(_graph(r), "runway:a")
    assert out == Outcome("resolved", None)
    assert r.count("question") == 0 and r.count("apply_answer") == 0


def test_pauses_then_resumes_with_answer():
    r = FakeResolver()
    g = _graph(r)
    out = run_resolve(g, "runway:a")
    assert out.status == "waiting"
    assert out.question["company"] == "Acme" and "error" not in out.question

    out = run_resolve(g, "runway:a", {"url": r.good_url})
    assert out == Outcome("resolved", None)
    assert ("apply_answer", "runway:a", {"url": r.good_url}) in r.calls
    assert r.count("auto_match") == 1, "match must not run again when the thread resumes"


def test_failed_answer_asks_again_with_the_error():
    r = FakeResolver()
    g = _graph(r)
    run_resolve(g, "runway:a")
    out = run_resolve(g, "runway:a", {"url": "https://app.joinrunway.io/job/x"})
    assert out.status == "waiting"
    assert "joinrunway.io" in out.question["error"]
    out = run_resolve(g, "runway:a", {"text": "posting text " * 30})
    assert out == Outcome("resolved", None)
    assert r.count("apply_answer") == 2


def test_skip():
    r = FakeResolver()
    g = _graph(r)
    run_resolve(g, "runway:a")
    assert run_resolve(g, "runway:a", {"skip": True}) == Outcome("skipped", None)


def test_paused_thread_survives_a_restart_and_is_not_rerun():
    saver = InMemorySaver()
    r = FakeResolver()
    run_resolve(_graph(r, saver), "runway:a")

    r2 = FakeResolver()  # "a new process": new graph and resolver, same checkpoint store
    g2 = _graph(r2, saver)
    out = run_resolve(g2, "runway:a")
    assert out.status == "waiting" and out.question["company"] == "Acme"
    assert r2.calls == [], "inspecting a paused thread must not run any node"

    assert run_resolve(g2, "runway:a", {"url": r2.good_url}).status == "resolved"
    assert r2.count("auto_match") == 0
    assert run_resolve(g2, "runway:a") == Outcome("resolved", None)  # finished: nothing runs
    assert r2.count("apply_answer") == 1


def test_threads_are_per_posting_and_answers_need_a_waiting_thread():
    r = FakeResolver()
    g = _graph(r)
    run_resolve(g, "runway:a")
    assert run_resolve(g, "runway:b").status == "waiting"
    assert r.count("auto_match") == 2
    with pytest.raises(ValueError):
        run_resolve(g, "runway:never-started", {"url": r.good_url})
