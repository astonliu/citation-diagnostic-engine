"""F8 fixtures for a RetractionIn relationship that links NO notice PMID.

Every record below is a PubMed METADATA FACT, re-derivable by EFetch on the
PMIDs named in each fixture; nothing here is a semantic label, a gold standard,
or a base rate, and none of it may be used as a precision numerator or
denominator. The three ``"Retraction."`` records were read live on 2026-08-23.

The defect these fixtures pin: ``resolve_formal_notice`` dated a notice ONLY by
fetching a linked notice record, so a ``RetractionIn`` carrying a ``RefSource``
and no ``PMID`` -- which PubMed emits for a whole publisher class -- resolved
``unresolved``, ``assess_f8_timing`` returned
``retraction_notice_or_date_unresolved``, and the reference was routed
UNSCOREABLE. F8 could not fire on any of them.

The other half of the pin, and the one that costs more if it breaks: the 24 of
300 sampled records that carry the SAME no-PMID self-referential shape while
KEEPING their original ``RETRACTED ARTICLE: ...`` title. PubMed holds no datable
boundary for those, so ``unresolved`` is the CORRECT verdict and a substring
title test turns every one of them into a fabricated accusation dated from the
article's own publication date.
"""
from __future__ import annotations

from .f5_notice import resolve_formal_notice
from .f8_retraction import (F8_CLEAR, F8_QUALIFIED, F8_TIMING_INDETERMINATE,
                            F8_UNRESOLVED, assess_f8_timing)


# ---------------------------------------------------------------------------
# Fixtures. Field-for-field as EFetch returns them.
# ---------------------------------------------------------------------------
def _record(pmid, *, title, doi, pub_date, pub_date_latest,
            issue_pub_date=None, issue_pub_date_latest=None,
            publication_types=("Journal Article",), links=()):
    return {
        "id": pmid, "title": title, "doi": doi,
        "pub_date": pub_date, "pub_date_latest": pub_date_latest,
        "issue_pub_date": pub_date if issue_pub_date is None else issue_pub_date,
        "issue_pub_date_latest": (pub_date_latest
                                  if issue_pub_date_latest is None
                                  else issue_pub_date_latest),
        "publication_types": list(publication_types),
        "comments_corrections": list(links),
    }


def _link(ref_type, *, pmid="", ref_source="", note=""):
    return {"ref_type": ref_type, "pmid": pmid,
            "ref_source": ref_source, "note": note}


def _self_notice_stub(pmid, doi, *, title="Retraction.",
                      pub_date="2023-01-01", pub_date_latest="2023-01-31",
                      article_date="2019-11-20"):
    """A Wiley/BioFactors retraction stub: the PMID's record IS the notice.

    ``pub_date``/``pub_date_latest`` carry the ORIGINAL article's ArticleDate,
    exactly as PubMed leaves them, and the retraction issue is in
    ``issue_pub_date``. The two disagree by four years on the live records, and
    dating the notice from the wrong one is a false-positive F8 generator."""
    return _record(
        pmid, title=title, doi=doi,
        pub_date=article_date, pub_date_latest=article_date,
        issue_pub_date=pub_date, issue_pub_date_latest=pub_date_latest,
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn", ref_source=doi)])


def _citing(pmid, day):
    return _record(pmid, title=f"Citing work {pmid}", doi="",
                   pub_date=day, pub_date_latest=day)


# 31785072 / 31746030 / 31758846 -- title "Retraction.", PubDate 2023 Jan,
# RetractionIn with no PMID and a RefSource equal to the record's own DOI.
_STUBS = {
    "31785072": _self_notice_stub("31785072", "10.1002/biof.1591"),
    "31746030": _self_notice_stub("31746030", "10.1002/biof.1588"),
    "31758846": _self_notice_stub("31758846", "10.1002/biof.1586"),
}

# 39730903 / 40272285 / 39719516 / 39985542 -- the SAME no-PMID self-DOI shape,
# but the ORIGINAL title is kept. PubMed holds no notice date for these.
_ORIGINAL_TITLE_KEPT = {
    "39730903": _record(
        "39730903",
        title=("RETRACTED ARTICLE: Fusion of transfer learning with "
               "nature-inspired dandelion algorithm for autism spectrum "
               "disorder detection and classification using facial features."),
        doi="10.1038/s41598-024-82299-6",
        pub_date="2024-12-28", pub_date_latest="2024-12-28",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn", ref_source="10.1038/s41598-024-82299-6")]),
}


def _fetch(*records):
    store = {}
    for record in records:
        store.update(record if isinstance(record, dict) and "id" not in record
                     else {record["id"]: record})
    return lambda work_id: store.get(str(work_id))


# ---------------------------------------------------------------------------
# Route 1 -- the record IS its own notice.
# ---------------------------------------------------------------------------
def test_a_retraction_stub_dates_the_notice_from_its_own_publication_date():
    """31746030 <- 40363788 (PMC12073636, citing_date_earliest 2025-04-29)."""
    fetch = _fetch(_STUBS, _citing("40363788", "2025-04-29"))
    out = assess_f8_timing("31746030", "40363788", fetch_meta=fetch)
    assert out.status == F8_QUALIFIED
    assert out.reason == "retracted_before_citation_31_day_floor_met"
    assert out.notice_date == "2023-01-31"
    assert out.citing_date_earliest == "2025-04-29"


def test_a_month_only_notice_date_is_taken_at_the_LATEST_day_in_the_interval():
    """``PubDate 2023 Jan`` has no day, so the gap must be the SMALLEST value
    consistent with the metadata. Rounding the other way -- 2023-01-01 -- would
    add 30 days to every gap and manufacture F8 positives at the floor."""
    fetch = _fetch(_STUBS, _citing("40363788", "2025-04-29"))
    out = assess_f8_timing("31746030", "40363788", fetch_meta=fetch)
    assert out.notice_date == "2023-01-31"
    assert out.timing_gap_days == 819          # 2023-01-31 -> 2025-04-29


def test_a_citation_inside_the_notice_interval_never_fires():
    """31785072 <- 36677863 (PMC9867214, 2023-01-13). The notice interval
    2023-01-01..2023-01-31 is NOT wholly before the citation, so the boundary is
    uncertain and the row holds. Whatever else it is, it is not an accusation."""
    fetch = _fetch(_STUBS, _citing("36677863", "2023-01-13"))
    out = assess_f8_timing("31785072", "36677863", fetch_meta=fetch)
    assert out.status != F8_QUALIFIED
    assert out.status == F8_UNRESOLVED
    assert out.reason == "retraction_notice_or_date_unresolved"


def test_a_notice_that_postdates_the_citation_is_clear():
    """31758846 <- 34203307 (PMC8268219, 2021-06-28): the retraction had not
    happened yet when the citation was made."""
    fetch = _fetch(_STUBS, _citing("34203307", "2021-06-28"))
    out = assess_f8_timing("31758846", "34203307", fetch_meta=fetch)
    assert out.status == F8_CLEAR
    assert out.reason == "no_retraction_in_force_at_citation"


def test_the_self_notice_route_names_the_record_itself_as_the_notice():
    status = resolve_formal_notice(
        "31746030", as_of_date="2025-04-29", fetch_meta=_fetch(_STUBS))
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "flagged"
    assert status.date == "2023-01-31"
    assert status.linked_notice_work_id == "31746030"
    assert status.relationship == "RetractionIn"


def test_the_notice_is_dated_from_the_ISSUE_date_not_the_stale_ArticleDate():
    """The other measured false positive.

    PubMed replaces the title and moves the JournalIssue PubDate to the
    retraction's issue, but LEAVES ``ArticleDate Electronic`` at the ORIGINAL
    article's e-publication date -- 31758846 is ArticleDate 2019-11-23, PubDate
    2023 Jan -- and ``pub_date`` prefers ArticleDate. Dating the notice from
    ``pub_date`` backdates the retraction by four years, and this 2021 citation,
    which is CLEAR, becomes a 583-day F8 accusation."""
    fetch = _fetch(_STUBS, _citing("34203307", "2021-06-28"))
    out = assess_f8_timing("31758846", "34203307", fetch_meta=fetch)
    assert out.status == F8_CLEAR
    assert out.timing_gap_days is None


def test_a_self_notice_with_no_issue_date_holds_rather_than_borrowing_one():
    """Fail-closed: the article's own date is not a notice date, so a stub with
    no issue date falls through to the RefSource routes and, finding nothing
    there either, holds."""
    record = _self_notice_stub("5100", "10.1000/own")
    record["issue_pub_date"] = record["issue_pub_date_latest"] = ""
    out = assess_f8_timing(
        "5100", "6001", fetch_meta=_fetch({"5100": record},
                                          _citing("6001", "2025-06-01")))
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


# ---------------------------------------------------------------------------
# The discriminator. All four self-notice conditions are required.
# ---------------------------------------------------------------------------
def test_an_original_title_kept_record_holds_and_is_never_dated_from_itself():
    """The measured false positive this whole change had to avoid.

    39730903 satisfies (a) no linked PMID, (b) self-referential RefSource DOI
    and (c) the Retracted Publication pubtype -- everything except the stub
    title. ``"retract" in title.lower()`` fires on it and dates the notice
    2024-12-28, the ARTICLE's date, which accuses every citation after it."""
    fetch = _fetch(_ORIGINAL_TITLE_KEPT, _citing("99999999", "2025-06-01"))
    out = assess_f8_timing("39730903", "99999999", fetch_meta=fetch)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"
    assert not out.notice_date


def test_a_title_that_merely_discusses_retraction_is_not_a_stub():
    record = _self_notice_stub(
        "5001", "10.1000/self", title="Citation of retracted articles in oncology")
    out = assess_f8_timing(
        "5001", "6001", fetch_meta=_fetch({"5001": record},
                                          _citing("6001", "2025-06-01")))
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


def test_a_stub_title_without_the_self_referential_doi_is_not_a_self_notice():
    """(b) is what establishes that this record IS the notice. Without it a
    stub-titled record with a no-PMID RetractionIn is just an undatable link."""
    record = _self_notice_stub("5002", "10.1000/own")
    record["comments_corrections"] = [
        _link("RetractionIn", ref_source="10.9999/other-notice")]
    out = assess_f8_timing(
        "5002", "6001", fetch_meta=_fetch({"5002": record},
                                          _citing("6001", "2025-06-01")))
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


def test_a_stub_without_the_retracted_publication_pubtype_is_not_a_self_notice():
    record = _self_notice_stub("5003", "10.1000/own")
    record["publication_types"] = ["Journal Article"]
    out = assess_f8_timing(
        "5003", "6001", fetch_meta=_fetch({"5003": record},
                                          _citing("6001", "2025-06-01")))
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


def test_a_stub_title_is_matched_case_and_punctuation_insensitively():
    for title in ("Retraction.", "RETRACTION", "Retraction Notice",
                  "Retraction statement."):
        record = _self_notice_stub("5004", "10.1000/own", title=title)
        out = assess_f8_timing(
            "5004", "6001", fetch_meta=_fetch({"5004": record},
                                              _citing("6001", "2025-06-01")))
        assert out.status == F8_QUALIFIED, title


# ---------------------------------------------------------------------------
# Route 2 -- a RefSource citation string carrying a date.
# ---------------------------------------------------------------------------
def test_a_refsource_citation_string_dates_the_notice():
    record = _record(
        "7001", title="An original title that was kept", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn",
                     ref_source=("Bioengineered. 2024 Dec;15(1):2302653. "
                                 "doi: 10.1080/21655979.2024.2302653."))])
    fetch = _fetch({"7001": record}, _citing("7002", "2025-06-01"))
    out = assess_f8_timing("7001", "7002", fetch_meta=fetch)
    assert out.status == F8_QUALIFIED
    assert out.notice_date == "2024-12-31"      # conservative end of 2024 Dec


def test_a_refsource_date_is_still_compared_against_the_citation():
    record = _record(
        "7003", title="An original title that was kept", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn",
                     ref_source="Bioengineered. 2024 Dec;15(1):2302653.")])
    fetch = _fetch({"7003": record}, _citing("7004", "2020-01-01"))
    assert assess_f8_timing("7003", "7004", fetch_meta=fetch).status == F8_CLEAR


def test_a_day_precise_refsource_date_is_used_as_given():
    record = _record(
        "7005", title="An original title that was kept", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn",
                     ref_source="J Biol Chem. 2021 Aug 19;297(2):101001.")])
    fetch = _fetch({"7005": record}, _citing("7006", "2021-09-19"))
    out = assess_f8_timing("7005", "7006", fetch_meta=fetch)
    assert out.notice_date == "2021-08-19"
    assert out.timing_gap_days == 31
    assert out.status == F8_QUALIFIED


def test_a_bare_doi_refsource_is_never_read_as_a_date():
    """``10.1016/j.cell.2019.05.003`` carries a year-shaped token that is part
    of an IDENTIFIER. Reading it as a date would date a notice from a DOI
    suffix, so every DOI is stripped before the scan and a bare DOI is
    undatable by construction."""
    record = _record(
        "7007", title="An original title that was kept", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn", ref_source="10.1016/j.cell.2019.05.003")])
    fetch = _fetch({"7007": record}, _citing("7008", "2025-06-01"))
    out = assess_f8_timing("7007", "7008", fetch_meta=fetch)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


# ---------------------------------------------------------------------------
# Route 3 -- a RefSource DOI that is NOT the record's own.
# ---------------------------------------------------------------------------
def _foreign_doi_record(pmid="8001"):
    return _record(
        pmid, title="An original title that was kept", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn",
                     ref_source="10.1016/j.cell.2019.05.003")])


def test_a_foreign_refsource_doi_is_resolved_to_a_pmid_and_dated():
    notice = _record("8002", title="Retraction notice", doi="10.1016/j.cell.2019.05.003",
                     pub_date="2022-03-04", pub_date_latest="2022-03-04",
                     publication_types=("Retraction of Publication",))
    fetch = _fetch({"8001": _foreign_doi_record()},
                   {"8002": notice}, _citing("8003", "2025-06-01"))
    out = assess_f8_timing(
        "8001", "8003", fetch_meta=fetch,
        resolve_doi_to_pmid=lambda doi: "8002")
    assert out.status == F8_QUALIFIED
    assert out.notice_date == "2022-03-04"


def test_the_doi_route_is_unavailable_without_a_resolver_and_holds():
    fetch = _fetch({"8001": _foreign_doi_record()}, _citing("8003", "2025-06-01"))
    out = assess_f8_timing("8001", "8003", fetch_meta=fetch)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


def test_a_resolver_that_did_not_answer_holds_under_the_RETRYABLE_reason():
    """A resolver outage is a transport outcome, not a finding that the notice
    is undatable. It must not be reported as the structural hold, because that
    reason is deliberately excluded from the retry set."""
    def boom(_doi):
        raise RuntimeError("id converter returned 502")

    fetch = _fetch({"8001": _foreign_doi_record()}, _citing("8003", "2025-06-01"))
    out = assess_f8_timing("8001", "8003", fetch_meta=fetch,
                           resolve_doi_to_pmid=boom)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "retraction_notice_or_date_unresolved"


def test_the_resolver_is_never_asked_about_the_records_own_doi():
    """A self-referential DOI resolves back to the same record; asking is a
    paid call that can only return the article itself."""
    asked = []
    fetch = _fetch(_ORIGINAL_TITLE_KEPT, _citing("99999999", "2025-06-01"))
    out = assess_f8_timing(
        "39730903", "99999999", fetch_meta=fetch,
        resolve_doi_to_pmid=lambda doi: asked.append(doi))
    assert asked == []
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"


# ---------------------------------------------------------------------------
# Route 4 -- the hold, and everything the change must NOT move.
# ---------------------------------------------------------------------------
def test_an_undatable_no_pmid_row_holds_with_a_diagnosable_reason():
    record = _record(
        "9001", title="An original title that was kept", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn", ref_source="")])
    fetch = _fetch({"9001": record}, _citing("9002", "2025-06-01"))
    out = assess_f8_timing("9001", "9002", fetch_meta=fetch)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "notice_pmid_absent_refsource_undatable"
    assert out.notice_date == ""


def test_the_structural_hold_is_not_retried():
    from .f8_retraction import F8_RETRYABLE_REASONS, assess_f8_timing_with_retry
    assert "notice_pmid_absent_refsource_undatable" not in F8_RETRYABLE_REASONS
    fetch = _fetch(_ORIGINAL_TITLE_KEPT, _citing("99999999", "2025-06-01"))
    _out, attempts = assess_f8_timing_with_retry(
        "39730903", "99999999", fetch_meta=fetch)
    assert len(attempts) == 1


def test_a_linked_notice_pmid_still_outranks_every_new_route():
    """A record whose title IS a stub but which DOES link a notice PMID must
    still be dated from the LINKED notice. 34787073 ("Retracted article: ...")
    and 33837566 ("RETRACTED: ...") are the live cases; a title-first test would
    date both from their own publication date and flip 34787073 from a 130-day
    qualified gap to a clear."""
    subject = _record(
        "34787073", title="Retraction", doi="10.1000/own",
        pub_date="2021-11-16", pub_date_latest="2021-11-16",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("RetractionIn", pmid="38000000", ref_source="10.1000/own")])
    notice = _record("38000000", title="Retraction notice", doi="10.1000/notice",
                     pub_date="2024-01-29", pub_date_latest="2024-01-29",
                     publication_types=("Retraction of Publication",))
    fetch = _fetch({"34787073": subject}, {"38000000": notice},
                   _citing("38847716", "2024-06-07"))
    out = assess_f8_timing("34787073", "38847716", fetch_meta=fetch)
    assert out.status == F8_QUALIFIED
    assert out.notice_date == "2024-01-29"
    assert out.timing_gap_days == 130


def test_a_pre_citation_erratum_and_no_retraction_still_clears():
    subject = _record(
        "9101", title="A perfectly ordinary paper", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        links=[_link("ErratumIn", pmid="9102")])
    erratum = _record("9102", title="Erratum", doi="10.1000/erratum",
                      pub_date="2020-01-01", pub_date_latest="2020-01-01",
                      publication_types=("Published Erratum",))
    fetch = _fetch({"9101": subject}, {"9102": erratum},
                   _citing("9103", "2025-06-01"))
    out = assess_f8_timing("9101", "9103", fetch_meta=fetch)
    assert out.status == F8_CLEAR
    assert out.reason == "only_non_retraction_notice_in_force"


def test_one_erratum_link_still_pre_empts_the_direct_pubtype_fallback():
    """The load-bearing pubtype guard. A work PubMed marks Retracted Publication
    that links only an erratum stays UNRESOLVED -- retracted, with no datable
    notice -- rather than clearing on the erratum."""
    subject = _record(
        "9201", title="A retracted paper with an erratum", doi="10.1000/own",
        pub_date="2019-05-01", pub_date_latest="2019-05-01",
        publication_types=("Journal Article", "Retracted Publication"),
        links=[_link("ErratumIn", pmid="9202")])
    erratum = _record("9202", title="Erratum", doi="10.1000/erratum",
                      pub_date="2020-01-01", pub_date_latest="2020-01-01",
                      publication_types=("Published Erratum",))
    fetch = _fetch({"9201": subject}, {"9202": erratum},
                   _citing("9203", "2025-06-01"))
    out = assess_f8_timing("9201", "9203", fetch_meta=fetch)
    assert out.status == F8_UNRESOLVED
    assert out.reason == "retraction_notice_or_date_unresolved"


def test_the_31_day_floor_is_unmoved_by_the_new_routes():
    """A self-notice dated 30 days before the citation is INDETERMINATE, not a
    fire. The floor is a confidence floor, not a definition."""
    record = _self_notice_stub(
        "9301", "10.1000/own", pub_date="2023-01-01", pub_date_latest="2023-01-01")
    fetch = _fetch({"9301": record}, _citing("9302", "2023-01-31"))
    out = assess_f8_timing("9301", "9302", fetch_meta=fetch)
    assert out.status == F8_TIMING_INDETERMINATE
    assert out.timing_gap_days == 30
    fetch = _fetch({"9301": record}, _citing("9303", "2023-02-01"))
    assert assess_f8_timing("9301", "9303", fetch_meta=fetch).status == \
        F8_QUALIFIED
