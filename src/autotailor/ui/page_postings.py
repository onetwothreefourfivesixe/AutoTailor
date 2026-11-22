from __future__ import annotations

import pandas as pd
import streamlit as st

from autotailor import db
from autotailor.eligibility import passes_gate
from autotailor.ui.common import connection, load_settings_or_stop

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
        p = db.get_posting(conn, pid)
        m = matches.get(pid)
        artifacts = db.artifacts_for(conn, pid)

    st.subheader(f"{p.title}, {p.company}")
    links = [u for u in (p.canonical_url, p.source_url) if u]
    if links:
        st.markdown(" · ".join(f"[{'apply' if i == 0 else 'source'}]({u})" for i, u in enumerate(dict.fromkeys(links))))
    if m:
        st.markdown(f"**Eligibility: {m.verdict.value}**")
        st.markdown("\n".join(f"- {r}" for r in m.verdict_reasons) or "- no reasons recorded")
        if m.fit_score is not None:
            st.markdown(f"**Fit: {m.fit_score:.2f}**")
            st.markdown("\n".join(f"- {r}" for r in m.fit_reasons))
    if p.requirements:
        st.markdown("**Requirements read from the posting**")
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
        st.markdown("\n".join(f"- {k}: {v}" for k, v in stated.items()) or "- nothing stated")
        if r.evidence:
            st.markdown("Evidence:\n" + "\n".join(f'- {e.field}: "{e.quote}"' for e in r.evidence))
    if artifacts:
        st.caption("Tailored in run(s): " + ", ".join(sorted({str(a["run_id"]) for a in artifacts})))
    with st.expander("Posting text"):
        st.text(p.description or "(no description stored)")
