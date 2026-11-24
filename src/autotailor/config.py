"""User settings: one TOML file, read and written by both the CLI and the UI.

Nothing user-specific is hard-coded anywhere else. If a value describes the
user, a run, or a model choice, it belongs in `Settings`.
"""

from __future__ import annotations

import json
import tomllib
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_validator


class _Model(BaseModel):
    # Reject unknown keys so a typo in config.toml is an error, not a silent no-op.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- Profile ---------------------------------------------------------------


class DegreeLevel(StrEnum):
    ASSOCIATE = "associate"
    BACHELOR = "bachelor"
    MASTER = "master"
    MBA = "mba"
    PHD = "phd"
    OTHER = "other"


class Education(_Model):
    level: DegreeLevel
    field: str = Field(description="Major or field of study.")
    status: Literal["completed", "in_progress"]
    graduation_date: date | None = Field(
        default=None, description="Actual or expected graduation date."
    )
    school: str | None = None


class Profile(_Model):
    name: str = ""
    education: list[Education] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    open_to_remote: bool = True
    work_authorization: str = ""
    role_interests: list[str] = Field(default_factory=list)


# --- Eligibility rules -----------------------------------------------------


class EligibilityRules(_Model):
    higher_degree_satisfies_lower: bool = Field(
        default=True,
        description='A posting asking for a Bachelor\'s admits a Master\'s student, '
        'unless the posting says "undergraduates only".',
    )
    in_progress_counts: bool = Field(
        default=True,
        description="A degree in progress counts toward a posting's degree requirement.",
    )
    graduation_window_slack_months: int = Field(
        default=0,
        ge=0,
        description="Months of tolerance on either side of a posting's graduation window.",
    )
    keep_unclear: bool = Field(
        default=True,
        description="Keep postings whose requirements are ambiguous (flagged), rather than dropping them.",
    )


# --- Runs ------------------------------------------------------------------


class RunSettings(_Model):
    jobs_per_run: int = Field(default=10, ge=1, description="Resumes tailored per run (top X).")
    max_runs_per_day: int = Field(default=3, ge=1, description="Tailoring runs allowed per calendar day.")
    max_runs_per_week: int = Field(default=10, ge=1, description="Tailoring runs allowed in any 7 days.")
    min_fit_score: float = Field(default=0.6, ge=0.0, le=1.0, description="Only postings at or above this fit score are tailored.")
    cut_low_fit: bool = Field(
        default=True,
        description="Cut postings whose fit score is below the cut-off. Postings not yet fit-scored are never cut, "
        "nor ones you tailored, applied to, overrode or added questions for.",
    )
    cut_action: Literal["delete", "hide"] = Field(
        default="delete",
        description="'delete': remove cut postings from the database (a small record keeps them from being fetched "
        "again; the jobs list's Cut view lists them); 'hide': keep them stored but out of the jobs list and Today.",
    )
    cut_below: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="The cut-off; empty uses min_fit_score. Set it lower to keep near misses in view without tailoring them.",
    )  # fmt: skip
    shortlist_size: int = Field(
        default=25,
        ge=1,
        description="How many postings (best embedding similarity first) get a model fit score per run.",
    )
    parallel_jobs: int = Field(
        default=2,
        ge=1,
        description="Resumes tailored at the same time. Ollama queues requests beyond its own "
        "OLLAMA_NUM_PARALLEL, so more than 2 rarely helps with a local model.",
    )


class TailorSettings(_Model):
    min_bullets: int = Field(default=6, ge=1, description="Fewest bullets on a tailored resume.")
    max_bullets: int = Field(
        default=14, ge=1, description="Most bullets the model picks; filling the page may add more."
    )
    fill_page: bool = Field(
        default=True,
        description="After tailoring, add the next most relevant CV bullets (verbatim, spread across "
        "sections) until the page is full, or trim a resume that runs over one page.",
    )
    rewording: Literal["light", "off"] = Field(
        default="light",
        description="'light': bullets keep your wording, with the posting's exact term swapped in where a bullet "
        "already says the same thing; 'off': bullets are copied word for word.",
    )
    max_attempts: int = Field(default=3, ge=1, description="Drafts before giving up on a posting.")
    length_growth: float = Field(
        default=1.3, ge=1.0, description="A rephrased bullet may be at most this many times its source's length."
    )


class SourceSettings(_Model):
    ats_boards: bool = True
    paste: bool = True
    runway_email: bool = True
    # Keep a board posting only if its title contains one of these terms (whole
    # words, case-insensitive, plural allowed). Empty keeps every title.
    title_include: list[str] = Field(default_factory=list)
    # Drop a board posting whose title contains any of these terms.
    title_exclude: list[str] = Field(default_factory=list)
    max_age_days: int = Field(
        default=21, ge=0,
        description="Skip board postings first published more than this many days ago, and leave stored "
        "postings that have aged past it out of ranking and tailoring. 0 turns the cutoff off.",
    )  # fmt: skip
    keep_undated: bool = Field(
        default=True, description="Keep postings that don't say when they were published (the cutoff can't apply)."
    )
    delete_expired: bool = Field(
        default=True,
        description="Delete stored postings older than max_age_days (by publish date, or by when they were first "
        "pulled if undated) at every fetch and rank, keeping ones you tailored, applied to or added questions for.",
    )
    # company slug lists per ATS, e.g. greenhouse = ["figma", "scaleai"]
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)


class MailSettings(_Model):
    drop_folder: bool = True  # D: default, no credentials
    imap: bool = False  # B: Gmail label pull (M7)
    imap_host: str = "imap.gmail.com"
    imap_label: str = "Runway"
    imap_user: str = ""  # password: OS keyring, or AUTOTAILOR_IMAP_PASSWORD in .env; never here
    imap_days: int = Field(default=14, ge=1, description="How far back each IMAP pull looks.")
    smtp_listener: bool = False  # A: optional local listener (M7)
    smtp_port: int = 8025
    allowed_senders: list[str] = Field(default_factory=lambda: ["hello@tower.joinrunway.io"])


# --- Models ----------------------------------------------------------------

Provider = Literal["anthropic", "openai", "google_genai", "ollama"]
Step = Literal["extract", "score", "tailor", "humanize"]
STEPS: tuple[Step, ...] = ("extract", "score", "tailor", "humanize")


class ModelSpec(_Model):
    provider: Provider = "anthropic"
    model: str = "claude-opus-5-5"
    max_tokens: int = Field(default=16000, ge=256)
    context_window: int = Field(
        default=16384,
        ge=2048,
        description="Ollama only: tokens the model reads at once (prompt plus answer). Ollama's own "
        "default is small and silently cuts longer prompts. Larger values use more memory.",
    )

    @property
    def is_local(self) -> bool:
        """True when this model runs on the user's machine (no data leaves it)."""
        return self.provider == "ollama"


EmbeddingProvider = Literal["ollama", "openai", "google_genai"]

# nomic-embed-text models expect these task prefixes; other models don't use any.
_NOMIC_PREFIXES = ("search_query: ", "search_document: ")


class EmbeddingSpec(_Model):
    """Embedding model used to pre-rank postings: local (Ollama) or cloud (OpenAI, Gemini).

    Anthropic has no embeddings API, so a Claude-only setup pairs with one of these.
    """

    provider: EmbeddingProvider = "ollama"
    model: str = "nomic-embed-text"
    # Leave unset to pick automatically (nomic models get their task prefixes,
    # others none). Set to "" to force no prefix.
    query_prefix: str | None = None
    document_prefix: str | None = None
    max_chars: int = Field(default=6000, ge=200, description="Posting text beyond this is cut.")

    @property
    def is_local(self) -> bool:
        return self.provider == "ollama"

    @property
    def prefixes(self) -> tuple[str, str]:
        """(query prefix, document prefix) actually used."""
        auto = _NOMIC_PREFIXES if "nomic" in self.model.lower() else ("", "")
        return (
            auto[0] if self.query_prefix is None else self.query_prefix,
            auto[1] if self.document_prefix is None else self.document_prefix,
        )

    @property
    def cache_key(self) -> str:
        """Identifies vectors in the embedding cache; vectors from different models never mix."""
        return f"{self.provider}:{self.model}"


class ModelSettings(_Model):
    default: ModelSpec = Field(default_factory=ModelSpec)
    embeddings: EmbeddingSpec = Field(default_factory=EmbeddingSpec)
    # Per-step overrides; a step not listed here uses `default`.
    steps: dict[Step, ModelSpec] = Field(default_factory=dict)
    ollama_base_url: str = "http://127.0.0.1:11434"

    @field_validator("ollama_base_url")
    @classmethod
    def _ipv4_loopback(cls, url: str) -> str:
        """`localhost` -> 127.0.0.1. Ollama listens on IPv4 only, and on Windows `localhost`
        tries IPv6 (::1) first and waits about 2 s for that to fail on every request."""
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        if parts.hostname != "localhost":
            return url
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        return urlunsplit(parts._replace(netloc=netloc))

    def for_step(self, step: Step) -> ModelSpec:
        return self.steps.get(step, self.default)

    def mode(self) -> Literal["local", "cloud", "mixed"]:
        """Whether every model (chat steps and embeddings) runs locally, in the cloud, or both."""
        local = [self.for_step(step).is_local for step in STEPS] + [self.embeddings.is_local]
        if all(local):
            return "local"
        if not any(local):
            return "cloud"
        return "mixed"


# --- Root ------------------------------------------------------------------


class ResumeSettings(_Model):
    template: str = Field(
        default="jake",
        description="'jake' (Jake's Resume, LaTeX), 'jake-compact' (denser: 10pt, tech stack after titles, one-line leadership), 'typst' (the original AutoTailor look), or the file name "
        "of your own .tex template in data/templates/.",
    )
    word_template: str = Field(
        default="", description="File name of your own .docx template in data/templates/; empty for the built-in Word layout."
    )
    entry_order: Literal["relevance", "cv"] = Field(
        default="relevance",
        description="Order jobs and projects within a section by relevance to the posting, or keep your CV's order.",
    )
    skills: Literal["all", "relevant"] = Field(
        default="all",
        description="'all': every CV skill, the posting's first in each group; 'relevant': only the skills the "
        "tailoring model picked.",
    )


class LogSettings(_Model):
    enabled: bool = Field(default=True, description="Record every model call in the local AI log.")
    keep_prompts: bool = Field(default=True, description="Store prompts and responses, not just timing and status.")
    retention_days: int = Field(default=15, ge=1, description="Delete logged calls older than this.")


class Settings(_Model):
    profile: Profile = Field(default_factory=Profile)
    eligibility: EligibilityRules = Field(default_factory=EligibilityRules)
    run: RunSettings = Field(default_factory=RunSettings)
    tailor: TailorSettings = Field(default_factory=TailorSettings)
    sources: SourceSettings = Field(default_factory=SourceSettings)
    mail: MailSettings = Field(default_factory=MailSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    log: LogSettings = Field(default_factory=LogSettings)
    resume: ResumeSettings = Field(default_factory=ResumeSettings)


def load_settings(path: Path) -> Settings:
    """Load settings; a missing file yields the defaults."""
    if not path.exists():
        return Settings()
    with path.open("rb") as f:
        return Settings.model_validate(tomllib.load(f))


def save_settings(settings: Settings, path: Path) -> None:
    # TOML has no null, so unset optional values are simply left out.
    data = settings.model_dump(mode="json", exclude_none=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data), encoding="utf-8")


def set_value(settings: Settings, dotted_key: str, raw: str) -> Settings:
    """Return a copy of `settings` with one value changed, fully re-validated.

    `dotted_key` is a path such as "run.jobs_per_run". `raw` is parsed as JSON
    when possible ("12", "true", '["NYC"]'), otherwise taken as a plain string.
    """
    try:
        value: Any = json.loads(raw)
    except json.JSONDecodeError:
        value = raw

    data = settings.model_dump(mode="json")
    node = data
    *parents, leaf = dotted_key.split(".")
    for part in parents:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"No such setting: {dotted_key}")
        node = node[part]
    if not isinstance(node, dict):
        raise KeyError(f"No such setting: {dotted_key}")
    node[leaf] = value
    return Settings.model_validate(data)  # raises on unknown keys or bad values
