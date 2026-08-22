"""Offline precision tests for the production F8 timing gate."""
from __future__ import annotations

from .decide import decide
from .doi_lookup import DOI_ANSWERED_ABSENT
from .f8_retraction import (F8_CLEAR, F8_QUALIFIED, F8_TIMING_INDETERMINATE,
                            F8_NOT_APPLICABLE, F8_UNRESOLVED, assess_f8_timing)
from .schema import ClaimedRef, F8, Reference, UNSCOREABLE


def _records(citing_date: str, notice_date: str = "2024-01-01"):
    return {
        "10": {"id": "10", "pub_date": citing_date,
               "pub_date_latest": citing_date},
        "20": {"id": "20", "pub_date": "2020-01-01",
               "pub_date_latest": "2020-01-01",
               "publication_types": ["Retracted Publication"],
               "comments_corrections": [
                   {"ref_type": "RetractionIn", "pmid": "30", "note": ""}]},
        "30": {"id": "30", "pub_date": notice_date,
               "pub_date_latest": notice_date,
               "publication_types": ["Retraction Notice"],
               "comments_corrections": []},
    }


def _assess(citing_date: str, notice_date: str = "2024-01-01"):
    records = _records(citing_date, notice_date)
    return assess_f8_timing("20", "10", fetch_meta=records.get)


def test_f8_requires_the_full_registered_31_day_gap():
    assert _assess("2024-02-01").status == F8_QUALIFIED
    short = _assess("2024-01-31")
    assert short.status == F8_TIMING_INDETERMINATE
    assert short.timing_gap_days == 30


def test_post_citation_retraction_is_clear_and_missing_notice_holds():
    assert _assess("2023-12-01").status == F8_CLEAR
    records = _records("2024-02-01")
    del records["30"]
    assert assess_f8_timing("20", "10", fetch_meta=records.get).status == F8_UNRESOLVED


def test_absent_cited_work_does_not_block_f1_or_f2():
    assert assess_f8_timing("", "10", fetch_meta=lambda _id: None).status == \
        F8_NOT_APPLICABLE
    assert assess_f8_timing("999", "10", fetch_meta=lambda _id: None).status == \
        F8_NOT_APPLICABLE
    ref = Reference(
        "PMC1:R1", "claim [1]",
        ClaimedRef(title="Invented work", claimed_doi="10.1/missing"))
    ref.log.f8_timing_status = F8_NOT_APPLICABLE
    ref.log.pmid_present = False
    ref.log.doi_lookup_status = DOI_ANSWERED_ABSENT
    out = decide(ref, True, None, {"pubmed": 0, "crossref": 0, "openalex": 0})
    assert out.label == "F1"


def test_timing_boundary_routes_to_exclusion_not_f8():
    ref = Reference("PMC1:R1", "claim [1]", ClaimedRef(title="x"))
    ref.log.f8_timing_status = F8_TIMING_INDETERMINATE
    ref.log.f8_timing_gap_days = 30
    out = decide(ref, False, None, None)
    assert out.label == UNSCOREABLE


def test_qualified_timing_emits_f8_with_bound_dates():
    ref = Reference("PMC1:R1", "claim [1]", ClaimedRef(title="x"))
    ref.log.f8_timing_status = F8_QUALIFIED
    ref.log.f8_timing_gap_days = 31
    ref.log.retracted = True
    out = decide(ref, False, None, None)
    assert out.label == F8
    assert out.log.decided_by == "f8_retracted_before_citation_timing_met"


# --------------------------------------------------------------------------
# A RESOLVED NON-RETRACTION NOTICE IS AN ANSWER, NOT A MISSING BOUNDARY.
# Observed on PMC7474863: three cited works carrying a pre-citation `ErratumIn`
# and no retraction returned `unresolved / retraction_notice_or_date_unresolved`
# after two full EFetch rounds. Nothing was missing -- the notice resolved, and
# it resolved to a correction. Because `decide` routes F8_UNRESOLVED to
# UNSCOREABLE with `f8_timing_boundary_unresolved`, every such work was dropped
# from the scoreable set and terminated UNJUDGEABLE in Band 2.
# --------------------------------------------------------------------------
def _corrected_records(citing_date: str, erratum_date: str = "2023-07-01",
                       cited_pubtypes=("Journal Article",),
                       with_retraction: bool = False):
    """A cited work whose only linked notice is a published erratum."""
    links = [{"ref_type": "ErratumIn", "pmid": "40", "note": ""}]
    if with_retraction:
        links.append({"ref_type": "RetractionIn", "pmid": "30", "note": ""})
    return {
        "10": {"id": "10", "pub_date": citing_date,
               "pub_date_latest": citing_date},
        "20": {"id": "20", "pub_date": "2020-01-01",
               "pub_date_latest": "2020-01-01",
               "publication_types": list(cited_pubtypes),
               "comments_corrections": links},
        "30": {"id": "30", "pub_date": "2024-01-01",
               "pub_date_latest": "2024-01-01",
               "publication_types": ["Retraction Notice"],
               "comments_corrections": []},
        "40": {"id": "40", "pub_date": erratum_date,
               "pub_date_latest": erratum_date,
               "publication_types": ["Published Erratum"],
               "comments_corrections": []},
    }


def test_a_resolved_erratum_clears_f8_rather_than_holding_it():
    records = _corrected_records("2024-02-01")
    out = assess_f8_timing("20", "10", fetch_meta=records.get)
    assert out.status == F8_CLEAR
    assert out.reason == "only_non_retraction_notice_in_force"


def test_more_resolved_metadata_never_makes_f8_less_judgeable():
    # THE INVERSION, pinned in both directions: a work with no linked notices
    # resolved CLEAR while the SAME work with one resolved erratum did not.
    records = _corrected_records("2024-02-01")
    with_erratum = assess_f8_timing("20", "10", fetch_meta=records.get)
    records["20"]["comments_corrections"] = []
    without = assess_f8_timing("20", "10", fetch_meta=records.get)
    assert without.status == F8_CLEAR
    assert with_erratum.status == F8_CLEAR
    assert with_erratum.status == without.status


def test_an_erratum_never_clears_a_work_pubmed_marks_retracted():
    # THE GUARD. The severity argument covers the RELATIONSHIP list only. A work
    # whose record carries `Retracted Publication` but links no `RetractionIn`
    # never reaches resolve_formal_notice's direct-pubtype fallback, because one
    # erratum link pre-empts it. Clearing on the erratum would be a false
    # negative on a retracted work, so it must stay held.
    records = _corrected_records(
        "2024-02-01",
        cited_pubtypes=("Journal Article", "Retracted Publication"))
    out = assess_f8_timing("20", "10", fetch_meta=records.get)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "retraction_notice_or_date_unresolved"


def test_a_retraction_still_outranks_a_correction():
    # _SEVERITY puts retraction ahead of correction, so adding an erratum to a
    # retracted work must not divert the assessment onto the clearing branch.
    records = _corrected_records(
        "2024-02-01", cited_pubtypes=("Journal Article", "Retracted Publication"),
        with_retraction=True)
    out = assess_f8_timing("20", "10", fetch_meta=records.get)
    assert out.status == F8_QUALIFIED
    assert out.notice_date == "2024-01-01"


def test_the_clearing_answer_is_not_retried():
    # `only_non_retraction_notice_in_force` is a resolved answer, so it must not
    # appear in F8_RETRYABLE_REASONS and must stop the retry loop on attempt 1 --
    # the erratum rows on PMC7474863 each paid two full EFetch rounds to
    # re-derive one deterministic result.
    from .f8_retraction import F8_RETRYABLE_REASONS, assess_f8_timing_with_retry
    assert "only_non_retraction_notice_in_force" not in F8_RETRYABLE_REASONS
    records = _corrected_records("2024-02-01")
    out, attempts = assess_f8_timing_with_retry("20", "10",
                                                fetch_meta=records.get)
    assert out.status == F8_CLEAR
    assert len(attempts) == 1


# --------------------------------------------------------------------------
# F8 OUTRANKS AN UNSCOREABLE TITLE. On PMC7474863 the two retracted Surgisphere
# papers were cited 94 and 93 days after their notices; the `element-citation`
# one was labelled F8 and the `mixed-citation` one -- which carries no
# <article-title>, hence `no_claimed_title` -- was booked UNSCOREABLE, making F8
# recall a function of the publisher's XML markup.
# --------------------------------------------------------------------------
def _retracted_unscoreable_ref(reason: str = "no_claimed_title"):
    ref = Reference("PMC1:R1", "claim [1]", ClaimedRef(title=""))
    ref.log.unscoreable_reason = reason
    ref.log.f8_timing_status = F8_QUALIFIED
    ref.log.f8_timing_gap_days = 93
    ref.log.retracted = True
    return ref


def test_a_retracted_work_is_f8_even_with_no_parsed_title():
    out = decide(_retracted_unscoreable_ref(), False, None, None)
    assert out.label == F8
    assert out.log.decided_by == "f8_retracted_before_citation_timing_met"


def test_an_unscoreable_row_without_a_retraction_stays_unscoreable():
    # The reordering must not cost a genuinely unscoreable row its label. F8
    # fires only on the POSITIVE determination `retracted is True`; False (types
    # fetched, not retracted) and None (never learned) both fall through.
    for state in (False, None):
        ref = _retracted_unscoreable_ref()
        ref.log.retracted = state
        ref.log.f8_timing_status = F8_CLEAR if state is False else F8_UNRESOLVED
        out = decide(ref, False, None, None)
        assert out.label == UNSCOREABLE, state
        assert out.log.decided_by == "unscoreable", state
