"""ITEMS 5, 6, 7 AND 8 -- abstentions hold, violations raise, joins refuse.

THREE GUARANTEES, ALL OF THEM PRECISION GUARANTEES.

An ABSTENTION must stay an abstention. Every hold reason in this system exists
because something could not be established, and the failure mode is not that a
hold becomes a crash -- it is that a hold quietly becomes a confident NEGATIVE.
"we could not tell" reported as "not established" is an accusation the evidence
does not support, and it moves the false-alarm rate with nothing failing.

A CONTRACT VIOLATION must stay a violation. Fail-closed paths -- a content-hash
mismatch, a non-verbatim span, malformed model JSON -- must raise or quarantine.
The failure they guard is a violation DEGRADING INTO A VERDICT: a record that
looks like a judgment and is really a parse accident.

A BAD JOIN must abort before writing anything. The failure here is the
clean-looking empty run: every pair excluded, a valid manifest, a green exit,
and a denominator of zero that nobody notices until the numbers are in a table.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import preband_contract as pc
from cre.f1 import sentence_spans
from cre.f1.judgment_engine import (
    ClaimSupport, DecisionStatus, DiscriminatorContractError,
    EntityAssessment, EntityState, ProvenanceAssessment, ProvenanceState,
    SupportState, TemporalAssessment, TemporalState, decide_judgment,
)
from cre.f1.freeze import canon_v1 as canon

from .test_hierarchy import CLAIMS, decide, entity, prov, support, temporal


# ---------------------------------------------------------------------------
# item 5 -- every abstention path stays an abstention
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kwargs,reason", [
    # An unwired discriminator is silence, never a confident negative.
    (dict(claim_support=support(SupportState.UNJUDGEABLE)),
     "claim support is unjudgeable"),
    # F7's evidence could not be read: hold, do not clear the entity question.
    (dict(claim_support=support(SupportState.SUPPORTED),
          entity_assessments=entity(EntityState.UNJUDGEABLE),
          provenance=prov(ProvenanceState.PROPER_ORIGIN)),
     "entity evidence is unjudgeable"),
    # Incomplete provenance retrieval: "cannot be determined", not "misattributed".
    (dict(claim_support=support(SupportState.SUPPORTED),
          provenance=prov(ProvenanceState.UNJUDGEABLE)),
     "provenance is unjudgeable"),
    # F5's supersession search could not be completed.
    (dict(claim_support=support(SupportState.SUPPORTED),
          provenance=prov(ProvenanceState.PROPER_ORIGIN),
          temporal=temporal(TemporalState.UNJUDGEABLE)),
     "temporal evidence is unjudgeable"),
    # The temporal seam was never wired at all -- absence is not clearance.
    (dict(claim_support=support(SupportState.SUPPORTED),
          provenance=prov(ProvenanceState.PROPER_ORIGIN), temporal=None),
     "temporal assessment missing"),
])
def test_an_unsettled_question_holds_and_never_becomes_a_finding(kwargs, reason):
    d = decide(**kwargs)
    assert d.status is DecisionStatus.HELD_UNJUDGEABLE
    assert reason in d.hold_reasons
    assert d.primary_label is None
    assert d.findings == ()


def test_a_reference_with_no_atomic_claims_holds_rather_than_clearing():
    """Nothing was asserted, so nothing was established -- and nothing failed.

    Reporting this as ``accurate`` would put every pointer sentence in the
    cleared numerator and inflate the accuracy figure with pairs the band never
    actually judged.
    """
    d = decide(claims=(), claim_support=(), temporal=temporal())
    assert d.status is DecisionStatus.HELD_UNJUDGEABLE
    assert "no atomic claims" in d.hold_reasons
    assert d.primary_label is None


# ---------------------------------------------------------------------------
# item 6 -- every fail-closed path still raises
# ---------------------------------------------------------------------------
def test_a_confirmed_finding_without_its_evidence_raises_rather_than_labelling():
    """The three accusing findings each require their evidence, by construction.

    F7 without two entity keys, F3 without an origin chain, F5 without its
    precondition set: each would otherwise be a public accusation backed by a
    state enum and nothing else.
    """
    with pytest.raises(DiscriminatorContractError, match="entity keys"):
        EntityAssessment(claim_index=0,
                         state=EntityState.DIFFERENT_ENTITY_SUPPORTED)
    with pytest.raises(DiscriminatorContractError, match="origin chain"):
        ProvenanceAssessment(state=ProvenanceState.MISATTRIBUTED_CONFIRMED)
    with pytest.raises(DiscriminatorContractError, match="confirmed F5"):
        TemporalAssessment(state=TemporalState.QUALIFYING_CONTRADICTION,
                           claim_index=0)


def test_provenance_supplied_without_full_support_is_a_contract_error():
    """F3 is only asked once every claim is established.

    A provenance verdict arriving on a partially-supported pair means the
    orchestrator called the discriminators out of order. Accepting it would
    report a misattribution decided against evidence F3 never sees.
    """
    with pytest.raises(DiscriminatorContractError,
                       match="allowed only after full support"):
        decide(claim_support=support(SupportState.UNESTABLISHED),
               provenance=prov(ProvenanceState.PROPER_ORIGIN))


def test_a_non_bool_preband_flag_is_refused_rather_than_coerced():
    """``preband_cleared`` decides whether a pair is in the band at all.

    Python would happily treat ``1`` or ``"cleared"`` as true, and a truthy
    string is exactly what a refactor that started passing a LABEL instead of a
    flag would produce -- admitting every F1/F2 reference into the Band 2
    denominator.
    """
    for value in (1, "cleared", None):
        with pytest.raises(DiscriminatorContractError, match="exact bool"):
            decide(preband_cleared=value,
                   claim_support=support(SupportState.SUPPORTED),
                   provenance=prov(ProvenanceState.PROPER_ORIGIN))


def test_an_f5_target_that_the_cited_paper_never_supported_is_refused():
    """Supersession accuses a paper of being out of date about ITS OWN claim.

    Pointing it at a claim the paper never made is not a weaker finding, it is a
    different and false one.
    """
    with pytest.raises(DiscriminatorContractError, match="supported"):
        decide(claims=CLAIMS,
               claim_support=support(SupportState.UNESTABLISHED),
               temporal=temporal(TemporalState.QUALIFYING_CONTRADICTION,
                                 claim_index=0))


# ---------------------------------------------------------------------------
# item 7 -- the join contract
# ---------------------------------------------------------------------------
def test_a_zero_overlap_join_aborts_instead_of_running_empty():
    """THE CLEAN-LOOKING EMPTY RUN.

    Corpus ids and disposition ids from different populations produce a run in
    which every pair is excluded fail-closed. It completes, it validates, and its
    denominator is zero.
    """
    disp = pc.load_injected({"PMC1:a": "cleared", "PMC1:b": "cleared"})
    acc = pc.join_accounting(disp, ["PMC9:x", "PMC9:y"])
    assert acc["matched"] == 0
    with pytest.raises(pc.PrebandContractError, match="ZERO overlap"):
        pc.enforce_join(acc, disp=disp)


def test_no_disposition_at_all_aborts_rather_than_excluding_everything():
    acc = pc.join_accounting(None, ["PMC1:a"])
    with pytest.raises(pc.PrebandContractError, match="no preband_disposition"):
        pc.enforce_join(acc, disp=None)


def test_a_partial_join_is_permitted_but_its_gap_is_counted():
    """Partial coverage is a legitimate run; an INVISIBLE gap is not.

    The accounting is what keeps a half-covered corpus from being reported as a
    whole one, so the counts are pinned rather than merely the absence of a
    raise.
    """
    disp = pc.load_injected({"PMC1:a": "cleared", "PMC1:b": "F2"})
    acc = pc.join_accounting(disp, ["PMC1:a", "PMC1:b", "PMC1:c"])
    pc.enforce_join(acc, disp=disp)
    assert acc["matched"] == 2
    assert acc["missing_from_disposition"] == 1
    assert acc["matched_cleared"] == 1
    with pytest.raises(pc.PrebandContractError, match="full coverage"):
        pc.enforce_join(acc, disp=disp, require_full_coverage=True)


def test_an_unregistered_disposition_label_is_refused_at_load():
    """The label vocabulary is closed where Band 2 reads it.

    An unknown label would fall through every branch of the pre-band gate, and
    the pair would be excluded for a reason no bucket names.
    """
    with pytest.raises(pc.PrebandContractError):
        pc.load_injected({"PMC1:a": "probably_fine"})


# ---------------------------------------------------------------------------
# item 8 -- spans are verbatim by construction
# ---------------------------------------------------------------------------
def test_every_segmented_sentence_is_a_substring_of_its_own_section():
    """A quoted span must be text the paper actually contains.

    The segmenter is what turns a section into addressable units, and if a unit
    is not verbatim then every span resolved through it is a paraphrase
    presented as a quotation -- in a system whose entire output is evidence
    spans shown to a reviewer.
    """
    sections = [
        {"label": "Results",
         "text": "Drug X reduced outcome Y. The effect persisted at two years. "
                 "No serious adverse events were observed."},
        {"label": "Methods",
         "text": "We randomised 400 adults (mean age 61 yr.) to drug X vs. placebo."},
    ]
    segmented = sentence_spans.segment_sections(sections)
    assert segmented, "the segmenter returned nothing for two real sections"
    for section in sections:
        for unit in segmented[section["label"]]:
            assert unit["text"] in section["text"], unit


def test_an_unresolvable_span_scores_zero_rather_than_matching_something():
    """A quotation with no home is recorded as unresolved, never nearest-fit.

    ``best_alignment`` returning a plausible-but-wrong unit is how a fabricated
    span acquires a citation. The floor has to be a real zero.
    """
    units = sentence_spans.segment_section(
        "Results", "Drug X reduced outcome Y. The effect persisted.")
    match, score = sentence_spans.best_alignment(
        "Entirely unrelated prose about hydrology and riverbank sediment", units)
    assert score == 0.0 or match is None
    match, score = sentence_spans.best_alignment("Drug X reduced outcome Y", units)
    assert match is not None and score > 0.0
    assert match["text"] in "Drug X reduced outcome Y. The effect persisted."
