"""Offline tests for immutable F5 source packets and fact escalation."""
from __future__ import annotations

import hashlib
import json

import pytest

from .f5_evidence_store import (
    FACT_ASSESSMENT_NOT_PERFORMED,
    F5EvidenceStore,
    adapt_fulltext_sections,
    build_source_packet,
    source_packet_from_dict,
)
from .f5_contradiction_prompt import render_comparability_source
from .f5_seams import make_fetch_comparability_source
from .f5_supersession import (
    CandidateWork, ComparabilitySource, EvidenceTier, F5Policy, NoticeStatus,
    RetrievalResult, decide_f5,
)
from .judgment_engine import ClaimSupport, SupportState, TemporalState
from . import judgment_run as jr
from .f5_evidence_store import SOURCE_PACKET_SCHEMA_VERSION


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _metadata(work_id="111", abstract="Drug X reduced mortality."):
    return {
        "id": work_id,
        "title": f"Work {work_id}",
        "abstract": abstract,
        "pub_date": "2010-01-01",
        "pub_date_latest": "2010-01-01",
        "pub_date_precision": "day",
        "authors": ["Smith"],
        "authors_full": ["Smith Alice"],
        "mesh_terms": ["Mortality"],
        "mesh_major_terms": ["Mortality"],
        "publication_types": ["Randomized Controlled Trial"],
    }


def _fulltext(work_id="111", *, complete=True):
    methods = "Adults were randomized to Drug X or placebo."
    results = "Drug X did not reduce mortality at 30 days."
    return {
        "pmid": work_id,
        "pmcid": "PMC" + work_id,
        "resolved": True,
        "sections": [
            {"label": "methods", "title": "Trial design", "text": methods,
             "content_sha256": _hash(methods)},
            {"label": "results", "title": "Primary outcome", "text": results,
             "content_sha256": _hash(results)},
        ],
        "sections_present": ["methods", "results"],
        "retrieval_complete": complete,
        "incomplete_reasons": [] if complete else ["body_too_short"],
    }


def test_real_sections_shape_maps_and_preserves_provenance():
    adapted = adapt_fulltext_sections(_fulltext(), work_id="111")
    assert "[methods] Trial design" in adapted.methods
    assert "Adults were randomized" in adapted.methods
    assert "[results] Primary outcome" in adapted.results
    assert "did not reduce mortality" in adapted.results
    stored = adapted.provenance["sections"]
    assert [row["label"] for row in stored] == ["methods", "results"]
    assert stored[0]["content_sha256"] == _hash(stored[0]["text"])
    assert adapted.provenance["pmcid"] == "PMC111"
    assert adapted.source_status == "complete"


def test_nonstandard_sections_are_preserved_in_the_actual_judge_text():
    sentence = "30-day mortality was 18% with Drug X and 12% with placebo."
    body = _fulltext()
    body["sections"] = [{
        "label": "table", "title": "Table 2", "text": sentence,
        "content_sha256": _hash(sentence),
    }]
    body["sections_present"] = ["table"]
    adapted = adapt_fulltext_sections(body, work_id="111")
    source = ComparabilitySource(other_sections=adapted.other_sections)
    assert sentence in adapted.other_sections
    assert sentence in render_comparability_source(source)


def test_fulltext_identity_and_hash_mismatch_fail_closed():
    with pytest.raises(ValueError, match="does not match requested"):
        adapt_fulltext_sections(_fulltext("222"), work_id="111")
    corrupt = _fulltext()
    corrupt["sections"][0]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not match stored text"):
        adapt_fulltext_sections(corrupt, work_id="111")


def test_packet_hashes_exact_stored_text_and_is_source_isolated():
    a = build_source_packet(
        _metadata("111", "Drug X reduced mortality."),
        as_of_date="2024-01-01", retrieved_at="2024-01-01T00:00:00Z",
        evidence_tier="rct", evidence_tier_basis="publication_type")
    b = build_source_packet(
        _metadata("222", "Drug X did not reduce mortality."),
        as_of_date="2024-01-01", retrieved_at="2024-01-01T00:00:00Z",
        evidence_tier="rct", evidence_tier_basis="publication_type")
    assert a.source_hashes["abstract"] == _hash(a.abstract)
    assert b.source_hashes["abstract"] == _hash(b.abstract)
    assert a.packet_sha256 == a.compute_sha256()
    assert a.packet_sha256 != b.packet_sha256
    assert "did not reduce" not in a.abstract
    assert "did not reduce" in b.abstract
    assert source_packet_from_dict(a.to_dict()) == a
    tampered = a.to_dict()
    tampered["abstract"] = "Drug X did not reduce mortality."
    with pytest.raises(ValueError, match="source_hashes.abstract"):
        source_packet_from_dict(tampered)


def _store(*, abstract, fulltext, assessor):
    calls = {"fulltext": 0}

    def fetch_fulltext(_work_id):
        calls["fulltext"] += 1
        return fulltext

    store = F5EvidenceStore(
        fetch_metadata=lambda _wid: _metadata(abstract=abstract),
        fetch_abstract=lambda _wid: abstract,
        fetch_fulltext=fetch_fulltext,
        classify_evidence_tier=lambda _meta: "rct",
        assess_missing_facts=assessor,
        fact_assessor_version="fixture_v1",
        retrieved_at=lambda: "2024-01-01T00:00:00Z",
    )
    return store, calls


def test_long_but_factually_thin_abstract_escalates_by_missing_fact():
    abstract = "Background context. " * 40  # long is deliberately irrelevant

    def assessor(*, abstract, methods, results, **_kwargs):
        return [] if results and "30 days" in results else [
            "primary_outcome", "result_direction"]

    store, calls = _store(
        abstract=abstract, fulltext=_fulltext(), assessor=assessor)
    packet = store.get("111", as_of_date="2024-01-01")
    assert len(abstract) > 200
    assert calls["fulltext"] == 1
    assert packet.missing_facts == ()
    assert packet.results and "30 days" in packet.results


def test_short_sufficient_abstract_is_not_rejected_by_length():
    abstract = "Adults: Drug X reduced 30-day mortality versus placebo."

    def assessor(*, abstract, **_kwargs):
        return [] if "Adults" in abstract and "30-day" in abstract else [
            "population", "primary_outcome"]

    store, calls = _store(
        abstract=abstract, fulltext=_fulltext(), assessor=assessor)
    packet = store.get("111", as_of_date="2024-01-01")
    assert len(abstract) < 200
    assert calls["fulltext"] == 0
    assert packet.source_status == "complete"


def test_incomplete_transport_cannot_be_promoted_by_a_clear_fact_screen():
    store, _calls = _store(
        abstract="Background.", fulltext=_fulltext(complete=False),
        assessor=lambda **kwargs: [] if kwargs.get("results") else ["result"],
    )
    packet = store.get("111", as_of_date="2024-01-01")
    assert packet.results and "did not reduce" in packet.results
    assert packet.missing_facts == ()
    assert packet.fulltext["retrieval_complete"] is False
    assert packet.source_status == "partial"


def test_no_fact_assessor_fetches_richest_evidence_and_records_unknown():
    calls = []
    store = F5EvidenceStore(
        fetch_metadata=lambda _wid: _metadata(abstract="Background only."),
        fetch_abstract=lambda _wid: "Background only.",
        fetch_fulltext=lambda wid: calls.append(wid) or _fulltext(wid),
        classify_evidence_tier=lambda _meta: "rct",
        retrieved_at=lambda: "2024-01-01T00:00:00Z",
    )
    packet = store.get("111", as_of_date="2024-01-01")
    assert calls == ["111"]
    assert packet.results and "did not reduce" in packet.results
    assert packet.source_status == "partial"
    assert packet.missing_facts == (FACT_ASSESSMENT_NOT_PERFORMED,)


def test_missing_fulltext_keeps_required_facts_partial_and_retries():
    def assessor(**_kwargs):
        return ["population", "primary_outcome"]

    store, calls = _store(
        abstract="Drug X was studied.", fulltext=None, assessor=assessor)
    first = store.get("111", as_of_date="2024-01-01")
    second = store.get("111", as_of_date="2024-01-01")
    assert first.source_status == second.source_status == "partial"
    assert first.missing_facts == ("population", "primary_outcome")
    assert calls["fulltext"] == 2  # a transport failure is not a success-cache hit
    assert store.counters["fulltext_failures"] == 2
    assert store.counters["cache_hits"] == 0


def test_changed_content_creates_new_packet_not_old_cache_hit():
    current = {"abstract": "Drug X reduced mortality."}
    store = F5EvidenceStore(
        fetch_metadata=lambda _wid: _metadata(abstract=current["abstract"]),
        fetch_abstract=lambda _wid: current["abstract"],
        classify_evidence_tier=lambda _meta: "rct",
        assess_missing_facts=lambda **_kwargs: [],
        fact_assessor_version="fixture_v1",
        retrieved_at=lambda: "2024-01-01T00:00:00Z",
    )
    first = store.get("111", as_of_date="2024-01-01")
    again = store.get("111", as_of_date="2024-01-01")
    assert again.packet_sha256 == first.packet_sha256
    assert store.counters["cache_hits"] == 1
    current["abstract"] = "Drug X did not reduce mortality."
    changed = store.get("111", as_of_date="2024-01-01")
    assert changed.packet_sha256 != first.packet_sha256
    assert "did not reduce" in changed.abstract


def test_live_source_seam_emits_distinct_bound_packets_without_length_rule():
    metadata = {
        "111": _metadata("111", "Background. " * 30),
        "222": _metadata("222", "Candidate background. " * 20),
    }
    fulltexts = {"111": _fulltext("111"), "222": _fulltext("222")}
    calls = []

    def missing(*, results, **_kwargs):
        return [] if results else ["primary_outcome", "result_direction"]

    fetch = make_fetch_comparability_source(
        lambda wid: metadata[wid]["abstract"],
        lambda wid: calls.append(wid) or fulltexts[wid],
        fetch_meta=lambda wid: metadata[wid],
        assess_missing_facts=missing,
        fact_assessor_version="fixture_v1",
        retrieved_at=lambda: "2024-01-01T00:00:00Z")
    cited = fetch("111", as_of_date="2024-01-01")
    candidate = fetch("222", as_of_date="2024-01-01")
    assert calls == ["111", "222"]
    assert cited.work_id == "111" and candidate.work_id == "222"
    assert cited.packet_sha256 != candidate.packet_sha256
    assert cited.source_status == candidate.source_status == "complete"
    assert len(fetch.source_packet_log) == 2
    assert {row["work_id"] for row in fetch.source_packet_log} == {"111", "222"}


def test_required_fact_missing_stops_deep_judge_and_blocks_clean_negative():
    calls = {"judge": 0}
    cited = ComparabilitySource(
        abstract="Drug X reduced mortality.", work_id="111")
    incomplete = ComparabilitySource(
        abstract="Drug X was studied.", work_id="222", source_status="partial",
        missing_facts=("primary_outcome",))

    def retrieve(_meta, _claim, *, after_date, as_of_date):
            return RetrievalResult(
                (CandidateWork("222", pub_date="2020-01-01", authors=("Jones",),
                               tier_hint="rct", registry_ids=("NCT222",),
                               demonstrably_distinct_from=("111",)),),
                "adequate", "ok")

    def fetch(wid, *, as_of_date):
        return cited if wid == "111" else incomplete

    def judge(*_args):
        calls["judge"] += 1
        raise AssertionError("incomplete packet reached deep comparison")

    temporal, records = decide_f5(
        ("Drug X reduced mortality",),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        {"cited_work_id": "111", "cited_meta": {
            "authors": ["Smith"], "cited_tier": "rct",
            "registry_ids": ["NCT111"]},
         "cited_date": "2010-01-01", "as_of_date": "2024-01-01"},
        retrieve_superseding_candidates=retrieve,
        fetch_comparability_source=fetch,
        check_formal_notice=lambda _wid, **_kw: NoticeStatus(
            lookup_status="ok", source_role="no_notice_type"),
        classify_evidence_tier=lambda meta: EvidenceTier(
            meta.get("tier_hint") or meta.get("cited_tier", "rct")),
        find_supersession_attestation=lambda *_a, **_kw: None,
        judge_contradiction=judge, policy=F5Policy())
    assert calls["judge"] == 0
    assert temporal.state is TemporalState.UNJUDGEABLE
    candidate_row = records[0]["candidate_assessments"][0]
    assert candidate_row["reason"] == "candidate_source_incomplete"
    assert candidate_row["candidate_source_missing_facts"] == ["primary_outcome"]


@pytest.mark.parametrize(
    ("directional", "expected_state"),
    [(False, TemporalState.UNJUDGEABLE),
     (True, TemporalState.QUALIFYING_CONTRADICTION)],
)
def test_unscreened_rich_evidence_can_prove_positive_but_never_clean_negative(
        directional, expected_state):
    calls = {"judge": 0}
    cited_span = "Drug X reduced mortality."
    candidate_span = "Drug X did not reduce mortality."
    cited = ComparabilitySource(
        abstract=cited_span, work_id="111", source_status="partial",
        missing_facts=(FACT_ASSESSMENT_NOT_PERFORMED,))
    candidate = ComparabilitySource(
        results=candidate_span, work_id="222", source_status="partial",
        missing_facts=(FACT_ASSESSMENT_NOT_PERFORMED,))

    def retrieve(_meta, _claim, *, after_date, as_of_date):
        return RetrievalResult(
            (CandidateWork("222", pub_date="2020-01-01", authors=("Jones",),
                           tier_hint="rct", registry_ids=("NCT222",),
                           demonstrably_distinct_from=("111",)),),
            "adequate", "ok")

    def judge(*_args):
        calls["judge"] += 1
        return json.dumps({
                "directional_contradiction": directional,
                "relation_to_cited_finding": (
                    "opposes" if directional else "uncertain"),
            "claim_match": "match", "outcome_relation": "same",
            "population_relation": "equivalent",
            "cited_direction": "decrease",
            "candidate_direction": "no_effect",
            "magnitude": "directional reversal",
            "cited_finding_span": cited_span,
            "candidate_contradiction_span": candidate_span,
            "confidence": 0.9, "scope_mismatch_axis": "none",
        })

    temporal, records = decide_f5(
        ("Drug X reduced mortality",),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        {"cited_work_id": "111", "cited_meta": {
            "authors": ["Smith"], "cited_tier": "rct",
            "registry_ids": ["NCT111"]},
         "cited_date": "2010-01-01", "as_of_date": "2024-01-01"},
        retrieve_superseding_candidates=retrieve,
        fetch_comparability_source=lambda wid, **_kw: (
            cited if wid == "111" else candidate),
        check_formal_notice=lambda _wid, **_kw: NoticeStatus(
            lookup_status="ok", source_role="no_notice_type"),
        classify_evidence_tier=lambda meta: EvidenceTier(
            meta.get("tier_hint") or meta.get("cited_tier", "rct")),
        find_supersession_attestation=lambda *_a, **_kw: None,
        judge_contradiction=judge, policy=F5Policy())
    assert calls["judge"] == 1
    assert temporal.state is expected_state
    if directional:
        assert records[0]["candidate_assessments"][0]["reason"] == \
            "qualifying_contradiction"
    else:
        assert records[0]["candidate_assessments"][0]["reason"] == \
            "relation_uncertain"


def test_source_work_id_cross_contamination_is_rejected_before_judge():
    calls = {"judge": 0}

    def retrieve(_meta, _claim, *, after_date, as_of_date):
        return RetrievalResult(
            (CandidateWork("222", pub_date="2020-01-01", authors=("Jones",),
                           tier_hint="rct"),), "adequate", "ok")

    def fetch(wid, *, as_of_date):
        if wid == "111":
            return ComparabilitySource(abstract="Cited result", work_id="111")
        return ComparabilitySource(
            abstract="Text copied from a different candidate", work_id="333")

    with pytest.raises(ValueError, match="does not match candidate"):
        decide_f5(
            ("Drug X reduced mortality",),
            (ClaimSupport(0, SupportState.SUPPORTED),),
            {"cited_work_id": "111", "cited_meta": {
                "authors": ["Smith"], "cited_tier": "rct"},
             "cited_date": "2010-01-01", "as_of_date": "2024-01-01"},
            retrieve_superseding_candidates=retrieve,
            fetch_comparability_source=fetch,
            check_formal_notice=lambda _wid, **_kw: NoticeStatus(
                lookup_status="ok", source_role="no_notice_type"),
            classify_evidence_tier=lambda meta: EvidenceTier(
                meta.get("tier_hint") or meta.get("cited_tier", "rct")),
            find_supersession_attestation=lambda *_a, **_kw: None,
            judge_contradiction=lambda *_a: calls.__setitem__("judge", 1),
            policy=F5Policy())
    assert calls["judge"] == 0


def test_historical_cutoff_uncertainty_is_named_not_backfilled():
    packet = build_source_packet(
        _metadata(), as_of_date="2020-01-01",
        retrieved_at="2024-01-01T00:00:00Z",
        evidence_tier="rct", evidence_tier_basis="publication_type")
    assert packet.source_status == "partial"
    assert "historical_content_as_of_cutoff_unverified" in packet.missing_facts


def test_manifest_tallies_source_packets_by_bound_hash_and_role():
    records = [{
        "cited_source_packet_sha256": "a" * 64,
        "cited_source_status": "complete",
        "candidate_assessments": [
            {"candidate_source_packet_sha256": "b" * 64,
             "candidate_source_status": "partial"},
            {"candidate_source_packet_sha256": "b" * 64,
             "candidate_source_status": "partial"},
        ],
    }]
    block = jr._f5_manifest_block(
        None, records, {"retrieval_calls": 0, "attestation_calls": 0,
                        "judge_calls": 0, "retrieval_protocols": []})
    assert block["source_packet_schema_version"] == SOURCE_PACKET_SCHEMA_VERSION
    assert block["source_packet_count"] == 2
    assert block["source_packet_hashes"] == ["a" * 64, "b" * 64]
    assert block["source_status_counts"] == {
        "candidate:partial": 2, "cited:complete": 1}
