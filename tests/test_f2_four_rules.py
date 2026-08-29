"""C1-C5: roman context gate, author plausibility, page suffix, corporate un-inversion.

Four false-positive mechanisms isolated in the seed-43 flagged pool. Fixtures are
the seed-43 rows verbatim.

THE GUARDS COME FIRST. The two genuine Part I / Part II pairs below are the reason
C1 is a context gate and not a loosening: both share a DOI AND a first author, so
the ONLY thing separating them from a same-work pair is the series ordinal. They
were written and observed passing BEFORE C1 was implemented, so a regression in
them is attributable rather than ambient.
"""
from __future__ import annotations

import pytest

from cde.refs.biblio_match import VERDICT_WRONG_PAPER, flag_verdict
from cde.refs.schema import ClaimedRef, RetrievedRecord
from cde.refs.work_identity import _series_conflict


# ==========================================================================
# GUARDS -- genuine series pairs that must STAY wrong-paper
# ==========================================================================

def test_guard_PMC11805211_R4_tmd_part_ii_vs_part_i_stays_wrong_paper():
    """Part II vs part I, SHARED DOI 10.1038/bdj.2006.122, same first author, same
    volume, same page. The series ordinal is the only discriminating evidence."""
    c = ClaimedRef(
        title="TMD and occlusion Part II. Damned if we do? Occlusion: The interface "
              "of dentistry, orthodontics, and TMD",
        authors=["Luther"], year=2007, journal="Br Dent J", volume="202",
        pages="E2", claimed_doi="10.1038/bdj.2006.122")
    r = RetrievedRecord(
        resolved=True,
        title="TMD and occlusion part I. Damned if we do? Occlusion: the interface "
              "of dentistry  and orthodontics.",
        authors=["Luther"], year=2007, journal="Br Dent J", volume="202",
        pages="E2; discussion 38-9", doi="10.1038/bdj.2006.122")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_guard_PMC10382254_B16_qsar_ii_vs_i_stays_wrong_paper():
    """': II. Verification' vs ': I. The decision scheme', SHARED DOI, shared first
    author. Segment-initial ordinal followed by a period -- the exact shape the
    context gate must still catch."""
    c = ClaimedRef(
        title="The QSAR toolbox automated read-across workflow for predicting acute "
              "oral toxicity: II. Verification and validation",
        authors=["Kutsarova"], year=2021, journal="Regul Toxicol Pharmacol",
        volume="20", pages="100194", claimed_doi="10.1016/j.yrtph.2021.105015")
    r = RetrievedRecord(
        resolved=True,
        title="Automated read-across workflow for predicting acute oral toxicity: I. "
              "The  decision scheme in the QSAR toolbox.",
        authors=["Kutsarova", "Mehmed"], year=2021, journal="Regul Toxicol Pharmacol",
        volume="125", pages="105015", doi="10.1016/j.yrtph.2021.105015")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


# ==========================================================================
# C1 -- the context gate
# ==========================================================================

@pytest.mark.parametrize("left,right", [
    # segment-initial, followed by '.'
    ("AM1-BCC model: I. Method", "AM1-BCC model: II. Parameterization"),
    # series keyword
    ("TMD and occlusion Part II. Damned if we do?",
     "TMD and occlusion part I. Damned if we do?"),
    ("…acute oral toxicity: II. Verification and validation",
     "…acute oral toxicity: I. The decision scheme"),
])
def test_C1_real_series_ordinals_still_conflict(left, right):
    assert _series_conflict(left, right) is True


@pytest.mark.parametrize("left,right", [
    # a bare I inside a parenthetical protein label, and a stray x from "X-ray"
    ("X-ray crystal structure of the Fe-only hydrogenase ( Cp I) from "
     "Clostridium pasteurianum to 1.8 angstrom resolution",
     "X-ray crystal structure of the Fe-only hydrogenase (CpI) from Clostridium  "
     "pasteurianum to 1.8 angstrom resolution."),
    # the multiplication sign vs the letter x in a hybrid binomial
    ("Evaluation of drought response of two poplar clones ( Populus × canadensis "
     "Mönch 'I-214' and P. deltoides Marsh. 'Dvina')",
     "Evaluation of drought response of two poplar clones (Populus x canadensis "
     "Monch  'I-214' and P. deltoides Marsh. 'Dvina')"),
])
def test_C1_roman_letters_outside_series_context_do_not_conflict(left, right):
    assert _series_conflict(left, right) is False


def test_C1_identical_roman_sets_never_conflict():
    """The unmatched-only restriction: an earlier revision compared every cross-pair
    with ta != tb, including ordinals present on BOTH sides, and added 43 rows to
    review_wrong_paper frame-wide."""
    assert _series_conflict("Study of vitamin D and outcome I. Design",
                            "Study of vitamin D and outcome I. Design") is False


# ==========================================================================
# C2 -- author plausibility
# ==========================================================================

@pytest.mark.parametrize("name", ["ICH S7A", "ICH M3 (R2)"])
def test_C2_structural_corruption_is_implausible(name):
    from cde.refs.biblio_match import _implausible_author
    assert _implausible_author(name) is True


def test_C2_does_not_discard_what_the_codebase_already_repairs():
    """SPEC DEVIATION, deliberate. The C2 acceptance matrix lists
    '...Committee: 3' as implausible, but D2b (landed at 0fce7a4) RECOVERS that
    exact name by stripping the colon-introduced section number, and its test
    asserts first_author_match is True. A blanket colon rule would silently undo
    a finer landed rule and turn a True into None, losing information rather than
    adding it. So C2 normalizes away what is already recoverable -- the D2b section
    number, and a parenthetical acronym gloss -- and judges only the residue."""
    from cde.refs.biblio_match import _implausible_author
    assert _implausible_author(
        "American Diabetes Association Professional Practice Committee: 3") is False
    assert _implausible_author("World Medical Association (WMA)") is False


@pytest.mark.parametrize("name", [
    "Lee", "Kost-Alimova", "van der Waals", "O'Brien",
    "American Diabetes Association", "World Health Organization",
    "Garcia Lopez Martinez", "Van Der Berg", "Maria Del Carmen Garcia Lopez",
    "Ferreira Dos Santos Silva", "de la Cruz",
])
def test_C2_real_names_are_plausible(name):
    """This list is what caught a three-or-more-capitalized-tokens rule: every
    threshold that spares these also spares real corruptions, so the rule is
    STRUCTURAL (digit or bracket/colon) and nothing else."""
    from cde.refs.biblio_match import _implausible_author
    assert _implausible_author(name) is False


def test_C2_filtered_to_empty_yields_None_never_False():
    """Tri-state discipline: 'ICH S7A' is not a person and cannot DISAGREE with
    one. An absent comparison is None."""
    from cde.refs.biblio_match import match_score
    c = ClaimedRef(title="Guidance on safety pharmacology studies", authors=["ICH S7A"],
                   year=2005, journal="Fed Regist")
    r = RetrievedRecord(resolved=True, title="Guidance on safety pharmacology studies",
                        authors=["Food and Drug Administration, HHS"], year=2005,
                        journal="Fed Regist")
    fields = match_score(c, r).fields
    assert fields.first_author_match is None
    assert fields.author_match is None


# ==========================================================================
# C3 -- MEDLINE editorial page suffixes
# ==========================================================================

@pytest.mark.parametrize("raw,clean", [
    ("212-8; quiz 276", "212-8"),
    ("E2; discussion 38-9", "E2"),
    ("1083-91; author reply 92", "1083-91"),
])
def test_C3_editorial_suffix_is_stripped(raw, clean):
    """Asserted as EQUALITY WITH THE CLEAN FORM, not against a literal: the elided
    end page still expands afterwards (F2-A, '212-8' -> '212-218'), so pinning a
    literal would pin that unrelated behaviour too. What matters for pages_match is
    that the suffix cannot make the two sides differ."""
    from cde.refs.biblio_match import _canonical_pages
    assert _canonical_pages(raw) == _canonical_pages(clean)


@pytest.mark.parametrize("raw", ["S90-S102", "1365-1368", "e022455", ""])
def test_C3_ordinary_page_forms_are_untouched(raw):
    """The suffix rule must not disturb any page shape that carries no ';'."""
    from cde.refs.biblio_match import _canonical_pages
    assert ";" not in raw            # fixture sanity
    before = _canonical_pages(raw)
    assert before == _canonical_pages(raw)   # deterministic
    assert "quiz" not in before and "discussion" not in before


# ==========================================================================
# C4 -- corporate name un-inversion
# ==========================================================================

def test_C4_uninverts_only_on_an_exact_surname_and_initials_match():
    from cde.refs.biblio_match import _uninvert_corporate
    assert _uninvert_corporate(
        "Association AD", ["American Diabetes Association"]) == "American Diabetes Association"


def test_C4_leaves_an_ordinary_vancouver_personal_name_alone():
    """Both conditions are required. An ordinary personal name has no multi-word
    roster entry whose leading initials match, so it must come back None."""
    from cde.refs.biblio_match import _uninvert_corporate
    assert _uninvert_corporate("Smith JA", ["Smith", "Jones"]) is None


@pytest.mark.parametrize("written,roster", [
    ("Association AD", ["American Diabetes Society"]),      # last word differs
    ("Association XY", ["American Diabetes Association"]),  # initials differ
    ("Association", ["American Diabetes Association"]),     # no initials at all
])
def test_C4_near_misses_return_None(written, roster):
    from cde.refs.biblio_match import _uninvert_corporate
    assert _uninvert_corporate(written, roster) is None


# ==========================================================================
# C5 -- a repair must never CLEAR, and must never divert a row it did not move
# ==========================================================================

def test_C5_a_repaired_row_that_would_clear_is_audited_instead():
    """C4 un-inverts 'Association AD' and then EVERY field agrees, so without C5
    the row would take the clean-match short-circuit and leave the audited
    population unseen."""
    c = ClaimedRef(
        title="9. Pharmacologic approaches to glycemic treatment: Standards of "
              "medical care in diabetes-2019",
        authors=["Association AD"], year=2019, journal="Diabetes Care",
        volume="42", pages="S90-S102", claimed_doi="10.2337/dc19-s009")
    r = RetrievedRecord(
        resolved=True,
        title="9. Pharmacologic Approaches to Glycemic Treatment: Standards of "
              "Medical Care in  Diabetes-2019.",
        authors=["American Diabetes Association"], year=2019, journal="Diabetes Care",
        volume="42", pages="S90-S102", doi="10.2337/dc19-s009")
    verdict, m = flag_verdict(c, r)
    assert verdict != "match"
    assert m.same_work_reason == "corporate_name_inverted"


def test_C5_does_not_divert_a_row_the_repair_never_moved():
    """THE INVARIANT THAT REGRESSED TWICE. A repair that changes a FIELD has not
    necessarily changed the VERDICT: a row already matching cleanly, whose pages
    differ only by an editorial suffix, was never in the wrong-paper band and must
    stay in `match`. Diverting on 'a repair happened' rather than 'the repair moved
    it' took 59 rows out of `match` on the seed-37 frame, then 42 after a partial
    fix. C5 is gated on the UNREPAIRED comparison carrying a confident
    disagreement."""
    c = ClaimedRef(title="A perfectly ordinary and highly distinctive study title",
                   authors=["Smith"], year=2019, journal="J Test", volume="42",
                   pages="212-8", claimed_doi="10.1000/ok")
    r = RetrievedRecord(resolved=True,
                        title="A perfectly ordinary and highly distinctive study title",
                        authors=["Smith"], year=2019, journal="J Test", volume="42",
                        pages="212-8; quiz 276", doi="10.1000/ok")
    assert flag_verdict(c, r)[0] == "match"
