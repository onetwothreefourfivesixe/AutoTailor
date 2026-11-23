# Contributing to AutoTailor

Thanks for helping. This guide covers how to set up a development copy, how the code fits together, the rules every change must keep, and how to propose a change. For what AutoTailor does and how to use it, start with the [README](README.md); the roadmap and design decisions are in [PLAN.md](PLAN.md) and [UI_PLAN.md](UI_PLAN.md).

## Ground rules

AutoTailor handles people's CVs and job applications. These rules hold in every change:

1. **Local first.** User data (profile, CV, postings, outputs, history) stays in the user's folder: `config.toml`, `.env`, `cv/`, `data/`. Never add telemetry, remote storage or a call to a service the user didn't configure.
2. **Never submit anything.** AutoTailor prepares applications; it never sends one, fills in a form, or emails an employer.
3. **No invention.** Tailored resumes and answers may only use facts from the user's CV (and, for answers, their own notes). Every resume bullet traces to a CV bullet ID, and the verifiers (`resume/verify.py`, `answers.check_answer`) must keep rejecting anything new. Don't weaken them to make a model pass.
4. **Respect site terms.** Never fetch LinkedIn, Handshake, Runway, Indeed or Glassdoor automatically (`sources.NO_FETCH_DOMAINS`), and never open links inside Runway emails. Only public job-board APIs (Greenhouse, Lever, Ashby) and pages the user gives us are fetched.
5. **Show where data goes.** Any feature that sends text to a model must use the configured step model (`llm.get_chat_model`), so it is labelled local or cloud and recorded in the AI log.
6. **Secrets stay out of config.** API keys and passwords live in `.env` or the OS keyring, never in `config.toml` (it's printed and saved with every run) and never in logs.
7. **Local servers stay local.** The API and the web app bind to 127.0.0.1, require the token or the session cookie, and reject other `Host` headers. Keep it that way.

## Setting up

You need Python 3.11+, [uv](https://docs.astral.sh/uv/), Node.js 20.19+ (22 LTS recommended), and optionally [Ollama](https://ollama.com) for live tests with local models.

```bash
git clone <your fork> && cd AutoTailor
uv sync                                        # Python dependencies, including test tools
(cd web && npm install && npm run build)       # the web app
uv run autotailor init                         # your own settings, CV template and database
```

Work against a throwaway data folder so you don't touch your real applications: every command honours `AUTOTAILOR_HOME`.

```bash
export AUTOTAILOR_HOME=/tmp/autotailor-dev
uv run autotailor init
```

For web app development, run the API and the Vite dev server side by side (the dev server calls the API from its own server side, so no CORS setup is needed):

```bash
uv run autotailor api                                  # terminal 1
cd web && AUTOTAILOR_HOME=$AUTOTAILOR_HOME npm run dev # terminal 2: http://127.0.0.1:5173
uv run autotailor ui-login --port 5173                 # a sign-in link for the dev server
```

## How the code is organized

The pipeline is: **sources → screen (eligibility) → rank (fit) → tailor (resume) → outputs**, with short answers, Runway intake and application tracking alongside. Everything is reachable three ways that share one code path: the CLI (`cli.py`), background tasks (`tasks.py`, which run CLI commands in a worker) and the local API (`api.py`, `api_more.py`), which the web app calls.

```
src/autotailor/
  cli.py            every command (Typer)
  config.py         settings schema (config.toml), defaults and validation
  paths.py          where files live (AUTOTAILOR_HOME)
  db.py             SQLite storage and schema migrations
  models.py         shared data models (postings, requirements, matches, resumes)
  cv.py             master CV schema
  llm.py            the one place chat models are created (per step)
  ailog.py          local log of every model call (LangChain callback)
  sources/          job sources: ats.py (Greenhouse, Lever, Ashby), paste.py, runway.py (email parser)
  ingest.py, dedupe.py   title filter, duplicate detection, saving
  eligibility.py    requirements vs the user's profile, verdicts
  matching.py       embedding pre-rank, fit score, shortlist
  chains/           LangChain chains and LangGraph graphs
    extract_posting.py, extract_requirements.py, score_fit.py   chains
    tailor_graph.py   draft -> verify -> retry
    batch_graph.py    parallel tailoring with checkpoints
    resolve_graph.py  Runway role -> employer posting, pausing to ask the user
    answer_graph.py   short answers: suggest, opt in, draft, humanize, check
    structured_prompts.json   every prompt's text and few-shot examples
  resume/           verify.py, layout.py, render.py (PDF/Word/previews), latex.py (Tectonic), fill.py (page fill, keyword match), output.py
  batch.py          runs: jobs, limits, outputs, report.md
  mail.py, resolve.py   Runway email intake and matching
  answers.py        short-answer storage, prompts' inputs, the facts-and-style check
  compare.py        model comparisons
  tasks.py          background task queue and worker
  intake.py         previews for pasted and dropped input
  inbox.py          the Needs-you list
  api.py, api_more.py   local HTTP API (FastAPI); api_setup.py has the first-run wizard's routes
  cv_import.py      reading a resume file (PDF, Word, text) into a master-CV draft
  ui/               classic Streamlit UI (`ui --classic`) and shared helpers in logic.py
  templates/        resume templates (Typst, Jake's Resume LaTeX)
web/                the web app: SvelteKit 2 + Svelte 5 + Tailwind 4
  src/routes/       pages (+page.svelte) and their server loads (+page.server.ts)
  src/lib/          components, the browser API client, live events, types
  src/lib/server/   server-only code: config, session, API calls with the token
  src/hooks.server.ts   Host check and sign-in for every request
  e2e/              Playwright end-to-end tests
tests/              Python tests (pytest); fixtures/ holds sample postings, a sample CV and a synthetic Runway email
```

## Running the tests

```bash
uv run pytest                      # all Python tests (about 2 minutes)
uv run pytest tests/test_m12_api.py -q   # one file
cd web && npm run check            # TypeScript and Svelte type checks
cd web && npm test                 # Vitest unit tests
cd web && npm run build && npm run e2e   # Playwright end-to-end (first time: npx playwright install chromium)
```

- Tests never call a real model or the network: models, job boards and IMAP are faked. Tests that call a real model are marked `live` and run only with `AUTOTAILOR_LIVE=1` (they use your configured models, so keep them cheap).
- Python tests render resumes with the Typst template (see `tests/conftest.py`); LaTeX is tested only where Tectonic is installed.
- The end-to-end tests start their own API and web server on a temporary data folder (`tests/e2e_server.py`, ports 8696 and 8796).

## Making a change

### Code

- **Python:** type hints on public functions, a docstring saying what a module or function is for, and plain error messages that tell the user what to do next (they're shown in the terminal and the app). Keep model-facing text (prompts, verifier messages) in plain instructions.
- **Web app:** Svelte 5 runes (`$state`, `$derived`, `$props`), Tailwind utilities with the design tokens in `src/app.css` (use `bg-surface`, `text-muted`, `text-good` and so on, not raw colors, so dark mode works), and the shapes in `src/lib/types.ts` for API data. Browser code calls the API through `src/lib/client/api.ts`; server code through `src/lib/server/api.ts`. The API token must never reach the browser.
- **Accessibility:** every control reachable by keyboard, a label on every input, and text alongside color for meaning.

### Common tasks

- **Database change:** add a `_SCHEMA_V<n>` script in `db.py`, register it in `_MIGRATIONS`, and bump `SCHEMA_VERSION`. Migrations run inside one locked transaction, so write plain SQL statements (no `BEGIN`/`COMMIT`), and add a test that upgrades an older database.
- **New setting:** add the field to the right model in `config.py` with a default and a `description`, then expose it on the matching Settings section in `web/src/routes/settings/[section]/+page.svelte`.
- **Prompt change:** edit `chains/structured_prompts.json`. Example answers are validated against the output schema when the prompt loads, so a typo fails immediately. Run the relevant live test with your model before and after (`AUTOTAILOR_LIVE=1 uv run pytest tests/test_m2_requirements_chain.py -k live -s`).
- **New job source:** add a fetcher in `sources/` returning `JobPosting`s with a stable `id`, route it through `ingest.ingest` for filtering and de-duplication, and check the site's terms first (rule 4).
- **New API route or page:** add the route in `api_more.py` with a test in `tests/test_m12_api.py`, the type in `web/src/lib/types.ts`, then the page under `web/src/routes/`. Long-running work should start a task (`tasks.enqueue`), not run inside the request.

### Checklist before you open a pull request

- [ ] `uv run pytest` passes
- [ ] for web changes: `npm run check`, `npm test` and `npm run e2e` pass
- [ ] new behaviour has tests; bug fixes have a test that failed before the fix
- [ ] user-facing changes are reflected in the README (and PLAN.md's status log for larger ones)
- [ ] no personal data in the diff: no real CVs, emails, `.eml` files, `config.toml`, `.env`, or anything from `data/`

## Proposing a change

1. **Open an issue first** for anything larger than a small fix, describing the problem and your idea, so we can agree on the approach before you write it.
2. **Branch from `main`** in your fork, with a short descriptive name (`fix-runway-date-parsing`, `add-workday-source`).
3. **Keep pull requests focused:** one change per pull request, with a description of what changed and why, how you tested it, and screenshots for anything visible in the web app.
4. **Expect review** on the ground rules above in particular; a change that sends data somewhere new, fetches a new site or loosens a verifier needs a clear reason.

## Reporting bugs

Open an issue with what you did, what you expected, and what happened, including the error output (from the terminal, or the task's output under Activity) and your OS, Python and Node versions.

**Never attach personal data.** Replace your CV, profile, emails and postings with made-up examples. `tests/fixtures/` has a sample CV, sample postings and a synthetic Runway email you can use to reproduce problems.

If you find a security problem (for example a way for another website or another user to reach the local API or the app), please don't post the details publicly. Message the maintainer privately on Discord first: **1void_heart1**.

## Contact

Questions, ideas or help getting set up: reach the maintainer on Discord at **1void_heart1**, or open an issue.
