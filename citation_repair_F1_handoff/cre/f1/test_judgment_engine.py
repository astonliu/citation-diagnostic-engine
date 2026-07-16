from __future__ import annotations

import pytest

from .judgment_engine import (
    ClaimSupport,
    DecisionStatus,
    DiscriminatorContractError,
    EntityAssessment,
    EntityState,
    ProvenanceAssessment,
    ProvenanceState,
    SupportState,
    TemporalAssessment,
    TemporalState,
    decide_judgment,
    evaluate_judgment,
    from_legacy_coverage,
)


CLAIMS = ("Drug X reduces outcome Y", "Drug X improves outcome Z")
NO_F5 = TemporalAssessment(TemporalState.NO_QUALIFYING_CONTRADICTION)
PROPER = ProvenanceAssessment(ProvenanceState.PROPER_ORIGIN)


def support(*states: SupportState) -> tuple[ClaimSupport, ...]:
    return tuple(ClaimSupport(i, state) for i, state in enumerate(states))


def decide(
    states: tuple[SupportState, ...],
    *,
    entities=(),
    provenance=None,
    temporal=NO_F5,
):
    if provenance is None and all(state is SupportState.SUPPORTED for state in states):
        provenance = PROPER
    return decide_judgment(
        preband_cleared=True,
        claims=CLAIMS[: len(states)],
        claim_support=support(*states),
        entity_assessments=entities,
        provenance=provenance,
        temporal=temporal,
    )


def test_legacy_coverage_maps_exact_tristate_only() -> None:
    rows = from_legacy_coverage(
        CLAIMS,
        [
            {"established": True, "evidence_span": "span"},
            {"established": False},
        ],
    )
    assert [row.state for row in rows] == [
        SupportState.SUPPORTED,
        SupportState.UNESTABLISHED,
    ]
    unknown = from_legacy_coverage(CLAIMS[:1], [{"established": None}])
    assert unknown[0].state is SupportState.UNJUDGEABLE
    with pytest.raises(DiscriminatorContractError, match="exact True"):
        from_legacy_coverage(CLAIMS[:1], [{"established": "false"}])


def test_f6_precedes_f4_but_preserves_both_findings() -> None:
    result = decide(
        (SupportState.WEAKER_STRENGTH, SupportState.UNESTABLISHED)
    )
    assert result.status is DecisionStatus.TERMINAL
    assert result.primary_label == "F6"
    assert result.findings == ("F6", "F4")


def test_confirmed_entity_substitution_derives_f7_before_f6() -> None:
    entity = EntityAssessment(
        0,
        EntityState.DIFFERENT_ENTITY_SUPPORTED,
        claimed_entity_key="RXNORM:1",
        evidence_entity_key="RXNORM:2",
        relation_supported=True,
    )
    result = decide(
        (SupportState.UNESTABLISHED, SupportState.SUPPORTED),
        entities=(entity,),
    )
    assert result.primary_label == "F7"
    assert result.findings == ("F7", "F6")


def test_identifier_mismatch_without_supported_relation_is_rejected() -> None:
    with pytest.raises(DiscriminatorContractError, match="relation is supported"):
        EntityAssessment(
            0,
            EntityState.DIFFERENT_ENTITY_SUPPORTED,
            claimed_entity_key="HGNC:1",
            evidence_entity_key="HGNC:2",
            relation_supported=False,
        )


def test_full_support_proper_origin_is_accurate() -> None:
    result = decide((SupportState.SUPPORTED, SupportState.SUPPORTED))
    assert result.status is DecisionStatus.TERMINAL
    assert result.primary_label == "accurate"
    assert result.findings == ()


def test_confirmed_origin_chain_derives_f3() -> None:
    provenance = ProvenanceAssessment(
        ProvenanceState.MISATTRIBUTED_CONFIRMED,
        origin_chain=("PMID:review", "PMID:primary"),
        evidence_spans=("review cites primary", "primary reports finding"),
    )
    result = decide(
        (SupportState.SUPPORTED, SupportState.SUPPORTED),
        provenance=provenance,
    )
    assert result.primary_label == "F3"
    assert result.findings == ("F3",)


def test_f3_without_origin_chain_is_rejected() -> None:
    with pytest.raises(DiscriminatorContractError, match="origin chain"):
        ProvenanceAssessment(ProvenanceState.MISATTRIBUTED_CONFIRMED)


def test_unresolved_provenance_is_held() -> None:
    result = decide(
        (SupportState.SUPPORTED, SupportState.SUPPORTED),
        provenance=ProvenanceAssessment(ProvenanceState.UNJUDGEABLE),
    )
    assert result.status is DecisionStatus.HELD_UNJUDGEABLE
    assert result.primary_label is None
    assert "provenance is unjudgeable" in result.hold_reasons


def test_qualifying_temporal_evidence_derives_f5() -> None:
    temporal = TemporalAssessment(
        TemporalState.QUALIFYING_CONTRADICTION,
        claim_index=0,
        newer_work_id="PMID:newer",
        same_claim_or_outcome=True,
        comparable_population=True,
        f8_notice=False,
        evidence_spans=("newer paper contradicts the supported result",),
    )
    result = decide(
        (SupportState.SUPPORTED, SupportState.SUPPORTED),
        temporal=temporal,
    )
    assert result.primary_label == "F5"
    assert result.findings == ("F5",)


def test_f5_does_not_erase_an_evidence_fault() -> None:
    temporal = TemporalAssessment(
        TemporalState.QUALIFYING_CONTRADICTION,
        claim_index=1,
        newer_work_id="PMID:newer",
        same_claim_or_outcome=True,
        comparable_population=True,
        f8_notice=False,
        evidence_spans=("contradiction",),
    )
    result = decide(
        (SupportState.UNESTABLISHED, SupportState.SUPPORTED),
        temporal=temporal,
    )
    assert result.primary_label == "F6"
    assert result.findings == ("F6", "F5")


def test_f5_cannot_target_an_unsupported_claim() -> None:
    temporal = TemporalAssessment(
        TemporalState.QUALIFYING_CONTRADICTION,
        claim_index=0,
        newer_work_id="PMID:newer",
        same_claim_or_outcome=True,
        comparable_population=True,
        f8_notice=False,
        evidence_spans=("contradiction",),
    )
    with pytest.raises(DiscriminatorContractError, match="paper supported"):
        decide((SupportState.UNESTABLISHED,), temporal=temporal)


def test_any_unknown_evidence_holds_without_erasing_findings() -> None:
    result = decide(
        (SupportState.UNESTABLISHED, SupportState.UNJUDGEABLE)
    )
    assert result.status is DecisionStatus.HELD_UNJUDGEABLE
    assert result.primary_label is None
    assert result.findings == ("F6",)


def test_uncleared_preband_item_is_outside_band() -> None:
    result = decide_judgment(
        preband_cleared=False,
        claims=CLAIMS[:1],
        claim_support=support(SupportState.SUPPORTED),
        provenance=PROPER,
        temporal=NO_F5,
    )
    assert result.status is DecisionStatus.OUTSIDE_BAND
    assert result.primary_label is None


def test_support_rows_must_match_claim_order_and_length() -> None:
    with pytest.raises(DiscriminatorContractError, match="one row per claim"):
        decide_judgment(
            preband_cleared=True,
            claims=CLAIMS,
            claim_support=support(SupportState.SUPPORTED),
            provenance=None,
            temporal=NO_F5,
        )
    with pytest.raises(DiscriminatorContractError, match="contiguous indices"):
        decide_judgment(
            preband_cleared=True,
            claims=CLAIMS,
            claim_support=(
                ClaimSupport(1, SupportState.SUPPORTED),
                ClaimSupport(0, SupportState.SUPPORTED),
            ),
            provenance=PROPER,
            temporal=NO_F5,
        )


def test_string_booleans_are_rejected() -> None:
    with pytest.raises(DiscriminatorContractError, match="exact bool"):
        TemporalAssessment(
            TemporalState.QUALIFYING_CONTRADICTION,
            claim_index=0,
            newer_work_id="PMID:newer",
            same_claim_or_outcome="true",
            comparable_population=True,
            f8_notice=False,
            evidence_spans=("evidence",),
        )


def test_evaluate_calls_provenance_only_after_full_support() -> None:
    calls = []

    def support_assessor(claims):
        return support(SupportState.UNESTABLISHED, SupportState.SUPPORTED)

    def entity_assessor(claims, support_rows):
        return ()

    def provenance_assessor(claims, support_rows):
        calls.append("provenance")
        return PROPER

    def temporal_assessor(claims, support_rows):
        return NO_F5

    result = evaluate_judgment(
        preband_cleared=True,
        claims=CLAIMS,
        support_assessor=support_assessor,
        entity_assessor=entity_assessor,
        provenance_assessor=provenance_assessor,
        temporal_assessor=temporal_assessor,
    )
    assert result.primary_label == "F6"
    assert calls == []


def test_assessor_dicts_are_not_permissively_coerced() -> None:
    with pytest.raises(DiscriminatorContractError, match="ClaimSupport"):
        evaluate_judgment(
            preband_cleared=True,
            claims=CLAIMS[:1],
            support_assessor=lambda claims: [
                {"claim_index": 0, "state": "SUPPORTED"}
            ],
            entity_assessor=lambda claims, support_rows: (),
            provenance_assessor=lambda claims, support_rows: PROPER,
            temporal_assessor=lambda claims, support_rows: NO_F5,
        )


def test_legacy_null_rationale_normalizes_to_empty() -> None:
    """An explicit-null legacy rationale is a log-only value; it normalizes to ""
    (parallel to a null evidence_span) instead of rejecting the row (finding #7).
    A non-string, non-null rationale is still malformed."""
    rows = from_legacy_coverage(CLAIMS[:1], [{"established": True, "rationale": None}])
    assert rows[0].state is SupportState.SUPPORTED
    assert rows[0].rationale == ""
    # null evidence_span was already tolerated -- confirm the symmetry holds.
    spanless = from_legacy_coverage(
        CLAIMS[:1], [{"established": True, "evidence_span": None}])
    assert spanless[0].evidence_spans == ()
    with pytest.raises(DiscriminatorContractError, match="string or null"):
        from_legacy_coverage(CLAIMS[:1], [{"established": True, "rationale": 5}])


@pytest.mark.parametrize("bad", [None, 42, object()])
def test_non_iterable_assessor_output_raises_contract_error(bad) -> None:
    """A non-iterable assessor / legacy-verdicts return must surface as
    DiscriminatorContractError, not a raw TypeError that escapes callers'
    `except DiscriminatorContractError` handling (finding #8)."""
    with pytest.raises(DiscriminatorContractError, match="sequence"):
        evaluate_judgment(
            preband_cleared=True, claims=CLAIMS[:1],
            support_assessor=lambda claims: bad,
            entity_assessor=lambda claims, support_rows: (),
            provenance_assessor=lambda claims, support_rows: None,
            temporal_assessor=lambda claims, support_rows: NO_F5)
    with pytest.raises(DiscriminatorContractError, match="sequence"):
        evaluate_judgment(
            preband_cleared=True, claims=CLAIMS[:1],
            support_assessor=lambda claims: support(SupportState.SUPPORTED),
            entity_assessor=lambda claims, support_rows: bad,
            provenance_assessor=lambda claims, support_rows: PROPER,
            temporal_assessor=lambda claims, support_rows: NO_F5)
    with pytest.raises(DiscriminatorContractError, match="sequence"):
        from_legacy_coverage(CLAIMS[:1], bad)


def test_temporal_assessor_none_is_a_contract_error_not_a_silent_hold() -> None:
    """Distinguish an intentionally-omitted temporal assessment from an assessor
    that incorrectly returns None (finding #9). Through evaluate_judgment a None
    return is malformed and raises; a DIRECT decide_judgment caller may still
    pass temporal=None to signal an intentional omission (-> held)."""
    with pytest.raises(DiscriminatorContractError, match="TemporalAssessment"):
        evaluate_judgment(
            preband_cleared=True, claims=CLAIMS[:1],
            support_assessor=lambda claims: support(SupportState.SUPPORTED),
            entity_assessor=lambda claims, support_rows: (),
            provenance_assessor=lambda claims, support_rows: PROPER,
            temporal_assessor=lambda claims, support_rows: None)
    # decide_judgment still treats an explicit temporal=None as an intentional
    # omission -> held (unchanged behavior for direct callers).
    held = decide_judgment(
        preband_cleared=True, claims=CLAIMS[:1],
        claim_support=support(SupportState.SUPPORTED),
        provenance=PROPER, temporal=None)
    assert held.status is DecisionStatus.HELD_UNJUDGEABLE
    assert "temporal assessment missing" in held.hold_reasons


@pytest.mark.parametrize("bad_provenance", [None, "PROPER", 0])
def test_provenance_assessor_bad_shape_under_full_support_raises(bad_provenance):
    """Symmetric with the temporal guard (decision 3): when all claims are
    supported, the provenance assessor is invoked and must return a
    ProvenanceAssessment; None or another shape is malformed assessor output and
    raises through evaluate_judgment."""
    with pytest.raises(DiscriminatorContractError, match="ProvenanceAssessment"):
        evaluate_judgment(
            preband_cleared=True, claims=CLAIMS[:1],
            support_assessor=lambda claims: support(SupportState.SUPPORTED),
            entity_assessor=lambda claims, support_rows: (),
            provenance_assessor=lambda claims, support_rows: bad_provenance,
            temporal_assessor=lambda claims, support_rows: NO_F5)


def test_direct_decide_judgment_provenance_none_is_intentional_hold():
    """A DIRECT decide_judgment(provenance=None) under full support is an
    intentional omission -> HELD (decision 3); only the assessor path treats a
    None as malformed."""
    held = decide_judgment(
        preband_cleared=True, claims=CLAIMS[:1],
        claim_support=support(SupportState.SUPPORTED),
        provenance=None, temporal=NO_F5)
    assert held.status is DecisionStatus.HELD_UNJUDGEABLE
    assert "provenance assessment missing after full support" in held.hold_reasons
