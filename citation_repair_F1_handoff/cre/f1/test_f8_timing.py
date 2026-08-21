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
