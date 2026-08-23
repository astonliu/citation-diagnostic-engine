"""What F5 had to prove before Phase 2 was allowed to run parallel.

``F5SeamBundle.thread_safe`` used to be a class-level ``False``, so wiring F5 at
all pinned F3/F4/F5/F7 to one thread -- 1,720 s of one measured run's 3,789 s of
busy time. The claim is now EARNED, and these tests are the earning:

* the bundle declares thread safety only when the two seams that reach a model
  declare it, and a missing verifier is not a pass;
* the declaration survives the manifest instrumentation, which is the layer the
  Phase 2 gate actually reads;
* the counters that feed the F5 manifest block do not lose increments under
  threads, because ``fn.calls += 1`` on a function attribute is a
  read-modify-write;
* the audit logs reach a deterministic order, because arrival order stops being
  reference order the moment the workers overlap;
* and a wired, thread-safe F5 actually flips ``phase2_parallel`` while a bundle
  that makes no claim still keeps Phase 2 serial.

No paid call and no network: every seam is a stub.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from . import f5_seams as f5s
from . import judgment_run as jr
from .f5_notice import NoticeStatus
from .f5_supersession import (
    ComparabilitySource, EvidenceTier, RetrievalResult,
)
from .schema import ClaimedRef, Reference


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _transport(reply="{}", *, thread_safe=None):
    def complete(_prompt):
        return reply
    if thread_safe is not None:
        complete.thread_safe = thread_safe
    return complete


def _bundle(*, generator_safe=None, verifier_safe=None, with_verifier=True,
            fetch_abstract=None, **kwargs):
    return f5s.build_f5_seams(
        fetch_meta=lambda work_id: {
            "id": work_id, "title": f"Title {work_id}",
            "pub_date": "2015-01-01", "pub_date_latest": "2015-01-01",
            "pub_date_precision": "day", "publication_types": ["Journal Article"]},
        fetch_abstract=(fetch_abstract
                        or (lambda work_id: f"Abstract {work_id}.")),
        search_candidates=lambda *a, **k: [],
        complete=_transport(thread_safe=generator_safe),
        verifier_complete=(_transport("verified", thread_safe=verifier_safe)
                           if with_verifier else None),
        **kwargs)


# --------------------------------------------------------------------------
# the declaration
# --------------------------------------------------------------------------
def test_a_transport_that_declares_nothing_leaves_the_bundle_unsafe():
    bundle = _bundle()
    assert isinstance(bundle, f5s.F5SeamBundle)
    assert bundle.thread_safe is False


def test_both_model_seams_must_declare_it():
    assert _bundle(generator_safe=True, verifier_safe=True).thread_safe is True
    assert _bundle(generator_safe=True, verifier_safe=False).thread_safe is False
    assert _bundle(generator_safe=False, verifier_safe=True).thread_safe is False


def test_an_unwired_verifier_is_not_a_pass():
    # An absent seam has made no claim. Phase 2 does not get to assume one for
    # it, however safe the generator is.
    bundle = _bundle(generator_safe=True, with_verifier=False)
    assert bundle["verify_contradiction"] is None
    assert bundle.thread_safe is False


def test_the_declaration_survives_manifest_instrumentation():
    # _instrument_f5_seams used to return a plain dict, which silently dropped
    # the attribute the Phase 2 gate reads.
    bundle = _bundle(generator_safe=True, verifier_safe=True)
    wrapped, _observed = jr._instrument_f5_seams(bundle)
    assert wrapped.thread_safe is True
    assert getattr(jr._instrument_f5_seams(_bundle())[0],
                   "thread_safe", False) is False


def test_a_plain_injected_dict_still_makes_no_claim():
    wrapped, _observed = jr._instrument_f5_seams(dict(_bundle(
        generator_safe=True, verifier_safe=True)))
    assert getattr(wrapped, "thread_safe", False) is False


# --------------------------------------------------------------------------
# the counters
# --------------------------------------------------------------------------
def _sources():
    cited = ComparabilitySource(abstract="Drug X reduced outcome Y.",
                                work_id="111", packet_sha256="a" * 64)
    candidate = ComparabilitySource(abstract="Drug X did not reduce outcome Y.",
                                    work_id="222", packet_sha256="b" * 64)
    return cited, candidate


_JUDGE_REPLY = json.dumps({
    "directional_contradiction": True,
    "relation_to_cited_finding": "opposes", "claim_match": "match",
    "outcome_relation": "same", "population_relation": "equivalent",
    "cited_direction": "decrease", "candidate_direction": "no_effect",
    "magnitude": "large", "scope_mismatch_axis": "none",
    "cited_finding_span": {"label": "abstract", "sentence_ids": ["s1"]},
    "candidate_contradiction_span": {"label": "abstract",
                                     "sentence_ids": ["s1"]},
    "confidence": 0.9,
})


def test_concurrent_judging_loses_no_counts_and_the_tallies_reconcile():
    cited, candidate = _sources()
    judge = f5s.make_judge_contradiction(
        _transport(_JUDGE_REPLY, thread_safe=True),
        judgment_cache={}, model_id="test-model",
        model_settings={"max_tokens": 8})
    calls = 200
    with ThreadPoolExecutor(max_workers=8) as pool:
        # Distinct claims for half the calls and one repeated claim for the
        # rest, so the run exercises the cache-hit counter as well as the
        # single-flight path.
        claims = [f"claim {index % 4}" for index in range(calls)]
        list(pool.map(lambda claim: judge(cited, candidate, claim), claims))
    assert judge.calls == calls
    # Every call is either a paid model call or a cache hit; nothing is lost and
    # nothing is counted twice.
    assert judge.model_calls + judge.cache_hits == calls
    # Four distinct claims, and the single flight means each is paid for once.
    assert judge.model_calls == 4


def test_concurrent_verification_loses_no_counts():
    verifier = f5s.make_verify_contradiction(
        _transport("ok", thread_safe=True), model_id="test-model")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(verifier, [f"prompt {i}" for i in range(200)]))
    assert verifier.calls == 200
    assert verifier.model_calls == 200


def test_one_lock_is_shared_by_every_counter_in_a_bundle():
    bundle = _bundle(generator_safe=True, verifier_safe=True)
    assert bundle.counter_lock is bundle["judge_contradiction"].counter_lock
    assert bundle.counter_lock is bundle["verify_contradiction"].counter_lock


# --------------------------------------------------------------------------
# the audit logs
# --------------------------------------------------------------------------
def test_every_audit_log_reaches_one_deterministic_order():
    packets = [{"work_id": "222", "packet_sha256": "b"},
               {"work_id": "111", "packet_sha256": "z"},
               {"work_id": "111", "packet_sha256": "a"}]
    thin = ["333", "111", "222"]
    misses = [{"key": "candidate_contradiction_span", "entry": {"label": "b"}},
              {"key": "cited_finding_span", "entry": {"label": "z"}},
              {"key": "cited_finding_span", "entry": {"label": "a"}}]
    protocols = [{"date_window": {"after": "2020-01-01"}, "candidate_cap": 50},
                 {"date_window": {"after": "2010-01-01"}, "candidate_cap": 50}]
    bundle = f5s.F5SeamBundle({}, audit_logs={
        "source_packet_log": packets, "thin_source_log": thin,
        "span_miss_log": misses, "protocol_log": protocols})
    bundle.sort_audit_logs()
    assert [(row["work_id"], row["packet_sha256"]) for row in packets] == [
        ("111", "a"), ("111", "z"), ("222", "b")]
    assert thin == ["111", "222", "333"]
    assert [(row["key"], row["entry"]["label"]) for row in misses] == [
        ("candidate_contradiction_span", "b"),
        ("cited_finding_span", "a"), ("cited_finding_span", "z")]
    assert protocols[0]["date_window"]["after"] == "2010-01-01"
    # Idempotent: sorting an already sorted log is a no-op, so a resumed run
    # cannot reorder what a previous segment wrote.
    before = [json.dumps(row, sort_keys=True) for row in packets]
    bundle.sort_audit_logs()
    assert [json.dumps(row, sort_keys=True) for row in packets] == before


def test_the_thin_source_log_the_bundle_sorts_is_the_one_it_writes():
    thin = []
    bundle = _bundle(generator_safe=True, verifier_safe=True,
                     thin_source_log=thin)
    assert bundle.audit_logs["thin_source_log"] is thin
    # No caller-supplied packet log: the bundle must still own the substitute
    # list the fetch seam actually appends to, or sorting would sort nothing.
    assert bundle.audit_logs["source_packet_log"] is (
        bundle["fetch_comparability_source"].source_packet_log)


def test_a_parallel_fetch_appends_every_thin_source_exactly_once():
    thin = []
    bundle = _bundle(generator_safe=True, verifier_safe=True,
                     thin_source_log=thin,
                     fetch_abstract=lambda work_id: "",
                     retrieved_at=lambda: "2015-06-01T00:00:00+00:00")
    fetch = bundle["fetch_comparability_source"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda work_id: fetch(work_id, as_of_date="2024-01-01"),
            [str(100 + index) for index in range(64)]))
    assert len(thin) == 64
    f5s.sort_audit_log("thin_source_log", thin)
    assert thin == sorted(str(100 + index) for index in range(64))


# --------------------------------------------------------------------------
# the gate, on a real run
# --------------------------------------------------------------------------
def _ref(index):
    return Reference(
        citation_id=f"c{index}",
        citance=f"Drug X reduces outcome {index} [1].",
        claimed=ClaimedRef(claimed_pmid=str(1000 + index),
                           title=f"Cited title {index}"),
        cited_reference_marker="1", source_pmcid="PMC1", source_pmid="900",
        source_title="Citing title")


def _empty_retrieval_bundle(*, thread_safe):
    """A wired F5 that retrieves nothing: the gate is about the bundle, not F5."""
    seams = {
        "retrieve_superseding_candidates": (
            lambda cited_meta, claim, *, after_date, as_of_date:
            RetrievalResult((), "empty", "ok",
                            rationale="no candidate under this protocol")),
        "fetch_comparability_source": (
            lambda work_id, *, as_of_date: ComparabilitySource(
                abstract="unused", work_id=work_id)),
        "check_formal_notice": (
            lambda work_id, *, as_of_date: NoticeStatus(
                lookup_status="ok", source_role="no_notice_type")),
        "classify_evidence_tier": lambda meta: EvidenceTier("rct"),
        "find_supersession_attestation": (
            lambda cited_meta, claim, rid, *, as_of_date: None),
        "judge_contradiction": lambda *a, **k: _JUDGE_REPLY,
        "verify_contradiction": lambda _prompt: "unused",
    }
    return f5s.F5SeamBundle(seams, thread_safe=thread_safe)


def _run(tmp_path, monkeypatch, *, f5_seams, max_workers=4):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    refs = [_ref(index) for index in range(1, 5)]
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: refs)
    out_dir = tmp_path / "out"
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(out_dir),
        extractor=lambda sentence: [sentence.split("[")[0].strip()],
        coverage_judge=lambda claims, evidence: [
            {"established": True, "rationale": "r",
             "evidence_span": "reduced outcome"} for _ in claims],
        fetch_abstract=lambda pmid: (
            "Drug X reduced outcome 1 in a controlled study; Drug X reduces "
            "outcome 2, outcome 3 and outcome 4."),
        preband_disposition={ref.citation_id: "cleared" for ref in refs},
        model="test-model", max_workers=max_workers,
        f5_seams=f5_seams,
        f5_evidence_builder=lambda item: {
            "cited_work_id": "111",
            "cited_meta": {"authors": ["Smith"], "cited_tier": "rct",
                           "registry_ids": ["NCT00000001"]},
            "cited_date": "2010-01-01", "as_of_date": "2024-01-01"})
    rows = [json.loads(line) for line in
            (out_dir / "judgment_predictions.jsonl").read_text().splitlines()]
    return manifest, rows


def test_a_thread_safe_f5_bundle_lets_phase_2_run_parallel(tmp_path, monkeypatch):
    manifest, rows = _run(
        tmp_path, monkeypatch,
        f5_seams=_empty_retrieval_bundle(thread_safe=True))
    parallel = manifest["parallel_execution"]
    assert parallel["f5_f7_phase2_serialized"] is False
    assert parallel["phase2_workers"] == 4
    assert parallel["f5_thread_safe_parallel"] is True
    assert [row["citation_id"] for row in rows] == ["c1", "c2", "c3", "c4"]


def test_a_bundle_that_makes_no_claim_still_keeps_phase_2_serial(
        tmp_path, monkeypatch):
    manifest, _rows = _run(
        tmp_path, monkeypatch,
        f5_seams=_empty_retrieval_bundle(thread_safe=False))
    parallel = manifest["parallel_execution"]
    assert parallel["f5_f7_phase2_serialized"] is True
    assert parallel["phase2_workers"] == 1
    assert "f5_thread_safe_parallel" not in parallel


def test_parallel_phase_2_decides_exactly_what_serial_phase_2_decided(
        tmp_path, monkeypatch):
    serial, serial_rows = _run(
        tmp_path / "serial", monkeypatch,
        f5_seams=_empty_retrieval_bundle(thread_safe=False), max_workers=1)
    parallel, parallel_rows = _run(
        tmp_path / "parallel", monkeypatch,
        f5_seams=_empty_retrieval_bundle(thread_safe=True), max_workers=4)

    def decisions(rows):
        return [{key: row[key] for key in (
            "citation_id", "disposition", "findings", "coverage_verdicts",
            "f5_records", "terminal_outcome", "terminal_reason")}
            for row in rows]

    assert decisions(parallel_rows) == decisions(serial_rows)
    assert serial["f5"]["retrieval_status_counts"] == \
        parallel["f5"]["retrieval_status_counts"]


def test_the_retrieval_protocol_tally_is_drained_not_sliced():
    # Two concurrent callers reading their own "before" length would each copy
    # the same new protocols, and the manifest would report a retrieval that ran
    # once as having run twice.
    executed = []
    barrier = threading.Barrier(4)

    def retrieve(cited_meta, claim, *, after_date, as_of_date):
        executed.append({"claim": claim})
        barrier.wait(timeout=5)
        return RetrievalResult((), "empty", "ok", rationale="none")
    retrieve.executed_protocols = executed
    seams = dict(_empty_retrieval_bundle(thread_safe=True))
    seams["retrieve_superseding_candidates"] = retrieve
    wrapped, observed = jr._instrument_f5_seams(seams)
    call = wrapped["retrieve_superseding_candidates"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(
            lambda claim: call({}, claim, after_date="2010-01-01",
                               as_of_date="2024-01-01"),
            [f"claim {index}" for index in range(4)]))
    assert observed["retrieval_calls"] == 4
    assert len(observed["retrieval_protocols"]) == len(executed) == 4


# --------------------------------------------------------------------------
# the evidence store, which is where a parallel duplicate would become a
# duplicate PACKET and not merely a duplicate fetch
# --------------------------------------------------------------------------
def test_concurrent_gets_for_one_work_mint_exactly_one_packet():
    from .f5_evidence_store import F5EvidenceStore

    clock = iter(f"2015-06-01T00:00:{second:02d}+00:00" for second in range(60))
    store = F5EvidenceStore(
        fetch_metadata=lambda work_id: {
            "id": work_id, "title": f"Title {work_id}",
            "pub_date": "2015-01-01", "pub_date_latest": "2015-01-01",
            "pub_date_precision": "day", "publication_types": ["Journal Article"],
            "abstract": f"Abstract {work_id}."},
        fetch_abstract=lambda work_id: f"Abstract {work_id}.",
        classify_evidence_tier=lambda meta: EvidenceTier("rct"),
        # A DIFFERENT retrieved_at on every call, which is the whole hazard:
        # retrieved_at is part of the packet but not of the content key, so two
        # unsynchronized builders for one work would mint two packet hashes for
        # one source -- two packet rows, two judgment-cache keys, and one paid
        # comparison done twice.
        retrieved_at=lambda: next(clock))
    with ThreadPoolExecutor(max_workers=8) as pool:
        packets = list(pool.map(
            lambda _index: store.get("111", as_of_date="2024-01-01"),
            range(32)))
    assert len({packet.packet_sha256 for packet in packets}) == 1
    # 32 gets, one built packet, and every counter accounts for all 32.
    assert store.counters["metadata_calls"] == 32
    assert store.counters["abstract_calls"] == 32
    assert store.counters["cache_hits"] == 31


def test_two_works_do_not_serialize_on_each_other():
    from .f5_evidence_store import F5EvidenceStore

    entered = threading.Barrier(2, timeout=5)

    def fetch_metadata(work_id):
        # Both threads must be INSIDE get() at once or this times out: the lock
        # is per work, and a global one would deadlock this barrier.
        entered.wait()
        return {"id": work_id, "title": f"Title {work_id}",
                "pub_date": "2015-01-01", "pub_date_latest": "2015-01-01",
                "pub_date_precision": "day",
                "publication_types": ["Journal Article"]}

    store = F5EvidenceStore(
        fetch_metadata=fetch_metadata,
        fetch_abstract=lambda work_id: f"Abstract {work_id}.",
        classify_evidence_tier=lambda meta: EvidenceTier("rct"),
        retrieved_at=lambda: "2015-06-01T00:00:00+00:00")
    with ThreadPoolExecutor(max_workers=2) as pool:
        work_ids = ["111", "222"]
        packets = list(pool.map(
            lambda work_id: store.get(work_id, as_of_date="2024-01-01"),
            work_ids))
    assert [packet.work_id for packet in packets] == work_ids


# --------------------------------------------------------------------------
# the candidate screen, which also reaches a model
# --------------------------------------------------------------------------
def test_a_wired_screen_must_declare_thread_safety_too():
    from . import f5_candidate_screen as fcs

    safe = fcs.make_candidate_screen(_transport("{}", thread_safe=True))
    unsafe = fcs.make_candidate_screen(_transport("{}"))
    assert safe.thread_safe is True
    assert unsafe.thread_safe is False

    base = _bundle(generator_safe=True, verifier_safe=True)
    assert base.thread_safe is True
    with_safe = dict(base, screen_candidates=safe)
    with_unsafe = dict(base, screen_candidates=unsafe)
    assert f5s.f5_seams_thread_safe(with_safe) is True
    # A screen that makes no claim is not a screen Phase 2 may run concurrently.
    assert f5s.f5_seams_thread_safe(with_unsafe) is False
    # An ABSENT screen is not an unanswered question: the seam is optional.
    assert f5s.f5_seams_thread_safe(dict(base, screen_candidates=None)) is True


def test_the_screen_counts_every_call_under_concurrency():
    from . import f5_candidate_screen as fcs
    from .f5_supersession import CandidateWork

    def reply(prompt):
        # One row per handle the prompt offered, echoed back as plausible.
        handles = [line[1:line.index("]")] for line in prompt.splitlines()
                   if line.startswith("[c")]
        return json.dumps({"screened": [
            {"candidate": handle, "decision": "plausible",
             "claim_relevance": "match", "possible_relation": "uncertain",
             "missing_facts": []}
            for handle in handles]})

    reply.thread_safe = True
    screen = fcs.make_candidate_screen(reply)
    candidates = [CandidateWork(
        id="222", pub_date="2020-01-01", authors=("Jones",), tier_hint="rct",
        title="A later trial", abstract="Later results.")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(
            lambda index: screen(claim=f"claim {index}", candidates=candidates),
            range(120)))
    assert screen.calls == 120
    assert len(screen.render_log) == 120
