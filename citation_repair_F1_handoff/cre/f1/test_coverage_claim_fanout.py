"""The per-claim coverage loop, overlapped -- and what that must not change.

Coverage was 126 calls and 1,605 s, 42% of one measured run's busy time, and
every claim in ``make_coverage_judge_v3``'s loop is independent: same sections,
same id map, one prompt each. Passing a ``claim_executor`` dispatches them
together.

The whole safety argument is that NOTHING ELSE MOVES. The prompts are the same
bytes, the verdicts come back in claim order with the same content, the same
malformed reply raises the same error for the same claim, and the paid-call meter
-- which is thread-local by design, read as a per-thread delta by
``judgment_run`` -- still counts every attempt on the thread that judged the
record. These tests are those four claims.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from . import coverage_prompts_v3 as v3
from .anthropic_transport import CACHE_BREAK_MARKER, split_cache_blocks


SECTIONS = [
    {"label": "results", "title": "Results",
     "text": "Drug X reduced infarct size. Mortality was unchanged.",
     "content_sha256": "unused"},
    {"label": "methods", "title": "Methods",
     "text": "Rats were randomized to Drug X or vehicle.",
     "content_sha256": "unused"},
]
CLAIMS = [f"Claim number {index}" for index in range(1, 13)]


def _evidence(sections=None, *, complete=True):
    sections = SECTIONS if sections is None else sections
    return {"cited_fulltext": {
        "pmid": "1", "pmcid": "PMC9", "resolved": True, "sections": sections,
        "sections_present": sorted({s["label"] for s in sections}),
        "retrieval_complete": complete, "incomplete_reasons": [],
        "sanitized_paths": [], "source": "live"}}


def _reply(claim):
    """A reply that carries its own claim, so a swapped verdict is visible."""
    return json.dumps({
        "engages_subject": True, "contradicts": False,
        "unconfirmed_specifics": [], "rationale": f"about {claim}",
        "evidence_spans": [{"label": "results", "sentence_ids": ["s1"]}]})


def _claim_of(prompt):
    """The claim a rendered prompt carries, marker and all removed."""
    tail = prompt.split("ATOMIC CLAIM\n", 1)[1]
    return tail.replace(CACHE_BREAK_MARKER, "", 1).split("\n", 1)[0]


def _transport(*, delay_of=lambda claim: 0.0, seen=None, lock=None):
    """A stub that answers from the CLAIM the prompt carries, not from order."""
    def call(prompt):
        claim = _claim_of(prompt)
        if seen is not None:
            with lock:
                seen.append(prompt)
        # Answer the later claims first, so an implementation that trusted
        # completion order would return verdicts in the wrong order.
        time_to_wait = delay_of(claim)
        if time_to_wait:
            threading.Event().wait(time_to_wait)
        return _reply(claim)
    return call


def _pool():
    return ThreadPoolExecutor(max_workers=4, thread_name_prefix="claim-test")


# -- the equivalence -------------------------------------------------------
def test_overlapped_claims_return_the_serial_verdicts_in_the_serial_order():
    serial = v3.make_coverage_judge_v3(_transport())(CLAIMS, _evidence())
    with _pool() as pool:
        overlapped = v3.make_coverage_judge_v3(
            # Reverse-order delays: claim 12 answers first, claim 1 last.
            _transport(delay_of=lambda claim: 0.004 * (
                len(CLAIMS) - int(claim.split()[-1]))),
            claim_executor=pool)(CLAIMS, _evidence())
    assert overlapped == serial
    assert [verdict["rationale"] for verdict in overlapped] == [
        f"about {claim}" for claim in CLAIMS]


def test_the_prompts_are_the_same_bytes_in_the_same_order():
    serial_seen, overlapped_seen = [], []
    lock = threading.Lock()
    v3.make_coverage_judge_v3(_transport(seen=serial_seen, lock=lock))(
        CLAIMS, _evidence())
    with _pool() as pool:
        v3.make_coverage_judge_v3(
            _transport(seen=overlapped_seen, lock=lock),
            claim_executor=pool)(CLAIMS, _evidence())
    assert sorted(overlapped_seen) == sorted(serial_seen)
    assert len(overlapped_seen) == len(CLAIMS)


def test_the_calls_actually_overlap():
    active = peak = 0
    lock = threading.Lock()

    def call(prompt):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threading.Event().wait(0.01)
        with lock:
            active -= 1
        return _reply("x")

    with _pool() as pool:
        v3.make_coverage_judge_v3(call, claim_executor=pool)(
            CLAIMS, _evidence())
    assert peak >= 2


# -- the meter -------------------------------------------------------------
def test_every_attempt_is_billed_on_the_calling_thread():
    with _pool() as pool:
        judge = v3.make_coverage_judge_v3(_transport(), claim_executor=pool)
        before = judge.paid_call_meter.count()
        judge(CLAIMS, _evidence())
        # The meter is THREAD-LOCAL and judgment_run reads it as a delta on this
        # thread. A worker bumping it would leave this delta at zero and the
        # record would report free coverage.
        assert judge.paid_call_meter.count() - before == len(CLAIMS)


def test_a_failing_reply_still_bills_the_calls_that_were_made():
    def call(prompt):
        if prompt.rstrip().endswith("Claim number 3") or "Claim number 3" in prompt:
            return "not json"
        return _reply("x")

    with _pool() as pool:
        judge = v3.make_coverage_judge_v3(call, claim_executor=pool)
        with pytest.raises(ValueError):
            judge(CLAIMS, _evidence())
        # Dispatched is paid, whatever the reply was.
        assert judge.paid_call_meter.count() == len(CLAIMS)


def test_the_first_failure_in_claim_order_is_the_one_raised():
    def call(prompt):
        claim = _claim_of(prompt)
        index = int(claim.split()[-1])
        if index in (4, 9):
            raise ValueError(f"reply for {claim} is unusable")
        return _reply(claim)

    with _pool() as pool:
        judge = v3.make_coverage_judge_v3(call, claim_executor=pool)
        with pytest.raises(ValueError, match="Claim number 4"):
            judge(CLAIMS, _evidence())


def test_an_executor_without_submit_is_refused_at_build_time():
    with pytest.raises(ValueError, match="submit"):
        v3.make_coverage_judge_v3(_transport(), claim_executor=object())


def test_the_fail_closed_empty_evidence_path_still_makes_no_call():
    called = []
    with _pool() as pool:
        judge = v3.make_coverage_judge_v3(
            lambda prompt: called.append(prompt) or _reply("x"),
            claim_executor=pool)
        out = judge(CLAIMS, _evidence(sections=[]))
    assert called == []
    assert len(out) == len(CLAIMS)
    assert all(verdict["established"] is None for verdict in out)
    assert judge.paid_call_meter.count() == 0


# -- the cache breakpoint the coverage prompt now carries -------------------
def test_the_coverage_prompt_carries_exactly_one_marker():
    assert v3.COVERAGE_PROMPT_V3.count(CACHE_BREAK_MARKER) == 1


def test_the_split_coverage_prompt_is_byte_exact():
    prompt = v3.render_prompt("A claim", SECTIONS)
    blocks = split_cache_blocks(prompt)
    assert isinstance(blocks, list) and len(blocks) == 2
    assert "".join(block["text"] for block in blocks) == prompt.replace(
        CACHE_BREAK_MARKER, "")
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[1]


def test_the_cached_head_is_the_static_header_and_holds_no_claim():
    head = split_cache_blocks(v3.render_prompt("A claim", SECTIONS))[0]["text"]
    assert head.endswith("ATOMIC CLAIM\n")
    assert "A claim" not in head
    assert "Rats were randomized" not in head


def test_two_claims_and_two_papers_share_the_identical_head():
    other = [{"label": "results", "title": "Results",
              "text": "A different paper entirely.",
              "content_sha256": "unused"}]
    heads = {
        split_cache_blocks(v3.render_prompt(claim, sections))[0]["text"]
        for claim, sections in (("A claim", SECTIONS), ("Another", SECTIONS),
                                ("A claim", other))}
    assert len(heads) == 1


def test_a_marker_inside_untrusted_text_cannot_break_the_split():
    # The claim comes from a model and the sections from a fetched paper. A
    # second marker would make the adapter refuse the prompt and quarantine the
    # whole reference, so the marker is removed from both values.
    hostile = [{"label": "results", "title": "Results",
                "text": f"Sneaky {CACHE_BREAK_MARKER} text.",
                "content_sha256": "unused"}]
    prompt = v3.render_prompt(f"claim {CACHE_BREAK_MARKER} here", hostile)
    assert prompt.count(CACHE_BREAK_MARKER) == 1
    blocks = split_cache_blocks(prompt)
    assert len(blocks) == 2
    assert CACHE_BREAK_MARKER not in "".join(b["text"] for b in blocks)


def test_the_marker_never_reaches_the_model_through_the_judge():
    seen = []
    judge = v3.make_coverage_judge_v3(
        lambda prompt: seen.append(prompt) or _reply("x"))
    judge(["A claim"], _evidence())
    # This is the defect change 4 fixes elsewhere: the marker is only ever
    # removed by the adapter, so a transport that does not call it transmits the
    # literal. Here the prompt still carries it and the SPLIT is what removes it.
    assert seen[0].count(CACHE_BREAK_MARKER) == 1
    assert CACHE_BREAK_MARKER not in "".join(
        block["text"] for block in split_cache_blocks(seen[0]))


# -- the measurement the deferred body cache needs --------------------------
def test_the_body_log_records_claims_per_cited_body():
    judge = v3.make_coverage_judge_v3(_transport())
    other = [{"label": "results", "title": "Results",
              "text": "A different paper entirely.", "content_sha256": "unused"}]
    judge(CLAIMS[:3], _evidence())
    judge(CLAIMS[3:5], _evidence())          # same body, two more claims
    judge(CLAIMS[:4], _evidence(sections=other))
    rows = judge.body_log
    assert [row["claims"] for row in rows] == [3, 2, 4]
    assert len({row["body_sha256"] for row in rows}) == 2
    # 9 claims over 2 distinct bodies: the fan-out the saving is a function of.
    assert sum(row["claims"] for row in rows) == 9


def test_the_body_log_is_not_written_when_no_call_is_made():
    judge = v3.make_coverage_judge_v3(_transport())
    judge(CLAIMS, _evidence(sections=[]))
    assert judge.body_log == []


def test_the_body_log_survives_concurrent_references():
    with _pool() as pool:
        judge = v3.make_coverage_judge_v3(_transport(), claim_executor=pool)
        with ThreadPoolExecutor(max_workers=6) as outer:
            list(outer.map(lambda index: judge(CLAIMS[:2], _evidence()),
                           range(24)))
    assert len(judge.body_log) == 24
    assert sum(row["claims"] for row in judge.body_log) == 48
