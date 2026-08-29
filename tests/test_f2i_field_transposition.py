"""F2-I tests (spec §13): a title parked in the wrong slot -> not-yet-judgeable.

The rule is GATED on a populated NLM authority (no snapshot here), so the tests
populate the module-level JOURNAL_AUTHORITY with a couple of real serials and
restore it after. Without that population the rule is inert (proved below).
"""
from __future__ import annotations

import pytest

from cde.refs import journal_identity as ji
from cde.refs.unscoreable import classify_unscoreable
from cde.refs.schema import ClaimedRef


@pytest.fixture
def populated_authority(monkeypatch):
    a = ji.JournalAuthority()
    # Real serials that MUST NOT be reclaimed as transposed titles.
    a.add("Proceedings of the National Academy of Sciences of the United States "
          "of America", "NLM7505876")
    a.add("Journal of Speech, Language, and Hearing Research", "NLM9705610")
    a.add("Frontiers in Public Health", "NLM101616579")
    monkeypatch.setattr(ji, "JOURNAL_AUTHORITY", a)
    # unscoreable imported the name; point it at the same populated table.
    import cde.refs.unscoreable as u
    monkeypatch.setattr(u, "JOURNAL_AUTHORITY", a)
    return a


def _c(**kw):
    return ClaimedRef(**kw)


def test_journal_holds_title_is_field_transposition(populated_authority):
    c = _c(title="", journal="Foldseek: fast and accurate protein structure search",
           authors=["van Kempen"], claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, None)
    assert bucket == "field_transposition_journal_holds_title"


def test_authors_hold_title_is_field_transposition(populated_authority):
    c = _c(title="", journal="",
           authors=["Seroprevalence of human brucellosis in a rural area of "
                    "western Kenya"], claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, None)
    assert bucket == "field_transposition_authors_hold_title"


def test_real_long_journal_is_not_reclaimed(populated_authority):
    # 13-word real journal -> resolves to an NlmId -> stays no_claimed_title.
    c = _c(title="", journal="Proceedings of the National Academy of Sciences of "
           "the United States of America", claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, None)
    assert bucket == "no_claimed_title"


def test_real_journal_with_commas_is_not_reclaimed(populated_authority):
    c = _c(title="", journal="Journal of Speech, Language, and Hearing Research",
           claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, None)
    assert bucket == "no_claimed_title"


def test_short_journal_never_reclaimed(populated_authority):
    # Under 6 words -> not title-shaped, stays no_claimed_title regardless.
    c = _c(title="", journal="Front. Public Health", claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, None)
    assert bucket == "no_claimed_title"


def test_f2i_is_inert_without_a_snapshot():
    # No populated_authority fixture: the module authority is empty, so a
    # transposed title-in-journal is NOT reclaimed -- the rule degenerates to
    # nothing (never to a word-count misfire), so behavior is unchanged.
    assert ji.JOURNAL_AUTHORITY.is_empty()
    c = _c(title="", journal="Foldseek: fast and accurate protein structure search",
           claimed_pmid="x")
    bucket, _ = classify_unscoreable(c, None)
    assert bucket == "no_claimed_title"
