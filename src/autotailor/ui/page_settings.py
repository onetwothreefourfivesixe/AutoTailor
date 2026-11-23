from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
import streamlit as st
from pydantic import ValidationError

from autotailor.config import STEPS, DegreeLevel, Settings
from autotailor.ui import logic
from autotailor.ui.common import PROVIDER_NAMES, lines, load_settings_or_stop, paths

PREFIX = "set."
PROVIDERS = ["ollama", "anthropic", "openai", "google_genai"]
EMBED_PROVIDERS = ["ollama", "openai", "google_genai"]


def _k(name: str) -> str:
    return PREFIX + name


def _reset() -> None:
    for k in [k for k in st.session_state if str(k).startswith(PREFIX)]:
        del st.session_state[k]


def _model_fields(label: str, spec: dict[str, Any], key: str, providers: list[str], chat: bool = True) -> dict[str, Any]:
    c1, c2 = st.columns([1, 2])
    provider = c1.selectbox(
        f"{label} provider", providers, index=providers.index(spec["provider"]) if spec["provider"] in providers else 0,
        format_func=lambda p: PROVIDER_NAMES.get(p, p), key=_k(key + ".provider"),
    )  # fmt: skip
    model = c2.text_input(f"{label} model", spec["model"], key=_k(key + ".model"),
                          help="The provider's model ID; for Ollama, a name from `ollama list`.")  # fmt: skip
    out = {**spec, "provider": provider, "model": model}
    if provider == "ollama":
        st.caption("Runs on this machine.")
        if chat:
            out["context_window"] = st.number_input(
                f"{label} context window (tokens)", 2048, 262144, spec.get("context_window", 16384), step=1024,
                key=_k(key + ".ctx"), help="Ollama silently cuts prompts longer than this.",
            )  # fmt: skip
    else:
        st.caption(f":orange[Sends posting and CV text to {PROVIDER_NAMES[provider]}.] Its API key goes in `.env`.")
    return out


def render() -> None:
    settings = load_settings_or_stop()
    data = settings.model_dump(mode="json")
    st.title("Settings")
    st.caption(f"Saved to `{paths().config}`. The CLI reads the same file.")

    tabs = st.tabs(["Profile", "Eligibility", "Runs", "Job sources", "Models", "AI log", "Resume template"])

    with tabs[0]:
        pr = data["profile"]
        st.markdown("**Education**: one row per degree, in progress or completed.")
        edu = pd.DataFrame(pr["education"] or [], columns=["level", "field", "status", "graduation_date", "school"])
        edu["graduation_date"] = pd.to_datetime(edu["graduation_date"]).dt.date
        edited = st.data_editor(
            edu, num_rows="dynamic", hide_index=True, width="stretch", key=_k("education"),
            column_config={
                "level": st.column_config.SelectboxColumn("Level", options=[d.value for d in DegreeLevel], required=True),
                "field": st.column_config.TextColumn("Major / field", required=True),
                "status": st.column_config.SelectboxColumn("Status", options=["in_progress", "completed"], required=True),
                "graduation_date": st.column_config.DateColumn("Graduation (expected)"),
                "school": st.column_config.TextColumn("School"),
            },
        )  # fmt: skip
        pr["education"] = [
            {k: (v.isoformat() if isinstance(v, date) else (None if pd.isna(v) else v)) for k, v in row.items()}
            for row in edited.to_dict("records")
            if not all(pd.isna(v) or v == "" for v in row.values())
        ]
        pr["locations"] = lines(st.text_area("Locations you'd work in (one per line)", "\n".join(pr["locations"]), key=_k("locations")))
        pr["open_to_remote"] = st.checkbox("Open to remote roles", pr["open_to_remote"], key=_k("remote"))
        pr["work_authorization"] = st.text_input("Work authorization", pr["work_authorization"], key=_k("auth"),
                                                 help="e.g. 'US citizen' or 'F-1, needs sponsorship'.")  # fmt: skip
        pr["role_interests"] = lines(st.text_area(
            "Role interests (one per line)", "\n".join(pr["role_interests"]), key=_k("interests"),
            help="Used to rank postings and in the fit score's 'interests' criterion.",
        ))  # fmt: skip

    with tabs[1]:
        el = data["eligibility"]
        fields = Settings.model_fields["eligibility"].annotation.model_fields
        el["higher_degree_satisfies_lower"] = st.checkbox(
            "A higher degree satisfies a lower requirement", el["higher_degree_satisfies_lower"],
            help=fields["higher_degree_satisfies_lower"].description, key=_k("higher"),
        )  # fmt: skip
        el["in_progress_counts"] = st.checkbox(
            "A degree in progress counts", el["in_progress_counts"],
            help=fields["in_progress_counts"].description, key=_k("inprog"),
        )  # fmt: skip
        el["graduation_window_slack_months"] = st.number_input(
            "Graduation window slack (months)", 0, 24, el["graduation_window_slack_months"],
            help=fields["graduation_window_slack_months"].description, key=_k("slack"),
        )  # fmt: skip
        el["keep_unclear"] = st.checkbox(
            "Keep postings with unclear requirements", el["keep_unclear"],
            help=fields["keep_unclear"].description, key=_k("unclear"),
        )  # fmt: skip
        st.caption("After changing these or your education, run `autotailor screen --rejudge` (no model calls).")

    with tabs[2]:
        rn, tl = data["run"], data["tailor"]
        c1, c2 = st.columns(2)
        rn["jobs_per_run"] = c1.number_input("Resumes per run (top X)", 1, 100, rn["jobs_per_run"], key=_k("jobs"))
        rn["min_fit_score"] = c2.slider("Minimum fit score", 0.0, 1.0, float(rn["min_fit_score"]), 0.05, key=_k("minfit"))
        rn["max_runs_per_day"] = c1.number_input("Runs per day, at most", 1, 100, rn["max_runs_per_day"], key=_k("day"))
        rn["max_runs_per_week"] = c2.number_input("Runs per 7 days, at most", 1, 500, rn["max_runs_per_week"], key=_k("week"))
        rn["shortlist_size"] = c1.number_input("Postings fit-scored per run", 1, 500, rn["shortlist_size"], key=_k("short"))
        rn["parallel_jobs"] = c2.number_input("Resumes tailored at once", 1, 16, rn["parallel_jobs"], key=_k("par"))
        st.markdown("**Tailored resumes**")
        c1, c2, c3 = st.columns(3)
        tl["min_bullets"] = c1.number_input("Fewest bullets", 1, 40, tl["min_bullets"], key=_k("minb"))
        tl["max_bullets"] = c2.number_input("Most bullets", 1, 40, tl["max_bullets"], key=_k("maxb"))
        tl["max_attempts"] = c3.number_input("Drafts before giving up", 1, 10, tl["max_attempts"], key=_k("att"))

    with tabs[3]:
        so = data["sources"]
        so["ats_boards"] = st.checkbox("Fetch from company job boards", so["ats_boards"], key=_k("ats"))
        st.caption("Company slugs, one per line: the name in the board's URL (`boards.greenhouse.io/<slug>`).")
        c1, c2, c3 = st.columns(3)
        so["greenhouse"] = lines(c1.text_area("Greenhouse", "\n".join(so["greenhouse"]), key=_k("gh")))
        so["lever"] = lines(c2.text_area("Lever", "\n".join(so["lever"]), key=_k("lever")))
        so["ashby"] = lines(c3.text_area("Ashby", "\n".join(so["ashby"]), key=_k("ashby")))
        c1, c2 = st.columns(2)
        so["title_include"] = lines(c1.text_area("Keep titles containing (one per line; empty keeps all)",
                                                 "\n".join(so["title_include"]), key=_k("inc")))  # fmt: skip
        so["title_exclude"] = lines(c2.text_area("Drop titles containing", "\n".join(so["title_exclude"]), key=_k("exc")))
        so["max_age_days"] = int(st.number_input("Skip postings older than (days; 0 for no cutoff)", min_value=0,
                                                 value=int(so.get("max_age_days", 21)), key=_k("age")))  # fmt: skip
        so["keep_undated"] = st.checkbox("Keep postings with no publish date", so.get("keep_undated", True), key=_k("undated"))
        st.caption("LinkedIn, Handshake, Runway, Indeed and Glassdoor are never fetched; paste those postings with `autotailor add`.")

    with tabs[4]:
        md = data["models"]
        st.markdown("**Default model** (used by every step without its own)")
        md["default"] = _model_fields("Default", md["default"], "default", PROVIDERS)
        for step in STEPS:
            own = step in md["steps"]
            with st.expander(f"Step: {step}" + ("" if not own else f" ({md['steps'][step]['provider']}:{md['steps'][step]['model']})")):
                use_own = st.checkbox("Use its own model", own, key=_k(f"step.{step}.own"))
                if use_own:
                    md["steps"][step] = _model_fields(step, md["steps"].get(step, md["default"]), f"step.{step}", PROVIDERS)
                else:
                    md["steps"].pop(step, None)
        st.markdown("**Embeddings** (pre-ranking postings)")
        md["embeddings"] = _model_fields("Embeddings", md["embeddings"], "emb", EMBED_PROVIDERS, chat=False)

    with tabs[5]:
        lg = data["log"]
        st.caption("Every model call can be recorded locally (Postings page > AI log). Nothing here leaves this machine.")
        lg["enabled"] = st.checkbox("Record model calls", lg["enabled"], key=_k("log.on"))
        lg["keep_prompts"] = st.checkbox("Keep the prompts and responses (not just timing and status)",
                                         lg["keep_prompts"], key=_k("log.text"))  # fmt: skip
        lg["retention_days"] = st.number_input("Keep for (days)", 1, 365, lg["retention_days"], key=_k("log.days"))
        from autotailor.ui.common import connection

        with connection() as conn:
            n = conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0]
            if st.button(f"Clear the log ({n} calls)", key=_k("log.clear"), disabled=n == 0):
                conn.execute("DELETE FROM llm_calls")
                conn.commit()
                st.rerun()

    with tabs[6]:
        data["resume"] = _resume_tab(data["resume"])

    st.divider()
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Save settings", type="primary"):
        try:
            logic.save_settings_checked(data, paths().config, paths().config_backups)
        except ValidationError as e:
            st.error("Not saved; fix these first:\n\n" + "\n".join(
                f"- `{'.'.join(str(x) for x in err['loc'])}`: {err['msg']}" for err in e.errors()
            ))  # fmt: skip
        else:
            st.success(f"Saved. The previous file is in `{paths().config_backups}`.")
    if c2.button("Discard changes"):
        _reset()
        st.rerun()


def _resume_tab(rs: dict[str, Any]) -> dict[str, Any]:
    """Pick the resume template; upload your own LaTeX or Word template; preview with your CV."""
    from autotailor.resume import latex
    from autotailor.resume.render import BUILTIN_TEMPLATES

    tdir = paths().templates
    tdir.mkdir(parents=True, exist_ok=True)
    tex = sorted(p.name for p in tdir.glob("*.tex")) + sorted(p.name for p in tdir.glob("*.tex.j2"))
    docx = sorted(p.name for p in tdir.glob("*.docx"))
    options = list(BUILTIN_TEMPLATES) + tex
    names = {**BUILTIN_TEMPLATES, **{t: f"{t} (yours, LaTeX)" for t in tex}}
    current = rs["template"] if rs["template"] in options else "jake"
    rs["template"] = st.selectbox("PDF template", options, index=options.index(current),
                                  format_func=lambda t: names[t], key=_k("res.tpl"))  # fmt: skip
    word = [""] + docx
    rs["word_template"] = st.selectbox(
        "Word template", word, index=word.index(rs["word_template"]) if rs["word_template"] in word else 0,
        format_func=lambda t: t or "Built-in Word layout", key=_k("res.word"),
    )  # fmt: skip
    if rs["template"] not in ("typst",):
        st.caption("LaTeX templates compile with Tectonic, downloaded into data/tools/ on first use (about 10 MB, then "
                   "the TeX packages it needs). No resume content leaves this machine.")  # fmt: skip

    with st.expander("Use your own template"):
        st.markdown(
            "**LaTeX**: start from Jake's template and change the look. Values go in as `((( name )))`, "
            "statements as `((* for s in sections *))`; all text is LaTeX-escaped for you, and `((( url|url )))` "
            "is for links. Available: `name`, `contact_items` (`text`, `url`), `sections` (`title`, `items` with "
            "`left1`, `right1`, `left2`, `right2`, `bullets`), `skills` (`name`, `items`).  \n"
            "**Word**: a .docx using the same names in docxtpl syntax, e.g. `{{ name }}`, "
            "`{%p for s in sections %}` ... `{%p endfor %}`."
        )
        st.download_button("Download Jake's template (.tex.j2)", latex.builtin("jake.tex.j2"), "jake.tex.j2", key=_k("res.dl"))
        up = st.file_uploader("Upload a .tex, .tex.j2 or .docx template", type=["tex", "j2", "docx"], key=_k("res.up"))
        if up is not None and st.button("Add template", key=_k("res.add")):
            (tdir / up.name).write_bytes(up.getvalue())
            st.success(f"Added {up.name}. Pick it above, then Save settings.")
            st.rerun()

    if st.button("Preview with my CV", key=_k("res.preview")):
        _preview(rs["template"])
    return rs


def _preview(template: str) -> None:
    from autotailor.cv import load_master_cv
    from autotailor.models import TailoredBullet, TailoredResume
    from autotailor.resume.layout import build_layout
    from autotailor.resume.render import pdf_page_count, pdf_to_png, render_resume

    try:
        cv = load_master_cv(paths().master_cv)
    except (OSError, ValueError) as e:
        st.error(f"Can't read the master CV: {e}")
        return
    ids = [b.id for e in cv.entries() for b in e.bullets][:12]  # a typical one-page selection
    sample = TailoredResume(bullets=[TailoredBullet(source_id=i, text=cv.bullets()[i].text) for i in ids])
    with st.spinner("Rendering (the first LaTeX render also sets up Tectonic) ..."):
        try:
            r = render_resume(build_layout(sample, cv), template, paths().templates, paths().tools)
        except Exception as e:  # LaTeX errors, missing files
            st.error(str(e))
            return
    if r.warning:
        st.warning(r.warning)
    pages = pdf_page_count(r.pdf)
    st.image(pdf_to_png(r.pdf), caption=f"{pages} page(s), 12 bullets from your CV")
