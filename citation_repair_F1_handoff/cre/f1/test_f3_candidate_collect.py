"""Offline tests for the F3 candidate collector (§9 of the build spec).

No live network: the three NCBI helpers (ncbi_pubtypes, ncbi_pmid_to_pmcid,
ncbi_pmc_reflist) are monkeypatched on the module, and collect() calls them as
module globals so the patches take effect.

Reconciliation note on the §9 integration fixture:
    The spec lists a three-sentence fixture (single-cite attribution,
    non-attribution, multi-cite attribution) AND asserts `attribution_hits == 1`
    with the reason "the non-attribution sentence did not fire". Two attribution
    sentences cannot yield one hit, so the binding assertion (== 1) implies a
    single attribution sentence reaching the counter. We honor that here: the
    collect() integration fixture holds one single-cite attribution sentence
    plus one non-attribution sentence. The multi-cite / cardinality path is
    covered by (a) the dedicated cite_cardinality() spot-checks the spec lists
    and (b) an end-to-end multi-cite emit test (test_multicite_cardinality_e2e).

Run:  PYTHONPATH=<repo> python -m pytest cre/f1/test_f3_candidate_collect.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from cre.f1 import f3_candidate_collect as f3


# --------------------------------------------------------------------------
# Fixtures (crafted XML written to a tmp dir)
# --------------------------------------------------------------------------
# One single-cite priority-attribution sentence -> R1 (PMID 111, patched to a
# Review with a resolvable PMCID) and one non-attribution sentence -> R2.
REVIEW_XML = """<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">2000001</article-id>
    <title-group><article-title>A citing paper on origins</article-title></title-group>
  </article-meta></front>
  <body><sec>
    <title>Introduction</title>
    <p>The mechanism was first described by Smith and colleagues
       <xref ref-type="bibr" rid="R1">1</xref>. Blood pressure was measured in
       all participants <xref ref-type="bibr" rid="R2">2</xref>.</p>
  </sec></body>
  <back><ref-list>
    <ref id="R1">
      <element-citation publication-type="journal">
        <person-group><name><surname>Smith</surname></name></person-group>
        <article-title>A review of the mechanism</article-title>
        <source>Nat Rev</source><year>2015</year>
        <pub-id pub-id-type="pmid">111</pub-id>
      </element-citation>
    </ref>
    <ref id="R2">
      <element-citation publication-type="journal">
        <person-group><name><surname>Jones</surname></name></person-group>
        <article-title>Blood pressure methods</article-title>
        <source>J Meth</source><year>2018</year>
        <pub-id pub-id-type="pmid">222</pub-id>
      </element-citation>
    </ref>
  </ref-list></back>
</article>
"""

# One attribution sentence citing two refs (multi-cite via shared citance); the
# refs carry NO PMID, so no network is touched and permissive mode emits it.
MULTICITE_XML = """<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">2000002</article-id>
    <title-group><article-title>A citing paper, multi-cite</article-title></title-group>
  </article-meta></front>
  <body><sec>
    <p>This phenomenon was first reported in two independent cohorts
       <xref ref-type="bibr" rid="R3">3</xref><xref ref-type="bibr" rid="R4">4</xref>.</p>
  </sec></body>
  <back><ref-list>
    <ref id="R3">
      <element-citation publication-type="journal">
        <person-group><name><surname>Alpha</surname></name></person-group>
        <article-title>Cohort one</article-title><source>J A</source><year>2011</year>
      </element-citation>
    </ref>
    <ref id="R4">
      <element-citation publication-type="journal">
        <person-group><name><surname>Beta</surname></name></person-group>
        <article-title>Cohort two</article-title><source>J B</source><year>2012</year>
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


@pytest.fixture
def review_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir()
    _write(str(d), "PMC1000001.xml", REVIEW_XML)
    return str(d)


@pytest.fixture
def patched_ncbi(monkeypatch):
    """Patch the three network helpers on the module. PMID 111 -> Review with a
    resolvable PMCID + a one-entry provenance list; everything else -> not a
    review."""
    def fake_pubtypes(pmid, api_key="", email="", session=None):
        return ["Review", "Journal Article"] if str(pmid) == "111" else \
               ["Journal Article"]

    def fake_pmid_to_pmcid(pmid, api_key="", email="", session=None):
        return "PMC90909" if str(pmid) == "111" else ""

    def fake_reflist(pmcid, api_key="", email="", session=None):
        return ([{"title": "The original primary finding",
                  "claimed_pmid": "555", "year": 2010}], True)

    monkeypatch.setattr(f3, "ncbi_pubtypes", fake_pubtypes)
    monkeypatch.setattr(f3, "ncbi_pmid_to_pmcid", fake_pmid_to_pmcid)
    monkeypatch.setattr(f3, "ncbi_pmc_reflist", fake_reflist)


def _read_candidates(out_dir):
    path = os.path.join(out_dir, "f3_candidates.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# --------------------------------------------------------------------------
# Integration: default review+pmcid gate
# --------------------------------------------------------------------------
def test_default_gate_emits_review_candidate(tmp_path, review_dir, patched_ncbi):
    out_dir = str(tmp_path / "out")
    manifest = f3.collect(review_dir, out_dir, enrich_review=True)

    counts = manifest["counts"]
    # The non-attribution sentence did not fire; only the single attribution
    # sentence (R1) is a hit.
    assert counts["attribution_hits"] == 1
    assert counts["docs_processed"] == 1
    assert counts["cited_is_review"] == 1
    assert counts["review_has_pmcid"] == 1
    assert counts["emitted"] == 1
    assert counts["filtered_out"] == 0

    cands = _read_candidates(out_dir)
    assert len(cands) == 1
    c = cands[0]
    assert c["candidate_id"] == "PMC1000001:R1"
    assert c["citing_pmcid"] == "PMC1000001"
    assert c["attribution_pattern"] == "first_verb"
    assert "first described" in c["attribution_phrase"].lower()
    assert c["cited_is_review"] is True
    assert c["cited_pmcid"] == "PMC90909"
    assert c["emit_reason"] == "review+pmcid"

    # single-cite cardinality
    assert c["single_cite_estimate"] is True
    assert c["cite_cardinality_estimate"] == 1

    # provenance staged under --enrich-review
    assert c["provenance_candidates"]
    assert c["provenance_candidates"][0]["claimed_pmid"] == "555"
    assert c["review_fulltext_available"] is True

    # verification worksheet: six keys, all null (tool never fills them)
    v = c["verification"]
    assert set(v.keys()) == {"F3_V1_coverage", "F3_V2_origin",
                             "F3_V3_repair_target_pmid", "F3_V4_loop_closed",
                             "confirmed_F3", "annotator"}
    assert all(val is None for val in v.values())


def test_manifest_is_calibration_only(tmp_path, review_dir, patched_ncbi):
    out_dir = str(tmp_path / "out")
    manifest = f3.collect(review_dir, out_dir)
    assert manifest["calibration_only"] is True
    assert "CALIBRATION-ONLY" in manifest["warning"]
    assert "gold" in manifest["warning"].lower()
    assert manifest["attribution_patterns"] == f3.ATTRIBUTION_PATTERN_NAMES
    assert "review" in manifest["review_pubtypes"]
    # the manifest file was written
    assert os.path.exists(os.path.join(out_dir, "f3_collect_manifest.json"))


# --------------------------------------------------------------------------
# Resume: second run processes 0 new docs, no duplicate append
# --------------------------------------------------------------------------
def test_resume_no_duplicate(tmp_path, review_dir, patched_ncbi):
    out_dir = str(tmp_path / "out")
    f3.collect(review_dir, out_dir, enrich_review=True)
    first = _read_candidates(out_dir)

    manifest2 = f3.collect(review_dir, out_dir, enrich_review=True)
    assert manifest2["counts"]["docs_processed"] == 0
    assert manifest2["counts"]["emitted"] == 0

    second = _read_candidates(out_dir)
    assert len(second) == len(first)   # no duplicate appended


# --------------------------------------------------------------------------
# Permissive gate: emit all attribution hits
# --------------------------------------------------------------------------
def test_permissive_gate(tmp_path, review_dir, patched_ncbi):
    out_dir = str(tmp_path / "out")
    manifest = f3.collect(review_dir, out_dir, require_review_oa=False)
    # both attribution sentences? No -- still only one attribution sentence, but
    # now it emits regardless of the review/pmcid check.
    assert manifest["counts"]["attribution_hits"] == 1
    cands = _read_candidates(out_dir)
    assert len(cands) == 1
    assert cands[0]["emit_reason"] == "attribution-hit"


# --------------------------------------------------------------------------
# End-to-end multi-cite (cardinality via shared citance, no network)
# --------------------------------------------------------------------------
def test_multicite_cardinality_e2e(tmp_path):
    d = tmp_path / "xml"
    d.mkdir()
    _write(str(d), "PMC2000002.xml", MULTICITE_XML)
    out_dir = str(tmp_path / "out")
    # No PMIDs on these refs -> no network; permissive mode emits them.
    manifest = f3.collect(str(d), out_dir, require_review_oa=False)

    # Two refs share the one attribution citance -> two attribution hits, both
    # emitted, each with cardinality 2 (group count) and single_cite False.
    assert manifest["counts"]["attribution_hits"] == 2
    assert manifest["counts"]["attribution_hits_no_pmid"] == 2
    cands = _read_candidates(out_dir)
    assert len(cands) == 2
    for c in cands:
        assert c["cite_cardinality_estimate"] == 2
        assert c["single_cite_estimate"] is False
        assert c["emit_reason"] == "attribution-hit"
        assert "resolved" in c["note"].lower()   # PMID-must-be-resolved note


# --------------------------------------------------------------------------
# Precision spot-checks on attribution_hit(...)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    "X was first demonstrated by Y",
    "originally reported in",
    "this landmark trial",
    "they pioneered the approach",
    "the term was coined",
])
def test_attribution_fires(sentence):
    assert f3.attribution_hit(sentence) is not None


@pytest.mark.parametrize("sentence", [
    "have since confirmed",
    "we also measured",
    "in a recent study",
])
def test_attribution_does_not_fire(sentence):
    assert f3.attribution_hit(sentence) is None


# --------------------------------------------------------------------------
# Cardinality spot-checks on cite_cardinality(...)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sentence,expected", [
    ("shown earlier [12, 13]", 2),
    ("the earliest report [4-6]", 2),
    ("first described in [9]", 1),
])
def test_cite_cardinality(sentence, expected):
    assert f3.cite_cardinality(sentence) == expected
