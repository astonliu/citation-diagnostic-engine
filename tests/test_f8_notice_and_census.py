"""The parts of the 2026-08-16 F8 spec that survive its premise being wrong.

THAT PREMISE, for whoever reads this next: the spec's headline finding is that
F8 "does not exist in this package" and that nothing assigns the label. That was
true of the branch it was written against. It is NOT true here -- the F1/F8
deterministic retraction gate (``decide.py``'s F8 branch, ``run.retraction_state``,
``ncbi_meta.is_retracted``) landed 2026-08-15 and is mid-merge into this tree. So
the spec's Defects 1 and 2 and its "state that F8 is unimplemented" item are all
held pending ZD's re-scope, and only what is INDEPENDENT of the premise is tested
here:

  * Defect 4 -- the five date/lookup/publication-type defects in
    ``f5_seams.make_check_formal_notice``. That is F5's seam; the F8 gate does not
    touch it, and every one of the five is a live correctness bug today.
  * Defect 3 -- the per-label counter that drops the rows F8 fires on.

Offline: every metadata reader here is a stub.
"""
from __future__ import annotations

import pytest

from cde.diagnose import f5_seams as s
from cde.diagnose.supersession import NoticeStatus


def notice(pubtypes=("Retracted Publication",), notice_date="2022-01-01",
           *, meta=...):
    """check_formal_notice over a one-record stub."""
    if meta is ...:
        meta = {"publication_types": list(pubtypes)}
        if notice_date is not None:
            meta["notice_date"] = notice_date
    return s.make_check_formal_notice(
        lambda w: ({**meta, "id": w} if isinstance(meta, dict) and meta else meta))


# --------------------------------------------------------------------------
# Defect 4.1 / 4.2 -- dates were compared as raw strings, and failed asymmetrically.
# --------------------------------------------------------------------------
def test_a_slash_dated_notice_is_in_force_not_cleared():
    """`str(date) > str(as_of_date)` is lexicographic: "2024/01/15" sorts ABOVE
    "2024-06-01" because "/" (0x2F) is above "-" (0x2D). A real January
    retraction therefore read as CLEAR when asked about June."""
    status = notice(notice_date="2024/01/15")("W", as_of_date="2024-06-01")
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"
    assert status.date_status == "unparseable"
    assert status.date_raw == "2024/01/15"


def test_a_text_dated_notice_holds_instead_of_crashing_mid_run():
    """The old failure was ASYMMETRIC: a non-ISO date that sorted LATER cleared
    silently, while one that sorted earlier fell into NoticeStatus's validator
    and raised, taking the run with it. Both now take the same path."""
    status = notice(notice_date="15 Jan 2024")("W", as_of_date="2024-06-01")
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"
    assert status.date_status == "unparseable"
    assert status.date_raw == "15 Jan 2024"


def test_both_malformed_shapes_now_agree():
    a = notice(notice_date="2024/01/15")("W", as_of_date="2024-06-01")
    b = notice(notice_date="15 Jan 2024")("W", as_of_date="2024-06-01")
    assert (a.notice_kind, a.notice_resolution, a.date_status) == \
           (b.notice_kind, b.notice_resolution, b.date_status)


def test_an_unparseable_as_of_date_does_not_clear_the_notice():
    status = notice(notice_date="2022-01-01")("W", as_of_date="not-a-date")
    assert status.notice_kind == "none"
    assert status.notice_resolution == "unresolved"
    assert status.date_status == "as_of_unavailable"


def test_a_real_comparison_is_recorded_as_one():
    """The load-bearing behaviour is unchanged, and now says that it happened."""
    later = notice(notice_date="2025-06-01")("W", as_of_date="2024-01-01")
    assert later.notice_kind == "none"          # did not exist yet
    assert later.date_status == "after_cutoff"
    earlier = notice(notice_date="2022-01-01")("W", as_of_date="2024-01-01")
    assert earlier.notice_kind == "retraction"
    assert earlier.date_status == "compared"


# --------------------------------------------------------------------------
# Defect 4.3 -- a missing notice_date silently disabled the timing gate.
# --------------------------------------------------------------------------
def test_an_undated_notice_stays_in_force_and_says_the_gate_did_not_run():
    """Fail-closed is the right OUTCOME -- an undated retraction should not
    clear -- but "in force at every as_of_date" looked exactly like a comparison
    that had been made and passed. Nothing in production populates notice_date
    today, so this is the default case, not the edge."""
    status = notice(notice_date=None)("W", as_of_date="1900-01-01")
    assert status.notice_kind == "retraction"
    assert status.notice_resolution == "unresolved"
    assert status.date_status == "absent"
    assert status.date_raw is None


# --------------------------------------------------------------------------
# Defect 4.4 -- a lookup failure was indistinguishable from a clean record.
# --------------------------------------------------------------------------
def test_no_answer_is_not_a_clean_record():
    """`meta = fetch_meta(work_id) or {}` collapsed None and {} into one value,
    and both returned resolved_clear -- an outage reading as "not retracted".
    This module already guards that exact confusion for retrieval."""
    failed = s.make_check_formal_notice(lambda w: None)("W", as_of_date="2024-06-01")
    clean = s.make_check_formal_notice(
        lambda w: {"id": w, "publication_types": ["Journal Article"]})(
            "W", as_of_date="2024-06-01")
    assert failed.lookup_status == "no_record"
    assert failed.notice_resolution == "unresolved"      # holds, never clears
    assert clean.lookup_status == "ok"
    assert clean.notice_resolution == "resolved_clear"
    assert failed != clean


def test_an_empty_record_is_also_not_an_answer():
    empty = s.make_check_formal_notice(lambda w: {})("W", as_of_date="2024-06-01")
    assert empty.lookup_status == "no_record"
    assert empty.notice_resolution == "unresolved"


# --------------------------------------------------------------------------
# Defect 4.5 -- the PT inversion.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("pubtype", ["Retraction of Publication", "Retraction Notice"])
def test_citing_a_retraction_notice_is_not_citing_a_retracted_paper(pubtype):
    """PubMed's "Retracted Publication" (this article WAS retracted) and
    "Retraction of Publication" (this article IS the notice) mean opposite
    things. _RETRACTION_TYPES carried both, so citing a notice -- legitimate,
    and routine in meta-research -- read as citing a retracted paper."""
    status = notice(pubtypes=(pubtype,), notice_date=None)("W", as_of_date="2024-06-01")
    assert status.notice_kind == "none"
    assert status.source_role == "retraction_notice"


def test_a_genuinely_retracted_paper_is_still_flagged():
    status = notice(pubtypes=("Retracted Publication",))("W", as_of_date="2024-06-01")
    assert status.notice_kind == "retraction"
    assert status.source_role == "retracted_article"


def test_a_notice_is_distinguishable_from_a_paper_with_no_notice_type():
    """Returning kind="none" for both is right and insufficient: it is how the
    inversion stayed invisible."""
    n = notice(pubtypes=("Retraction Notice",), notice_date=None)(
        "W", as_of_date="2024-06-01")
    plain = notice(pubtypes=("Journal Article",), notice_date=None)(
        "W", as_of_date="2024-06-01")
    assert n.notice_kind == plain.notice_kind == "none"
    assert n.source_role == "retraction_notice"
    assert plain.source_role == "no_notice_type"


def test_matching_is_exact_so_a_looser_pattern_cannot_invert_it():
    """A substring rule like "retract" matches both types and would flag every
    notice while missing every retracted paper -- and still look like it works."""
    assert s._notice_from_pubtypes(["Retracted Publication"]) == \
        ("retraction", "retracted_article")
    assert s._notice_from_pubtypes(["Retraction of Publication"]) == \
        ("none", "retraction_notice")
    assert s._notice_from_pubtypes(["retracted publication  "]) == \
        ("retraction", "retracted_article")


def test_corrections_and_eoc_are_unchanged():
    assert s._notice_from_pubtypes(["Published Erratum"])[0] == "correction"
    assert s._notice_from_pubtypes(["Expression of Concern"])[0] == "eoc"


# --------------------------------------------------------------------------
# The new NoticeStatus fields are validated, and default to asserting nothing.
# --------------------------------------------------------------------------
def test_a_hand_built_status_claims_no_lookup():
    assert NoticeStatus().lookup_status == "not_performed"
    assert NoticeStatus().date_status == "not_applicable"
    assert NoticeStatus().source_role == "unknown"


@pytest.mark.parametrize("kw", [
    {"lookup_status": "maybe"}, {"date_status": "sometime"},
    {"source_role": "whatever"}, {"date_raw": 20240115},
])
def test_the_new_fields_are_enumerated_not_free_text(kw):
    with pytest.raises(ValueError):
        NoticeStatus(**kw)


# --------------------------------------------------------------------------
# Defect 3 -- the only per-label F8 counter was biased against F8.
# --------------------------------------------------------------------------
def test_a_label_on_a_reference_with_no_citance_is_still_counted(
        tmp_path, monkeypatch):
    """jb.exclusion_reason (no citance / no cited PMID) runs BEFORE the pre-band
    gate, so an F8-labelled reference lacking a citing sentence was booked as
    excluded_no_citance and its label counted nowhere. schema.py records that
    F1/F2/F8 carry no atomic claims -- so the references those labels fire on are
    precisely the ones this counter dropped."""
    from cde.diagnose import pipeline as jr
    from .test_judgment_run import (abstract_ok, extractor_of,
                                    judge_established, make_ref)

    no_citance = make_ref("c")
    no_citance.citance = ""
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [no_citance])
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(tmp_path / "out"),
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
        preband_disposition={"c": "F8"}, model="test-model")

    # The row is still excluded for the reason that actually claimed it ...
    assert manifest["counts"].get("excluded_no_citance") == 1
    # ... and was previously invisible to every per-label counter.
    assert manifest["excluded_preband_by_label"] == {}
    # The census sees it.
    assert manifest["preband_label_census"] == {"F8": 1}


def test_the_two_counters_agree_when_no_earlier_gate_fires(tmp_path, monkeypatch):
    """With a citance present the pre-band gate claims the row, and both
    counters see it. The numbers are meant to differ only by what an earlier
    gate took first."""
    from cde.diagnose import pipeline as jr
    from .test_judgment_run import (abstract_ok, extractor_of,
                                    judge_established, make_ref)

    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("c")])
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(tmp_path / "out"),
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
        preband_disposition={"c": "F8"}, model="test-model")
    assert manifest["excluded_preband_by_label"] == {"F8": 1}
    assert manifest["preband_label_census"] == {"F8": 1}
