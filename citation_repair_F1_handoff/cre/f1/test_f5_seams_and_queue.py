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


def _production_bundle():
    def fetch_meta(work_id):
        return {
            "id": str(work_id), "pmid": str(work_id),
            "pub_date": "2010-01-01", "pub_date_latest": "2010-01-01",
            "abstract": "abstract", "publication_types": [
                "Randomized Controlled Trial"],
        }

    generator = lambda _prompt: "{}"
    verifier = lambda _prompt: "{}"
    seams = s.build_f5_seams(
        fetch_meta=fetch_meta, fetch_abstract=lambda _wid: "abstract",
        search_candidates=lambda *_a, **_k: [], complete=generator,
        verifier_complete=verifier, judgment_model_id="model",
        verifier_model_id="model")
    builder = s.make_f5_evidence_builder(
        fetch_meta, as_of_date="2024-01-01")
    policy = f5.F5Policy(
        mode="deployment", generator_model_id="model",
        verifier_model_id="model")
    return seams, builder, policy


def test_production_bundle_is_explicitly_validated():
    seams, builder, policy = _production_bundle()
    s.validate_production_f5_configuration(
        seams=seams, evidence_builder=builder, policy=policy,
        run_model="model")


def test_production_bundle_rejects_missing_verifier():
    seams, builder, policy = _production_bundle()
    seams["verify_contradiction"] = None
    with pytest.raises(ValueError, match="generator and verifier"):
        s.validate_production_f5_configuration(
            seams=seams, evidence_builder=builder, policy=policy,
            run_model="model")


def test_builder_rejects_reused_generator_as_verifier_transport():
    transport = lambda _prompt: "{}"
    with pytest.raises(ValueError, match="transports must be distinct"):
        s.build_f5_seams(
            fetch_meta=lambda _wid: {}, fetch_abstract=lambda _wid: "",
            search_candidates=lambda *_a, **_k: [], complete=transport,
            verifier_complete=transport)


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
                   "notice_date": "2022-01-01", "id": w})
    status = check("W1", as_of_date="2024-01-01")
    assert status.notice_kind == "retraction"


def test_notice_dated_after_as_of_date_is_not_applied():
    """Bakker et al.: papers are retracted while reviews are in press, so status is
    a function of the date you check. A later notice did not exist yet."""
    check = s.make_check_formal_notice(
        lambda w: {"publication_types": ["Retracted Publication"],
                   "notice_date": "2025-06-01", "id": w})
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


@pytest.mark.parametrize("cap", [0, -1, True, 1.5])
def test_candidate_cap_rejects_nonpositive_or_noninteger_values(cap):
    with pytest.raises(ValueError, match="positive integer"):
        s.make_retrieve_superseding_candidates(lambda *_a, **_k: [], cap=cap)


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
            "directional_contradiction": True,
            "relation_to_cited_finding": "opposes", "claim_match": "match",
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
            "directional_contradiction": False,
            "relation_to_cited_finding": "uncertain",
            "claim_match": "uncertain",
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


def test_live_judge_transport_rejects_duplicate_keys_before_resolution():
    import json
    cited = f5.ComparabilitySource(abstract="Earlier result.")
    candidate = f5.ComparabilitySource(results="Later opposite result.")
    body = {
        "directional_contradiction": True,
        "relation_to_cited_finding": "opposes",
        "claim_match": "match", "outcome_relation": "same",
        "population_relation": "equivalent",
        "cited_direction": "increase", "candidate_direction": "decrease",
        "magnitude": "large", "confidence": 0.9,
        "scope_mismatch_axis": "none",
        "cited_finding_span": {"label": "abstract", "sentence_ids": ["s1"]},
        "candidate_contradiction_span": {
            "label": "results", "sentence_ids": ["s1"]},
    }
    raw = json.dumps(body).replace(
        '"relation_to_cited_finding": "opposes",',
        '"relation_to_cited_finding": "confirms", '
        '"relation_to_cited_finding": "opposes",')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        s.make_judge_contradiction(lambda _prompt: raw)(
            cited, candidate, "claim")


def _cacheable_sources():
    cited = f5.ComparabilitySource(
        abstract="Earlier result.", packet_sha256="a" * 64)
    candidate = f5.ComparabilitySource(
        results="Later opposite result.", packet_sha256="b" * 64)
    return cited, candidate


def _cacheable_response():
    import json
    return json.dumps({
        "directional_contradiction": True,
        "relation_to_cited_finding": "opposes",
        "claim_match": "match", "outcome_relation": "same",
        "population_relation": "equivalent",
        "cited_direction": "increase", "candidate_direction": "decrease",
        "magnitude": "large", "confidence": 0.9,
        "scope_mismatch_axis": "none",
        "cited_finding_span": {
            "label": "abstract", "sentence_ids": ["s1"]},
        "candidate_contradiction_span": {
            "label": "results", "sentence_ids": ["s1"]},
    })


def test_pairwise_cache_reuses_only_the_same_bound_request():
    calls = []

    def complete(prompt):
        calls.append(prompt)
        return _cacheable_response()

    cited, candidate = _cacheable_sources()
    judge = s.make_judge_contradiction(
        complete, judgment_cache={}, model_id="model-1",
        model_settings={"temperature": 0})
    first = judge(cited, candidate, "claim")
    second = judge(cited, candidate, "claim")
    assert first == second
    assert (judge.calls, judge.model_calls, judge.cache_hits) == (2, 1, 1)
    assert len(calls) == 1

    changed_candidate = f5.ComparabilitySource(
        results=candidate.results, packet_sha256="c" * 64)
    judge(cited, changed_candidate, "claim")
    judge(cited, candidate, "different claim")
    assert (judge.calls, judge.model_calls, judge.cache_hits) == (4, 3, 1)


def test_malformed_judgment_is_never_cached_and_is_retried():
    replies = iter(("not json", _cacheable_response()))
    cited, candidate = _cacheable_sources()
    judge = s.make_judge_contradiction(
        lambda _prompt: next(replies), judgment_cache={}, model_id="model-1",
        model_settings={"temperature": 0})
    with pytest.raises(Exception):
        judge(cited, candidate, "claim")
    assert f5._parse_contradiction(judge(cited, candidate, "claim"))
    assert (judge.calls, judge.model_calls, judge.cache_hits) == (2, 2, 0)


def test_pairwise_cache_single_flights_concurrent_identical_requests():
    import concurrent.futures
    import threading
    import time

    call_lock = threading.Lock()
    model_calls = 0

    def complete(_prompt):
        nonlocal model_calls
        with call_lock:
            model_calls += 1
        time.sleep(0.03)
        return _cacheable_response()

    cited, candidate = _cacheable_sources()
    judge = s.make_judge_contradiction(
        complete, judgment_cache={}, model_id="model-1",
        model_settings={"temperature": 0})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(judge, cited, candidate, "claim") for _ in range(2)]
        results = [future.result() for future in futures]
    assert results[0] == results[1]
    assert model_calls == 1
    assert (judge.calls, judge.model_calls, judge.cache_hits) == (2, 1, 1)


def test_pairwise_cache_tracks_mutated_settings_in_the_request_key():
    calls = []
    settings = {"temperature": 0}
    cited, candidate = _cacheable_sources()
    judge = s.make_judge_contradiction(
        lambda prompt: calls.append(prompt) or _cacheable_response(),
        judgment_cache={}, model_id="model-1", model_settings=settings)
    judge(cited, candidate, "claim")
    settings["temperature"] = 1
    judge(cited, candidate, "claim")
    assert len(calls) == 2
    assert (judge.model_calls, judge.cache_hits) == (2, 0)


def test_shared_cache_single_flights_across_distinct_wrappers():
    import concurrent.futures
    import threading
    import time

    cache = {}
    call_lock = threading.Lock()
    model_calls = 0

    def complete(_prompt):
        nonlocal model_calls
        with call_lock:
            model_calls += 1
        time.sleep(0.03)
        return _cacheable_response()

    cited, candidate = _cacheable_sources()
    kwargs = dict(judgment_cache=cache, model_id="model-1",
                  model_settings={"temperature": 0})
    first = s.make_judge_contradiction(complete, **kwargs)
    second = s.make_judge_contradiction(complete, **kwargs)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(first, cited, candidate, "claim"),
                   pool.submit(second, cited, candidate, "claim")]
        results = [future.result() for future in futures]
    assert results[0] == results[1]
    assert model_calls == 1
    assert first.model_calls + second.model_calls == 1
    assert first.cache_hits + second.cache_hits == 1


def test_unresolved_sentence_ids_are_not_cached_as_absent_evidence():
    import json

    invalid = json.loads(_cacheable_response())
    invalid["candidate_contradiction_span"]["sentence_ids"] = ["s99"]
    replies = iter((json.dumps(invalid), _cacheable_response()))
    cited, candidate = _cacheable_sources()
    judge = s.make_judge_contradiction(
        lambda _prompt: next(replies), judgment_cache={}, model_id="model-1",
        model_settings={"temperature": 0})
    first = f5._parse_contradiction(judge(cited, candidate, "claim"))
    second = f5._parse_contradiction(judge(cited, candidate, "claim"))
    assert first.candidate_contradiction_span == ""
    assert second.candidate_contradiction_span == "Later opposite result."
    assert (judge.model_calls, judge.cache_hits) == (2, 0)


# ==========================================================================
# Item 4 -- the discovery queue
# ==========================================================================

def _records():
    return [{
        "claim_index": 0, "claim_text": "Metformin reduces HbA1c",
        "activation": {"applicability": "eligible"},
        "cited_work_id": "W1", "cited_date": "2015-01-01",
        "cited_source_packet_sha256": "a" * 64,
        "controversy_bundle_sha256": "b" * 64,
        "search_complete": False,
        "controversy_bundle": {
            "human_review_reason": "Review the complete controversy evidence"},
        "temporal_state": "QUALIFYING_CONTRADICTION", "proposed_route": "F5",
        "candidate_assessments": [
            {"candidate_work_id": "W2", "candidate_date": "2021-01-01",
             "discovery_disposition": "surface", "scope_mismatch_axis": "none",
             "reason": "directional_contradiction", "confidence": 0.9,
             "candidate_source_packet_sha256": "c" * 64,
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
                "cited_finding_span", "candidate_contradiction_span",
                "cited_source_packet_sha256",
                "candidate_source_packet_sha256",
                "controversy_bundle_sha256", "human_review_reason"):
        assert row.get(key) is not None
    assert row["search_complete"] is False
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


def test_flagged_only_claim_gets_a_blind_direct_bundle_reference():
    record = _records()[0]
    record["candidate_assessments"] = [
        dict(record["candidate_assessments"][2])]
    queue = q.build_queue([record])
    assert len(queue) == 1
    assert queue[0]["row_kind"] == "controversy_bundle_reference"
    assert queue[0]["candidate_work_id"] is None
    assert queue[0]["controversy_bundle_sha256"] == "b" * 64
    q.assert_blind(queue)


def test_failed_empty_search_gets_a_blind_bundle_reference_warning():
    record = _records()[0]
    record["candidate_assessments"] = []
    record["search_complete"] = False
    queue = q.build_queue([record])
    assert len(queue) == 1
    assert queue[0]["row_kind"] == "controversy_bundle_reference"
    assert queue[0]["search_complete"] is False
    q.assert_blind(queue)


def test_not_applicable_empty_claim_never_enters_f5_queue():
    record = _records()[0]
    record["candidate_assessments"] = []
    record["search_complete"] = False
    record["activation"] = {"applicability": "not_applicable"}
    assert q.build_queue([record]) == []


def test_the_queue_has_its_own_filename_not_the_shared_annotation_queue():
    """judgment_band_annotation_queue.jsonl is written by two entry points and 24
    assertions pin its contents, 8 of them asserting it is EMPTY."""
    assert q.QUEUE_FILENAME == "f5_discovery_queue.jsonl"
    assert "judgment_band" not in q.QUEUE_FILENAME
