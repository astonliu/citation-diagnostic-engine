"""F2 seed-31 preview fix (2026-07-15) -- regression + generalization.

Seed 31's cached preview surfaced five HIGH rows. They adjudicated:

  * PMC12511533:R301|11602907  -- a JATS field-shift parked the year "2001" in the
    title slot and the journal masthead "Surgery" in the author slot; it bears zero
    title evidence and must route to UNSCOREABLE (numeric_or_year_only_title).
  * PMC8353697:r125|15938103, PMC8353697:r97|12698653 -- Russian articles whose
    PubMed record carries NO Volume (the citing source stored the ISSUE in
    <volume>). They are the SAME translated work and must route to
    review_same_work_variant (RULE F, translated_title_missing_volume_anchors).
  * PMC12841101:B131|15268348, PMC12841101:B331|15267790 -- genuinely DISTINCT
    chemistry works (J Chem Phys); they must STAY review_wrong_paper.

Seed 31 is now BURNED DEVELOPMENT DATA for the two mechanisms exercised here.
PMIDs and exact metadata are used in TESTS only; production logic memorizes no
PMID/PMCID/title/journal. Every mechanism also carries PMID-free generic
positives and the adversarial negatives from the diagnosis.
"""
from __future__ import annotations

import pytest

from cde.refs.biblio_match import (
    VERDICT_MATCH, VERDICT_SAME_WORK_VARIANT, VERDICT_WRONG_PAPER,
    VERDICT_UNSCOREABLE, SAME_WORK_TITLE_SIM_MIN, flag_verdict,
)
from cde.refs.eval_report import build_f2_record, high_band_rate_of_scoreable
from cde.refs.unscoreable import classify_unscoreable
from cde.refs.parser import parse_pmc_xml
from cde.refs.schema import ClaimedRef, RetrievedRecord
from cde.refs.work_identity import (
    TRANSLATION_MISSING_VOLUME_TITLE_MIN, TRANSLATION_MISSING_VOLUME_ROSTER_MIN,
    _journal_family_transliteration,
)

# Auto-clear outcomes -- no diverted row may land here.
_AUTO_CLEAR = {VERDICT_MATCH, "cleared", "correct", "match"}
_RUS = ["English Abstract", "Journal Article"]


def _claimed(d: dict) -> ClaimedRef:
    return ClaimedRef(
        title=d["ct"], authors=list(d["ca"]), year=d["cy"], journal=d["cj"],
        claimed_pmid=d["pmid"], claimed_doi=d.get("cdoi", ""),
        volume=d.get("cv", ""), pages=d.get("cp", ""))


def _resolved(d: dict) -> RetrievedRecord:
    return RetrievedRecord(
        resolved=True, title=d["rt"], authors=list(d["ra"]), year=d["ry"],
        journal=d["rj"], pmid=d["pmid"], doi=d.get("rdoi", ""),
        volume=d.get("rv", ""), pages=d.get("rp", ""),
        language=d.get("rlang", "eng"),
        publication_types=list(d.get("rpub", ["Journal Article"])),
        alternate_titles=list(d.get("ralt", [])))


# ---------------------------------------------------------------------------
# The five EXACT seed-31 preview rows (claimed reconstructed from the preview;
# resolved from canonical PubMed metadata for the five in-scope PMIDs).
# ---------------------------------------------------------------------------
PREVIEW = [
    {  # Row 1: numeric-title parser artifact -> UNSCOREABLE
        "tag": "R301|11602907", "label": 0,
        "ct": "2001", "ca": ["Surgery"], "cy": None, "cj": "",
        "cv": "130", "cp": "748-751", "cdoi": "10.1067/msy.2001.118094",
        "rt": "Differences in arterial and mixed venous IL-6 levels: the lungs as a source of cytokine storm in sepsis.",
        "ra": ["Tyburski", "Dente", "Wilson", "Steffes", "Devlin", "Carlin", "Flynn", "Shanti"],
        "ry": 2001, "rj": "Surgery", "rdoi": "10.1067/msy.2001.118094",
        "rv": "130", "rp": "748-51", "rlang": "eng", "rpub": ["Journal Article"],
        "pmid": "11602907",
        "expect_verdict": VERDICT_UNSCOREABLE, "expect_reason": "numeric_or_year_only_title",
    },
    {  # Row 2: distinct chemistry work -> STAY wrong_paper
        "tag": "B131|15268348", "label": 1,
        "ct": "Assessment of the Perdew-Burke-Ernzerhof exchange-correlation functional",
        "ca": ["Ernzerhof", "Scuseria"], "cy": 1999, "cj": "J Chem Phys",
        "cv": "110", "cp": "5029-5036", "cdoi": "10.1063/1.478401",
        "rt": "Current-dependent extension of the Perdew-Burke-Ernzerhof exchange-correlation functional.",
        "ra": ["Maximoff", "Ernzerhof", "Scuseria"], "ry": 2004, "rj": "J Chem Phys",
        "rdoi": "10.1063/1.1634553", "rv": "120", "rp": "2105-9",
        "rlang": "eng", "rpub": ["Journal Article"], "pmid": "15268348",
        "expect_verdict": VERDICT_WRONG_PAPER, "expect_reason": "",
    },
    {  # Row 3: distinct chemistry work -> STAY wrong_paper
        "tag": "B331|15267790", "label": 1,
        "ct": "The generalized Douglas-Kroll transformation",
        "ca": ["Wolf", "Reiher", "Hess"], "cy": 2002, "cj": "J Chem Phys",
        "cv": "117", "cp": "9215-9226", "cdoi": "10.1063/1.1515314",
        "rt": "Correlated ab initio calculations of spectroscopic parameters of SnO within the framework of the higher-order generalized Douglas-Kroll transformation.",
        "ra": ["Wolf", "Reiher", "Hess"], "ry": 2004, "rj": "J Chem Phys",
        "rdoi": "10.1063/1.1690757", "rv": "120", "rp": "8624-31",
        "rlang": "eng", "rpub": ["Journal Article"], "pmid": "15267790",
        "expect_verdict": VERDICT_WRONG_PAPER, "expect_reason": "",
    },
    {  # Row 4: translated same work, PubMed has NO volume -> variant (low tier)
        "tag": "r125|15938103", "label": 0,
        "ct": "The use of continuous high volume hemodiafiltration in patients with sepsis and multiple organ failure.",
        "ca": ["Biryukova", "Purlo", "Denisova", "Mondoev", "Levina", "Galstyan"],
        "cy": 2005, "cj": "Anesteziologiya i reanimatologiya", "cv": "2", "cp": "69-72",
        "rt": "[Use of continuous high-volume hemofiltration in patients with sepsis and multiple organ dysfunction].",
        "ra": ["Biriukova", "Purlo", "Denisova", "Mondoev", "Levina", "Galstian"],
        "ry": 2005, "rj": "Anesteziol Reanimatol", "rv": "", "rp": "69-71",
        "rlang": "rus", "rpub": _RUS, "pmid": "15938103",
        "expect_verdict": VERDICT_SAME_WORK_VARIANT,
        "expect_reason": "translated_title_missing_volume_anchors",
    },
    {  # Row 5: translated same work, PubMed has NO volume -> variant (high tier)
        "tag": "r97|12698653", "label": 0,
        "ct": "Pancreatic necrosis and its complications. The basic principles of surgical tactics.",
        "ca": ["Gostishchev", "Glushko"], "cy": 2003,
        "cj": "Khirurgiya. Zhurnal im. N.I. Pirogova", "cv": "3", "cp": "50-54",
        "rt": "[Pancreonecrosis and its complications, basic principles of surgical approach].",
        "ra": ["Gostishev", "Glushko"], "ry": 2003, "rj": "Khirurgiia (Mosk)",
        "rv": "", "rp": "50-4", "rlang": "rus", "rpub": _RUS,
        "ralt": ["Pankreonekroz i ego oslozhneniia, osnovnye printsipy khirurgicheskoi taktiki."],
        "pmid": "12698653",
        "expect_verdict": VERDICT_SAME_WORK_VARIANT,
        "expect_reason": "translated_title_missing_volume_anchors",
    },
]


def _record(d: dict) -> dict:
    return build_f2_record(d["pmid"], "PMCsrc", _claimed(d), _resolved(d))


def test_preview_row_count_is_frozen():
    assert len(PREVIEW) == 5
    assert sum(1 for d in PREVIEW if d["label"] == 0) == 3   # R301, r125, r97
    assert sum(1 for d in PREVIEW if d["label"] == 1) == 2   # B131, B331


@pytest.mark.parametrize("d", PREVIEW, ids=[d["tag"] for d in PREVIEW])
def test_preview_rows_route_exactly_as_specified(d):
    rec = _record(d)
    assert rec["verdict"] == d["expect_verdict"], (d["tag"], rec["verdict"])
    reason = rec["same_work_reason"] or rec["unscoreable_reason"]
    assert reason == d["expect_reason"], (d["tag"], reason)
    assert rec["verdict"] not in _AUTO_CLEAR


def test_R301_gate_is_reachable_via_classify_unscoreable_directly():
    d = PREVIEW[0]
    bucket, _reason = classify_unscoreable(_claimed(d), _resolved(d))
    assert bucket == "numeric_or_year_only_title"


def test_three_label0_rows_leave_high_and_two_label1_rows_stay_high():
    by_tag = {d["tag"]: _record(d)["verdict"] for d in PREVIEW}
    # label-0 rows leave the HIGH (review_wrong_paper) band ...
    assert by_tag["R301|11602907"] != VERDICT_WRONG_PAPER
    assert by_tag["r125|15938103"] != VERDICT_WRONG_PAPER
    assert by_tag["r97|12698653"] != VERDICT_WRONG_PAPER
    # ... and the two genuine wrong-papers stay HIGH.
    assert by_tag["B131|15268348"] == VERDICT_WRONG_PAPER
    assert by_tag["B331|15267790"] == VERDICT_WRONG_PAPER


def test_quarantine_accounting_excludes_diverted_rows_from_high_band():
    records = [_record(d) for d in PREVIEW]
    metric = high_band_rate_of_scoreable(records)
    # Only the two chemistry rows remain in the HIGH numerator / scoreable frame.
    assert metric["flagged_f2_high"] == 2
    assert metric["denominator_scoreable"] == 2
    assert metric["same_work_variant_excluded"] == 2      # r125, r97
    assert metric["unscoreable_excluded"] == 1            # R301
    assert metric["high_band_rate_of_scoreable"] == 1.0
    # No diverted row is an auto-clear.
    assert all(r["verdict"] not in _AUTO_CLEAR for r in records)


def test_translated_variants_are_human_reviewed_not_auto_cleared():
    for tag in ("r125|15938103", "r97|12698653"):
        d = next(x for x in PREVIEW if x["tag"] == tag)
        rec = _record(d)
        assert rec["verdict"] == VERDICT_SAME_WORK_VARIANT   # audited quarantine
        assert rec["same_work_reason"] == "translated_title_missing_volume_anchors"


# ===========================================================================
# Two-occurrence XML: the malformed field-shift occurrence becomes UNSCOREABLE
# while a well-formed occurrence carrying the SAME PMID survives independently.
# Proves there is no PMID-level dedup -- each occurrence is its own audit unit.
# ===========================================================================
_TWO_OCCURRENCE_XML = """<?xml version="1.0"?>
<article xmlns:xlink="http://www.w3.org/1999/xlink">
 <front><article-meta>
   <article-id pub-id-type="pmid">99000001</article-id>
   <title-group><article-title>A citing source article</article-title></title-group>
 </article-meta></front>
 <back><ref-list>
  <ref id="R301">
   <element-citation publication-type="journal">
    <person-group person-group-type="author"><name><surname>Surgery</surname></name></person-group>
    <article-title>2001</article-title>
    <volume>130</volume><issue>4</issue><fpage>748</fpage><lpage>751</lpage>
    <pub-id pub-id-type="doi">10.1067/msy.2001.118094</pub-id>
    <pub-id pub-id-type="pmid">11602907</pub-id>
   </element-citation>
  </ref>
  <ref id="R315">
   <element-citation publication-type="journal">
    <person-group person-group-type="author"><name><surname>Tyburski</surname></name><name><surname>Dente</surname></name></person-group>
    <article-title>Differences in arterial and mixed venous IL-6 levels: the lungs as a source of cytokine storm in sepsis.</article-title>
    <source>Surgery</source><year>2001</year><volume>130</volume><fpage>748</fpage><lpage>751</lpage>
    <pub-id pub-id-type="doi">10.1067/msy.2001.118094</pub-id>
    <pub-id pub-id-type="pmid">11602907</pub-id>
   </element-citation>
  </ref>
 </ref-list></back>
</article>"""


def test_two_occurrences_same_pmid_are_distinct_audit_units(tmp_path):
    xml = tmp_path / "PMC_TEST.xml"
    xml.write_text(_TWO_OCCURRENCE_XML)
    refs = parse_pmc_xml(str(xml), source_pmcid="PMC_TEST")
    assert len(refs) == 2
    by_id = {r.citation_id.split(":")[-1]: r for r in refs}
    # Both occurrences carry the SAME claimed PMID (no dedup on it).
    assert by_id["R301"].claimed.claimed_pmid == by_id["R315"].claimed.claimed_pmid == "11602907"

    resolved = _resolved(PREVIEW[0])   # the canonical Tyburski record for 11602907

    malformed = build_f2_record("11602907", "PMC_TEST", by_id["R301"].claimed, resolved)
    assert malformed["verdict"] == VERDICT_UNSCOREABLE
    assert malformed["unscoreable_reason"] == "numeric_or_year_only_title"

    wellformed = build_f2_record("11602907", "PMC_TEST", by_id["R315"].claimed, resolved)
    assert wellformed["verdict"] != VERDICT_UNSCOREABLE   # survives as its own unit
    assert wellformed["verdict"] == VERDICT_MATCH         # it is the correct citation


# ===========================================================================
# RULE F (translated_title_missing_volume_anchors) -- PMID-free generic positives.
# Divergent mastheads (journal_equivalent / near_transliteration both fail) route
# these to RULE F specifically, not the earlier translated_title_metadata rule.
# ===========================================================================
def test_rule_F_high_tier_generic_positive():
    c = ClaimedRef(title="Endoscopic management of large kidney stones in the acute phase",
                   authors=["Kovalenko", "Petrova", "Sokolov"], year=2016,
                   journal="Urologiya. Nauchno-prakticheskiy zhurnal", volume="4", pages="120-127")
    r = RetrievedRecord(resolved=True,
                        title="[Endoscopic management of large kidney stones in the acute period].",
                        authors=["Kovalenkov", "Petrova", "Sokolov"], year=2016,
                        journal="Urologiia (Mosk)", volume="", pages="120-125",
                        language="rus", publication_types=_RUS)
    verdict, m = flag_verdict(c, r)
    assert m.title_sim >= 0.85
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "translated_title_missing_volume_anchors"


def test_rule_F_low_tier_generic_positive_needs_roster_backstop():
    kw = dict(title="Course and treatment of severe acute pancreatitis in older patients",
              year=2014, journal="Khirurgiya i klinicheskaya praktika", volume="2", pages="44-49")
    rkw = dict(title="[Clinical course and treatment results of acute destructive pancreatitis in aged patients].",
               year=2014, journal="Khirurgiia (Mosk)", volume="", pages="44-47",
               language="rus", publication_types=_RUS)
    # roster containment >= 0.60 (3 of 5 surnames shared) -> fires in the low tier
    c = ClaimedRef(authors=["Morozov", "Ivanova", "Fedorov", "Smirnov", "Volkov"], **kw)
    r = RetrievedRecord(resolved=True, authors=["Morozof", "Ivanova", "Fedorov", "Smirnov", "Kozlov"], **rkw)
    verdict, m = flag_verdict(c, r)
    assert TRANSLATION_MISSING_VOLUME_TITLE_MIN <= m.title_sim < 0.85
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "translated_title_missing_volume_anchors"

    # roster containment < 0.60 (1 of 5) -> low-tier backstop keeps it wrong_paper
    c2 = ClaimedRef(authors=["Morozov", "Alpha", "Bravo", "Charlie", "Delta"], **kw)
    r2 = RetrievedRecord(resolved=True, authors=["Morozof", "Victor", "Whiskey", "Xray", "Yankee"], **rkw)
    v2, m2 = flag_verdict(c2, r2)
    assert TRANSLATION_MISSING_VOLUME_TITLE_MIN <= m2.title_sim < 0.85
    assert v2 == VERDICT_WRONG_PAPER


# ===========================================================================
# RULE F adversarial negatives -- each fails exactly ONE precondition and must
# stay wrong_paper. Mastheads are r97-shaped so only RULE F is under test.
# ===========================================================================
_JC, _JR = "Khirurgiya i klinicheskaya praktika", "Khirurgiia (Mosk)"
_TB_C = "Clinical features and treatment outcomes of severe acute pancreatitis in elderly patients"
_TB_R = "[Clinical course and treatment results of acute destructive pancreatitis in aged patients]."
_AUTH_C = ["Morozov", "Ivanova", "Fedorov", "Smirnov", "Volkov"]
_AUTH_R = ["Morozof", "Ivanova", "Fedorov", "Smirnov", "Kozlov"]


def _mk(cv="2", cj=_JC, cy=2015, cp="44-49", rv="", rj=_JR, ry=2015, rp="44-47"):
    c = ClaimedRef(title=_TB_C, authors=_AUTH_C, year=cy, journal=cj, volume=cv, pages=cp)
    r = RetrievedRecord(resolved=True, title=_TB_R, authors=_AUTH_R, year=ry, journal=rj,
                        volume=rv, pages=rp, language="rus", publication_types=_RUS)
    return c, r


def test_rule_F_base_shape_is_a_true_positive():
    # Sanity anchor: the base mid-band shape DOES fire, so each negative below
    # isolates the single precondition it removes.
    verdict, m = flag_verdict(*_mk())
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "translated_title_missing_volume_anchors"


_RULE_F_NEGATIVES = {
    # resolved volume PRESENT -> RULE F defers, RULE D's volume guard governs
    "resolved_volume_present": _mk(rv="80"),
    # first pages differ -> the missing-volume anchor is not corroborated
    "different_first_page": _mk(rp="88-93"),
    # different journal families -> journal-family transliteration fails
    "different_journals": _mk(cj="Nevrologiya i praktika", rj="Kardiologiia (Mosk)"),
    # year gap -> exact-year precondition fails
    "year_gap": _mk(cy=2013, ry=2015),
}


@pytest.mark.parametrize("name", sorted(_RULE_F_NEGATIVES))
def test_rule_F_adversarial_negatives_stay_wrong_paper(name):
    c, r = _RULE_F_NEGATIVES[name]
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_WRONG_PAPER, (name, verdict, m.same_work_reason, m.title_sim)


def test_rule_F_english_record_not_bracketed_stays_wrong_paper():
    c = ClaimedRef(title="Surgical management of acute pancreatitis in elderly patients cohort",
                   authors=["Smith"], year=2012, journal="Surgery Today", volume="4", pages="30-35")
    r = RetrievedRecord(resolved=True,
                        title="Surgical management of acute pancreatitis in elderly patients: a review.",
                        authors=["Smyth"], year=2012, journal="Surgery Today", volume="", pages="30-36",
                        language="eng", publication_types=["Journal Article"])
    assert flag_verdict(c, r)[0] == VERDICT_WRONG_PAPER


# ===========================================================================
# Regression preservation + helper guards.
# ===========================================================================
def test_seed29_translation_with_volume_still_owned_by_rule_D():
    # 12500577 shape: a bracketed translation with a Volume present on BOTH sides
    # routes via RULE D; RULE F's resolved-volume-absent precondition makes it
    # defer, so the volume guard stays intact.
    c = ClaimedRef(title="Cytokine profiles during acute inflammation in a rodent sepsis model",
                   authors=["Kuznetsov"], year=2010, journal="", volume="48")
    r = RetrievedRecord(resolved=True,
                        title="[Cytokine profiles in acute inflammation studied in an experimental sepsis model].",
                        authors=["Kuznetzov"], year=2010, journal="Immunologiia", volume="48",
                        language="rus")
    verdict, m = flag_verdict(c, r)
    assert verdict == VERDICT_SAME_WORK_VARIANT
    assert m.same_work_reason == "translated_title_transliterated_author"


def test_journal_family_transliteration_matches_stem_and_rejects_generic():
    # Matches a shared distinctive stem across divergent mastheads ...
    assert _journal_family_transliteration(
        "Khirurgiya. Zhurnal im. N.I. Pirogova", "Khirurgiia (Mosk)") is True
    assert _journal_family_transliteration(
        "Anesteziologiya i reanimatologiya", "Anesteziol Reanimatol") is True
    # ... but never keys on a generic leading journal word, so two unrelated
    # "Journal of Clinical ..." titles do not collide.
    assert _journal_family_transliteration(
        "Journal of Clinical Oncology", "Journal of Clinical Investigation") is False
    assert _journal_family_transliteration(
        "International Medical Review", "International Medical Journal") is False


def test_rule_F_title_floors_are_rule_local_and_below_the_global_gate():
    assert TRANSLATION_MISSING_VOLUME_TITLE_MIN == 0.78
    assert TRANSLATION_MISSING_VOLUME_ROSTER_MIN == 0.60
    assert TRANSLATION_MISSING_VOLUME_TITLE_MIN < SAME_WORK_TITLE_SIM_MIN
    assert SAME_WORK_TITLE_SIM_MIN == 0.92
