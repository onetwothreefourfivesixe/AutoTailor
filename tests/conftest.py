"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _typst_resumes(monkeypatch):
    """Render resumes with the built-in Typst template in tests. The default (Jake's Resume)
    needs Tectonic; the template tests opt in explicitly."""
    from autotailor.resume.output import Style

    monkeypatch.setattr(Style, "from_settings", classmethod(lambda cls, settings, paths: cls("typst")))
