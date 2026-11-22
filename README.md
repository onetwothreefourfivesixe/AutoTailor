# AutoTailor

Local-first job matching and resume tailoring. It pulls job postings, keeps the ones you are eligible for, and tailors a resume from your master CV for the best matches. It never submits applications, and your data stays on your machine.

Status: early development. See [PLAN.md](PLAN.md) for the roadmap.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run autotailor init      # creates config.toml, .env, cv/master.yaml, data/
uv run autotailor doctor    # reports what is still missing
```

Then:

1. Edit `config.toml`: add your education under `[[profile.education]]`, plus locations and interests.
2. Edit `cv/master.yaml`: your full CV, with a unique `id` on every entry and bullet.
3. Put the API key for your model provider in `.env` (see Models; not needed for Ollama).

`config.toml`, `.env`, `cv/` and `data/` hold personal data or secrets and are git-ignored.

## Commands

| Command | What it does |
|---|---|
| `autotailor init` | Create the config, CV template and database. Safe to re-run. |
| `autotailor doctor` | Check config, CV, database and model credentials. |
| `autotailor config show` | Print all settings. |
| `autotailor config set <key> <value>` | Change one setting, e.g. `config set run.jobs_per_run 5`. |
| `autotailor cv validate` | Check the master CV for errors and duplicate IDs. |
| `autotailor fetch` | Pull postings from the job boards listed in `config.toml`, or one board with `-b greenhouse:figma`. |
| `autotailor add <url>` | Add one posting by URL. Greenhouse, Lever and Ashby URLs need no model. |
| `autotailor add --file posting.txt` | Add a posting from copied text (also accepts text piped on stdin). |
| `autotailor jobs list` / `jobs show <id>` | Browse stored postings; `-v eligible` filters by verdict, `show` gives the reasons. |
| `autotailor screen` | Check eligibility: titles first, then each posting's requirements against your profile. |
| `autotailor screen --rejudge` | Recompute verdicts after changing your profile or `[eligibility]` rules; no model calls. |
| `autotailor rank` | Rank eligible postings: local embedding similarity to your CV, then model fit scores for the top `run.shortlist_size`. |
| `autotailor shortlist` | Show the postings that will be tailored next (fit ≥ `run.min_fit_score`, up to `run.jobs_per_run`). |
| `autotailor tailor` | Tailor a resume (PDF and Word) for each shortlisted role, `run.parallel_jobs` at a time, into `data/outputs/<date>-run<N>/` with a `report.md`. Every resume must pass verification against your CV. |
| `autotailor resume [run]` | Continue a run stopped with Ctrl-C or by an error; finished resumes are not redone. |
| `autotailor runs` | Past runs: status, results and report location. |
| `autotailor run` | The whole pipeline: Runway mail, `fetch`, Runway matching, `screen`, `rank`, `tailor`. |
| `autotailor mail import [files]` | Import Runway emails saved as `.eml` into `data/inbox/` (or the files named). |
| `autotailor mail pull` | Pull Runway emails from a Gmail label over IMAP (read-only). |
| `autotailor mail listen` | Local mail server on 127.0.0.1 that accepts Runway emails forwarded from a desktop mail client. |
| `autotailor runway list` / `runway resolve` | Runway roles, and finding each one's posting on the employer's site (asks you when it can't). |
| `autotailor ui` | Local web UI at http://127.0.0.1:8501: settings, master CV editor, runs and postings. |

### Web UI

`autotailor ui` starts a local web UI. It listens on 127.0.0.1 only and has Streamlit's usage statistics switched off. On WSL, open the address in a Windows browser.

- **Overview**: which models run where, posting counts, the next shortlist, recent runs.
- **Settings**: everything in `config.toml`. Saves are validated first, and the previous file is kept in `data/config_backups/`.
- **Master CV**: edit, add, reorder and remove entries and bullets in every section, or edit the raw YAML. Saves are validated; the previous file is kept in `data/cv_backups/`. New entries and bullets get IDs when saved, and IDs never change. Deleting a bullet that a past resume used asks first and shows where it was used.
- **Runs**: each run's resumes (PDF and Word downloads), each bullet beside the CV bullet it came from with reworded words marked, and the roles that weren't tailored, with reasons.
- **Runway**: import emails, see each Runway role's status, and answer the roles waiting for an employer link.
- **Postings**: every posting, including ineligible ones, with the requirements read from it (and the quoted evidence), the eligibility and fit reasons, and the full text.

Runs are started from the terminal, not the UI, so long model calls don't depend on a browser tab.

### Runs

Each `tailor` (or `run`) is one run. Its folder holds one subfolder per role (PDF, Word, `tailored.json`, `posting.md` with the apply link and the eligibility and fit reasons) and `report.md`, which links them all and lists any role that wasn't tailored, with the reasons.

`run.max_runs_per_day` and `run.max_runs_per_week` cap new runs (resuming doesn't count); `--ignore-limits` overrides them once. Press Ctrl-C to stop a run: jobs in progress finish and are saved, and `autotailor resume` does the rest. Progress is saved in `data/checkpoints.db`.

## Job sources

Company job boards are listed per ATS in `config.toml` by the company's slug, the name in its board URL (`boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>`):

```toml
[sources]
title_include = ["intern", "internship", "co-op"]   # keep only matching titles; [] keeps all
title_exclude = []
greenhouse = ["figma"]
lever = ["palantir"]
ashby = ["openai"]
```

LinkedIn, Handshake, Runway, Indeed and Glassdoor are never fetched automatically, because their terms forbid it. For those, copy the posting text and use `autotailor add --file`.

### Runway

Runway's terms forbid automated access to its site, so AutoTailor reads Runway's daily notification emails instead and never opens a Runway link. Each email gives a role's title, company, location and match score, but no description. AutoTailor then looks for the same role on the employer's own job board; when it can't find it, the role waits for you to open the Runway link in your browser and paste the employer's posting URL or text (`autotailor runway resolve`, or the Runway page of the UI).

Getting the emails in, from simplest:

1. **Drop folder** (on by default): in Gmail, open the email, "⋮" > "Download message", save the `.eml` into `data/inbox/`, then `autotailor mail import` (or `autotailor run`).
2. **Gmail label over IMAP**: add a Gmail filter that labels mail from `hello@tower.joinrunway.io` (e.g. "Runway"), create an app password, then `autotailor config set mail.imap true`, `config set mail.imap_user you@gmail.com`, `config set mail.imap_label Runway`, and `autotailor mail login`. Without a usable OS keyring (e.g. WSL), put `AUTOTAILOR_IMAP_PASSWORD=...` in `.env` instead. Pulls are read-only and never mark mail as read.
3. **Local mail server**: `autotailor mail listen`, then in a desktop mail client add an outgoing server `127.0.0.1:8025` and forward Runway emails through it.

Only email from `mail.allowed_senders` is read; everything else is ignored.

## Models

Each pipeline step (`extract`, `score`, `tailor`, `humanize`) uses `[models.default]` unless overridden under `[models.steps.<step>]`.

| Provider | `provider` value | API key variable | Runs |
|---|---|---|---|
| Anthropic (Claude) | `anthropic` | `ANTHROPIC_API_KEY` | cloud |
| OpenAI | `openai` | `OPENAI_API_KEY` | cloud |
| Google Gemini | `google_genai` | `GOOGLE_API_KEY` | cloud |
| Ollama | `ollama` | none | on your machine |

Keys go in `.env`, one `NAME=value` per line; a variable already exported in your shell takes precedence. Keys are deliberately not in `config.toml`, because that file is printed by `config show` and saved with every run.

`model` is the provider's own model ID (for Ollama, a name from `ollama list`). For example, to run one step locally:

```toml
[models.steps.extract]
provider = "ollama"
model = "qwen3-vl:4b-instruct"
```

Ranking also uses an embedding model, set under `[models.embeddings]`: local `nomic-embed-text` by default (`ollama pull nomic-embed-text`), or a cloud one.

| Provider | `provider` value | Example `model` | Key |
|---|---|---|---|
| Ollama | `ollama` | `nomic-embed-text` | none |
| OpenAI | `openai` | `text-embedding-3-small` | `OPENAI_API_KEY` |
| Google Gemini | `google_genai` | `gemini-embedding-001` | `GOOGLE_API_KEY` |

Anthropic has no embeddings API, so with Claude for the chat steps, pick OpenAI or Gemini embeddings (or keep Ollama).

### All local, mixed, or cloud only

Any combination works; `autotailor doctor` reports which one you have.

```toml
# All local: nothing leaves your machine. Needs Ollama and enough RAM/VRAM.
[models.default]
provider = "ollama"
model = "qwen3-vl:4b-instruct"
[models.embeddings]
provider = "ollama"
model = "nomic-embed-text"
```

```toml
# Mixed: cheap steps local, writing in the cloud.
[models.default]
provider = "ollama"
model = "qwen3-vl:4b-instruct"
[models.steps.tailor]
provider = "google_genai"
model = "gemini-2.5-pro"
```

```toml
# Cloud only: no Ollama needed.
[models.default]
provider = "google_genai"
model = "gemini-2.5-pro"
[models.embeddings]
provider = "google_genai"
model = "gemini-embedding-001"
```

Ollama models read at most `context_window` tokens at once (default 16384, set per model block). Ollama's own default is much smaller and cuts long prompts without an error, so lower it only if you run short of memory.

Embedding vectors are cached per provider and model, so switching embedding models re-embeds everything once.

`autotailor doctor` shows, per step, whether data leaves your machine, and checks that the key for each cloud provider in use is set.

## Layout

```
src/autotailor/
  config.py   settings (profile, eligibility rules, run limits, sources, mail, models)
  models.py   job posting, requirements, match
  cv.py       master CV schema
  db.py       SQLite storage and schema migrations
  llm.py      model factory
  cli.py      command line
  text.py     HTML to plain text
  dedupe.py   duplicate detection across sources
  ingest.py   title filter, dedupe and save
  eligibility.py  title pass, requirements vs profile, verdicts
  matching.py     embedding pre-rank, profile text, fit score, shortlist
  batch.py        batch runs: jobs, worker, run limits, checkpoints, outputs, report.md
  mail.py         Runway mail intake: drop folder, IMAP, local SMTP listener
  resolve.py      Runway role -> employer posting (job board match, or your link/text)
  ui/         Streamlit app (app.py, one page_*.py per page); logic.py holds the tested save/ID/diff helpers
  resume/     verify.py (nothing beyond the CV), layout.py, render.py (Typst PDF, Word), output.py
  sources/    ats.py (Greenhouse, Lever, Ashby), paste.py (URL or pasted text), runway.py (email parser)
  chains/     LangChain steps: extract_posting.py (M1 exercise), extract_requirements.py (M2 exercise), score_fit.py (M3 exercise), tailor_graph.py (M4 exercise, LangGraph), batch_graph.py (M5 exercise, fan-out and checkpoints), resolve_graph.py (M7 exercise, interrupts)
              structured_prompts.json holds every step's prompt text and few-shot examples
tests/
```

## Tests

```bash
uv run pytest
```

Exercise tests show as `x` (expected failure) until the exercise is done. Tests that call a real model are skipped unless `AUTOTAILOR_LIVE=1` is set.
