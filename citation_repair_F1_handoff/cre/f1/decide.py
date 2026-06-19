"""Phase 1h -- decision logic.

Pure function over accumulated evidence. The conjunction that defines F1:
  (claimed-PMID mismatch OR dead PMID) AND survives LLM filter AND
  claimed content not found in any database.

Precision-first: anything ambiguous goes to human_review or cleared, never F1.
"""
from __future__ import annotations

from .schema import (Reference, F1, F2, CLEARED, UNVERIFIABLE, HUMAN_REVIEW,
                     V_FORMATTING, V_UNCERTAIN)
from .confirm import found_anywhere, all_errored


def decide(ref: Reference, was_flagged: bool, llm_verdict: str | None,
           db_hits: dict | None, match_threshold: float = 85.0) -> Reference:
    log = ref.log

    # No claimed PMID -> never F1 (Topaz-style exclusion).
    if not log.pmid_present:
        ref.label, ref.confidence = UNVERIFIABLE, "HIGH"
        ref.rationale = "No claimed PMID; outside the F1-verifiable set."
        log.decided_by = "no_pmid"
        return ref

    # Resolved and metadata matched -> cleared.
    if not was_flagged:
        ref.label, ref.confidence = CLEARED, "HIGH"
        ref.rationale = (f"Claimed PMID resolves; title similarity "
                         f"{log.title_similarity:.0f}.")
        log.decided_by = "metadata_match"
        return ref

    # Flagged but no LLM verdict yet -> caller should have run the filter.
    if llm_verdict is None:
        ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
        ref.rationale = "Flagged mismatch; LLM filter not run."
        log.decided_by = "no_llm"
        return ref

    # LLM says benign formatting -> cleared.
    if llm_verdict == V_FORMATTING:
        ref.label, ref.confidence = CLEARED, "MED"
        ref.rationale = "Mismatch judged a formatting discrepancy, not fabrication."
        log.decided_by = "llm_formatting"
        return ref

    # LLM uncertain -> human review (precision-first; do not flag).
    if llm_verdict == V_UNCERTAIN:
        ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
        ref.rationale = "LLM filter uncertain; escalated for human adjudication."
        log.decided_by = "llm_uncertain"
        return ref

    # Survivor (fabrication or reference_error). Need the confirmation search.
    if db_hits is None:
        ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
        ref.rationale = "Confirmation search not run on a flagged survivor."
        log.decided_by = "no_confirm"
        return ref

    # Every confirmation search errored (all None) -> we never actually looked.
    # Do NOT assert "not found anywhere"; that would be a false accusation on a
    # network blip. Precision-first: escalate. (Does not alter the F1 conjunction;
    # it guards the no-data case the conjunction never contemplated.)
    if all_errored(db_hits):
        ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
        ref.rationale = ("All confirmation searches errored (network/parse); "
                         "cannot rule out that the claimed work exists.")
        log.decided_by = "confirm_all_errored"
        return ref

    if found_anywhere(db_hits, match_threshold):
        # Real work exists, but the claimed PMID pointed elsewhere -> wrong ref.
        ref.label, ref.confidence = F2, "MED"
        ref.rationale = ("Claimed work found in a database but claimed PMID "
                         "resolves to a different paper: wrong reference.")
        log.decided_by = "confirm_found_f2"
        return ref

    # Not found in PubMed, Crossref, or OpenAlex -> fabricated.
    ref.label = F1
    ref.confidence = "HIGH" if not log.pmid_resolved else "MED"
    ref.rationale = ("Claimed title not found in PubMed, Crossref, or OpenAlex; "
                     + ("claimed PMID did not resolve." if not log.pmid_resolved
                        else "claimed PMID resolves to an unrelated paper."))
    log.decided_by = "confirm_not_found_f1"
    return ref
