"""F2 matcher-revision tests (F2_MATCHER_REVISION_SPEC, 2026-07-25).

Each change lands with its own acceptance-matrix rows, verified independently and
in dependency order A -> B -> C -> D -> E -> F. Fixtures use ONLY the literal
strings from the spec's acceptance matrix (naturally-occurring data discipline).

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_matcher_revision.py -q
"""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import (_canonical_pages, field_agreement,
                                 flag_verdict, is_preprint_source,
                                 is_preprint_resolved, VERDICT_MATCH,
                                 VERDICT_SAME_WORK_VARIANT, VERDICT_WRONG_PAPER)
from cre.f1.schema import ClaimedRef, RetrievedRecord


# ======================================================================
# F2-A -- page-range canonicalization
# ======================================================================
def _pages_match(written: str, resolved: str):
    fa = field_agreement(ClaimedRef(pages=written),
                         RetrievedRecord(resolved=True, pages=resolved))
    return fa.pages_match


# Acceptance matrix rows (written_pages, resolved_pages, expected pages_match).
# `is`-comparison against the tri-state literal, never a falsy check.
@pytest.mark.parametrize("written,resolved,expected", [
    ("141-144", "141-4", True),
    ("925–8.e4", "925-928.e4", True),   # 925–8.e4 vs 925-928.e4
    ("3143-3421", "3143-421", True),
    ("1-12", "1-12", True),
    ("9-11", "9-12", False),
    ("", "141-4", None),
])
def test_f2a_pages_match_acceptance(written, resolved, expected):
    assert _pages_match(written, resolved) is expected


# Direct canonicalizer checks on the spec's verified inputs.
@pytest.mark.parametrize("raw,canon", [
    ("141-4", "141-144"),
    ("1083-91", "1083-1091"),
    ("3143-421", "3143-3421"),
    ("117–32", "117-132"),     # en dash
    ("71-8", "71-78"),
    ("1-12", "1-12"),
    ("9-11", "9-11"),
    ("925–8.e4", "925-928.e4"),
    ("S100", "s100"),               # non-range: folded, otherwise unchanged
    ("e0224455", "e0224455"),
    ("CD010442", "cd010442"),
])
def test_f2a_canonical_pages(raw, canon):
    assert _canonical_pages(raw) == canon


def test_f2a_pages_match_stays_tristate_when_absent():
    assert _pages_match("", "") is None
    assert _pages_match("141-4", "") is None


# ======================================================================
# F2-B -- 10.1101 preprint signal requires a date stamp; resolved-side flag
# ======================================================================
@pytest.mark.parametrize("doi,expected", [
    ("10.1101/2020.02.08.939660", True),    # date-stamped bioRxiv/medRxiv
    ("10.1101/gr.209601.116", False),       # Genome Research (CSHL journal)
    ("10.1101/gad.1255404", False),         # Genes & Development (CSHL journal)
    ("10.48550/arXiv.2101.00001", True),    # arXiv, preprint-only registrant
])
def test_f2b_claimed_preprint_prefix_discrimination(doi, expected):
    claimed = ClaimedRef(claimed_doi=doi)
    assert is_preprint_source(claimed) is expected


# The 79 CSHL-journal rows must stop reading as a preprint on the RESOLVED side
# too (the date-stamp fix protects both directions).
@pytest.mark.parametrize("doi,journal,ptypes,expected", [
    ("10.1101/2020.02.08.939660", "bioRxiv", [], True),
    ("10.1101/gr.209601.116", "Genome Res", [], False),   # CSHL journal
    ("10.1101/gad.1255404", "Genes Dev", [], False),      # CSHL journal
    ("10.1038/s41586-020-2649-2", "Nature", ["Journal Article"], False),
    ("", "medRxiv preprint", [], True),                   # preprint-server journal
    ("", "N Engl J Med", ["Preprint"], True),             # MEDLINE Preprint pubtype
])
def test_f2b_resolved_preprint_discrimination(doi, journal, ptypes, expected):
    rec = RetrievedRecord(resolved=True, doi=doi, journal=journal,
                          publication_types=ptypes)
    assert is_preprint_resolved(rec) is expected


def test_f2b_resolved_preprint_elevates_ordinary_citation_to_high():
    # Citation reads as an ordinary article (no preprint markers); claimed PMID
    # resolves to a date-stamped bioRxiv record -> evidence toward a fault, HIGH.
    c = ClaimedRef(title="A convolutional network approach to variant detection",
                   authors=["Aguilar"], year=2022, journal="Bioinformatics")
    r = RetrievedRecord(resolved=True,
                        title="Deep learning for genomic variant calling in cancer",
                        authors=["Aguilar"], year=2020, journal="bioRxiv",
                        doi="10.1101/2020.07.15.204305")
    v, m = flag_verdict(c, r)
    assert m.resolved_preprint is True
    assert m.same_work_reason == "resolved_preprint_target"
    assert v == VERDICT_WRONG_PAPER


def test_f2b_claimed_preprint_not_elevated_by_resolved_side():
    # Claimed side IS a preprint cite -> the resolved-preprint elevation must not
    # fire (a preprint->preprint cite is not this subtype).
    c = ClaimedRef(title="Deep learning for genomic variant calling in cancer",
                   authors=["Aguilar"], year=2020, journal="bioRxiv")
    r = RetrievedRecord(resolved=True,
                        title="Deep learning for genomic variant calling in cancer",
                        authors=["Aguilar"], year=2020, journal="bioRxiv")
    v, m = flag_verdict(c, r)
    assert m.resolved_preprint is False


def test_f2b_cshl_journal_resolved_not_elevated():
    # Resolved to a CSHL journal (non-date-stamped 10.1101) -> NOT a preprint,
    # must not be elevated by the resolved-side signal.
    c = ClaimedRef(title="Chromatin architecture in development",
                   authors=["Lee"], year=2017, journal="Genome Res")
    r = RetrievedRecord(resolved=True,
                        title="Chromatin architecture in development",
                        authors=["Lee"], year=2017, journal="Genome Res",
                        doi="10.1101/gr.209601.116")
    v, m = flag_verdict(c, r)
    assert m.resolved_preprint is False
    assert v == VERDICT_MATCH
