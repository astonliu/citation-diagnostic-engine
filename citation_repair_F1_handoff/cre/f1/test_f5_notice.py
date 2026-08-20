"""Offline, cutoff-aware formal-notice fixtures for F5."""
from __future__ import annotations

import pytest

from .f5_notice import resolve_formal_notice


def _meta(work_id, *, pub_date="2020-01-01", pub_date_latest=None,
          publication_types=(), links=()):
    return {
        "id": work_id,
        "title": f"Work {work_id}",
        "pub_date": pub_date,
        "pub_date_latest": pub_date_latest or pub_date,
        "publication_types": list(publication_types),
        "comments_corrections": list(links),
    }


def _link(ref_type, pmid):
    return {"ref_type": ref_type, "pmid": pmid, "note": ""}


@pytest.mark.parametrize(
    ("ref_type", "kind", "role"),
    [("RetractionIn", "retraction", "retracted_article"),
     ("ErratumIn", "correction", "corrected_article"),
     ("ExpressionOfConcernIn", "eoc", "eoc_subject")],
)
def test_linked_subject_notice_is_resolved_as_of_cutoff(ref_type, kind, role):
    records = {
        "111": _meta("111", links=[_link(ref_type, "222")]),
        "222": _meta("222", pub_date="2024-01-01"),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == kind
    assert status.notice_resolution == "flagged"
    assert status.source_role == role
    assert status.linked_notice_work_id == "222"
    assert status.date_status == "compared"


def test_retraction_notice_article_is_not_inverted_into_retracted_subject():
    records = {
        "222": _meta(
            "222", publication_types=["Retraction of Publication"],
            links=[_link("RetractionOf", "111")]),
    }
    status = resolve_formal_notice(
        "222", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "none"
    assert status.notice_resolution == "resolved_clear"
    assert status.source_role == "retraction_notice"


def test_notice_after_cutoff_is_recorded_but_not_applied():
    records = {
        "111": _meta("111", links=[_link("RetractionIn", "222")]),
        "222": _meta("222", pub_date="2024-01-02"),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "none"
    assert status.notice_resolution == "resolved_clear"
    assert status.date_status == "after_cutoff"
    assert status.linked_notice_work_id == "222"
    assert status.source_role == "retracted_article"


@pytest.mark.parametrize(
    ("notice_meta", "date_status", "lookup_status"),
    [(_meta("222", pub_date="", pub_date_latest=""), "absent", "ok"),
     (_meta("222", pub_date="2024/01/01"), "unparseable", "ok"),
     (None, "absent", "no_record")],
)
def test_unresolved_link_can_never_be_reported_clear(
        notice_meta, date_status, lookup_status):
    records = {
        "111": _meta("111", links=[_link("RetractionIn", "222")]),
        "222": notice_meta,
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"
    assert status.date_status == date_status
    assert status.lookup_status == lookup_status


def test_lookup_exception_is_unresolved_not_clean():
    def fail(_work_id):
        raise RuntimeError("synthetic transport failure")

    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=fail)
    assert status.notice_resolution == "unresolved"
    assert status.lookup_status == "failure"


def test_missing_metadata_identity_is_unresolved_not_assumed():
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01",
        fetch_meta=lambda _wid: {"publication_types": []})
    assert status.notice_resolution == "unresolved"
    assert status.lookup_status == "no_record"


def test_unresolved_retraction_outranks_in_force_correction():
    records = {
        "111": _meta("111", links=[
            _link("ErratumIn", "222"), _link("RetractionIn", "333")]),
        "222": _meta("222", pub_date="2023-01-01"),
        "333": _meta("333", pub_date=""),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"
    assert status.linked_notice_work_id == "333"


def test_relevant_relationship_with_missing_linked_pmid_is_unresolved():
    records = {
        "111": _meta("111", links=[_link("ExpressionOfConcernIn", "")]),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "eoc"
    assert status.notice_resolution == "unresolved"
    assert status.lookup_status == "no_record"


def test_uncertain_publication_interval_straddling_cutoff_is_unresolved():
    records = {
        "111": _meta("111", links=[_link("ErratumIn", "222")]),
        "222": _meta(
            "222", pub_date="2023-01-01", pub_date_latest="2025-01-01"),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "correction"
    assert status.notice_resolution == "unresolved"
    assert status.date_status == "boundary_uncertain"


def test_non_ascii_linked_pmid_is_unresolved_not_fetched_as_valid_identity():
    records = {
        "111": _meta("111", links=[_link("RetractionIn", "１２３")]),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"
    assert status.linked_notice_work_id is None


def test_conflicting_subject_and_notice_roles_never_false_clear():
    subject = _meta(
        "111", publication_types=["Retracted Publication"],
        links=[_link("RetractionOf", "222")])
    subject["notice_date"] = "2023-01-01"
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta={"111": subject}.get)
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"


def test_corrected_republished_original_is_flagged_by_cutoff():
    records = {
        "111": _meta(
            "111", links=[_link("CorrectedandRepublishedIn", "222")]),
        "222": _meta("222", pub_date="2023-01-01"),
    }
    status = resolve_formal_notice(
        "111", as_of_date="2024-01-01", fetch_meta=records.get)
    assert status.notice_kind == "correction"
    assert status.notice_resolution == "flagged"
    assert status.source_role == "corrected_article"
