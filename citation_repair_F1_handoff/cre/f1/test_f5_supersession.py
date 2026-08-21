"""Offline acceptance tests for the F5 (stale / superseded) supersession detector.

No network, no model: every retrieval / notice / tier / attestation / contradiction
seam is an injected fake. Covers the spec acceptance matrix (blueprint Sec 7, the
F5_SUPERSESSION_SPEC matrix) plus the exhaustive edge cases (Sec 9): the
detection contract, the discovery-vs-deployment rollup, the retrieval-adequacy
rule for confident-negative-vs-held, the deterministic Sec 18a.6 comparability
combination, authorship/cohort independence with the fail-closed open combinator,
the hypothetical (never-deployed) Path-A gate, the cited/candidate formal-notice
handling, verbatim span verification, deterministic multi-candidate selection,
fail-closed strict-JSON parsing, the frozen-engine mapping through
``decide_judgment``, and the ``validate_f5_record`` replay guard.
"""
from __future__ import annotations

import json

import pytest

from . import judgment_run as jr
from .f5_candidate_screen import CandidateScreenBatch, CandidateScreenDecision
from .f5_evidence_store import build_source_packet
from .f5_supersession import (
    Attestation,
    CandidateWork,
    ComparabilitySource,
    ContradictionJudgment,
    EvidenceTier,
    F5Policy,
    NoticeStatus,
    RetrievalResult,
    decide_f5,
    derive_comparability_decision,
    make_temporal_assessor,
    record_sha256,
    validate_f5_policy,
    validate_f5_record,
)
from .judgment_engine import (
    ClaimSupport,
    DecisionStatus,
    DiscriminatorContractError,
    ProvenanceAssessment,
    ProvenanceState,
    SupportState,
    TemporalState,
    decide_judgment,
)

CLAIM = "Drug X prevents disease Y in adults"
CLAIMS = (CLAIM,)

CITED_SRC = ComparabilitySource(
    abstract="Drug X reduced disease Y risk in adults in a randomized trial.")
CAND_SRC = ComparabilitySource(
    abstract="In a larger randomized trial, Drug X did NOT reduce disease Y and "
    "increased harm in adults.")
CITED_FINDING_SPAN = "Drug X reduced disease Y risk in adults"
CAND_CONTRA_SPAN = "Drug X did NOT reduce disease Y"


def _formal_packet(work_id, date, abstract):
    return build_source_packet(
        {"id": work_id, "title": f"Work {work_id}", "pub_date": date,
         "pub_date_latest": date, "pub_date_precision": "day",
         "authors": ["Author"],
         "publication_types": ["Randomized Controlled Trial"]},
        as_of_date="2024-01-01",
        retrieved_at="2024-01-01T00:00:00+00:00",
        evidence_tier="rct", evidence_tier_basis="test",
        abstract=abstract, historical_content_verified=True)


FORMAL_CITED_PACKET = _formal_packet("111", "2010-01-01", CITED_SRC.abstract)
FORMAL_CAND_PACKET = _formal_packet("222", "2020-01-01", CAND_SRC.abstract)
FORMAL_CITED_SRC = ComparabilitySource(
    abstract=FORMAL_CITED_PACKET.abstract,
    packet_sha256=FORMAL_CITED_PACKET.packet_sha256)
FORMAL_CAND_SRC = ComparabilitySource(
    abstract=FORMAL_CAND_PACKET.abstract,
    packet_sha256=FORMAL_CAND_PACKET.packet_sha256)
FORMAL_PACKET_MAP = {
    FORMAL_CITED_PACKET.packet_sha256: FORMAL_CITED_PACKET.to_dict(),
    FORMAL_CAND_PACKET.packet_sha256: FORMAL_CAND_PACKET.to_dict(),
}

EVIDENCE = {
    "cited_work_id": "W1",
    "cited_meta": {
        "id": "W1", "authors": ["Smith"], "cited_tier": "rct",
        "registry_ids": ["NCT-W1"],
    },
    "cited_date": "2010-01-01",
    "as_of_date": "2024-01-01",
}


# --------------------------------------------------------------------------
# Builders.
# --------------------------------------------------------------------------
def contradiction_json(
    *,
    directional_contradiction=True,
    relation_to_cited_finding=None,
    claim_match="match",
    outcome_relation="same",
    population_relation="equivalent",
    cited_direction="decrease",
    candidate_direction="no_effect",
    magnitude="directional reversal",
    cited_finding_span=CITED_FINDING_SPAN,
    candidate_contradiction_span=CAND_CONTRA_SPAN,
    confidence=0.9,
    # Eleventh key (2026-08-12). Defaults to "none" so every existing fixture keeps
    # asserting what it was written to assert: the axis is RECORDED, never routed
    # on, so adding it must not move any decision these tests pin.
    scope_mismatch_axis="none",
):
    if relation_to_cited_finding is None:
        relation_to_cited_finding = (
            "opposes" if directional_contradiction else "neutral")
    return json.dumps({
        "directional_contradiction": directional_contradiction,
        "relation_to_cited_finding": relation_to_cited_finding,
        "claim_match": claim_match,
        "outcome_relation": outcome_relation,
        "population_relation": population_relation,
        "cited_direction": cited_direction,
        "candidate_direction": candidate_direction,
        "magnitude": magnitude,
        "cited_finding_span": cited_finding_span,
        "candidate_contradiction_span": candidate_contradiction_span,
        "confidence": confidence,
        "scope_mismatch_axis": scope_mismatch_axis,
    })


def verifier_json(**overrides):
    payload = {
        "same_claim_or_outcome": True,
        "comparable_population": True,
        "opposite_directions": True,
        "cited_span_supports_claim": True,
        "candidate_span_contradicts_claim": True,
        "rationale": "independently confirmed",
    }
    payload.update(overrides)
    return json.dumps(payload)


def candidate(work_id="W2", *, pub_date="2020-01-01", authors=("Jones",),
              tier_hint="rct", registry_ids=None,
              demonstrably_distinct_from=None):
    if registry_ids is None:
        registry_ids = (f"NCT-{work_id}",)
    if demonstrably_distinct_from is None:
        demonstrably_distinct_from = ("W1",)
    return CandidateWork(id=work_id, pub_date=pub_date, authors=tuple(authors),
                         tier_hint=tier_hint, registry_ids=tuple(registry_ids),
                         demonstrably_distinct_from=tuple(
                             demonstrably_distinct_from))


def make_seams(
    *,
    candidates=None,
    adequacy="adequate",
    status="ok",
    contradiction=None,
    verifier=None,
    cited_notice=None,
    candidate_notices=None,
    tier_map=None,
    attestation=None,
    cited_source=CITED_SRC,
    candidate_source=CAND_SRC,
):
    if candidates is None:
        candidates = (candidate(),)
    if contradiction is None:
        contradiction = contradiction_json()
    if cited_notice is None:
        cited_notice = NoticeStatus(
            lookup_status="ok", source_role="no_notice_type")
    candidate_notices = candidate_notices or {}
    tier_map = tier_map or {}
    calls = {"attestation": 0, "judge": 0}

    def retrieve(cited_meta, claim, *, after_date, as_of_date):
        return RetrievalResult(candidates=tuple(candidates), adequacy=adequacy,
                               status=status, query_hash="qh")

    def fetch(work_id, *, as_of_date):
        return cited_source if work_id == EVIDENCE["cited_work_id"] else candidate_source

    def notice(work_id, *, as_of_date):
        if work_id == EVIDENCE["cited_work_id"]:
            return cited_notice
        return candidate_notices.get(work_id, NoticeStatus(
            lookup_status="ok", source_role="no_notice_type"))

    def tier(meta):
        hint = meta.get("tier_hint") if isinstance(meta, dict) else None
        wid = meta.get("work_id") if isinstance(meta, dict) else None
        if wid in tier_map:
            return EvidenceTier(tier_map[wid])
        if hint:
            return EvidenceTier(hint)
        return EvidenceTier(meta.get("cited_tier", "rct"))

    def attest(cited_meta, claim, replacement_work_id, *, as_of_date):
        calls["attestation"] += 1
        if attestation is None:
            return None
        return attestation(replacement_work_id)

    def judge(cited_src, cand_src, claim):
        calls["judge"] += 1
        if callable(contradiction):
            return contradiction(cited_src, cand_src, claim)
        return contradiction

    def verify(prompt):
        calls["verifier"] = calls.get("verifier", 0) + 1
        return verifier if verifier is not None else verifier_json()

    return dict(
        retrieve_superseding_candidates=retrieve,
        fetch_comparability_source=fetch,
        check_formal_notice=notice,
        classify_evidence_tier=tier,
        find_supersession_attestation=attest,
        judge_contradiction=judge,
        verify_contradiction=(verify if verifier is not None else None),
    ), calls


def run(support=None, *, evidence=EVIDENCE, policy=None, **seam_kwargs):
    if support is None:
        support = (ClaimSupport(0, SupportState.SUPPORTED, "covered", ("cov-span",)),)
    if policy is None:
        policy = F5Policy()
    seams, calls = make_seams(**seam_kwargs)
    temporal, records = decide_f5(CLAIMS, support, evidence, policy=policy, **seams)
    return temporal, records, calls


SUPPORTED = (ClaimSupport(0, SupportState.SUPPORTED, "covered", ("cov-span",)),)


def screen_batch(*rows):
    return CandidateScreenBatch(
        decisions=tuple(rows), prompt_sha256="a" * 64,
        response_sha256="b" * 64)


def screen_row(work_id, decision="plausible", *, relevance="match",
               relation="uncertain", missing=()):
    return CandidateScreenDecision(
        candidate_work_id=work_id, decision=decision,
        claim_relevance=relevance, possible_relation=relation,
        missing_facts=tuple(missing))


def test_abstract_screen_clear_mismatch_is_retained_and_blocks_clean_negative():
    seams, calls = make_seams()
    seams["screen_candidates"] = lambda **_kwargs: screen_batch(
        screen_row("W2", "clear_mismatch", relevance="mismatch",
                   relation="neutral"))

    policy = F5Policy(candidate_screen_enabled=True)
    temporal, records = decide_f5(
        CLAIMS, SUPPORTED, EVIDENCE, policy=policy, **seams)

    record = records[0]
    row = record["candidate_assessments"][0]
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert calls["judge"] == 0
    assert row["reason"] == "abstract_screen_clear_mismatch"
    assert row["discovery_disposition"] == "unassessable"
    assert record["search_complete"] is False
    assert record["cost_stage_counts"]["screen_clear_mismatch"] == 1
    validate_f5_record(record, policy)


def test_malformed_screen_ids_fail_open_to_every_deep_comparison():
    candidates = (candidate("W2"), candidate("W3"))
    seams, calls = make_seams(candidates=candidates)
    seams["screen_candidates"] = lambda **_kwargs: screen_batch(
        screen_row("W2"))  # W3 is missing: the batch is unusable.

    _temporal, records = decide_f5(
        CLAIMS, SUPPORTED, EVIDENCE,
        policy=F5Policy(candidate_screen_enabled=True), **seams)

    assert calls["judge"] == 2
    assert records[0]["candidate_screen_status"] == \
        "malformed_open_to_deep_comparison"
    assert all(row["screen_decision"] == "not_performed"
               for row in records[0]["candidate_assessments"])


def test_deep_comparison_budget_preserves_skipped_candidate_and_holds():
    candidates = (candidate("W2"), candidate("W3"))
    neutral = contradiction_json(
        directional_contradiction=False,
        relation_to_cited_finding="neutral",
        cited_direction="decrease", candidate_direction="decrease")
    seams, calls = make_seams(candidates=candidates, contradiction=neutral)

    policy = F5Policy(max_deep_comparisons=1)
    temporal, records = decide_f5(
        CLAIMS, SUPPORTED, EVIDENCE,
        policy=policy, **seams)

    record = records[0]
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert calls["judge"] == 1
    assert record["budget_exhausted"] is True
    assert record["search_complete"] is False
    assert record["evidence_profile"]["source_complete"] is False
    assert record["candidate_assessments"][1]["reason"] == \
        "deep_comparison_budget_exhausted"
    assert record["cost_stage_counts"]["candidates_budget_skipped"] == 1
    validate_f5_record(record, policy)


def test_known_same_cohort_does_not_consume_the_deep_comparison_budget():
    candidates = (candidate("W2"), candidate("W3"))
    evidence = {
        **EVIDENCE,
        "cited_meta": {
            **EVIDENCE["cited_meta"],
            "same_cohort_work_ids": ["W2"],
        },
    }
    seams, calls = make_seams(candidates=candidates)
    policy = F5Policy(max_deep_comparisons=1)

    temporal, records = decide_f5(
        CLAIMS, SUPPORTED, evidence, policy=policy, **seams)

    record = records[0]
    by_id = {row["candidate_work_id"]: row
             for row in record["candidate_assessments"]}
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert record["selected_contradiction_work_id"] == "W3"
    assert calls["judge"] == 1
    assert by_id["W2"]["reason"] == "not_independent"
    assert by_id["W2"]["contradiction_response"] is None
    assert record["budget_exhausted"] is False
    validate_f5_record(record, policy)


def test_incomplete_source_without_a_judge_call_does_not_spend_judge_budget():
    candidates = (candidate("W2"), candidate("W3"))
    seams, calls = make_seams(candidates=candidates)
    incomplete = ComparabilitySource(
        abstract="Population was not reported.", source_status="partial",
        missing_facts=("population",))

    def fetch(work_id, *, as_of_date):
        if work_id == "W1":
            return CITED_SRC
        return incomplete if work_id == "W2" else CAND_SRC

    seams["fetch_comparability_source"] = fetch
    policy = F5Policy(max_deep_comparisons=1)
    temporal, records = decide_f5(
        CLAIMS, SUPPORTED, EVIDENCE, policy=policy, **seams)

    record = records[0]
    by_id = {row["candidate_work_id"]: row
             for row in record["candidate_assessments"]}
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert record["selected_contradiction_work_id"] == "W3"
    assert calls["judge"] == 1
    assert by_id["W2"]["reason"] == "candidate_source_incomplete"
    assert by_id["W3"]["reason"] == "qualifying_contradiction"
    assert record["budget_exhausted"] is False
    assert record["cost_stage_counts"]["candidates_entering_deep_comparison"] == 2
    assert record["cost_stage_counts"]["deep_comparison_calls"] == 1
    validate_f5_record(record, policy)


def test_retrieval_receives_the_evidence_level_cited_work_id_without_mutation():
    seen = {}
    seams, _calls = make_seams()

    def retrieve(cited_meta, claim, *, after_date, as_of_date):
        seen.update(cited_meta)
        return RetrievalResult(
            candidates=(candidate(),), adequacy="adequate", status="ok",
            query_hash="qh")

    seams["retrieve_superseding_candidates"] = retrieve
    evidence = {
        **EVIDENCE,
        "cited_work_id": "12345",
        "cited_meta": dict(EVIDENCE["cited_meta"]),
    }
    decide_f5(CLAIMS, SUPPORTED, evidence, policy=F5Policy(), **seams)

    assert seen["cited_work_id"] == "12345"
    assert "cited_work_id" not in evidence["cited_meta"]


# --------------------------------------------------------------------------
# Deterministic Sec 18a.6 comparability combination.
# --------------------------------------------------------------------------
def test_comparability_hard_mismatch_dominates_uncertainty():
    assert derive_comparability_decision("match", "same", "equivalent") == "comparable"
    assert derive_comparability_decision("match", "same", "encompassing_direct") == "comparable"
    assert derive_comparability_decision("match", "not_same", "equivalent") == "not_comparable"
    assert derive_comparability_decision("mismatch", "same", "equivalent") == "not_comparable"
    assert derive_comparability_decision("match", "same", "narrower") == "not_comparable"
    assert derive_comparability_decision("match", "same", "disjoint") == "not_comparable"
    # hard mismatch dominates a co-occurring uncertainty (step 1 before step 2)
    assert derive_comparability_decision("mismatch", "uncertain", "unclear") == "not_comparable"
    assert derive_comparability_decision("uncertain", "same", "equivalent") == "uncertain"
    assert derive_comparability_decision(
        "match", "same", "encompassing_without_qualifying_direct_evidence") == "uncertain"
    assert derive_comparability_decision("match", "same", "unclear") == "uncertain"


def test_comparability_rejects_off_enum():
    with pytest.raises(ValueError):
        derive_comparability_decision("bogus", "same", "equivalent")


# --------------------------------------------------------------------------
# Row 1: full qualifying contradiction -> QUALIFYING_CONTRADICTION + surface.
# --------------------------------------------------------------------------
def test_full_qualifying_derives_quaifying_contradiction():
    temporal, records, _ = run()
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert temporal.claim_index == 0
    assert temporal.newer_work_id == "W2"
    assert temporal.same_claim_or_outcome is True
    assert temporal.comparable_population is True
    assert temporal.f8_notice is False
    assert temporal.evidence_spans == (CITED_FINDING_SPAN, CAND_CONTRA_SPAN)
    rec = records[0]
    assert rec["temporal_state"] == "QUALIFYING_CONTRADICTION"
    assert rec["discovery_disposition"] == "surface"
    assert rec["selected_contradiction_work_id"] == "W2"
    assert rec["f5_path"] == "B"  # no attestation -> escalate
    assert rec["controversy_bundle_sha256"] == \
        rec["controversy_bundle"]["bundle_sha256"]
    assert rec["evidence_profile"]["opposing_paper_count"] == 1
    assert rec["controversy_bundle"]["selected_contradiction_work_id"] == "W2"
    assert rec["path_a_eligible"] is False
    assert rec["reportable"] is False


def test_full_qualifying_fires_engine_f5_terminal():
    temporal, _records, _ = run()
    decision = decide_judgment(
        preband_cleared=True,
        claims=CLAIMS,
        claim_support=SUPPORTED,
        provenance=ProvenanceAssessment(ProvenanceState.PROPER_ORIGIN),
        temporal=temporal,
    )
    assert decision.status is DecisionStatus.TERMINAL
    assert decision.primary_label == "F5"
    assert decision.findings == ("F5",)


# --------------------------------------------------------------------------
# Row 2: outcome_relation=not_same -> confident negative, candidate do_not_surface.
# --------------------------------------------------------------------------
def test_outcome_not_same_is_confident_negative():
    temporal, records, _ = run(contradiction=contradiction_json(outcome_relation="not_same"))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    rec = records[0]
    assert rec["f5_path"] == "not_F5"
    assert rec["candidate_assessments"][0]["discovery_disposition"] == "do_not_surface"
    assert rec["candidate_assessments"][0]["comparability_decision"] == "not_comparable"


# --------------------------------------------------------------------------
# Row 3: comparability uncertain -> UNJUDGEABLE, may surface.
# --------------------------------------------------------------------------
def test_comparability_uncertain_holds_and_surfaces():
    temporal, records, _ = run(contradiction=contradiction_json(claim_match="uncertain"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    rec = records[0]
    assert rec["candidate_assessments"][0]["comparability_decision"] == "uncertain"
    assert rec["candidate_assessments"][0]["discovery_disposition"] == "surface"
    assert rec["discovery_disposition"] == "surface"


# --------------------------------------------------------------------------
# Row 4: independence false (same-cohort re-analysis) -> not_F5, never Path B.
# --------------------------------------------------------------------------
def test_same_cohort_reanalysis_is_not_f5():
    evidence = dict(EVIDENCE)
    evidence["cited_meta"] = {"authors": ["Smith"], "cited_tier": "rct",
                              "same_cohort_work_ids": ["W2"]}
    temporal, records, _ = run(evidence=evidence)
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    rec = records[0]
    assert rec["f5_path"] == "not_F5"
    cand = rec["candidate_assessments"][0]
    assert cand["independent"] == "not_independent"
    assert cand["discovery_disposition"] == "do_not_surface"


# --------------------------------------------------------------------------
# Row 5: independence unknown -> UNJUDGEABLE.
# --------------------------------------------------------------------------
def test_independence_unknown_holds():
    # Missing candidate author info -> unknown -> borderline hold.
    temporal, records, _ = run(candidates=(candidate(
        authors=(), registry_ids=(), demonstrably_distinct_from=()),))
    assert temporal.state is TemporalState.UNJUDGEABLE
    cand = records[0]["candidate_assessments"][0]
    assert cand["independent"] == "unknown"


def test_author_overlap_open_combinator_holds():
    # Shared author, no confirmed same cohort -> the OPEN Lock-D cell -> unknown.
    evidence = dict(EVIDENCE)
    evidence["cited_meta"] = {
        "id": "W1", "authors": ["Smith"], "cited_tier": "rct"}
    temporal, records, _ = run(
        evidence=evidence,
        candidates=(candidate(
            authors=("Smith",), registry_ids=(),
            demonstrably_distinct_from=()),))
    assert temporal.state is TemporalState.UNJUDGEABLE
    cand = records[0]["candidate_assessments"][0]
    assert cand["independent"] == "unknown"
    assert cand["independence_basis"] == "author_overlap_open_combinator"


def test_disjoint_authors_without_study_identity_remain_unknown():
    evidence = dict(EVIDENCE)
    evidence["cited_meta"] = {
        "id": "W1", "authors": ["Smith"], "cited_tier": "rct"}
    temporal, records, _ = run(
        evidence=evidence,
        candidates=(candidate(
            authors=("Jones",), registry_ids=(),
            demonstrably_distinct_from=()),))
    assert temporal.state is TemporalState.UNJUDGEABLE
    candidate_row = records[0]["candidate_assessments"][0]
    assert candidate_row["independent"] == "unknown"
    assert candidate_row["independence_basis"] == \
        "disjoint_authorship_insufficient"
    assert candidate_row["study_cluster_uncertain"] is True


def test_explicitly_distinct_data_record_distinct_clusters():
    temporal, records, _ = run()
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    candidate_row = records[0]["candidate_assessments"][0]
    assert candidate_row["independent"] == "independent"
    assert candidate_row["independence_basis"] == "explicit_distinct_data"
    assert candidate_row["cited_study_cluster_id"] != \
        candidate_row["candidate_study_cluster_id"]
    assert candidate_row["study_cluster_uncertain"] is False


def test_explicit_source_bound_no_overlap_can_establish_independence():
    cited_source = ComparabilitySource(
        abstract=("Cohort Alpha, Chicago, 2009, NCT00000001. "
                  "Drug X reduced disease Y risk in adults."),
        packet_sha256="a" * 64)
    candidate_source = ComparabilitySource(
        abstract=("We enrolled separate Cohort Beta, Tokyo, 2019; no participants "
                  "came from Alpha, NCT00000002. Drug X did NOT reduce disease Y "
                  "in adults."),
        packet_sha256="b" * 64)
    evidence = {
        **EVIDENCE,
        "cited_meta": {
            "id": "W1", "authors": ["Smith"], "cited_tier": "rct",
            "registry_ids": ["NCT00000001"],
        },
    }
    contradiction = contradiction_json(
        cited_finding_span="Drug X reduced disease Y risk in adults.",
        candidate_contradiction_span=(
            "Drug X did NOT reduce disease Y in adults."))
    temporal, records, _ = run(
        evidence=evidence,
        candidates=(candidate(
            registry_ids=("NCT00000002",),
            demonstrably_distinct_from=()),),
        cited_source=cited_source, candidate_source=candidate_source,
        contradiction=contradiction)
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    row = records[0]["candidate_assessments"][0]
    assert row["independent"] == "independent"
    assert row["independence_basis"] == "source_bound_distinct_data"
    assert "no participants came from Alpha" in row[
        "source_bound_distinct_span"]


# --------------------------------------------------------------------------
# Row 6: empty / failed / adequacy=empty retrieval -> UNJUDGEABLE (never negative).
# --------------------------------------------------------------------------
def test_empty_retrieval_holds():
    temporal, records, _ = run(candidates=(), adequacy="empty", status="ok")
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "retrieval_empty"


def test_failed_retrieval_holds():
    temporal, records, _ = run(candidates=(), adequacy="empty", status="failure")
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "retrieval_failure"


def test_partial_retrieval_without_qualifier_holds():
    temporal, records, _ = run(
        contradiction=contradiction_json(outcome_relation="not_same"), status="partial")
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "retrieval_partial"


def test_partial_retrieval_with_qualifier_still_fires():
    temporal, _records, _ = run(status="partial")
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION


def test_inadequate_retrieval_never_confident_negative():
    # An inadequate retrieval (status=ok) with a single clearly-nonqualifying
    # candidate must HOLD (UNJUDGEABLE), never license a confident negative.
    temporal, records, _ = run(
        adequacy="inadequate",
        contradiction=contradiction_json(outcome_relation="not_same"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "retrieval_inadequate"
    assert records[0]["discovery_disposition"] == "unassessable"


def test_empty_retrieval_disposition_is_unassessable():
    # An empty retrieval rolls up to discovery_disposition=unassessable (Sec 8a /
    # Sec 10): you cannot surface what you never retrieved.
    _temporal, records, _ = run(candidates=(), adequacy="empty", status="ok")
    assert records[0]["discovery_disposition"] == "unassessable"


# --------------------------------------------------------------------------
# Row 7: >=1 unjudgeable candidate + none qualifying -> UNJUDGEABLE.
# --------------------------------------------------------------------------
def test_unjudgeable_candidate_blocks_confident_negative():
    # Two candidates: one clear negative, one flagged (unassessable) -> held.
    def contra(cited_src, cand_src, claim):
        return contradiction_json(outcome_relation="not_same")
    temporal, records, _ = run(
        candidates=(candidate("W2"), candidate("W3")),
        candidate_notices={"W3": NoticeStatus(notice_kind="correction",
                                              notice_resolution="flagged")},
        contradiction=contra,
    )
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "candidate_set_not_fully_judgeable"


def test_qualifying_candidate_wins_over_unjudgeable_sibling():
    # Sec 8 / Sec 9-1: a judgeable candidate that qualifies -> F5 REGARDLESS of
    # other unjudgeable candidates. W2 qualifies; W3 is a flagged (unassessable)
    # sibling -> QUALIFYING, not held.
    def contra(cited_src, cand_src, claim):
        return contradiction_json()
    temporal, records, _ = run(
        candidates=(candidate("W2"), candidate("W3")),
        candidate_notices={"W3": NoticeStatus(notice_kind="retraction",
                                              notice_resolution="unresolved")},
        contradiction=contra,
    )
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert temporal.newer_work_id == "W2"


# --------------------------------------------------------------------------
# Row 8: adequate + nonempty + fully judgeable, all nonqualifying -> negative.
# --------------------------------------------------------------------------
def test_all_nonqualifying_is_confident_negative():
    temporal, records, _ = run(
        contradiction=contradiction_json(
            directional_contradiction=False, candidate_direction="decrease"))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0]["reason"] == "all_candidates_nonqualifying"
    assert records[0]["candidate_assessments"][0]["reason"] == "not_directional_contradiction"


def test_comparable_confirmation_is_preserved_but_never_called_opposition():
    temporal, records, _ = run(contradiction=contradiction_json(
        directional_contradiction=False,
        relation_to_cited_finding="confirms",
        candidate_direction="decrease"))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    row = records[0]["candidate_assessments"][0]
    assert row["reason"] == "comparable_confirmation"
    assert records[0]["controversy_bundle"][
        "comparable_confirmations"][0]["candidate_work_id"] == "W2"
    assert records[0]["evidence_profile"]["evidence_pattern"] == \
        "confirmation_only_found"


def test_confirmation_with_incomplete_source_remains_unassessable():
    incomplete = ComparabilitySource(
        abstract=CAND_SRC.abstract, source_status="partial")
    temporal, records, _ = run(
        candidate_source=incomplete,
        contradiction=contradiction_json(
            directional_contradiction=False,
            relation_to_cited_finding="confirms",
            candidate_direction="decrease"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["candidate_assessments"][0]["reason"] == \
        "confirmation_source_incomplete"


def test_mixed_finding_surfaces_path_b_without_clean_replacement():
    temporal, records, _ = run(contradiction=contradiction_json(
        directional_contradiction=False,
        relation_to_cited_finding="mixed",
        candidate_direction="mixed"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["f5_path"] == "B"
    assert records[0]["selected_replacement_work_id"] is None
    assert records[0]["candidate_assessments"][0]["reason"] == "mixed_finding"
    assert records[0]["evidence_profile"]["evidence_pattern"] == "mixed_evidence"


# --------------------------------------------------------------------------
# Row 9: qualifying + bound SR/MA attestation + >=2yr + equal-or-higher tier ->
# QUALIFYING; path_a_eligible=True; f5_path=B (deploy off); path_a_deployed=False.
# --------------------------------------------------------------------------
_ATTEST_SPAN = "the earlier finding is reversed"
_ATTEST_SOURCE = (
    "This systematic review concludes that " + _ATTEST_SPAN + " by more recent trials.")


def _attest_for(work_id, *, attestation_type="systematic_review",
                attestation_date="2021-01-01", replacement_date=None,
                source_text=_ATTEST_SOURCE):
    return Attestation(
        attestation_type=attestation_type,
        source_id="SR-1",
        attestation_date=attestation_date,
        replacement_work_id=work_id,
        attestation_conclusion_span=_ATTEST_SPAN,
        replacement_date=replacement_date,
        source_text=source_text,
    )


def test_path_a_eligible_but_not_deployed():
    temporal, records, calls = run(
        attestation=lambda wid: _attest_for(wid),
    )
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    rec = records[0]
    assert rec["path_a_eligible"] is True
    assert rec["path_a_deployed"] is False
    assert rec["f5_path"] == "B"          # deploy_path_a=False -> stays B
    assert rec["selected_replacement_work_id"] == "W2"
    assert calls["attestation"] == 1


def test_deploy_path_a_is_hard_gated_off():
    # Blueprint Sec 13: this build runs under a HARD deploy_path_a=False. Enabling
    # it is rejected outright -- a policy flip can never deploy Path A.
    with pytest.raises(ValueError, match="deploy_path_a must be False"):
        run(policy=F5Policy(deploy_path_a=True), attestation=lambda wid: _attest_for(wid))


# --------------------------------------------------------------------------
# Row 10: attestation present but no detected contradiction -> not F5, not Path A.
# --------------------------------------------------------------------------
def test_attestation_alone_cannot_manufacture_f5():
    # A would-be attestation exists, but the contradiction judgment does not
    # qualify (outcome not_same) -> the attestation seam is never consulted.
    temporal, records, calls = run(
        contradiction=contradiction_json(outcome_relation="not_same"),
        attestation=lambda wid: _attest_for(wid),
    )
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False
    assert records[0]["f5_path"] == "not_F5"
    assert calls["attestation"] == 0


# --------------------------------------------------------------------------
# Row 11: flagged / retracted candidate -> unjudgeable audit row, never a
# replacement; the only candidate -> UNJUDGEABLE.
# --------------------------------------------------------------------------
def test_flagged_candidate_is_unjudgeable_audit_row():
    temporal, records, _ = run(
        candidate_notices={"W2": NoticeStatus(notice_kind="retraction",
                                             notice_resolution="unresolved")})
    assert temporal.state is TemporalState.UNJUDGEABLE
    cand = records[0]["candidate_assessments"][0]
    assert cand["reason"] == "candidate_flagged_notice"
    assert cand["discovery_disposition"] == "unassessable"


# --------------------------------------------------------------------------
# Row 12: cited claim not SUPPORTED -> detector never emits QUALIFYING.
# --------------------------------------------------------------------------
def test_non_supported_claim_never_emits_qualifying():
    support = (ClaimSupport(0, SupportState.UNESTABLISHED, "not established"),)
    seams, _ = make_seams()
    temporal, records = decide_f5(CLAIMS, support, EVIDENCE, policy=F5Policy(), **seams)
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0] == {"claim_index": 0, "assessed": False}
    # And the engine does not raise its SUPPORTED-only guard.
    decision = decide_judgment(
        preband_cleared=True, claims=CLAIMS, claim_support=support,
        provenance=None, temporal=temporal)
    assert "F5" not in decision.findings


def test_weaker_strength_claim_out_of_scope():
    support = (ClaimSupport(0, SupportState.WEAKER_STRENGTH, "weaker"),)
    seams, _ = make_seams()
    temporal, records = decide_f5(CLAIMS, support, EVIDENCE, policy=F5Policy(), **seams)
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0] == {"claim_index": 0, "assessed": False}


# --------------------------------------------------------------------------
# Row 13: two qualifying candidates -> deterministic pick (tier -> recent -> id).
# --------------------------------------------------------------------------
def test_two_qualifying_candidates_pick_highest_tier():
    cands = (candidate("W2", pub_date="2020-01-01", authors=("Jones",), tier_hint="rct"),
             candidate("W3", pub_date="2019-01-01", authors=("Lee",),
                       tier_hint="systematic_review_or_meta_analysis"))
    temporal, records, _ = run(candidates=cands)
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    # W3 is a higher tier (SR/MA) than W2 (RCT) despite being older.
    assert temporal.newer_work_id == "W3"


def test_two_qualifying_same_tier_pick_most_recent():
    cands = (candidate("W2", pub_date="2018-01-01", tier_hint="rct"),
             candidate("W3", pub_date="2021-01-01", tier_hint="rct"))
    temporal, _records, _ = run(candidates=cands)
    assert temporal.newer_work_id == "W3"  # same tier -> most recent


def test_path_a_replacement_selected_among_eligible_by_tier():
    # Sec 9-11: the Path-A replacement is chosen among ELIGIBLE candidates by
    # (tier desc -> recent -> id). W2 (RCT) and W3 (SR/MA) both qualify and both
    # get a bound attestation; the SR/MA is the selected replacement even though
    # the detection representative is likewise the highest tier.
    cands = (candidate("W2", pub_date="2020-01-01", authors=("Jones",), tier_hint="rct"),
             candidate("W3", pub_date="2019-01-01", authors=("Lee",),
                       tier_hint="systematic_review_or_meta_analysis"))
    temporal, records, _ = run(candidates=cands,
                               attestation=lambda wid: _attest_for(wid))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    rec = records[0]
    assert rec["path_a_eligible"] is True
    assert rec["selected_replacement_work_id"] == "W3"      # highest tier
    assert rec["selected_contradiction_work_id"] == "W3"    # detection rep too


def test_same_cohort_via_non_list_iterable_still_not_independent():
    # FINDING-6 regression: a confirmed same-cohort set given as a NON-list
    # iterable (dict_keys) must still fail the independence guard, never fail
    # OPEN to 'independent'.
    evidence = dict(EVIDENCE)
    evidence["cited_meta"] = {"authors": ["Smith"], "cited_tier": "rct",
                              "same_cohort_work_ids": {"W2": 1}.keys()}
    temporal, records, _ = run(evidence=evidence)
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    cand = records[0]["candidate_assessments"][0]
    assert cand["independent"] == "not_independent"


def test_same_cohort_string_payload_raises():
    evidence = dict(EVIDENCE)
    evidence["cited_meta"] = {"authors": ["Smith"], "same_cohort_work_ids": "W2"}
    with pytest.raises(ValueError, match="same_cohort_work_ids"):
        run(evidence=evidence)


# --------------------------------------------------------------------------
# Row 14: malformed / off-enum contradiction JSON -> ValueError (quarantine).
# --------------------------------------------------------------------------
def test_malformed_contradiction_json_raises():
    with pytest.raises(ValueError):
        run(contradiction="not json at all")


def test_off_enum_contradiction_raises():
    with pytest.raises(ValueError):
        run(contradiction=contradiction_json(outcome_relation="reversed"))


def test_confidence_out_of_range_raises():
    with pytest.raises(ValueError):
        run(contradiction=contradiction_json(confidence=1.5))


def test_extra_key_contradiction_raises():
    payload = json.loads(contradiction_json())
    payload["surprise"] = 1
    with pytest.raises(ValueError):
        run(contradiction=json.dumps(payload))


# --------------------------------------------------------------------------
# Row 15: validate_f5_record replay guard.
# --------------------------------------------------------------------------
def test_validate_record_passes_for_untampered():
    _temporal, records, _ = run(attestation=lambda wid: _attest_for(wid))
    validate_f5_record(records[0], F5Policy())


def test_validate_record_detects_sha_tamper():
    _temporal, records, _ = run()
    rec = dict(records[0])
    rec["reason"] = "tampered"
    with pytest.raises(ValueError, match="record_sha256 mismatch"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_detects_comparability_drift():
    _temporal, records, _ = run()
    rec = json.loads(json.dumps(records[0]))  # deep copy
    rec["candidate_assessments"][0]["comparability_decision"] = "not_comparable"
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="comparability_decision drifted"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_detects_f5_path_drift():
    _temporal, records, _ = run()
    rec = json.loads(json.dumps(records[0]))
    rec["f5_path"] = "A"
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="f5_path"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_rejects_rehashed_false_open_independence():
    _temporal, records, _ = run(candidates=(candidate(
        registry_ids=(), demonstrably_distinct_from=()),))
    rec = json.loads(json.dumps(records[0]))
    cand = rec["candidate_assessments"][0]
    assert cand["independent"] == "unknown"
    cand["independent"] = "independent"
    cand["independence_basis"] = "explicit_distinct_data"
    cand["reason"] = "qualifying_contradiction"
    cand["discovery_disposition"] = "surface"
    cand["criteria_fired"] = [
        "directional_contradiction", "comparable", "independent",
        "spans_verbatim", "confidence_ok", "notice_clear",
    ]
    rec["temporal_state"] = "QUALIFYING_CONTRADICTION"
    rec["reason"] = "qualifying_contradiction"
    rec["selected_contradiction_work_id"] = cand["candidate_work_id"]
    rec["same_claim_or_outcome"] = True
    rec["comparable_population"] = True
    rec["cited_finding_span"] = cand["cited_finding_span"]
    rec["candidate_contradiction_span"] = cand[
        "candidate_contradiction_span"]
    rec["confidence"] = cand["confidence"]
    rec["f5_path"] = "B"
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="distinct-data evidence"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_rejects_rehashed_neutral_as_confirmation():
    from .f5_controversy_bundle import build_controversy_bundle

    _temporal, records, _ = run(contradiction=contradiction_json(
        directional_contradiction=False,
        relation_to_cited_finding="neutral", candidate_direction="decrease"))
    rec = json.loads(json.dumps(records[0]))
    rec["candidate_assessments"][0]["reason"] = "comparable_confirmation"
    bundle = build_controversy_bundle(rec, citation_id=rec.get("citation_id"))
    rec["controversy_bundle"] = bundle
    rec["controversy_bundle_sha256"] = bundle["bundle_sha256"]
    rec["evidence_profile"] = bundle["evidence_profile"]
    rec["search_complete"] = bundle["search_complete"]
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="confirmation reason conflicts"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_detects_policy_version_mismatch():
    _temporal, records, _ = run()
    with pytest.raises(ValueError, match="f5_policy_version"):
        validate_f5_record(records[0], F5Policy(policy_version="different_v"))


def test_validate_record_detects_engine_boolean_tamper():
    # FINDING-1 regression: flipping comparable_population and RECOMPUTING the
    # sha (defeating the whole-record hash) must still be caught by re-deriving
    # the engine booleans from the selected contradiction's stored axes.
    _temporal, records, _ = run()
    rec = json.loads(json.dumps(records[0]))
    rec["comparable_population"] = False
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="engine booleans drifted"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_detects_comparability_policy_mismatch():
    # FINDING-2 regression: the comparability policy version is bound to the
    # supplied policy, not merely carried in the record.
    _temporal, records, _ = run()
    with pytest.raises(ValueError, match="comparability_policy_version"):
        validate_f5_record(records[0], F5Policy(comparability_policy_version="other_v"))


def test_validate_record_rejects_impossible_screen_status_provenance():
    from .f5_controversy_bundle import build_controversy_bundle

    _temporal, records, _ = run()
    rec = json.loads(json.dumps(records[0]))
    rec["candidate_screen_status"] = "not_needed_no_candidates"
    rec["candidate_screen_version"] = "f5_candidate_screen_v1"
    bundle = build_controversy_bundle(rec, citation_id=rec.get("citation_id"))
    rec["controversy_bundle"] = bundle
    rec["controversy_bundle_sha256"] = bundle["bundle_sha256"]
    rec["evidence_profile"] = bundle["evidence_profile"]
    rec["search_complete"] = bundle["search_complete"]
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="policy disabled"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_rejects_clear_mismatch_that_reached_deep_judgment():
    from .f5_controversy_bundle import build_controversy_bundle

    seams, _calls = make_seams()
    seams["screen_candidates"] = lambda **_kwargs: screen_batch(
        screen_row("W2", "plausible", relevance="match", relation="opposes"))
    policy = F5Policy(candidate_screen_enabled=True)
    _temporal, records = decide_f5(
        CLAIMS, SUPPORTED, EVIDENCE, policy=policy, **seams)
    rec = json.loads(json.dumps(records[0]))
    cand = rec["candidate_assessments"][0]
    cand["screen_decision"] = "clear_mismatch"
    cand["screen_claim_relevance"] = "mismatch"
    cand["screen_possible_relation"] = "neutral"
    cand["screen_missing_facts"] = []
    rec["cost_stage_counts"]["screen_plausible"] = 0
    rec["cost_stage_counts"]["screen_clear_mismatch"] = 1
    bundle = build_controversy_bundle(rec, citation_id=rec.get("citation_id"))
    rec["controversy_bundle"] = bundle
    rec["controversy_bundle_sha256"] = bundle["bundle_sha256"]
    rec["evidence_profile"] = bundle["evidence_profile"]
    rec["search_complete"] = bundle["search_complete"]
    rec["record_sha256"] = record_sha256(rec)
    with pytest.raises(ValueError, match="reached deep comparison"):
        validate_f5_record(rec, policy)


# --------------------------------------------------------------------------
# Edge cases (blueprint Sec 9).
# --------------------------------------------------------------------------
def test_cited_retraction_flags_upstream_inconsistency():
    temporal, records, _ = run(cited_notice=NoticeStatus(
        notice_kind="retraction", notice_resolution="flagged",
        lookup_status="ok", date_status="compared",
        source_role="retracted_article"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "cited_retracted_upstream_f8_inconsistency"


def test_unresolved_cited_notice_holds_before_retrieval():
    seams, _calls = make_seams(cited_notice=NoticeStatus(
        notice_resolution="unresolved", lookup_status="failure"))
    retrieval_calls = {"count": 0}

    def retrieve(*_args, **_kwargs):
        retrieval_calls["count"] += 1
        raise AssertionError("unresolved notice reached retrieval")

    seams["retrieve_superseding_candidates"] = retrieve
    temporal, records = decide_f5(
        CLAIMS, SUPPORTED, EVIDENCE, policy=F5Policy(), **seams)
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert retrieval_calls["count"] == 0
    assert records[0]["reason"] == "cited_notice_unresolved"
    assert records[0]["cited_notice_lookup_status"] == "failure"


def test_cited_correction_caps_at_path_b():
    # Cited work under an EoC: detection still fires, but Path A is capped even
    # with a bound attestation (Sec 9-21).
    temporal, records, _ = run(
        cited_notice=NoticeStatus(
            notice_kind="eoc", notice_resolution="flagged",
            lookup_status="ok", date_status="compared",
            source_role="eoc_subject"),
        attestation=lambda wid: _attest_for(wid))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    rec = records[0]
    assert rec["cited_eoc_caps"] is True
    assert rec["path_a_eligible"] is False
    assert rec["f5_path"] == "B"


def test_span_not_verbatim_holds_unassessable():
    temporal, records, _ = run(
        contradiction=contradiction_json(cited_finding_span="not in the source"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    cand = records[0]["candidate_assessments"][0]
    assert cand["reason"] == "span_unverifiable"
    assert cand["discovery_disposition"] == "unassessable"


def test_whitespace_only_span_holds_unassessable():
    # A blank/whitespace span is a substring of most sources but would trip the
    # frozen engine's nonblank evidence_spans guard -> must hold, never qualify.
    src = ComparabilitySource(abstract="Drug X reduced disease Y risk in adults. ")
    temporal, records, _ = run(
        cited_source=src,
        contradiction=contradiction_json(cited_finding_span=" "))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["candidate_assessments"][0]["reason"] == "span_unverifiable"


def test_candidate_predates_cited_is_do_not_surface():
    temporal, records, _ = run(candidates=(candidate("W2", pub_date="2005-01-01"),))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    cand = records[0]["candidate_assessments"][0]
    assert cand["reason"] == "candidate_predates_cited"
    assert cand["discovery_disposition"] == "do_not_surface"


def test_below_confidence_floor_is_do_not_surface():
    temporal, records, _ = run(contradiction=contradiction_json(confidence=0.05))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0]["candidate_assessments"][0]["reason"] == "below_confidence_floor"


def test_directional_false_nonsignificant_not_qualifying():
    # Newer result merely nonsignificant: absence of evidence is not reversal.
    temporal, records, _ = run(
        contradiction=contradiction_json(directional_contradiction=False,
                                         relation_to_cited_finding="opposes",
                                         candidate_direction="no_effect"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["candidate_assessments"][0]["reason"] == \
        "opposition_not_directional"


def test_tier_downgrade_detects_but_no_path_a():
    # Cited RCT contradicted by a case series (lower tier): detection fires, but
    # the equal-or-higher-tier Path-A gate fails -> Path B, ineligible.
    cands = (candidate("W2", tier_hint="case_series_or_report"),)
    temporal, records, _ = run(candidates=cands, attestation=lambda wid: _attest_for(wid))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    rec = records[0]
    assert rec["path_a_eligible"] is False
    assert rec["f5_path"] == "B"
    assert rec["candidate_assessments"][0]["tier_relation"] == "lower"


def test_date_gap_below_threshold_blocks_path_a():
    evidence = dict(EVIDENCE)
    evidence["cited_date"] = "2019-06-01"
    cands = (candidate("W2", pub_date="2020-01-01"),)  # < 2 years
    temporal, records, _ = run(evidence=evidence, candidates=cands,
                               attestation=lambda wid: _attest_for(
                                   wid, attestation_date="2020-06-01"))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False


def test_preprint_candidate_path_b_only():
    cands = (candidate("W2", tier_hint="preprint_unreviewed"),)
    temporal, records, _ = run(candidates=cands, attestation=lambda wid: _attest_for(wid))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False  # preprint < RCT tier
    assert records[0]["f5_path"] == "B"


def test_attestation_unbound_replacement_does_not_gate_path_a():
    # Attestation bound to the WRONG work id (replacement_work_id != candidate.id)
    # -> ineligible (the binding check, Sec 6-I).
    temporal, records, _ = run(attestation=lambda wid: _attest_for("W_OTHER"))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False


def test_attestation_type_outside_policy_does_not_gate_path_a():
    # A bound guideline attestation, but the policy admits only SR / MA -> the
    # attestation_type-vs-policy gate rejects it (Sec 6-I / Sec 13).
    policy = F5Policy(attestation_types=frozenset({"systematic_review", "meta_analysis"}))
    temporal, records, _ = run(
        policy=policy,
        attestation=lambda wid: _attest_for(wid, attestation_type="major_guideline_revision"))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False


def test_guideline_coincident_with_replacement_is_ineligible():
    # Sec 6-I: a guideline that IS the replacement (source_id == candidate.id)
    # can never gate Path A; only an SR/MA may coincide with the replacement.
    temporal, records, _ = run(
        attestation=lambda wid: Attestation(
            attestation_type="major_guideline_revision",
            source_id=wid,                      # coincident with the replacement
            attestation_date="2021-01-01",
            replacement_work_id=wid,
            attestation_conclusion_span=_ATTEST_SPAN,
            source_text=_ATTEST_SOURCE))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False


def test_sr_coincident_with_replacement_can_gate_path_a():
    # An SR/MA that both contradicts and is the replacement, with a validated
    # conclusion span, MAY gate Path A (Sec 6-I: separate role, not separate doc).
    cands = (candidate("W2", tier_hint="systematic_review_or_meta_analysis"),)
    temporal, records, _ = run(
        candidates=cands,
        attestation=lambda wid: Attestation(
            attestation_type="systematic_review",
            source_id=wid,                      # coincident, but SR is allowed
            attestation_date="2021-01-01",
            replacement_work_id=wid,
            attestation_conclusion_span=_ATTEST_SPAN,
            source_text=_ATTEST_SOURCE))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is True


def test_attestation_without_source_text_cannot_gate_path_a():
    # Sec 5 / Sec 10: the conclusion span is required for EVERY attestation and
    # must be verbatim in the attestation source. No source_text -> unverifiable
    # span -> ineligible (fail closed), even though every other gate passes.
    temporal, records, _ = run(
        attestation=lambda wid: _attest_for(wid, source_text=None))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False


def test_attestation_span_not_in_source_cannot_gate_path_a():
    temporal, records, _ = run(
        attestation=lambda wid: _attest_for(
            wid, source_text="an unrelated abstract with no reversal conclusion"))
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert records[0]["path_a_eligible"] is False


def test_attestation_not_temporally_bounded_ineligible():
    # attestation_date after as_of_date -> temporal bound fails.
    temporal, records, _ = run(
        attestation=lambda wid: _attest_for(wid, attestation_date="2030-01-01"))
    assert records[0]["path_a_eligible"] is False


# --------------------------------------------------------------------------
# Multi-claim aggregation + engine precedence.
# --------------------------------------------------------------------------
def test_multi_claim_f6_and_f5_engine_order():
    claims = (CLAIM, "Gene Z causes disease Q")
    support = (
        ClaimSupport(0, SupportState.SUPPORTED, "covered", ("cov",)),
        ClaimSupport(1, SupportState.UNESTABLISHED, "not established"),
    )
    # Claim 0 qualifies; claim 1 is UNESTABLISHED (never assessed by F5).
    seams, _ = make_seams()
    temporal, records = decide_f5(claims, support, EVIDENCE, policy=F5Policy(), **seams)
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert temporal.claim_index == 0
    assert records[1] == {"claim_index": 1, "assessed": False}
    decision = decide_judgment(
        preband_cleared=True, claims=claims, claim_support=support,
        provenance=None, temporal=temporal)
    # F5 rides along the F6 evidence fault; F5 is lowest precedence.
    assert decision.findings == ("F6", "F5")


# --------------------------------------------------------------------------
# Deployment-mode / unfrozen-lock fail-closed behavior.
# --------------------------------------------------------------------------
def test_unfrozen_confidence_floor_fails_closed():
    policy = F5Policy(
        mode="deployment", confidence_floor=None,
        generator_model_id="model", verifier_model_id="model")
    temporal, records, _ = run(policy=policy, verifier=verifier_json())
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["candidate_assessments"][0]["reason"] == "confidence_floor_unfrozen"


def test_deployment_below_floor_holds_not_confident_negative():
    # Sec 9-22: in deployment mode low confidence HOLDS (UNJUDGEABLE), it is not a
    # confident negative (that would be the discovery-mode override, Sec 8a).
    policy = F5Policy(
        mode="deployment", confidence_floor=0.25,
        generator_model_id="model", verifier_model_id="model")
    temporal, records, _ = run(policy=policy,
                               contradiction=contradiction_json(confidence=0.05),
                               verifier=verifier_json(),
                               cited_source=FORMAL_CITED_SRC,
                               candidate_source=FORMAL_CAND_SRC)
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["candidate_assessments"][0]["reason"] == "below_confidence_floor"


# --------------------------------------------------------------------------
# RetrievalResult contract + config validation.
# --------------------------------------------------------------------------
def test_invalid_retrieval_adequate_but_empty_raises():
    with pytest.raises(ValueError, match="empty candidate list requires"):
        RetrievalResult(candidates=(), adequacy="adequate", status="ok")


def test_invalid_retrieval_inadequate_but_empty_raises():
    # Blueprint Sec 5: an empty candidate list requires adequacy='empty' (empty
    # candidates with 'inadequate' is also an invalid combination).
    with pytest.raises(ValueError, match="empty candidate list requires"):
        RetrievalResult(candidates=(), adequacy="inadequate", status="ok")


def test_invalid_retrieval_empty_but_nonempty_raises():
    with pytest.raises(ValueError, match="empty"):
        RetrievalResult(candidates=(candidate(),), adequacy="empty", status="ok")


def test_policy_validation_rejects_bad_mode():
    with pytest.raises(ValueError, match="mode"):
        validate_f5_policy(F5Policy(mode="bogus"))


def test_policy_validation_rejects_bad_confidence_floor():
    with pytest.raises(ValueError, match="confidence_floor"):
        validate_f5_policy(F5Policy(confidence_floor=2.0))


def test_evidence_validation_requires_cited_before_as_of():
    evidence = dict(EVIDENCE)
    evidence["cited_date"] = "2025-01-01"  # after as_of_date
    seams, _ = make_seams()
    with pytest.raises(ValueError, match="cited_date"):
        decide_f5(CLAIMS, SUPPORTED, evidence, policy=F5Policy(), **seams)


def test_input_validation_rejects_support_length_mismatch():
    seams, _ = make_seams()
    with pytest.raises(ValueError, match="one row per claim"):
        decide_f5(CLAIMS, (), EVIDENCE, policy=F5Policy(), **seams)


def test_make_temporal_assessor_rejects_noncallable_seam():
    seams, _ = make_seams()
    seams["judge_contradiction"] = "not callable"
    with pytest.raises(ValueError, match="judge_contradiction"):
        make_temporal_assessor(evidence=EVIDENCE, policy=F5Policy(), **seams)


# --------------------------------------------------------------------------
# Formal Path-B detection is reportable only after positive verification.
# --------------------------------------------------------------------------
def test_discovery_record_remains_nonreportable():
    _temporal, records, _ = run(
        policy=F5Policy(), attestation=lambda wid: _attest_for(wid))
    assert records[0]["reportable"] is False
    assert records[0]["verifier_result"] == "not_run"


def test_deployment_positive_is_reportable_after_verifier_confirmation():
    policy = F5Policy(
        mode="deployment", generator_model_id="model",
        verifier_model_id="model")
    temporal, records, calls = run(
        policy=policy, verifier=verifier_json(),
        attestation=lambda wid: _attest_for(wid),
        cited_source=FORMAL_CITED_SRC,
        candidate_source=FORMAL_CAND_SRC)
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert calls["verifier"] == 1
    assert records[0]["verifier_result"] == "confirmed"
    assert records[0]["reportable"] is True
    validate_f5_record(records[0], policy, FORMAL_PACKET_MAP)
    block = jr._f5_manifest_block(policy, records, {
        "_verifier_wired": True,
        "verifier_calls": 1,
        "retrieval_protocols": [],
    })
    assert block["reportable"] is True
    assert block["verifier_wired"] is True
    assert block["production_evidence_builder"] is True


def test_deployment_verifier_disagreement_holds_without_f5():
    policy = F5Policy(
        mode="deployment", generator_model_id="model",
        verifier_model_id="model")
    temporal, records, calls = run(
        policy=policy,
        verifier=verifier_json(candidate_span_contradicts_claim=False),
        cited_source=FORMAL_CITED_SRC,
        candidate_source=FORMAL_CAND_SRC)
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert calls["verifier"] == 1
    assert records[0]["verifier_result"] == "rejected"
    assert records[0]["temporal_state"] == "UNJUDGEABLE"
    assert records[0]["reportable"] is True
    validate_f5_record(records[0], policy)


def test_deployment_unbound_source_packets_hold_before_verifier_call():
    policy = F5Policy(
        mode="deployment", generator_model_id="model",
        verifier_model_id="model")
    temporal, records, calls = run(
        policy=policy, verifier=verifier_json())
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert calls.get("verifier", 0) == 0
    assert records[0]["candidate_assessments"][0]["reason"] == \
        "verifier_source_unbound"


def test_deployment_malformed_verifier_output_is_loud_parse_failure():
    policy = F5Policy(
        mode="deployment", generator_model_id="model",
        verifier_model_id="model")
    with pytest.raises(ValueError, match="F5 verifier keys mismatch"):
        run(policy=policy, verifier='{"same_claim_or_outcome": true}',
            cited_source=FORMAL_CITED_SRC,
            candidate_source=FORMAL_CAND_SRC)


def test_deployment_rejects_unwired_verifier_before_seam_calls():
    policy = F5Policy(
        mode="deployment", generator_model_id="model",
        verifier_model_id="model")
    seams, calls = make_seams()
    with pytest.raises(ValueError, match="independent verify_contradiction"):
        decide_f5(CLAIMS, SUPPORTED, EVIDENCE, policy=policy, **seams)
    assert calls["judge"] == 0


def test_policy_rejects_any_sufficient_path_a_rule():
    with pytest.raises(ValueError, match="path_a_rule"):
        validate_f5_policy(F5Policy(path_a_rule="any_sufficient"))


def test_policy_rejects_nondefault_tier_rule():
    with pytest.raises(ValueError, match="tier_rule"):
        validate_f5_policy(F5Policy(tier_rule="higher_only"))


def test_policy_rejects_set_independence_rule():
    with pytest.raises(ValueError, match="independence_rule"):
        validate_f5_policy(F5Policy(independence_rule="authors_and_data"))


def test_policy_rejects_nondefault_comparability_rule():
    with pytest.raises(ValueError, match="comparability_rule"):
        validate_f5_policy(F5Policy(comparability_rule="v2"))


def test_policy_rejects_deploy_path_a():
    with pytest.raises(ValueError, match="deploy_path_a must be False"):
        validate_f5_policy(F5Policy(deploy_path_a=True))
