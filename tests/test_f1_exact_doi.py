"""Exact-DOI F1/F2 scope expansion; all provider traffic is synthetic."""
from __future__ import annotations

import threading

import pytest

from cde.runtime import ratelimit
from cde.refs.confirm import confirm
from cde.refs.doi_lookup import (DOI_ANSWERED_ABSENT, DOI_CONFLICT, DOI_FOUND,
                               DOI_FOUND_NO_METADATA, DOI_INCOMPLETE,
                               lookup_exact_doi)
from cde.refs.lookup import fuzzy_biblio_lookup
from cde.refs.run import process_reference
from cde.refs.schema import CLEARED, F1, F2, HUMAN_REVIEW, ClaimedRef, Reference


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.headers = {}
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class Session:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self._lock = threading.Lock()

    def get(self, url, params=None, timeout=None):
        with self._lock:
            self.calls.append((url, dict(params or {})))
        return self.handler(url, params or {})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ratelimit.time, "sleep", lambda *_a, **_k: None)


def _ref(title="Imaginary effects of moonlight on DNA", doi="10.9999/exact.fake"):
    return Reference(
        "paper:ref1", "Claim [1].",
        ClaimedRef(title=title, authors=["Example"], year=2025,
                   journal="Journal of Imaginary Results", claimed_doi=doi))


def _crossref_record(doi, title, author="Example", year=2025,
                     journal="Journal of Imaginary Results"):
    return {"status": "ok", "message": {
        "DOI": doi, "title": [title], "author": [{"family": author}],
        "issued": {"date-parts": [[year]]}, "container-title": [journal]}}


def _empty_routes(*, crossref_exact_status=404, crossref_title_items=None):
    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(payload={"responseCode": 100})
        if "api.crossref.org/works/" in url:
            return Response(crossref_exact_status)
        if "api.datacite.org/dois/" in url:
            return Response(404)
        if "api.openalex.org/works" in url and str(params.get("filter", "")).startswith("doi:"):
            return Response(payload={"results": []})
        if "esearch.fcgi" in url:
            return Response(payload={"esearchresult": {"idlist": []}})
        if "api.crossref.org/works" in url:
            return Response(payload={"status": "ok", "message": {
                "items": list(crossref_title_items or [])}})
        if "api.openalex.org/works" in url:
            return Response(payload={"results": []})
        raise AssertionError((url, params))
    return handler


def _llm_must_not_run(_prompt):
    raise AssertionError("exact DOI existence does not require an LLM verdict")


def test_exact_doi_absent_and_complete_title_sweep_emits_f1():
    ref = _ref()
    out = process_reference(ref, _llm_must_not_run,
                            session=Session(_empty_routes()))
    assert out.label == F1
    assert out.log.doi_lookup_status == DOI_ANSWERED_ABSENT
    assert out.log.decided_by == "exact_doi_absent_confirm_not_found_f1"
    assert out.log.db_hits == {"pubmed": 0.0, "crossref": 0.0,
                               "openalex": 0.0}


def test_one_doi_provider_outage_holds_even_when_title_sweep_is_empty():
    ref = _ref()
    out = process_reference(
        ref, _llm_must_not_run,
        session=Session(_empty_routes(crossref_exact_status=503)))
    assert out.label == HUMAN_REVIEW
    assert out.log.doi_lookup_status == DOI_INCOMPLETE
    assert out.log.decided_by == "exact_doi_incomplete_hold"


def test_exact_doi_metadata_match_clears_without_title_search():
    ref = _ref(title="Real effects of moonlight on DNA", doi="10.1000/real")

    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(payload={"responseCode": 1})
        if "api.crossref.org/works/" in url:
            return Response(payload=_crossref_record(
                "10.1000/real", "Real effects of moonlight on DNA"))
        if "api.datacite.org/dois/" in url:
            return Response(404)
        if "api.openalex.org/works" in url:
            return Response(payload={"results": []})
        raise AssertionError((url, params))

    out = process_reference(ref, _llm_must_not_run, session=Session(handler))
    assert out.label == CLEARED
    assert out.log.doi_lookup_status == DOI_FOUND
    assert out.log.doi_metadata_source == "crossref"
    assert out.log.doi_match is True


def test_exact_doi_resolves_to_different_metadata_emits_f2():
    ref = _ref(doi="10.1000/other")

    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(payload={"responseCode": 1})
        if "api.crossref.org/works/" in url:
            return Response(payload=_crossref_record(
                "10.1000/other", "A completely different real paper",
                author="Other", year=2010, journal="Real Journal"))
        if "api.datacite.org/dois/" in url:
            return Response(404)
        if "api.openalex.org/works" in url:
            return Response(payload={"results": []})
        raise AssertionError((url, params))

    out = process_reference(ref, _llm_must_not_run, session=Session(handler))
    assert out.label == F2
    assert out.log.decided_by == "exact_doi_metadata_mismatch_f2"


def test_absent_exact_doi_but_title_exists_routes_f2_not_f1():
    ref = _ref(doi="10.9999/not.registered")
    item = _crossref_record(
        "10.1000/real-work", ref.claimed.title)["message"]
    out = process_reference(
        ref, _llm_must_not_run,
        session=Session(_empty_routes(crossref_title_items=[item])))
    assert out.label == F2
    assert out.log.decided_by == "exact_doi_absent_title_found_f2"


def test_exact_endpoint_absence_conflicting_with_same_doi_search_hit_holds():
    ref = _ref(doi="10.9999/not.registered")
    item = _crossref_record(
        ref.claimed.claimed_doi, ref.claimed.title)["message"]
    out = process_reference(
        ref, _llm_must_not_run,
        session=Session(_empty_routes(crossref_title_items=[item])))
    assert out.label == HUMAN_REVIEW
    assert out.log.doi_lookup_status == DOI_CONFLICT
    assert out.log.decided_by == "exact_doi_incomplete_hold"


def test_exact_doi_providers_overlap_and_result_order_is_deterministic():
    barrier = threading.Barrier(4, timeout=2)

    def handler(url, params):
        if ("api.crossref.org/works/" in url
                or "api.datacite.org/dois/" in url
                or "doi.org/api/handles/" in url
                or str(params.get("filter", "")).startswith("doi:")):
            barrier.wait()
        if "doi.org/api/handles/" in url:
            return Response(payload={"responseCode": 100})
        if "api.crossref.org/works/" in url:
            return Response(404)
        if "api.datacite.org/dois/" in url:
            return Response(404)
        return Response(payload={"results": []})

    result = lookup_exact_doi("https://doi.org/10.9999/Exact.Fake",
                              s=Session(handler))
    assert result.status == DOI_ANSWERED_ABSENT
    assert result.normalized_doi == "10.9999/exact.fake"
    assert list(result.providers) == ["doi_proxy", "crossref", "datacite",
                                      "openalex"]


def test_doi_system_positive_without_metadata_holds_not_f1():
    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(payload={"responseCode": 1})
        if "api.crossref.org/works/" in url or "api.datacite.org/dois/" in url:
            return Response(404)
        if "api.crossref.org/works" in url:
            return Response(payload={"status": "ok", "message": {"items": []}})
        if "api.openalex.org/works" in url:
            return Response(payload={"results": []})
        if "esearch.fcgi" in url:
            return Response(payload={"esearchresult": {"idlist": []}})
        raise AssertionError((url, params))

    ref = _ref()
    out = process_reference(ref, _llm_must_not_run, session=Session(handler))
    assert out.label == HUMAN_REVIEW
    assert out.log.doi_lookup_status == DOI_FOUND_NO_METADATA
    assert out.log.decided_by == "exact_doi_found_metadata_unavailable_hold"


def test_doi_system_and_metadata_provider_conflict_holds():
    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(payload={"responseCode": 100})
        if "api.crossref.org/works/" in url:
            return Response(payload=_crossref_record(
                "10.9999/exact.fake", "A registry record"))
        if "api.datacite.org/dois/" in url:
            return Response(404)
        return Response(payload={"results": []})

    result = lookup_exact_doi("10.9999/exact.fake", s=Session(handler))
    assert result.status == DOI_CONFLICT


def test_title_confirmation_providers_overlap():
    barrier = threading.Barrier(3, timeout=2)

    def handler(url, _params):
        barrier.wait()
        if "esearch.fcgi" in url:
            return Response(payload={"esearchresult": {"idlist": []}})
        if "crossref" in url:
            return Response(payload={"status": "ok", "message": {"items": []}})
        return Response(payload={"results": []})

    ref = _ref()
    hits = confirm(ref, s=Session(handler))
    assert hits == {"pubmed": 0.0, "crossref": 0.0, "openalex": 0.0}


def test_noid_candidate_providers_overlap():
    barrier = threading.Barrier(2, timeout=2)

    def handler(url, _params):
        barrier.wait()
        if "crossref" in url:
            return Response(payload={"status": "ok", "message": {"items": []}})
        return Response(payload={"results": []})

    rec = fuzzy_biblio_lookup(_ref(doi=""), session=Session(handler))
    assert rec.resolved is False


# --- the Handle API's real negative is a 404, not a 200 --------------------
# Every fixture above answers a missing handle with HTTP 200 carrying
# ``responseCode: 100``. The live service answers **404** carrying that body
# (verified against https://doi.org/api/handles/10.1109/access.2023.3124567).
# Reading it through the 200-gated ``_json`` helper turned the DOI resolver's
# authoritative "absent" into PROVIDER_ERROR, which left the negative sweep
# incomplete and made DOI_ANSWERED_ABSENT -- and therefore the exact-DOI F1
# route -- unreachable for every DOI that does not exist. These two pin the
# status code so the fixture cannot drift back.

def test_handle_api_404_body_is_an_answer_not_an_outage():
    from cde.refs.doi_lookup import PROVIDER_ABSENT, _doi_proxy
    session = Session(lambda url, params: Response(
        404, {"responseCode": 100, "handle": "10.9999/exact.fake"}))
    status, record = _doi_proxy("10.9999/exact.fake", session)
    assert status == PROVIDER_ABSENT
    assert record is None


def test_handle_api_404_with_no_body_stays_an_outage():
    # A 404 that carries no parseable Handle response says nothing about the
    # handle, so it must NOT be read as absence.
    from cde.refs.doi_lookup import PROVIDER_ERROR, _doi_proxy
    session = Session(lambda url, params: Response(404))
    status, _ = _doi_proxy("10.9999/exact.fake", session)
    assert status == PROVIDER_ERROR


def test_exact_doi_absent_sweep_completes_when_the_resolver_404s():
    # End-to-end: with the resolver answering its real 404 and the other three
    # providers absent, the sweep is COMPLETE and reports answered-absent.
    base = _empty_routes()

    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(404, {"responseCode": 100})   # the real status
        return base(url, params)

    result = lookup_exact_doi("10.9999/exact.fake", s=Session(handler))
    assert result.status == DOI_ANSWERED_ABSENT
    assert result.providers["doi_proxy"] == "absent"


def test_exact_doi_f1_is_reachable_when_the_resolver_404s():
    # The same 404, end to end through the engine: a reference whose printed DOI
    # does not exist and whose title finds nothing anywhere reaches F1. Before
    # the Handle-API status fix this returned human_review on a phantom outage.
    base = _empty_routes()

    def handler(url, params):
        if "doi.org/api/handles/" in url:
            return Response(404, {"responseCode": 100})
        return base(url, params)

    out = process_reference(_ref(), _llm_must_not_run, session=Session(handler))
    assert out.label == F1
    assert out.log.doi_lookup_status == DOI_ANSWERED_ABSENT
    assert out.log.decided_by == "exact_doi_absent_confirm_not_found_f1"
