"""Matching: rank eligible postings by how well they fit the user's CV.

Two stages:

1. Pre-rank (cheap). Every eligible posting and every CV bullet is embedded
   with the configured embedding model: local (Ollama) by default, or a cloud
   one (OpenAI, Gemini). A posting's prescore is the mean
   similarity of its 5 closest CV bullets, blended with its similarity to the
   user's role interests. Vectors are cached in SQLite, so only new or changed
   text is embedded on later runs.
2. Fit score (model, M3 exercise). Only the best `run.shortlist_size`
   postings by prescore go to the fit-scoring chain, which grades each against
   a rubric. The final fit score is computed here from those grades, with fixed
   weights, rather than asking the model to do the arithmetic.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import numpy as np

from autotailor import db
from autotailor.config import Profile, Settings
from autotailor.cv import MasterCV
from autotailor.models import FitAssessment, JobPosting, Match

if TYPE_CHECKING:
    import sqlite3

    from langchain_core.embeddings import Embeddings

# How much each rubric criterion counts toward the fit score. Sums to 1.
FIT_WEIGHTS = {"skills": 0.35, "experience": 0.35, "level": 0.15, "interests": 0.15}
TOP_K_BULLETS = 5
INTEREST_WEIGHT = 0.3
_EMBED_BATCH = 32


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def get_embeddings(settings: Settings) -> Embeddings:
    """The configured embedding model. LangChain's `Embeddings` interface is the same for
    every provider: `.embed_documents(list[str]) -> list[vector]` and `.embed_query(str)`."""
    from autotailor.llm import load_api_keys

    spec = settings.models.embeddings
    if spec.provider == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(model=spec.model, base_url=settings.models.ollama_base_url)
    load_api_keys()
    if spec.provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=spec.model)
    if spec.provider == "google_genai":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=spec.model)
    raise ValueError(f"Unknown embedding provider: {spec.provider}")


# --- what gets embedded ------------------------------------------------------


def cv_chunks(cv: MasterCV) -> dict[str, str]:
    """One chunk per CV bullet (with its entry's title for context), plus one per skill group."""
    chunks: dict[str, str] = {}
    for entry in cv.entries():
        where = f"{entry.title}" + (f", {entry.organization}" if entry.organization else "")
        for b in entry.bullets:
            chunks[f"cv:{b.id}"] = f"{where}: {b.text}"
    for edu in cv.education:
        for b in edu.details:
            chunks[f"cv:{b.id}"] = f"{edu.degree} {edu.field or ''}, {edu.school}: {b.text}"
    for group in cv.skills:
        chunks[f"cv:skills:{group.name}"] = f"Skills, {group.name}: {', '.join(group.items)}"
    return chunks


def interests_text(profile: Profile) -> str | None:
    return ("Roles I want: " + "; ".join(profile.role_interests)) if profile.role_interests else None


def posting_text(p: JobPosting, max_chars: int) -> str:
    return f"{p.title}\n{p.company}\n\n{p.description or ''}"[:max_chars]


def _unit(v: list[float]) -> np.ndarray:
    a = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(a)
    return a / n if n else a


def embed_cached(
    conn: sqlite3.Connection,
    embeddings: Embeddings,
    model: str,
    items: dict[str, str],
    prefix: str,
) -> dict[str, np.ndarray]:
    """Unit vectors for `items` (key -> text), embedding only what isn't cached."""
    out: dict[str, np.ndarray] = {}
    missing: list[tuple[str, str, str]] = []
    for key, text in items.items():
        h = _hash(prefix + text)
        cached = db.get_embedding(conn, key, model, h)
        if cached is None:
            missing.append((key, text, h))
        else:
            out[key] = _unit(cached)
    for i in range(0, len(missing), _EMBED_BATCH):
        batch = missing[i : i + _EMBED_BATCH]
        vectors = embeddings.embed_documents([prefix + text for _, text, _ in batch])
        for (key, _, h), vec in zip(batch, vectors, strict=True):
            db.put_embedding(conn, key, model, h, vec)
            out[key] = _unit(vec)
        conn.commit()
    return out


def prescore_postings(
    conn: sqlite3.Connection,
    postings: list[JobPosting],
    cv: MasterCV,
    settings: Settings,
    embeddings: Embeddings | None = None,
) -> dict[str, float]:
    """Embedding similarity between each posting and the CV. Higher is a closer match."""
    spec = settings.models.embeddings
    emb = embeddings or get_embeddings(settings)

    profile_items = cv_chunks(cv)
    interests = interests_text(settings.profile)
    if interests:
        profile_items["interests"] = interests
    query_prefix, document_prefix = spec.prefixes
    profile_vecs = embed_cached(conn, emb, spec.cache_key, profile_items, query_prefix)
    posting_vecs = embed_cached(
        conn,
        emb,
        spec.cache_key,
        {f"posting:{p.id}": posting_text(p, spec.max_chars) for p in postings},
        document_prefix,
    )

    bullet_keys = [k for k in profile_vecs if k != "interests"]
    if not bullet_keys:
        raise ValueError("The master CV has no bullets or skills to match against.")
    bullets = np.stack([profile_vecs[k] for k in bullet_keys])
    interest_vec = profile_vecs.get("interests")
    k = min(TOP_K_BULLETS, len(bullet_keys))

    scores: dict[str, float] = {}
    for p in postings:
        v = posting_vecs[f"posting:{p.id}"]
        sims = bullets @ v
        score = float(np.sort(sims)[-k:].mean())
        if interest_vec is not None:
            score = (1 - INTEREST_WEIGHT) * score + INTEREST_WEIGHT * float(interest_vec @ v)
        scores[p.id] = round(score, 4)
    return scores


# --- fit scores --------------------------------------------------------------


def profile_text(cv: MasterCV, profile: Profile) -> str:
    """The candidate as the fit-scoring model sees it: interests, education, skills, then every
    CV bullet with its ID in [brackets] so the model can point at specific bullets."""
    lines: list[str] = []
    if profile.role_interests:
        lines.append("Role interests: " + "; ".join(profile.role_interests))
    lines.append("")
    lines.append("Education:")
    for e in profile.education:
        when = f", expected {e.graduation_date:%B %Y}" if e.status == "in_progress" and e.graduation_date else (
            f", {e.graduation_date:%B %Y}" if e.graduation_date else ""
        )
        status = "in progress" if e.status == "in_progress" else "completed"
        lines.append(f"- {e.level.value} in {e.field} ({status}{when}){', ' + e.school if e.school else ''}")
    for edu in cv.education:
        for b in edu.details:
            lines.append(f"- [{b.id}] {b.text}")
    if cv.skills:
        lines.append("")
        lines.append("Skills:")
        lines.extend(f"- {g.name}: {', '.join(g.items)}" for g in cv.skills)
    sections = [
        ("Work experience", cv.experience), ("Projects", cv.projects), ("Activities", cv.activities),
        ("Volunteer", cv.volunteer), ("Awards", cv.awards), ("Certifications", cv.certifications),
    ]  # fmt: skip
    for name, entries in sections:
        if not entries:
            continue
        lines.append("")
        lines.append(f"{name}:")
        for e in entries:
            parts = [e.title, e.organization, " – ".join(x for x in (e.start, e.end) if x)]
            lines.append("## " + " | ".join(x for x in parts if x))
            lines.extend(f"- [{b.id}] {b.text}" for b in e.bullets)
    return "\n".join(lines).strip()


def compute_fit_score(a: FitAssessment) -> float:
    """Weighted average of the rubric scores, mapped from 1-5 onto 0-1."""
    total = sum(w * (getattr(a, name).score - 1) / 4 for name, w in FIT_WEIGHTS.items())
    return round(total, 3)


def clean_assessment(a: FitAssessment, cv: MasterCV) -> tuple[FitAssessment, list[str]]:
    """Drop bullet IDs that aren't in the CV (and duplicates). Returns the cleaned
    assessment and the dropped IDs."""
    known = cv.bullets()
    kept: list[str] = []
    dropped: list[str] = []
    for raw in a.relevant_bullet_ids:
        bid = raw.strip().strip("[]")
        if bid in known and bid not in kept:
            kept.append(bid)
        elif bid not in known:
            dropped.append(raw)
    return a.model_copy(update={"relevant_bullet_ids": kept}), dropped


def fit_reasons(a: FitAssessment) -> list[str]:
    reasons = [
        f"{name.capitalize()} {getattr(a, name).score}/5: {getattr(a, name).reason}" for name in FIT_WEIGHTS
    ]
    reasons += [f"Missing: {m}" for m in a.missing]
    return reasons


# --- ranking -----------------------------------------------------------------


def ranked(conn: sqlite3.Connection, settings: Settings) -> list[tuple[JobPosting, Match]]:
    """Postings that pass the eligibility gate, best first: fit score where there is one,
    then prescore. Postings without a description are left out (nothing to tailor to)."""
    from autotailor.eligibility import passes_gate

    matches = db.all_matches(conn)
    rows = [
        (p, matches[p.id])
        for p in db.list_postings(conn, None)
        if p.id in matches and p.description and passes_gate(matches[p.id].verdict, settings.eligibility)
    ]
    rows.sort(
        key=lambda pm: (
            pm[1].fit_score is not None,
            pm[1].fit_score or 0.0,
            pm[1].prescore or 0.0,
        ),
        reverse=True,
    )
    return rows


def shortlist(conn: sqlite3.Connection, settings: Settings) -> list[tuple[JobPosting, Match]]:
    """The top `run.jobs_per_run` postings whose fit score meets `run.min_fit_score`."""
    run = settings.run
    return [
        (p, m) for p, m in ranked(conn, settings) if m.fit_score is not None and m.fit_score >= run.min_fit_score
    ][: run.jobs_per_run]
