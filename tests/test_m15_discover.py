"""Finding companies' job boards from a description of the fields a user is interested in."""

from __future__ import annotations


import httpx
from langchain_core.runnables import RunnableLambda

from autotailor.config import Settings
from autotailor.sources import discover as d

# A fake set of job boards: what each API knows.
BOARDS = {
    ("greenhouse", "natera"): {"name": "Natera", "jobs": 3, "text": "Natera is hiring"},
    ("greenhouse", "verve"): {"name": "Verve", "jobs": 2, "text": "Verve ad platform"},  # not Verve Therapeutics
    ("ashby", "cohere"): {"name": None, "jobs": 4, "text": "Cohere builds large language models"},  # the AI lab
    ("ashby", "abridge"): {"name": None, "jobs": 5, "text": "Abridge: generative AI for clinical documentation"},
    ("lever", "ginkgobioworks"): {"name": None, "jobs": 1, "text": "Ginkgo Bioworks programs cells with synthetic biology"},
}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    for (ats, slug), b in BOARDS.items():
        if ats == "greenhouse" and url.endswith(f"/v1/boards/{slug}"):
            return httpx.Response(200, json={"name": b["name"]})
        if ats == "greenhouse" and url.endswith(f"/v1/boards/{slug}/jobs"):
            return httpx.Response(200, json={"jobs": [{"title": b["text"]}] * b["jobs"]})
        if ats == "lever" and f"/postings/{slug}?" in url:
            return httpx.Response(200, json=[{"text": b["text"]}] * b["jobs"])
        if ats == "ashby" and url.endswith(f"/job-board/{slug}"):
            return httpx.Response(200, json={"jobs": [{"title": b["text"]}] * b["jobs"]})
    return httpx.Response(404, json={"error": "not found"})


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler))


class FakeModel:
    def __init__(self, reply) -> None:  # noqa: ANN001
        self.reply = reply

    def with_structured_output(self, schema, **kwargs):  # noqa: ANN001, ANN003
        return RunnableLambda(lambda _: schema.model_validate(self.reply))


def test_words_are_matched_to_industries():
    assert d.match_industries("biomedical") == ["biotech", "genomics", "diagnostics", "medical devices", "digital health"]
    assert d.match_industries("robots and space") == ["robotics and autonomy", "aerospace and defense"]
    assert "quant trading" in d.match_industries("Quant finance")
    assert d.match_industries("journalism") == []
    picked = d.pick_industries("journalism", Settings(), llm=FakeModel({"industries": ["games and media"]}))
    assert picked == ["games and media"]


def test_slug_guesses_and_checks():
    assert d.slug_candidates("Recursion Pharmaceuticals") == [
        ("recursionpharmaceuticals", True), ("recursion-pharmaceuticals", True), ("recursion", False)]
    with _client() as c:
        found, missing = d.find_boards(c, ["Natera", "Verve Therapeutics", "Cohere Health", "Abridge", "Ginkgo Bioworks", "Nope Bio"])
    assert {(b.ats, b.slug) for b in found} == {("greenhouse", "natera"), ("ashby", "abridge"), ("lever", "ginkgobioworks")}
    assert set(missing) == {"Verve Therapeutics", "Cohere Health", "Nope Bio"}  # other companies' boards are refused


def test_invented_name_runs_are_dropped():
    names = ["Moderna", "BioTechLab", "BioTechSaaS", "BioTechData", "BioTechCorp", "Natera"]
    assert d._plausible(names) == ["Moderna", "Natera"]


def test_discover_uses_the_catalog_then_the_model(monkeypatch):
    monkeypatch.setattr(d, "builtin_catalog", lambda: [
        {"name": "Natera", "ats": "greenhouse", "slug": "natera", "industries": ["diagnostics"]},
        {"name": "Gone Bio", "ats": "greenhouse", "slug": "gonebio", "industries": ["biotech"]},  # board removed since
        {"name": "Stripe", "ats": "greenhouse", "slug": "stripe", "industries": ["fintech"]},
    ])  # fmt: skip
    llm = FakeModel({"companies": [{"name": "Ginkgo Bioworks"}, {"name": "Natera"}, {"name": "Made Up Bio"}]})
    with _client() as c:
        r = d.discover("biomedical", Settings(), c, ask_model=True, llm=llm)
    assert [(b.slug, b.jobs) for b in r.boards] == [("natera", 3), ("ginkgobioworks", 1)]
    assert r.from_model == ["Ginkgo Bioworks"] and r.not_found == ["Made Up Bio"]
    with _client() as c:
        only = d.discover("biomedical", Settings(), c, ask_model=False)
    assert [b.slug for b in only.boards] == ["natera"]


def test_catalog_is_well_formed():
    boards = d.catalog()
    keys = [(b["ats"], b["slug"]) for b in boards]
    assert len(keys) == len(set(keys)) and len(boards) > 250
    assert all(b["ats"] in d.ATS and b["industries"] and set(b["industries"]) <= set(d.INDUSTRIES) for b in boards)
    health = [b for b in boards if {"biotech", "genomics", "diagnostics", "medical devices", "digital health"} & set(b["industries"])]
    assert len(health) > 150


def test_api_discover(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from typer.testing import CliRunner

    from autotailor.api import TOKEN_HEADER, api_token, create_app
    from autotailor.cli import app
    from autotailor.paths import get_paths

    monkeypatch.setenv("AUTOTAILOR_HOME", str(tmp_path))
    CliRunner().invoke(app, ["init"])
    monkeypatch.setattr("autotailor.sources.make_client", _client)
    monkeypatch.setattr(d, "builtin_catalog", lambda: [{"name": "Natera", "ats": "greenhouse", "slug": "natera", "industries": ["diagnostics"]}])
    c = TestClient(create_app(tmp_path))
    c.headers[TOKEN_HEADER] = api_token(get_paths(tmp_path))
    r = c.post("/api/setup/discover", json={"text": "biomedical", "ask_model": False}).json()
    assert r["industries"][0] == "biotech" and r["boards"] == [
        {"name": "Natera", "ats": "greenhouse", "slug": "natera", "jobs": 3, "following": False, "from_model": False}]
    assert c.post("/api/setup/discover", json={"text": "x"}).status_code == 422


def test_the_catalog_learns_from_model_finds_and_forgets_gone_boards(tmp_path, monkeypatch):
    store = tmp_path / "learned_boards.json"
    builtin = [{"name": "Natera", "ats": "greenhouse", "slug": "natera", "industries": ["diagnostics"]},
               {"name": "Gone Bio", "ats": "greenhouse", "slug": "gonebio", "industries": ["biotech"]}]  # fmt: skip
    monkeypatch.setattr(d, "builtin_catalog", lambda: builtin)
    llm = FakeModel({"companies": [{"name": "Ginkgo Bioworks"}]})
    with _client() as c:
        first = d.discover("biomedical", Settings(), c, ask_model=True, llm=llm, store=store)
    assert first.from_model == ["Ginkgo Bioworks"]
    learned = d.load_learned(store)
    assert [b["slug"] for b in learned["added"]] == ["ginkgobioworks"] and "biotech" in learned["added"][0]["industries"]
    assert learned["gone"] == [["greenhouse", "gonebio"]]

    # Next time, without the model: the learned board comes from the catalog; the gone one is skipped.
    with _client() as c:
        second = d.discover("biomedical", Settings(), c, ask_model=False, store=store)
    assert {b.slug for b in second.boards} == {"natera", "ginkgobioworks"} and second.from_model == []
    assert ("greenhouse", "gonebio") not in {(b["ats"], b["slug"]) for b in d.catalog(store)}

    # The gone board comes back: the next search notices and lists it again.
    BOARDS[("greenhouse", "gonebio")] = {"name": "Gone Bio", "jobs": 2, "text": "Gone Bio"}
    try:
        with _client() as c:
            third = d.discover("biomedical", Settings(), c, ask_model=False, store=store)
        assert "gonebio" in {b.slug for b in third.boards} and d.load_learned(store)["gone"] == []
    finally:
        del BOARDS[("greenhouse", "gonebio")]

    # A learned board that disappears is dropped from the store.
    BOARDS_BACKUP = BOARDS.pop(("lever", "ginkgobioworks"))
    try:
        with _client() as c:
            d.discover("biomedical", Settings(), c, ask_model=False, store=store)
        assert d.load_learned(store)["added"] == []
    finally:
        BOARDS[("lever", "ginkgobioworks")] = BOARDS_BACKUP


def test_model_finds_must_mention_the_field():
    with _client() as c:
        found, _ = d.find_boards(c, ["Natera", "Abridge"], topic=["clinical", "climate"])
    assert [b.slug for b in found] == ["abridge"]  # Abridge's jobs mention "clinical"; Natera's don't
