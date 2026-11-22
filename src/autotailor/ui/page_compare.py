"""Model comparison reports (M9). Comparisons run from the terminal: `autotailor compare ...`."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from autotailor import compare as cmp
from autotailor.ui.common import paths


def render() -> None:
    st.title("Compare models")
    st.caption("Each report reran one step on saved postings with several models. Your stored results weren't changed.")
    st.code(
        "autotailor models                                   # what's in use, what's pulled\n"
        "autotailor compare tailor -m ollama:gemma4:12b -n 3\n"
        "autotailor compare score  -m ollama:qwen3-vl:4b-instruct -n 10\n"
        "autotailor compare extract -m ollama:gemma4:12b\n"
        "autotailor compare answers -m ollama:gemma4:12b",
        language="bash",
    )
    folder = paths().comparisons
    reports = sorted(folder.glob("*.md"), reverse=True) if folder.exists() else []
    if not reports:
        st.info("No comparisons yet.")
        return
    path = st.selectbox("Report", reports, format_func=lambda p: p.stem)
    data = path.with_suffix(".json")
    if data.exists():
        trials = [cmp.Trial(**t) for t in json.loads(data.read_text(encoding="utf-8"))]
        step = path.stem.rsplit("-", 1)[-1]
        if step in cmp.STEP_OF and trials:
            st.dataframe(pd.DataFrame(cmp.summarize(step, trials)), hide_index=True, width="stretch")  # type: ignore[arg-type]
    st.markdown(path.read_text(encoding="utf-8").split("## Per item", 1)[-1] if data.exists() else path.read_text(encoding="utf-8"))
