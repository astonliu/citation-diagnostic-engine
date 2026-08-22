"""Natural-paper F3-F7 orchestration -- the thin layer that wires committed pieces.

This module runs *naturally occurring* PMC-OA citing papers end-to-end through the
F3-F7 band and emits ONE durable, reviewable record per citation-claim pair. It
adds NO new discriminator and invents NO advisor-locked semantics. The single
LIVE semantic discriminator is coverage -> F6; F4 (strength, generator +
positive-only verifier role) and F3 (provenance) run only when the discriminator LLM is
wired; F5 (temporal supersession) runs only when its offline seams +
evidence-builder are wired (fail-closed to UNJUDGEABLE otherwise); F7 (entity)
runs only when its offline seams + evidence-builder are supplied, otherwise the
entity seam stays empty. Unwired discriminators are never asserted (neither
positively nor as confident negatives).

Pipeline per parsed ref:
  1. exclusion_reason        (no citance / no cited pmid)         -> EXCLUDED
  2. build_item
  3. pre-band gate           (consume an F1/F2/F8 disposition)    -> EXCLUDED if not cleared;
                                                                     FAIL CLOSED if unknown
  4. assemble_evidence       (cited abstract; review reflist opt;
                              cited_fulltext when the full-text seam is wired)
  5. extract_atomic_claims + coverage_verdicts (injected LLM)     -> stage-local HOLD on ValueError
                              -- abstract scope (coverage_v2) by default, or the
                              retrieved BODY (coverage_v3, DEC-030/032) when BOTH
                              fetch_fulltext and coverage_judge_v3 are supplied
  6. type through judgment_engine (from_legacy_coverage -> decide_judgment) with
     entity from F7 when its seams are wired, else the empty seam; provenance from
     F3 when wired; temporal from F5 when its seams are wired, else UNJUDGEABLE
     (unwired seams never emit a confident negative)
  7. derive the durable disposition from route(verdicts), cross-checked against the
     engine's findings, and emit exactly one per-pair record.

F4 MODES: a distinct ``f4_verifier_call_llm`` (plus nonblank distinct model ids)
runs F4 in "formal" mode -- the only reportable configuration. Otherwise F4 runs
in "development" mode (generator-only reuse), stamped non-reportable everywhere.
Configuration is validated UP FRONT, before any output file exists: a config
defect aborts the whole run and can never be mistaken for per-pair quarantine.

TAMPER EVIDENCE + MANIFEST LIFECYCLE: every emitted prediction record is hashed
whole (pinned canonical JSON) into a link chain persisted to a sidecar
(``judgment_run_record_hashes.jsonl``); the manifest holds the chain tip +
record count and a ``status`` in {"in_progress", "complete"}, written atomically
(temp file + os.replace) during execution. ``complete`` is immutable -- further
work starts a NEW segment (fresh out_dir) chained from the frozen tip via
``chain_genesis``. Resume is allowed only from ``in_progress`` after exact chain
replay against the sidecar AND the manifest anchor; torn tails are preserved as
evidence (``judgment_run_torn_tail.jsonl``), never silently truncated.

TRUST BOUNDARY (state honestly): the chain detects mutation only while the
manifest tip is externally frozen or trusted (committed / registered elsewhere).
An attacker who can rewrite the predictions, the sidecar, AND the manifest
together defeats these local hashes: they provide integrity, not authentication.

Everything expensive (extractor, coverage_judge, fetch_abstract, review pubtype
lookup) is an INJECTED seam, so the module is fully offline-testable. The live run
(Colab, ZD-authorized) wires the host LLM and NCBI helpers; this module performs no
network or paid call itself.

RAW-RESPONSE NOTE: the band's coverage_judge contract returns parsed dicts
(established/rationale/evidence_span); the fully-verbose raw model text is retained
only if the injected judge includes a "raw" key. This module preserves whatever the
judge returns, verbatim, per claim. F4 strength records DO inline the raw
generator/verifier responses (see f4_strength).

Frozen substrate reused UNCHANGED: judgment_band.* and judgment_engine.*.
Nothing in F1/F2 is modified.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Iterable, Optional

from . import judgment_band as jb
from . import cocitation
from . import marker_scope
from . import preband_contract as pc
from .preband_contract import PrebandContractError
from .judgment_engine import (
    ClaimSupport,
    DiscriminatorContractError,
    EntityAssessment,
    EntityState,
    ProvenanceAssessment,
    ProvenanceState,
    SupportState,
    TemporalAssessment,
    TemporalState,
    decide_judgment,
    from_legacy_coverage,
)
from .parser import parse_pmc_xml
from .band_prompts import CLAIM_EXTRACT_PROMPT_VERSION, COVERAGE_PROMPT_VERSION
from .recording_adapter import paid_call_meter
from .ncbi_meta import DEFAULT_EMAIL
from .f4_strength import (
    F4Policy,
    F4_STRENGTH_PROMPT,
    F4_VERIFIER_PROMPT,
    refine_support_strength,
    validate_f4_config,
)
from .f3_provenance import (
    DEFAULT_F3_POLICY,
    F3_V2_ORIGIN_PROMPT,
    F3_V3_SELECT_PROMPT,
    F3_V4_LOOPCLOSE_PROMPT,
    make_provenance_assessor,
)
from .f5_supersession import F5Policy, F5_REPORTABLE, decide_f5
from .f7_entity import (
    PRODUCTION_F7_BUILDER_NOTE,
    PRODUCTION_F7_EVIDENCE_BUILDER,
    F7Policy,
    F7_ATTRIBUTION_PROMPT,
    F7_EVIDENCE_PROMPT,
    F7_TUPLES_PROMPT,
    F7_VERIFIER_PROMPT,
    f7_reachability,
    hold_reason_histogram,
    make_entity_assessor,
    validate_f7_record,
)
# The opt-in full-text coverage path (DEC-030/032). Both live OUTSIDE the frozen
# substrate for the same reason coverage_aggregate does: band_prompts.py cannot be
# edited without drifting its pinned blob OID.
from . import fulltext_reader as ftr
from .coverage_aggregate import no_usable_fulltext_dict
from .coverage_prompts_v3 import (
    COVERAGE_PROMPT_VERSION_V3,
    RESPONSE_PARSER_VERSION as RESPONSE_PARSER_VERSION_V3)
from .parser_versions import CLAIM_PARSER_VERSION, COVERAGE_PARSER_VERSION
# The routing layer. `disposition` says where the pipeline stopped; `terminal_outcome`
# says what we concluded about the citation. They were one field and had to stop being
# one -- see terminal_outcome.py for the two defects that forced the split.
from . import terminal_outcome as tox
from . import citation_selection as csel

#: DEC-070: the recorded value when the provider rejects the parameter. Defined
#: here (not imported from production_launcher) because judgment_run must not
#: depend on its own launcher.
TEMPERATURE_UNSUPPORTED = "unsupported"

#: What the manifest and every full-text-scoped record call the evidence scope.
#: Matches judgment_band's BAND_MODE_FULLTEXT marker so one run's two layers agree.
EVIDENCE_SCOPE_FULLTEXT = "fulltext_sections"
EVIDENCE_SCOPE_ABSTRACT = "abstract"

# --- dispositions (every pair lands in exactly one) -----------------------
DISP_EXCLUDED_NO_CITANCE = "excluded_no_citance"
DISP_EXCLUDED_NO_CITED_PMID = "excluded_no_cited_pmid"
DISP_EXCLUDED_PREBAND_MISSING = "excluded_preband_disposition_missing"
DISP_EXCLUDED_PREBAND = "excluded_preband"          # + preband_label carries the F1/F2 label
#: ABOLISHED AS A TERMINAL OUTCOME. Kept as a NAME only, so the constant a
#: consumer may still import resolves and so the string can be recognised when an
#: old prediction file is replayed. Nothing in this module assigns it any more:
#: the six faults it used to bundle are typed by
#: ``terminal_outcome.classify_parse_failure`` and routed to their own answers.
DISP_QUARANTINE_PARSE = "quarantine_parse"
#: A stage raised on this pair. The record KEEPS every earlier success -- claims,
#: evidence, coverage verdicts -- and the router terminates it UNJUDGEABLE.
DISP_HELD_STAGE_FAILURE = "held_stage_failure"
DISP_HELD_NO_CLAIMS = "held_no_atomic_claims"
DISP_HELD_CLAIM_EXTRACTION_FAILURE = "held_claim_extraction_failure"
DISP_PREDICTED = "predicted"                        # label == F6
DISP_HELD_FULL_COVERAGE = "held_full_coverage_pending_F3_F5_F7"  # legacy (discriminators unwired)
DISP_HELD_INSUFFICIENT = "held_insufficient_evidence"
# Wired-path holds (F4 + F3 live; F5/F7 live only when their seams are supplied).
DISP_HELD_PENDING_F5_F7 = "held_pending_F5_F7"            # full coverage, not overstated, proper origin
DISP_HELD_PROVENANCE_UNJUDGEABLE = "held_provenance_unjudgeable"
DISP_HELD_STRENGTH_UNJUDGEABLE = "held_strength_unjudgeable"
# CO-CITATION. The coverage route is F6_FLAGGED, but every claim this reference
# did not establish was established by a reference cited in the SAME sentence.
# Citing eight papers for one sentence is normal practice and the eight are cited
# COLLECTIVELY, so "supports part of the claim but not all of it" is not a
# statement about this reference. HELD, never PREDICTED and never a clear: this
# reference genuinely did not establish those claims, the group did, and the
# attribution is on the group record.
DISP_HELD_COCITATION_COVERED = "held_cocitation_covered"
DISP_HELD_UNSUPPORTED_COCITATION_MEMBER = "held_unsupported_cocitation_member"

# Pipeline/taxonomy labels that mean "the cited work was verified as the right,
# existing paper" -> the F3-F7 band may proceed. Everything else is out of band.
_CLEAR_LABELS = frozenset({"cleared", "accurate"})
#: WHAT THE BAND MAY JUDGE, which is wider than what F2 counts as a clear. A
#: proved same-work row is not an F2 clear and IS the right paper, so it is
#: admitted here while staying out of the F2 denominator. Testing admission
#: against `_CLEAR_LABELS` conflated the two questions and silently dropped 16
#: references from the band for a reason with no bearing on claim support.
_BAND2_ADMITTING = _CLEAR_LABELS | frozenset({"same_work"})

# Dispositions at which the pair was actually JUDGED, as opposed to excluded
# before the substrate. NO LONGER THE ANNOTATION-QUEUE GATE: the queue is filled
# from the terminal outcome (records carrying an F3-F7 finding), because a blind
# gold-label payload for `held_no_atomic_claims` asks an annotator to confirm
# "no claim here" about a citance the parser had reduced to "5,8,10,19". Retained
# as the "reached the judgment substrate" predicate and for readers that import it.
_SCOREABLE = frozenset(
    {DISP_PREDICTED, DISP_HELD_NO_CLAIMS,
     DISP_HELD_CLAIM_EXTRACTION_FAILURE,
     DISP_HELD_FULL_COVERAGE, DISP_HELD_INSUFFICIENT,
     DISP_HELD_PENDING_F5_F7, DISP_HELD_PROVENANCE_UNJUDGEABLE,
     DISP_HELD_STRENGTH_UNJUDGEABLE,
     DISP_HELD_UNSUPPORTED_COCITATION_MEMBER,
     # Scoreable: the pair was fully judged and there IS something for a human to
     # adjudicate -- whether collective support by the co-cited group is the right
     # reading of this citation. Dropping it from the queue would be the silent
     # clear the co-citation fix must never become.
     DISP_HELD_COCITATION_COVERED}
)


def _record_stage_failure(rec: dict, stage: str, exc: ValueError) -> None:
    """Keep a model/schema failure local to its taxonomy stage."""
    rec.setdefault("stage_failures", []).append({
        "stage": stage,
        "error_type": type(exc).__name__,
        "message": str(exc),
    })


def _stage_failed(rec: dict, stage: str) -> bool:
    return any(row.get("stage") == stage
               for row in rec.get("stage_failures") or [])


#: Redacted before a failed response is preserved. A raw response is kept for
#: audit -- you cannot diagnose a contract failure from a hash -- but it travels
#: through a durable artifact, so anything credential-shaped is removed on the way
#: in rather than trusted not to be there.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{8,}|api[_-]?key\"?\s*[:=]\s*\"?[A-Za-z0-9_\-]{8,}|"
    r"Bearer\s+[A-Za-z0-9._\-]{8,})", re.IGNORECASE)

#: A preserved response is evidence, not a payload. Truncated so one pathological
#: reply cannot dominate the prediction file, and the original length is recorded
#: so the truncation is visible rather than silent.
_PRESERVED_RESPONSE_LIMIT = 4000


def _preserve_failed_response(rec: dict, *, stage: str, attempt: int,
                              raw) -> None:
    """Keep the bytes that failed, with secrets stripped, for audit.

    A schema contract failure that discards the response leaves a human queue
    item nobody can act on: "the model answered wrongly" with no way to see how.
    """
    text = raw if isinstance(raw, str) else ("" if raw is None else str(raw))
    redacted = _SECRET_RE.sub("[REDACTED]", text)
    rec.setdefault("failed_model_responses", []).append({
        "stage": stage,
        "attempt": attempt,
        "response_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "response_chars": len(text),
        "truncated": len(redacted) > _PRESERVED_RESPONSE_LIMIT,
        "response": redacted[:_PRESERVED_RESPONSE_LIMIT],
    })


#: The closed vocabulary of billed stages. Pinned as a constant, and by a test,
#: because two independent counters now exist and NEITHER can be joined to the
#: other by name. ``AdapterReceipt`` counts SEAMS; this ledger counts STAGES, and
#: the two differ on purpose in both directions:
#:
#: * one seam serves two stages -- ``discriminator_call_llm`` is F3 and F4 both,
#:   and separating their spend is the main thing this ledger adds over the
#:   receipt, so renaming these keys to seam names would destroy it;
#: * one seam invocation is many calls -- ``coverage_judge_v3`` fires once per
#:   reference and bills once per claim.
#:
#: So a receipt/ledger cross-check must compare TOTALS, or map deliberately
#: (``coverage_judge``/``coverage_judge_v3`` -> ``coverage``, ``f7_generator`` ->
#: ``F7``, ``f7_verifier`` -> ``F7_verifier``, ``extractor`` ->
#: ``claim_extraction``), and must exclude the free packet- or cache-served seams
#: like ``fetch_abstract``. Joining the key sets directly would be asserting a
#: coincidence.
PAID_CALL_STAGES = (
    "claim_extraction", "coverage",
    "F3", "F4", "F4_verifier", "F5", "F5_verifier", "F7", "F7_verifier",
)


def _count_paid_call(rec: dict, stage: str, *, retry: bool) -> None:
    """Book one billed attempt against this record. EVERY attempt, retries too.

    Paid-call accounting that counts only successful attempts understates the
    bill by exactly the retries the retry budget was added to make -- so the
    counter that would justify the budget is the one the budget breaks.
    """
    ledger = rec.setdefault(
        "paid_calls", {"total": 0, "retries": 0, "by_stage": {}})
    ledger["total"] = int(ledger.get("total") or 0) + 1
    if retry:
        ledger["retries"] = int(ledger.get("retries") or 0) + 1
    by_stage = ledger.setdefault("by_stage", {})
    by_stage[stage] = int(by_stage.get(stage) or 0) + 1


def _book_paid_calls(rec: dict, stage: str, n: int) -> None:
    """Book ``n`` billed attempts against one stage."""
    for _ in range(max(0, int(n))):
        _count_paid_call(rec, stage, retry=False)


def _note_unmetered(rec: dict, stage: str) -> None:
    """Record that a stage ran through a callable carrying NO meter.

    An absent meter means the spend is UNKNOWN, which is not the same fact as
    zero, and the difference is the whole value of this ledger: a reader costing
    a run off ``paid_calls.total`` must be able to tell a real zero from a stage
    nobody counted. Only stages that actually ran are listed.
    """
    ledger = rec.setdefault(
        "paid_calls", {"total": 0, "retries": 0, "by_stage": {}})
    unmetered = ledger.setdefault("unmetered_stages", [])
    if stage not in unmetered:
        unmetered.append(stage)


def _metered(rec: dict, stage: str, call):
    """Wrap one model-shaped callable so EVERY invocation books a paid call.

    Counting invocations rather than predicting them from claim counts is the
    point: a stage that raises on claim 3 of 5 has already paid for two replies,
    and any arithmetic over ``len(claims)`` books either two calls too many or
    two too few. ``None`` passes through untouched -- an unwired seam stays
    unwired, exactly as ``AdapterReceipt.wrap_all`` treats it.

    No attribute is copied onto the wrapper because none is read: the three
    consumers (``refine_support_strength``, ``decide_f5``,
    ``make_entity_assessor``) only CALL these callables. ``model_id`` is sniffed
    off an F5 transport, but by ``make_judge_contradiction`` at BUILD time, long
    before this wrapper exists, and the F5 model ids that reach a record come
    from the policy rather than the seam.
    """
    if call is None:
        return None

    def metered(*args, **kwargs):
        _count_paid_call(rec, stage, retry=False)
        return call(*args, **kwargs)

    return metered


def _metered_pair(rec: dict, generator_stage: str, verifier_stage: str,
                  generator, verifier):
    """Meter a generator/verifier pair WITHOUT inventing distinctness.

    F5 (``verify_contradiction is judge_contradiction``) and F7
    (``verifier_call_llm is call_llm``) both REFUSE a verifier that is the
    generator, because an independent verifier that is the same callable is not
    independent. Two wrappers around one object are two objects, so wrapping
    each side separately would turn a configuration those checks exist to
    refuse into one they accept -- metering would have silenced the guard it was
    added underneath. Same object in, same object out; the merged role's calls
    then land under the generator stage, which is what actually happened.
    """
    if verifier is not None and verifier is generator:
        shared = _metered(rec, generator_stage, generator)
        return shared, shared
    return (_metered(rec, generator_stage, generator),
            _metered(rec, verifier_stage, verifier))


def _record_attempt(rec: dict, *, stage: str, attempt: int, result: str,
                    bucket: str = "", message: str = "",
                    max_tokens=None) -> None:
    """Append one line to the per-record retry ledger."""
    rec.setdefault("retry_history", []).append({
        "stage": stage,
        "attempt": attempt,
        "result": result,
        **({"failure_bucket": bucket} if bucket else {}),
        **({"message": message} if message else {}),
        **({"max_tokens": max_tokens} if max_tokens is not None else {}),
    })


def _run_with_retry(rec: dict, stage: str, call, *, larger_token_call=None):
    """Run ``call`` under the bounded retry budget its failure bucket allows.

    Returns ``(value, parse_failure_or_None)``. The budget is chosen from the
    FIRST failure's bucket, because that is what says whether asking again can
    possibly help: an empty or truncated response is worth a second ask (and a
    bigger ceiling), a deterministic hash mismatch or a semantic refusal is not,
    and spending a paid call to reproduce identical bytes is waste dressed as
    diligence.

    Every attempt is booked as a paid call, the failing bytes are preserved, and
    an exhausted budget returns a structured ``parse_failure`` the router turns
    into a terminal outcome. It never raises for a model-reply fault; structural
    and configuration errors still propagate.
    """
    attempt = 0
    bucket = ""
    last_exc = None
    max_attempts, allow_larger = 1, False
    while True:
        attempt += 1
        is_retry = attempt > 1
        _count_paid_call(rec, stage, retry=is_retry)
        larger = bool(allow_larger and is_retry and larger_token_call is not None)
        try:
            value = (larger_token_call() if larger else call())
        except ValueError as exc:
            last_exc = exc
            if attempt == 1:
                bucket = tox.classify_parse_failure(str(exc))
                max_attempts, allow_larger = tox.retry_budget(bucket)
            _record_attempt(rec, stage=stage, attempt=attempt, result="failed",
                            bucket=bucket, message=str(exc),
                            max_tokens="larger" if larger else None)
            _preserve_failed_response(
                rec, stage=stage, attempt=attempt,
                raw=getattr(exc, "raw_response", ""))
            if attempt >= max_attempts:
                return None, {
                    "stage": stage,
                    "bucket": bucket,
                    "message": str(last_exc),
                    "attempts": attempt,
                    "larger_token_retry_used": bool(allow_larger and attempt > 1),
                    "resolved": False,
                }
            continue
        _record_attempt(rec, stage=stage, attempt=attempt, result="success",
                        max_tokens="larger" if larger else None)
        if attempt > 1:
            # A retry that WORKED is still a fact about this record: it says the
            # first response was unusable and the pair cost more than one call.
            rec.setdefault("parse_failure_recovered", []).append({
                "stage": stage, "attempts": attempt,
                "bucket": bucket, "message": str(last_exc or ""),
            })
        return value, None


def _record_parse_failure(rec: dict, stage: str, exc) -> str:
    """Type a raised model/schema failure and attach it, unresolved, to ``rec``.

    ``parse_error`` is kept alongside as a plain string because existing readers
    and saved artifacts use it; the typed block is what the router reads.

    A block is attached ONLY for a RECOGNISED model-response fault. An
    unrecognised message is not evidence that the model broke its contract -- a
    misconfigured seam raises ValueError too -- and routing it to the human queue
    on that guess would fill the queue with engine configuration defects wearing a
    contract failure's name. Left untyped, it falls through to the stage-failure
    branch and terminates UNJUDGEABLE, which is what a per-pair exception is.
    """
    bucket = tox.classify_parse_failure(str(exc))
    rec["parse_error"] = str(exc)
    if bucket != tox.PARSE_UNCLASSIFIED:
        rec["parse_failure"] = {
            "stage": stage,
            "bucket": bucket,
            "message": str(exc),
            "attempts": int(
                (rec.get("paid_calls") or {}).get("by_stage", {}).get(stage) or 1),
            "resolved": False,
        }
    _preserve_failed_response(rec, stage=stage, attempt=1,
                              raw=getattr(exc, "raw_response", ""))
    return bucket


def _preserve_stage_failure(rec: dict, stage: str, exc) -> None:
    """A stage raised. KEEP the record; record the failure against the stage.

    This is the whole of the per-pair-exception rule: an exception in F3-F7 is a
    fact about that stage, not permission to delete the claims and evidence the
    earlier stages produced. The pair terminates UNJUDGEABLE with its work
    intact, so a human or a rerun has something to start from.
    """
    if not isinstance(rec, dict):
        return
    _record_stage_failure(rec, stage, exc)
    _record_parse_failure(rec, stage, exc)
    rec["disposition"] = DISP_HELD_STAGE_FAILURE
    holds = list(rec.get("hold_reasons") or [])
    holds.append(f"{stage} stage failed")
    rec["hold_reasons"] = holds
    rec["ts"] = int(time.time())
    print(f"[judgment-run-stage-failure] {rec.get('citation_id')}: "
          f"{stage}: {exc}")


#: The rationale stamped on a verdict whose absence claim was refused because the
#: body was never read. A named constant so the string is greppable in an output
#: file and cannot drift between producer and reader.
ABSTRACT_SCOPE_ABSENCE_RATIONALE = "abstract_silent_body_unread"


def _guard_abstract_scope_absence(verdicts: list) -> int:
    """AN ABSTRACT MAY NOT ASSERT ABSENCE. Coerce ``False`` -> ``None``.

    An abstract is a summary. It can ESTABLISH a claim, and it can CONTRADICT
    one -- both rest on evidence that is PRESENT. It cannot establish that a
    claim is absent from the paper, because the paper's body was never read.

    ``established=False`` maps to ``SupportState.UNESTABLISHED``
    (``judgment_engine.from_legacy_coverage``), which is what makes F6 fire. Let
    an argument from silence through at abstract scope and F6 fires by
    construction on every abstract-scope row, and the F6 precision figure stops
    meaning anything. Same defect class as DEC-030's scope stamp: a verdict must
    never be read at a scope it was not produced at.

    A CONTRADICTION IS NOT SILENCE and survives untouched. On the production
    judge this coerces ZERO rows -- ``coverage_aggregate.aggregate_coverage``
    already returns ``False`` only when ``contradicts`` is true, and maps silence
    and unconfirmed specifics to ``None``. The guard exists because the judge is
    an INJECTED seam and ``coverage_verdicts`` takes ``established`` straight from
    whatever it returns, so nothing else in the path re-derives it. It is a net
    under the seam, not a change to the honest path.

    Returns the number of verdicts coerced, so a nonzero count is visible in the
    record rather than silently applied.
    """
    coerced = 0
    for verdict in verdicts:
        if verdict.get("established") is False and (
                verdict.get("contradicts") is not True):
            verdict["established"] = None
            original = verdict.get("rationale") or ""
            verdict["rationale"] = (
                f"{ABSTRACT_SCOPE_ABSENCE_RATIONALE}"
                + (f" | {original}" if original else ""))
            coerced += 1
    return coerced


def _evidence_absence_detail(item: dict, *, fulltext_path: bool) -> dict:
    """Say WHICH retrieval produced nothing, so "unjudgeable" is diagnosable.

    "No usable evidence" with no further detail is indistinguishable from a
    transport outage, and the two need opposite responses: one is a permanent
    property of the cited work, the other is a rerun.
    """
    evidence = item.get("evidence") or {}
    abstract = evidence.get("cited_abstract")
    fulltext = evidence.get("cited_fulltext")
    resolved = item.get("resolved_identifier") or {}
    return {
        "abstract_present": bool(isinstance(abstract, str) and abstract.strip()),
        "fulltext_requested": bool(fulltext_path),
        "fulltext_retrieval_complete": bool(
            isinstance(fulltext, dict)
            and fulltext.get("retrieval_complete") is True),
        "fulltext_sections": len(
            (fulltext or {}).get("sections") or []
            if isinstance(fulltext, dict) else []),
        "cited_pmid": item.get("cited_pmid") or "",
        "resolved_identifier_kind": resolved.get("kind") or "",
        "resolved_identifier_status": resolved.get("status") or "",
    }


def _coverage_failure_verdicts(claims: list, evidence: dict, exc: ValueError,
                               *, fulltext_path: bool) -> list:
    """Produce honest UNKNOWN coverage rows after a strict reply failure."""
    rationale = f"coverage stage failed: {type(exc).__name__}: {exc}"
    if fulltext_path:
        raw = no_usable_fulltext_dict()
        raw["rationale"] = rationale
        return jb.coverage_verdicts(
            claims, evidence, judge=lambda cl, _ev: [dict(raw) for _ in cl],
            prompt_version=COVERAGE_PROMPT_VERSION_V3,
            parser_version=RESPONSE_PARSER_VERSION_V3)
    return jb.coverage_verdicts(
        claims, evidence,
        judge=lambda cl, _ev: [{
            "established": None,
            "rationale": rationale,
            "evidence_span": "",
        } for _ in cl])

_TRUST_BOUNDARY = (
    "The record hash chain detects mutation only while the manifest chain tip is "
    "externally frozen or trusted (committed/registered elsewhere). An attacker "
    "who can rewrite predictions, sidecar, and manifest together defeats these "
    "local hashes: integrity, not authentication."
)

PubtypesLookup = Callable[[str], Optional[list]]


def _sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_hex(s: str) -> bool:
    return all(c in "0123456789abcdef" for c in s)


# --------------------------------------------------------------------------
# Whole-record hash chain (item 4b). Pinned canonicalization; one link per
# emitted prediction record; sidecar + manifest anchor.
# --------------------------------------------------------------------------
def _canonical_sha256(obj) -> str:
    return hashlib.sha256(
        json.dumps(
            obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()


def _chain_link(prev_link: str, prediction_sha256: str) -> str:
    return hashlib.sha256((prev_link + prediction_sha256).encode("utf-8")).hexdigest()


def _write_json_atomic(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _write_jsonl_atomic(path: str, rows) -> None:
    """Write canonical JSONL as one atomic artifact, never a partial success."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(
                row, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _require_f5_artifact_coverage(
        f5_block: dict, *, packet_hashes, bundle_hashes,
        packet_by_hash=None, f5_records=None) -> None:
    """Every prediction-referenced F5 object must be recoverable from files."""
    missing_packets = set(f5_block.get("source_packet_hashes") or []) - set(
        packet_hashes)
    if missing_packets:
        raise ValueError(
            "F5 source packet artifact is missing prediction-referenced "
            f"packets: {sorted(missing_packets)}")
    missing_bundles = set(
        f5_block.get("controversy_bundle_hashes") or []) - set(bundle_hashes)
    if missing_bundles:
        raise ValueError(
            "F5 controversy artifact is missing prediction-referenced "
            f"bundles: {sorted(missing_bundles)}")
    if packet_by_hash is None or f5_records is None:
        return

    def require_packet_identity(work_id, packet_hash, role):
        if packet_hash is None:
            return
        packet = packet_by_hash.get(packet_hash)
        if packet is None:  # Named more specifically by the coverage check.
            return
        if packet.get("work_id") != work_id:
            raise ValueError(
                f"F5 {role} packet identity mismatch: work {work_id!r} "
                f"references packet for {packet.get('work_id')!r}")

    for record in f5_records:
        require_packet_identity(
            record.get("cited_work_id"),
            record.get("cited_source_packet_sha256"), "cited")
        for candidate in record.get("candidate_assessments") or []:
            require_packet_identity(
                candidate.get("candidate_work_id"),
                candidate.get("candidate_source_packet_sha256"), "candidate")


def _read_jsonl_lines(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n") for line in f if line.strip()]


def _preserve_torn_tail(out_dir: str, lines: list) -> str:
    path = os.path.join(out_dir, "judgment_run_torn_tail.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    return path


def _recover_chain(out_dir: str, manifest_path: str, pred_path: str,
                   sidecar_path: str, chain_genesis: str) -> tuple:
    """Validate prior state in ``out_dir``; return ``(prev_link, count, genesis)``.

    Fresh dir -> ``(chain_genesis, 0, chain_genesis)``. ``status="complete"`` is
    immutable -> raise. Any tamper / anchor mismatch / torn tail -> raise (the
    torn tail is preserved as evidence first). The manifest is the frozen
    anchor: replaying predictions against the sidecar alone is insufficient."""
    pred_lines = _read_jsonl_lines(pred_path)
    side_lines = _read_jsonl_lines(sidecar_path)
    if not os.path.exists(manifest_path):
        if pred_lines or side_lines:
            raise ValueError(
                "judgment_run: existing predictions/sidecar without a run manifest "
                "-- prior state cannot be chain-validated; refusing to append")
        return chain_genesis, 0, chain_genesis
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)
    status = manifest.get("status")
    if status == "complete":
        raise ValueError(
            "judgment_run: manifest status is 'complete' (immutable, never "
            "reopened); start a new run segment in a fresh out_dir with "
            "chain_genesis set to the frozen tip")
    if status != "in_progress":
        raise ValueError(
            f"judgment_run: unknown manifest status {status!r}; refusing to resume")
    genesis = manifest.get("chain_genesis", "")
    if chain_genesis and chain_genesis != genesis:
        raise ValueError(
            "judgment_run: chain_genesis argument conflicts with the in-progress "
            "manifest's recorded genesis")
    anchor_tip = manifest.get("chain_tip", genesis)
    anchor_count = manifest.get("chain_record_count", 0)

    if len(side_lines) > len(pred_lines):
        _preserve_torn_tail(out_dir, side_lines[len(pred_lines):])
        raise ValueError(
            "judgment_run: sidecar has more entries than predictions; unmatched "
            "tail preserved in judgment_run_torn_tail.jsonl; audited recovery "
            "required")

    # Exact replay: recompute every prediction hash + link and verify the sidecar.
    prev = genesis
    links = []
    for i, (pline, sline) in enumerate(zip(pred_lines, side_lines)):
        try:
            prec = json.loads(pline)
            srec = json.loads(sline)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"judgment_run: unparseable prediction/sidecar record {i}: {exc}"
            ) from exc
        psha = _canonical_sha256(prec)
        link = _chain_link(prev, psha)
        if (srec.get("prediction_sha256") != psha or srec.get("link") != link
                or srec.get("citation_id") != prec.get("citation_id")):
            raise ValueError(
                f"judgment_run: hash-chain validation failed at record {i} "
                f"(citation_id={prec.get('citation_id')!r}); refusing to append")
        prev = link
        links.append(link)

    # The manifest anchor must sit ON the recovered chain (it may lag the
    # sidecar by trailing not-yet-flushed manifest updates, never lead it).
    if anchor_count > len(side_lines):
        raise ValueError(
            "judgment_run: manifest chain_record_count exceeds the sidecar; "
            "unreconcilable state")
    if anchor_count > 0:
        if links[anchor_count - 1] != anchor_tip:
            raise ValueError(
                "judgment_run: recovered chain does not match the manifest tip "
                "anchor; refusing to resume")
    elif anchor_tip != genesis:
        raise ValueError(
            "judgment_run: manifest tip anchor inconsistent with zero records")

    if len(pred_lines) > len(side_lines):
        tail = pred_lines[len(side_lines):]
        _preserve_torn_tail(out_dir, tail)
        raise ValueError(
            f"judgment_run: {len(tail)} prediction line(s) beyond the validated "
            "chain (torn write); tail preserved in judgment_run_torn_tail.jsonl; "
            "audited recovery required -- never silent truncation")

    return prev, len(side_lines), genesis


def _load_disposition(preband_disposition) -> "pc.Disposition | None":
    """Load and VALIDATE the disposition; ``None`` when none was supplied.

    Delegates to ``preband_contract``. This used to be a permissive reader that
    silently skipped falsy ids, accepted any label, accepted any id shape, and
    resolved duplicates last-write-wins -- so an ``F2`` row followed by a
    ``cleared`` row for one citation_id admitted a known wrong-paper into the
    judgment band. A path is now loaded as a canonical, schema-versioned,
    digest-bound artifact; a dict remains a developer/test injection and is
    stamped non-canonical in the manifest.
    """
    return pc.load_disposition(preband_disposition)


def _preband(citation_id: str, disp: "dict | None") -> "tuple[bool, str, object]":
    """(cleared, disposition, preband_label). Fail closed: an id absent from the
    disposition is NOT cleared. A None disposition means none was supplied ->
    every pair is excluded_preband_disposition_missing (fully fail-closed)."""
    if disp is None or citation_id not in disp:
        return False, DISP_EXCLUDED_PREBAND_MISSING, None
    label = disp[citation_id]
    key = (label or "").strip().casefold() if isinstance(label, str) else ""
    if key in _BAND2_ADMITTING:
        return True, "", label
    return False, DISP_EXCLUDED_PREBAND, label


def _new_record(item: dict) -> dict:
    """Durable per-pair skeleton, keyed on citation_id == '<src_pmcid>:<ref_id>'."""
    c = item.get("cited_claimed", {})
    return {
        "citation_id": item["citation_id"],
        "citing_pmcid": item.get("citing_pmcid"),
        "citing_pmid": item.get("citing_pmid"),
        "citing_sentence": item.get("citing_sentence"),
        # WHAT THE EXTRACTOR WAS ACTUALLY SHOWN. Recorded on every record because
        # "no claims" means one thing on prose and something else entirely on a
        # stranded bibliography marker, and the durable record is the only place
        # a later reader can tell them apart.
        "claim_input_status": tox.claim_input_status(item.get("citing_sentence")),
        "cited_in_body": item.get("cited_in_body"),
        **({"citing_source_section": item["citing_source_section"]}
           if item.get("citing_source_section") else {}),
        "cited_pmid": item.get("cited_pmid"),
        "cited_claimed": c,
        "cited_is_review": item.get("cited_is_review"),
        "preband_cleared": None,
        "preband_label": None,
        "route": None,
        "disposition": None,
        "label": None,                 # taxonomy label; only F6 is emitted live
        "findings": [],
        "hold_reasons": [],
        "atomic_claims": [],
        "coverage_verdicts": [],
        "evidence": {},
        "evidence_usable": None,
        **({"sentence_partition_failures":
            list(item.get("sentence_partition_failures") or [])}
           if item.get("sentence_partition_failures") else {}),
        "strength_records": [],
        "provenance": None,
        # RETRY + PAID-CALL LEDGER. Every attempt this record cost, including the
        # ones that failed: a retry that is not counted is a paid call the run
        # accounting cannot see.
        "retry_history": [],
        "paid_calls": {"total": 0, "retries": 0, "by_stage": {}},
        "stage_failures": [],
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "coverage_prompt_version": COVERAGE_PROMPT_VERSION,
        "ts": None,
    }


def _excluded_record(ref, disposition: str, preband_label=None,
                     resolved_identifier=None) -> dict:
    """A durable record for a pair excluded before the coverage substrate."""
    c = ref.claimed
    return {
        "citation_id": ref.citation_id,
        "citing_pmcid": ref.source_pmcid,
        "citing_pmid": ref.source_pmid,
        "citing_sentence": ref.citance,
        "claim_input_status": tox.claim_input_status(ref.citance),
        # Tri-state, carried verbatim: only an explicit False licenses the
        # uncited-reference scope exclusion in the router.
        "cited_in_body": getattr(ref, "cited_in_body", None),
        "cited_pmid": c.claimed_pmid,
        "cited_claimed": {"title": c.title, "claimed_pmid": c.claimed_pmid,
                          "claimed_doi": c.claimed_doi},
        # THE IDENTIFIER BAND 1 ACTUALLY RESOLVED, carried forward verbatim with
        # its source and status. Band 2 used to re-check the CLAIMED pmid and drop
        # a reference Band 1 had already cleared through a DOI match -- 77 of them
        # on the natural run -- as `excluded_no_cited_pmid`. Never guessed: an
        # unresolved reference carries an empty value and says so.
        **({"resolved_identifier": dict(resolved_identifier)}
           if resolved_identifier else {}),
        "cited_is_review": None,
        "preband_cleared": False if disposition != DISP_EXCLUDED_NO_CITANCE
        and disposition != DISP_EXCLUDED_NO_CITED_PMID else None,
        "preband_label": preband_label,
        "route": None,
        "disposition": disposition,
        "label": None,
        "findings": [],
        "hold_reasons": [],
        "atomic_claims": [],
        "coverage_verdicts": [],
        "evidence": {},
        "evidence_usable": None,
        **({"sentence_partition_failures": list(
            getattr(ref, "citance_sentence_partition_failures", None) or [])}
           if getattr(ref, "citance_sentence_partition_failures", None) else {}),
        "retry_history": [],
        "paid_calls": {"total": 0, "retries": 0, "by_stage": {}},
        "stage_failures": [],
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "coverage_prompt_version": COVERAGE_PROMPT_VERSION,
        "ts": int(time.time()),
    }


def judge_pair_coverage(item: dict, *, extractor, coverage_judge, fetch_abstract,
                        fetch_reflist=None, fetch_fulltext=None,
                        fetch_openalex_abstract=None,
                        coverage_judge_v3=None, claims_cache=None,
                        claims_cache_order=None, scope_counts=None) -> tuple:
    """PHASE 1 of :func:`judge_pair`: evidence, atomic claims, coverage verdicts.

    Split out because the CO-CITATION GROUP a reference belongs to cannot be
    aggregated until every member of that group has its own coverage verdicts.
    The caller runs this over a whole document, computes the group coverage, then
    calls :func:`judge_pair_finish` per pair in the original order -- so the emit
    order, the record shape and the hash chain are exactly what they were.

    ``claims_cache`` is the per-document CITING-SENTENCE claim cache
    (``sentence -> claims``), mirroring ``run_band``'s.  A concurrent caller may
    instead supply the internal ordered cache plus ``claims_cache_order``.
    Atomic claims are a pure
    function of the sentence, so a group of four references sharing one citance
    made FOUR identical extraction calls on this path and one on the band path.
    That was waste at 1.30 billed calls/reference, and a latent correctness risk
    besides: any extraction drift across the four silently triggers
    ``EXCLUDED_CLAIMS_DIFFER`` and the group's coverage credit vanishes with
    nothing in the output saying so. Omitted -- the default -- each call extracts
    for itself exactly as before.

    ``scope_counts`` accumulates the MARKER ATTRIBUTION tally (see
    ``marker_scope``): the claims this reference was actually cited for, and the
    (reference, claim) pairs that were therefore never asked.

    Returns ``(rec, claims, verdicts)``. A strict model-reply failure is recorded
    against its stage and converted to an honest hold so the citation pair still
    reaches human review. Structural/programming errors continue to propagate.
    """
    rec = _new_record(item)
    rec["preband_cleared"] = True
    # The co-citation group, carried on the durable record so a reader can see
    # which references shared this citance. It does NOT replace citation_id:
    # labels key on that across prompt versions and the Band-1 disposition joins
    # on it.
    rec["citance_group_id"] = item.get("citance_group_id", "") or ""
    rec["citance_group_members"] = list(item.get("citance_group_members") or [])
    # RANGE-EXPANSION PROVENANCE travels with the group, or the distinction is
    # lost exactly where a human reads it. A member recovered from a rendered
    # range ("16-18" links 16 and 18; 17 is cited and unlinked) is a deduction
    # from contiguous numbering, not a link the publisher wrote, and an
    # adjudicator judging the flag needs to know which they are looking at.
    rec["citance_group_inferred_members"] = list(
        item.get("citance_group_inferred_members") or [])
    rec["citance_marker_inferred"] = bool(item.get("citance_marker_inferred"))

    # OPT-IN FULL-TEXT COVERAGE (DEC-030/032), mirroring run_band's branch exactly.
    # MODE COMES FROM CONFIGURATION, never from the fetched value: inferring it from
    # the presence of cited_fulltext would let a failed fetch silently drop an
    # opted-in run back to abstract scope and judge it with v2 -- a scope change
    # nothing in the output would record.
    fulltext_path = fetch_fulltext is not None
    # Set only when the full-text path degraded to a real abstract judgment, so
    # the scope stamp below can tell "judged at full text" from "configured for
    # full text, judged at abstract scope".
    abstract_scope_fallback = False
    item["evidence"] = jb.assemble_evidence(
        item, fetch_abstract=fetch_abstract, fetch_reflist=fetch_reflist,
        fetch_fulltext=fetch_fulltext,
        fetch_openalex_abstract=fetch_openalex_abstract)
    rec["evidence"] = item["evidence"]
    from .band_prompts import evidence_is_usable
    rec["evidence_usable"] = bool(evidence_is_usable(item["evidence"]))
    # THE GATE IS "IS THE TEXT READABLE", NOT "WAS THE PAPER IDENTIFIED".
    # Band 1 clearing a reference means the cited WORK is the right one; it says
    # nothing about whether any of that work's text can be fetched. An IEEE
    # conference paper matched to a Crossref DOI at 100% title similarity is
    # certainly identified and has no PubMed record and no PMC full text, so
    # there is nothing for a claim to be checked against. Recorded here, before
    # any discriminator runs, so the router can terminate the pair UNJUDGEABLE
    # without spending a call that could only produce a confident answer about an
    # empty evidence set.
    rec["cited_text_retrievable"] = rec["evidence_usable"]
    rec["evidence_scope"] = rec.get(
        "evidence_scope",
        EVIDENCE_SCOPE_FULLTEXT if fulltext_path else EVIDENCE_SCOPE_ABSTRACT)
    rec["evidence_status"] = (
        "retrievable" if rec["evidence_usable"] else "unretrievable")
    if not rec["evidence_usable"]:
        rec["evidence_unretrievable_detail"] = _evidence_absence_detail(
            item, fulltext_path=fulltext_path)

    sentence = item["citing_sentence"]

    def _extract_once():
        if claims_cache is None:
            return jb.extract_atomic_claims(sentence, extractor=extractor)
        if isinstance(claims_cache, _OrderedClaimsCache):
            if claims_cache_order is None:
                raise ValueError(
                    "claims_cache_order is required for the ordered claims cache")
            return claims_cache.get_or_extract(
                sentence, claims_cache_order, extractor)
        if sentence not in claims_cache:
            # Assigned only on success. A failed extraction is not reused by
            # another reference sharing the sentence.
            claims_cache[sentence] = jb.extract_atomic_claims(
                sentence, extractor=extractor)
        return list(claims_cache[sentence])

    try:
        claims = _extract_once()
        rec["claim_extraction_attempts"] = 1
        _count_paid_call(rec, "claim_extraction", retry=False)
        # BOUNDED RETRY ON AN EMPTY EXTRACTION FROM REAL PROSE. An empty list is
        # the answer that becomes NONE, so it is the one answer worth paying to
        # confirm. Only on prose: retrying a marker-only or bled citance asks the
        # same broken question twice and bills for it.
        if (not claims
                and rec.get("claim_input_status") == tox.CLAIM_INPUT_PROSE):
            for attempt in range(2, tox.CLAIM_EXTRACTION_ATTEMPTS + 1):
                if isinstance(claims_cache, dict):
                    claims_cache.pop(sentence, None)
                _count_paid_call(rec, "claim_extraction", retry=True)
                rec["claim_extraction_attempts"] = attempt
                retried = _extract_once()
                _record_attempt(
                    rec, stage="claim_extraction", attempt=attempt,
                    result="success" if retried else "empty")
                if retried:
                    claims = retried
                    break
        # THE ATTESTATION NONE DEPENDS ON. "No claims" only means "this sentence
        # asserts nothing empirical" if that was DECIDED; inferred from an empty
        # list it is just the extractor's silence, which is exactly how 209
        # broken parses became "no claim". Prose that survived a retry and still
        # yields nothing is the only shape that may attest it.
        if not claims:
            rec["claim_extraction_asserts_nothing"] = (
                rec.get("claim_input_status") == tox.CLAIM_INPUT_PROSE
                and int(rec.get("claim_extraction_attempts") or 0)
                >= tox.CLAIM_EXTRACTION_ATTEMPTS)
    except ValueError as exc:
        _record_stage_failure(rec, "claim_extraction", exc)
        _record_attempt(rec, stage="claim_extraction",
                        attempt=int(rec.get("claim_extraction_attempts") or 1),
                        result="failed",
                        bucket=tox.classify_parse_failure(str(exc)),
                        message=str(exc))
        claims = []
    # MARKER ATTRIBUTION. A reference is asked only the claims its own marker
    # cluster was cited for; a claim it was never cited for cannot produce a
    # verdict against it, because the question is not put at all. Fails closed to
    # the whole sentence on any ambiguity -- see ``marker_scope``.
    scope = marker_scope.scope_item_claims(item, claims)
    claims = list(scope["claims"])
    if scope["status"] == marker_scope.SCOPE_SCOPED:
        item["claim_scope_id"] = scope["scope_id"]
    if marker_scope.should_record(scope):
        scope_record = {k: v for k, v in scope.items() if k != "claims"}
        rec["marker_scope"] = scope_record
        # F7's evidence builder consumes the exact post-scope item later in
        # Phase 2.  It needs the decision, not merely the cluster geometry, so a
        # refused/ambiguous scope can never be mistaken for a successful one.
        item["marker_scope"] = dict(scope_record)
    if scope_counts is not None:
        marker_scope.tally(scope_counts, scope, item.get("citing_pmcid") or "")
    # COVERAGE IS BILLED PER CLAIM, and this call site cannot see those calls:
    # the transport is closed over inside the judge, so the count is read off the
    # judge's own meter as a per-thread DELTA. Both judges are snapshotted
    # because one record can spend through both -- the incomplete-retrieval
    # fallback below runs the v2 judge on a record configured for v3.
    coverage_meters = [m for m in (paid_call_meter(coverage_judge),
                                   paid_call_meter(coverage_judge_v3))
                       if m is not None]
    coverage_before = sum(m.count() for m in coverage_meters)
    coverage_ran = False
    try:
        if not fulltext_path:
            # Default path: abstract scope, v2 prompt, no parser-version key.
            coverage_ran = True
            verdicts = jb.coverage_verdicts(claims, item["evidence"],
                                            judge=coverage_judge)
            if item["evidence"].get("cited_abstract_body_unretrievable") is True:
                # An OpenAlex abstract for a work with no retrievable body. The
                # asymmetry applies PERMANENTLY here, not merely after a failed
                # retrieval: there is no body to fetch, ever, so no absence claim
                # about this work can ever be checked.
                rec["abstract_scope_fallback"] = {
                    "fired": True,
                    "body_unretrievable": True,
                    "coerced_false_to_none": _guard_abstract_scope_absence(
                        verdicts),
                }
        else:
            fulltext = item["evidence"].get("cited_fulltext")
            complete = (isinstance(fulltext, dict)
                        and fulltext.get("retrieval_complete") is True)
            if complete:
                coverage_ran = True
                verdicts = jb.coverage_verdicts(
                    claims, item["evidence"], judge=coverage_judge_v3,
                    prompt_version=COVERAGE_PROMPT_VERSION_V3,
                    parser_version=RESPONSE_PARSER_VERSION_V3)
            else:
                # THE BODY WAS NOT RETRIEVED. That is not the same fact as "there
                # is no evidence": 108 of the 112 rows this branch held on the
                # natural run already carried the cited ABSTRACT, usable, in the
                # same evidence dict. Emitting the deterministic hold over a
                # present abstract throws away evidence we hold and calls the
                # citation unjudgeable on the strength of a retrieval failure.
                rec["fulltext_incomplete_hold"] = True
                # WHICH KIND OF "INCOMPLETE" DECIDES WHETHER THE ABSTRACT IS AN
                # ANSWER OR A DOWNGRADE.
                #
                # `no_pmcid` / `no_body`: the resolver answered and this article
                # has no retrievable body. Retrying returns the same answer
                # forever, so the abstract is not a lesser scope -- it is the
                # whole of the evidence that will ever exist, and judging at
                # abstract scope is the right and only answer.
                #
                # `resolver_error` / `body_unparseable` / `body_too_small`: the
                # body may well exist and WE failed to get or read it. Judging
                # the abstract here would silently downgrade the evidence scope
                # of a row that was entitled to full text, and the record would
                # carry an honest-looking abstract-scope verdict for a paper we
                # merely failed to fetch. That row holds, and the retry gets its
                # chance.
                # `fulltext` is whatever the injected reader returned and may be
                # any shape at all -- None, a string, an int. Read it defensively:
                # a non-dict is an unretrieved body, not a crash, and certainly
                # not a licence to judge at abstract scope.
                reasons = (fulltext.get("incomplete_reasons") or []
                           if isinstance(fulltext, dict) else [])
                body_absent = ftr.body_is_permanently_absent(reasons)
                rec["fulltext_incomplete_class"] = {
                    "reasons": [str(r) for r in reasons],
                    "body_permanently_absent": body_absent,
                }
                if body_absent and jb.evidence_is_usable(item["evidence"]):
                    coverage_ran = True
                    verdicts = jb.coverage_verdicts(
                        claims, item["evidence"], judge=coverage_judge)
                    rec["abstract_scope_fallback"] = {
                        "fired": True,
                        "coerced_false_to_none": _guard_abstract_scope_absence(
                            verdicts),
                    }
                    abstract_scope_fallback = True
                else:
                    verdicts = jb.coverage_verdicts(
                        claims, item["evidence"],
                        judge=lambda cl, _ev: [no_usable_fulltext_dict()
                                               for _ in cl],
                        prompt_version=COVERAGE_PROMPT_VERSION_V3,
                        parser_version=RESPONSE_PARSER_VERSION_V3)
    except ValueError as exc:
        _record_stage_failure(rec, "coverage", exc)
        verdicts = _coverage_failure_verdicts(
            claims, item["evidence"], exc, fulltext_path=fulltext_path)
    finally:
        # BOOKED IN `finally`, SO THE FAILURE PATH IS BILLED TOO. A judge that
        # raised on claim 3 of 5 was already charged for two replies; booking
        # only on success would understate the bill by exactly the failures the
        # stage-failure record exists to surface -- the same argument
        # _count_paid_call makes about retries. The deterministic verdicts
        # _coverage_failure_verdicts then produces cost nothing and carry no
        # meter, so they add nothing here.
        if coverage_meters:
            _book_paid_calls(
                rec, "coverage",
                sum(m.count() for m in coverage_meters) - coverage_before)
        elif coverage_ran:
            _note_unmetered(rec, "coverage")

    if fulltext_path and not abstract_scope_fallback:
        # PROVENANCE MUST STATE THE SCOPE THE ROW WAS ACTUALLY JUDGED AT.
        # _new_record stamps the frozen ABSTRACT version on every record, which is
        # correct on the default path and a FALSE PROVENANCE STAMP here -- the same
        # defect class as DEC-020's omitted temperature. Overwritten, not added, so
        # a reader never has to reconcile two version fields on one row.
        rec["coverage_prompt_version"] = COVERAGE_PROMPT_VERSION_V3
        rec["response_parser_version"] = RESPONSE_PARSER_VERSION_V3
        rec["evidence_scope"] = EVIDENCE_SCOPE_FULLTEXT
    elif abstract_scope_fallback:
        # THE SAME RULE, APPLIED TO THE FALLBACK. This row was configured for the
        # full-text path and JUDGED at abstract scope by the v2 prompt, so it must
        # be stamped abstract or every reader downstream -- including the F4 scope
        # pair and the asymmetry guard itself -- would read it at a scope it was
        # never produced at. No `response_parser_version`: the v2 reply contract
        # carries none, and stamping one would name a contract that did not run.
        rec["coverage_prompt_version"] = COVERAGE_PROMPT_VERSION
        rec["evidence_scope"] = EVIDENCE_SCOPE_ABSTRACT
    # The cluster match, per verdict as well as per record: a reader auditing one
    # claim must not have to join back to the row header to see which clause of
    # the sentence it belonged to.
    marker_scope.stamp_verdicts(verdicts, scope)
    if scope_counts is not None:
        # Answered-and-failed, counted beside never-asked so the two can never
        # be read as one number.
        marker_scope.tally_verdicts(scope_counts, verdicts)
    rec["atomic_claims"] = claims
    rec["coverage_verdicts"] = verdicts
    rec["ts"] = int(time.time())
    # Kept on the item too, so the co-citation aggregation can read them through
    # the same accessor the band uses (jb.item_buckets).
    item["atomic_claims"] = claims
    item["coverage_verdicts"] = verdicts
    return rec, claims, verdicts


def judge_pair(item: dict, *, extractor, coverage_judge, fetch_abstract,
               fetch_reflist=None, fetch_openalex_abstract=None,
               discriminator_call_llm=None,
               f4_verifier_call_llm=None,
               f3_fetch_reflist=None, f3_resolve_pmcid=None,
               f4_policy=None, f3_policy=None,
               f5_seams=None, f5_evidence_builder=None, f5_policy=None,
               f7_seams=None, f7_evidence_builder=None, f7_policy=None,
               fetch_fulltext=None, coverage_judge_v3=None,
               cogroup_covered=()) -> dict:
    """Type a single PRE-BAND-CLEARED item through coverage + the engine.

    Mutates and returns a durable record. Strict per-stage model/evidence errors
    become explicit reviewable holds; structural/configuration errors may still
    propagate to the caller's invariant quarantine.
    ``f4_policy=None`` defaults to a development-mode (non-reportable) F4Policy;
    ``f4_verifier_call_llm`` is threaded into ``refine_support_strength``.

    FULL-TEXT COVERAGE (opt-in, DEC-030/032). Supplying BOTH ``fetch_fulltext`` and
    ``coverage_judge_v3`` moves coverage from the cited abstract to the retrieved
    body: evidence gains ``cited_fulltext``, a complete retrieval is judged by the v3
    judge, and a retrieval that is NOT complete is held with no model call of either
    version. The record's ``coverage_prompt_version`` is OVERWRITTEN to
    ``coverage_v3`` on that path, because ``_new_record`` stamps the frozen abstract
    version on every record and leaving it would be a false provenance stamp.
    Supplying neither leaves every byte of this function's output unchanged;
    supplying one alone is a configuration error and raises in the caller.

    F5 (temporal supersession) is wired through injected seams, fail-closed like
    F3: when BOTH ``f5_seams`` (a dict of the six offline ``decide_f5`` seam
    callables) and ``f5_evidence_builder`` (``item -> evidence`` dict) are
    supplied, ``decide_f5`` produces the ``TemporalAssessment`` and the per-claim
    F5 records; otherwise the temporal seam holds ``UNJUDGEABLE`` (unevaluated,
    an honest hold -- never a fabricated confident negative). ``f5_policy`` runs
    discovery-mode (non-reportable) by default. A deployment-mode F5Policy with
    a distinct positive verifier makes Path-B detection reportable; autonomous
    Path A remains disabled.

    F7 (wrong entity) is wired the same way: when BOTH ``f7_seams`` (a dict of the
    five ``make_entity_assessor`` seam callables) and ``f7_evidence_builder``
    (``item -> EvidenceContext``) are supplied, the assessor produces the
    per-claim ``EntityAssessment`` rows and the durable F7 records; otherwise the
    entity seam stays empty and F7 is never asserted either way. F7 is
    deliberately NOT gated on support state -- it may coexist with F6/F4, so
    support is context, not a veto. ``make_entity_assessor`` raises ValueError on
    a configuration defect, which remains loud; per-pair provenance/model defects
    become explicit F7 stage holds.

    ``cogroup_covered`` is the CO-CITATION OVERLAY (see
    ``judgment_engine.decide_judgment``): one flag per claim, True when a
    reference cited in the SAME sentence established that claim. Empty -- the
    default -- means no co-citation context and reproduces the previous output
    byte for byte. ``run_natural_judgment`` supplies it by running
    :func:`judge_pair_coverage` over a whole document first.
    """
    rec, claims, verdicts = judge_pair_coverage(
        item, extractor=extractor, coverage_judge=coverage_judge,
        fetch_abstract=fetch_abstract, fetch_reflist=fetch_reflist,
        fetch_fulltext=fetch_fulltext,
        fetch_openalex_abstract=fetch_openalex_abstract,
        coverage_judge_v3=coverage_judge_v3)
    return judge_pair_finish(
        rec, item, claims, verdicts, fetch_abstract=fetch_abstract,
        cogroup_covered=cogroup_covered,
        discriminator_call_llm=discriminator_call_llm,
        f4_verifier_call_llm=f4_verifier_call_llm,
        f3_fetch_reflist=f3_fetch_reflist, f3_resolve_pmcid=f3_resolve_pmcid,
        f4_policy=f4_policy, f3_policy=f3_policy,
        f5_seams=f5_seams, f5_evidence_builder=f5_evidence_builder,
        f5_policy=f5_policy, f7_seams=f7_seams,
        f7_evidence_builder=f7_evidence_builder, f7_policy=f7_policy)


def judge_pair_finish(rec: dict, item: dict, claims, verdicts, *,
                      fetch_abstract, cogroup_covered=(),
                      discriminator_call_llm=None, f4_verifier_call_llm=None,
                      f3_fetch_reflist=None, f3_resolve_pmcid=None,
                      f4_policy=None, f3_policy=None,
                      f5_seams=None, f5_evidence_builder=None, f5_policy=None,
                      f7_seams=None, f7_evidence_builder=None,
                      f7_policy=None) -> dict:
    """PHASE 2 of :func:`judge_pair`: type through the engine, derive disposition.

    Separated from phase 1 so a caller can interpose the document's CO-CITATION
    group coverage (``cogroup_covered``) between the two. Mutates and returns
    ``rec``.
    """
    # NO RETRIEVABLE CITED TEXT -> STOP HERE, BEFORE ANY DISCRIMINATOR.
    # Every F3-F7 seam below asks a model to compare a claim against the cited
    # work's text. With no text there is no comparison to make, and a label
    # returned from an empty evidence set is a confident statement about nothing.
    # This is a property of the CITED WORK, not a defect in this engine, so it is
    # UNJUDGEABLE and never human review -- and it costs zero paid calls.
    if rec.get("cited_text_retrievable") is False:
        rec["route"] = jb.route(verdicts)
        rec["disposition"] = DISP_HELD_INSUFFICIENT
        rec["hold_reasons"] = [tox.REASON_CITED_TEXT_UNAVAILABLE]
        rec["discriminators_skipped"] = {
            "reason": tox.REASON_CITED_TEXT_UNAVAILABLE,
            "stages": ["F4", "F3", "F5", "F7"],
            "detail": rec.get("evidence_unretrievable_detail") or {},
        }
        return rec

    if not claims:
        # NO_CLAIMS since 2026-08-11 (ZD calibration item 1). This branch always
        # had the case right -- DISP_HELD_NO_CLAIMS below -- while jb.route
        # returned FULL_COVERAGE from a vacuous all([]) and disagreed with it on
        # the same record. The two ends now agree.
        rec["route"] = jb.route(verdicts)
        if _stage_failed(rec, "claim_extraction"):
            rec["disposition"] = DISP_HELD_CLAIM_EXTRACTION_FAILURE
            rec["hold_reasons"] = ["claim extraction stage failed"]
        else:
            rec["disposition"] = DISP_HELD_NO_CLAIMS
            rec["hold_reasons"] = ["no atomic claims"]
        return rec

    # Coverage -> typed support. The entity seam stays empty unless the F7 seams
    # are supplied (an unwired F7 is never handed a confident negative). F4/F3/F5/F7
    # run only when their seams are wired; otherwise this reproduces the legacy
    # path exactly.
    coverage_support = from_legacy_coverage(claims, verdicts)
    support = coverage_support

    if discriminator_call_llm is not None:
        # F4 (strength) refinement: SUPPORTED -> WEAKER_STRENGTH / UNJUDGEABLE.
        policy = f4_policy if f4_policy is not None else F4Policy(mode="development")
        f4_evidence = dict(item["evidence"])
        f4_evidence["coverage_evidence_scope"] = rec.get(
            "evidence_scope", EVIDENCE_SCOPE_ABSTRACT)
        try:
            # F4 falls back to the generator when no distinct verifier is
            # wired (f4_strength: `verifier or call_llm`), so leaving the
            # verifier unwrapped as None keeps the merged role's calls booked
            # under "F4" -- which is what actually happened.
            f4_call, f4_verify = _metered_pair(
                rec, "F4", "F4_verifier",
                discriminator_call_llm, f4_verifier_call_llm)
            support, strength_records = refine_support_strength(
                claims, coverage_support, f4_evidence,
                call_llm=f4_call,
                verifier_call_llm=f4_verify,
                policy=policy)
            rec["strength_records"] = list(strength_records)
        except ValueError as exc:
            _record_stage_failure(rec, "F4", exc)
            support = tuple(
                ClaimSupport(
                    row.claim_index,
                    SupportState.UNJUDGEABLE,
                    (f"{row.rationale} | " if row.rationale else "")
                    + f"F4 stage failed: {type(exc).__name__}: {exc}",
                    row.evidence_spans,
                ) if row.state is SupportState.SUPPORTED else row
                for row in coverage_support)
            coverage_scope = rec.get("evidence_scope", EVIDENCE_SCOPE_ABSTRACT)
            rec["strength_records"] = [{
                "claim_index": row.claim_index,
                "assessed": False,
                "derived": "UNJUDGEABLE",
                "reason": "stage_failure",
                "error": str(exc),
                "f4_evidence_scope": "abstract",
                "coverage_evidence_scope": coverage_scope,
                "evidence_scopes_match": coverage_scope == "abstract",
            } for row in coverage_support
                if row.state is SupportState.SUPPORTED]

    all_supported = bool(support) and all(
        s.state is SupportState.SUPPORTED for s in support)

    # F3 (provenance) only under full support (engine requires provenance=None
    # otherwise). Unwired -> UNJUDGEABLE seam (not evaluated, honest hold).
    provenance = None
    if all_supported:
        if discriminator_call_llm is not None:
            cited_pmcid = (f3_resolve_pmcid(item["cited_pmid"])
                           if f3_resolve_pmcid is not None else None)
            # BOOKED SEPARATELY FROM F4 even though it is the same transport.
            # The receipt cannot separate them -- both are
            # `discriminator_call_llm` -- so this ledger is the only place a
            # reader can see what provenance cost as distinct from strength.
            assessor = make_provenance_assessor(
                call_llm=_metered(rec, "F3", discriminator_call_llm),
                fetch_reflist=f3_fetch_reflist or (lambda _p: ([], False)),
                fetch_abstract=fetch_abstract,
                cited_pmid=item["cited_pmid"], cited_pmcid=cited_pmcid,
                cited_is_review=item.get("cited_is_review"),
                policy=f3_policy or DEFAULT_F3_POLICY)
            try:
                provenance = assessor(claims, support)
            except ValueError as exc:
                _record_stage_failure(rec, "F3", exc)
                provenance = ProvenanceAssessment(
                    ProvenanceState.UNJUDGEABLE,
                    rationale=f"F3 stage failed: {type(exc).__name__}: {exc}")
        else:
            provenance = ProvenanceAssessment(ProvenanceState.UNJUDGEABLE)
        rec["provenance"] = {
            "state": provenance.state.value,
            "origin_chain": list(provenance.origin_chain),
            "evidence_spans": list(provenance.evidence_spans),
            "rationale": provenance.rationale,
        }

    # F5 (temporal supersession): wired through injected seams, fail-closed like
    # F3. When BOTH f5_seams and f5_evidence_builder are supplied, decide_f5
    # produces the TemporalAssessment (and the per-claim F5 records); otherwise the
    # temporal seam holds UNJUDGEABLE (unevaluated -- never a fabricated confident
    # negative). A malformed per-pair seam payload/evidence becomes an explicit
    # F5 stage hold while preserving the rest of the judged pair.
    temporal = TemporalAssessment(TemporalState.UNJUDGEABLE)
    if f5_seams is not None and f5_evidence_builder is not None:
        try:
            f5_evidence = f5_evidence_builder(item)
            effective_f5_policy = (
                f5_policy if f5_policy is not None else F5Policy())
            # Only two of the eight F5 seams reach a model; the rest are
            # offline resolvers. The bundle is COPIED rather than mutated so the
            # original stays intact for the source-packet lookup below and for
            # anything the caller validated it as.
            metered_f5_seams = dict(f5_seams)
            (metered_f5_seams["judge_contradiction"],
             metered_f5_seams["verify_contradiction"]) = _metered_pair(
                rec, "F5", "F5_verifier",
                f5_seams.get("judge_contradiction"),
                f5_seams.get("verify_contradiction"))
            temporal, f5_records = decide_f5(
                claims, support, f5_evidence,
                policy=effective_f5_policy,
                **metered_f5_seams)
            if effective_f5_policy.mode == "deployment":
                from .f5_evidence_store import source_packet_from_dict
                from .f5_supersession import validate_f5_record
                packet_rows = getattr(
                    f5_seams.get("fetch_comparability_source"),
                    "source_packet_log", None)
                if not isinstance(packet_rows, list):
                    raise ValueError(
                        "deployment F5 requires a source-packet replay log")
                packet_map = {}
                for packet_row in packet_rows:
                    packet = source_packet_from_dict(packet_row)
                    packet_map[packet.packet_sha256] = packet_row
                for f5_record in f5_records:
                    validate_f5_record(
                        f5_record, effective_f5_policy, packet_map)
            rec["f5_records"] = list(f5_records)
        except ValueError as exc:
            _record_stage_failure(rec, "F5", exc)
            temporal = TemporalAssessment(
                TemporalState.UNJUDGEABLE,
                rationale=f"F5 stage failed: {type(exc).__name__}: {exc}")

    # F7 (wrong entity): wired through injected seams like F3/F5. Deliberately NOT
    # gated on support state -- an entity mismatch may coexist with F6/F4, so
    # support is context, not a veto. make_entity_assessor raises ValueError on a
    # configuration defect (which stays loud) or a per-pair provenance/model
    # defect (which becomes an explicit F7 stage hold, never a negative).
    entities: tuple = ()
    if f7_seams is not None and f7_evidence_builder is not None:
        try:
            evidence_context = f7_evidence_builder(item)
        except ValueError as exc:
            _record_stage_failure(rec, "F7", exc)
            entities = tuple(EntityAssessment(
                index, EntityState.UNJUDGEABLE,
                rationale=f"F7 stage failed: {type(exc).__name__}: {exc}")
                for index in range(len(claims)))
        else:
            # Configuration is validated outside the per-pair failure boundary.
            # A broken seam bundle must remain loud rather than converting every
            # row in a run into the same expensive hold.
            # F7's transports are the one pair already visible to the
            # adapter receipt (they are receipt-bound at construction), but the
            # receipt is RUN-scoped and this ledger is RECORD-scoped, so the
            # per-record count still has to be taken here. Copied, not mutated:
            # the caller's bundle is what validate_production_f7_configuration
            # was handed.
            metered_f7_seams = dict(f7_seams)
            (metered_f7_seams["call_llm"],
             metered_f7_seams["verifier_call_llm"]) = _metered_pair(
                rec, "F7", "F7_verifier",
                f7_seams.get("call_llm"), f7_seams.get("verifier_call_llm"))
            entity_assessor = make_entity_assessor(
                **metered_f7_seams,
                evidence_context=evidence_context,
                policy=f7_policy if f7_policy is not None else F7Policy())
            try:
                entities = tuple(entity_assessor(claims))
                for record in entity_assessor.records:
                    validate_f7_record(record, evidence_context)
                rec["f7_records"] = list(entity_assessor.records)
            except ValueError as exc:
                _record_stage_failure(rec, "F7", exc)
                entities = tuple(EntityAssessment(
                    index, EntityState.UNJUDGEABLE,
                    rationale=f"F7 stage failed: {type(exc).__name__}: {exc}")
                    for index in range(len(claims)))

    own_buckets = jb.item_buckets(item)
    effective_cogroup_covered = tuple(
        bool(flag) and index < len(own_buckets)
        and own_buckets[index] != cocitation.BUCKET_CONTRADICTED
        for index, flag in enumerate(cogroup_covered or ()))
    decision = decide_judgment(
        preband_cleared=True, claims=claims, claim_support=support,
        entity_assessments=entities, provenance=provenance,
        temporal=temporal, cogroup_covered=effective_cogroup_covered)
    rec["findings"] = list(decision.findings)
    rec["hold_reasons"] = list(decision.hold_reasons)
    for failure in rec.get("stage_failures") or []:
        reason = f"{failure['stage']} stage failed"
        if reason not in rec["hold_reasons"]:
            rec["hold_reasons"].append(reason)

    solo_route = jb.route(verdicts)
    member_route = (rec.get("cocitation") or {}).get("member_route")
    r = member_route or solo_route
    rec["solo_route"] = solo_route
    rec["route"] = r

    if discriminator_call_llm is None:
        # Legacy path: coverage->F6 is the only DISCRIMINATOR-GATED live fault.
        #
        # F7 IS NOT DISCRIMINATOR-GATED. Its seams are independent of
        # ``discriminator_call_llm`` (see the F7 block above, which runs on both
        # paths), and it rides highest in the engine ordering -- so a confirmed
        # wrong-entity finding owns the label here exactly as it does on the
        # wired path below. Without this check the early return swallowed a
        # finding ``decide_judgment`` had ALREADY MADE: rec["findings"] carried
        # "F7", rec["label"] stayed None, and the disposition string that went
        # out said the pair was "held_full_coverage_pending_F3_F5_F7" -- naming
        # F7 as pending while a confirmed F7 sat two keys away in the same
        # record. seam_status then reported the seam wired and fired=0, which is
        # the instrumentation built to prevent this reporting the exact lie it
        # exists to prevent.
        #
        # F5 rides LOWEST and is dropped by this same early return. That half is
        # deliberately left to the F5 spec rather than reconciled silently here:
        # its fix goes immediately below this block, after the F6 route, because
        # precedence puts it there.
        if "F7" in decision.findings:
            rec["disposition"] = DISP_PREDICTED
            rec["label"] = "F7"
            return rec
        if r == cocitation.ROUTE_UNSUPPORTED_MEMBER:
            rec["disposition"] = DISP_HELD_UNSUPPORTED_COCITATION_MEMBER
            return rec
        if (solo_route == jb.ROUTE_F6_FLAGGED
                and r == cocitation.ROUTE_GROUP_COVERED
                and cogroup_covered):
            rec["disposition"] = DISP_HELD_COCITATION_COVERED
            return rec
        if r == jb.ROUTE_F6_FLAGGED:
            if "F6" not in decision.findings:
                # The co-citation overlay explains this route: every claim this
                # reference did not establish was established by a reference cited
                # in the SAME sentence, so the sentence is collectively supported
                # and F6 -- "supports part of the claim but not all of it" -- is
                # not a statement about this reference. It is NOT a clear either:
                # this reference did not establish those claims, so the pair holds
                # for human adjudication and the group record carries the
                # attribution.
                if cogroup_covered:
                    rec["disposition"] = DISP_HELD_COCITATION_COVERED
                    return rec
                raise DiscriminatorContractError(
                    "route F6_FLAGGED but engine findings lack F6")
            rec["disposition"] = DISP_PREDICTED
            rec["label"] = "F6"
        elif "F5" in decision.findings:
            # THE OTHER HALF OF THE EARLY-RETURN BUG DESCRIBED ABOVE, fixed where
            # that comment says it belongs: after the F6 route, because precedence
            # puts F5 there. Without it a qualifying temporal finding reached
            # rec["findings"] and terminal_outcome F5 while rec["label"] stayed
            # None and the disposition said the pair was held "pending F3_F5_F7"
            # -- naming F5 as pending with a confirmed F5 in the same record, the
            # same lie the F7 half was fixed to stop telling. F3/F4 cannot fire on
            # this path at all (they are discriminator-gated), so F5 sits directly
            # under F6 here. (The verdict enum is deliberately NOT named in this
            # module, not even in a comment: test_source_never_asserts_confident_
            # negatives_for_unbuilt_gates greps this source to keep the
            # orchestrator a thin wiring layer, and a comment defeats that grep.)
            rec["disposition"] = DISP_PREDICTED
            rec["label"] = "F5"
        elif r == jb.ROUTE_FULL_COVERAGE:
            rec["disposition"] = DISP_HELD_FULL_COVERAGE     # F3/F7 uncleared
        else:
            rec["disposition"] = DISP_HELD_INSUFFICIENT
        return rec

    # Wired path: F6 / F4 / F3 live; F5 (temporal) and F7 (entity) live when their
    # seams are wired. Precedence follows the engine ordering (F7, F6, F4, F3, F5):
    # F7 rides highest, F5 lowest and only owns the label when it is the sole fault.
    findings = decision.findings
    if "F7" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F7"
    elif "F6" in findings:
        if solo_route != jb.ROUTE_F6_FLAGGED:
            raise DiscriminatorContractError("F6 finding without an F6 coverage route")
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F6"
    elif (solo_route == jb.ROUTE_F6_FLAGGED
          and r == cocitation.ROUTE_GROUP_COVERED
          and cogroup_covered and not findings):
        # Same case as the legacy branch above, on the wired path: the F6 route is
        # fully explained by co-cited coverage. ``not findings`` is the guard that
        # makes this safe -- F4 (overstatement) and F7 (wrong entity) are
        # PER-REFERENCE faults that co-citation says nothing about, and F3/F5 are
        # their own findings, so any surviving finding falls through to its own
        # branch and keeps the label.
        rec["disposition"] = DISP_HELD_COCITATION_COVERED
    elif "F4" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F4"
    elif "F3" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F3"
    elif "F5" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F5"
    elif r == cocitation.ROUTE_UNSUPPORTED_MEMBER:
        rec["disposition"] = DISP_HELD_UNSUPPORTED_COCITATION_MEMBER
    elif any(s.state is SupportState.UNJUDGEABLE for s in support):
        # A coverage unknown vs a strength (F4) unknown, distinguished for the record.
        if any(s.state is SupportState.UNJUDGEABLE for s in coverage_support):
            rec["disposition"] = DISP_HELD_INSUFFICIENT
        else:
            rec["disposition"] = DISP_HELD_STRENGTH_UNJUDGEABLE
    elif provenance is not None and provenance.state is ProvenanceState.PROPER_ORIGIN:
        # Full coverage, not overstated, rightful origin: accurate on every gate
        # that ran; held for F5/F7, which are asserted only when wired.
        rec["disposition"] = DISP_HELD_PENDING_F5_F7
    else:
        rec["disposition"] = DISP_HELD_PROVENANCE_UNJUDGEABLE
    return rec


def _cocitation_overlay(items: "list[dict]") -> dict:
    """Aggregate one document's judged items into their co-citation groups.

    Returns the per-citation overlay the engine consumes plus the group records
    and the size accounting the manifest reports.

    ``by_citation_id`` maps citation_id -> ``(cogroup_covered_flags, summary)``.
    The flags are EMPTY for a singleton, for a member the aggregation excluded,
    and whenever their length does not match the member's own claim list -- a
    length mismatch would excuse the wrong claim, so it fails closed to "no
    co-citation context" rather than guessing an alignment.
    """
    by_citation_id: dict = {}
    group_records: list = []
    size_distribution: dict = {}
    members_in_groups = 0
    groups = cocitation.partition(items)
    for gid, members in groups.items():
        size = len(members)
        size_distribution[str(size)] = size_distribution.get(str(size), 0) + 1
        if size <= 1:
            continue
        members_in_groups += size
        aggregated = cocitation.aggregate(members, buckets_of=jb.item_buckets)
        flags = cocitation.cogroup_covered_flags(aggregated)
        contributing = set(aggregated.get("contributing_members", []))
        routes = {
            m["citation_id"]: cocitation.member_route(
                buckets=jb.item_buckets(m),
                solo_route=jb.route(m.get("coverage_verdicts") or []),
                aggregated=aggregated, group_size=size,
                citation_id=m["citation_id"])
            for m in members
        }
        # The SENTENCE id, never the partition key: when marker attribution
        # narrowed this unit to one clause the key is a cluster id, and
        # citance_group_id must keep meaning "the sentence occurrence".
        record = cocitation.group_record(
            cocitation.group_id_of(members[0]) or gid, members, aggregated,
            routes)
        group_records.append(record)
        summary = {
            "citance_group_id": record["citance_group_id"],
            **({"marker_scope_id": record["marker_scope_id"]}
               if "marker_scope_id" in record else {}),
            "size": size,
            "members": list(record["members"]),
            "claims_covered": record["claims_covered"],
            "claims_uncovered": record["claims_uncovered"],
            "claims_unknown": record["claims_unknown"],
            "uncovered_claims": list(record["uncovered_claims"]),
        }
        for m in members:
            cid = m["citation_id"]
            own_claims = m.get("atomic_claims") or []
            usable = (cid in contributing and len(flags) == len(own_claims)
                      and any(flags))
            member_summary = dict(summary)
            member_summary["member_route"] = routes[cid]
            by_citation_id[cid] = (flags if usable else (), member_summary)
    sentence_groups = {
        cocitation.group_id_of(members[0])
        for members in groups.values() if members
    }
    return {
        "by_citation_id": by_citation_id,
        "group_records": group_records,
        "groups": len(groups),
        "sentence_groups": len(sentence_groups),
        "members_in_cocitation_groups": members_in_groups,
        "group_size_distribution": size_distribution,
    }


def _module_hashes(fulltext_path: bool, f5_seams, f7_seams) -> dict:
    """Hash every module that can govern a number on THIS run's wiring.

    Captured before execution, so the digests describe the bytes that ran. The
    conditional blocks exist because the default abstract path's manifest bytes
    are an opt-in guarantee: an unconditional key changes every default run.
    """
    names = ["cre.f1.judgment_band", "cre.f1.judgment_engine",
             "cre.f1.band_prompts", "cre.f1.parser", "cre.f1.schema",
             # Governs WHICH CLAIMS each reference was asked. A run whose
             # attribution rule changed would otherwise be indistinguishable
             # from one whose model changed its mind.
             "cre.f1.marker_scope",
             "cre.f1.f4_strength", "cre.f1.f3_provenance",
             "cre.f1.judgment_run", "cre.f1.preband_contract"]
    if fulltext_path:
        names += ["cre.f1.coverage_prompts_v3", "cre.f1.coverage_aggregate",
                  "cre.f1.fulltext_reader", "cre.f1.sentence_spans"]
    if f5_seams is not None:
        names += ["cre.f1.f5_activation", "cre.f1.f5_supersession",
                  "cre.f1.f5_candidate_screen",
                  "cre.f1.f5_contradiction_prompt",
                  "cre.f1.f5_seams", "cre.f1.f5_evidence_store",
                  "cre.f1.f5_notice", "cre.f1.f5_study_cluster",
                  "cre.f1.f5_controversy_bundle",
                  "cre.f1.f5_candidate_finder",
                  "cre.f1.f5_discovery_queue"]
    # F7 can OWN the published label (it rides highest in the engine ordering),
    # so an F7 run that does not hash f7_entity records the governing module of
    # its headline number nowhere. Same defect class the f5 block fixed.
    if f7_seams is not None:
        names += ["cre.f1.f7_entity", "cre.f1.f7_evidence_builder",
                  "cre.f1.f7_seams"]
    out: dict = {}
    for name in names:
        try:
            mod = __import__(name, fromlist=["x"])
        except ImportError:
            continue
        f = getattr(mod, "__file__", None)
        if f and os.path.exists(f):
            out[os.path.basename(f)] = _sha256_file(f)
    return out


def _f3_manifest_block(f3_policy, discriminator_wired: bool) -> dict:
    """The ``"f3"`` block: the EFFECTIVE policy, not the default it may not be.

    F3's hop limit, trace sources and unresolved state govern whether a claim
    can reach an F3 finding at all. They were recorded nowhere, so an F3 number
    could move with no change visible in any artifact.
    """
    policy = f3_policy if f3_policy is not None else DEFAULT_F3_POLICY
    return {
        "wired": discriminator_wired,
        "origin_sensitive_prompt_version": policy.origin_sensitive_prompt_version,
        "v3_select_prompt_version": policy.v3_select_prompt_version,
        "v4_prompt_version": policy.v4_prompt_version,
        "trace_sources": list(policy.trace_sources),
        "max_hop_count": policy.max_hop_count,
        "unresolved_state": policy.unresolved_state,
        "policy_sha256": _canonical_sha256({
            "origin_sensitive_prompt_version": policy.origin_sensitive_prompt_version,
            "v3_select_prompt_version": policy.v3_select_prompt_version,
            "v4_prompt_version": policy.v4_prompt_version,
            "trace_sources": list(policy.trace_sources),
            "max_hop_count": policy.max_hop_count,
            "unresolved_state": policy.unresolved_state,
        }),
    }


def _f7_manifest_block(f7_policy, f7_records, *, wired: bool = False,
                       evidence_context_supplied: bool = False,
                       f7_seams=None) -> dict:
    """The ``"f7"`` block: policy, prompt digests, model ids, tallies.

    F7 rides HIGHEST in the engine ordering, so it can determine the published
    label outright -- and it previously emitted no module hash, no prompt hash,
    no policy block and no model ids. This is the counterpart to ``"f4"`` and
    ``"f5"``.

    ``relation_prompt_sha256`` is the schema-D digest reported by the injected
    relation comparator. The relation prompt is BUILT INSIDE that comparator, so
    this module cannot hash its text; when the comparator does not report one the
    value is ``None`` and ``relation_prompt_digest_present`` is False. It is never
    filled with the version string -- a version is not a digest, and storing one
    under a ``_sha256`` name is a false provenance record.

    ``wired`` is PASSED IN, not assumed. It used to be the literal ``True``, so a
    run supplying ``f7_seams`` without ``f7_evidence_builder`` published
    ``f7.wired = true`` next to ``seam_status.F7.wired = false`` in one manifest,
    and ``_f7_manifest_block(None, [])`` claimed a wired F7 with no seams at all.
    Both fields now come from the same expression and cannot disagree.
    """
    policy = f7_policy if f7_policy is not None else F7Policy()
    reachability = f7_reachability(policy)
    # HOW AUTHORITY-INDEX INTEGRITY WAS ESTABLISHED. Present only for the
    # disk-backed normalizer, which is the only one that has an index file to
    # attest. Recorded because the load path no longer re-runs quick_check --
    # without this the manifest would be silent about integrity and a reader
    # could only infer it from an absence.
    index_manifest = getattr(
        (f7_seams or {}).get("normalizer") if hasattr(f7_seams, "get") else None,
        "index_manifest", None)
    records = list(f7_records or [])
    # The per-tuple traces live at record["tuple_records"]; the relation digest
    # is stamped there, not on the outer §9 packet.
    traces = [tr for r in records for tr in (r.get("tuple_records") or [])]
    # Only traces that ACTUALLY ran a relation comparison can carry a relation
    # digest; one that short-circuited earlier never consulted that prompt.
    attempted = [tr for tr in traces
                 if tr.get("relation_component_result") is not None]
    real = [tr.get("prompt_sha256", {}).get("relation") for tr in attempted]
    real = [d for d in real
            if isinstance(d, str) and len(d) == 64 and _is_hex(d)]
    digest_present = bool(attempted) and len(real) == len(attempted)
    outcomes: dict = {}
    for r in records:
        k = str(r.get("derived"))
        outcomes[k] = outcomes.get(k, 0) + 1
    return {
        "wired": wired,
        # Reportable requires records AND that every relation comparison which
        # actually ran reported its prompt digest. A run whose relation prompt
        # cannot be identified cannot back a published F7 number.
        "reportable": bool(records) and digest_present,
        # reportable=False is NOT a fault report. An honest run in which every
        # claim matched the paper's own entity gets it too: a matched claim
        # short-circuits before the relation stage, so no schema-D digest exists
        # to pin. Stated because the field name invites the opposite reading.
        # (The verdict enums themselves are deliberately not named in this
        # module -- see the guard in test_judgment_run: this layer routes leaf
        # verdicts and never inline-asserts one.)
        "reportable_note": (
            "reportable=False does not mean anything went wrong. A run whose "
            "claims all matched the cited paper's own entity never reaches the "
            "relation stage, so it has no schema-D digest and is not reportable "
            "-- the same value an unwired or a defective run gets. Read it with "
            "records_emitted, outcome_counts and hold_reasons, never alone."
        ),
        "attribution_prompt_version": policy.attribution_prompt_version,
        "tuples_prompt_version": policy.tuples_prompt_version,
        "evidence_prompt_version": policy.evidence_prompt_version,
        "relation_prompt_version": policy.relation_prompt_version,
        "verifier_prompt_version": policy.verifier_prompt_version,
        "generator_model_id": policy.generator_model_id,
        "verifier_model_id": policy.verifier_model_id,
        "cross_ontology_lock": policy.cross_ontology_lock,
        "authorities_sha256": _sha256_text(policy.authorities_json),
        # THE DIGEST IS NOT THE ANSWER. authorities_sha256 of an empty table is
        # 44136fa3... -- sha256("{}") -- and nothing in any artifact compares it
        # to that constant, so recognising a structurally unreachable run meant
        # already knowing the constant on sight. These fields say it in words:
        # which types are locked, whether F7 could fire at all, and why not.
        "authorities_locked_types": reachability["locked_types"],
        "same_type_reachable": reachability["same_type_reachable"],
        "unreachable_reason": reachability["unreachable_reason"],
        # Cross-type F7 is unreachable BY DESIGN and always will be -- the cross
        # comparator's relation enum has no provably_distinct, because "a drug is
        # not a gene" is not the finding F7 makes. Published so the zero in
        # outcome_counts is read as a guardrail holding, not as a gap.
        "cross_type_reachable": reachability["cross_type_reachable"],
        "cross_type_note": reachability["cross_type_note"],
        "cross_ontology_lock_present": reachability["cross_ontology_lock_present"],
        "prompt_sha256": {
            "attribution": _sha256_text(F7_ATTRIBUTION_PROMPT),
            "tuples": _sha256_text(F7_TUPLES_PROMPT),
            "evidence": _sha256_text(F7_EVIDENCE_PROMPT),
            "verifier": _sha256_text(F7_VERIFIER_PROMPT),
        },
        # INDEX INTEGRITY, STATED RATHER THAN INFERRED. The load path proves the
        # SQLite index is byte-identical to the file that passed
        # `PRAGMA quick_check` when it was built; it does not re-scan every page
        # of four databases on every run. Both halves are named here so the
        # provenance says how integrity was established, not merely that it was.
        **({"authority_index_integrity": index_manifest()}
           if callable(index_manifest) else {}),
        "relation_prompt_digest_present": digest_present,
        "relation_prompt_sha256": sorted(set(real)) or None,
        "relation_comparisons_attempted": len(attempted),
        "records_emitted": len(records),
        "outcome_counts": dict(sorted(outcomes.items())),
        # WHY F7 HELD, not just how often. outcome_counts keys on the three
        # EntityState values, so every one of the module's enumerated hold
        # reasons collapsed into a single "UNJUDGEABLE" tally -- a number with no
        # cause attached, while the causes sat on disk in rec["f7_records"] and
        # in no artifact anyone reads. Both granularities, per the histogram's
        # own contract: claim-level is the roll-up acted on, tuple-level is every
        # reason produced including those a roll-up discarded.
        "hold_reasons": hold_reason_histogram(records),
        # F7 HAS NO PRODUCTION EVIDENCE BUILDER. EvidenceContext is constructed
        # only in this package's tests. Until that changes, every F7 number this
        # block can report came from a fixture, and no F7 rate may be quoted from
        # a real corpus -- so the manifest says it rather than leaving a reader to
        # infer it from a builder they cannot see.
        "production_evidence_builder": PRODUCTION_F7_EVIDENCE_BUILDER,
        "evidence_context_supplied": evidence_context_supplied,
        "note": (
            "Schema D (relation) is built inside the INJECTED relation_comparator, "
            "so its prompt text is not visible here. Its digest is reported by the "
            "comparator; when absent it stays None and this run is not F7-reportable. "
            "The version string is recorded separately and never stands in for a digest."
        ),
        "production_note": PRODUCTION_F7_BUILDER_NOTE,
    }


def _queue_audit(pred_path: str, queue_path: str,
                 review_path: str = "") -> dict:
    """Audit BOTH QUEUE FILES against the PREDICTIONS FILE.

    Both sides are re-read from disk on purpose. An earlier version compared two
    in-memory lists that were appended in the same branch, so it agreed with
    itself by construction and proved nothing about what was written. A queue is
    a denominator: if it holds fewer rows than the run put in it, every rate
    computed from it is over a different population than the manifest reports.

    WHAT THE ANNOTATION QUEUE MUST NOW EQUAL. It used to be audited against
    ``_SCOREABLE`` -- every held disposition, including ``held_no_atomic_claims``.
    That made the audit pass while the queue was full of rows an annotator cannot
    label: "no claim here, please confirm" for a citance the parser had reduced to
    "5,8,10,19". The queue's job is blind GOLD-LABELLING of findings, so it is
    audited against exactly the records carrying an F3-F7 finding. The
    human-review queue is audited separately against the records the router
    flagged, and the two must not intersect.
    """
    finding_ids: list = []
    review_ids: list = []
    for line in _read_jsonl_lines(pred_path):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        outcome = rec.get("terminal_outcome")
        if tox.carries_finding(outcome):
            finding_ids.append(rec.get("citation_id"))
        elif rec.get("human_review_required") is True:
            review_ids.append(rec.get("citation_id"))
    queued: list = []
    for line in _read_jsonl_lines(queue_path):
        try:
            queued.append(json.loads(line).get("item_key"))
        except json.JSONDecodeError:
            continue
    reviewed: list = []
    if review_path:
        for line in _read_jsonl_lines(review_path):
            try:
                reviewed.append(json.loads(line).get("citation_id"))
            except json.JSONDecodeError:
                continue
    overlap = sorted(set(queued) & set(reviewed))
    return {
        "queue_rows": len(queued),
        "finding_rows": len(finding_ids),
        # Kept under its original key so existing readers of the manifest do not
        # silently start reading a missing field as zero.
        "scoreable_rows": len(finding_ids),
        "matches": (len(queued) == len(finding_ids)
                    and set(queued) == set(finding_ids)),
        "symmetric_difference": sorted(
            x for x in (set(queued) ^ set(finding_ids)) if x is not None)[:10],
        "human_review_rows": len(reviewed),
        "human_review_expected": len(review_ids),
        "human_review_matches": (len(reviewed) == len(review_ids)
                                 and set(reviewed) == set(review_ids)),
        # NONE and UNJUDGEABLE belong to neither file, and no record may sit in
        # both. A row in both queues would be counted twice by whichever report
        # summed them.
        "queues_disjoint": not overlap,
        "queue_overlap": overlap[:10],
        "source": "files_on_disk",
    }


def _instrument_f5_seams(seams: dict) -> tuple[dict, dict]:
    """Wrap the injected F5 calls so the manifest reports observations.

    A swappable seam cannot be described honestly by a module constant. The
    wrapper counts the call that actually ran; retrieval protocol details are
    copied only when that seam exposes an executed-protocol log.
    """
    observed = {"retrieval_calls": 0, "attestation_calls": 0,
                "judge_calls": 0, "judge_model_calls": 0,
                "judge_cache_hits": 0, "verifier_calls": 0,
                "retrieval_protocols": []}
    evidence_store = getattr(
        seams.get("fetch_comparability_source"), "evidence_store", None)
    observed["evidence_store_counters"] = (
        getattr(evidence_store, "counters", {}) if evidence_store is not None else {})
    observed["source_packet_log"] = getattr(
        seams.get("fetch_comparability_source"), "source_packet_log", [])
    observed["candidate_cap"] = getattr(
        seams.get("retrieve_superseding_candidates"),
        "candidate_cap", "not_collected")
    observed["_judge_counter_source"] = seams.get("judge_contradiction")
    observed["_verifier_wired"] = callable(seams.get("verify_contradiction"))
    wrapped = dict(seams)
    observation_lock = threading.Lock()

    def observe(name, counter):
        fn = seams[name]

        def call(*args, **kwargs):
            with observation_lock:
                observed[counter] += 1
                before = len(getattr(fn, "executed_protocols", ()))
            result = fn(*args, **kwargs)
            if name == "retrieve_superseding_candidates":
                with observation_lock:
                    protocols = list(getattr(fn, "executed_protocols", ()))
                    observed["retrieval_protocols"].extend(protocols[before:])
            return result
        for attribute in ("model_id", "model_settings", "thread_safe"):
            if hasattr(fn, attribute):
                setattr(call, attribute, getattr(fn, attribute))
        return call

    wrapped["retrieve_superseding_candidates"] = observe(
        "retrieve_superseding_candidates", "retrieval_calls")
    wrapped["find_supersession_attestation"] = observe(
        "find_supersession_attestation", "attestation_calls")
    wrapped["judge_contradiction"] = observe(
        "judge_contradiction", "judge_calls")
    if callable(seams.get("verify_contradiction")):
        wrapped["verify_contradiction"] = observe(
            "verify_contradiction", "verifier_calls")
    return wrapped, observed


def _f5_manifest_block(f5_policy, f5_records, f5_runtime) -> dict:
    """The ``"f5"`` manifest block: policy, retrieval protocol, tallies.

    F5 previously emitted NO module hash, NO prompt hash and NO policy block, so
    the governing settings of an F5 number were recorded nowhere. This is the
    counterpart to the existing ``"f4"`` block.

    The retrieval protocol is recorded in READABLE form, not only as
    ``retrieval_query_hash``: a hash is not a protocol, and nobody can audit what
    was searched from one. Absence is reported as "none found under this protocol"
    and never as "no superseding paper exists" -- SciFact-Open measured that 34.3%
    (251/732) of pooled candidates assumed to hold no evidence actually held it."""
    from .f5_seams import CANDIDATE_CAP, RERANKER
    from .f5_supersession import (
        F5Policy, F5_REPORTABLE, F5_VERIFIER_PROMPT,
    )
    from .f5_activation import ACTIVATION_SCHEMA_VERSION
    from .f5_evidence_store import SOURCE_PACKET_SCHEMA_VERSION
    from .f5_notice import NOTICE_RESOLVER_VERSION
    from .f5_study_cluster import STUDY_CLUSTER_VERSION
    from .f5_controversy_bundle import CONTROVERSY_BUNDLE_SCHEMA_VERSION
    from .f5_candidate_screen import CANDIDATE_SCREEN_VERSION
    from .f5_contradiction_prompt import RESPONSE_PARSER_VERSION
    from .f5_discovery_queue import (
        QUEUE_VERSION, disposition_counts, negative_reason)

    def tally(field):
        out = {}
        for record in f5_records or []:
            value = record.get(field)
            if value is not None:
                key = str(value)
                out[key] = out.get(key, 0) + 1
        return dict(sorted(out.items()))

    def stage_total(field: str) -> int:
        return sum(
            int((record.get("cost_stage_counts") or {}).get(field) or 0)
            for record in (f5_records or [])
            if isinstance(record, dict))

    negative_counts = {}
    for record in f5_records or []:
        status = record.get("retrieval_status")
        adequacy = record.get("retrieval_adequacy")
        if status is None or adequacy is None:
            continue
        reason = negative_reason(str(status), str(adequacy))
        negative_counts[reason] = negative_counts.get(reason, 0) + 1

    activation_records = [
        record for record in (f5_records or [])
        if isinstance(record.get("activation"), dict)
    ]
    activation_applicability_counts: dict[str, int] = {}
    activation_reason_counts: dict[str, int] = {}
    for record in activation_records:
        activation = record["activation"]
        applicability = str(activation.get("applicability"))
        reason_code = str(activation.get("reason_code"))
        activation_applicability_counts[applicability] = (
            activation_applicability_counts.get(applicability, 0) + 1)
        activation_reason_counts[reason_code] = (
            activation_reason_counts.get(reason_code, 0) + 1)

    packet_hashes = set()
    controversy_bundle_hashes = set()
    source_status_counts: dict[str, int] = {}
    notice_resolution_counts: dict[str, int] = {}
    study_cluster_components: list[set[str]] = []
    for record in f5_records or []:
        bundle_hash = record.get("controversy_bundle_sha256")
        if bundle_hash:
            controversy_bundle_hashes.add(str(bundle_hash))
        cited_hash = record.get("cited_source_packet_sha256")
        if cited_hash:
            packet_hashes.add(str(cited_hash))
        cited_status = record.get("cited_source_status")
        if cited_status:
            key = f"cited:{cited_status}"
            source_status_counts[key] = source_status_counts.get(key, 0) + 1
        cited_notice_resolution = record.get("cited_notice_resolution")
        if cited_notice_resolution:
            key = f"cited:{cited_notice_resolution}"
            notice_resolution_counts[key] = notice_resolution_counts.get(key, 0) + 1
        for cluster in record.get("study_clusters") or []:
            if isinstance(cluster, dict):
                evidence_ids = cluster.get("identity_evidence_ids")
                if not evidence_ids:
                    evidence_ids = [
                        f"pmid:{value}" for value in cluster.get("work_ids") or []]
                component = {
                    str(value) for value in evidence_ids
                    if isinstance(value, str) and value}
                if component:
                    study_cluster_components.append(component)
        for candidate in record.get("candidate_assessments") or []:
            candidate_hash = candidate.get("candidate_source_packet_sha256")
            if candidate_hash:
                packet_hashes.add(str(candidate_hash))
            candidate_status = candidate.get("candidate_source_status")
            if candidate_status:
                key = f"candidate:{candidate_status}"
                source_status_counts[key] = source_status_counts.get(key, 0) + 1
            candidate_notice_resolution = candidate.get("candidate_notice_resolution")
            if candidate_notice_resolution:
                key = f"candidate:{candidate_notice_resolution}"
                notice_resolution_counts[key] = notice_resolution_counts.get(key, 0) + 1

    # Claims can retrieve different reports from one version family. Merge the
    # exact identity evidence across the entire run before counting votes; a
    # per-claim union of opaque cluster IDs can count one study twice.
    merged_components: list[set[str]] = []
    for component in study_cluster_components:
        overlaps = [existing for existing in merged_components
                    if existing & component]
        if not overlaps:
            merged_components.append(set(component))
            continue
        combined = set(component)
        for existing in overlaps:
            combined.update(existing)
            merged_components.remove(existing)
        merged_components.append(combined)
    run_study_clusters = []
    for component in sorted(merged_components, key=lambda values: sorted(values)):
        canonical = "\x1f".join(sorted(component)).encode("utf-8")
        run_study_clusters.append({
            "cluster_id": f"run:{hashlib.sha256(canonical).hexdigest()[:20]}",
            "identity_evidence_ids": sorted(component),
        })
    study_cluster_ids = {
        cluster["cluster_id"] for cluster in run_study_clusters}

    policy = f5_policy if f5_policy is not None else F5Policy()
    effective_records = [
        record for record in (f5_records or [])
        if isinstance(record, dict) and "reportable" in record
    ]
    formal_reportable = bool(
        F5_REPORTABLE
        and policy.mode == "deployment"
        and (f5_runtime or {}).get("_verifier_wired") is True
        and effective_records
        and all(record.get("reportable") is True
                for record in effective_records)
    )
    protocols = list((f5_runtime or {}).get("retrieval_protocols") or [])
    attestation_calls = int((f5_runtime or {}).get("attestation_calls") or 0)
    judge_source = (f5_runtime or {}).get("_judge_counter_source")
    def observed_judge_counter(attribute: str, fallback_key: str):
        if judge_source is not None:
            value = getattr(judge_source, attribute, None)
        else:
            value = (f5_runtime or {}).get(fallback_key)
        if (isinstance(value, int) and not isinstance(value, bool)
                and value >= 0):
            return value
        return "not_collected"

    judge_model_calls = observed_judge_counter(
        "model_calls", "judge_model_calls")
    judge_cache_hits = observed_judge_counter(
        "cache_hits", "judge_cache_hits")
    evidence_counters = dict(
        (f5_runtime or {}).get("evidence_store_counters") or {})
    protocol_caps = {
        protocol.get("candidate_cap") for protocol in protocols
        if isinstance(protocol, dict)
        and isinstance(protocol.get("candidate_cap"), int)
        and not isinstance(protocol.get("candidate_cap"), bool)
    }
    reported_candidate_cap = (f5_runtime or {}).get(
        "candidate_cap", "not_collected")
    if reported_candidate_cap == "not_collected" and len(protocol_caps) == 1:
        reported_candidate_cap = next(iter(protocol_caps))
    elif len(protocol_caps) > 1:
        reported_candidate_cap = "inconsistent_across_executed_protocols"
    block = {
        "mode": policy.mode,
        "deploy_path_a": policy.deploy_path_a,
        "reportable": formal_reportable,
        "contradiction_prompt_version": policy.contradiction_prompt_version,
        "response_parser_version": RESPONSE_PARSER_VERSION,
        "verifier_prompt_version": policy.verifier_prompt_version,
        "verifier_prompt_sha256": _sha256_text(F5_VERIFIER_PROMPT),
        "generator_model_id": policy.generator_model_id,
        "verifier_model_id": policy.verifier_model_id,
        "verifier_wired": (f5_runtime or {}).get("_verifier_wired") is True,
        "verifier_calls": int((f5_runtime or {}).get("verifier_calls") or 0),
        "activation_schema_version": ACTIVATION_SCHEMA_VERSION,
        "candidate_screen_version": CANDIDATE_SCREEN_VERSION,
        "candidate_screen_enabled": policy.candidate_screen_enabled,
        "source_packet_schema_version": SOURCE_PACKET_SCHEMA_VERSION,
        "source_packet_hashes": sorted(packet_hashes),
        "source_packet_count": len(packet_hashes),
        "controversy_bundle_schema_version": CONTROVERSY_BUNDLE_SCHEMA_VERSION,
        "controversy_bundle_hashes": sorted(controversy_bundle_hashes),
        "controversy_bundle_count": len(controversy_bundle_hashes),
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "notice_resolver_version": NOTICE_RESOLVER_VERSION,
        "notice_resolution_counts": dict(sorted(notice_resolution_counts.items())),
        "study_cluster_version": STUDY_CLUSTER_VERSION,
        "study_cluster_ids": sorted(study_cluster_ids),
        "study_cluster_count": len(study_cluster_ids),
        "study_clusters": run_study_clusters,
        "activation_applicability_counts": dict(sorted(
            activation_applicability_counts.items())),
        "activation_reason_counts": dict(sorted(activation_reason_counts.items())),
        "activation_claims_considered": len(activation_records),
        "activation_claims_activated": sum(
            count for applicability, count
            in activation_applicability_counts.items()
            if applicability in {"eligible", "uncertain"}),
        "activation_claims_searched": sum(
            1 for record in activation_records
            if record.get("retrieval_status") is not None),
        "activation_not_applicable_claims": activation_applicability_counts.get(
            "not_applicable", 0),
        "comparability_policy_version": policy.comparability_policy_version,
        "retrieval_calls": int((f5_runtime or {}).get("retrieval_calls") or 0),
        "retrieval_status_counts": tally("retrieval_status"),
        "retrieval_adequacy_counts": tally("retrieval_adequacy"),
        "negative_reason_counts": dict(sorted(negative_counts.items())),
        "candidate_cap": reported_candidate_cap,
        "deep_comparison_budget": policy.max_deep_comparisons,
        "budget_exhausted_claims": sum(
            record.get("budget_exhausted") is True
            for record in (f5_records or []) if isinstance(record, dict)),
        "reranker": RERANKER,
        "queue_version": QUEUE_VERSION,
        "disposition_counts": disposition_counts(f5_records),
        "records_emitted": len(f5_records or []),
        "attestation_lookup_performed": bool(attestation_calls),
        "attestation_calls": attestation_calls,
        "attestation_lookup_note": (
            f"The injected attestation seam was called {attestation_calls} time(s)."
            if attestation_calls else
            "No attestation lookup call was observed; seam provenance is not asserted."),
        "contradiction_judge_calls": int(
            (f5_runtime or {}).get("judge_calls") or 0),
        "stage_counters": {
            "retrieval_calls": int(
                (f5_runtime or {}).get("retrieval_calls") or 0),
            "evidence_metadata_calls": int(
                evidence_counters.get("metadata_calls") or 0),
            "evidence_abstract_calls": int(
                evidence_counters.get("abstract_calls") or 0),
            "fulltext_attempts": int(
                evidence_counters.get("fulltext_attempts") or 0),
            "fulltext_successes": int(
                evidence_counters.get("fulltext_successes") or 0),
            "fulltext_failures": int(
                evidence_counters.get("fulltext_failures") or 0),
            "evidence_cache_hits": int(
                evidence_counters.get("cache_hits") or 0),
            "pairwise_judge_requests": int(
                (f5_runtime or {}).get("judge_calls") or 0),
            "pairwise_model_calls": judge_model_calls,
            "pairwise_cache_hits": judge_cache_hits,
            "abstract_screen_calls": stage_total("abstract_screen_calls"),
            "candidates_retrieved": stage_total("candidates_retrieved"),
            "candidates_structurally_admissible": stage_total(
                "candidates_structurally_admissible"),
            "screen_plausible": stage_total("screen_plausible"),
            "screen_clear_mismatch": stage_total("screen_clear_mismatch"),
            "screen_uncertain": stage_total("screen_uncertain"),
            "candidates_entering_deep_comparison": stage_total(
                "candidates_entering_deep_comparison"),
            "deep_comparison_calls": stage_total("deep_comparison_calls"),
            "candidates_budget_skipped": stage_total(
                "candidates_budget_skipped"),
            "candidates_aggregated": stage_total("candidates_aggregated"),
        },
        "cost_counters": {
            "model_calls": judge_model_calls,
            "model_calls_avoided_by_cache": judge_cache_hits,
            "input_tokens": "not_collected",
            "output_tokens": "not_collected",
            "cost_usd": "not_collected",
        },
        "production_evidence_builder": True,
        "real_data_runs_completed": 0,
        "audit_convergence": "formal_positive_requires_independent_verifier",
        "note": (
            "F5 has a concrete PubMed production runtime and evidence builder. "
            "real_data_runs_completed remains zero until an actual governed run "
            "is executed; this code-path test does not invent that measurement."
        ),
    }
    if protocols:
        block["retrieval_protocol"] = protocols[0] if len(protocols) == 1 else protocols
        block["retrieval_protocols_executed"] = len(protocols)
    else:
        block["retrieval_protocol_note"] = (
            "The injected retrieval seam exposed no executed-protocol record; "
            "no module default is substituted for what actually ran.")
    return block


class _OrderedClaimsCache:
    """Per-document, thread-safe claim extraction with serial-equivalent reuse.

    References are registered in document order before their worker is
    submitted.  For one citing sentence, only the earliest registered reference
    may call the extractor.  A successful result is reused by every later
    reference, exactly like the former serial ``dict`` cache.  A failed attempt
    is *not* cached: its owner raises and the next reference gets the retry,
    preserving per-reference retry ownership; the caller records the exhausted
    extraction as a reviewable stage hold.

    Different citing sentences may extract concurrently.  That is the useful
    parallelism; allowing two calls for the same sentence would spend more,
    permit claim drift inside one co-citation group, and change results.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._waiting: dict[str, list[int]] = {}
        self._inflight: set[str] = set()
        self._values: dict[str, list] = {}

    def register(self, sentence: str, order: int) -> None:
        with self._condition:
            if sentence in self._values:
                return
            queue = self._waiting.setdefault(sentence, [])
            if queue and order <= queue[-1]:
                raise ValueError(
                    "claims-cache registrations must follow document order")
            queue.append(order)
            self._condition.notify_all()

    def get_or_extract(self, sentence: str, order: int, extractor) -> list:
        with self._condition:
            while True:
                if sentence in self._values:
                    return list(self._values[sentence])
                queue = self._waiting.get(sentence) or []
                if (queue and queue[0] == order
                        and sentence not in self._inflight):
                    self._inflight.add(sentence)
                    break
                self._condition.wait()

        try:
            claims = jb.extract_atomic_claims(sentence, extractor=extractor)
        except BaseException:
            with self._condition:
                queue = self._waiting.get(sentence) or []
                if queue and queue[0] == order:
                    queue.pop(0)
                self._inflight.discard(sentence)
                self._condition.notify_all()
            raise

        with self._condition:
            self._values[sentence] = list(claims)
            self._waiting.pop(sentence, None)
            self._inflight.discard(sentence)
            self._condition.notify_all()
        return list(claims)


def _merge_marker_scope_counts(target: dict, source: dict) -> None:
    """Fold one worker's marker-scope tally into the run in commit order."""
    for key, value in source.items():
        if isinstance(value, dict):
            destination = target[key]
            for subkey, amount in value.items():
                destination[subkey] = destination.get(subkey, 0) + amount
        else:
            target[key] += value


def run_natural_judgment(
    xml_dir: str, out_dir: str, *,
    extractor, coverage_judge, fetch_abstract, fetch_reflist=None,
    preband_disposition=None, pubtypes_lookup: "PubtypesLookup | None" = None,
    discriminator_call_llm=None, f4_verifier_call_llm=None,
    f4_verifier_model_id: str = "",
    f3_fetch_reflist=None, f3_resolve_pmcid=None,
    f4_policy=None, f3_policy=None,
    f5_seams=None, f5_evidence_builder=None, f5_policy=None,
    f7_seams=None, f7_evidence_builder=None, f7_policy=None,
    fetch_fulltext=None, coverage_judge_v3=None, fetch_openalex_abstract=None,
    model: str = "", email: str = DEFAULT_EMAIL, api_key: str = "",
    max_docs: "int | None" = None, session=None,
    chain_genesis: str = "",
    assistant_prefill: str = "", stop_sequences: tuple = (), temperature=None,
    code_commit: str = "", corpus_manifest_path: str = "",
    citation_selection_path: str = "",
    require_full_coverage: bool = False, require_reportable: bool = False,
    production: bool = False, max_workers: int = 1,
) -> dict:
    """Run the F3-F7 band end-to-end over a dir of natural PMC-OA citing papers.

    Writes in ``out_dir``:
      * ``judgment_predictions.jsonl``           -- ONE durable record per pair (all pairs).
      * ``judgment_run_record_hashes.jsonl``     -- whole-record hash chain sidecar.
      * ``judgment_run_manifest.json``           -- pins + funnel counts + chain anchor
                                                    (status in_progress/complete; atomic writes).
      * ``judgment_band_annotation_queue.jsonl`` -- blind payload for scoreable pairs only.

    ``preband_disposition``: dict[citation_id->label] or a path to an F1/F2 JSONL.
    ``pubtypes_lookup``: optional pmid->pubtypes callable for the review check (F3
    reflist input; not a coverage input). When absent, cited_is_review stays None.

    F4 wiring: a distinct ``f4_verifier_call_llm`` + nonblank ``f4_verifier_model_id``
    (with the generator's id in ``model``) runs F4 in formal (reportable) mode;
    otherwise F4 runs in development mode, non-reportable. An explicit ``f4_policy``
    overrides this construction. Configuration is validated BEFORE any output file
    is created; a config defect aborts the run (it is never per-pair quarantine).

    ``chain_genesis``: the frozen chain tip of a prior COMPLETE segment, when this
    run extends it as a new segment (fresh out_dir). Empty for a first segment.

    ADAPTER IDENTITY (``model`` / ``assistant_prefill`` / ``stop_sequences`` /
    ``temperature``). Every number this layer emits is conditional on the adapter
    that produced it, and this module makes no model call itself -- every one goes
    through an injected callable -- so it cannot observe them and must be told.
    They are RECORDED, never used. ``temperature`` takes ``None`` for absent
    rather than ``""``: DEC-046B pins ``temperature=0``, and 0 is both a real value
    and a falsy one, so a truthiness guard would drop exactly the pinned value it
    exists to record. An unsupplied value is recorded as ABSENT, never guessed.
    ``run_band`` gained these on 2026-08-12; this entry point -- the one that
    produces every published F3-F7 number -- did not, which is the gap this
    parameter set closes.

    JOIN CONTRACT (``preband_disposition`` / ``require_full_coverage``). The
    corpus id domain is collected by a PREFLIGHT parse before any output file
    exists, the disposition is validated against it, and a zero-overlap or
    no-disposition join ABORTS instead of completing as a clean empty run. Set
    ``require_full_coverage`` to additionally abort when any corpus citation_id
    is absent from the disposition. An empty corpus, or one whose every document
    fails to parse, aborts for the same reason.

    ``code_commit`` / ``corpus_manifest_path``: bound into the manifest so a
    published number can be tied to the Band-2 tree and the frozen corpus bytes
    it was computed over.

    PRODUCTION (``production=True``) is the single switch the launcher sets. It
    runs a MANDATORY PREFLIGHT before any output file exists -- canonical file
    artifact only, complete id coverage, disposition corpus digest equal to the
    corpus being judged, zero parse failures, nonempty code_commit and model --
    and it REFUSES resume and ``max_docs``. Each of those conditions could
    otherwise be violated by a run that still returned ``status="complete"``.
    It also implies ``require_full_coverage`` and ``require_reportable``.

    Resume is refused because this module writes predictions per reference but
    checkpoints per document: an interrupted document is replayed and its rows
    appended a second time, a resumed manifest's counters cover only the final
    invocation, and a resumed segment can combine different models, temperatures
    and commits under one manifest. Recovery is a FRESH out_dir and a restart.

    REPORTABILITY (``require_reportable``). A run can complete cleanly and still
    be unfit to publish: a development pass, a ``max_docs`` slice, a resumed
    segment, a dict-injected fixture, a disposition built over a different
    corpus. ``manifest["reportability"]`` records the verdict and every failed
    clause on EVERY run; ``require_reportable=True`` additionally raises. The
    production launcher sets it. Its clauses are listed in
    ``preband_contract.reportability_report``; the two that are not obvious are
    the corpus cross-binding (the disposition's corpus digest must EQUAL the
    corpus actually judged -- recording both without comparing them lets a
    disposition built over corpus A run against corpus B at full coverage) and
    the single-segment rule (resume writes predictions per reference but
    checkpoints per document, so an interrupted document is replayed and its
    rows appended twice; both defects are pinned by strict xfails in
    ``test_adversarial_judgment_run``).

    PARALLEL EXECUTION (``max_workers``).  Values above one overlap independent
    reference work inside a document, while the co-citation barrier, counter
    folds, durable records, annotation queue and hash chain all commit in the
    original parser order.  Prompts, evidence scope, parsers, policies and retry
    rules are unchanged.  F5/F7 Phase 2 remains serial when either stateful seam
    bundle is wired; Phase 1 still overlaps safely.
    """
    if (isinstance(max_workers, bool) or not isinstance(max_workers, int)
            or not 1 <= max_workers <= 32):
        raise ValueError("max_workers must be an integer from 1 through 32")
    if (f5_seams is None) != (f5_evidence_builder is None):
        raise ValueError(
            "f5_seams and f5_evidence_builder must be supplied together; a "
            "half-wired F5 path cannot publish coherent provenance")
    if (f7_seams is None) != (f7_evidence_builder is None):
        raise ValueError(
            "f7_seams and f7_evidence_builder must be supplied together; a "
            "half-wired F7 path cannot publish coherent provenance")
    if f5_seams is not None and not isinstance(f5_seams, dict):
        raise ValueError("f5_seams must be a dict")
    if (production and f5_seams is not None
            and f5_seams.get("verify_contradiction") is f5_seams.get(
                "judge_contradiction")):
        raise ValueError(
            "production F5 generator and verifier must be distinct")
    f5_runtime = {"retrieval_calls": 0, "attestation_calls": 0,
                  "judge_calls": 0, "verifier_calls": 0,
                  "retrieval_protocols": []}
    if f5_seams is not None:
        f5_seams, f5_runtime = _instrument_f5_seams(f5_seams)

    # --- F4 configuration, validated up front (item 3): outside the per-pair
    # try/except, before any output file exists.
    if f4_policy is not None:
        eff_f4_policy = f4_policy
    elif discriminator_call_llm is not None:
        eff_f4_policy = F4Policy(mode="formal", generator_model_id=model,
                                 verifier_model_id=(f4_verifier_model_id or model))
    else:
        eff_f4_policy = F4Policy(mode="development", generator_model_id=model)
    validate_f4_config(eff_f4_policy, discriminator_call_llm, f4_verifier_call_llm,
                       require_generator=False)
    # F5 config validated up front too (a policy defect aborts the run, never a
    # per-pair quarantine). Only when F5 is actually wired.
    if f5_seams is not None:
        from .f5_supersession import validate_f5_policy
        effective_f5_policy = f5_policy if f5_policy is not None else F5Policy()
        validate_f5_policy(effective_f5_policy)
        if production:
            if effective_f5_policy.mode != "deployment":
                raise ValueError(
                    "production F5 requires F5Policy(mode='deployment')")
            if not callable(f5_seams.get("verify_contradiction")):
                raise ValueError(
                    "production F5 requires an independent verifier seam")
            if f5_seams.get("verify_contradiction") is f5_seams.get(
                    "judge_contradiction"):
                raise ValueError(
                    "production F5 generator and verifier must be distinct")
            if effective_f5_policy.generator_model_id != model:
                raise ValueError(
                    "production F5 generator_model_id must equal the run model")
            generator_model = str(getattr(
                f5_seams.get("judge_contradiction"), "model_id", "") or "")
            verifier_model = str(getattr(
                f5_seams.get("verify_contradiction"), "model_id", "") or "")
            if generator_model != effective_f5_policy.generator_model_id:
                raise ValueError(
                    "production F5 generator seam model_id does not match policy")
            if verifier_model != effective_f5_policy.verifier_model_id:
                raise ValueError(
                    "production F5 verifier seam model_id does not match policy")
    # F7 config validated up front for the same reason, and against a sharper
    # failure: F7's DEFAULT policy is one under which F7 cannot fire at all.
    # ``authorities_json`` defaults to ``"{}"`` -- valid JSON, a legal empty
    # table, parsed without complaint -- and an empty table sends every claim to
    # ``authority_not_locked``. A run could therefore wire both F7 seams, pay for
    # the model calls, and publish ``wired: true, fired: 0`` beside an
    # ``authorities_sha256`` of 44136fa3... (which is just sha256("{}"), and
    # nothing compares it to anything), while never having been able to produce
    # an F7 for any input. That is indistinguishable from an honest zero, so it
    # refuses here -- before any output file exists, never as a per-pair
    # quarantine -- exactly like the full-text XOR gate below.
    #
    # THE SINGLE DEFINITION of "F7 is wired", used by both the f7 manifest block
    # and seam_status.F7 so the two cannot disagree. It is the same condition
    # judge_pair_finish actually branches on.
    f7_wired = f7_seams is not None and f7_evidence_builder is not None
    f7_reachability_report = None
    if f7_seams is not None:
        from .f7_entity import validate_f7_policy
        f7_reachability_report = validate_f7_policy(
            f7_policy if f7_policy is not None else F7Policy())
        if production:
            from .f7_seams import validate_production_f7_configuration
            validate_production_f7_configuration(
                seams=f7_seams, evidence_builder=f7_evidence_builder,
                policy=f7_policy)
    # The full-text path needs BOTH seams, validated UP FRONT like every other
    # config defect -- before any output file exists, so it can never be mistaken
    # for a per-pair quarantine. Supplying only one would either judge full text
    # with the abstract-scoped prompt or fetch a body nothing reads; both are scope
    # changes no output would record, which is why this raises instead of
    # half-enabling. Same rule and same wording as run_band's gate.
    if (fetch_fulltext is None) != (coverage_judge_v3 is None):
        raise ValueError(
            "the full-text path needs BOTH fetch_fulltext and coverage_judge_v3; "
            "supplying one alone would silently judge full text with the "
            "abstract-scoped prompt, or fetch a body nothing reads")
    fulltext_path = fetch_fulltext is not None

    # THREE DISTINGUISHABLE TEMPERATURE STATES, and nothing else. A number means
    # it was sent; the literal "unsupported" means the provider rejects the
    # parameter and it was NOT sent (DEC-070); the key absent from the manifest
    # means it was never recorded. A typo like "0" or "unsupportd" would look
    # like a fourth state to a reader and is refused here rather than written.
    if not (temperature is None
            or isinstance(temperature, (int, float))
            or temperature == TEMPERATURE_UNSUPPORTED):
        raise ValueError(
            f"temperature must be a number, None, or {TEMPERATURE_UNSUPPORTED!r} "
            f"(DEC-046B / DEC-070); got {temperature!r}")

    pred_path = os.path.join(out_dir, "judgment_predictions.jsonl")
    queue_path = os.path.join(out_dir, "judgment_band_annotation_queue.jsonl")
    review_path = os.path.join(out_dir, "human_review_required.jsonl")
    manifest_path = os.path.join(out_dir, "judgment_run_manifest.json")
    checkpoint_path = os.path.join(out_dir, "judgment_run_checkpoint.jsonl")
    sidecar_path = os.path.join(out_dir, "judgment_run_record_hashes.jsonl")
    groups_path = os.path.join(out_dir, "judgment_run_cocitation_groups.jsonl")

    corpus_bindings: dict = {}

    # --- THE JOIN, validated before any output file exists --------------
    # A wholesale id mismatch used to complete as status="complete" with
    # accounting_ok=true and an empty annotation queue -- indistinguishable from
    # a successful run. So the disposition is validated, the corpus id domain is
    # collected by a preflight parse, and the join is enforced UP FRONT, in the
    # same place and for the same reason as the F4/F5/full-text config gates.
    disp_obj = _load_disposition(preband_disposition)
    disp = disp_obj.mapping if disp_obj is not None else None

    files = sorted(fn for fn in os.listdir(xml_dir)
                   if fn.endswith((".xml", ".nxml")))
    # Resume-safe: only the docs this segment will actually process define the
    # expected id domain, so accounting stays honest across a resumed run.
    done = jb._load_checkpoint(checkpoint_path)
    # Production preflight phase 1 -- single fresh segment. Checked BEFORE the
    # corpus is scanned: an exhausted out_dir otherwise surfaces as a confusing
    # "empty corpus" error and the resume problem is never named.
    if production:
        pc.assert_production_launch_shape(
            max_docs=max_docs, out_dir=out_dir, chain_genesis=chain_genesis)
    pending = [fn for fn in files if jb._pmcid_from_filename(fn) not in done]
    to_process = pending[:max_docs] if max_docs is not None else pending
    expected_ids, expected_per_doc, preflight_parse_failures = (
        pc.collect_expected_ids(xml_dir, to_process, parse_pmc_xml,
                                jb._pmcid_from_filename))
    # THE SELECTION IS VERIFIED AND INTERSECTED BEFORE ANY OUTPUT FILE EXISTS,
    # against the same preflight parse the join gate uses. A selection naming ids
    # this corpus does not contain would otherwise produce a silently SMALLER run
    # that still completes cleanly -- the exact failure shape the join gate exists
    # to prevent, one layer earlier.
    selection = (csel.load_selection(citation_selection_path)
                 if citation_selection_path else None)
    selection_accounting = csel.assert_selection_covered(selection, expected_ids)
    if selection is not None:
        # From here on the SELECTED ids are the population: the join, the
        # coverage requirement and every denominator must all mean the same set,
        # or the run reports fractions of two different things.
        expected_ids = set(selection.ids)
        expected_per_doc = {
            pmcid: sum(1 for cid in selection.ids
                       if cid.split(":", 1)[0] == pmcid)
            for pmcid in expected_per_doc
        }
    join_acc = pc.join_accounting(disp_obj, expected_ids)
    pc.enforce_join(join_acc, disp=disp_obj,
                    require_full_coverage=require_full_coverage or production)

    # PRODUCTION PREFLIGHT -- mandatory, and before any output file exists, so a
    # misconfigured production run costs nothing. Every condition here is also a
    # reportability clause, but reportability is checked on the FINISHED manifest
    # (after the compute is spent, and only if the caller asks). Checking up
    # front makes them mandatory rather than advisory.
    if production:
        corpus_bindings.update(pc.assert_production_preflight(
            disp=disp_obj, join_acc=join_acc,
            parse_failures=preflight_parse_failures,
            code_commit=code_commit, model=model,
            corpus_manifest_path=corpus_manifest_path, xml_dir=xml_dir))

    # Load-bearing module hashes, captured BEFORE execution so they describe the
    # bytes that actually ran. Read after the loop, they could describe a module
    # edited mid-run -- provenance for code that never executed.
    module_hashes = _module_hashes(fulltext_path, f5_seams, f7_seams)

    os.makedirs(out_dir, exist_ok=True)

    # Resume gate: exact chain replay against sidecar + manifest anchor. Raises
    # on complete/tamper/torn state; a fresh dir starts at the genesis link.
    prev_link, chain_count, genesis = _recover_chain(
        out_dir, manifest_path, pred_path, sidecar_path, chain_genesis)

    counts: dict[str, int] = {}          # one entry per emitted record; sums to refs_seen
    preband_by_label: dict[str, int] = {}  # auxiliary funnel; NOT summed into totals
    # EVERY Band-1 label, whatever gate excluded the row FIRST. preband_by_label
    # above counts only rows the PRE-BAND gate excluded, and jb.exclusion_reason
    # (no citance / no cited PMID) runs before it -- so a reference Band 1
    # labelled F8 that also lacks a citing sentence is booked as
    # excluded_no_citance and its label is counted nowhere. That is not a rare
    # corner: schema.py records that F1/F2/F8 are existence/metadata-level faults
    # carrying no atomic claims, so the references those labels legitimately fire
    # on are PRECISELY the ones missing a citance. The only per-label F8 counter
    # in the run was therefore biased against F8 by construction. Kept separate
    # rather than folded into preband_by_label, whose "excluded BY the pre-band
    # gate" meaning is load-bearing elsewhere.
    preband_label_census: dict[str, int] = {}
    f4_counts = {"eligible_claims": 0, "unassessed_no_usable_abstract": 0,
                 "generator_calls": 0, "verifier_calls": 0}
    f4_outcomes: dict[str, int] = {}
    f4_hold_reasons: dict[str, int] = {}
    f4_scope_pairs: dict[str, int] = {}
    stage_failure_counts: dict[str, int] = {}
    stage_failure_records = 0
    # Full-text retrieval funnel. Separate from `counts` for the same reason
    # f4_counts is: `counts` sums to the record total and admits no statistics.
    fulltext_counts = {"no_usable_fulltext": 0}
    # MARKER ATTRIBUTION accounting. A (reference, claim) pair that was never
    # asked has to be countable, or "we narrowed the question" and "the reference
    # answered" become the same number.
    scope_counts = marker_scope.new_counts()
    # Co-citation accounting. Its OWN tally, never `counts`: `counts` is one entry
    # per emitted record and is summed into `total_records`, so a statistic there
    # would corrupt the record count rather than add information.
    cocitation_counts = {"groups": 0, "sentence_groups": 0,
                         "cocitation_groups": 0,
                         "members_in_cocitation_groups": 0,
                         "group_claims_covered": 0,
                         "group_claims_uncovered": 0,
                         "group_claims_unknown": 0}
    group_sizes: dict = {}

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    def progress_manifest() -> dict:
        return {
            "layer": "F3-F7 natural-paper orchestration (judgment_run)",
            "status": "in_progress",
            "model": model,
            **({"parallel_execution": {
                "max_workers": max_workers, "ordered_commit": True}}
               if max_workers > 1 else {}),
            "chain_genesis": genesis,
            "chain_tip": prev_link,
            "chain_record_count": chain_count,
            "trust_boundary": _TRUST_BOUNDARY,
        }

    # The in-progress manifest exists (atomically) from before the first record
    # and is refreshed at every checkpoint boundary so its tip/count track the
    # sidecar; it is only ever advanced over chain-validated records.
    _write_json_atomic(manifest_path, progress_manifest())

    refs_seen = 0
    docs_processed = 0
    scoreable_records = 0
    human_review_records = 0
    executed_ids: set = set()
    emitted_labels: dict = {}
    finding_labels: dict = {}
    pubtype_cache: dict = {}
    # One entry per emitted record, keyed by the CLOSED terminal vocabulary, and
    # the human-review split by its exact reason. Both are what the run is
    # reported by, so they are counted at emit rather than derived afterwards
    # from a file that may have been rotated.
    terminal_counts: dict = {}
    human_review_by_reason: dict = {}
    # References the taxonomy has NOTHING TO SAY ABOUT, as opposed to ones it
    # tried to judge and could not. Reported so the count reads as a methods
    # sentence rather than as a failure rate.
    scope_exclusions: dict = {}
    # Which identifier kind each reference entered the band on. The point of the
    # v2 handoff is that "doi" is now a nonzero bucket instead of a silent drop.
    identifier_kinds: dict = {}
    # WHERE EACH ABSTRACT CAME FROM. A PubMed abstract and an OpenAlex
    # reconstruction of publisher metadata are different evidence and are never
    # summed as one number.
    evidence_abstract_sources: dict = {}
    # How often the full-text path degraded to a real abstract judgment, and how
    # many absence claims the asymmetry guard refused. A nonzero coercion count
    # on the production judge means an injected judge bypassed
    # `aggregate_coverage`, and is worth seeing.
    abstract_fallback_counts = {"records": 0, "coerced_false_to_none": 0}
    # EVERY billed attempt, retries included. A retry that is not counted is a
    # paid call the accounting cannot see.
    paid_call_totals = {"total": 0, "retries": 0}
    paid_call_by_stage: dict = {}
    # STAGES THAT RAN UNCOUNTED, carried up from the per-record ledgers. Without
    # this the fix stops one level short of the artifact that matters: the
    # manifest would publish a total that looks complete over records that were
    # never fully counted, which is the same undercount one layer up.
    paid_call_unmetered: dict = {}
    paid_call_unmetered_records = 0

    pred_fh = open(pred_path, "a", encoding="utf-8")
    queue_fh = open(queue_path, "a", encoding="utf-8")
    review_fh = open(review_path, "a", encoding="utf-8")
    ckpt_fh = open(checkpoint_path, "a", encoding="utf-8")
    side_fh = open(sidecar_path, "a", encoding="utf-8")
    groups_fh = open(groups_path, "a", encoding="utf-8")

    # Every F5 record produced this run, for the discovery queue and the manifest
    # tallies. Collected at emit so it follows the same crash invariant as the
    # predictions themselves.
    f5_records_all: list = []
    # F7's audit records, collected at emit for the same reason: F7 can own the
    # published label, so its provenance block must be built from what actually
    # ran, not from the policy alone.
    f7_records_all: list = []
    sentence_partition_diagnostics: dict[str, dict] = {}
    sentence_partition_affected_records = 0

    def emit(rec: dict) -> None:
        nonlocal prev_link, chain_count, sentence_partition_affected_records
        nonlocal stage_failure_records, paid_call_unmetered_records
        # THE TERMINAL OUTCOME IS STAMPED BEFORE THE RECORD IS HASHED, so the
        # chain covers the conclusion and not merely the inputs to it. Every
        # record gets one, from the closed vocabulary, with its reason.
        outcome, reason = tox.resolve(rec)
        tox.assert_valid(outcome, reason)
        rec["terminal_outcome"] = outcome
        rec["terminal_reason"] = reason
        rec["terminal_outcome_version"] = tox.TERMINAL_OUTCOME_VERSION
        rec["human_review_required"] = tox.is_human_review(outcome)
        rec.setdefault("claim_input_status",
                       tox.claim_input_status(rec.get("citing_sentence")))
        rec.setdefault("retry_history", [])
        rec.setdefault("paid_calls", {"total": 0, "retries": 0, "by_stage": {}})
        rec.setdefault("stage_failures", [])
        terminal_counts[outcome] = terminal_counts.get(outcome, 0) + 1
        if rec["human_review_required"]:
            human_review_by_reason[reason] = (
                human_review_by_reason.get(reason, 0) + 1)
        if reason in tox.TERMINAL_SCOPE_EXCLUSION_REASONS:
            scope_exclusions[reason] = scope_exclusions.get(reason, 0) + 1
        source = str((rec.get("evidence") or {}).get("cited_abstract_source") or "")
        if source:
            evidence_abstract_sources[source] = (
                evidence_abstract_sources.get(source, 0) + 1)
        if (rec.get("abstract_scope_fallback") or {}).get("fired"):
            abstract_fallback_counts["records"] += 1
            abstract_fallback_counts["coerced_false_to_none"] += int(
                rec["abstract_scope_fallback"].get("coerced_false_to_none") or 0)
        ledger = rec.get("paid_calls") or {}
        paid_call_totals["total"] += int(ledger.get("total") or 0)
        paid_call_totals["retries"] += int(ledger.get("retries") or 0)
        for stage_name, n in (ledger.get("by_stage") or {}).items():
            paid_call_by_stage[stage_name] = (
                paid_call_by_stage.get(stage_name, 0) + int(n or 0))
        unmetered = ledger.get("unmetered_stages") or []
        if unmetered:
            paid_call_unmetered_records += 1
            for stage_name in unmetered:
                paid_call_unmetered[stage_name] = (
                    paid_call_unmetered.get(stage_name, 0) + 1)
        f5_records_all.extend(rec.get("f5_records") or [])
        f7_records_all.extend(rec.get("f7_records") or [])
        stage_failures = rec.get("stage_failures") or []
        if stage_failures:
            stage_failure_records += 1
        for failure in stage_failures:
            stage = str(failure.get("stage") or "unknown")
            stage_failure_counts[stage] = stage_failure_counts.get(stage, 0) + 1
        failures = rec.get("sentence_partition_failures") or []
        if failures:
            sentence_partition_affected_records += 1
        for failure in failures:
            key = str(failure.get("text_sha256") or _canonical_sha256(failure))
            sentence_partition_diagnostics[key] = dict(failure)
        # Write order pins the crash invariant: predictions >= sidecar >= manifest.
        pred_fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        pred_fh.flush()
        psha = _canonical_sha256(rec)
        prev_link = _chain_link(prev_link, psha)
        chain_count += 1
        side_fh.write(json.dumps(
            {"citation_id": rec["citation_id"], "prediction_sha256": psha,
             "link": prev_link}, ensure_ascii=False) + "\n")
        side_fh.flush()
        bump(rec["disposition"])
        if rec.get("label"):
            emitted_labels[rec["label"]] = emitted_labels.get(rec["label"], 0) + 1
        for finding in rec.get("findings") or []:
            finding_labels[finding] = finding_labels.get(finding, 0) + 1
        # Mechanical F4 counters, derived from the audit records themselves.
        for sr in rec.get("strength_records") or []:
            derived = sr.get("derived")
            if derived:
                f4_outcomes[derived] = f4_outcomes.get(derived, 0) + 1
            reason = sr.get("reason")
            if derived == "UNJUDGEABLE" and reason:
                f4_hold_reasons[reason] = f4_hold_reasons.get(reason, 0) + 1
            scope_key = (
                f"coverage={sr.get('coverage_evidence_scope', 'unknown')}|"
                f"f4={sr.get('f4_evidence_scope', 'unknown')}")
            f4_scope_pairs[scope_key] = f4_scope_pairs.get(scope_key, 0) + 1
            if sr.get("assessed"):
                f4_counts["eligible_claims"] += 1
                f4_counts["generator_calls"] += 1
                if "verifier_response" in sr:
                    f4_counts["verifier_calls"] += 1
            elif sr.get("reason") == "no_usable_abstract":
                f4_counts["unassessed_no_usable_abstract"] += 1
        # TWO QUEUES, TWO POPULATIONS, NO OVERLAP.
        #
        # The annotation queue is for BLIND GOLD-LABELLING, so it may hold only
        # rows that carry an F3-F7 finding -- something a human is being asked to
        # agree or disagree with. It used to be filled from `_SCOREABLE`, which
        # included `held_no_atomic_claims`: 209 broken parses went to annotators
        # as "no claim here, please confirm", which is not a judgment anyone can
        # make about a citance that reads "5,8,10,19".
        #
        # The human-review queue is for rows where THIS ENGINE is broken. NONE and
        # UNJUDGEABLE are conclusions, not work items, and enter neither file.
        if tox.carries_finding(rec["terminal_outcome"]):
            nonlocal scoreable_records
            scoreable_records += 1
            payload = jb.annotation_payload({
                "item_key": rec["citation_id"],
                "citing_sentence": rec["citing_sentence"],
                "cited_pmid": rec["cited_pmid"],
                "atomic_claims": rec["atomic_claims"],
                "evidence": rec["evidence"],
            })
            queue_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            queue_fh.flush()
        elif rec["human_review_required"]:
            nonlocal human_review_records
            human_review_records += 1
            review_fh.write(json.dumps({
                "citation_id": rec["citation_id"],
                "citing_pmcid": rec.get("citing_pmcid"),
                "terminal_outcome": rec["terminal_outcome"],
                "reason": rec["terminal_reason"],
                "claim_input_status": rec.get("claim_input_status"),
                "citing_sentence": rec.get("citing_sentence"),
                "cited_pmid": rec.get("cited_pmid"),
                "resolved_identifier": rec.get("resolved_identifier") or {},
                "atomic_claims": rec.get("atomic_claims") or [],
                "evidence_scope": rec.get("evidence_scope"),
                "evidence_status": rec.get("evidence_status"),
                "stage_failures": rec.get("stage_failures") or [],
                "retry_history": rec.get("retry_history") or [],
                "paid_calls": rec.get("paid_calls") or {},
                "parse_failure": rec.get("parse_failure") or {},
                # The bytes that failed, secrets already stripped on the way in.
                # A contract failure a human cannot see is a ticket nobody can act
                # on.
                "failed_model_responses": rec.get("failed_model_responses") or [],
            }, ensure_ascii=False) + "\n")
            review_fh.flush()

    worker_pool = (ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="cre-judgment")
        if max_workers > 1 else None)
    f7_parallel_safe = (f7_seams is None
                        or getattr(f7_seams, "thread_safe", False) is True)
    phase2_parallel = (
        worker_pool is not None and f5_seams is None and f7_parallel_safe)

    def coverage_attempt(item: dict, claims_cache, order: int) -> tuple:
        # Each worker owns its tally.  Shared counter mutation would make a
        # correct numeric result depend on thread timing; the main thread folds
        # these in parser order after each future resolves.
        local_scope_counts = marker_scope.new_counts()
        try:
            rec, claims, verdicts = judge_pair_coverage(
                item, extractor=extractor, coverage_judge=coverage_judge,
                fetch_abstract=fetch_abstract, fetch_reflist=fetch_reflist,
                fetch_fulltext=fetch_fulltext,
                fetch_openalex_abstract=fetch_openalex_abstract,
                coverage_judge_v3=coverage_judge_v3,
                claims_cache=claims_cache, claims_cache_order=order,
                scope_counts=local_scope_counts)
            return rec, (item, claims, verdicts), local_scope_counts, None
        except ValueError as exc:
            return None, item, local_scope_counts, exc

    def finish_attempt(rec: dict, item: dict, claims, verdicts,
                       flags) -> tuple:
        try:
            finished = judge_pair_finish(
                rec, item, claims, verdicts,
                fetch_abstract=fetch_abstract, cogroup_covered=flags,
                discriminator_call_llm=discriminator_call_llm,
                f4_verifier_call_llm=f4_verifier_call_llm,
                f3_fetch_reflist=f3_fetch_reflist,
                f3_resolve_pmcid=f3_resolve_pmcid,
                f4_policy=eff_f4_policy, f3_policy=f3_policy,
                f5_seams=f5_seams,
                f5_evidence_builder=f5_evidence_builder,
                f5_policy=f5_policy, f7_seams=f7_seams,
                f7_evidence_builder=f7_evidence_builder,
                f7_policy=f7_policy)
            return finished, None
        except ValueError as exc:
            # THE PARTIALLY BUILT RECORD COMES BACK, NOT None. Phase 2 raising
            # does not unmake Phase 1: the claims, the evidence, the coverage
            # verdicts and any stage that already succeeded are on `rec`, and
            # they are the only reason a human could act on this row. Returning
            # None here is what made the caller rebuild an empty record and throw
            # all of it away.
            return rec, exc

    t0 = time.time()
    try:
        scanned = 0
        for fn in files:
            pmcid = jb._pmcid_from_filename(fn)
            if pmcid in done:                             # resume: already processed
                continue
            if max_docs is not None and scanned >= max_docs:
                break
            scanned += 1
            docs_processed += 1
            print(f"[judgment-run] doc {docs_processed}: {pmcid} "
                  f"| elapsed {time.time() - t0:.0f}s | pairs {refs_seen}")
            try:
                refs = parse_pmc_xml(os.path.join(xml_dir, fn), source_pmcid=pmcid)
            except Exception as e:                        # noqa: BLE001 - best effort
                if production:
                    # The preflight parsed this document successfully; a second
                    # pass failing means the two passes disagree about the
                    # population. Skipping would silently shrink it.
                    raise PrebandContractError(
                        f"production: {pmcid} parsed in the preflight but FAILED "
                        f"during execution ({type(e).__name__}: {e}); the "
                        "validated population is not the judged population")
                print(f"[judgment-run-parse-skip] {pmcid}: {e}")
                ckpt_fh.write(json.dumps({"pmcid": pmcid, "error": str(e)}) + "\n")
                ckpt_fh.flush()
                done.add(pmcid)
                _write_json_atomic(manifest_path, progress_manifest())
                continue

            # THE HASH-PINNED CITATION SELECTION, APPLIED HERE AND NOWHERE
            # ELSE: after the real parser has produced the real Reference
            # objects, before any of them is judged. Narrowing the POPULATION at
            # this point leaves every surviving reference running the identical
            # code path it would on a whole-corpus run -- which is the only way a
            # subset run's numbers mean the same thing as the full run's. `None`
            # (the ordinary case) returns the list unchanged.
            refs = csel.apply_selection(refs, selection)
            if not refs:
                print(f"[judgment-run-selection-skip] {pmcid}: "
                      "no selected references in this document")
                ckpt_fh.write(json.dumps({"pmcid": pmcid}) + "\n")
                ckpt_fh.flush()
                done.add(pmcid)
                _write_json_atomic(manifest_path, progress_manifest())
                continue

            # PHASE 1 over the whole document. A co-citation group verdict needs
            # every member's coverage, so nothing is emitted until the document's
            # coverage is complete. `pending` preserves REF ORDER exactly, so the
            # emit sequence, the record shapes and the hash chain are unchanged --
            # only the moment of writing moves, from per reference to per
            # document, which is already the checkpoint granularity.
            pending: list = []
            marker_scope.tally_document(scope_counts, refs)
            # Atomic claims are a pure function of the citing sentence, so one
            # citance citing [52,53,54,55] extracts ONCE instead of once per
            # reference. Scoped to the document -- a citance is a within-document
            # object -- which captures the whole fanout while keeping the cache
            # bounded on a corpus run.
            claims_cache = (_OrderedClaimsCache()
                            if worker_pool is not None else {})
            for order, ref in enumerate(refs):
                refs_seen += 1
                executed_ids.add(ref.citation_id)
                # THE BAND-1 LABEL IS READ FIRST, and counted regardless of which
                # gate goes on to exclude the row. It used to be read only after
                # the exclusion check below had already `continue`d, which lost
                # every label on a reference with no citance or no cited PMID --
                # the exact population F1/F2/F8 fire on.
                cleared, disp_label, preband_label = _preband(ref.citation_id, disp)
                if isinstance(preband_label, str) and preband_label.strip():
                    ck = preband_label.strip()
                    preband_label_census[ck] = preband_label_census.get(ck, 0) + 1
                # THE IDENTIFIER BAND 1 RESOLVED, read from the disposition
                # artifact rather than re-derived from the reference. This is the
                # whole of the handoff fix: `jb.exclusion_reason` below now sees
                # what Band 1 actually found, instead of re-checking the CLAIMED
                # pmid and discarding a reference Band 1 had already cleared.
                identifier = (disp_obj.identifier(ref.citation_id)
                              if disp_obj is not None else {})
                if identifier:
                    identifier_kinds[identifier.get("kind") or "none"] = (
                        identifier_kinds.get(identifier.get("kind") or "none", 0)
                        + 1)
                reason = jb.exclusion_reason(ref, resolved_identifier=identifier)
                if reason is not None:                    # no citance / no cited work
                    pending.append((_excluded_record(
                        ref, reason, resolved_identifier=identifier), None))
                    continue
                if not cleared:
                    rec = _excluded_record(ref, disp_label, preband_label,
                                           resolved_identifier=identifier)
                    pending.append((rec, None))
                    if disp_label == DISP_EXCLUDED_PREBAND:
                        k = str(preband_label)
                        preband_by_label[k] = preband_by_label.get(k, 0) + 1
                    continue

                item = jb.build_item(ref, resolved_identifier=identifier)
                if item is None:                          # defensive; exclusion caught above
                    pending.append((_excluded_record(
                        ref, DISP_EXCLUDED_NO_CITANCE,
                        resolved_identifier=identifier), None))
                    continue

                # Review check (optional injected lookup; cached per pmid).
                pmid = item["cited_pmid"]
                if pubtypes_lookup is not None:
                    if pmid not in pubtype_cache:
                        pubtype_cache[pmid] = pubtypes_lookup(pmid)
                    item["cited_is_review"] = jb.is_review(pubtype_cache[pmid])

                if worker_pool is None:
                    result = coverage_attempt(item, claims_cache, order)
                else:
                    claims_cache.register(item["citing_sentence"], order)
                    result = worker_pool.submit(
                        coverage_attempt, item, claims_cache, order)
                # A future placeholder is resolved below in parser order.  On
                # the one-worker path the tuple is already complete, preserving
                # the original call sequence and failure semantics.
                pending.append(result)

            # Resolve Phase 1 in original reference order and fold every shared
            # tally on the main thread.  Workers may finish in any order; none
            # can commit observable run state out of order.
            resolved_pending: list = []
            for entry in pending:
                if isinstance(entry, Future):
                    entry = entry.result()
                # Excluded records were appended directly as (rec, None).
                if len(entry) == 2:
                    resolved_pending.append(entry)
                    continue
                rec, extra_or_item, local_scope_counts, error = entry
                _merge_marker_scope_counts(scope_counts, local_scope_counts)
                if error is not None:
                    item = extra_or_item
                    # Phase 1 raised, so there is no partially-built record to
                    # keep -- but the FAILURE is still typed and routed rather
                    # than dumped into one terminal quarantine bucket.
                    rec = _new_record(item)
                    rec["preband_cleared"] = True
                    rec["disposition"] = DISP_HELD_STAGE_FAILURE
                    _record_parse_failure(rec, "coverage", error)
                    rec["ts"] = int(time.time())
                    print(f"[judgment-run-stage-failure] "
                          f"{rec['citation_id']}: coverage: {error}")
                    resolved_pending.append((rec, None))
                    continue
                if rec.get("fulltext_incomplete_hold") is True:
                    # Its OWN tally, never `counts`. `counts` is one entry per
                    # emitted record and is summed into `total_records`, so an
                    # extra key there would corrupt the record count rather than
                    # add a statistic -- same reason f4_counts is separate.
                    fulltext_counts["no_usable_fulltext"] += 1
                resolved_pending.append((rec, extra_or_item))
            pending = resolved_pending

            # CO-CITATION AGGREGATION over the judged pairs of this document.
            judged_items = [extra[0] for _rec, extra in pending if extra is not None]
            overlay = _cocitation_overlay(judged_items)
            doc_group_records = overlay["group_records"]
            for record in doc_group_records:
                cocitation_counts["group_claims_covered"] += record["claims_covered"]
                cocitation_counts["group_claims_uncovered"] += record["claims_uncovered"]
                cocitation_counts["group_claims_unknown"] += record["claims_unknown"]
            cocitation_counts["groups"] += overlay["groups"]
            cocitation_counts["sentence_groups"] += overlay["sentence_groups"]
            cocitation_counts["cocitation_groups"] += len(doc_group_records)
            cocitation_counts["members_in_cocitation_groups"] += overlay[
                "members_in_cocitation_groups"]
            for size, n in overlay["group_size_distribution"].items():
                group_sizes[size] = group_sizes.get(size, 0) + n
            for record in doc_group_records:
                groups_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            groups_fh.flush()

            # PHASE 2 computes independently but commits in original ref order.
            # F5/F7 seam bundles may carry internal mutable audit state not owned
            # by this orchestrator, so their Phase 2 remains serial rather than
            # assuming thread safety and risking a semantic change.
            if not phase2_parallel:
                for rec, extra in pending:
                    if extra is None:
                        emit(rec)
                        continue
                    item, claims, verdicts = extra
                    cid = item["citation_id"]
                    flags, summary = overlay["by_citation_id"].get(
                        cid, ((), None))
                    if summary is not None:
                        rec["cocitation"] = summary
                    rec, error = finish_attempt(
                        rec, item, claims, verdicts, flags)
                    if error is not None:
                        _preserve_stage_failure(rec, "F3_F7", error)
                    emit(rec)
            else:
                phase2_pending: list = []
                for rec, extra in pending:
                    if extra is None:
                        phase2_pending.append((rec, None, None))
                        continue
                    item, claims, verdicts = extra
                    cid = item["citation_id"]
                    flags, summary = overlay["by_citation_id"].get(
                        cid, ((), None))
                    if summary is not None:
                        rec["cocitation"] = summary
                    result = worker_pool.submit(
                        finish_attempt, rec, item, claims, verdicts, flags)
                    phase2_pending.append((result, item, extra))

                for result, item, extra in phase2_pending:
                    if extra is None:
                        emit(result)
                        continue
                    rec, error = result.result()
                    if error is not None:
                        _preserve_stage_failure(rec, "F3_F7", error)
                    emit(rec)

            ckpt_fh.write(json.dumps({"pmcid": pmcid}) + "\n")
            ckpt_fh.flush()
            done.add(pmcid)
            # Checkpoint boundary: advance the manifest anchor atomically.
            _write_json_atomic(manifest_path, progress_manifest())
    finally:
        if worker_pool is not None:
            worker_pool.shutdown(wait=True, cancel_futures=True)
        pred_fh.close()
        queue_fh.close()
        review_fh.close()
        ckpt_fh.close()
        side_fh.close()
        groups_fh.close()

    # Module hashes were captured BEFORE execution (see `_module_hashes` at the
    # top of this function): read here, they could describe a module edited
    # mid-run, i.e. provenance for bytes that never executed. Re-read now and
    # compare, so a mid-run edit is DETECTED rather than silently recorded.
    module_hashes_after = _module_hashes(fulltext_path, f5_seams, f7_seams)
    module_hashes_stable = module_hashes_after == module_hashes

    prompt_hashes = {
        "F4_STRENGTH_PROMPT": _sha256_text(F4_STRENGTH_PROMPT),
        "F4_VERIFIER_PROMPT": _sha256_text(F4_VERIFIER_PROMPT),
        "F3_V2_ORIGIN_PROMPT": _sha256_text(F3_V2_ORIGIN_PROMPT),
        "F3_V3_SELECT_PROMPT": _sha256_text(F3_V3_SELECT_PROMPT),
        "F3_V4_LOOPCLOSE_PROMPT": _sha256_text(F3_V4_LOOPCLOSE_PROMPT),
    }

    # Full-text coverage provenance -- ONLY when the path is wired, and CONDITIONAL
    # for exactly the reason the F5 block below is: module_sha256 is built from a
    # fixed tuple, so appending to it unconditionally would add a key to every
    # abstract-path manifest and move bytes the opt-in guarantee pins. A v3 run's
    # coverage number is conditional on the reader, the v3 prompt and the span
    # resolver, so with the path wired all three are recorded.
    if fulltext_path:
        from .coverage_prompts_v3 import COVERAGE_PROMPT_V3
        prompt_hashes["COVERAGE_PROMPT_V3"] = _sha256_text(COVERAGE_PROMPT_V3)

    # F5 provenance -- ONLY when F5 is actually wired. An F5 run previously emitted
    # no module hash, no prompt hash and no policy block, so the governing settings
    # of an F5 number were recorded nowhere (DEC-020's failure mode, CONTRADICTIONS
    # 41 a third time). Added CONDITIONALLY because the default abstract path's
    # manifest bytes are an opt-in guarantee: an unconditional key, even a
    # zero-valued one, changes every default run.
    if f5_seams is not None and f5_runtime["judge_calls"] > 0:
        from .f5_contradiction_prompt import F5_CONTRADICTION_PROMPT
        prompt_hashes["F5_CONTRADICTION_PROMPT"] = _sha256_text(F5_CONTRADICTION_PROMPT)

    # "F4 actually evaluated" is mechanically defined by the counters; reportable
    # additionally requires the formal wiring end-to-end.
    # DEC-072 retired the two clauses that required a DISTINCT verifier callable
    # and a DIFFERENT verifier model id. DEC-063 fixes the project on one model,
    # so those made F4 permanently unreportable. Everything else stands --
    # retiring two clauses is not retiring the gate.
    f4_reportable = (
        eff_f4_policy.mode == "formal"
        and discriminator_call_llm is not None
        and bool(eff_f4_policy.generator_model_id.strip())
        and f4_counts["generator_calls"] > 0
    )
    # Was the verifier a genuinely independent model, or the generator again?
    # Recorded per run, because under one model the verifier confirms premises
    # the same model produced, and a reader must not have to infer that.
    f4_self_verified = (
        f4_verifier_call_llm is None
        or f4_verifier_call_llm is discriminator_call_llm
        or (eff_f4_policy.verifier_model_id or eff_f4_policy.generator_model_id)
        == eff_f4_policy.generator_model_id
    )

    # Clean end == the input dir is exhausted; a max_docs-bounded pass leaves the
    # manifest in_progress (a deliberate pause), resumable after chain replay.
    remaining = [fn for fn in files if jb._pmcid_from_filename(fn) not in done]
    status = "complete" if not remaining else "in_progress"

    total_records = sum(counts.values())
    queue_audit = _queue_audit(pred_path, queue_path, review_path)
    manifest_queue_rows = queue_audit["queue_rows"]
    manifest = {
        "layer": "F3-F7 natural-paper orchestration (judgment_run)",
        "status": status,
        **({"parallel_execution": {
            "max_workers": max_workers,
            "phase1_workers": max_workers,
            "phase2_workers": max_workers if phase2_parallel else 1,
            "ordered_commit": True,
            "cocitation_barrier_preserved": True,
            "same_sentence_extraction": "ordered_single_flight",
            "f5_f7_phase2_serialized": not phase2_parallel,
            **({"f7_thread_safe_parallel": True}
               if f7_seams is not None and phase2_parallel else {}),
            "quality_invariants": (
                "Prompts, models, evidence scope, parsers, policies, verifier "
                "gates, per-sentence extraction reuse, terminal filtering, "
                "record order and hash-chain order are unchanged."
            ),
        }} if max_workers > 1 else {}),
        "discriminators_wired": discriminator_call_llm is not None,
        "warning": (
            "coverage->F6 always live; F4 (strength) + F3 (provenance) live only when "
            "discriminator_call_llm is wired; F5 (temporal supersession) is live only "
            "when both f5_seams and f5_evidence_builder are supplied, otherwise it holds "
            "UNJUDGEABLE; F7 (entity) is asserted only when both f7_seams and "
            "f7_evidence_builder are supplied, otherwise its seam stays empty and F7 is "
            "never asserted either way. Nothing is declared ACCURATE while any gate is "
            "unwired or held. "
            "F4 results are reportable only in formal mode; DEC-072 permits the "
            "same model to fill both roles, and that circularity is recorded. "
            "No machine label here is ground truth; precision is measured later by "
            "human review."
        ),
        "trust_boundary": _TRUST_BOUNDARY,
        "model": model,
        # PARSER CONTRACTS, both axes, on EVERY path. DEC-022 makes prompt version
        # and parser version independent: relaxing a strict loader is a parser bump
        # with no prompt change. The abstract path recorded neither, so a contract
        # move was invisible in the artifact.
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "claim_extract_parser_version": CLAIM_PARSER_VERSION,
        "coverage_parser_version": (RESPONSE_PARSER_VERSION_V3 if fulltext_path
                                    else COVERAGE_PARSER_VERSION),
        # The scope the run ACTUALLY judged at, on every path. Recorded
        # unconditionally because "absent" was indistinguishable from "unknown"
        # to any reader who did not already know the opt-in convention.
        "evidence_scope_effective": (EVIDENCE_SCOPE_FULLTEXT if fulltext_path
                                     else EVIDENCE_SCOPE_ABSTRACT),
        # The scope the run ACTUALLY judged at. Defaults to the frozen abstract
        # version, so a default run stamps exactly what it stamped before.
        "coverage_prompt_version": (COVERAGE_PROMPT_VERSION_V3 if fulltext_path
                                    else COVERAGE_PROMPT_VERSION),
        # Full-text path only, and every key conditional: an abstract-path manifest
        # must stay byte-identical (the opt-in guarantee), so absent here means the
        # path was never wired -- never "unknown".
        **({"response_parser_version": RESPONSE_PARSER_VERSION_V3,
            "evidence_scope": EVIDENCE_SCOPE_FULLTEXT,
            "fetch_fulltext_wired": True,
            "fulltext_counts": dict(fulltext_counts),
            "fulltext_note": (
                "Coverage was judged against the retrieved BODY (DEC-030), not the "
                "cited abstract. A reference whose retrieval was not complete is "
                "held deterministically with NO model call of either version and "
                "counted under fulltext_counts.no_usable_fulltext -- DEC-032 holds "
                "rather than flags, because an argument from silence needs a "
                "complete text. Mode comes from CONFIGURATION: a failed fetch holds "
                "and never falls back to abstract scope."
            )} if fulltext_path else {}),
        "module_sha256": module_hashes,
        "module_sha256_stable": module_hashes_stable,
        "module_sha256_capture": "before_execution",
        **({"module_sha256_after": module_hashes_after}
           if not module_hashes_stable else {}),
        "prompt_sha256": prompt_hashes,
        # --- POPULATION PROVENANCE ------------------------------------------
        # The disposition defines which citations were judged AT ALL, so it
        # defines the denominator of every rate downstream. Recording only
        # "supplied: true" and a collapsed size made a result impossible to tie
        # back to the population it was computed over.
        "preband": {
            **(disp_obj.provenance() if disp_obj is not None
               else {"source": None, "canonical": False}),
            "join": join_acc,
            "require_full_coverage": require_full_coverage,
            "expected_docs": len(expected_per_doc),
            "expected_refs_per_doc": expected_per_doc,
            "preflight_parse_failures": preflight_parse_failures,
        },
        "corpus": {
            "xml_dir": xml_dir,
            "documents_in_scope": len(to_process),
            **({"manifest_path": corpus_manifest_path,
                "manifest_sha256": _sha256_file(corpus_manifest_path)}
               if corpus_manifest_path else {}),
        },
        "code_commit": code_commit,
        # --- ADAPTER IDENTITY, verbatim -------------------------------------
        # Absent when unsupplied: a defaulted value would be a fabricated
        # provenance record. `temperature` uses `is not None`, NOT truthiness --
        # DEC-046B pins temperature=0 and 0 is falsy, so a truthiness guard would
        # drop exactly the pinned value this exists to record.
        "adapter": {
            **({"model": model} if model else {}),
            **({"assistant_prefill": assistant_prefill}
               if assistant_prefill else {}),
            **({"stop_sequences": list(stop_sequences)} if stop_sequences else {}),
            **({"temperature": temperature} if temperature is not None else {}),
        },
        "f3": _f3_manifest_block(f3_policy, discriminator_call_llm is not None),
        # ONE expression feeds both this block's "wired" and seam_status.F7's,
        # so the two can no longer contradict each other inside one manifest.
        # The block is still emitted on f7_seams alone -- "seams supplied, no
        # evidence builder" is a real configuration and deserves a record saying
        # so, which is precisely what wired=False beside a present block says.
        **({"f7": _f7_manifest_block(
            f7_policy, f7_records_all, wired=f7_wired,
            evidence_context_supplied=f7_evidence_builder is not None,
            f7_seams=f7_seams)}
           if f7_seams is not None else {}),
        "f4": {
            "mode": eff_f4_policy.mode,
            "reportable": f4_reportable,
            "generator_model_id": eff_f4_policy.generator_model_id,
            "verifier_model_id": eff_f4_policy.verifier_model_id,
            "strength_prompt_version": eff_f4_policy.strength_prompt_version,
            "verifier_prompt_version": eff_f4_policy.verifier_prompt_version,
            "eligible_claims": f4_counts["eligible_claims"],
            "unassessed_no_usable_abstract":
                f4_counts["unassessed_no_usable_abstract"],
            "generator_calls": f4_counts["generator_calls"],
            "verifier_calls": f4_counts["verifier_calls"],
            "outcome_counts": dict(sorted(f4_outcomes.items())),
            "hold_reason_counts": dict(sorted(f4_hold_reasons.items())),
            "evidence_scope_pair_counts": dict(sorted(f4_scope_pairs.items())),
            "findings_count": finding_labels.get("F4", 0),
            "emitted_label_count": emitted_labels.get("F4", 0),
            "human_adjudication": {
                "f4_label_supported_by_current_queue": False,
                "precision_figure_obtainable": False,
                "note": (
                    "The current annotation queue has no F4 response label. "
                    "No F4 precision figure may be quoted until a dedicated "
                    "human-adjudication landing site exists."
                ),
            },
            # THE RESIDUAL RISK DEC-072 ACCEPTS, made visible in the artifact
            # rather than only in the vault. Formal mode no longer requires an
            # independent verifier, so under one model the verifier confirms
            # premises the same model produced. Nothing in code replaces the
            # retired guard; the answer is a human-adjudicated sample.
            "self_verification": {
                "self_verified": True if f4_self_verified else None,
                "independent_verifier": None,
                "independence_verified": False,
                "generator_model_id_claimed": eff_f4_policy.generator_model_id,
                "verifier_model_id_claimed": eff_f4_policy.verifier_model_id,
                "governing_decision": "DEC-072",
                "note": (
                    "F4 ran with the generator as its own verifier (DEC-072 "
                    "retired the distinct-verifier requirement; DEC-063 fixes "
                    "the project on one model). The verifier confirms premises "
                    "the same model produced -- a real circularity that NOTHING "
                    "in code checks. An F4 precision figure requires a "
                    "human-adjudicated sample of F4 rows."
                    if f4_self_verified else
                    "The caller supplied distinct callable/model identifiers, "
                    "but independence is not verified and is not asserted."
                ),
            },
        },
        **({"f5": _f5_manifest_block(f5_policy, f5_records_all, f5_runtime)}
           if f5_seams is not None else {}),
        "chain_genesis": genesis,
        "chain_tip": prev_link,
        "chain_record_count": chain_count,
        "params": {
            "xml_dir": xml_dir, "out_dir": out_dir, "max_docs": max_docs,
            **({"max_workers": max_workers} if max_workers > 1 else {}),
            "email": email, "api_key_present": bool(api_key),
            "preband_disposition_supplied": disp is not None,
            "preband_disposition_size": len(disp) if disp is not None else 0,
            "preband_disposition_arg": (
                preband_disposition if isinstance(preband_disposition, str)
                else f"<{type(preband_disposition).__name__}>"),
            "pubtypes_lookup_wired": pubtypes_lookup is not None,
            "fetch_reflist_wired": fetch_reflist is not None,
            "f4_verifier_wired": f4_verifier_call_llm is not None,
            "chain_genesis": chain_genesis,
        },
        # The preflight parse and the execution parse are two passes over the
        # same XML. If they disagree, the population that was VALIDATED is not
        # the population that was JUDGED.
        "executed_domain": {
            "preflight_ids": len(expected_ids),
            "executed_ids": len(executed_ids),
            "matches_preflight": executed_ids == set(expected_ids),
            "only_in_preflight": sorted(set(expected_ids) - executed_ids)[:10],
            "only_in_execution": sorted(executed_ids - set(expected_ids))[:10],
        },
        # The blind queue is the annotation denominator; it must equal the
        # scoreable predictions exactly, by id, not just by count.
        "queue_audit": queue_audit,
        "emitted_labels": dict(sorted(emitted_labels.items())),
        "finding_labels": dict(sorted(finding_labels.items())),
        # WIRED IS NOT FIRED. Omitting discriminator_call_llm silently disables
        # F3 AND F4; unwired F5/F7 seams do the same. Without this block a run
        # that never checked reads exactly like one that checked and found
        # nothing -- the same defect class as the tautological queue audit.
        # NOT a refusal: an unwired seam is a legitimate development
        # configuration. It just may not be silent.
        "seam_status": {
            "F1": {
                **dict(((disp_obj.check_attestations if disp_obj is not None else {})
                        .get("F1") or {})),
                "wired": bool(((disp_obj.check_attestations if disp_obj is not None else {})
                               .get("F1") or {}).get("performed")),
                "gate": "canonical pre-band F1 attestation",
            },
            "F2": {
                **dict(((disp_obj.check_attestations if disp_obj is not None else {})
                        .get("F2") or {})),
                "wired": bool(((disp_obj.check_attestations if disp_obj is not None else {})
                               .get("F2") or {}).get("performed")),
                "gate": "canonical pre-band F2 attestation",
            },
            "F3": {"wired": discriminator_call_llm is not None,
                   "fired": emitted_labels.get("F3", 0),
                   "gate": "discriminator_call_llm"},
            "F4": {"wired": discriminator_call_llm is not None,
                   "fired": emitted_labels.get("F4", 0),
                   "findings": finding_labels.get("F4", 0),
                   "gate": "discriminator_call_llm",
                   "assessed_claims": f4_counts["eligible_claims"]},
            "F5": {"wired": f5_seams is not None and f5_evidence_builder is not None,
                   "fired": emitted_labels.get("F5", 0),
                   "gate": (
                       "f5_seams AND f5_evidence_builder AND "
                       "discriminator_call_llm AND nonempty_atomic_claims AND "
                       "supported_target_claim AND no_higher_priority_F7_F6_F4_F3"
                   )},
            "F7": {"wired": f7_wired,
                   "fired": emitted_labels.get("F7", 0),
                   "gate": "f7_seams AND f7_evidence_builder",
                   # An F7 seam can be wired and still unable to fire, if the
                   # authority table locks nothing. run_natural_judgment now
                   # REFUSES that configuration up front, so within this entry
                   # point wired=True does imply "could fire". Published anyway,
                   # because "wired" alone never carried that guarantee and a
                   # reader should not have to know which entry point ran.
                   "authorities_locked_types": (
                       f7_reachability_report["locked_types"]
                       if f7_reachability_report is not None else []),
                   "production_evidence_builder":
                       PRODUCTION_F7_EVIDENCE_BUILDER},
            "F8": {
                **dict(((disp_obj.check_attestations if disp_obj is not None else {})
                        .get("F8") or {})),
                "wired": bool(((disp_obj.check_attestations if disp_obj is not None else {})
                               .get("F8") or {}).get("performed")),
                "gate": "canonical pre-band F8 attestation",
                "implemented_in_this_package": True,
                "note": (
                    "F8 is produced upstream by the source-bound PubMed "
                    "retraction-notice timing gate and consumed here through "
                    "the canonical pre-band attestation."
                ),
            },
            "F6": {"wired": True, "fired": emitted_labels.get("F6", 0),
                   "gate": "always live (coverage)"},
            "note": (
                "'wired' is whether the seam could fire at all; 'fired' is how "
                "many labels it produced. An unwired seam reporting fired=0 has "
                "NOT found zero faults -- it was never asked."
            ),
        },
        **({"corpus_document_sha256": corpus_bindings.get("document_sha256", {})}
           if production else {}),
        "counts": counts,
        "excluded_preband_by_label": preband_by_label,
        # The COMPLETE Band-1 label census, unbiased by gate ordering. Differs
        # from excluded_preband_by_label by exactly the rows an earlier gate
        # claimed first -- which for F1/F2/F8 is most of them.
        "preband_label_census": dict(sorted(preband_label_census.items())),
        "preband_label_census_note": (
            "Every Band-1 label seen, whatever excluded the row first. "
            "excluded_preband_by_label counts only rows the PRE-BAND gate "
            "excluded, and the no-citance / no-cited-PMID gate runs ahead of it "
            "-- so a reference labelled F1/F2/F8, which by definition carries no "
            "atomic claims, is routinely counted there and nowhere else. These "
            "two numbers are meant to differ; neither is a correction of the "
            "other."
        ),
        "f8": {
            "implemented_in_this_package": True,
            "attestation": dict(
                ((disp_obj.check_attestations if disp_obj is not None else {})
                 .get("F8") or {})),
            "note": (
                "F8 is a pre-band decision produced by cre.f1.run using linked "
                "PubMed retraction notices, the earliest defensible citing "
                "publication date, and the registered 31-day inclusion floor."
            ),
        },
        "docs_processed": docs_processed,
        "refs_seen": refs_seen,
        "total_records": total_records,
        # Every pair is accounted for exactly once. NOTE this is an internal
        # bookkeeping identity ONLY: it is true of a run that excluded every
        # pair, which is why it is no longer sufficient on its own and the
        # preband.join accounting above carries the population check.
        "accounting_ok": total_records == refs_seen,
        "scoreable_records": scoreable_records,
        "annotation_queue_rows": manifest_queue_rows,
        # WHICH REFERENCES THIS RUN WAS ALLOWED TO TOUCH, bound to the artifact
        # that decided it and the source runs that artifact was derived from.
        "citation_selection": (
            {**selection.binding(), **selection_accounting}
            if selection is not None else {"selection_applied": False}),
        "preband_identifier_kinds": dict(sorted(identifier_kinds.items())),
        "evidence_abstract_sources": dict(sorted(evidence_abstract_sources.items())),
        "abstract_scope_fallback": dict(abstract_fallback_counts),
        # THE OUTCOME LAYER. `disposition` above says where each pair stopped;
        # this says what was concluded about it. One entry per emitted record,
        # from the closed vocabulary, so the two can be reconciled but never
        # confused.
        "terminal_outcomes": {
            "version": tox.TERMINAL_OUTCOME_VERSION,
            "counts": dict(sorted(terminal_counts.items())),
            "total": sum(terminal_counts.values()),
            "accounting_ok": sum(terminal_counts.values()) == total_records,
            "vocabulary": sorted(tox.TERMINAL_OUTCOMES),
        },
        "scope_exclusions": {
            "counts_by_reason": dict(sorted(scope_exclusions.items())),
            "total": sum(scope_exclusions.values()),
            "vocabulary": sorted(tox.TERMINAL_SCOPE_EXCLUSION_REASONS),
            "note": (
                "References the F3-F7 taxonomy has nothing to say about -- an "
                "uncited reference has no citing sentence, therefore no "
                "attributed claim. Terminal UNJUDGEABLE, in neither queue, and "
                "distinct from a reference the band tried to judge and could not."
            ),
        },
        "human_review": {
            "path": review_path,
            "records": human_review_records,
            "by_reason": dict(sorted(human_review_by_reason.items())),
            "allowed_reasons": sorted(tox.HUMAN_REVIEW_REASONS),
            "note": (
                "HUMAN_REVIEW_REQUIRED only. NONE and UNJUDGEABLE are "
                "conclusions, not work items, and enter neither queue; the "
                "annotation queue holds only records carrying an F3-F7 finding."
            ),
        },
        # PAID-CALL ACCOUNTING, retries included. Counting only the attempts that
        # succeeded would understate the bill by exactly the retries the retry
        # budget exists to spend.
        #
        # AND EVERY BILLED STAGE, not just claim extraction. This block once
        # summed a ledger that booked ONE of seven stages -- coverage, F3, F4,
        # F5 and F7 all reached the model without being booked -- so a manifest
        # could report `total_attempts` near the reference count while the run
        # had paid several times that. `unmetered` is the other half of being
        # readable: a stage whose spend was never observed is NAMED with the
        # number of records it affected, because a reader costing or auditing a
        # run has to be able to tell a real zero from a stage nobody counted.
        "paid_calls": {
            "total_attempts": paid_call_totals["total"],
            "retry_attempts": paid_call_totals["retries"],
            "by_stage": dict(sorted(paid_call_by_stage.items())),
            "unmetered": {
                "records": paid_call_unmetered_records,
                "by_stage": dict(sorted(paid_call_unmetered.items())),
                "note": (
                    "Stages that ran through a callable carrying no paid-call "
                    "meter. Their spend is UNKNOWN, not zero, and is NOT in "
                    "total_attempts -- which is therefore a floor, not the bill, "
                    "on any run where records is nonzero."
                ),
            },
        },
        "stage_failures": {
            "affected_reference_records": stage_failure_records,
            "by_stage": dict(sorted(stage_failure_counts.items())),
            "note": (
                "Per-stage model/evidence failures are held and human-queued; "
                "they do not erase the rest of the citation-pair record."
            ),
        },
        # CO-CITATION. A sentence citing eight references cites them
        # COLLECTIVELY; judging each alone against the whole sentence made F6
        # fire by construction on every member. Fixing that CHANGES THE UNIT OF
        # ANALYSIS, which is a reporting consequence and is surfaced, not absorbed.
        "cocitation": {
            "groups_path": groups_path,
            # BOTH candidate denominators, deliberately unreconciled. Per CITATION
            # is one row per reference -- the historical unit, and every counter
            # above. Per CITATION-GROUP is one row per sentence occurrence -- the
            # unit a collectively-cited claim is actually made in. They differ,
            # both are defensible, and choosing is a reporting decision for ZD.
            "denominator_per_citation": total_records,
            # Backward-compatible name for the historical sentence-level unit.
            # The explicit sentence/scope names below keep a cluster partition
            # from silently changing what "group" means.
            "denominator_per_citation_group": cocitation_counts["sentence_groups"],
            "denominator_per_sentence_group": cocitation_counts["sentence_groups"],
            "denominator_per_scope_unit": cocitation_counts["groups"],
            "cocitation_groups": cocitation_counts["cocitation_groups"],
            "members_in_cocitation_groups":
                cocitation_counts["members_in_cocitation_groups"],
            "group_size_distribution": dict(sorted(
                group_sizes.items(), key=lambda kv: int(kv[0]))),
            "group_claims_covered": cocitation_counts["group_claims_covered"],
            "group_claims_uncovered": cocitation_counts["group_claims_uncovered"],
            "group_claims_unknown": cocitation_counts["group_claims_unknown"],
            "held_cocitation_covered": counts.get(DISP_HELD_COCITATION_COVERED, 0),
            "held_unsupported_cocitation_member": counts.get(
                DISP_HELD_UNSUPPORTED_COCITATION_MEMBER, 0),
            "note": (
                "A group is one SENTENCE OCCURRENCE and its members are the "
                "references that occurrence gave its citance to -- EXCEPT where "
                "marker attribution narrowed the sentence to its marker "
                "clusters, in which case the unit is the cluster and the record "
                "carries a marker_scope_id naming it (see the marker_scope "
                "block). A claim a "
                "co-cited reference established does not raise F6 against this "
                "one; the pair is HELD (held_cocitation_covered), never predicted "
                "and never cleared. F4 (overstatement) and F7 (wrong entity) are "
                "per-reference and still own the label when they fire. Claims NO "
                "member covered are counted in group_claims_uncovered and listed "
                "per group in groups_path -- a real defect, owned by the group."
            ),
        },
        # MARKER ATTRIBUTION. Which claims each reference was actually cited for,
        # and how many (reference, claim) pairs were therefore never asked.
        # Published rather than absorbed: narrowing the question changes what
        # every rate above is a rate OF.
        "marker_scope": marker_scope.manifest_block(scope_counts),
        "sentence_partition_diagnostics": {
            "regex_semantics_changed": False,
            "affected_reference_records": sentence_partition_affected_records,
            "unique_nonpartitioning_blocks": len(sentence_partition_diagnostics),
            "uncovered_characters": sum(
                int(row.get("uncovered_chars") or 0)
                for row in sentence_partition_diagnostics.values()),
            "events": [sentence_partition_diagnostics[key]
                       for key in sorted(sentence_partition_diagnostics)],
            "note": (
                "The legacy sentence regex is unchanged. These events assert "
                "and record when its spans fail to tile the input, converting "
                "silent deletion into a counted diagnostic without moving a "
                "sentence boundary or verdict."
            ),
        },
        "predictions_path": pred_path,
        "annotation_queue_path": queue_path,
        "cocitation_groups_path": groups_path,
        "checkpoint_path": checkpoint_path,
        "record_hashes_path": sidecar_path,
        "manifest_path": manifest_path,
    }
    # The F5 discovery queue -- its OWN artifact, never appended to
    # annotation_queue_path (that file is written by two entry points and 24
    # assertions across 8 test files pin its contents, 8 of them asserting it is
    # empty). Written only when F5 ran, so the default path gains no file.
    if f5_seams is not None:
        from .f5_discovery_queue import (QUEUE_FILENAME, assert_blind, build_queue)
        f5_queue = build_queue(f5_records_all)
        assert_blind(f5_queue)      # on the BUILT rows, at every depth
        f5_queue_path = os.path.join(out_dir, QUEUE_FILENAME)
        with open(f5_queue_path, "w", encoding="utf-8") as fh:
            for row in f5_queue:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        manifest["f5_discovery_queue_path"] = f5_queue_path
        manifest["f5"]["queued_rows"] = len(f5_queue)
        manifest["f5"]["discovery_queue_artifact_sha256"] = _sha256_file(
            f5_queue_path)

        # Persist the source-bound evidence and receiver bundles independently
        # of the prediction rows. Deduplicate packets by their content hash;
        # every bundle remains one claim/citation decision. Both files are
        # canonical and atomically replaced, so their recorded SHA describes
        # exactly the bytes a receiver reads.
        packet_by_hash = {}
        for packet in f5_runtime.get("source_packet_log") or []:
            from .f5_evidence_store import source_packet_from_dict
            validated_packet = source_packet_from_dict(packet)
            packet = validated_packet.to_dict()
            packet_hash = validated_packet.packet_sha256
            previous = packet_by_hash.get(packet_hash)
            if previous is not None and previous != packet:
                raise ValueError("one F5 packet hash names conflicting packet rows")
            packet_by_hash[packet_hash] = packet
        packet_rows = [packet_by_hash[key] for key in sorted(packet_by_hash)]
        bundle_rows = [
            record["controversy_bundle"] for record in f5_records_all
            if isinstance(record.get("controversy_bundle"), dict)]
        from .f5_controversy_bundle import validate_controversy_bundle
        for record in f5_records_all:
            bundle = record.get("controversy_bundle")
            if isinstance(bundle, dict):
                validate_controversy_bundle(
                    bundle,
                    candidate_assessments=record.get("candidate_assessments"),
                    record=record)
        bundle_rows.sort(key=lambda row: (
            str(row.get("citation_id") or ""),
            int(row.get("claim_index") or 0),
            str(row.get("bundle_sha256") or "")))
        artifact_bundle_hashes = {
            row["bundle_sha256"] for row in bundle_rows}
        _require_f5_artifact_coverage(
            manifest["f5"], packet_hashes=packet_by_hash,
            bundle_hashes=artifact_bundle_hashes,
            packet_by_hash=packet_by_hash, f5_records=f5_records_all)
        packet_path = os.path.join(out_dir, "f5_evidence_packets.jsonl")
        bundle_path = os.path.join(out_dir, "f5_controversy_bundles.jsonl")
        _write_jsonl_atomic(packet_path, packet_rows)
        _write_jsonl_atomic(bundle_path, bundle_rows)
        manifest["f5"].update({
            "source_packet_artifact_path": packet_path,
            "source_packet_artifact_rows": len(packet_rows),
            "source_packet_artifact_sha256": _sha256_file(packet_path),
            "source_packet_artifact_packet_hashes": sorted(packet_by_hash),
            "controversy_bundle_artifact_path": bundle_path,
            "controversy_bundle_artifact_rows": len(bundle_rows),
            "controversy_bundle_artifact_sha256": _sha256_file(bundle_path),
            "controversy_bundle_artifact_bundle_hashes": sorted(
                row["bundle_sha256"] for row in bundle_rows),
        })
    # Reportability is a STRICTER predicate than "completed without error", and
    # it is evaluated on the finished manifest plus the predictions file, so it
    # cannot be satisfied by intent. Recorded on every run -- a non-reportable
    # run is a legitimate run that says so -- and ENFORCED when the caller asks.
    manifest["reportability"] = pc.reportability_report(manifest, pred_path)

    # The manifest is written FIRST so the evidence of a failed run survives
    # every abort below.
    _write_json_atomic(manifest_path, manifest)

    # The residual join failure the up-front domain check cannot see: every pair
    # that REACHED the pre-band gate fell through it. Structural exclusions never
    # reached the join, so they do not dilute the denominator.
    pc.enforce_join_reached(
        counts, DISP_EXCLUDED_PREBAND_MISSING,
        (DISP_EXCLUDED_NO_CITANCE, DISP_EXCLUDED_NO_CITED_PMID))
    if require_reportable or production:
        pc.assert_reportable_run(manifest, pred_path)
    return manifest
