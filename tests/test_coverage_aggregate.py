"""Offline tests for cre/f1/coverage_aggregate -- the band's TRI-STATE coverage
aggregation (the F6 abstract-scope correction, ZD 2026-07-27).

band_prompts.py is a frozen substrate (blob-OID pinned), so the tri-state mapping
lives in this downstream module instead. These tests pin the target truth table,
the tri-state judge, and -- critically -- the DIVERGENCE guard that locks the band
onto this module and away from the frozen Boolean band_prompts path."""
from __future__ import annotations

import json

import pytest

from cde.claims import band_prompts as bp
from cde.claims import aggregate as ca
from cde.claims import band as jb


def _coverage_reply(*, engages=True, contradicts=False, unconfirmed=None,
                    rationale="supported", evidence_span="span"):
    return json.dumps({
        "engages_subject": engages,
        "contradicts": contradicts,
        "unconfirmed_specifics": unconfirmed or [],
        "rationale": rationale,
        "evidence_span": evidence_span,
    })


# ==========================================================================
# The target truth table -- the single place the abstract-scoped mapping is
# pinned end-to-end, so it can never silently revert.
# ==========================================================================
def test_aggregate_coverage_tristate_truth_table():
    """Absence of evidence in an abstract is unknown (None), never a coverage
    gap; only contradiction (False) is a gap at abstract scope.

      engages | contradicts | unconfirmed | established
        True  |    False    |    []       |   True
        True  |    True      |    any      |   False
        True  |    False    |   [>=1]     |   None
        False |    False    |    []       |   None
    """
    assert ca.aggregate_coverage(True, False, []) is True
    assert ca.aggregate_coverage(True, True, []) is False
    assert ca.aggregate_coverage(True, True, ["x"]) is False     # "any" specifics
    assert ca.aggregate_coverage(True, False, ["x"]) is None
    assert ca.aggregate_coverage(False, False, []) is None
    # The engages_subject=false validation invariant still holds under tri-state.
    with pytest.raises(ValueError):
        ca.aggregate_coverage(False, True, [])
    with pytest.raises(ValueError):
        ca.aggregate_coverage(False, False, ["x"])
    # Type guards match band_prompts.aggregate_coverage.
    with pytest.raises(ValueError):
        ca.aggregate_coverage("true", False, [])
    with pytest.raises(ValueError):
        ca.aggregate_coverage(True, "false", [])
    with pytest.raises(ValueError):
        ca.aggregate_coverage(True, False, "x")


# ==========================================================================
# The tri-state coverage judge (parse the frozen prompt, then tri-state map)
# ==========================================================================
def test_tristate_judge_dict_ignores_boolean_established_and_carries_raw_fields():
    verdict = bp.parse_coverage(_coverage_reply(unconfirmed=["a mouse model"]))
    # band_prompts set the Boolean established to False; the band re-derives None.
    assert verdict.established is False
    d = ca.tristate_judge_dict(verdict)
    assert d["established"] is None
    assert d["engages_subject"] is True
    assert d["contradicts"] is False
    assert d["unconfirmed_specifics"] == ["a mouse model"]


def test_make_coverage_judge_routes_tristate_over_usable_abstract():
    replies = iter([
        _coverage_reply(),                                   # -> True
        _coverage_reply(contradicts=True),                   # -> False
        _coverage_reply(unconfirmed=["ApoE model"]),         # -> None
        _coverage_reply(engages=False, evidence_span=""),    # -> None
    ])
    judge = ca.make_coverage_judge(lambda prompt: next(replies))
    verdicts = judge(["c1", "c2", "c3", "c4"], {"cited_abstract": "An abstract."})
    assert [v["established"] for v in verdicts] == [True, False, None, None]
    # a contradiction dominates -> F6
    assert jb.route(verdicts) == jb.ROUTE_F6_FLAGGED


def test_make_coverage_judge_holds_when_only_true_and_none():
    replies = iter([
        _coverage_reply(),                                   # -> True
        _coverage_reply(unconfirmed=["mouse model"]),        # -> None
    ])
    judge = ca.make_coverage_judge(lambda prompt: next(replies))
    verdicts = judge(["c1", "c2"], {"cited_abstract": "An abstract."})
    assert [v["established"] for v in verdicts] == [True, None]
    assert jb.route(verdicts) == jb.ROUTE_HELD


def test_s09_three_claim_vector_is_tristate_and_routes_held():
    """The priority and year meta-claims leave specifics unconfirmed -> None
    (unknown), not False; the bare finding is established. None + None + True
    (no False) routes HELD, the full-text escalation queue."""
    replies = iter([
        _coverage_reply(unconfirmed=["priority", "Study Q"]),
        _coverage_reply(unconfirmed=["2012", "Study Q"]),
        _coverage_reply(),
    ])
    judge = ca.make_coverage_judge(lambda prompt: next(replies))
    verdicts = judge(["m1", "m2", "finding"],
                     {"cited_abstract": "Protein P binds receptor R."})
    assert [v["established"] for v in verdicts] == [None, None, True]
    assert jb.route(verdicts) == jb.ROUTE_HELD


def test_no_usable_abstract_makes_no_llm_call_and_routes_held():
    calls = []

    def call_llm(prompt):
        calls.append(prompt)
        raise AssertionError("LLM must not be called on an unusable abstract")

    judge = ca.make_coverage_judge(call_llm)
    verdicts = judge(["Metformin activates AMPK"], {"cited_abstract": None})
    assert calls == []
    assert [v["established"] for v in verdicts] == [None]
    assert jb.route(verdicts) == jb.ROUTE_HELD
    # the single-claim mirror agrees, still no call
    d = ca.judge_coverage_tristate(call_llm, "c", {"cited_abstract": "N/A"})
    assert d["established"] is None
    assert calls == []


# ==========================================================================
# Bypass guard -- the band MUST route through coverage_aggregate, never through
# the frozen Boolean band_prompts path. Locks the divergence so a regression is
# loud.
# ==========================================================================
def test_band_prompts_aggregate_is_boolean_and_diverges_from_the_band_tristate():
    """KNOWN DEBT: band_prompts.aggregate_coverage stays BOOLEAN (frozen substrate)
    and is WRONG for the band on the silent/unconfirmed cases -- it derives False
    (F6) where the band must have None (HELD). This test pins both the footgun and
    the correct tri-state, so wiring the band to bp.make_coverage_judge would flip
    these assertions and fail loudly."""
    # The frozen Boolean footgun (do NOT use on the band path):
    assert bp.aggregate_coverage(True, False, ["x"]) is False
    assert bp.aggregate_coverage(False, False, []) is False
    # The band's correct tri-state:
    assert ca.aggregate_coverage(True, False, ["x"]) is None
    assert ca.aggregate_coverage(False, False, []) is None

    # And the two judges diverge on the SAME model reply + usable abstract.
    reply = _coverage_reply(unconfirmed=["a mouse model"])
    evidence = {"cited_abstract": "An abstract."}
    boolean = bp.make_coverage_judge(lambda p: reply)(["c"], evidence)[0]
    tristate = ca.make_coverage_judge(lambda p: reply)(["c"], evidence)[0]
    assert boolean["established"] is False          # frozen path: F6
    assert tristate["established"] is None           # band path: HELD
    assert jb.route([boolean]) == jb.ROUTE_F6_FLAGGED
    assert jb.route([tristate]) == jb.ROUTE_HELD
