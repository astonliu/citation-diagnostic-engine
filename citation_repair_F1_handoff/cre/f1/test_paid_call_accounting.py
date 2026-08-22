"""Per-record paid-call accounting: every billed stage, or an explicit gap.

WHY THIS FILE EXISTS. Before it, NOTHING in the suite referenced
``rec["paid_calls"]`` -- not one assertion in 2700 tests -- which is exactly how
the ledger came to book only one of seven stages while its own docstring claimed
"EVERY attempt". ``_count_paid_call`` had two live call sites, both
``"claim_extraction"``; the third sat in ``_run_with_retry``, which nothing
calls. A single F7 packet reported ``total: 1`` against seven paid calls. The
manifest's ``paid_calls.total_attempts`` sums these per-record ledgers, so the
undercount was not a budgeting annoyance but a hole in a provenance artifact.

WHAT IS PINNED HERE. That coverage is billed PER CLAIM and not per seam call;
that the calls made before a mid-loop failure are still billed; that the
zero-call deterministic paths bill nothing; that F3 and F4 are separable even
though they share one transport; that generator and verifier are separable; that
an uncounted stage is NAMED rather than silently reported as zero; and -- the one
that would be a silent fail-open -- that metering does not invent distinctness
between a generator and a verifier that are the same object.
"""
from __future__ import annotations

import json
import threading

import pytest

from . import judgment_band as jb
from . import judgment_run as jr
from . import test_f7_entity as f7t
from .coverage_aggregate import make_coverage_judge
from .f7_entity import F7Policy
from .recording_adapter import PaidCallMeter, paid_call_meter
from .test_f7_orchestrator_wiring import ORIGINATES, builder, seams
from .test_judgment_run import (
    CLEARED,
    abstract_missing,
    abstract_ok,
    disc_llm,
    extractor_of,
    f4_json,
    judge_established,
    make_ref,
    run,
)

CLAIM_A = "Drug X reduces outcome Y."
CLAIM_B = "Drug X halves outcome Y."


def _coverage_reply(*, engages=True, contradicts=False, unconfirmed=None,
                    rationale="supported", evidence_span="span"):
    return json.dumps({
        "engages_subject": engages,
        "contradicts": contradicts,
        "unconfirmed_specifics": unconfirmed or [],
        "rationale": rationale,
        "evidence_span": evidence_span,
    })


def _counting_transport(*replies):
    """A call_llm that answers in order and records how many times it was asked."""
    def call(_prompt):
        call.asked += 1
        index = min(call.asked - 1, len(replies) - 1)
        reply = replies[index]
        if isinstance(reply, Exception):
            raise reply
        return reply
    call.asked = 0
    return call


def _coverage_pair(*, claims, transport, fetch_abstract=abstract_ok):
    """One item through the REAL v2 coverage judge, returning the record."""
    item = jb.build_item(make_ref("c"))
    return jr.judge_pair(
        item,
        extractor=extractor_of(*claims),
        coverage_judge=make_coverage_judge(transport),
        fetch_abstract=fetch_abstract)


def _ledger(rec) -> dict:
    return rec.get("paid_calls") or {}


def _stage(rec, name) -> int:
    return int((_ledger(rec).get("by_stage") or {}).get(name) or 0)


# ==========================================================================
# THE METER -- thread-local by construction, which is the whole design
# ==========================================================================
def test_the_meter_is_thread_local_so_workers_cannot_steal_each_others_calls():
    """One coverage judge is built per RUN and shared by every worker in
    run_natural_judgment's pool. A shared integer would hand one record another
    thread's calls -- the same defect the pool's own tally refuses. Each thread
    must see only what it spent."""
    meter = PaidCallMeter()
    seen = {}
    barrier = threading.Barrier(3)

    def worker(name, bumps):
        barrier.wait()          # force real overlap, not sequential execution
        for _ in range(bumps):
            meter.bump()
        seen[name] = meter.count()

    threads = [threading.Thread(target=worker, args=(n, b))
               for n, b in (("a", 5), ("b", 11))]
    for t in threads:
        t.start()
    barrier.wait()
    for t in threads:
        t.join()

    assert seen == {"a": 5, "b": 11}
    # The main thread bumped nothing, so it sees nothing -- not the other 16.
    assert meter.count() == 0


def test_a_callable_with_no_meter_reports_none_and_not_zero():
    """None and 0 are different facts: one is 'unknown', the other is 'free'."""
    assert paid_call_meter(lambda _p: "") is None
    assert paid_call_meter(make_coverage_judge(lambda _p: "")) is not None
    # A non-meter object parked on the attribute is not a meter.
    stub = lambda _p: ""
    stub.paid_call_meter = 7
    assert paid_call_meter(stub) is None


# ==========================================================================
# COVERAGE -- billed PER CLAIM, which is the undercount that started this
# ==========================================================================
def test_coverage_books_one_call_per_claim_not_one_per_seam_invocation():
    """jb.coverage_verdicts invokes the judge ONCE for all claims, but the judge
    bills one model call per claim. A receipt entry per seam call would report 1
    where 2 were paid."""
    transport = _counting_transport(_coverage_reply())
    rec = _coverage_pair(claims=[CLAIM_A, CLAIM_B], transport=transport)

    assert transport.asked == 2                  # ground truth: two replies paid
    assert _stage(rec, "coverage") == 2
    # claim extraction is still booked, and the total is the sum of the stages.
    assert _stage(rec, "claim_extraction") == 1
    assert _ledger(rec)["total"] == sum(_ledger(rec)["by_stage"].values())
    assert "unmetered_stages" not in _ledger(rec)


def test_the_zero_call_coverage_path_books_nothing():
    """No usable abstract short-circuits before the model. Nothing was paid, and
    the ledger must say 0 rather than len(claims)."""
    transport = _counting_transport(_coverage_reply())
    rec = _coverage_pair(claims=[CLAIM_A, CLAIM_B], transport=transport,
                         fetch_abstract=abstract_missing)

    assert transport.asked == 0
    assert _stage(rec, "coverage") == 0
    assert "coverage" not in (_ledger(rec).get("by_stage") or {})


def test_coverage_calls_made_before_a_mid_loop_failure_are_still_billed():
    """THE CASE NO ARITHMETIC OVER len(claims) GETS RIGHT. The judge answers
    claim 1, then returns unparseable bytes for claim 2 and raises. Two replies
    were paid for. Booking on success only would report 0; predicting from the
    claim count would report 3."""
    transport = _counting_transport(_coverage_reply(), "not json", "unreached")
    rec = _coverage_pair(claims=[CLAIM_A, CLAIM_B, "Drug X is safe."],
                         transport=transport)

    assert transport.asked == 2
    assert _stage(rec, "coverage") == 2
    # The failure is still surfaced as a stage failure; billing does not hide it.
    assert any(f["stage"] == "coverage" for f in rec["stage_failures"])


def test_an_uncounted_coverage_stage_is_named_rather_than_reported_as_zero():
    """An injected judge carrying no meter has made an UNKNOWN number of paid
    calls. Defaulting that to 0 is what made the old ledger unreadable: a reader
    could not tell a real zero from a stage nobody counted."""
    rec = jr.judge_pair(
        jb.build_item(make_ref("c")),
        extractor=extractor_of(CLAIM_A),
        coverage_judge=judge_established(True),   # a plain stub, no meter
        fetch_abstract=abstract_ok)

    assert _ledger(rec)["unmetered_stages"] == ["coverage"]
    assert "coverage" not in (_ledger(rec).get("by_stage") or {})


# ==========================================================================
# F3 / F4 -- one transport, two stages, separable only in this ledger
# ==========================================================================
def test_f4_and_f3_are_booked_separately_though_they_share_one_transport():
    """Both are `discriminator_call_llm`, so the adapter receipt cannot tell
    strength spend from provenance spend. This ledger is the only place that
    distinction exists."""
    rec = jr.judge_pair(
        jb.build_item(make_ref("c")),
        extractor=extractor_of(CLAIM_A),
        coverage_judge=make_coverage_judge(_counting_transport(_coverage_reply())),
        fetch_abstract=abstract_ok,
        discriminator_call_llm=disc_llm(f4=f4_json(), v2=ORIGINATES),
        f3_fetch_reflist=lambda _p: ([], False))

    assert _stage(rec, "F4") >= 1
    assert _stage(rec, "F3") >= 1
    assert _ledger(rec)["total"] == sum(_ledger(rec)["by_stage"].values())


# ==========================================================================
# F7 -- generator and verifier, and the guard metering must not silence
# ==========================================================================
def _f7_pair(*, policy=None, **kw):
    return jr.judge_pair(
        jb.build_item(make_ref("c")),
        extractor=extractor_of(f7t.CLAIM),
        coverage_judge=judge_established(True),
        fetch_abstract=abstract_ok,
        discriminator_call_llm=disc_llm(f4=f4_json(), v2=ORIGINATES),
        f7_evidence_builder=builder(),
        f7_policy=policy if policy is not None else f7t.policy(),
        **kw)


def test_f7_generator_and_verifier_are_booked_as_distinct_stages():
    """F7 asks the generator three times per claim (clause attribution, body
    evidence, entity tuples) and the independent verifier once. The adapter
    receipt sees those four calls but is RUN-scoped; only this ledger says what
    one record cost, and only it separates the two roles per record."""
    rec = _f7_pair(f7_seams=seams())

    assert rec["label"] == "F7"                     # the path really ran
    assert _stage(rec, "F7") == 3
    assert _stage(rec, "F7_verifier") == 1
    assert _ledger(rec)["total"] == sum(_ledger(rec)["by_stage"].values())


def test_an_f7_that_holds_without_asking_the_model_books_nothing():
    """The default policy holds UNJUDGEABLE deterministically. Nothing was
    asked, so nothing is billed -- a real zero, distinguishable from the
    'unmetered' case above because the stage is simply absent."""
    rec = _f7_pair(f7_seams=seams(), policy=F7Policy())

    assert _stage(rec, "F7") == 0
    assert _stage(rec, "F7_verifier") == 0
    assert "entity evidence is unjudgeable" in rec["hold_reasons"]


def test_metering_does_not_invent_distinctness_between_generator_and_verifier():
    """THE FAIL-OPEN THIS GUARDS. f7_entity refuses `verifier_call_llm is
    call_llm`, because a verifier that is the generator is not independent. Two
    wrappers around one object are two objects -- so metering each side
    separately would convert a configuration F7 exists to refuse into one it
    accepts, silencing the guard from underneath it."""
    shared = f7t.gen_llm()
    with pytest.raises(ValueError, match="verifier"):
        _f7_pair(f7_seams=seams(gen=shared, ver=shared))


def test_a_distinct_verifier_still_passes_the_guard_when_metered():
    """The other half of the same claim: metering must not BREAK a valid pair."""
    rec = _f7_pair(f7_seams=seams())
    assert not any(f["stage"] == "F7" for f in rec.get("stage_failures") or [])
    assert _stage(rec, "F7_verifier") == 1


# ==========================================================================
# THE LEDGER'S OWN ARITHMETIC
# ==========================================================================
def test_the_total_is_always_the_sum_of_the_stages():
    """A total that drifts from its own breakdown is worse than either number
    alone, because it gives a reader two answers and no way to choose."""
    rec = _coverage_pair(claims=[CLAIM_A, CLAIM_B],
                         transport=_counting_transport(_coverage_reply()))
    ledger = _ledger(rec)
    assert ledger["total"] == sum(ledger["by_stage"].values())
    assert ledger["retries"] == 0


# ==========================================================================
# THE MANIFEST -- the provenance artifact the per-record ledgers roll up into
# ==========================================================================
def test_the_manifest_carries_every_billed_stage_not_only_claim_extraction(
        tmp_path, monkeypatch):
    """The manifest sums the per-record ledgers, so the undercount was visible
    THERE: a reportable run's provenance block could name a total near its
    reference count while the run had paid several times that."""
    manifest, _rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of(CLAIM_A, CLAIM_B),
        coverage_judge=make_coverage_judge(
            _counting_transport(_coverage_reply())),
        disposition=CLEARED, monkeypatch=monkeypatch)

    block = manifest["paid_calls"]
    assert block["by_stage"]["coverage"] == 2
    assert block["by_stage"]["claim_extraction"] == 1
    assert block["total_attempts"] == sum(block["by_stage"].values())
    # Nothing ran uncounted, so the total is the bill and not a floor.
    assert block["unmetered"]["records"] == 0
    assert block["unmetered"]["by_stage"] == {}


def test_the_manifest_names_a_stage_that_ran_uncounted(tmp_path, monkeypatch):
    """A run wired with an unmetered judge must not publish a total that reads as
    complete. The stage is named and the affected record count is given, so
    total_attempts is legible as a floor."""
    manifest, _rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of(CLAIM_A),
        coverage_judge=judge_established(True),   # no meter
        disposition=CLEARED, monkeypatch=monkeypatch)

    block = manifest["paid_calls"]
    assert block["unmetered"]["records"] == 1
    assert block["unmetered"]["by_stage"] == {"coverage": 1}
    assert "coverage" not in block["by_stage"]


# ==========================================================================
# THE TWO COUNTERS -- reconcilable by total, NOT joinable by name
# ==========================================================================
def test_every_booked_stage_name_is_in_the_pinned_vocabulary(
        tmp_path, monkeypatch):
    """A typo'd stage string would not raise -- it would quietly open a second
    bucket and split one stage's spend across two keys, which is the silent gap
    this ledger exists to close. The vocabulary is pinned so that fails here
    rather than in a manifest nobody re-reads."""
    manifest, _rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of(CLAIM_A, CLAIM_B),
        coverage_judge=make_coverage_judge(
            _counting_transport(_coverage_reply())),
        disposition=CLEARED, monkeypatch=monkeypatch)

    booked = set(manifest["paid_calls"]["by_stage"])
    assert booked <= set(jr.PAID_CALL_STAGES), booked - set(jr.PAID_CALL_STAGES)


def test_the_ledger_and_the_receipt_are_deliberately_not_name_joinable():
    """Documented so nobody 'fixes' the divergence by renaming. The receipt
    counts SEAMS and this ledger counts STAGES: one seam (discriminator_call_llm)
    serves both F3 and F4, and separating those is the whole reason the ledger
    beats the receipt here -- renaming to seam names would throw it away. A
    cross-check compares totals or maps explicitly."""
    from .recording_adapter import RUN_SEAMS

    shared_names = set(jr.PAID_CALL_STAGES) & set(RUN_SEAMS)
    assert shared_names == set(), shared_names
    # And the seam that proves why: one transport, two stages.
    assert "discriminator_call_llm" in RUN_SEAMS
    assert {"F3", "F4"} <= set(jr.PAID_CALL_STAGES)
