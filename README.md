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
| `autotailor answers add <posting> "<question>"` | Add an application's short-answer questions (or `--file`, one per blank-line-separated block). The limit is read from the text, e.g. "(150 words)". |
| `autotailor answers run` | Suggest CV points for each question; draft and humanize the ones you opt in to. Output in `data/outputs/answers/`. |
| `autotailor runway list` / `runway resolve` | Runway roles, and finding each one's posting on the employer's site (asks you when it can't). |
| `autotailor models` | Which model each step uses, whether it's pulled in Ollama, and sizes. |
| `autotailor compare <step> -m provider:model` | Rerun `extract`, `score`, `tailor` or `answers` on saved data with several models; report in `data/comparisons/`. Changes nothing stored. |
| `autotailor api` | Local HTTP API at http://127.0.0.1:8765 (docs at `/docs`), for a custom frontend or a browser extension. |
| `autotailor answers decide <id>` / `runway answer <id>` | Answer an opt-in or a Runway link question without prompts (`--draft/--points-only --use 1,3`, `--url/--text/--skip`). |
| `autotailor worker` | Run queued background tasks (the UI starts it automatically). |
| `autotailor ui` | Local web UI at http://127.0.0.1:8501: settings, master CV editor, runs and postings. |

### Web UI

`autotailor ui` starts a local web UI. It listens on 127.0.0.1 only and has Streamlit's usage statistics switched off. On WSL, open the address in a Windows browser.

- **Needs you**: everything waiting on you (Runway links, answer opt-ins, unclear verdicts near the top, resumes over a page, stopped runs), each with its action.
- **Add jobs**: paste job links or a posting's text, or drop `.eml`, `.pdf`, `.html`, `.txt` files; a pasted raw email (Gmail "Show original") works too. Everything is previewed, editable, before it's saved.
- **Runs**: start work (whole pipeline, fetch, screen, rank, tailor, Runway matching, answer points). It runs in a background worker process (`autotailor worker`, started for you), so you can leave the page; progress and output update live, and Stop works like Ctrl-C (finished resumes are kept; Resume continues).
- **Postings** has a tab per stage: the text with the requirement quotes highlighted, eligibility with an **override** (wins everywhere, survives re-screening), fit with the cited CV bullets, the resume (preview of the saved PDF, diff against the CV, every draft attempt), answers, and the **AI log**: every model call for that posting with its prompt, response, model, time and tokens. The log stays on this machine and is kept 15 days (Settings > AI log).

- **Overview**: which models run where, posting counts, the next shortlist, recent runs.
- **Settings**: everything in `config.toml`. Saves are validated first, and the previous file is kept in `data/config_backups/`.
- **Master CV**: edit, add, reorder and remove entries and bullets in every section, or edit the raw YAML. Saves are validated; the previous file is kept in `data/cv_backups/`. New entries and bullets get IDs when saved, and IDs never change. Deleting a bullet that a past resume used asks first and shows where it was used.
- **Runs**: each run's resumes (PDF and Word downloads), each bullet beside the CV bullet it came from with reworded words marked, and the roles that weren't tailored, with reasons.
- **Compare models**: comparison reports, with a summary table per model.
- **Short answers**: add a posting's questions, see suggested points, pick which to draft from, and edit the drafts.
- **Runway**: import emails, see each Runway role's status, and answer the roles waiting for an employer link.
- **Postings**: every posting, including ineligible ones, with the requirements read from it (and the quoted evidence), the eligibility and fit reasons, and the full text.

Runs are started from the terminal, not the UI, so long model calls don't depend on a browser tab.

### Local API

`autotailor api` serves everything the UI does over HTTP, on 127.0.0.1 only: overview and the Needs-you list, postings (with overrides and a resume preview image), runs and their files, background tasks (start by kind or by typed command, cancel, and a live event stream at `/api/tasks/{id}/events`), adding jobs and emails (preview, then save), the AI log, settings, the CV (validated; deleting bullets used in past resumes needs `confirm_delete_used`), Runway answers and short answers. Interactive documentation is at `/docs`.

Every request must send the token from `data/api_token` in the `X-AutoTailor-Token` header (or `?token=` for download links), and the `Host` header must be localhost. This keeps websites open in your browser from using the API. To develop a browser frontend, allow its origin: `autotailor api --cors http://localhost:5173`.

### Resume templates

The PDF template is chosen in Settings > Resume template:

- **Jake's Resume** (default): the popular LaTeX template by Jake Gutierrez (MIT). LaTeX is compiled with [Tectonic](https://tectonic-typesetting.github.io/), which AutoTailor downloads into `data/tools/` on first use (no sudo). Tectonic then fetches the TeX packages it needs once; no resume content leaves the machine. If it can't be downloaded (offline), the Typst template is used with a warning.
- **AutoTailor classic** (Typst).
- **Your own LaTeX template**: download Jake's as a starting point, change the look, upload it. Values go in as `((( name )))`, statements as `((* for s in sections *))`; all text is escaped for LaTeX.
- **Your own Word template** (`.docx`, docxtpl syntax `{{ name }}`) for the Word file.

Preview any template with your own CV before saving.

### Short answers

For each question, AutoTailor first suggests 2-5 experiences from your CV that answer it, and stops there unless you ask for a draft. A draft is written from only the points you pick (plus any notes you add), then rewritten by the humanizer step, whose prompt removes the usual signs of AI writing (inflated claims, stock words like "delve" or "testament", em dashes, formulaic endings). Every draft is then checked: no numbers or names that aren't in the chosen experiences, your CV skills and profile, the posting or the question; within the word limit; none of the stock phrases. Problems go back to the humanizer, up to `tailor.max_attempts` times; anything left is listed for you to fix. Treat the result as a first draft.

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

### Choosing models

`autotailor compare` reruns one step on postings (or questions) you already have, once with the model the step uses now and once per `-m` model, and writes a side-by-side report:

```bash
ollama pull gemma4:12b
autotailor compare tailor  -m ollama:gemma4:12b -n 3     # verification pass rate, drafts needed, bullets chosen
autotailor compare score   -m ollama:gemma4:12b -n 10    # rank agreement with your stored fit scores
autotailor compare extract -m ollama:gemma4:12b          # same eligibility verdict as stored?
autotailor compare answers -m ollama:gemma4:12b          # problems left after humanizing, word count
```

Every trial is timed, so quality and speed can be weighed together. A cloud model in the list asks before any data is sent. Then set the winner per step with `autotailor config set` or on the Settings page. Rough guide for local models: a model needs about its download size in GPU memory plus some for the context; anything larger spills into system RAM and runs several times slower.

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
  answers.py      short answers: question parsing, suggested points, facts-and-style check
  compare.py      model comparisons on saved data (M9)
  api.py          local HTTP API (FastAPI) (M11)
  tasks.py        background task queue and worker (M10)
  intake.py       pasted/dropped jobs and emails, previews (M10)
  ailog.py        local AI call log (LangChain callback) (M10)
  inbox.py        the Needs-you list (M10)
  ui/         Streamlit app (app.py, one page_*.py per page); logic.py holds the tested save/ID/diff helpers
  resume/     verify.py (nothing beyond the CV), layout.py, render.py (templates, PDF, Word, previews), latex.py (Tectonic), output.py
  sources/    ats.py (Greenhouse, Lever, Ashby), paste.py (URL or pasted text), runway.py (email parser)
  chains/     LangChain steps: extract_posting.py (M1 exercise), extract_requirements.py (M2 exercise), score_fit.py (M3 exercise), tailor_graph.py (M4 exercise, LangGraph), batch_graph.py (M5 exercise, fan-out and checkpoints), resolve_graph.py (M7 exercise, interrupts), answer_graph.py (M8 exercise, opt-in drafting)
              structured_prompts.json holds every step's prompt text and few-shot examples
tests/
```

## Tests

```bash
uv run pytest
```

Exercise tests show as `x` (expected failure) until the exercise is done. Tests that call a real model are skipped unless `AUTOTAILOR_LIVE=1` is set.
