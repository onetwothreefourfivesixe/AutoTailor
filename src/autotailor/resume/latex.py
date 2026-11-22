"""LaTeX resume templates, compiled with Tectonic (M10).

Tectonic is a self-contained TeX engine. AutoTailor downloads it into
data/tools/ the first time a LaTeX template is used (no sudo, nothing installed
system-wide). On its first compiles Tectonic fetches the TeX packages it needs
from its package server into data/tools/tectonic-cache/; only package names go
out, never resume content.

Templates are Jinja with LaTeX-friendly delimiters, so they don't clash with
TeX's braces:

    ((( value )))        a value, LaTeX-escaped
    ((* for x in xs *))  a statement
    ((# note #))         a comment
    ((( url|url )))      a URL for \\href (only %, # and \\ escaped)
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

TECTONIC_VERSION = "0.17.0"
TECTONIC_URL = (
    "https://github.com/tectonic-typesetting/tectonic/releases/download/"
    f"tectonic%40{TECTONIC_VERSION}/tectonic-{TECTONIC_VERSION}-x86_64-unknown-linux-musl.tar.gz"
)
COMPILE_TIMEOUT = 180


class LatexUnavailable(RuntimeError):
    """Tectonic isn't installed and couldn't be downloaded (e.g. offline)."""


class LatexError(RuntimeError):
    """The template didn't compile; the message has the TeX error."""


# --- escaping and templating -------------------------------------------------------------

_ESCAPES = {
    "\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}  # fmt: skip
_ESC = re.compile("|".join(re.escape(k) for k in _ESCAPES))


class Raw(str):
    """Already-LaTeX text: inserted as is."""


def escape(value: Any) -> str:
    if isinstance(value, Raw):
        return value
    text = "" if value is None else str(value)
    return _ESC.sub(lambda m: _ESCAPES[m.group()], text)


def _url(value: Any) -> Raw:
    return Raw(re.sub(r"([%#\\])", r"\\\1", str(value or "")))


def environment():  # noqa: ANN201
    import jinja2

    env = jinja2.Environment(
        block_start_string="((*", block_end_string="*))",
        variable_start_string="(((", variable_end_string=")))",
        comment_start_string="((#", comment_end_string="#))",
        trim_blocks=True, lstrip_blocks=True, autoescape=False, undefined=jinja2.StrictUndefined,
        finalize=escape,
    )  # fmt: skip
    env.filters["url"] = _url
    env.filters["raw"] = Raw
    return env


def builtin(name: str) -> str:
    return (resources.files("autotailor.templates") / name).read_text(encoding="utf-8")


def render_tex(template: str, layout: dict[str, Any]) -> str:
    return environment().from_string(template).render(**layout)


# --- Tectonic ---------------------------------------------------------------------------------


def tectonic_path(tools: Path) -> Path:
    return tools / "tectonic"


def ensure_tectonic(tools: Path) -> Path:
    """The Tectonic binary, downloading it into `tools` on first use."""
    found = shutil.which("tectonic")
    if found:
        return Path(found)
    exe = tectonic_path(tools)
    if exe.exists():
        return exe
    import httpx

    tools.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=tools) as tmp:
            archive = Path(tmp) / "tectonic.tar.gz"
            with httpx.stream("GET", TECTONIC_URL, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                with archive.open("wb") as f:
                    for chunk in r.iter_bytes():
                        f.write(chunk)
            with tarfile.open(archive) as tar:
                member = next(m for m in tar.getmembers() if Path(m.name).name == "tectonic" and m.isfile())
                member.name = "tectonic"
                tar.extract(member, tmp, filter="data")
            shutil.move(str(Path(tmp) / "tectonic"), exe)
    except (httpx.HTTPError, OSError, StopIteration, tarfile.TarError) as e:
        raise LatexUnavailable(f"Couldn't download Tectonic ({type(e).__name__}: {e}).") from e
    exe.chmod(0o755)
    return exe


def compile_pdf(tex: str, tools: Path) -> bytes:
    """Compile a .tex source to PDF bytes."""
    exe = ensure_tectonic(tools)
    env = {**os.environ, "TECTONIC_CACHE_DIR": str(tools / "tectonic-cache")}
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "resume.tex"
        src.write_text(tex, encoding="utf-8")
        try:
            proc = subprocess.run(
                [str(exe), "--chatter", "minimal", str(src)], cwd=tmp, env=env,
                capture_output=True, text=True, timeout=COMPILE_TIMEOUT,
            )  # fmt: skip
        except subprocess.TimeoutExpired as e:
            raise LatexError("Tectonic timed out (is the package server reachable?).") from e
        pdf = Path(tmp) / "resume.pdf"
        if proc.returncode != 0 or not pdf.exists():
            log = (proc.stdout + proc.stderr).strip()
            errors = [ln for ln in log.splitlines() if ln.startswith(("!", "error", "l."))] or log.splitlines()[-8:]
            raise LatexError("LaTeX failed:\n" + "\n".join(errors[:12]))
        return pdf.read_bytes()
