"""Acceptance matrix for the same-work rule (language / container / subset).

Fixtures live in ``f2_samework_hard_cases.json`` beside this file. Cases 5, 7, 13,
18 and 19 are the ones that break a naive implementation and must NOT be "fixed":
each is a row the rule is required to leave flagged.

PATH NOTE: the spec's verification command names ``tests/test_f2_samework_rule.py``
and ``cre.f1.tools.replay_seed``. Neither exists -- this project has no ``tests/``
directory and no ``tools`` package; every test lives in ``cre/f1/test_*.py``. Placed
per the project's own layout rather than creating a second convention.
"""
from __future__ import annotations

import json
import os

import pytest

from cre.f1.f2_samework_rule import (
    ADDRESS_AGREEMENT_MIN, AUTHOR_FUZZ_MIN, JOURNAL_FUZZ_MIN,
    LANG_MIN_STOPWORDS, SUBSET_MIN_CONTENT_WORDS,
    address_agreement, author_evidence, classify_samework, entry_container,
    entry_for, entry_language, entry_subset,
)

_FIXTURES = json.load(open(
    os.path.join(os.path.dirname(__file__), "f2_samework_hard_cases.json"),
    encoding="utf-8"))


@pytest.mark.parametrize("case", _FIXTURES, ids=[c["id"] for c in _FIXTURES])
def test_acceptance_matrix(case):
    verdict, entry, n = classify_samework(case["w"], case["r"])
    assert entry == case["entry"], (
        f"{case['id']}: expected entry {case['entry']!r}, got {entry!r} "
        f"({case['defends']})")
    assert verdict == case["verdict"], (
        f"{case['id']}: expected {case['verdict']}, got {verdict} "
        f"(entry={entry}, address={n}) -- {case['defends']}")


def test_the_fixture_file_covers_the_whole_matrix():
    assert len(_FIXTURES) == 24
    assert len({c["id"] for c in _FIXTURES}) == 24


# =====================================================================
# The rows a naive implementation "fixes" -- pinned separately so a regression
# names the invariant rather than a fixture id.
# =====================================================================

def test_no_address_only_path_exists_at_any_threshold():
    """11 genuine F2 rows in seed 45 share DOI, volume, first page AND journal
    with the resolved record and are still different papers. A row with a full
    address and no entry must stay flagged."""
    w = {"title": "Compliance and adherence in glaucoma management",
         "first_author": "Robin", "year": 2011, "volume": "59", "pages": "93",
         "journal": "Indian J Ophthalmol", "doi": "10.4103/0301-4738.77008"}
    r = {"title": "A comparative study between intravitreal triamcinolone and "
                  "bevacizumab for macular oedema.",
         "first_author": "Lim", "year": 2011, "volume": "59", "pages": "93",
         "journal": "Indian J Ophthalmol", "doi": "10.4103/0301-4738.77008"}
    assert address_agreement(w, r) >= 4          # the address is FULL
    assert entry_for(w, r) is None               # and no entry fires
    assert classify_samework(w, r)[0] == "FLAG"  # so it stays flagged


def test_container_markers_never_match_as_substrings():
    """`press` as a bare substring fired on 5 of 8 container hits in seed 45 --
    inside `expressed`, `expression`, and `press de banca`."""
    assert entry_container("Gene expression profiling", "Proteomic analysis") is False
    assert entry_container("Efecto del press de banca", "Bench press effect") is False
    assert entry_container("Ewing Sarcoma. StatPearls.", "Ewing Sarcoma.") is True
    assert entry_container("Atlas of anatomy, Wolters Kluwer", "Atlas of anatomy") is True


def test_container_comparison_is_exact_not_fuzzy():
    from cre.f1.f2_samework_rule import container_same_work
    assert container_same_work("Clavicle Fractures. StatPearls.", "Clavicle Fracture.", True) is False
    assert container_same_work("Clavicle Fracture. StatPearls.", "Clavicle Fracture.", True) is True


def test_container_ignores_year_because_a_living_chapter_is_redated():
    """The StatPearls rows differ by four years (2022 vs 2026) and are the same
    chapter; year is deliberately not consulted for container rows."""
    w = {"title": "Ewing Sarcoma. StatPearls. Treasure", "first_author": "Durer",
         "year": 2022, "volume": "", "pages": "", "journal": "", "doi": ""}
    r = {"title": "Ewing Sarcoma.", "first_author": "Durer",
         "year": 2026, "volume": "", "pages": "", "journal": "", "doi": ""}
    assert classify_samework(w, r) == ("SAME_WORK", "container", 0)


def test_author_evidence_is_tri_state_and_None_is_not_False():
    # corporate on one side -> NOT DISCRIMINATIVE, not a disagreement
    assert author_evidence("EFSA Panel on Dietetic Products", "Turck") is None
    assert author_evidence("", "Turck") is None
    assert author_evidence("Nagendra babu", "Nagendrababu") is True
    assert author_evidence("Moune", "Nyouma Moune") is True
    assert author_evidence("Chen", "Chan") is False
    # the invariant itself
    assert author_evidence("EFSA Panel on Dietetic Products", "Turck") is not False


def test_volume_is_never_fuzzed():
    """114 vs 144 and 8 vs 298 are real parser errors in seed 45."""
    base = {"year": 2022, "pages": "102268", "journal": "J Biol Chem", "doi": ""}
    assert address_agreement({**base, "volume": "114"}, {**base, "volume": "144"}) \
        < address_agreement({**base, "volume": "114"}, {**base, "volume": "114"})


def test_first_page_strips_letter_prefix_and_leading_zeros():
    base = {"year": 2021, "volume": "12", "journal": "Nat Commun", "doi": ""}
    n = address_agreement({**base, "pages": "6040"}, {**base, "pages": "e06040"})
    assert n == address_agreement({**base, "pages": "6040"}, {**base, "pages": "6040"})


def test_language_never_reads_the_record_language_field():
    """A citing author can translate a title PubMed holds only in English; 4 of
    seed 45's 10 cross-language rows have LA - eng records. The signal is the
    title strings, so a record-side language field cannot be consulted."""
    import inspect
    import re as _re
    from cre.f1 import f2_samework_rule as m
    # Strip docstrings before grepping: the module DOCUMENTS that it does not read
    # the field, and an earlier version of this test tripped on its own explanation.
    src = _re.sub(r'"""..*?"""', "", inspect.getsource(m), flags=_re.S)
    for forbidden in ('resolved_language', '.get("language")', ".get('language')",
                      '["language"]', "['language']"):
        assert forbidden not in src, f"the rule must not read {forbidden}"


def test_every_threshold_is_a_named_constant():
    assert (ADDRESS_AGREEMENT_MIN, AUTHOR_FUZZ_MIN, JOURNAL_FUZZ_MIN,
            SUBSET_MIN_CONTENT_WORDS, LANG_MIN_STOPWORDS) == (4, 85, 80, 4, 2)


def test_there_is_no_abstain_state():
    """Coverage stays 1.000: every row is SAME_WORK or FLAG, never a third state."""
    for case in _FIXTURES:
        assert classify_samework(case["w"], case["r"])[0] in ("SAME_WORK", "FLAG")


# =====================================================================
# Seed-43 DOI guards -- the count is PRINTED, because a guard that cannot fail
# is not a guard (on the seed-45 reband file 0 of 4 are present).
# =====================================================================
SEED43_DOI_GUARDS = ("PMC9262164:CIT0038", "PMC12441761:ps70070-bib-0016",
                     "PMC9280646:B31", "PMC8578828:mpp13125-bib-0169")


def test_seed43_doi_guards_report_their_presence(capsys):
    ids = {c["id"] for c in _FIXTURES}
    present = [g for g in SEED43_DOI_GUARDS if g in ids]
    print(f"seed-43 DOI guards present in the file under test: "
          f"{len(present)} of {len(SEED43_DOI_GUARDS)} {present}")
    captured = capsys.readouterr().out
    assert "seed-43 DOI guards present" in captured
    # Stated rather than asserted away: these four are seed-43 rows and are NOT in
    # the seed-45 fixture set, so this guard is reporting 0 of 4. It is kept
    # because the count is the finding -- an assert over an empty set passes while
    # testing nothing, which is what this test exists to make visible.
    assert len(present) == 0


# =====================================================================
# Entry 4 -- corporate author. The MIRROR of entries 1-3: title strong,
# author non-discriminative.
# =====================================================================
_CORP = json.load(open(
    os.path.join(os.path.dirname(__file__), "f2_corporate_author_cases.json"),
    encoding="utf-8"))


@pytest.mark.parametrize("case", _CORP, ids=[c["id"] for c in _CORP])
def test_corporate_acceptance_matrix(case):
    verdict, entry, n = classify_samework(case["w"], case["r"])
    assert entry == case["entry"], (
        f"{case['id']}: expected entry {case['entry']!r}, got {entry!r} ({case['defends']})")
    assert verdict == case["verdict"], (
        f"{case['id']}: expected {case['verdict']}, got {verdict} "
        f"(entry={entry}, address={n}) -- {case['defends']}")


def test_corporate_fixture_file_is_complete():
    assert len(_CORP) == 9 and len({c["id"] for c in _CORP}) == 9


def test_title_sim_is_not_a_path_into_entry_4():
    """The rule this entry most needs: a similarity score above 0.95 survives a
    SWAPPED content word, and entry 4 has no title-shape evidence to fall back on."""
    from cre.f1.biblio_match import title_sim
    from cre.f1.f2_samework_rule import title_equivalent
    a = "The effect of smoking on cardiovascular outcomes in older adults"
    b = "The effect of alcohol on cardiovascular outcomes in older adults."
    assert title_sim(a, b) >= 0.90          # similar by score...
    assert title_equivalent(a, b)[0] is False   # ...and NOT equivalent


def test_entry_4_requires_reason_corporate_not_merely_None():
    """`missing` is a thin record, not evidence the author cannot discriminate.
    Without this check the entry would accept any blank author slot."""
    from cre.f1.f2_samework_rule import author_evidence_detail, entry_corporate
    assert author_evidence_detail([], ["Turck"]) == (None, "missing")
    fires, why = entry_corporate(
        {"title": "Dietary reference values for sodium", "authors": []},
        {"title": "Dietary reference values for sodium.", "authors": ["Turck"]})
    assert fires is False and why["author_reason"] == "missing"


def test_entry_4_leaves_disagreeing_personal_rosters_flagged():
    """The most important negative: without the corporate-reason check this entry
    would clear every row whose title matches and whose authors merely differ,
    which is a large slice of real F2."""
    from cre.f1.f2_samework_rule import entry_corporate
    fires, why = entry_corporate(
        {"title": "Outcomes after transcatheter aortic valve replacement", "authors": ["Smith", "Jones"]},
        {"title": "Outcomes after transcatheter aortic valve replacement.", "authors": ["Nguyen", "Okonkwo"]})
    assert fires is False and why["author_reason"] == "disagree"


def test_entry_4_runs_last_so_it_cannot_move_the_earlier_fixtures():
    from cre.f1.f2_samework_rule import entry_for
    for case in _FIXTURES:
        entry = entry_for(case["w"], case["r"])
        if case["entry"] is not None:
            assert entry == case["entry"], case["id"]
