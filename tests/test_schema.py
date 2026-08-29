"""The record types the two bands exchange, and the invariants they enforce.

WHY THE F6 INVARIANT IS CHECKED BY THE RECORD ITSELF rather than by whoever
writes one. F6 means "the cited work does not establish every atomic claim", so
an F6 record whose claims are all supported is not a mislabelled record -- it is
a record that contradicts itself, and it would be counted in an F6 rate anyway.
The type refuses to hold one.
"""
from __future__ import annotations

import pytest

from cde.refs.schema import (
    ACCURATE, CLEARED, F1, F2, F6, HUMAN_REVIEW, UNVERIFIABLE,
    Annotation, AtomicClaim, CitedPaper, ClaimedRef, EvalRecord, GoldRecord,
    PredictionRecord, Reference, Repair, SourcePaper, check_f6_invariant,
    pipeline_state_to_taxonomy,
)

MIXED = [AtomicClaim("a", True), AtomicClaim("b", False)]
ALL_SUPPORTED = [AtomicClaim("a", True), AtomicClaim("b", True)]


@pytest.mark.parametrize("label,claims,ok", [
    (F6, MIXED, True),              # a real coverage gap
    (F6, ALL_SUPPORTED, False),     # F6 with nothing unsupported is incoherent
    (ACCURATE, MIXED, False),       # accurate with an unsupported claim likewise
    (ACCURATE, ALL_SUPPORTED, True),
    (F1, [], True),                 # no claims -> the invariant does not apply
])
def test_the_f6_invariant_accepts_only_coherent_pairings(label, claims, ok):
    assert (check_f6_invariant(label, claims) is None) is ok


def test_gold_record_validate_enforces_the_invariant_and_the_label_vocabulary():
    GoldRecord("c1", "Aspirin reduces mortality [12].", "[12]",
               CitedPaper(pmid="12345678", title="X"), SourcePaper(pmid="999"),
               label=F6, atomic_claims=MIXED).validate()

    with pytest.raises(ValueError, match="F6"):
        GoldRecord("c2", "x", "[1]", CitedPaper(), SourcePaper(),
                   label=F6, atomic_claims=ALL_SUPPORTED).validate()

    with pytest.raises(ValueError, match="taxonomy"):
        GoldRecord("c3", "x", "[1]", CitedPaper(), SourcePaper(),
                   label="supported").validate()


@pytest.mark.parametrize("state,taxonomy", [
    (F1, F1),
    (CLEARED, ACCURATE),
    # An operational state is NOT a taxonomy answer. Mapping either of these to
    # a label would put references the pipeline declined to judge into a
    # reported rate.
    (UNVERIFIABLE, None),
    (HUMAN_REVIEW, None),
])
def test_a_pipeline_state_maps_to_a_taxonomy_label_only_where_one_exists(
        state, taxonomy):
    assert pipeline_state_to_taxonomy(state) == taxonomy


def test_a_prediction_carries_the_evidence_its_label_was_decided_on():
    r = Reference("c1", "cit", ClaimedRef(title="t", claimed_pmid="1"))
    r.label, r.confidence, r.rationale = F1, "HIGH", "not found"
    r.log.db_hits = {"pubmed": 0, "crossref": 0, "openalex": 0}
    r.log.decided_by = "exact_doi_absent_confirm_not_found_f1"
    pred = r.to_prediction()
    assert pred.label == F1
    assert pred.annotations[0].confidence == 0.95
    assert pred.evidence["db_hits"] == {"pubmed": 0, "crossref": 0, "openalex": 0}
    assert pred.evidence["pipeline_state"] == F1


def test_eval_scores_the_label_and_the_repair_as_separate_questions():
    """A right label with the wrong replacement is not a correct answer.

    And a gold record with NO repair scores ``repair_correct`` as None rather
    than True: there was nothing to get right, and counting it as a success
    would inflate the repair rate with every citation that needed no repair.
    """
    gold = GoldRecord("c1", "x", "[1]", CitedPaper(), SourcePaper(), label=F2,
                      repair=Repair(action="replace",
                                    recommended_references=[{"pmid": "555"}]))
    good = PredictionRecord("c1", label=F2,
                            repair=Repair(action="replace",
                                          recommended_references=[{"pmid": "555"}]),
                            annotations=[Annotation("llm", F2, confidence=0.9)])
    ev = EvalRecord.score("c1", gold, good).evaluation
    assert ev["label_correct"] and ev["exact_match"] and ev["repair_correct"] is True

    wrong_target = PredictionRecord("c1", label=F2,
                                    repair=Repair(action="replace",
                                                  recommended_references=[{"pmid": "000"}]))
    assert EvalRecord.score("c1", gold, wrong_target).evaluation[
        "repair_correct"] is False

    no_repair_gold = GoldRecord("c2", "x", "[1]", CitedPaper(), SourcePaper(),
                                label=ACCURATE)
    assert EvalRecord.score("c2", no_repair_gold,
                            PredictionRecord("c2", label=ACCURATE)
                            ).evaluation["repair_correct"] is None

    mismatch = GoldRecord("c3", "x", "[1]", CitedPaper(), SourcePaper(), label=F1)
    assert EvalRecord.score("c3", mismatch,
                            PredictionRecord("c3", label=ACCURATE)
                            ).evaluation["label_correct"] is False
