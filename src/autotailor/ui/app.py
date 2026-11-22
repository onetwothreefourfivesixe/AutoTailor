"""Streamlit entry point. Start with `autotailor ui` (binds to 127.0.0.1 only)."""

from __future__ import annotations

import streamlit as st

from autotailor.ui import page_cv, page_overview, page_postings, page_runs, page_runway, page_settings

st.set_page_config(page_title="AutoTailor", layout="wide")

pages = [
    st.Page(page_overview.render, title="Overview", url_path="overview", default=True),
    st.Page(page_settings.render, title="Settings", url_path="settings"),
    st.Page(page_cv.render, title="Master CV", url_path="cv"),
    st.Page(page_runs.render, title="Runs", url_path="runs"),
    st.Page(page_postings.render, title="Postings", url_path="postings"),
    st.Page(page_runway.render, title="Runway", url_path="runway"),
]
st.navigation(pages).run()
