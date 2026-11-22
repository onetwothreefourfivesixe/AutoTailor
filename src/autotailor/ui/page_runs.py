from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from autotailor import batch, db
from autotailor.models import TailoredResume
from autotailor.ui import logic
from autotailor.ui.common import connection, fmt_time, load_cv, paths


def _not_tailored(report_text: str) -> str | None:
    """The report's 'Not tailored' section, if any."""
    if "## Not tailored" not in report_text:
        return None
    return report_text.split("## Not tailored", 1)[1].strip()


def render() -> None:
    from autotailor.ui import tasks_panel

    st.title("Runs")
    st.caption("Work runs in a background process: you can leave this page, and Stop keeps finished resumes.")
    tasks_panel.start_buttons()
    tasks_panel.live_tasks()
    st.subheader("Past runs")
    with connection() as conn:
        runs = db.list_runs(conn, None)
        if not runs:
            st.info("No runs yet.")
            return
        st.dataframe(
            pd.DataFrame([
                {"Run": r["id"], "Started": fmt_time(r["started_at"]), "Finished": fmt_time(r["finished_at"]),
                 "Status": r["status"], "Tailored": batch.parse_counts(r).get("tailored"),
                 "Not tailored": batch.parse_counts(r).get("failed"), "Model": logic.run_model(r)}
                for r in runs
            ]),
            hide_index=True, width="stretch",
        )  # fmt: skip

        run_id = st.selectbox("Run", [r["id"] for r in runs], format_func=lambda i: f"Run {i}")
        run = db.get_run(conn, run_id)
        outputs = logic.run_outputs(conn, run_id, paths().outputs)
    folder = batch.run_folder(paths().outputs, run)
    st.caption(f"Folder: `{folder}`")
    if run["status"] in ("running", "interrupted"):
        st.warning(f"This run was stopped part way. `autotailor resume {run_id}` continues it.")

    report = folder / "report.md"
    if report.exists() and (failed := _not_tailored(report.read_text(encoding="utf-8"))):
        with st.expander("Not tailored", expanded=False):
            st.markdown(failed)

    if not outputs:
        st.info("This run saved no resumes.")
        return
    cv, _ = load_cv()
    for o in outputs:
        with st.expander(f"{o.index}. {o.company} | {o.title}"):
            c1, c2, c3 = st.columns(3)
            if o.pdf and o.pdf.exists():
                c1.download_button("PDF", o.pdf.read_bytes(), o.pdf.name, mime="application/pdf", key=f"pdf{run_id}{o.posting_id}")
            if o.docx and o.docx.exists():
                c2.download_button(
                    "Word", o.docx.read_bytes(), o.docx.name, key=f"docx{run_id}{o.posting_id}",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )  # fmt: skip
            c3.caption(f"Model: `{o.model}`  \nPosting: `{o.posting_id}`")
            if not (o.data and o.data.exists()):
                continue
            resume = TailoredResume.model_validate_json(o.data.read_text(encoding="utf-8"))
            if cv is None:
                st.write([b.text for b in resume.bullets])
                continue
            st.markdown("**Bullets, against the master CV** (wording changed by the model is underlined)")
            for row in logic.resume_diff(resume, cv):
                if row.original is None:
                    st.markdown(f"- `{row.source_id}` (no longer in the CV): {row.tailored}")
                elif row.changed:
                    st.markdown(f"- `{row.source_id}`: {row.html}  \n  <small>CV: {html.escape(row.original)}</small>",
                                unsafe_allow_html=True)  # fmt: skip
                else:
                    st.markdown(f"- `{row.source_id}`: {html.escape(row.tailored)} <small>(unchanged)</small>", unsafe_allow_html=True)
            if resume.skills:
                st.markdown("**Skills:** " + ", ".join(resume.skills))
