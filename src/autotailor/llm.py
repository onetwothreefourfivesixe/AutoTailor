"""Model factory: the one place a chat model is constructed.

Every chain and graph node asks for its model by step name:

    from autotailor.llm import get_chat_model
    llm = get_chat_model(settings, "extract")

Which provider and model that returns is decided entirely by `config.toml`, so
swapping a cloud model for a local one never touches chain code.

LangChain concept: `init_chat_model` returns a `BaseChatModel`. Every provider's
chat model implements that same interface (`.invoke`, `.stream`,
`.with_structured_output`, ...), which is what makes them interchangeable.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autotailor.config import EmbeddingSpec, ModelSpec, Settings, Step
from autotailor.paths import get_paths

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


# Environment variable holding the API key for each cloud provider.
# Ollama is local and needs none.
API_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}


ENV_TEMPLATE = """\
# API keys for AutoTailor. This file is git-ignored; never commit it.
# Fill in only the providers you use, without quotes. Ollama needs no key.
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
GOOGLE_API_KEY=
"""


def load_api_keys(env_file: Path | None = None) -> None:
    """Load API keys from the .env file into the environment.

    Variables already set in the real environment win, so a key exported in the
    shell overrides the file.
    """
    from dotenv import dotenv_values

    for name, value in dotenv_values(env_file or get_paths().env).items():
        # Skip blank template lines, and never overwrite a key already set.
        if value and not os.environ.get(name):
            os.environ[name] = value


def model_kwargs(spec: ModelSpec, settings: Settings) -> dict[str, Any]:
    """Provider-specific constructor arguments for `init_chat_model`.

    Kept separate from construction so it can be tested without credentials.
    Each provider names its output-length limit differently. Sampling
    parameters (temperature, top_p) are deliberately not set: current Claude
    models reject them, and provider defaults are fine elsewhere.
    """
    if spec.provider in ("anthropic", "openai"):
        return {"max_tokens": spec.max_tokens}
    if spec.provider == "google_genai":
        return {"max_output_tokens": spec.max_tokens}
    if spec.provider == "ollama":
        return {
            "base_url": settings.models.ollama_base_url,
            "num_predict": spec.max_tokens,
            "num_ctx": spec.context_window,
        }
    raise ValueError(f"Unknown provider: {spec.provider}")


def get_chat_model(settings: Settings, step: Step) -> BaseChatModel:
    """Build the chat model configured for a pipeline step."""
    from langchain.chat_models import init_chat_model  # imported lazily: slow import

    load_api_keys()
    spec = settings.models.for_step(step)
    return init_chat_model(
        spec.model, model_provider=spec.provider, **model_kwargs(spec, settings)
    )


def describe(spec: ModelSpec | EmbeddingSpec) -> str:
    where = "local" if spec.is_local else "cloud: sends data off this machine"
    return f"{spec.provider}:{spec.model} ({where})"


MODE_DESCRIPTIONS = {
    "local": "all local: no posting or CV text leaves this machine",
    "cloud": "cloud only: posting and CV text is sent to the providers below",
    "mixed": "mixed: steps marked 'cloud' send posting or CV text to that provider",
}
