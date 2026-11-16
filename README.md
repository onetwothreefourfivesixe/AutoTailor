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

`autotailor doctor` shows, per step, whether data leaves your machine, and checks that the key for each cloud provider in use is set.

## Layout

```
src/autotailor/
  config.py   settings (profile, eligibility rules, run limits, sources, mail, models)
  models.py   job posting, requirements, match
  cv.py       master CV schema
  db.py       SQLite storage
  llm.py      model factory
  cli.py      command line
tests/
```

## Tests

```bash
uv run pytest
```
