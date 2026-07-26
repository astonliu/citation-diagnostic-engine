"""F2 matcher-revision tests (F2_MATCHER_REVISION_SPEC, 2026-07-25).

Each change lands with its own acceptance-matrix rows, verified independently and
in dependency order A -> B -> C -> D -> E -> F. Fixtures use ONLY the literal
strings from the spec's acceptance matrix (naturally-occurring data discipline).

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_matcher_revision.py -q
"""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import (_canonical_pages, field_agreement,
                                 is_preprint_source)
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
