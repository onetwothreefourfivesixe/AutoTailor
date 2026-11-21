"""Command-line interface. Run `autotailor --help`."""

from __future__ import annotations

import os
import sys
from importlib import resources
from pathlib import Path

import httpx
import typer
from pydantic import ValidationError

from autotailor import __version__, db
from autotailor.config import STEPS, Settings, load_settings, save_settings, set_value
from autotailor.cv import load_master_cv
from autotailor.ingest import ingest
from autotailor.llm import API_KEY_ENV, ENV_TEMPLATE, MODE_DESCRIPTIONS, describe, load_api_keys
from autotailor.models import PostingStatus, Source, Verdict
from autotailor.paths import get_paths

app = typer.Typer(no_args_is_help=True, help="Local-first job matching and resume tailoring.")
config_app = typer.Typer(no_args_is_help=True, help="Show or change settings.")
cv_app = typer.Typer(no_args_is_help=True, help="Work with the master CV.")
jobs_app = typer.Typer(no_args_is_help=True, help="Browse stored job postings.")
app.add_typer(config_app, name="config")
app.add_typer(cv_app, name="cv")
app.add_typer(jobs_app, name="jobs")


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
    models = settings.models
    typer.echo(f"     models: {MODE_DESCRIPTIONS[models.mode()]}")
    specs = [(step, models.for_step(step)) for step in STEPS] + [("embeddings", models.embeddings)]
    for name, spec in specs:
        typer.echo(f"     model[{name}] = {describe(spec)}")
    for var in sorted({API_KEY_ENV[spec.provider] for _, spec in specs if not spec.is_local}):
        has_key = bool(os.environ.get(var))
        check(var, has_key, "" if has_key else f"not set; add it to {paths.env}")

    local_models = sorted({spec.model for _, spec in specs if spec.is_local})
    if local_models:
        try:
            r = httpx.get(f"{models.ollama_base_url.rstrip('/')}/api/tags", timeout=5)
            r.raise_for_status()
            pulled = {m["name"] for m in r.json().get("models", [])}
        except httpx.HTTPError as e:
            check("Ollama", False, f"not reachable at {models.ollama_base_url} ({e.__class__.__name__})")
        else:
            check("Ollama", True, models.ollama_base_url)
            for model in local_models:
                found = model in pulled or f"{model}:latest" in pulled
                check(f"ollama model {model}", found, "" if found else f"run `ollama pull {model}`")

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


# --- ingestion (M1) --------------------------------------------------------


def _configured_boards(settings: Settings) -> list[tuple[Source, str]]:
    src = settings.sources
    return [
        *((Source.GREENHOUSE, slug) for slug in src.greenhouse),
        *((Source.LEVER, slug) for slug in src.lever),
        *((Source.ASHBY, slug) for slug in src.ashby),
    ]


def _parse_board(spec: str) -> tuple[Source, str]:
    from autotailor.sources.ats import ATS_SOURCES

    name, _, slug = spec.partition(":")
    names = {s.value: s for s in ATS_SOURCES}
    if name not in names or not slug:
        _fail(f"Bad board '{spec}'. Use <source>:<slug> with source one of {', '.join(names)}.")
    return names[name], slug


@app.command()
def fetch(
    board: list[str] = typer.Option(
        None, "--board", "-b", help="<source>:<slug>, e.g. greenhouse:figma. Repeatable."
    ),
    no_filter: bool = typer.Option(False, "--no-filter", help="Ignore the title filter."),
) -> None:
    """Pull postings from company job boards (Greenhouse, Lever, Ashby).

    Without --board, fetches every board listed under [sources] in config.toml.
    """
    from autotailor.sources import make_client
    from autotailor.sources.ats import fetch_board

    settings = _settings()
    if board:
        boards = [_parse_board(b) for b in board]
    else:
        if not settings.sources.ats_boards:
            _fail("Job board fetching is off (sources.ats_boards = false).")
        boards = _configured_boards(settings)
        if not boards:
            _fail(
                "No boards configured. Add company slugs to config.toml, e.g.\n"
                '  autotailor config set sources.greenhouse \'["figma"]\'\n'
                "or pass one directly: autotailor fetch -b greenhouse:figma"
            )

    include = [] if no_filter else settings.sources.title_include
    exclude = [] if no_filter else settings.sources.title_exclude
    conn = db.connect(get_paths().db)
    failures = 0
    with make_client() as client:
        for source, slug in boards:
            label = f"{source.value}:{slug}"
            try:
                postings = fetch_board(client, source, slug)
            except httpx.HTTPStatusError as e:
                failures += 1
                code = e.response.status_code
                why = f"no board named '{slug}'" if code == 404 else f"HTTP {code}"
                typer.secho(f"{label}  failed: {why}", fg=typer.colors.RED, err=True)
                continue
            except httpx.HTTPError as e:
                failures += 1
                typer.secho(f"{label}  failed: {e}", fg=typer.colors.RED, err=True)
                continue
            report = ingest(conn, postings, include, exclude)
            typer.echo(f"{label}  {report.summary()}")
    conn.close()
    if failures == len(boards):
        raise typer.Exit(1)


@app.command()
def add(
    url: str | None = typer.Argument(None, help="Posting URL. Job board URLs need no model."),
    file: Path | None = typer.Option(
        None, "--file", "-f", exists=True, dir_okay=False, help="Text file with the posting."
    ),
) -> None:
    """Add one posting from a URL, a text file, or text piped on stdin."""
    from autotailor.chains.extract_posting import extract_posting_fields
    from autotailor.sources import make_client
    from autotailor.sources.paste import NoFetchError, from_text, from_url

    settings = _settings()
    if url and file:
        _fail("Give a URL or --file, not both.")
    text: str | None = None
    if file:
        text = file.read_text(encoding="utf-8")
    elif not url:
        if sys.stdin.isatty():
            _fail("Give a URL, --file PATH, or pipe the posting text in.")
        text = sys.stdin.read()

    def extract(posting_text: str):
        spec = settings.models.for_step("extract")
        typer.echo(f"Reading the posting with {describe(spec)} ...", err=True)
        return extract_posting_fields(posting_text, settings)

    try:
        if url:
            with make_client() as client:
                posting = from_url(client, url, extract)
        else:
            posting = from_text(text or "", extract)
    except NotImplementedError:
        _fail(
            "The extraction chain is not written yet: it is the M1 exercise in "
            "src/autotailor/chains/extract_posting.py.\n"
            "Job board URLs (Greenhouse, Lever, Ashby) work without it."
        )
        return
    except NoFetchError as e:
        _fail(str(e))
        return
    except (httpx.HTTPError, LookupError, ValueError) as e:
        _fail(f"Could not add the posting: {e}")
        return

    conn = db.connect(get_paths().db)
    duplicate_of = db.find_duplicate(conn, posting)
    report = ingest(conn, [posting])
    conn.close()
    where = f"{posting.company} | {posting.title} | {posting.location or 'location not stated'}"
    if report.new:
        typer.echo(f"Added {posting.id}: {where}")
    elif duplicate_of:
        extra = " (filled in missing details)" if report.merged else ""
        typer.echo(f"Already stored as {duplicate_of}{extra}: {where}")
    else:
        typer.echo(f"Refreshed {posting.id}: {where}")


@jobs_app.command("list")
def jobs_list(
    limit: int = typer.Option(30, "--limit", "-n", min=1),
    status: PostingStatus | None = typer.Option(None, "--status", "-s"),
    source: Source | None = typer.Option(None, "--source"),
    verdict: Verdict | None = typer.Option(None, "--verdict", "-v", help="Only this eligibility verdict."),
) -> None:
    """List stored postings, newest first, with their eligibility verdict."""
    conn = db.connect(get_paths().db)
    matches = db.all_matches(conn)
    rows = db.list_postings(conn, None if verdict else limit, status, source.value if source else None)
    conn.close()
    if verdict:
        rows = [p for p in rows if p.id in matches and matches[p.id].verdict == verdict][:limit]
    if not rows:
        typer.echo("No matching postings. Try `autotailor fetch`, `autotailor add` or `autotailor screen`.")
        return
    colors = {"eligible": typer.colors.GREEN, "ineligible": typer.colors.RED, "unclear": typer.colors.YELLOW}
    id_width = max(len(p.id) for p in rows)
    for p in rows:
        date = p.posted_at.date().isoformat() if p.posted_at else " " * 10
        v = matches[p.id].verdict.value if p.id in matches else "unscreened"
        typer.echo(
            f"{date}  {typer.style(f'{v:<10}', fg=colors.get(v))}  {p.id:<{id_width}}  "
            f"{p.company[:24]:<24}  {p.title[:70]}"
        )


@jobs_app.command("show")
def jobs_show(
    posting_id: str,
    full: bool = typer.Option(False, "--full", help="Print the whole description."),
) -> None:
    """Show one posting."""
    conn = db.connect(get_paths().db)
    p = db.get_posting(conn, posting_id)
    m = db.get_match(conn, posting_id)
    conn.close()
    if p is None:
        _fail(f"No posting with ID {posting_id}.")
        return
    typer.echo(f"{p.title}\n{p.company} | {p.location or 'location not stated'}")
    typer.echo(f"ID: {p.id}  status: {p.status.value}  posted: {p.posted_at or 'unknown'}")
    if p.canonical_url:
        typer.echo(f"URL: {p.canonical_url}")
    if m:
        typer.echo(f"\nEligibility: {m.verdict.value}")
        for reason in m.verdict_reasons:
            typer.echo(f"  - {reason}")
    else:
        typer.echo("\nEligibility: not screened yet (run `autotailor screen`)")
    if m and (m.prescore is not None or m.fit_score is not None):
        typer.echo("\nMatch:")
        if m.prescore is not None:
            typer.echo(f"  Embedding similarity: {m.prescore:.3f}")
        if m.fit_score is not None:
            typer.echo(f"  Fit score: {m.fit_score:.2f}")
            for reason in m.fit_reasons:
                typer.echo(f"  - {reason}")
        if m.fit and m.fit.relevant_bullet_ids:
            typer.echo(f"  Most relevant CV bullets: {', '.join(m.fit.relevant_bullet_ids)}")
    if p.description:
        limit = None if full else 1500
        body = p.description[:limit]
        typer.echo("\n" + body + ("\n... (use --full for the rest)" if not full and len(p.description) > 1500 else ""))


# --- eligibility (M2) -------------------------------------------------------


@app.command()
def screen(
    posting_ids: list[str] = typer.Argument(None, help="Only these postings. Default: all."),
    rejudge: bool = typer.Option(
        False, "--rejudge", help="Recompute verdicts from stored requirements only; no model calls."
    ),
    reextract: bool = typer.Option(
        False, "--reextract", help="Read requirements again even where already stored."
    ),
    limit: int = typer.Option(
        50, "--limit", "-n", min=1, help="Most postings to send to the model in one run."
    ),
) -> None:
    """Check eligibility: title pass, requirements extraction, then a verdict per posting.

    Postings whose title already rules them out are not sent to the model.
    Re-run with --rejudge after changing your profile or eligibility rules.
    """
    from autotailor import eligibility
    from autotailor.chains.extract_requirements import extract_requirements_batch

    settings = _settings()
    profile, rules = settings.profile, settings.eligibility
    if not profile.education:
        _fail("Add your education to config.toml first ([[profile.education]]).")
    conn = db.connect(get_paths().db)
    postings = (
        [p for pid in posting_ids if (p := db.get_posting(conn, pid))]
        if posting_ids
        else db.list_postings(conn, None)
    )
    if posting_ids and len(postings) < len(posting_ids):
        typer.secho("Some IDs were not found and are skipped.", fg=typer.colors.YELLOW, err=True)

    to_read = [] if rejudge else [
        p for p in postings
        if p.description
        and (p.requirements is None or reextract)
        and not eligibility.title_rules_out(p, profile, rules)
    ]  # fmt: skip
    skipped = len(to_read) - limit if len(to_read) > limit else 0
    to_read = to_read[:limit]

    if to_read:
        spec = settings.models.for_step("extract")
        typer.echo(f"Reading requirements of {len(to_read)} postings with {describe(spec)} ...", err=True)
        try:
            results = extract_requirements_batch(
                [p.description or "" for p in to_read],
                settings,
                max_concurrency=1 if spec.is_local else 4,
            )
        except NotImplementedError:
            skipped = 0  # nothing can be read until the chain exists
            typer.secho(
                "The requirements chain is not written yet (M2 exercise in "
                "src/autotailor/chains/extract_requirements.py). Using titles only for now.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            results = []
        except Exception as e:  # e.g. missing API key, Ollama down, unknown model name
            skipped = 0
            typer.secho(
                f"Could not run the extraction model ({describe(spec)}): {e}\nUsing titles only for now.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            results = []
        failed = 0
        for p, result in zip(to_read, results, strict=False):
            if isinstance(result, Exception):
                failed += 1
                typer.secho(f"{p.id}: extraction failed: {result}", fg=typer.colors.RED, err=True)
                continue
            db.set_requirements(conn, p.id, result)
            p.requirements = result
        if failed:
            typer.secho(f"{failed} extractions failed; those postings stay unclear.", fg=typer.colors.RED, err=True)

    counts = {v: 0 for v in Verdict}
    for p in postings:
        m = eligibility.assess(p, profile, rules)
        db.save_match(conn, m)
        counts[m.verdict] += 1
    conn.close()

    typer.echo(
        f"Screened {len(postings)}: {counts[Verdict.ELIGIBLE]} eligible, "
        f"{counts[Verdict.UNCLEAR]} unclear, {counts[Verdict.INELIGIBLE]} ineligible."
    )
    if skipped:
        typer.echo(f"{skipped} more postings still need reading; run `autotailor screen` again.")
    typer.echo("See `autotailor jobs list -v eligible` and `autotailor jobs show <id>` for reasons.")


# --- matching (M3) ----------------------------------------------------------


def _print_ranking(conn, settings: Settings, limit: int) -> None:
    from autotailor.matching import ranked

    rows = ranked(conn, settings)[:limit]
    if not rows:
        typer.echo("Nothing ranked yet.")
        return
    min_fit = settings.run.min_fit_score
    id_width = max(len(p.id) for p, _ in rows)
    typer.echo(f"{'fit':>5}  {'sim':>5}  {'verdict':<10}  {'id':<{id_width}}  company / title")
    for p, m in rows:
        fit = f"{m.fit_score:.2f}" if m.fit_score is not None else "  -  "
        sim = f"{m.prescore:.3f}" if m.prescore is not None else "  -  "
        color = typer.colors.GREEN if m.fit_score is not None and m.fit_score >= min_fit else None
        typer.echo(
            typer.style(f"{fit:>5}", fg=color)
            + f"  {sim:>5}  {m.verdict.value:<10}  {p.id:<{id_width}}  {p.company[:22]} | {p.title[:60]}"
        )


@app.command()
def rank(
    rescore: bool = typer.Option(False, "--rescore", help="Fit-score the shortlist again, even if scored before."),
    show: int = typer.Option(None, "--show", "-n", min=1, help="Rows to print (default: shortlist size)."),
) -> None:
    """Rank eligible postings: local embedding pre-rank, then fit scores for the shortlist.

    Run `autotailor screen` first; only postings that pass eligibility are ranked.
    """
    from autotailor import matching
    from autotailor.chains.score_fit import score_fit_batch
    from autotailor.eligibility import passes_gate

    settings = _settings()
    paths = get_paths()
    try:
        cv = load_master_cv(paths.master_cv)
    except (OSError, ValidationError, ValueError) as e:
        _fail(f"Cannot read the master CV: {e}")
        return
    conn = db.connect(paths.db)
    matches = db.all_matches(conn)
    candidates = [
        p
        for p in db.list_postings(conn, None)
        if p.id in matches and p.description and passes_gate(matches[p.id].verdict, settings.eligibility)
    ]
    if not candidates:
        _fail("No eligible postings to rank. Run `autotailor fetch` or `add`, then `autotailor screen`.")

    spec = settings.models.embeddings
    typer.echo(f"Pre-ranking {len(candidates)} postings with embeddings from {describe(spec)} ...", err=True)
    try:
        prescores = matching.prescore_postings(conn, candidates, cv, settings)
    except Exception as e:  # Ollama down / model not pulled, or a missing key or bad model name
        hint = (
            f"Is Ollama running, and is the model pulled? Try `ollama pull {spec.model}`."
            if spec.is_local
            else f"Check {API_KEY_ENV[spec.provider]} in .env and the model name under [models.embeddings]."
        )
        _fail(f"Embedding failed: {e}\n{hint}")
        return
    for pid, score in prescores.items():
        db.save_prescore(conn, pid, score)

    shortlisted = sorted(candidates, key=lambda p: prescores[p.id], reverse=True)[: settings.run.shortlist_size]
    to_score = [p for p in shortlisted if rescore or matches[p.id].fit is None]
    if to_score:
        score_spec = settings.models.for_step("score")
        typer.echo(f"Fit-scoring {len(to_score)} postings with {describe(score_spec)} ...", err=True)
        try:
            results = score_fit_batch(
                [matching.posting_text(p, 12000) for p in to_score],
                matching.profile_text(cv, settings.profile),
                settings,
                max_concurrency=1 if score_spec.is_local else 4,
            )
        except NotImplementedError:
            typer.secho(
                "The fit-scoring chain is not written yet (M3 exercise in "
                "src/autotailor/chains/score_fit.py). Ranking by embedding similarity only.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            results = []
        except Exception as e:  # e.g. missing API key, Ollama down, unknown model name
            typer.secho(
                f"Could not run the fit-scoring model ({describe(score_spec)}): {e}\n"
                "Ranking by embedding similarity only.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            results = []
        failed = 0
        for p, result in zip(to_score, results, strict=False):
            if isinstance(result, Exception):
                failed += 1
                typer.secho(f"{p.id}: fit scoring failed: {result}", fg=typer.colors.RED, err=True)
                continue
            cleaned, dropped = matching.clean_assessment(result, cv)
            if dropped:
                typer.secho(f"{p.id}: ignored unknown CV bullet IDs {dropped}", fg=typer.colors.YELLOW, err=True)
            db.save_fit(conn, p.id, matching.compute_fit_score(cleaned), matching.fit_reasons(cleaned), cleaned)
        if failed:
            typer.secho(f"{failed} postings could not be scored.", fg=typer.colors.RED, err=True)

    _print_ranking(conn, settings, show or settings.run.shortlist_size)
    picked = matching.shortlist(conn, settings)
    conn.close()
    typer.echo(
        f"\n{len(picked)} postings meet the minimum fit score ({settings.run.min_fit_score}) "
        f"for tailoring (up to {settings.run.jobs_per_run} per run). See `autotailor shortlist`."
    )


@app.command("shortlist")
def shortlist_cmd() -> None:
    """Show the postings that will be tailored next (best fit first)."""
    from autotailor.matching import shortlist

    settings = _settings()
    conn = db.connect(get_paths().db)
    picked = shortlist(conn, settings)
    conn.close()
    if not picked:
        typer.echo(
            f"No postings meet the minimum fit score ({settings.run.min_fit_score}) yet. "
            "Run `autotailor rank`, or lower run.min_fit_score."
        )
        return
    for i, (p, m) in enumerate(picked, 1):
        typer.echo(f"{i:>2}. {m.fit_score:.2f}  {p.id}  {p.company} | {p.title}")


# --- tailoring (M4) ---------------------------------------------------------


@app.command()
def tailor(
    posting_ids: list[str] = typer.Argument(None, help="Only these postings. Default: the shortlist."),
    force: bool = typer.Option(False, "--force", help="Tailor again even if a resume already exists."),
) -> None:
    """Write a tailored resume (PDF, Word) plus the posting for each shortlisted role.

    Each resume is drafted by the tailoring graph and must pass verification
    against the master CV; a draft that still fails after the allowed attempts
    is reported, not written.
    """
    from datetime import date

    from autotailor import matching
    from autotailor.chains.tailor_graph import tailor_posting
    from autotailor.models import ArtifactKind
    from autotailor.resume.output import write_outputs

    settings = _settings()
    paths = get_paths()
    try:
        cv = load_master_cv(paths.master_cv)
    except (OSError, ValidationError, ValueError) as e:
        _fail(f"Cannot read the master CV: {e}")
        return
    conn = db.connect(paths.db)
    rows = matching.ranked(conn, settings)
    if posting_ids:
        targets = []
        for pid in posting_ids:
            p = db.get_posting(conn, pid)
            if p is None or not p.description:
                typer.secho(f"{pid}: not found or has no description; skipped.", fg=typer.colors.YELLOW, err=True)
                continue
            targets.append((p, db.get_match(conn, pid)))
    else:
        targets = matching.shortlist(conn, settings)
    if not force:
        done = [p for p, _ in targets if any(a["kind"] == ArtifactKind.RESUME_PDF for a in db.artifacts_for(conn, p.id))]
        if done:
            typer.echo(f"Skipping {len(done)} already tailored (use --force to redo).", err=True)
        targets = [(p, m) for p, m in targets if p not in done]
    if not targets:
        conn.close()
        typer.echo("Nothing to tailor. Run `autotailor rank` (see `autotailor shortlist`), or pass posting IDs.")
        return

    spec = settings.models.for_step("tailor")
    run_id = db.start_run(conn, settings.model_dump_json())
    out_root = paths.outputs / f"{date.today().isoformat()}-run{run_id}"
    typer.echo(f"Tailoring {len(targets)} resumes with {describe(spec)} ...", err=True)
    base_profile = matching.profile_text(cv, settings.profile)
    counts = {"tailored": 0, "failed": 0}
    for i, (p, m) in enumerate(targets, 1):
        profile = base_profile
        if m and m.fit and m.fit.relevant_bullet_ids:
            profile += "\n\nMost relevant CV bullets for this posting (from fit scoring): " + ", ".join(
                m.fit.relevant_bullet_ids
            )
        label = f"{i:>2}. {p.company} | {p.title}"
        try:
            result = tailor_posting(matching.posting_text(p, 12000), profile, cv, settings)
        except NotImplementedError:
            db.finish_run(conn, run_id, "failed", counts)
            conn.close()
            _fail("The tailoring graph is not written yet: it is the M4 exercise in src/autotailor/chains/tailor_graph.py.")
            return
        except Exception as e:  # model errors, missing key, invalid output after retries
            counts["failed"] += 1
            typer.secho(f"{label}: failed: {e}", fg=typer.colors.RED, err=True)
            continue
        if result.resume is None or result.errors:
            counts["failed"] += 1
            typer.secho(
                f"{label}: no resume written; the last of {result.attempts} drafts still had problems:",
                fg=typer.colors.RED,
                err=True,
            )
            for err in result.errors[:5]:
                typer.secho(f"      - {err}", fg=typer.colors.RED, err=True)
            continue
        w = write_outputs(out_root, i, p, m, result.resume, cv, matching.same_role(p, rows))
        model = f"{spec.provider}:{spec.model}"
        for kind, path in ((ArtifactKind.RESUME_PDF, w.pdf), (ArtifactKind.RESUME_DOCX, w.docx), (ArtifactKind.RESUME_DATA, w.data)):
            db.add_artifact(conn, run_id, p.id, kind.value, str(path.relative_to(paths.outputs)), model)
        counts["tailored"] += 1
        pages = "" if w.pages <= 1 else typer.style(f"  ({w.pages} pages: lower tailor.max_bullets?)", fg=typer.colors.YELLOW)
        typer.echo(f"{label}  ({result.attempts} draft{'s' if result.attempts > 1 else ''}){pages}")
    db.finish_run(conn, run_id, "done", counts)
    conn.close()
    typer.echo(f"\n{counts['tailored']} tailored, {counts['failed']} failed. Output: {out_root}")


if __name__ == "__main__":
    app()
