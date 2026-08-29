"""F2 matcher-revision tests (F2_MATCHER_REVISION_SPEC, 2026-07-25).

Each change lands with its own acceptance-matrix rows, verified independently and
in dependency order A -> B -> C -> D -> E -> F. Fixtures use ONLY the literal
strings from the spec's acceptance matrix (naturally-occurring data discipline).

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_matcher_revision.py -q
"""
from __future__ import annotations

import pytest

from cde.refs.biblio_match import (_canonical_pages, _strict_title_prefix,
                                 field_agreement, flag_verdict,
                                 is_preprint_source, is_preprint_resolved,
                                 VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT,
                                 VERDICT_WRONG_PAPER)
from cde.refs.schema import ClaimedRef, RetrievedRecord


# ======================================================================
# F2-A -- page-range canonicalization
# ======================================================================
def _pages_match(written: str, resolved: str):
    fa = field_agreement(ClaimedRef(pages=written),
                         RetrievedRecord(resolved=True, pages=resolved))
    return fa.pages_match


# §7.2 required equality/inequality cases (written_pages, resolved_pages, expected).
# `is`-comparison against the tri-state literal, never a falsy check.
@pytest.mark.parametrize("written,resolved,expected", [
    ("141-144", "141-4", True),
    ("1083-1091", "1083-91", True),
    ("3143-3421", "3143-421", True),
    ("117-132", "117–32", True),        # en dash
    ("925-928.e4", "925–8.e4", True),
    ("S141-S144", "S141-4", True),      # shared page-side prefix
    ("1199-1208", "1199-8", True),      # boundary carry
    ("1-12", "1-12", True),
    ("9-11", "9-12", False),
    ("", "141-4", None),                # empty vs any -> None
    ("123-5,130-2", "123-125,130-132", True),   # comma segments, independent
])
def test_f2a_pages_match_acceptance(written, resolved, expected):
    assert _pages_match(written, resolved) is expected


# Direct canonicalizer checks (spec §7.1 verified inputs + §7.2 required cases).
@pytest.mark.parametrize("raw,canon", [
    ("141-4", "141-144"),
    ("1083-91", "1083-1091"),
    ("3143-421", "3143-3421"),
    ("117–32", "117-132"),     # en dash
    ("71-8", "71-78"),
    ("1-12", "1-12"),
    ("9-11", "9-11"),
    ("925–8.e4", "925-928.e4"),
    ("S141-S144", "s141-144"),      # shared prefix preserved once
    ("S141-4", "s141-144"),         # end-side prefix supplied by start's
    ("e123-e130", "e123-130"),
    ("e123-30", "e123-130"),
    ("1199-8", "1199-1208"),        # end below start -> boundary carry
    ("001-9", "001-009"),           # leading zeroes preserved
    ("A12-B15", "a12-b15"),         # differing prefixes -> unexpanded
    ("123-5,130-2", "123-125,130-132"),   # comma segments canonicalized independently
    ("S100", "s100"),               # non-range: folded, otherwise unchanged
    ("e0224455", "e0224455"),
    ("CD010442", "cd010442"),
    ("xii-xv", "xii-xv"),           # roman-numeral non-digit range: unexpanded
])
def test_f2a_canonical_pages(raw, canon):
    assert _canonical_pages(raw) == canon


def test_f2a_pages_match_stays_tristate_when_absent():
    assert _pages_match("", "") is None
    assert _pages_match("141-4", "") is None


# ======================================================================
# F2-B -- 10.1101 preprint signal requires a date stamp; resolved-side flag
# ======================================================================
@pytest.mark.parametrize("doi,expected", [
    ("10.1101/2020.02.08.939660", True),    # date-stamped bioRxiv/medRxiv
    ("10.1101/gr.209601.116", False),       # Genome Research (CSHL journal)
    ("10.1101/gad.1255404", False),         # Genes & Development (CSHL journal)
    ("10.48550/arXiv.2101.00001", True),    # arXiv, preprint-only registrant
])
def test_f2b_claimed_preprint_prefix_discrimination(doi, expected):
    claimed = ClaimedRef(claimed_doi=doi)
    assert is_preprint_source(claimed) is expected


# The 79 CSHL-journal rows must stop reading as a preprint on the RESOLVED side
# too (the date-stamp fix protects both directions).
@pytest.mark.parametrize("doi,journal,ptypes,expected", [
    ("10.1101/2020.02.08.939660", "bioRxiv", [], True),
    ("10.1101/gr.209601.116", "Genome Res", [], False),   # CSHL journal
    ("10.1101/gad.1255404", "Genes Dev", [], False),      # CSHL journal
    ("10.1038/s41586-020-2649-2", "Nature", ["Journal Article"], False),
    ("", "medRxiv preprint", [], True),                   # preprint-server journal
    ("", "N Engl J Med", ["Preprint"], True),             # MEDLINE Preprint pubtype
])
def test_f2b_resolved_preprint_discrimination(doi, journal, ptypes, expected):
    rec = RetrievedRecord(resolved=True, doi=doi, journal=journal,
                          publication_types=ptypes)
    assert is_preprint_resolved(rec) is expected


def test_f2b_resolved_preprint_elevates_ordinary_citation_to_high():
    # Citation reads as an ordinary article (no preprint markers); claimed PMID
    # resolves to a date-stamped bioRxiv record -> evidence toward a fault, HIGH.
    c = ClaimedRef(title="A convolutional network approach to variant detection",
                   authors=["Aguilar"], year=2022, journal="Bioinformatics")
    r = RetrievedRecord(resolved=True,
                        title="Deep learning for genomic variant calling in cancer",
                        authors=["Aguilar"], year=2020, journal="bioRxiv",
                        doi="10.1101/2020.07.15.204305")
    v, m = flag_verdict(c, r)
    assert m.resolved_preprint is True
    assert m.same_work_reason == "resolved_preprint_target"
    assert v == VERDICT_WRONG_PAPER


def test_f2b_claimed_preprint_not_elevated_by_resolved_side():
    # Claimed side IS a preprint cite -> the resolved-preprint elevation must not
    # fire (a preprint->preprint cite is not this subtype).
    c = ClaimedRef(title="Deep learning for genomic variant calling in cancer",
                   authors=["Aguilar"], year=2020, journal="bioRxiv")
    r = RetrievedRecord(resolved=True,
                        title="Deep learning for genomic variant calling in cancer",
                        authors=["Aguilar"], year=2020, journal="bioRxiv")
    v, m = flag_verdict(c, r)
    assert m.resolved_preprint is False


def test_f2b_cshl_journal_resolved_not_elevated():
    # Resolved to a CSHL journal (non-date-stamped 10.1101) -> NOT a preprint,
    # must not be elevated by the resolved-side signal.
    c = ClaimedRef(title="Chromatin architecture in development",
                   authors=["Lee"], year=2017, journal="Genome Res")
    r = RetrievedRecord(resolved=True,
                        title="Chromatin architecture in development",
                        authors=["Lee"], year=2017, journal="Genome Res",
                        doi="10.1101/gr.209601.116")
    v, m = flag_verdict(c, r)
    assert m.resolved_preprint is False
    assert v == VERDICT_MATCH


# ======================================================================
# F2-C -- physical-location same-work rule (pages + volume + journal)
# ======================================================================
def test_f2c_same_pages_volume_journal_quarantines_title_divergence():
    # Same physical location (canonicalized pages, volume, journal all agree), the
    # title diverges, and NO field confidently disagrees (author unparsed) ->
    # description defect on the same work -> SAME_WORK_VARIANT (not wrong paper).
    c = ClaimedRef(title="Regulation of the cell cycle in fission yeast",
                   authors=[], year=2003, journal="Nature",
                   volume="425", pages="859-864")
    r = RetrievedRecord(resolved=True,
                        title="Cyclin-dependent kinases and the fission yeast cycle",
                        authors=["Nurse"], year=2003, journal="Nature",
                        volume="425", pages="859-64")   # elided end page (F2-A)
    v, m = flag_verdict(c, r)
    assert m.fields.pages_match is True and m.fields.volume_match is True
    assert m.fields.journal_match is True
    assert m.same_work_reason == "physical_location_same_work"
    assert v == VERDICT_SAME_WORK_VARIANT


def test_item1_physical_location_low_title_is_traced_not_silently_matched():
    # §5.4/§10/§10.1: a physical-location row whose score reaches accept ONLY via
    # corroboration (title_sim < accept) must NOT short-circuit to match -- it is
    # rebanded to review_same_work_variant with an auditable reason, not suppressed
    # silently. Author+journal agree so the override would otherwise floor it to
    # accept; title diverges (low title_sim); coordinates agree.
    c = ClaimedRef(title="Regulation of the cell cycle", authors=["Nurse"],
                   year=2003, journal="Nature", volume="425", pages="859-864")
    r = RetrievedRecord(resolved=True,
                        title="Cyclin-dependent kinases in yeast mitotic control "
                              "and checkpoint signalling across the division cycle",
                        authors=["Nurse"], year=2003, journal="Nature",
                        volume="425", pages="859-64")
    v, m = flag_verdict(c, r)
    assert m.title_sim < 0.85 and m.score >= 0.85    # lifted to accept by boosts
    assert m.same_work_reason == "physical_location_same_work"
    assert v == VERDICT_SAME_WORK_VARIANT            # traced, not silent match


def test_item1_physical_location_genuine_title_match_stays_match():
    # A genuine same_record (title clears accept on its own) with agreeing
    # coordinates is an ordinary match (§5.4 step 8), NOT a coordinate-review row.
    c = ClaimedRef(title="Cyclin-dependent kinases and the fission yeast cycle",
                   authors=["Nurse"], year=2003, journal="Nature",
                   volume="425", pages="859-864")
    r = RetrievedRecord(resolved=True,
                        title="Cyclin-dependent kinases and the fission yeast cycle",
                        authors=["Nurse"], year=2003, journal="Nature",
                        volume="425", pages="859-64")
    v, m = flag_verdict(c, r)
    assert m.title_sim >= 0.85
    assert v == VERDICT_MATCH


def test_f2c_author_disagreement_defers_to_wrong_paper():
    # A confident author disagreement is affirmative distinct-work evidence: the
    # shared page range then reads as a mis-assembled/coincident citation (the
    # run-on-DOI adversarial signature), so F2-C defers to WRONG_PAPER.
    c = ClaimedRef(title="A study of X", authors=["Alpha"], year=2010,
                   journal="Cell", volume="140", pages="123-130")
    r = RetrievedRecord(resolved=True, title="A different study of Y",
                        authors=["Bravo"], year=2010, journal="Cell",
                        volume="140", pages="123-30")
    v, m = flag_verdict(c, r)
    assert m.fields.author_match is False
    assert m.same_work_reason != "physical_location_same_work"
    assert v == VERDICT_WRONG_PAPER


def test_f2c_disagreeing_dois_defer_to_wrong_paper():
    # 37192094-shape: two real papers, different DOIs, coincident vol/pages/journal.
    # Disagreeing DOIs are affirmative distinct-work evidence -> stays WRONG_PAPER.
    c = ClaimedRef(title="Experiences of organisational practices in leadership",
                   authors=["Mousa"], year=2023, journal="BMJ Leader",
                   claimed_doi="10.1136/leader-2022-000653",
                   volume="7", pages="266-272")
    r = RetrievedRecord(resolved=True,
                        title="Clinical academics' experiences during COVID-19",
                        authors=["Trusson"], year=2023, journal="BMJ Lead",
                        doi="10.1136/leader-2020-000414", volume="7",
                        pages="266-272")
    v, m = flag_verdict(c, r)
    assert m.fields.doi_match is False
    assert m.same_work_reason != "physical_location_same_work"
    assert v == VERDICT_WRONG_PAPER


def test_f2c_pmc8015328_ref011_survives_as_wrong_paper():
    # Confirmed TRUE_F2 (ZD): claimed PMID 31169370, Paurodontella persica vs
    # compostiocola. doi_match=True and journal_match=True, but volume and pages
    # do NOT agree -> physical-location rule must NOT fire; stays HIGH.
    c = ClaimedRef(title="Description of Paurodontella persica sp. n. from Iran",
                   authors=["Panahandeh"], year=2019, journal="Zootaxa",
                   claimed_doi="10.11646/zootaxa.4658.1.1",
                   volume="4658", pages="150-160")
    r = RetrievedRecord(resolved=True,
                        title="Description of Paurodontella compostiocola sp. n.",
                        authors=["Panahandeh"], year=2019, journal="Zootaxa",
                        doi="10.11646/zootaxa.4658.1.1",
                        volume="4632", pages="45-52")
    v, m = flag_verdict(c, r)
    assert m.fields.doi_match is True and m.fields.journal_match is True
    assert m.fields.volume_match is False and m.fields.pages_match is False
    assert m.same_work_reason != "physical_location_same_work"
    assert v == VERDICT_WRONG_PAPER


def test_f2c_does_not_fire_on_pages_alone():
    # Pages agree but volume disagrees -> the conjunction must not fire.
    c = ClaimedRef(title="A study of X", authors=["Alpha"], year=2010,
                   journal="Cell", volume="140", pages="123-130")
    r = RetrievedRecord(resolved=True, title="A different study of Y",
                        authors=["Bravo"], year=2011, journal="Cell",
                        volume="150", pages="123-30")
    v, m = flag_verdict(c, r)
    assert m.fields.pages_match is True and m.fields.volume_match is False
    assert m.same_work_reason != "physical_location_same_work"
    assert v == VERDICT_WRONG_PAPER


# ======================================================================
# F2-D -- strict-prefix title same-work rule
# ======================================================================
def test_f2d_strict_prefix_helper_word_boundary():
    # Strict prefix at a word boundary.
    assert _strict_title_prefix(
        "Metals toxicity and oxidative stress in cells",
        "Metals toxicity and oxidative stress in cells and disease models") is True
    # Not a word boundary -> not a prefix.
    assert _strict_title_prefix("metallica studies of the genome pathway",
                                "metallicaseous studies of the genome pathway") is False
    # General containment (embedded mid-string) is NOT a strict prefix.
    assert _strict_title_prefix(
        "The Multidimensional Scale of Perceived Social Support",
        "Psychometric characteristics of the Multidimensional Scale of "
        "Perceived Social Support") is False
    # Equal titles are not a strict prefix.
    assert _strict_title_prefix("Identical distinctive title here",
                                "Identical distinctive title here") is False


def test_f2d_branch_is_review_only_in_current_revision():
    # §11 revival: a strict-prefix pair with guarded author/year corroboration
    # qualifies for the audited same-work band, never match.
    from cde.refs.biblio_match import _F2D_STRICT_PREFIX_ENABLED, _strict_title_prefix
    assert _F2D_STRICT_PREFIX_ENABLED is True
    short = "Tumor microenvironment signalling"
    long = (short + " in metastatic colorectal adenocarcinoma progression and "
            "immune checkpoint evasion mechanisms across an international "
            "multicenter prospective validation cohort with extended survival "
            "followup and molecular subtyping")
    assert _strict_title_prefix(short, long) is True       # helper retained
    c = ClaimedRef(title=short, authors=["Kim"], year=2020, journal="")
    r = RetrievedRecord(resolved=True, title=long, authors=["Kim"], year=2020,
                        journal="")
    v, m = flag_verdict(c, r, accept=0.95)
    assert v == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "strict_prefix_title"


def test_f2d_drug_trial_family_prefix_stays_wrong_paper():
    # EMPEROR-Reduced abstract is a strict prefix of EMPEROR-Preserved, but they
    # are DIFFERENT trials; first-author position disagrees -> WRONG_PAPER.
    c = ClaimedRef(title="Empagliflozin in heart failure",
                   authors=["Packer", "Anker", "Butler"], year=2020,
                   journal="European Heart Journal", volume="41", pages="S917")
    r = RetrievedRecord(resolved=True,
                        title="Empagliflozin in heart failure with a preserved "
                              "ejection fraction",
                        authors=["Anker", "Butler", "Packer"], year=2021,
                        journal="N Engl J Med", volume="385", pages="1451-1461")
    v, m = flag_verdict(c, r)
    assert m.same_work_reason != "strict_prefix_title"
    assert v == VERDICT_WRONG_PAPER


def test_f2d_containment_class_stays_wrong_paper():
    # 2280326-shape: claimed title embedded mid-string (not a prefix) -> the
    # deliberately-untouched containment class must stay WRONG_PAPER.
    ct = "The Multidimensional Scale of Perceived Social Support"
    rt = ("Psychometric characteristics of the Multidimensional Scale of "
          "Perceived Social Support")
    c = ClaimedRef(title=ct, authors=["Zimet"], year=1988, journal="J Pers Assess")
    r = RetrievedRecord(resolved=True, title=rt, authors=["Zimet"], year=1990,
                        journal="J Pers Assess")
    v, m = flag_verdict(c, r)
    assert m.same_work_reason != "strict_prefix_title"
    assert v == VERDICT_WRONG_PAPER


def test_f2d_disagreeing_dois_defer():
    # A truncation carrying the wrong DOI is a mis-assembled citation -> defer.
    c = ClaimedRef(title="Comprehensive analysis of tumor microenvironment signals",
                   authors=[], journal="Cell",
                   claimed_doi="10.1016/j.cell.2019.01.001")
    r = RetrievedRecord(resolved=True,
                        title="Comprehensive analysis of tumor microenvironment "
                              "signals in colorectal cancer progression and immune "
                              "evasion across a multicenter patient cohort study",
                        authors=["Kim"], journal="Cell",
                        doi="10.1016/j.cell.2019.99.999")
    v, m = flag_verdict(c, r)
    assert m.fields.doi_match is False
    assert m.same_work_reason != "strict_prefix_title"


# ======================================================================
# F2-E -- leading title furniture excision
# ======================================================================
from cde.refs.titlefurniture import excise_leading_furniture  # noqa: E402
from cde.refs.parser import parse_pmc_xml  # noqa: E402
from cde.refs.biblio_match import title_sim  # noqa: E402
from cde.refs.eval_report import build_f2_record  # noqa: E402

# (written_title, resolved_title, expected title_sim after excision) -- the spec's
# acceptance table, literal strings.
_F2E_ROWS = [
    ("Chapter Nine - Sculpting the Transcriptome During the Oocyte-to-Embryo "
     "Transition in Mouse",
     "Sculpting the Transcriptome During the Oocyte-to-Embryo Transition in Mouse.",
     1.0),
    ("Chapter Five - In Situ Metabolomics in Cancer by Mass Spectrometry Imaging",
     "In Situ Metabolomics in Cancer by Mass Spectrometry Imaging.", 1.0),
    ("Gopichand; Singh, R.D.; Ahuja, P.S. Biology and chemistry of Ginkgo biloba.",
     "Biology and chemistry of Ginkgo biloba.", 1.0),
]


@pytest.mark.parametrize("wt,rt,expected", _F2E_ROWS)
def test_f2e_excision_recovers_title_sim(wt, rt, expected):
    clean, excised = excise_leading_furniture(wt)
    assert excised != ""                       # furniture was found
    assert round(title_sim(clean, rt), 4) == expected


def test_f2e_no_trailing_excision():
    # A title ending in a capitalized proper noun must NOT be excised (front only).
    wt = "Effect of the antimicrobial agent in Response to Allicin"
    clean, excised = excise_leading_furniture(wt)
    assert excised == ""
    assert clean == wt


def test_f2e_stub_guard_leaves_title_untouched():
    # Excising would leave fewer than 4 content words -> leave untouched.
    wt = "Chapter Nine - Cancer"
    clean, excised = excise_leading_furniture(wt)
    assert excised == "" and clean == wt


def test_f2e_single_word_title_is_unscoreable():
    from cde.refs.biblio_match import VERDICT_UNSCOREABLE
    c = ClaimedRef(title="Anaesthesiology", authors=["X"], year=2010)
    r = RetrievedRecord(resolved=True, title="Regional anaesthesia techniques",
                        authors=["Y"], year=2010)
    rec = build_f2_record("111", "PMCx", c, r)
    assert rec["verdict"] == VERDICT_UNSCOREABLE
    assert rec["unscoreable_reason"] == "single_word_title"


def test_f2e_three_word_real_title_stays_scoreable():
    # The 31665581 guard shape: a real 3-word title must NOT be gated unscoreable.
    from cde.refs.biblio_match import VERDICT_UNSCOREABLE
    c = ClaimedRef(title="Disseminated varicella infection", authors=["Pannu"],
                   year=2019, journal="J Foo")
    r = RetrievedRecord(resolved=True, title="Purple Urine after Catheterization",
                        authors=["Sabanis"], year=2019, journal="J Foo")
    rec = build_f2_record("31665581", "PMCx", c, r)
    assert rec["verdict"] != VERDICT_UNSCOREABLE


def test_f2e_parser_populates_written_title_excised(tmp_path):
    doc = (b'<article><body/><back><ref-list><ref id="r1"><mixed-citation>'
           b'<article-title>Gopichand; Singh, R.D.; Ahuja, P.S. Biology and '
           b'chemistry of Ginkgo biloba.</article-title>'
           b'<source>Nat Prod</source><year>2011</year>'
           b'<pub-id pub-id-type="pmid">10214977</pub-id>'
           b'</mixed-citation></ref></ref-list></back></article>')
    p = tmp_path / "doc.xml"
    p.write_bytes(doc)
    refs = parse_pmc_xml(str(p))
    assert refs
    assert refs[0].claimed.title == "Biology and chemistry of Ginkgo biloba."
    assert refs[0].claimed.written_title_excised == "Gopichand; Singh, R.D.; Ahuja, P.S."


# ======================================================================
# Standing consistency check (group on normalized title + claimed PMID)
# ======================================================================
def test_consistency_check_flags_split_verdicts_in_a_group():
    from cde.refs.eval_report import find_verdict_consistency_conflicts
    # ref68-shape: same claimed PMID + same title, but two different verdicts.
    recs = [
        {"citation_id": "a:ref55", "claimed_pmid": "9984901",
         "written_title": "Regulation of the immune response", "verdict": VERDICT_WRONG_PAPER},
        {"citation_id": "a:ref66", "claimed_pmid": "9984901",
         "written_title": "Regulation of the immune response", "verdict": VERDICT_WRONG_PAPER},
        {"citation_id": "a:ref68", "claimed_pmid": "9984901",
         "written_title": "Regulation of the immune response",
         "verdict": VERDICT_SAME_WORK_VARIANT},
        # an unrelated, internally-consistent group (must NOT be reported)
        {"citation_id": "b:r1", "claimed_pmid": "111",
         "written_title": "Another paper", "verdict": VERDICT_MATCH},
        {"citation_id": "b:r2", "claimed_pmid": "111",
         "written_title": "Another paper", "verdict": VERDICT_MATCH},
    ]
    conflicts = find_verdict_consistency_conflicts(recs)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["claimed_pmid"] == "9984901"
    assert c["citation_ids"] == ["a:ref55", "a:ref66", "a:ref68"]
    assert set(c["verdicts"]) == {VERDICT_WRONG_PAPER, VERDICT_SAME_WORK_VARIANT}


def test_consistency_check_ignores_singletons_and_agreeing_groups():
    from cde.refs.eval_report import find_verdict_consistency_conflicts
    recs = [
        {"citation_id": "x", "claimed_pmid": "1", "written_title": "T",
         "verdict": VERDICT_WRONG_PAPER},
        {"citation_id": "y", "claimed_pmid": "2", "written_title": "U",
         "verdict": VERDICT_MATCH},
    ]
    assert find_verdict_consistency_conflicts(recs) == []


# ======================================================================
# Item 1 (§14.3/§15) -- preprint_published_version requires version evidence
# ======================================================================
def test_item1_preprint_shape_with_disagreeing_dois_is_not_same_work():
    # PMC12733676:B29 shape: preprint-shaped claim, DIFFERENT DOIs (+journal/year
    # disagree) -> a different paper, NOT a version family -> routes to review.
    c = ClaimedRef(title="Least Squares Generative Adversarial Networks",
                   authors=["Mao"], year=2016, journal="arXiv:1611.04076",
                   claimed_doi="10.48550/arXiv.1611.04076")
    r = RetrievedRecord(resolved=True,
                        title="On the Effectiveness of Least Squares Generative "
                              "Adversarial Networks", authors=["Mao"], year=2018,
                        journal="IEEE TPAMI", doi="10.1109/tpami.2018.2872043")
    v, m = flag_verdict(c, r)
    assert m.fields.doi_match is False
    assert m.same_work_reason == "preprint_shape_unconfirmed"
    assert v == VERDICT_WRONG_PAPER


def test_item1_preprint_retitle_with_shared_doi_stays_same_work():
    # A preprint retitle with a shared DOI (doiT): venue changes and year drifts,
    # but the DOI does not disagree -> version family stands.
    c = ClaimedRef(title="Deep learning for variant calling", authors=["Aguilar"],
                   year=2020, journal="bioRxiv",
                   claimed_doi="10.1101/2020.07.15.204305")
    r = RetrievedRecord(resolved=True,
                        title="A convolutional approach to variant detection",
                        authors=["Aguilar"], year=2022, journal="Bioinformatics",
                        doi="10.1101/2020.07.15.204305")
    v, m = flag_verdict(c, r)
    assert m.same_work_reason == "preprint_published_version"
    assert v == VERDICT_SAME_WORK_VARIANT


def test_item1_declared_relation_establishes_version_family():
    # A declared is-preprint-of link on the resolved record establishes the version
    # relation even when the DOI is absent on a side.
    c = ClaimedRef(title="Some preprint title", authors=["Lee"], year=2020,
                   journal="bioRxiv")
    r = RetrievedRecord(resolved=True, title="The published retitle", authors=["Lee"],
                        year=2022, journal="Nature",
                        related_pmids={"is-preprint-of": ["123456"]})
    v, m = flag_verdict(c, r)
    assert m.same_work_reason == "preprint_published_version"
    assert v == VERDICT_SAME_WORK_VARIANT
