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
@app.callback()
def _startup() -> None:
    """Runs before every command."""
    from autotailor import tasks

    tasks.ctrl_break_acts_like_ctrl_c()  # Windows: how the worker stops a task


app.add_typer(config_app, name="config")
app.add_typer(cv_app, name="cv")
app.add_typer(jobs_app, name="jobs")
mail_app = typer.Typer(no_args_is_help=True, help="Bring in Runway notification emails.")
runway_app = typer.Typer(no_args_is_help=True, help="Runway roles: list them, find their employer postings.")
app.add_typer(mail_app, name="mail")
app.add_typer(runway_app, name="runway")
answers_app = typer.Typer(no_args_is_help=True, help="Short-answer questions: suggested points, opt-in drafts.")
app.add_typer(answers_app, name="answers")


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


def _prune(conn, settings: Settings, compact: bool = False) -> None:  # noqa: ANN001
    """Delete low-fit and too-old postings (see matching.prune) and say how many."""
    from autotailor import matching

    n = matching.prune(conn, settings)
    if n["low_fit"] or n["too_old"]:
        parts = [f"{n['low_fit']} below the fit cut-off"] * bool(n["low_fit"]) + [
            f"{n['too_old']} older than {settings.sources.max_age_days} days"] * bool(n["too_old"])  # fmt: skip
        typer.echo(f"Deleted {' and '.join(parts)} (kept: ones you tailored, applied to or added questions for).", err=True)
        if compact:
            db.compact(conn)


@app.command()
def discover(
    fields: str = typer.Argument(..., help='The fields or industries you\'re interested in, e.g. "biomedical".'),
    n: int = typer.Option(40, "-n", min=10, max=100, help="How many more companies to ask the model for."),
    no_model: bool = typer.Option(False, "--no-model", help="Only the built-in catalog; don't ask the model for more."),
    add: bool = typer.Option(False, "--add", help="Follow every board found (adds them to [sources])."),
) -> None:
    """Find companies in the fields you describe whose job boards AutoTailor can read.

    Your words are matched to industries in AutoTailor's catalog of verified job boards; the
    `extract` step's model can suggest more companies (only your description is sent), whose
    Greenhouse, Lever and Ashby boards are checked. Only boards that exist are listed.
    """
    from autotailor.config import save_settings
    from autotailor.sources import make_client
    from autotailor.sources.discover import LEARNED
    from autotailor.sources.discover import discover as run

    settings = _settings()
    spec = settings.models.for_step("extract")
    typer.echo(f"Asking {describe(spec)} for companies in: {fields} ...", err=True)
    with make_client() as client:
        d = run(fields, settings, client, ask_model=not no_model, n=n, store=get_paths().data / LEARNED)
    found, missing = d.boards, d.not_found
    typer.echo(f"Industries: {', '.join(d.industries) or 'none'}", err=True)
    for b in found:
        tag = "  (suggested by the model)" if b.name in d.from_model else ""
        typer.echo(f"{b.ats + ':' + b.slug:<40} {b.jobs:>4} jobs  {b.name}{tag}")
    typer.echo(f"\n{len(found)} boards found ({len(d.from_model)} from the model's suggestions, now saved to "
               f"the catalog for later searches); no board found for {len(missing)} suggestions.", err=True)  # fmt: skip
    if add and found:
        for b in found:
            lst = getattr(settings.sources, b.ats)
            if b.slug not in lst:
                lst.append(b.slug)
        save_settings(settings, get_paths().config)
        typer.echo(f"Following {len(found)} more boards (see `autotailor config show`).")


@app.command()
def prune() -> None:
    """Delete postings below the fit cut-off and ones older than sources.max_age_days, then
    shrink the database file. Runs on its own after every fetch and rank."""
    settings = _settings()
    path = get_paths().db
    before = path.stat().st_size
    conn = db.connect(path)
    _prune(conn, settings)
    db.compact(conn)
    conn.close()
    typer.echo(f"Database: {before / 1e6:.1f} MB -> {path.stat().st_size / 1e6:.1f} MB.")


@app.command()
def fetch(
    board: list[str] = typer.Option(
        None, "--board", "-b", help="<source>:<slug>, e.g. greenhouse:figma. Repeatable."
    ),
    no_filter: bool = typer.Option(False, "--no-filter", help="Ignore the title filter."),
    max_age: int = typer.Option(
        None, "--max-age", min=0, help="Skip postings older than this many days (default: sources.max_age_days; 0 for no cutoff)."
    ),
) -> None:
    """Pull postings from company job boards (Greenhouse, Lever, Ashby).

    Without --board, fetches every board listed under [sources] in config.toml.
    Postings first published more than sources.max_age_days ago (21 by default) are skipped.
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
    days = settings.sources.max_age_days if max_age is None else max_age
    if days:
        typer.echo(f"Keeping postings from the last {days} days.", err=True)
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
            report = ingest(conn, postings, include, exclude, days, settings.sources.keep_undated)
            typer.echo(f"{label}  {report.summary()}")
    # This fetch's cutoff (--max-age) also decides what counts as too old now.
    sources = settings.sources.model_copy(update={"max_age_days": days})
    _prune(conn, settings.model_copy(update={"sources": sources}), compact=True)
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
    include_cut: bool = typer.Option(False, "--include-cut", help="Also list postings below the fit cut-off."),
) -> None:
    """List stored postings, newest first, with their eligibility verdict.

    Postings whose fit score is below the cut-off (run.cut_low_fit) are left out unless --include-cut.
    """
    from autotailor.matching import is_cut

    settings = _settings()
    conn = db.connect(get_paths().db)
    matches = db.all_matches(conn)
    rows = db.list_postings(conn, None, status, source.value if source else None)
    conn.close()
    cut = [p for p in rows if is_cut(matches.get(p.id), settings.run)]
    if not include_cut:
        rows = [p for p in rows if p not in cut]
    if verdict:
        rows = [p for p in rows if p.id in matches and matches[p.id].verdict == verdict]
    rows = rows[:limit]
    if cut and not include_cut:
        typer.echo(f"({len(cut)} postings below the fit cut-off are left out; --include-cut lists them.)", err=True)
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
                ids=[p.id for p in to_read],
            )
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
    posting_ids: list[str] = typer.Argument(None, help="Fit-score just these postings (always, even if scored before)."),
    rescore: bool = typer.Option(False, "--rescore", help="Fit-score the shortlist again, even if scored before."),
    show: int = typer.Option(None, "--show", "-n", min=1, help="Rows to print (default: shortlist size)."),
) -> None:
    """Rank eligible postings: local embedding pre-rank, then fit scores for the shortlist.

    Run `autotailor screen` first; only postings that pass eligibility are ranked. With posting
    IDs, those screened postings are fit-scored whatever their verdict, and kept even if they
    score below the cut-off this time (the next fetch or rank applies it).
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
    chosen = [i.strip() for i in (posting_ids or []) if i.strip()]
    if chosen:
        candidates = []
        for pid in chosen:
            p = db.get_posting(conn, pid)
            if p is None or not p.description:
                typer.secho(f"{pid}: not found or has no description; skipped.", fg=typer.colors.YELLOW, err=True)
                continue
            if pid not in matches:
                typer.secho(f"{pid}: not screened yet; run `autotailor screen {pid}` first.", fg=typer.colors.YELLOW, err=True)
                continue
            candidates.append(p)
        if not candidates:
            _fail("None of those postings can be scored.")
    else:
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
    to_score = candidates if chosen else [p for p in shortlisted if rescore or matches[p.id].fit is None]
    if to_score:
        score_spec = settings.models.for_step("score")
        typer.echo(f"Fit-scoring {len(to_score)} postings with {describe(score_spec)} ...", err=True)
        try:
            results = score_fit_batch(
                [matching.posting_text(p, 12000) for p in to_score],
                matching.profile_text(cv, settings.profile),
                settings,
                max_concurrency=1 if score_spec.is_local else 4,
                ids=[p.id for p in to_score],
            )
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

    if chosen:
        for pid in chosen:
            m = db.get_match(conn, pid)
            if m and m.fit_score is not None:
                typer.echo(f"{pid}: fit {m.fit_score:.2f}")
    else:
        _prune(conn, settings, compact=True)
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


# --- Runway mail (M7) ------------------------------------------------------


def _mail_summary(result) -> None:  # noqa: ANN001
    typer.echo(result.summary())
    if result.new:
        typer.echo("Next: `autotailor runway resolve` finds each new role's posting on the employer's site.")


@mail_app.command("import")
def mail_import(
    files: list[Path] = typer.Argument(None, help=".eml files. Default: everything in data/inbox/."),
) -> None:
    """Import Runway emails saved as .eml files (the drop folder, or files you name).

    In Gmail: open the email, "⋮" > "Download message", and save it into data/inbox/.
    Files from the drop folder move to data/inbox/done/ once imported.
    """
    from autotailor import mail

    settings = _settings()
    paths = get_paths()
    conn = db.connect(paths.db)
    try:
        if files:
            total = mail.MailResult()
            for f in files:
                total.add(mail.import_file(conn, f, settings))
        else:
            paths.inbox.mkdir(parents=True, exist_ok=True)
            total = mail.import_folder(conn, paths.inbox, settings)
            if not total.messages and not total.ignored:
                typer.echo(f"No .eml files in {paths.inbox}.")
                return
    finally:
        conn.close()
    _mail_summary(total)


@mail_app.command("pull")
def mail_pull(days: int = typer.Option(None, "--days", help="Look back this many days (default mail.imap_days).")) -> None:
    """Pull Runway emails from a Gmail label over IMAP (read-only; nothing is marked read).

    Setup: a Gmail filter that labels Runway mail, an app password
    (Google Account > Security > App passwords), then
    `autotailor config set mail.imap true`, `config set mail.imap_user you@gmail.com`,
    `config set mail.imap_label Runway`, and `autotailor mail login`.
    """
    from autotailor import mail

    settings = _settings()
    paths = get_paths()
    m = settings.mail
    if not m.imap_user:
        _fail("Set mail.imap_user first: autotailor config set mail.imap_user you@gmail.com")
    password = mail.imap_password(paths.env)
    if not password:
        _fail(f"No IMAP app password. Run `autotailor mail login`, or put {mail.IMAP_PASSWORD_ENV}=... in .env.")
    typer.echo(f"Checking '{m.imap_label}' for {mail.mask(m.imap_user)} on {m.imap_host} ...", err=True)
    try:
        raws = mail.fetch_imap(m, password, days or m.imap_days)
    except Exception as e:  # imaplib.IMAP4.error, socket errors, missing label
        _fail(f"IMAP failed: {e}")
        return
    conn = db.connect(paths.db)
    total = mail.MailResult()
    for raw in raws:
        total.add(mail.import_raw(conn, raw, "imap", settings))
    conn.close()
    _mail_summary(total)


@mail_app.command("login")
def mail_login() -> None:
    """Store the IMAP app password in the OS keyring (or explain the .env fallback)."""
    from autotailor import mail

    password = typer.prompt("Gmail app password", hide_input=True)
    try:
        mail.store_password_in_keyring(password)
    except Exception as e:  # no keyring backend (WSL, headless Linux)
        _fail(
            f"No usable OS keyring here ({type(e).__name__}). Put this line in .env instead "
            f"(it is git-ignored):\n  {mail.IMAP_PASSWORD_ENV}=<the app password>"
        )
    typer.echo("Saved in the OS keyring.")


@mail_app.command("listen")
def mail_listen(port: int = typer.Option(None, "--port", "-p", help="Default mail.smtp_port.")) -> None:
    """Run a mail server on 127.0.0.1 that accepts forwarded Runway emails.

    In a desktop mail client (Thunderbird, Outlook), add an outgoing server
    127.0.0.1:<port>, no authentication, and forward Runway emails through it to
    any address (e.g. inbox@autotailor.local). Only Runway emails are accepted.
    """
    import threading

    from autotailor import mail

    settings = _settings()
    paths = get_paths()
    port = port or settings.mail.smtp_port
    lock = threading.Lock()

    def on_saved(path: Path) -> None:
        with lock:
            conn = db.connect(paths.db)
            try:
                result = mail.import_file(conn, path, settings, origin="smtp")
            finally:
                conn.close()
            done = paths.inbox / "done"
            done.mkdir(parents=True, exist_ok=True)
            path.rename(done / path.name)
            typer.echo(f"Received: {result.summary()}")

    controller = mail.smtp_controller(paths.inbox, settings.mail.allowed_senders, port, on_saved)
    controller.start()
    typer.echo(f"Listening on 127.0.0.1:{port} for forwarded Runway emails. Ctrl-C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        controller.stop()


@runway_app.command("list")
def runway_list(status: str = typer.Option(None, "--status", "-s", help="open, resolved or skipped.")) -> None:
    """Runway roles from your emails, and whether each has been matched to the employer's posting."""
    conn = db.connect(get_paths().db)
    rows = db.list_runway_roles(conn, status)
    conn.close()
    if not rows:
        typer.echo("No Runway roles yet. Save a Runway email into data/inbox/ and run `autotailor mail import`.")
        return
    colors = {"open": typer.colors.YELLOW, "resolved": typer.colors.GREEN, "skipped": None}
    for r in rows:
        score = f"{r['match_score']:.0%}" if r["match_score"] is not None else "  -"
        st = typer.style(f"{r['status']:<8}", fg=colors.get(r["status"]))
        typer.echo(f"{st} {score:>4}  {r['company']} | {r['title']}  -> {r['posting_id'] or '-'}")


def _ask_in_terminal(question: dict) -> dict | None:
    """Show a resolve question; return an answer, or None to leave it for later."""
    typer.echo("")
    typer.secho(f"{question['company']} | {question['title']}" + (f" ({question['location']})" if question.get("location") else ""), bold=True)
    if question.get("runway_url"):
        typer.echo(f"  Runway: {question['runway_url']}")
    if question.get("error"):
        typer.secho(f"  {question['error']}", fg=typer.colors.RED)
    typer.echo("  Paste the employer's posting URL, 'f <file>' for a file with the posting text, "
               "'s' to skip this role, or Enter to leave it for later.")  # fmt: skip
    reply = typer.prompt("  ", default="", show_default=False).strip()
    if not reply:
        return None
    if reply.lower() in ("s", "skip"):
        return {"skip": True}
    if reply.lower().startswith("f "):
        path = Path(reply[2:].strip()).expanduser()
        try:
            return {"text": path.read_text(encoding="utf-8")}
        except OSError as e:
            typer.secho(f"  Can't read {path}: {e}", fg=typer.colors.RED)
            return _ask_in_terminal(question)
    return {"url": reply}


@runway_app.command("resolve")
def runway_resolve(
    auto: bool = typer.Option(False, "--auto", help="Only match automatically; leave the rest waiting, don't ask."),
) -> None:
    """Find each open Runway role's posting on the employer's site.

    Tries the company's job board first. Roles it can't find wait for you:
    without --auto you are asked now; otherwise answer later here or in the UI
    (`autotailor ui`, Runway page). Answers are kept across restarts.
    """
    from autotailor import batch, resolve
    from autotailor.chains.resolve_graph import build_resolve_graph, run_resolve
    from autotailor.sources import make_client

    settings = _settings()
    paths = get_paths()
    conn = db.connect(paths.db)
    pending = resolve.open_roles(conn)
    if not pending:
        conn.close()
        typer.echo("No open Runway roles.")
        return
    counts = {"resolved": 0, "skipped": 0, "waiting": 0}
    with batch.open_checkpointer(paths.checkpoints) as saver, make_client() as client:
        resolver = resolve.Resolver(conn, client, settings)
        graph = build_resolve_graph(resolver, saver)
        typer.echo(f"Resolving {len(pending)} Runway roles ...", err=True)
        from autotailor.tasks import report_progress

        from autotailor.ailog import current_item

        for i, pid in enumerate(pending):
            report_progress(i, len(pending), f"{i} of {len(pending)} roles checked")
            current_item.set(pid)
            p = db.get_posting(conn, pid)
            label = f"{p.company} | {p.title}" if p else pid
            if p is None or p.description:
                # Already filled in (e.g. a later board fetch merged into it).
                ok = resolver.auto_match(pid)
                counts["resolved" if ok else "waiting"] += 1
                continue
            out = run_resolve(graph, pid)
            while out.status == "waiting" and not auto:
                answer = _ask_in_terminal(out.question)
                if answer is None:
                    break
                out = run_resolve(graph, pid, answer)
            counts[out.status] += 1
            if out.status == "resolved":
                typer.secho(f"  found: {label}", fg=typer.colors.GREEN, err=True)
    conn.close()
    typer.echo(f"{counts['resolved']} resolved, {counts['skipped']} skipped, {counts['waiting']} waiting for a link.")
    if counts["waiting"]:
        typer.echo("Answer the waiting ones with `autotailor runway resolve`, or on the Runway page of `autotailor ui`.")


# --- short answers (M8) ------------------------------------------------------


@answers_app.command("add")
def answers_add(
    posting_id: str = typer.Argument(..., help="The posting the questions belong to (see `jobs list`)."),
    question: str = typer.Argument(None, help="The question, with its limit if any, e.g. 'Why us? (150 words)'."),
    file: Path = typer.Option(None, "--file", "-f", help="Several questions, separated by blank lines."),
    words: int = typer.Option(None, "--words", "-w", help="Word limit, if the question text doesn't say."),
) -> None:
    """Add short-answer questions from an application."""
    from autotailor.answers import parse_question, split_questions

    texts = split_questions(file.read_text(encoding="utf-8")) if file else ([question] if question else [])
    if not texts:
        _fail("Give a question, or --file with questions separated by blank lines.")
    conn = db.connect(get_paths().db)
    if db.get_posting(conn, posting_id) is None:
        conn.close()
        _fail(f"No posting {posting_id}. See `autotailor jobs list`.")
    for t in texts:
        text, limit = parse_question(t)
        qid = db.add_question(conn, posting_id, text, words or limit)
        typer.echo(f"{qid:>4}  {text}" + (f"  [{words or limit} words]" if words or limit else ""))
    conn.close()
    typer.echo("Next: `autotailor answers run` suggests points for each, and drafts the ones you choose.")


@answers_app.command("list")
def answers_list(posting_id: str = typer.Argument(None, help="Only this posting.")) -> None:
    """Questions and where each one stands."""
    conn = db.connect(get_paths().db)
    rows = db.list_questions(conn, posting_id)
    if not rows:
        conn.close()
        typer.echo("No questions yet. Add one with `autotailor answers add <posting id> \"...\"`.")
        return
    current = None
    for q in rows:
        if q["posting_id"] != current:
            current = q["posting_id"]
            p = db.get_posting(conn, current)
            typer.secho(f"\n{p.company} | {p.title}" if p else f"\n{current}", bold=True)
        typer.echo(f"{q['id']:>4}  {q['status']:<12} {q['text'][:90]}")
    conn.close()


def _choose_in_terminal(payload: dict) -> dict:
    """Show the suggested points; ask whether to draft, which points, and any notes."""
    typer.echo("")
    typer.secho(f"{payload['company']} | {payload['title']}", bold=True)
    limit = f"  ({payload['word_limit']} words)" if payload.get("word_limit") else ""
    typer.secho(f"Q: {payload['question']}{limit}", fg=typer.colors.CYAN)
    typer.echo("Points to draw on:")
    for i, pt in enumerate(payload["points"], 1):
        typer.echo(f"  {i}. {pt['point']}  [{pt['source_id']}]")
        typer.echo(typer.style(f"     why: {pt['why']}", dim=True))
    if not typer.confirm("Draft an answer from these?", default=False):
        return {"draft": False}
    raw = typer.prompt("Which points (e.g. 1,3; Enter for all)", default="", show_default=False)
    use = [int(x) for x in raw.replace(" ", "").split(",") if x.isdigit() and 1 <= int(x) <= len(payload["points"])]
    notes = typer.prompt("Anything to add or stress (Enter to skip)", default="", show_default=False)
    return {"draft": True, "use": use or None, "notes": notes}


@answers_app.command("run")
def answers_run(
    question_ids: list[int] = typer.Argument(None, help="Only these questions. Default: all not yet done."),
    posting: str = typer.Option(None, "--posting", "-p", help="Only this posting's questions."),
    redo: bool = typer.Option(False, "--redo", help="Start the named questions over (new points, new draft)."),
    no_ask: bool = typer.Option(False, "--no-ask", help="Only suggest points; decide later (UI or another run)."),
) -> None:
    """Suggest points for each question; draft and humanize the ones you opt in to.

    Drafts are checked against your CV (no new numbers or names), the word
    limit, and stock AI phrasing. Results go to data/outputs/answers/.
    """
    from autotailor import batch
    from autotailor.answers import AnswerTools, save_outcome, write_answers
    from autotailor.chains.answer_graph import build_answer_graph, run_answer, thread_id

    settings = _settings()
    paths = get_paths()
    cv = _load_cv_or_fail()
    conn = db.connect(paths.db)
    rows = [db.get_question(conn, i) for i in question_ids] if question_ids else db.list_questions(conn, posting)
    rows = [r for r in rows if r is not None and (redo or question_ids or r["status"] in ("new", "waiting"))]
    if posting:
        rows = [r for r in rows if r["posting_id"] == posting]
    if not rows:
        conn.close()
        typer.echo("No questions to work on. `autotailor answers list` shows them all.")
        return
    tools = AnswerTools(conn, cv, settings)
    spec = settings.models.for_step("tailor")
    model = f"{spec.provider}:{spec.model}"
    touched: set[str] = set()
    with batch.open_checkpointer(paths.checkpoints) as saver:
        from autotailor.llm import get_chat_model

        graph = build_answer_graph(tools, get_chat_model(settings, "tailor"), get_chat_model(settings, "humanize"),
                                   saver, settings.tailor.max_attempts)  # fmt: skip
        for q in rows:
            qid = q["id"]
            touched.add(q["posting_id"])
            from autotailor.ailog import current_item

            current_item.set(f"question:{qid}")  # labels this question's model calls in the AI log
            if redo:
                saver.delete_thread(thread_id(qid))
            try:
                out = run_answer(graph, qid)
                if out.status == "waiting" and no_ask:
                    save_outcome(conn, qid, out, model)
                    typer.echo(f"{qid}: {len(out.payload['points'])} points suggested; waiting for your decision.")
                    continue
                if out.status == "waiting":
                    save_outcome(conn, qid, out, model)
                    decision = _choose_in_terminal(out.payload)
                    if decision["draft"]:
                        typer.echo("Drafting and humanizing ...", err=True)
                    out = run_answer(graph, qid, decision)
            except Exception as e:  # model errors, missing keys
                # A thread that crashed part way has neither a question nor an outcome; start it over next time.
                saver.delete_thread(thread_id(qid))
                db.update_question(conn, qid, status="new")
                typer.secho(f"{qid}: failed: {type(e).__name__}: {e}", fg=typer.colors.RED, err=True)
                continue
            save_outcome(conn, qid, out, model)
            if out.status in ("drafted", "needs_review"):
                typer.echo("")
                typer.echo(out.payload["text"])
                if out.payload["problems"]:
                    typer.secho("Still to fix by hand:", fg=typer.colors.YELLOW)
                    for x in out.payload["problems"]:
                        typer.secho(f"  - {x}", fg=typer.colors.YELLOW)
    for pid in touched:
        path = write_answers(conn, pid, paths.outputs)
        typer.echo(f"\nSaved: {path}")
    conn.close()


@answers_app.command("decide")
def answers_decide(
    question_id: int = typer.Argument(...),
    draft: bool = typer.Option(True, "--draft/--points-only", help="Draft an answer, or keep the suggested points."),
    use: str = typer.Option("", "--use", help="Point numbers to draft from, e.g. 1,3 (default: all)."),
    notes: str = typer.Option("", "--notes", help="Anything to add or stress."),
) -> None:
    """Answer a waiting question's opt-in without prompts (used by the UI and the API)."""
    from autotailor import batch
    from autotailor.ailog import current_item
    from autotailor.answers import AnswerTools, save_outcome, write_answers
    from autotailor.chains.answer_graph import build_answer_graph, run_answer
    from autotailor.llm import get_chat_model

    settings = _settings()
    paths = get_paths()
    cv = _load_cv_or_fail()
    conn = db.connect(paths.db)
    q = db.get_question(conn, question_id)
    if q is None:
        conn.close()
        _fail(f"No question {question_id}.")
    spec = settings.models.for_step("tailor")
    model = f"{spec.provider}:{spec.model}"
    current_item.set(f"question:{question_id}")
    with batch.open_checkpointer(paths.checkpoints) as saver:
        graph = build_answer_graph(AnswerTools(conn, cv, settings), get_chat_model(settings, "tailor"),
                                   get_chat_model(settings, "humanize"), saver, settings.tailor.max_attempts)  # fmt: skip
        out = run_answer(graph, question_id)
        if out.status != "waiting":
            conn.close()
            _fail(f"Question {question_id} isn't waiting for a decision (it is {out.status}).")
        nums = [int(x) for x in use.replace(" ", "").split(",") if x.isdigit()]
        decision = {"draft": draft, "use": nums or None, "notes": notes}
        if draft:
            typer.echo("Drafting and humanizing ...", err=True)
        out = run_answer(graph, question_id, decision)
    save_outcome(conn, question_id, out, model)
    path = write_answers(conn, q["posting_id"], paths.outputs)
    conn.close()
    if out.status in ("drafted", "needs_review"):
        typer.echo(out.payload["text"])
        for x in out.payload["problems"]:
            typer.secho(f"  - {x}", fg=typer.colors.YELLOW)
    typer.echo(f"{out.status}. Saved: {path}")


@runway_app.command("answer")
def runway_answer(
    posting_id: str = typer.Argument(..., help="The Runway role's posting ID (see `runway list`)."),
    url: str = typer.Option(None, "--url", help="The employer's posting URL."),
    text: str = typer.Option(None, "--text", help="The posting text."),
    skip: bool = typer.Option(False, "--skip", help="Drop this role."),
) -> None:
    """Answer a waiting Runway role without prompts (used by the UI and the API)."""
    from autotailor import batch, resolve
    from autotailor.ailog import current_item
    from autotailor.chains.resolve_graph import build_resolve_graph, run_resolve
    from autotailor.sources import make_client

    if sum(bool(x) for x in (url, text, skip)) != 1:
        _fail("Give exactly one of --url, --text or --skip.")
    answer = {"skip": True} if skip else {"url": url} if url else {"text": text}
    settings = _settings()
    paths = get_paths()
    conn = db.connect(paths.db)
    current_item.set(posting_id)
    with batch.open_checkpointer(paths.checkpoints) as saver, make_client() as client:
        graph = build_resolve_graph(resolve.Resolver(conn, client, settings), saver)
        out = run_resolve(graph, posting_id)
        if out.status == "waiting":
            out = run_resolve(graph, posting_id, answer)
    conn.close()
    if out.status == "waiting":
        _fail(out.question.get("error") or "Still not found.")
    typer.echo(f"{posting_id}: {out.status}.")


@answers_app.command("show")
def answers_show(posting_id: str = typer.Argument(..., help="Posting whose questions to show.")) -> None:
    """Print a posting's questions with their points and drafts."""
    from autotailor.answers import answers_markdown

    conn = db.connect(get_paths().db)
    typer.echo(answers_markdown(conn, posting_id))
    conn.close()


@answers_app.command("remove")
def answers_remove(question_id: int = typer.Argument(...)) -> None:
    """Delete a question."""
    conn = db.connect(get_paths().db)
    db.delete_question(conn, question_id)
    conn.close()
    typer.echo(f"Removed question {question_id}.")


# --- models and comparisons (M9) ------------------------------------------------


@app.command("models")
def models_cmd() -> None:
    """Which model each step uses, and which Ollama models are pulled (with sizes)."""
    settings = _settings()
    m = settings.models
    used: dict[str, list[str]] = {}
    for step in STEPS:
        spec = m.for_step(step)
        used.setdefault(f"{spec.provider}:{spec.model}", []).append(step)
    used.setdefault(f"{m.embeddings.provider}:{m.embeddings.model}", []).append("embeddings")
    try:
        tags = httpx.get(f"{m.ollama_base_url.rstrip('/')}/api/tags", timeout=5).json()["models"]
    except (httpx.HTTPError, KeyError, ValueError):
        tags = None
    pulled = {t["name"]: t for t in tags or []}
    pulled.update({k.removesuffix(":latest"): v for k, v in list(pulled.items())})
    typer.secho("In use:", bold=True)
    for name, steps in used.items():
        provider, _, model = name.partition(":")
        if provider == "ollama":
            t = pulled.get(model)
            state = (f"pulled, {t['size'] / 1e9:.1f} GB" if t else typer.style(f"NOT PULLED: ollama pull {model}", fg=typer.colors.RED)) \
                if tags is not None else "Ollama not reachable"
        else:
            state = typer.style("cloud: sends data off this machine", fg=typer.colors.YELLOW)
        typer.echo(f"  {name:<40} {', '.join(steps):<28} {state}")
    if tags:
        typer.secho("Pulled in Ollama:", bold=True)
        for t in tags:
            d = t.get("details", {})
            typer.echo(f"  {t['name']:<40} {t['size'] / 1e9:5.1f} GB  {d.get('parameter_size', '')} {d.get('quantization_level', '')}")


def _compare_items(conn, settings: Settings, step: str, ids: list[str] | None, n: int):  # noqa: ANN202, ANN001
    from autotailor import matching

    if step == "answers":
        if ids:
            return [int(i) for i in ids]
        qs = [q for q in db.list_questions(conn) if q["points_json"] or q["answer"]]
        return [q["id"] for q in qs][:n]
    if ids:
        return [p for i in ids if (p := db.get_posting(conn, i)) and p.description]
    if step == "tailor":
        tailored = {a["posting_id"] for r in db.list_runs(conn, None) for a in db.run_artifacts(conn, r["id"])}
        rows = [p for p, _ in matching.ranked(conn, settings) if p.id in tailored] or [p for p, _ in matching.shortlist(conn, settings)]
        return rows[:n]
    if step == "score":
        return [p for p, m in matching.ranked(conn, settings) if m.fit_score is not None][:n]
    with_reqs = [p for p in db.list_postings(conn, None) if p.description and p.requirements is not None]
    return (with_reqs or [p for p in db.list_postings(conn, None) if p.description])[:n]


@app.command()
def compare(
    step: str = typer.Argument(..., help="extract, score, tailor or answers."),
    model: list[str] = typer.Option(None, "--model", "-m", help="provider:model, e.g. ollama:gemma4:12b. Repeatable."),
    current: bool = typer.Option(True, "--current/--no-current", help="Include the model the step uses now."),
    n: int = typer.Option(5, "--n", "-n", min=1, help="How many postings (or questions) to try."),
    ids: list[str] = typer.Option(None, "--id", help="Specific posting (or question) IDs. Repeatable."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before sending data to a cloud model."),
) -> None:
    """Rerun one step on saved postings with several models, side by side. Nothing stored changes.

    Example: autotailor compare tailor -m ollama:gemma4:12b -n 3
    Writes a report to data/comparisons/ (also on the Compare page of `autotailor ui`).
    """
    from datetime import datetime

    from autotailor import compare as cmp

    if step not in ("extract", "score", "tailor", "answers"):
        _fail("Step must be one of: extract, score, tailor, answers.")
    settings = _settings()
    paths = get_paths()
    base = settings.models.for_step(cmp.STEP_OF[step])
    try:
        specs = ([base] if current else []) + [cmp.parse_model(x, base) for x in model or []]
    except ValueError as e:
        _fail(str(e))
        return
    specs = list({cmp.label(s): s for s in specs}.values())
    if len(specs) < 2:
        _fail("Name at least one other model to compare with, e.g. -m ollama:gemma4:12b.")
    if missing := cmp.missing_ollama_models(specs, settings.models.ollama_base_url):
        _fail("Not pulled in Ollama: " + ", ".join(missing) + "\n" + "\n".join(f"  ollama pull {x}" for x in missing))
    cloud = [cmp.label(s) for s in specs if not s.is_local]
    if cloud and not yes and not typer.confirm(f"{', '.join(cloud)} will receive posting and CV text. Continue?"):
        raise typer.Exit(1)

    cv = _load_cv_or_fail()
    conn = db.connect(paths.db)
    items = _compare_items(conn, settings, step, ids, n)
    if not items:
        conn.close()
        _fail({"tailor": "No tailored or shortlisted postings yet: run `autotailor rank` first.",
               "score": "No fit-scored postings yet: run `autotailor rank` first.",
               "answers": "No questions with points yet: `autotailor answers add` and `answers run`.",
               "extract": "No postings with a description yet."}[step])  # fmt: skip
    typer.echo(f"Comparing {step} on {len(items)} item(s) with: {', '.join(cmp.label(s) for s in specs)} ...", err=True)
    started = datetime.now()
    try:
        if step == "extract":
            trials = cmp.compare_extract(conn, settings, specs, items)
        elif step == "score":
            trials = cmp.compare_score(conn, cv, settings, specs, items)
        elif step == "tailor":
            trials = cmp.compare_tailor(conn, cv, settings, specs, items, paths.outputs)
        else:
            trials = cmp.compare_answers(conn, cv, settings, specs, items)
    finally:
        conn.close()
    for row in cmp.summarize(step, trials):  # type: ignore[arg-type]
        typer.echo("  " + "  ".join(f"{k.replace('_', ' ')}: {v}" for k, v in row.items() if v is not None))
    path = cmp.write_report(paths.comparisons, step, trials, started)  # type: ignore[arg-type]
    typer.echo(f"Report: {path}")


# --- tailoring (M4) ---------------------------------------------------------


def _tailor_targets(conn, settings: Settings, posting_ids: list[str] | None, force: bool) -> list:
    from autotailor import matching
    from autotailor.models import ArtifactKind

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
        done = {
            p.id for p, _ in targets
            if any(a["kind"] == ArtifactKind.RESUME_PDF for a in db.artifacts_for(conn, p.id))
        }  # fmt: skip
        if done:
            typer.echo(f"Skipping {len(done)} already tailored (use --force to redo).", err=True)
        targets = [(p, m) for p, m in targets if p.id not in done]
    return targets


def _execute_run(conn, settings: Settings, cv, run_id: int, jobs: list | None) -> None:
    """Run (or resume) the batch graph for one run, then write its outputs and report."""
    import threading

    from autotailor import batch
    from autotailor import tasks as tasks_mod
    from autotailor.chains.batch_graph import build_batch_graph, run_batch

    paths = get_paths()
    spec = settings.models.for_step("tailor")
    model = f"{spec.provider}:{spec.model}"
    cancel = threading.Event()
    parallel = settings.run.parallel_jobs
    with batch.open_checkpointer(paths.checkpoints) as saver:
        if jobs is None and not batch.has_checkpoint(saver, run_id):
            db.finish_run(conn, run_id, "failed", {})
            _fail(f"Run {run_id} has nothing saved to resume from. Start a new one with `autotailor tailor`.")
        work = batch.make_worker(cv, settings, cancel)
        if tasks_mod.current_task_id() is not None:  # running as a UI task: report each finished job
            total = len(jobs) if jobs is not None else None
            finished = [0]
            inner = work

            def work(job, inner=inner):  # noqa: ANN001, ANN202
                result = inner(job)
                finished[0] += 1
                tasks_mod.report_progress(finished[0], total, f"{finished[0]} resume(s) done")
                return result

        graph = build_batch_graph(work, saver)
        try:
            with batch.interrupt_guard(cancel, parallel):
                results = run_batch(graph, jobs, batch.thread_id(run_id), parallel)
        except (KeyboardInterrupt, batch.Cancelled):
            db.set_run_status(conn, run_id, "interrupted")
            typer.secho(
                f"\nRun {run_id} stopped. Finished resumes are saved; `autotailor resume` continues it.",
                fg=typer.colors.YELLOW,
                err=True,
            )
            raise typer.Exit(130) from None
        except Exception as e:
            db.set_run_status(conn, run_id, "interrupted")
            _fail(f"Run {run_id} stopped: {type(e).__name__}: {e}\n`autotailor resume` continues it.")
            return

    run = db.get_run(conn, run_id)
    folder = batch.run_folder(paths.outputs, run)
    rows = batch.save_results(conn, run_id, folder, paths.outputs, results, cv, settings, model)
    report = batch.write_report(folder, run, model, rows)
    counts = batch.counts(rows)
    db.finish_run(conn, run_id, "done", counts)

    for r in rows:
        name = f"{r.posting.company} | {r.posting.title}" if r.posting else r.result["posting_id"]
        label = f"{r.index:>2}. {name}"
        if r.result["status"] == "tailored" and r.files:
            n = r.result["attempts"]
            pages = "" if r.pages <= 1 else typer.style(f"  ({r.pages} pages: lower tailor.max_bullets?)", fg=typer.colors.YELLOW)
            typer.echo(f"{label}  ({n} draft{'s' if n != 1 else ''}){pages}")
            if r.warning:
                typer.secho(f"      {r.warning}", fg=typer.colors.YELLOW, err=True)
        else:
            typer.secho(f"{label}: no resume written: {r.result['message']}", fg=typer.colors.RED, err=True)
            for err in r.result["errors"][:5]:
                typer.secho(f"      - {err}", fg=typer.colors.RED, err=True)
    typer.echo(f"\n{counts['tailored']} tailored, {counts['failed']} failed. Report: {report}")


def _load_cv_or_fail():
    try:
        return load_master_cv(get_paths().master_cv)
    except (OSError, ValidationError, ValueError) as e:
        _fail(f"Cannot read the master CV: {e}")


@app.command()
def tailor(
    posting_ids: list[str] = typer.Argument(None, help="Only these postings. Default: the shortlist."),
    force: bool = typer.Option(False, "--force", help="Tailor again even if a resume already exists."),
    ignore_limits: bool = typer.Option(
        False, "--ignore-limits", help="Run even if run.max_runs_per_day/week is reached."
    ),
) -> None:
    """Tailor a resume (PDF, Word) for each shortlisted role, in parallel, plus a run report.

    Each resume is drafted by the tailoring graph and must pass verification
    against the master CV; one that still fails after the allowed attempts is
    reported, not written. Ctrl-C stops the run; `autotailor resume` continues it.
    """
    from autotailor import batch

    settings = _settings()
    cv = _load_cv_or_fail()
    conn = db.connect(get_paths().db)
    if not ignore_limits and (why := batch.limit_message(conn, settings.run)):
        conn.close()
        _fail(f"Run limit reached: {why} Use --ignore-limits, or raise the limit with `autotailor config set`.")
    unfinished = db.unfinished_runs(conn)
    if unfinished:
        ids = ", ".join(str(r["id"]) for r in unfinished)
        typer.secho(f"Unfinished run(s) {ids}: `autotailor resume` continues the latest.", fg=typer.colors.YELLOW, err=True)
    targets = _tailor_targets(conn, settings, posting_ids, force)
    if not targets:
        conn.close()
        typer.echo("Nothing to tailor. Run `autotailor rank` (see `autotailor shortlist`), or pass posting IDs.")
        return

    jobs = batch.make_jobs(targets, cv, settings)
    run_id = db.start_run(conn, settings.model_dump_json())
    spec = settings.models.for_step("tailor")
    typer.echo(
        f"Run {run_id}: tailoring {len(jobs)} resumes with {describe(spec)}, "
        f"{settings.run.parallel_jobs} at a time ...",
        err=True,
    )
    try:
        _execute_run(conn, settings, cv, run_id, jobs)
    finally:
        conn.close()


@app.command()
def resume(
    run_id: int = typer.Argument(None, help="Run to continue. Default: the latest unfinished run."),
) -> None:
    """Continue a run that was stopped part way; finished resumes are not redone."""
    settings = _settings()
    cv = _load_cv_or_fail()
    conn = db.connect(get_paths().db)
    if run_id is None:
        unfinished = db.unfinished_runs(conn)
        if not unfinished:
            conn.close()
            typer.echo("No unfinished runs.")
            return
        run_id = unfinished[0]["id"]
    run = db.get_run(conn, run_id)
    if run is None:
        conn.close()
        _fail(f"No run {run_id}. See `autotailor runs`.")
    typer.echo(f"Resuming run {run_id} (started {run['started_at']}) ...", err=True)
    db.set_run_status(conn, run_id, "running")
    try:
        _execute_run(conn, settings, cv, run_id, None)
    finally:
        conn.close()


@app.command()
def runs(limit: int = typer.Option(20, "--limit", "-n", min=1, help="Most recent runs to show.")) -> None:
    """Show past tailoring runs, with their results and output folders."""
    from datetime import datetime

    from autotailor import batch

    paths = get_paths()
    conn = db.connect(paths.db)
    rows = db.list_runs(conn, limit)
    conn.close()
    if not rows:
        typer.echo("No runs yet. `autotailor tailor` starts one.")
        return
    colors = {"done": typer.colors.GREEN, "interrupted": typer.colors.YELLOW, "running": typer.colors.YELLOW,
              "failed": typer.colors.RED}  # fmt: skip
    for r in rows:
        c = batch.parse_counts(r)
        started = datetime.fromisoformat(r["started_at"]).astimezone()
        summary = f"{c.get('tailored', 0)} tailored, {c.get('failed', 0)} not" if c else ""
        status = typer.style(f"{r['status']:<11}", fg=colors.get(r["status"]))
        folder = batch.run_folder(paths.outputs, r)
        where = str(folder / "report.md") if (folder / "report.md").exists() else ""
        typer.echo(f"{r['id']:>4}  {started:%Y-%m-%d %H:%M}  {status}  {summary:<22}  {where}")


@app.command("run")
def run_all(
    skip_fetch: bool = typer.Option(False, "--skip-fetch", help="Don't pull from the job boards first."),
    ignore_limits: bool = typer.Option(
        False, "--ignore-limits", help="Run even if run.max_runs_per_day/week is reached."
    ),
) -> None:
    """The whole pipeline: Runway mail, fetch, screen, rank, then tailor the top X.

    Stops early if a step has nothing to pass on.
    """
    from autotailor import batch

    settings = _settings()
    conn = db.connect(get_paths().db)
    why = None if ignore_limits else batch.limit_message(conn, settings.run)
    conn.close()
    if why:
        _fail(f"Run limit reached: {why} Use --ignore-limits, or raise the limit with `autotailor config set`.")

    def step(title: str) -> None:
        typer.secho(f"\n== {title}", bold=True)

    m = settings.mail
    if settings.sources.runway_email and (m.drop_folder or m.imap):
        step("Runway mail")
        if m.drop_folder:
            mail_import(files=None)
        if m.imap:
            try:
                mail_pull(days=None)
            except typer.Exit:
                typer.secho("IMAP pull failed; continuing.", fg=typer.colors.YELLOW, err=True)

    if skip_fetch:
        pass
    elif not settings.sources.ats_boards or not _configured_boards(settings):
        typer.echo("No job boards configured; skipping fetch.", err=True)
    else:
        step("Fetch")
        try:
            fetch(board=None, no_filter=False, max_age=None)
        except typer.Exit as e:
            if e.exit_code:
                typer.secho("Every board failed; continuing with stored postings.", fg=typer.colors.YELLOW, err=True)
    conn = db.connect(get_paths().db)
    open_runway = db.list_runway_roles(conn, "open")
    conn.close()
    if open_runway:
        step("Runway roles")
        runway_resolve(auto=True)
    step("Screen")
    screen(posting_ids=None, rejudge=False, reextract=False, limit=50)
    step("Rank")
    rank(posting_ids=None, rescore=False, show=None)
    step("Tailor")
    tailor(posting_ids=None, force=False, ignore_limits=True)


@app.command()
def api(
    port: int = typer.Option(8765, "--port", "-p", help="Port on 127.0.0.1."),
    cors: list[str] = typer.Option(None, "--cors", help="Allow a browser frontend at this origin, e.g. http://localhost:5173."),
) -> None:
    """Run the local HTTP API (for a custom frontend or a browser extension). Docs at /docs."""
    import uvicorn

    from autotailor.api import TOKEN_HEADER, api_token, create_app

    from autotailor import tasks

    paths = get_paths()
    api_token(paths)  # created on first run
    if paths.db.exists():  # tasks still waiting from before a restart get a worker
        conn = db.connect(paths.db)
        try:
            if tasks.ensure_worker_for_queue(conn, paths.home, paths.data):
                typer.echo("Started the background worker for tasks still waiting.")
        finally:
            conn.close()
    typer.echo(f"AutoTailor API: http://127.0.0.1:{port}  (docs: /docs)")
    typer.echo(f"Send the header {TOKEN_HEADER} with the token in {paths.data / 'api_token'}.")
    uvicorn.run(create_app(paths.home, cors or None), host="127.0.0.1", port=port, log_level="warning")


@app.command()
def worker(once: bool = typer.Option(False, "--once", help="Run what's queued, then exit.")) -> None:
    """Run queued background tasks (the UI starts this automatically)."""
    from autotailor import tasks

    paths = get_paths()
    paths.data.mkdir(parents=True, exist_ok=True)
    tasks.pid_file(paths.data).write_text(str(os.getpid()))
    try:
        tasks.worker_loop(paths.home, once=once)
    finally:
        tasks.pid_file(paths.data).unlink(missing_ok=True)


def _web_dir() -> Path:
    """The SvelteKit app (web/ at the repo root)."""
    return Path(__file__).resolve().parents[2] / "web"


def _login_link(paths, port: int, minutes: int = 10) -> str:  # noqa: ANN001
    """Write a one-time sign-in code for the web app (data/ui_login.json) and return its link."""
    import json as _json
    import secrets
    import time

    code = secrets.token_urlsafe(24)
    f = paths.data / "ui_login.json"
    f.write_text(_json.dumps({"code": code, "expires": time.time() + minutes * 60}))
    f.chmod(0o600)
    return f"http://127.0.0.1:{port}/login?code={code}"


def _wait_for(url: str, seconds: float = 30) -> bool:
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1)
            return True
        except httpx.HTTPError:
            time.sleep(0.3)
    return False


@app.command()
def ui(
    port: int = typer.Option(8501, "--port", "-p", help="Port for the web app on 127.0.0.1."),
    api_port: int = typer.Option(8765, "--api-port", help="Port for the local API on 127.0.0.1."),
    classic: bool = typer.Option(False, "--classic", help="The previous Streamlit UI instead."),
    browser: bool = typer.Option(True, "--browser/--no-browser", help="Open the sign-in link in a browser."),
) -> None:
    """Open the local web app (and the API it uses). Ctrl-C stops both.

    Both listen on 127.0.0.1 only. The app signs you in with a one-time link
    printed here; the API token never reaches the browser.
    """
    import shutil
    import subprocess

    paths = get_paths()
    if not paths.config.exists():  # first run: set up the files the app needs
        init()
    env = {**os.environ, "AUTOTAILOR_HOME": str(paths.home)}
    if classic:
        app_path = resources.files("autotailor.ui") / "app.py"
        typer.echo(f"AutoTailor (classic): http://127.0.0.1:{port}  (Ctrl-C to stop)")
        cmd = [
            sys.executable, "-m", "streamlit", "run", str(app_path),
            "--server.address", "127.0.0.1", "--server.port", str(port), "--server.headless", "true",
            "--browser.gatherUsageStats", "false", "--browser.serverAddress", "127.0.0.1",
            "--client.toolbarMode", "minimal", "--global.showWarningOnDirectExecution", "false",
        ]  # fmt: skip
        try:
            raise typer.Exit(subprocess.call(cmd, env=env))
        except KeyboardInterrupt:
            raise typer.Exit(0) from None

    node = shutil.which("node")
    build = _web_dir() / "build" / "index.js"
    if node is None:
        _fail("The web app needs Node.js 20.19 or later (https://nodejs.org). Or use `autotailor ui --classic`.")
    if not build.exists():
        _fail(f"The web app isn't built yet. Run:\n  cd {_web_dir()} && npm install && npm run build\n"
              "Or use `autotailor ui --classic`.")  # fmt: skip
    from autotailor.api import api_token
    from autotailor.uilog import UiLog

    paths.data.mkdir(parents=True, exist_ok=True)
    api_token(paths)
    api_url = f"http://127.0.0.1:{api_port}"
    # Everything the servers print also goes to data/ui.log, so a stop leaves a record
    # even when the terminal that started them is gone.
    log = UiLog(paths.data / "ui.log")
    log.write("ui", f"starting (pid {os.getpid()}): web on port {port}, API on port {api_port}")
    log.watch_console()
    piped = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
             "encoding": "utf-8", "errors": "replace", "bufsize": 1}  # fmt: skip
    procs = {
        "api": subprocess.Popen(
            [sys.executable, "-m", "autotailor.cli", "api", "--port", str(api_port)],
            # Unbuffered, or a piped Python child holds its output (and its last words) back.
            env={**env, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}, **piped,
        ),
        "web": subprocess.Popen(
            [node, str(build)],
            env={**env, "HOST": "127.0.0.1", "PORT": str(port), "ORIGIN": f"http://127.0.0.1:{port}",
                 "AUTOTAILOR_API_URL": api_url, "BODY_SIZE_LIMIT": "50M"},  # fmt: skip
            **piped,
        ),
    }
    for name, p in procs.items():
        log.follow(name, p)
    try:
        if not (_wait_for(f"{api_url}/api/health") and _wait_for(f"http://127.0.0.1:{port}/login")):
            log.write("ui", "the servers didn't start")
            typer.secho(f"The servers didn't start; see the messages above (also in {log.path}).",
                        fg=typer.colors.RED, err=True)  # fmt: skip
            raise typer.Exit(1)
        log.write("ui", "running")
        link = _login_link(paths, port)
        typer.echo("")
        typer.secho("AutoTailor is running.", bold=True)
        typer.echo(f"Open this one-time sign-in link (valid 10 minutes):\n  {link}")
        typer.echo(f"Ctrl-C stops it. Server messages are also saved to {log.path}")
        if browser:
            import webbrowser

            try:
                webbrowser.open(link)
            except Exception:  # no browser available (e.g. a bare WSL shell)
                pass
        while all(p.poll() is None for p in procs.values()):
            import time

            time.sleep(0.5)
        for name, p in procs.items():
            if p.poll() is not None:
                log.write("ui", f"{name} server exited with code {p.returncode}; stopping the other")
                typer.secho(f"The {name} server stopped (exit code {p.returncode}); see {log.path}",
                            fg=typer.colors.RED, err=True)  # fmt: skip
        raise typer.Exit(1)
    except KeyboardInterrupt:
        log.write("ui", "Ctrl-C: stopping")
        raise typer.Exit(0) from None
    except BaseException as e:
        if not isinstance(e, typer.Exit):
            log.write("ui", f"launcher error: {type(e).__name__}: {e}")
        raise
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.terminate()
        for name, p in procs.items():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
            log.write("ui", f"{name} server stopped (exit code {p.returncode})")
        log.write("ui", "stopped")
        log.close()


@app.command("ui-login")
def ui_login(port: int = typer.Option(8501, "--port", "-p", help="The web app's port.")) -> None:
    """Print a new one-time sign-in link for the running web app (e.g. for another browser)."""
    typer.echo(_login_link(get_paths(), port))


if __name__ == "__main__":
    app()
