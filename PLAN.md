# AutoTailor plan

Working plan and progress tracker. Update the status boxes and the decision log as work lands.

## Goal

A local-first job application assistant. It pulls job postings, keeps the ones the user is eligible for and well matched to, tailors a resume from a master CV for each of the top X, and hands back the resumes alongside the postings. It never submits applications.

Later: for short-answer application questions, it suggests relevant experiences and points. Writing a full answer is opt-in per question, and all generated prose passes through a humanizer step.

## Principles

- **Local data.** Profile, CV, postings, outputs and run history live on disk (SQLite plus files). Nothing is stored on a server.
- **Swappable models.** Every model, chat steps and embeddings alike, is chosen in config: all local (Ollama), cloud only (Anthropic, OpenAI, Gemini; embeddings via OpenAI or Gemini), or mixed per step. `doctor` reports the mode.
- **No invention.** Tailored resumes may only select and rephrase content from the master CV. Every output bullet must trace to a source bullet ID.
- **Eligibility before fit.** A posting the user cannot apply to (wrong degree level, wrong graduation window) is filtered out before ranking, however high its match score.
- **Learning project.** The user implements the LangChain and LangGraph parts. Claude builds the plumbing around them and leaves stubs, concept notes and failing tests.

## Decisions so far

| Topic | Decision |
|---|---|
| Job sources | Public ATS APIs (Greenhouse, Lever, Ashby), pasted URL or text, and the user's Runway Radar |
| Master CV | Structured YAML with stable IDs and tags per experience and bullet |
| Resume output | PDF from a template (Typst or HTML), with DOCX export |
| Default model | Cloud API, with Ollama as the local option |
| Orchestration | LangChain for single steps; LangGraph for the per-job fan-out and the opt-in pause |
| Interface | CLI (Typer) plus a local web UI for settings and run history (Streamlit, bound to localhost) |
| Settings | Everything user-specific is configuration, never hard-coded: degree levels, graduation date, majors, jobs per run, run limits, model |
| Runway access | No automated access to the Runway site (their terms forbid it). Email digest and user-saved pages only |
| Work split | Scaffold plus TODO stubs for the user |

## Pipeline

```
sources -> normalize -> store -> eligibility gate -> match/rank -> top X
                                                                     |
                                              (per job, in parallel) v
                                   tailor resume -> verify -> render PDF/DOCX
                                   short answers -> suggest points -> [opt-in] draft -> humanize
```

### Data model (SQLite + Pydantic)

- `Profile`: degree level (in progress and completed), graduation date, majors, locations, work authorization, role interests.
- `MasterCV`: experiences, projects, education, skills; each bullet has an ID and tags.
- `JobPosting`: source, source URL, canonical URL, company, title, location, posted date, full description, external match score (if the source gives one), and `Requirements` (below).
- `Requirements`: degree levels accepted, graduation window, enrollment requirement, majors, work authorization, plus the quoted evidence for each field.
- `Match`: eligibility verdict with reasons, fit score with reasons.
- `Run` and `Artifact`: what was generated for which posting, with which model.

## Settings and history UI

One settings store (`config.toml` for settings, SQLite for history). The CLI and the UI read and write the same store, so either can be used alone.

**Settings (all editable in the UI):**

- Profile: degree levels (completed and in progress), graduation date, majors, locations, work authorization, role interests.
- Eligibility rules: for example, whether "Bachelor's required" admits a current Master's student; how strictly to treat graduation windows.
- Run settings: jobs tailored per run (X), maximum runs per day or week, which sources are enabled, minimum fit score.
- Models: provider and model per step, with a visible marker on any step that sends data off the machine.

**Master CV editor:**

- Edit the CV in the UI: add, change, reorder and remove entries and bullets in every section, and edit tags, contact details and skills.
- Saves go through the same `MasterCV` validation as the CLI; an invalid CV is never written.
- IDs are generated for new entries and bullets and are not editable afterwards, so past runs keep pointing at the right text.
- Deleting a bullet that past resumes used asks for confirmation and shows where it was used.
- Each save keeps the previous file as a timestamped backup under `data/cv_backups/`.
- Later option: import from a PDF or DOCX resume through an extraction chain, with the result shown for review before saving. (The first CV was converted by hand from the user's PDF.)

**History (read-only views):**

- Runs: date, sources, counts pulled, dropped as ineligible, flagged unclear, tailored.
- Per posting: requirements extracted with quoted evidence, eligibility verdict and reasons, fit score and reasons.
- Per output: the tailored resume, a diff against the master CV, the model used, and the short-answer suggestions or drafts.
- Dropped postings stay visible with their reason, so a wrong rule can be spotted and corrected.

## Eligibility gate

Purpose: make sure pulled roles match the education level and graduation timing the posting requires. Runway's own match score does not enforce this; the Radar and Explore lists include roles aimed at Master's, PhD, MBA and specific graduating classes.

1. **Title pass (cheap, no model).** Patterns such as "Master's Degree", "PhD Intern", "MBA Internship", "Undergraduate", "2027 Graduate", "Summer 2027", "New Grad". Used to drop obvious mismatches early and to prioritize which descriptions to fetch.
2. **Description pass (model, structured output).** Extract `Requirements` from the full posting, with the supporting quote for each field.
3. **Compare to `Profile`.** Verdicts:
   - `eligible`: all stated requirements met.
   - `ineligible`: a stated requirement clearly fails. Dropped, but kept in the store with the reason.
   - `unclear`: the posting is silent or ambiguous. Kept and flagged for the user, never silently dropped.
4. Only `eligible` and `unclear` postings go on to ranking.

Rules to settle with the user: whether "Bachelor's required" admits a current Master's student, how to treat "graduating between X and Y" ranges, and whether "preferred" qualifications count as soft signals only.

## Runway ingestion

What the screenshots show:

- **Explore**: a table of roles with title, company, location, posted date, match score and a track button; sortable by match score; filterable (for example, last 30 days).
- **My Radar**: saved filter chips (Internship, Hybrid, In Person, Remote Only, Remote OK, 7 locations), a "new roles" section, a "tracked roles" section, weekly notifications turned on, and a Share button.
- Rows carry no description, so each role must be followed to its original posting to read education requirements.

### Runway's policies (checked 2026-11-15)

Source: Terms of Use at https://www.joinrunway.io/terms (Runway Labs, Inc.; no last-updated date shown), read through a summarizing fetch, so re-read the original before relying on exact wording.

- Section 2.3(v) forbids using "any robot, spider, scraper, or other automated means to access, copy, or collect Job Listings", and extracting data to build a competing product or dataset.
- Section 2.3 also forbids accessing the site "by any means other than the interface provided".
- Section 2.4 forbids reverse engineering.
- Section 2.3(vi) forbids sharing account access.
- The terms mention no API. `app.joinrunway.io/robots.txt` disallows `/api/` and the exact `/explore` URL.

Consequences (Claude's reading, not legal advice):

- **Ruled out:** a Playwright or other scripted session against the app, calling the app's internal API, and scripted fetching of the Radar Share link.
- **Still open:** processing what Runway sends or shows the user through its normal interface, on the user's own machine, for the user's own applications.

### Approach

1. **Notification email (primary).** Parse the digest from a local mailbox (IMAP or `.eml` export). Runway delivers it to the user; nothing touches their site. Format confirmed from a sample dated 2026-11-13:
   - Sender `hello@tower.joinrunway.io`, subject "These N roles went live today...", campaign `daily-job-notification` (daily, despite the Radar button saying weekly). HTML part only, no plain text.
   - Per role: title, company, location, "posted about N hours ago", match score, and a "View Role" link.
   - The link is a Postmark tracking URL (`track.pstmrk.it/...`) wrapping `app.joinrunway.io/job/<id>`. The Runway job ID can be recovered by URL-decoding the link text offline.
   - No description and no link to the employer's posting.
   - Parser rules: never request any link in the email (that would be automated access to Runway, and the same email carries an unsubscribe link). Decode the job ID offline for dedupe, and show the Runway link to the user as a clickable reference only. Convert the relative posted time to an absolute date using the email's `Date` header.
2. **User-saved page.** The user opens Radar in their browser and saves the page; AutoTailor parses the saved file. Access is manual and through the provided interface. This is the grey end of what is allowed; keep it to the user's own Radar and personal use.
3. **Ask Runway.** Email them about an export or permission for personal use. Worth doing in parallel; an explicit yes would remove the grey area.

### Mail intake

The user wants to forward Runway mail to a local server that feeds the pipeline. Constraint: a home machine cannot receive mail forwarded from Gmail's servers without a public mail endpoint (inbound port 25 is usually blocked, and a relay service would see the mail). So "forwarding" is one of:

- **A. Local SMTP listener (`aiosmtpd` on `localhost:8025`).** A desktop mail client on the same machine (Thunderbird, Outlook) gets an extra outgoing server pointing at localhost; forwarding a message to `inbox@autotailor.local` hands it straight to AutoTailor. Fully local, no credentials stored. Does not work from Gmail's web or phone apps.
- **B. IMAP pull of a label (automatic).** A Gmail filter labels Runway mail; AutoTailor polls only that label with an app password kept in the OS keyring. Nothing to forward by hand; mail goes directly from Gmail to the machine.
- **C. Public inbound relay (tunnel or inbound-mail webhook).** Works from any device, but a third party handles the mail and the machine exposes an endpoint. Not planned; conflicts with the local-data principle.
- **D. Drop folder.** `.eml` files saved into `inbox/`. Always available as the fallback and for tests.

All intakes produce the same thing: a raw message handed to the Runway parser, stored, and queued for the next run (subject to the run caps). Only mail from the Runway sender address is accepted; anything else is ignored. Decided 2026-11-15: D is the default, B is built next (the user mainly uses Gmail in the browser), and A is an optional extra after that. Each intake is switched on in settings; only D is on out of the box.

After import:

- **Resolve** each role to the original posting on the employer's own site, then fetch the full description from there (through the ATS connectors where it is Greenhouse, Lever or Ashby), not from Runway. The email carries no outbound link, so resolution works like this:
  1. Map the company name to its public job board (Greenhouse, Lever, Ashby; a local company-to-board table that grows over time) and match by title and location.
  2. If no board is known or no confident match is found, mark the role `needs link` in the UI. The user opens the Runway link themselves and pastes the employer URL or the posting text.
  3. Roles stay in the store unresolved rather than being dropped; the title pass of the eligibility gate still runs on them.
- **Deduplicate** by canonical URL, then by company plus title plus location (the lists contain near-duplicates such as the same role under two related company names).
- **Keep Runway's match score** as one input, not the verdict. AutoTailor's own eligibility gate and fit score decide.
- Tracked roles are treated as user-endorsed and skip ranking, but still pass the eligibility gate.

## Milestones

Legend: C = Claude builds, U = user implements from stubs.

- [x] **M0 Skeleton.** C: repo, config, SQLite schema, Pydantic models, CV YAML schema, CLI shell, model factory.
- [x] **M1 Ingestion.** C (done): ATS connectors, URL and paste import, dedupe, `fetch`/`add`/`jobs` commands. U (done): first chain, posting text to `PostingFields`, in `src/autotailor/chains/extract_posting.py`.
- [x] **M2 Eligibility.** C (done): title patterns, profile comparison, quote verification, verdict storage, `screen` command. U (done; live tests flaky on the 4B model): `Requirements` extraction chain with quoted evidence, in `src/autotailor/chains/extract_requirements.py`.
- [x] **M3 Matching.** C (done): embeddings (local Ollama `nomic-embed-text` by default, or OpenAI/Gemini; cached in SQLite), prescore, shortlist, `rank`/`shortlist` commands. U (done; live test passes on the 4B model): fit-scoring chain with a rubric, in `src/autotailor/chains/score_fit.py`.
- [x] **M4 Tailoring graph.** C (done): verifier, layout, Typst PDF and Word renderers, `tailor` command, outputs folder, runs/artifacts records, one shortlist slot per role. U (done; offline tests pass, live test not yet run): LangGraph draft → verify → retry graph in `src/autotailor/chains/tailor_graph.py`.
- [x] **M5 Batch run.** C (done): `batch.py` (jobs, worker, run limits, SQLite checkpointer, Ctrl-C handling, outputs, `report.md`), `tailor` on the batch graph, `resume`, `runs`, end-to-end `run`. U (done): fan-out with `Send`, reducer fan-in, checkpointed resume, in `src/autotailor/chains/batch_graph.py`.
- [x] **M6 Settings, CV editor and history UI.** C (done): Streamlit app (`autotailor ui`, 127.0.0.1 only, usage stats off) with Overview, Settings, Master CV (structured and raw YAML editing), Runs and Postings pages. No user exercise.
- [ ] **M7 Runway.** C (done): email parser (`sources/runway.py`), intakes (drop folder, Gmail IMAP label pull, local SMTP listener) into one import step (`mail.py`), resolver (`resolve.py`: learned board, configured boards, slugs guessed from name and logo domain; or the user's URL/text), schema v4, `mail`/`runway` commands, Runway UI page, `run` includes mail and auto-matching. U (open): resolve graph with `interrupt()` and `Command(resume=...)`, in `src/autotailor/chains/resolve_graph.py` (replaces "wire it into the graph as a source node": interrupts were planned for M7, and this is where the pipeline genuinely needs the user).
- [ ] **M8 Short answers.** C: question storage, CLI and UI prompts. U: interrupt for opt-in, draft node, humanizer node.
- [ ] **M9 Local models.** C: Ollama config and docs. U: swap models and compare outputs on saved runs.

Concept order for the user: prompts and structured output (M1, M2), composing chains (M3), graph state and conditional edges (M4), fan-out and checkpoints (M5), interrupts (M7).

## Humanizer

A final rewrite node applied to every piece of generated prose. Its prompt carries a rubric based on Wikipedia's "Signs of AI writing" (inflated claims, stock phrases, repetitive structure, filler). It must not change facts; the bullet-trace verifier runs again after it.

## Open questions

- Whether a saved Radar page is still wanted now that the email works (it would add tracked roles, which the email omits).
- Whether the user wants to ask Runway for an export or permission.
- "Number of runs" is read as two settings: jobs tailored per run, and a cap on runs per day or week. Confirm.
- Resume template: one fixed layout, or several.
- Handshake and LinkedIn: paste-only for now; revisit after M6.

## Status log

- 2026-11-15: Plan written. No code yet. Next: M0.
- 2026-11-15: Profile and run settings made configurable; settings and history UI added (M6). Runway terms checked: automated access ruled out, ingestion limited to email digest and user-saved pages.
- 2026-11-15: Sample Runway email analyzed; email is the primary Runway source. It has no descriptions or employer links, so roles are resolved through employer job boards or a user-pasted link. `.eml` files are git-ignored (they contain personal data).
- 2026-11-15: M0 done (uv project, settings, domain models, CV schema, SQLite, model factory, CLI; 14 tests passing). Nothing committed yet. Education is a list of `[[profile.education]]` entries (level, field, status, graduation date). Next: M1.
- 2026-11-15: User's CV converted into `cv/master.yaml` (26 entries, 51 bullets, text verbatim); CV schema gained `volunteer`, `awards`, `certifications`. Profile set: B.A. in progress, UC Berkeley, expected May 2029 (day of month assumed). CV editor added to the M6 UI scope. Local model path smoke-tested: Ollama is running on this machine with `qwen3-vl:4b-instruct`; plain and structured-output calls work through `get_chat_model` (first call ~40 s to load the model). User is a first-year undergraduate, so the eligibility gate will drop most "2027 graduate", Master's and PhD roles.
- 2026-11-17: M1 Claude half done: Greenhouse/Lever/Ashby connectors (checked against live APIs), URL and paste import, title filter, dedupe by URL then company+title+city, DB schema v2 with migration. User's title filter set to intern/internship/co-op; no boards configured yet. Waiting on the user's extraction chain exercise. LinkedIn/Handshake/Runway/Indeed/Glassdoor URLs are refused for fetching.
- 2026-11-17: M1 user chain done (passes offline tests; live test flaky on the 4B local model: sometimes puts 'Hybrid' in location). M2 Claude half done: title pass (PhD/MBA/Master's/Undergraduate, class-year patterns), requirements vs profile with configurable rules, quotes must be found in the posting or an ineligible finding is downgraded to unclear, verdicts stored in `matches`, `screen` command with batching and `--rejudge`. Requirements.evidence changed from dict to a list of {field, quote} so OpenAI strict mode accepts the schema. Waiting on the user's requirements chain. Note: on /mnt/d the `anthropic` package takes ~28 s to import cold under WSL.
- 2026-11-18: M3 Claude half done. Prescore = mean of top-5 CV-bullet cosine similarities, blended 70/30 with role-interest similarity. Fit score = weighted rubric (skills .35, experience .35, level .15, interests .15) computed in code from the chain's 1-5 scores; unknown bullet IDs are dropped. Schema v3. Prompt loader now fills any example variables. Known issue for M4: boards like Palantir post one role per city, so the shortlist fills with copies; tailor once per company+title.
- 2026-11-18: Cloud-only mode added: embeddings can be OpenAI or Gemini (checked live with `gemini-embedding-001`, 3072 dims). Prefixes now chosen automatically per model. Embedding cache keyed by provider:model. `doctor` reports all local / mixed / cloud only, checks keys for every cloud model, and checks Ollama and pulled models when any model is local.
- 2026-11-19: User's config switched to all local (every step `qwen3-vl:4b-instruct`, embeddings `nomic-embed-text`). Found Ollama silently truncates prompts at its default context (a ~8k-token prompt was read as 2,050 tokens); added `context_window` (default 16384, passed as `num_ctx`). The full-CV fit prompt is ~3.1k tokens.
- 2026-11-21: M4 Claude half done. Verifier: each bullet must cite a real CV bullet ID; rewrites may not add numbers, or capitalized/technical terms absent from the source bullet, its entry and the CV skills; length ≤ 1.3x source (+40 chars); 6-14 bullets; skills must be CV skills. No false alarms on all 51 of the user's bullets. PDF via the `typst` package (bundled New Computer Modern font, data passed as JSON so no escaping), Word via python-docx. Outputs: `data/outputs/<date>-run<N>/<NN>-<company>-<title>/` with PDF, DOCX, tailored.json, posting.md. Shortlist now takes one slot per company+title. Reference solution for the exercise was checked against the tests in the scratch folder only.
- 2026-11-21: M3 live test fixed: the model copied bullet IDs with their brackets; `FitAssessment.relevant_bullet_ids` and `TailoredBullet.source_id` now strip them, and the prompt says so. User's M4 graph passes the offline tests.
- 2026-11-21: M5 Claude half done. Each run is one LangGraph thread (`run-<id>`) in `data/checkpoints.db` (`langgraph-checkpoint-sqlite`). Checked by experiment: when a task raises or the user presses Ctrl-C, finished tasks' writes are saved and `invoke(None)` reruns only the rest; a hard kill (`kill -9`) loses the whole step's writes, because LangGraph saves them at the end of the step. After Ctrl-C `get_state().next` can be empty for an unfinished run, so resume checks for saved values instead. Ctrl-C cancels jobs not yet started; SIGTERM/SIGHUP do the same. A graph invoked inside a node inherits the parent's checkpointer, so the worker runs the M4 graph in a fresh context. Run limits count runs started today / in the last 7 days, excluding failed ones; resuming doesn't count. Without the M5 graph, `tailor` falls back to one job at a time with no resume. Reference solution checked against the tests and a real Ctrl-C/resume in the scratch folder only.
- 2026-11-21: User's M5 batch graph done (all 6 tests pass). M6 done: Streamlit 1.67 UI. Settings and CV saves are validated before writing and back up the previous file (`data/config_backups/`, `data/cv_backups/`). CV IDs are generated on save (`<section>-<slug>`, bullets `<entry id>-<slug>`), never edited, and never reuse an ID a past run refers to. Deleting a bullet used by a past resume needs a second Save, and the warning lists the runs. Runs page shows each tailored bullet beside its CV source with reworded words marked. The pipeline itself is started from the terminal only. The user's real CV round-trips through the YAML writer unchanged, and every page renders on the real data. Not done from the plan: per-run counts of pulled/ineligible/unclear (runs only record tailoring), and short-answer views (M8).
- 2026-11-21: User added 139 job boards (90 Greenhouse, 46 Ashby, 3 Lever), each checked live. Most large NASDAQ companies (Apple, Microsoft, Alphabet, Amazon, Meta, Nvidia, Tesla, ...) use Workday or their own sites and can't be fetched; a Workday connector would be the next source to consider.
- 2026-11-21: M7 Claude half done. Parser reads title, company, location, match score, posted time (relative to the Date header) and the company domain from the logo file name; the Runway job ID is decoded offline from the Postmark link. Nothing in the email is ever requested. All 10 roles of the real sample parse. Forwarded mail is accepted (as attachment, or inline with the quoted From line). Placeholders dedupe against board postings already stored (Runway's score is kept). `title_include` is not applied to Runway roles (the radar is already the user's filter); `title_exclude` is. On the real sample, 3 of 10 roles matched a board automatically (the rest are on Workday-type sites or closed since 2026-11-13). OS keyring is locked under WSL, so the IMAP password falls back to `AUTOTAILOR_IMAP_PASSWORD` in `.env`. The SMTP listener accepts lines over 1000 characters (some clients forward HTML that way). Reference solution checked against the tests, the CLI (refused Runway link, skip, leave for later, no re-run on the next pass) and the UI page, in the scratch folder only.
