from __future__ import annotations

import pandas as pd
import streamlit as st

from autotailor import batch, db, matching
from autotailor.config import STEPS
from autotailor.llm import MODE_DESCRIPTIONS
from autotailor.models import Verdict
from autotailor.ui.common import connection, fmt_time, load_cv, load_settings_or_stop, where


def render() -> None:
    settings = load_settings_or_stop()
    st.title("AutoTailor")
    st.caption("Everything here is read from and saved to files on this machine.")

    models = settings.models
    mode = models.mode()
    (st.success if mode == "local" else st.warning)(f"Models: {MODE_DESCRIPTIONS[mode]}.")
    rows = [
        {"Step": step, "Model": f"{(s := models.for_step(step)).provider}:{s.model}", "Data": where(s)}
        for step in STEPS
    ]
    e = models.embeddings
    rows.append({"Step": "embeddings", "Model": f"{e.provider}:{e.model}", "Data": where(e)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    _, cv_error = load_cv()
    if cv_error:
        st.error(f"Master CV: {cv_error}")

    with connection() as conn:
        counts = db.table_counts(conn)
        matches = db.all_matches(conn)
        by_verdict = {v: sum(1 for m in matches.values() if m.verdict == v) for v in Verdict}
        unscreened = counts["postings"] - len(matches)
        c = st.columns(5)
        c[0].metric("Postings", counts["postings"])
        c[1].metric("Eligible", by_verdict[Verdict.ELIGIBLE])
        c[2].metric("Unclear", by_verdict[Verdict.UNCLEAR])
        c[3].metric("Ineligible", by_verdict[Verdict.INELIGIBLE])
        c[4].metric("Not screened", unscreened)

        st.subheader("Next run")
        picked = matching.shortlist(conn, settings)
        if picked:
            st.dataframe(
                pd.DataFrame([
                    {"Fit": m.fit_score, "Company": p.company, "Role": p.title, "Location": p.location, "ID": p.id}
                    for p, m in picked
                ]),
                hide_index=True, width="stretch",
                column_config={"Fit": st.column_config.NumberColumn(format="%.2f")},
            )  # fmt: skip
        else:
            st.info(f"No postings meet the minimum fit score ({settings.run.min_fit_score}) yet.")

        st.subheader("Recent runs")
        runs = db.list_runs(conn, 5)
        if runs:
            st.dataframe(
                pd.DataFrame([
                    {"Run": r["id"], "Started": fmt_time(r["started_at"]), "Status": r["status"],
                     "Tailored": batch.parse_counts(r).get("tailored"), "Not tailored": batch.parse_counts(r).get("failed")}
                    for r in runs
                ]),
                hide_index=True, width="stretch",
            )  # fmt: skip
            if any(r["status"] in ("running", "interrupted") for r in runs):
                st.warning("A run was stopped part way. `autotailor resume` continues it.")
        else:
            st.info("No runs yet.")

    st.subheader("Running the pipeline")
    st.markdown("Runs start from a terminal, so long model calls don't depend on this page staying open:")
    st.code(
        "autotailor run        # fetch, screen, rank, tailor\n"
        "autotailor tailor     # only tailor the current shortlist\n"
        "autotailor resume     # continue a run stopped with Ctrl-C",
        language="bash",
    )
