"""Phase 1h -- decision logic.

Pure function over accumulated evidence. The conjunction that defines F1:
  (claimed-PMID mismatch OR dead PMID) AND survives LLM filter AND
  claimed content not found in any database.

Precision-first: anything ambiguous goes to human_review or cleared, never F1.

F1 IS REACHABLE ONLY FROM EVIDENCE THAT WAS ACTUALLY GATHERED. Each clause of
that conjunction has a matching "and we really checked" precondition, because
every one of them used to be satisfiable by a failure:

  * "dead PMID" requires the EFetch to have ANSWERED (``pmid_transport_status``).
    An unanswered fetch is held, never read as an absence.
  * "not found in any database" requires EVERY confirmation search to have
    answered (``confirm.fully_answered``). A partial sweep cannot support a
    claim about all of them.

A retrieval failure holds the reference. It is never evidence of non-existence,
and it must never raise the confidence of an accusation.
"""
from __future__ import annotations

from .schema import (Reference, F1, F2, CLEARED, UNVERIFIABLE, HUMAN_REVIEW,
                     UNSCOREABLE, V_FORMATTING, V_UNCERTAIN, fetch_answered)
from .confirm import found_anywhere, all_errored, fully_answered, unanswered


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

    # A deterministic identity rule found evidence of a translation, correction,
    # revision, or malformed rendering of the resolved work.  Keep the row
    # visible for audit, but do not let an LLM/search disagreement turn it into
    # an automatic F1/F2 accusation.
    if log.same_work_reason:
        ref.label, ref.confidence = HUMAN_REVIEW, "MED"
        ref.rationale = (f"Resolved identifier appears to represent the same work "
                         f"or a work variant ({log.same_work_reason}); quarantined "
                         f"for human adjudication.")
        log.decided_by = "same_work_variant_quarantine"
        return ref

    # THE CLAIMED-PMID FETCH NEVER ANSWERED.
    #
    # Not "the PMID is dead" -- NCBI did not reply (non-200, a 429 that survived
    # every retry, a connection error). We do not know whether that PMID
    # resolves, so no branch below is entitled to speak about it: not F1 ("did
    # not resolve"), and not F2 ("resolves to a different paper"). Both would be
    # asserting an observation that was never made, and F1 in particular is a
    # public accusation that a real, indexed paper does not exist.
    #
    # A transport failure must cost us a decision, never buy us one -- note that
    # the F1 confidence rule below reads an unresolved PMID as the STRONGER
    # signal, so before this guard existed an outage actively RAISED the stated
    # confidence of the accusation it caused.
    if log.pmid_present and not fetch_answered(log.pmid_transport_status):
        ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
        ref.rationale = (f"The claimed PMID could not be checked: the PubMed "
                         f"fetch did not answer ({log.pmid_transport_status}). "
                         f"Whether it resolves is unknown; held for human "
                         f"review rather than reported as a finding.")
        log.decided_by = "pmid_fetch_no_answer"
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
        # Real work exists, but the claimed PMID did not lead to it -> wrong ref.
        # The rationale states only what was observed: a PMID that ANSWERED and
        # had no record did not "resolve to a different paper", and saying so
        # asserted a resolution that never happened.
        ref.label, ref.confidence = F2, "MED"
        ref.rationale = ("Claimed work found in a database but the claimed PMID "
                         + ("resolves to a different paper"
                            if log.pmid_resolved else
                            "has no PubMed record")
                         + ": wrong reference.")
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

    # EVERY search must have answered before an accusation is reachable
    # (ZD, 2026-08-16). F1 asserts the work exists in NO database; that claim is
    # only supported by having actually looked in all of them. One
    # healthy-but-empty search alongside an errored one used to be enough, so a
    # single-provider outage could carry a real, indexed paper to a public
    # accusation of fabrication.
    #
    # Deliberately placed AFTER found_anywhere: a POSITIVE finding needs no
    # completeness -- if a database returned the work, an outage at another
    # provider is irrelevant, and holding there would cost F2 recall for nothing.
    # Only the negative claim requires a complete sweep.
    if not fully_answered(db_hits):
        missing = unanswered(db_hits)
        ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
        ref.rationale = (f"Claimed title not found in the databases that "
                         f"answered, but {', '.join(missing)} did not answer; "
                         f"the claimed work cannot be ruled out without them.")
        # all-errored keeps its established reason code; the partial case is new.
        log.decided_by = ("confirm_all_errored" if all_errored(db_hits)
                          else "confirm_incomplete_evidence")
        return ref

    # PMID path: fabricated. Every search answered, and none of them found it.
    ref.label = F1
    # HIGH on an unresolved PMID is only sound because the guard above has
    # already established that the fetch ANSWERED -- so "did not resolve" is an
    # observed absence, not an outage. This line is why the transport-status
    # field had to exist: it used to raise the confidence of the accusation
    # precisely when the evidence was missing.
    ref.confidence = "HIGH" if not log.pmid_resolved else "MED"
    ref.rationale = ("Claimed title not found in PubMed, Crossref, or OpenAlex; "
                     + ("claimed PMID did not resolve." if not log.pmid_resolved
                        else "claimed PMID resolves to an unrelated paper."))
    log.decided_by = "confirm_not_found_f1"
    return ref
