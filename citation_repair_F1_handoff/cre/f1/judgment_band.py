"""F3-F7 judgment-band front-end -- the stratum-agnostic scoreable substrate.

WHAT THIS IS
------------
One shared front-end that turns a parsed citation into a *judgeable item* (its
atomic claims plus the evidence needed to check them), applies the ABSTRACT-
SCOPED COVERAGE GATE, and routes on the three findings an abstract can support:

  * the cited abstract CONTRADICTS an atomic claim         -> ``F6_FLAGGED``
  * every atomic claim is stated and supported             -> ``FULL_COVERAGE``
                                                            (staged for the F3
                                                             provenance
                                                             discriminator, next
                                                             spec)
  * an abstract is silent on a claim, leaves a specificity
    unconfirmed, or could not be judged                    -> ``HELD_LOW_CONFIDENCE``

``F6_FLAGGED`` AT THIS STAGE MEANS THE CITED ABSTRACT CONTRADICTS THE CLAIM --
not that the cited paper fails to support it (ZD 2026-07-27). A coverage gap in
the taxonomy sense cannot be established from an abstract alone: a claim genuinely
supported in the cited paper's Results but absent from its abstract is *unknown*
here, and is held, never flagged. Adjudicating a true coverage gap requires
full-text evidence, a later stage; the HELD bucket is that escalation queue.

It emits a per-item record AND an annotator payload that is IDENTICAL across
strata and BLIND to the system's proposed verdict. The band is the FROZEN
substrate: it changes WHAT the annotator sees, so its prompts are pinned
(:data:`CLAIM_EXTRACT_PROMPT_VERSION`, :data:`COVERAGE_PROMPT_VERSION`) and held
fixed; only downstream discriminator/finder prompts iterate (they change the
flagged set, i.e. the denominator, not per-item labels).

GOLD DISCIPLINE (do not violate)
--------------------------------
The gold label is the HUMAN annotator's. No automated step here assigns a
semantic F3-F7 label. ``coverage_judge`` and ``extractor`` are the SYSTEM under
evaluation, NOT ground truth. The system's ``proposed_route`` / ``proposed_verdict``
live on the item record ONLY -- they are NEVER written into the annotation
payload; they are revealed to the annotator only after commit, for disagreement
analysis. The tri-state ``established`` is tested with ``is False`` / ``is True``:
``None`` is *unknown*, never a coverage gap (mirrors the ``author_match``
discipline in biblio_match).

LLM INJECTION SEAMS
-------------------
``extractor``, ``coverage_judge``, ``fetch_abstract``, ``fetch_reflist`` are
injected callables, so the module is fully offline-testable. The CLI/notebook
wires the host LLM and the live NCBI helpers. The cited-work review check uses
``ncbi_meta.ncbi_pubtypes`` / ``is_review`` as module globals (monkeypatched in
tests, wired live in the notebook) -- the same pattern the collector uses.

Reuses ``parse_pmc_xml`` so the band's sampling frame is IDENTICAL to the F1
eval, and ``ncbi_pmc_reflist`` (via ``fetch_reflist``) for a cited review's own
reference list -- the F3-V3 rightful-primary candidate pool.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from .parser import parse_pmc_xml
# Production-safe NCBI helpers (NOT the CALIBRATION-ONLY collector). Imported as
# module globals so tests can monkeypatch them on THIS module's namespace.
from .ncbi_meta import ncbi_pubtypes, is_review, DEFAULT_EMAIL

# --------------------------------------------------------------------------
# Pinned prompt versions -- written into every item record and the manifest.
# The band's prompts are the FROZEN substrate: stabilize on a small batch, then
# HOLD. Bump these only on a deliberate substrate change (which can invalidate
# already-collected human labels).
# --------------------------------------------------------------------------
from .band_prompts import (
    CLAIM_EXTRACT_PROMPT_VERSION, COVERAGE_PROMPT_VERSION, evidence_is_usable)

# Label space for the F3 slice of the band (the annotator's terminal choices).
# Coverage-gap -> F6, misattribution -> F3, otherwise ACCURATE.
LABEL_SPACE_F3 = ["F6", "F3", "ACCURATE"]

# Deterministic routes out of the coverage gate.
ROUTE_F6_FLAGGED = "F6_FLAGGED"
ROUTE_FULL_COVERAGE = "FULL_COVERAGE"
ROUTE_HELD = "HELD_LOW_CONFIDENCE"
# Malformed model output: the injected extractor / coverage judge raised while
# parsing a reply (strict-schema failure). The reference is quarantined to a
# durable row-level record instead of aborting the whole run_band batch.
ROUTE_PARSE_QUARANTINE = "PARSE_QUARANTINE"

# Exclusion reasons (structural, counted; NOT taxonomy categories). A cited PMID
# that fails to FETCH at band time is an operational exclusion handled in
# run_band, not one of these structural reasons.
EXCLUDED_NO_CITANCE = "excluded_no_citance"
EXCLUDED_NO_CITED_PMID = "excluded_no_cited_pmid"

# Contract types (documentation only).
#   extractor(sentence: str)        -> list[str]           (atomic claims)
#   coverage_judge(claims, evidence)-> list[dict]          (per-claim verdicts,
#                                       each carrying "established": bool|None)
#   fetch_abstract(pmid: str)       -> str | None          (cited paper abstract)
#   fetch_reflist(pmid: str)        -> (provenance, avail)  (cited review reflist)
Extractor = Callable[[str], list]
CoverageJudge = Callable[[list, dict], list]
FetchAbstract = Callable[[str], Optional[str]]
FetchReflist = Callable[[str], tuple]


# ==========================================================================
# 1. Build the judgeable unit from a parsed ref
# ==========================================================================
def exclusion_reason(ref) -> "str | None":
    """Structural reason this ref cannot enter the band, or None.

    Checked in order so a ref missing both a citance and a cited PMID is
    reported as the citance exclusion (no sentence => nothing to judge)."""
    if not (ref.citance or "").strip():
        return EXCLUDED_NO_CITANCE
    if not (ref.claimed.claimed_pmid or "").strip():
        return EXCLUDED_NO_CITED_PMID
    return None


def build_item(ref) -> "dict | None":
    """Assemble the judgeable unit from a parsed ref.

    Returns None (the caller counts :func:`exclusion_reason`) when there is no
    citing sentence or no cited PMID. Items enter the band only after the
    F1/F2/F8 existence checks; a cited PMID that fails to FETCH at band time is an
    operational exclusion (handled in :func:`run_band`), not a structural one.

    ``item_key == citation_id == "<citing_pmcid>:<ref_id>"`` -- labels key on it
    and are reused across finder-prompt versions. ``citing_sentence`` and
    ``cited_pmid`` additionally pin pair identity."""
    if exclusion_reason(ref) is not None:
        return None
    c = ref.claimed
    return {
        "item_key": ref.citation_id,           # "<citing_pmcid>:<ref_id>"
        "citation_id": ref.citation_id,
        "citing_pmcid": ref.source_pmcid,
        "citing_pmid": ref.source_pmid,
        "citing_title": ref.source_title,
        "citing_sentence": ref.citance,
        "cited_marker": ref.cited_reference_marker,
        "cited_pmid": c.claimed_pmid,
        "cited_claimed": {
            "title": c.title,
            "authors": list(c.authors),
            "year": c.year,
            "journal": c.journal,
            "claimed_pmid": c.claimed_pmid,
            "claimed_doi": c.claimed_doi,
        },
        # Filled downstream (kept here so the record shape is stable).
        "cited_is_review": None,
        "atomic_claims": [],
        "evidence": {},
        "coverage_verdicts": [],
        "proposed_route": None,
        "proposed_verdict": None,
    }


# ==========================================================================
# 2. Atomic claim extraction (injected LLM extractor)
# ==========================================================================
def extract_atomic_claims(sentence: str, *, extractor: Extractor) -> list:
    """Decompose the citing sentence into atomic claims via the injected
    ``extractor``. Pinned to :data:`CLAIM_EXTRACT_PROMPT_VERSION` (stamped into
    every record by :func:`run_band`).

    Returns a list of claim strings (empty on no sentence or an empty/failed
    extractor result). Non-string / blank items are dropped defensively."""
    if not (sentence or "").strip():
        return []
    raw = extractor(sentence) or []
    return [c.strip() for c in raw if isinstance(c, str) and c.strip()]


# ==========================================================================
# 3. Evidence assembly (cited abstract always; review reflist when applicable)
# ==========================================================================
def assemble_evidence(item: dict, *, fetch_abstract: FetchAbstract,
                      fetch_reflist: "FetchReflist | None" = None) -> dict:
    """Assemble the evidence a judge needs to check the claims.

    Always fetches the cited paper's abstract. When the cited work is a review
    (``item["cited_is_review"] is True``) and ``fetch_reflist`` is supplied, also
    fetches the review's own reference list (via ``ncbi_pmc_reflist``) -- the
    F3-V3 rightful-primary candidate pool that a downstream provenance
    discriminator and the annotator draw on."""
    cited_pmid = item.get("cited_pmid", "")
    evidence = {
        "cited_pmid": cited_pmid,
        "cited_abstract": fetch_abstract(cited_pmid) if fetch_abstract else None,
        "cited_is_review": item.get("cited_is_review"),
        "review_reflist": [],
        "review_fulltext_available": None,
    }
    if item.get("cited_is_review") is True and fetch_reflist is not None:
        prov, avail = fetch_reflist(cited_pmid)
        evidence["review_reflist"] = prov or []
        evidence["review_fulltext_available"] = avail
    return evidence


# ==========================================================================
# 4. Coverage verdicts (injected LLM coverage judge) + deterministic route
# ==========================================================================
def _tristate(value) -> "bool | None":
    """Coerce a judge's ``established`` field to the tri-state {True, False,
    None}. Anything that is not exactly True or False is unknown (None)."""
    if value is True:
        return True
    if value is False:
        return False
    return None


def coverage_verdicts(claims: list, evidence: dict, *,
                      judge: CoverageJudge) -> list:
    """Per-claim coverage verdict from the injected ``judge``.

    ``established`` is abstract-scoped and tri-state: True means the abstract
    STATES AND SUPPORTS the claim (presence, F3-D3), False means the abstract
    CONTRADICTS it (the only abstract-scoped fault), None means the abstract is
    silent, leaves a load-bearing specificity unconfirmed, or the judge could not
    decide -- unknown, never a gap. Verdicts are stamped with
    :data:`COVERAGE_PROMPT_VERSION`. A judge that returns nothing / too few items
    leaves the remaining claims at ``established=None``.

    The raw structured fields (``engages_subject``, ``contradicts``,
    ``unconfirmed_specifics``) ride alongside so run_band can tally the coverage
    distribution; they default to None/[] on a judge (e.g. the no-usable-abstract
    path) that does not supply them. Item-record fields only -- never blind."""
    if not claims:
        return []
    raw = judge(claims, evidence) or []
    out = []
    for i, claim in enumerate(claims):
        v = raw[i] if i < len(raw) else None
        v = v if isinstance(v, dict) else {}
        out.append({
            "claim": claim,
            "established": _tristate(v.get("established")),
            "rationale": v.get("rationale", ""),
            "evidence_span": v.get("evidence_span", ""),
            "engages_subject": v.get("engages_subject"),
            "contradicts": v.get("contradicts"),
            "unconfirmed_specifics": v.get("unconfirmed_specifics", []),
            "prompt_version": COVERAGE_PROMPT_VERSION,
        })
    return out


def route(verdicts: list) -> str:
    """Deterministic coverage-gate route from the per-claim verdicts.

    Tri-state discipline: decided with ``is False`` / ``is True``, never a falsy
    check (``None`` is unknown, not a gap).
      * any ``established is False`` -> ``F6_FLAGGED``
      * all ``established is True``  -> ``FULL_COVERAGE`` (vacuously true when
                                        there are no claims)
      * otherwise (some None, no False) -> ``HELD_LOW_CONFIDENCE``"""
    established = [v.get("established") for v in verdicts]
    if any(e is False for e in established):
        return ROUTE_F6_FLAGGED
    if all(e is True for e in established):
        return ROUTE_FULL_COVERAGE
    return ROUTE_HELD


def _proposed_verdict(route_value: str) -> "str | None":
    """The system's terminal-label GUESS for disagreement analysis (item record
    only, never the annotation payload). Only a refuted claim yields a concrete
    guess (F6); FULL_COVERAGE is staged for the F3 discriminator (undecided
    here) and HELD carries no guess."""
    return "F6" if route_value == ROUTE_F6_FLAGGED else None


# Coverage-distribution buckets: the four findings an abstract can yield about a
# claim, tallied PER ATOMIC CLAIM for the calibration pass. This separates "the
# abstract contradicts this" (the only abstract-scoped fault) from "the abstract
# doesn't say," so the ratio can decide whether abstract-scoped F6 is a headline
# result or a screening stage.
COVERAGE_ESTABLISHED = "coverage_established"
COVERAGE_CONTRADICTED = "coverage_contradicted"
COVERAGE_UNCONFIRMED_SPECIFIC = "coverage_unconfirmed_specific"
COVERAGE_OFF_TOPIC = "coverage_off_topic"
_COVERAGE_BUCKETS = (
    COVERAGE_ESTABLISHED, COVERAGE_CONTRADICTED,
    COVERAGE_UNCONFIRMED_SPECIFIC, COVERAGE_OFF_TOPIC,
)


def coverage_bucket(verdict: dict) -> "str | None":
    """Classify one per-claim coverage verdict into its abstract-scoped bucket.

    Sourced from the raw structured fields the coverage parser carries:
      * ``contradicts`` true                         -> COVERAGE_CONTRADICTED (F6)
      * ``engages_subject`` false                    -> COVERAGE_OFF_TOPIC (HELD)
      * engaged, specifics unconfirmed               -> COVERAGE_UNCONFIRMED_SPECIFIC (HELD)
      * engaged, no contradiction, nothing unconfirmed -> COVERAGE_ESTABLISHED

    Returns None when the verdict carries no structured judgment (the
    deterministic no-usable-abstract path), so it is not miscounted as a judged
    claim -- those are already accounted for by the item-level
    ``no_usable_abstract`` counter."""
    if verdict.get("contradicts") is True:
        return COVERAGE_CONTRADICTED
    if verdict.get("engages_subject") is False:
        return COVERAGE_OFF_TOPIC
    if verdict.get("engages_subject") is True:
        if verdict.get("unconfirmed_specifics"):
            return COVERAGE_UNCONFIRMED_SPECIFIC
        return COVERAGE_ESTABLISHED
    return None


# ==========================================================================
# 5. Annotation payload -- identical across strata, BLIND to the proposed verdict
# ==========================================================================
# The ONLY evidence keys an annotator may see: exactly what assemble_evidence
# produces. Blindness is enforced by whitelist, not by trusting the item to be
# clean -- the evidence dict is built from injected fetchers and model-adjacent
# data, so a rationale, an evidence_span, or a nested proposed_route can end up
# in it. Anything not named here is dropped from the payload (the item record
# keeps it; only the annotator's view is narrowed).
ANNOTATION_EVIDENCE_KEYS = ("cited_pmid", "cited_abstract", "cited_is_review",
                            "review_reflist", "review_fulltext_available")
_ANNOTATION_BLIND_KEYS = frozenset({
    "proposed_route", "proposed_verdict", "rationale",
})


def _scrub_annotation_value(value):
    """Recursively remove machine-judgment keys from annotator-visible data.

    The outer evidence whitelist is necessary but not sufficient: injected
    evidence can contain nested dictionaries (notably review-reference rows).
    Preserve their useful source metadata while dropping forbidden keys at any
    depth. Tuples are normalized to lists so the returned payload remains JSON-
    serializable under the same contract as the ordinary evidence path.
    """
    if isinstance(value, dict):
        return {
            key: _scrub_annotation_value(nested)
            for key, nested in value.items()
            if key not in _ANNOTATION_BLIND_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_scrub_annotation_value(nested) for nested in value]
    return value


def _new_worksheet() -> dict:
    """The annotator's Phase-2 worksheet, all null until they commit. Six-key
    shape reused verbatim from the two F3 tools so downstream tooling is shared.
    The annotator walks coverage then provenance FRESH and BLIND, reproducing the
    routed tree the system walks; their terminal label is gold."""
    return {
        "F3_V1_coverage": None,       # does the cited paper state AND support the claim?
        "F3_V2_origin": None,         # own primary result (ACCURATE) or restatement (F3)?
        "F3_V3_repair_target_pmid": None,  # rightful primary from the review reflist
        "F3_V4_loop_closed": None,    # does that primary actually contain the finding?
        "confirmed_F3": None,
        "annotator": None,
    }


def annotation_payload(item: dict) -> dict:
    """Build the annotator payload for one item.

    IDENTICAL fields for every stratum, and BLIND: it carries the unit, the
    atomic claims, the evidence, the label space, and an empty worksheet -- but
    NEVER ``proposed_route`` / ``proposed_verdict`` (revealed only after the
    annotator commits, for disagreement analysis).

    Blindness is a WHITELIST at both levels: these seven keys, and within
    ``evidence`` only :data:`ANNOTATION_EVIDENCE_KEYS`. The machine-judgment
    keys are also removed recursively from nested item-derived containers. A
    contaminated evidence dict is silently narrowed rather than rejected -- a
    stray key is not worth aborting a batch over, and dropping it costs the
    annotator nothing."""
    evidence = item.get("evidence")
    if not isinstance(evidence, dict):
        evidence = {}
    return {
        "item_key": _scrub_annotation_value(item["item_key"]),
        "citing_sentence": _scrub_annotation_value(item["citing_sentence"]),
        "cited_pmid": _scrub_annotation_value(item["cited_pmid"]),
        "atomic_claims": _scrub_annotation_value(item.get("atomic_claims", [])),
        "evidence": _scrub_annotation_value({
            k: v for k, v in evidence.items()
            if k in ANNOTATION_EVIDENCE_KEYS
        }),
        "label_space": list(LABEL_SPACE_F3),
        "worksheet": _new_worksheet(),
    }


# ==========================================================================
# 6. Pipeline over a dir of PMC-OA citing papers -- drive-first, resumable
# ==========================================================================
def _safe_text(s: str) -> str:
    """Force an exception message into text that always survives the JSONL write.

    An encoding error's own message can carry the offending character, so storing
    a raw ``str(e)`` in a durable record can be exactly as unwritable as the row
    it replaced -- that would move the crash, not remove it."""
    return s.encode("utf-8", "backslashreplace").decode("ascii", "replace")


def _safe_json(obj):
    """Recursively replace ONLY the strings UTF-8 cannot encode.

    Valid non-ASCII -- accents, smart quotes, CJK, emoji -- is preserved verbatim.
    A blanket ASCII-fold would silently narrow the corpus, which is a worse defect
    than the crash this guards against."""
    if isinstance(obj, str):
        try:
            obj.encode("utf-8")
        except UnicodeEncodeError:
            return _safe_text(obj)
        return obj
    if isinstance(obj, dict):
        return {_safe_json(k): _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    return obj


def _reject_unencodable(obj, what: str) -> None:
    """Raise ValueError if obj cannot survive the JSONL round trip.

    json.loads accepts a lone surrogate; UTF-8 cannot encode one. So strict schema
    validation passes, the record is built, and the write blows up later in
    _append_jsonl -- past the per-reference guard, aborting the batch after the
    doc's earlier rows are already flushed and before its checkpoint line is
    written. Catching it here, while that guard is still in scope, turns a
    batch-killing UnicodeEncodeError into an ordinary parse quarantine."""
    try:
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, TypeError, ValueError) as e:
        raise ValueError(f"{what} is not JSONL-encodable: {_safe_text(str(e))}")


def _append_jsonl(fh, obj: dict) -> None:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    fh.flush()


def _pmcid_from_filename(fn: str) -> str:
    import re
    return re.sub(r"\.n?xml$", "", fn)


def _load_checkpoint(path: str) -> set:
    done: set = set()
    if not os.path.exists(path):
        return done
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            pmcid = rec.get("pmcid")
            if pmcid:
                done.add(pmcid)
    return done


def run_band(xml_dir: str, out_dir: str, *, extractor: Extractor,
             coverage_judge: CoverageJudge, fetch_abstract: FetchAbstract,
             fetch_reflist: "FetchReflist | None" = None,
             max_docs: "int | None" = None, email: str = DEFAULT_EMAIL,
             api_key: str = "", session=None) -> dict:
    """Run the judgment-band front-end over a directory of PMC-OA citing papers.

    Pipeline per doc: parse -> build items -> extract claims -> assemble
    evidence -> coverage verdicts -> route. Drive-first, checkpoint-and-resume
    (same pattern as the collector), with live progress prints on the doc loop.

    The DOCUMENT is the unit of durability: a doc's rows are buffered in memory
    and written only once the whole doc is through, immediately before its
    checkpoint line. The checkpoint is per document, so anything written before
    it is replayed on resume; buffering keeps a mid-document stop from leaving
    half a doc's rows behind for the next run to append a second time.

    The REFERENCE stays the unit of judgment and of counting. Only claim
    extraction is shared: it is keyed on the exact citing sentence within a doc,
    so a citance citing [5,6,7,8] extracts once and is judged four times. Every
    counter, item record, and queue row remains per reference.

    Writes three files in ``out_dir``:
      * ``judgment_band_items.jsonl``            -- one item record per unit,
        carrying the system's proposed_route / proposed_verdict (item record ONLY).
      * ``judgment_band_annotation_queue.jsonl`` -- one BLIND annotator payload
        per unit (no proposed verdict).
      * ``judgment_band_manifest.json``          -- counts, params, pinned prompt
        versions, gold-discipline warning.

    Every helper (extractor, coverage_judge, fetch_abstract, fetch_reflist) is
    injected, so this is fully offline-testable. The cited-work review check uses
    the module-global ``ncbi_pubtypes`` / ``is_review`` (monkeypatched in tests,
    wired live in the notebook)."""
    os.makedirs(out_dir, exist_ok=True)
    items_path = os.path.join(out_dir, "judgment_band_items.jsonl")
    queue_path = os.path.join(out_dir, "judgment_band_annotation_queue.jsonl")
    manifest_path = os.path.join(out_dir, "judgment_band_manifest.json")
    checkpoint_path = os.path.join(out_dir, "judgment_band_checkpoint.jsonl")

    done = _load_checkpoint(checkpoint_path)
    if session is None:
        import requests
        session = requests.Session()

    counts = {
        "docs_processed": 0,
        "refs_seen": 0,
        "items_built": 0,
        EXCLUDED_NO_CITANCE: 0,
        EXCLUDED_NO_CITED_PMID: 0,
        "no_usable_abstract": 0,      # sentinel/missing/empty abstract (evidence_is_usable False)
        "cited_is_review": 0,
        ROUTE_F6_FLAGGED: 0,
        ROUTE_FULL_COVERAGE: 0,
        ROUTE_HELD: 0,
        ROUTE_PARSE_QUARANTINE: 0,     # malformed model output, row quarantined
        # Per-atomic-claim coverage distribution (not per item). The route
        # counters above stay item-level and unchanged; these four partition the
        # coverage judgments made against a usable abstract.
        COVERAGE_ESTABLISHED: 0,
        COVERAGE_CONTRADICTED: 0,      # the only abstract-scoped evidence of a fault
        COVERAGE_UNCONFIRMED_SPECIFIC: 0,
        COVERAGE_OFF_TOPIC: 0,
    }
    pubtype_cache: dict = {}

    files = sorted(fn for fn in os.listdir(xml_dir)
                   if fn.endswith((".xml", ".nxml")))

    items_fh = open(items_path, "a", encoding="utf-8")
    queue_fh = open(queue_path, "a", encoding="utf-8")
    ckpt_fh = open(checkpoint_path, "a", encoding="utf-8")
    try:
        scanned = 0
        for fn in files:
            pmcid = _pmcid_from_filename(fn)
            if pmcid in done:
                continue
            if max_docs is not None and scanned >= max_docs:
                break
            scanned += 1
            counts["docs_processed"] += 1
            path = os.path.join(xml_dir, fn)
            print(f"[band] doc {counts['docs_processed']}: {pmcid}")

            try:
                refs = parse_pmc_xml(path, source_pmcid=pmcid)
            except Exception as e:                    # noqa: BLE001 - best-effort
                # _safe_text FIRST: a parse error's message can itself carry text
                # neither the console nor the checkpoint file can encode, and the
                # print would abort the batch from the very path meant to survive
                # one. Print exactly what gets stored.
                safe_error = _safe_text(str(e))
                print(f"[band-parse-skip] {pmcid}: {safe_error}")
                _append_jsonl(ckpt_fh, {"pmcid": pmcid, "error": safe_error})
                done.add(pmcid)
                continue

            # Rows for THIS doc, held until it completes (see the docstring).
            doc_items: list = []
            doc_queue: list = []
            # Atomic claims are a pure function of the citing sentence, so one
            # citance citing [5,6,7,8] extracts ONCE instead of once per
            # reference. Coverage is deliberately NOT shared: each cited paper
            # brings its own evidence, so it stays one judge call per reference
            # and the per-claim tally stays per reference too. Scoped to the doc
            # -- a citance is a within-document object, so this captures the
            # fanout while keeping the cache bounded on a corpus run.
            claims_cache: dict = {}

            for ref in refs:
                counts["refs_seen"] += 1
                reason = exclusion_reason(ref)
                if reason is not None:
                    counts[reason] += 1
                    continue
                item = build_item(ref)
                if item is None:                      # defensive; should not happen
                    continue

                # Review check (module-global helpers; cached per PMID).
                pmid = item["cited_pmid"]
                if pmid not in pubtype_cache:
                    pubtype_cache[pmid] = ncbi_pubtypes(pmid, api_key, email,
                                                        session)
                review = is_review(pubtype_cache[pmid])
                item["cited_is_review"] = review
                if review is True:
                    counts["cited_is_review"] += 1

                # Evidence, claims, coverage, route.
                item["evidence"] = assemble_evidence(
                    item, fetch_abstract=fetch_abstract,
                    fetch_reflist=fetch_reflist)
                # A sentinel / missing / empty abstract is NOT necessarily a fetch
                # failure and the row is NOT excluded -- it is simply not scoreable
                # by the coverage judge, which routes it HELD without an LLM call.
                # Count it under a precise name, with the SAME sufficiency gate the
                # judge uses so the two agree; a bare truthy-string check would miss
                # sentinels ("N/A", "unavailable", ...) and under-count.
                if not evidence_is_usable(item["evidence"]):
                    counts["no_usable_abstract"] += 1
                # Claim extraction and coverage parsing run injected model output
                # through STRICT schema validation, which raises ValueError on a
                # malformed reply (```json fence, extra/missing key, wrong type).
                # Guard the pair so one bad reply quarantines THIS reference to a
                # durable row-level record instead of aborting the whole batch.
                # Only ValueError (the parse/schema failure) is caught; operational
                # errors (network, etc.) still propagate as before.
                try:
                    sentence = item["citing_sentence"]
                    if sentence not in claims_cache:
                        # Assigned only on success, so a sentence whose reply is
                        # malformed keeps its per-reference retry and its
                        # per-reference quarantine count.
                        claims_cache[sentence] = extract_atomic_claims(
                            sentence, extractor=extractor)
                    # Copy: the cached list is shared by every reference on this
                    # citance, and each item record owns its own claims.
                    item["atomic_claims"] = list(claims_cache[sentence])
                    item["coverage_verdicts"] = coverage_verdicts(
                        item["atomic_claims"], item["evidence"],
                        judge=coverage_judge)
                    # Strict schema validation passes text that JSONL cannot
                    # encode (a lone surrogate parses as a perfectly good string).
                    # Check BOTH durable artifacts here, inside the guard, so the
                    # failure quarantines this reference through the branch below
                    # instead of escaping to _append_jsonl and killing the batch.
                    _reject_unencodable(item, "item record")
                    _reject_unencodable(annotation_payload(item),
                                        "annotation payload")
                except ValueError as e:
                    item["proposed_route"] = ROUTE_PARSE_QUARANTINE
                    item["proposed_verdict"] = None
                    # _safe_text for the same reason as the doc-level path above:
                    # the message can carry the very text that could not be
                    # encoded (a strict loader interpolates a raw duplicate JSON
                    # key, for one), so both the print and the stored value would
                    # re-raise from inside this handler. Sanitize once, use twice.
                    safe_error = _safe_text(str(e))
                    item["parse_error"] = safe_error
                    item["claim_extract_prompt_version"] = CLAIM_EXTRACT_PROMPT_VERSION
                    item["coverage_prompt_version"] = COVERAGE_PROMPT_VERSION
                    item["ts"] = int(time.time())
                    counts[ROUTE_PARSE_QUARANTINE] += 1
                    print(f"[band-parse-quarantine] {pmcid} "
                          f"{item['citation_id']}: {safe_error}")
                    # Durable record for later inspection/retry; not added to the
                    # blind annotation queue (no coverage verdicts to annotate).
                    # The row recording the failure must itself be writable: when
                    # the payload is what was unencodable it is still on the item
                    # (evidence is assembled before this guard, and the verdicts
                    # were assigned before the check caught them). Sanitize only
                    # the strings UTF-8 rejects; valid non-ASCII survives verbatim.
                    doc_items.append(_safe_json(item))
                    continue
                r = route(item["coverage_verdicts"])
                item["proposed_route"] = r
                item["proposed_verdict"] = _proposed_verdict(r)
                counts[r] += 1

                # Per-atomic-claim coverage tally (calibration): the abstract-
                # scoped distribution the tri-state gate produces. Verdicts on
                # the no-usable-abstract path carry no structured fields and are
                # skipped (coverage_bucket returns None), already counted at item
                # level under no_usable_abstract.
                for verdict in item["coverage_verdicts"]:
                    bucket = coverage_bucket(verdict)
                    if bucket is not None:
                        counts[bucket] += 1

                # Stamp pinned prompt versions on the item record.
                item["claim_extract_prompt_version"] = CLAIM_EXTRACT_PROMPT_VERSION
                item["coverage_prompt_version"] = COVERAGE_PROMPT_VERSION
                item["ts"] = int(time.time())

                counts["items_built"] += 1
                doc_items.append(item)
                doc_queue.append(annotation_payload(item))

            # Doc is through: publish its rows, THEN checkpoint it. An interrupt
            # anywhere above leaves nothing durable for the resume to duplicate.
            for row in doc_items:
                _append_jsonl(items_fh, row)
            for row in doc_queue:
                _append_jsonl(queue_fh, row)
            _append_jsonl(ckpt_fh, {"pmcid": pmcid})
            done.add(pmcid)
    finally:
        items_fh.close()
        queue_fh.close()
        ckpt_fh.close()

    manifest = {
        "band": "F3-F7 judgment-band front-end (coverage gate; stratum-agnostic)",
        "warning": (
            "FRONT-END SUBSTRATE, not a labeler. coverage_judge and extractor "
            "are the SYSTEM under evaluation, NOT ground truth. No F3-F7 label "
            "is machine-assigned here; the gold label is the human annotator's. "
            "proposed_route / proposed_verdict live on the item record ONLY and "
            "are BLIND to the annotator until after commit."
        ),
        "detector_independent_annotation": True,
        "claim_extract_prompt_version": CLAIM_EXTRACT_PROMPT_VERSION,
        "coverage_prompt_version": COVERAGE_PROMPT_VERSION,
        "label_space": list(LABEL_SPACE_F3),
        "params": {
            "xml_dir": xml_dir,
            "out_dir": out_dir,
            "max_docs": max_docs,
            "email": email,
            "api_key_present": bool(api_key),
            "fetch_reflist_wired": fetch_reflist is not None,
        },
        "counts": counts,
        "coverage_distribution": {
            COVERAGE_ESTABLISHED: counts[COVERAGE_ESTABLISHED],
            COVERAGE_CONTRADICTED: counts[COVERAGE_CONTRADICTED],
            COVERAGE_UNCONFIRMED_SPECIFIC: counts[COVERAGE_UNCONFIRMED_SPECIFIC],
            COVERAGE_OFF_TOPIC: counts[COVERAGE_OFF_TOPIC],
            "note": (
                "Per atomic claim, abstract-scoped. coverage_contradicted is the "
                "ONLY abstract-scoped evidence of a fault (routes F6_FLAGGED); "
                "coverage_unconfirmed_specific and coverage_off_topic are the "
                "full-text escalation queue (route HELD_LOW_CONFIDENCE). "
                "coverage_established routes FULL_COVERAGE. The no-usable-abstract "
                "path is excluded here and counted under counts.no_usable_abstract."
            ),
        },
        "distinct_cited_pmids_looked_up": len(pubtype_cache),
        "items_path": items_path,
        "annotation_queue_path": queue_path,
        "checkpoint_path": checkpoint_path,
        "manifest_path": manifest_path,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest
