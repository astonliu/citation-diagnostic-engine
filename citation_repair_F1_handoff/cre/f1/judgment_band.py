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

A fourth, NON-coverage route exists for the case where the gate never ran:

  * the extractor returned no atomic claims at all          -> ``NO_CLAIMS``

``NO_CLAIMS`` is EXCLUDED FROM THE SCOREABLE DENOMINATOR and counted separately
(its own manifest counter, added only when it fires). It is a structural
non-judgment, the same kind of thing as ``excluded_no_citance``: nothing was
judged, so the reference can be neither a numerator nor a denominator member of a
coverage or precision figure. It is emphatically NOT folded into FULL_COVERAGE,
which is where the vacuous ``all([])`` used to put it.

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
# The opt-in full-text path (DEC-030/032). Both modules live OUTSIDE the frozen
# substrate for the same reason coverage_aggregate does: band_prompts.py cannot
# be edited without drifting its pinned blob OID.
from .coverage_aggregate import no_usable_fulltext_dict
# Co-citation grouping (leaf; imports nothing from this module, so no cycle). The
# four coverage-bucket names live there as the single source of truth and are
# re-exported below under the public names this module has always used, so the
# two vocabularies cannot drift and no manifest byte moves.
from . import cocitation
from .cocitation import (
    ROUTE_GROUP_COVERED, ROUTE_GROUP_COVERAGE_GAP, ROUTE_UNSUPPORTED_MEMBER)
from .coverage_prompts_v3 import (
    COVERAGE_PROMPT_VERSION_V3, SPAN_MISS_STATUSES,
    RESPONSE_PARSER_VERSION as RESPONSE_PARSER_VERSION_V3)
from .sentence_spans import segmenter_provenance

# Label space for the F3 slice of the band (the annotator's terminal choices).
# Coverage-gap -> F6, misattribution -> F3, otherwise ACCURATE.
LABEL_SPACE_F3 = ["F6", "F3", "ACCURATE"]

# Deterministic routes out of the coverage gate.
ROUTE_F6_FLAGGED = "F6_FLAGGED"
ROUTE_FULL_COVERAGE = "FULL_COVERAGE"
ROUTE_HELD = "HELD_LOW_CONFIDENCE"
# The extractor returned NO atomic claims for this reference, so there is nothing
# to judge and no verdict of any kind. Its own terminal route because the vacuous
# case is NOT a coverage outcome: `all()` over an empty list returns True, so
# before this route existed a claim-less reference fell out of the FULL_COVERAGE
# branch -- a false clear entering the F3 discriminator that no downstream counter
# could tell from a real one. Measured live (calibration runs 1 and 2, 2026-08-11):
# run 1 reported FULL_COVERAGE 3 with all four coverage counters at 0, and run 2
# reported FULL_COVERAGE 6 against 6 complete retrievals and 8 non-HELD routes.
# Both reconcile on exactly 3 claim-less items.
ROUTE_NO_CLAIMS = "NO_CLAIMS"
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
    ``cited_pmid`` additionally pin pair identity. THE GROUP DOES NOT REPLACE THE
    ID: ``citance_group_id`` rides alongside as context, because labels key on
    citation_id across prompt versions and the Band-1 disposition joins on it
    (``preband_contract``).

    ``citance_group_id`` / ``citance_group_members`` carry the co-citation group
    the parser resolved -- the other references this sentence cites collectively.
    Empty on a Reference built outside ``parser.link_citances``, which every
    consumer reads as a singleton, i.e. the pre-group behaviour."""
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
        "citance_group_id": getattr(ref, "citance_group_id", "") or "",
        "citance_group_members": list(
            getattr(ref, "citance_group_members", None) or []),
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
                      fetch_reflist: "FetchReflist | None" = None,
                      fetch_fulltext=None) -> dict:
    """Assemble the evidence a judge needs to check the claims.

    Always fetches the cited paper's abstract. When the cited work is a review
    (``item["cited_is_review"] is True``) and ``fetch_reflist`` is supplied, also
    fetches the review's own reference list (via ``ncbi_pmc_reflist``) -- the
    F3-V3 rightful-primary candidate pool that a downstream provenance
    discriminator and the annotator draw on.

    ``fetch_fulltext`` (``pmid -> fulltext_reader.fetch_fulltext`` dict) is the
    OPT-IN full-text seam (DEC-030). Supplied, it adds ``cited_fulltext`` carrying
    the reader's result whole -- resolved, retrieval_complete, incomplete_reasons,
    sections_present, sections, sanitized_paths -- so the completeness signal the
    DEC-032 aggregate needs travels with the evidence. Absent, the key is not
    added at all and this returns byte-identical evidence to before.

    The abstract fields are assembled unconditionally either way: during
    calibration the two scopes COEXIST, and retiring the abstract path happens at
    freeze time, not here."""
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
    if fetch_fulltext is not None:
        evidence["cited_fulltext"] = fetch_fulltext(cited_pmid)
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
                      judge: CoverageJudge,
                      prompt_version: str = COVERAGE_PROMPT_VERSION,
                      parser_version: str = "") -> list:
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
    path) that does not supply them. Item-record fields only -- never blind.

    THE SPAN SHAPE FOLLOWS ``parser_version``. Empty -- the default -- means the
    frozen five-key ABSTRACT contract: one ``evidence_span`` string, and no
    ``response_parser_version`` key, so a default run's records are byte-identical
    to what they have always been. Non-empty means the current FULL-TEXT contract:
    one ``evidence_spans`` LIST of ``{label, text}`` entries, one per contiguous
    passage (ZD 2026-08-11, run 3 item 2, superseding the ``_v2`` label/text pair).

    The parser version is stamped on EVERY verdict of the full-text path --
    including the deterministic holds, which never went through a parser. That stamp
    names the REPLY CONTRACT the row was produced under, not a claim that a reply
    was parsed. It is what lets a reader tell a ``_v2`` row from a span-list row,
    and without it on every row a file spanning a contract change could not be read
    at all."""
    if not claims:
        return []
    raw = judge(claims, evidence) or []
    out = []
    for i, claim in enumerate(claims):
        v = raw[i] if i < len(raw) else None
        v = v if isinstance(v, dict) else {}
        record = {
            "claim": claim,
            "established": _tristate(v.get("established")),
            "rationale": v.get("rationale", ""),
        }
        if parser_version:
            record["evidence_spans"] = list(v.get("evidence_spans") or [])
            # RECORDED AND REPORTED, never a gate (DEC-047). Absent from a judge that
            # does not supply it would be indistinguishable from a genuine
            # not_applicable, so it defaults to the honest "we did not ask".
            record["span_status"] = v.get("span_status") or "not_applicable"
        else:
            record["evidence_span"] = v.get("evidence_span", "")
        record.update({
            "engages_subject": v.get("engages_subject"),
            "contradicts": v.get("contradicts"),
            "unconfirmed_specifics": v.get("unconfirmed_specifics", []),
            # Defaults to the abstract-scoped version, so the default path stamps
            # exactly what it stamped before; the full-text path passes v3.
            "prompt_version": prompt_version,
        })
        if parser_version:
            record["response_parser_version"] = parser_version
        out.append(record)
    return out


def route(verdicts: list) -> str:
    """Deterministic coverage-gate route from the per-claim verdicts.

    Tri-state discipline: decided with ``is False`` / ``is True``, never a falsy
    check (``None`` is unknown, not a gap).
      * NO verdicts at all             -> ``NO_CLAIMS`` (checked FIRST)
      * any ``established is False``   -> ``F6_FLAGGED``
      * all ``established is True``    -> ``FULL_COVERAGE``
      * otherwise (some None, no False) -> ``HELD_LOW_CONFIDENCE``

    The empty case is checked first and terminates, because both branches below
    it answer it wrongly: ``all()`` over an empty list is vacuously True, so an
    empty list used to route FULL_COVERAGE -- "every claim is established" said
    of no claims. FULL_COVERAGE is the stage that feeds the F3 discriminator, and
    a claim-less reference entering it is a false clear. Only the VACUOUS case
    moved; routing for non-empty lists is unchanged."""
    if not verdicts:
        return ROUTE_NO_CLAIMS
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
    here), HELD carries no guess, and NO_CLAIMS carries none either -- nothing was
    judged, so there is nothing to guess from."""
    return "F6" if route_value == ROUTE_F6_FLAGGED else None


# Coverage-distribution buckets: the four findings an abstract can yield about a
# claim, tallied PER ATOMIC CLAIM for the calibration pass. This separates "the
# abstract contradicts this" (the only abstract-scoped fault) from "the abstract
# doesn't say," so the ratio can decide whether abstract-scoped F6 is a headline
# result or a screening stage.
COVERAGE_ESTABLISHED = cocitation.BUCKET_ESTABLISHED
COVERAGE_CONTRADICTED = cocitation.BUCKET_CONTRADICTED
COVERAGE_UNCONFIRMED_SPECIFIC = cocitation.BUCKET_UNCONFIRMED_SPECIFIC
COVERAGE_OFF_TOPIC = cocitation.BUCKET_OFF_TOPIC
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


def item_buckets(item: dict) -> list:
    """One coverage bucket per claim for one item, aligned to ``atomic_claims``.

    ``None`` entries are verdicts carrying no structured judgment (the
    deterministic no-usable-abstract / no-usable-fulltext path): neither evidence
    of coverage nor evidence of a gap."""
    return [coverage_bucket(v) for v in (item.get("coverage_verdicts") or [])]


def apply_cocitation_routing(items: "list[dict]") -> tuple:
    """Re-route a document's items with their CO-CITATION GROUPS in view.

    Called once per document, after every item has its per-reference coverage
    verdicts and its solo route, and before anything is written. Judging stays
    per reference and the prompts are untouched; only the interpretation of the
    result becomes group-aware.

    Mutates each item in place and returns ``(group_records, route_counts,
    stats)``. The route counters are returned rather than applied so the caller
    keeps ownership of the manifest's counter set.

    A group of ONE is a no-op: its route, verdict and record are exactly what
    they were, and no ``cocitation`` block or ``proposed_route_solo`` key is
    added. A document with no co-cited sentence therefore produces the same item
    rows and the same manifest counters as before this existed.

    PARSE_QUARANTINE rows are held out entirely: they carry no trustworthy
    verdicts, so they can neither cover a claim for a sibling nor be excused by
    one. They keep their quarantine route and are named in the group record's
    ``excluded_members``.
    """
    routable = [it for it in items
                if it.get("proposed_route") != ROUTE_PARSE_QUARANTINE]
    quarantined_by_group: dict = {}
    for it in items:
        if it.get("proposed_route") == ROUTE_PARSE_QUARANTINE:
            gid = cocitation.group_id_of(it)
            if gid:
                quarantined_by_group.setdefault(gid, []).append(
                    {"citation_id": it.get("citation_id"),
                     "reason": cocitation.EXCLUDED_NO_VERDICTS})

    group_records: list = []
    route_counts: dict = {}
    size_distribution: dict = {}
    groups = cocitation.partition(routable)
    for gid, members in groups.items():
        size = len(members)
        size_distribution[str(size)] = size_distribution.get(str(size), 0) + 1
        aggregated = cocitation.aggregate(members, buckets_of=item_buckets)
        if gid in quarantined_by_group:
            aggregated["excluded_members"].extend(quarantined_by_group[gid])
        routes: dict = {}
        for item in members:
            solo = item.get("proposed_route")
            final = cocitation.member_route(
                buckets=item_buckets(item), solo_route=solo,
                aggregated=aggregated, group_size=size,
                citation_id=item["citation_id"])
            if final != solo:
                # Present exactly when the group changed the answer, so a reader
                # can see what it changed and a run with no co-citation keeps
                # byte-identical rows.
                item["proposed_route_solo"] = solo
            item["proposed_route"] = final
            item["proposed_verdict"] = _proposed_verdict(final)
            routes[item["citation_id"]] = final
            route_counts[final] = route_counts.get(final, 0) + 1
        if size > 1:
            record = cocitation.group_record(gid, members, aggregated, routes)
            group_records.append(record)
            for item in members:
                item["cocitation"] = {
                    "citance_group_id": record["citance_group_id"],
                    "size": size,
                    "members": list(record["members"]),
                    "claims_covered": record["claims_covered"],
                    "claims_uncovered": record["claims_uncovered"],
                    "claims_unknown": record["claims_unknown"],
                    "uncovered_claims": list(record["uncovered_claims"]),
                }
    stats = {
        "groups": len(groups),
        "cocitation_groups": sum(1 for m in groups.values() if len(m) > 1),
        "members_in_cocitation_groups": sum(
            len(m) for m in groups.values() if len(m) > 1),
        "group_size_distribution": dict(sorted(
            size_distribution.items(), key=lambda kv: int(kv[0]))),
    }
    return group_records, route_counts, stats


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
                            "review_reflist", "review_fulltext_available",
                            # The retrieved body the annotator judges against at
                            # full-text scope. It is nested and reader-built, so
                            # the RECURSIVE scrub is what keeps it blind -- the
                            # outer whitelist alone would let a contaminated
                            # section carry proposed_route in with it.
                            "cited_fulltext")
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


def _child_path(path: str, part) -> str:
    return f"{path}.{part}" if path else str(part)


def _safe_json(obj, touched=None, _path=""):
    """Recursively rewrite ONLY what JSONL cannot carry, recording every change.

    Valid non-ASCII -- accents, smart quotes, CJK, emoji -- is preserved verbatim.
    A blanket ASCII-fold would silently narrow the corpus, which is a worse defect
    than the crash this guards against.

    Three things get rewritten:

      * a string UTF-8 cannot encode (a lone surrogate in model text);
      * a value ``json`` cannot serialize at all -- a CODE defect rather than
        model text, e.g. a seam returning an HTTP response instead of its text.
        Without this the quarantine row stays as unwritable as the row it
        replaced and the batch still dies at the write, which is the exact
        failure this whole path exists to prevent;
      * a key whose sanitized form collides with a sibling, which would
        otherwise let the second value silently overwrite the first.

    ``touched`` collects one dotted path per rewrite, so the durable row can state
    which fields are no longer verbatim. A reader inspecting a quarantined row
    must never have to guess which text is the model's and which is ours."""
    if touched is None:
        touched = []
    if isinstance(obj, str):
        try:
            obj.encode("utf-8")
        except UnicodeEncodeError:
            touched.append(_path or "<root>")
            return _safe_text(obj)
        return obj
    if isinstance(obj, dict):
        out: dict = {}
        for key, value in obj.items():
            safe_key = key
            if isinstance(key, str):
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError:
                    safe_key = _safe_text(key)
                    touched.append(_child_path(_path, safe_key) + " (key)")
            if safe_key in out:
                index = 2
                while f"{safe_key}#{index}" in out:
                    index += 1
                safe_key = f"{safe_key}#{index}"
                touched.append(_child_path(_path, safe_key) + " (key collision)")
            out[safe_key] = _safe_json(value, touched,
                                       _child_path(_path, safe_key))
        return out
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v, touched, f"{_path}[{i}]")
                for i, v in enumerate(obj)]
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    touched.append(_path or "<root>")
    return _safe_text(repr(obj))


# Why a quarantine happened. Both causes route to ROUTE_PARSE_QUARANTINE -- the
# route is unchanged -- but they must not share one counter: the first is model
# noise, the second is a bug in our own wiring, and a code defect that reads as
# model noise in the funnel is a defect nobody goes looking for.
QUARANTINE_CAUSE_UNENCODABLE_TEXT = "unencodable_text"
QUARANTINE_CAUSE_NOT_SERIALIZABLE = "not_serializable"


class UnencodableRecord(ValueError):
    """A record that cannot survive the JSONL round trip.

    A ``ValueError`` subclass, so the existing per-reference guard catches it with
    no new branch; ``cause`` is what lets the funnel tell the two apart."""

    def __init__(self, message: str, cause: str):
        super().__init__(message)
        self.cause = cause


def _reject_unencodable(obj, what: str) -> None:
    """Raise UnencodableRecord (a ValueError) if obj cannot survive the round trip.

    BACKLOG (deferred, deliberately out of the full-text wiring scope): this guard
    covers the row-level quarantine guarantees that spec requires -- a lone
    surrogate or a non-serializable value anywhere in the item record. NaN /
    Infinity (which json.dumps accepts and emits as invalid JSON), cyclic records,
    tuple dict keys, and unencodable values reaching the MANIFEST rather than a
    row are known gaps, none of them reachable from the seams wired here. They are
    a separate hardening pass, not a silent widening of this one.

    json.loads accepts a lone surrogate; UTF-8 cannot encode one. So strict schema
    validation passes, the record is built, and the write blows up later in
    _append_jsonl -- past the per-reference guard, aborting the batch after the
    doc's earlier rows are already flushed and before its checkpoint line is
    written. Catching it here, while that guard is still in scope, turns a
    batch-killing UnicodeEncodeError into an ordinary parse quarantine."""
    try:
        json.dumps(obj, ensure_ascii=False).encode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError) as e:
        raise UnencodableRecord(
            f"{what} is not JSONL-encodable: {_safe_text(str(e))}",
            QUARANTINE_CAUSE_UNENCODABLE_TEXT)
    except (TypeError, ValueError) as e:
        raise UnencodableRecord(
            f"{what} is not JSONL-serializable: {_safe_text(str(e))}",
            QUARANTINE_CAUSE_NOT_SERIALIZABLE)


def _append_jsonl(fh, obj: dict) -> None:
    fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
    fh.flush()


def _pmcid_from_filename(fn: str) -> str:
    import re
    return re.sub(r"\.n?xml$", "", fn)


#: Mode marker, written to the checkpoint ONLY on the opt-in full-text path. An
#: abstract-path run writes no marker at all, so its checkpoint file stays
#: byte-identical; absence of a marker IS the abstract mode.
BAND_MODE_FULLTEXT = "fulltext_coverage_v3"
_MODE_KEY = "band_mode"


def _checkpoint_mode(path: str) -> "tuple[bool, str | None]":
    """``(has_prior_work, mode)`` for an existing checkpoint file.

    ``mode`` is None for an abstract-path checkpoint, which carries no marker.
    ``_load_checkpoint`` ignores any line without a ``pmcid``, so the marker line
    is inert to the resume set."""
    if not os.path.exists(path):
        return False, None
    has_work = False
    mode = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            has_work = True
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get(_MODE_KEY):
                mode = rec[_MODE_KEY]
    return has_work, mode


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
             fetch_fulltext=None, coverage_judge_v3: "CoverageJudge | None" = None,
             max_docs: "int | None" = None, email: str = DEFAULT_EMAIL,
             api_key: str = "", session=None,
             model: str = "", assistant_prefill: str = "",
             stop_sequences: tuple = (), temperature=None) -> dict:
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

    Writes four files in ``out_dir``:
      * ``judgment_band_items.jsonl``            -- one item record per unit,
        carrying the system's proposed_route / proposed_verdict (item record ONLY).
      * ``judgment_band_annotation_queue.jsonl`` -- one BLIND annotator payload
        per unit (no proposed verdict).
      * ``judgment_band_cocitation_groups.jsonl`` -- one record per CO-CITATION
        group (a sentence occurrence citing two or more references): its members,
        which claims the group covered, and which claims no member covered.
      * ``judgment_band_manifest.json``          -- counts, params, pinned prompt
        versions, the co-citation block with BOTH candidate denominators, and the
        gold-discipline warning.

    Every helper (extractor, coverage_judge, fetch_abstract, fetch_reflist) is
    injected, so this is fully offline-testable. The cited-work review check uses
    the module-global ``ncbi_pubtypes`` / ``is_review`` (monkeypatched in tests,
    wired live in the notebook).

    FULL-TEXT PATH (opt-in, DEC-030/032). Supplying BOTH ``fetch_fulltext`` and
    ``coverage_judge_v3`` moves coverage from the cited abstract to the retrieved
    body: evidence gains ``cited_fulltext``, and a reference whose retrieval is
    complete is judged by the v3 judge over its sections. A reference whose
    retrieval is NOT complete is held without a model call, counted under
    ``no_usable_fulltext``, exactly mirroring the no-usable-abstract gate.
    Supplying neither leaves every byte of this function's output unchanged --
    that is the point of the opt-in, and it holds until calibration (DEC-040)
    says v3 is ready to freeze. Supplying only one is a configuration error and
    raises, rather than half-enabling a scope change.

    MODEL IDENTITY (``model`` / ``assistant_prefill`` / ``stop_sequences`` /
    ``temperature``).
    Every coverage number is conditional on the adapter that produced it -- the
    same class of omission as DEC-020's missing ``temperature`` / ``top_p`` -- so
    these three ride verbatim into ``manifest["params"]``. They are RECORDED, not
    used: the band makes no model call itself (every one goes through an injected
    callable), so it cannot observe them and must be told. An unsupplied value is
    recorded as ABSENT rather than guessed: a defaulted model name would be a
    fabricated provenance record, and a defaulted key would also change the
    manifest bytes of every default run. The notebook passes them; the band never
    infers them.

    ``temperature`` takes ``None`` for absent rather than ``""``, because 0 is both a
    REAL value and a falsy one -- DEC-046 pins ``temperature=0``, and a truthiness
    test would silently drop exactly the pinned value it exists to record
    (CONTRADICTIONS 41). A pin that is not recorded is not evidenced."""
    if (fetch_fulltext is None) != (coverage_judge_v3 is None):
        raise ValueError(
            "the full-text path needs BOTH fetch_fulltext and coverage_judge_v3; "
            "supplying one alone would silently judge full text with the "
            "abstract-scoped prompt, or fetch a body nothing reads")
    fulltext_path = fetch_fulltext is not None
    # Provenance must state the scope a row was ACTUALLY judged at. Defaults to
    # the frozen abstract version, so a default run stamps exactly what it
    # stamped before.
    coverage_version = (COVERAGE_PROMPT_VERSION_V3 if fulltext_path
                        else COVERAGE_PROMPT_VERSION)
    os.makedirs(out_dir, exist_ok=True)
    items_path = os.path.join(out_dir, "judgment_band_items.jsonl")
    queue_path = os.path.join(out_dir, "judgment_band_annotation_queue.jsonl")
    groups_path = os.path.join(out_dir, "judgment_band_cocitation_groups.jsonl")
    manifest_path = os.path.join(out_dir, "judgment_band_manifest.json")
    checkpoint_path = os.path.join(out_dir, "judgment_band_checkpoint.jsonl")

    # Resume gate: an out_dir belongs to ONE coverage mode. The checkpoint keys
    # on pmcid alone, so resuming across a mode switch would skip already-done
    # documents and leave one output set holding rows judged at two different
    # evidence scopes -- undetectable afterwards. Mismatch raises in EITHER
    # direction; the abstract path has no marker, so its files never move.
    _prior_work, _prior_mode = _checkpoint_mode(checkpoint_path)
    _wanted_mode = BAND_MODE_FULLTEXT if fulltext_path else None
    if _prior_work and _prior_mode != _wanted_mode:
        raise ValueError(
            f"judgment_band: this out_dir holds a run in mode "
            f"{_prior_mode or 'abstract (coverage_v2)'!r}, but this run is mode "
            f"{_wanted_mode or 'abstract (coverage_v2)'!r}. Resuming across a "
            "coverage-scope change would mix evidence scopes in one output set. "
            "Start a fresh out_dir.")

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
        # Sub-counters partitioning the encodability share of PARSE_QUARANTINE.
        # A plain malformed reply counts in neither; a code defect (a value json
        # cannot serialize) lands in the second and is visible in the funnel
        # instead of hiding inside the model-noise total.
        "parse_quarantine_" + QUARANTINE_CAUSE_UNENCODABLE_TEXT: 0,
        "parse_quarantine_" + QUARANTINE_CAUSE_NOT_SERIALIZABLE: 0,
        # Per-atomic-claim coverage distribution (not per item). The route
        # counters above stay item-level and unchanged; these four partition the
        # coverage judgments made against a usable abstract.
        COVERAGE_ESTABLISHED: 0,
        COVERAGE_CONTRADICTED: 0,      # the only abstract-scoped evidence of a fault
        COVERAGE_UNCONFIRMED_SPECIFIC: 0,
        COVERAGE_OFF_TOPIC: 0,
    }
    # Added ONLY on the opt-in full-text path. An unconditional key would change
    # the manifest of every default run, and the default path is required to be
    # byte-identical until calibration says v3 is ready to freeze.
    if fulltext_path:
        counts["no_usable_fulltext"] = 0
        # An engaged claim that ended up with no evidence recorded. A RECALL MISS,
        # counted and reported -- it used to raise and quarantine the whole reference
        # (DEC-047). Seeded to 0 on this path only, same precedent as the counter
        # above: an unconditional key would move the default run's manifest bytes.
        counts["evidence_span_not_found"] = 0
    # Evidence-selection measurement (DEC-047 item 5). Sentence ids make selection
    # measurable with NO new annotation, so these accumulate per run and feed
    # Recall@k / sentence-selection F1 once gold spans exist. Full-text path only.
    span_status_tally: dict = {}
    span_source_tally: dict = {}
    span_count_tally: dict = {}
    engaged_claims = 0
    engaged_claims_with_span = 0
    pubtype_cache: dict = {}
    # Co-citation accounting. THE UNIT OF ANALYSIS IS NOW AMBIGUOUS and that is
    # reported, not resolved here: some verdicts attach to a group of citations
    # rather than to one, so every downstream rate has two candidate
    # denominators. Both are surfaced in the manifest; picking one is ZD's call.
    cocitation_groups = 0
    cocitation_stats: dict = {}
    group_sizes: dict = {}
    covered_claims_total = 0
    uncovered_claims_total = 0
    unknown_claims_total = 0

    files = sorted(fn for fn in os.listdir(xml_dir)
                   if fn.endswith((".xml", ".nxml")))

    items_fh = open(items_path, "a", encoding="utf-8")
    queue_fh = open(queue_path, "a", encoding="utf-8")
    groups_fh = open(groups_path, "a", encoding="utf-8")
    ckpt_fh = open(checkpoint_path, "a", encoding="utf-8")
    if fulltext_path and _prior_mode is None:
        # Written once, before any document. The abstract path writes nothing.
        _append_jsonl(ckpt_fh, {_MODE_KEY: BAND_MODE_FULLTEXT})
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
                    fetch_reflist=fetch_reflist,
                    fetch_fulltext=fetch_fulltext)
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
                    held_for_incomplete_fulltext = False
                    # MODE COMES FROM CONFIGURATION, never from the fetched value.
                    # Inferring it from the presence of cited_fulltext would let a
                    # failed fetch (the reader returns None on an unresolvable
                    # PMID) silently drop an opted-in run back to abstract scope
                    # and judge it with v2 -- a scope change nothing in the output
                    # would record.
                    fulltext = item["evidence"].get("cited_fulltext")
                    complete = (isinstance(fulltext, dict)
                                and fulltext.get("retrieval_complete") is True)
                    if not fulltext_path:
                        # Default path, untouched: abstract scope, v2 prompt.
                        item["coverage_verdicts"] = coverage_verdicts(
                            item["atomic_claims"], item["evidence"],
                            judge=coverage_judge)
                    elif complete:
                        item["coverage_verdicts"] = coverage_verdicts(
                            item["atomic_claims"], item["evidence"],
                            judge=coverage_judge_v3,
                            prompt_version=COVERAGE_PROMPT_VERSION_V3,
                            parser_version=RESPONSE_PARSER_VERSION_V3)
                    else:
                        # Mirrors the no-usable-abstract gate: deterministic HELD,
                        # no model call of EITHER version. Reached by an
                        # incomplete retrieval and equally by a reader result that
                        # is None or not a dict at all -- a fetch failure is an
                        # unretrieved body, which is exactly what this branch is
                        # for. An incomplete body cannot support an argument from
                        # silence, and DEC-032 holds rather than flags when it
                        # cannot.
                        counts["no_usable_fulltext"] += 1
                        held_for_incomplete_fulltext = True
                        item["coverage_verdicts"] = coverage_verdicts(
                            item["atomic_claims"], item["evidence"],
                            judge=lambda claims, _evidence: [
                                no_usable_fulltext_dict() for _ in claims],
                            prompt_version=COVERAGE_PROMPT_VERSION_V3,
                            parser_version=RESPONSE_PARSER_VERSION_V3)
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
                    # None for an ordinary malformed reply; one of the two
                    # QUARANTINE_CAUSE_* values when the record itself could not
                    # be written. Tri-state: None means "not an encodability
                    # failure", never "unknown cause".
                    cause = getattr(e, "cause", None)
                    item["parse_error"] = safe_error
                    item["parse_quarantine_cause"] = cause
                    item["claim_extract_prompt_version"] = CLAIM_EXTRACT_PROMPT_VERSION
                    item["coverage_prompt_version"] = coverage_version
                    item["ts"] = int(time.time())
                    counts[ROUTE_PARSE_QUARANTINE] += 1
                    if cause is not None:
                        counts["parse_quarantine_" + cause] += 1
                    print(f"[band-parse-quarantine] {pmcid} "
                          f"{item['citation_id']}: {safe_error}")
                    # Durable record for later inspection/retry; not added to the
                    # blind annotation queue (no coverage verdicts to annotate).
                    # The row recording the failure must itself be writable: when
                    # the payload is what was unencodable it is still on the item
                    # (evidence is assembled before this guard, and the verdicts
                    # were assigned before the check caught them). Sanitize only
                    # what JSONL rejects; valid non-ASCII survives verbatim.
                    # sanitized_paths is ALWAYS present -- empty when the row is
                    # verbatim -- so an absent field never has to be interpreted,
                    # and a reader can tell our text from the model's.
                    sanitized_paths: list = []
                    row = _safe_json(item, sanitized_paths)
                    row["sanitized_paths"] = sanitized_paths
                    doc_items.append(row)
                    continue
                # The SOLO route: this reference judged alone against the whole
                # citing sentence. It is provisional until the document's
                # co-citation groups are known (apply_cocitation_routing, below),
                # so it is recorded now and COUNTED THERE -- counting here would
                # tally a route the group may still overturn.
                r = route(item["coverage_verdicts"])
                item["proposed_route"] = r
                item["proposed_verdict"] = _proposed_verdict(r)

                # Per-atomic-claim coverage tally (calibration): the abstract-
                # scoped distribution the tri-state gate produces. Verdicts on
                # the no-usable-abstract path carry no structured fields and are
                # skipped (coverage_bucket returns None), already counted at item
                # level under no_usable_abstract.
                for verdict in item["coverage_verdicts"]:
                    bucket = coverage_bucket(verdict)
                    if bucket is not None:
                        counts[bucket] += 1

                # Evidence-selection tallies (DEC-047 item 5), full-text path only.
                # Keyed off engages_subject rather than span_status, because the
                # DENOMINATOR is "claims where selection was actually attempted": an
                # off-topic claim has nothing to point at, and a deterministic hold
                # never reached the model, so counting either would understate recall
                # by padding it with rows that were never in the task.
                if fulltext_path:
                    for verdict in item["coverage_verdicts"]:
                        if verdict.get("engages_subject") is not True:
                            continue
                        engaged_claims += 1
                        spans = verdict.get("evidence_spans") or []
                        status = verdict.get("span_status") or "not_applicable"
                        span_status_tally[status] = (
                            span_status_tally.get(status, 0) + 1)
                        span_count_tally[str(len(spans))] = (
                            span_count_tally.get(str(len(spans)), 0) + 1)
                        if spans:
                            engaged_claims_with_span += 1
                        for span in spans:
                            source = span.get("span_source") or "unknown"
                            span_source_tally[source] = (
                                span_source_tally.get(source, 0) + 1)
                        if status in SPAN_MISS_STATUSES:
                            counts["evidence_span_not_found"] += 1

                # Stamp pinned prompt versions on the item record.
                item["claim_extract_prompt_version"] = CLAIM_EXTRACT_PROMPT_VERSION
                item["coverage_prompt_version"] = coverage_version
                item["ts"] = int(time.time())

                counts["items_built"] += 1
                doc_items.append(item)
                if not held_for_incomplete_fulltext:
                    # An item held because the body was never retrieved carries no
                    # coverage judgment and no body for the annotator to judge
                    # against, so it is recorded durably but NOT queued for blind
                    # annotation. It is not lost -- it is in the items file, with
                    # its incomplete_reasons -- it is simply not answerable yet.
                    doc_queue.append(item)

            # CO-CITATION POST-PASS. Every reference in this document now has its
            # own coverage verdicts, so the groups the parser resolved can finally
            # be aggregated: a claim a sibling established is not this member's
            # coverage gap. Runs here, on the already-buffered document, because a
            # group verdict needs every member judged and the document is already
            # the unit of durability. Routes are counted here, not inline, so a
            # counter can never record a route the group overturned.
            doc_groups, doc_route_counts, doc_stats = apply_cocitation_routing(
                doc_items)
            for route_name, n in doc_route_counts.items():
                # ``.get`` for the same reason ROUTE_NO_CLAIMS uses it: a
                # pre-seeded group counter would add zero-valued keys to the
                # manifest of every default run. A group route counter appears
                # exactly when that route actually fired.
                counts[route_name] = counts.get(route_name, 0) + n
            cocitation_groups += len(doc_groups)
            for key in ("groups", "cocitation_groups",
                        "members_in_cocitation_groups"):
                cocitation_stats[key] = cocitation_stats.get(key, 0) + doc_stats[key]
            for size, n in doc_stats["group_size_distribution"].items():
                group_sizes[size] = group_sizes.get(size, 0) + n
            for record in doc_groups:
                uncovered_claims_total += record["claims_uncovered"]
                covered_claims_total += record["claims_covered"]
                unknown_claims_total += record["claims_unknown"]

            # Doc is through: publish its rows, THEN checkpoint it. An interrupt
            # anywhere above leaves nothing durable for the resume to duplicate.
            for row in doc_items:
                _append_jsonl(items_fh, row)
            for row in doc_groups:
                _append_jsonl(groups_fh, row)
            for row in doc_queue:
                # Built AFTER the post-pass so the payload is derived from the
                # final item. annotation_payload is blind to the route either way,
                # so the annotator's view is unchanged -- the ordering is for the
                # reader of this code, not a behaviour change.
                _append_jsonl(queue_fh, annotation_payload(row))
            _append_jsonl(ckpt_fh, {"pmcid": pmcid})
            done.add(pmcid)
    finally:
        items_fh.close()
        queue_fh.close()
        groups_fh.close()
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
        "coverage_prompt_version": coverage_version,
        "label_space": list(LABEL_SPACE_F3),
        "params": {
            "xml_dir": xml_dir,
            "out_dir": out_dir,
            "max_docs": max_docs,
            "email": email,
            "api_key_present": bool(api_key),
            "fetch_reflist_wired": fetch_reflist is not None,
            **({"fetch_fulltext_wired": True,
                "band_mode": BAND_MODE_FULLTEXT,
                "evidence_scope": "fulltext_sections"} if fulltext_path else {}),
            # Adapter identity, verbatim. Absent when not supplied -- see the
            # MODEL IDENTITY paragraph above: absent is a truthful record, a
            # default would be a fabricated one.
            **({"model": model} if model else {}),
            **({"assistant_prefill": assistant_prefill}
               if assistant_prefill else {}),
            **({"stop_sequences": list(stop_sequences)}
               if stop_sequences else {}),
            # `is not None`, NOT truthiness: DEC-046 pins temperature=0, and 0 is
            # falsy, so a truthiness test would drop the one value that matters.
            **({"temperature": temperature} if temperature is not None else {}),
        },
        "counts": counts,
        "coverage_distribution": {
            COVERAGE_ESTABLISHED: counts[COVERAGE_ESTABLISHED],
            COVERAGE_CONTRADICTED: counts[COVERAGE_CONTRADICTED],
            COVERAGE_UNCONFIRMED_SPECIFIC: counts[COVERAGE_UNCONFIRMED_SPECIFIC],
            COVERAGE_OFF_TOPIC: counts[COVERAGE_OFF_TOPIC],
            "note": (
                "Per atomic claim, FULL-TEXT scoped (coverage_v3, DEC-030/032). "
                "coverage_contradicted routes F6_FLAGGED. Silence against a "
                "COMPLETE retrieval is absence and also routes F6_FLAGGED; "
                "against an incomplete one it holds. References whose body was "
                "not retrieved are excluded here and counted under "
                "counts.no_usable_fulltext."
            ) if fulltext_path else (
                "Per atomic claim, abstract-scoped. coverage_contradicted is the "
                "ONLY abstract-scoped evidence of a fault (routes F6_FLAGGED); "
                "coverage_unconfirmed_specific and coverage_off_topic are the "
                "full-text escalation queue (route HELD_LOW_CONFIDENCE). "
                "coverage_established routes FULL_COVERAGE. The no-usable-abstract "
                "path is excluded here and counted under counts.no_usable_abstract."
            ),
        },
        # Evidence-selection measurement (DEC-047 item 5). Full-text path ONLY: an
        # unconditional key would change the manifest bytes of every default run and
        # break the opt-in guarantee. Spans are RECORDED AND REPORTED and do not gate
        # any verdict, so everything here is a measurement, never a filter.
        **({"evidence_selection": {
            # A stored sentence id only means something relative to the segmenter
            # that cut it, so re-resolving a run's spans later needs this.
            "segmenter": segmenter_provenance(),
            "engaged_claims": engaged_claims,
            "engaged_claims_with_span": engaged_claims_with_span,
            "span_count_distribution": dict(span_count_tally),
            "span_status": dict(span_status_tally),
            "span_source": dict(span_source_tally),
            "note": (
                "Per ENGAGED atomic claim (engages_subject true) -- the claims where "
                "evidence selection was actually attempted. Off-topic claims and "
                "deterministic holds are excluded: neither entered the task, and "
                "counting them would pad the recall denominator. span_status is "
                "selected / aligned (evidence recorded) or not_found / unaligned (a "
                "recall MISS, counted in counts.evidence_span_not_found). "
                "span_source is per SPAN, not per claim: 'selected' means the judge "
                "named sentence ids, 'aligned' means it quoted prose that was matched "
                "post hoc at word-level Jaccard >= 0.7. NONE of this gates a verdict "
                "(DEC-047): incompleteness is measured as recall, not punished as "
                "error. A verbatim span is a SURFACE property and is never evidence "
                "that a verdict is correct."
            ),
        }} if fulltext_path else {}),
        # CO-CITATION. A sentence citing eight references cites them
        # COLLECTIVELY; judging each alone against the whole sentence made F6
        # fire by construction on every member. Grouping fixes the judgment and
        # in doing so CHANGES THE UNIT OF ANALYSIS, which is a reporting
        # consequence, not an implementation detail.
        "cocitation": {
            "groups_path": groups_path,
            # BOTH candidate denominators, deliberately unreconciled. A rate can
            # be per CITATION (one row per reference, the historical unit, still
            # every counter above) or per CITATION-GROUP (one row per sentence
            # occurrence, the unit a collectively-cited claim is actually made
            # in). They differ, they are both defensible, and choosing between
            # them is a reporting decision for ZD -- so both are published and
            # neither is silently adopted.
            "denominator_per_citation": counts["items_built"],
            "denominator_per_citation_group": cocitation_stats.get("groups", 0),
            "cocitation_groups": cocitation_stats.get("cocitation_groups", 0),
            "members_in_cocitation_groups": cocitation_stats.get(
                "members_in_cocitation_groups", 0),
            "group_size_distribution": dict(sorted(
                group_sizes.items(), key=lambda kv: int(kv[0]))),
            # Claim-level accounting over the co-citation groups only. An
            # uncovered claim is a REAL DEFECT that belongs to the group rather
            # than to an arbitrary member, so it is counted here and listed per
            # group in groups_path -- never dropped because "it's a group".
            "group_claims_covered": covered_claims_total,
            "group_claims_uncovered": uncovered_claims_total,
            "group_claims_unknown": unknown_claims_total,
            "routes": {
                ROUTE_GROUP_COVERED: counts.get(ROUTE_GROUP_COVERED, 0),
                ROUTE_GROUP_COVERAGE_GAP: counts.get(ROUTE_GROUP_COVERAGE_GAP, 0),
                ROUTE_UNSUPPORTED_MEMBER: counts.get(ROUTE_UNSUPPORTED_MEMBER, 0),
            },
            "note": (
                "A group is one SENTENCE OCCURRENCE and its members are the "
                "references that occurrence gave its citance to (first-citance-"
                "wins, so a reference already carrying an earlier sentence "
                "belongs to that sentence's group instead). GROUP_COVERED means "
                "the group covers every claim and this member contributed; "
                "GROUP_COVERAGE_GAP means at least one claim NO member covered; "
                "UNSUPPORTED_MEMBER means this member engaged nothing at all and "
                "is a fault, never a clear. Contradiction stays per reference and "
                "still routes F6_FLAGGED -- a sibling covering a claim says "
                "nothing about this paper's counter-evidence."
            ),
        },
        "distinct_cited_pmids_looked_up": len(pubtype_cache),
        "items_path": items_path,
        "annotation_queue_path": queue_path,
        "cocitation_groups_path": groups_path,
        "checkpoint_path": checkpoint_path,
        "manifest_path": manifest_path,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return manifest
