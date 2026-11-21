"""M4 exercise: your first LangGraph graph. Draft a tailored resume, verify it, retry.

    START -> draft -> verify --(passed, or out of attempts)--> END
               ^         |
               +---------+  (verifier found problems: try again with its feedback)

`draft` asks the model for a TailoredResume (autotailor/models.py): which CV
bullets to use, lightly reworded toward the posting, plus the most relevant
skills. `verify` runs `verify_resume` (given, autotailor/resume/verify.py),
which rejects anything the CV doesn't say: unknown bullet IDs, new numbers,
added tools or names, bloated bullets, too few or too many bullets. If it finds
problems, the graph loops back to `draft` and the model sees the problems in
its prompt. AutoTailor only renders a resume that passes.

You implement everything marked TODO. Check your work with:

    uv run pytest tests/test_m4_tailor_graph.py
    AUTOTAILOR_LIVE=1 uv run pytest tests/test_m4_tailor_graph.py -k live -s

------------------------------------------------------------------------------
LangGraph concepts
------------------------------------------------------------------------------

LangChain chains (M1-M3) run in a straight line. A LangGraph graph can branch
and loop, and carries a shared *state* between steps.

1. State.  A TypedDict listing every key the graph tracks:

       class MyState(TypedDict):
           posting: str
           attempts: int

   Each node receives the whole state and returns a dict with only the keys it
   changes; LangGraph merges that update in. A node never edits the state in
   place.

2. Reducers (optional).  By default a returned key replaces the old value.
   Annotating a list key with a reducer makes updates append instead:

       history: Annotated[list[str], operator.add]   # node returns {"history": ["x"]}

   Handy for keeping every attempt's errors, not just the latest.

3. Nodes.  Plain functions `state -> dict`. Register them by name:

       builder = StateGraph(MyState)
       builder.add_node("draft", draft_fn)

   A node that needs extra things (the model, the CV) can be a closure defined
   inside `build_graph`, or a `functools.partial`.

4. Edges.  Fixed:        builder.add_edge(START, "draft")
                         builder.add_edge("draft", "verify")
   Conditional: a router function looks at the state and returns a label;
   the mapping sends each label to a node (or END):

       builder.add_conditional_edges("verify", route, {"retry": "draft", "done": END})

5. Compile and run.  `graph = builder.compile()` gives a runnable, like a
   chain: `graph.invoke(initial_state)` returns the final state.
   `graph.get_graph().draw_mermaid()` prints a diagram of it.

6. Loop safety.  LangGraph aborts after 25 steps (`recursion_limit`) with
   GraphRecursionError. Your own attempts counter should end the loop long
   before that.

------------------------------------------------------------------------------
Contract (the tests check these)
------------------------------------------------------------------------------

- build_graph(llm, cv, limits) returns a compiled graph with nodes named
  "draft" and "verify".
- Input state: {"posting": str, "profile": str}. Anything else you need
  (attempts, feedback) starts from defaults you set in the draft node.
- Final state has "resume" (the last draft), "errors" (verify_resume's list for
  that draft, [] if it passed) and "attempts" (drafts made).
- The draft step calls llm.with_structured_output(TailoredResume). On a retry,
  the previous draft's error messages must appear in the prompt.
- At most limits.max_attempts drafts, then stop even if errors remain.

The prompt: add a "tailor" entry to structured_prompts.json with variables
{posting}, {profile} and {feedback}, and load it with
load_prompt("tailor", TailoredResume). On the first draft, feedback can be an
empty string or something like "None yet." The prompt should tell the model:
- pick the bullets most relevant to the posting (the profile already lists
  IDs in [brackets]; prefer the ones fit scoring marked as most relevant);
- rewording means swapping in the posting's vocabulary where the facts already
  match, never adding facts, numbers, tools or names;
- order bullets most relevant first, and stay within the bullet limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from langchain_protocol import TypedDict
from langgraph.graph import END, START, StateGraph

from autotailor.chains.prompts import load_prompt  # noqa: F401  (for your prompt)
from autotailor.config import Settings, TailorSettings
from autotailor.cv import MasterCV
from autotailor.llm import get_chat_model
from autotailor.models import TailoredResume
from autotailor.resume.verify import verify_resume  # noqa: F401  (for your verify node)

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph


# TODO(you): define the graph's state, e.g. class TailorState(TypedDict): ...
class TailorState(TypedDict):
  posting: str
  profile: str
  resume: TailoredResume | None
  errors: list[str]
  attempts: int

def build_graph(llm: BaseChatModel, cv: MasterCV, limits: TailorSettings) -> CompiledStateGraph:
    """TODO(you): build and compile the draft -> verify -> (retry | END) graph."""
    prompt = load_prompt("tailor", TailoredResume).partial(
        min_bullets=str(limits.min_bullets), max_bullets=str(limits.max_bullets)
    )
    draft_chain = prompt | llm.with_structured_output(TailoredResume)
    def draft(state: TailorState):
        errors = state.get("errors", [])
        feedback = "\n".join(errors) if errors else ""
        resume = draft_chain.invoke({"posting": state["posting"], "profile": state["profile"], "feedback": feedback})
        return {"resume": resume, "attempts": state.get("attempts", 0) + 1}
    
    def verify(state: TailorState):
        return {"errors": verify_resume(state["resume"], cv, limits)}

    def route(state: TailorState):
        if not state["errors"] or state["attempts"] >= limits.max_attempts:
            return "done"
        return "retry"

    graph: StateGraph = StateGraph(TailorState)

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


def tailor_posting(posting: str, profile: str, cv: MasterCV, settings: Settings) -> TailorResult:
    """Run the graph for one posting with the `tailor` step's model. Given."""
    graph = build_graph(get_chat_model(settings, "tailor"), cv, settings.tailor)
    final = graph.invoke({"posting": posting, "profile": profile})
    return TailorResult(final.get("resume"), list(final.get("errors", [])), final.get("attempts", 0))
