# AutoTailor

**Spend your time applying, not sorting postings and rewriting your resume.**

AutoTailor is a job-search assistant for students and early-career applicants. It finds internships and jobs you're actually eligible for, ranks them by how well they fit you, and writes a tailored one-page resume for each of the best ones, using only what's in your own CV.

## Why AutoTailor exists

Applying for internships usually means hundreds of postings, and each one costs time before you've even applied:

- **Most postings aren't open to you.** "Rising seniors only", "graduating 2027", "PhD students": you find out after reading the whole thing.
- **One resume doesn't fit every role.** Recruiters and applicant-tracking systems look for the posting's own words, so a generic resume gets passed over, but rewriting it for every application takes an evening.
- **Auto-apply tools have a price.** Most upload your CV and history to someone else's servers, and some apply on your behalf with resumes you never read, or with claims you never made.

AutoTailor does the tedious part and leaves the decisions to you:

- **Your data stays on your computer.** Your CV, the postings and everything AutoTailor produces are kept in files on your machine. You choose whether the AI models run locally (with [Ollama](https://ollama.com)) or in the cloud, step by step.
- **It never applies for you.** It prepares the resume (and, if you ask, short-answer drafts); you read them and apply yourself.
- **It doesn't make things up.** Every resume bullet comes from a bullet in your CV. A checker rejects anything new, such as a number, a tool or a company that isn't in your CV.

Who it's for: anyone applying to many roles at once, especially students looking for internships, who wants tailored applications without handing their data to a service.

> Status: early development, used daily by its author. Expect rough edges.

**Contents:** [What it does](#what-it-does) · [Install](#install) · [First run](#first-run) · [Using the web app](#using-the-web-app) · [Where jobs come from](#where-jobs-come-from) · [Choosing AI models](#choosing-ai-models) · [Getting API keys](#getting-api-keys) · [Your resume's look](#your-resumes-look) · [Short answers](#short-answers) · [Privacy](#privacy) · [Troubleshooting](#troubleshooting) · [Command reference](#command-reference) · [Contributing](#contributing)

## What it does

1. **Collects postings** from company job boards (Greenhouse, Lever, Ashby), from links or text you paste in, and from Runway's notification emails.
2. **Screens them**: reads each posting's requirements (degree level, graduation date, majors) and drops the ones you can't apply to. Each verdict shows the sentence it was based on, and you can override any verdict.
3. **Ranks the rest** by how well they match your CV and interests.
4. **Tailors a resume** for each of the top roles: it picks your most relevant bullets and lightly rewords them toward the posting, then checks the result against your CV. You get a PDF and a Word file, plus the posting and the reasons it was chosen.
5. **Helps with short answers** (optional): it suggests which of your experiences answer a question and, only if you ask, drafts an answer.
6. **Tracks your applications**: when you've applied, mark it, and follow it from applied to offer.

## Install

You need:

- [Python 3.11+](https://www.python.org/downloads/) and [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Node.js](https://nodejs.org) 20.19 or later (22 LTS recommended) for the web app
- Optionally [Ollama](https://ollama.com), to run the AI models on your own computer

Then, in the project folder:

```bash
uv sync                                   # install AutoTailor
(cd web && npm install && npm run build)  # build the web app (once, and again after updating)
```

## First run

```bash
uv run autotailor ui
```

That's it: the first time, AutoTailor creates its settings and database, then prints a one-time sign-in link: open it in your browser (on WSL, a Windows browser) to start the **setup wizard**. (`autotailor ui --browser` opens the link for you.) The wizard walks you through:

1. **Where the AI runs**: on your computer with Ollama (free and private), in the cloud with an API key (it shows you how to get one, step by step, and saves it on your computer), or a mix.
2. **Your CV**: drag in your current resume or CV (PDF, Word, text or Markdown). AutoTailor reads it, sorts it into sections, and shows you the result to check before saving. Any line the AI didn't copy word for word is flagged. Include everything you might ever put on a resume; AutoTailor picks the right parts for each job.
3. **About you**: your degree and expected graduation date (filled in from your CV), the roles you want, where you'd work.
4. **Job boards**: describe the fields you're interested in, in your own words ("biomedical", "robotics and space", "quant finance"), and press **Find companies**: AutoTailor picks matching companies from its catalog of over 700 verified job boards (biotech, medical devices, digital health, AI, developer tools, fintech, quant trading, security, robotics, aerospace and defense, hardware, climate and energy, education, logistics, games and media, consumer, business software), checks each for open jobs, and ticks them (for "biomedical", about 160). Optionally it also asks the AI for more companies; each one's board must exist, belong to that company and mention the field before it's listed, and the ones that pass are saved to your catalog (`data/learned_boards.json`) so later searches include them. Boards that disappear are dropped from searches until they come back. You can also tick companies by hand, or add your own.

Then press **Run the pipeline now**: AutoTailor fetches postings, screens and ranks them, and tailors resumes for the best ones. Progress shows in the task tray, bottom right; the results appear on the Today page. Press Ctrl-C in the terminal to stop the app.

You can rerun the wizard any time from `/setup`, and change everything later under Settings and Master CV.

<details>
<summary>Prefer setting up by hand?</summary>

Run `uv run autotailor init`, then edit `config.toml` (your `[profile]`: degrees, graduation date, locations, interests), `cv/master.yaml` (your CV; each entry and bullet needs a short unique `id`, as in the template), and `.env` (API keys, see [Getting API keys](#getting-api-keys)). Check with `uv run autotailor cv validate` and `uv run autotailor doctor`.

```toml
[[profile.education]]
level = "bachelor"
field = "Computer Science"
status = "in_progress"
graduation_date = 2029-05-31
school = "UC Berkeley"
```

</details>

## Using the web app

| Page | What you do there |
|---|---|
| **Today** | Start here. What needs your attention, your best eligible matches, and which AI model each step uses. |
| **Jobs** | Every posting, with filters and saved views ("Eligible, no resume", "Applied", ...). Select several to tailor or hide them at once. |
| **Job** | One posting in detail: the text with the requirement sentences highlighted, why it's eligible (or not), how well you fit, the tailored resume, short answers, and every AI call made for it. |
| **Runway** | Roles from Runway emails, and the few that need you to paste the employer's link. |
| **Applications** | A board of what you've applied to. Drag cards as you hear back. |
| **Runs** | Each batch of tailored resumes, with downloads. |
| **Master CV** | Edit your CV with a live preview of how a resume will look. |
| **Settings** | Your profile, eligibility rules, limits, job sources, AI models, resume template. |
| **Activity** | Everything that ran in the background, and every AI call. |

Handy anywhere:

- **Add** (press `A`, or drop a file anywhere on the page): paste job links or a posting's text, or drop `.pdf`, `.html`, `.txt` or Runway `.eml` files. You see what was read before anything is saved.
- **Search** (`Ctrl K`, or `⌘ K` on a Mac): jump to any job or page, or start any task. Type a command with its options (`rank --rescore`) to run it.
- **Command line** (in the sidebar): AutoTailor's commands as in the terminal, with their output as they run, ↑/↓ history, and a list of every command (**Commands**; add `--help` to one for its options). It runs AutoTailor's own commands only, never a shell.
- **Score fit**: on a job's page, or for the jobs you select on the Jobs page (`s`), scores fit with the `score` step's model, again if it was scored before (same as `autotailor rank <posting ids>`).
- **Keyboard on the Jobs page:** `j`/`k` to move, `Enter` to open, `x` to select, `e`/`i` to mark eligible or ineligible, `t` to tailor, `h` to hide, `/` to search.

Long jobs (screening, ranking, tailoring) run in the background, so you can leave the page or close the browser. **Stop** in the task tray works like Ctrl-C: finished resumes are kept, and **Resume** on the Runs page picks up where it stopped.

Prefer the terminal? Everything in the app is also a command; see the [Command reference](#command-reference). The previous, simpler interface is still available with `autotailor ui --classic`.

### What you get for each job

Each run creates a folder in `data/outputs/` (for example `2026-11-22-run3/`), with one subfolder per job:

- `<your-name>-resume.pdf` and `<your-name>-resume.docx`: the tailored resume
- `posting.md`: the posting, its apply link, and why it was eligible and chosen
- `tailored.json`: which CV bullets were used

The run's `report.md` links everything and lists any job that couldn't be tailored, with the reason. Always read a resume before you send it.

### How a resume is tailored

1. **It starts from your usual resume.** Tick "usual" on the bullets of the resume you'd normally send (Master CV page, or `base: true` in `cv/master.yaml`; a CV imported in the setup wizard starts with every bullet ticked). For each posting the AI keeps what helps, drops what helps least (often a project unrelated to the job), adds CV bullets that fit better, and gives the jobs closest to the posting more bullets. Jobs and projects are listed most relevant first.
2. **Your wording stays.** Bullets are copied as you wrote them; the AI may only swap in the posting's term for something a bullet already says ("data visualization" for "made charts"). A checker rejects any draft that rewrites a bullet in its own words, or adds tools, numbers or results your CV doesn't state, and the AI tries again. For word-for-word copies only, set Settings › Runs and limits › Bullet wording. If no draft passes, you get your usual resume, ordered and filled for the posting, with a note saying so.
3. **The page is filled.** The rest of your usual resume goes in first, then your next most relevant CV bullets, word for word, until one more would spill onto a second page. A draft that runs over a page is trimmed from its least relevant end (your education lines stay). Turn this off under Settings › Runs and limits › "Fill the page".
4. **Keyword match.** The job's Resume tab shows how many of the posting's key terms appear on the resume (80% or more is a strong match) and which are missing. **Click the missing ones that are true for you** (or type your own) and press **Add with AI**: each is worked in exactly once, into the bullet whose experience involves it (keeping its facts, adding nothing else) or into the closest skills group. The result opens in the editor with a list of the changes, for you to review and save. From the terminal: `autotailor ats <posting id> "data pipelines" "Apache Spark"` (`--dry-run` to see the changes first).
5. **Your final edits.** Click the resume on the job's Resume tab to see it full size, then **Edit** for a full editor of the page: your name and contact line; every section (rename, reorder, add your own, remove); every entry (title, organization, location, dates, tech stack); every bullet (reword it, write a new one, or insert one from your CV); and the skills. A live preview re-renders as you type and shows the page count. **Save** renders the PDF and Word files for that job only (your Master CV doesn't change); words or numbers your CV doesn't have get a note, not an error. **Restore tailored version** undoes every edit.

## Where jobs come from

**Company job boards.** Most tech companies post on Greenhouse, Lever or Ashby. Add a company by the name in its job board's web address (its "slug") under Settings › Job sources, or in `config.toml`:

```toml
[sources]
title_include = ["intern", "internship", "co-op"]   # keep only these titles; [] keeps everything
greenhouse = ["figma", "stripe"]    # boards.greenhouse.io/figma
lever = ["palantir"]                # jobs.lever.co/palantir
ashby = ["openai"]                  # jobs.ashbyhq.com/openai
max_age_days = 21                   # skip postings published more than 3 weeks ago; 0 = no cutoff
keep_undated = true                 # keep postings that don't say when they were published
```

Saved postings older than the cutoff (by publish date, or by when they were first pulled) are deleted at every fetch and rank (`delete_expired = true`), and so are postings whose fit score is below the cut-off (Settings › Runs and limits: by default the minimum fit score; choose "hide" there to keep them instead). Postings you tailored, applied to, overrode or added questions for are never deleted. A tiny record of each deleted posting stops it from being fetched and scored again; the Jobs page's **Cut** view lists them, with **Allow again**. `autotailor prune` does all this on demand and shrinks the database file. `autotailor fetch --max-age 60` overrides the cutoff once.

Many large companies (Apple, Google, Microsoft, Amazon, ...) use their own careers sites, which AutoTailor can't read. Paste those postings instead.

**Finding companies by field.** `uv run autotailor discover "medical devices, neurotech"` lists companies in those fields with open jobs (`--add` follows them all, `--no-model` uses only the built-in catalog). Only your description is sent to the model, never your CV.

**Pasted postings.** Use **Add** with a link or the posting's text. LinkedIn, Handshake, Indeed, Glassdoor and Runway don't allow automated access, so AutoTailor never opens their links: open the posting yourself and paste its text.

**Runway.** AutoTailor reads Runway's daily notification emails; it never visits Runway's site. Each email names the roles but has no details, so AutoTailor finds each role on the employer's own job board. When it can't, the role appears on the Runway page for you to paste the employer's link. Ways to bring the emails in, simplest first:

1. **Drop the file in:** in Gmail, open the email, choose ⋮ › Download message, and drop the `.eml` file onto the app (or save it into `data/inbox/`).
2. **Gmail label (automatic):** make a Gmail filter that labels Runway's emails (from `hello@tower.joinrunway.io`), create a Google [app password](https://myaccount.google.com/apppasswords), then turn on Settings › Mail › IMAP and run `uv run autotailor mail login`. (On WSL, put `AUTOTAILOR_IMAP_PASSWORD=...` in `.env` instead.) AutoTailor only reads that label and never marks anything as read.
3. **Forward from a desktop mail client:** run `uv run autotailor mail listen` and forward emails through the outgoing server `127.0.0.1:8025`.

## Choosing AI models

AutoTailor uses AI for four steps: **extract** (reading requirements), **score** (judging fit), **tailor** (writing resumes and answer drafts) and **humanize** (polishing answers). It also uses an **embedding** model to pre-sort jobs. Each step can use a different model, set in Settings › Models.

| Provider | Runs | Needs |
|---|---|---|
| Ollama | on your computer | Ollama installed, and the model pulled (`ollama pull <name>`) |
| Anthropic (Claude) | in the cloud | `ANTHROPIC_API_KEY` in `.env` |
| OpenAI | in the cloud | `OPENAI_API_KEY` in `.env` |
| Google Gemini | in the cloud | `GOOGLE_API_KEY` in `.env` |

API keys go in `.env` (one `NAME=value` per line), never in `config.toml`. See [Getting API keys](#getting-api-keys) for how to get one.

Three common setups in `config.toml`:

```toml
# Everything on your computer: nothing leaves it. Needs a decent GPU or patience.
[models.default]
provider = "ollama"
model = "qwen3-vl:4b-instruct"
[models.embeddings]
provider = "ollama"
model = "nomic-embed-text"
```

```toml
# Mixed: quick steps on your computer, resume writing in the cloud.
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

**Not sure which model is best?** Compare them on jobs you already have. AutoTailor reruns a step with another model and shows the results side by side, without changing anything you've saved:

```bash
uv run autotailor compare tailor -m ollama:gemma4:12b -n 3
```

Results appear under Settings › Models. As a rough guide, a local model needs about its download size in GPU memory; bigger ones still work but run several times slower. `uv run autotailor models` shows which models you have and their sizes.

## Getting API keys

You only need a key for the cloud providers you actually use; Ollama needs none. Cloud models are pay-per-use (Google also has a limited free tier), so set a spending limit in the provider's console when you create the key. Each key goes on its own line in `.env`:

```ini
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=AIza...
```

### Anthropic (Claude)

1. Sign in or create an account at [console.anthropic.com](https://console.anthropic.com).
2. Add credit under **Settings › Billing** (the API is prepaid).
3. Go to **Settings › API Keys** ([direct link](https://console.anthropic.com/settings/keys)), choose **Create Key**, and name it "AutoTailor".
4. Copy the key (it starts with `sk-ant-`; it's shown only once) into `.env` as `ANTHROPIC_API_KEY=...`.

### OpenAI

1. Sign in or create an account at [platform.openai.com](https://platform.openai.com).
2. Add a payment method under **Settings › Billing** ([direct link](https://platform.openai.com/settings/organization/billing)).
3. Open **API keys** ([direct link](https://platform.openai.com/api-keys)) and choose **Create new secret key**.
4. Copy the key (it starts with `sk-`; it's shown only once) into `.env` as `OPENAI_API_KEY=...`.

### Google Gemini

1. Sign in with a Google account at [Google AI Studio](https://aistudio.google.com).
2. Open **Get API key** ([direct link](https://aistudio.google.com/app/apikey)) and choose **Create API key**.
3. Copy the key (it starts with `AIza`) into `.env` as `GOOGLE_API_KEY=...`.
4. The free tier has low rate limits, and its terms allow Google to use what you send to improve its products. For your CV and applications, consider enabling billing on the key's Google Cloud project, which switches to the paid terms. Check Google's current terms before deciding.

### Ollama (no key)

1. Install Ollama from [ollama.com/download](https://ollama.com/download) and start it.
2. Download the models you set in `config.toml`, for example `ollama pull qwen3-vl:4b-instruct` and `ollama pull nomic-embed-text`.

After adding keys or models, run `uv run autotailor doctor` to confirm each step can reach its model. Keep `.env` private: it's excluded from git, and you shouldn't paste keys into the app or share them. If a key leaks, delete it in the provider's console and create a new one.

## Your resume's look

Choose the template in Settings › Resume template, and preview it with your own CV before saving.

- **Jake's Resume** (default): the popular clean, single-column LaTeX template by Jake Gutierrez. The first time you use it, AutoTailor downloads a small LaTeX engine ([Tectonic](https://tectonic-typesetting.github.io/)) into `data/tools/`, which takes about a minute; after that each resume takes a few seconds.
- **Jake's Resume, compact**: the same look at 10pt with tighter margins (about twice the bullets per page), each job's or project's tech stack after its title (`stack:` in your CV), leadership entries on two lines, and one skills row per group.
- **AutoTailor classic**: a simpler look that needs no download.
- **Your own design**: download Jake's template from the same page, change it, and upload it. A Word (`.docx`) template is also supported, for the Word copy.

Every resume should fit on one page. If one doesn't, the app tells you; lower "Most bullets" under Settings › Runs and limits.

Entries you never want on a resume (high school once you're in college, an old certificate) can stay in your CV: tick "Hide from resumes" on the Master CV page (`hidden: true`). The skills section lists all your skills, the posting's first in each group; Settings › Runs and limits › Skills section can show only the posting's.

## Short answers

For an application's "Why do you want to work here?" style questions, open the job's **Answers** tab and add the question, with its limit if it has one, e.g. `(150 words)`. AutoTailor then:

1. suggests 2 to 5 experiences from your CV that answer it, and stops there;
2. only if you choose **Draft from these**, writes a draft from the experiences you ticked, plus any notes you add;
3. rewrites the draft to remove the usual signs of AI writing (grand claims, stock phrases, em dashes);
4. checks the draft adds nothing that isn't in your CV and stays within the word limit, and lists anything it couldn't fix.

Edit the draft right there; it's a starting point, not a finished answer.

## Privacy

- Your profile, CV, postings, resumes and history live in this folder: `config.toml`, `.env`, `cv/` and `data/`. They're excluded from git, so they won't be committed by accident.
- With local models, nothing about you leaves your computer. With a cloud model, the text that step needs (a posting, your CV) is sent to that provider. The app labels each step "on this machine" or "sent to …", and `autotailor doctor` lists them.
- The app and its API only accept connections from your own computer, and the app needs a sign-in link from the terminal.
- Every AI call is logged locally (prompt, response, timing) so you can see exactly what was sent; the log is deleted after 15 days. Change or turn this off in Settings › AI log.

## Troubleshooting

**"The web app isn't built yet"**: run `cd web && npm install && npm run build`, then `autotailor ui` again.

**The sign-in link says it expired**: links work once, for 10 minutes. Run `uv run autotailor ui-login` for a new one (while the app is running).

**A model step fails**: run `uv run autotailor doctor`. Common causes are a missing API key in `.env`, Ollama not running, or a model that isn't pulled (`ollama pull <name>`).

**Most jobs show "unclear"**: their requirements haven't been read yet, or the model couldn't decide. Run screening (Today › Run pipeline, or `autotailor screen`); for the rest, open the job and set the verdict yourself.

**A resume is two pages**: lower "Most bullets" in Settings › Runs and limits, or try the classic template.

**Something else**: check Activity for the task's output, or run the same step in a terminal (e.g. `uv run autotailor screen`) to see the full error.

## Command reference

Everything the app does is also available in the terminal. Run any command with `--help` for its options. Prefix each with `uv run` (or activate the virtual environment).

### Setup and settings

| Command | What it does |
|---|---|
| `autotailor init` | Create settings, the CV template and the database (safe to re-run) |
| `autotailor doctor` | Check that everything needed is in place |
| `autotailor config show` / `config set <key> <value>` | Show or change a setting, e.g. `config set run.jobs_per_run 5` |
| `autotailor cv validate` | Check your CV for mistakes |
| `autotailor models` | Which model each step uses, and what's installed in Ollama |

### Finding and screening jobs

| Command | What it does |
|---|---|
| `autotailor run` | The whole pipeline: emails, fetch, screen, rank, tailor |
| `autotailor fetch` | Pull postings from your job boards (or one: `-b greenhouse:figma`) |
| `autotailor add <url>` / `add --file posting.txt` | Add one posting by link or text |
| `autotailor screen` | Check eligibility (`--rejudge` recomputes after you change your profile, without AI calls) |
| `autotailor rank` / `shortlist` | Rank eligible postings / show the ones that will be tailored next |
| `autotailor jobs list` / `jobs show <id>` | Browse postings and see the reasons for each verdict |

### Resumes and answers

| Command | What it does |
|---|---|
| `autotailor tailor` | Tailor resumes for the shortlist (or name postings) |
| `autotailor resume` / `runs` | Continue a stopped run / list past runs |
| `autotailor answers add <posting> "<question>"` | Add an application question |
| `autotailor answers run` | Suggest points for each question and draft the ones you choose |
| `autotailor compare <step> -m provider:model` | Try another model on saved data |

### Runway emails

| Command | What it does |
|---|---|
| `autotailor mail import` | Import `.eml` files from `data/inbox/` |
| `autotailor mail pull` / `mail login` | Pull from a Gmail label / store its app password |
| `autotailor mail listen` | Accept forwarded emails on 127.0.0.1:8025 |
| `autotailor runway list` / `runway resolve` | List Runway roles / find their employer postings |

### The app

| Command | What it does |
|---|---|
| `autotailor ui` | Start the web app and print a sign-in link (`--classic` for the older interface) |
| `autotailor ui-login` | A new sign-in link for the running app |
| `autotailor api` | Only the local API (for scripts; documentation at <http://127.0.0.1:8765/docs>) |

## Contributing

Bug reports and improvements are welcome. Questions? Find the maintainer on Discord: **1void_heart1**. See [CONTRIBUTING.md](CONTRIBUTING.md) for how the code is organized, how to run the tests, and how to propose a change. The roadmap and design notes are in [PLAN.md](PLAN.md) and [UI_PLAN.md](UI_PLAN.md).
