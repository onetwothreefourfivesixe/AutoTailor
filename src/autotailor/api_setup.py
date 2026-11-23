"""API routes for the first-run setup wizard: status, CV import, API keys, suggested job boards.

Registered by api.create_app; same access rules (token, Host check).
"""

from __future__ import annotations

import json
import os
import re
from importlib import resources
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ValidationError

from autotailor.config import Settings
from autotailor.llm import API_KEY_ENV

SETUP_DONE = "setup_done"


class KeyIn(BaseModel):
    provider: Literal["anthropic", "openai", "google_genai"]
    key: str


def _template_text() -> str:
    return (resources.files("autotailor.templates") / "master_cv.example.yaml").read_text(encoding="utf-8")


def set_env_value(env_file: Path, name: str, value: str) -> None:
    """Set NAME=value in .env, replacing an existing line (keeps the rest of the file as it is)."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    pattern = re.compile(rf"^\s*{re.escape(name)}\s*=")
    out, done = [], False
    for line in lines:
        if pattern.match(line):
            if not done:
                out.append(f"{name}={value}")
                done = True
        else:
            out.append(line)
    if not done:
        out.append(f"{name}={value}")
    env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    env_file.chmod(0o600)


def keys_present(env_file: Path) -> dict[str, bool]:
    """Which providers have a key (in .env or the environment). Never returns the keys."""
    from dotenv import dotenv_values

    saved = dotenv_values(env_file) if env_file.exists() else {}
    return {p: bool((saved.get(var) or os.environ.get(var) or "").strip()) for p, var in API_KEY_ENV.items()}


def register(app: FastAPI, _conn, _settings, _paths, _need) -> None:  # noqa: ANN001
    @app.get("/api/setup/status")
    def status(request: Request) -> dict:
        """What's still missing before AutoTailor can do its job, step by step."""
        from autotailor.config import load_settings
        from autotailor.cv import load_master_cv

        p = _paths(request)
        try:
            settings = load_settings(p.config)
            settings_ok = True
        except ValidationError:
            settings, settings_ok = Settings(), False
        cv_state = "missing"
        if p.master_cv.exists():
            if p.master_cv.read_text(encoding="utf-8") == _template_text():
                cv_state = "template"
            else:
                try:
                    load_master_cv(p.master_cv)
                    cv_state = "ready"
                except (OSError, ValueError):
                    cv_state = "invalid"
        keys = keys_present(p.env)
        steps = [settings.models.for_step(s) for s in ("extract", "score", "tailor", "humanize")]
        missing_keys = sorted({s.provider for s in steps if not s.is_local and not keys.get(s.provider)})
        boards = sum(len(getattr(settings.sources, a)) for a in ("greenhouse", "lever", "ashby"))
        checks = {
            "settings": settings_ok,
            "cv": cv_state == "ready",
            "profile": bool(settings.profile.education),
            "models": not missing_keys,
            "sources": boards > 0,
        }
        return {
            "checks": checks,
            "complete": all(checks.values()),
            "dismissed": (p.data / SETUP_DONE).exists(),
            "cv": cv_state,
            "keys": keys,
            "missing_keys": missing_keys,
            "boards": boards,
            "mode": settings.models.mode(),
        }

    @app.post("/api/setup/finish")
    def finish(request: Request) -> dict:
        """Mark setup as done (or skipped), so the app stops sending you to it."""
        p = _paths(request)
        p.data.mkdir(parents=True, exist_ok=True)
        (p.data / SETUP_DONE).write_text("1")
        return {"ok": True}

    @app.put("/api/setup/keys")
    def put_key(body: KeyIn, request: Request) -> dict:
        """Save an API key to .env (owner-only file). The key is never sent back."""
        key = body.key.strip()
        if not key or any(c.isspace() for c in key) or len(key) > 500:
            raise HTTPException(422, "That doesn't look like an API key (it should be one line with no spaces).")
        p = _paths(request)
        set_env_value(p.env, API_KEY_ENV[body.provider], key)
        os.environ[API_KEY_ENV[body.provider]] = key  # usable now, without a restart
        return {"keys": keys_present(p.env)}

    @app.get("/api/setup/boards")
    def boards(settings: Settings = Depends(_settings)) -> dict:
        """Company job boards known to work, and which ones you already follow."""
        suggested = json.loads((resources.files("autotailor.sources") / "suggested_boards.json").read_text(encoding="utf-8"))
        mine = {(a, s) for a in ("greenhouse", "lever", "ashby") for s in getattr(settings.sources, a)}
        return {"boards": [{**b, "following": (b["ats"], b["slug"]) in mine} for b in suggested]}

    @app.post("/api/setup/cv")
    def import_cv(request: Request, file: UploadFile = File(...), settings: Settings = Depends(_settings)) -> dict[str, Any]:
        """Read a resume or CV file into a master-CV draft for review. Nothing is saved.

        PDF, Word, text and Markdown are sorted into sections by the `extract` step's
        model; an AutoTailor .yaml is read as is.
        """
        from autotailor import cv_import
        from autotailor.ui import logic

        name = file.filename or "cv"
        data = file.file.read()
        if name.lower().endswith((".yaml", ".yml")):
            try:
                cv = logic.parse_cv_yaml(data.decode("utf-8", "replace")).model_dump(mode="json")
            except Exception as e:  # YAML or schema errors
                raise HTTPException(422, f"That YAML isn't a valid master CV: {e}") from e
            source = None
        else:
            try:
                source = cv_import.document_text(name, data)
            except ValueError as e:
                raise HTTPException(422, str(e)) from e
            except Exception as e:  # corrupt files
                raise HTTPException(422, f"Couldn't read that file ({type(e).__name__}).") from e
            if len(source.strip()) < cv_import.MIN_CHARS:
                raise HTTPException(422, "There's very little text in that file. Is it the right one?")
            try:
                cv = cv_import.parse_cv(source, settings)
            except Exception as e:  # model unavailable, missing key, invalid output
                raise HTTPException(502, f"The model couldn't read your CV: {type(e).__name__}: {e}") from e
        try:
            from autotailor.cv import MasterCV

            MasterCV.model_validate(cv)
        except ValidationError as e:
            raise HTTPException(422, f"The result isn't a valid CV: {e}") from e
        return {
            "cv": cv,
            "unverified": cv_import.unverified_bullets(cv, source) if source else [],
            "education": cv_import.profile_from_cv(cv),
            "source_chars": len(source) if source else None,
        }
