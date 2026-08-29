"""F2-F tests: A-side resolver cascade + A-vs-B routing (no live network).

Every HTTP call is driven by a fake session whose ``get`` returns canned JSON, so
the cascade, the three-way routing, and the pre-flight are exercised with zero
real requests. Mirrors the fake-session style of the existing biblio_match tests.
"""
from __future__ import annotations

import json

import pytest

from cde.refs import resolve_a as ra
from cde.refs.resolve_a import (assess_a_vs_b, resolve_by_cited_doi, preflight,
                              resolve_a_batch, OUTCOME_NOT_F2,
                              OUTCOME_F2_WITH_REPAIR, OUTCOME_UNSCOREABLE)
from cde.refs.schema import ClaimedRef, RetrievedRecord


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    # The error tests raise RequestException, which request_with_retry retries with
    # real exponential backoff. Drive it with zero sleeping (the backoff is
    # injectable for exactly this reason); production behavior is unchanged.
    import cde.runtime.ratelimit as rl
    monkeypatch.setattr(rl.time, "sleep", lambda *a, **k: None)


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.headers = {}

    def json(self):
        return self._payload


class _FakeSession:
    """Routes URLs to canned responses. ``routes`` maps a URL substring -> payload
    (or None for a 404-ish miss)."""
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(url)
        # DOIs are case-insensitive and the resolver normalizes them to lowercase
        # before building the URL, so match case-insensitively.
        low = url.lower()
        for frag, payload in self.routes.items():
            if frag.lower() in low:
                if isinstance(payload, Exception):
                    raise payload              # simulate a thrown request
                if payload is None:
                    return _Resp({}, status=404)
                return _Resp(payload)
        return _Resp({}, status=404)


def _crossref_work(doi, title, author_family="Aguilar", year=2020,
                   journal="Bioinformatics"):
    return {"message": {
        "DOI": doi, "title": [title], "author": [{"family": author_family}],
        "issued": {"date-parts": [[year]]}, "container-title": [journal],
        "volume": "36", "page": "100-108"}}


# --------------------------------------------------------------------------
# Step 1 -- cited DOI dereference
# --------------------------------------------------------------------------
def test_cited_doi_dereferences_when_distinct():
    claimed = ClaimedRef(title="A convolutional network approach to variant "
                         "detection", authors=["Aguilar"], year=2020,
                         claimed_doi="10.1093/bioinformatics/btz100")
    sess = _FakeSession({"10.1093/bioinformatics/btz100": _crossref_work(
        "10.1093/bioinformatics/btz100",
        "A convolutional network approach to variant detection")})
    rec = resolve_by_cited_doi(claimed, "10.9999/wrong.doi", session=sess)
    assert rec is not None and rec.resolved
    assert rec.doi == "10.1093/bioinformatics/btz100"


def test_cited_doi_skipped_when_same_as_resolved():
    claimed = ClaimedRef(claimed_doi="10.1093/x")
    assert resolve_by_cited_doi(claimed, "10.1093/X", session=_FakeSession({})) is None


def test_cited_doi_skipped_when_absent():
    assert resolve_by_cited_doi(ClaimedRef(), "10.1/y", session=_FakeSession({})) is None


# --------------------------------------------------------------------------
# Three-way routing
# --------------------------------------------------------------------------
def test_a_resolves_to_c_not_b_is_f2_with_repair():
    # Cited DOI resolves A to a DIFFERENT paper than B -> F2, repair target = A.
    claimed = ClaimedRef(title="Neural semantic networks in aging",
                         authors=["Lee"], year=2020,
                         claimed_doi="10.1000/A-real-cited")
    b = RetrievedRecord(resolved=True, title="Impurity profiling of aspirin",
                        authors=["Jones"], year=2020, doi="10.1000/B-resolved",
                        pmid="222")
    sess = _FakeSession({"10.1000/A-real-cited": _crossref_work(
        "10.1000/A-real-cited", "Neural semantic networks in aging",
        author_family="Lee")})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("cited_doi",))
    assert ar.outcome == OUTCOME_F2_WITH_REPAIR
    assert ar.proposed_repair["doi"] == "10.1000/a-real-cited"   # DOIs normalized lc
    assert ar.a_vs_b_doi_match is False


def test_a_resolves_to_b_is_not_f2():
    # Cited DOI resolves A to the SAME DOI as B -> identifier correct, not F2.
    claimed = ClaimedRef(title="Some title", authors=["Lee"], year=2020,
                         claimed_doi="10.1000/shared")
    b = RetrievedRecord(resolved=True, title="Some title", authors=["Lee"],
                        year=2020, doi="10.1000/SHARED", pmid="222")
    # DOIs equal after normalization -> cited-doi step is skipped; fall through to
    # candidates, which returns B-equivalent. Simulate via candidates search miss +
    # title match through the OpenAlex/crossref search returning the same title.
    sess = _FakeSession({"api.crossref.org/works": {"message": {"items": [
        {"DOI": "10.1000/shared", "title": ["Some title"],
         "author": [{"family": "Lee"}], "issued": {"date-parts": [[2020]]},
         "container-title": ["J"], "volume": "1", "page": "1-2"}]}}})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("cited_doi", "candidates"))
    assert ar.outcome == OUTCOME_NOT_F2


def test_a_resolves_to_nothing_is_unscoreable():
    claimed = ClaimedRef(title="A totally unfindable garbled title fragment",
                         authors=["Nemo"], year=1900)
    b = RetrievedRecord(resolved=True, title="B", doi="10.1/b", pmid="1")
    # A genuine clean miss: every source COMPLETES (200) with no candidate. (An
    # all-404 session would now be retrieval_incomplete -> undetermined, §14.6.)
    sess = _FakeSession({"api.crossref.org/works": {"message": {"items": []}},
                         "api.openalex.org/works": {"results": []}})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("candidates",))
    assert ar.outcome == OUTCOME_UNSCOREABLE
    assert ar.proposed_repair is None


def test_resolution_carries_no_taxonomy_label():
    # DETECTOR OUTPUT IS NEVER GOLD: the result object exposes an outcome/evidence/
    # proposed repair, but nothing named 'label'.
    ar = assess_a_vs_b(ClaimedRef(title="x", authors=["Y"]),
                       RetrievedRecord(resolved=True, title="z", pmid="1"),
                       session=_FakeSession({}), steps=("candidates",))
    assert not hasattr(ar, "label")
    assert "label" not in json.dumps({k: v for k, v in vars(ar).items()})


# --------------------------------------------------------------------------
# Pre-flight + checkpointed batch
# --------------------------------------------------------------------------
def test_preflight_passes_on_good_roundtrip():
    sess = _FakeSession({"10.1093/bioinformatics/btz100": _crossref_work(
        "10.1093/bioinformatics/btz100",
        "A convolutional network approach to variant detection")})
    report = preflight(session=sess)
    assert report["ok"] and report["outcome"] == OUTCOME_F2_WITH_REPAIR


def test_preflight_raises_when_roundtrip_broken():
    with pytest.raises(RuntimeError):
        preflight(session=_FakeSession({}))   # DOI dereference misses -> broken


def test_batch_checkpoints_and_resumes(tmp_path):
    rows = [{"id": "r1", "doi": "10.1000/a1", "btitle": "B one", "bdoi": "10.1000/b1"},
            {"id": "r2", "doi": "10.1000/a2", "btitle": "B two", "bdoi": "10.1000/b2"}]
    sess = _FakeSession({
        "10.1000/a1": _crossref_work("10.1000/a1", "A one title"),
        "10.1000/a2": _crossref_work("10.1000/a2", "A two title")})
    out = tmp_path / "resolve_a.jsonl"
    counts = resolve_a_batch(
        rows, str(out),
        claimed_of=lambda r: ClaimedRef(title="written", authors=["A"],
                                        claimed_doi=r["doi"]),
        resolved_of=lambda r: RetrievedRecord(resolved=True, title=r["btitle"],
                                              doi=r["bdoi"], pmid="1"),
        id_of=lambda r: r["id"], session=sess, steps=("cited_doi",))
    assert counts[OUTCOME_F2_WITH_REPAIR] == 2
    # Re-run: both ids already checkpointed -> skipped, no duplicate lines.
    counts2 = resolve_a_batch(
        rows, str(out),
        claimed_of=lambda r: ClaimedRef(title="written", authors=["A"],
                                        claimed_doi=r["doi"]),
        resolved_of=lambda r: RetrievedRecord(resolved=True, title=r["btitle"],
                                              doi=r["bdoi"], pmid="1"),
        id_of=lambda r: r["id"], session=sess, steps=("cited_doi",))
    assert counts2["skipped"] == 2
    assert len(out.read_text().strip().splitlines()) == 2


# ==========================================================================
# Rev 5 audit: DataCite dereference (arXiv) + bioRxiv preprint->published link
# ==========================================================================
from cde.refs.resolve_a import (dereference_doi, resolve_by_datacite_doi,
                              biorxiv_published_doi)
from cde.refs.biblio_match import is_preprint_resolved


def _datacite_preprint(doi, title, family="Aguilar", year=2021):
    return {"data": {"attributes": {
        "doi": doi, "titles": [{"title": title}],
        "creators": [{"familyName": family}], "publicationYear": year,
        "container": {"title": ""},
        "types": {"resourceTypeGeneral": "Preprint"}}}}


def test_arxiv_doi_dereferences_via_datacite_not_crossref():
    doi = "10.48550/arXiv.2101.00001"
    # Crossref would 404 for an arXiv DOI; the fake has ONLY a DataCite route.
    sess = _FakeSession({"api.datacite.org/dois/10.48550/arxiv.2101.00001":
                         _datacite_preprint("10.48550/arxiv.2101.00001",
                                            "A deep learning preprint")})
    rec = dereference_doi(doi, session=sess)
    assert rec is not None and rec.title == "A deep learning preprint"
    # DataCite Preprint resourceType -> is_preprint_resolved fires (spec §9 analogue)
    assert is_preprint_resolved(rec) is True
    # routed straight to DataCite: no Crossref call was made
    assert not any("crossref" in u for u in sess.calls)


def test_crossref_miss_falls_back_to_datacite_not_called_dead():
    doi = "10.5555/only-on-datacite"
    sess = _FakeSession({
        "api.crossref.org/works/10.5555/only-on-datacite": None,   # 404
        "api.datacite.org/dois/10.5555/only-on-datacite":
            _datacite_preprint("10.5555/only-on-datacite", "Registered on DataCite",
                               year=2020)})
    rec = dereference_doi(doi, session=sess)
    assert rec is not None and rec.title == "Registered on DataCite"
    # Crossref was tried first, DataCite second -- a Crossref 404 is not "dead".
    assert any("crossref" in u for u in sess.calls)
    assert any("datacite" in u for u in sess.calls)


def test_biorxiv_published_link_routes_same_work_not_repair():
    # PMC8887078:R1 shape: cited bioRxiv preprint whose published version IS B.
    claimed = ClaimedRef(title="A new coronavirus associated with human "
                         "respiratory disease in China", authors=["Wu"], year=2020,
                         claimed_doi="10.1101/2020.02.07.937862")
    b = RetrievedRecord(resolved=True,
                        title="The species Severe acute respiratory syndrome-related "
                              "coronavirus: classifying 2019-nCoV and naming it "
                              "SARS-CoV-2", authors=["Coronaviridae Study Group"],
                        year=2020, doi="10.1038/s41564-020-0695-z", pmid="32123347")
    sess = _FakeSession({"api.biorxiv.org/details/biorxiv/10.1101/2020.02.07.937862":
                         {"collection": [{"published": "10.1038/s41564-020-0695-z"}]}})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("cited_doi",))
    assert ar.outcome == OUTCOME_NOT_F2
    assert ar.a_source == "biorxiv_relation"
    assert ar.proposed_repair is None
    assert ar.evidence["relation"] == "preprint_of"


def test_biorxiv_unpublished_preprint_falls_through_to_cascade():
    # A bioRxiv preprint with no recorded publication -> no version-family shortcut;
    # the cascade dereferences the preprint itself.
    claimed = ClaimedRef(title="An unpublished preprint", authors=["X"], year=2021,
                         claimed_doi="10.1101/2021.01.01.000001")
    b = RetrievedRecord(resolved=True, title="Different resolved paper",
                        doi="10.1000/b", pmid="1")
    sess = _FakeSession({
        "api.biorxiv.org/details/biorxiv/10.1101/2021.01.01.000001":
            {"collection": [{"published": "NA"}]},
        "api.biorxiv.org/details/medrxiv/10.1101/2021.01.01.000001":
            {"collection": [{"published": "NA"}]},
        "api.crossref.org/works/10.1101/2021.01.01.000001":
            _crossref_work("10.1101/2021.01.01.000001", "An unpublished preprint")})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("cited_doi",))
    assert ar.a_source != "biorxiv_relation"
    assert ar.outcome == OUTCOME_F2_WITH_REPAIR      # A (preprint) != B


def test_biorxiv_published_doi_ignores_non_biorxiv_dois():
    # A non-datestamped 10.1101 (CSHL journal) or any other DOI -> no bioRxiv call.
    assert biorxiv_published_doi("10.1101/gr.209601.116", session=_FakeSession({})) == ""
    assert biorxiv_published_doi("10.1038/x", session=_FakeSession({})) == ""


# ==========================================================================
# Item 3: OUTCOME_UNDETERMINED reachable (§14.6) + PMC8015328:ref011 resolver
# ==========================================================================
import requests as _requests  # noqa: E402
from cde.refs.resolve_a import OUTCOME_UNDETERMINED  # noqa: E402


def test_provider_error_routes_undetermined_not_unscoreable():
    # A thrown search request must NOT be conflated with 'A not found'.
    claimed = ClaimedRef(title="A findable enough title fragment", authors=["X"],
                         year=2010)
    b = RetrievedRecord(resolved=True, title="B", doi="10.1/b", pmid="1")
    sess = _FakeSession({"api.crossref.org/works": _requests.ConnectionError("boom"),
                         "api.openalex.org/works": _requests.ConnectionError("boom")})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("candidates",))
    assert ar.outcome == OUTCOME_UNDETERMINED
    assert ar.evidence["provider_errors"]        # records which source errored


def test_clean_miss_routes_unscoreable_not_undetermined():
    # Every source completed (200) with no candidate -> clean miss -> unscoreable.
    claimed = ClaimedRef(title="A totally unfindable garbled fragment",
                         authors=["Nemo"], year=1900)
    b = RetrievedRecord(resolved=True, title="B", doi="10.1/b", pmid="1")
    sess = _FakeSession({"api.crossref.org/works": {"message": {"items": []}},
                         "api.openalex.org/works": {"results": []}})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("candidates",))
    assert ar.outcome == OUTCOME_UNSCOREABLE


# --- PMC8015328:ref011 as a resolver fixture (§18.2) ---------------------------
# Confirmed TRUE_F2. The citation carries B's DOI (contaminated: written_doi ==
# resolved_doi), so path 1 is correctly skipped; Nematology is not PubMed-indexed,
# so path 2 misses and Crossref must carry A. A (Paurodontella persica) has its own
# DOI, distinct from B (P. composticola) -> f2_with_repair, repair target = A.
_REF011_TITLE = ("Description of Paurodontella persica sp. n. "
                 "(Nematoda: Hexatylina) from Iran")
_REF011_B_DOI = "10.11646/zootaxa.4658.1.9"       # the composticola record (B)
_REF011_A_DOI = "10.1163/15685411-00003140"        # persica's real DOI (A)


def _ref011_claimed():
    return ClaimedRef(title=_REF011_TITLE, authors=["Panahandeh"], year=2019,
                      journal="Nematology", claimed_doi=_REF011_B_DOI,
                      volume="21", pages="1-12")


def _ref011_b():
    return RetrievedRecord(
        resolved=True, title="Description of Paurodontella composticola sp. n.",
        authors=["Panahandeh"], year=2019, journal="Zootaxa", doi=_REF011_B_DOI,
        pmid="31169370", volume="4658", pages="120-130")


def _ref011_crossref_hit():
    return {"message": {"items": [{
        "DOI": _REF011_A_DOI, "title": [_REF011_TITLE],
        "author": [{"family": "Panahandeh"}], "issued": {"date-parts": [[2019]]},
        "container-title": ["Nematology"], "volume": "21", "page": "1-12"}]}}


def test_ref011_resolver_crossref_carries_it_to_f2_with_repair():
    sess = _FakeSession({"api.crossref.org/works": _ref011_crossref_hit(),
                         "api.openalex.org/works": {"results": []},
                         "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch":
                             {"esearchresult": {"idlist": []}}})
    ar = assess_a_vs_b(_ref011_claimed(), _ref011_b(), session=sess,
                       steps=("cited_doi", "pubmed", "candidates"))
    assert ar.outcome == OUTCOME_F2_WITH_REPAIR
    assert ar.proposed_repair["doi"] == _REF011_A_DOI
    assert ar.a_vs_b_doi_match is False


def test_ref011_resolver_crossref_error_is_undetermined():
    # Crossref throws -> undetermined, NOT unscoreable (the failure the audit
    # flagged: a network error was indistinguishable from 'A not found').
    sess = _FakeSession({"api.crossref.org/works": _requests.Timeout("slow"),
                         "api.openalex.org/works": _requests.Timeout("slow"),
                         "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch":
                             {"esearchresult": {"idlist": []}}})
    ar = assess_a_vs_b(_ref011_claimed(), _ref011_b(), session=sess,
                       steps=("cited_doi", "pubmed", "candidates"))
    assert ar.outcome == OUTCOME_UNDETERMINED


def test_ref011_resolver_crossref_clean_miss_is_unscoreable():
    # Crossref completes but does not index Nematology -> clean miss -> unscoreable
    # (distinct from the network-error case above).
    sess = _FakeSession({"api.crossref.org/works": {"message": {"items": []}},
                         "api.openalex.org/works": {"results": []},
                         "eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch":
                             {"esearchresult": {"idlist": []}}})
    ar = assess_a_vs_b(_ref011_claimed(), _ref011_b(), session=sess,
                       steps=("cited_doi", "pubmed", "candidates"))
    assert ar.outcome == OUTCOME_UNSCOREABLE


# ==========================================================================
# Item 4: uniqueness check -> ambiguous_multiple (§14.5)
# ==========================================================================
from cde.refs.resolve_a import OUTCOME_AMBIGUOUS_MULTIPLE, resolve_by_candidates  # noqa: E402


def test_two_eligible_clusters_route_ambiguous_multiple():
    # Two DISTINCT works (different DOIs) whose titles both clear the eligibility
    # bar against the written title -> refuse to pick one; ambiguous_multiple, no
    # repair. Titles differ (so retrieve_candidates does not dedup them) but each
    # is >= A_TITLE_CONFIDENT similar to the claimed title.
    claimed = ClaimedRef(title="Deep learning for medical image segmentation",
                         authors=["Lee"], year=2020)
    b = RetrievedRecord(resolved=True, title="An unrelated resolved paper",
                        doi="10.1/b", pmid="1")
    sess = _FakeSession({"api.crossref.org/works": {"message": {"items": [
        {"DOI": "10.1000/x1", "title": ["Deep learning for medical image "
         "segmentation"], "author": [{"family": "Lee"}],
         "issued": {"date-parts": [[2020]]}, "container-title": ["J A"]},
        {"DOI": "10.1000/x2", "title": ["Deep learning for medical image "
         "segmentation methods"], "author": [{"family": "Kim"}],
         "issued": {"date-parts": [[2019]]}, "container-title": ["J B"]}]}},
        "api.openalex.org/works": {"results": []}})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("candidates",))
    assert ar.outcome == OUTCOME_AMBIGUOUS_MULTIPLE
    assert ar.proposed_repair is None
    assert len(ar.evidence["candidates"]) == 2


def test_one_eligible_cluster_resolves_uniquely():
    # Two hits that are the SAME work (shared DOI) collapse to one cluster ->
    # resolved_unique, a repair is proposed.
    claimed = ClaimedRef(title="Deep learning for medical image segmentation",
                         authors=["Lee"], year=2020)
    b = RetrievedRecord(resolved=True, title="An unrelated resolved paper",
                        doi="10.1/b", pmid="1")
    sess = _FakeSession({"api.crossref.org/works": {"message": {"items": [
        {"DOI": "10.1000/same", "title": ["Deep learning for medical image "
         "segmentation"], "author": [{"family": "Lee"}],
         "issued": {"date-parts": [[2020]]}, "container-title": ["J A"]}]}},
        "api.openalex.org/works": {"results": [
            {"doi": "https://doi.org/10.1000/same",
             "title": "Deep learning for medical image segmentation",
             "publication_year": 2020}]}})
    ar = assess_a_vs_b(claimed, b, session=sess, steps=("candidates",))
    assert ar.outcome == OUTCOME_F2_WITH_REPAIR
    assert ar.proposed_repair["doi"] == "10.1000/same"
