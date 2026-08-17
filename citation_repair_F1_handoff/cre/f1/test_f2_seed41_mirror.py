"""Adversarial fixtures mirroring the seed-41 flagged pool -- TWIN design.

REVISION 2 (2026-08-11), after an implementer review found four fixture bugs in
revision 1. Every correction is noted at the case it applies to. Revision 1's
mistakes were mine, not the code's; production was not changed for any of them.

These are hand-built UNIT FIXTURES, not data. They are never gold, never a
denominator, and no verdict here is a label. They pin behaviour at the boundary
of each defect family found in `f2_seed41_seed41_01_HIGH.jsonl`
(SHA-256 198fe1fc9f59417316fa4248b2b3ed0d3dda28540bd588dd7b413ded620c557a).

Every case is a TWIN: two records differing as little as possible that must land
on OPPOSITE verdicts. A fix clearing the CLEAR twin must not clear the STAY twin.

`xfail(strict=True)` marks a KNOWN OPEN DEFECT -- a written backlog entry. A
strict xfail that starts PASSING is a signal to flip it, not to delete it. None
of them authorizes a production change; see the spec's scope section.
"""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import (
    VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT, VERDICT_WRONG_PAPER, flag_verdict,
)
from cre.f1.parser import parse_pmc_xml
from cre.f1.schema import ClaimedRef, RetrievedRecord
from cre.f1.work_identity import _series_conflict, journal_equivalent


def _c(**kw):
    return ClaimedRef(**kw)


def _r(**kw):
    return RetrievedRecord(resolved=True, **kw)


def _cleared(verdict):
    """The row left the HIGH review band. MATCH and the audited quarantine both
    count; which one is decided by the flag_verdict return ladder, not here."""
    return verdict in (VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT)


def _one_ref(tmp_path, inner_xml, pmcid="PMC9999999"):
    """Parse a single <ref> through the real parser and return its ClaimedRef."""
    path = tmp_path / f"{pmcid}.xml"
    path.write_text(
        '<?xml version="1.0"?><pmc-articleset><article>'
        '<front><article-meta><title-group><article-title>Citing paper'
        '</article-title></title-group></article-meta></front>'
        f'<back><ref-list>{inner_xml}</ref-list></back>'
        '</article></pmc-articleset>', encoding="utf-8")
    refs = parse_pmc_xml(str(path), source_pmcid=pmcid)
    assert refs, "fixture XML produced no references"
    return refs[0].claimed


_BJA = dict(year=1997, journal="Br J Anaesth", volume="79", pages="740-743")


# =====================================================================
# FAMILY 1 -- roman tokens synthesized by abbreviation punctuation.
# `_ROMAN_RE` matches i|ii|iii|iv|v|vi|vii|viii|ix|x on \b boundaries and a
# period is a boundary, so "I.v." -> {i, v} while "Iv" -> {iv}.
# =====================================================================

# RESOLVED 2026-08-12 by C1, NOT PREDICTED BY ITS SPEC. The context gate makes the
# dotted-abbreviation mask's non-compositionality moot: an undotted 'Iv' beside a
# real numeral is not in series context, so it never enters the set that the mask
# was failing to clean. Reported as an unpredicted XPASS rather than absorbed.
def test_1a_KNOWN_DEFECT_mask_is_not_compositional_beside_a_real_numeral():
    c = _c(title="Iv anesthesia: II. Pharmacokinetics", authors=["Hase", "Oda"],
           claimed_doi="10.1093/bja/79.6.740", **_BJA)
    r = _r(title="I.v. anaesthesia: II. Pharmacokinetics.", authors=["Hase", "Oda"],
           doi="10.1093/bja/79.6.740", **_BJA)
    assert _cleared(flag_verdict(c, r)[0])


def test_1b_TWIN_a_masked_abbreviation_must_not_blind_a_real_series_difference():
    """REV-2 CORRECTION: revision 1 asserted the final verdict, so its protection
    leaned on composite scoring and the return ladder. Assert the predicate."""
    assert _series_conflict("Iv anesthesia: II. Pharmacokinetics",
                            "I.v. anaesthesia: III. Metabolism.") is True


def test_1c_dotted_iv_alone_no_longer_conflicts():
    assert _series_conflict("Iv fentanyl decreases the clearance of midazolam",
                            "I.v. fentanyl decreases the clearance of midazolam.") is False


def test_1d_reversed_dotted_form_vi_no_longer_conflicts():
    assert _series_conflict("Vi loading protocols in adult sedation",
                            "V.i. loading protocols in adult sedation.") is False


def test_1e_TWIN_undotted_roman_series_conflict_survives_the_mask():
    assert _series_conflict("Airway management, Part IV: outcomes",
                            "Airway management, Part VI: outcomes.") is True


def test_1f_TWIN_genuine_section_labels_survive_the_mask():
    """The AM1-BCC guard in predicate form."""
    assert _series_conflict("AM1-BCC model: I. Method",
                            "AM1-BCC model: II. Parameterization") is True


def test_1g_undotted_roman_series_conflict_with_a_SHARED_doi_stays_high():
    c = _c(title="Airway management, Part IV: outcomes", authors=["Duthie"],
           claimed_doi="10.1093/bja/58.9.950", **_BJA)
    r = _r(title="Airway management, Part VI: outcomes.", authors=["Duthie"],
           doi="10.1093/bja/58.9.950", **_BJA)
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_1h_hyphen_boundary_is_one_sided_and_so_cannot_conflict():
    """`X-ray` -> {x}; `Xray` -> {}. One side empty, `a and b` fails, rule inert.
    Pins the asymmetry rather than claiming it is safe."""
    assert _series_conflict("X-ray crystallography of lysozyme",
                            "Xray crystallography of lysozyme") is False


# RESOLVED 2026-08-12 by C1's series-context gate: a roman letter only counts as an
# ordinal when a series keyword precedes it or it is segment-initial and closed by
# '.' or ':'. An abbreviated genus is neither, so both sides now yield empty sets.
def test_1i_KNOWN_DEFECT_roman_numeral_plus_abbreviated_genus_false_conflicts():
    assert _series_conflict("Vi capsular antigen of V. cholerae",
                            "VI capsular antigen of Vibrio cholerae.") is False


# =====================================================================
# FAMILY 2 -- Defect 3: duplicate <article-title>, PARSER level
# REV-2: revision 1 tested no parser behaviour at all. These do.
# =====================================================================

_GBD_DUP = (
    '<ref id="cit0002"><element-citation publication-type="journal">'
    '<person-group person-group-type="author"><collab>GBD</collab></person-group>'
    '<article-title>Tobacco Collaborators</article-title>'
    '<article-title>2015 Smoking prevalence and attributable disease burden in '
    '195 countries and territories, 1990-2015: a systematic analysis from the '
    'Global Burden of Disease Study 2015</article-title>'
    '<source>Lancet</source><year>2017</year><volume>389</volume>'
    '<fpage>1885</fpage><lpage>1906</lpage>'
    '<pub-id pub-id-type="doi">10.1016/S0140-6736(17)30819-X</pub-id>'
    '<pub-id pub-id-type="pmid">28390697</pub-id>'
    '</element-citation></ref>')


def test_2a_single_article_title_is_extracted_unchanged(tmp_path):
    """TWIN baseline: with one <article-title>, the parser must keep picking it."""
    xml = _GBD_DUP.replace(
        '<article-title>Tobacco Collaborators</article-title>', '')
    claimed = _one_ref(tmp_path, xml)
    assert "Smoking prevalence" in claimed.title
    assert claimed.claimed_doi == "10.1016/S0140-6736(17)30819-X"


def test_2b_duplicate_article_titles_select_the_substantive_title(tmp_path):
    claimed = _one_ref(tmp_path, _GBD_DUP)
    assert "Smoking prevalence" in claimed.title


def test_2c_the_discarded_title_is_still_recoverable_from_raw(tmp_path):
    """Why Defect 3 is fixable at all: `raw` retains the full citation text, so
    the evidence is present even while the extraction is wrong."""
    claimed = _one_ref(tmp_path, _GBD_DUP)
    assert "Smoking prevalence" in claimed.raw


# =====================================================================
# FAMILY 3 -- Defect 4: a <collab> carrying the title's leading clause
# REV-2 CORRECTED DIAGNOSIS. Revision 1 claimed this fails on title extraction
# and would be fixed by an extra title candidate. That is WRONG: the matcher
# already de-prefixes, title_sim is 1.0, and the row is blocked by
# `corporate_author_conflict` (work_identity.py:557), which flag_verdict turns
# into an unconditional VERDICT_WRONG_PAPER. `_corporate_names_conflict` reads
# "Declaration"/"Helsinki" as distinctive words the other org cannot account for,
# and that block is NOT lifted by a shared DOI. A parser title candidate cannot
# reach it. This is a MISSING-ROUTE finding -> stop and report, do not implement.
# =====================================================================

_HELSINKI = (
    '<ref id="CR25"><element-citation publication-type="journal">'
    '<person-group person-group-type="author"><collab>World Medical Association '
    'Declaration of Helsinki</collab></person-group>'
    '<article-title>Ethical principles for medical research involving human '
    'subjects</article-title>'
    '<source>JAMA</source><year>2013</year><volume>310</volume>'
    '<fpage>2191</fpage><lpage>2194</lpage>'
    '<pub-id pub-id-type="doi">10.1001/jama.2013.281053</pub-id>'
    '<pub-id pub-id-type="pmid">24141714</pub-id>'
    '</element-citation></ref>')


def test_3a_collab_is_parsed_as_an_author_which_is_correct_JATS(tmp_path):
    """Pins that the parser is NOT at fault: <collab> IS an author element."""
    claimed = _one_ref(tmp_path, _HELSINKI)
    assert claimed.authors == ["World Medical Association Declaration of Helsinki"]
    assert claimed.title == "Ethical principles for medical research involving human subjects"


# RESOLVED 2026-08-11. This was xfail(strict) and is now an ordinary passing
# assertion. It was recorded as "blocked on a DECISION not an implementation" --
# the collab string holds title words, so _corporate_names_conflict reads two
# organizations and corporate_author_conflict hard-returned wrong_paper despite an
# identical DOI, volume, first page and year -- and it needed an authorized route
# rather than a parser change. RULE A2 (_doi_anchored_same_work) is that route:
# an exact shared DOI, offered the row at the corporate block, with a genuine
# two-organization check (_distinct_organizations) replacing the title-based
# containment test that could not tell a displaced boundary from a second body.
def test_3b_KNOWN_DEFECT_collab_holding_title_words_reads_as_two_organizations(tmp_path):
    claimed = _one_ref(tmp_path, _HELSINKI)
    r = _r(title="World Medical Association Declaration of Helsinki: ethical "
                 "principles for medical research involving human subjects.",
           authors=["World Medical Association"], year=2013, journal="JAMA",
           volume="310", pages="2191-4", doi="10.1001/jama.2013.281053")
    assert _cleared(flag_verdict(claimed, r)[0])


def test_3c_TWIN_two_DIFFERENT_organizations_with_divergent_titles_stay_high():
    """Any fix for 3b must not turn corporate containment into identity."""
    c = _c(title="Policy statement on infant feeding",
           authors=["American Academy of Pediatrics"],
           claimed_doi="10.1542/peds.2011-3084", pages="394-404",
           year=2012, journal="Pediatrics", volume="129")
    r = _r(title="Committee report on complementary foods and micronutrient intake",
           authors=["American Academy of Pediatrics Committee on Nutrition"],
           doi="10.1542/peds.2011-3084", pages="394-404",
           year=2012, journal="Pediatrics", volume="129")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_3d_TWIN_a_collab_prefix_that_does_NOT_reconstruct_the_title_stays_high():
    """The concatenation must be CHECKED. Same collab, different work."""
    c = _c(title="Ethical principles for veterinary research",
           authors=["World Medical Association Declaration of Helsinki"],
           claimed_doi="10.1001/jama.2013.281053", pages="2191-2194",
           year=2013, journal="JAMA", volume="310")
    r = _r(title="World Medical Association Declaration of Helsinki: ethical "
                 "principles for medical research involving human subjects.",
           authors=["World Medical Association"], year=2013, journal="JAMA",
           volume="310", pages="2191-4", doi="10.1001/jama.2013.281053")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


# =====================================================================
# FAMILY 4 -- title-year share-none
# =====================================================================

def test_4a_adjacent_annual_waves_of_one_study_share_NO_year_and_conflict():
    assert _series_conflict("Global Burden of Disease Study 2015",
                            "Global Burden of Disease Study 2016") is True


def test_4b_TWIN_a_year_inside_a_study_name_that_IS_shared_must_not_conflict():
    assert _series_conflict(
        "Smoking prevalence and attributable disease burden in 195 countries and "
        "territories, 1990-2015: a systematic analysis from the Global Burden of "
        "Disease Study 2015",
        "Global Burden of Disease Study 2015 analysis of smoking prevalence") is False


def test_4c_annual_edition_family_with_a_shared_doi_stays_high():
    c = _c(title="Heart Disease and Stroke Statistics-2017 Update",
           authors=["Benjamin"], claimed_doi="10.1161/CIR.0000000000000659",
           year=2019, journal="Circulation", volume="139", pages="e56-e528")
    r = _r(title="Heart Disease and Stroke Statistics-2019 Update: A Report From "
                 "the American Heart Association.",
           authors=["Benjamin"], doi="10.1161/CIR.0000000000000659",
           year=2019, journal="Circulation", volume="139", pages="e56-e528")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


# =====================================================================
# FAMILY 5 -- the citing paper writes the YEAR into <volume>
# REV-2 CORRECTION: revision 1 used the Cochrane titles, whose title_sim is
# 0.9130 -- BELOW the 0.92 gate -- so the volume clause was legitimately active
# and the case proved nothing about volume. Split into two.
# =====================================================================

_COCHRANE = dict(year=2016, pages="CD008932",
                 claimed_doi="10.1002/14651858.CD008932.pub3")


def test_5a_volume_disagreement_alone_is_inert_ABOVE_the_gate():
    """Titles identical, so title_sim is 1.0 and the volume clause cannot fire."""
    title = "Nipple- and areola-sparing mastectomy for the treatment of breast cancer"
    c = _c(title=title, authors=["Mota", "Riera", "Ricci"],
           journal="Cochrane Database Syst. Rev.", volume="2016", **_COCHRANE)
    r = _r(title=title + ".", authors=["Mota", "Riera", "Ricci"], year=2016,
           journal="Cochrane Database Syst Rev", volume="11", pages="CD008932",
           doi="10.1002/14651858.CD008932.pub3")
    assert _cleared(flag_verdict(c, r)[0])


@pytest.mark.xfail(strict=True, reason=(
    "OPEN punctuation-normalization defect, SEPARATE from the volume rule: "
    "'Nipple-and Areola-sparing' normalizes to 'nippleand areolasparing' because "
    "an intra-word hyphen is DELETED while a token-boundary hyphen becomes a "
    "SPACE. That drops title_sim to 0.9130, below the 0.92 gate, which then "
    "legitimately activates the volume disagreement."))
def test_5b_KNOWN_DEFECT_hyphen_boundary_asymmetry_drops_title_sim_under_the_gate():
    c = _c(title="Nipple-and Areola-sparing Mastectomy for the Treatment of Breast Cancer",
           authors=["Mota", "Riera", "Ricci"],
           journal="Cochrane Database Syst. Rev.", volume="2016", **_COCHRANE)
    r = _r(title="Nipple- and areola-sparing mastectomy for the treatment of breast cancer.",
           authors=["Mota", "Riera", "Ricci"], year=2016,
           journal="Cochrane Database Syst Rev", volume="11", pages="CD008932",
           doi="10.1002/14651858.CD008932.pub3")
    _verdict, match = flag_verdict(c, r)
    assert match.title_sim >= 0.92


# =====================================================================
# FAMILY 6 -- route guards. An identical DOI is never sufficient alone.
# =====================================================================

def test_6a_identical_doi_with_every_other_field_different_stays_high():
    """Mirrors ZD's seed-37 TRUE_F2 label on PMC8887078:R27 -- an agreeing DOI on
    a genuinely different work (a wrong DOI attached to the reference)."""
    c = _c(title="Mitochondrial dynamics in cardiac hypertrophy",
           authors=["Tanaka", "Mizutani"], year=2004, journal="Circ Res",
           volume="95", pages="100-110", claimed_doi="10.1161/01.RES.0000000001")
    r = _r(title="Rainfall variability in the Sahel over the twentieth century",
           authors=["Nicholson"], year=1993, journal="J Climate",
           volume="6", pages="1463-1466", doi="10.1161/01.RES.0000000001")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_6b_below_the_RULE_A_floor_other_identity_routes_may_still_clear():
    """REV-2 CORRECTION: revision 1 asserted wrong_paper here, reasoning that
    title_sim 0.8558 is under 0.92. That was wrong -- DOI_SAME_WORK_TITLE_MIN
    governs RULE A ONLY, and this pair clears via overwhelming_bibliographic_anchor
    at score 1.0. Pinned as CLEARED so nobody 'fixes' the 0.92 gate to force it."""
    c = _c(title="Pharmacokinetics of fentanyl in children",
           authors=["Duthie"], year=1986, journal="Br J Anaesth",
           volume="58", pages="950-956", claimed_doi="10.1093/bja/58.9.950")
    r = _r(title="Pharmacokinetics of fentanyl during constant rate i.v. infusion "
                 "for the relief of pain after surgery.",
           authors=["Duthie"], year=1986, journal="Br J Anaesth",
           volume="58", pages="950-6", doi="10.1093/bja/58.9.950")
    assert _cleared(flag_verdict(c, r)[0])


# =====================================================================
# FAMILY 7 -- journal equivalence
# =====================================================================

@pytest.mark.parametrize("left, right", [
    ("Br. J. Anaesth.", "Br J Anaesth"),
    ("Cochrane Database Syst. Rev.", "Cochrane Database Syst Rev"),
    ("Antioxidants", "Antioxidants (Basel)"),
    ("Agric. Food Chem.", "J Agric Food Chem"),
    ("Angew. Chem. Int. Ed.", "Angew Chem Int Ed Engl"),
])
def test_7a_periods_and_basel_suffixes_stay_equivalent(left, right):
    assert journal_equivalent(left, right)


@pytest.mark.xfail(strict=True, reason=(
    "OPEN, documented false match (spec §8.1, weak containment comparator): "
    "Blood and Blood Advances are DIFFERENT journals. REV-2: revision 1 asserted "
    "the wrong answer as True, which would have permanently codified the bug. "
    "Asserting the CORRECT answer under a strict xfail means a future comparator "
    "repair produces an intentional XPASS signal instead of a surprise failure."))
def test_7b_KNOWN_DEFECT_blood_vs_blood_adv_wrongly_compares_equal():
    assert journal_equivalent("Blood", "Blood Adv") is False


# =====================================================================
# FAMILY 8 -- tri-state. None means unknown, never False.
# REV-2 CORRECTION: the flags live on match.fields, not on match.
# =====================================================================

def test_8_absent_authors_give_unknown_not_disagreement():
    c = _c(title="Smoking prevalence and attributable disease burden",
           authors=[], year=2017, journal="Lancet", volume="389",
           pages="1885-1906", claimed_doi="10.1016/S0140-6736(17)30819-X")
    r = _r(title="Smoking prevalence and attributable disease burden in 195 "
                 "countries and territories, 1990-2015.",
           authors=["GBD 2015 Tobacco Collaborators"], year=2017,
           journal="Lancet", volume="389", pages="1885-1906",
           doi="10.1016/S0140-6736(17)30819-X")
    _verdict, match = flag_verdict(c, r)
    assert match.fields.first_author_match is not False
    assert match.fields.author_match is not False
