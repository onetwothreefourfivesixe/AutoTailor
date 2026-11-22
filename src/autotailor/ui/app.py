"""Streamlit entry point. Start with `autotailor ui` (binds to 127.0.0.1 only)."""

from __future__ import annotations

import streamlit as st

from autotailor.ui import page_needs, page_add, page_answers, page_compare, page_cv, page_overview, page_postings, page_runs, page_runway, page_settings

st.set_page_config(page_title="AutoTailor", layout="wide")

pages = [
    st.Page(page_overview.render, title="Overview", url_path="overview", default=True),
    st.Page(page_needs.render, title="Needs you", url_path="needs"),
    st.Page(page_add.render, title="Add jobs", url_path="add"),
    st.Page(page_settings.render, title="Settings", url_path="settings"),
    st.Page(page_cv.render, title="Master CV", url_path="cv"),
    st.Page(page_runs.render, title="Runs", url_path="runs"),
    st.Page(page_postings.render, title="Postings", url_path="postings"),
    st.Page(page_runway.render, title="Runway", url_path="runway"),
    st.Page(page_answers.render, title="Short answers", url_path="answers"),
    st.Page(page_compare.render, title="Compare models", url_path="compare"),
]
st.navigation(pages).run()
