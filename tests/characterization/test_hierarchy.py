"""ITEM 4 -- the F7 > F6 > F4 > F3 > F5 hierarchy, driven directly.

WHY DIRECTLY AND NOT THROUGH THE PIPELINE. The orchestrator can only reach a
handful of these combinations without a great deal of wiring, and the ones it
cannot reach are exactly the ones a refactor breaks quietly. ``decide_judgment``
takes typed assessments and returns a decision with no I/O at all, so the whole
table can be driven in milliseconds -- and the precedence order is a rule about
the TABLE, not about any one path through the pipeline.
"""
from __future__ import annotations

import pytest

from cde.diagnose.engine import (
    ClaimSupport, DecisionStatus, DiscriminatorContractError, EntityAssessment,
    EntityState, ProvenanceAssessment, ProvenanceState, SupportState,
    TemporalAssessment, TemporalState, decide_judgment,
)

CLAIMS = ("Drug X reduces outcome Y",)
TWO = ("Drug X reduces outcome Y", "Drug X prevents readmission")


def support(*states):
    return tuple(ClaimSupport(claim_index=i, state=s, rationale="r",
                              evidence_spans=("span",))
                 for i, s in enumerate(states))


def entity(state):
    # A confirmed F7 requires two normalised entity keys: the engine refuses to
    # record "different entity" without naming which two entities differ.
    keys = ({"claimed_entity_key": "drug-x", "evidence_entity_key": "drug-z",
             "relation_supported": True}
            if state is EntityState.DIFFERENT_ENTITY_SUPPORTED else {})
    return (EntityAssessment(claim_index=0, state=state, rationale="r", **keys),)


def prov(state):
    # A confirmed F3 must name the origin it was misattributed away from: the
    # engine refuses to record "wrong origin" without saying what the right one
    # was and where that was read.
    chain = (("PMID:30000009", "PMID:30000010")
             if state is ProvenanceState.MISATTRIBUTED_CONFIRMED else ())
    return ProvenanceAssessment(state=state, rationale="r", origin_chain=chain,
                                evidence_spans=("span",))


def temporal(state=TemporalState.NO_QUALIFYING_CONTRADICTION, claim_index=None):
    # A confirmed F5 carries the whole precondition set explicitly: a newer
    # work, the same outcome, a comparable population, and an F8 clearance that
    # says False rather than merely not saying True. Supersession is the one
    # finding that accuses a paper of being out of date, and the engine will not
    # record it on a partial case.
    extra = ({"newer_work_id": "PMID:30000011", "same_claim_or_outcome": True,
              "comparable_population": True, "f8_notice": False}
             if state is TemporalState.QUALIFYING_CONTRADICTION else {})
    return TemporalAssessment(state=state, claim_index=claim_index,
                              rationale="r", evidence_spans=("span",), **extra)


def decide(**kw):
    kw.setdefault("preband_cleared", True)
    kw.setdefault("claims", CLAIMS)
    kw.setdefault("provenance", None)
    kw.setdefault("temporal", temporal())
    return decide_judgment(**kw)


# ---------------------------------------------------------------------------
# the precedence order
# ---------------------------------------------------------------------------
def test_the_reported_finding_follows_the_precedence_order_exactly():
    """F7 outranks F6 outranks F4 outranks F3 outranks F5.

    Each row states the whole assessment and the finding that must come back
    first. Precedence is what decides which fault a reviewer is shown, so a
    reordering here changes the headline label on real citations without
    changing how many are flagged -- invisible to a count-based check.
    """
    # F7 over F6: a different-entity paper that also fails to support the claim.
    d = decide(claim_support=support(SupportState.UNESTABLISHED),
               entity_assessments=entity(EntityState.DIFFERENT_ENTITY_SUPPORTED))
    assert d.findings == ("F7", "F6")
    assert d.primary_label == "F7"

    # F6 over F4: one claim unsupported, another merely overstated.
    d = decide(claims=TWO,
               claim_support=support(SupportState.UNESTABLISHED,
                                     SupportState.WEAKER_STRENGTH))
    assert d.findings == ("F6", "F4")
    assert d.primary_label == "F6"

    # F4 over F5, and F5 last of all.
    d = decide(claim_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.PROPER_ORIGIN),
               temporal=temporal(TemporalState.QUALIFYING_CONTRADICTION,
                                 claim_index=0))
    assert d.findings == ("F5",)


def test_f4_and_f3_are_reported_together_and_f3_is_not_swallowed():
    """THE PROVENANCE GATE READS THE PRE-REFINEMENT COVERAGE TUPLE.

    A citing sentence can overstate its source AND cite the wrong origin. F4's
    verdict is ABOUT an already-established claim, so an overstatement must not
    close the gate F3 is decided behind. When the gate was fed the F4-refined
    tuple, such a pair reported only the overstatement and the misattribution
    vanished -- silently, with a plausible label still attached.

    So the coverage tuple says SUPPORTED, the refined tuple says
    WEAKER_STRENGTH, and BOTH findings must come back, F4 first by precedence.
    """
    d = decide(claim_support=support(SupportState.WEAKER_STRENGTH),
               coverage_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.MISATTRIBUTED_CONFIRMED))
    assert d.findings == ("F4", "F3")
    assert d.primary_label == "F4"
    assert d.status is DecisionStatus.TERMINAL


def test_an_f4_verifier_that_merely_disagrees_still_leaves_f3_reachable():
    """The same defect's other half: a strength-UNJUDGEABLE claim.

    It holds the record -- correctly, strength could not be settled -- but it
    must not also erase a CONFIRMED misattribution from the findings, or the
    reference is held with no record of the fault that was established.
    """
    d = decide(claim_support=support(SupportState.UNJUDGEABLE),
               coverage_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.MISATTRIBUTED_CONFIRMED))
    assert "F3" in d.findings
    assert d.status is DecisionStatus.HELD_UNJUDGEABLE
    assert "claim support is unjudgeable" in d.hold_reasons


def test_a_co_cited_claim_holds_rather_than_raising_f6_or_clearing():
    """The co-citation overlay touches exactly one finding, in one direction.

    A sentence citing eight references cites them collectively, so a claim a
    sibling established is not THIS reference's coverage gap -- F6 would
    otherwise fire by construction on every member of every group. But it is not
    this reference's support either, so it must not CLEAR: a co-citation group
    is never a blanket excuse.
    """
    d = decide(claim_support=support(SupportState.UNESTABLISHED),
               cogroup_covered=(True,))
    assert "F6" not in d.findings
    assert d.status is DecisionStatus.HELD_UNJUDGEABLE
    assert "claim coverage attributed to a co-cited reference" in d.hold_reasons
    assert d.primary_label is None


def test_co_citation_never_downgrades_a_confirmed_per_reference_fault():
    """F4 and F7 are per-reference properties and the overlay leaves them alone.

    A cited paper that overstates, or that is about a different entity, is at
    fault whether or not siblings exist. Appending the hold reason
    unconditionally would turn a confirmed fault from terminal into held -- and
    hide exactly the faults co-citation must never touch.
    """
    d = decide(claims=TWO,
               claim_support=support(SupportState.UNESTABLISHED,
                                     SupportState.WEAKER_STRENGTH),
               cogroup_covered=(True, False))
    assert d.findings == ("F4",)
    assert d.status is DecisionStatus.TERMINAL


def test_a_reference_the_preband_did_not_clear_is_outside_the_band():
    """``preband_cleared=False`` short-circuits before any discriminator runs.

    An F1/F2/F8 reference is not a Band 2 miss and must never enter a Band 2
    denominator -- if it did, every reported rate would be diluted by references
    the band was never asked about.
    """
    d = decide(preband_cleared=False,
               claim_support=support(SupportState.UNESTABLISHED),
               entity_assessments=entity(EntityState.DIFFERENT_ENTITY_SUPPORTED))
    assert d.status is DecisionStatus.OUTSIDE_BAND
    assert d.findings == ()
    assert d.primary_label is None


def test_full_support_with_proper_origin_and_no_contradiction_clears():
    """The one path to a clean terminal answer, pinned so it stays reachable.

    A refactor that made clearing impossible would look like a very careful
    system and would report a false-alarm rate of nearly one.
    """
    d = decide(claim_support=support(SupportState.SUPPORTED),
               provenance=prov(ProvenanceState.PROPER_ORIGIN))
    assert d.status is DecisionStatus.TERMINAL
    assert d.primary_label == "accurate"
    assert d.findings == ()
