"""Plain-Python helpers behind the UI (no Streamlit here, so they are unit-tested).

- Saving: the CV and settings are validated before anything is written, and the
  previous file is kept as a timestamped backup.
- IDs: new CV entries and bullets get generated IDs; existing IDs never change,
  so past runs keep pointing at the right text.
- Usage: which past resumes used a bullet (read from their tailored.json).
- Diff: a tailored resume's bullets beside the master-CV bullets they came from.
"""

from __future__ import annotations

import difflib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from autotailor import db
from autotailor.config import Settings, save_settings
from autotailor.cv import MasterCV
from autotailor.models import ArtifactKind, TailoredResume

CV_SECTIONS: dict[str, str] = {
    "experience": "Experience",
    "projects": "Projects",
    "activities": "Leadership & Activities",
    "volunteer": "Volunteer",
    "awards": "Awards",
    "certifications": "Certifications",
}
SECTION_PREFIX = {
    "education": "edu", "experience": "exp", "projects": "proj", "activities": "act",
    "volunteer": "vol", "awards": "award", "certifications": "cert",
}  # fmt: skip

CV_HEADER = "# Master CV. Edited in the AutoTailor UI; keep IDs stable once written: past runs refer to them.\n\n"


# --- IDs ----------------------------------------------------------------------------


def _slug(text: str, max_words: int = 3) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return "-".join(words[:max_words]) or "item"


def new_id(prefix: str, hint: str, taken: set[str]) -> str:
    """A fresh valid ID like 'exp-acme-robotics', unique among `taken` (which it adds to)."""
    base = f"{prefix}-{_slug(hint)}" if prefix else _slug(hint)
    candidate, n = base, 2
    while candidate in taken:
        candidate, n = f"{base}-{n}", n + 1
    taken.add(candidate)
    return candidate


def all_ids(data: dict[str, Any]) -> set[str]:
    """Every entry and bullet ID in a CV dict (draft or saved)."""
    ids: set[str] = set()
    for section in ("education", *CV_SECTIONS):
        for e in data.get(section) or []:
            if e.get("id"):
                ids.add(e["id"])
            for b in (e.get("details") if section == "education" else e.get("bullets")) or []:
                if b.get("id"):
                    ids.add(b["id"])
    return ids


def fill_missing_ids(data: dict[str, Any], keep: set[str] = frozenset()) -> dict[str, Any]:
    """Give every entry and bullet without an ID a generated one. Existing IDs are untouched.

    `keep`: IDs that must not be reused even though they're no longer in `data`
    (e.g. deleted bullets that past runs refer to).
    """
    taken = all_ids(data) | set(keep)
    for section in ("education", *CV_SECTIONS):
        prefix = SECTION_PREFIX[section]
        for e in data.get(section) or []:
            if not e.get("id"):
                hint = e.get("school") if section == "education" else (e.get("organization") or e.get("title"))
                e["id"] = new_id(prefix, hint or section, taken)
            key = "details" if section == "education" else "bullets"
            for b in e.get(key) or []:
                if not b.get("id"):
                    b["id"] = new_id(e["id"], b.get("text") or "bullet", taken)
    return data


# --- saving ------------------------------------------------------------------------


def _prune(value: Any) -> Any:
    """Drop None, empty strings and empty lists so the YAML stays as tidy as a hand-written file."""
    if isinstance(value, dict):
        out = {k: _prune(v) for k, v in value.items()}
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


class _Dumper(yaml.SafeDumper):
    pass


def _str(dumper: yaml.SafeDumper, s: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", s, style="|" if "\n" in s else None)


_Dumper.add_representer(str, _str)


def cv_to_yaml(cv: MasterCV) -> str:
    data = _prune(cv.model_dump(mode="json"))
    data.setdefault("contact", {"name": cv.contact.name})
    return CV_HEADER + yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=10_000)


def backup(path: Path, backups: Path) -> Path | None:
    """Copy `path` to backups/<stem>-<timestamp><suffix>. None if there was nothing to keep."""
    if not path.exists():
        return None
    backups.mkdir(parents=True, exist_ok=True)
    dest = backups / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S-%f}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def save_cv(data: dict[str, Any] | MasterCV, path: Path, backups: Path, keep_ids: set[str] = frozenset()) -> MasterCV:
    """Validate, back up the current file, then write. Raises (writing nothing) if invalid."""
    if isinstance(data, MasterCV):
        cv = data
    else:
        cv = MasterCV.model_validate(fill_missing_ids(data, keep_ids))
    backup(path, backups)
    _write_atomic(path, cv_to_yaml(cv))
    return cv


def save_settings_checked(data: dict[str, Any], path: Path, backups: Path) -> Settings:
    """Validate a settings dict, back up config.toml, then write. Raises if invalid."""
    settings = Settings.model_validate(data)
    backup(path, backups)
    save_settings(settings, path)
    return settings


def parse_cv_yaml(text: str) -> MasterCV:
    """For the raw YAML editor. Raises yaml.YAMLError or pydantic.ValidationError."""
    return MasterCV.model_validate(yaml.safe_load(text) or {})


# --- where bullets were used -----------------------------------------------------------


@dataclass
class Use:
    run_id: int
    posting_id: str
    company: str
    title: str


def bullet_usage(conn: sqlite3.Connection, outputs: Path) -> dict[str, list[Use]]:
    """Bullet ID -> the saved resumes that used it, from each run's tailored.json."""
    rows = conn.execute(
        "SELECT a.run_id, a.posting_id, a.path, p.company, p.title FROM artifacts a "
        "LEFT JOIN postings p ON p.id = a.posting_id WHERE a.kind = ? ORDER BY a.run_id",
        (ArtifactKind.RESUME_DATA.value,),
    ).fetchall()
    usage: dict[str, list[Use]] = {}
    for r in rows:
        try:
            resume = TailoredResume.model_validate_json((outputs / r["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for b in resume.bullets:
            usage.setdefault(b.source_id, []).append(
                Use(r["run_id"], r["posting_id"], r["company"] or "?", r["title"] or "?")
            )
    return usage


def removed_ids(old: MasterCV | dict[str, Any], new: dict[str, Any] | MasterCV) -> set[str]:
    def ids(x: MasterCV | dict[str, Any]) -> set[str]:
        return all_ids(x.model_dump() if isinstance(x, MasterCV) else x)

    return ids(old) - ids(new)


# --- history -------------------------------------------------------------------------


@dataclass
class DiffRow:
    source_id: str
    original: str | None  # None: the ID is no longer in the master CV
    tailored: str
    changed: bool
    html: str  # tailored text with words not in the original marked <ins>


def _word_diff(a: str, b: str) -> str:
    import html

    aw, bw = a.split(), b.split()
    out: list[str] = []
    for op, _i1, _i2, j1, j2 in difflib.SequenceMatcher(a=aw, b=bw).get_opcodes():
        chunk = html.escape(" ".join(bw[j1:j2]))
        if not chunk:
            continue
        out.append(chunk if op == "equal" else f"<ins>{chunk}</ins>")
    return " ".join(out)


def resume_diff(resume: TailoredResume, cv: MasterCV) -> list[DiffRow]:
    src = cv.bullets()
    rows = []
    for b in resume.bullets:
        orig = src.get(b.source_id)
        text = orig.text if orig else None
        rows.append(
            DiffRow(b.source_id, text, b.text, text != b.text, _word_diff(text or "", b.text))
        )
    return rows


@dataclass
class RunOutput:
    index: int
    posting_id: str
    company: str
    title: str
    folder: Path
    pdf: Path | None
    docx: Path | None
    data: Path | None
    model: str | None


def run_outputs(conn: sqlite3.Connection, run_id: int, outputs: Path) -> list[RunOutput]:
    """The resumes a run saved, in the run's order."""
    by_posting: dict[str, dict[str, Any]] = {}
    for a in db.run_artifacts(conn, run_id):
        d = by_posting.setdefault(a["posting_id"], {"model": a["model"]})
        d[a["kind"]] = outputs / a["path"]
    out: list[RunOutput] = []
    for pid, d in by_posting.items():
        pdf = d.get(ArtifactKind.RESUME_PDF.value)
        anyfile = pdf or d.get(ArtifactKind.RESUME_DATA.value) or d.get(ArtifactKind.RESUME_DOCX.value)
        folder = anyfile.parent if anyfile else outputs
        m = re.match(r"(\d+)-", folder.name)
        p = db.get_posting(conn, pid)
        out.append(RunOutput(
            index=int(m.group(1)) if m else 0, posting_id=pid,
            company=p.company if p else "?", title=p.title if p else pid, folder=folder, pdf=pdf,
            docx=d.get(ArtifactKind.RESUME_DOCX.value), data=d.get(ArtifactKind.RESUME_DATA.value),
            model=d["model"],
        ))  # fmt: skip
    return sorted(out, key=lambda o: o.index)


def run_model(run: sqlite3.Row) -> str:
    """The tailor step's model, from the settings snapshot saved with the run."""
    try:
        s = Settings.model_validate_json(run["settings_json"])
    except ValueError:
        try:
            raw = json.loads(run["settings_json"] or "{}")
        except json.JSONDecodeError:
            return "?"
        spec = (raw.get("models") or {}).get("default") or {}
        return f"{spec.get('provider', '?')}:{spec.get('model', '?')}"
    spec = s.models.for_step("tailor")
    return f"{spec.provider}:{spec.model}"
