"""F2-G tests (spec §8): layered authoritative journal identity.

No NLM Serfile snapshot is available in this environment, so the AUTHORITY layers
are exercised with a small synthetic ``JournalAuthority`` (exact-lookup only) and
the containment fallback is verified to be unchanged for the three rejected-rewrite
regression pairs. The real seed-37 abbreviation rows resolve identically once the
pinned snapshot supplies the same alias->canonical-ID mappings.
"""
from __future__ import annotations

import pytest

from cre.f1.journal_identity import (journal_identity, JournalAuthority,
                                     containment_only_census, M_EXACT_TEXT,
                                     M_CONTAINMENT, M_ISSN, M_AUTHORITY_ALIAS,
                                     M_MANUAL_ALIAS, M_AMBIGUOUS)
from cre.f1.biblio_match import field_agreement
from cre.f1.schema import ClaimedRef, RetrievedRecord


# --- containment fallback unchanged: the three rejected-rewrite regression pairs
@pytest.mark.parametrize("left,right", [
    ("Antioxidants", "Antioxidants (Basel)"),
    ("Agric. Food Chem.", "J Agric Food Chem"),
    ("Angew. Chem. Int. Ed.", "Angew Chem Int Ed Engl"),
])
def test_regression_pairs_still_match_via_containment(left, right):
    match, method, authoritative = journal_identity(left, right)
    assert match is True
    assert method == M_CONTAINMENT
    assert authoritative is False        # containment never satisfies F2-C's gate


def test_exact_text_is_non_authoritative():
    match, method, authoritative = journal_identity("Nature", "nature")
    assert match is True and method == M_EXACT_TEXT and authoritative is False


# --- ISSN intersection: authoritative, only when BOTH sides carry ISSN sets
def test_issn_intersection_is_authoritative():
    match, method, authoritative = journal_identity(
        "Some Journal", "A Differently Abbreviated Journal",
        left_issns=["1234-5678"], right_issns=["1234-5678", "9999-0000"])
    assert match is True and method == M_ISSN and authoritative is True


def test_issn_requires_both_sides():
    # A resolved-side ISSN with no written-side ISSN does not create a match.
    match, method, _ = journal_identity("J Foo", "Completely Other Title",
                                        right_issns=["1234-5678"])
    assert method != M_ISSN


# --- authority alias -> canonical NLM ID (exact lookup, ambiguity -> None)
def _authority():
    a = JournalAuthority()
    # The audit's four mechanically-resolvable seed-37 abbreviation pairs.
    a.add("Current Topics in Developmental Biology", "NLM0421041")
    a.add("Curr Top Dev Biol", "NLM0421041")
    a.add("Journal of Bone & Joint Surgery, British Volume", "NLM0375355")
    a.add("J Bone Joint Surg Br", "NLM0375355")
    a.add("Journal of the American Medical Association", "NLM7501160")
    a.add("JAMA", "NLM7501160")
    # A distinct journal, and an ambiguous alias shared by two records.
    a.add("Blood", "NLM7603509")
    a.add("Blood Advances", "NLM101698425")
    a.add("Blood Adv", "NLM101698425")
    a.add("Bulletin", "NLM_A")            # ambiguous: two records
    a.add("Bulletin", "NLM_B")
    return a


@pytest.mark.parametrize("left,right", [
    ("Current Topics in Developmental Biology", "Curr Top Dev Biol"),
    ("Journal of Bone & Joint Surgery, British Volume", "J Bone Joint Surg Br"),
    ("Journal of the American Medical Association", "JAMA"),
])
def test_authority_alias_unique_is_authoritative(left, right):
    match, method, authoritative = journal_identity(left, right, authority=_authority())
    assert match is True and method == M_AUTHORITY_ALIAS and authoritative is True


def test_authority_distinguishes_blood_from_blood_adv():
    # The whole reason the fuzzy rewrite was rejected: Blood != Blood Adv. With an
    # exact authority they map to DIFFERENT canonical IDs -> authoritative False.
    match, method, authoritative = journal_identity("Blood", "Blood Adv",
                                                    authority=_authority())
    assert match is False and method == M_AUTHORITY_ALIAS and authoritative is True


def test_ambiguous_alias_yields_none_not_true():
    match, method, authoritative = journal_identity("Bulletin", "Bulletin",
                                                    authority=_authority())
    assert match is None and method == M_AMBIGUOUS and authoritative is False


def test_manual_alias_method_label():
    a = JournalAuthority()
    a.add("Europ Moll Biology Organ Rep", "NLM101201960", method=M_MANUAL_ALIAS)
    a.add("EMBO Rep", "NLM101201960", method=M_MANUAL_ALIAS)
    match, method, authoritative = journal_identity(
        "Europ Moll Biology Organ Rep", "EMBO Rep", authority=a)
    assert match is True and method == M_MANUAL_ALIAS and authoritative is True


def test_authority_abstains_when_not_in_table_falls_through_to_containment():
    # A journal absent from the authority falls through to the containment result.
    match, method, _ = journal_identity("Antioxidants", "Antioxidants (Basel)",
                                        authority=_authority())
    assert match is True and method == M_CONTAINMENT


# --- residual census
def test_containment_only_census_ranks_by_frequency():
    pairs = [("Antioxidants", "Antioxidants (Basel)"),
             ("Antioxidants", "Antioxidants (Basel)"),
             ("Agric. Food Chem.", "J Agric Food Chem"),
             ("Nature", "Nature")]            # exact_text, not containment -> excluded
    census = containment_only_census(pairs)
    assert census[0]["count"] == 2
    assert census[0]["written_journal"] == "Antioxidants"
    assert all(row["written_journal"] != "Nature" for row in census)


# --- field_agreement surfaces the method + authoritative flag
def test_field_agreement_exposes_method_and_authoritative():
    c = ClaimedRef(journal="Antioxidants")
    r = RetrievedRecord(resolved=True, journal="Antioxidants (Basel)")
    fa = field_agreement(c, r)
    assert fa.journal_match is True
    assert fa.journal_match_method == M_CONTAINMENT
    assert fa.journal_match_authoritative is False
