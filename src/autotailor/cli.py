"""Command-line interface. Run `autotailor --help`."""

from __future__ import annotations

import os
from importlib import resources

import typer
from pydantic import ValidationError

from autotailor import __version__, db
from autotailor.config import STEPS, Settings, load_settings, save_settings, set_value
from autotailor.cv import load_master_cv
from autotailor.llm import API_KEY_ENV, ENV_TEMPLATE, describe, load_api_keys
from autotailor.paths import get_paths

app = typer.Typer(no_args_is_help=True, help="Local-first job matching and resume tailoring.")
config_app = typer.Typer(no_args_is_help=True, help="Show or change settings.")
cv_app = typer.Typer(no_args_is_help=True, help="Work with the master CV.")
app.add_typer(config_app, name="config")
app.add_typer(cv_app, name="cv")


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _settings() -> Settings:
    try:
        return load_settings(get_paths().config)
    except ValidationError as e:
        _fail(f"config.toml is invalid:\n{e}")
        raise  # unreachable; keeps type checkers happy


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def init() -> None:
    """Create config.toml, the master CV template, and the database."""
    paths = get_paths()
    paths.ensure_dirs()

    if paths.config.exists():
        typer.echo(f"kept     {paths.config}")
    else:
        save_settings(Settings(), paths.config)
        typer.echo(f"created  {paths.config}")

    if paths.master_cv.exists():
        typer.echo(f"kept     {paths.master_cv}")
    else:
        template = resources.files("autotailor.templates") / "master_cv.example.yaml"
        paths.master_cv.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
        typer.echo(f"created  {paths.master_cv}")

    if paths.env.exists():
        typer.echo(f"kept     {paths.env}")
    else:
        paths.env.write_text(ENV_TEMPLATE, encoding="utf-8")
        paths.env.chmod(0o600)
        typer.echo(f"created  {paths.env}")

    db.connect(paths.db).close()
    typer.echo(f"ready    {paths.db}")
    typer.echo(
        "\nNext: edit config.toml (profile) and cv/master.yaml, put your API key in .env, "
        "then run `autotailor doctor`."
    )


@app.command()
def doctor() -> None:
    """Check that everything needed for a run is in place."""
    paths = get_paths()
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        mark = typer.style("ok  ", fg=typer.colors.GREEN) if passed else typer.style("FAIL", fg=typer.colors.RED)
        typer.echo(f"{mark} {label}{': ' + detail if detail else ''}")

    check("config.toml", paths.config.exists(), "" if paths.config.exists() else "run `autotailor init`")
    settings = _settings()

    check(
        "profile education",
        bool(settings.profile.education),
        "" if settings.profile.education else "add at least one [[profile.education]] entry",
    )

    if paths.master_cv.exists():
        try:
            cv = load_master_cv(paths.master_cv)
            check("master CV", True, f"{len(cv.entries())} entries, {len(cv.bullets())} bullets")
        except (ValidationError, ValueError) as e:
            check("master CV", False, str(e).splitlines()[0])
    else:
        check("master CV", False, "run `autotailor init`")

    if paths.db.exists():
        conn = db.connect(paths.db)
        counts = db.table_counts(conn)
        conn.close()
        check("database", True, ", ".join(f"{k}={v}" for k, v in counts.items()))
    else:
        check("database", False, "run `autotailor init`")

    load_api_keys(paths.env)
    key_vars: set[str] = set()
    for step in STEPS:
        spec = settings.models.for_step(step)
        if not spec.is_local:
            key_vars.add(API_KEY_ENV[spec.provider])
        typer.echo(f"     model[{step}] = {describe(spec)}")
    for var in sorted(key_vars):
        has_key = bool(os.environ.get(var))
        check(var, has_key, "" if has_key else f"not set; add it to {paths.env}")

    raise typer.Exit(0 if ok else 1)


@config_app.command("show")
def config_show() -> None:
    """Print the current settings (defaults included)."""
    import tomli_w

    typer.echo(tomli_w.dumps(_settings().model_dump(mode="json", exclude_none=True)))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(help="Dotted path, e.g. run.jobs_per_run"),
    value: str = typer.Argument(help='New value; JSON for lists/numbers/booleans, e.g. 12 or \'["NYC"]\''),
) -> None:
    """Change one setting. The whole file is re-validated before saving."""
    paths = get_paths()
    try:
        updated = set_value(_settings(), key, value)
    except KeyError as e:
        _fail(str(e.args[0]))
    except ValidationError as e:
        _fail(f"Rejected:\n{e}")
    else:
        save_settings(updated, paths.config)
        typer.echo(f"{key} = {value}")


@cv_app.command("validate")
def cv_validate() -> None:
    """Check the master CV for schema errors and duplicate IDs."""
    path = get_paths().master_cv
    if not path.exists():
        _fail(f"No master CV at {path}. Run `autotailor init`.")
    try:
        cv = load_master_cv(path)
    except (ValidationError, ValueError) as e:
        _fail(f"Master CV is invalid:\n{e}")
    else:
        typer.echo(f"Valid: {len(cv.entries())} entries, {len(cv.bullets())} bullets.")


if __name__ == "__main__":
    app()
