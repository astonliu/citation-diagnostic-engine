"""F2 final-revision tests (F2_FINAL_REVISION_SPEC).

Defect A: parser reads <string-name><surname> as well as <name><surname>.
Defect B: flag_verdict diverts a near-identical-title + real-disagreement pair to
          review_same_work_variant BEFORE the wrong-paper branch, and those rows
          are excluded from high_band_rate_of_scoreable.

Plus the load-bearing interaction (31665581 must NOT divert -- it is a genuine
wrong-paper) and the acceptance-matrix mechanisms on constructed fixtures (the
live per-PMID numbers come from the Colab seed=7 run; here we prove behavior).

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_revision.py -q
"""
from __future__ import annotations

import json

from lxml import etree

from cre.f1.parser import _authors_from, _surnames_under, parse_pmc_xml
from cre.f1.biblio_match import (match_score, flag_verdict, field_agreement,
                                 SAME_WORK_TITLE_SIM_MIN, VERDICT_MATCH,
                                 VERDICT_WRONG_PAPER, VERDICT_FORMATTING,
                                 VERDICT_SAME_WORK_VARIANT)
from cre.f1.eval_report import (build_f2_record, high_band_rate_of_scoreable,
                                assert_f2_fixes_loaded)
from cre.f1.schema import ClaimedRef, RetrievedRecord

ACCEPT = 0.85


def _verdict(ct, rt, ca, ra, cy=None, ry=None):
    v, _ = flag_verdict(ClaimedRef(title=ct, authors=ca, year=cy),
                        RetrievedRecord(resolved=True, title=rt, authors=ra, year=ry))
    return v


# ======================================================================
# Defect A -- <string-name><surname> author extraction
# ======================================================================
def test_string_name_surname_parses():                       # spec test 1
    xml = b'<element-citation><string-name><surname>Pannu</surname></string-name>' \
          b'<article-title>t</article-title></element-citation>'
    assert _authors_from(etree.fromstring(xml)) == ["Pannu"]


def test_mixed_name_and_string_name_document_order_no_dupes():  # spec test 2
    xml = b'''<element-citation><person-group person-group-type="author">
        <name><surname>Alpha</surname></name>
        <string-name><surname>Beta</surname></string-name>
        <name><surname>Gamma</surname></name>
      </person-group><article-title>t</article-title></element-citation>'''
    assert _authors_from(etree.fromstring(xml)) == ["Alpha", "Beta", "Gamma"]


def test_nested_name_in_string_name_not_double_counted():
    xml = b'<element-citation><person-group person-group-type="author">' \
          b'<string-name><name><surname>Delta</surname></name></string-name>' \
          b'</person-group></element-citation>'
    assert _authors_from(etree.fromstring(xml)) == ["Delta"]


def test_pure_name_ref_behavior_unchanged():
    # Constraint: refs that already parse under <name><surname> are unchanged.
    xml = b'<element-citation><person-group person-group-type="author">' \
          b'<name><surname>Solo</surname></name></person-group></element-citation>'
    assert _authors_from(etree.fromstring(xml)) == ["Solo"]


def test_31665581_string_name_author_gives_author_match_False(tmp_path):  # spec test 3
    # A mixed-citation whose author sits in <string-name>; after parse the
    # written author populates -> author_match is False (not None) against the
    # resolved (different) paper -> promotes to the wrong-paper band.
    doc = (b'<article><body/><back><ref-list><ref id="r1"><mixed-citation>'
           b'<string-name><surname>Pannu</surname></string-name>'
           b'<article-title>Disseminated varicella infection</article-title>'
           b'<source>J Foo</source><year>2019</year>'
           b'<pub-id pub-id-type="pmid">31665581</pub-id>'
           b'</mixed-citation></ref></ref-list></back></article>')
    p = tmp_path / "doc.xml"
    p.write_bytes(doc)
    refs = parse_pmc_xml(str(p))
    assert refs and refs[0].claimed.authors == ["Pannu"]        # parsed, not lost
    resolved = RetrievedRecord(resolved=True, title="Purple Urine after "
                               "Catheterization", authors=["Sabanis"], year=2019)
    fa = field_agreement(refs[0].claimed, resolved)
    assert fa.author_match is False                              # False, not None


# ======================================================================
# Defect B -- SAME_WORK_VARIANT quarantine
# ======================================================================
def test_threshold_constant_is_095():
    assert SAME_WORK_TITLE_SIM_MIN == 0.95


def test_high_titlesim_author_false_diverts():                # spec test 4
    # Near-identical title, author confidently disagrees, below accept ->
    # review_same_work_variant (isolated: year is None here).
    m = match_score(ClaimedRef(title="Metals toxicity and oxidative stress in disease",
                               authors=["X"]),
                    RetrievedRecord(resolved=True,
                                    title="Metals toxicity and oxidative stress",
                                    authors=["Y"]))
    assert m.title_sim >= SAME_WORK_TITLE_SIM_MIN and m.score < ACCEPT
    assert m.fields.author_match is False and m.fields.year_match is None
    assert _verdict("Metals toxicity and oxidative stress in disease",
                    "Metals toxicity and oxidative stress", ["X"], ["Y"]) \
        == VERDICT_SAME_WORK_VARIANT


def test_anomaly_identical_title_author_year_false_diverts():
    # 32809578 / 29493996 / 15892631 shape.
    assert _verdict("Hypothalamic dysfunction", "Hypothalamic Dysfunction.",
                    ["Aname"], ["Bname"], 2018, 2010) == VERDICT_SAME_WORK_VARIANT


def test_high_titlesim_author_none_does_not_divert():         # spec test 5
    # Tri-state: author_match None (unparsed) must NOT trigger the divert
    # (guarded by `is False`, not `not author_match`).
    v = _verdict("Hypothalamic dysfunction", "Hypothalamic dysfunction",
                 [], ["Bname"])                                 # claimed authors empty
    assert v != VERDICT_SAME_WORK_VARIANT


def test_low_titlesim_disagreement_stays_wrong_paper():       # spec test 6
    v = _verdict("Interactions between plant RRM proteins",
                 "Proteomic comparison of near-isogenic barley",
                 ["X"], ["Y"], 2016, 2016)
    assert v == VERDICT_WRONG_PAPER


def test_same_work_variant_excluded_from_high_band_rate():    # spec test 7
    def rec(v):   # minimal record carrying a verdict
        return {"verdict": v}
    records = [rec(VERDICT_WRONG_PAPER), rec(VERDICT_WRONG_PAPER),
              rec(VERDICT_FORMATTING), rec(VERDICT_MATCH),
              rec(VERDICT_SAME_WORK_VARIANT), rec(VERDICT_SAME_WORK_VARIANT)]
    out = high_band_rate_of_scoreable(records)
    assert out["flagged_f2_high"] == 2                 # only WRONG_PAPER in numerator
    assert out["denominator_scoreable"] == 4           # 6 total - 2 same-work-variant
    assert out["same_work_variant_excluded"] == 2
    assert out["high_band_rate_of_scoreable"] == 0.5   # 2/4, SAME_WORK excluded both ways


# ======================================================================
# Interaction (load-bearing): Defect B must NOT swallow the case Defect A promoted
# ======================================================================
def test_31665581_wrong_paper_not_diverted_to_same_work_variant():
    m = match_score(ClaimedRef(title="Disseminated varicella infection",
                               authors=["Pannu"], year=2019),
                    RetrievedRecord(resolved=True,
                                    title="Purple Urine after Catheterization",
                                    authors=["Sabanis"], year=2019))
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN                # different titles
    v = _verdict("Disseminated varicella infection",
                 "Purple Urine after Catheterization", ["Pannu"], ["Sabanis"], 2019, 2019)
    assert v == VERDICT_WRONG_PAPER                             # HIGH, not diverted


# ======================================================================
# Both-fixes-loaded guard
# ======================================================================
def test_assert_f2_fixes_loaded_passes():
    assert_f2_fixes_loaded()          # raises if either fix is not the loaded code


# ======================================================================
# v3 runner scaffold: acceptance-matrix mechanisms end-to-end + v2 preserved
# ======================================================================
def _c(title, author, year):
    return ClaimedRef(title=title, authors=[author], year=year)


def _r(title, author, year):
    return RetrievedRecord(resolved=True, title=title, authors=[author], year=year)


# (pmid, expected_verdict, claimed, resolved) -- constructed to reproduce each
# acceptance-matrix mechanism (live per-PMID titles/scores come from Colab).
_ACCEPTANCE = [
    # 31665581: Defect A promotes it; different titles -> WRONG_PAPER (HIGH), NOT diverted
    ("31665581", VERDICT_WRONG_PAPER,
     _c("Disseminated varicella infection", "Pannu", 2019),
     _r("Purple Urine after Catheterization", "Sabanis", 2019)),
    # ANOMALY trio: identical title + author/year drift -> SAME_WORK_VARIANT (excluded)
    ("32809578", VERDICT_SAME_WORK_VARIANT,
     _c("Hypothalamic dysfunction", "Aname", 2018),
     _r("Hypothalamic Dysfunction.", "Bname", 2010)),
    ("29493996", VERDICT_SAME_WORK_VARIANT,
     _c("Acute liver failure", "Aname", 2019),
     _r("Acute Liver Failure.", "Bname", 2011)),
    ("15892631", VERDICT_SAME_WORK_VARIANT,
     _c("Metals, toxicity and oxidative stress", "Aname", 2005),
     _r("Metals, toxicity and oxidative stress.", "Bname", 2015)),
    # six regression guards: different titles + disagreement -> WRONG_PAPER (HIGH)
    ("27665045", VERDICT_WRONG_PAPER,
     _c("Interactions between plant RRM proteins", "X", 2016),
     _r("Proteomic comparison of near-isogenic barley", "Y", 2016)),
    ("25750229", VERDICT_WRONG_PAPER,
     _c("The chemical basis of morphogenesis", "Turing", 1952),
     _r("A commentary on Turing's morphogenesis paper", "Smith", 2015)),
    ("16639420", VERDICT_WRONG_PAPER,   # same-author series, part VIII vs X, years differ
     _c("Evolution in closely adjacent plant populations VIII: clinal patterns of "
        "heavy metal tolerance at a mine boundary", "Antonovics", 1971),
     _r("Evolution in closely adjacent plant populations X: long-term persistence "
        "of prereproductive isolation", "Antonovics", 1990)),
    ("32355637", VERDICT_WRONG_PAPER,
     _c("Reframing integration in care", "A", 2018),
     _r("Conceptualising integration a framework", "B", 2015)),
    ("18152150", VERDICT_WRONG_PAPER,
     _c("The heat of shortening and dynamic constants of muscle", "Hill", 1938),
     _r("The heat of activation and heat of shortening in a twitch", "Other", 1949)),
    ("22926653", VERDICT_WRONG_PAPER,
     _c("Etiology clinical profile and prognosis of ARDS", "A", 2013),
     _r("The Berlin definition of ARDS", "B", 2012)),
]


def test_v3_runner_acceptance_matrix_and_metric(tmp_path):
    from cre.f1.f2_run_v3 import run_f2_seed7_v3
    items = [(pmid, "PMCx", c, r) for (pmid, _v, c, r) in _ACCEPTANCE]
    summary = run_f2_seed7_v3(items, out_dir=str(tmp_path))

    # every record banded as the acceptance matrix expects
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    for pmid, expected, _c_, _r_ in _ACCEPTANCE:
        assert recs[pmid]["verdict"] == expected, (
            f"{pmid}: expected {expected}, got {recs[pmid]['verdict']} "
            f"(title_sim={recs[pmid]['title_sim']})")
    # ANOMALY trio band with title_sim >= 0.95; guards + 31665581 with < 0.95
    for pmid in ("32809578", "29493996", "15892631"):
        assert recs[pmid]["title_sim"] >= 0.95
    for pmid in ("31665581", "27665045", "25750229", "16639420",
                 "32355637", "18152150", "22926653"):
        assert recs[pmid]["title_sim"] < 0.95, f"{pmid} must not divert"

    # metric: 7 HIGH (wrong-paper), 3 SAME_WORK_VARIANT excluded from both sides
    assert summary["flagged_f2_high"] == 7
    assert summary["same_work_variant_excluded"] == 3
    assert summary["denominator_scoreable"] == 7          # 10 - 3


def test_v3_runner_preserves_v2(tmp_path):
    from cre.f1.f2_run_v3 import run_f2_seed7_v3
    v2 = tmp_path / "f2_random_oa_seed7_v2.jsonl"
    v2.write_text('{"pmid": "old"}\n')                    # pre-existing v2 output
    run_f2_seed7_v3([("1", "PMC1", _c("A title", "Lee", 2020),
                      _r("A title", "Lee", 2020))], out_dir=str(tmp_path))
    assert v2.read_text() == '{"pmid": "old"}\n'          # untouched
    assert (tmp_path / "f2_random_oa_seed7_v3.jsonl").exists()


def test_v3_runner_refuses_to_write_v2(tmp_path):
    import pytest
    from cre.f1.f2_run_v3 import run_f2_seed7_v3
    with pytest.raises(RuntimeError):
        run_f2_seed7_v3([], out_dir=str(tmp_path), version="v2")
