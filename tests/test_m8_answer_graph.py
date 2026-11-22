"""M8 exercise tests: the answer graph (opt-in interrupt, draft and humanize chains, check loop).

Expected to fail (shown as `x`) until chains/answer_graph.py is written. They also
need your "draft_answer" prompt in structured_prompts.json. Models and tools are fakes.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from autotailor.chains.answer_graph import build_answer_graph, run_answer

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="M8 exercise not written yet")

POINTS = [
    {"source_id": "exp-lab-detector", "point": "Trained a YOLO detector in PyTorch", "why": "ML depth", "cv_text": "..."},
    {"source_id": "proj-ocr", "point": "Built an OCR pipeline", "why": "shipping", "cv_text": "..."},
    {"source_id": "proj-budget", "point": "Made a budgeting app", "why": "product sense", "cv_text": "..."},
]


class FakeTools:
    def __init__(self, problems: list[list[str]] | None = None) -> None:
        self.problems = list(problems or [])  # one list per check call; [] when clean
        self.calls: list[tuple] = []

    def question(self, qid):
        return {"question_id": qid, "question": "Why do you want to work here?", "word_limit": 150, "company": "Acme", "title": "ML Intern"}

    def suggest(self, qid):
        self.calls.append(("suggest", qid))
        return [dict(p) for p in POINTS]

    def draft_inputs(self, qid, chosen, notes=""):
        self.calls.append(("draft_inputs", [p["source_id"] for p in chosen], notes))
        return {"question": "Why do you want to work here?", "company": "Acme", "title": "ML Intern", "word_limit": 150,
                "points": "\n".join(f"- {p['point']}" for p in chosen), "notes": notes or "None."}  # fmt: skip

    def humanize_inputs(self, qid, text, problems):
        self.calls.append(("humanize_inputs", text, list(problems)))
        return {"text": text, "feedback": "\n".join(problems) or "None yet.", "word_limit": 150}

    def check(self, qid, text, chosen, notes=""):
        self.calls.append(("check", text))
        return self.problems.pop(0) if self.problems else []

    def count(self, name):
        return sum(1 for c in self.calls if c[0] == name)


class FakeLLM:
    """Replies in turn from `replies`, recording each prompt's text."""

    def __init__(self, replies: list[str]) -> None:
        self.replies, self.prompts = list(replies), []

    def __call__(self, prompt_value):
        self.prompts.append("\n".join(str(m.content) for m in prompt_value.to_messages()))
        return AIMessage(content=self.replies.pop(0) if self.replies else "final text")

    def runnable(self):
        return RunnableLambda(self)


def _build(tools, draft, human, saver=None, max_attempts=3):
    return build_answer_graph(tools, draft.runnable(), human.runnable(), saver or InMemorySaver(), max_attempts)


def test_nodes():
    g = _build(FakeTools(), FakeLLM([]), FakeLLM([]))
    assert {"suggest", "choose", "draft", "humanize", "check"} <= set(g.get_graph().nodes)


def test_pauses_with_points_and_stops_at_points_only():
    tools, draft, human = FakeTools(), FakeLLM([]), FakeLLM([])
    g = _build(tools, draft, human)
    out = run_answer(g, 1)
    assert out.status == "waiting"
    assert out.payload["question"] == "Why do you want to work here?" and len(out.payload["points"]) == 3

    out = run_answer(g, 1, {"draft": False})
    assert out.status == "points" and len(out.payload["points"]) == 3
    assert draft.prompts == [] and human.prompts == []  # opting out calls no writing model
    assert tools.count("suggest") == 1


def test_opt_in_drafts_humanizes_and_checks():
    tools, draft, human = FakeTools(), FakeLLM(["rough draft"]), FakeLLM(["clean answer"])
    g = _build(tools, draft, human)
    run_answer(g, 2)
    out = run_answer(g, 2, {"draft": True, "use": [1, 3], "notes": "mention the robotics club"})
    assert out.status == "drafted"
    assert out.payload["text"] == "clean answer" and out.payload["problems"] == []
    assert [p["source_id"] for p in out.payload["chosen"]] == ["exp-lab-detector", "proj-budget"]
    assert ("draft_inputs", ["exp-lab-detector", "proj-budget"], "mention the robotics club") in tools.calls
    assert "Made a budgeting app" in draft.prompts[0] and "OCR" not in draft.prompts[0]  # only the chosen points
    assert "Why do you want to work here?" in draft.prompts[0] and "robotics club" in draft.prompts[0]
    assert "rough draft" in human.prompts[0]  # humanize edits the draft
    assert tools.count("suggest") == 1, "suggest must not run again on resume"


def test_check_problems_go_back_to_humanize():
    tools = FakeTools(problems=[["Remove the numbers ['40']"], []])
    draft, human = FakeLLM(["rough"]), FakeLLM(["first edit with 40%", "second edit"])
    g = _build(tools, draft, human)
    run_answer(g, 3)
    out = run_answer(g, 3, {"draft": True})
    assert out.status == "drafted" and out.payload["text"] == "second edit" and out.payload["attempts"] == 2
    assert "first edit with 40%" in human.prompts[1]  # the second pass edits the latest text
    assert "Remove the numbers ['40']" in human.prompts[1]  # ...with the problems as feedback
    assert len(draft.prompts) == 1  # no re-draft


def test_gives_up_after_max_attempts():
    tools = FakeTools(problems=[["too long"]] * 5)
    g = _build(tools, FakeLLM(["rough"]), FakeLLM([]), max_attempts=2)
    run_answer(g, 4)
    out = run_answer(g, 4, {"draft": True})
    assert out.status == "needs_review" and out.payload["problems"] == ["too long"] and out.payload["attempts"] == 2
    assert tools.count("check") == 2


def test_inspecting_and_errors():
    saver = InMemorySaver()
    tools = FakeTools()
    run_answer(_build(tools, FakeLLM([]), FakeLLM([]), saver), 5)
    tools2 = FakeTools()
    g2 = _build(tools2, FakeLLM(["d"]), FakeLLM(["h"]), saver)  # a "restart": same checkpoints
    assert run_answer(g2, 5).status == "waiting" and tools2.calls == []
    assert run_answer(g2, 5, {"draft": True}).status == "drafted"
    assert run_answer(g2, 5).status == "drafted"  # finished: reported, not re-run
    with pytest.raises(ValueError):
        run_answer(g2, 99, {"draft": True})
