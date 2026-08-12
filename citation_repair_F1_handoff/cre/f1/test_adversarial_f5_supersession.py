"""Adversarial deterministic-boundary tests for F5 supersession."""
from __future__ import annotations

import json

import pytest

from cre.f1 import f5_supersession as f5
from cre.f1.judgment_engine import ClaimSupport, SupportState


def _contradiction():
    return json.dumps({"directional_contradiction": True, "claim_match": "match",
                       "outcome_relation": "same", "population_relation": "equivalent",
                       "cited_direction": "down", "candidate_direction": "up", "magnitude": "reversal",
                       "cited_finding_span": "cited", "candidate_contradiction_span": "candidate",
                       "confidence": 0.9, "scope_mismatch_axis": "none"})


@pytest.mark.parametrize("raw", [_contradiction() + " trailing", _contradiction() + _contradiction()])
def test_contradiction_transport_rejects_extra_data(raw):
    with pytest.raises(ValueError, match="not one bare JSON object"):
        f5._parse_contradiction(raw)


@pytest.mark.parametrize("date", ["2024-02-30", "2024-13-01", "2024-01-01T00:00:00", "", "not-a-date"])
def test_dates_fail_closed_without_normalization(date):
    with pytest.raises(ValueError):
        f5._parse_date(date, "adversarial")


def test_date_gap_and_comparability_are_deterministic_at_boundaries():
    assert f5._date_gap_years("2020-02-29", "2021-02-28") == f5._date_gap_years("2020-02-29", "2021-02-28")
    assert f5.derive_comparability_decision("mismatch", "uncertain", "unclear") == "not_comparable"


def test_candidate_constructor_rejects_invalid_version_date():
    with pytest.raises(ValueError):
        f5.CandidateWork(id="x", pub_date="2020-00-01")


def test_f5_invokes_the_injected_contradiction_judgment_for_eligible_candidate():
    """This is deliberately a code-path, not a source-text assertion: F5 has
    deterministic gates but its candidate conclusion still depends on this seam."""
    cited = f5.ComparabilitySource(abstract="cited finding")
    candidate = f5.ComparabilitySource(abstract="candidate contradiction")
    calls = []
    temporal, _ = f5.decide_f5(
        ("claim",), (ClaimSupport(0, SupportState.SUPPORTED),),
        {"cited_work_id": "W1", "cited_meta": {"authors": ["A"], "cited_tier": "rct"},
         "cited_date": "2020-01-01", "as_of_date": "2024-01-01"},
        retrieve_superseding_candidates=lambda *a, **k: f5.RetrievalResult(
            (f5.CandidateWork(id="W2", pub_date="2021-01-01", authors=("B",), tier_hint="rct"),),
            "adequate", "ok"),
        fetch_comparability_source=lambda work_id, **k: cited if work_id == "W1" else candidate,
        check_formal_notice=lambda *a, **k: f5.NoticeStatus(),
        classify_evidence_tier=lambda meta: f5.EvidenceTier(meta.get("tier_hint", meta.get("cited_tier"))),
        find_supersession_attestation=lambda *a, **k: None,
        judge_contradiction=lambda *a, **k: calls.append(True) or _contradiction(),
        policy=f5.F5Policy())
    assert calls == [True]
    assert temporal.state.value == "QUALIFYING_CONTRADICTION"


# FIXED 2026-08-12: _parse_date now gates on an explicit ^\\d{4}-\\d{2}-\\d{2}$
# before delegating, so basic-format and ISO week dates are rejected.
@pytest.mark.parametrize("date", ["20240101", "2024-W01-1"])
def test_date_contract_rejects_non_yyyy_mm_dd_iso_forms(date):
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        f5._parse_date(date, "adversarial")


# FIXED 2026-08-12: NoticeStatus.__post_init__ now PARSES the date, not just its type.
def test_notice_status_rejects_invalid_date_text():
    with pytest.raises(ValueError):
        f5.NoticeStatus(notice_kind="correction", notice_resolution="flagged",
                        date="not-a-date")


# FIXED 2026-08-12: RetrievalResult.__post_init__ rejects duplicate candidate ids.
def test_version_chain_rejects_duplicate_candidate_work_ids():
    candidates = (
        f5.CandidateWork(id="W2", pub_date="2021-01-01"),
        f5.CandidateWork(id="W2", pub_date="2022-01-01"),
    )
    with pytest.raises(ValueError, match="duplicate"):
        f5.RetrievalResult(candidates, "adequate", "ok")
