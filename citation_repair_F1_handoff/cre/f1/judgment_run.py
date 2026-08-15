"""Natural-paper F3-F7 orchestration -- the thin layer that wires committed pieces.

This module runs *naturally occurring* PMC-OA citing papers end-to-end through the
F3-F7 band and emits ONE durable, reviewable record per citation-claim pair. It
adds NO new discriminator and invents NO advisor-locked semantics. The single
LIVE semantic discriminator is coverage -> F6; F4 (strength, generator +
independent verifier) and F3 (provenance) run only when the discriminator LLM is
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
  5. extract_atomic_claims + coverage_verdicts (injected LLM)     -> QUARANTINE on ValueError
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
import time
from typing import Callable, Iterable, Optional

from . import judgment_band as jb
from . import preband_contract as pc
from .preband_contract import PrebandContractError
from .judgment_engine import (
    DiscriminatorContractError,
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
from .f5_supersession import F5Policy, decide_f5
from .f7_entity import (
    F7Policy,
    F7_ATTRIBUTION_PROMPT,
    F7_EVIDENCE_PROMPT,
    F7_TUPLES_PROMPT,
    F7_VERIFIER_PROMPT,
    make_entity_assessor,
)
# The opt-in full-text coverage path (DEC-030/032). Both live OUTSIDE the frozen
# substrate for the same reason coverage_aggregate does: band_prompts.py cannot be
# edited without drifting its pinned blob OID.
from .coverage_aggregate import no_usable_fulltext_dict
from .coverage_prompts_v3 import (
    COVERAGE_PROMPT_VERSION_V3,
    RESPONSE_PARSER_VERSION as RESPONSE_PARSER_VERSION_V3)
from .parser_versions import CLAIM_PARSER_VERSION, COVERAGE_PARSER_VERSION

#: What the manifest and every full-text-scoped record call the evidence scope.
#: Matches judgment_band's BAND_MODE_FULLTEXT marker so one run's two layers agree.
EVIDENCE_SCOPE_FULLTEXT = "fulltext_sections"
EVIDENCE_SCOPE_ABSTRACT = "abstract"

# --- dispositions (every pair lands in exactly one) -----------------------
DISP_EXCLUDED_NO_CITANCE = "excluded_no_citance"
DISP_EXCLUDED_NO_CITED_PMID = "excluded_no_cited_pmid"
DISP_EXCLUDED_PREBAND_MISSING = "excluded_preband_disposition_missing"
DISP_EXCLUDED_PREBAND = "excluded_preband"          # + preband_label carries the F1/F2 label
DISP_QUARANTINE_PARSE = "quarantine_parse"
DISP_HELD_NO_CLAIMS = "held_no_atomic_claims"
DISP_PREDICTED = "predicted"                        # label == F6
DISP_HELD_FULL_COVERAGE = "held_full_coverage_pending_F3_F5_F7"  # legacy (discriminators unwired)
DISP_HELD_INSUFFICIENT = "held_insufficient_evidence"
# Wired-path holds (F4 + F3 live; F5/F7 live only when their seams are supplied).
DISP_HELD_PENDING_F5_F7 = "held_pending_F5_F7"            # full coverage, not overstated, proper origin
DISP_HELD_PROVENANCE_UNJUDGEABLE = "held_provenance_unjudgeable"
DISP_HELD_STRENGTH_UNJUDGEABLE = "held_strength_unjudgeable"

# Pipeline/taxonomy labels that mean "the cited work was verified as the right,
# existing paper" -> the F3-F7 band may proceed. Everything else is out of band.
_CLEAR_LABELS = frozenset({"cleared", "accurate"})

# Scoreable dispositions get a blind annotation payload; excluded/quarantine do not.
_SCOREABLE = frozenset(
    {DISP_PREDICTED, DISP_HELD_FULL_COVERAGE, DISP_HELD_INSUFFICIENT,
     DISP_HELD_PENDING_F5_F7, DISP_HELD_PROVENANCE_UNJUDGEABLE,
     DISP_HELD_STRENGTH_UNJUDGEABLE}
)

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


def _IS_HEX(s: str) -> bool:
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
    if key in _CLEAR_LABELS:
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
        "strength_records": [],
        "provenance": None,
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "coverage_prompt_version": COVERAGE_PROMPT_VERSION,
        "ts": None,
    }


def _excluded_record(ref, disposition: str, preband_label=None) -> dict:
    """A durable record for a pair excluded before the coverage substrate."""
    c = ref.claimed
    return {
        "citation_id": ref.citation_id,
        "citing_pmcid": ref.source_pmcid,
        "citing_pmid": ref.source_pmid,
        "citing_sentence": ref.citance,
        "cited_pmid": c.claimed_pmid,
        "cited_claimed": {"title": c.title, "claimed_pmid": c.claimed_pmid,
                          "claimed_doi": c.claimed_doi},
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
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "coverage_prompt_version": COVERAGE_PROMPT_VERSION,
        "ts": int(time.time()),
    }


def judge_pair(item: dict, *, extractor, coverage_judge, fetch_abstract,
               fetch_reflist=None, discriminator_call_llm=None,
               f4_verifier_call_llm=None,
               f3_fetch_reflist=None, f3_resolve_pmcid=None,
               f4_policy=None, f3_policy=None,
               f5_seams=None, f5_evidence_builder=None, f5_policy=None,
               f7_seams=None, f7_evidence_builder=None, f7_policy=None,
               fetch_fulltext=None, coverage_judge_v3=None) -> dict:
    """Type a single PRE-BAND-CLEARED item through coverage + the engine.

    Mutates and returns a durable record. Raises ValueError (propagated from the
    strict band parsers) on malformed model output -- the caller quarantines it.
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
    development-mode (``deploy_path_a=False``, non-reportable) by default.

    F7 (wrong entity) is wired the same way: when BOTH ``f7_seams`` (a dict of the
    five ``make_entity_assessor`` seam callables) and ``f7_evidence_builder``
    (``item -> EvidenceContext``) are supplied, the assessor produces the
    per-claim ``EntityAssessment`` rows and the durable F7 records; otherwise the
    entity seam stays empty and F7 is never asserted either way. F7 is
    deliberately NOT gated on support state -- it may coexist with F6/F4, so
    support is context, not a veto. ``make_entity_assessor`` raises ValueError on
    a configuration or provenance defect, which the caller quarantines exactly
    like the other strict discriminators.
    """
    rec = _new_record(item)
    rec["preband_cleared"] = True

    # OPT-IN FULL-TEXT COVERAGE (DEC-030/032), mirroring run_band's branch exactly.
    # MODE COMES FROM CONFIGURATION, never from the fetched value: inferring it from
    # the presence of cited_fulltext would let a failed fetch silently drop an
    # opted-in run back to abstract scope and judge it with v2 -- a scope change
    # nothing in the output would record.
    fulltext_path = fetch_fulltext is not None
    item["evidence"] = jb.assemble_evidence(
        item, fetch_abstract=fetch_abstract, fetch_reflist=fetch_reflist,
        fetch_fulltext=fetch_fulltext)
    rec["evidence"] = item["evidence"]
    from .band_prompts import evidence_is_usable
    rec["evidence_usable"] = bool(evidence_is_usable(item["evidence"]))

    claims = jb.extract_atomic_claims(item["citing_sentence"], extractor=extractor)
    if not fulltext_path:
        # Default path, untouched: abstract scope, v2 prompt, no parser-version key.
        verdicts = jb.coverage_verdicts(claims, item["evidence"],
                                        judge=coverage_judge)
    else:
        fulltext = item["evidence"].get("cited_fulltext")
        complete = (isinstance(fulltext, dict)
                    and fulltext.get("retrieval_complete") is True)
        if complete:
            verdicts = jb.coverage_verdicts(
                claims, item["evidence"], judge=coverage_judge_v3,
                prompt_version=COVERAGE_PROMPT_VERSION_V3,
                parser_version=RESPONSE_PARSER_VERSION_V3)
        else:
            # Mirrors the no-usable-abstract gate: deterministic HELD, no model call
            # of EITHER version. Reached by a partial retrieval and equally by a
            # reader result that is None or not a dict -- a fetch failure is an
            # unretrieved body, which is what this branch is for. DEC-032 holds
            # rather than flags when it cannot argue from silence.
            rec["fulltext_incomplete_hold"] = True
            verdicts = jb.coverage_verdicts(
                claims, item["evidence"],
                judge=lambda cl, _ev: [no_usable_fulltext_dict() for _ in cl],
                prompt_version=COVERAGE_PROMPT_VERSION_V3,
                parser_version=RESPONSE_PARSER_VERSION_V3)
        # PROVENANCE MUST STATE THE SCOPE THE ROW WAS ACTUALLY JUDGED AT.
        # _new_record stamps the frozen ABSTRACT version on every record, which is
        # correct on the default path and a FALSE PROVENANCE STAMP here -- the same
        # defect class as DEC-020's omitted temperature. Overwritten, not added, so
        # a reader never has to reconcile two version fields on one row.
        rec["coverage_prompt_version"] = COVERAGE_PROMPT_VERSION_V3
        rec["response_parser_version"] = RESPONSE_PARSER_VERSION_V3
        rec["evidence_scope"] = EVIDENCE_SCOPE_FULLTEXT
    rec["atomic_claims"] = claims
    rec["coverage_verdicts"] = verdicts
    rec["ts"] = int(time.time())

    if not claims:
        # NO_CLAIMS since 2026-08-11 (ZD calibration item 1). This branch always
        # had the case right -- DISP_HELD_NO_CLAIMS below -- while jb.route
        # returned FULL_COVERAGE from a vacuous all([]) and disagreed with it on
        # the same record. The two ends now agree.
        rec["route"] = jb.route(verdicts)
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
        support, strength_records = refine_support_strength(
            claims, coverage_support, item["evidence"],
            call_llm=discriminator_call_llm,
            verifier_call_llm=f4_verifier_call_llm,
            policy=policy)
        rec["strength_records"] = list(strength_records)

    all_supported = bool(support) and all(
        s.state is SupportState.SUPPORTED for s in support)

    # F3 (provenance) only under full support (engine requires provenance=None
    # otherwise). Unwired -> UNJUDGEABLE seam (not evaluated, honest hold).
    provenance = None
    if all_supported:
        if discriminator_call_llm is not None:
            cited_pmcid = (f3_resolve_pmcid(item["cited_pmid"])
                           if f3_resolve_pmcid is not None else None)
            assessor = make_provenance_assessor(
                call_llm=discriminator_call_llm,
                fetch_reflist=f3_fetch_reflist or (lambda _p: ([], False)),
                fetch_abstract=fetch_abstract,
                cited_pmid=item["cited_pmid"], cited_pmcid=cited_pmcid,
                cited_is_review=item.get("cited_is_review"),
                policy=f3_policy or DEFAULT_F3_POLICY)
            provenance = assessor(claims, support)
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
    # negative). decide_f5 raises ValueError on a malformed seam payload / evidence,
    # which the caller quarantines exactly like the other strict discriminators.
    temporal = TemporalAssessment(TemporalState.UNJUDGEABLE)
    if f5_seams is not None and f5_evidence_builder is not None:
        f5_evidence = f5_evidence_builder(item)
        temporal, f5_records = decide_f5(
            claims, support, f5_evidence,
            policy=f5_policy if f5_policy is not None else F5Policy(),
            **f5_seams)
        rec["f5_records"] = list(f5_records)

    # F7 (wrong entity): wired through injected seams like F3/F5. Deliberately NOT
    # gated on support state -- an entity mismatch may coexist with F6/F4, so
    # support is context, not a veto. make_entity_assessor raises ValueError on a
    # configuration or provenance defect; that propagates to the caller's
    # quarantine rather than being swallowed into a fabricated negative.
    entities: tuple = ()
    if f7_seams is not None and f7_evidence_builder is not None:
        entity_assessor = make_entity_assessor(
            **f7_seams,
            evidence_context=f7_evidence_builder(item),
            policy=f7_policy if f7_policy is not None else F7Policy())
        entities = tuple(entity_assessor(claims))
        rec["f7_records"] = list(entity_assessor.records)

    decision = decide_judgment(
        preband_cleared=True, claims=claims, claim_support=support,
        entity_assessments=entities, provenance=provenance,
        temporal=temporal)
    rec["findings"] = list(decision.findings)
    rec["hold_reasons"] = list(decision.hold_reasons)

    r = jb.route(verdicts)
    rec["route"] = r

    if discriminator_call_llm is None:
        # Legacy path: coverage->F6 is the only live discriminator.
        if r == jb.ROUTE_F6_FLAGGED:
            if "F6" not in decision.findings:
                raise DiscriminatorContractError(
                    "route F6_FLAGGED but engine findings lack F6")
            rec["disposition"] = DISP_PREDICTED
            rec["label"] = "F6"
        elif r == jb.ROUTE_FULL_COVERAGE:
            rec["disposition"] = DISP_HELD_FULL_COVERAGE     # F3/F5/F7 uncleared
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
        if r != jb.ROUTE_F6_FLAGGED:
            raise DiscriminatorContractError("F6 finding without an F6 coverage route")
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F6"
    elif "F4" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F4"
    elif "F3" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F3"
    elif "F5" in findings:
        rec["disposition"] = DISP_PREDICTED
        rec["label"] = "F5"
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


def _module_hashes(fulltext_path: bool, f5_seams, f7_seams) -> dict:
    """Hash every module that can govern a number on THIS run's wiring.

    Captured before execution, so the digests describe the bytes that ran. The
    conditional blocks exist because the default abstract path's manifest bytes
    are an opt-in guarantee: an unconditional key changes every default run.
    """
    names = ["cre.f1.judgment_band", "cre.f1.judgment_engine",
             "cre.f1.band_prompts", "cre.f1.parser", "cre.f1.schema",
             "cre.f1.f4_strength", "cre.f1.f3_provenance",
             "cre.f1.judgment_run", "cre.f1.preband_contract"]
    if fulltext_path:
        names += ["cre.f1.coverage_prompts_v3", "cre.f1.coverage_aggregate",
                  "cre.f1.fulltext_reader", "cre.f1.sentence_spans"]
    if f5_seams is not None:
        names += ["cre.f1.f5_supersession", "cre.f1.f5_contradiction_prompt",
                  "cre.f1.f5_seams", "cre.f1.f5_discovery_queue"]
    # F7 can OWN the published label (it rides highest in the engine ordering),
    # so an F7 run that does not hash f7_entity records the governing module of
    # its headline number nowhere. Same defect class the f5 block fixed.
    if f7_seams is not None:
        names.append("cre.f1.f7_entity")
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


def _f7_manifest_block(f7_policy, f7_records) -> dict:
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
    """
    policy = f7_policy if f7_policy is not None else F7Policy()
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
            if isinstance(d, str) and len(d) == 64 and _IS_HEX(d)]
    digest_present = bool(attempted) and len(real) == len(attempted)
    outcomes: dict = {}
    for r in records:
        k = str(r.get("derived"))
        outcomes[k] = outcomes.get(k, 0) + 1
    return {
        "wired": True,
        # Reportable requires records AND that every relation comparison which
        # actually ran reported its prompt digest. A run whose relation prompt
        # cannot be identified cannot back a published F7 number.
        "reportable": bool(records) and digest_present,
        "attribution_prompt_version": policy.attribution_prompt_version,
        "tuples_prompt_version": policy.tuples_prompt_version,
        "evidence_prompt_version": policy.evidence_prompt_version,
        "relation_prompt_version": policy.relation_prompt_version,
        "verifier_prompt_version": policy.verifier_prompt_version,
        "generator_model_id": policy.generator_model_id,
        "verifier_model_id": policy.verifier_model_id,
        "cross_ontology_lock": policy.cross_ontology_lock,
        "authorities_sha256": _sha256_text(policy.authorities_json),
        "prompt_sha256": {
            "attribution": _sha256_text(F7_ATTRIBUTION_PROMPT),
            "tuples": _sha256_text(F7_TUPLES_PROMPT),
            "evidence": _sha256_text(F7_EVIDENCE_PROMPT),
            "verifier": _sha256_text(F7_VERIFIER_PROMPT),
        },
        "relation_prompt_digest_present": digest_present,
        "relation_prompt_sha256": sorted(set(real)) or None,
        "relation_comparisons_attempted": len(attempted),
        "records_emitted": len(records),
        "outcome_counts": dict(sorted(outcomes.items())),
        "note": (
            "Schema D (relation) is built inside the INJECTED relation_comparator, "
            "so its prompt text is not visible here. Its digest is reported by the "
            "comparator; when absent it stays None and this run is not F7-reportable. "
            "The version string is recorded separately and never stands in for a digest."
        ),
    }


def _f5_manifest_block(f5_policy, f5_records) -> dict:
    """The ``"f5"`` manifest block: policy, retrieval protocol, tallies.

    F5 previously emitted NO module hash, NO prompt hash and NO policy block, so
    the governing settings of an F5 number were recorded nowhere. This is the
    counterpart to the existing ``"f4"`` block.

    The retrieval protocol is recorded in READABLE form, not only as
    ``retrieval_query_hash``: a hash is not a protocol, and nobody can audit what
    was searched from one. Absence is reported as "none found under this protocol"
    and never as "no superseding paper exists" -- SciFact-Open measured that 34.3%
    (251/732) of pooled candidates assumed to hold no evidence actually held it."""
    from .f5_seams import (ATTESTATION_LOOKUP_PERFORMED, ATTESTATION_STUB_REASON,
                           CANDIDATE_CAP, RERANKER, retrieval_protocol)
    from .f5_supersession import F5Policy
    from .f5_discovery_queue import QUEUE_VERSION, disposition_counts

    policy = f5_policy if f5_policy is not None else F5Policy()
    return {
        "mode": policy.mode,
        "deploy_path_a": policy.deploy_path_a,
        "reportable": False,        # unreachable by construction; stated, not implied
        "contradiction_prompt_version": policy.contradiction_prompt_version,
        "comparability_policy_version": policy.comparability_policy_version,
        "retrieval_protocol": retrieval_protocol(),
        "candidate_cap": CANDIDATE_CAP,
        "reranker": RERANKER,
        "queue_version": QUEUE_VERSION,
        "disposition_counts": disposition_counts(f5_records),
        "records_emitted": len(f5_records or []),
        # So path_a_eligible=False can never be read as "no attestation exists".
        "attestation_lookup_performed": ATTESTATION_LOOKUP_PERFORMED,
        "attestation_lookup_note": ATTESTATION_STUB_REASON,
    }


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
    fetch_fulltext=None, coverage_judge_v3=None,
    model: str = "", email: str = DEFAULT_EMAIL, api_key: str = "",
    max_docs: "int | None" = None, session=None,
    chain_genesis: str = "",
    assistant_prefill: str = "", stop_sequences: tuple = (), temperature=None,
    code_commit: str = "", corpus_manifest_path: str = "",
    require_full_coverage: bool = False, require_reportable: bool = False,
    production: bool = False,
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
    """
    # --- F4 configuration, validated up front (item 3): outside the per-pair
    # try/except, before any output file exists.
    if f4_policy is not None:
        eff_f4_policy = f4_policy
    elif f4_verifier_call_llm is not None:
        eff_f4_policy = F4Policy(mode="formal", generator_model_id=model,
                                 verifier_model_id=f4_verifier_model_id)
    else:
        eff_f4_policy = F4Policy(mode="development", generator_model_id=model)
    validate_f4_config(eff_f4_policy, discriminator_call_llm, f4_verifier_call_llm,
                       require_generator=False)
    # F5 config validated up front too (a policy defect aborts the run, never a
    # per-pair quarantine). Only when F5 is actually wired.
    if f5_seams is not None:
        from .f5_supersession import validate_f5_policy
        validate_f5_policy(f5_policy if f5_policy is not None else F5Policy())
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

    pred_path = os.path.join(out_dir, "judgment_predictions.jsonl")
    queue_path = os.path.join(out_dir, "judgment_band_annotation_queue.jsonl")
    manifest_path = os.path.join(out_dir, "judgment_run_manifest.json")
    checkpoint_path = os.path.join(out_dir, "judgment_run_checkpoint.jsonl")
    sidecar_path = os.path.join(out_dir, "judgment_run_record_hashes.jsonl")

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
    join_acc = pc.join_accounting(disp_obj, expected_ids)
    pc.enforce_join(join_acc, disp=disp_obj,
                    require_full_coverage=require_full_coverage or production)

    # PRODUCTION PREFLIGHT -- mandatory, and before any output file exists, so a
    # misconfigured production run costs nothing. Every condition here is also a
    # reportability clause, but reportability is checked on the FINISHED manifest
    # (after the compute is spent, and only if the caller asks). Checking up
    # front makes them mandatory rather than advisory.
    if production:
        corpus_bindings = pc.assert_production_preflight(
            disp=disp_obj, join_acc=join_acc,
            parse_failures=preflight_parse_failures,
            code_commit=code_commit, model=model,
            corpus_manifest_path=corpus_manifest_path, xml_dir=xml_dir)

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
    f4_counts = {"eligible_claims": 0, "generator_calls": 0, "verifier_calls": 0}
    # Full-text retrieval funnel. Separate from `counts` for the same reason
    # f4_counts is: `counts` sums to the record total and admits no statistics.
    fulltext_counts = {"no_usable_fulltext": 0}

    def bump(key: str) -> None:
        counts[key] = counts.get(key, 0) + 1

    def progress_manifest() -> dict:
        return {
            "layer": "F3-F7 natural-paper orchestration (judgment_run)",
            "status": "in_progress",
            "model": model,
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
    queue_rows = 0
    executed_ids: set = set()
    queue_ids: list = []
    scoreable_ids: list = []
    emitted_labels: dict = {}
    pubtype_cache: dict = {}

    pred_fh = open(pred_path, "a", encoding="utf-8")
    queue_fh = open(queue_path, "a", encoding="utf-8")
    ckpt_fh = open(checkpoint_path, "a", encoding="utf-8")
    side_fh = open(sidecar_path, "a", encoding="utf-8")

    # Every F5 record produced this run, for the discovery queue and the manifest
    # tallies. Collected at emit so it follows the same crash invariant as the
    # predictions themselves.
    f5_records_all: list = []
    # F7's audit records, collected at emit for the same reason: F7 can own the
    # published label, so its provenance block must be built from what actually
    # ran, not from the policy alone.
    f7_records_all: list = []

    def emit(rec: dict) -> None:
        nonlocal prev_link, chain_count
        f5_records_all.extend(rec.get("f5_records") or [])
        f7_records_all.extend(rec.get("f7_records") or [])
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
        # Mechanical F4 counters, derived from the audit records themselves.
        for sr in rec.get("strength_records") or []:
            if sr.get("assessed"):
                f4_counts["eligible_claims"] += 1
                f4_counts["generator_calls"] += 1
                if "verifier_response" in sr:
                    f4_counts["verifier_calls"] += 1
            elif sr.get("reason") == "no_usable_abstract":
                f4_counts["eligible_claims"] += 1
        if rec["disposition"] in _SCOREABLE:
            nonlocal scoreable_records, queue_rows
            scoreable_records += 1
            queue_rows += 1
            scoreable_ids.append(rec["citation_id"])
            queue_ids.append(rec["citation_id"])
            payload = jb.annotation_payload({
                "item_key": rec["citation_id"],
                "citing_sentence": rec["citing_sentence"],
                "cited_pmid": rec["cited_pmid"],
                "atomic_claims": rec["atomic_claims"],
                "evidence": rec["evidence"],
            })
            queue_fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            queue_fh.flush()

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

            for ref in refs:
                refs_seen += 1
                executed_ids.add(ref.citation_id)
                reason = jb.exclusion_reason(ref)
                if reason is not None:                    # no citance / no cited pmid
                    emit(_excluded_record(ref, reason))
                    continue
                cleared, disp_label, preband_label = _preband(ref.citation_id, disp)
                if not cleared:
                    rec = _excluded_record(ref, disp_label, preband_label)
                    emit(rec)
                    if disp_label == DISP_EXCLUDED_PREBAND:
                        k = str(preband_label)
                        preband_by_label[k] = preband_by_label.get(k, 0) + 1
                    continue

                item = jb.build_item(ref)
                if item is None:                          # defensive; exclusion caught above
                    emit(_excluded_record(ref, DISP_EXCLUDED_NO_CITANCE))
                    continue

                # Review check (optional injected lookup; cached per pmid).
                pmid = item["cited_pmid"]
                if pubtypes_lookup is not None:
                    if pmid not in pubtype_cache:
                        pubtype_cache[pmid] = pubtypes_lookup(pmid)
                    item["cited_is_review"] = jb.is_review(pubtype_cache[pmid])

                try:
                    rec = judge_pair(item, extractor=extractor,
                                     coverage_judge=coverage_judge,
                                     fetch_abstract=fetch_abstract,
                                     fetch_reflist=fetch_reflist,
                                     discriminator_call_llm=discriminator_call_llm,
                                     f4_verifier_call_llm=f4_verifier_call_llm,
                                     f3_fetch_reflist=f3_fetch_reflist,
                                     f3_resolve_pmcid=f3_resolve_pmcid,
                                     f4_policy=eff_f4_policy, f3_policy=f3_policy,
                                     f5_seams=f5_seams,
                                     f5_evidence_builder=f5_evidence_builder,
                                     f5_policy=f5_policy,
                                     f7_seams=f7_seams,
                                     f7_evidence_builder=f7_evidence_builder,
                                     f7_policy=f7_policy,
                                     fetch_fulltext=fetch_fulltext,
                                     coverage_judge_v3=coverage_judge_v3)
                    if rec.get("fulltext_incomplete_hold") is True:
                        # Its OWN tally, never `counts`. `counts` is one entry per
                        # emitted record and is summed into `total_records`, so an
                        # extra key there would corrupt the record count rather than
                        # add a statistic -- same reason f4_counts is separate.
                        # Counted at all because a run whose bodies mostly failed to
                        # retrieve looks identical in the route counters to one
                        # judged against complete text: DEC-032 makes both hold.
                        fulltext_counts["no_usable_fulltext"] += 1
                except ValueError as e:                   # strict-parser failure -> quarantine
                    rec = _new_record(item)
                    rec["preband_cleared"] = True
                    rec["disposition"] = DISP_QUARANTINE_PARSE
                    rec["parse_error"] = str(e)
                    rec["ts"] = int(time.time())
                    print(f"[judgment-run-quarantine] {rec['citation_id']}: {e}")
                emit(rec)

            ckpt_fh.write(json.dumps({"pmcid": pmcid}) + "\n")
            ckpt_fh.flush()
            done.add(pmcid)
            # Checkpoint boundary: advance the manifest anchor atomically.
            _write_json_atomic(manifest_path, progress_manifest())
    finally:
        pred_fh.close()
        queue_fh.close()
        ckpt_fh.close()
        side_fh.close()

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
    if f5_seams is not None:
        from .f5_contradiction_prompt import F5_CONTRADICTION_PROMPT
        prompt_hashes["F5_CONTRADICTION_PROMPT"] = _sha256_text(F5_CONTRADICTION_PROMPT)

    # "F4 actually evaluated" is mechanically defined by the counters; reportable
    # additionally requires the formal wiring end-to-end.
    f4_reportable = (
        eff_f4_policy.mode == "formal"
        and discriminator_call_llm is not None
        and f4_verifier_call_llm is not None
        and f4_verifier_call_llm is not discriminator_call_llm
        and bool(eff_f4_policy.generator_model_id.strip())
        and bool(eff_f4_policy.verifier_model_id.strip())
        and eff_f4_policy.generator_model_id != eff_f4_policy.verifier_model_id
        and f4_counts["generator_calls"] > 0
    )

    # Clean end == the input dir is exhausted; a max_docs-bounded pass leaves the
    # manifest in_progress (a deliberate pause), resumable after chain replay.
    remaining = [fn for fn in files if jb._pmcid_from_filename(fn) not in done]
    status = "complete" if not remaining else "in_progress"

    total_records = sum(counts.values())
    manifest = {
        "layer": "F3-F7 natural-paper orchestration (judgment_run)",
        "status": status,
        "discriminators_wired": discriminator_call_llm is not None,
        "warning": (
            "coverage->F6 always live; F4 (strength) + F3 (provenance) live only when "
            "discriminator_call_llm is wired; F5 (temporal supersession) is live only "
            "when both f5_seams and f5_evidence_builder are supplied, otherwise it holds "
            "UNJUDGEABLE; F7 (entity) is asserted only when both f7_seams and "
            "f7_evidence_builder are supplied, otherwise its seam stays empty and F7 is "
            "never asserted either way. Nothing is declared ACCURATE while any gate is "
            "unwired or held. "
            "F4 results are reportable ONLY in formal mode (distinct verifier). "
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
        **({"f7": _f7_manifest_block(f7_policy, f7_records_all)}
           if f7_seams is not None else {}),
        "f4": {
            "mode": eff_f4_policy.mode,
            "reportable": f4_reportable,
            "generator_model_id": eff_f4_policy.generator_model_id,
            "verifier_model_id": eff_f4_policy.verifier_model_id,
            "strength_prompt_version": eff_f4_policy.strength_prompt_version,
            "verifier_prompt_version": eff_f4_policy.verifier_prompt_version,
            "eligible_claims": f4_counts["eligible_claims"],
            "generator_calls": f4_counts["generator_calls"],
            "verifier_calls": f4_counts["verifier_calls"],
        },
        **({"f5": _f5_manifest_block(f5_policy, f5_records_all)}
           if f5_seams is not None else {}),
        "chain_genesis": genesis,
        "chain_tip": prev_link,
        "chain_record_count": chain_count,
        "params": {
            "xml_dir": xml_dir, "out_dir": out_dir, "max_docs": max_docs,
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
        "queue_audit": {
            "queue_rows": len(queue_ids),
            "scoreable_rows": len(scoreable_ids),
            "matches": (len(queue_ids) == len(scoreable_ids)
                        and set(queue_ids) == set(scoreable_ids)),
            "symmetric_difference": sorted(
                set(queue_ids) ^ set(scoreable_ids))[:10],
        },
        "emitted_labels": dict(sorted(emitted_labels.items())),
        **({"corpus_document_sha256": corpus_bindings.get("document_sha256", {})}
           if production else {}),
        "counts": counts,
        "excluded_preband_by_label": preband_by_label,
        "docs_processed": docs_processed,
        "refs_seen": refs_seen,
        "total_records": total_records,
        # Every pair is accounted for exactly once. NOTE this is an internal
        # bookkeeping identity ONLY: it is true of a run that excluded every
        # pair, which is why it is no longer sufficient on its own and the
        # preband.join accounting above carries the population check.
        "accounting_ok": total_records == refs_seen,
        "scoreable_records": scoreable_records,
        "annotation_queue_rows": queue_rows,
        "predictions_path": pred_path,
        "annotation_queue_path": queue_path,
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
