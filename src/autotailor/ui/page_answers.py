"""Short-answer questions: add them, get suggested points, opt in to drafts, edit the result."""

from __future__ import annotations

import json
from contextlib import contextmanager

import streamlit as st

from autotailor import batch, db
from autotailor.answers import AnswerTools, parse_question, save_outcome, word_count, write_answers
from autotailor.ui.common import connection, load_cv, load_settings_or_stop, paths


@contextmanager
def _graph(conn, cv, settings):  # noqa: ANN001, ANN202
    from autotailor.chains.answer_graph import build_answer_graph
    from autotailor.llm import get_chat_model

    with batch.open_checkpointer(paths().checkpoints) as saver:
        tools = AnswerTools(conn, cv, settings)
        try:
            graph = build_answer_graph(tools, get_chat_model(settings, "tailor"), get_chat_model(settings, "humanize"),
                                       saver, settings.tailor.max_attempts)  # fmt: skip
        except NotImplementedError:
            graph = None
        yield graph, tools


def _save(conn, qid: int, out, model: str) -> None:  # noqa: ANN001
    save_outcome(conn, qid, out, model)


def render() -> None:
    settings = load_settings_or_stop()
    st.title("Short answers")
    st.caption("Suggested points come from your CV. A written draft is only made when you ask for one, and it is "
               "checked for invented facts, the word limit and stock AI phrasing.")  # fmt: skip
    cv, err = load_cv()
    if cv is None:
        st.error(err)
        return
    spec = settings.models.for_step("tailor")
    model = f"{spec.provider}:{spec.model}"

    with connection() as conn:
        postings = [p for p in db.list_postings(conn, None) if p.description]
        asked = {q["posting_id"] for q in db.list_questions(conn)}
        postings.sort(key=lambda p: (p.id not in asked, p.company.lower()))
        if not postings:
            st.info("No postings with a description yet.")
            return
        pid = st.selectbox("Posting", [p.id for p in postings],
                           format_func=lambda i: next(f"{p.company} | {p.title}" for p in postings if p.id == i))  # fmt: skip

        with st.form("add_q", clear_on_submit=True):
            text = st.text_area("Add a question (include its limit, e.g. '(150 words)')", height=80)
            if st.form_submit_button("Add") and text.strip():
                q, limit = parse_question(text)
                db.add_question(conn, pid, q, limit)
                st.rerun()

        rows = db.list_questions(conn, pid)
        if not rows:
            st.caption("No questions for this posting yet.")
            return
        with _graph(conn, cv, settings) as (graph, tools):
            from autotailor.chains.answer_graph import run_answer

            for q in rows:
                _question(conn, q, graph, tools, run_answer, model)
        if any(q["answer"] or q["points_json"] for q in db.list_questions(conn, pid)):
            path = write_answers(conn, pid, paths().outputs)
            st.caption(f"Saved to `{path}`.")


def _question(conn, q, graph, tools, run_answer, model: str) -> None:  # noqa: ANN001
    from autotailor.ailog import current_item

    qid = q["id"]
    current_item.set(f"question:{qid}")
    limit = f" ({q['word_limit']} words)" if q["word_limit"] else ""
    with st.container(border=True):
        st.markdown(f"**{q['text']}**{limit}  \n<small>status: {q['status']}</small>", unsafe_allow_html=True)
        points = json.loads(q["points_json"] or "[]")
        if q["status"] == "new":
            if st.button("Suggest points", key=f"sug{qid}"):
                with st.spinner("Reading your CV ..."):
                    if graph is None:
                        db.update_question(conn, qid, status="points", points=tools.suggest(qid))
                    else:
                        _save(conn, qid, run_answer(graph, qid), model)
                st.rerun()
            return
        if q["status"] == "waiting" and graph is not None:
            picked = [st.checkbox(f"{pt['point']}  ·  _{pt['why']}_", value=True, key=f"pt{qid}.{i}")
                      for i, pt in enumerate(points)]  # fmt: skip
            notes = st.text_input("Anything to add or stress (optional)", key=f"notes{qid}")
            c1, c2, _ = st.columns([1, 1, 3])
            decision = None
            if c1.button("Draft from these", key=f"draft{qid}", type="primary"):
                decision = {"draft": True, "use": [i + 1 for i, on in enumerate(picked) if on] or None, "notes": notes}
            if c2.button("Points are enough", key=f"pts{qid}"):
                decision = {"draft": False}
            if decision is not None:
                with st.spinner("Drafting and humanizing ..." if decision["draft"] else "Saving ..."):
                    _save(conn, qid, run_answer(graph, qid, decision), model)
                st.rerun()
            return
        if points:
            st.markdown("Points:\n" + "\n".join(f"- {pt['point']} (`{pt['source_id']}`)" for pt in points))
        if q["answer"]:
            edited = st.text_area("Answer (edit freely; it's yours)", q["answer"], height=200, key=f"ans{qid}")
            st.caption(f"{word_count(edited)} words{limit}")
            for x in json.loads(q["problems_json"] or "[]"):
                st.warning(x)
            if edited != q["answer"] and st.button("Save edit", key=f"save{qid}"):
                db.update_question(conn, qid, answer=edited, status="drafted", problems=[])
                st.rerun()
