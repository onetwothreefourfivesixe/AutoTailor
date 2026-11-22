"""Add jobs and emails: paste URLs or text, drop files; preview, then save."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from autotailor import intake, tasks
from autotailor.ui.common import connection, load_settings_or_stop, paths

KEY = "add.candidates"


def _extractor(settings):  # noqa: ANN001, ANN202
    from autotailor.chains.extract_posting import extract_posting_fields

    return lambda text: extract_posting_fields(text, settings)


def render() -> None:
    st.title("Add jobs and emails")
    _after_save()
    _form()


def _form() -> None:
    settings = load_settings_or_stop()
    st.caption("Nothing is saved until you confirm. LinkedIn, Handshake, Runway, Indeed and Glassdoor links "
               "aren't opened (their terms forbid it): paste the posting text instead.")  # fmt: skip

    t_urls, t_text, t_files = st.tabs(["Links", "Paste text or email", "Drop files"])
    new: list[intake.Candidate] = []
    with t_urls:
        urls = st.text_area("Job posting URLs, one per line", height=120, key="add.urls")
        if st.button("Read links", key="add.read_urls") and urls.strip():
            from autotailor.sources import make_client

            with st.spinner("Reading ..."), make_client() as client:
                new = intake.read_urls(urls, client, _extractor(settings))
    with t_text:
        text = st.text_area("A job posting's text, or a whole email (Gmail: ⋮ > Show original > copy all)", height=220, key="add.text")
        if st.button("Read text", key="add.read_text") and text.strip():
            with st.spinner("Reading ..."):
                new = [intake.read_text(text, _extractor(settings), settings)]
    with t_files:
        files = st.file_uploader("Drop .eml, .pdf, .html, .txt or .md files", accept_multiple_files=True,
                                 type=["eml", "pdf", "html", "htm", "txt", "md"], key="add.files")  # fmt: skip
        if st.button("Read files", key="add.read_files") and files:
            with st.spinner("Reading ..."):
                new = [intake.read_file(f.name, f.getvalue(), _extractor(settings), settings) for f in files]
    if new:
        with connection() as conn:
            intake.mark_duplicates(conn, new)
        st.session_state[KEY] = st.session_state.get(KEY, []) + new

    cands: list[intake.Candidate] = st.session_state.get(KEY, [])
    if not cands:
        return
    st.subheader("Preview")
    df = pd.DataFrame([{
        "keep": c.kind != "error" and not (c.kind == "email" and not c.roles),
        "status": c.status,
        "company": c.posting.company if c.posting else "",
        "title": c.posting.title if c.posting else (", ".join(r.title for r in c.roles[:3]) + (" …" if len(c.roles) > 3 else "")),
        "location": (c.posting.location or "") if c.posting else "",
        "from": c.source,
        "problem": c.error or "",
    } for c in cands])  # fmt: skip
    edited = st.data_editor(
        df, hide_index=True, width="stretch", key="add.preview",
        disabled=["status", "from", "problem"],
        column_config={"keep": st.column_config.CheckboxColumn("Save", width="small")},
    )  # fmt: skip
    c1, c2, _ = st.columns([1, 1, 4])
    if c1.button("Save", type="primary", key="add.save"):
        keep = []
        for c, row in zip(cands, edited.to_dict("records"), strict=True):
            if not row["keep"] or c.kind == "error":
                continue
            if c.posting is not None:  # apply the user's corrections
                c.posting = c.posting.model_copy(update={
                    "company": row["company"].strip() or c.posting.company,
                    "title": row["title"].strip() or c.posting.title,
                    "location": row["location"].strip() or None,
                })  # fmt: skip
            keep.append(c)
        with connection() as conn:
            report = intake.save(conn, keep, settings)
        st.session_state.pop(KEY, None)
        st.session_state["add.saved"] = report.new
        st.session_state["add.flash"] = f"Saved: {report.summary()}."
        st.rerun()
    if c2.button("Clear", key="add.clear"):
        st.session_state.pop(KEY, None)
        st.rerun()


def _after_save() -> None:
    if flash := st.session_state.pop("add.flash", None):
        st.success(flash)
    saved = st.session_state.get("add.saved") or []
    runway_new = [p for p in saved if p.startswith("runway:")]
    if not saved:
        return
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button(f"Screen the {len(saved)} new now", key="add.screen"):
        with connection() as conn:
            if runway_new:
                tasks.enqueue(conn, "resolve")
            tasks.enqueue(conn, "screen", [p for p in saved if not p.startswith("runway:")] or [])
        tasks.ensure_worker(paths().home, paths().data)
        st.session_state.pop("add.saved", None)
        st.toast("Queued. Follow it on the Runs page.")
    if runway_new and c2.button("Match Runway roles", key="add.resolve"):
        with connection() as conn:
            tasks.enqueue(conn, "resolve")
        tasks.ensure_worker(paths().home, paths().data)
        st.toast("Queued. Follow it on the Runs page.")

