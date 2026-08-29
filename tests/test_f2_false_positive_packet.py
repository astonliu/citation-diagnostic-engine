"""The 21-case adjudicated false-positive packet (2026-08-11).

A human expert read the actual articles behind 21 flagged references and judged
ALL 21 to be the SAME work, so none of them belongs in the ``review_wrong_paper``
band. The fixtures are the packet's WRITTEN/RESOLVED panels verbatim.

The dominant structural fault is an author/title/journal BOUNDARY SHIFT: the
citing paper's reference text is split at the wrong place, so a group author is
left in the title slot, or the journal name is swallowed into it. One misplaced
boundary then makes the author, the title and the journal each look wrong, and the
matcher counts three "independent" disagreements that are really one parsing
fault. RULE A2 (``_doi_anchored_same_work``) answers that by scoring an exact
shared DOI against the citation's slots as a WHOLE.

14 of the 21 are fixed here. The remaining 7 are pinned as ``xfail(strict)`` with
the specific reason each still misses, so that fixing one is reported rather than
passing unnoticed.
"""
from __future__ import annotations

import pytest

from cde.refs.biblio_match import VERDICT_WRONG_PAPER, flag_verdict
from cde.refs.schema import ClaimedRef, RetrievedRecord


def _c(authors, doi, journal, pages, title, volume, year):
    return ClaimedRef(title=title, authors=[a for a in authors if a], year=year,
                      journal=journal, claimed_doi=doi, volume=volume, pages=pages)


def _r(authors, doi, journal, pages, title, volume, year, pubtypes=()):
    return RetrievedRecord(resolved=True, title=title,
                           authors=[a for a in authors if a], year=year,
                           journal=journal, doi=doi, volume=volume, pages=pages,
                           publication_types=list(pubtypes))


# --- the 14 the DOI anchor recovers -----------------------------------------
FIXED = [
 ("PMC10033911:CR25 boundary: collab name holds title words",
  _c(["World Medical Association Declaration of Helsinki"], "10.1001/jama.2013.281053",
     "JAMA", "2191-2194", "Ethical principles for medical research involving human subjects",
     "310", 2013),
  _r(["World Medical Association"], "10.1001/jama.2013.281053", "JAMA", "2191-4",
     "World Medical Association Declaration of Helsinki: ethical principles for medical  research involving human subjects.",
     "310", 2013)),

 ("PMC10757015:bib30 boundary: journal name swallowed into title",
  _c(["Subramanian", "Singireddy", "Krishnamoorthy", "Rajappan"], "10.18433/j3k308",
     "Societe canadienne des sciences pharmaceutiques", "103-111",
     "Nanosponges: a novel class of drug delivery system--review. Journal of pharmacy & pharmaceutical sciences : a publication of the Canadian Society for Pharmaceutical Sciences",
     "15", 2012),
  _r(["S", "S", "Krishnamoorthy", "Rajappan"], "10.18433/j3k308", "J Pharm Pharm Sci",
     "103-11", "Nanosponges: a novel class of drug delivery system--review.", "15", 2012)),

 ("PMC10935370:B3 hyphen tokenisation + Cochrane volume",
  _c(["Mota", "Riera", "Ricci", "Barrett", "de Castria", "Atallah", "Bevilacqua"],
     "10.1002/14651858.CD008932.pub3", "Cochrane Database Syst. Rev.", "CD008932",
     "Nipple-and Areola-sparing Mastectomy for the Treatment of Breast Cancer", "2016", 2016),
  _r(["Mota", "Riera", "Ricci", "Barrett", "de Castria", "Atallah", "Bevilacqua"],
     "10.1002/14651858.CD008932.pub3", "Cochrane Database Syst Rev", "CD008932",
     "Nipple- and areola-sparing mastectomy for the treatment of breast cancer.", "11", 2016)),

 ("PMC11130037:CR117 boundary: group author left in title slot",
  _c(["McCarter", "Antonescu", "Ballman"], "10.1016/j.jamcollsurg.2012.05.008",
     "J Am Coll Surg", "53-59",
     "American College of Surgeons Oncology Group (ACOSOG) Intergroup Adjuvant Gist Study Team. Microscopically positive margins for primary gastrointestinal stromal tumors: analysis of risk factors and tumor recurrence",
     "215", 2012),
  _r(["McCarter", "Antonescu", "Ballman", "DeMatteo",
      "American College of Surgeons Oncology Group (ACOSOG) Intergroup Adjuvant Gist  Study Team"],
     "10.1016/j.jamcollsurg.2012.05.008", "J Am Coll Surg", "53-9; discussion 59-60",
     "Microscopically positive margins for primary gastrointestinal stromal tumors:  analysis of risk factors and tumor recurrence.",
     "215", 2012)),

 ("PMC11827757:LI_5 boundary: title in journal slot, group author in title",
  _c(["Suzuki", "Ono", "Hirasawa"], "10.1016/j.cgh.2022.07.029",
     "Long-term survival endoscopic resection for gastric cancer: Real-world evidence from a multicenter prospective cohort. Clin Gastroenterol Hepatol",
     "307-318 e2", "J WEB/EGC group", "21", 2023),
  _r(["Suzuki", "Ono", "Hirasawa", "Takeuchi", "J-WEB/EGC group"],
     "10.1016/j.cgh.2022.07.029", "Clin Gastroenterol Hepatol", "307-318.e2",
     "Long-term Survival After Endoscopic Resection For Gastric Cancer: Real-world  Evidence From a Multicenter Prospective Cohort.",
     "21", 2023)),

 ("PMC11908408:R10 boundary: leading year moved into author slot",
  _c(["American Heart Association. 2005"], "10.1542/peds.2006-0219", "Pediatrics",
     "e989-e1004",
     "American Heart Association (AHA) guidelines for cardiopulmonary resuscitation (CPR) and emergency cardiovascular care (ECC) of pediatric and neonatal patients: pediatric basic life support",
     "117", 2006),
  _r(["American Heart Association"], "10.1542/peds.2006-0219", "Pediatrics", "e989-1004",
     "2005 American Heart Association (AHA) guidelines for cardiopulmonary  resuscitation (CPR) and emergency cardiovascular care (ECC) of pediatric and neonatal patients: pediatric basic life support.",
     "117", 2006)),

 ("PMC12042236:CIT0030 two ACOG committees, one document",
  _c(["ACOG Committee on Obstetric Practice"], "10.1016/s0029-7844(01)01747-1",
     "Obstetr Gynecol", "159-167",
     "ACOG practice bulletin. Diagnosis and management of preeclampsia and eclampsia. Number 33, January 2002",
     "99", 2002),
  _r(["ACOG Committee on Practice Bulletins--Obstetrics"], "10.1016/s0029-7844(01)01747-1",
     "Obstet Gynecol", "159-67",
     "ACOG practice bulletin. Diagnosis and management of preeclampsia and eclampsia.  Number 33, January 2002.",
     "99", 2002)),

 ("PMC13189598:b17 citing paper abbreviates a 20-word title",
  _c(["Serruys", "Daemen"], "10.1161/circulationaha.106.666826", "Circulation",
     "1433-1439", "Late stent thrombosis", "115", 2007),
  _r(["Serruys", "Daemen"], "10.1161/CIRCULATIONAHA.106.666826", "Circulation",
     "1433-9; discussion 1439",
     "Are drug-eluting stents associated with a higher rate of late thrombosis than  bare metal stents? Late stent thrombosis: a nuisance in both bare metal and drug-eluting stents.",
     "115", 2007)),

 ("PMC8864812:bib5 boundary: collab split across author and title",
  _c(["Coronaviridae"], "10.1038/s41564-020-0695-z", "Nat. Microbiol.", "536-544",
     "Study Group of the International Committee on Taxonomy of, the species Severe acute respiratory syndrome-related coronavirus: classifying 2019-nCoV and naming it SARS-CoV-2",
     "5", 2020),
  _r(["Coronaviridae Study Group of the International Committee on Taxonomy of Viruses"],
     "10.1038/s41564-020-0695-z", "Nat Microbiol", "536-544",
     "The species Severe acute respiratory syndrome-related coronavirus: classifying  2019-nCoV and naming it SARS-CoV-2.",
     "5", 2020)),

 ("PMC9304938:B13 boundary: investigator group left in title",
  _c(["Ting", "Roberts", "Abou Mehrem", "Khurshid", "Drolet", "Monterrosa"],
     "10.1017/ice.2021.380", "Infect Control Hosp Epidemiol", "1-5",
     "Canadian Neonatal Network Investigators, Variability in antimicrobial use among infants born at <33 weeks gestational age",
     "", 2021),
  _r(["Ting", "Roberts", "Abou Mehrem", "Khurshid", "Drolet", "Monterrosa", "Shah",
      "Canadian Neonatal Network (CNN) Investigatorsa"], "10.1017/ice.2021.380",
     "Infect Control Hosp Epidemiol", "128-132",
     "Variability in antimicrobial use among infants born at <33 weeks gestational age.",
     "44", 2023)),

 ("PMC9374052:bib-0031 boundary: roster displaced into the title slot",
  _c(["BJ"], "10.1016/j.cell.2018.10.022",
     "Impact of genetic polymorphisms on human immune cell gene expression. Cell",
     "1701-15", "Singh D, Madrigal A, Valdovino-Gonzalez AG, White BM, Zapardiel-Gonzalo J, et al",
     "175", 2018),
  _r(["Schmiedel", "Singh", "Madrigal", "Valdovino-Gonzalez", "White", "Zapardiel-Gonzalo",
      "Vijayanand"], "10.1016/j.cell.2018.10.022", "Cell", "1701-1715.e16",
     "Impact of Genetic Polymorphisms on Human Immune Cell Gene Expression.", "175", 2018)),

 ("PMC9473640:B73 boundary: journal name prefixed to the title",
  _c(["Sahu", "Pal", "Sharma", "Biswas"], "10.1080/09273948.2016.1249375",
     "Ocul Immunol Inflamm", "753–9",
     "Ocular immunology and inflammation clinical profile, treatment, and visual outcome of ocular toxocara in a tertiary eye care centre",
     "26", 2016),
  _r(["Sahu", "Pal", "Sharma", "Biswas"], "10.1080/09273948.2016.1249375",
     "Ocul Immunol Inflamm", "753-759",
     "Clinical Profile, Treatment, and Visual Outcome of Ocular Toxocara in a Tertiary  Eye Care Centre.",
     "26", 2018)),

 ("PMC9504389:B34 ahead-of-print locator and expanded acronym",
  _c(["Garcia-Cremades", "Vučićević", "Hendrix", "Jayachandran", "Jarlsberg",
      "Grant", "Celum", "Martin", "Baeten", "Marrazzo"], "10.1093/cid/ciac313",
     "Clin. Infect. Dis.", "ciac313",
     "Characterizing HIV-preventive, plasma tenofovir concentrations. A pooled participant-level data analysis from HIV pre-exposure prophylaxis (PrEP) clinical trials",
     "26", 2022),
  _r(["Garcia-Cremades", "Vucicevic", "Hendrix", "Jayachandran", "Jarlsberg", "Grant",
      "Celum", "Martin", "Baeten", "Marrazzo", "Savic"], "10.1093/cid/ciac313",
     "Clin Infect Dis", "1873-1882",
     "Characterizing HIV-Preventive, Plasma Tenofovir Concentrations-A Pooled  Participant-level Data Analysis From Human Immunodeficiency Virus Preexposure Prophylaxis Clinical Trials.",
     "75", 2022)),

 ("PMC9994517:B8 first author dropped, short title",
  _c(["Dominguez", "Lechuga", "Izquierdo-Dominguez", "Rojas-Lechuga", "Mullol", "Alobid"],
     "10.18176/jiaci.0567", "J Investig Allergol Clin Immunol", "30",
     "COVID-19 and olfactory dysfunction", "", 2020),
  _r(["Izquierdo-Dominguez", "Rojas-Lechuga", "Mullol", "Alobid"], "10.18176/jiaci.0567",
     "J Investig Allergol Clin Immunol", "317-326",
     "Olfactory Dysfunction in the COVID-19 Outbreak.", "30", 2020)),
]


@pytest.mark.parametrize("name,claimed,resolved", FIXED, ids=[f[0].split()[0] for f in FIXED])
def test_packet_case_leaves_the_wrong_paper_band(name, claimed, resolved):
    """Adjudicated SAME work, so the row must not sit in the wrong-paper band."""
    verdict, match = flag_verdict(claimed, resolved)
    assert verdict != VERDICT_WRONG_PAPER, (name, verdict, match.same_work_reason)


@pytest.mark.parametrize("name,claimed,resolved", FIXED, ids=[f[0].split()[0] for f in FIXED])
def test_packet_case_is_never_auto_cleared(name, claimed, resolved):
    """A shared DOI lifts a row OUT of the wrong-paper band; it does not clear it.
    Every recovered row stays in an audited review band (§16.2)."""
    verdict, _ = flag_verdict(claimed, resolved)
    assert verdict not in {"match", "cleared", "correct"}, (name, verdict)


# --- the 7 still open, each pinned with the reason it misses ------------------
_NO_DOI = ("no DOI on either side: the anchor this change rests on is absent, and "
           "extending it to DOI-less pairs is a materially larger change")

OPEN = [
 ("PMC12145127:B18", _NO_DOI,
  _c(["World"], "", "Bulletin of the World Health Organization. 2001", "373-4",
     "Ethical principles for medical research involving human subjects", "79", 2001),
  _r(["World Medical Association."], "", "Bull World Health Organ", "373-4",
     "World Medical Association Declaration of Helsinki. Ethical principles for medical  research involving human subjects.",
     "79", 2001)),
 ("PMC12384468:B18", _NO_DOI + "; pages also disagree outright (338-354 vs 16-35)",
  _c(["Fradeani", "Barducci", "Bacherini", "Brennan"], "", "Int. J. Esthet. Dent.",
     "338-354",
     "Esthetic rehabilitation of a severely worn dentition using minimally invasive prosthetic procedures (MIPP)",
     "11", 2016),
  _r(["Fradeani", "Barducci", "Bacherini"], "", "Int J Esthet Dent", "16-35",
     "Esthetic rehabilitation of a worn dentition with a minimally invasive prosthetic  procedure (MIPP).",
     "11", 2016)),
 ("PMC12477600:B50", _NO_DOI + "; guidance document vs the Federal Register notice announcing it",
  _c(["ICH"], "", "", "",
     "M3(R2) Guidance on nonclinical Safety Studies for the conduct of human clinical trials and marketing authorization for pharmaceuticals",
     "", 2009),
  _r(["Food and Drug Administration, HHS"], "", "Fed Regist", "3471-2",
     "International Conference on Harmonisation; Guidance on M3(R2) Nonclinical Safety  Studies for the Conduct of Human Clinical Trials and Marketing Authorization for Pharmaceuticals; availability. Notice.",
     "75", 2010)),
 ("PMC12477600:B82", _NO_DOI + "; guidance document vs the Federal Register notice announcing it",
  _c(["USFDA"], "", "", "",
     "E14 Clinical evaluation of QT/QTc interval prolongation and proarrhythmic potential for non-antiarrhythmic drugs",
     "", 2005),
  _r(["Food and Drug Administration, HHS"], "", "Fed Regist", "61134-5",
     "International Conference on Harmonisation; guidance on E14 Clinical Evaluation of  QT/QTc Interval Prolongation and Proarrhythmic Potential for Non-Antiarrhythmic Drugs; availability. Notice.",
     "70", 2005)),
 ("PMC12477600:B83", _NO_DOI + "; guidance document vs the Federal Register notice announcing it",
  _c(["USFDA"], "", "", "",
     "S7B Nonclinical evaluation of the potential for delayed ventricular repolarization (QT Interval Prolongation) by human pharmaceuticals",
     "", 2005),
  _r(["Food and Drug Administration, HHS"], "", "Fed Regist", "61133-4",
     "International Conference on Harmonisation; guidance on S7B Nonclinical Evaluation  of the Potential for Delayed Ventricular Repolarization (QT Interval Prolongation) by Human Pharmaceuticals; availability. Notice.",
     "70", 2005)),
 ("PMC8645138:CR43",
  "DOI agrees, but boundary-tolerant agreement is only 0.600: 'single-cell' folds to "
  "one token against the resolved 'Single Cell', and the book-series journal slot "
  "('Single-cell omics') adds no recoverable content. A hyphen-splitting change to "
  "canonical_title would fix it and is far wider than this change set.",
  _c(["Dwivedi", "Purohit", "Misra", "Lingeswaran", "Vishnoi", "Pareek", "Sharma", "Misra"],
     "10.1007/s12291-019-0811-0", "Single-cell omics", "69-103",
     "Application of single-cell omics in breast cancer", "", 2019),
  _r(["Dwivedi", "Purohit", "Misra", "Lingeswaran", "Vishnoi", "Pareek", "Misra", "Sharma"],
     "10.1007/s12291-019-0811-0", "Indian J Clin Biochem", "3-18",
     "Single Cell Omics of Breast Cancer: An Update on Characterization and Diagnosis.",
     "34", 2019)),
 ("PMC9976121:bibr38", _NO_DOI + "; StatPearls living chapter, no journal, no pages, "
  "and a 5-year year gap (2021 vs 2026)",
  _c(["Shikdar", "Vashisht", "Bhattacharya"], "", "", "",
     "International normalized ratio (INR)", "", 2021),
  _r(["Shikdar", "Vashisht", "Zubair", "Bhattacharya"], "", "", "",
     "International Normalized Ratio: Assessment, Monitoring, and Clinical  Implications.",
     "", 2026)),
]


@pytest.mark.parametrize("name,why,claimed,resolved", OPEN, ids=[o[0] for o in OPEN])
def test_packet_open_case(name, why, claimed, resolved):
    """Adjudicated SAME work but NOT recovered by this change set.

    Marked xfail(strict) rather than asserted as wrong-paper, so that a later fix
    is reported instead of silently flipping a green test to red.
    """
    verdict, _ = flag_verdict(claimed, resolved)
    if verdict == VERDICT_WRONG_PAPER:
        pytest.xfail(why)
    assert verdict != VERDICT_WRONG_PAPER
