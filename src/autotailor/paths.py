"""Where AutoTailor keeps its files.

Everything lives under one home directory: the current directory by default,
or the directory named by the AUTOTAILOR_HOME environment variable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def config(self) -> Path:
        return self.home / "config.toml"

    @property
    def env(self) -> Path:
        """API keys. Kept apart from config.toml, which is displayed and saved with each run."""
        return self.home / ".env"

    @property
    def master_cv(self) -> Path:
        return self.home / "cv" / "master.yaml"

    @property
    def data(self) -> Path:
        return self.home / "data"

    @property
    def db(self) -> Path:
        return self.data / "autotailor.db"

    @property
    def checkpoints(self) -> Path:
        """LangGraph checkpoints for batch runs, so an interrupted run can resume."""
        return self.data / "checkpoints.db"

    @property
    def cv_backups(self) -> Path:
        """Previous versions of the master CV, one per save from the UI."""
        return self.data / "cv_backups"

    @property
    def config_backups(self) -> Path:
        """Previous versions of config.toml, one per save from the UI."""
        return self.data / "config_backups"

    @property
    def inbox(self) -> Path:
        """Drop folder for .eml files (the default mail intake)."""
        return self.data / "inbox"

    @property
    def outputs(self) -> Path:
        return self.data / "outputs"

    def ensure_dirs(self) -> None:
        for d in (self.master_cv.parent, self.data, self.inbox, self.outputs):
            d.mkdir(parents=True, exist_ok=True)


def get_paths(home: Path | None = None) -> Paths:
    if home is None:
        home = Path(os.environ.get("AUTOTAILOR_HOME", Path.cwd()))
    return Paths(home=home.resolve())
