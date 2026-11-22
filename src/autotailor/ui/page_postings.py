from __future__ import annotations

import html
import json
import re

import pandas as pd
import streamlit as st

from autotailor import ailog, db, eligibility
from autotailor.eligibility import passes_gate
from autotailor.models import Verdict
from autotailor.ui.common import connection, fmt_time, load_cv, load_settings_or_stop, paths

FILTERS = ["eligible", "unclear", "ineligible", "not screened"]


def render() -> None:
    settings = load_settings_or_stop()
    st.title("Postings")
    st.caption("Every stored posting, including the ones dropped by eligibility, with the reasons.")
    with connection() as conn:
        postings = db.list_postings(conn, None)
        matches = db.all_matches(conn)
        if not postings:
            st.info("No postings yet. `autotailor fetch` or `autotailor add <url>`.")
            return

        c1, c2 = st.columns([2, 1])
        show = c1.multiselect("Show", FILTERS, default=FILTERS)
        search = c2.text_input("Search company or title")
        rows = []
        for p in postings:
            m = matches.get(p.id)
            verdict = m.verdict.value if m else "not screened"
            if verdict not in show:
                continue
            if search and search.lower() not in f"{p.company} {p.title}".lower():
                continue
            rows.append({
                "ID": p.id, "Company": p.company, "Role": p.title, "Location": p.location, "Verdict": verdict,
                "Fit": m.fit_score if m else None, "Similarity": m.prescore if m else None,
                "Ranked": bool(m and passes_gate(m.verdict, settings.eligibility)),
            })  # fmt: skip
        df = pd.DataFrame(rows)
        if df.empty:
            st.info("Nothing matches.")
            return
        df = df.sort_values(["Fit", "Similarity"], ascending=False, na_position="last")
        st.dataframe(
            df, hide_index=True, width="stretch",
            column_config={
                "Fit": st.column_config.NumberColumn(format="%.2f"),
                "Similarity": st.column_config.NumberColumn(format="%.2f"),
            },
        )  # fmt: skip

        pid = st.selectbox("Details for", df["ID"].tolist(),
                           format_func=lambda i: f"{df.set_index('ID').loc[i, 'Company']} | {df.set_index('ID').loc[i, 'Role']}")  # fmt: skip
    detail(pid, settings)


def detail(pid: str, settings) -> None:  # noqa: ANN001
    """One posting, as tabs: the text with evidence highlighted, eligibility (with override),
    fit, the tailored resume, short answers, and the AI calls made for it."""
    with connection() as conn:
        p = db.get_posting(conn, pid)
        m = db.get_match(conn, pid)
        artifacts = db.artifacts_for(conn, pid)
        questions = db.list_questions(conn, pid)
        calls = ailog.calls(conn, pid, limit=50)
    if p is None:
        st.warning("That posting is gone.")
        return
    st.subheader(f"{p.title}, {p.company}")
    links = [u for u in (p.canonical_url, p.source_url) if u]
    if links:
        st.markdown(" · ".join(f"[{'apply' if i == 0 else 'source'}]({u})" for i, u in enumerate(dict.fromkeys(links))))
    tabs = st.tabs(["Posting", "Eligibility", "Fit", "Resume", f"Answers ({len(questions)})", f"AI log ({len(calls)})"])
    with tabs[0]:
        _posting_tab(p)
    with tabs[1]:
        _eligibility_tab(p, m)
    with tabs[2]:
        _fit_tab(m)
    with tabs[3]:
        _resume_tab(artifacts)
    with tabs[4]:
        _answers_tab(questions)
    with tabs[5]:
        _ai_tab(calls)


def highlight(text: str, quotes: list[str]) -> str:
    """HTML of `text` with each quote marked (matched ignoring case and spacing)."""
    out = html.escape(text)
    for q in sorted({q.strip(" .\"'") for q in quotes if q.strip()}, key=len, reverse=True):
        words = [re.escape(html.escape(w)) for w in q.split()]
        if words:
            out = re.sub(r"\s+".join(words), lambda mt: f"<mark>{mt.group(0)}</mark>", out, flags=re.I)
    return out.replace("\n", "<br>")


def _posting_tab(p) -> None:  # noqa: ANN001
    quotes = [e.quote for e in p.requirements.evidence] if p.requirements else []
    if quotes:
        st.caption("Highlighted: the quotes the requirements were read from.")
        missing = [q for q in quotes if not eligibility.quote_found(q, p.description or "")]
        if missing:
            st.warning("Not found in the text (so not trusted): " + "; ".join(f'"{q}"' for q in missing))
    st.markdown(f"<div style='max-height: 32rem; overflow-y: auto'>{highlight(p.description or '(no description stored)', quotes)}</div>",
                unsafe_allow_html=True)  # fmt: skip


def _eligibility_tab(p, m) -> None:  # noqa: ANN001
    if m is None:
        st.info("Not screened yet.")
    else:
        st.markdown(f"**{m.verdict.value}**" + (f" (overridden; the rules said {m.original_verdict.value})" if m.original_verdict else ""))
        st.markdown("\n".join(f"- {r}" for r in m.verdict_reasons) or "- no reasons recorded")
    if p.requirements:
        r = p.requirements
        facts = {
            "Degree levels": ", ".join(d.value for d in r.degree_levels) or None,
            "Undergraduates only": "yes" if r.undergraduates_only else None,
            "Enrollment required": {True: "yes", False: "no", None: None}[r.enrollment_required],
            "Graduation between": (f"{r.graduation_earliest or '…'} and {r.graduation_latest or '…'}"
                                   if r.graduation_earliest or r.graduation_latest else None),
            "Majors": ", ".join(r.majors) or None,
            "Work authorization": r.work_authorization,
        }  # fmt: skip
        stated = {k: v for k, v in facts.items() if v}
        st.markdown("**Requirements read from the posting**\n" + ("\n".join(f"- {k}: {v}" for k, v in stated.items()) or "- nothing stated"))
    st.markdown("**Override**")
    st.caption("When the model or a rule got it wrong. Overrides win everywhere and survive re-screening.")
    c1, c2, c3 = st.columns([1, 3, 1])
    choice = c1.selectbox("Verdict", ["eligible", "ineligible"], key=f"ov.v.{p.id}")
    note = c2.text_input("Why", key=f"ov.n.{p.id}", placeholder="e.g. they confirmed by email that first-years can apply")
    if c3.button("Override", key=f"ov.s.{p.id}"):
        with connection() as conn:
            db.set_override(conn, p.id, Verdict(choice), note)
        st.rerun()
    if m is not None and m.original_verdict and st.button("Remove override", key=f"ov.c.{p.id}"):
        with connection() as conn:
            db.clear_override(conn, p.id)
        st.rerun()


def _fit_tab(m) -> None:  # noqa: ANN001
    if m is None or m.fit is None:
        st.info("No fit score yet (`autotailor rank`).")
        return
    from autotailor import matching

    st.markdown(f"**Fit {m.fit_score:.2f}** (weights: " + ", ".join(f"{k} {w:.0%}" for k, w in matching.FIT_WEIGHTS.items()) + ")")
    for name in matching.FIT_WEIGHTS:
        c = getattr(m.fit, name)
        st.markdown(f"- **{name.capitalize()} {c.score}/5**: {c.reason}")
    if m.fit.missing:
        st.markdown("**Missing:** " + "; ".join(m.fit.missing))
    cv, _ = load_cv()
    if cv and m.fit.relevant_bullet_ids:
        bullets = cv.bullets()
        st.markdown("**Most relevant CV bullets**")
        st.markdown("\n".join(f"- `{b}`: {bullets[b].text}" if b in bullets else f"- `{b}` (no longer in the CV)" for b in m.fit.relevant_bullet_ids))


def _resume_tab(artifacts) -> None:  # noqa: ANN001
    from autotailor.models import ArtifactKind, TailoredResume

    data = next((a for a in artifacts if a["kind"] == ArtifactKind.RESUME_DATA.value), None)
    if data is None:
        st.info("Not tailored yet.")
        return
    out = paths().outputs
    folder = (out / data["path"]).parent
    st.caption(f"Run {data['run_id']} · model `{data['model']}` · `{folder}`")
    try:
        resume = TailoredResume.model_validate_json((out / data["path"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st.warning("The saved resume file is missing.")
        return
    cv, _ = load_cv()
    c1, c2 = st.columns([3, 2])
    for a in artifacts:
        if a["run_id"] == data["run_id"] and a["kind"] in (ArtifactKind.RESUME_PDF.value, ArtifactKind.RESUME_DOCX.value):
            f = out / a["path"]
            if f.exists():
                c2.download_button(f.suffix[1:].upper(), f.read_bytes(), f.name, key=f"dl{a['id']}")
    pdf = next((out / a["path"] for a in artifacts
                if a["run_id"] == data["run_id"] and a["kind"] == ArtifactKind.RESUME_PDF.value), None)  # fmt: skip
    if pdf is not None and pdf.exists():
        from autotailor.resume.render import pdf_to_png

        try:
            c1.image(pdf_to_png(pdf.read_bytes()), caption="The saved PDF")
        except Exception as e:  # a damaged file shouldn't break the page
            c1.warning(f"No preview: {e}")
    if cv is not None:
        from autotailor.ui import logic

        with c2:
            st.markdown("**Against the master CV** (changed words underlined)")
            for row in logic.resume_diff(resume, cv):
                text = row.html if row.changed else html.escape(row.tailored)
                st.markdown(f"<small>`{row.source_id}`</small> {text}", unsafe_allow_html=True)
    history = json.loads((folder / "attempts.json").read_text(encoding="utf-8")) if (folder / "attempts.json").exists() else []
    if history:
        with st.expander(f"All {len(history)} drafts"):
            for i, h in enumerate(history, 1):
                st.markdown(f"**Draft {i}**: " + ("passed" if not h.get("errors") else f"{len(h['errors'])} problem(s)"))
                st.markdown("\n".join(f"- {e}" for e in h.get("errors", [])))


def _answers_tab(questions) -> None:  # noqa: ANN001
    if not questions:
        st.info("No questions. Add them on the Short answers page.")
        return
    for q in questions:
        st.markdown(f"**{q['text']}** · {q['status']}")
        if q["answer"]:
            st.write(q["answer"])


def _ai_tab(calls) -> None:  # noqa: ANN001
    if not calls:
        st.info("No model calls logged for this posting (calls are kept "
                f"{load_settings_or_stop().log.retention_days} days).")  # fmt: skip
        return
    for c in calls:
        where = "local" if c["local"] else f"sent to {c['provider']}"
        tokens = f", {c['tokens_in']}→{c['tokens_out']} tokens" if c["tokens_in"] else ""
        head = f"{fmt_time(c['at'])} · {c['step']} · {c['model']} ({where}) · {c['ms'] / 1000:.1f} s{tokens}"
        with st.expander(("FAILED · " if c["error"] else "") + head):
            if c["error"]:
                st.error(c["error"])
            for msg in json.loads(c["messages"] or "[]"):
                st.markdown(f"**{msg['role']}**")
                st.text(msg["content"][:6000])
            if c["response"]:
                st.markdown("**response**")
                st.text(c["response"][:6000])
