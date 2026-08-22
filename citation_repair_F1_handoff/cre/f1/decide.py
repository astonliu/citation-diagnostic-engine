"""Phase 1h -- decision logic.

Pure function over accumulated evidence. The conjunction that defines F1:
  (claimed-PMID mismatch OR dead PMID) AND survives LLM filter AND
  claimed content not found in any database.

Precision-first: anything ambiguous goes to human_review or cleared, never F1.

F1 IS REACHABLE ONLY FROM EVIDENCE THAT WAS ACTUALLY GATHERED. Each clause of
that conjunction has a matching "and we really checked" precondition, because
every one of them used to be satisfiable by a failure:

  * "dead PMID" requires the identifier lookup to have ANSWERED
    (``pmid_transport_status``).
    An unanswered fetch is held, never read as an absence.
  * "not found in any database" requires EVERY confirmation search to have
    answered (``confirm.fully_answered``). A partial sweep cannot support a
    claim about all of them.

A retrieval failure holds the reference. It is never evidence of non-existence,
and it must never raise the confidence of an accusation.
"""
from __future__ import annotations

from .schema import (Reference, F1, F2, F8, CLEARED, UNVERIFIABLE, HUMAN_REVIEW,
                     SAME_WORK,
                     UNSCOREABLE, V_FORMATTING, V_UNCERTAIN, fetch_answered)
from .confirm import found_anywhere, all_errored, fully_answered, unanswered
from .doi_lookup import (DOI_FOUND, DOI_FOUND_NO_METADATA,
                         DOI_ANSWERED_ABSENT, DOI_INCOMPLETE, DOI_CONFLICT,
                         DOI_NOT_ATTEMPTED)
from .f8_retraction import F8_TIMING_INDETERMINATE, F8_UNRESOLVED
from .unscoreable import (F8_TIMING_BOUNDARY_UNRESOLVED,
                          IDENTITY_UNRESOLVED_AFTER_TITLE_SEARCH)
from .confirm import (TITLE_SEARCH_IDENTITY_MIN, best_title_score,
                      identity_settled_by_title)


def decide(ref: Reference, was_flagged: bool, llm_verdict: str | None,
           db_hits: dict | None, match_threshold: float = 85.0) -> Reference:
    log = ref.log

    # F8: the citation points at a RETRACTED source (RESEARCH_PLAN_v2.2 §4.3 --
    # "F8 is deterministic (retraction flag from PubMed / Retraction Watch) and is
    # routed through the existence-check layer with F1/F2, never reaching the human
    # classifier"). ``log.retracted`` is the tri-state the existence layer recorded
    # in run.process_reference; this branch is a pure read of it, so decide() makes
    # no network call.
    #
    # FIRST, AND THAT IS THE FIX. This branch used to sit BELOW the UNSCOREABLE
    # one, on the reasoning that "a non-title comparison carries no citation to
    # judge at all". That reasoning is about TITLE COMPARABILITY, which is what F2
    # needs; F8 needs a resolved PMID and two dates and nothing else. A reference
    # printed as an unstructured ``mixed-citation`` carries no ``<article-title>``
    # and so earns ``unscoreable_reason = no_claimed_title`` -- and that alone was
    # enough to suppress a row on which the timing gate had already returned
    # QUALIFIED against a ``Retracted Publication`` record with a resolved,
    # pre-citation notice date. Verified on PMC7474863, which cites two retracted
    # Surgisphere papers 93 and 94 days after their notices: the ``element-citation``
    # one was labelled F8 and the ``mixed-citation`` one was booked
    # ``unscoreable/no_claimed_title``, making F8 recall a function of the
    # publisher's XML markup rather than of the citation.
    #
    # Nothing that reached UNSCOREABLE for a real reason loses its label to this
    # reordering: F8 fires only on the POSITIVE determination ``retracted is True``,
    # and the one unscoreable reason that could co-occur without a resolved PMID --
    # ``non_article_reference`` -- returns from ``run.process_reference`` before the
    # F8 gate ever runs, so it cannot arrive here with a retraction.
    #
    # It also stays ABOVE the same-work quarantine and every F1/F2 path: a resolved
    # record marked retracted is F8 regardless of what the same-work rule concluded
    # about title identity, and regardless of whether the metadata comparison
    # flagged.
    #
    # ``is True`` is mandatory. False means "types fetched, not retracted"; None
    # means "we never learned" (no resolved PMID, or the lookup failed) and is NOT
    # an accusation -- precision-first, an unknown falls through to the normal path.
    if log.retracted is True:
        ref.label, ref.confidence = F8, "HIGH"
        if log.f8_timing_status:
            log.retraction_reason = "retracted_before_citation_31_day_floor_met"
            ref.rationale = (
                "The resolved work was formally retracted before citation and "
                f"the conservative publication-date gap is "
                f"{log.f8_timing_gap_days} days, meeting the registered 31-day "
                "F8 floor.")
            log.decided_by = "f8_retracted_before_citation_timing_met"
        else:
            # Legacy/development path retained for byte-compatible direct calls.
            # production_launcher.launch_full always supplies the timing seam.
            log.retraction_reason = "retracted_publication"
            ref.rationale = (
                "Resolved record carries the PubMed publication type "
                "'Retracted Publication': the cited source has been retracted.")
            log.decided_by = "retracted_publication_pubtype"
        return ref

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

    if log.f8_timing_status == F8_TIMING_INDETERMINATE:
        ref.label, ref.confidence = UNSCOREABLE, "HIGH"
        ref.rationale = (
            "The cited work was retracted, but the earliest defensible citing "
            f"date is only {log.f8_timing_gap_days} days after the notice; the "
            "registered 31-day F8 inclusion floor is not met.")
        log.decided_by = "f8_timing_indeterminate_excluded"
        return ref
    if log.f8_timing_status == F8_UNRESOLVED:
        # A BOUNDARY THAT DID NOT ARRIVE IS NOT A QUESTION FOR A HUMAN. Every
        # F8_UNRESOLVED reason names a missing date or an unanswered fetch, and it
        # has now been retried (`assess_f8_timing_with_retry`). A human
        # adjudicator cannot supply a date PubMed did not return either, so
        # queueing it wastes the queue on work nobody can do. It leaves the
        # scoreable set as a named, counted exclusion and terminates UNJUDGEABLE
        # in Band 2.
        #
        # PRECISION-FIRST IS UNCHANGED: an unknown retraction state is still
        # never an F8. This moves the row out of the human queue; it does not
        # turn it into an accusation.
        ref.label, ref.confidence = UNSCOREABLE, "HIGH"
        ref.rationale = (
            "F8 retraction timing could not be resolved after a bounded retry "
            f"({log.f8_timing_reason or 'boundary unresolved'}); the citation is "
            "neither cleared nor accused, and the missing boundary is not a "
            "question a human adjudicator can answer.")
        log.decided_by = "f8_timing_boundary_unresolved"
        log.unscoreable_reason = F8_TIMING_BOUNDARY_UNRESOLVED
        return ref


    # A deterministic identity rule found evidence of a translation, correction,
    # revision, or malformed rendering of the resolved work.  Keep the row
    # visible for audit, but do not let an LLM/search disagreement turn it into
    # an automatic F1/F2 accusation.
    if log.same_work_reason:
        # mixed_identity_conflict stays a human decision (an explicit F2 frame
        # call); a proved same-work variant is machine-final. Set per branch.
        ref.label, ref.confidence = HUMAN_REVIEW, "MED"
        if log.identity_disposition == "mixed_identity_conflict":
            ref.rationale = (
                "The citation combines an exact identifier/location anchor with "
                "conflicting title, year, and author-roster identity evidence "
                "(mixed_identity_citation); quarantined for an explicit F2 frame "
                "decision, not described as a same-work variant.")
            log.decided_by = "mixed_identity_conflict_quarantine"
        else:
            # MACHINE-FINAL, NOT AN ABSTAIN. A deterministic rule proved the same
            # work; there is no further question for a human to answer. The row
            # leaves the F2 scoreable denominator and PROCEEDS into the F3-F7
            # band, because a translated or retitled paper is still the right
            # paper and claim support is unaffected.
            ref.label, ref.confidence = SAME_WORK, "HIGH"
            ref.rationale = (f"Resolved identifier represents the same work or a "
                             f"work variant ({log.same_work_reason}); identity is "
                             f"settled. Excluded from F2 scoring, admitted to the "
                             f"F3-F7 band.")
            log.decided_by = "same_work_variant_resolved"
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

    # Exact DOI route for the newly in-scope no-PMID population.  This is kept
    # entirely outside the measured PMID/F2 path: the printed DOI is checked
    # without fuzzy mutation, and its returned metadata enters the established
    # matcher before this function is called.
    if not log.pmid_present and ref.claimed.claimed_doi:
        doi_status = log.doi_lookup_status
        if doi_status == DOI_FOUND:
            # compare_and_flag found a material mismatch between the printed
            # citation and the work identified by that exact DOI. Same-work
            # variants have already taken the higher quarantine branch above.
            if was_flagged:
                ref.label, ref.confidence = F2, "MED"
                ref.rationale = (
                    "The exact claimed DOI exists, but its authoritative "
                    "metadata does not identify the paper described by the "
                    "printed reference: wrong reference metadata.")
                log.decided_by = "exact_doi_metadata_mismatch_f2"
                return ref
        elif doi_status == DOI_FOUND_NO_METADATA:
            ref.label, ref.confidence = HUMAN_REVIEW, "MED"
            ref.rationale = (
                "The exact claimed DOI exists in the DOI system, but no usable "
                "authority metadata was available for an F2 comparison.")
            log.decided_by = "exact_doi_found_metadata_unavailable_hold"
            return ref
        elif doi_status in (DOI_INCOMPLETE, DOI_CONFLICT, DOI_NOT_ATTEMPTED, ""):
            ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
            ref.rationale = (
                f"The exact claimed DOI check was {doi_status or 'not run'}; "
                "its existence cannot be determined safely.")
            log.decided_by = "exact_doi_incomplete_hold"
            return ref
        elif doi_status == DOI_ANSWERED_ABSENT:
            if db_hits is None:
                ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
                ref.rationale = (
                    "The exact claimed DOI was absent, but the independent "
                    "title/name confirmation sweep was not completed.")
                log.decided_by = "exact_doi_no_confirm"
                return ref
            if found_anywhere(db_hits, match_threshold):
                ref.label, ref.confidence = F2, "MED"
                ref.rationale = (
                    "The exact claimed DOI is absent, while the paper described "
                    "by the printed title was found independently: wrong "
                    "reference metadata.")
                log.decided_by = "exact_doi_absent_title_found_f2"
                return ref
            if not fully_answered(db_hits):
                missing = unanswered(db_hits)
                ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
                ref.rationale = (
                    "The exact claimed DOI was absent, but the title/name sweep "
                    f"was incomplete ({', '.join(missing)} did not answer).")
                log.decided_by = "exact_doi_confirm_incomplete"
                return ref
            ref.label, ref.confidence = F1, "HIGH"
            ref.rationale = (
                "The exact claimed DOI was absent from the DOI system, Crossref, "
                "DataCite, and OpenAlex, and the claimed work was not found by "
                "the complete independent PubMed, Crossref, and OpenAlex title "
                "sweep.")
            log.decided_by = "exact_doi_absent_confirm_not_found_f1"
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

    # LLM UNCERTAIN -> ASK THE DATABASES, NOT A HUMAN.
    #
    # This used to escalate on the model's shrug alone, with `db_hits: {}` on
    # every row -- the deterministic title search had not been run. It runs
    # first now (`run.process_reference`), and a NEAR-EXACT hit settles the
    # identity by itself: the claimed work is in a database under that title, so
    # whatever the metadata discrepancy is, it is not a fabricated reference.
    #
    # NO SEMANTIC LABEL IS ASSIGNED HERE. This is a scored database lookup
    # against a named threshold, never a model judgment about whether two titles
    # denote the same work.
    if llm_verdict == V_UNCERTAIN:
        best = best_title_score(db_hits) if db_hits else None
        log.title_search_best_score = best
        if db_hits and identity_settled_by_title(db_hits):
            ref.label, ref.confidence = CLEARED, "MED"
            ref.rationale = (
                f"LLM filter uncertain, but the claimed title matches a database "
                f"record near-exactly (best score {best:.2f} >= "
                f"{TITLE_SEARCH_IDENTITY_MIN:.0f}); the cited work exists as "
                f"claimed and the discrepancy is not a fabrication.")
            log.decided_by = "title_search_identity_settled"
            return ref
        # No match, or a match too weak to stand in for an adjudication. This is
        # a SEMANTIC UNCERTAINTY ABOUT IDENTITY -- a title that is in none of the
        # databases we query may still be a real trade-proceedings abstract --
        # and a human adjudicator has no source we did not already ask.
        ref.label, ref.confidence = UNSCOREABLE, "HIGH"
        ref.rationale = (
            "LLM filter uncertain and the claimed title was not found "
            + (f"above the identity threshold (best score {best:.2f} < "
               f"{TITLE_SEARCH_IDENTITY_MIN:.0f})" if best is not None
               else "in any database that answered")
            + "; identity unresolved after title search.")
        log.decided_by = "identity_unresolved_after_title_search"
        log.unscoreable_reason = IDENTITY_UNRESOLVED_AFTER_TITLE_SEARCH
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
