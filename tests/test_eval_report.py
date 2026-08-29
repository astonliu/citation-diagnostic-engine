"""Tests for eval_report.build_f2_record -- the canonical, re-bandable F2
run-output record. Verifies the full schema, that raw strings are persisted
alongside the computed verdicts, and the acceptance criteria from the
'persist raw first_author/journal/year_from_dep' task.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_eval_report.py -q
"""
from __future__ import annotations
import json

from cde.refs.eval_report import build_f2_record, _F2_RECORD_KEYS
from cde.refs.biblio_match import (VERDICT_WRONG_PAPER,
                                 VERDICT_SAME_WORK_VARIANT)
from cde.refs.schema import ClaimedRef, RetrievedRecord


def _claimed(title, authors, year, journal, **kw):
    return ClaimedRef(title=title, authors=authors, year=year, journal=journal, **kw)


def _resolved(title, authors, year, journal, **kw):
    return RetrievedRecord(resolved=True, title=title, authors=authors, year=year,
                           journal=journal, **kw)


EXISTING_KEYS = {
    "pmid", "src_pmcid", "written_title", "resolved_title", "written_year",
    "resolved_year", "match_score", "title_sim", "author_match", "year_match",
    "journal_match", "resolved", "flag",
}
NEW_KEYS = {
    "written_first_author", "resolved_first_author", "written_journal",
    "resolved_journal", "resolved_year_from_dep", "citation_id",
    "first_author_match", "doi_match", "written_doi", "resolved_doi",
    "score_below_accept", "same_work_reason", "identity_signals",
    "written_authors", "resolved_authors", "written_raw", "resolved_is_container",
    "resolved_alternate_titles", "resolved_language",
    "resolved_publication_types", "resolved_related_pmids",
    "claimed_pmid", "resolved_pmid",
}


def test_full_schema_present():
    rec = build_f2_record("1", "PMC1",
                          _claimed("A title", ["Lee"], 2020, "J Foo"),
                          _resolved("A title", ["Lee"], 2020, "J Foo"))
    assert EXISTING_KEYS <= set(rec)            # all 13 existing keys
    assert NEW_KEYS <= set(rec)                 # all 5 required new keys
    assert set(rec) == set(_F2_RECORD_KEYS)     # exactly the canonical schema


def test_wrong_paper_persists_raw_strings_and_verdict():
    # 31665581 shape: written != resolved first author -> author_match False.
    c = _claimed("Disseminated varicella infection", ["Smith"], 2019, "N Engl J Med",
                 claimed_pmid="31665581")
    r = _resolved("Purple Urine after Catheterization", ["Placais"], 2019,
                  "N Engl J Med", pmid="31665581")
    rec = build_f2_record("31665581", "PMC9", c, r)
    assert rec["written_first_author"] == "Smith"
    assert rec["resolved_first_author"] == "Placais"
    assert rec["written_first_author"] != rec["resolved_first_author"]
    assert rec["author_match"] is False         # raw strings AND computed verdict
    assert rec["claimed_pmid"] == "31665581"
    assert rec["resolved_pmid"] == "31665581"
    assert rec["flag"] is True
    assert rec["verdict"] == VERDICT_WRONG_PAPER


def test_persists_year_from_dep_flag():
    c = _claimed("Impact of urban structure on COVID-19 spread", ["Aguilar"],
                 2020, "eLife")
    r = _resolved("Impact of urban structure on COVID-19 spread", ["Aguilar"],
                  2022, "eLife", year_from_dep=True)
    rec = build_f2_record("35264587", "PMC2", c, r)
    assert rec["resolved_year_from_dep"] is True
    # control: a record without the flag persists False
    r2 = _resolved("X", ["Aguilar"], 2020, "eLife")
    assert build_f2_record("x", "PMC3", c, r2)["resolved_year_from_dep"] is False


def test_empty_authors_give_empty_string_not_index_error():
    c = _claimed("Sparse ref", [], 2019, "")
    r = _resolved("Different paper", ["Jones"], 2019, "")
    rec = build_f2_record("31665581", "PMC4", c, r)
    assert rec["written_first_author"] == ""
    assert rec["resolved_first_author"] == "Jones"


def test_record_json_roundtrips():
    c = _claimed("A title", ["Lee"], 2020, "J Foo", volume="12", pages="1-9")
    r = _resolved("A title", ["Lee"], 2020, "J Foo", volume="12", pages="1-9")
    rec = build_f2_record("1", "PMC1", c, r)
    line = json.dumps(rec, ensure_ascii=False)
    back = json.loads(line)
    assert back == rec
    assert back["written_volume"] == "12" and back["resolved_pages"] == "1-9"


def test_full_author_rosters_and_medline_identity_fields_roundtrip():
    c = _claimed("A title", ["Lee", "Patel"], 2020, "J Foo",
                 raw="Lee, Patel. A title. J Foo. 2020.")
    r = _resolved(
        "A title", ["Smith", "Lee", "Patel"], 2020, "J Foo",
        is_container=False, alternate_titles=["Un titre"], language="fre",
        publication_types=["Journal Article"],
        related_pmids={"CIN": ["99"]})
    rec = build_f2_record("1", "PMC1", c, r)
    assert rec["written_authors"] == ["Lee", "Patel"]
    assert rec["resolved_authors"] == ["Smith", "Lee", "Patel"]
    assert rec["written_raw"] == "Lee, Patel. A title. J Foo. 2020."
    assert rec["resolved_alternate_titles"] == ["Un titre"]
    assert rec["resolved_language"] == "fre"
    assert rec["resolved_publication_types"] == ["Journal Article"]
    assert rec["resolved_related_pmids"] == {"CIN": ["99"]}


def test_actual_flag_is_separate_from_numeric_threshold():
    # Strong metadata boosts can leave score >= accept even though a confident
    # year disagreement keeps the pair in WRONG_PAPER.
    c = _claimed("Smear layer pathological considerations", ["Pashley"],
                 1948, "Oper Dent")
    r = _resolved("Smear layer physiological considerations", ["Pashley"],
                  1985, "Oper Dent")
    rec = build_f2_record("x", "PMCx", c, r)
    assert rec["verdict"] == VERDICT_WRONG_PAPER
    assert rec["match_score"] >= 0.85
    assert rec["score_below_accept"] is False
    assert rec["flag"] is True


def test_identity_reason_doi_and_citation_occurrence_are_persisted():
    c = _claimed("Smear layer pathological considerations", ["Pashley"],
                 1948, "Oper Dent", claimed_doi="10.1/example")
    r = _resolved("Smear layer physiological considerations", ["Pashley"],
                  1984, "Oper Dent", doi="10.1/example")
    rec = build_f2_record("6396586", "PMC1", c, r,
                          citation_id="PMC1:r7")
    assert rec["citation_id"] == "PMC1:r7"
    assert rec["written_doi"] == rec["resolved_doi"] == "10.1/example"
    assert rec["doi_match"] is True
    assert rec["verdict"] == VERDICT_SAME_WORK_VARIANT
    assert rec["same_work_reason"] == "single_token_metadata_typo"
    assert "year_transposition" in rec["identity_signals"]
    assert rec["flag"] is True
