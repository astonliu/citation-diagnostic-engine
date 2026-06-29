"""Tests for eval_report.build_f2_record -- the canonical, re-bandable F2
run-output record. Verifies the full schema, that raw strings are persisted
alongside the computed verdicts, and the acceptance criteria from the
'persist raw first_author/journal/year_from_dep' task.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_eval_report.py -q
"""
from __future__ import annotations
import json

from cre.f1.eval_report import build_f2_record, _F2_RECORD_KEYS
from cre.f1.biblio_match import VERDICT_WRONG_PAPER
from cre.f1.schema import ClaimedRef, RetrievedRecord


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
    "resolved_journal", "resolved_year_from_dep",
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
