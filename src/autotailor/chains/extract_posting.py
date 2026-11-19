"""M1 exercise: your first LangChain chain.

Job: read a pasted job posting (any site, any format) and pull out the basic
facts AutoTailor needs to store it. `autotailor add` calls this whenever a
posting did not come from a job board API.

    pasted text  ->  prompt  ->  chat model  ->  PostingFields

You implement two functions below: `build_prompt` and `build_chain`. The rest
is done. Check your work with:

    uv run pytest tests/test_m1_extract_chain.py

Those tests are marked "expected to fail" while the functions raise
NotImplementedError, and turn into normal passing tests once you implement them.
To try a real model (whatever `config.toml` sets for the `extract` step):

    AUTOTAILOR_LIVE=1 uv run pytest tests/test_m1_extract_chain.py -k live -s

------------------------------------------------------------------------------
Concepts (LangChain 1.x; `langchain-core` holds these building blocks)
------------------------------------------------------------------------------

1. Chat model.  `get_chat_model(settings, "extract")` returns a
   `BaseChatModel`: Claude, OpenAI, Gemini or Ollama, all with the same
   methods. You receive one as the `llm` argument; never construct one here.

2. Prompt template.  A `ChatPromptTemplate` is a list of messages with
   `{placeholders}`, turned into real messages when it is invoked:

       from langchain_core.prompts import ChatPromptTemplate
       prompt = ChatPromptTemplate.from_messages([
           ("system", "You are ..."),
           ("human", "Here is the thing: {thing}"),
       ])
       prompt.invoke({"thing": "..."})   # -> a ChatPromptValue (the messages)

   Put stable instructions in the system message and the variable input in
   the human message. Literal curly braces in your text must be doubled: {{ }}.

3. Structured output.  `llm.with_structured_output(SomePydanticModel)` returns
   a new runnable that makes the model answer in that schema and gives you a
   validated instance of the model instead of free text. The `description=` on
   each Field is sent to the model, so the schema itself is part of the prompt.

4. LCEL (LangChain Expression Language).  Anything "runnable" (prompts, models,
   parsers, plain functions wrapped in RunnableLambda) can be chained with `|`:

       chain = prompt | something
       chain.invoke({"thing": "..."})

   The output of each step becomes the input of the next. A chain is itself a
   runnable, so it also gets `.batch([...])`, `.stream(...)` and `.ainvoke(...)`
   for free.

Things to think about while writing the prompt:
- Postings pasted from a browser contain junk: navigation text, "Apply now",
  cookie banners, similar-jobs lists. Tell the model what to ignore.
- The company name is often only in the header, a logo alt text, or "About us".
- Missing facts should come back as null, not guessed. Say so explicitly.
- The pasted text is data from a web page, not instructions. Say that too, so
  a posting containing "ignore previous instructions" can't steer the model.

Reference: docs.langchain.com, Python section: "Models" and "Structured output".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from autotailor.chains.prompts import load_prompt
from autotailor.config import Settings
from autotailor.llm import get_chat_model

if TYPE_CHECKING:  # names used only in type hints; not available at runtime
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import Runnable


class PostingFields(BaseModel):
    """Basic facts about a job posting. Given; the tests and the CLI depend on this shape."""

    company: str = Field(description="The hiring company's name, as the posting states it.")
    title: str = Field(description="The job title, exactly as written in the posting.")
    location: str | None = Field(
        default=None,
        description="Where the job is based, e.g. 'New York, NY'. Null if not stated.",
    )
    workplace: Literal["onsite", "hybrid", "remote"] | None = Field(
        default=None, description="Work arrangement, if the posting states it. Null otherwise."
    )
    employment_type: Literal["internship", "full_time", "part_time", "contract", "other"] | None = (
        Field(default=None, description="Type of employment, if stated. Null otherwise.")
    )


def build_prompt() -> ChatPromptTemplate:
    """TODO(you): return a ChatPromptTemplate for extracting PostingFields.

    Requirements (the tests check these):
    - Exactly one input variable, named `text` (the pasted posting).
    - A system message with your instructions, then a human message containing {text}.
    """
    # The prompt text lives in structured_prompts.json (key "extract_posting").
    return load_prompt("extract_posting", PostingFields)


def build_chain(llm: BaseChatModel) -> Runnable:
    """TODO(you): compose the prompt and the model into one runnable.
w
    Invoking the result with {"text": "<posting>"} must return a PostingFields
    instance. Use build_prompt() and llm.with_structured_output(PostingFields).
    """
    return build_prompt() | llm.with_structured_output(PostingFields)


def extract_posting_fields(text: str, settings: Settings) -> PostingFields:
    """Run the chain with the model configured for the `extract` step. Given."""
    llm = get_chat_model(settings, "extract")
    return build_chain(llm).invoke({"text": text})
