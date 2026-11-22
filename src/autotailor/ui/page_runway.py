"""Runway roles: import emails, and answer the roles the resolve graph is waiting on."""

from __future__ import annotations

from contextlib import contextmanager

import pandas as pd
import streamlit as st

from autotailor import batch, db, mail, resolve
from autotailor.ui.common import connection, fmt_time, load_settings_or_stop, paths


@contextmanager
def _graph(conn, settings):  # noqa: ANN001, ANN202
    """The user's resolve graph over the shared checkpoint file, or None if not written yet."""
    from autotailor.chains.resolve_graph import build_resolve_graph
    from autotailor.sources import make_client

    with batch.open_checkpointer(paths().checkpoints) as saver, make_client() as client:
        try:
            graph = build_resolve_graph(resolve.Resolver(conn, client, settings), saver)
        except NotImplementedError:
            graph = None
        yield graph


def render() -> None:
    settings = load_settings_or_stop()
    st.title("Runway")
    st.caption(
        "Roles from Runway's notification emails. AutoTailor never opens Runway links; it reads the email, "
        "then looks for each role on the employer's own job board."
    )
    from autotailor.chains.resolve_graph import run_resolve

    c1, c2 = st.columns(2)
    if c1.button("Import emails from data/inbox"):
        with connection() as conn:
            result = mail.import_folder(conn, paths().inbox, settings)
        st.success(result.summary() if (result.messages or result.ignored) else f"No .eml files in `{paths().inbox}`.")
    if c2.button("Find open roles on job boards"):
        with connection() as conn, _graph(conn, settings) as graph, st.spinner("Checking employer job boards ..."):
            if graph is None:
                st.warning("The resolve graph isn't written yet (M7 exercise).")
            else:
                outs = [run_resolve(graph, pid) for pid in resolve.open_roles(conn)]
                st.success(f"{sum(o.status == 'resolved' for o in outs)} found, "
                           f"{sum(o.status == 'waiting' for o in outs)} need a link.")  # fmt: skip

    with connection() as conn:
        rows = db.list_runway_roles(conn)
        if not rows:
            st.info(f"No Runway roles yet. Save a Runway email (Gmail: ⋮ > Download message) into `{paths().inbox}`.")
            return
        st.dataframe(
            pd.DataFrame([
                {"Status": r["status"], "Match": r["match_score"], "Company": r["company"], "Role": r["title"],
                 "Location": r["location"], "Seen": fmt_time(r["seen_at"]), "Posting": r["posting_id"]}
                for r in rows
            ]),
            hide_index=True, width="stretch",
            column_config={"Match": st.column_config.NumberColumn(format="percent")},
        )  # fmt: skip

        st.subheader("Waiting for a link")
        with _graph(conn, settings) as graph:
            if graph is None:
                st.info("Answering needs the resolve graph (M7 exercise in src/autotailor/chains/resolve_graph.py).")
                return
            waiting = []
            for pid in resolve.open_roles(conn):
                snap = graph.get_state({"configurable": {"thread_id": _thread(pid)}})
                if snap.interrupts:
                    waiting.append((pid, snap.interrupts[0].value))
            if not waiting:
                st.caption("Nothing waiting. New roles are checked by `autotailor runway resolve` or the button above.")
                return
            for pid, q in waiting:
                _answer_form(graph, pid, q, run_resolve)


def _thread(pid: str) -> str:
    from autotailor.chains.resolve_graph import thread_id

    return thread_id(pid)


def _answer_form(graph, pid: str, q: dict, run_resolve) -> None:  # noqa: ANN001
    with st.container(border=True):
        st.markdown(f"**{q['company']} | {q['title']}**" + (f" · {q['location']}" if q.get("location") else ""))
        if q.get("runway_url"):
            st.markdown(f"[Open on Runway]({q['runway_url']}), then follow it to the employer's posting.")
        if q.get("error"):
            st.error(q["error"])
        url = st.text_input("Employer's posting URL", key=f"rw.url.{pid}")
        text = st.text_area("…or paste the posting text", key=f"rw.text.{pid}", height=100)
        c1, c2, _ = st.columns([1, 1, 4])
        answer = None
        if c1.button("Use this", key=f"rw.use.{pid}", type="primary"):
            answer = {"url": url} if url.strip() else {"text": text} if text.strip() else None
            if answer is None:
                st.warning("Enter a URL or paste the text first.")
        if c2.button("Skip role", key=f"rw.skip.{pid}"):
            answer = {"skip": True}
        if answer is not None:
            with st.spinner("Reading the posting ..."):
                out = run_resolve(graph, pid, answer)
            if out.status == "waiting":
                st.error(out.question.get("error") or "Still not found.")
            else:
                st.success(f"{out.status.capitalize()}.")
                st.rerun()
