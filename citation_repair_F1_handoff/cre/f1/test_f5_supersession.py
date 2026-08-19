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

EVIDENCE = {
    "cited_work_id": "W1",
    "cited_meta": {"authors": ["Smith"], "cited_tier": "rct"},
    "cited_date": "2010-01-01",
    "as_of_date": "2024-01-01",
}


# --------------------------------------------------------------------------
# Builders.
# --------------------------------------------------------------------------
def contradiction_json(
    *,
    directional_contradiction=True,
    claim_match="match",
    outcome_relation="same",
    population_relation="equivalent",
    cited_direction="reduces",
    candidate_direction="no effect / harm",
    magnitude="directional reversal",
    cited_finding_span=CITED_FINDING_SPAN,
    candidate_contradiction_span=CAND_CONTRA_SPAN,
    confidence=0.9,
    # Eleventh key (2026-08-12). Defaults to "none" so every existing fixture keeps
    # asserting what it was written to assert: the axis is RECORDED, never routed
    # on, so adding it must not move any decision these tests pin.
    scope_mismatch_axis="none",
):
    return json.dumps({
        "directional_contradiction": directional_contradiction,
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


def candidate(work_id="W2", *, pub_date="2020-01-01", authors=("Jones",),
              tier_hint="rct"):
    return CandidateWork(id=work_id, pub_date=pub_date, authors=tuple(authors),
                         tier_hint=tier_hint)


def make_seams(
    *,
    candidates=None,
    adequacy="adequate",
    status="ok",
    contradiction=None,
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
        cited_notice = NoticeStatus()
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
        return candidate_notices.get(work_id, NoticeStatus())

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

    return dict(
        retrieve_superseding_candidates=retrieve,
        fetch_comparability_source=fetch,
        check_formal_notice=notice,
        classify_evidence_tier=tier,
        find_supersession_attestation=attest,
        judge_contradiction=judge,
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
    temporal, records, _ = run(candidates=(candidate(authors=()),))
    assert temporal.state is TemporalState.UNJUDGEABLE
    cand = records[0]["candidate_assessments"][0]
    assert cand["independent"] == "unknown"


def test_author_overlap_open_combinator_holds():
    # Shared author, no confirmed same cohort -> the OPEN Lock-D cell -> unknown.
    temporal, records, _ = run(candidates=(candidate(authors=("Smith",)),))
    assert temporal.state is TemporalState.UNJUDGEABLE
    cand = records[0]["candidate_assessments"][0]
    assert cand["independent"] == "unknown"
    assert cand["independence_basis"] == "author_overlap_open_combinator"


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
        contradiction=contradiction_json(directional_contradiction=False))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0]["reason"] == "all_candidates_nonqualifying"
    assert records[0]["candidate_assessments"][0]["reason"] == "not_directional_contradiction"


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
    with pytest.raises(ValueError, match="comparability_decision drifted"):
        validate_f5_record(rec, F5Policy())


def test_validate_record_detects_f5_path_drift():
    _temporal, records, _ = run()
    rec = json.loads(json.dumps(records[0]))
    rec["f5_path"] = "A"
    with pytest.raises(ValueError, match="f5_path"):
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


# --------------------------------------------------------------------------
# Edge cases (blueprint Sec 9).
# --------------------------------------------------------------------------
def test_cited_retraction_flags_upstream_inconsistency():
    temporal, records, _ = run(cited_notice=NoticeStatus(notice_kind="retraction"))
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["reason"] == "cited_retracted_upstream_f8_inconsistency"


def test_cited_correction_caps_at_path_b():
    # Cited work under an EoC: detection still fires, but Path A is capped even
    # with a bound attestation (Sec 9-21).
    temporal, records, _ = run(
        cited_notice=NoticeStatus(notice_kind="eoc"),
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
                                         candidate_direction="no significant effect"))
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records[0]["candidate_assessments"][0]["reason"] == "not_directional_contradiction"


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
    policy = F5Policy(mode="deployment", confidence_floor=None)
    temporal, records, _ = run(policy=policy)
    assert temporal.state is TemporalState.UNJUDGEABLE
    assert records[0]["candidate_assessments"][0]["reason"] == "confidence_floor_unfrozen"


def test_deployment_below_floor_holds_not_confident_negative():
    # Sec 9-22: in deployment mode low confidence HOLDS (UNJUDGEABLE), it is not a
    # confident negative (that would be the discovery-mode override, Sec 8a).
    policy = F5Policy(mode="deployment", confidence_floor=0.25)
    temporal, records, _ = run(policy=policy,
                               contradiction=contradiction_json(confidence=0.05))
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
# No reportable output is ever produced in this development-mode build.
# --------------------------------------------------------------------------
def test_records_are_never_reportable():
    for policy in (F5Policy(), F5Policy(mode="deployment", confidence_floor=0.25)):
        _temporal, records, _ = run(policy=policy,
                                    attestation=lambda wid: _attest_for(wid))
        assert records[0]["reportable"] is False
        assert records[0]["verifier_result"] == "not_run"


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
