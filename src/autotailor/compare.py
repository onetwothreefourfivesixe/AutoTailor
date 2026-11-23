"""Compare models on saved data (M9). Nothing stored is changed.

Each comparison reruns one pipeline step on postings (or questions) already in
the database, once per model, and measures what matters for that step:

- extract: requirements read from each posting -> the eligibility verdict they
  lead to, and whether it matches the stored one.
- score:   fit score per posting -> how close to the stored score, and whether
  the models rank the postings the same way (Spearman correlation).
- tailor:  a resume per posting -> whether it passed verification, how many
  drafts it took, and how many of the same CV bullets it chose as the saved one.
- answers: a drafted answer per question -> fact/style problems left, drafts
  needed, word count.

Every trial records its time, so speed and quality can be weighed together.
Models run one after another (all of one model's trials, then the next), so
Ollama loads each model once.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx

from autotailor import db
from autotailor.config import ModelSpec, Settings
from autotailor.cv import MasterCV

CompareStep = Literal["extract", "score", "tailor", "answers"]
STEP_OF: dict[str, str] = {"extract": "extract", "score": "score", "tailor": "tailor", "answers": "tailor"}


def parse_model(text: str, base: ModelSpec | None = None) -> ModelSpec:
    """'ollama:gemma4:12b' -> ModelSpec(provider='ollama', model='gemma4:12b'). The provider is
    everything before the first colon; limits are copied from `base`."""
    provider, sep, model = text.partition(":")
    if not sep or not model:
        raise ValueError(f"Give a model as provider:model, e.g. ollama:gemma4:12b (got '{text}').")
    data = (base or ModelSpec()).model_dump()
    data.update(provider=provider, model=model)
    return ModelSpec.model_validate(data)


def label(spec: ModelSpec) -> str:
    return f"{spec.provider}:{spec.model}"


def with_model(settings: Settings, step: str, spec: ModelSpec) -> Settings:
    """A copy of `settings` where `step` uses `spec`. The original is untouched."""
    s = settings.model_copy(deep=True)
    s.models.steps[step] = spec
    return s


def missing_ollama_models(specs: list[ModelSpec], base_url: str) -> list[str]:
    """Local models that aren't pulled (empty if Ollama can't be reached: the run will say so)."""
    local = [s.model for s in specs if s.is_local]
    if not local:
        return []
    try:
        names = {m["name"] for m in httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=5).json()["models"]}
    except (httpx.HTTPError, KeyError, ValueError):
        return []
    have = names | {n.removesuffix(":latest") for n in names}
    return [m for m in local if m not in have]


@dataclass
class Trial:
    model: str
    item: str  # posting or question ID
    label: str  # human-readable: "Company | Title" or the question
    ok: bool
    seconds: float
    metrics: dict[str, Any] = field(default_factory=dict)
    output: str = ""  # short text of what the model produced
    error: str | None = None


def _timed(fn: Callable[[], Any]) -> tuple[Any, float, str | None]:
    t0 = time.perf_counter()
    try:
        return fn(), time.perf_counter() - t0, None
    except Exception as e:  # one failure doesn't stop the comparison
        return None, time.perf_counter() - t0, f"{type(e).__name__}: {str(e)[:200]}"


# --- the four comparisons ----------------------------------------------------------------


def compare_extract(conn: sqlite3.Connection, settings: Settings, specs: list[ModelSpec], postings) -> list[Trial]:  # noqa: ANN001
    from autotailor import eligibility
    from autotailor.chains.extract_requirements import extract_requirements_batch

    trials = []
    for spec in specs:
        s = with_model(settings, "extract", spec)
        for p in postings:
            stored = db.get_match(conn, p.id)
            res, secs, err = _timed(lambda p=p, s=s: extract_requirements_batch([p.description or ""], s, max_concurrency=1)[0])
            if isinstance(res, Exception):
                res, err = None, f"{type(res).__name__}: {str(res)[:200]}"
            t = Trial(label(spec), p.id, f"{p.company} | {p.title}", res is not None, secs, error=err)
            if res is not None:
                verdict = eligibility.assess(p.model_copy(update={"requirements": res}), settings.profile, settings.eligibility).verdict
                t.metrics = {"verdict": verdict.value, "stored_verdict": stored.verdict.value if stored else None,
                             "same_verdict": stored is not None and verdict == stored.verdict,
                             "quotes": len(res.evidence)}  # fmt: skip
                t.output = ", ".join(f"{k}={v}" for k, v in res.model_dump(exclude={"evidence"}, exclude_defaults=True).items()) or "nothing stated"
            trials.append(t)
    return trials


def compare_score(conn: sqlite3.Connection, cv: MasterCV, settings: Settings, specs: list[ModelSpec], postings) -> list[Trial]:  # noqa: ANN001
    from autotailor import matching
    from autotailor.chains.score_fit import score_fit_batch

    profile = matching.profile_text(cv, settings.profile)
    trials = []
    for spec in specs:
        s = with_model(settings, "score", spec)
        for p in postings:
            stored = db.get_match(conn, p.id)
            res, secs, err = _timed(lambda p=p, s=s: score_fit_batch([matching.posting_text(p, 12000)], profile, s, max_concurrency=1)[0])
            if isinstance(res, Exception):
                res, err = None, f"{type(res).__name__}: {str(res)[:200]}"
            t = Trial(label(spec), p.id, f"{p.company} | {p.title}", res is not None, secs, error=err)
            if res is not None:
                cleaned, dropped = matching.clean_assessment(res, cv)
                fit = matching.compute_fit_score(cleaned)
                t.metrics = {"fit": fit, "stored_fit": stored.fit_score if stored else None,
                             "bullets_cited": len(cleaned.relevant_bullet_ids), "unknown_ids": len(dropped)}  # fmt: skip
                t.output = "; ".join(f"{k} {getattr(cleaned, k).score}" for k in matching.FIT_WEIGHTS)
            trials.append(t)
    return trials


def _saved_bullets(conn: sqlite3.Connection, posting_id: str, outputs: Path) -> set[str] | None:
    from autotailor.models import ArtifactKind, TailoredResume

    for a in db.artifacts_for(conn, posting_id):
        if a["kind"] == ArtifactKind.RESUME_DATA.value:
            try:
                r = TailoredResume.model_validate_json((outputs / a["path"]).read_text(encoding="utf-8"))
                return {b.source_id for b in r.bullets}
            except (OSError, ValueError):
                return None
    return None


def compare_tailor(conn: sqlite3.Connection, cv: MasterCV, settings: Settings, specs: list[ModelSpec], postings,  # noqa: ANN001
                   outputs: Path) -> list[Trial]:  # fmt: skip
    from autotailor import batch, matching
    from autotailor.chains.tailor_graph import tailor_posting

    base_profile = matching.profile_text(cv, settings.profile, for_resume=True)
    trials = []
    for spec in specs:
        s = with_model(settings, "tailor", spec)
        for p in postings:
            profile = batch.job_profile(base_profile, db.get_match(conn, p.id))
            res, secs, err = _timed(lambda p=p, s=s, profile=profile: tailor_posting(matching.posting_text(p, 12000), profile, cv, s))
            ok = res is not None and res.resume is not None and not res.errors
            t = Trial(label(spec), p.id, f"{p.company} | {p.title}", ok, secs, error=err)
            if res is not None:
                chosen = {b.source_id for b in res.resume.bullets} if res.resume else set()
                saved = _saved_bullets(conn, p.id, outputs)
                t.metrics = {"drafts": res.attempts, "bullets": len(chosen), "problems": len(res.errors),
                             "same_bullets_as_saved": (round(len(chosen & saved) / len(chosen | saved), 2)
                                                       if saved and chosen else None)}  # fmt: skip
                t.output = " | ".join(b.text for b in res.resume.bullets[:3]) if res.resume else "; ".join(res.errors[:2])
                if not ok and not err:
                    t.error = "; ".join(res.errors[:2])
            trials.append(t)
    return trials


def compare_answers(conn: sqlite3.Connection, cv: MasterCV, settings: Settings, specs: list[ModelSpec],
                    question_ids: list[int]) -> list[Trial]:  # fmt: skip
    """Draft and humanize each question with each model (all suggested points, no notes)."""
    from langgraph.checkpoint.memory import InMemorySaver

    from autotailor.answers import AnswerTools, word_count
    from autotailor.chains.answer_graph import build_answer_graph, run_answer
    from autotailor.llm import get_chat_model

    trials = []
    for spec in specs:
        s = with_model(with_model(settings, "tailor", spec), "humanize", spec)
        tools = AnswerTools(conn, cv, s)
        for qid in question_ids:
            q = db.get_question(conn, qid)
            stored_points = json.loads(q["points_json"] or "[]")
            if stored_points:  # same points for every model, so only the writing differs
                tools.suggest = lambda _qid, pts=stored_points: [dict(x) for x in pts]  # type: ignore[method-assign]

            def run(qid=qid, s=s, tools=tools):
                graph = build_answer_graph(tools, get_chat_model(s, "tailor"), get_chat_model(s, "humanize"),
                                           InMemorySaver(), s.tailor.max_attempts)  # fmt: skip
                run_answer(graph, qid)
                return run_answer(graph, qid, {"draft": True})

            out, secs, err = _timed(run)
            ok = out is not None and out.status == "drafted"
            t = Trial(label(spec), str(qid), q["text"][:80], ok, secs, error=err)
            if out is not None and out.status in ("drafted", "needs_review"):
                t.metrics = {"humanize_passes": out.payload["attempts"], "problems_left": len(out.payload["problems"]),
                             "words": word_count(out.payload["text"]), "limit": q["word_limit"]}  # fmt: skip
                t.output = out.payload["text"]
            trials.append(t)
    return trials


# --- summary and report ------------------------------------------------------------------------


def spearman(a: list[float], b: list[float]) -> float | None:
    """Rank correlation of two score lists (1 = same order, -1 = reversed). None if under 3 items."""
    if len(a) < 3 or len(a) != len(b):
        return None

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        r = [0.0] * len(xs)
        i = 0
        while i < len(order):  # average ranks for ties
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r

    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / len(ra), sum(rb) / len(rb)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return round(cov / (va * vb), 2) if va and vb else None


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 2) if xs else None


def summarize(step: CompareStep, trials: list[Trial]) -> list[dict[str, Any]]:
    """One row per model."""
    rows = []
    for model in dict.fromkeys(t.model for t in trials):
        ts = [t for t in trials if t.model == model]
        row: dict[str, Any] = {"model": model, "items": len(ts), "ok": f"{sum(t.ok for t in ts)}/{len(ts)}",
                               "avg_seconds": _mean([t.seconds for t in ts])}  # fmt: skip
        m = [t.metrics for t in ts if t.metrics]
        if step == "extract":
            same = [x["same_verdict"] for x in m if x["stored_verdict"]]
            row["same_verdict_as_stored"] = f"{sum(same)}/{len(same)}" if same else None
        elif step == "score":
            pairs = [(x["fit"], x["stored_fit"]) for x in m if x["stored_fit"] is not None]
            row["avg_abs_diff_from_stored"] = _mean([abs(a - b) for a, b in pairs])
            row["rank_agreement_with_stored"] = spearman([a for a, _ in pairs], [b for _, b in pairs])
            row["unknown_bullet_ids"] = sum(x["unknown_ids"] for x in m)
        elif step == "tailor":
            row["avg_drafts"] = _mean([x["drafts"] for x in m])
            row["same_bullets_as_saved"] = _mean([x["same_bullets_as_saved"] for x in m])
        elif step == "answers":
            row["avg_humanize_passes"] = _mean([x["humanize_passes"] for x in m])
            row["problems_left"] = sum(x["problems_left"] for x in m)
            row["avg_words"] = _mean([x["words"] for x in m])
        rows.append(row)
    return rows


_EXPLAIN = {
    "extract": "Requirements read from each posting, and the eligibility verdict they lead to. "
               "'same verdict as stored' compares with the verdict saved by your last `screen`.",
    "score": "Fit score per posting. 'rank agreement' is the Spearman correlation with your stored scores "
             "(1 = same order); 'unknown bullet IDs' counts made-up CV references.",
    "tailor": "A resume per posting. 'ok' means it passed verification against your CV; 'same bullets as saved' is "
              "the overlap (Jaccard) with the resume saved in a run, where there is one.",
    "answers": "A drafted answer per question, from the same suggested points for every model. 'ok' means no "
               "fact or style problems were left; 'problems left' counts what the check still flagged.",
}  # fmt: skip


def report_markdown(step: CompareStep, trials: list[Trial], started: datetime) -> str:
    rows = summarize(step, trials)
    cols = list(rows[0]) if rows else []
    lines = [f"# Model comparison: {step}", "", f"{started:%Y-%m-%d %H:%M}. {_EXPLAIN[step]}", ""]
    if rows:
        lines += ["| " + " | ".join(c.replace("_", " ") for c in cols) + " |", "|" + "---|" * len(cols)]
        lines += ["| " + " | ".join("–" if r[c] is None else str(r[c]) for c in cols) + " |" for r in rows]
        lines.append("")
    lines += ["## Per item", ""]
    for item in dict.fromkeys(t.item for t in trials):
        ts = [t for t in trials if t.item == item]
        lines += [f"### {ts[0].label}", ""]
        for t in ts:
            status = "ok" if t.ok else "failed"
            metrics = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in t.metrics.items() if v is not None)
            lines.append(f"- **{t.model}** ({status}, {t.seconds:.1f} s){': ' + metrics if metrics else ''}")
            if t.error:
                lines.append(f"  - error: {t.error}")
            if t.output:
                lines.append(f"  - > {t.output.replace(chr(10), ' ')[:600]}")
        lines.append("")
    return "\n".join(lines)


def write_report(folder: Path, step: CompareStep, trials: list[Trial], started: datetime) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y%m%d-%H%M%S}-{step}"
    (folder / f"{stem}.json").write_text(json.dumps([asdict(t) for t in trials], indent=1), encoding="utf-8")
    path = folder / f"{stem}.md"
    path.write_text(report_markdown(step, trials, started), encoding="utf-8")
    return path
