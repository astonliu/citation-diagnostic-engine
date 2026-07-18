"""Offline acceptance tests for the F3 provenance discriminator.

No network, no model: call_llm / fetch_reflist / fetch_abstract are all injected
stubs. Covers the spec's acceptance matrix, the fail-closed parsing, the
conservative ADVISOR-LOCK posture, and the two end-to-end evaluate_judgment rows.
"""
from __future__ import annotations

import json

import pytest

from .f3_provenance import (
    DEFAULT_F3_POLICY,
    F3Policy,
    make_provenance_assessor,
)
from .judgment_engine import (
    ClaimSupport,
    DecisionStatus,
    ProvenanceAssessment,
    ProvenanceState,
    SupportState,
    TemporalAssessment,
    TemporalState,
    evaluate_judgment,
)

CLAIM = "Protein P binds receptor R"
CLAIMS = (CLAIM,)
CITED_ABSTRACT = "The cited work's abstract text."
PRIMARY_ABSTRACT = "The primary work's abstract text."

# A reflist result shaped exactly like ncbi_meta.ncbi_pmc_reflist's return:
# (provenance_candidates, review_fulltext_available).
REFLIST = (
    [
        {"title": "Primary paper on P and R", "claimed_pmid": "111", "year": "2001"},
        {"title": "Unrelated method paper", "claimed_pmid": "222", "year": "1999"},
    ],
    True,
)


# --------------------------------------------------------------------------
# Stub builders. Each LLM stub is keyed on a marker present only in the prompt
# for a given verification step (V2/V3/V4), so one call_llm serves all three.
# --------------------------------------------------------------------------
def _j(obj: dict) -> str:
    return json.dumps(obj)


def make_call_llm(*, v2=None, v3=None, v4=None):
    """Build a call_llm that dispatches on which prompt it received."""
    def call_llm(prompt: str) -> str:
        if "CANDIDATE PRIMARY ABSTRACT" in prompt:      # V4 loop-close
            assert v4 is not None, "unexpected V4 call"
            return v4
        if "CANDIDATE REFERENCES" in prompt:            # V3 select
            assert v3 is not None, "unexpected V3 call"
            return v3
        if "CITED-WORK ABSTRACT" in prompt:             # V2 origin
            assert v2 is not None, "unexpected V2 call"
            return v2
        raise AssertionError("prompt matched no known verification step")
    return call_llm


def v2_json(verdict, span="", rationale="r"):
    return _j({"verdict": verdict, "evidence_span": span, "rationale": rationale})


def v3_json(index, rationale="r"):
    return _j({"selected_index": index, "rationale": rationale})


def v4_json(contains, span="", rationale="r"):
    return _j({"contains_finding": contains, "evidence_span": span, "rationale": rationale})


def reflist_ok(_pmcid):
    return REFLIST


def reflist_empty(_pmcid):
    return ([], True)


def reflist_none(_pmcid):
    return (None, None)


def abstract_for(mapping):
    def fetch(_id):
        return mapping.get(_id)
    return fetch


def build(*, call_llm, fetch_reflist=reflist_ok, cited_pmid="900",
          cited_pmcid="PMC900", cited_is_review=None, policy=DEFAULT_F3_POLICY,
          abstracts=None):
    if abstracts is None:
        abstracts = {"900": CITED_ABSTRACT, "111": PRIMARY_ABSTRACT}
    return make_provenance_assessor(
        call_llm=call_llm,
        fetch_reflist=fetch_reflist,
        fetch_abstract=abstract_for(abstracts),
        cited_pmid=cited_pmid,
        cited_pmcid=cited_pmcid,
        cited_is_review=cited_is_review,
        policy=policy,
    )


def support_all_supported(n=1):
    return tuple(ClaimSupport(i, SupportState.SUPPORTED) for i in range(n))


# --------------------------------------------------------------------------
# Acceptance matrix.
# --------------------------------------------------------------------------
def test_originates_is_proper_origin_without_reflist_call():
    # V2=originate -> PROPER_ORIGIN; no reflist call needed.
    called = {"reflist": 0}

    def counting_reflist(_pmcid):
        called["reflist"] += 1
        return REFLIST

    assessor = build(
        call_llm=make_call_llm(v2=v2_json("originates", "we report P binds R")),
        fetch_reflist=counting_reflist,
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.PROPER_ORIGIN
    assert called["reflist"] == 0
    assert "f3_v2_origin_v1" in result.rationale


def test_not_origin_sensitive_is_proper_origin_never_f3():
    # A claim whose meaning does not depend on origin cannot be F3; its
    # provenance is proper by construction, so ACCURATE stays reachable.
    assessor = build(call_llm=make_call_llm(v2=v2_json("not_origin_sensitive")))
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.PROPER_ORIGIN


def test_unclear_is_unjudgeable_never_f3():
    assessor = build(call_llm=make_call_llm(v2=v2_json("unclear")))
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_restatement_primary_in_reflist_v4_contains_is_confirmed():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(0),
            v4=v4_json(True, "here we demonstrate P binds R"),
        )
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.MISATTRIBUTED_CONFIRMED
    assert len(result.origin_chain) >= 2
    assert result.origin_chain == ("PMID:900", "PMID:111")
    assert result.evidence_spans == (
        "as first shown by [1]",
        "here we demonstrate P binds R",
    )


def test_restatement_no_defensible_primary_is_unjudgeable():
    # V3 returns null: no listed reference plausibly originates the finding.
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(None),
        )
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_restatement_candidate_found_but_v4_lacks_finding_is_unjudgeable():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(0),
            v4=v4_json(False),
        )
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_v4_contains_but_blank_span_cannot_confirm():
    # contains_finding true but no originating span -> cannot build a real
    # evidence span -> hold rather than fabricate.
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(0),
            v4=v4_json(True, ""),
        )
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_restatement_blank_v2_span_cannot_anchor_chain():
    # A restatement verdict with no anchoring span must hold, not fabricate.
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("restatement", "")),
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_empty_reflist_is_unjudgeable_no_exception():
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("restatement", "as first shown by [1]")),
        fetch_reflist=reflist_empty,
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_none_reflist_is_unjudgeable_no_exception():
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("restatement", "as first shown by [1]")),
        fetch_reflist=reflist_none,
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_unresolved_cited_pmcid_is_unjudgeable_no_exception():
    # cited PMCID unresolved -> cannot fetch reflist -> hold (not F3, no raise).
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("restatement", "as first shown by [1]")),
        cited_pmcid=None,
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_primary_abstract_missing_is_unjudgeable_no_exception():
    # V3 selects candidate 0 (pmid 111) but its abstract is not fetchable.
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(0),
        ),
        abstracts={"900": CITED_ABSTRACT},  # no 111
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_cited_abstract_missing_is_unjudgeable_no_exception():
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("originates")),
        abstracts={},  # no cited abstract
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


# --------------------------------------------------------------------------
# Fail-closed parsing: malformed MODEL output raises ValueError.
# --------------------------------------------------------------------------
def _run_v2(raw_text):
    assessor = build(call_llm=lambda _p: raw_text)
    return assessor(CLAIMS, support_all_supported())


def test_malformed_fenced_json_raises():
    with pytest.raises(ValueError):
        _run_v2('```json\n{"verdict": "originates", "evidence_span": "", "rationale": "r"}\n```')


def test_malformed_extra_key_raises():
    with pytest.raises(ValueError):
        _run_v2(_j({"verdict": "originates", "evidence_span": "", "rationale": "r", "extra": 1}))


def test_malformed_duplicate_key_raises():
    with pytest.raises(ValueError):
        _run_v2('{"verdict": "originates", "verdict": "restatement", "evidence_span": "", "rationale": "r"}')


def test_malformed_unknown_verdict_raises():
    with pytest.raises(ValueError):
        _run_v2(v2_json("maybe"))


def test_malformed_v4_nonbool_contains_raises():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(0),
            v4=_j({"contains_finding": "true", "evidence_span": "s", "rationale": "r"}),
        )
    )
    with pytest.raises(ValueError):
        assessor(CLAIMS, support_all_supported())


def test_malformed_v3_out_of_range_index_raises():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(9),  # only 2 candidates
        )
    )
    with pytest.raises(ValueError):
        assessor(CLAIMS, support_all_supported())


def test_malformed_v3_bool_index_raises():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3='{"selected_index": true, "rationale": "r"}',
        )
    )
    with pytest.raises(ValueError):
        assessor(CLAIMS, support_all_supported())


# --------------------------------------------------------------------------
# Conservative-policy gates.
# --------------------------------------------------------------------------
def test_zero_hop_budget_holds_restatement():
    policy = F3Policy(max_hop_count=0)
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("restatement", "as first shown by [1]")),
        policy=policy,
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_trace_sources_disabled_holds_restatement():
    policy = F3Policy(trace_sources=())
    assessor = build(
        call_llm=make_call_llm(v2=v2_json("restatement", "as first shown by [1]")),
        policy=policy,
    )
    result = assessor(CLAIMS, support_all_supported())
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_policy_and_prompt_versions_are_stamped():
    assessor = build(call_llm=make_call_llm(v2=v2_json("unclear")))
    result = assessor(CLAIMS, support_all_supported())
    assert "f3_v2_origin_v1" in result.rationale
    assert "max_hop_count=1" in result.rationale
    assert "unresolved_state=UNJUDGEABLE" in result.rationale


def test_multi_claim_originates_and_restatement_prefers_confirmed():
    two = ("Protein P binds receptor R", "Protein P activates pathway Q")
    # First claim originates; second is a confirmed restatement. F3 dominates.
    calls = {"n": 0}

    def call_llm(prompt):
        if "CITED-WORK ABSTRACT" in prompt and "CANDIDATE" not in prompt:
            calls["n"] += 1
            # claim 0 originates, claim 1 restates
            if two[0] in prompt:
                return v2_json("originates", "we report P binds R")
            return v2_json("restatement", "as first shown by [2]")
        if "CANDIDATE REFERENCES" in prompt:
            return v3_json(0)
        if "CANDIDATE PRIMARY ABSTRACT" in prompt:
            return v4_json(True, "here we demonstrate P activates Q")
        raise AssertionError("unexpected prompt")

    assessor = build(call_llm=call_llm)
    result = assessor(two, support_all_supported(2))
    assert result.state is ProvenanceState.MISATTRIBUTED_CONFIRMED


def test_multi_claim_originates_and_unclear_holds():
    two = ("Protein P binds receptor R", "Protein P activates pathway Q")

    def call_llm(prompt):
        if two[0] in prompt:
            return v2_json("originates", "we report P binds R")
        return v2_json("unclear")

    assessor = build(call_llm=call_llm)
    result = assessor(two, support_all_supported(2))
    assert result.state is ProvenanceState.UNJUDGEABLE


def test_mixed_originates_and_not_origin_sensitive_is_proper_origin():
    # One claim originates in the cited work, one is not origin-sensitive:
    # neither is a misattribution risk -> PROPER_ORIGIN.
    two = ("Protein P binds receptor R", "Protein P activates pathway Q")

    def call_llm(prompt):
        if two[0] in prompt:
            return v2_json("originates", "we report P binds R")
        return v2_json("not_origin_sensitive")

    assessor = build(call_llm=call_llm)
    result = assessor(two, support_all_supported(2))
    assert result.state is ProvenanceState.PROPER_ORIGIN


def test_unclear_dominates_not_origin_sensitive_holds():
    # A genuine unknown holds the pair even when another claim is not
    # origin-sensitive.
    two = ("Protein P binds receptor R", "Protein P activates pathway Q")

    def call_llm(prompt):
        if two[0] in prompt:
            return v2_json("not_origin_sensitive")
        return v2_json("unclear")

    assessor = build(call_llm=call_llm)
    result = assessor(two, support_all_supported(2))
    assert result.state is ProvenanceState.UNJUDGEABLE


# --------------------------------------------------------------------------
# End-to-end through evaluate_judgment (the two matrix rows).
# --------------------------------------------------------------------------
def _support_assessor(n):
    def support_assessor(claims):
        return tuple(ClaimSupport(i, SupportState.SUPPORTED) for i in range(len(claims)))
    return support_assessor


def _entity_assessor(claims, support_rows):
    return ()


def _temporal_assessor(claims, support_rows):
    return TemporalAssessment(TemporalState.NO_QUALIFYING_CONTRADICTION)


def test_end_to_end_restatement_path_yields_f3():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(0),
            v4=v4_json(True, "here we demonstrate P binds R"),
        )
    )
    result = evaluate_judgment(
        preband_cleared=True,
        claims=CLAIMS,
        support_assessor=_support_assessor(1),
        entity_assessor=_entity_assessor,
        provenance_assessor=assessor,
        temporal_assessor=_temporal_assessor,
    )
    assert result.status is DecisionStatus.TERMINAL
    assert result.primary_label == "F3"
    assert result.findings == ("F3",)
    assert result.provenance.state is ProvenanceState.MISATTRIBUTED_CONFIRMED


def test_end_to_end_unresolved_path_is_held_not_f3():
    assessor = build(
        call_llm=make_call_llm(
            v2=v2_json("restatement", "as first shown by [1]"),
            v3=v3_json(None),
        )
    )
    result = evaluate_judgment(
        preband_cleared=True,
        claims=CLAIMS,
        support_assessor=_support_assessor(1),
        entity_assessor=_entity_assessor,
        provenance_assessor=assessor,
        temporal_assessor=_temporal_assessor,
    )
    assert result.status is DecisionStatus.HELD_UNJUDGEABLE
    assert "provenance is unjudgeable" in result.hold_reasons
    assert result.primary_label is None


def test_end_to_end_proper_origin_is_accurate():
    assessor = build(call_llm=make_call_llm(v2=v2_json("originates", "we report it")))
    result = evaluate_judgment(
        preband_cleared=True,
        claims=CLAIMS,
        support_assessor=_support_assessor(1),
        entity_assessor=_entity_assessor,
        provenance_assessor=assessor,
        temporal_assessor=_temporal_assessor,
    )
    assert result.status is DecisionStatus.TERMINAL
    assert result.primary_label == "accurate"
    assert result.findings == ()
