"""Fixture-based tests for the no-ID branch (HANDOFF_NOID task).

References with no claimed PMID used to be excluded outright ("unverifiable",
the Topaz blind spot). They now run a structured bibliographic lookup (Crossref
bibliographic + OpenAlex title search) and only escalate -- never go straight to
F1. The precision-first rule for this path: a confident no-find is HUMAN_REVIEW,
not F1.

No network is touched: recorded responses in ./fixtures are replayed through the
same FakeSession used by test_live_paths, and the LLM `complete` callable is a
plain lambda. Mirrors that module's fakes so the two suites stay consistent.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_noid.py -q
"""
from __future__ import annotations
import json
import os

import pytest
import requests

from cre.f1 import lookup, run, ratelimit, confirm
from cre.f1 import schema as S
from cre.f1.lookup import compare_and_flag, fuzzy_biblio_lookup
from cre.f1.decide import decide
from cre.f1.run import process_reference
from cre.f1.schema import Reference, ClaimedRef, RetrievedRecord

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


def _fx(name: str) -> dict:
    with open(os.path.join(FIX, name)) as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Fakes (same shape as test_live_paths)
# --------------------------------------------------------------------------
class FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None, headers=None):
        self.status_code = status_code
        self.text = text
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeSession:
    """Routes GETs through a handler(url, params, call_index) -> FakeResponse."""
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        r = self.handler(url, params, len(self.calls))
        self.calls.append((url, params))
        return r


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # Exercise throttle + backoff logic, but never actually sleep.
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(run.time, "sleep", lambda *a, **k: None)


def _fabrication(_prompt: str) -> str:
    return '{"verdict": "fabrication", "reason": "no real paper found"}'


def _formatting(_prompt: str) -> str:
    return '{"verdict": "formatting_discrepancy", "reason": "abbreviated title"}'


def _boom(_prompt: str) -> str:                # LLM must not run on these paths
    raise AssertionError("LLM filter should not be called here")


# Routers reused across the end-to-end cases.
def _route_all_empty(url, params, n):
    if "esearch" in url:
        return FakeResponse(200, json_data=_fx("pubmed_esearch_empty.json"))
    if "esummary" in url:
        return FakeResponse(200, json_data={"result": {}})
    if "crossref" in url:
        return FakeResponse(200, json_data=_fx("crossref_empty.json"))
    if "openalex" in url:
        return FakeResponse(200, json_data=_fx("openalex_empty.json"))
    return FakeResponse(404)


def _route_all_errored(url, params, n):
    return FakeResponse(500)                    # every DB errors


# ==========================================================================
# 1. No title, no PMID -> UNVERIFIABLE, never attempts a lookup.
# ==========================================================================
def test_no_title_no_pmid_is_unverifiable():
    ref = Reference("t1", "", ClaimedRef())    # no title, no claimed PMID
    sess = FakeSession(lambda u, p, n: FakeResponse(500))   # must stay untouched
    flagged = compare_and_flag(ref, 85.0, session=sess)

    assert flagged is False
    assert ref.log.noid_lookup_attempted is False
    assert sess.calls == []                    # genuinely nothing to search on

    out = decide(ref, flagged, None, None)
    assert out.label == S.UNVERIFIABLE
    assert out.confidence == "HIGH"
    assert out.log.decided_by == "no_pmid_no_title"


# ==========================================================================
# 2. No PMID, title found in Crossref at high similarity -> CLEARED.
# ==========================================================================
def test_no_pmid_high_similarity_match_is_cleared():
    def handler(url, params, n):
        if "crossref" in url:
            return FakeResponse(200, json_data=_fx("crossref_noid_match.json"))
        if "openalex" in url:
            return FakeResponse(200, json_data=_fx("openalex_empty.json"))
        return FakeResponse(404)

    ref = Reference("t2", "", ClaimedRef(
        title="Bibliometric drift in retracted oncology literature",
        authors=["Okafor"], year=2021, journal="Journal of Scholarly Metrics"))

    out = process_reference(ref, _boom, session=FakeSession(handler))

    assert out.label == S.CLEARED
    assert ref.log.noid_lookup_attempted is True
    assert ref.log.mismatch_flagged is False
    assert ref.log.title_similarity >= 85.0
    assert ref.retrieved.resolved is True
    assert ref.retrieved.pmid == ""            # no PMID on this path
    assert out.log.decided_by == "noid_metadata_match"


# ==========================================================================
# 3. No PMID, a candidate is found but title similarity is low -> continues to
#    the LLM/confirm path; LLM says formatting_discrepancy -> CLEARED.
# ==========================================================================
def test_no_pmid_low_similarity_continues_to_llm(monkeypatch):
    # fuzzy_biblio_lookup itself gates on title_similarity, so a real resolved
    # hit always clears the same threshold compare_and_flag re-checks. Patch it
    # to return a resolved record with a dissimilar title to drive the low-sim
    # branch deterministically.
    monkeypatch.setattr(lookup, "fuzzy_biblio_lookup",
                        lambda ref, threshold=85.0, session=None:
                        RetrievedRecord(resolved=True,
                                        title="An entirely different paper title"))

    ref = Reference("t3", "", ClaimedRef(
        title="Bibliometric drift in retracted oncology literature"))

    out = process_reference(ref, _formatting, session=FakeSession(_route_all_empty))

    assert ref.log.noid_lookup_attempted is True
    assert ref.log.mismatch_flagged is True            # low-sim -> flagged
    assert ref.log.title_similarity < 85.0
    assert ref.log.llm_verdict == S.V_FORMATTING
    assert out.label == S.CLEARED                       # formatting, not F1
    assert out.log.decided_by == "llm_formatting"


# ==========================================================================
# 4. No PMID, found nowhere (both lookup DBs empty, then confirm empty) ->
#    HUMAN_REVIEW. NEVER F1.
# ==========================================================================
def test_no_pmid_not_found_anywhere_is_human_review():
    ref = Reference("t4", "", ClaimedRef(
        title="Quantum entanglement therapy for refractory migraine"))

    out = process_reference(ref, _fabrication, session=FakeSession(_route_all_empty))

    assert ref.log.noid_lookup_attempted is True
    assert ref.log.noid_not_found is True
    assert ref.log.llm_verdict == S.V_FABRICATION
    assert out.label == S.HUMAN_REVIEW
    assert out.label != S.F1                            # precision-first
    assert out.confidence == "MED"
    assert out.log.decided_by == "noid_confirm_not_found_human_review"


# ==========================================================================
# 5. No PMID, every DB errors (network failure) -> HUMAN_REVIEW via the
#    existing all-errored safeguard. NEVER F1.
# ==========================================================================
def test_no_pmid_all_dbs_errored_is_human_review(monkeypatch):
    # Force the confirmation searches to the errored (None) state explicitly.
    # The cheap fuzzy lookup still errors via FakeSession(500); confirm() reads
    # module-global search_* functions, which a protected script-style test
    # (test_pipeline.py) reassigns at collection time and never restores -- so
    # pin them here to keep this test deterministic under full-suite runs.
    for fn in ("search_pubmed", "search_crossref", "search_openalex"):
        monkeypatch.setattr(confirm, fn, lambda *a, **k: None)

    ref = Reference("t5", "", ClaimedRef(
        title="Quantum entanglement therapy for refractory migraine"))

    out = process_reference(ref, _fabrication, session=FakeSession(_route_all_errored))

    assert ref.log.noid_lookup_attempted is True
    # All confirmation searches errored -> we never actually looked.
    assert all(v is None for v in ref.log.db_hits.values())
    assert out.label == S.HUMAN_REVIEW
    assert out.label != S.F1
    assert out.log.decided_by == "confirm_all_errored"


# ==========================================================================
# 6. No PMID, cheap lookup misses but confirm FINDS the title -> HUMAN_REVIEW,
#    never F2 (there is no claimed PMID to call a "wrong reference"). NEVER F1.
# ==========================================================================
def test_no_pmid_confirm_found_is_human_review_not_f2(monkeypatch):
    # Cheap fuzzy lookup finds nothing (escalates), but the title-only
    # confirmation search lands a high match in PubMed.
    monkeypatch.setattr(confirm, "search_pubmed", lambda *a, **k: 97.0)
    monkeypatch.setattr(confirm, "search_crossref", lambda *a, **k: 0.0)
    monkeypatch.setattr(confirm, "search_openalex", lambda *a, **k: 0.0)

    ref = Reference("t6", "", ClaimedRef(
        title="Quantum entanglement therapy for refractory migraine"))

    out = process_reference(ref, _fabrication, session=FakeSession(_route_all_empty))

    assert ref.log.noid_lookup_attempted is True
    assert out.label == S.HUMAN_REVIEW
    assert out.label not in (S.F1, S.F2)               # F2 presupposes a PMID
    assert out.log.decided_by == "noid_confirm_found_human_review"


# ==========================================================================
# 7. Robustness: a null entry in a Crossref author array must not crash the
#    reference (the API can emit nulls). Degrades, never raises.
# ==========================================================================
def test_fuzzy_lookup_tolerates_null_author_entry():
    title = "Bibliometric drift in retracted oncology literature"
    malformed = {"status": "ok", "message": {"items": [
        {"title": [title], "author": [None, {"family": "Okafor"}],
         "issued": {"date-parts": [["2021"]]}},   # note: year as a string
    ]}}

    def handler(url, params, n):
        if "crossref" in url:
            return FakeResponse(200, json_data=malformed)
        return FakeResponse(200, json_data=_fx("openalex_empty.json"))

    rec = fuzzy_biblio_lookup(Reference("n", "", ClaimedRef(title=title)),
                              session=FakeSession(handler))
    assert rec.resolved is True
    assert rec.authors == ["Okafor"]                   # null skipped
    assert rec.year == 2021 and isinstance(rec.year, int)   # string coerced


# ==========================================================================
# Direct fuzzy_biblio_lookup unit checks (DB response-shape handling).
# ==========================================================================
def test_fuzzy_lookup_both_dbs_errored_returns_unresolved():
    # Network failure must NOT read as "found nothing": resolved stays False and
    # the caller escalates (it does not get short-circuited into a not-found F1).
    rec = fuzzy_biblio_lookup(
        Reference("e", "", ClaimedRef(title="Some title")),
        session=FakeSession(_route_all_errored))
    assert rec.resolved is False


def test_fuzzy_lookup_openalex_wins_when_crossref_empty():
    def handler(url, params, n):
        if "crossref" in url:
            return FakeResponse(200, json_data=_fx("crossref_empty.json"))
        if "openalex" in url:
            return FakeResponse(200, json_data=_fx("openalex_works.json"))
        return FakeResponse(404)

    title = ("Global burden of 369 diseases and injuries in 204 countries and "
             "territories, 1990-2019: a systematic analysis for the Global "
             "Burden of Disease Study 2019")
    rec = fuzzy_biblio_lookup(Reference("o", "", ClaimedRef(title=title)),
                              session=FakeSession(handler))
    assert rec.resolved is True
    assert rec.title.startswith("Global burden")     # OpenAlex display_name fallback
    assert rec.pmid == ""
