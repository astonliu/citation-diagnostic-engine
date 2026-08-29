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

F2_V3_2 (resolved-side gate): a row whose resolved record came back UNRESOLVED
      (RetrievedRecord.resolved is False) has no resolved work to mismatch against,
      so build_f2_record routes it to VERDICT_UNRESOLVED (reason
      "resolved_unresolved") instead of banding it review_wrong_paper, and
      high_band_rate_of_scoreable drops it from BOTH flagged_f2_high and
      denominator_scoreable (counted as resolved_unresolved_excluded). Tri-state:
      only explicit ``resolved is False`` -- resolved=None (unknown) is NOT swept in.

F2_V3_3 (SAME_WORK threshold): SAME_WORK_TITLE_SIM_MIN lowered 0.95 -> 0.92 so the
      two confirmed seam variants (12199786 / 9802808, title_sim ~0.944-0.947) band
      review_same_work_variant instead of review_wrong_paper. The gate expression is
      unchanged; reband_from_cache emits same_work_newly_quarantined (the PMIDs that
      crossed the gate) so no row moves silently. The 7 regression guards all sit at
      title_sim < 0.92 and stay review_wrong_paper.

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f2_v3_1.py -q
"""
from __future__ import annotations

import json
import os

import pytest
from lxml import etree

from cde.refs.biblio_match import (normalize_title, title_sim, field_agreement,
                                 match_score, flag_verdict, _norm,
                                 SAME_WORK_TITLE_SIM_MIN, VERDICT_MATCH,
                                 VERDICT_WRONG_PAPER, VERDICT_FORMATTING,
                                 VERDICT_SAME_WORK_VARIANT, VERDICT_UNSCOREABLE,
                                 VERDICT_UNRESOLVED)
from cde.refs.eval_report import (build_f2_record, high_band_rate_of_scoreable,
                                _F2_RECORD_KEYS)
from cde.refs.lookup import _normalize
from cde.refs.parser import parse_pmc_xml
from cde.refs.schema import ClaimedRef, RetrievedRecord, UNSCOREABLE as SCHEMA_UNSCOREABLE

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
    from cde.refs.f2_run_v3 import reband_from_cache
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
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    cache = tmp_path / "resolved.jsonl"; cache.write_text("")
    for frozen in ("v2", "v3", "V3"):
        with pytest.raises(RuntimeError):
            reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                              version=frozen)


def test_reband_preserves_existing_v3(tmp_path):
    from cde.refs.f2_run_v3 import reband_from_cache
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
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "111", "resolved": True,
                               "title": "Purple Urine after Catheterization",
                               "authors": ["Sabanis"], "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1", src_pmcids={"PMC0001"})
    assert summary["n_joined"] == 1
    assert summary["n_pmid_only_join"] == 1
    assert "n_ambiguous_dropped" not in summary


def test_reband_fans_pmid_cache_row_to_every_source_occurrence(tmp_path):
    # The cache record is the official work for PMID 999, so it is the correct
    # comparator for every citation occurrence even across source papers.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [("r1", "T one", "A", 2019, "999")])
    _write_xml(str(xml_dir), "PMC0002", [("r1", "T two", "B", 2019, "999")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "999", "resolved": True,
                               "title": "Resolved", "authors": ["Z"], "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1",
                                src_pmcids={"PMC0001", "PMC0002"})
    assert summary["n_joined"] == 2
    assert summary["n_cache_rows_joined"] == 1
    assert summary["n_occurrence_fanout"] == 1
    assert "n_ambiguous_dropped" not in summary
    assert summary["n_records"] == 2
    rows = [json.loads(line) for line in open(summary["records_path"])]
    assert {row["citation_id"] for row in rows} == {"PMC0001:r1", "PMC0002:r1"}


def test_unsourced_cache_requires_explicit_source_frame(tmp_path):
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [("r1", "A title", "A", 2019, "999")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "999", "resolved": True,
                               "title": "A title", "authors": ["A"],
                               "year": 2019}])
    with pytest.raises(RuntimeError, match="requires src_pmcids"):
        reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                          version="v3_1")


def test_reband_preserves_two_same_source_citations_of_one_pmid(tmp_path):
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "The correctly cited work", "Lee", 2020, "999"),
        ("r2", "An unrelated written paper", "Jones", 2010, "999"),
    ])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "999", "resolved": True,
                               "title": "The correctly cited work",
                               "authors": ["Lee"], "year": 2020}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1", src_pmcids={"PMC0001"})
    rows = [json.loads(line) for line in open(summary["records_path"])]
    assert [(row["citation_id"], row["verdict"]) for row in rows] == [
        ("PMC0001:r1", VERDICT_MATCH),
        ("PMC0001:r2", VERDICT_WRONG_PAPER),
    ]
    assert summary["n_joined"] == 2
    assert summary["n_cache_rows_joined"] == 1
    assert summary["n_occurrence_fanout"] == 1


def test_fresh_runner_assigns_unique_occurrence_ids_for_repeated_pmid(tmp_path):
    from cde.refs.f2_run_v3 import run_f2_seed7_v3
    resolved = RetrievedRecord(resolved=True, title="The resolved paper",
                               authors=["Lee"], year=2020)
    items = [
        ("999", "PMC1", ClaimedRef(title="The resolved paper",
                                    authors=["Lee"], year=2020), resolved),
        ("999", "PMC1", ClaimedRef(title="A different claimed paper",
                                    authors=["Jones"], year=2010), resolved),
    ]
    summary = run_f2_seed7_v3(items, out_dir=str(tmp_path), version="v4")
    rows = [json.loads(line) for line in open(summary["records_path"])]
    assert [row["citation_id"] for row in rows] == [
        "PMC1:f2occ1", "PMC1:f2occ2"]
    assert len({row["citation_id"] for row in rows}) == 2


def test_reband_counts_unmatched_cache_line(tmp_path):
    # cache PMID has no claimed ref in the XML frame -> unmatched, dropped.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [("r1", "T", "A", 2019, "111")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC0001", "pmid": "does-not-exist",
                               "resolved": True, "title": "R", "authors": ["Z"],
                               "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1", refuse_empty=False)
    assert summary["n_unmatched_dropped"] == 1
    assert summary["n_joined"] == 0


def test_reband_present_but_unmatched_src_pmcid_never_misjoins(tmp_path):
    # REGRESSION (adversarial review): a cache line that CARRIES a src_pmcid whose
    # exact (src_pmcid, pmid) key misses must be UNMATCHED -- never silently
    # re-joined to a different source paper via the PMID-only fallback, even when
    # the PMID is unique across the frame. Guarantees 'never mis-joined'.
    from cde.refs.f2_run_v3 import reband_from_cache
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
                                version="v3_1", refuse_empty=False)
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
    # an obsolete always-zero ambiguity bucket.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC_B", [("r1", "T one", "A", 2019, "999")])
    _write_xml(str(xml_dir), "PMC_C", [("r1", "T two", "B", 2019, "999")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC_X", "pmid": "999",
                               "resolved": True, "title": "R", "authors": ["Z"],
                               "year": 2019}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1", refuse_empty=False)
    assert summary["n_unmatched_dropped"] == 1
    assert "n_ambiguous_dropped" not in summary
    assert summary["n_joined"] == 0


def test_reband_retrieved_reconstruction_ignores_envelope_keys(tmp_path):
    # a FLAT (un-enveloped) cache line with extra envelope keys must reconstruct
    # cleanly (top-level fallback when there is no nested "rec").
    from cde.refs.f2_run_v3 import _retrieved_from_cache
    rec = _retrieved_from_cache({"src_pmcid": "PMCx", "pmid": "5", "resolved": True,
                                 "title": "R", "authors": ["Z"], "year": 2001,
                                 "some_unknown_future_key": 42})
    assert rec.title == "R" and rec.pmid == "5" and rec.resolved is True


def test_retrieved_from_cache_reads_nested_rec():
    # the real cache envelope: {"pmid": ..., "rec": {RetrievedRecord fields}}.
    # Reconstruction must DESCEND into "rec", not read the top level.
    from cde.refs.f2_run_v3 import _retrieved_from_cache
    line = {"pmid": "111", "rec": {
        "resolved": True, "title": "Purple Urine after Catheterization",
        "authors": ["Sabanis"], "year": 2019, "journal": "N Engl J Med",
        "doi": "10.x/y", "volume": "12", "pages": "1-9",
        "is_container": False, "year_from_dep": False, "pmid": "111"}}
    r = _retrieved_from_cache(line)
    assert r.resolved is True
    assert r.title == "Purple Urine after Catheterization"
    assert r.authors == ["Sabanis"]
    assert r.year == 2019
    assert r.journal == "N Engl J Med"
    assert r.pmid == "111"
    assert r.volume == "12" and r.pages == "1-9"


def test_resolved_cache_dedupes_identical_rows_and_rejects_conflicts(tmp_path):
    from cde.refs.f2_run_v3 import load_resolved_cache
    duplicate = {"pmid": "999", "resolved": True, "title": "Same work",
                 "authors": ["Lee"], "year": 2020}
    path = tmp_path / "dupes.jsonl"
    _write_cache(str(path), [duplicate, duplicate])
    assert len(load_resolved_cache(str(path))) == 1

    conflict = dict(duplicate, title="Conflicting work")
    path2 = tmp_path / "conflict.jsonl"
    _write_cache(str(path2), [duplicate, conflict])
    with pytest.raises(RuntimeError, match="Conflicting resolved-cache rows"):
        load_resolved_cache(str(path2))


def test_resolved_cache_rejects_envelope_nested_pmid_mismatch(tmp_path):
    from cde.refs.f2_run_v3 import load_resolved_cache
    path = tmp_path / "wrong-id.jsonl"
    _write_cache(str(path), [{
        "pmid": "111",
        "rec": {"pmid": "222", "resolved": True, "title": "Work 222"},
    }])
    with pytest.raises(RuntimeError, match="PMID mismatch"):
        load_resolved_cache(str(path))


def test_mixed_sourced_and_unsourced_cache_rows_do_not_duplicate_occurrence(
        tmp_path):
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC1", [
        ("r1", "The correctly cited work", "Lee", 2020, "999")])
    resolved = {"pmid": "999", "resolved": True,
                "title": "The correctly cited work", "authors": ["Lee"],
                "year": 2020}
    cache = tmp_path / "mixed.jsonl"
    _write_cache(str(cache), [resolved, {"src_pmcid": "PMC1", **resolved}])
    summary = reband_from_cache(
        str(xml_dir), str(cache), out_dir=str(tmp_path), version="v3_1",
        src_pmcids={"PMC1"})
    rows = [json.loads(line) for line in open(summary["records_path"])]
    assert [row["citation_id"] for row in rows] == ["PMC1:r1"]
    assert summary["n_records"] == summary["n_joined"] == 1
    assert summary["n_occurrence_duplicates_deduped"] == 1


def test_reband_with_nested_rec_envelope(tmp_path):
    # full reband path against the REAL nested-"rec" cache format.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"src_pmcid": "PMC0001", "pmid": "111", "rec": {
        "resolved": True, "title": "Purple Urine after Catheterization",
        "authors": ["Sabanis"], "year": 2019}}])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_1")
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    assert summary["n_joined"] == 1
    assert recs["111"]["resolved_title"] == "Purple Urine after Catheterization"
    assert recs["111"]["verdict"] == VERDICT_WRONG_PAPER
    assert recs["111"]["resolved_first_author"] == "Sabanis"


def test_reband_aborts_when_resolved_titles_mostly_empty(tmp_path):
    # Pre-write guard: cache lines that carry NO RetrievedRecord fields (neither
    # nested "rec" nor top-level) reconstruct to resolved=False + empty title, so
    # every scoreable row lands wrong-paper with an empty resolved_title. The guard
    # must ABORT before writing -- this is exactly the wrong-level-read failure.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC1", [
        ("r1", "Claimed title one", "A", 2019, "111"),
        ("r2", "Claimed title two", "B", 2019, "222")])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [{"pmid": "111"}, {"pmid": "222"}])   # no rec, no fields
    with pytest.raises(RuntimeError, match="resolved_title"):
        reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                          version="v3_1", src_pmcids={"PMC1"})
    # nothing written -- the abort precedes the write.
    assert not (tmp_path / "f2_random_oa_seed7_v3_1.jsonl").exists()


# ======================================================================
# F2_V3_2 -- resolved-side (unresolved) gate
# ======================================================================
def test_reband_unresolved_row_bands_unresolved_not_wrong_paper(tmp_path):
    # A cache line whose resolved record came back UNRESOLVED (rec.resolved=False,
    # empty title) has no resolved work to mismatch against -> it MUST NOT band
    # review_wrong_paper. It routes to the distinct, recoverable `unresolved`
    # bucket (reason "resolved_unresolved"), scores stay None (never fabricated to
    # 0.0), and it leaves BOTH the HIGH count and the scoreable denominator. The
    # genuine resolved=True wrong-paper row on the same source is unaffected.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    # PMC0001: 111 real wrong-paper (resolved=True); 222 + 444 unresolved
    # (resolved=False); the two resolved=True rows keep the guard well under 50%.
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Disseminated varicella infection", "Pannu", 2019, "111"),
        ("r2", "A perfectly valid claimed title", "Norris", 2019, "222"),
        ("r3", "A shared correct title", "Lee", 2020, "333"),
        ("r4", "Another valid claimed title", "Ochoa", 2015, "444"),
    ])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [
        {"src_pmcid": "PMC0001", "pmid": "111", "rec": {
            "resolved": True, "title": "Purple Urine after Catheterization",
            "authors": ["Sabanis"], "year": 2019}},
        # UNRESOLVED: the claimed PMID did not resolve (resolved False, empty title).
        {"src_pmcid": "PMC0001", "pmid": "222", "rec": {
            "resolved": False, "title": "", "authors": [], "year": None}},
        {"src_pmcid": "PMC0001", "pmid": "333", "rec": {
            "resolved": True, "title": "A shared correct title",
            "authors": ["Lee"], "year": 2020}},
        {"src_pmcid": "PMC0001", "pmid": "444", "rec": {
            "resolved": False, "title": "", "authors": [], "year": None}},
    ])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_2")
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    # the unresolved rows: distinct bucket, NOT wrong-paper, scores NOT fabricated
    for pmid in ("222", "444"):
        assert recs[pmid]["verdict"] == VERDICT_UNRESOLVED
        assert recs[pmid]["verdict"] != VERDICT_WRONG_PAPER
        assert recs[pmid]["unscoreable_reason"] == "resolved_unresolved"
        assert recs[pmid]["title_sim"] is None
        assert recs[pmid]["author_match"] is None
        assert recs[pmid]["match_score"] is None
        assert recs[pmid]["flag"] is None
        # the unresolved record keeps the canonical, JSON-round-trippable schema
        # (these dicts came back through json.dumps -> file -> json.loads).
        assert set(recs[pmid]) == set(_F2_RECORD_KEYS)
    # the genuine wrong-paper row is UNAFFECTED (resolved=True, real mismatch)
    assert recs["111"]["verdict"] == VERDICT_WRONG_PAPER
    assert recs["333"]["verdict"] == VERDICT_MATCH
    # metric: HIGH = only 111; the two unresolved rows leave BOTH sides.
    assert summary["flagged_f2_high"] == 1
    assert summary["resolved_unresolved_excluded"] == 2
    # denominator = the two scoreable (resolved=True) rows only: 111 + 333.
    assert summary["denominator_scoreable"] == 2
    assert summary["unscoreable_excluded"] == 0
    assert os.path.exists(tmp_path / "f2_random_oa_seed7_v3_2.jsonl")


def test_resolved_none_not_swept_into_unresolved_gate():
    # TRI-STATE GUARD: resolved=None (unknown -- e.g. not-yet-looked-up) must NOT
    # be caught by the resolved-side gate, which keys on `is False` ONLY. Such a
    # row proceeds to NORMAL scoring; here it matches, so it stays a scoreable
    # MATCH -- never diverted to the `unresolved` bucket.
    c = ClaimedRef(title="A shared title", authors=["Lee"], year=2020,
                   journal="J Foo")
    r = RetrievedRecord(resolved=None, title="A shared title", authors=["Lee"],
                        year=2020, journal="J Foo")
    rec = build_f2_record("1", "PMC1", c, r)
    assert rec["verdict"] != VERDICT_UNRESOLVED
    assert rec["unscoreable_reason"] != "resolved_unresolved"
    # scored normally (gate did NOT fire): scores are populated, not None
    assert rec["title_sim"] is not None
    assert rec["match_score"] is not None
    assert rec["verdict"] == VERDICT_MATCH
    # and the metric keeps it in the scoreable frame (not an unresolved exclusion)
    out = high_band_rate_of_scoreable([rec])
    assert out["resolved_unresolved_excluded"] == 0
    assert out["denominator_scoreable"] == 1


# ======================================================================
# F2_V3_3 -- SAME_WORK_TITLE_SIM_MIN lowered 0.95 -> 0.92
# ======================================================================
def test_same_work_threshold_constant_is_092():
    # single-constant change (v3.3). Pin the value so the gate can't drift silently.
    assert SAME_WORK_TITLE_SIM_MIN == 0.92


# The two confirmed seam rows. The REAL titles live in the Colab resolved cache
# (unreachable here); these reconstruct the MECHANISM -- title_sim in [0.92, 0.95)
# with a confident field disagreement -- exactly as test_f2_recall_guard.py uses
# constructed inputs for the guard PMIDs (spec C6). What is pinned is the
# threshold-dependent band flip, not the exact real title strings.
_SEAM_ROWS = [
    # 12199786: title near-identical, YEAR typo (2022 vs 2002); authors agree so
    # year is the sole confident disagreement.
    ("12199786",
     "Molecular mechanisms of insulin resistance in type 2 diabetes",
     "Molecular mechanism of insulin resistance in type 2 diabetes",
     "Tanaka", "Tanaka", 2022, 2002),
    # 9802808: title differs only by a dropped word ("injury"); an AUTHOR formatting
    # artifact reads as a confident disagreement; years agree.
    ("9802808",
     "Spinal cord injury and functional recovery in the adult rat model",
     "Spinal cord and functional recovery in the adult rat model",
     "Nakamura", "Yamamoto", 2010, 2010),
]


@pytest.mark.parametrize("pmid,ct,rt,ca,ra,cy,ry", _SEAM_ROWS)
def test_seam_rows_same_work_variant_at_092_wrong_paper_at_095(
        pmid, ct, rt, ca, ra, cy, ry, monkeypatch):     # DoD test (pin to constant)
    # The band is pinned to the CONSTANT: at 0.92 (v3.3) the seam row quarantines as
    # review_same_work_variant; at 0.95 (v3.2) the SAME row banded review_wrong_paper
    # (false HIGH). title_sim sits in [0.92, 0.95) -- normalization already works;
    # only the threshold moved.
    import cde.refs.biblio_match as bm
    claimed = ClaimedRef(title=ct, authors=[ca], year=cy)
    resolved = RetrievedRecord(resolved=True, title=rt, authors=[ra], year=ry)
    m = match_score(claimed, resolved)
    assert SAME_WORK_TITLE_SIM_MIN <= m.title_sim < 0.95, (
        f"{pmid} title_sim {m.title_sim} must sit in the [0.92, 0.95) seam band")
    # confident disagreement present (is False, not None) -> reaches the gate
    assert (m.fields.author_match is False) or (m.fields.year_match is False)
    # new gate (module default 0.92): quarantined same-work variant
    v092, _ = flag_verdict(claimed, resolved)
    assert v092 == VERDICT_SAME_WORK_VARIANT, pmid
    # old gate (0.95): the very same row was HIGH wrong-paper
    monkeypatch.setattr(bm, "SAME_WORK_TITLE_SIM_MIN", 0.95)
    v095, _ = flag_verdict(claimed, resolved)
    assert v095 == VERDICT_WRONG_PAPER, pmid


def test_title_sim_just_below_092_stays_wrong_paper(monkeypatch):   # DoD test
    # The gate is 0.92, NOT lower: a row with title_sim just under 0.92 and a
    # confident disagreement stays review_wrong_paper. Dropping the gate to 0.90
    # would (wrongly) quarantine it -- proving the boundary is exactly 0.92.
    import cde.refs.biblio_match as bm
    claimed = ClaimedRef(
        title="Regulation of glucose metabolism by circadian clock proteins",
        authors=["Alpha"], year=2015)
    resolved = RetrievedRecord(
        resolved=True,
        title="Regulation of glucose transport by circadian clock proteins",
        authors=["Beta"], year=2015)
    m = match_score(claimed, resolved)
    assert 0.90 <= m.title_sim < SAME_WORK_TITLE_SIM_MIN, (
        f"fixture title_sim {m.title_sim} must sit just below the 0.92 gate")
    v, _ = flag_verdict(claimed, resolved)
    assert v == VERDICT_WRONG_PAPER
    # gate really is 0.92: at 0.90 the same row would quarantine (boundary proof)
    monkeypatch.setattr(bm, "SAME_WORK_TITLE_SIM_MIN", 0.90)
    v90, _ = flag_verdict(claimed, resolved)
    assert v90 == VERDICT_SAME_WORK_VARIANT


def test_reband_surfaces_newly_quarantined_seam_row(tmp_path):     # audit visibility
    # A seam row (title_sim in [0.92, 0.95)) that bands review_same_work_variant at
    # 0.92 is enumerated in same_work_newly_quarantined so the 0.95 -> 0.92 move can
    # be audited row-by-row; a genuine wrong-paper (title_sim < 0.92) is NOT listed.
    from cde.refs.f2_run_v3 import reband_from_cache
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    _write_xml(str(xml_dir), "PMC0001", [
        ("r1", "Spinal cord injury and functional recovery in the adult rat model",
         "Nakamura", 2010, "9802808"),
        ("r2", "Disseminated varicella infection", "Pannu", 2019, "111"),
    ])
    cache = tmp_path / "resolved.jsonl"
    _write_cache(str(cache), [
        {"src_pmcid": "PMC0001", "pmid": "9802808", "rec": {
            "resolved": True,
            "title": "Spinal cord and functional recovery in the adult rat model",
            "authors": ["Yamamoto"], "year": 2010}},
        {"src_pmcid": "PMC0001", "pmid": "111", "rec": {
            "resolved": True, "title": "Purple Urine after Catheterization",
            "authors": ["Sabanis"], "year": 2019}},
    ])
    summary = reband_from_cache(str(xml_dir), str(cache), out_dir=str(tmp_path),
                                version="v3_3")
    recs = {json.loads(l)["pmid"]: json.loads(l)
            for l in open(summary["records_path"])}
    assert recs["9802808"]["verdict"] == VERDICT_SAME_WORK_VARIANT
    assert 0.92 <= recs["9802808"]["title_sim"] < 0.95
    assert recs["111"]["verdict"] == VERDICT_WRONG_PAPER
    # the newly-quarantined seam row is surfaced; the genuine wrong-paper is not.
    assert summary["same_work_newly_quarantined"] == ["9802808"]
    assert summary["n_same_work_newly_quarantined"] == 1
    assert "111" not in summary["same_work_newly_quarantined"]
