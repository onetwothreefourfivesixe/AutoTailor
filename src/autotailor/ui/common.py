"""Shared bits for the UI pages."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator

import streamlit as st
from pydantic import ValidationError

from autotailor import db
from autotailor.config import EmbeddingSpec, ModelSpec, Settings, load_settings
from autotailor.cv import MasterCV, load_master_cv
from autotailor.paths import Paths, get_paths


def paths() -> Paths:
    return get_paths()


def load_settings_or_stop() -> Settings:
    try:
        return load_settings(paths().config)
    except ValidationError as e:
        st.error(f"config.toml is invalid, so the UI can't read it. Fix it by hand or restore a backup "
                 f"from `{paths().config_backups}`.\n\n```\n{e}\n```")  # fmt: skip
        st.stop()
        raise  # unreachable


def load_cv() -> tuple[MasterCV | None, str | None]:
    """The master CV, or None with the reason it couldn't be read."""
    try:
        return load_master_cv(paths().master_cv), None
    except FileNotFoundError:
        return None, f"No master CV at `{paths().master_cv}`. Run `autotailor init`."
    except (OSError, ValueError) as e:
        return None, str(e)


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = db.connect(paths().db)
    try:
        yield conn
    finally:
        conn.close()


def where(spec: ModelSpec | EmbeddingSpec) -> str:
    return "on this machine" if spec.is_local else f"sent to {PROVIDER_NAMES.get(spec.provider, spec.provider)}"


PROVIDER_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "google_genai": "Google", "ollama": "Ollama"}


def fmt_time(iso: str | None) -> str:
    from datetime import datetime

    if not iso:
        return ""
    return f"{datetime.fromisoformat(iso).astimezone():%Y-%m-%d %H:%M}"


def lines(text: str) -> list[str]:
    """Text-area value -> list, one item per non-empty line."""
    return [x.strip() for x in text.splitlines() if x.strip()]


def csv(text: str) -> list[str]:
    """'a, b, c' -> ['a', 'b', 'c']."""
    return [x.strip() for x in (text or "").split(",") if x.strip()]
