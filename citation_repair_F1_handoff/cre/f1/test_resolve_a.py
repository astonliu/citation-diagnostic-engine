"""F2-F tests: A-side resolver cascade + A-vs-B routing (no live network).

Every HTTP call is driven by a fake session whose ``get`` returns canned JSON, so
the cascade, the three-way routing, and the pre-flight are exercised with zero
real requests. Mirrors the fake-session style of the existing biblio_match tests.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import resolve_a as ra
from cre.f1.resolve_a import (assess_a_vs_b, resolve_by_cited_doi, preflight,
                              resolve_a_batch, OUTCOME_NOT_F2,
                              OUTCOME_F2_WITH_REPAIR, OUTCOME_UNSCOREABLE)
from cre.f1.schema import ClaimedRef, RetrievedRecord


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
        for frag, payload in self.routes.items():
            if frag in url:
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
    sess = _FakeSession({})   # every search misses
    ar = assess_a_vs_b(claimed, b, session=sess,
                       steps=("cited_doi", "pubmed", "candidates"))
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
