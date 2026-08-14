"""Realistic acceptance fixtures for the rules fixed before seed-47 rescoring."""
from __future__ import annotations

import pytest

from cre.f1.biblio_match import (
    VERDICT_FORMATTING,
    VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE,
    VERDICT_SAME_WORK_VARIANT,
    VERDICT_WRONG_PAPER,
    field_agreement,
    flag_verdict,
    is_corporate_author,
)
from cre.f1.schema import ClaimedRef, RetrievedRecord
from cre.f1.eval_report import build_f2_record, high_band_rate_of_scoreable
from cre.f1.parser import _first_author_is_collab


def _pair(*, wt, rt, wa=(), ra=(), wy=None, ry=None, wj="", rj="",
          wv="", rv="", wp="", rp="", wd="", rd="", wc=False, rc=False):
    return (
        ClaimedRef(title=wt, authors=list(wa), year=wy, journal=wj,
                   volume=wv, pages=wp, claimed_doi=wd,
                   first_author_is_collab=wc),
        RetrievedRecord(resolved=True, title=rt, authors=list(ra), year=ry,
                        journal=rj, volume=rv, pages=rp, doi=rd,
                        has_collective_author=rc),
    )


@pytest.mark.parametrize(("citation_id", "written", "resolved"), [
    ("PMC8477800:B12",
     "Fatores associados à vulnerabilidade de idosos vivendo com HIV/Aids em Belo Horizonte (MG).",
     "Factors associated with the vulnerability of older people living with HIV/AIDS in Belo Horizonte (MG), Brazil."),
    ("PMC10814133:B39",
     "Sobrepeso y obesidad en niños y adolescentes en México, actualización de la Encuesta Nacional de Salud y Nutrición de Medio Camino",
     "[Overweight and obesity in children and adolescents, 2016 Halfway National Health and Nutrition Survey update]."),
    ("PMC11143784:bb0130",
     "Arthroscopie du genou pour le traitement du lipome arborescens: une revue systématique de la littérature",
     "Knee Arthroscopy for the Treatment of Lipoma Arborescens: A Systematic Review of the Literature."),
    ("PMC11143784:bb0015", "Les tumeurs et pseudotumeurs du genou",
     "Imaging of tumors and tumor-like lesions of the knee."),
    ("PMC12958192:bib26",
     "Die physiologischen Vorgänge im Eutergewebe der Milchkuh während der Trockenstehzeit",
     "[Physiological processes in the mammary gland tissue of dairy cows during the dry period]."),
    ("PMC10641984:bib-0065",
     "Zoonoznyye infektsii v ochagakh chumy Respubliki Kazakhstan [Zoonotic infections in plague foci of the Republic of Kazakhstan]",
     "[Zoonotic infections in plague foci of the Republic of Kazakhstan]."),
    ("PMC9553826:CR51",
     "Propiedades psicométricas de la versión española de la escala Mindful Attention Awareness Scale (MAAS)",
     "Psychometric proprieties of Spanish version of Mindful Attention Awareness Scale (MAAS)."),
])
def test_rule_l2_real_cross_language_rows_leave_f2(citation_id, written, resolved):
    verdict, result = flag_verdict(
        ClaimedRef(title=written), RetrievedRecord(resolved=True, title=resolved))
    assert verdict == VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE, citation_id
    assert result.fields.year_match is None


@pytest.mark.parametrize(("title", "authors", "resolved_authors"), [
    ("Ewing Sarcoma. StatPearls. Treasure", ["Durer", "Shaikh"],
     ["Durer", "Gasalberti", "Shaikh"]),
    ("Laryngeal Cancer. StatPearls. Treasure", ["Koroulakis", "Agarwal"],
     ["Koroulakis", "Agarwal"]),
])
def test_rule_s_real_living_chapters_ignore_revision_year(title, authors,
                                                          resolved_authors):
    claimed, resolved = _pair(
        wt=title, rt=title.split(". StatPearls", 1)[0] + ".",
        wa=authors, ra=resolved_authors, wy=2022, ry=2026)
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert result.same_work_reason == "living_chapter_revision"
    assert result.fields.year_match is None
    # The comparison did not mutate the evidence stored on either input.
    assert claimed.title == title
    assert claimed.year == 2022 and resolved.year == 2026


@pytest.mark.parametrize("row", [
    dict(wt="Role of tubal surgery in the era of assisted reproductive technology: a committee opinion",
         rt="Role of tubal surgery in the era of assisted reproductive technology: a committee  opinion.",
         wa=["Practice Committee of the American Society for reproductive medicine. Electronic address Aao"],
         ra=["Practice Committee of the American Society for Reproductive Medicine. Electronic  address: ASRM@asrm.org"],
         wc=True, rc=True,
         wy=2021, ry=2021, wj="Fertil Steril", rj="Fertil Steril", wv="115", rv="115",
         wp="1143-1150", rp="1143-1150", wd="10.1016/j.fertnstert.2021.01.051", rd="10.1016/j.fertnstert.2021.01.051"),
    dict(wt="Tissue plasminogen activator for acute ischemic stroke",
         rt="Tissue plasminogen activator for acute ischemic stroke.",
         wa=["National Institute of Neurological D and Stroke rt PASSG"],
         ra=["National Institute of Neurological Disorders and Stroke rt-PA Stroke Study Group"],
         # This real row has neither JATS <collab> nor MEDLINE CN provenance;
         # its two differing spans are exact collective-name initialisms.
         wc=False, rc=False,
         wy=1995, ry=1995, wj="N.\xa0Engl. J. Med.", rj="N Engl J Med", wv="333", rv="333",
         wp="1581-1587", rp="1581-7", wd="10.1056/NEJM199512143332401", rd="10.1056/NEJM199512143332401"),
])
def test_rule_a_real_collective_authors_with_identical_fields(row):
    claimed, resolved = _pair(**row)
    fields = field_agreement(claimed, resolved)
    assert fields.journal_match is True       # includes the NBSP case
    assert fields.first_author_match is False
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert result.same_work_reason == "corporate_all_fields_identical"


@pytest.mark.parametrize("row", [
    dict(wt="A review of information retained by patients following consent for reduction mammoplasty",
         rt="Do they listen? A review of information retained by patients following consent for reduction mammoplasty.",
         wa=["listen?"], ra=["Godwin"], wy=2000, ry=2000, wj="Br J Plast Surg", rj="Br J Plast Surg",
         wv="53", rv="53", wp="121-125", rp="121-5", wd="10.1054/bjps.1999.3220", rd="10.1054/bjps.1999.3220"),
    dict(wt="Factors associated with post obturation pain following single-visit nonsurgical root canal treatment: a systematic review",
         rt="Factors associated with postobturation pain following single-visit nonsurgical root canal treatment: A systematic review.",
         wa=["Nagendra babu", "Gutmann"], ra=["Nagendrababu", "Gutmann"], wy=2017, ry=2017,
         wj="Quintessence Int", rj="Quintessence Int", wv="48", rv="48", wp="193-208", rp="193-208",
         wd="10.3290/j.qi.a36894", rd="10.3290/j.qi.a36894"),
    dict(wt="Surface, kerf width and material removal rate of Ti 6 Al 4 V titanium alloy generated by wire electrical discharge machining",
         rt="Understanding the wire electrical discharge machining of Ti6Al4V alloy.",
         wa=["Basak", "Pramanik"], ra=["Pramanik", "Basak"], wy=2019, ry=2019, wj="Heliyon", rj="Heliyon",
         wv="5", rv="5", wp="01473", rp="e01473", wd="10.1016/j.heliyon.2019.e01473", rd="10.1016/j.heliyon.2019.e01473"),
    dict(wt="Scientific opinion—Risk assessment of aflatoxins in food", rt="Risk assessment of aflatoxins in food.",
         wa=["European Food Safety Authority EFSA CONTAM Panel, 2020"], ra=["Schrenk"],
         wy=2020, ry=2020, wj="EFSA J.", rj="EFSA J", wv="18", rv="18", wp="6040", rp="e06040",
         wd="10.2903/j.efsa.2020.6040", rd="10.2903/j.efsa.2020.6040"),
])
def test_rule_b_real_shared_doi_first_author_differs(row):
    claimed, resolved = _pair(**row)
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert result.same_work_reason == "shared_doi_first_author_differs"


@pytest.mark.parametrize("row", [
    dict(wt="Neurologic sequelae of open heart surgery in children",
         rt="Neurologic sequelae of open-heart surgery in children. An 'irritating question'.",
         wa=["Ferry"], ra=["Ferry"], wy=1990, ry=1990, wj="Am J Dis Children", rj="Am J Dis Child",
         wv="114", rv="144", wp="369-373", rp="369-73", wd="10.1001/archpedi.1990.02150270119040", rd="10.1001/archpedi.1990.02150270119040"),
    dict(wt="Focus: Health Equity: Gender Issues in Obstructive Sleep Apnea", rt="Gender Issues in Obstructive Sleep Apnea.",
         wa=["Geer"], ra=["Geer"], wy=2021, ry=2021, wj="Yale J. Biol. Med.", rj="Yale J Biol Med",
         wv="94", rv="94", wp="487", rp="487-496"),
    dict(wt="Extended DNA binding interface beyond the canonical SAP domain contributes to SDE2 function at DNA replication forks",
         rt="Extended DNA-binding interfaces beyond the canonical SAP domain contribute to the function of replication stress regulator SDE2 at DNA replication forks.",
         wa=["Weinheimer"], ra=["Weinheimer"], wy=2022, ry=2022, wj="J. Biol. Chem.", rj="J Biol Chem",
         wv="8", rv="298", wp="102268", rp="102268", wd="10.1016/j.jbc.2022.102268", rd="10.1016/j.jbc.2022.102268"),
    dict(wt="(Alternans before cardioverter-defibrillator) trial",
         rt="The ABCD (Alternans Before Cardioverter Defibrillator) Trial: strategies using T-wave alternans to improve efficiency of sudden cardiac death prevention.",
         wa=["Constantini"], ra=["Costantini"], wy=2009, ry=2009, wj="J Am Coll Cardiol", rj="J Am Coll Cardiol",
         wv="53", rv="53", wp="471-479", rp="471-9", wd="10.1016/j.jacc.2008.08.077", rd="10.1016/j.jacc.2008.08.077"),
])
def test_rule_c_real_strict_extensions_leave_wrong_paper(row):
    claimed, resolved = _pair(**row)
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert result.same_work_reason in {
        "strict_prefix_title", "shared_doi_first_author_differs"}


def test_rule_k_three_of_four_anchors_and_doi_disagreement_disposition():
    base = dict(
        wt="Consensus care pathway for uncommon disease", rt="Different title",
        wa=["International Clinical Working Group"], ra=["Smith"],
        wy=2021, ry=2021, wj="J Clin Care", rj="J Clin Care",
        wp="101-108", rp="101-8")
    claimed, resolved = _pair(**base)
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert result.same_work_reason == "corporate_author_three_anchor"

    claimed, resolved = _pair(**base, wd="10.1/written", rd="10.1/resolved")
    verdict, result = flag_verdict(claimed, resolved)
    assert result.fields.doi_match is False
    assert verdict == VERDICT_FORMATTING
    assert result.same_work_reason == ""


def test_rule_k_never_infers_corporate_from_long_personal_name():
    assert is_corporate_author(
        ClaimedRef(authors=["Maria Del Carmen Garcia Lopez"])) is False


def test_corporate_provenance_and_cross_language_summary_are_persisted():
    try:
        from lxml import etree
    except ImportError:  # pragma: no cover
        import xml.etree.ElementTree as etree
    citation = etree.fromstring(
        b"<element-citation><person-group person-group-type='author'>"
        b"<collab>International Trial Group</collab></person-group>"
        b"</element-citation>")
    assert _first_author_is_collab(citation) is True

    claimed = ClaimedRef(
        title="Les tumeurs et pseudotumeurs du genou",
        first_author_is_collab=True)
    resolved = RetrievedRecord(
        resolved=True,
        title="Imaging of tumors and tumor-like lesions of the knee.",
        has_collective_author=True)
    record = build_f2_record("1", "PMC1", claimed, resolved)
    assert record["written_first_author_is_collab"] is True
    assert record["resolved_has_collective_author"] is True
    metric = high_band_rate_of_scoreable([record])
    assert metric["cross_language_excluded"] == 1
    assert metric["denominator_scoreable"] == 0


@pytest.mark.parametrize("row", [
    dict(wt="Female pheromonal chorusing in an arctiid moth, Utetheisa ornatrix",
         rt="Perception of conspecific female pheromone stimulates female calling in an arctiid moth, Utetheisa ornatrix.",
         wa=["Lim"], ra=["Lim"], wy=2007, ry=2007, wj="Behav Ecol", rj="J Chem Ecol",
         wv="18", rv="33", wp="165-173", rp="1257-71", wd="10.1007/s10886-007-9291-4", rd="10.1007/s10886-007-9291-4"),
    dict(wt="Food and bait preferences of Liometopum occidentale (hymenoptera: Formicidae)",
         rt="The survivorship and water loss of Liometopum luctuosum (Hymenoptera: Formicidae) and Liometopum occidentale (Hymenoptera: Formicidae) exposed to different temperatures and relative humidity.",
         wa=["Hoey-Chamberlain"], ra=["Hoey-Chamberlain"], wy=2014, ry=2014, wj="J Entomol Sci", rj="J Insect Sci",
         wv="49", rv="14", wp="30-43", wd="10.1093/jisesa/ieu111", rd="10.1093/jisesa/ieu111"),
    dict(wt="Accuracy of single and multi-trait genomic prediction models for grain yield in US Pacific Northwest winter wheat.",
         rt="Genomic Selection in Winter Wheat Breeding Using a Recommender Approach.",
         wa=["Lozada"], ra=["Lozada"], wy=2019, ry=2020, wj="Crop Breed. Genet. Genom.", rj="Genes (Basel)",
         wv="1", rv="11", wd="10.3390/genes11070779", rd="10.3390/genes11070779"),
    dict(wt="Management of bacterial spot of tomato caused by copper-resistant Xanthomonas perforans using a small molecule compound carvacrol",
         rt="Evaluation of a Small-Molecule Compound, N-Acetylcysteine, for the Management of Bacterial Spot of Tomato Caused by Copper-Resistant Xanthomonas perforans.",
         wa=["Qiao"], ra=["Qiao"], wy=2020, ry=2021, wj="Crop Protection", rj="Plant Dis",
         wv="132", rv="105", wp="105114", rp="108-113", wd="10.1094/PDIS-05-20-0928-RE", rd="10.1094/PDIS-05-20-0928-RE"),
    dict(wt="TMD and occlusion Part II. Damned if we do? Occlusion: The interface of dentistry, orthodontics, and TMD",
         rt="TMD and occlusion part I. Damned if we do? Occlusion: the interface of dentistry and orthodontics.",
         wa=["Luther"], ra=["Luther"], wy=2007, ry=2007, wj="Br Dent J", rj="Br Dent J",
         wv="202", rv="202", wp="E2", rp="E2; discussion 38-9", wd="10.1038/bdj.2006.122", rd="10.1038/bdj.2006.122"),
    dict(wt="The QSAR toolbox automated read-across workflow for predicting acute oral toxicity: II. Verification and validation",
         rt="Automated read-across workflow for predicting acute oral toxicity: I. The decision scheme in the QSAR toolbox.",
         wa=["Kutsarova"], ra=["Kutsarova"], wy=2021, ry=2021, wj="Comput. Toxicol", rj="Regul Toxicol Pharmacol",
         wv="20", rv="125", wp="100194", rp="105015", wd="10.1016/j.yrtph.2021.105015", rd="10.1016/j.yrtph.2021.105015"),
])
def test_doi_loss_and_series_guards_remain_wrong_paper(row):
    claimed, resolved = _pair(**row)
    verdict, _ = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_WRONG_PAPER
