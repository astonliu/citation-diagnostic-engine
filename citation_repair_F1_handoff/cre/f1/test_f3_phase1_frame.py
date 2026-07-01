"""Offline tests for the F3 Phase-1 sampling frame (two-phase design).

No live network: ncbi_pubtypes is monkeypatched ON THE f3_phase1_frame MODULE
(the module imports the helper into its own namespace, so the patch must target
that namespace, not f3_candidate_collect). The LLM claim-specificity classifier
is injected as a plain Python callable, so S2 refinement is tested with no host.

Run: PYTHONPATH=<repo> python -m pytest cre/f1/test_f3_phase1_frame.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from cre.f1 import f3_phase1_frame as fp


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
# A citing paper with two body sentences:
#   S_claim   -> cites R1 (PMID 111, patched to Review) : a specific finding.
#   S_editor  -> cites R2 (PMID 222, patched to non-review): navigational.
FRAME_XML = """<article>
  <front><article-meta>
    <article-id pub-id-type="pmid">3000001</article-id>
    <title-group><article-title>A citing paper</article-title></title-group>
  </article-meta></front>
  <body><sec>
    <title>Introduction</title>
    <p>A meta-analysis showed that the therapy reduced mortality by thirty percent
       <xref ref-type="bibr" rid="R1">1</xref>. This issue contains four
       interesting articles <xref ref-type="bibr" rid="R2">2</xref>.</p>
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
    <ref id="R2">
      <element-citation publication-type="journal">
        <person-group><name><surname>Jones</surname></name></person-group>
        <article-title>An editorial</article-title>
        <source>J Ed</source><year>2018</year>
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


@pytest.fixture
def frame_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir()
    _write(str(d), "PMC3000001.xml", FRAME_XML)
    return str(d)


@pytest.fixture
def patched_pubtypes(monkeypatch):
    """PMID 111 -> Review; everything else -> not a review. Patched on the
    f3_phase1_frame namespace (where screen_dir looks it up)."""
    def fake_pubtypes(pmid, api_key="", email="", session=None):
        return ["Review", "Journal Article"] if str(pmid) == "111" \
            else ["Journal Article"]
    monkeypatch.setattr(fp, "ncbi_pubtypes", fake_pubtypes)


def _read(out_dir, name):
    path = os.path.join(out_dir, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class _StubSession:
    """screen_dir defaults to requests.Session(); pass a stub so no import/
    network happens even if a patch is missed."""


# --------------------------------------------------------------------------
# S1 metadata gate
# --------------------------------------------------------------------------
def test_s1_only_review_pmid_counts(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    man = fp.screen_dir(frame_dir, out, session=_StubSession())
    c = man["counts"]
    assert c["docs_processed"] == 1
    assert c["citations_total"] == 2
    assert c["s1_cited_is_review"] == 1   # only PMID 111


# --------------------------------------------------------------------------
# S2 mechanical proxy
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sentence", [
    "A meta-analysis showed that the therapy reduced mortality by thirty percent.",
    "The protein binds the receptor and activates downstream signaling cascades.",
])
def test_s2_mechanical_accepts_claims(sentence):
    assert fp.s2_mechanical(sentence) is True


@pytest.mark.parametrize("sentence", [
    "See figure 2.",                       # too short / navigational
    "As described previously in ref 4.",   # methods/hedge pattern
    "We performed the assay in triplicate.",
])
def test_s2_mechanical_rejects_noise(sentence):
    assert fp.s2_mechanical(sentence) is False


# --------------------------------------------------------------------------
# Eligibility = S1 AND S2 ; mechanical mode
# --------------------------------------------------------------------------
def test_eligible_is_s1_and_s2_mechanical(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    man = fp.screen_dir(frame_dir, out, session=_StubSession())
    # S_claim: S1 True + S2 True -> eligible. S_editor: S1 False -> not.
    assert man["counts"]["eligible_stratum"] == 1
    elig = _read(out, "f3_phase1_eligible.jsonl")
    assert len(elig) == 1
    assert "meta-analysis" in elig[0]["citing_sentence"]


# --------------------------------------------------------------------------
# LLM claim-specificity refinement (injected callable)
# --------------------------------------------------------------------------
def test_llm_refinement_can_drop_a_review_cited_sentence(tmp_path, frame_dir,
                                                         patched_pubtypes):
    """A classifier that marks the S1 sentence as NON-specific must remove it
    from the eligible stratum (S2 overridden by the LLM verdict)."""
    def classifier(sentences):
        # mark everything as editorial/non-specific
        return [{"category": "editorial_navigational",
                 "carries_specific_claim": False} for _ in sentences]
    out = str(tmp_path / "out")
    man = fp.screen_dir(frame_dir, out, session=_StubSession(),
                        specificity_classifier=classifier)
    assert man["s2_mode"] == "llm_specificity"
    assert man["counts"]["eligible_stratum"] == 0


def test_llm_refinement_keeps_specific_claim(tmp_path, frame_dir,
                                             patched_pubtypes):
    def classifier(sentences):
        return [{"category": "specific_factual_claim",
                 "carries_specific_claim": True} for _ in sentences]
    out = str(tmp_path / "out")
    man = fp.screen_dir(frame_dir, out, session=_StubSession(),
                        specificity_classifier=classifier)
    assert man["counts"]["eligible_stratum"] == 1
    elig = _read(out, "f3_phase1_eligible.jsonl")
    assert elig[0]["S2_category"] == "specific_factual_claim"


def test_llm_failure_falls_back_to_mechanical(tmp_path, frame_dir,
                                              patched_pubtypes):
    """A None verdict for an item falls back to the mechanical S2 verdict."""
    def classifier(sentences):
        return [None for _ in sentences]
    out = str(tmp_path / "out")
    man = fp.screen_dir(frame_dir, out, session=_StubSession(),
                        specificity_classifier=classifier)
    # mechanical S2 is True for the meta-analysis sentence -> still eligible
    assert man["counts"]["eligible_stratum"] == 1


# --------------------------------------------------------------------------
# Detector-independence + no-label guarantees
# --------------------------------------------------------------------------
def test_no_f3_label_and_null_worksheet(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    fp.screen_dir(frame_dir, out, session=_StubSession())
    elig = _read(out, "f3_phase1_eligible.jsonl")
    ws = elig[0]["phase2_worksheet"]
    assert set(ws) == {"F3_V1_coverage", "F3_V2_origin", "F3_V3_repair_target_pmid",
                       "F3_V4_loop_closed", "confirmed_F3", "annotator"}
    assert all(v is None for v in ws.values())
    # no F3 label field anywhere on the record
    assert "confirmed_F3" not in elig[0] or elig[0].get("confirmed_F3") is None


def test_manifest_declares_detector_independent_and_sizing(tmp_path, frame_dir,
                                                           patched_pubtypes):
    out = str(tmp_path / "out")
    man = fp.screen_dir(frame_dir, out, session=_StubSession())
    assert man["detector_independent"] is True
    assert "SAMPLING FRAME" in man["warning"]
    s = man["sizing"]
    assert s["citations_per_parseable_doc"] == 2.0
    assert 0.0 <= s["eligible_per_citation"] <= 1.0
    assert "within_stratum_F3_rate" in s["note"]


def test_frame_file_has_every_citation(tmp_path, frame_dir, patched_pubtypes):
    out = str(tmp_path / "out")
    fp.screen_dir(frame_dir, out, session=_StubSession())
    frame = _read(out, "f3_phase1_frame.jsonl")
    # both citations recorded in the frame, only one eligible
    assert len(frame) == 2
    assert sum(1 for r in frame if r["eligible"]) == 1
