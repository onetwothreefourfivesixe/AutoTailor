"""Start background work and watch it (M10). Used at the top of the Runs page."""

from __future__ import annotations

import json

import streamlit as st

from autotailor import batch, db, tasks
from autotailor.ui.common import connection, fmt_time, load_settings_or_stop, paths


def _start(kind: str, args: list[str] | None = None) -> None:
    with connection() as conn:
        tid = tasks.enqueue(conn, kind, args)
    tasks.ensure_worker(paths().home, paths().data)
    st.toast(f"Queued: {tasks.LABELS[kind]} (task {tid})")


def start_buttons() -> None:
    settings = load_settings_or_stop()
    with connection() as conn:
        why = batch.limit_message(conn, settings.run)
        unfinished = db.unfinished_runs(conn)
    st.subheader("Start")
    c = st.columns(4)
    if c[0].button("Whole pipeline", type="primary", help="Runway mail, fetch, matching, screen, rank, tailor"):
        _start("run")
    if c[1].button("Fetch job boards"):
        _start("fetch")
    if c[2].button("Screen"):
        _start("screen")
    if c[3].button("Rank"):
        _start("rank")
    c = st.columns(4)
    ignore = c[1].checkbox("Ignore run limits", value=False, help=why or "Within today's and this week's limits.")
    if c[0].button("Tailor shortlist", disabled=bool(why) and not ignore):
        _start("tailor", ["--ignore-limits"] if ignore else [])
    if c[2].button("Match Runway roles"):
        _start("resolve")
    if c[3].button("Suggest answer points"):
        _start("answers")
    if why:
        st.caption(f"Run limit: {why}")
    with st.form("cmd", clear_on_submit=False, border=False):
        c1, c2 = st.columns([5, 1])
        text = c1.text_input("Command", placeholder="e.g. rank --rescore · tailor lever:abc --force · compare score -m ollama:gemma4:12b",
                             label_visibility="collapsed")  # fmt: skip
        if c2.form_submit_button("Run") and text.strip():
            try:
                args = tasks.parse_command(text)
            except ValueError as e:
                st.error(str(e))
            else:
                _start("cli", args)
    st.caption("Any `autotailor` command (see `autotailor --help`); output appears below.")
    for r in unfinished[:3]:
        if st.button(f"Resume run {r['id']} (stopped {fmt_time(r['started_at'])})", key=f"resume{r['id']}"):
            _start("resume", [str(r["id"])])


@st.fragment(run_every=2)
def live_tasks() -> None:
    """Queue and progress, refreshed every 2 seconds without rerunning the whole page."""
    with connection() as conn:
        rows = tasks.recent(conn, 8)
    if not rows:
        return
    running = tasks.worker_running(paths().data)
    st.subheader("Background tasks")
    if any(r["status"] == "queued" for r in rows) and not running:
        st.warning("Tasks are queued but the worker isn't running.")
        if st.button("Start worker"):
            tasks.ensure_worker(paths().home, paths().data)
    for r in rows:
        label = f"{tasks.LABELS.get(r['kind'], r['kind'])} {' '.join(json.loads(r['args_json']))}".strip()
        head = f"**{label}** · task {r['id']} · {r['status']} · queued {fmt_time(r['created_at'])}"
        with st.container(border=True):
            c1, c2 = st.columns([5, 1])
            c1.markdown(head)
            if r["status"] in ("queued", "running"):
                if c2.button("Stop" if r["status"] == "running" else "Cancel", key=f"stop{r['id']}"):
                    with connection() as conn:
                        tasks.cancel(conn, r["id"])
            if r["status"] == "running":
                if r["total"]:
                    st.progress(min(1.0, (r["done"] or 0) / r["total"]), text=r["message"] or "")
                elif r["message"]:
                    st.caption(r["message"])
            if r["log"]:
                with st.expander("Output", expanded=r["status"] == "running"):
                    st.code(r["log"][-4000:], language=None)
