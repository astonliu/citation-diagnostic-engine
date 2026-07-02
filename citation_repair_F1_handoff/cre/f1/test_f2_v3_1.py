"""F2 v3.1 fix tests (F2_V3_1_FIX_SPEC).

Bug 1: build_f2_record applies the SAME classify_unscoreable gate the live path
       uses BEFORE scoring, so an empty / non-title / book-container claimed ref
       bands as VERDICT_UNSCOREABLE (not a fabricated title_sim=0.0 WRONG_PAPER),
       and high_band_rate_of_scoreable drops it from BOTH the HIGH count and the
       denominator. This closes the 303-row empty-title leak (331 -> ~28 HIGH).
Bug 2: normalize_title / the shared name+title normalizer fold Unicode dash
       variants (U+2010/2011/2013/2014 ...) to ASCII '-' before the intra-token
       hyphen collapse, so 'Topka-Bielecka' spelled with U+2010 compares equal to
       the ASCII spelling (author_match True; title_sim >= 0.95).
Plus: the offline reband_from_cache entry point (join on (src_pmcid, claimed_pmid),
      no re-fetch, writes *_seed7_v3_1.*, preserves v3), and the six regression
      guards staying HIGH after both fixes.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_v3_1.py -q
"""
from __future__ import annotations

import json
import os

import pytest
from lxml import etree

from cre.f1.biblio_match import (normalize_title, title_sim, field_agreement,
                                 match_score, flag_verdict, _norm,
                                 SAME_WORK_TITLE_SIM_MIN, VERDICT_MATCH,
                                 VERDICT_WRONG_PAPER, VERDICT_FORMATTING,
                                 VERDICT_SAME_WORK_VARIANT, VERDICT_UNSCOREABLE)
from cre.f1.eval_report import (build_f2_record, high_band_rate_of_scoreable,
                                _F2_RECORD_KEYS)
from cre.f1.lookup import _normalize
from cre.f1.parser import parse_pmc_xml
from cre.f1.schema import ClaimedRef, RetrievedRecord, UNSCOREABLE as SCHEMA_UNSCOREABLE

ACCEPT = 0.85

# U+2010 HYPHEN (the codepoint that appears in PubMed/Crossref names).
U2010 = "‐"


# ======================================================================
# Bug 1 -- UNSCOREABLE gate in build_f2_record
# ======================================================================
def test_empty_title_bands_unscoreable_not_wrong_paper():        # spec test 1
    c = ClaimedRef(title="", authors=["Norris"], year=2019, journal="J Foo")
    r = RetrievedRecord(resolved=True, title="A real resolved paper title",
                        authors=["Smith"], year=2005)
    rec = build_f2_record("28146066", "PMC1", c, r)
    assert rec["verdict"] == VERDICT_UNSCOREABLE
    assert rec["unscoreable_reason"] == "no_claimed_title"
    # scores are NOT fabricated (the old bug scored title_sim=0.0 -> WRONG_PAPER)
    assert rec["title_sim"] is None
    assert rec["match_score"] is None
    assert rec["author_match"] is None
    assert rec["flag"] is None


def test_unscoreable_record_keeps_canonical_schema():
    # the UNSCOREABLE build path emits EXACTLY the canonical keys (re-bandable,
    # JSON-round-trippable) -- same key set as a scoreable record.
    c = ClaimedRef(title="", authors=[], year=None, journal="")
    r = RetrievedRecord(resolved=True, title="X", authors=["Y"], year=2005)
    rec = build_f2_record("1", "PMC1", c, r)
    assert set(rec) == set(_F2_RECORD_KEYS)
    assert json.loads(json.dumps(rec, ensure_ascii=False)) == rec


def test_scoreable_record_carries_empty_unscoreable_reason():
    c = ClaimedRef(title="A title", authors=["Lee"], year=2020, journal="J Foo")
    r = RetrievedRecord(resolved=True, title="A title", authors=["Lee"],
                        year=2020, journal="J Foo")
    rec = build_f2_record("1", "PMC1", c, r)
    assert rec["unscoreable_reason"] == ""
    assert rec["verdict"] == VERDICT_MATCH


def test_verdict_unscoreable_matches_schema_constant():
    # two label spaces, one string on purpose: the verdict band value equals the
    # pipeline-state/taxonomy-drop value.
    assert VERDICT_UNSCOREABLE == SCHEMA_UNSCOREABLE == "unscoreable"


def test_book_container_resolved_bands_unscoreable():
    # resolved-side signal: a chapter cite resolving to its parent book.
    c = ClaimedRef(title="A chapter title", authors=["Ed"], year=2010)
    r = RetrievedRecord(resolved=True, title="Big Reference Textbook",
                        authors=["Ed"], year=2010, is_container=True)
    rec = build_f2_record("2", "PMC2", c, r)
    assert rec["verdict"] == VERDICT_UNSCOREABLE
    assert rec["unscoreable_reason"] == "resolved_book_container"


def test_unscoreable_excluded_from_high_band_rate_both_sides():   # spec test 1 (metric)
    def rec(v):
        return {"verdict": v}
    records = ([rec(VERDICT_UNSCOREABLE)] * 303
               + [rec(VERDICT_WRONG_PAPER)] * 28
               + [rec(VERDICT_MATCH)] * 5
               + [rec(VERDICT_SAME_WORK_VARIANT)] * 3)
    out = high_band_rate_of_scoreable(records)
    assert out["flagged_f2_high"] == 28
    assert out["unscoreable_excluded"] == 303
    assert out["same_work_variant_excluded"] == 3
    # denominator = WRONG_PAPER + MATCH = 28 + 5 = 33 (UNSCOREABLE + SAME_WORK out)
    assert out["denominator_scoreable"] == 33
    assert out["high_band_rate_of_scoreable"] == round(28 / 33, 4) or \
        abs(out["high_band_rate_of_scoreable"] - 28 / 33) < 1e-9


def test_mixed_citation_title_in_raw_bands_unscoreable(tmp_path):  # spec test 2
    # 28146066 shape: free-text <mixed-citation>, no <article-title>. raw carries
    # the author-title-source run; structured title is empty -> UNSCOREABLE.
    doc = (b'<article><back><ref-list><ref id="r1"><mixed-citation>'
           b'Norris EJ, Coats JR. Current and future repellent technologies. '
           b'<source>Int J Environ Res Public Health</source>. '
           b'<year>2017</year>.'
           b'<pub-id pub-id-type="pmid">28146066</pub-id>'
           b'</mixed-citation></ref></ref-list></back></article>')
    p = tmp_path / "PMC28146066.xml"
    p.write_bytes(doc)
    refs = parse_pmc_xml(str(p), source_pmcid="PMC28146066")
    assert refs and refs[0].claimed.title == ""          # no structured title
    assert refs[0].claimed.raw                            # but raw is populated
    r = RetrievedRecord(resolved=True, title="Some unrelated resolved title",
                        authors=["Zzz"], year=2005)
    rec = build_f2_record("28146066", "PMC28146066", refs[0].claimed, r)
    assert rec["verdict"] == VERDICT_UNSCOREABLE
    assert rec["unscoreable_reason"] == "no_claimed_title"


# ======================================================================
# Bug 2 -- Unicode dash folding in the shared normalizer
# ======================================================================
def test_unicode_hyphen_author_normalizes_equal():               # spec test 3
    for name in ("Topka" + U2010 + "Bielecka", "Matías" + U2010 + "Guiu",
                 "Rouas" + U2010 + "Freiss"):
        ascii_name = name.replace(U2010, "-")
        assert _norm(name) == _norm(ascii_name), name


def test_unicode_hyphen_author_match_true():                     # spec test 3
    c = ClaimedRef(title="T", authors=["Topka" + U2010 + "Bielecka"])
    r = RetrievedRecord(resolved=True, title="T", authors=["Topka-Bielecka"])
    assert field_agreement(c, r).author_match is True


def test_title_dash_and_case_only_hits_same_work_threshold():    # spec test 4
    # Title differs ONLY by a U+2010 dash and case -> title_sim >= 0.95.
    claimed_title = "Metals" + U2010 + "Toxicity and Oxidative Stress in Disease"
    resolved_title = "metals-toxicity and oxidative stress in disease"
    assert title_sim(claimed_title, resolved_title) >= SAME_WORK_TITLE_SIM_MIN
    # with a REAL author disagreement, this reaches the SAME_WORK_VARIANT gate.
    v, m = flag_verdict(
        ClaimedRef(title=claimed_title, authors=["Alpha"], year=2005),
        RetrievedRecord(resolved=True, title=resolved_title, authors=["Beta"],
                        year=2015))
    assert m.title_sim >= SAME_WORK_TITLE_SIM_MIN
    assert v == VERDICT_SAME_WORK_VARIANT


def test_en_dash_title_folds_to_match():
    # en dash (U+2013) + em dash (U+2014) fold identically to ASCII '-'.
    assert title_sim("Cost–benefit analysis of care",
                     "Cost-benefit analysis of care") == 1.0
    assert title_sim("Follow—up study of outcomes",
                     "Follow-up study of outcomes") == 1.0


def test_lookup_normalize_dash_is_consistent_noop():
    # lookup._normalize folds dashes too (kept in step with biblio_match); there
    # a dash becomes a space either way, so both spellings still normalize equal.
    a = "Topka" + U2010 + "Bielecka"
    assert _normalize(a) == _normalize("Topka-Bielecka")


# ======================================================================
# Regression guards -- genuine wrong-papers stay HIGH after BOTH fixes
# ======================================================================
_GUARDS = [
    # (claimed_title, resolved_title, claimed_author, resolved_author, cy, ry)
    ("Disseminated varicella infection", "Purple Urine after Catheterization",
     "Pannu", "Sabanis", 2019, 2019),                                  # 31665581
    ("Evolution in closely adjacent plant populations VIII: clinal patterns of "
     "heavy metal tolerance at a mine boundary",
     "Evolution in closely adjacent plant populations X: long-term persistence "
     "of prereproductive isolation", "Antonovics", "Antonovics", 1971, 1990),  # 16639420
    ("The heat of shortening and dynamic constants of muscle",
     "The heat of activation and heat of shortening in a twitch",
     "Hill", "Other", 1938, 1949),                                     # 18152150
]


@pytest.mark.parametrize("ct,rt,ca,ra,cy,ry", _GUARDS)
def test_regression_guards_stay_wrong_paper(ct, rt, ca, ra, cy, ry):  # spec test 5
    m = match_score(ClaimedRef(title=ct, authors=[ca], year=cy),
                    RetrievedRecord(resolved=True, title=rt, authors=[ra], year=ry))
    assert m.title_sim < SAME_WORK_TITLE_SIM_MIN, "dash fold must not inflate a guard"
    v, _ = flag_verdict(ClaimedRef(title=ct, authors=[ca], year=cy),
                        RetrievedRecord(resolved=True, title=rt, authors=[ra], year=ry))
    assert v == VERDICT_WRONG_PAPER


# ======================================================================
# reband_from_cache -- offline re-band, no re-fetch
# ======================================================================
def _write_xml(dirpath, pmcid, refs):
    """refs: list of (ref_id, title, author, year, pmid). title '' -> no
    <article-title> element (free-text/empty-title shape)."""
    body = []
    for rid, title, author, year, pmid in refs:
        title_el = f"<article-title>{title}</article-title>" if title else ""
        body.append(
            f'<ref id="{rid}"><element-citation>'
            f'<person-group person-group-type="author"><name><surname>{author}'
            f'</surname></name></person-group>{title_el}<source>J</source>'
            f'<year>{year}</year><pub-id pub-id-type="pmid">{pmid}</pub-id>'
            f'</element-citation></ref>')
    xml = ('<article><back><ref-list>' + "".join(body)
           + '</ref-list></back></article>')
    path = os.path.join(dirpath, f"{pmcid}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(xml)


def _write_cache(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def test_reband_from_cache_joins_and_applies_both_fixes(tmp_path):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    out_dir = tmp_path / "out"
    # PMC0001: 111 wrong-paper, 222 empty-title -> UNSCOREABLE.
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111"),
        ("r2", "", "Norris", 2019, "222"),
    ])
    # PMC0002: 333 diacritic same-work (author + title spelled with U+2010).
    _write_xml(str(xml_dir), "PMC0002", [
        ("r1", "Gene" + U2010 + "Expression Analysis", "Topka" + U2010 + "Bielecka",
         2018, "333"),
    ])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [
        {"src_pmcid": "PMC0001", "pmid": "111", "resolved": True,
         "title": "Purple Urine after Catheterization", "authors": ["Sabanis"],
         "year": 2019},
        {"src_pmcid": "PMC0001", "pmid": "222", "resolved": True,
         "title": "A real resolved paper", "authors": ["Smith"], "year": 2005},
        {"src_pmcid": "PMC0002", "pmid": "333", "resolved": True,
         "title": "gene-expression analysis", "authors": ["Topka-Bielecka"],
         "year": 2018},
    ])
    from cre.f1.f2_run_v3 import reband_from_cache
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(out_dir),
                                version="v3_1")
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    assert recs["111"]["verdict"] == VERDICT_WRONG_PAPER
    assert recs["222"]["verdict"] == VERDICT_UNSCOREABLE      # Bug 1 through reband
    assert recs["333"]["verdict"] == VERDICT_MATCH            # Bug 2 through reband
    assert recs["333"]["title_sim"] >= SAME_WORK_TITLE_SIM_MIN
    assert recs["333"]["author_match"] is True
    # metric: 1 HIGH, denominator excludes the UNSCOREABLE row.
    assert summary["flagged_f2_high"] == 1
    assert summary["unscoreable_excluded"] == 1
    assert summary["denominator_scoreable"] == 2
    assert summary["n_joined"] == 3
    assert summary["rebanded_from_cache"] is True
    # writes v3_1, not v3.
    assert os.path.exists(out_dir / "f2_random_oa_seed7_v3_1.jsonl")


def test_reband_refuses_preserved_versions(tmp_path):
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    cache = tmp_path / "resolved.jsonl"; cache.write_text("")
    for frozen in ("v2", "v3", "V3"):
        with pytest.raises(RuntimeError):
            reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                              version=frozen)


def test_reband_preserves_existing_v3(tmp_path):
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC1", [("r1", "A title", "Lee", 2020, "1")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC1", "pmid": "1", "resolved": True,
                               "title": "A title", "authors": ["Lee"], "year": 2020}])
    v3 = tmp_path / "f2_random_oa_seed7_v3.jsonl"
    v3.write_text('{"pmid": "old_v3"}\n')
    reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path), version="v3_1")
    assert v3.read_text() == '{"pmid": "old_v3"}\n'         # v3 untouched
    assert (tmp_path / "f2_random_oa_seed7_v3_1.jsonl").exists()


def test_reband_pmid_only_join_when_src_pmcid_absent(tmp_path):
    # cache line lacks src_pmcid; PMID is unique across the frame -> safe join.
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "111", "resolved": True,
                               "title": "Purple Urine after Catheterization",
                               "authors": ["Sabanis"], "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1")
    assert summary["n_joined"] == 1
    assert summary["n_pmid_only_join"] == 1
    assert summary["n_ambiguous_dropped"] == 0


def test_reband_drops_ambiguous_pmid_only_join(tmp_path):
    # same PMID cited by TWO source papers, cache line has no src_pmcid ->
    # ambiguous, dropped and counted (never silently mis-joined).
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [("r1", "T one", "A", 2019, "999")])
    _write_xml(str(xml_dir), "PMC0002", [("r1", "T two", "B", 2019, "999")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "999", "resolved": True,
                               "title": "Resolved", "authors": ["Z"], "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1")
    assert summary["n_joined"] == 0
    assert summary["n_ambiguous_dropped"] == 1
    assert summary["n_records"] == 0


def test_reband_counts_unmatched_cache_line(tmp_path):
    # cache PMID has no claimed ref in the XML frame -> unmatched, dropped.
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [("r1", "T", "A", 2019, "111")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC0001", "pmid": "does-not-exist",
                               "resolved": True, "title": "R", "authors": ["Z"],
                               "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1")
    assert summary["n_unmatched_dropped"] == 1
    assert summary["n_joined"] == 0


def test_reband_present_but_unmatched_src_pmcid_never_misjoins(tmp_path):
    # REGRESSION (adversarial review): a cache line that CARRIES a src_pmcid whose
    # exact (src_pmcid, pmid) key misses must be UNMATCHED -- never silently
    # re-joined to a different source paper via the PMID-only fallback, even when
    # the PMID is unique across the frame. Guarantees 'never mis-joined'.
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    # PMID 999 is cited ONLY by PMC_B (unique across the frame).
    _write_xml(str(xml_dir), "PMC_B", [("r1", "Paper as cited by B", "Bauthor",
                                        2019, "999")])
    cache = tmp_path / "resolved.jsonl"
    # ...but the cache line declares src_pmcid PMC_X (stale / not in this XML dir).
    _write_cache(str(cache), [{"src_pmcid": "PMC_X", "pmid": "999",
                               "resolved": True, "title": "Resolved paper",
                               "authors": ["Zauthor"], "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1")
    assert summary["n_joined"] == 0                # NOT re-joined to PMC_B
    assert summary["n_pmid_only_join"] == 0
    assert summary["n_unmatched_dropped"] == 1
    assert summary["n_records"] == 0
    # nothing banded against PMC_B's claimed title
    lines = [l for l in open(summary["records_path"])]
    assert lines == []


def test_reband_present_but_unmatched_src_pmcid_not_counted_ambiguous(tmp_path):
    # Related miscount variant: present-but-unmatched src_pmcid whose PMID is cited
    # by TWO other papers must be n_unmatched (definitely-sourced line), NOT
    # n_ambiguous (the ambiguity only matters for the no-src_pmcid fallback).
    from cre.f1.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC_B", [("r1", "T one", "A", 2019, "999")])
    _write_xml(str(xml_dir), "PMC_C", [("r1", "T two", "B", 2019, "999")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC_X", "pmid": "999",
                               "resolved": True, "title": "R", "authors": ["Z"],
                               "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1")
    assert summary["n_unmatched_dropped"] == 1
    assert summary["n_ambiguous_dropped"] == 0
    assert summary["n_joined"] == 0


def test_reband_retrieved_reconstruction_ignores_envelope_keys(tmp_path):
    # a resolved cache line with extra envelope keys must reconstruct cleanly.
    from cre.f1.f2_run_v3 import _retrieved_from_cache
    rec = _retrieved_from_cache({"src_pmcid": "PMCx", "pmid": "5", "resolved": True,
                                 "title": "R", "authors": ["Z"], "year": 2001,
                                 "some_unknown_future_key": 42})
    assert rec.title == "R" and rec.pmid == "5" and rec.resolved is True
