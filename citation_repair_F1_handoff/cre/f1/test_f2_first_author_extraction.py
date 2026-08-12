"""D1-D7: first-author extraction and equivalence (seed 43, 2026-08-12).

Seven of the 21 confirmed seed-43 false positives carry a written first author
that was extracted or normalized badly, not a genuinely different one. Fixtures
are the seed-43 strings verbatim.

THE LINE THIS FILE HOLDS. ``first_author_equivalent`` is a required conjunct of
BOTH RULE A (``shared_doi_same_work``) and RULE A2 (``_doi_anchored_same_work``),
so it is the only guard between an exact shared DOI and a same-work route. A fix
that corrects how ONE name is NORMALIZED (a section number appended to an
organization; a hyphen treated as a component boundary exactly as a space already
is) does not change what counts as identity. A fix that extends what counts as a
MATCH (any token inside an unparsed contributor run; an acronym expanded against
the other name's words; a character-level truncation) does, and is out of scope
here -- see the declined defects below, each of which keeps its xfail.
"""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import VERDICT_WRONG_PAPER, flag_verdict, match_score
from cre.f1.schema import ClaimedRef, RetrievedRecord


def _pair(written_first, resolved_first, *, w_rest=(), r_rest=()):
    c = ClaimedRef(title="A study of something specific", authors=[written_first, *w_rest],
                   year=2020, journal="J Test", volume="10", pages="1-9")
    r = RetrievedRecord(resolved=True, title="A study of something specific",
                        authors=[resolved_first, *r_rest], year=2020, journal="J Test",
                        volume="10", pages="1-9")
    return c, r


def _fa(written_first, resolved_first, **kw):
    c, r = _pair(written_first, resolved_first, **kw)
    return match_score(c, r).fields.first_author_match


# =====================================================================
# FIXED
# =====================================================================

def test_D2b_corporate_name_with_trailing_section_number():
    """ADA Standards of Care: the citing paper appends the SECTION number to the
    committee name ("...Committee: 3"). A colon-introduced trailing number is a
    document locator, not part of the organization's name."""
    assert _fa("American Diabetes Association Professional Practice Committee: 3",
               "American Diabetes Association Professional Practice Committee") is True


@pytest.mark.xfail(strict=True, reason=(
    "D4 WITHHELD by ZD 2026-08-12 -- implemented, measured, and deliberately not "
    "landed. Aliasing a hyphenated compound surname component-wise (as a "
    "space-separated one already is) does fix Alimova/Kost-Alimova, but it moves "
    "seed-43 PMC8114883 into `match` while doi_match is False: "
    "has_confident_disagreement excludes doi_match, so the first-author signal was "
    "the only thing keeping a DOI-DISAGREEING row in the audited pool. Dropping "
    "such a row out of the review population is a recall loss in the matcher, "
    "which is non-negotiable. The real fix is to add the DOI disagreement to "
    "has_confident_disagreement; that is corpus-wide -- 340 of seed 43's match "
    "rows carry doi_match is False (0.61%), up to 4x the current review volume "
    "with an unknown wrong-paper/quarantine split -- so it is a redesign with its "
    "own spec, measurement and seed. Recorded as LR-5 and CONTRADICTIONS#F2-29."))
def test_D4_hyphenated_compound_surname_truncated_to_one_component():
    """JATS truncates a compound surname to one component. Space-separated
    compounds are ALREADY aliased component-wise ("Romeo" / "Romeo Casabona");
    a hyphen is the same relation spelled differently."""
    assert _fa("Alimova", "Kost-Alimova") is True


def test_D6_absent_written_author_is_None_never_False():
    """PMC12341016:REF2 -- the reference carries no author at all (title + URL +
    PMID only), so the parser correctly extracted nothing. Tri-state contract:
    absent is None, never a disagreement."""
    c = ClaimedRef(title="The alcohol, smoking and substance involvement screening test",
                   authors=[], year=2010, journal="Addiction")
    r = RetrievedRecord(resolved=True, title="Validation of the ASSIST",
                        authors=["Humeniuk", "Ali"], year=2010, journal="Addiction")
    assert match_score(c, r).fields.first_author_match is None


# =====================================================================
# DECLINED -- each keeps its xfail, with the reason it was declined
# =====================================================================

@pytest.mark.xfail(strict=True, reason=(
    "D1 DECLINED. 'Keehoon Lee Donggeun Kim Sang Sun Yoona' is an unparsed "
    "contributor RUN in one slot, given-name-first, with no delimiter. Matching it "
    "would mean accepting any token inside the run as the first author, which "
    "conflates ROSTER overlap with POSITION agreement -- the distinction "
    "first_author_equivalent exists to make, and a conjunct of RULE A/A2. The "
    "spec's own question (one <surname> element vs several concatenated by the "
    "parser) cannot be settled without PMC10538001's source JATS, which is not "
    "available locally; that answer decides whether the fix belongs in "
    "_surnames_under or _first_author_value."))
def test_D1_contributor_run_in_the_first_author_slot():
    assert _fa("Keehoon Lee Donggeun Kim Sang Sun Yoona", "Lee",
               r_rest=("Lee", "Kim", "Yoon")) is True


@pytest.mark.xfail(strict=True, reason=(
    "D2a DECLINED. 'Association AD' is 'American Diabetes Association' with the "
    "leading words replaced by their initials. Matching it requires expanding an "
    "acronym against the OTHER name's words, which is exactly what "
    "test_corporate_abbreviation_is_a_token_change_and_stays_high forbids ('AAP' "
    "vs 'American Academy of Pediatrics' must stay a conflict). Restricting the "
    "expansion to a TRAILING initials token would thread that needle, but it "
    "extends what counts as corporate identity and so relaxes a RULE A/A2 "
    "conjunct -- out of scope. Reported to ZD as the concrete option."))
def test_D2a_corporate_name_mangled_to_surname_plus_initials():
    assert _fa("Association AD", "American Diabetes Association") is True


@pytest.mark.xfail(strict=True, reason=(
    "D3 DECLINED -- publisher-side, belongs in the register not the code. "
    "PMC12414753:B34's reference text itself begins 'Anticancer Research . Breath "
    "of Danger: ...', i.e. the citing paper placed the JOURNAL name in the author "
    "position and the source JATS tagged it as the author. Nothing downstream can "
    "distinguish that from a real surname without re-parsing the reference."))
def test_D3_journal_word_in_the_author_field():
    assert _fa("Anticancer", "Mokbel") is True


@pytest.mark.xfail(strict=True, reason=(
    "D5 DECLINED, wiring question settled: NOT wired, and deliberately so. "
    "_first_author_typo's own contract is 'Narrow personal-surname typo signal; "
    "never a global author match', and it is used only as a gated signal inside "
    "assess_same_work (3 sites), never in first_author_equivalent. Wiring it into "
    "that conjunct would let a JaroWinkler>=0.91 surname similarity satisfy RULE A "
    "and RULE A2 -- replacing identity with similarity, which the spec's "
    "primary-risk section and the out-of-scope list both forbid. A narrower "
    "strict-prefix rule (Hanaich/Hanaichi: prefix, minlen 7; all five "
    "must-stay-False pairs: not prefixes, JW<=0.78) would separate cleanly and is "
    "reported to ZD as the concrete option, but it is still a relaxation of the "
    "same conjunct."))
def test_D5_one_character_truncated_surname():
    assert _fa("Hanaich", "Hanaichi") is True


@pytest.mark.xfail(strict=True, reason=(
    "D7 DECLINED -- upstream MEDLINE defect, verified via PubMed, NOT _au_surname. "
    "PMID 23891539's record has LastName/ForeName SWAPPED for every author: "
    "last_name='Catherine' fore_name='Quiblier', last_name='Susanna' "
    "fore_name='Wood', and so on for all six. _au_surname reads LastName and is "
    "behaving correctly; 'fixing' it would corrupt every correctly-ordered record. "
    "This is the wrong fix the spec warned the connector would prevent."))
def test_D7_resolved_side_yielded_a_given_name():
    assert _fa("Quiblier", "Catherine") is True


# =====================================================================
# The rows that must NOT move -- the most important assertions here
# =====================================================================

@pytest.mark.parametrize("written,resolved", [
    ("Yang", "Zhang"), ("Yan", "Liu"), ("Gupta", "Pandey"),
    ("Prise", "Jayaraman"), ("Peiró", "Margarit"),
])
def test_genuinely_different_rosters_stay_a_first_author_disagreement(written, resolved):
    """Same-work by OTHER evidence, but the first authors genuinely differ. A fix
    that turns any of these True has replaced identity with similarity."""
    assert _fa(written, resolved) is False


def test_report_vs_article_carriers_stay_wrong_paper():
    """The two genuine wrong papers RULE A2's first draft cleared. The
    first-author conjunct plus _roster_contradicted is what stops them, so any
    loosening of first-author matching is checked here first."""
    who = ClaimedRef(
        title="The World Health Organization Report 2002: Reducing Risks, Promoting Healthy Life 2002",
        authors=["WHO"], year=2020, journal="", claimed_doi="10.1080/1357628031000116808")
    who_r = RetrievedRecord(
        resolved=True, title="The world health report 2002 - reducing risks, promoting healthy life.",
        authors=["Guilbert"], year=2003, journal="Educ Health (Abingdon)",
        doi="10.1080/1357628031000116808", volume="16", pages="230",
        publication_types=["Letter"])
    assert flag_verdict(who, who_r)[0] == VERDICT_WRONG_PAPER

    nice = ClaimedRef(title="Evidence standards framework for digital health technologies",
                      authors=[], year=None, journal="NICE",
                      claimed_doi="10.1177/20552076211018617")
    nice_r = RetrievedRecord(
        resolved=True,
        title="The NICE Evidence Standards Framework for digital health and care technologies -  "
              "Developing and maintaining an innovative evidence framework with global impact.",
        authors=["Unsworth", "Dillon", "Collinson", "Powell", "Salmon"], year=2021,
        journal="Digit Health", doi="10.1177/20552076211018617", volume="7",
        pages="20552076211018617")
    assert flag_verdict(nice, nice_r)[0] == VERDICT_WRONG_PAPER


def test_numbered_group_designators_stay_wrong_paper():
    """The acceptance requirement is at the VERDICT level, and it holds: a bare
    trailing designator is not merged, because the D2b strip is colon-introduced
    ONLY and _corporate_token_equivalent refuses a truncation shorter than 5."""
    c = ClaimedRef(title="Randomized trial results",
                   authors=["National Clinical Study Group A"], year=2020,
                   journal="Trials", volume="21", pages="1-9", claimed_doi="10.1000/grp")
    r = RetrievedRecord(resolved=True, title="Randomized trial results",
                        authors=["National Clinical Study Group AB"], year=2020,
                        journal="Trials", volume="21", pages="1-9", doi="10.1000/grp")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


@pytest.mark.xfail(strict=True, reason=(
    "PRE-EXISTING over-match, measured at aa118ca BEFORE this change and NOT "
    "introduced by it (verified against a pristine baseline copy): at the "
    "FIRST-AUTHOR level 'Group A'/'Group AB' and 'Working Group 1'/'Working Group "
    "2' already match, because _first_author_aliases adds tokens[0] as an alias "
    "for compound surnames ('Romeo' for 'Romeo Casabona') and both names share "
    "their leading token. The verdict is still wrong_paper (test above), so the "
    "acceptance row holds; only the field-level signal is loose. The "
    "compound-surname alias is load-bearing elsewhere, so narrowing it is its own "
    "change with its own measurement. Recorded, not authorized."))
def test_KNOWN_numbered_group_designators_match_at_the_field_level():
    assert _fa("Working Group 1", "Working Group 2") is False
