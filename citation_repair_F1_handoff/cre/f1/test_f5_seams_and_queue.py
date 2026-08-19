"""F5 seams (item 3), discovery queue (item 4), absence language (item 5).

The six seams are the reason decide_f5 had never run on real data: only test fakes
had ever satisfied them. These pin the behaviours that decide whether a run is
honest, not merely whether it completes.
"""
from __future__ import annotations

import pytest

from cre.f1 import f5_discovery_queue as q
from cre.f1 import f5_seams as s
from cre.f1 import f5_supersession as f5


# ==========================================================================
# 3b -- classify_evidence_tier: deterministic, TOTAL, no model call
# ==========================================================================

@pytest.mark.parametrize("pubtypes,expected", [
    (["Meta-Analysis"], f5.EvidenceTier.SYSTEMATIC_REVIEW_OR_META_ANALYSIS),
    (["Systematic Review", "Journal Article"], f5.EvidenceTier.SYSTEMATIC_REVIEW_OR_META_ANALYSIS),
    (["Randomized Controlled Trial"], f5.EvidenceTier.RCT),
    (["Case Reports"], f5.EvidenceTier.CASE_SERIES_OR_REPORT),
])
def test_tier_maps_the_publication_types_pubmed_actually_emits(pubtypes, expected):
    assert s.classify_evidence_tier({"publication_types": pubtypes}) is expected


def test_tier_is_total_and_floors_the_unrecognised_rather_than_raising():
    """_tier_from raises on an unknown string, so the mapping must be TOTAL: an
    unrecognised record must not stop a run."""
    tier, basis = s.classify_evidence_tier_explained({"publication_types": ["Letter"]})
    assert tier is s.UNCLASSIFIED_TIER
    assert basis == "unclassified"
    # And it survives the detector's own coercion, which is what would raise.
    assert f5._tier_from(tier, "candidate tier") is tier


def test_tier_falls_back_to_mesh_only_when_publication_type_does_not_decide():
    tier, basis = s.classify_evidence_tier_explained(
        {"publication_types": ["Journal Article"], "mesh_terms": ["Retrospective Studies"]})
    assert tier is f5.EvidenceTier.RETROSPECTIVE_COHORT
    assert basis.startswith("mesh:")


# ==========================================================================
# 3a -- check_formal_notice: as_of_date is load-bearing
# ==========================================================================

def test_retraction_is_detected():
    check = s.make_check_formal_notice(
        lambda w: {"publication_types": ["Retracted Publication"],
                   "notice_date": "2022-01-01"})
    status = check("W1", as_of_date="2024-01-01")
    assert status.notice_kind == "retraction"


def test_notice_dated_after_as_of_date_is_not_applied():
    """Bakker et al.: papers are retracted while reviews are in press, so status is
    a function of the date you check. A later notice did not exist yet."""
    check = s.make_check_formal_notice(
        lambda w: {"publication_types": ["Retracted Publication"],
                   "notice_date": "2025-06-01"})
    assert check("W1", as_of_date="2024-01-01").notice_kind == "none"


# ==========================================================================
# 3d -- retrieval: adequacy and status must stay honest
# ==========================================================================

def test_transport_failure_is_status_failure_never_a_clean_empty():
    """The confusion that cost calibration run 1 its entire yield: an outage
    wearing the same reason string as a real absence."""
    def boom(*a, **k):
        raise RuntimeError("network down")
    result = s.make_retrieve_superseding_candidates(boom)(
        {}, "claim", after_date="2020-01-01", as_of_date="2024-01-01")
    assert result.status == "failure"
    assert result.candidates == ()
    assert "failed" in result.rationale


def test_clean_zero_result_is_status_ok_and_says_none_found_not_none_exists():
    result = s.make_retrieve_superseding_candidates(lambda *a, **k: [])(
        {}, "claim", after_date="2020-01-01", as_of_date="2024-01-01")
    assert (result.status, result.adequacy) == ("ok", "empty")
    assert "NOT a finding that none exists" in result.rationale


def test_a_failure_and_an_absence_are_distinguishable_by_reason():
    assert q.negative_reason("failure", "empty") == q.NEGATIVE_RETRIEVAL_FAILED
    assert q.negative_reason("ok", "empty") == q.NEGATIVE_NO_EVIDENCE_FOUND


def test_structural_filter_drops_candidates_not_strictly_after_the_cited_date():
    hits = [{"id": "W1", "pub_date": "2019-01-01"},   # before -> dropped
            {"id": "W2", "pub_date": "2020-01-01"},   # equal  -> dropped
            {"id": "W3", "pub_date": "2021-01-01"}]
    result = s.make_retrieve_superseding_candidates(lambda *a, **k: hits)(
        {}, "claim", after_date="2020-01-01", as_of_date="2024-01-01")
    assert [c.id for c in result.candidates] == ["W3"]


def test_the_cap_is_recorded_and_a_capped_result_is_inadequate_not_adequate():
    """A silent cap reads as 'we looked at everything'."""
    hits = [{"id": f"W{i}", "pub_date": "2021-01-01"} for i in range(10)]
    result = s.make_retrieve_superseding_candidates(lambda *a, **k: hits, cap=3)(
        {}, "claim", after_date="2020-01-01", as_of_date="2024-01-01")
    assert len(result.candidates) == 3
    assert result.adequacy == "inadequate"
    assert "CAPPED" in result.rationale
    assert s.retrieval_protocol()["candidate_cap"] == s.CANDIDATE_CAP


def test_duplicate_hits_are_collapsed_before_RetrievalResult_rejects_them():
    hits = [{"id": "W1", "pub_date": "2021-01-01"},
            {"id": "W1", "pub_date": "2022-01-01"}]
    result = s.make_retrieve_superseding_candidates(lambda *a, **k: hits)(
        {}, "claim", after_date="2020-01-01", as_of_date="2024-01-01")
    assert [c.id for c in result.candidates] == ["W1"]


def test_retrieval_protocol_is_readable_not_only_a_hash():
    protocol = s.retrieval_protocol(after_date="2020-01-01", as_of_date="2024-01-01")
    for key in ("planned_sources", "date_window", "candidate_cap", "reranker",
                "candidate_generation", "structural_filters",
                "adequacy_requires", "known_limitations"):
        assert key in protocol
    assert "sources_queried" not in protocol
    assert protocol["structural_filters"] == [
        "publication date strictly after after_date",
        "publication date on or before as_of_date",
    ]
    assert protocol["reranker"] == "none"          # stated limitation, not an oversight


# ==========================================================================
# 3e -- attestation is a DECLARED stub
# ==========================================================================

def test_attestation_seam_is_a_declared_stub_and_says_so():
    assert s.find_supersession_attestation({}, "claim", "W1", as_of_date="2024-01-01") is None
    assert s.ATTESTATION_LOOKUP_PERFORMED is False
    assert "not looked for" in s.ATTESTATION_STUB_REASON


# ==========================================================================
# 3f -- judge_contradiction resolves ids to text before the verbatim check
# ==========================================================================

def test_judge_resolves_selected_ids_so_the_verbatim_check_passes_by_construction():
    import json
    cited = f5.ComparabilitySource(abstract="Metformin reduced HbA1c by 1.2%.")
    cand = f5.ComparabilitySource(results="No between-group difference was seen.")

    def fake_complete(prompt):
        assert "s1" in prompt and "[abstract]" in prompt
        return json.dumps({
            "directional_contradiction": True, "claim_match": "match",
            "outcome_relation": "same", "population_relation": "equivalent",
            "cited_direction": "decrease", "candidate_direction": "no_effect",
            "magnitude": "large", "confidence": 0.8, "scope_mismatch_axis": "none",
            "cited_finding_span": {"label": "abstract", "sentence_ids": ["s1"]},
            "candidate_contradiction_span": {"label": "results", "sentence_ids": ["s1"]},
        })

    raw = s.make_judge_contradiction(fake_complete)(cited, cand, "claim")
    judgment = f5._parse_contradiction(raw)
    assert judgment.cited_finding_span in f5._source_text(cited)
    assert judgment.candidate_contradiction_span in f5._source_text(cand)


def test_unresolvable_span_is_logged_as_a_miss_and_does_not_raise():
    import json
    cited = f5.ComparabilitySource(abstract="Metformin reduced HbA1c by 1.2%.")
    misses = []

    def fake_complete(prompt):
        return json.dumps({
            "directional_contradiction": False, "claim_match": "uncertain",
            "outcome_relation": "uncertain", "population_relation": "unclear",
            "cited_direction": "unclear", "candidate_direction": "unclear",
            "magnitude": "unclear", "confidence": 0.1, "scope_mismatch_axis": "unclear",
            "cited_finding_span": {"label": "abstract", "sentence_ids": ["s99"]},
            "candidate_contradiction_span": {"label": "nope", "sentence_ids": ["s1"]},
        })

    raw = s.make_judge_contradiction(fake_complete, span_miss_log=misses)(
        cited, cited, "claim")
    assert f5._parse_contradiction(raw).cited_finding_span == ""
    assert len(misses) == 2


# ==========================================================================
# Item 4 -- the discovery queue
# ==========================================================================

def _records():
    return [{
        "claim_index": 0, "claim_text": "Metformin reduces HbA1c",
        "cited_work_id": "W1", "cited_date": "2015-01-01",
        "temporal_state": "QUALIFYING_CONTRADICTION", "proposed_route": "F5",
        "candidate_assessments": [
            {"candidate_work_id": "W2", "candidate_date": "2021-01-01",
             "discovery_disposition": "surface", "scope_mismatch_axis": "none",
             "reason": "directional_contradiction", "confidence": 0.9,
             "cited_finding_span": "Metformin reduced HbA1c by 1.2%.",
             "candidate_contradiction_span": "No between-group difference."},
            {"candidate_work_id": "W3", "candidate_date": "2022-01-01",
             "discovery_disposition": "do_not_surface",
             "scope_mismatch_axis": "species_or_strain", "reason": "not_comparable"},
            {"candidate_work_id": "W4", "candidate_date": "2023-01-01",
             "discovery_disposition": "unassessable", "reason": "span_unverifiable"},
        ]}]


def test_queue_holds_every_surface_row_with_what_an_annotator_needs():
    queue = q.build_queue(_records())
    assert len(queue) == 1
    row = queue[0]
    for key in ("claim_text", "cited_work_id", "candidate_work_id",
                "cited_finding_span", "candidate_contradiction_span"):
        assert row.get(key) is not None
    assert "scope_mismatch_axis" not in row
    assert "reason" not in row


def test_queue_is_blind_at_every_depth():
    queue = q.build_queue(_records())
    q.assert_blind(queue)                     # must not raise
    for field in q.BLIND_FIELDS:
        assert field not in set(q._walk_keys(queue[0]))


def test_assert_blind_catches_a_leak_nested_inside_a_row():
    """A top-level whitelist is necessary but NOT sufficient -- a candidate carries
    its own discovery_disposition, which must not ride in nested."""
    bad = [{"claim_text": "x", "candidate": {"discovery_disposition": "surface"}}]
    with pytest.raises(ValueError, match="blind field"):
        q.assert_blind(bad)


def test_assert_blind_catches_a_detector_value_under_a_renamed_key():
    with pytest.raises(ValueError, match="detector value"):
        q.assert_blind([{"human_note": "qualifying_contradiction"}])


def test_do_not_surface_and_unassessable_are_counted_but_never_queued():
    """DEC-045 read-across: recorded and counted, never put to an annotator."""
    queue = q.build_queue(_records())
    queued = {row["candidate_work_id"] for row in queue}
    assert queued == {"W2"}
    assert q.disposition_counts(_records()) == {
        "surface": 1, "do_not_surface": 1, "unassessable": 1}


def test_the_queue_has_its_own_filename_not_the_shared_annotation_queue():
    """judgment_band_annotation_queue.jsonl is written by two entry points and 24
    assertions pin its contents, 8 of them asserting it is EMPTY."""
    assert q.QUEUE_FILENAME == "f5_discovery_queue.jsonl"
    assert "judgment_band" not in q.QUEUE_FILENAME
