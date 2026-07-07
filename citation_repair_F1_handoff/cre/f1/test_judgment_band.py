"""Offline tests for the F3-F7 judgment-band front-end (coverage gate).

Fully offline: the LLM seams (extractor, coverage_judge) and the NCBI seams
(fetch_abstract, fetch_reflist) are injected as plain callables, and the
cited-work review check (``ncbi_pubtypes``) is monkeypatched ON THE
judgment_band MODULE namespace (run_band calls it as a module global, so the
patch must target that namespace).

Covers every row of the acceptance matrix in the build spec:
  * route: FULL_COVERAGE / F6_FLAGGED / HELD_LOW_CONFIDENCE
  * build_item exclusions (no citance / no cited PMID) + their counts
  * annotation payload: carries atomic_claims + evidence, EXCLUDES the proposed
    verdict; worksheet all null
  * review-cited evidence carries the reflist (F3-V3 pool)
  * tri-state ``established`` decided with ``is False`` (None is unknown)
  * pinned prompt versions on every record and the manifest

Run: PYTHONPATH=<repo> python -m pytest cre/f1/test_judgment_band.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from cre.f1 import judgment_band as jb
from cre.f1.schema import Reference, ClaimedRef


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
# One citing paper, one review-cited specific-claim sentence -> R1 (PMID 111,
# patched to a Review). This is the F3-shaped item.
FRAME_XML = """<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">3000001</article-id>
    <title-group><article-title>A citing paper</article-title></title-group>
  </article-meta></front>
  <body><sec>
    <title>Introduction</title>
    <p>A meta-analysis showed that the therapy reduced mortality by thirty percent
       <xref ref-type="bibr" rid="R1">1</xref>.</p>
  </sec></body>
  <back><ref-list>
    <ref id="R1">
      <element-citation publication-type="journal">
        <person-group><name><surname>Smith</surname></name></person-group>
        <article-title>A systematic review</article-title>
        <source>Nat Rev</source><year>2015</year>
        <pub-id pub-id-type="pmid">111</pub-id>
      </element-citation>
    </ref>
  </ref-list></back>
</article>
"""

# Three refs: R1 builds (citance + PMID), R2 has a citance but NO PMID
# (excluded_no_cited_pmid), R3 has a PMID but is NOT cited in the body
# (excluded_no_citance).
EXCL_XML = """<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">3000002</article-id>
    <title-group><article-title>Exclusions</article-title></title-group>
  </article-meta></front>
  <body><sec>
    <p>Finding one <xref ref-type="bibr" rid="R1">1</xref>. Finding two
       <xref ref-type="bibr" rid="R2">2</xref>.</p>
  </sec></body>
  <back><ref-list>
    <ref id="R1">
      <element-citation publication-type="journal">
        <article-title>Has pmid</article-title><source>J</source><year>2011</year>
        <pub-id pub-id-type="pmid">111</pub-id>
      </element-citation>
    </ref>
    <ref id="R2">
      <element-citation publication-type="journal">
        <article-title>No pmid</article-title><source>J</source><year>2012</year>
      </element-citation>
    </ref>
    <ref id="R3">
      <element-citation publication-type="journal">
        <article-title>Not cited</article-title><source>J</source><year>2013</year>
        <pub-id pub-id-type="pmid">333</pub-id>
      </element-citation>
    </ref>
  </ref-list></back>
</article>
"""


def _write(dirpath, name, content):
    p = os.path.join(dirpath, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


def _read(out_dir, name):
    path = os.path.join(out_dir, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


@pytest.fixture
def frame_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir()
    _write(str(d), "PMC3000001.xml", FRAME_XML)
    return str(d)


@pytest.fixture
def excl_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir()
    _write(str(d), "PMC3000002.xml", EXCL_XML)
    return str(d)


@pytest.fixture
def patched_pubtypes(monkeypatch):
    """PMID 111 -> Review; everything else -> not a review. Patched on the
    judgment_band namespace (where run_band looks it up)."""
    def fake_pubtypes(pmid, api_key="", email="", session=None):
        return ["Review", "Journal Article"] if str(pmid) == "111" \
            else ["Journal Article"]
    monkeypatch.setattr(jb, "ncbi_pubtypes", fake_pubtypes)


class _StubSession:
    """run_band defaults to requests.Session(); pass a stub so no import/network
    happens even if a patch is missed."""


# Injected LLM/NCBI seams -------------------------------------------------
def _extractor(sentence):
    """One atomic claim == the whole sentence (deterministic, offline)."""
    return [sentence]


def _abstract(pmid):
    return f"Abstract for PMID {pmid}."


def _reflist(pmid):
    return ([{"title": "The original primary finding",
              "claimed_pmid": "555", "year": 2010}], True)


def _judge_all(value):
    """A coverage judge that marks every claim with a fixed ``established``."""
    def judge(claims, evidence):
        return [{"established": value, "rationale": "stub"} for _ in claims]
    return judge


def _run(out_dir, xml_dir, *, judge, reflist=_reflist):
    return jb.run_band(
        xml_dir, out_dir, extractor=_extractor, coverage_judge=judge,
        fetch_abstract=_abstract, fetch_reflist=reflist, session=_StubSession())


def _ref(citance="A meta-analysis showed X.", pmid="111", ref_id="R1",
         pmcid="PMC3000001"):
    return Reference(
        citation_id=f"{pmcid}:{ref_id}",
        citance=citance,
        claimed=ClaimedRef(title="T", claimed_pmid=pmid),
        source_pmcid=pmcid, source_pmid="3000001", source_title="Citing paper",
    )


# ==========================================================================
# route(): the deterministic coverage gate
# ==========================================================================
def test_route_full_coverage_when_all_established():
    assert jb.route([{"established": True}, {"established": True}]) \
        == jb.ROUTE_FULL_COVERAGE


def test_route_f6_when_any_established_false():
    assert jb.route([{"established": True}, {"established": False}]) \
        == jb.ROUTE_F6_FLAGGED


def test_route_held_when_some_none_and_no_false():
    assert jb.route([{"established": True}, {"established": None}]) \
        == jb.ROUTE_HELD


def test_route_tristate_none_is_unknown_not_a_gap():
    """A None claim is UNKNOWN, decided with ``is False`` -- it must route to
    HELD (not F6, which a naive falsy check would produce). An explicit False is
    the gap."""
    assert jb.route([{"established": None}]) == jb.ROUTE_HELD
    assert jb.route([{"established": False}]) == jb.ROUTE_F6_FLAGGED
    # False dominates even alongside unknowns.
    assert jb.route([{"established": None}, {"established": False}]) \
        == jb.ROUTE_F6_FLAGGED


# ==========================================================================
# build_item(): the judgeable unit + structural exclusions
# ==========================================================================
def test_build_item_shape_and_keys():
    item = jb.build_item(_ref())
    assert item["item_key"] == item["citation_id"] == "PMC3000001:R1"
    assert item["citing_sentence"] == "A meta-analysis showed X."
    assert item["cited_pmid"] == "111"


def test_build_item_none_when_no_cited_pmid():
    ref = _ref(citance="A meta-analysis showed X.", pmid="")
    assert jb.build_item(ref) is None
    assert jb.exclusion_reason(ref) == jb.EXCLUDED_NO_CITED_PMID


def test_build_item_none_when_empty_citance():
    ref = _ref(citance="", pmid="111")
    assert jb.build_item(ref) is None
    assert jb.exclusion_reason(ref) == jb.EXCLUDED_NO_CITANCE


def test_run_band_counts_structural_exclusions(tmp_path, excl_dir,
                                               patched_pubtypes):
    out = str(tmp_path / "out")
    man = _run(out, excl_dir, judge=_judge_all(True))
    c = man["counts"]
    assert c["items_built"] == 1                    # only R1
    assert c[jb.EXCLUDED_NO_CITED_PMID] == 1        # R2 (citance, no PMID)
    assert c[jb.EXCLUDED_NO_CITANCE] == 1           # R3 (PMID, not cited)


# ==========================================================================
# extract_atomic_claims() + coverage_verdicts()
# ==========================================================================
def test_extract_atomic_claims_delegates_and_guards():
    assert jb.extract_atomic_claims("A claim.", extractor=_extractor) == ["A claim."]
    assert jb.extract_atomic_claims("", extractor=_extractor) == []
    # blanks / non-strings dropped
    assert jb.extract_atomic_claims(
        "x", extractor=lambda s: ["  ", None, "keep"]) == ["keep"]


def test_coverage_verdicts_stamp_version_and_align():
    claims = ["c1", "c2"]
    judge = lambda cl, ev: [{"established": True}]      # returns too few
    verdicts = jb.coverage_verdicts(claims, {}, judge=judge)
    assert len(verdicts) == 2
    assert verdicts[0]["established"] is True
    assert verdicts[1]["established"] is None           # missing -> unknown
    assert all(v["prompt_version"] == jb.COVERAGE_PROMPT_VERSION for v in verdicts)


def test_coverage_verdicts_coerce_nonbool_to_none():
    verdicts = jb.coverage_verdicts(
        ["c"], {}, judge=lambda cl, ev: [{"established": "yes"}])
    assert verdicts[0]["established"] is None           # not exactly True/False


# ==========================================================================
# assemble_evidence(): abstract always; review reflist when is_review is True
# ==========================================================================
def test_evidence_review_reflist_populated_when_review():
    item = {"cited_pmid": "111", "cited_is_review": True}
    ev = jb.assemble_evidence(item, fetch_abstract=_abstract,
                              fetch_reflist=_reflist)
    assert ev["cited_abstract"] == "Abstract for PMID 111."
    assert ev["review_reflist"][0]["claimed_pmid"] == "555"
    assert ev["review_fulltext_available"] is True


def test_evidence_no_reflist_when_not_review():
    item = {"cited_pmid": "222", "cited_is_review": False}
    ev = jb.assemble_evidence(item, fetch_abstract=_abstract,
                              fetch_reflist=_reflist)
    assert ev["review_reflist"] == []


def test_evidence_no_reflist_when_fetch_reflist_absent():
    item = {"cited_pmid": "111", "cited_is_review": True}
    ev = jb.assemble_evidence(item, fetch_abstract=_abstract, fetch_reflist=None)
    assert ev["review_reflist"] == []


# ==========================================================================
# run_band(): end-to-end routing + record/payload contract
# ==========================================================================
def test_run_band_routes_full_coverage(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    man = _run(out, frame_dir, judge=_judge_all(True))
    assert man["counts"][jb.ROUTE_FULL_COVERAGE] == 1
    items = _read(out, "judgment_band_items.jsonl")
    assert items[0]["proposed_route"] == jb.ROUTE_FULL_COVERAGE


def test_run_band_routes_f6_when_claim_refuted(tmp_path, frame_dir,
                                               patched_pubtypes):
    out = str(tmp_path / "out")
    man = _run(out, frame_dir, judge=_judge_all(False))
    assert man["counts"][jb.ROUTE_F6_FLAGGED] == 1
    items = _read(out, "judgment_band_items.jsonl")
    assert items[0]["proposed_route"] == jb.ROUTE_F6_FLAGGED
    assert items[0]["proposed_verdict"] == "F6"


def test_run_band_routes_held_when_unknown(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    man = _run(out, frame_dir, judge=_judge_all(None))
    assert man["counts"][jb.ROUTE_HELD] == 1
    items = _read(out, "judgment_band_items.jsonl")
    assert items[0]["proposed_route"] == jb.ROUTE_HELD


def test_run_band_review_evidence_carries_reflist(tmp_path, frame_dir,
                                                  patched_pubtypes):
    out = str(tmp_path / "out")
    _run(out, frame_dir, judge=_judge_all(True))
    items = _read(out, "judgment_band_items.jsonl")
    assert items[0]["cited_is_review"] is True
    assert items[0]["evidence"]["review_reflist"][0]["claimed_pmid"] == "555"


def test_annotation_payload_is_blind_and_complete(tmp_path, frame_dir,
                                                  patched_pubtypes):
    out = str(tmp_path / "out")
    _run(out, frame_dir, judge=_judge_all(True))
    queue = _read(out, "judgment_band_annotation_queue.jsonl")
    assert len(queue) == 1
    payload = queue[0]
    # carries the unit, claims, evidence, label space, worksheet
    assert "atomic_claims" in payload and payload["atomic_claims"]
    assert "evidence" in payload
    assert payload["label_space"] == ["F6", "F3", "ACCURATE"]
    assert payload["citing_sentence"] and payload["cited_pmid"] == "111"
    # BLIND: the system's proposed verdict is NOT in the payload
    assert "proposed_route" not in payload
    assert "proposed_verdict" not in payload


def test_annotation_worksheet_all_null(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    _run(out, frame_dir, judge=_judge_all(True))
    payload = _read(out, "judgment_band_annotation_queue.jsonl")[0]
    ws = payload["worksheet"]
    assert set(ws) == {"F3_V1_coverage", "F3_V2_origin",
                       "F3_V3_repair_target_pmid", "F3_V4_loop_closed",
                       "confirmed_F3", "annotator"}
    assert all(v is None for v in ws.values())


def test_prompt_versions_on_every_record_and_manifest(tmp_path, frame_dir,
                                                      patched_pubtypes):
    out = str(tmp_path / "out")
    man = _run(out, frame_dir, judge=_judge_all(True))
    assert man["claim_extract_prompt_version"] == jb.CLAIM_EXTRACT_PROMPT_VERSION
    assert man["coverage_prompt_version"] == jb.COVERAGE_PROMPT_VERSION
    for rec in _read(out, "judgment_band_items.jsonl"):
        assert rec["claim_extract_prompt_version"] == jb.CLAIM_EXTRACT_PROMPT_VERSION
        assert rec["coverage_prompt_version"] == jb.COVERAGE_PROMPT_VERSION


def test_run_band_writes_three_files(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    man = _run(out, frame_dir, judge=_judge_all(True))
    for name in ("judgment_band_items.jsonl",
                 "judgment_band_annotation_queue.jsonl",
                 "judgment_band_manifest.json"):
        assert os.path.exists(os.path.join(out, name))
    assert man["detector_independent_annotation"] is True


def test_run_band_resume_no_duplicate(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    _run(out, frame_dir, judge=_judge_all(True))
    first = _read(out, "judgment_band_items.jsonl")
    man2 = _run(out, frame_dir, judge=_judge_all(True))
    assert man2["counts"]["docs_processed"] == 0
    second = _read(out, "judgment_band_items.jsonl")
    assert len(second) == len(first)              # no duplicate append
