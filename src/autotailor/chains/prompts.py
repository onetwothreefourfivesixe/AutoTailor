"""Load prompts from structured_prompts.json into ChatPromptTemplates.

File format, per step name:

    "system":   list of lines (joined with newlines), plain text
    "human":    the final human message template; its {variables} are the prompt's inputs
    "examples": optional few-shot examples, each with
                  - one key per variable in "human" other than "text" (e.g. "today")
                  - "posting": list of lines, used as {text}
                  - "answer":  the ideal structured output, as real JSON

Each example becomes a human message (the "human" template filled in) plus an
AI message (the answer). Literal braces are escaped automatically, so nothing
in the file needs doubled braces. Answers are validated against the step's
schema when loaded, so a typo like "bs" for "bachelor" fails immediately.
"""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

PROMPTS_FILE = "structured_prompts.json"


def _escape(text: str) -> str:
    """Make text literal inside a prompt template."""
    return text.replace("{", "{{").replace("}", "}}")


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(f"{PROMPTS_FILE}: duplicate key(s) {dupes} in one object")
    return dict(pairs)


@cache
def _load_file() -> dict[str, Any]:
    raw = (resources.files("autotailor.chains") / PROMPTS_FILE).read_text(encoding="utf-8")
    return json.loads(raw, object_pairs_hook=_no_duplicate_keys)


def _text(value: str | list[str]) -> str:
    return "\n".join(value) if isinstance(value, list) else value


def load_prompt(step: str, schema: type[BaseModel] | None = None) -> ChatPromptTemplate:
    """Build the ChatPromptTemplate for `step` from structured_prompts.json.

    If `schema` is given, every example answer must validate against it.
    """
    spec = _load_file()[step]
    human = spec["human"]
    messages: list[tuple[str, str]] = [("system", _escape(_text(spec["system"])))]

    for i, example in enumerate(spec.get("examples", [])):
        answer = example["answer"]
        if schema is not None:
            try:
                schema.model_validate(answer)
            except Exception as e:
                raise ValueError(f"{PROMPTS_FILE}: {step} example {i + 1} answer is invalid:\n{e}") from e
        values = {k: _escape(str(v)) for k, v in example.items() if k not in ("posting", "answer")}
        values["text"] = _escape(_text(example["posting"]))
        messages.append(("human", human.format(**values)))
        messages.append(("ai", _escape(json.dumps(answer, indent=2, ensure_ascii=False))))

    messages.append(("human", human))
    return ChatPromptTemplate.from_messages(messages)
