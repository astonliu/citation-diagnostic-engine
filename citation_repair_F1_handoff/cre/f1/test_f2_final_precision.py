"""Final F2 precision repair: six inspected development rows and boundaries.

Production rules are metadata-shape based.  PMIDs below pin the authorized six
inspected rows only; generic fixtures exercise each rule's boundary separately.
"""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import (
    VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT, VERDICT_WRONG_PAPER, flag_verdict,
)
from cre.f1.eval_report import build_f2_record, high_band_rate_of_scoreable
from cre.f1.schema import ClaimedRef, RetrievedRecord
from cre.f1.work_identity import DOI_BIBLIOGRAPHIC_ANCHOR_TITLE_MIN


def _c(**kw):
    return ClaimedRef(**kw)


def _r(**kw):
    return RetrievedRecord(resolved=True, **kw)


ROWS = [
    ("24478674", _c(title="Brain plasticity-based therapeutics", authors=["Merzenich", "Van Vleet", "Nahum"], year=2014, journal="Front Hum Neurosci", claimed_doi="10.3389/fnhum.2014.00385"),
     _r(title="Hyperschematia after right brain damage: a meaningful entity?", authors=["Rode", "Pisella"], year=2014, journal="Front Hum Neurosci", doi="10.3389/fnhum.2014.00008"), VERDICT_WRONG_PAPER, ""),
    ("15268348", _c(title="Assessment of the Perdew-Burke-Ernzerhof exchange-correlation functional", authors=["Ernzerhof", "Scuseria"], year=1999, journal="J Chem Phys", volume="110", pages="5029-5036", claimed_doi="10.1063/1.478401"),
     _r(title="Current-dependent extension of the Perdew-Burke-Ernzerhof exchange-correlation functional", authors=["Maximoff", "Ernzerhof", "Scuseria"], year=2004, journal="J Chem Phys", volume="120", pages="2105-2109", doi="10.1063/1.1634553"), VERDICT_WRONG_PAPER, ""),
    ("15267790", _c(title="The generalized Douglas-Kroll transformation", authors=["Wolf", "Reiher", "Hess"], year=2002, journal="J Chem Phys", volume="117", pages="9215-9226", claimed_doi="10.1063/1.1515314"),
     _r(title="Correlated ab initio calculations of spectroscopic parameters of SnO within the framework of the higher-order generalized Douglas-Kroll transformation", authors=["Wolf", "Reiher", "Hess"], year=2004, journal="J Chem Phys", volume="120", pages="8624-8631", doi="10.1063/1.1690757"), VERDICT_WRONG_PAPER, ""),
    ("22291118", _c(title="Patient- and family-centered care and the pediatrician's role", authors=["Committee on Hospital Care and Institute for Patient-and Family-Centered Care"], year=2012, journal="Pediatrics", volume="129", pages="394-404", claimed_doi="10.1542/peds.2011-3084"),
     _r(title="Patient- and family-centered care and the pediatrician's role", authors=["Committee On Hospital Care And Institute For Patient And Family Centered Care"], year=2012, journal="Pediatrics", volume="129", pages="394-404", doi="10.1542/peds.2011-3084"), VERDICT_SAME_WORK_VARIANT, "shared_doi_same_work"),
    ("22905060", _c(title="Antibiotic resistance pattern of biofilm-forming uropathogens isolated from catheterized patients in Pondicherry, India, Australia", authors=["Pramodhini", "Niveditha", "Umadevi", "Kumar"], year=2012, journal="Med. J", volume="5", pages="344-348", claimed_doi="10.4066/AMJ.2012.1193"),
     _r(title="Antiobiotic resistance pattern of biofilm-forming uropathogens isolated from catheterised patients in Pondicherry, India", authors=["Subramanian", "Shanmugam", "Sivaraman", "Kumar", "Selvaraj"], year=2012, journal="Australian Medical Journal", volume="5", pages="344-348", doi="10.4066/AMJ.2012.1193"), VERDICT_SAME_WORK_VARIANT, "overwhelming_bibliographic_anchor"),
    ("21680844", _c(title="Music and language expertise influence the categorization in musically trained and untrained subjects", authors=["Elmer", "Klein", "Kuhnis", "Liem", "Meyer", "Jancke"], year=2014, journal="Cerebral Cortex", volume="22", pages="650-658", claimed_doi="10.1093/cercor/bhr142"),
     _r(title="Neurofunctional and behavioral correlates of phonetic and temporal categorization in musically trained and untrained subjects", authors=["Elmer", "Meyer", "Jancke"], year=2012, journal="Cerebral Cortex", volume="22", pages="650-658", doi="10.1093/cercor/bhr142"), VERDICT_SAME_WORK_VARIANT, "mixed_identity_citation"),
]


@pytest.mark.parametrize("pmid, claimed, resolved, verdict, reason", ROWS)
def test_authorized_six_rows_use_live_flag_and_record_paths(pmid, claimed, resolved, verdict, reason):
    actual, match = flag_verdict(claimed, resolved)
    record = build_f2_record(pmid, "PMC_authorized", claimed, resolved)
    assert actual == record["verdict"] == verdict
    assert match.same_work_reason == record["same_work_reason"] == reason
    assert actual not in {VERDICT_MATCH, "cleared", "correct"}


def test_authorized_six_accounting_is_three_high_three_quarantined():
    records = [build_f2_record(pmid, "PMC_authorized", c, r)
               for pmid, c, r, _v, _reason in ROWS]
    assert [r["verdict"] for r in records].count(VERDICT_WRONG_PAPER) == 3
    assert [r["verdict"] for r in records].count(VERDICT_SAME_WORK_VARIANT) == 3
    metric = high_band_rate_of_scoreable(records)
    assert metric["flagged_f2_high"] == 3
    assert metric["same_work_variant_excluded"] == 3


def test_corporate_formatting_is_exact_but_different_groups_stay_high():
    c = _c(title="Clinical guidance for pediatric care", authors=["National-Committee for Pediatric Care"], year=2020, journal="Child Health", claimed_doi="10.1000/corp")
    r = _r(title="Clinical guidance for pediatric care", authors=["National Committee For Pediatric Care"], year=2020, journal="Child Health", doi="10.1000/corp")
    assert flag_verdict(c, r)[0] != VERDICT_WRONG_PAPER
    other = _r(title=r.title, authors=["International Committee For Pediatric Care"], year=2020, journal=r.journal, doi=r.doi)
    assert flag_verdict(c, other)[0] == VERDICT_WRONG_PAPER


@pytest.mark.parametrize("left,right", [
    ("Annual report 2019", "Annual report 2020"),
    ("Guideline update Part I", "Guideline update Part II"),
])
def test_corporate_editions_and_ordinals_stay_high(left, right):
    c = _c(title=left, authors=["National Committee for Care"], year=2020, journal="Care", claimed_doi="10.1000/edition")
    r = _r(title=right, authors=["National Committee for Care"], year=2020, journal="Care", doi="10.1000/edition")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_anchor_requires_every_bibliographic_signal_and_substantive_title():
    c = _c(title="Longitudinal outcomes after comprehensive cardiac rehabilitation", authors=["Given", "Names"], year=2018, journal="Clinical Cardiology", volume="41", pages="100-108", claimed_doi="10.1000/anchor")
    r = _r(title="Longitudinal outcomes following comprehensive cardiac rehabilitation", authors=["Surname", "Other"], year=2018, journal="Clinical Cardiology", volume="41", pages="100-108", doi="10.1000/anchor")
    v, m = flag_verdict(c, r)
    assert m.title_sim >= DOI_BIBLIOGRAPHIC_ANCHOR_TITLE_MIN
    assert v == VERDICT_SAME_WORK_VARIANT
    assert flag_verdict(c, _r(title=r.title, authors=r.authors, year=r.year, journal=r.journal, volume=r.volume, pages="101-108", doi=r.doi))[0] == VERDICT_WRONG_PAPER
    assert flag_verdict(_c(title="Editorial", authors=c.authors, year=c.year, journal=c.journal, volume=c.volume, pages=c.pages, claimed_doi=c.claimed_doi), r)[0] == VERDICT_WRONG_PAPER


def test_exact_doi_and_adjacent_article_alone_are_not_enough():
    c = _c(title="Neural semantic networks in aging", authors=["Lee"], year=2020, journal="Brain", volume="10", pages="100-106", claimed_doi="10.1000/shared")
    unrelated = _r(title="Impurity profiling of acetylsalicylic acid", authors=["Jones"], year=2020, journal="Brain", volume="10", pages="100-106", doi="10.1000/shared")
    adjacent = _r(title="Neural semantic networks after traumatic injury", authors=["Lee"], year=2020, journal="Brain", volume="10", pages="107-112", doi="10.1000/shared")
    assert flag_verdict(c, unrelated)[0] == VERDICT_WRONG_PAPER
    assert flag_verdict(c, adjacent)[0] == VERDICT_WRONG_PAPER


def test_mixed_identity_needs_roster_and_large_year_conflict_not_print_drift():
    c = _c(title="Music and language expertise influence categorization in musically trained and untrained subjects", authors=["Smith", "Jones", "Khan", "Liem", "Meyer", "Jancke"], year=2016, journal="Cortex", volume="20", pages="200-208", claimed_doi="10.1000/mixed")
    r = _r(title="Neurofunctional and behavioral correlates of phonetic and temporal categorization in musically trained and untrained subjects", authors=["Smith", "Meyer", "Jancke"], year=2013, journal="Cortex", volume="20", pages="200-208", doi="10.1000/mixed")
    assert flag_verdict(c, r)[0] == VERDICT_SAME_WORK_VARIANT
    drift = _r(title=r.title, authors=r.authors, year=2015, journal=r.journal, volume=r.volume, pages=r.pages, doi=r.doi)
    verdict, match = flag_verdict(c, drift)
    assert not (verdict == VERDICT_SAME_WORK_VARIANT and match.same_work_reason == "mixed_identity_citation")


# ---------------------------------------------------------------------
# Adversarial hardening (2026-07-15 review of the three new rules)
# ---------------------------------------------------------------------

def test_corporate_ampersand_conjunction_is_formatting_only():
    """'&' vs 'and' is a conjunction/typography difference, never a veto."""
    c = _c(title="Patient safety in the emergency department", authors=["Committee on Quality & Safety"], year=2019, journal="Emergency Care", volume="12", pages="10-18", claimed_doi="10.1000/amp")
    r = _r(title="Patient safety in the emergency department", authors=["Committee on Quality and Safety"], year=2019, journal="Emergency Care", volume="12", pages="10-18", doi="10.1000/amp")
    verdict, match = flag_verdict(c, r)
    assert verdict != VERDICT_WRONG_PAPER
    assert verdict == VERDICT_MATCH or match.same_work_reason == "shared_doi_same_work"


def test_corporate_abbreviation_is_a_token_change_and_stays_high():
    """Formatting-only comparator: an abbreviated organization name deletes and
    replaces tokens, so it is NOT formatting equivalence -- the conflict holds."""
    c = _c(title="Feeding guidance for healthy infants", authors=["AAP Committee on Nutrition"], year=2014, journal="Pediatrics", volume="133", pages="e100-e108", claimed_doi="10.1000/abbr")
    r = _r(title="Feeding guidance for healthy infants", authors=["American Academy of Pediatrics Committee on Nutrition"], year=2014, journal="Pediatrics", volume="133", pages="e100-e108", doi="10.1000/abbr")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


# ---------------------------------------------------------------------
# Corporate-author conflict is AFFIRMATIVE evidence, not absence of a format
# match (2026-08-06). The block is lifted only when the two names are not in
# conflict AND the pair is PHYSICALLY proven to be one work; both conjuncts are
# load-bearing, and each test below fails if either is dropped.
# ---------------------------------------------------------------------

def test_corporate_acronym_gloss_with_physical_anchor_leaves_high():
    """'World Medical Association (WMA)' vs 'World Medical Association': a
    parenthetical acronym glossing the words beside it is typography, not a
    second organization.  The exact DOI and physical slot carry the proof."""
    c = _c(title="Declaration of Helsinki: ethical principles for medical research",
           authors=["World Medical Association (WMA)"], year=2025, journal="JAMA",
           volume="333", pages="71-74", claimed_doi="10.1000/wma")
    r = _r(title="Declaration of Helsinki: ethical principles for medical research",
           authors=["World Medical Association"], year=2025, journal="JAMA",
           volume="333", pages="71-74", doi="10.1000/wma")
    assert flag_verdict(c, r)[0] == VERDICT_SAME_WORK_VARIANT


def test_corporate_truncated_trailing_token_leaves_high():
    """JATS truncates the closing token of a long institutional name
    ('...Taxonomy of, V'); a truncation is not two organizations."""
    c = _c(title="The species severe acute respiratory syndrome-related coronavirus",
           authors=["Coronaviridae Study Group of the International Committee on Taxonomy of, V"],
           year=2020, journal="Nat Microbiol", volume="5", pages="536-544",
           claimed_doi="10.1000/cov")
    r = _r(title="The species severe acute respiratory syndrome-related coronavirus",
           authors=["Coronaviridae Study Group of the International Committee on Taxonomy of Viruses"],
           year=2020, journal="Nat Microbiol", volume="5", pages="536-544",
           doi="10.1000/cov")
    assert flag_verdict(c, r)[0] == VERDICT_SAME_WORK_VARIANT


def test_corporate_physical_slot_agreement_without_a_doi_leaves_high():
    """The second sufficiency route: no DOI on either side, but venue, volume,
    first page and year all agree.  A trailing parenthetical QUALIFIER naming a
    sub-body is kept as real tokens -- containment, not a gloss, carries it."""
    c = _c(title="Third report of the expert panel on high blood cholesterol in adults",
           authors=["National Cholesterol Education Program Expert Panel"],
           year=2002, journal="Circulation", volume="106", pages="3143-3421")
    r = _r(title="Third report of the expert panel on high blood cholesterol in adults",
           authors=["National Cholesterol Education Program (NCEP) Expert Panel "
                    "(Adult Treatment Panel III)"],
           year=2002, journal="Circulation", volume="106", pages="3143-421")
    assert flag_verdict(c, r)[0] == VERDICT_SAME_WORK_VARIANT


def test_corporate_containment_without_physical_proof_stays_high():
    """Name containment ALONE never clears the block: string shape is not proof
    of same work.  Volume and first page disagree, so the pair stays HIGH."""
    c = _c(title="Clinical guidance for pediatric care",
           authors=["Committee for Pediatric Care"], year=2020,
           journal="Child Health", volume="10", pages="1-9")
    r = _r(title="Clinical guidance for pediatric care",
           authors=["National Committee for Pediatric Care"], year=2020,
           journal="Child Health", volume="12", pages="40-49")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_corporate_acronym_gloss_does_not_rescue_a_different_organization():
    """The gloss rule strips typography; it never equates two DISTINCT bodies.
    'International ... (ICPC)' vs 'National ...' keeps its conflict despite a
    shared DOI and a fully agreeing physical slot."""
    c = _c(title="Clinical guidance for pediatric care",
           authors=["International Committee for Pediatric Care (ICPC)"], year=2020,
           journal="Child Health", volume="10", pages="1-9", claimed_doi="10.1000/icpc")
    r = _r(title="Clinical guidance for pediatric care",
           authors=["National Committee for Pediatric Care"], year=2020,
           journal="Child Health", volume="10", pages="1-9", doi="10.1000/icpc")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


@pytest.mark.parametrize("authors", [
    ["National Perioperative Care Committee"],   # corporate edition family
    ["Rhodes"],                                  # personal-author edition family
])
def test_spelled_out_edition_conflict_stays_high(authors):
    """'Second Edition' vs 'Third Edition' is a serial-edition conflict even
    without a roman numeral or an embedded 4-digit year; a shared run-on DOI
    plus near-identical titles must not route it out of HIGH."""
    c = _c(title="Practice guidelines for perioperative care, Second Edition", authors=authors, year=2017, journal="Periop Med", volume="6", pages="1-40", claimed_doi="10.1000/edfam")
    r = _r(title="Practice guidelines for perioperative care, Third Edition", authors=authors, year=2020, journal="Periop Med", volume="9", pages="1-44", doi="10.1000/edfam")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_same_edition_ordinal_on_both_sides_is_not_a_conflict():
    """Both titles naming the SAME edition share the ordinal -- no series
    conflict, so the anchored pair stays out of HIGH (review via shared DOI)."""
    c = _c(title="Practice guidelines for perioperative care, Second Edition", authors=["Rhodes"], year=2017, journal="Periop Med", volume="6", pages="1-40", claimed_doi="10.1000/edsame")
    r = _r(title="Practice guidelines for perioperative care, 2nd Edition", authors=["Rhodes"], year=2017, journal="Periop Med", volume="6", pages="1-40", doi="10.1000/edsame")
    assert flag_verdict(c, r)[0] != VERDICT_WRONG_PAPER


def test_anchor_rejects_supplement_versus_article_first_page():
    """A meeting-abstract locator (S100) and a plain article page (100) are
    different physical slots: RULE G must not anchor across them."""
    c = _c(title="Longitudinal outcomes after comprehensive cardiac rehabilitation", authors=["Given", "Names"], year=2018, journal="Clinical Cardiology", volume="41", pages="S100-S108", claimed_doi="10.1000/anchor")
    r = _r(title="Longitudinal outcomes following comprehensive cardiac rehabilitation", authors=["Surname", "Other"], year=2018, journal="Clinical Cardiology", volume="41", pages="100-108", doi="10.1000/anchor")
    verdict, match = flag_verdict(c, r)
    assert match.same_work_reason != "overwhelming_bibliographic_anchor"
    assert verdict == VERDICT_WRONG_PAPER


def test_mixed_identity_requires_locator_parity():
    """The mixed-identity quarantine must not treat an S-page as the article
    page's slot; without the page anchor the row stays HIGH."""
    c = _c(title="Outcomes of dapagliflozin in chronic kidney disease progression", authors=["Heer", "Field", "Stone", "Water"], year=2019, journal="Nephrology Today", volume="30", pages="S344", claimed_doi="10.1000/mx")
    r = _r(title="Dapagliflozin and renal outcomes in chronic kidney disease", authors=["Heer", "Berg"], year=2021, journal="Nephrology Today", volume="30", pages="344-352", doi="10.1000/mx")
    verdict, match = flag_verdict(c, r)
    assert match.same_work_reason != "mixed_identity_citation"
    assert verdict == VERDICT_WRONG_PAPER


def test_derivative_genre_clean_high_scoring_pair_is_not_forced_high():
    """Both titles of ONE review-genre work carry the same genre marker; a
    one-token drift with every field agreeing is a clean match, and the
    derivative block must not force it into the HIGH band."""
    c = _c(title="A systematic review of exercise interventions for adolescent depression", authors=["Carter", "Morres"], year=2016, journal="J Affect Disord", volume="191", pages="62-71")
    r = _r(title="A systematic review of exercise interventions for adolescents depression", authors=["Carter", "Morres"], year=2016, journal="J Affect Disord", volume="191", pages="62-71", doi="10.1016/j.jad.2015.11.014")
    assert flag_verdict(c, r)[0] == VERDICT_MATCH


# ---------------------------------------------------------------------
# Item 3 (frame-scoping remediation, 2026-08-07): the corporate containment
# allowance admitted false clears. Containment is NOT identity: a parent vs its
# own committee, or names differing in one distinctive token, must stay HIGH.
# These three are the acceptance criteria; each was review_wrong_paper at 858a22f,
# wrongly review_same_work_variant after e5ac4c7, and review_wrong_paper again now.
# ---------------------------------------------------------------------
def test_corporate_parent_vs_committee_divergent_titles_stays_high():
    # AAP vs its Committee on Nutrition; the extra tokens + DIVERGENT titles
    # (infants vs children) are two different works sharing one identifier.
    c = _c(title="Dietary guidance for infants",
           authors=["American Academy of Pediatrics"], year=2020,
           journal="Pediatrics", volume="145", pages="394-404",
           claimed_doi="10.1542/peds.2020-1")
    r = _r(title="Dietary guidance for children",
           authors=["American Academy of Pediatrics Committee on Nutrition"],
           year=2020, journal="Pediatrics", volume="145", pages="394-404",
           doi="10.1542/peds.2020-1")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_corporate_international_vs_interventional_stays_high():
    # Distinct first tokens (JaroWinkler 0.9227) must NOT be equated: two
    # different societies, identical title, shared identifier.
    c = _c(title="Consensus on coronary stenting",
           authors=["International Cardiology Society"], year=2020,
           journal="Cardiology", volume="30", pages="1-9",
           claimed_doi="10.1000/card")
    r = _r(title="Consensus on coronary stenting",
           authors=["Interventional Cardiology Society"], year=2020,
           journal="Cardiology", volume="30", pages="1-9", doi="10.1000/card")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_corporate_group_A_vs_group_AB_stays_high():
    # Short group designators A vs AB are distinct bodies, not a truncation.
    c = _c(title="Randomized trial results",
           authors=["National Clinical Study Group A"], year=2020, journal="Trials",
           volume="21", pages="1-9", claimed_doi="10.1000/grp")
    r = _r(title="Randomized trial results",
           authors=["National Clinical Study Group AB"], year=2020, journal="Trials",
           volume="21", pages="1-9", doi="10.1000/grp")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER
