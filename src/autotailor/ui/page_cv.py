"""Master CV editor.

The page keeps a *base* copy of the CV in session state (what was loaded, plus
entries added, removed or moved). Widgets are keyed by a per-entry uid and start
from the base values; every rerun rebuilds the draft from the widgets. Saving
validates the draft, fills in IDs for new entries and bullets, keeps a backup,
and writes cv/master.yaml. Then the page reloads from the file.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

import pandas as pd
import streamlit as st
import yaml
from pydantic import ValidationError

from autotailor.ui import logic
from autotailor.ui.common import connection, csv, lines, load_cv, paths

PREFIX = "cv."
BASE = "cv_base"
_uids = itertools.count()


def _uid() -> str:
    return f"u{next(_uids)}"


def _k(*parts: str) -> str:
    return PREFIX + ".".join(parts)


def _load_base() -> dict[str, Any] | None:
    cv, error = load_cv()
    if cv is None:
        st.error(f"Can't open the master CV: {error}")
        return None
    data = cv.model_dump(mode="json")
    for section in ("education", *logic.CV_SECTIONS):
        for e in data[section]:
            e["_uid"] = _uid()
    st.session_state[BASE] = data
    st.session_state["cv_saved_text"] = paths().master_cv.read_text(encoding="utf-8")
    return data


def _reload() -> None:
    for k in [k for k in st.session_state if str(k).startswith(PREFIX)]:
        del st.session_state[k]
    st.session_state.pop(BASE, None)
    st.session_state.pop("cv_confirm", None)


def _bullets_editor(bullets: list[dict], key: str) -> list[dict]:
    df = pd.DataFrame(
        [{"#": i + 1, "id": b["id"], "text": b["text"], "tags": ", ".join(b.get("tags") or [])} for i, b in enumerate(bullets)],
        columns=["#", "id", "text", "tags"],
    )
    edited = st.data_editor(
        df, num_rows="dynamic", hide_index=True, width="stretch", key=key,
        column_config={
            "#": st.column_config.NumberColumn("#", width="small", help="Order; change the numbers to reorder."),
            "id": st.column_config.TextColumn("ID", disabled=True, width="small", help="Set when saved; never changes."),
            "text": st.column_config.TextColumn("Bullet", width="large", required=True),
            "tags": st.column_config.TextColumn("Tags", width="small", help="Comma-separated."),
        },
    )  # fmt: skip
    rows = [r for r in edited.to_dict("records") if isinstance(r.get("text"), str) and r["text"].strip()]
    rows.sort(key=lambda r: (r["#"] if pd.notna(r["#"]) else 1e9))
    return [
        {"id": r["id"] if isinstance(r["id"], str) and r["id"] else None, "text": r["text"].strip(),
         "tags": csv(r["tags"] if isinstance(r["tags"], str) else "")}
        for r in rows
    ]  # fmt: skip


def _entry_controls(section: str, base_list: list[dict], i: int) -> None:
    c1, c2, c3, _ = st.columns([1, 1, 1, 5])
    uid = base_list[i]["_uid"]
    if c1.button("Move up", key=_k(section, uid, "up"), disabled=i == 0):
        base_list[i - 1], base_list[i] = base_list[i], base_list[i - 1]
        st.rerun()
    if c2.button("Move down", key=_k(section, uid, "down"), disabled=i == len(base_list) - 1):
        base_list[i + 1], base_list[i] = base_list[i], base_list[i + 1]
        st.rerun()
    if c3.button("Remove", key=_k(section, uid, "rm")):
        base_list.pop(i)
        st.rerun()


def _text(label: str, value: str | None, key: str, col=None) -> str | None:  # noqa: ANN001
    v = (col or st).text_input(label, value or "", key=key)
    return v.strip() or None


def _entry(section: str, base_list: list[dict], i: int) -> dict:
    e = base_list[i]
    uid = e["_uid"]
    title = st.session_state.get(_k(section, uid, "title"), e.get("title")) or "(new entry)"
    org = st.session_state.get(_k(section, uid, "org"), e.get("organization"))
    with st.expander(title + (f" · {org}" if org else ""), expanded=not e.get("id")):
        st.caption(f"ID: `{e['id']}`" if e.get("id") else "ID: set when saved")
        c1, c2 = st.columns(2)
        out = {
            "id": e.get("id"),
            "title": _text("Title", e.get("title"), _k(section, uid, "title"), c1) or "",
            "organization": _text("Organization", e.get("organization"), _k(section, uid, "org"), c2),
        }
        c1, c2, c3 = st.columns(3)
        out["location"] = _text("Location", e.get("location"), _k(section, uid, "loc"), c1)
        out["start"] = _text("Start", e.get("start"), _k(section, uid, "start"), c2)
        out["end"] = _text("End", e.get("end"), _k(section, uid, "end"), c3)
        out["tags"] = csv(st.text_input("Tags (comma-separated)", ", ".join(e.get("tags") or []), key=_k(section, uid, "tags")))
        out["bullets"] = _bullets_editor(e.get("bullets") or [], _k(section, uid, "bullets"))
        _entry_controls(section, base_list, i)
    return out


def _education(base_list: list[dict], i: int) -> dict:
    e = base_list[i]
    uid = e["_uid"]
    school = st.session_state.get(_k("education", uid, "school"), e.get("school")) or "(new school)"
    with st.expander(school, expanded=not e.get("id")):
        st.caption(f"ID: `{e['id']}`" if e.get("id") else "ID: set when saved")
        c1, c2, c3 = st.columns(3)
        out = {
            "id": e.get("id"),
            "school": _text("School", e.get("school"), _k("education", uid, "school"), c1) or "",
            "degree": _text("Degree", e.get("degree"), _k("education", uid, "degree"), c2) or "",
            "field": _text("Field", e.get("field"), _k("education", uid, "field"), c3),
        }
        c1, c2, c3, c4 = st.columns(4)
        out["location"] = _text("Location", e.get("location"), _k("education", uid, "loc"), c1)
        out["start"] = _text("Start", e.get("start"), _k("education", uid, "start"), c2)
        out["graduation"] = _text("Graduation", e.get("graduation"), _k("education", uid, "grad"), c3)
        out["gpa"] = _text("GPA", e.get("gpa"), _k("education", uid, "gpa"), c4)
        st.markdown("Details (coursework, honors)")
        out["details"] = _bullets_editor(e.get("details") or [], _k("education", uid, "details"))
        _entry_controls("education", base_list, i)
    return out


def _section(name: str, base: dict[str, Any], render_one) -> list[dict]:  # noqa: ANN001
    base_list = base[name]
    out = [render_one(base_list, i) if name == "education" else render_one(name, base_list, i) for i in range(len(base_list))]
    if st.button("Add entry", key=_k(name, "add")):
        base_list.append({"_uid": _uid()})
        st.rerun()
    return out


def _save(draft: dict[str, Any], old_text: str) -> None:
    """Validate and write; when the draft drops bullets that past resumes used, ask first."""
    old = yaml.safe_load(old_text) or {}
    with connection() as conn:
        usage = logic.bullet_usage(conn, paths().outputs)
    used_and_removed = {i: usage[i] for i in logic.removed_ids(old, draft) if i in usage}
    if used_and_removed and st.session_state.get("cv_confirm") != sorted(used_and_removed):
        st.session_state["cv_confirm"] = sorted(used_and_removed)  # confirmed by saving again with the same deletions
        st.warning(
            "These bullets were used in past resumes and would be deleted. Their resumes stay as they are, "
            "but you won't be able to trace them back to the CV. Press Save again to delete them.\n\n"
            + "\n".join(
                f"- `{bid}`: " + "; ".join(f"run {u.run_id}, {u.company} | {u.title}" for u in uses)
                for bid, uses in used_and_removed.items()
            )
        )
        return
    try:
        logic.save_cv(copy.deepcopy(draft), paths().master_cv, paths().cv_backups, keep_ids=set(usage) | logic.all_ids(old))
    except ValidationError as e:
        st.session_state.pop("cv_confirm", None)
        st.error("Not saved; fix these first:\n\n" + "\n".join(
            f"- `{'.'.join(str(x) for x in err['loc'])}`: {err['msg']}" for err in e.errors()
        ))  # fmt: skip
        return
    _reload()
    st.session_state["cv_flash"] = f"Saved. The previous version is in `{paths().cv_backups}`."
    st.rerun()


def render() -> None:
    st.title("Master CV")
    st.caption(f"`{paths().master_cv}`. Tailored resumes only use what's written here.")
    if flash := st.session_state.pop("cv_flash", None):
        st.success(flash)
    base = st.session_state.get(BASE) or _load_base()
    if base is None:
        return

    tabs = st.tabs(["Contact", "Education", *logic.CV_SECTIONS.values(), "Skills", "Raw YAML"])
    draft: dict[str, Any] = {}

    with tabs[0]:
        c = base["contact"]
        c1, c2 = st.columns(2)
        draft["contact"] = {
            "name": _text("Name", c.get("name"), _k("contact", "name"), c1) or "",
            "email": _text("Email", c.get("email"), _k("contact", "email"), c2),
            "phone": _text("Phone", c.get("phone"), _k("contact", "phone"), c1),
            "location": _text("Location", c.get("location"), _k("contact", "loc"), c2),
            "links": lines(st.text_area("Links (one per line)", "\n".join(c.get("links") or []), key=_k("contact", "links"))),
        }
    with tabs[1]:
        draft["education"] = _section("education", base, _education)
    for tab, section in zip(tabs[2 : 2 + len(logic.CV_SECTIONS)], logic.CV_SECTIONS, strict=True):
        with tab:
            draft[section] = _section(section, base, _entry)
    with tabs[-2]:
        df = pd.DataFrame([{"name": g["name"], "items": ", ".join(g["items"])} for g in base["skills"]], columns=["name", "items"])
        edited = st.data_editor(
            df, num_rows="dynamic", hide_index=True, width="stretch", key=_k("skills"),
            column_config={"name": st.column_config.TextColumn("Group", required=True),
                           "items": st.column_config.TextColumn("Skills (comma-separated)", width="large")},
        )  # fmt: skip
        draft["skills"] = [
            {"name": r["name"].strip(), "items": csv(r["items"] if isinstance(r["items"], str) else "")}
            for r in edited.to_dict("records") if isinstance(r["name"], str) and r["name"].strip()
        ]  # fmt: skip
    with tabs[-1]:
        _raw_yaml()

    st.divider()
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("Save CV", type="primary", key=_k("save")):
        _save(draft, st.session_state["cv_saved_text"])
    if c2.button("Discard changes", key=_k("discard")):
        _reload()
        st.rerun()


def _raw_yaml() -> None:
    st.caption(
        "The whole file. Useful for bulk edits. Saving here replaces the file and discards unsaved changes "
        "in the other tabs. Keep existing IDs as they are."
    )
    text = st.text_area("master.yaml", st.session_state["cv_saved_text"], height=500, key=_k("raw"))
    if st.button("Save YAML", key=_k("raw", "save")):
        try:
            cv = logic.parse_cv_yaml(text)
        except (yaml.YAMLError, ValidationError) as e:
            st.error(f"Not saved:\n\n```\n{e}\n```")
            return
        _save(cv.model_dump(mode="json"), st.session_state["cv_saved_text"])
