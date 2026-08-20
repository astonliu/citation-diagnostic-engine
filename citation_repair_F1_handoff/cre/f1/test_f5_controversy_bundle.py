"""Offline receiver-bundle tests using minimized assessment-shaped rows."""
from __future__ import annotations

import json

import pytest

from .f5_controversy_bundle import (
    CONTROVERSY_BUNDLE_SCHEMA_VERSION,
    FIELD_CONFIDENCE,
    build_controversy_bundle,
    controversy_bundle_sha256,
    validate_controversy_bundle,
)


def _candidate(work_id, *, relation, reason, cluster, independent="independent",
               notice_kind="none", notice_resolution="resolved_clear",
               lookup_status="ok", disposition="surface", confidence=0.8):
    return {
        "candidate_work_id": work_id,
        "candidate_date": "2022-01-01",
        "candidate_tier": "rct",
        "candidate_notice_kind": notice_kind,
        "candidate_notice_resolution": notice_resolution,
        "candidate_notice_lookup_status": lookup_status,
        "relation_to_cited_finding": relation,
        "directional_contradiction": reason == "qualifying_contradiction",
        "comparability_decision": "comparable",
        "reason": reason,
        "independent": independent,
        "candidate_study_cluster_id": cluster,
        "cited_study_cluster_id": "CITED",
        "confidence": confidence,
        "cited_finding_span": "Earlier finding.",
        "candidate_contradiction_span": f"Evidence from {work_id}.",
        "candidate_source_status": "complete",
        "candidate_source_packet_sha256": work_id.lower() * 8,
        "discovery_disposition": disposition,
    }


def _record():
    return {
        "claim_index": 0,
        "claim_text": "Drug X reduces mortality",
        "activation": {"applicability": "eligible"},
        "cited_work_id": "111",
        "cited_date": "2010-01-01",
        "as_of_date": "2024-01-01",
        "cited_tier": "rct",
        "cited_notice_kind": "none",
        "cited_notice_resolution": "resolved_clear",
        "cited_source_status": "complete",
        "cited_source_packet_sha256": "a" * 64,
        "cited_study_cluster_id": "CITED",
        "retrieval_status": "ok",
        "retrieval_adequacy": "adequate",
        "retrieval_query_hash": "q" * 64,
        "temporal_state": "QUALIFYING_CONTRADICTION",
        "candidate_assessments": [
            _candidate("OPP1", relation="opposes",
                       reason="qualifying_contradiction", cluster="OPP"),
            _candidate("SAME", relation="opposes", reason="not_independent",
                       cluster="CITED", independent="not_independent",
                       disposition="do_not_surface"),
            _candidate("CONF", relation="confirms",
                       reason="comparable_confirmation", cluster="CONF"),
            _candidate("MIX", relation="mixed",
                       reason="mixed_finding", cluster="MIX"),
            _candidate(
                "FLAG", relation=None, reason="candidate_flagged_notice",
                cluster="FLAG", independent=None, notice_kind="correction",
                notice_resolution="flagged", disposition="unassessable",
                confidence=None),
            _candidate("NEUT", relation="neutral",
                       reason="not_directional_contradiction", cluster="NEUT",
                       disposition="do_not_surface"),
        ],
        "selected_contradiction_work_id": "OPP1",
        "selected_replacement_work_id": None,
    }


def test_bundle_keeps_every_candidate_and_separates_papers_from_clusters():
    bundle = build_controversy_bundle(_record(), citation_id="citation-1")
    assert bundle["schema_version"] == CONTROVERSY_BUNDLE_SCHEMA_VERSION
    assert [row["candidate_work_id"] for row in
            bundle["qualifying_contradictions"]] == ["OPP1"]
    assert [row["candidate_work_id"] for row in
            bundle["same_study_context"]] == ["SAME"]
    assert [row["candidate_work_id"] for row in
            bundle["comparable_confirmations"]] == ["CONF"]
    assert [row["candidate_work_id"] for row in bundle["mixed_findings"]] == [
        "MIX"]
    assert [row["candidate_work_id"] for row in
            bundle["flagged_or_corrected"]] == ["FLAG"]
    assert [row["candidate_work_id"] for row in
            bundle["excluded_candidates"]] == ["NEUT"]
    profile = bundle["evidence_profile"]
    assert profile["opposing_paper_count"] == 1
    assert profile["confirming_paper_count"] == 2
    assert profile["independent_opposing_cluster_count"] == 1
    assert profile["independent_confirming_cluster_count"] == 2
    assert profile["evidence_pattern"] == "mixed_evidence"
    assert bundle["field_confidence"] == FIELD_CONFIDENCE


def test_bundle_hash_covers_the_exact_canonical_bundle():
    bundle = build_controversy_bundle(_record())
    assert bundle["bundle_sha256"] == controversy_bundle_sha256(bundle)
    changed = dict(bundle)
    changed["search_complete"] = False
    assert controversy_bundle_sha256(changed) != bundle["bundle_sha256"]


def test_strict_bundle_validation_rejects_gutted_or_missing_candidate_rows():
    record = _record()
    bundle = build_controversy_bundle(record)
    validate_controversy_bundle(
        bundle, candidate_assessments=record["candidate_assessments"],
        record=record)

    gutted = {
        "schema_version": bundle["schema_version"],
        "citation_id": bundle["citation_id"],
        "evidence_profile": {}, "search_complete": False,
    }
    gutted["bundle_sha256"] = controversy_bundle_sha256(gutted)
    with pytest.raises(ValueError, match="incomplete or unexpected"):
        validate_controversy_bundle(
            gutted, candidate_assessments=record["candidate_assessments"])

    missing = json.loads(json.dumps(bundle))
    for key in (
            "qualifying_contradictions", "plausible_or_uncertain",
            "comparable_confirmations", "mixed_findings",
            "flagged_or_corrected", "same_study_context",
            "excluded_candidates"):
        missing[key] = []
    missing["bundle_sha256"] = controversy_bundle_sha256(missing)
    with pytest.raises(ValueError, match="omits a retained candidate"):
        validate_controversy_bundle(
            missing, candidate_assessments=record["candidate_assessments"])

    wrong_count = json.loads(json.dumps(bundle))
    wrong_count["evidence_profile"]["opposing_paper_count"] = 999
    wrong_count["bundle_sha256"] = controversy_bundle_sha256(wrong_count)
    with pytest.raises(ValueError, match="evidence profile drifted"):
        validate_controversy_bundle(
            wrong_count, candidate_assessments=record["candidate_assessments"],
            record=record)

    wrong_category = json.loads(json.dumps(bundle))
    row = wrong_category["qualifying_contradictions"].pop()
    wrong_category["excluded_candidates"].append(row)
    wrong_category["bundle_sha256"] = controversy_bundle_sha256(wrong_category)
    with pytest.raises(ValueError, match="category membership drifted"):
        validate_controversy_bundle(
            wrong_category, candidate_assessments=record["candidate_assessments"],
            record=record)

    wrong_claim = json.loads(json.dumps(bundle))
    wrong_claim["claim_text"] = "Different claim"
    wrong_claim["cited_work"]["work_id"] = "999"
    wrong_claim["bundle_sha256"] = controversy_bundle_sha256(wrong_claim)
    with pytest.raises(ValueError, match="claim/cited binding drifted"):
        validate_controversy_bundle(
            wrong_claim, candidate_assessments=record["candidate_assessments"],
            record=record)


def test_incomplete_search_with_no_evidence_is_unassessable_not_no_controversy():
    record = _record()
    record["candidate_assessments"] = []
    record["retrieval_status"] = "partial"
    record["retrieval_adequacy"] = "empty"
    record["temporal_state"] = "UNJUDGEABLE"
    bundle = build_controversy_bundle(record)
    assert bundle["search_complete"] is False
    assert bundle["evidence_profile"]["evidence_pattern"] == "unassessable"


def test_same_study_semantics_remain_visible_without_becoming_extra_votes():
    record = _record()
    record["candidate_assessments"] = [
        _candidate(
            "SAME-MIX", relation="mixed", reason="mixed_finding",
            cluster="CITED", independent="not_independent"),
        _candidate(
            "SAME-CONF", relation="confirms", reason="comparable_confirmation",
            cluster="CITED", independent="not_independent"),
    ]
    bundle = build_controversy_bundle(record)
    assert [row["candidate_work_id"] for row in
            bundle["same_study_context"]] == ["SAME-MIX", "SAME-CONF"]
    assert [row["candidate_work_id"] for row in bundle["mixed_findings"]] == [
        "SAME-MIX"]
    assert [row["candidate_work_id"] for row in
            bundle["comparable_confirmations"]] == ["SAME-CONF"]
    assert bundle["evidence_profile"]["mixed_paper_count"] == 1
    assert bundle["evidence_profile"]["confirming_paper_count"] == 2
    assert bundle["evidence_profile"]["independent_confirming_cluster_count"] == 1
    assert bundle["evidence_profile"]["evidence_pattern"] == "mixed_evidence"
