"""Adversarial deterministic date, chain, and tri-state guards for F5."""
from __future__ import annotations

import itertools
import json

import pytest

from cde.diagnose import supersession as f5
from cde.diagnose.engine import ClaimSupport, SupportState, TemporalState


CLAIM = "Drug X reduces disease Y"
EVIDENCE = {
    "cited_work_id": "W1",
    "cited_meta": {"authors": ["Smith"], "cited_tier": "rct",
                   "registry_ids": ["NCT-W1"]},
    "cited_date": "2020-01-01",
    "as_of_date": "2024-02-29",
}
CITED = f5.ComparabilitySource(abstract="Drug X reduced disease Y in adults.")
CANDIDATE = f5.ComparabilitySource(abstract="Drug X did not reduce disease Y in adults.")


def _contradiction():
    return json.dumps({
        "directional_contradiction": True,
        "relation_to_cited_finding": "opposes",
        "claim_match": "match",
        "outcome_relation": "same",
        "population_relation": "equivalent",
        "cited_direction": "decrease",
        "candidate_direction": "no_effect",
        "magnitude": "reversal",
        "cited_finding_span": "Drug X reduced disease Y",
        "candidate_contradiction_span": "Drug X did not reduce disease Y",
        "confidence": 0.9,
        # Eleventh contract key (2026-08-12); "none" keeps every assertion in this
        # file pinned to what it was written to pin -- the axis is recorded, never
        # routed on.
        "scope_mismatch_axis": "none",
    })


def _seams(candidates):
    calls = {name: 0 for name in ("retrieve", "fetch", "notice", "tier", "attest", "judge")}

    def retrieve(*_args, **_kwargs):
        calls["retrieve"] += 1
        return f5.RetrievalResult(tuple(candidates), "adequate", "ok", "query")

    def fetch(work_id, **_kwargs):
        calls["fetch"] += 1
        return CITED if work_id == "W1" else CANDIDATE

    def notice(*_args, **_kwargs):
        calls["notice"] += 1
        return f5.NoticeStatus(
            lookup_status="ok", source_role="no_notice_type")

    def tier(meta):
        calls["tier"] += 1
        return f5.EvidenceTier(meta.get("tier_hint", meta.get("cited_tier")))

    def attest(*_args, **_kwargs):
        calls["attest"] += 1
        return None

    def judge(*_args, **_kwargs):
        calls["judge"] += 1
        return _contradiction()

    return {
        "retrieve_superseding_candidates": retrieve,
        "fetch_comparability_source": fetch,
        "check_formal_notice": notice,
        "classify_evidence_tier": tier,
        "find_supersession_attestation": attest,
        "judge_contradiction": judge,
    }, calls


def _run(candidates, support=None):
    seams, calls = _seams(candidates)
    if support is None:
        support = (ClaimSupport(0, SupportState.SUPPORTED),)
    temporal, records = f5.decide_f5(
        (CLAIM,), support, EVIDENCE, policy=f5.F5Policy(), **seams)
    return temporal, records, calls


def _malformed_contradictions():
    valid = _contradiction()
    return [
        "", " \n", "\ufeff" + valid, "```json\n" + valid + "\n```",
        "answer: " + valid, valid[:-4], valid.encode("utf-8"), "[]",
    ]


@pytest.mark.parametrize("raw", _malformed_contradictions())
def test_f5_contradiction_parser_rejects_malformed_or_wrapped_output(raw):
    with pytest.raises(ValueError):
        f5._parse_contradiction(raw)


def test_qualifying_version_chain_selection_is_input_order_independent():
    candidates = (
        f5.CandidateWork("W4", pub_date="2023-01-01", authors=("Jones",),
                         tier_hint="rct", registry_ids=("NCT-W4",),
                         demonstrably_distinct_from=("W1",)),
        f5.CandidateWork("W3", pub_date="2023-06-01", authors=("Jones",),
                         tier_hint="systematic_review_or_meta_analysis",
                         registry_ids=("NCT-W3",),
                         demonstrably_distinct_from=("W1",)),
        f5.CandidateWork("W2", pub_date="2023-06-01", authors=("Jones",),
                         tier_hint="systematic_review_or_meta_analysis",
                         registry_ids=("NCT-W2",),
                         demonstrably_distinct_from=("W1",)),
    )
    for order in itertools.permutations(candidates):
        temporal, records, calls = _run(order)
        assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
        assert temporal.newer_work_id == "W2"
        assert records[0]["selected_contradiction_work_id"] == "W2"
        assert calls["judge"] == 3


def test_identical_version_chain_replays_byte_identically():
    candidates = (
        f5.CandidateWork("W2", pub_date="2023-01-01", authors=("Jones",), tier_hint="rct"),
        f5.CandidateWork("W3", pub_date="2022-01-01", authors=("Jones",), tier_hint="rct"),
    )
    first = _run(candidates)[:2]
    second = _run(candidates)[:2]
    assert first == second


@pytest.mark.parametrize(
    "candidate_date,expected_state,judge_calls",
    [
        ("2020-01-01", TemporalState.NO_QUALIFYING_CONTRADICTION, 0),
        ("2019-12-31", TemporalState.NO_QUALIFYING_CONTRADICTION, 0),
        ("2024-03-01", TemporalState.NO_QUALIFYING_CONTRADICTION, 0),
        ("2024-02-29", TemporalState.QUALIFYING_CONTRADICTION, 1),
    ],
)
def test_date_window_boundaries_short_circuit_deterministically(
        candidate_date, expected_state, judge_calls):
    candidate = f5.CandidateWork(
        "W2", pub_date=candidate_date, authors=("Jones",), tier_hint="rct",
        registry_ids=("NCT-W2",), demonstrably_distinct_from=("W1",))
    temporal, _records, calls = _run((candidate,))
    assert temporal.state is expected_state
    assert calls["judge"] == judge_calls


@pytest.mark.parametrize("state", [
    SupportState.UNESTABLISHED,
    SupportState.UNJUDGEABLE,
    SupportState.WEAKER_STRENGTH,
])
def test_non_supported_tristates_never_reach_f5_seams(state):
    temporal, records, calls = _run(
        (f5.CandidateWork("W2", pub_date="2023-01-01"),),
        (ClaimSupport(0, state),),
    )
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert records == ({"claim_index": 0, "assessed": False},)
    assert all(count == 0 for count in calls.values())
