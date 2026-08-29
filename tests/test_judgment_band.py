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

from cde.claims import band as jb
from cde.refs.schema import Reference, ClaimedRef


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


# Two cited refs, both structurally eligible: R1 (PMID 111), R2 (PMID 222). Used
# to prove the run_band accounting partition (items_built XOR PARSE_QUARANTINE).
MIX_XML = """<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">3000003</article-id>
    <title-group><article-title>Mixed outcomes</article-title></title-group>
  </article-meta></front>
  <body><sec>
    <p>Finding one <xref ref-type="bibr" rid="R1">1</xref>. Finding two
       <xref ref-type="bibr" rid="R2">2</xref>.</p>
  </sec></body>
  <back><ref-list>
    <ref id="R1">
      <element-citation publication-type="journal">
        <article-title>First</article-title><source>J</source><year>2011</year>
        <pub-id pub-id-type="pmid">111</pub-id>
      </element-citation>
    </ref>
    <ref id="R2">
      <element-citation publication-type="journal">
        <article-title>Second</article-title><source>J</source><year>2012</year>
        <pub-id pub-id-type="pmid">222</pub-id>
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
def mix_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir()
    _write(str(d), "PMC3000003.xml", MIX_XML)
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


# ==========================================================================
# run_band(): malformed-model-output quarantine (finding #1) + sentinel
# fetch-failed accounting (finding #3)
# ==========================================================================
def _raising_judge(claims, evidence):
    """Mimics the strict coverage parser rejecting a fenced/extra-key reply."""
    raise ValueError("model output is not one bare JSON object")


def test_run_band_quarantines_malformed_coverage_without_aborting(
        tmp_path, frame_dir, patched_pubtypes):
    """A ValueError from the coverage judge (strict-parse failure) must NOT
    abort the batch: the reference is quarantined to a durable row-level record
    and the run completes with a manifest."""
    out = str(tmp_path / "out")
    man = _run(out, frame_dir, judge=_raising_judge)
    assert man["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert man["counts"]["items_built"] == 0        # quarantine is not a built item
    items = _read(out, "judgment_band_items.jsonl")
    assert len(items) == 1
    assert items[0]["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert items[0]["proposed_verdict"] is None
    assert "model output is not one bare JSON object" in items[0]["parse_error"]
    # stamped and durable, but NOT surfaced to the blind annotation queue
    assert items[0]["coverage_prompt_version"] == jb.COVERAGE_PROMPT_VERSION
    assert _read(out, "judgment_band_annotation_queue.jsonl") == []


def test_run_band_quarantines_malformed_claim_extraction(
        tmp_path, frame_dir, patched_pubtypes):
    """The guard also covers the claim-extraction call (parse_claims failure)."""
    def raising_extractor(sentence):
        raise ValueError("claims[0] contains a citation marker")
    out = str(tmp_path / "out")
    man = jb.run_band(
        frame_dir, out, extractor=raising_extractor, coverage_judge=_judge_all(True),
        fetch_abstract=_abstract, fetch_reflist=_reflist, session=_StubSession())
    assert man["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    items = _read(out, "judgment_band_items.jsonl")
    assert items[0]["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert "citation marker" in items[0]["parse_error"]


def test_run_band_counts_sentinel_abstract_as_no_usable_abstract(
        tmp_path, frame_dir, patched_pubtypes):
    """A sentinel-string abstract ('N/A') is unusable per evidence_is_usable, so
    it is counted under `no_usable_abstract` -- NOT `excluded_fetch_failed` (it is
    neither necessarily a fetch failure nor an exclusion) (finding #3, decision
    2). The row still processes and routes HELD without an LLM call."""
    out = str(tmp_path / "out")
    man = jb.run_band(
        frame_dir, out, extractor=_extractor, coverage_judge=_judge_all(None),
        fetch_abstract=lambda pmid: "N/A", fetch_reflist=_reflist,
        session=_StubSession())
    assert man["counts"]["no_usable_abstract"] == 1
    assert "excluded_fetch_failed" not in man["counts"]    # renamed, not both
    assert man["counts"][jb.ROUTE_HELD] == 1


def test_run_band_operational_error_still_propagates(
        tmp_path, frame_dir, patched_pubtypes):
    """The quarantine guard catches ONLY ValueError (malformed output); an
    operational error (e.g. a network failure surfacing as RuntimeError) must
    still propagate rather than being silently quarantined."""
    def boom_judge(claims, evidence):
        raise RuntimeError("network down")
    out = str(tmp_path / "out")
    with pytest.raises(RuntimeError, match="network down"):
        _run(out, frame_dir, judge=boom_judge)


def test_run_band_accounting_partitions_eligible_refs_exactly_once(
        tmp_path, mix_dir, patched_pubtypes):
    """Every structurally-eligible reference is counted exactly once as either
    items_built or PARSE_QUARANTINE, and each is durably recorded once in
    items.jsonl (decision 1). Mixed run: R1 (PMID 111) routes normally; R2 (PMID
    222) hits a strict-parse failure and is quarantined."""
    def mixed_judge(claims, evidence):
        if "222" in (evidence.get("cited_abstract") or ""):
            raise ValueError("model output is not one bare JSON object")
        return [{"established": True, "rationale": "ok"} for _ in claims]
    out = str(tmp_path / "out")
    man = jb.run_band(
        mix_dir, out, extractor=_extractor, coverage_judge=mixed_judge,
        fetch_abstract=_abstract, fetch_reflist=_reflist, session=_StubSession())
    c = man["counts"]
    eligible = (c["refs_seen"] - c[jb.EXCLUDED_NO_CITANCE]
                - c[jb.EXCLUDED_NO_CITED_PMID])
    assert eligible == 2
    assert c["items_built"] == 1
    assert c[jb.ROUTE_PARSE_QUARANTINE] == 1
    # exact partition: every eligible ref is items_built XOR quarantined, once.
    assert c["items_built"] + c[jb.ROUTE_PARSE_QUARANTINE] == eligible
    # durably recorded exactly once each in items.jsonl (no double-count/drop).
    items = _read(out, "judgment_band_items.jsonl")
    ids = [it["citation_id"] for it in items]
    assert len(ids) == len(set(ids)) == eligible
    assert sorted(it["proposed_route"] for it in items) == sorted(
        [jb.ROUTE_FULL_COVERAGE, jb.ROUTE_PARSE_QUARANTINE])
    # the blind annotation queue carries ONLY the built (routed) item.
    queue = _read(out, "judgment_band_annotation_queue.jsonl")
    assert len(queue) == c["items_built"] == 1


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


# ==========================================================================
# run_band(): Part C -- per-atomic-claim coverage distribution tally
# ==========================================================================
def _four_claim_extractor(sentence):
    """One claim per abstract-scoped coverage bucket (established, contradicted,
    unconfirmed-specific, off-topic)."""
    return ["c-established", "c-contradicted", "c-unconfirmed", "c-offtopic"]


def _structured_judge(claims, evidence):
    """A judge that carries the raw structured fields, one per bucket, with
    ``established`` consistent with the tri-state aggregation."""
    return [
        {"established": True, "engages_subject": True,
         "contradicts": False, "unconfirmed_specifics": []},
        {"established": False, "engages_subject": True,
         "contradicts": True, "unconfirmed_specifics": []},
        {"established": None, "engages_subject": True,
         "contradicts": False, "unconfirmed_specifics": ["a mouse model"]},
        {"established": None, "engages_subject": False,
         "contradicts": False, "unconfirmed_specifics": []},
    ]


def test_run_band_coverage_distribution_tallies_per_claim(
        tmp_path, frame_dir, patched_pubtypes):
    """Part C: the four abstract-scoped buckets are tallied PER ATOMIC CLAIM,
    surfaced under manifest['coverage_distribution'] AND in counts, and sum to
    the number of coverage verdicts. Only a contradiction is a fault, so this
    item (one contradicted claim) routes F6_FLAGGED."""
    out = str(tmp_path / "out")
    man = jb.run_band(
        frame_dir, out, extractor=_four_claim_extractor,
        coverage_judge=_structured_judge, fetch_abstract=_abstract,
        fetch_reflist=_reflist, session=_StubSession())

    dist = man["coverage_distribution"]
    assert dist[jb.COVERAGE_ESTABLISHED] == 1
    assert dist[jb.COVERAGE_CONTRADICTED] == 1
    assert dist[jb.COVERAGE_UNCONFIRMED_SPECIFIC] == 1
    assert dist[jb.COVERAGE_OFF_TOPIC] == 1
    assert "contradicted" in dist["note"].lower()

    # C1: the same four counters live in counts (existing route counters intact).
    for bucket in (jb.COVERAGE_ESTABLISHED, jb.COVERAGE_CONTRADICTED,
                   jb.COVERAGE_UNCONFIRMED_SPECIFIC, jb.COVERAGE_OFF_TOPIC):
        assert man["counts"][bucket] == 1

    # per-claim sums equal the number of verdicts (acceptance row 19)
    items = _read(out, "judgment_band_items.jsonl")
    n_verdicts = sum(len(it["coverage_verdicts"]) for it in items)
    assert n_verdicts == 4
    assert (dist[jb.COVERAGE_ESTABLISHED] + dist[jb.COVERAGE_CONTRADICTED]
            + dist[jb.COVERAGE_UNCONFIRMED_SPECIFIC]
            + dist[jb.COVERAGE_OFF_TOPIC]) == n_verdicts

    # a contradiction is the only abstract-scoped fault -> F6_FLAGGED
    assert man["counts"][jb.ROUTE_F6_FLAGGED] == 1


def test_run_band_coverage_distribution_skips_no_usable_abstract(
        tmp_path, frame_dir, patched_pubtypes):
    """The no-usable-abstract path carries no structured fields, so its verdicts
    are NOT counted in any coverage bucket (they are accounted at item level by
    no_usable_abstract). All four buckets stay zero here."""
    out = str(tmp_path / "out")
    man = jb.run_band(
        frame_dir, out, extractor=_extractor, coverage_judge=_judge_all(None),
        fetch_abstract=lambda pmid: "N/A", fetch_reflist=_reflist,
        session=_StubSession())
    assert man["counts"]["no_usable_abstract"] == 1
    dist = man["coverage_distribution"]
    assert all(dist[b] == 0 for b in (
        jb.COVERAGE_ESTABLISHED, jb.COVERAGE_CONTRADICTED,
        jb.COVERAGE_UNCONFIRMED_SPECIFIC, jb.COVERAGE_OFF_TOPIC))
