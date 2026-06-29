"""Phase 1h -- decision logic.

Pure function over accumulated evidence. The conjunction that defines F1:
  (claimed-PMID mismatch OR dead PMID) AND survives LLM filter AND
  claimed content not found in any database.

Precision-first: anything ambiguous goes to human_review or cleared, never F1.
"""
from __future__ import annotations

from .schema import (Reference, F1, F2, CLEARED, UNVERIFIABLE, HUMAN_REVIEW,
                     UNSCOREABLE, V_FORMATTING, V_UNCERTAIN)
from .confirm import found_anywhere, all_errored


def decide(ref: Reference, was_flagged: bool, llm_verdict: str | None,
           db_hits: dict | None, match_threshold: float = 85.0) -> Reference:
    log = ref.log

    # UNSCOREABLE: the (claimed, resolved) pair is not a scoreable title
    # comparison -- a non-title input (journal name / regulatory code), a
    # placeholder ("[Not Available]"), or a book/container record cited as a
    # chapter. It carries no wrong-reference evidence, so it is routed to a
    # counted coverage bucket and EXCLUDED from the F2 numerator. Crucially this
    # is checked BEFORE the `not was_flagged -> CLEARED` branch below, which would
    # otherwise stamp it ACCURATE -- a silent miscount of a non-title as a
    # correct citation.
    if log.unscoreable_reason:
        ref.label, ref.confidence = UNSCOREABLE, "HIGH"
        ref.rationale = (f"Not a scoreable title comparison "
                         f"({log.unscoreable_reason}); excluded from the F2 "
                         f"numerator and reported as UNSCOREABLE.")
        log.decided_by = "unscoreable"
        return ref

    # No claimed PMID.
    if not log.pmid_present:
        if not log.noid_lookup_attempted:
            # No title to search on -> genuinely unverifiable (Topaz-style).
            ref.label, ref.confidence = UNVERIFIABLE, "HIGH"
            ref.rationale = ("No claimed PMID and no title; outside the "
                             "verifiable set.")
            log.decided_by = "no_pmid_no_title"
            return ref
        # No-ID lookup ran; fall through to the normal decision logic below.
        # was_flagged drives the path identically to the PMID path, EXCEPT the
        # confirm-not-found outcome is human_review, not F1 (guard further down).

    # Resolved and metadata matched -> cleared.
    if not was_flagged:
        ref.label, ref.confidence = CLEARED, "HIGH"
        if not log.pmid_present:
            sim = log.title_similarity
            sim_txt = f"title similarity {sim:.0f}" if sim is not None \
                else "title similarity unavailable"
            ref.rationale = (f"No claimed PMID; bibliographic lookup found a "
                             f"matching record ({sim_txt}).")
            log.decided_by = "noid_metadata_match"
        else:
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
        if not log.pmid_present:
            # No-ID path: the title exists in a database, yet the cheap
            # structured lookup (title+author+year+journal) missed and the LLM
            # flagged it. Contradictory evidence and there is no claimed PMID to
            # call "wrong" -> F2 is inapplicable. Precision-first: escalate.
            ref.label, ref.confidence = HUMAN_REVIEW, "MED"
            ref.rationale = ("No claimed PMID; claimed title found in a database "
                             "but the structured bibliographic lookup did not "
                             "confirm it. Ambiguous; needs human adjudication.")
            log.decided_by = "noid_confirm_found_human_review"
            return ref
        # Real work exists, but the claimed PMID pointed elsewhere -> wrong ref.
        ref.label, ref.confidence = F2, "MED"
        ref.rationale = ("Claimed work found in a database but claimed PMID "
                         "resolves to a different paper: wrong reference.")
        log.decided_by = "confirm_found_f2"
        return ref

    # Not found in PubMed, Crossref, or OpenAlex.
    if not log.pmid_present:
        # No-ID path: "not found" is ambiguous (grey literature, books, parsing
        # gaps) and has a higher base rate than PMID-dead + title-not-found.
        # Precision-first: escalate rather than accuse.
        ref.label, ref.confidence = HUMAN_REVIEW, "MED"
        ref.rationale = ("No claimed PMID; claimed title not found in any "
                         "database. Cannot distinguish fabrication from an "
                         "unfindable legitimate source.")
        log.decided_by = "noid_confirm_not_found_human_review"
        return ref

    # PMID path: fabricated.
    ref.label = F1
    ref.confidence = "HIGH" if not log.pmid_resolved else "MED"
    ref.rationale = ("Claimed title not found in PubMed, Crossref, or OpenAlex; "
                     + ("claimed PMID did not resolve." if not log.pmid_resolved
                        else "claimed PMID resolves to an unrelated paper."))
    log.decided_by = "confirm_not_found_f1"
    return ref
