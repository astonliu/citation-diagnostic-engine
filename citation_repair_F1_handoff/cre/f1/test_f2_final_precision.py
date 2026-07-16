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
