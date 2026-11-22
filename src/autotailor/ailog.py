"""The AI call log (M10): every chat-model call, stored locally for `log.retention_days` days.

A LangChain callback handler, attached to every model `get_chat_model` builds,
records the step, model, where it ran, the item it was for, the prompt, the
response (or error), the time taken and token counts when the provider reports
them. Nothing here is sent anywhere: it's a table in data/autotailor.db.

Which item (posting or question) a call was for comes from, in order: the run's
metadata (`config={"metadata": {"item": ...}}`, used by the batch steps), else
the `item` context variable (set per job by the tailoring worker and others).
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from autotailor import db

current_item: contextvars.ContextVar[str | None] = contextvars.ContextVar("autotailor_item", default=None)
_purged: set[str] = set()


def _now() -> datetime:
    return datetime.now(UTC)


def purge(conn: sqlite3.Connection, retention_days: int) -> int:
    cutoff = (_now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    n = conn.execute("DELETE FROM llm_calls WHERE at < ?", (cutoff,)).rowcount
    conn.commit()
    return n


def _messages(messages: list[list[Any]]) -> list[dict[str, str]]:
    out = []
    for batch in messages:
        for m in batch:
            content = m.content if isinstance(m.content, str) else json.dumps(m.content, default=str)
            out.append({"role": getattr(m, "type", "message"), "content": content})
    return out


def _response_text(response: Any) -> tuple[str, int | None, int | None]:
    texts, tin, tout = [], None, None
    for gens in response.generations or []:
        for g in gens:
            msg = getattr(g, "message", None)
            text = getattr(g, "text", "") or ""
            calls = getattr(msg, "tool_calls", None) if msg is not None else None
            if calls:  # structured output arrives as a tool call
                text = (text + "\n" if text else "") + json.dumps([c.get("args", c) for c in calls], default=str)
            texts.append(text)
            usage = getattr(msg, "usage_metadata", None) if msg is not None else None
            if usage:
                tin = (tin or 0) + (usage.get("input_tokens") or 0)
                tout = (tout or 0) + (usage.get("output_tokens") or 0)
    return "\n".join(texts), tin, tout


class CallLogger(BaseCallbackHandler):
    """Writes one llm_calls row per model call. Safe across threads (own connection per write)."""

    def __init__(self, db_path: Path, step: str, provider: str, model: str, local: bool, keep_text: bool = True) -> None:
        self.db_path, self.step, self.provider, self.model, self.local = db_path, step, provider, model, local
        self.keep_text = keep_text
        self._open: dict[UUID, tuple[float, list[dict[str, str]], str | None]] = {}
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, metadata=None, **kwargs):  # noqa: ANN001, ANN003, ANN201
        item = (metadata or {}).get("item") or current_item.get()
        with self._lock:
            self._open[run_id] = (time.perf_counter(), _messages(messages), item)

    def on_llm_end(self, response, *, run_id, **kwargs):  # noqa: ANN001, ANN003, ANN201
        text, tin, tout = _response_text(response)
        self._write(run_id, text, None, tin, tout)

    def on_llm_error(self, error, *, run_id, **kwargs):  # noqa: ANN001, ANN003, ANN201
        self._write(run_id, None, f"{type(error).__name__}: {error}", None, None)

    def _write(self, run_id: UUID, response: str | None, error: str | None, tin: int | None, tout: int | None) -> None:
        with self._lock:
            started, messages, item = self._open.pop(run_id, (time.perf_counter(), [], None))
        task = os.environ.get("AUTOTAILOR_TASK_ID")
        try:
            conn = db.connect(self.db_path)
            conn.execute(
                "INSERT INTO llm_calls (at, step, provider, model, local, item, task_id, messages, response, error, "
                "ms, tokens_in, tokens_out) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_now().isoformat(timespec="seconds"), self.step, self.provider, self.model, int(self.local), item,
                 int(task) if task and task.isdigit() else None,
                 json.dumps(messages) if self.keep_text else None, response if self.keep_text else None, error,
                 int((time.perf_counter() - started) * 1000), tin, tout),
            )  # fmt: skip
            conn.commit()
            conn.close()
        except sqlite3.Error:
            pass  # logging must never break a run


def logger_for(settings: Any, step: str) -> CallLogger | None:
    """The handler for a step's model, or None when logging is off. Purges old rows once per process."""
    from autotailor.paths import get_paths

    log = settings.log
    if not log.enabled:
        return None
    path = get_paths().db
    if str(path) not in _purged:
        _purged.add(str(path))
        try:
            conn = db.connect(path)
            purge(conn, log.retention_days)
            conn.close()
        except sqlite3.Error:
            pass
    spec = settings.models.for_step(step)
    return CallLogger(path, step, spec.provider, spec.model, spec.is_local, keep_text=log.keep_prompts)


def calls(conn: sqlite3.Connection, item: str | None = None, limit: int = 100, step: str | None = None) -> list[sqlite3.Row]:
    sql, args = "SELECT * FROM llm_calls WHERE 1=1", []
    if item:
        sql += " AND item = ?"
        args.append(item)
    if step:
        sql += " AND step = ?"
        args.append(step)
    return conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
