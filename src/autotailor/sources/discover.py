"""Find company job boards for the industries a user describes in their own words.

1. The description is matched to industries (biotech, fintech, robotics, ...) by
   keywords, or, when none match, by the `extract` step's model choosing from the
   list (prompt "pick_industries"). Only the description is sent, never the CV.
2. The catalog (suggested_boards.json: boards verified when it was built, tagged
   by industry) gives that industry's companies. Each board is checked again,
   live, for its current number of open jobs; boards gone since are dropped.
3. Optionally the model also suggests more companies (prompt "suggest_companies").
   Likely board names ("slugs") are guessed for each and checked against the
   boards' public APIs, the same ones `fetch` reads; only boards that exist and
   belong to that company are kept. Small local models know fewer companies and
   sometimes invent names, so the catalog does most of the work.

The catalog grows and stays current on its own (`store`, data/learned_boards.json):
boards found through the model's suggestions are added under the industries that
were searched, so later searches start with them; boards found gone during a
search are dropped (or, if built in, skipped) until they come back.

A guessed slug can belong to a different company with a similar name, so every
board found is checked against the company's name: on Greenhouse the board's
company name, on Lever and Ashby (which don't report one) its job listings. A
slug shortened from the name ("cohere" for "Cohere Health") needs the full name
there, since the short one is often another company's (Cohere, the AI lab).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from autotailor.config import Settings

ATS = ("greenhouse", "lever", "ashby")
_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
_GH_NAME = "https://boards-api.greenhouse.io/v1/boards/{slug}"
# Words a company's board name often leaves out: "Recursion Pharmaceuticals" -> "recursion".
_SUFFIXES = {
    "inc", "llc", "ltd", "corp", "corporation", "company", "co", "group", "holdings", "the",
    "therapeutics", "pharmaceuticals", "pharma", "biosciences", "bioscience", "biotherapeutics",
    "biotechnologies", "biotech", "medical", "health", "healthcare", "technologies", "technology",
    "labs", "lab", "systems", "sciences", "science", "ai", "bio", "genomics", "diagnostics",
}  # fmt: skip


class SuggestedCompany(BaseModel):
    name: str = Field(description="The company's usual name, e.g. 'Ginkgo Bioworks'.")


class SuggestedCompanies(BaseModel):
    companies: list[SuggestedCompany] = Field(description="Companies in the fields described, most relevant first.")


@dataclass
class FoundBoard:
    name: str
    ats: str
    slug: str
    jobs: int
    query: str = ""  # the company name that was looked up


def _words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", name.lower().replace("&", " and "))


def slug_candidates(name: str) -> list[tuple[str, bool]]:
    """Likely board slugs for a company, each with whether it is spelled from the full name
    (trusted on any board) rather than shortened (trusted only where the name can be checked)."""
    words = _words(name)
    if not words:
        return []
    out = [("".join(words), True)]
    if len(words) > 1:
        out.append(("-".join(words), True))
        core = [w for w in words if w not in _SUFFIXES] or words[:1]
        if core != words:
            out.append(("".join(core), False))
    return list(dict.fromkeys(out))


def _same_company(a: str, b: str) -> bool:
    """Loose name match: the shorter name's words all appear in the longer one."""
    wa, wb = set(_words(a)) - _SUFFIXES, set(_words(b)) - _SUFFIXES
    return bool(wa and wb) and (wa <= wb or wb <= wa)


def probe(client: httpx.Client, ats: str, slug: str, company: str, trusted: bool = True,
          topic: list[str] | None = None) -> FoundBoard | None:  # fmt: skip
    """The board if it exists and belongs to `company`, else None. `trusted`: the slug is spelled
    from the full name (a shortened one needs stronger evidence)."""
    try:
        r = client.get(_URLS[ats].format(slug=slug))
        if r.status_code != 200:
            return None
        data: Any = r.json()
        jobs = len(data.get("jobs", [])) if isinstance(data, dict) else len(data)
        name = company
        full = r"\W*".join(re.escape(w) for w in _words(company))  # "Cohere Health", any spacing
        if ats == "greenhouse":
            meta = client.get(_GH_NAME.format(slug=slug))
            name = (meta.json().get("name") or company) if meta.status_code == 200 else company
            ok = _same_company(name, company) if trusted else re.search(full, name, re.I) is not None
        else:  # no company name on the board: its listings must name the company
            core = set(_words(company)) - _SUFFIXES or set(_words(company))
            ok = all(re.search(rf"\b{re.escape(w)}\b", r.text, re.I) for w in core) if trusted else (
                re.search(full, r.text, re.I) is not None)  # a shortened slug needs the full name
        if not ok:
            return None
        if topic and not any(re.search(rf"(?<![a-z]){re.escape(w)}", r.text, re.I) for w in topic):
            return None  # the company exists but its jobs say nothing about the field searched
        return FoundBoard(name=name.strip(), ats=ats, slug=slug, jobs=jobs, query=company)
    except (httpx.HTTPError, ValueError):
        return None


def find_boards(client: httpx.Client, companies: list[str], workers: int = 12,
                topic: list[str] | None = None) -> tuple[list[FoundBoard], list[str]]:  # fmt: skip
    """Check every company's likely slugs on every board. Returns (boards found, companies with none);
    a company with boards on several sites keeps the one with the most jobs. With `topic`, a board's
    listings must also mention one of those words (for model suggestions: the field searched)."""
    checks = [(c, ats, slug, trusted) for c in companies for slug, trusted in slug_candidates(c) for ats in ATS]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda t: (t[0], probe(client, t[1], t[2], t[0], t[3], topic)), checks))
    best: dict[str, FoundBoard] = {}
    for company, found in results:
        if found and (company not in best or found.jobs > best[company].jobs):
            best[company] = found
    seen: set[tuple[str, str]] = set()
    boards = []
    for c in companies:  # keep the model's order (most relevant first); one entry per board
        b = best.get(c)
        if b and (b.ats, b.slug) not in seen:
            seen.add((b.ats, b.slug))
            boards.append(b)
    return boards, [c for c in companies if c not in best]


def suggest_companies(text: str, settings: Settings, n: int = 60, llm: Any = None) -> list[str]:
    """Company names for the fields described, from the `extract` step's model."""
    from autotailor.chains.prompts import load_prompt

    if llm is None:
        from autotailor.llm import get_chat_model

        llm = get_chat_model(settings, "extract")
    from autotailor.llm import structured

    chain = load_prompt("suggest_companies", SuggestedCompanies) | structured(llm, SuggestedCompanies)
    out: SuggestedCompanies = chain.invoke({"interests": text.strip()[:500], "n": str(n)})
    names = [" ".join(c.name.split()) for c in out.companies if c.name.strip()]
    return list(dict.fromkeys(names))[: n * 2]


# --- industries and the catalog ----------------------------------------------------------------------

_HEALTH = ["biomedical", "bioengineering", "life science", "healthcare", "health care"]
INDUSTRIES: dict[str, list[str]] = {
    "biotech": [*_HEALTH, "biotech", "biology", "biological", "drug", "pharma", "therapeutic", "synthetic bio", "protein",
                "molecular", "biochem", "computational bio", "bioinformatic", "gene", "genetic", "crispr", "cell therap",
                "immunolog", "oncology", "cancer", "lab automation", "wet lab"],
    "genomics": [*_HEALTH, "genomic", "genetic", "dna", "rna", "sequencing", "bioinformatic", "computational bio",
                 "precision medicine", "single-cell", "proteomic"],
    "diagnostics": [*_HEALTH, "diagnos", "lab test", "medical imaging", "imaging", "radiolog", "patholog", "screening",
                    "cancer", "early detection"],
    "medical devices": [*_HEALTH, "medical device", "medtech", "med tech", "neurotech", "neuroscience", "brain",
                        "surgical", "surgery", "implant", "wearable", "prosthe", "biomechan", "medical robot", "bci"],
    "digital health": [*_HEALTH, "health", "medicine", "medical", "clinical", "clinic", "hospital", "patient",
                       "mental health", "therapy", "telehealth", "telemedicine", "wellness", "fitness", "nutrition",
                       "public health", "epidemiolog", "health insurance", "nursing", "pharmacy"],
    "ai": ["ai", "a.i.", "artificial intelligence", "machine learning", "ml", "llm", "deep learning", "nlp",
           "computer vision", "generative", "data science", "language model", "agents"],
    "developer tools": ["developer", "devtools", "dev tools", "infrastructure", "cloud", "database", "devops",
                        "open source", "backend", "data engineering", "distributed systems", "observability"],
    "security": ["security", "cybersecurity", "cyber", "identity", "privacy", "infosec"],
    "fintech": ["fintech", "finance", "financial", "payments", "banking", "crypto", "blockchain", "lending", "insurtech"],
    "quant trading": ["quant", "trading", "hedge fund", "market making", "quantitative", "high frequency", "hft"],
    "consumer": ["consumer", "social media", "social network", "marketplace", "e-commerce", "ecommerce", "retail",
                 "travel", "food", "mobile app"],
    "games and media": ["game", "gaming", "entertainment", "media", "music", "streaming", "video", "advertising", "adtech"],
    "business software": ["saas", "enterprise", "productivity", "b2b", "hr tech", "legal", "design tool", "collaboration"],
    "robotics and autonomy": ["robot", "autonomous", "self-driving", "drone", "autonomy", "mechatronic", "embedded",
                              "mechanical engineering"],
    "aerospace and defense": ["aerospace", "space", "defense", "defence", "rocket", "satellite", "aviation",
                              "national security", "military"],
    "hardware": ["hardware", "semiconductor", "chip", "electrical engineering", "embedded", "ev", "electric vehicle",
                 "computer architecture"],
    "climate and energy": ["climate", "energy", "sustainab", "clean tech", "cleantech", "renewable", "battery",
                           "carbon", "environment", "solar", "wind power", "grid", "decarboni", "electrifica",
                           "fusion", "geothermal", "hydrogen", "emission"],
    "education": ["education", "edtech", "learning", "teaching"],
    "logistics": ["logistics", "supply chain", "shipping", "delivery", "transportation"],
}  # fmt: skip


def match_industries(text: str) -> list[str]:
    """Industries whose keywords appear in the text (whole words, or word starts like 'robot')."""
    low = " " + text.lower() + " "
    return [ind for ind, kws in INDUSTRIES.items() if any(re.search(rf"(?<![a-z]){re.escape(k)}", low) for k in kws)]


def pick_industries(text: str, settings: Settings, llm: Any = None) -> list[str]:
    """The model's choice of industries for a description no keyword matched."""
    from typing import Literal

    from pydantic import create_model

    from autotailor.chains.prompts import load_prompt

    Industry = Literal[tuple(INDUSTRIES)]  # type: ignore[valid-type]
    Picked = create_model("PickedIndustries", industries=(list[Industry], Field(description="The matching industries.")))
    if llm is None:
        from autotailor.llm import get_chat_model

        llm = get_chat_model(settings, "extract")
    from autotailor.llm import structured

    chain = load_prompt("pick_industries", Picked) | structured(llm, Picked)
    out = chain.invoke({"interests": text.strip()[:500], "industries": ", ".join(INDUSTRIES)})
    return list(dict.fromkeys(out.industries))


def builtin_catalog() -> list[dict]:
    from importlib import resources

    return json.loads((resources.files("autotailor.sources") / "suggested_boards.json").read_text(encoding="utf-8"))


def load_learned(store: Path | None) -> dict:
    """{"added": [board, ...], "gone": [[ats, slug], ...]} from the user's store (empty if none)."""
    if store is None or not store.exists():
        return {"added": [], "gone": []}
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
        return {"added": list(data.get("added", [])), "gone": [list(g) for g in data.get("gone", [])]}
    except (OSError, ValueError):
        return {"added": [], "gone": []}


def save_learned(store: Path, learned: dict) -> None:
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(".tmp")
    tmp.write_text(json.dumps(learned, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(store)


def catalog(store: Path | None = None) -> list[dict]:
    """The built-in catalog plus boards learned from model suggestions, without ones found gone."""
    learned = load_learned(store)
    gone = {tuple(g) for g in learned["gone"]}
    boards: dict[tuple[str, str], dict] = {}
    for b in [*builtin_catalog(), *learned["added"]]:
        key = (b["ats"], b["slug"])
        if key in gone:
            continue
        if key in boards:  # learned again under more industries
            boards[key] = {**boards[key], "industries": sorted(set(boards[key]["industries"]) | set(b["industries"]))}
        else:
            boards[key] = b
    return list(boards.values())


def count_jobs(client: httpx.Client, ats: str, slug: str) -> int | None:
    """Open jobs on a board right now, or None if the board is gone."""
    try:
        r = client.get(_URLS[ats].format(slug=slug))
        if r.status_code != 200:
            return None
        data: Any = r.json()
        return len(data.get("jobs", [])) if isinstance(data, dict) else len(data)
    except (httpx.HTTPError, ValueError):
        return None


def _plausible(names: list[str]) -> list[str]:
    """Drop the runs of near-identical invented names small models produce ("BioTechLab",
    "BioTechSaaS", ...): any name sharing its first five letters with three or more others."""
    from collections import Counter

    stems = Counter(re.sub(r"[^a-z]", "", n.lower())[:5] for n in names)
    return [n for n in names if stems[re.sub(r"[^a-z]", "", n.lower())[:5]] < 4]


@dataclass
class Discovery:
    industries: list[str]
    boards: list[FoundBoard]  # catalog boards first, then ones the model suggested
    from_model: list[str]  # names of boards that came from the model's suggestions
    not_found: list[str]  # model suggestions with no board found


def discover(text: str, settings: Settings, client: httpx.Client, ask_model: bool = True, n: int = 40,
             llm: Any = None, workers: int = 16, store: Path | None = None) -> Discovery:  # fmt: skip
    """Boards for the fields described in `text`. With `store`, the catalog learns from this
    search: new boards from the model's suggestions are added, and boards found gone are dropped."""
    industries = match_industries(text) or pick_industries(text, settings, llm)
    wanted = set(industries)
    if store is not None:
        recheck_gone(client, store, workers)  # boards back since are searched again
    learned = load_learned(store)
    every = catalog(store)
    listed = [b for b in every if wanted & set(b.get("industries", []))]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        counts = list(pool.map(lambda b: count_jobs(client, b["ats"], b["slug"]), listed))
    boards = [FoundBoard(b["name"], b["ats"], b["slug"], jobs, b["name"]) for b, jobs in zip(listed, counts, strict=True)
              if jobs is not None]  # fmt: skip
    gone = {(b["ats"], b["slug"]) for b, jobs in zip(listed, counts, strict=True) if jobs is None}
    from_model: list[str] = []
    missing: list[str] = []
    extra: list[FoundBoard] = []
    if ask_model:
        known = {re.sub(r"[^a-z0-9]", "", b["name"].lower()) for b in every}
        names = [x for x in _plausible(suggest_companies(text, settings, n, llm))
                 if re.sub(r"[^a-z0-9]", "", x.lower()) not in known]  # fmt: skip
        # The model's picks need more evidence than the hand-checked catalog: the board's listings
        # must also mention the field (its industries' keywords), or a namesake slips in.
        topic = sorted({k for ind in industries for k in INDUSTRIES[ind] if len(k) > 3})
        extra, missing = find_boards(client, names, workers, topic)
        have = {(b.ats, b.slug) for b in boards}
        extra = [b for b in extra if (b.ats, b.slug) not in have]
        boards += extra
        from_model = [b.name for b in extra]
    if store is not None:
        # Forget gone boards (they're skipped until a search finds them back); add the model's finds.
        was_gone = {tuple(g) for g in learned["gone"]}
        learned["added"] = [b for b in learned["added"] if (b["ats"], b["slug"]) not in gone]
        builtin = {(b["ats"], b["slug"]) for b in builtin_catalog()}
        learned["gone"] = sorted(list(g) for g in (was_gone | (gone & builtin)))
        now = datetime.now(UTC).isoformat(timespec="seconds")
        learned["added"] += [{"name": b.name, "ats": b.ats, "slug": b.slug, "industries": sorted(wanted),
                              "found_for": text.strip()[:200], "added_at": now} for b in extra]  # fmt: skip
        if gone or extra or learned != load_learned(store):
            save_learned(store, learned)
    boards.sort(key=lambda b: (b.name in from_model, -b.jobs))  # catalog first, most jobs first
    return Discovery(industries, boards, from_model, missing)


LEARNED = "learned_boards.json"  # in the data folder


def recheck_gone(client: httpx.Client, store: Path, workers: int = 16) -> int:
    """Look again at boards marked gone; those that are back are used again. Returns how many."""
    learned = load_learned(store)
    if not learned["gone"]:
        return 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        alive = list(pool.map(lambda g: count_jobs(client, g[0], g[1]) is not None, learned["gone"]))
    back = sum(alive)
    if back:
        learned["gone"] = [g for g, ok in zip(learned["gone"], alive, strict=True) if not ok]
        save_learned(store, learned)
    return back
