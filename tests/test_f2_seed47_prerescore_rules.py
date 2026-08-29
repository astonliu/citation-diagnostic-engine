"""Realistic acceptance fixtures for the rules fixed before seed-47 rescoring."""
from __future__ import annotations

import pytest

from cde.refs.biblio_match import (
    VERDICT_FORMATTING,
    VERDICT_MATCH,
    VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE,
    VERDICT_SAME_WORK_VARIANT,
    VERDICT_WRONG_PAPER,
    SAME_WORK_TITLE_SIM_MIN,
    field_agreement,
    flag_verdict,
    is_corporate_author,
)
from cde.refs.samework import entry_language_trigger
from cde.refs.schema import ClaimedRef, RetrievedRecord
from cde.refs.eval_report import build_f2_record, high_band_rate_of_scoreable
from cde.refs.parser import _first_author_is_collab


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
    # PMC10641984:bib-0065 was HERE and is VOIDED (2026-08-14), not moved.
    #   written : "Zoonoznyye infektsii v ochagakh chumy Respubliki Kazakhstan
    #              [Zoonotic infections in plague foci of the Republic of Kazakhstan]"
    #   resolved: "[Zoonotic infections in plague foci of the Republic of Kazakhstan]."
    # KNOWN L2 NON-COVERAGE, by design. Both titles are Latin script, so the
    # script arm cannot fire; transliterated Russian carries none of the Spanish /
    # French / German / Portuguese function words the stopword arm profiles, so
    # that arm cannot fire either. Only the resolved-bracket arm caught it, and
    # that arm was out of contract (resolved-side only) and is cut.
    #
    # No fourth trigger was added, deliberately. The first authors are Tynybekov
    # (written) and Stepanov (resolved) -- not a transliteration variant of each
    # other -- so we do not actually know these are one work, and no
    # TRANSLATION_* route claims it either (RULE F needs _first_author_typo or
    # first_author_equivalent; neither holds). Building a trigger for a pair we
    # cannot adjudicate ourselves is how a hidden filter gets made. The row
    # returns to review_wrong_paper and an adjudicator decides it.
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


# ===========================================================================
# RULE S may not hand near_identical_title a title it just shortened.
#
# PMC10932574:b26-inj-2346250-125 (seed 43, labelled TRUE_F2) was cleared to
# review_same_work_variant via the PRE-EXISTING near_identical_title route: RULE S
# strips the living-source container off the written title, title_sim rises
# 0.9021 -> 0.9778, crosses SAME_WORK_TITLE_SIM_MIN, and the route fires on a row
# whose author_match is False, first_author_match is False, and whose year,
# journal, volume, pages and DOI are ALL None. A title-similarity route must not
# override two explicit author disagreements with zero address corroboration --
# especially when the similarity is an artifact of a transformation this matcher
# performed itself.
#
# CONSTRAINT: a title MODIFIED by the living-source strip may not reach
# near_identical_title unless at least one of {year, journal, volume, first_page,
# doi} agrees.
#
# TITLE STRINGS BELOW ARE A SIGNATURE-MATCHED RECONSTRUCTION, NOT THE ROW'S TEXT.
# The real strings live in the seed-43 frame, which is not in this checkout. They
# reproduce the exact defect -- living-source pair, raw title_sim below the gate,
# stripped title_sim above it, both author signals False, all five address fields
# None -- and the same route. Swap in the real strings when the frame is in hand;
# the assertion does not change.
# ===========================================================================
def test_living_source_strip_cannot_carry_a_row_into_near_identical_title():
    claimed, resolved = _pair(
        wt="Blunt Abdominal Trauma Injury. StatPearls. Treasure Island (FL)",
        rt="Blunt Abdominal Trauma.",
        wa=["Kwon"], ra=["Lotfollahzadeh", "Burns"])
    verdict, result = flag_verdict(claimed, resolved)
    f = result.fields
    # The signature this guard is pinned to -- tri-state, never a falsy check.
    assert f.author_match is False
    assert f.first_author_match is False
    assert (f.year_match, f.journal_match, f.volume_match,
            f.pages_match, f.doi_match) == (None, None, None, None, None)
    assert result.title_sim >= SAME_WORK_TITLE_SIM_MIN   # the strip lifted it
    assert verdict == VERDICT_WRONG_PAPER, (
        "PMC10932574:b26-inj-2346250-125", verdict, result.same_work_reason)


def test_living_source_strip_still_allowed_when_one_address_field_agrees():
    """The constraint is a corroboration floor, not a blanket kill on RULE S.

    Same shape as the guard above with a single agreeing address field, which is
    all the constraint asks for."""
    claimed, resolved = _pair(
        wt="Blunt Abdominal Trauma Injury. StatPearls. Treasure Island (FL)",
        rt="Blunt Abdominal Trauma.",
        wa=["Kwon"], ra=["Lotfollahzadeh", "Burns"],
        wj="StatPearls", rj="StatPearls")
    verdict, result = flag_verdict(claimed, resolved)
    assert result.fields.journal_match is True
    assert result.title_sim >= SAME_WORK_TITLE_SIM_MIN
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert result.same_work_reason == "near_identical_title"


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


# ===========================================================================
# BLOCKING GUARD for the RULE L2 reorder (2026-08-14).
#
# L2 used to be the first statement in flag_verdict. It is now applied after
# C2/C4 repair, match_score and assess_same_work, so that the named TRANSLATION_*
# identity routes get first refusal. That exposes cross-language pairs to the
# clean-match early return (`not has_confident_disagreement and score >= accept`)
# which they could never reach from an entry-point return.
#
# ZERO cross-language pairs may reach VERDICT_MATCH. A row that reaches `match`
# leaves the audited population entirely and is never seen by a human, so this is
# the one outcome the reorder must not be able to produce. Both arms of the L2
# branch terminate; this asserts it behaviourally rather than trusting the read.
# ===========================================================================
_L2_MATCH_GUARD_PAIRS = [
    # Declined by every identity route -> hard exclusion.
    ("no_identity_proof",
     dict(wt="Les tumeurs et pseudotumeurs du genou",
          rt="Imaging of tumors and tumor-like lesions of the knee.")),
    # Identity-proven translation, and engineered to score HIGH: same year, same
    # venue, agreeing volume/pages/DOI. Before the reorder the L2 entry return
    # caught this; after it, the clean-match return is the hazard.
    ("identity_proven_and_high_scoring",
     dict(wt="Frequência dos tipos de cefaleia no centro de atendimento terciário do Hospital das Clínicas da Universidade Federal de Minas Gerais",
          rt="Frequency of types of headache in the tertiary care center of the Hospital das Clinicas of the Universidade Federal de Minas Gerais, MG, Brazil",
          wa=["Junior"], ra=["Silva AA Jr"], wy=2012, ry=2012,
          wj="Rev. Assoc. Med. Bras.", rj="Rev Assoc Med Bras (1992)",
          wv="58", rv="58", wp="709-713", rp="709-13",
          wd="10.1590/S0104-42302012000600017",
          rd="10.1590/S0104-42302012000600017")),
    # Non-Latin script on one side only, every physical field agreeing.
    ("script_trigger_all_fields_agree",
     dict(wt="Клинические рекомендации по лечению острого панкреатита",
          rt="Clinical guidelines for the treatment of acute pancreatitis.",
          wa=["Ivanov"], ra=["Ivanov"], wy=2015, ry=2015,
          wj="Khirurgiia", rj="Khirurgiia", wv="7", rv="7",
          wp="12-18", rp="12-8", wd="10.17116/hirurgia2015712-18",
          rd="10.17116/hirurgia2015712-18")),
]


@pytest.mark.parametrize(("name", "row"), _L2_MATCH_GUARD_PAIRS)
def test_l2_reorder_never_routes_a_cross_language_pair_to_match(name, row):
    claimed, resolved = _pair(**row)
    assert entry_language_trigger(claimed.title, resolved.title) is not None, name
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict != VERDICT_MATCH, (name, verdict, result.same_work_reason)
    # And the two permitted destinations, nothing else.
    assert verdict in (VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE,
                       VERDICT_SAME_WORK_VARIANT), (name, verdict)


def test_l2_reorder_match_leakage_count_is_zero():
    """The count the contract asks to be reported, asserted as zero."""
    leaked = [
        name for name, row in _L2_MATCH_GUARD_PAIRS
        if flag_verdict(*_pair(**row))[0] == VERDICT_MATCH
    ]
    assert leaked == [], leaked


def test_l2_declined_pair_is_excluded_not_sent_to_wrong_paper():
    """A cross-language pair the identity routes DECLINE exits out_of_scope --
    never review_wrong_paper. That direction is the contract for the reorder."""
    claimed, resolved = _pair(
        wt="Les tumeurs et pseudotumeurs du genou",
        rt="Imaging of tumors and tumor-like lesions of the knee.")
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_OUT_OF_SCOPE_CROSS_LANGUAGE
    assert result.cross_language_trigger in ("script", "stopword")
    assert result.same_work_reason == ""


# RULE B's four positive fixtures were deleted here with the rule (2026-08-14).
# They asserted that an exact shared DOI plus agreeing year and venue routes a
# first-author difference to review_same_work_variant. Two of the six rows Rule B
# cleared across seeds 43 and 45 were labelled TRUE_F2, so the assertion was
# wrong, not merely narrow. One of the four -- the Heliyon Ti6Al4V row,
# PMC8124989:B45-materials-14-02292 -- is now a WRONG_PAPER guard in
# test_shared_doi_never_clears_a_wrong_paper_row below, which is the same fixture
# data carrying the opposite, label-backed expectation.


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
    assert result.same_work_reason == "strict_prefix_title"


# ===========================================================================
# DOI-relaxation regression guards (revision spec §2).
#
# RULE B is CUT. These rows are the fixture that keeps it -- or any successor
# DOI-anchored relaxation -- from coming back. An exact shared DOI plus agreeing
# year, journal, volume and physical location does NOT prove work identity, and
# a row cleared out of the wrong-paper band never reaches an adjudicator.
#
# NOT INCLUDED, and it should be: PMC12442810:cit0019, the second labelled
# TRUE_F2 Rule B cleared. Only its citation_id is recorded in either spec; the
# titles/authors/DOI/year/journal live in the seed-45 frame, which is on Drive
# and not reachable from this checkout. Fabricating a plausible fixture for it
# would put invented evidence in the regression set, so the guard is left unwritten
# and named here instead. Add it when the frames are next in hand.
# ===========================================================================
@pytest.mark.parametrize(("citation_id", "row"), [
    # PMC8124989:B45-materials-14-02292 -- labelled TRUE_F2 under frozen DEC-055.
    # Rule B cleared it to review_same_work_variant on the shared Heliyon DOI while
    # the two titles describe different work and the first authors are transposed.
    ("PMC8124989:B45-materials-14-02292",
     dict(wt="Surface, kerf width and material removal rate of Ti 6 Al 4 V titanium alloy generated by wire electrical discharge machining",
          rt="Understanding the wire electrical discharge machining of Ti6Al4V alloy.",
          wa=["Basak", "Pramanik"], ra=["Pramanik", "Basak"], wy=2019, ry=2019,
          wj="Heliyon", rj="Heliyon", wv="5", rv="5", wp="01473", rp="e01473",
          wd="10.1016/j.heliyon.2019.e01473", rd="10.1016/j.heliyon.2019.e01473")),
    # PMC12494917:bib3 -- the companion. Identical DOI, journal, year, volume and
    # first page; two plainly different papers.
    ("PMC12494917:bib3",
     dict(wt="Compliance and adherence in glaucoma management",
          rt="A comparative study between intravitreal triamcinolone and bevacizumab.",
          wa=["Tripathi"], ra=["Ahmad"], wy=2011, ry=2011,
          wj="Indian J Ophthalmol", rj="Indian J Ophthalmol", wv="59", rv="59",
          wp="93", rp="93", wd="10.4103/0301-4738.77008",
          rd="10.4103/0301-4738.77008")),
])
def test_shared_doi_never_clears_a_wrong_paper_row(citation_id, row):
    claimed, resolved = _pair(**row)
    assert field_agreement(claimed, resolved).doi_match is True, citation_id
    verdict, result = flag_verdict(claimed, resolved)
    assert verdict == VERDICT_WRONG_PAPER, (
        citation_id, verdict, result.same_work_reason)


# RULE K's positive fixture was deleted here with the rule (2026-08-14). It
# asserted three-of-four-anchor corporate rows route to
# corporate_author_three_anchor, and that the DOI-disagreeing variant routes to
# review_formatting. Both dispositions are void: the rule fired twice across
# three frames and zero times on seed 45, against five documented interaction
# defects (see flag_verdict). The acceptance-matrix row "corporate author,
# journal+year+pages agree, DOI False -> review_formatting" is struck with it.
#
# The negative below SURVIVES the cut. is_corporate_author is still live -- RULE A
# uses it -- so the guard that a five-token personal name is not a corporate
# author still has a subject. Renamed off "rule_k" because that rule is gone; the
# assertion is untouched.
def test_corporate_predicate_never_infers_corporate_from_long_personal_name():
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
