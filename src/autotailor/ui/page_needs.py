"""Everything waiting on you, each with its action."""

from __future__ import annotations

import streamlit as st

from autotailor import db, inbox, tasks
from autotailor.models import Verdict
from autotailor.ui.common import connection, fmt_time, load_settings_or_stop, paths

HEADINGS = {
    "runway_link": "Runway roles needing a link",
    "answer_choice": "Short answers: draft or points only?",
    "unclear": "Unclear eligibility near the top",
    "resume_pages": "Resumes over one page",
    "answer_problems": "Drafts with problems left",
    "interrupted_run": "Runs stopped part way",
}
GO_TO = {"runway_link": "Runway", "answer_choice": "Short answers", "answer_problems": "Short answers"}


def count() -> int:
    settings = load_settings_or_stop()
    with connection() as conn:
        return len(inbox.collect(conn, settings, paths().outputs))


def render() -> None:
    settings = load_settings_or_stop()
    st.title("Needs you")
    with connection() as conn:
        needs = inbox.collect(conn, settings, paths().outputs)
    if not needs:
        st.success("Nothing is waiting on you.")
        return
    for kind, heading in HEADINGS.items():
        items = [n for n in needs if n.kind == kind]
        if not items:
            continue
        st.subheader(f"{heading} ({len(items)})")
        if kind in GO_TO:
            st.caption(f"Answer these on the {GO_TO[kind]} page.")
        for n in items:
            with st.container(border=True):
                c1, c2 = st.columns([4, 2])
                c1.markdown(f"**{n.title}**  \n{n.detail}" + (f"  \n<small>{fmt_time(n.when)}</small>" if n.when else ""),
                            unsafe_allow_html=True)  # fmt: skip
                _action(n, c2)


def _action(n: inbox.Need, col) -> None:  # noqa: ANN001
    if n.kind == "unclear":
        a, b = col.columns(2)
        for btn, verdict in ((a, Verdict.ELIGIBLE), (b, Verdict.INELIGIBLE)):
            if btn.button(verdict.value.capitalize(), key=f"need.{verdict.value}.{n.ref}"):
                with connection() as conn:
                    db.set_override(conn, n.ref, verdict, "confirmed from the Needs-you list")
                st.rerun()
    elif n.kind == "interrupted_run":
        if col.button("Resume", key=f"need.resume.{n.ref}"):
            with connection() as conn:
                tasks.enqueue(conn, "resume", [n.ref])
            tasks.ensure_worker(paths().home, paths().data)
            st.toast("Queued. Follow it on the Runs page.")
    elif n.kind == "resume_pages":
        col.caption(f"Posting `{n.ref}`")
