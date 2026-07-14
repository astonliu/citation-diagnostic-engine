"""Precision guards for proof-backed same-work identity rules."""
import pytest
from cre.f1.biblio_match import (
    VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT, VERDICT_WRONG_PAPER,
    field_agreement, flag_verdict, normalize_title,
)
from cre.f1.schema import ClaimedRef, RetrievedRecord
from cre.f1.work_identity import canonical_title, journal_equivalent


def _verdict(claimed, resolved):
    return flag_verdict(claimed, resolved)[0]


def test_bracketed_translation_with_year_and_venue_is_quarantined():
    c = ClaimedRef(title="Arbovirusne infekcije u SR Srbiji",
                   authors=["Bordjoški"], year=1972,
                   journal="Vojnosanit Pregl")
    r = RetrievedRecord(resolved=True, title="[Arbovirus infections in Serbia]",
                        authors=["Bordoski"], year=1972,
                        journal="Vojnosanit Pregl")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "translated_title_metadata"


def test_translation_allows_joint_author_and_venue_transliteration_only():
    c = ClaimedRef(
        title="Polymorphism of the PRLR/AluI gene in pigs",
        authors=["Mihailov"], year=2014, journal="Cytol Genet")
    r = RetrievedRecord(
        resolved=True,
        title="[Polymorphism of the PRLR/AluI gene in pigs]",
        authors=["Mikhailov"], year=2014, journal="Tsitol Genet")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "translated_title_metadata"
    assert "transliterated_author_and_venue" in match.identity_signals


def test_unbracketed_translation_requires_visible_shared_anchors():
    c = ClaimedRef(
        title=("Frequência dos tipos de cefaleia no centro de atendimento "
               "terciário do Hospital das Clínicas da Universidade Federal "
               "de Minas Gerais"),
        authors=["Junior"], year=2012, journal="Rev. Assoc. Med. Bras.")
    r = RetrievedRecord(
        resolved=True,
        title=("Frequency of types of headache in the tertiary care center of "
               "the Hospital das Clinicas of the Universidade Federal de Minas "
               "Gerais, MG, Brazil"),
        authors=["Silva AA Jr"], year=2012,
        journal="Rev Assoc Med Bras (1992)")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "translated_title_shared_anchors"


def test_greek_letters_fold_to_named_forms_but_keep_year_typo_visible():
    c = ClaimedRef(
        title="Β1 and Β2-Adrenergic Receptors Polymorphism in Hypertension",
        authors=["Vriz"], year=2017, journal="Acta Cardiol")
    r = RetrievedRecord(
        resolved=True,
        title="beta1 and beta2-adrenergic receptors polymorphism in hypertension",
        authors=["Vriz"], year=2011, journal="Acta Cardiol")
    verdict, match = flag_verdict(c, r)
    assert match.title_sim == 1.0
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "canonical_title_exact"


def test_chemical_charge_typography_preserves_the_sign():
    plus_parenthesized = "SitABCD is the alkaline Mn(2+) transporter of Salmonella"
    plus_bare = "SitABCD is the alkaline Mn2+ transporter of Salmonella"
    minus_bare = "SitABCD is the alkaline Mn2- transporter of Salmonella"
    assert canonical_title(plus_parenthesized) == canonical_title(plus_bare)
    assert normalize_title(plus_parenthesized) == normalize_title(plus_bare)
    assert canonical_title(plus_bare) != canonical_title(minus_bare)
    assert normalize_title(plus_bare) != normalize_title(minus_bare)


def test_jats_spaced_and_monovalent_chemical_charges_keep_semantics():
    assert normalize_title("Mn 2+ transporter") == normalize_title(
        "Mn2+ transporter")
    assert canonical_title("Na + dependent channel") == canonical_title(
        "Na+ dependent channel")
    assert canonical_title("Na+ dependent channel") != canonical_title(
        "Na- dependent channel")
    # An ordinary prose dash is not an ionic charge.
    assert "minus" not in canonical_title("A - B comparison")


def test_malformed_full_citation_wrapper_needs_embedded_metadata():
    title = ("Multi-modality imaging assessment of native valvular regurgitation: "
             "an EACVI and ESC council of valvular heart disease position paper")
    c = ClaimedRef(
        title=("Lancellotti P, Pibarot P, Chambers J, et al.; Scientific Document "
               "Committee of the European Association of Cardiovascular Imaging. "
               + title + ". Eur Heart J Cardiovasc Imaging. 2022;23:e171-232."))
    r = RetrievedRecord(resolved=True, title=title, authors=["Lancellotti"],
                        year=2022, journal="Eur Heart J Cardiovasc Imaging",
                        volume="23", pages="e171-e232")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "malformed_title_wrapper"


def test_wrapper_grammar_alone_cannot_prove_identity():
    resolved_title = "Deep neural networks for detection of rare tumors"
    c = ClaimedRef(title="Jones et al. 2018. " + resolved_title,
                   authors=["Jones"], year=2018, journal="Journal A")
    r = RetrievedRecord(resolved=True, title=resolved_title,
                        authors=["Smith"], year=2020, journal="Journal B")
    assert _verdict(c, r) == VERDICT_WRONG_PAPER


def test_same_author_directional_containment_sequel_stays_wrong_paper():
    resolved_title = "The effectiveness of cancer therapy in older patients"
    c = ClaimedRef(
        title=(resolved_title + " a multicenter randomized trial of long term "
               "survival and treatment toxicity"),
        authors=["Smith"], year=2024, journal="J Oncology")
    r = RetrievedRecord(resolved=True, title=resolved_title,
                        authors=["Smith"], year=2020, journal="J Oncology")
    verdict, match = flag_verdict(c, r)
    assert match.title_sim < 0.92
    assert verdict == VERDICT_WRONG_PAPER


def test_section_heading_leakage_is_not_generic_containment():
    resolved_title = ("The H1N1 crisis: a case study of the integration of mental "
                      "and behavioral health in public health crises")
    c = ClaimedRef(
        title=("Special Focus " + resolved_title
               + " Background: The H1N1 Crisis and Mental and Behavioral Health Concerns"),
        authors=["Pfefferbaum"], year=2021,
        journal="Disaster Med Public Health Prep")
    r = RetrievedRecord(resolved=True, title=resolved_title,
                        authors=["Pfefferbaum"], year=2012,
                        journal="Disaster Med Public Health Prep")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "malformed_title_wrapper"


def test_corporate_prefix_document_is_quarantined():
    c = ClaimedRef(
        title="Position paper on bisphosphonate-related osteonecrosis of the jaw 2009 update")
    r = RetrievedRecord(
        resolved=True,
        title=("American Association of Oral and Maxillofacial Surgeons position "
               "paper on bisphosphonate-related osteonecrosis of the jaw - 2009 update"))
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "corporate_title_prefix"


def test_title_stem_plus_author_typo_year_and_venue_is_quarantined():
    c = ClaimedRef(title="The post-stroke hemiplegic patient",
                   authors=["Fughl-Meyer"], year=1975,
                   journal="Scand J Rehab Med")
    r = RetrievedRecord(
        resolved=True,
        title="The post-stroke hemiplegic patient. 1. a method for evaluation of physical performance",
        authors=["Fugl-Meyer"], year=1975,
        journal="Scand J Rehabil Med")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "title_stem_same_issue"


def test_corrigendum_and_living_chapter_have_narrow_reasons():
    c1 = ClaimedRef(title="Using ONETEP for accurate and efficient DFT calculations",
                    authors=["Skylaris"], year=2005, journal="J Phys")
    r1 = RetrievedRecord(
        resolved=True,
        title="Corrigendum: Using ONETEP for accurate and efficient DFT calculations",
        authors=["Skylaris"], year=2020, journal="J Phys")
    v1, m1 = flag_verdict(c1, r1)
    assert (v1, m1.same_work_reason) == (
        VERDICT_SAME_WORK_VARIANT, "correction_notice")

    c2 = ClaimedRef(title="Alkaline phosphatase", authors=["Lowe"], year=2024,
                    journal="StatPearls")
    r2 = RetrievedRecord(
        resolved=True,
        title="Serum Alkaline Phosphatase: Clinical and Laboratory Perspectives",
        authors=["Lowe"], year=2026, journal="NCBI Bookshelf StatPearls")
    v2, m2 = flag_verdict(c2, r2)
    assert (v2, m2.same_work_reason) == (
        VERDICT_SAME_WORK_VARIANT, "living_chapter_revision")


def test_one_token_typo_needs_locator_or_adjacent_year_transposition():
    c = ClaimedRef(title="Smear layer: Pathological considerations",
                   authors=["Pashley"], year=1948, journal="Oper Dent")
    r = RetrievedRecord(resolved=True,
                        title="Smear layer: physiological considerations",
                        authors=["Pashley"], year=1984, journal="Oper Dent")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "single_token_metadata_typo"

    # Same author/journal/year but a semantic word change is a possible distinct
    # publication; title similarity is not independent corroboration.
    distinct = RetrievedRecord(
        resolved=True, title="A diagnostic marker for recurrent disease",
        authors=["Lee"], year=2020, journal="J Clin Med")
    claimed = ClaimedRef(title="A prognostic marker for recurrent disease",
                         authors=["Lee"], year=2020, journal="J Clin Med")
    assert _verdict(claimed, distinct) != VERDICT_SAME_WORK_VARIANT


def test_commentary_generic_series_and_doi_controls_stay_wrong_paper():
    original = ClaimedRef(
        title="On the nature of the function expressive of the law of human mortality",
        authors=["Gompertz"], year=1825, journal="Philos Trans")
    commentary = RetrievedRecord(
        resolved=True,
        title=("Deciphering death: a commentary on Gompertz (1825) on the nature "
               "of the function expressive of the law of human mortality"),
        authors=["Kirkwood"], year=2015, journal="Philos Trans")
    assert _verdict(original, commentary) == VERDICT_WRONG_PAPER

    c = ClaimedRef(title="Clinical management of acute liver failure",
                   authors=["Lee"], year=2010)
    r = RetrievedRecord(resolved=True,
                        title="Commentary: Clinical management of acute liver failure",
                        authors=["Jones"], year=2020)
    assert _verdict(c, r) == VERDICT_WRONG_PAPER

    generic_c = ClaimedRef(title="Editorial", authors=["Smith"], year=2020,
                           journal="J Med")
    generic_r = RetrievedRecord(resolved=True, title="Editorial",
                                authors=["Jones"], year=2020, journal="J Med")
    assert _verdict(generic_c, generic_r) == VERDICT_WRONG_PAPER

    series_c = ClaimedRef(
        title="Evolution in closely adjacent plant populations VIII clinal patterns",
        authors=["Antonovics"], year=1971)
    series_r = RetrievedRecord(
        resolved=True,
        title="Evolution in closely adjacent plant populations X long term persistence",
        authors=["Antonovics"], year=1990)
    assert _verdict(series_c, series_r) == VERDICT_WRONG_PAPER

    doi_c = ClaimedRef(title="Neural semantic networks in aging", authors=["Lee"],
                       year=2020, claimed_doi="10.1000/same")
    doi_r = RetrievedRecord(resolved=True,
                            title="Impurity profiling of acetylsalicylic acid",
                            authors=["Jones"], year=2021, doi="10.1000/same")
    assert _verdict(doi_c, doi_r) == VERDICT_WRONG_PAPER


def test_book_and_edition_announcement_are_distinct_documents():
    """An editorial naming a book is not the book's citation target.

    PMID 31643080 is a Cochrane Database editorial announcing the 2019 second
    edition.  Cochrane's own citation guidance identifies the Handbook as a
    separate Higgins/Thomas-edited Wiley book, and the editorial itself cites
    that book as a reference.  Shared title words, year, and an editor appearing
    later in the editorial author list therefore cannot prove work identity.
    """
    handbook = ClaimedRef(
        title="Cochrane Handbook for Systematic Reviews of Interventions",
        authors=["Higgins"], year=2019, journal="John Wiley & Sons")
    announcement = RetrievedRecord(
        resolved=True,
        pmid="31643080",
        title=("Updated guidance for trusted systematic reviews: a new edition "
               "of the Cochrane Handbook for Systematic Reviews of Interventions"),
        authors=["Cumpston", "Li", "Page", "Chandler", "Welch", "Higgins",
                 "Thomas"],
        year=2019, journal="Cochrane Database Syst Rev",
        doi="10.1002/14651858.ED000142")

    verdict, match = flag_verdict(handbook, announcement)

    assert match.fields.author_match is True
    assert match.fields.first_author_match is False
    assert match.same_work_reason == ""
    assert verdict == VERDICT_WRONG_PAPER


def test_generic_edition_announcement_does_not_create_same_work_identity():
    book = ClaimedRef(
        title="Handbook of Auditable Evidence Synthesis",
        authors=["Rivera"], year=2024, journal="Example Academic Press")
    editorial = RetrievedRecord(
        resolved=True,
        title=("Updated guidance: a new edition of the Handbook of Auditable "
               "Evidence Synthesis"),
        authors=["Chen", "Rivera"], year=2024,
        journal="Journal of Evidence Methods")

    verdict, match = flag_verdict(book, editorial)

    assert match.fields.author_match is True
    assert match.fields.first_author_match is False
    assert match.same_work_reason == ""
    assert verdict == VERDICT_WRONG_PAPER


@pytest.mark.parametrize("modifier", [
    "A scoping review of ",
    "A narrative review of ",
    "An updated systematic review of ",
    "A meta-analysis of ",
])
def test_derivative_review_prefixes_never_enter_same_work_quarantine(modifier):
    original_title = (
        "Long term cardiovascular outcomes after childhood cancer treatment "
        "in a nationwide population cohort with detailed adjustment for "
        "treatment era demographic factors comorbidity burden and competing "
        "mortality across three decades of follow up")
    c = ClaimedRef(title=original_title, authors=["Lee"], year=2018,
                   journal="J Clinical Outcomes")
    r = RetrievedRecord(
        resolved=True, title=modifier + original_title,
        authors=["Jones"], year=2024, journal="Reviews in Medicine")
    verdict, match = flag_verdict(c, r)
    assert match.title_sim >= 0.92
    assert verdict == VERDICT_WRONG_PAPER


def test_corporate_author_and_short_journal_do_not_collide():
    c = ClaimedRef(title="A sufficiently distinctive committee report title",
                   authors=["Committee A"], year=2020, journal="J")
    r = RetrievedRecord(resolved=True,
                        title="A sufficiently distinctive committee report title",
                        authors=["Committee B"], year=2010,
                        journal="Journal of Cardiology")
    fields = field_agreement(c, r)
    assert fields.author_match is False
    assert fields.first_author_match is False
    assert fields.journal_match is False
    assert journal_equivalent("J", "Journal of Cardiology") is False
    assert _verdict(c, r) == VERDICT_WRONG_PAPER


def test_coauthor_overlap_does_not_replace_first_author_identity():
    c = ClaimedRef(title="Neural semantic networks in older adults",
                   authors=["Alice"], year=2020, journal="J Cognition")
    r = RetrievedRecord(
        resolved=True, title="Neural semantic processing in younger adults",
        authors=["Bob", "Alice"], year=2020, journal="J Cognition")
    verdict, match = flag_verdict(c, r)
    assert match.title_sim >= 0.85 and match.title_sim < 0.92
    assert match.fields.author_match is True
    assert match.fields.first_author_match is False
    assert verdict == VERDICT_WRONG_PAPER


@pytest.mark.parametrize("resolved_name", [
    "John Smith", "Smith J", "J Smith", "Smith, John",
])
def test_first_author_position_normalizes_provider_name_formats(resolved_name):
    c = ClaimedRef(title="A sufficiently distinctive exact article title",
                   authors=["Smith"], year=2020, journal="J Clinical Medicine")
    r = RetrievedRecord(
        resolved=True, title="A sufficiently distinctive exact article title",
        authors=[resolved_name, "Jones"], year=2020,
        journal="J Clinical Medicine")
    verdict, match = flag_verdict(c, r)
    assert match.fields.author_match is True
    assert match.fields.first_author_match is True
    assert verdict == VERDICT_MATCH


@pytest.mark.parametrize("claimed_name,resolved_name", [
    ("Romeo", "Romeo Casabona"),
    ("Van", "Van der Weyden"),
])
def test_first_author_position_accepts_truncated_compound_surnames(
        claimed_name, resolved_name):
    c = ClaimedRef(title="A sufficiently distinctive exact article title",
                   authors=[claimed_name], year=2020,
                   journal="J Clinical Medicine")
    r = RetrievedRecord(
        resolved=True, title="A sufficiently distinctive exact article title",
        authors=[resolved_name, "Other"], year=2020,
        journal="J Clinical Medicine")
    assert field_agreement(c, r).first_author_match is True
    assert _verdict(c, r) == VERDICT_MATCH


def test_declaration_edition_rule_is_narrow():
    c = ClaimedRef(
        title=("Declaration of Helsinki ethical principles for scientific "
               "requirements and research protocols"),
        year=2013, journal="Bulletin of the World Health Organization")
    r = RetrievedRecord(
        resolved=True,
        title=("World Medical Association Declaration of Helsinki Ethical "
               "principles for medical research involving human subjects"),
        authors=["World Medical Association"], year=2001,
        journal="Bull World Health Organ")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "corporate_declaration_edition"

    distinct = RetrievedRecord(
        resolved=True,
        title="World Medical Association Declaration on Organ Transplantation",
        authors=["World Medical Association"], year=2001,
        journal="Bull World Health Organ")
    assert _verdict(c, distinct) == VERDICT_WRONG_PAPER

    cdc_c = ClaimedRef(title="Quarantine and isolation", authors=["CDC"],
                       year=2022, journal="CDC guidance")
    cdc_r = RetrievedRecord(
        resolved=True,
        title="Science Brief Options to Reduce Quarantine for Contacts of Persons with SARS-CoV-2",
        authors=["CDC"], year=2020, journal="CDC guidance")
    assert _verdict(cdc_c, cdc_r) == VERDICT_WRONG_PAPER


def test_clean_exact_match_remains_match():
    c = ClaimedRef(title="A distinctive exact bibliographic title",
                   authors=["Lee"], year=2020, journal="J Clin Med")
    r = RetrievedRecord(resolved=True,
                        title="A distinctive exact bibliographic title",
                        authors=["Lee"], year=2020, journal="J Clin Med")
    assert _verdict(c, r) == VERDICT_MATCH


def test_exact_review_title_is_not_mistaken_for_a_derivative_relation():
    title = "A review of domain adaptation without target labels"
    c = ClaimedRef(title=title, authors=["Kouw"], year=2019,
                   journal="IEEE Trans Pattern Anal Mach Intell")
    r = RetrievedRecord(resolved=True, title=title + ".", authors=["Kouw"],
                        year=2021,
                        journal="IEEE Trans Pattern Anal Mach Intell")
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "canonical_title_exact"


def test_short_exact_title_needs_same_year_and_venue_or_living_source():
    c = ClaimedRef(title="Catalase in vitro", authors=["Abei"], year=1984,
                   journal="Methods Enzymol")
    r = RetrievedRecord(resolved=True, title="Catalase in vitro.",
                        authors=["Aebi"], year=1984,
                        journal="Methods Enzymol")
    assert _verdict(c, r) == VERDICT_SAME_WORK_VARIANT

    living_c = ClaimedRef(title="Glomus Jugulare", authors=["Thomas"], year=2024,
                          journal="StatPearls")
    living_r = RetrievedRecord(resolved=True, title="Glomus Jugulare.",
                               authors=["Matz"], year=2026, journal="")
    verdict, match = flag_verdict(living_c, living_r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert match.same_work_reason == "living_chapter_revision"


_HELDOUT_WRONG_PAPER_CONTROLS = [
    ("21494410", "spBayes for large univariate and multivariate point-referenced spatio-temporal data models",
     "spBayes: An R Package for Univariate and Multivariate Hierarchical Point-referenced Spatial Models",
     "Finley", "Finley", 2015, 2007, "Journal of Statistical Software", "J Stat Softw"),
    ("28846305", "High Anion Gap Metabolic Acidosis",
     "Anion Gap and Non-Anion Gap Metabolic Acidosis", "Brubaker", "Kharsa",
     2023, 2026, "", ""),
    ("26935792", "The foraging ecology of larval and juvenile fishes",
     "Diel variations in the assemblage structure and foraging ecology of larval and 0+ year juvenile fishes in a man-made floodplain waterbody",
     "Nunn", "Tewson", 2012, 2016, "Rev Fish Biol Fish", "J Fish Biol"),
    ("34992227", "Structural differences in the semantic networks of younger and older adults",
     "Management of validation of HPLC method for determination of acetylsalicylic acid impurities in a new pharmaceutical product",
     "Wulff", "Kowalska", 2022, 2022, "Scientific Reports", "Sci Rep"),
    ("27002359", "South African national HIV prevalence",
     "New insights into HIV epidemic in South Africa: key findings from the National HIV Prevalence, Incidence and Behaviour Survey, 2012",
     "Shisana", "Zuma", 2012, 2016, "incidence and behaviour survey", "Afr J AIDS Res"),
    ("34924703", "Growth and yield of cowpea cultivars under water deficit at different growth stages",
     "Growth responses and differential expression of VrDREB2A gene at different growth stages of mungbean under drought stress",
     "Mousa", "Vu", 2018, 2021, "Legume Research", "Physiol Mol Biol Plants"),
    ("14664599", "Stereochemical Reversal of Nucleophilic Substitution Reactions Depending upon Substituent",
     "Stereochemistry of nucleophilic substitution reactions depending upon substituent: evidence for electrostatic stabilization of pseudoaxial conformers",
     "Romero", "Ayala", 2000, 2003, "J Am Chem Soc", "J Am Chem Soc"),
    ("31913322", "Executive functions predict verbal fluency scores in healthy participants",
     "Re-epithelialization and immune cell behaviour in an ex vivo human skin model",
     "Amunts", "Rakita", 2020, 2020, "Scientific Reports", "Sci Rep"),
    ("25602489", "Doubly blessed: Older adults know more vocabulary and know better what they know",
     "Interactive effects of working memory and trial history on Stroop interference in cognitively healthy aging",
     "Kave", "Aschenbrenner", 2015, 2015, "Psychology and Aging", "Psychol Aging"),
    ("25750242", "On the nature of the function expressive of the law of human mortality, and on a new mode of determining the value of life contingencies",
     "Deciphering death: a commentary on Gompertz (1825) On the nature of the function expressive of the law of human mortality",
     "Gompertz", "Kirkwood", 1825, 2015, "Philos Trans R Soc Lond", "Philos Trans R Soc Lond B"),
    ("21826579", "Place identity and place scale: the impact of place salience",
     "Influence of genetic risk information on parental role identity in adolescent girls and young women from families with fragile X syndrome",
     "Bernardo", "McConkie-Rosell", 2013, 2012, "Psyecology", "J Genet Couns"),
    ("23801981", "The relationship between attachment styles and aggression in adolescents: the mediating role of self-regulation",
     "How to quantify individuality in music performance? Studying artistic expression with averaging procedures",
     "Besharat", "Wollner", 2013, 2013, "Front Psychol", "Front Psychol"),
    ("24382143", "Characterization of the kidney transcriptome of the South American olive mouse Abrothrix olivacea",
     "A genome-wide association study of seed protein and oil content in soybean",
     "Giorello", "Hwang", 2014, 2014, "BMC Genomics", "BMC Genomics"),
    ("31825817", "Personalized nutritional approaches for managing obesity and metabolic diseases through zinc supplementation",
     "Severe magnesium deficiency compromises systemic bone mineral density and aggravates inflammatory bone resorption",
     "Tian", "Belluci", 2020, 2020, "J Nutr Biochem", "J Nutr Biochem"),
    ("32982872", "Parenting, attachment, and aggression: an empirical overview",
     "How Our Gaze Reacts to Another Person's Tears? Experimental Insights Into Eye Tracking Technology",
     "Santona", "Pico", 2020, 2020, "Front Psychol", "Front Psychol"),
    ("34009768", "Quarantine and isolation. 2022",
     "Science Brief: Options to Reduce Quarantine for Contacts of Persons with SARS-CoV-2 Infection Using Symptom Monitoring and Diagnostic Testing",
     "Centers for Disease Control and Prevention",
     "National Center for Immunization and Respiratory Diseases", 2020, 2020, "", ""),
]


@pytest.mark.parametrize("pmid,ct,rt,ca,ra,cy,ry,cj,rj",
                         _HELDOUT_WRONG_PAPER_CONTROLS)
def test_all_heldout_wrong_paper_controls_remain_high(
        pmid, ct, rt, ca, ra, cy, ry, cj, rj):
    c = ClaimedRef(title=ct, authors=[ca], year=cy, journal=cj)
    r = RetrievedRecord(resolved=True, title=rt, authors=[ra], year=ry,
                        journal=rj, pmid=pmid)
    verdict, match = flag_verdict(c, r)
    assert verdict == VERDICT_WRONG_PAPER, (
        pmid, verdict, match.same_work_reason, match.title_sim)
