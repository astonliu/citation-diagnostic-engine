"""F2 wrong-paper precision redesign (2026-07-14) -- regression + generalization.

Seed 29 was the first prospective seed; its 16 HIGH rows adjudicated 9 genuine
wrong-paper (label 1) and 7 same-work/version-family / parser-artifact false
positives (label 0), a frozen prospective precision of 9/16 = 0.5625. Seed 29 is
now BURNED DEVELOPMENT DATA. This file pins the fix:

  * the 7 label-0 rows must LEAVE the HIGH band and enter the
    ``review_same_work_variant`` human-review quarantine;
  * the 9 label-1 rows must STAY ``review_wrong_paper`` / HIGH;
  * none of the quarantined rows may route to match / cleared / correct.

The 16 rows below are the EXACT seed-29 audit records (verbatim written/resolved
fields). Beyond them, every new mechanism has a PMID-free generic positive and an
adversarial negative, so production behavior never depends on a memorized PMID or
title. Load-bearing wrong-paper guards from F2_STATE.md are re-pinned here too.
"""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import (
    VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT, VERDICT_WRONG_PAPER,
    VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE,
    SAME_WORK_TITLE_SIM_MIN, flag_verdict,
)
from cre.f1.schema import ClaimedRef, RetrievedRecord

# Outcomes that are auto-clears -- a recognized same-work row must NEVER land here.
_AUTO_CLEAR = {VERDICT_MATCH, "cleared", "correct", "match"}


def _claimed(d: dict) -> ClaimedRef:
    return ClaimedRef(
        title=d["written_title"], authors=list(d["written_authors"]),
        year=d["written_year"], journal=d["written_journal"],
        claimed_pmid=d["pmid"], claimed_doi=d["written_doi"],
        volume=d["written_volume"], pages=d["written_pages"])


def _resolved(d: dict) -> RetrievedRecord:
    return RetrievedRecord(
        resolved=True, title=d["resolved_title"], authors=list(d["resolved_authors"]),
        year=d["resolved_year"], journal=d["resolved_journal"], pmid=d["pmid"],
        doi=d["resolved_doi"], volume=d["resolved_volume"], pages=d["resolved_pages"],
        is_container=d["resolved_is_container"], year_from_dep=d["resolved_year_from_dep"],
        alternate_titles=list(d["resolved_alternate_titles"]),
        language=d["resolved_language"],
        publication_types=list(d["resolved_publication_types"]))


# ---------------------------------------------------------------------------
# EXACT seed-29 HIGH audit rows (7 label-0 same-work FPs, then 9 label-1 F2s).
# ---------------------------------------------------------------------------
SEED29 = [
  {"written_title": "1995-1996 and 1999-2000, MMWR Morb", "written_authors": ["Spina bifida and anencephaly before and after folic acid mandate--United States"], "written_year": 2004, "written_journal": "Mortal Wkly Rep", "written_doi": "", "written_volume": "53", "written_pages": "362-365", "resolved_title": "Spina bifida and anencephaly before and after folic acid mandate--United States,  1995-1996 and 1999-2000.", "resolved_authors": ["Centers for Disease Control and Prevention (CDC)"], "resolved_year": 2004, "resolved_journal": "MMWR Morb Mortal Wkly Rep", "resolved_doi": "", "resolved_volume": "53", "resolved_pages": "362-5", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "15129193", "label": 0},
  {"written_title": "Medusomyces (Tea fungus): A scientific history, composition, features of physiology and metabolism", "written_authors": ["Yurkevich", "Kutyshenko"], "written_year": 2002, "written_journal": "Biophysics", "written_doi": "", "written_volume": "47", "written_pages": "1035-1048", "resolved_title": "[Medusomyces (tea fungus): scientific history, composition, physiology, and  metabolism].", "resolved_authors": ["Iurkevich", "Kutyshenko"], "resolved_year": 2002, "resolved_journal": "Biofizika", "resolved_doi": "", "resolved_volume": "47", "resolved_pages": "1116-29", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": ["Meduzomitset (chainyi grib): nauchania istoriia, sostav, osobennosti fiziologii i  metabolizma."], "resolved_language": "rus", "resolved_publication_types": ["English Abstract", "Historical Article", "Journal Article"], "pmid": "12500577", "label": 0},
  {"written_title": "Association of aoplipo protein levels with peripheral arterial disease: a meta-analysis of literature studeies", "written_authors": ["Forte", "Calcaterra", "Lupoli", "Orsini"], "written_year": 2020, "written_journal": "Eur J Prev Cardiol", "written_doi": "10.1093/eurjpc/zwaa029", "written_volume": "", "written_pages": "zwaa029", "resolved_title": "Association of apolipoprotein levels with peripheral arterial disease: a  meta-analysis of literature studies.", "resolved_authors": ["Forte", "Calcaterra", "Lupoli", "Orsini", "Chiurazzi", "Tripaldella", "Iannuzzo", "Di Minno"], "resolved_year": 2022, "resolved_journal": "Eur J Prev Cardiol", "resolved_doi": "10.1093/eurjpc/zwaa029", "resolved_volume": "28", "resolved_pages": "1980-1990", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article", "Meta-Analysis"], "pmid": "33624016", "label": 0},
  {"written_title": "Virus interference. I. The interferon", "written_authors": ["Isaacs", "Lindenmann"], "written_year": 1957, "written_journal": "Proc. R. Soc. Lond. B Biol. Sci.", "written_doi": "", "written_volume": "147", "written_pages": "258-267", "resolved_title": "Pillars Article: Virus Interference. I. The Interferon. Proc R Soc Lond B Biol  Sci. 1957. 147: 258-267.", "resolved_authors": ["Isaacs", "Lindenmann"], "resolved_year": 2015, "resolved_journal": "J Immunol", "resolved_doi": "", "resolved_volume": "195", "resolved_pages": "1911-20", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Biography", "Historical Article", "Journal Article", "Seminal Article"], "pmid": "26297790", "label": 0},
  {"written_title": "A 10-year prospective comparison of anterior cruciate ligament reconstructions with hamstring tendon and patellar tendon autograft", "written_authors": ["Pinczewski", "Lyman", "Salmon", "Russell"], "written_year": 2009, "written_journal": "J Sci Med Sport", "written_doi": "10.1177/0363546506296042", "written_volume": "12", "written_pages": "S59", "resolved_title": "A 10-year comparison of anterior cruciate ligament reconstructions with hamstring  tendon and patellar tendon autograft: a controlled, prospective trial.", "resolved_authors": ["Pinczewski", "Lyman", "Salmon", "Russell", "Roe", "Linklater"], "resolved_year": 2007, "resolved_journal": "Am J Sports Med", "resolved_doi": "10.1177/0363546506296042", "resolved_volume": "35", "resolved_pages": "564-74", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Clinical Trial", "Comparative Study", "Journal Article", "Randomized Controlled Trial", "Research Support, Non-U.S. Gov't"], "pmid": "17261567", "label": 0},
  {"written_title": "The nature of association of oral para-functional habits with anxiety and big-five personality traits in Saudi adult population", "written_authors": ["Albesher", "Aljohani", "Alsenani", "Turkistani", "Salam", "Almutairi"], "written_year": 2019, "written_journal": "Saudi Dent J", "written_doi": "10.1016/j.sdentj.2019.02.026", "written_volume": "31", "written_pages": "S39-S40", "resolved_title": "Association of oral parafunctional habits with anxiety and the Big-Five  Personality Traits in the Saudi adult population.", "resolved_authors": ["Almutairi", "Albesher", "Aljohani", "Alsinanni", "Turkistani", "Salam"], "resolved_year": 2021, "resolved_journal": "Saudi Dent J", "resolved_doi": "10.1016/j.sdentj.2020.01.003", "resolved_volume": "33", "resolved_pages": "90-98", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "33551622", "label": 0},
  {"written_title": "Modeling and evaluating the effects of hyaluronic acid degradation on human astrocyte reactivity using multi-interpenetrating polymer networks (MIPNs)", "written_authors": ["Munoz-Pinto", "Jimenez-Vergara", "Van Drunen", "Cagle"], "written_year": 2019, "written_journal": "Alzheimers Dement.", "written_doi": "10.1016/j.jalz.2019.06.2509", "written_volume": "15", "written_pages": "P1025", "resolved_title": "Modeling the effects of hyaluronic acid degradation on the regulation of human  astrocyte phenotype using multicomponent interpenetrating polymer networks (mIPNs).", "resolved_authors": ["Jimenez-Vergara", "Van Drunen", "Cagle", "Munoz-Pinto"], "resolved_year": 2020, "resolved_journal": "Sci Rep", "resolved_doi": "10.1038/s41598-020-77655-1", "resolved_volume": "10", "resolved_pages": "20734", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article", "Research Support, Non-U.S. Gov't"], "pmid": "33244148", "label": 0},
  {"written_title": "Evolutionary Algorithms for Constrained Parameter Optimization Problems", "written_authors": ["Michalewicz", "Schoenauer"], "written_year": 1996, "written_journal": "Evol. Comput.", "written_doi": "10.1162/evco.1996.4.1.1", "written_volume": "4", "written_pages": "1-32", "resolved_title": "Evolutionary algorithms, homomorphous mappings, and constrained parameter  optimization.", "resolved_authors": ["Koziel", "Michalewicz"], "resolved_year": 1999, "resolved_journal": "Evol Comput", "resolved_doi": "10.1162/evco.1999.7.1.19", "resolved_volume": "7", "resolved_pages": "19-44", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article", "Research Support, Non-U.S. Gov't", "Research Support, U.S. Gov't, Non-P.H.S."], "pmid": "10199994", "label": 1},
  {"written_title": "Improvement of meat tenderness by simultaneous application of high-intensity ultrasonic radiation and papain treatment", "written_authors": ["Barekat", "Soltanizadeh"], "written_year": 2017, "written_journal": "Innov. Food Sci. Emerg. Technol.", "written_doi": "10.1016/j.ifset.2016.12.009", "written_volume": "39", "written_pages": "223-229", "resolved_title": "Application of high-intensity ultrasonic radiation coupled with papain treatment  to modify functional properties of beef Longissimus lumborum.", "resolved_authors": ["Barekat", "Soltanizadeh"], "resolved_year": 2019, "resolved_journal": "J Food Sci Technol", "resolved_doi": "10.1007/s13197-018-3479-1", "resolved_volume": "56", "resolved_pages": "224-232", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "30728564", "label": 1},
  {"written_title": "The World Health Organization Report 2002: Reducing Risks, Promoting Healthy Life 2002", "written_authors": ["WHO"], "written_year": 2020, "written_journal": "", "written_doi": "10.1080/1357628031000116808", "written_volume": "", "written_pages": "", "resolved_title": "The world health report 2002 - reducing risks, promoting healthy life.", "resolved_authors": ["Guilbert"], "resolved_year": 2003, "resolved_journal": "Educ Health (Abingdon)", "resolved_doi": "10.1080/1357628031000116808", "resolved_volume": "16", "resolved_pages": "230", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Letter"], "pmid": "14741909", "label": 1},
  {"written_title": "Experiences of organisational practices that advance women in healthcare leadership: a qualitative study", "written_authors": ["Mousa", "Rowley", "Bramley"], "written_year": 2023, "written_journal": "BMJ Leader", "written_doi": "10.1136/leader-2022-000653", "written_volume": "7", "written_pages": "266-272", "resolved_title": "Clinical academics' experiences during the COVID-19 pandemic: a qualitative study  of challenges and opportunities when working at the clinical frontline.", "resolved_authors": ["Trusson", "Rowley", "Bramley"], "resolved_year": 2023, "resolved_journal": "BMJ Lead", "resolved_doi": "10.1136/leader-2020-000414", "resolved_volume": "7", "resolved_pages": "266-272", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article", "Research Support, Non-U.S. Gov't"], "pmid": "37192094", "label": 1},
  {"written_title": "Evidence standards framework for digital health technologies", "written_authors": [], "written_year": None, "written_journal": "NICE", "written_doi": "10.1177/20552076211018617", "written_volume": "", "written_pages": "", "resolved_title": "The NICE Evidence Standards Framework for digital health and care technologies -  Developing and maintaining an innovative evidence framework with global impact.", "resolved_authors": ["Unsworth", "Dillon", "Collinson", "Powell", "Salmon", "Oladapo", "Ayiku", "Shield", "Holden", "Patel", "Campbell", "Greaves", "Joshi", "Powell", "Tonnel"], "resolved_year": 2021, "resolved_journal": "Digit Health", "resolved_doi": "10.1177/20552076211018617", "resolved_volume": "7", "resolved_pages": "20552076211018617", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "34249371", "label": 1},
  {"written_title": "Cyber victimization and aggression: Are they linked with adolescent smoking and drinking?", "written_authors": ["Chan", "La Greca"], "written_year": 2016, "written_journal": "Child Youth Care Forum", "written_doi": "10.1007/s10566-015-9318-x", "written_volume": "45", "written_pages": "47-63", "resolved_title": "Preventing Adolescent Social Anxiety and Depression and Reducing Peer  Victimization: Intervention Development and Open Trial.", "resolved_authors": ["La Greca", "Ehrenreich-May", "Mufson", "Chan"], "resolved_year": 2016, "resolved_journal": "Child Youth Care Forum", "resolved_doi": "10.1007/s10566-016-9363-0", "resolved_volume": "45", "resolved_pages": "905-926", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "27857509", "label": 1},
  {"written_title": "Synthesis, X-ray structure, Hirshfeld analysis, and DFT studies of a new Pd (II) complex with an anionic  s -triazine NNO donor ligand", "written_authors": ["Soliman", "Lasri", "Haukka", "Elmarghany", "Al-Majid", "El-Faham", "Barakat"], "written_year": 2020, "written_journal": "J. Mol. Struct.", "written_doi": "10.1016/j.molstruc.2020.128463", "written_volume": "1217", "written_pages": "128463", "resolved_title": "A New Pt(II) Complex with Anionic s-Triazine Based NNO-Donor Ligand: Synthesis,  X-ray Structure, Hirshfeld Analysis and DFT Studies.", "resolved_authors": ["Altowyan", "Soliman", "Lasri", "Eltayeb", "Haukka", "Barakat", "El-Faham"], "resolved_year": 2022, "resolved_journal": "Molecules", "resolved_doi": "10.3390/molecules27051628", "resolved_volume": "27", "resolved_pages": "", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "35268727", "label": 1},
  {"written_title": "Decision-making method using a visual approach for cluster analysis problems; indicative classification algorithms and grouping scope. Expert Systems", "written_authors": ["Bittmann", "Gelbard"], "written_year": 2007, "written_journal": "The Journal of Knowledge Engineering", "written_doi": "10.1016/j.earlhumdev.2020.105191", "written_volume": "24", "written_pages": "171-187", "resolved_title": "The future of Cochrane Neonatal.", "resolved_authors": ["Soll", "Ovelman", "McGuire"], "resolved_year": 2020, "resolved_journal": "Early Hum Dev", "resolved_doi": "10.1016/j.earlhumdev.2020.105191", "resolved_volume": "150", "resolved_pages": "105191", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article", "Research Support, Non-U.S. Gov't"], "pmid": "33036834", "label": 1},
  {"written_title": "Electrochemical Detection of Uric Acid and Ascorbic Acid using r-GO/NPs based Sensors", "written_authors": ["Mazzaraa", "Patellaa", "Aiello", "O’Riordan"], "written_year": 2021, "written_journal": "Electrochim. Acta", "written_doi": "10.1016/j.electacta.2021.138652", "written_volume": "388", "written_pages": "138652", "resolved_title": "Electrochemical detection of dopamine with negligible interference from ascorbic  and uric acid by means of reduced graphene oxide and metals-NPs based electrodes.", "resolved_authors": ["Patella", "Sortino", "Mazzara", "Aiello", "Drago", "Torino", "Vilasi", "O'Riordan", "Inguanta"], "resolved_year": 2021, "resolved_journal": "Anal Chim Acta", "resolved_doi": "10.1016/j.aca.2021.339124", "resolved_volume": "1187", "resolved_pages": "339124", "resolved_is_container": False, "resolved_year_from_dep": False, "resolved_alternate_titles": [], "resolved_language": "eng", "resolved_publication_types": ["Journal Article"], "pmid": "34753568", "label": 1},
]

_L0 = [d for d in SEED29 if d["label"] == 0]
_L1 = [d for d in SEED29 if d["label"] == 1]


def test_seed29_row_count_is_frozen():
    assert len(SEED29) == 16
    assert len(_L0) == 7 and len(_L1) == 9


@pytest.mark.parametrize("d", _L0, ids=[d["pmid"] for d in _L0])
def test_seed29_false_positives_leave_high_for_same_work_quarantine(d):
    verdict, m = flag_verdict(_claimed(d), _resolved(d))
    if d["pmid"] == "12500577":
        assert verdict == VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE
        assert m.same_work_reason == ""
        return
    assert verdict == VERDICT_SAME_WORK_VARIANT, (
        d["pmid"], verdict, m.same_work_reason, m.title_sim)
    # Load-bearing recall invariant: quarantine, never an auto-clear.
    assert verdict not in _AUTO_CLEAR
    assert m.same_work_reason  # a named, auditable reason was recorded


@pytest.mark.parametrize("d", _L1, ids=[d["pmid"] for d in _L1])
def test_seed29_genuine_wrong_papers_stay_high(d):
    verdict, m = flag_verdict(_claimed(d), _resolved(d))
    assert verdict == VERDICT_WRONG_PAPER, (
        d["pmid"], verdict, m.same_work_reason, m.title_sim)


def test_seed29_each_quarantine_reason_is_one_of_the_new_mechanisms():
    reasons = {d["pmid"]: flag_verdict(_claimed(d), _resolved(d))[1].same_work_reason
               for d in _L0}
    assert reasons == {
        "26297790": "historical_republication",
        # 17261567 shares a DOI but its title_sim (0.917) is below RULE A's
        # near-identical floor after hardening; it is a supplement abstract (S59)
        # of the same study, so RULE B claims it.
        "17261567": "conference_abstract_publication",
        "33624016": "shared_doi_same_work",
        "33551622": "conference_abstract_publication",
        "33244148": "conference_abstract_publication",
        "12500577": "",
        "15129193": "shifted_author_title_artifact",
    }


# ===========================================================================
# Generic (PMID-free) positives -- each new mechanism, constructed inputs.
# ===========================================================================
def test_rule_A_shared_doi_first_author_quarantines_year_drift():
    c = ClaimedRef(title="A distinctive clinical trial of drug X in adults",
                   authors=["Nakamura", "Ito"], year=2018, journal="J Trials",
                   claimed_doi="10.1234/abc.def")
    r = RetrievedRecord(resolved=True,
                        title="A distinctive clinical trial of drug X in adults: final report",
                        authors=["Nakamura", "Ito", "Sato"], year=2020,
                        journal="Clinical Reports", doi="10.1234/abc.def")
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "shared_doi_same_work"


def test_rule_A_shared_doi_overrides_derivative_wording():
    # Both titles carry "meta-analysis"; the shared DOI + same first author proves
    # it is the SAME meta-analysis (not a meta-analysis ABOUT the other).
    c = ClaimedRef(title="Association of biomarker Z with disease: a meta-analysis of studies",
                   authors=["Rossi"], year=2020, journal="Eur J Med",
                   claimed_doi="10.1093/xyz/aa1")
    r = RetrievedRecord(resolved=True,
                        title="Association of biomarker Z with disease: a meta-analysis of studies.",
                        authors=["Rossi", "Bianchi"], year=2022, journal="Eur J Med",
                        doi="10.1093/xyz/aa1")
    assert flag_verdict(c, r)[0] == VERDICT_SAME_WORK_VARIANT


def test_rule_B_conference_supplement_abstract_to_full_publication():
    # A specific (>=6 distinctive tokens) abstract title -> its full publication.
    c = ClaimedRef(title="Intravenous ferric carboxymaltose for iron deficiency anemia in chronic heart failure patients",
                   authors=["Alvarez", "Kim"], year=2018, journal="J Cardiol",
                   volume="12", pages="S44")
    r = RetrievedRecord(resolved=True,
                        title="Intravenous ferric carboxymaltose for iron deficiency anemia in chronic heart failure patients: a randomized controlled trial",
                        authors=["Kim", "Alvarez", "Ng"], year=2020,
                        journal="Circulation", volume="35", pages="1201-10")
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "conference_abstract_publication"


def test_rule_C_historical_republication_reprint_prefix():
    # A genuine reprint recites the ORIGINAL citation (year + volume + pages) in its
    # title -- proof it re-runs THAT work, not a different paper carrying the word.
    c = ClaimedRef(title="Adaptation and natural selection in populations",
                   authors=["Haldane"], year=1932, journal="Ann Eugen",
                   volume="5", pages="220-234")
    r = RetrievedRecord(
        resolved=True,
        title="Classic Article: Adaptation and natural selection in populations. Ann Eugen. 1932. 5: 220-234.",
        authors=["Haldane"], year=2016, journal="Genetics",
        publication_types=["Classical Article", "Journal Article"])
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "historical_republication"


def test_rule_C_reprint_via_publication_type_only():
    c = ClaimedRef(title="On the electrodynamics of moving bodies",
                   authors=["Einstein"], year=1905, journal="Ann Phys",
                   volume="17", pages="891-921")
    r = RetrievedRecord(
        resolved=True,
        title="On the electrodynamics of moving bodies. Ann Phys. 1905. 17: 891-921. (Republished).",
        authors=["Einstein"], year=2005, journal="Ann Phys",
        publication_types=["Republished Article", "Journal Article"])
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "historical_republication"


def test_rule_D_translation_with_transliterated_metadata():
    # Title similar-but-not-identical (below the 0.92 gate), bracketed non-English
    # resolved record, same year, transliterated first author, matching volume.
    c = ClaimedRef(title="Cytokine profiles during acute inflammation in a rodent sepsis model",
                   authors=["Kuznetsov"], year=2010, journal="", volume="48")
    r = RetrievedRecord(
        resolved=True,
        title="[Cytokine profiles in acute inflammation studied in an experimental sepsis model].",
        authors=["Kuznetzov"], year=2010, journal="Immunologiia", volume="48",
        language="rus")
    verdict, m = flag_verdict(c, r)
    assert 0.85 <= m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert verdict == VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE
    assert m.same_work_reason == ""


def test_rule_E_shifted_author_title_parser_artifact():
    # The article title leaked into the author slot; the title slot holds a tail
    # fragment. Journal + volume + year corroborate the SAME resolved work.
    c = ClaimedRef(title="2001 and 2010 national survey",
                   authors=["Prevalence of asthma among children in urban districts"],
                   year=2014, journal="Morb Mortal", volume="63", pages="12-18")
    r = RetrievedRecord(
        resolved=True,
        title="Prevalence of asthma among children in urban districts, 2001 and 2010 national survey.",
        authors=["National Health Statistics Group"], year=2014,
        journal="MMWR Morb Mortal Wkly Rep", volume="63", pages="12-8")
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "shifted_author_title_artifact"


# ===========================================================================
# Adversarial negatives -- each mechanism's boundary must STAY wrong-paper.
# ===========================================================================
def test_rule_A_negative_shared_doi_different_first_author_stays_wrong_paper():
    # A recombined/run-on citation can carry another paper's DOI; without a
    # first-author match the DOI alone must not prove identity.
    c = ClaimedRef(title="Neural semantic networks in aging",
                   authors=["Lee"], year=2020, claimed_doi="10.1000/same")
    r = RetrievedRecord(resolved=True, title="Impurity profiling of acetylsalicylic acid",
                        authors=["Jones"], year=2021, doi="10.1000/same")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_rule_B_negative_supplement_page_but_different_endpoint_stays_wrong_paper():
    # Different trial sub-analysis (title_sim < 0.87) cited with a supplement page
    # and overlapping authors: must remain HIGH (33148016 shape).
    c = ClaimedRef(title="Reduction in ischemic stroke with icosapent ethyl insights from the trial",
                   authors=["Bhatt", "Steg"], year=2021, journal="Stroke", pages="S123")
    r = RetrievedRecord(
        resolved=True,
        title="Reduction in revascularization with icosapent ethyl: insights from revascularization analyses",
        authors=["Peterson", "Bhatt", "Steg"], year=2021, journal="Circulation")
    verdict, m = flag_verdict(c, r)
    assert m.title_sim < 0.87
    assert verdict == VERDICT_WRONG_PAPER


def test_rule_B_negative_supplement_page_no_author_overlap_stays_wrong_paper():
    # Supplement-page abstract of a DIFFERENT study (hypertension vs diabetes),
    # no shared author, title_sim below the 0.92 gate: must remain HIGH.
    c = ClaimedRef(title="Prevalence of hypertension among urban office workers in a screening survey",
                   authors=["Alpha"], year=2019, journal="J Pub Health", pages="S30")
    r = RetrievedRecord(
        resolved=True,
        title="Prevalence of diabetes among urban office workers in a workplace screening survey",
        authors=["Beta"], year=2021, journal="Circulation")
    verdict, m = flag_verdict(c, r)
    assert 0.87 <= m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert verdict == VERDICT_WRONG_PAPER


def test_rule_C_negative_containment_without_reprint_marker_stays_wrong_paper():
    # Zimet (2280326): claimed title contained in resolved, same author, year
    # drift, NO reprint marker -> a genuinely different follow-up paper.
    c = ClaimedRef(title="The Multidimensional Scale of Perceived Social Support",
                   authors=["Zimet"], year=1988, journal="J Pers Assess")
    r = RetrievedRecord(
        resolved=True,
        title="Psychometric characteristics of the Multidimensional Scale of Perceived Social Support",
        authors=["Zimet"], year=1990, journal="J Pers Assess")
    verdict, m = flag_verdict(c, r)
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert verdict == VERDICT_WRONG_PAPER


def test_rule_C_negative_reprint_marker_different_author_stays_wrong_paper():
    # A reprint record whose first author does NOT match the claimed author is not
    # proof the claimed original is the same work (26297790 proportions, ts<0.92).
    c = ClaimedRef(title="Virus interference. I. The interferon",
                   authors=["Other"], year=1957, journal="Proc. R. Soc. Lond. B Biol. Sci.",
                   volume="147", pages="258-267")
    r = RetrievedRecord(
        resolved=True,
        title="Pillars Article: Virus Interference. I. The Interferon. Proc R Soc Lond B Biol  Sci. 1957. 147: 258-267.",
        authors=["Isaacs", "Lindenmann"], year=2015, journal="J Immunol",
        publication_types=["Historical Article"])
    verdict, m = flag_verdict(c, r)
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert verdict == VERDICT_WRONG_PAPER


def test_rule_D_negative_translation_year_mismatch_stays_wrong_paper():
    # Same translation shape as the RULE D positive but with a >1-year gap: the
    # same-year requirement keeps it out of the translation quarantine.
    c = ClaimedRef(title="Cytokine profiles during acute inflammation in a rodent sepsis model",
                   authors=["Kuznetsov"], year=2008, journal="", volume="48")
    r = RetrievedRecord(
        resolved=True,
        title="[Cytokine profiles in acute inflammation studied in an experimental sepsis model].",
        authors=["Kuznetzov"], year=2010, journal="Immunologiia", volume="48",
        language="rus")
    verdict, m = flag_verdict(c, r)
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN
    assert verdict == VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE


def test_rule_E_negative_corporate_author_not_treated_as_shifted_title():
    c = ClaimedRef(title="1995 and 1999 data",
                   authors=["Centers for Disease Control and Prevention National Center"],
                   year=2004, journal="MMWR", volume="53")
    r = RetrievedRecord(
        resolved=True,
        title="Centers for Disease Control and Prevention National Center report 1995 and 1999 data",
        authors=["CDC"], year=2004, journal="MMWR", volume="53")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


# ===========================================================================
# Load-bearing wrong-paper guards (F2_STATE.md) -- rich fixtures.
# ===========================================================================
_GUARDS = [
    # 33148016: different REDUCE-IT sub-analysis (endpoint), cited from a
    # supplement abstract ("Abstract 57:") but with empty pages; shared roster.
    ("33148016",
     "Abstract 57: Reduction in Ischemic Stroke With Icosapent Ethyl—Insights From REDUCE-IT",
     ["Bhatt", "Steg", "Miller", "Brinton"], 2021, "Stroke", "",
     "Reduction in Revascularization With Icosapent Ethyl: Insights From REDUCE-IT Revascularization Analyses.",
     ["Peterson", "Bhatt", "Steg", "Miller", "Brinton"], 2021, "Circulation", ""),
    # 35523811: related but distinct radar activity-recognition papers.
    ("35523811",
     "Patient activity recognition using radar sensors and machine learning",
     ["Bhavanasi", "Werthen-Brabants", "Torino"], 2022, "Neural Comput. Appl.", "",
     "Split BiRNN for real-time activity recognition using radar and deep learning.",
     ["Werthen-Brabants", "Bhavanasi", "Torino"], 2022, "Sci Rep", ""),
    # 36844755: unrelated papers with incidental title overlap ("21st century").
    ("36844755",
     "Climate change due to increasing concentration of carbon dioxide and its impacts on environment in 21st century; a mini review",
     ["Kabir", "Khan"], 2023, "J. King Saud Univ.-Sci.", "",
     "The 21st century disaster: The COVID-19 epidemiology, risk factors and control.",
     ["Khan", "Kabir"], 2023, "J King Saud Univ Sci", ""),
]


@pytest.mark.parametrize("pmid,ct,ca,cy,cj,cpg,rt,ra,ry,rj,rpg", _GUARDS,
                         ids=[g[0] for g in _GUARDS])
def test_loadbearing_wrong_paper_guards_stay_high(pmid, ct, ca, cy, cj, cpg,
                                                  rt, ra, ry, rj, rpg):
    c = ClaimedRef(title=ct, authors=ca, year=cy, journal=cj, claimed_pmid=pmid, pages=cpg)
    r = RetrievedRecord(resolved=True, title=rt, authors=ra, year=ry, journal=rj,
                        pmid=pmid, pages=rpg)
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_WRONG_PAPER, (pmid, verdict, m.same_work_reason, m.title_sim)


# ===========================================================================
# Hardening regressions -- realistic recall breaks surfaced by an adversarial
# multi-agent review of the five rules. Each is a GENUINELY DIFFERENT paper the
# first-pass rules quarantined; all must stay VERDICT_WRONG_PAPER. Keyed to the
# rule they exercise so a future loosening is caught.
# ===========================================================================
_HARDENING_NEGATIVES = [
    # RULE A: a run-on attaches a sibling's DOI to a research-SERIES part; the
    # ordinal conflict (Part I vs Part II) must not be overridden by the DOI.
    ("A_series", dict(title="Studies on the mechanism of DNA base excision repair. Part I. Glycosylase kinetics",
                      authors=["Lindahl T"], year=2001, journal="J Biol Chem",
                      claimed_doi="10.1074/jbc.m2003.045678", volume="276", pages="1201-1210"),
     dict(title="Studies on the mechanism of DNA base excision repair. Part II. Endonuclease kinetics",
          authors=["Lindahl T"], year=2003, journal="J Biol Chem",
          doi="10.1074/jbc.m2003.045678", volume="278", pages="3401-3410")),
    # RULE A: a common-surname collision ("Wang L" vs "Wang Y") + a mis-attached
    # DOI must not read as first-author identity (title_sim 0.90 < 0.92 floor).
    ("A_common_surname", dict(title="Machine learning models for early prediction of sepsis in intensive care units",
                              authors=["Wang L"], year=2019, journal="J Am Med Inform Assoc",
                              claimed_doi="10.1093/jamia/ocab234", volume="26", pages="401-410"),
     dict(title="Machine learning models for early detection of acute kidney injury in hospitalized patients",
          authors=["Wang Y"], year=2022, journal="J Am Med Inform Assoc",
          doi="10.1093/jamia/ocab234", volume="29", pages="1123-1132")),
    # RULE B: two DIFFERENT landmark trials sharing serial co-authors, one cited
    # from a supplement abstract -- roster containment (0.5) must exclude it.
    ("B_sibling_trial", dict(title="Dapagliflozin in patients with heart failure and reduced ejection fraction",
                             authors=["McMurray J", "Heerspink H"], year=2019, journal="N Engl J Med", pages="S45"),
     dict(title="Dapagliflozin in patients with chronic kidney disease and reduced ejection fraction",
          authors=["Heerspink H", "Wheeler D"], year=2021, journal="N Engl J Med", pages="1436-1446")),
    ("B_sibling_trial2", dict(title="Rivaroxaban for the prevention of venous thromboembolism in acutely ill patients",
                              authors=["Spyropoulos AC", "Ageno W", "Cohen AT", "Gibson CM"], year=2018, journal="Blood", pages="S487"),
     dict(title="Rivaroxaban for the prevention of major cardiovascular events in coronary artery disease",
          authors=["Eikelboom JW", "Connolly SJ", "Bosch J", "Gibson CM"], year=2017, journal="N Engl J Med", pages="1319-1330")),
    # RULE B (round 2): serial trialists put the SAME core team on different
    # trials, so an abstract cited "first-3 et al." defeats roster containment
    # (1.0) -- the content-coverage guard (DELIVER's "mildly preserved" absent from
    # DAPA-HF's title) keeps it out.
    ("B_shared_core_team", dict(title="Dapagliflozin in Heart Failure with Mildly Reduced or Preserved Ejection Fraction",
                                authors=["Solomon SD", "McMurray JJV", "Claggett B"], year=2022, journal="J Am Coll Cardiol", volume="79", pages="S1900"),
     dict(title="Dapagliflozin in Patients with Heart Failure and Reduced Ejection Fraction",
          authors=["McMurray JJV", "Solomon SD", "Inzucchi SE", "Kober L", "Kosiborod MN", "Martinez FA", "Jhund PS", "Claggett B"],
          year=2019, journal="N Engl J Med", volume="381", pages="1995-2008", doi="10.1056/nejmoa1911303")),
    # RULE A (round 2): serial ANNUAL editions differ only by an embedded arabic
    # year (not a Roman numeral), share the first author, and title_sim ~1.0. A
    # cross-edition DOI mis-attach must not read as same-work -- distinct papers in
    # a series stay wrong-paper.
    ("A_annual_edition", dict(title="Heart Disease and Stroke Statistics-2017 Update: A Report From the American Heart Association",
                              authors=["Benjamin EJ", "Blaha MJ", "Chiuve SE"], year=2017, journal="Circulation",
                              volume="135", pages="e146-e603", claimed_doi="10.1161/CIR.0000000000000659"),
     dict(title="Heart Disease and Stroke Statistics-2019 Update: A Report From the American Heart Association",
          authors=["Benjamin EJ", "Muntner P", "Alonso A"], year=2019, journal="Circulation",
          volume="139", pages="e56-e528", doi="10.1161/cir.0000000000000659")),
    ("A_annual_edition_corporate", dict(title="Standards of Medical Care in Diabetes-2019",
                                        authors=["American Diabetes Association"], year=2019, journal="Diabetes Care",
                                        volume="42", pages="S1-S2", claimed_doi="10.2337/dc21-Sint"),
     dict(title="Standards of Medical Care in Diabetes-2021",
          authors=["American Diabetes Association"], year=2021, journal="Diabetes Care",
          volume="44", pages="S1-S2", doi="10.2337/dc21-sint")),
    # RULE B (round 3): a SHORT generic abstract title ("Empagliflozin in heart
    # failure") cannot disambiguate a drug's trial family -- its few tokens are
    # trivially covered by any sibling trial's full title. The min-specificity guard
    # keeps these different trials out. EMPEROR-Reduced abstract vs EMPEROR-Preserved.
    ("B_generic_abstract_title", dict(title="Empagliflozin in heart failure",
                                      authors=["Packer", "Anker", "Butler", "Filippatos", "Ferreira", "Pocock", "Zannad"],
                                      year=2020, journal="European Heart Journal", volume="41", pages="S917"),
     dict(title="Empagliflozin in heart failure with a preserved ejection fraction",
          authors=["Anker", "Butler", "Filippatos", "Ferreira", "Pocock", "Carson", "Anand", "Packer"],
          year=2021, journal="The New England Journal of Medicine", volume="385", pages="1451-1461")),
    ("B_generic_abstract_title2", dict(title="Dapagliflozin in heart failure",
                                       authors=["McMurray", "Solomon", "Jhund", "Kober", "Kosiborod", "de Boer"],
                                       year=2019, journal="European Heart Journal", volume="40", pages="S878"),
     dict(title="Dapagliflozin in heart failure with mildly reduced or preserved ejection fraction",
          authors=["Solomon", "McMurray", "Claggett", "de Boer", "Jhund", "Kober", "Kosiborod"],
          year=2022, journal="The New England Journal of Medicine", volume="387", pages="1089-1098")),
    # RULE C: a different, longer-titled paper carrying a reprint word but NOT
    # reciting the claimed original citation.
    ("C_reprint_word_different_paper", dict(title="The pathogenesis of atherosclerosis",
                                            authors=["Ross R"], year=1976, journal="N Engl J Med", volume="295", pages="369-377"),
     dict(title="Reprinted from Nature: The pathogenesis of atherosclerosis in chronic kidney disease",
          authors=["Ross R"], year=1999, journal="N Engl J Med", volume="340", pages="115-126",
          publication_types=["Classical Article"])),
    # RULE D: two DIFFERENT same-journal/volume/year Russian papers with
    # transliteration-similar surnames but NO PubMed-bracketed title.
    ("D_diff_foreign_paper", dict(title="Antioxidant activity of flavonoids in experimental atherosclerosis",
                                  authors=["Grigorev"], year=2009, journal="Eksperimental'naia i Klinicheskaia Farmakologiia", volume="72", pages="34-38"),
     dict(title="Antioxidant activity of carotenoids in experimental diabetes",
          authors=["Grigorov"], year=2009, journal="Eksperimental'naia i Klinicheskaia Farmakologiia", volume="72", pages="51-55", language="rus")),
    # RULE E: consortium/cohort group authors whose name appears in a DIFFERENT
    # paper's title (ADNI / TCGA) -- coverage guard must exclude them.
    ("E_consortium_ADNI", dict(title="Amyloid-beta associated cortical thinning in clinically normal elderly",
                               authors=["Alzheimer's Disease Neuroimaging Initiative"], year=2013, journal="Alzheimer's & Dementia", volume="9", pages="381-388"),
     dict(title="The Alzheimer's Disease Neuroimaging Initiative (ADNI): MRI methods",
          authors=["Jack CR", "Bernstein MA", "Fox NC"], year=2013, journal="Alzheimer's & Dementia", volume="9", pages="685-691")),
    ("E_consortium_TCGA", dict(title="Integrated genomic characterization of endometrial carcinoma",
                               authors=["Cancer Genome Atlas Research Network"], year=2013, journal="Nature", volume="497", pages="67-73"),
     dict(title="The Cancer Genome Atlas Research Network: advancing pan-cancer analysis",
          authors=["Weinstein JN", "Collisson EA"], year=2013, journal="Nature", volume="497", pages="1113-1120")),
]


@pytest.mark.parametrize("name,c_kw,r_kw", _HARDENING_NEGATIVES,
                         ids=[h[0] for h in _HARDENING_NEGATIVES])
def test_adversarial_hardening_negatives_stay_wrong_paper(name, c_kw, r_kw):
    c = ClaimedRef(**c_kw)
    r = RetrievedRecord(resolved=True, **r_kw)
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_WRONG_PAPER, (name, verdict, m.same_work_reason, m.title_sim)


def test_same_work_title_gate_not_lowered():
    assert SAME_WORK_TITLE_SIM_MIN >= 0.92


# ---------------------------------------------------------------------
# §15.2 version chain (2026-08-11). Two records that are nodes of ONE
# publication lineage are the SAME WORK; identifiers legitimately change
# between nodes, so identifier inequality alone must not route to wrong-paper.
# ---------------------------------------------------------------------

@pytest.mark.parametrize("pages,expected", [
    ("S100", True),      # supplement page -- the only shape the old regex caught
    ("P1025", True),     # poster
    ("e46", True),       # e-locator (PMC9829249:R20)
    ("e022455", True),   # long e-locator
    ("207", True),       # bare abstract NUMBER (PMC8097933:CR9)
    ("207A", True),      # abstract number with a section letter
    ("548-553", False),  # ordinary page RANGE
    ("1439-1450", False),
    ("e171-232", False),  # e-locator RANGE is an ordinary electronic article
    ("", False),
])
def test_abstract_locator_shapes(pages, expected):
    from cre.f1.work_identity import is_abstract_locator
    assert is_abstract_locator(pages) is expected


def test_page_parity_predicate_is_not_widened():
    """_is_supplement_locator stays NARROW: it is a page-PARITY predicate feeding
    _first_pages_agree, and widening it was measured to stop
    overwhelming_bibliographic_anchor firing on 4 seed-37 rows (LR-1)."""
    from cre.f1.work_identity import _is_supplement_locator
    assert _is_supplement_locator("S100") is True
    assert _is_supplement_locator("e46") is False
    assert _is_supplement_locator("207") is False


def test_version_chain_route1_shared_doi_across_nodes():
    """ROUTE 1: the abstract record carries the FULL PAPER's DOI. Venue, volume
    and pages all differ because they are different nodes of one lineage."""
    c = ClaimedRef(title="Apolipoprotein B and non-HDL-cholesterol better reflect residual "
                         "risk than LDL-cholesterol in statin-treated patients with "
                         "atherosclerosis.",
                   authors=["Johannesen", "Langsted", "Nordestgaard"], year=2021,
                   journal="Atherosclerosis", volume="331", pages="e46",
                   claimed_doi="10.1016/j.jacc.2021.01.027")
    r = RetrievedRecord(resolved=True,
                        title="Apolipoprotein B and Non-HDL Cholesterol Better Reflect "
                              "Residual Risk Than LDL  Cholesterol in Statin-Treated Patients.",
                        authors=["Johannesen", "Mortensen", "Langsted", "Nordestgaard"],
                        year=2021, journal="J Am Coll Cardiol", volume="77",
                        pages="1439-1450", doi="10.1016/j.jacc.2021.01.027")
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "version_chain_same_work"


def test_version_chain_route2_content_lineage_across_venues():
    """ROUTE 2: no shared identifier -- a Lancet conference abstract and the full
    paper in a different journal, with a specific title and high content
    coverage."""
    c = ClaimedRef(title="The impact of adverse media reporting on doctor–patient "
                         "relationships in China: an analysis with propensity-score matching",
                   authors=["Jing", "Wang", "Liu", "Liu"], year=2017,
                   journal="Lancet", volume="390", pages="S100",
                   claimed_doi="10.1016/s0140-6736(17)33238-5")
    r = RetrievedRecord(resolved=True,
                        title="Impact of adverse media reporting on public perceptions of "
                              "the doctor-patient  relationship in China: an analysis with "
                              "propensity score matching method.",
                        authors=["Sun", "Liu", "Liu", "Wang", "Wang", "Hu"], year=2018,
                        journal="BMJ Open", volume="8", pages="e022455",
                        doi="10.1136/bmjopen-2018-022455")
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "version_chain_same_work"


def test_version_chain_requires_a_non_final_node():
    """The locator/preprint gate is load-bearing: the SAME content evidence
    between two ordinary page-ranged articles is not a lineage."""
    c = ClaimedRef(title="The impact of adverse media reporting on doctor–patient "
                         "relationships in China: an analysis with propensity-score matching",
                   authors=["Jing", "Wang", "Liu", "Liu"], year=2017,
                   journal="Lancet", volume="390", pages="100-108")
    r = RetrievedRecord(resolved=True,
                        title="Impact of adverse media reporting on public perceptions of "
                              "the doctor-patient  relationship in China: an analysis with "
                              "propensity score matching method.",
                        authors=["Sun", "Liu", "Liu", "Wang", "Wang", "Hu"], year=2018,
                        journal="BMJ Open", volume="8", pages="e022455")
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


def test_version_chain_keeps_rule_b_sibling_guards_at_full_strength():
    """The relaxation is on TITLE and ROSTER only. A sibling trial reaching the
    rule through an abstract locator is still excluded by the distinctive-token
    and content-coverage guards -- the first draft of this rule dropped both and
    swallowed the whole adversarial-hardening negative set."""
    from cre.f1.work_identity import (
        CONFERENCE_ABSTRACT_CONTENT_COVERAGE_MIN,
        CONFERENCE_ABSTRACT_MIN_DISTINCTIVE_TOKENS, version_chain_same_work)
    assert CONFERENCE_ABSTRACT_CONTENT_COVERAGE_MIN == 0.77
    assert CONFERENCE_ABSTRACT_MIN_DISTINCTIVE_TOKENS == 6
    # DAPA-HF sibling: specific title (8 tokens) but coverage 0.75 < 0.77.
    c = ClaimedRef(title="Dapagliflozin in Heart Failure with Mildly Reduced or "
                         "Preserved Ejection Fraction",
                   authors=["Solomon SD", "McMurray JJV", "Claggett B"], year=2022,
                   journal="J Am Coll Cardiol", volume="79", pages="S1900")
    r = RetrievedRecord(resolved=True,
                        title="Dapagliflozin in Patients with Heart Failure and Reduced "
                              "Ejection Fraction",
                        authors=["McMurray JJV", "Solomon SD", "Claggett B"], year=2019,
                        journal="N Engl J Med", volume="381", pages="1995-2008",
                        doi="10.1056/nejmoa1911303")
    assert version_chain_same_work(c, r, title_similarity=0.8899,
                                   preprint_source=False) is False
