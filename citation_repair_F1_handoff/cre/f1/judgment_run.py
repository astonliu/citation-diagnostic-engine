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
  4. assemble_evidence       (cited abstract; review reflist opt)
  5. extract_atomic_claims + coverage_verdicts (injected LLM)     -> QUARANTINE on ValueError
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
from .f7_entity import F7Policy, make_entity_assessor

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


def _load_disposition(preband_disposition) -> "dict[str, object] | None":
    """Return a citation_id -> label map, or None when no disposition is supplied.

    Accepts a dict (used verbatim) or a path to a JSONL whose rows carry
    ``citation_id`` and ``label`` (e.g. run.py's log / prediction records)."""
    if preband_disposition is None:
        return None
    if isinstance(preband_disposition, dict):
        return dict(preband_disposition)
    out: dict[str, object] = {}
    with open(preband_disposition, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            cid = rec.get("citation_id")
            if cid:
                out[cid] = rec.get("label")
    return out


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
               f7_seams=None, f7_evidence_builder=None, f7_policy=None) -> dict:
    """Type a single PRE-BAND-CLEARED item through coverage + the engine.

    Mutates and returns a durable record. Raises ValueError (propagated from the
    strict band parsers) on malformed model output -- the caller quarantines it.
    ``f4_policy=None`` defaults to a development-mode (non-reportable) F4Policy;
    ``f4_verifier_call_llm`` is threaded into ``refine_support_strength``.

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

    item["evidence"] = jb.assemble_evidence(
        item, fetch_abstract=fetch_abstract, fetch_reflist=fetch_reflist)
    rec["evidence"] = item["evidence"]
    from .band_prompts import evidence_is_usable
    rec["evidence_usable"] = bool(evidence_is_usable(item["evidence"]))

    claims = jb.extract_atomic_claims(item["citing_sentence"], extractor=extractor)
    verdicts = jb.coverage_verdicts(claims, item["evidence"], judge=coverage_judge)
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
    model: str = "", email: str = DEFAULT_EMAIL, api_key: str = "",
    max_docs: "int | None" = None, session=None,
    chain_genesis: str = "",
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

    os.makedirs(out_dir, exist_ok=True)
    pred_path = os.path.join(out_dir, "judgment_predictions.jsonl")
    queue_path = os.path.join(out_dir, "judgment_band_annotation_queue.jsonl")
    manifest_path = os.path.join(out_dir, "judgment_run_manifest.json")
    checkpoint_path = os.path.join(out_dir, "judgment_run_checkpoint.jsonl")
    sidecar_path = os.path.join(out_dir, "judgment_run_record_hashes.jsonl")

    # Resume gate: exact chain replay against sidecar + manifest anchor. Raises
    # on complete/tamper/torn state; a fresh dir starts at the genesis link.
    prev_link, chain_count, genesis = _recover_chain(
        out_dir, manifest_path, pred_path, sidecar_path, chain_genesis)

    # Drive-first, resume-safe: skip docs already recorded in the checkpoint and
    # append so a dropped runtime costs one doc, never the whole (paid) run.
    done = jb._load_checkpoint(checkpoint_path)
    disp = _load_disposition(preband_disposition)
    counts: dict[str, int] = {}          # one entry per emitted record; sums to refs_seen
    preband_by_label: dict[str, int] = {}  # auxiliary funnel; NOT summed into totals
    f4_counts = {"eligible_claims": 0, "generator_calls": 0, "verifier_calls": 0}

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

    files = sorted(fn for fn in os.listdir(xml_dir)
                   if fn.endswith((".xml", ".nxml")))
    refs_seen = 0
    docs_processed = 0
    pubtype_cache: dict = {}

    pred_fh = open(pred_path, "a", encoding="utf-8")
    queue_fh = open(queue_path, "a", encoding="utf-8")
    ckpt_fh = open(checkpoint_path, "a", encoding="utf-8")
    side_fh = open(sidecar_path, "a", encoding="utf-8")

    def emit(rec: dict) -> None:
        nonlocal prev_link, chain_count
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
                print(f"[judgment-run-parse-skip] {pmcid}: {e}")
                ckpt_fh.write(json.dumps({"pmcid": pmcid, "error": str(e)}) + "\n")
                ckpt_fh.flush()
                done.add(pmcid)
                _write_json_atomic(manifest_path, progress_manifest())
                continue

            for ref in refs:
                refs_seen += 1
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
                                     f7_policy=f7_policy)
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

    # Load-bearing module hashes, read at run time (the precision claim is
    # conditional on these + the prompt versions/templates + the models). This
    # pins the coverage substrate AND the F4/F3 discriminators and orchestrator.
    module_hashes = {}
    for mod in (jb, __import__("cre.f1.judgment_engine", fromlist=["x"]),
                __import__("cre.f1.band_prompts", fromlist=["x"]),
                __import__("cre.f1.parser", fromlist=["x"]),
                __import__("cre.f1.schema", fromlist=["x"]),
                __import__("cre.f1.f4_strength", fromlist=["x"]),
                __import__("cre.f1.f3_provenance", fromlist=["x"]),
                __import__("cre.f1.judgment_run", fromlist=["x"])):
        f = getattr(mod, "__file__", None)
        if f and os.path.exists(f):
            module_hashes[os.path.basename(f)] = _sha256_file(f)

    prompt_hashes = {
        "F4_STRENGTH_PROMPT": _sha256_text(F4_STRENGTH_PROMPT),
        "F4_VERIFIER_PROMPT": _sha256_text(F4_VERIFIER_PROMPT),
        "F3_V2_ORIGIN_PROMPT": _sha256_text(F3_V2_ORIGIN_PROMPT),
        "F3_V3_SELECT_PROMPT": _sha256_text(F3_V3_SELECT_PROMPT),
        "F3_V4_LOOPCLOSE_PROMPT": _sha256_text(F3_V4_LOOPCLOSE_PROMPT),
    }

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
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "coverage_prompt_version": COVERAGE_PROMPT_VERSION,
        "module_sha256": module_hashes,
        "prompt_sha256": prompt_hashes,
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
        "chain_genesis": genesis,
        "chain_tip": prev_link,
        "chain_record_count": chain_count,
        "params": {
            "xml_dir": xml_dir, "out_dir": out_dir, "max_docs": max_docs,
            "email": email, "api_key_present": bool(api_key),
            "preband_disposition_supplied": disp is not None,
            "preband_disposition_size": len(disp) if disp is not None else 0,
            "pubtypes_lookup_wired": pubtypes_lookup is not None,
            "fetch_reflist_wired": fetch_reflist is not None,
            "f4_verifier_wired": f4_verifier_call_llm is not None,
            "chain_genesis": chain_genesis,
        },
        "counts": counts,
        "excluded_preband_by_label": preband_by_label,
        "docs_processed": docs_processed,
        "refs_seen": refs_seen,
        "total_records": total_records,
        # Every pair is accounted for exactly once.
        "accounting_ok": total_records == refs_seen,
        "predictions_path": pred_path,
        "annotation_queue_path": queue_path,
        "checkpoint_path": checkpoint_path,
        "record_hashes_path": sidecar_path,
        "manifest_path": manifest_path,
    }
    _write_json_atomic(manifest_path, manifest)
    return manifest
