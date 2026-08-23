"""cre/f1/f5_seams.py -- the six injected callables F5 needs to run.

``f5_supersession`` is a finished detector that supplies NO implementation for the
six callables it invokes; only test fakes have ever satisfied them, so
``decide_f5`` has never run on real data. This module is those implementations.
They live OUTSIDE the detector on purpose: the detector type-checks every return
and fails closed, and that boundary is what lets the seams be swapped or stubbed
without touching validated routing.

The six are deliberately UNEQUAL in depth, because their evidence bases are:

  check_formal_notice            mature machine-readable infrastructure. Cheap.
  classify_evidence_tier         deterministic mapping, NO model call.
  fetch_comparability_source     abstract-first, escalate only when inconclusive.
  retrieve_superseding_candidates the expensive one; structural filter first.
  find_supersession_attestation  a DECLARED STUB. Path A is unreachable.
  judge_contradiction            renders ids, resolves ids, emits the contract.

WHAT THIS IS FOR. F5 runs here as a DISCOVERY INSTRUMENT -- high-recall candidate
generation feeding a human annotation queue -- not as a scored detector. No
claim-level supersession ground truth exists anywhere in the literature (the
largest hand-built sets are 396, 146 and 49 items, each with two independent
reviewers plus adjudication), so there is nothing to score against. Nothing here
ships autonomously: ``deploy_path_a`` stays hard-gated off and ``reportable``
stays False.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import threading
from typing import Optional

from . import f5_contradiction_prompt as fcp
from .f5_supersession import (
    Attestation, CandidateWork, ComparabilitySource, EvidenceTier,
    RetrievalResult,
    # The ONE date parser, borrowed rather than reimplemented: a second
    # implementation is a second thing that can disagree about what a date is.
    _parse_contradiction, _parse_date, _reject_duplicate_keys,
)
from .f5_evidence_store import (
    FACT_ASSESSMENT_NOT_PERFORMED,
    F5EvidenceStore,
    adapt_fulltext_sections,
)
from .f5_notice import make_notice_resolver


def _notice_from_pubtypes(pubtypes):
    """Legacy classifier retained for callers that inspect publication types.

    The cutoff-aware resolver additionally uses linked PubMed relationships to
    distinguish a correction/EoC notice article from the affected subject.
    """
    lowered = {str(value).strip().casefold() for value in (pubtypes or ())}
    if "retracted publication" in lowered:
        return "retraction", "retracted_article"
    if lowered & {"retraction of publication", "retraction notice"}:
        return "none", "retraction_notice"
    if "expression of concern" in lowered:
        return "eoc", "eoc_notice"
    if lowered & {"published erratum", "erratum"}:
        return "correction", "correction_notice"
    return "none", "no_notice_type"

# --------------------------------------------------------------------------
# Retrieval protocol -- recorded, not implied.
# --------------------------------------------------------------------------
#: How deep to retrieve. Set DELIBERATELY and recorded in the manifest, because a
#: silent cap reads as "we looked at everything". Shallow retrieval is the most
#: expensive mistake available here: BM25 on SciFact-Open goes Recall@1 20.22% ->
#: Recall@50 66.09%, and Sarol goes Recall@1 0.09 -> Recall@20 0.55.
CANDIDATE_CAP = 50

#: v1 has NO learned reranker. This is a stated limitation, not an oversight: BM25
#: beats every pure dense retriever on SciFact in BEIR (nDCG@10 0.665 vs DPR
#: 0.318), and monoT5 reranking is the known next gain (Recall@3 30.87% -> 48.26%)
#: but does not fit the deadline.
RERANKER = "none"

RETRIEVAL_PROTOCOL_VERSION = "f5_retrieval_v3"


class _JudgmentCacheControl:
    """Synchronization shared by every wrapper using one cache dictionary."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.inflight: dict[str, threading.Event] = {}


_JUDGMENT_CACHE_CONTROL_KEY = object()
_JUDGMENT_CACHE_INIT_LOCK = threading.Lock()


def _canonical_json(value) -> str:
    """A total, stable ordering key for a log row that has no natural id."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(value)


#: How each F5 audit log is ordered before it is written, hashed or reported.
#:
#: WHY SORT INSTEAD OF LOCKING THE APPEND. ``list.append`` under threads never
#: corrupts the list, but ARRIVAL order stops being reference order the moment
#: Phase 2 runs parallel, and a nondeterministic audit-log order must not reach a
#: manifest hash or a persisted artifact. Sorting on a field the row already
#: carries makes the order a function of the CONTENT, so serial and parallel runs
#: write the same bytes without pretending the appends happened in order.
_AUDIT_LOG_SORT_KEYS = {
    "source_packet_log": lambda row: (
        str((row or {}).get("work_id") or ""),
        str((row or {}).get("packet_sha256") or "")),
    "thin_source_log": lambda row: str(row or ""),
    "span_miss_log": lambda row: (
        str((row or {}).get("key") or ""), _canonical_json((row or {}).get("entry"))),
    # The retrieval protocol carries no per-call id -- every field in it is part
    # of what was executed -- so its whole canonical form is the key.
    "protocol_log": _canonical_json,
}


def sort_audit_log(name: str, rows) -> None:
    """Sort one F5 audit log IN PLACE into its deterministic order."""
    key = _AUDIT_LOG_SORT_KEYS.get(name)
    if key is None or not isinstance(rows, list):
        return
    rows.sort(key=key)


def retrieval_protocol(*, after_date: str = "", as_of_date: str = "",
                       mesh_terms=(), candidate_cap: int = CANDIDATE_CAP) -> dict:
    """The protocol in READABLE form, for the manifest.

    ``retrieval_query_hash`` on the record is a hash, and a hash is not a protocol
    -- nobody can audit what was searched from it. This is what actually gets
    recorded so a reader can tell what was and was not looked at."""
    return {
        "protocol_version": RETRIEVAL_PROTOCOL_VERSION,
        # These are PLANNED sources. Per-call attempted/succeeded/skipped
        # sources are populated from CandidateSearchResult.streams below. A
        # static protocol must never claim that an unavailable input was queried.
        "planned_sources": [
            "pubmed_esearch_claim", "pubmed_esearch_mesh",
            "pubmed_pubmed_citedin",
        ],
        "date_window": {"after": after_date, "as_of": as_of_date},
        "mesh_terms": list(mesh_terms),
        "candidate_cap": candidate_cap,
        "reranker": RERANKER,
        "candidate_generation": (
            "union of claim Title/Abstract search, cited-work MeSH search, and "
            "the PubMed forward-citation neighbourhood"
        ),
        "structural_filters": [
            "publication date strictly after after_date",
            "publication date on or before as_of_date",
        ],
        "adequacy_requires": [
            "all planned streams answered",
            "all retained candidate metadata answered",
            "no uncertain date-boundary exclusions",
            "candidate retrieval was not truncated",
        ],
        "known_limitations": [
            "no learned reranker in v1 (monoT5 is the identified next gain)",
            "absence of a candidate is NOT evidence that no superseding work exists",
        ],
    }


# --------------------------------------------------------------------------
# 3b. classify_evidence_tier -- deterministic, no model call.
# --------------------------------------------------------------------------
#: PubMed publication types -> OCEBM tier. Ordered most-specific first: a record
#: carrying both "Meta-Analysis" and "Journal Article" is a meta-analysis.
_PUBTYPE_TIER = (
    ("meta-analysis", EvidenceTier.SYSTEMATIC_REVIEW_OR_META_ANALYSIS),
    ("systematic review", EvidenceTier.SYSTEMATIC_REVIEW_OR_META_ANALYSIS),
    ("randomized controlled trial", EvidenceTier.RCT),
    ("controlled clinical trial", EvidenceTier.RCT),
    ("clinical trial, phase iii", EvidenceTier.RCT),
    ("clinical trial, phase ii", EvidenceTier.RCT),
    ("clinical trial", EvidenceTier.RCT),
    ("observational study", EvidenceTier.PROSPECTIVE_COHORT),
    ("comparative study", EvidenceTier.RETROSPECTIVE_COHORT),
    ("case reports", EvidenceTier.CASE_SERIES_OR_REPORT),
    ("preprint", EvidenceTier.PREPRINT_UNREVIEWED),
)

#: MeSH publication-characteristic terms, consulted only when publication type
#: does not decide it.
_MESH_TIER = (
    ("cohort studies", EvidenceTier.PROSPECTIVE_COHORT),
    ("prospective studies", EvidenceTier.PROSPECTIVE_COHORT),
    ("retrospective studies", EvidenceTier.RETROSPECTIVE_COHORT),
    ("case-control studies", EvidenceTier.CASE_CONTROL),
    ("cross-sectional studies", EvidenceTier.CROSS_SECTIONAL),
)

#: The conservative FLOOR for anything unrecognised. ``_tier_from`` raises on an
#: unknown string, so this mapping must be TOTAL over what PubMed actually emits;
#: an unrecognised record therefore lands at the bottom of the ladder rather than
#: stopping the run.
#:
#: CAVEAT, stated because the enum value is a claim: this floor does NOT assert the
#: work is a preprint. Tier is used to decide whether a candidate has the standing
#: to supersede, so the fail-closed default is the value that can never outrank
#: anything. A record landing here is unclassified, not unreviewed, and that
#: distinction is lost in the stored value -- which is why
#: ``classify_evidence_tier_explained`` returns the basis alongside the tier.
UNCLASSIFIED_TIER = EvidenceTier.PREPRINT_UNREVIEWED


def classify_evidence_tier_explained(meta: dict) -> "tuple[EvidenceTier, str]":
    """``(tier, basis)`` -- the basis says WHICH signal decided, or 'unclassified'."""
    meta = meta or {}
    # CandidateWork carries the result of this same deterministic classifier
    # because the production finder fetched publication types before the
    # temporal assessor receives the smaller CandidateWork object.  Honour only
    # an exact enum value; an invented hint falls through to the source metadata
    # rules rather than becoming authority by assertion.
    hint = meta.get("tier_hint")
    if isinstance(hint, str):
        try:
            return EvidenceTier(hint), f"tier_hint:{hint}"
        except ValueError:
            pass
    pubtypes = [str(p).strip().lower() for p in (meta.get("publication_types") or [])]
    for needle, tier in _PUBTYPE_TIER:
        if any(needle == p for p in pubtypes):
            return tier, f"publication_type:{needle}"
    for needle, tier in _PUBTYPE_TIER:
        if any(needle in p for p in pubtypes):
            return tier, f"publication_type_contains:{needle}"
    mesh = [str(m).strip().lower() for m in (meta.get("mesh_terms") or [])]
    for needle, tier in _MESH_TIER:
        if any(needle in m for m in mesh):
            return tier, f"mesh:{needle}"
    return UNCLASSIFIED_TIER, "unclassified"


def classify_evidence_tier(meta: dict) -> EvidenceTier:
    """Deterministic PubMed-metadata -> OCEBM tier. NO model call, TOTAL."""
    return classify_evidence_tier_explained(meta)[0]


# --------------------------------------------------------------------------
# 3a. check_formal_notice -- the F5/F8 boundary.
# --------------------------------------------------------------------------
def make_check_formal_notice(fetch_meta):
    """Build the cutoff-aware linked-notice resolver over injected metadata."""
    return make_notice_resolver(fetch_meta)


# --------------------------------------------------------------------------
# 3c. fetch_comparability_source -- abstract first, escalate only if needed.
# --------------------------------------------------------------------------
def make_fetch_comparability_source(
        fetch_abstract, fetch_fulltext=None, *, fetch_meta=None,
        assess_missing_facts=None, fact_assessor_version="none",
        fetch_notice=None,
        source_packet_log=None, thin_source_log=None, retrieved_at=None):
    """``fetch_comparability_source(work_id, *, as_of_date)``.

    ABSTRACT FIRST. Rosemblat measured that for the species axis the disambiguating
    fact was in the evidence sentence in 6 of 24 cases but required the FULL
    ABSTRACT in 17 of 24 -- so the abstract is the floor, not the sentence.
    Full text is fetched only when an injected, versioned fact assessor names
    required facts absent from the abstract. Character count is never used.

    A THIN SOURCE MUST BE VISIBLE. ``_source_text`` is the only thing span
    verification checks against, so an empty source silently turns every candidate
    into UNASSESSABLE. Anything thin is appended to ``thin_source_log`` so the run
    can report it instead of reporting a quiet zero."""
    clock = retrieved_at or (
        lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    packet_log = source_packet_log if source_packet_log is not None else []
    # The dedup scan below is a read-modify-write over a list that Phase 2
    # workers share, and the thin-source append lands on another. Neither may
    # interleave with itself: two workers scanning the same absent hash would
    # both append the same packet row.
    log_lock = threading.Lock()
    store = None
    if fetch_meta is not None:
        store = F5EvidenceStore(
            fetch_metadata=fetch_meta, fetch_abstract=fetch_abstract,
            fetch_fulltext=fetch_fulltext,
            classify_evidence_tier=classify_evidence_tier,
            assess_missing_facts=assess_missing_facts,
            fact_assessor_version=fact_assessor_version,
            fetch_notice=fetch_notice,
            retrieved_at=clock,
        )

    def fetch_comparability_source(work_id: str, *, as_of_date: str) -> ComparabilitySource:
        if store is not None:
            packet = store.get(work_id, as_of_date=as_of_date)
            with log_lock:
                if not any(
                        row.get("packet_sha256") == packet.packet_sha256
                        for row in packet_log):
                    packet_log.append(packet.to_dict())
            source = ComparabilitySource(
                abstract=packet.abstract, methods=packet.methods,
                results=packet.results, other_sections=packet.other_sections,
                protocol=packet.protocol,
                registry_record=packet.registry_record,
                publication_type="; ".join(packet.publication_types) or None,
                work_id=packet.work_id, source_status=packet.source_status,
                missing_facts=packet.missing_facts,
                packet_sha256=packet.packet_sha256,
            )
        else:
            # Offline/backward-compatible seam construction without metadata can
            # still use fact-based escalation, but cannot claim a source packet.
            abstract = (fetch_abstract(work_id) or "").strip()
            methods = results = other_sections = None
            adapted = None
            missing = (FACT_ASSESSMENT_NOT_PERFORMED,)
            if assess_missing_facts is not None:
                missing = tuple(assess_missing_facts(
                    work_id=work_id, abstract=abstract or None,
                    methods=None, results=None, other_sections=None))
            if missing and fetch_fulltext is not None:
                adapted = adapt_fulltext_sections(
                    fetch_fulltext(work_id), work_id=work_id)
                methods, results = adapted.methods, adapted.results
                other_sections = adapted.other_sections
                if assess_missing_facts is not None:
                    missing = tuple(assess_missing_facts(
                        work_id=work_id, abstract=abstract or None,
                        methods=methods, results=results,
                        other_sections=other_sections))
            has_text = bool(abstract or methods or results or other_sections)
            source_status = (
                "failure" if not has_text else
                "partial" if (
                    missing
                    or (adapted is not None and adapted.source_status != "complete")
                ) else "complete"
            )
            source = ComparabilitySource(
                abstract=abstract or None, methods=methods, results=results,
                other_sections=other_sections,
                work_id=work_id,
                source_status=source_status,
                missing_facts=missing,
            )
        if thin_source_log is not None and not (
                source.abstract or source.methods or source.results
                or source.other_sections):
            with log_lock:
                thin_source_log.append(work_id)
        return source
    fetch_comparability_source.evidence_store = store
    fetch_comparability_source.source_packet_log = packet_log
    return fetch_comparability_source


# --------------------------------------------------------------------------
# 3d. retrieve_superseding_candidates -- structural filter, then depth.
# --------------------------------------------------------------------------
def make_retrieve_superseding_candidates(search_candidates, *, cap: int = CANDIDATE_CAP,
                                         protocol_log=None):
    """``retrieve_superseding_candidates(cited_meta, claim, *, after_date, as_of_date)``.

    STRUCTURE BEFORE SEMANTICS. RobotReviewer LIVE went from 23% to 55% precision at
    unchanged 100% recall on structural narrowing alone, so the date window, MeSH
    overlap and the citation neighbourhood are applied first and the semantic step
    only ranks what survives.

    ADEQUACY AND STATUS ARE HONEST, and they have to be: they gate
    confident-negative-versus-hold inside the detector. A transport failure returns
    ``status="failure"``, NEVER ``adequacy="empty"`` -- that exact confusion, an
    outage wearing the same reason string as a real absence, cost calibration run 1
    its entire yield. ``__post_init__`` already enforces empty <=> adequacy=empty,
    so a failure with no candidates is reported as empty+failure and the detector
    holds rather than concluding."""
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError("candidate cap must be a positive integer")
    executed_protocols = protocol_log if protocol_log is not None else []

    def retrieve_superseding_candidates(cited_meta: dict, claim: str, *,
                                        after_date: str, as_of_date: str) -> RetrievalResult:
        mesh = list((cited_meta or {}).get("mesh_terms") or [])
        protocol = retrieval_protocol(
            after_date=after_date, as_of_date=as_of_date, mesh_terms=mesh,
            candidate_cap=cap)
        executed_protocols.append(protocol)
        try:
            hits = search_candidates(cited_meta, claim, after_date=after_date,
                                     as_of_date=as_of_date, cap=cap)
        except Exception as exc:                      # transport / parse failure
            return RetrievalResult(
                (), "empty", "failure",
                rationale=f"retrieval failed, no search was completed: {exc!r}")
        if hits is None:
            return RetrievalResult((), "empty", "failure",
                                   rationale="retrieval returned nothing at all")

        # The production finder returns an auditable CandidateSearchResult.
        # Keep accepting a plain iterable for offline fixtures and third-party
        # adapters, but do not erase partial/truncated production retrieval into
        # an apparently complete search.
        search_status = getattr(hits, "status", "ok")
        query_hash = str(getattr(hits, "query_hash", "") or "")
        truncated = bool(getattr(hits, "truncated", False))
        search_rationale = str(getattr(hits, "rationale", "") or "")
        streams = getattr(hits, "streams", None)
        rows = getattr(hits, "hits", hits)
        if search_status not in {"ok", "partial", "failure"}:
            return RetrievalResult(
                (), "empty", "failure", query_hash=query_hash,
                rationale=f"retrieval returned invalid status {search_status!r}")
        if streams is not None:
            executed = list(streams)
            protocol["executed_streams"] = executed
            protocol["sources_attempted"] = [
                row.get("name") for row in executed
                if not str(row.get("status") or "").startswith("skipped")]
            protocol["sources_succeeded"] = [
                row.get("name") for row in executed if row.get("status") == "ok"]
            protocol["sources_skipped"] = [
                row.get("name") for row in executed
                if str(row.get("status") or "").startswith("skipped")]
        if query_hash:
            protocol["query_hash"] = query_hash
        protocol["truncated"] = truncated
        streams_complete = streams is None or all(
            isinstance(row, dict) and row.get("status") == "ok"
            for row in streams)
        # Defense in depth: production CandidateSearchResult already reports a
        # skipped stream as partial. The adapter independently enforces the same
        # rule so a malformed/older provider cannot turn an incomplete protocol
        # into a confident negative merely by claiming status="ok".
        effective_status = search_status
        if search_status == "ok" and not streams_complete:
            effective_status = "partial"

        seen, candidates = set(), []
        for hit in rows:
            if not isinstance(hit, dict):
                continue
            work_id = str(hit.get("id") or "").strip()
            pub_date = str(hit.get("pub_date") or "").strip()
            if not work_id or work_id in seen:
                continue          # duplicates are rejected by RetrievalResult too
            if after_date and pub_date and pub_date <= after_date:
                continue          # strictly after, per the structural filter
            seen.add(work_id)
            authors = tuple(str(x).strip() for x in (hit.get("authors") or ())
                            if str(x).strip())
            mesh_terms = tuple(str(x).strip() for x in
                               (hit.get("mesh") or hit.get("mesh_terms") or ())
                               if str(x).strip())
            tier_hint = hit.get("tier_hint")
            if tier_hint is None:
                tier_hint = classify_evidence_tier(hit).value
            candidates.append(CandidateWork(
                id=work_id,
                title=str(hit.get("title") or ""),
                abstract=str(hit.get("abstract") or ""),
                pub_date=pub_date,
                authors=authors,
                mesh=mesh_terms,
                tier_hint=str(tier_hint),
                registry_ids=tuple(str(x).strip() for x in
                                   (hit.get("registry_ids") or ()) if str(x).strip()),
                version_work_ids=tuple(str(x).strip() for x in
                                       (hit.get("version_work_ids") or ())
                                       if str(x).strip()),
                cohort_ids=tuple(str(x).strip() for x in
                                 (hit.get("cohort_ids") or ()) if str(x).strip()),
                dataset_ids=tuple(str(x).strip() for x in
                                  (hit.get("dataset_ids") or ()) if str(x).strip()),
                demonstrably_distinct_from=tuple(
                    str(x).strip() for x in
                    (hit.get("demonstrably_distinct_from") or ()) if str(x).strip()),
                doi=str(hit.get("doi") or "") or None,
            ))
            if len(candidates) >= cap:
                break

        if not candidates:
            return RetrievalResult(
                (), "empty", effective_status, query_hash=query_hash,
                rationale=(search_rationale or
                           "no admissible later evidence was found under this "
                           "protocol; this is NOT a finding that none exists"))
        adequacy = ("adequate" if effective_status == "ok" and not truncated
                    and len(candidates) < cap else "inadequate")
        return RetrievalResult(
            tuple(candidates), adequacy, effective_status, query_hash=query_hash,
            rationale=(search_rationale or
                       f"{len(candidates)} candidate(s) under "
                       f"{RETRIEVAL_PROTOCOL_VERSION}"
                       + (f"; CAPPED/incomplete at {cap}, more may exist"
                          if adequacy == "inadequate" else "")))
    retrieve_superseding_candidates.executed_protocols = executed_protocols
    retrieve_superseding_candidates.candidate_cap = cap
    return retrieve_superseding_candidates


# --------------------------------------------------------------------------
# 3e. find_supersession_attestation -- a DECLARED STUB.
# --------------------------------------------------------------------------
ATTESTATION_LOOKUP_PERFORMED = False

ATTESTATION_STUB_REASON = (
    "v1 performs NO attestation lookup. deploy_path_a is hard-gated off, so Path A "
    "is unreachable by construction and this seam could only ever set an audit "
    "flag. path_a_eligible=False therefore means 'not looked for', NOT 'no "
    "attestation exists in the world'."
)


def find_supersession_attestation(cited_meta: dict, claim: str, candidate_id: str,
                                  *, as_of_date: str) -> Optional[Attestation]:
    """DECLARED STUB -- always returns None. See ATTESTATION_STUB_REASON.

    Named rather than a lambda so it is self-declaring at every call site and in
    any traceback, and so the manifest can record that the lookup was not
    performed."""
    return None


# --------------------------------------------------------------------------
# 3f. judge_contradiction -- renders ids, resolves ids, emits the contract.
# --------------------------------------------------------------------------
def make_judge_contradiction(
        complete, *, span_miss_log=None, judgment_cache=None,
        model_id: "str | None" = None, model_settings=None,
        counter_lock=None):
    """``judge_contradiction(cited_source, candidate_source, claim) -> str``.

    ``complete(prompt) -> str`` is the model call, injected so this is testable
    offline. The judge SELECTS sentence ids; this resolves them back to text before
    the detector's verbatim check ever runs, so a selected span passes that check
    by construction. An unresolvable span becomes an empty string, which the
    detector records as ``span_unverifiable`` -- a RECORDED MISS, which is the
    DEC-047 rule, and it is appended to ``span_miss_log`` so misses are counted
    rather than inferred."""
    cache = judgment_cache if judgment_cache is not None else {}
    if not isinstance(cache, dict):
        raise ValueError("judgment_cache must be a dict or None")
    # COUNTERS ARE NOT ATOMIC. ``fn.calls += 1`` on a function attribute is a
    # read-modify-write and drops increments under threads, and these counters
    # ARE the manifest's F5 call tallies (``_f5_manifest_block``): a lost
    # increment is a wrong published number, not a cosmetic one. The lock covers
    # the increments only -- never the model call, which is the whole point of
    # running Phase 2 parallel.
    counters = counter_lock if counter_lock is not None else threading.Lock()
    resolved_model_id = str(
        model_id or getattr(complete, "model_id", "") or "").strip()
    resolved_settings = (
        model_settings if model_settings is not None
        else getattr(complete, "model_settings", {}))

    # The cache may be shared by several wrappers in one run. Its lock and
    # in-flight map must therefore be shared too, or concurrent wrappers both
    # become owners and duplicate the paid request.
    with _JUDGMENT_CACHE_INIT_LOCK:
        control = cache.get(_JUDGMENT_CACHE_CONTROL_KEY)
        if control is None:
            control = _JudgmentCacheControl()
            cache[_JUDGMENT_CACHE_CONTROL_KEY] = control
        elif not isinstance(control, _JudgmentCacheControl):
            raise ValueError("judgment_cache contains invalid control state")

    def current_settings_json() -> str:
        try:
            return json.dumps(
                resolved_settings, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False)
        except (TypeError, ValueError):
            return ""

    def cache_key(cited_source, candidate_source, claim: str) -> str | None:
        settings_json = current_settings_json()
        if (not cited_source.packet_sha256 or not candidate_source.packet_sha256
                or not resolved_model_id or not settings_json):
            return None
        body = {
            "claim_sha256": hashlib.sha256(
                claim.encode("utf-8")).hexdigest(),
            "cited_packet_sha256": cited_source.packet_sha256,
            "candidate_packet_sha256": candidate_source.packet_sha256,
            "prompt_version": fcp.CONTRADICTION_PROMPT_VERSION,
            "parser_version": fcp.RESPONSE_PARSER_VERSION,
            "model_id": resolved_model_id,
            "settings": json.loads(settings_json),
        }
        return hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode("utf-8")).hexdigest()

    def judge_contradiction(cited_source, candidate_source, claim: str) -> str:
        with counters:
            judge_contradiction.calls += 1
        key = cache_key(cited_source, candidate_source, claim)
        owner = False
        if key is not None:
            while True:
                # NO NESTED LOCKS. The counter lock is taken AFTER this one is
                # released, never inside it: two locks held in one order here and
                # the other order anywhere else is the only way this code could
                # deadlock, and not nesting them means it cannot.
                with control.lock:
                    cached = cache.get(key)
                    event = None if isinstance(cached, str) else (
                        control.inflight.get(key))
                    if not isinstance(cached, str) and event is None:
                        event = threading.Event()
                        control.inflight[key] = event
                        owner = True
                if isinstance(cached, str):
                    with counters:
                        judge_contradiction.cache_hits += 1
                    return cached
                if owner:
                    break
                event.wait()
        prompt = fcp.render_prompt(cited_source, candidate_source, claim)
        try:
            with counters:
                judge_contradiction.model_calls += 1
            raw = complete(prompt)
            obj = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)

            cited_units = fcp.source_units(cited_source)
            cand_units = fcp.source_units(candidate_source)
            unresolved_span = False
            for span_key, units in (("cited_finding_span", cited_units),
                                    ("candidate_contradiction_span", cand_units)):
                entry = obj.get(span_key)
                if isinstance(entry, str):
                    # A judge that ignored the instruction and quoted prose: align it
                    # rather than discard it, same floor as the coverage judge.
                    entry = {"label": "abstract", "text": entry}
                text, span_source = fcp.resolve_span(entry, units)
                if (span_source == fcp.SPAN_SOURCE_UNRESOLVED
                        and span_miss_log is not None):
                    # Appended, not ordered: ``sort_audit_log`` puts the log in
                    # its deterministic order before anything reads it.
                    span_miss_log.append({"key": span_key, "entry": entry})
                if span_source == fcp.SPAN_SOURCE_UNRESOLVED:
                    unresolved_span = True
                obj[span_key] = text
            resolved = json.dumps(obj)
            # Only a fully valid resolved response is reusable. Exceptions and
            # malformed/partial outputs wake waiters but never become absence.
            _parse_contradiction(resolved)
            # A sentence-id miss is a recoverable resolution failure, not a
            # reusable judgment. Settings are re-read after the call so a
            # mutable settings object cannot cache an answer under stale facts.
            if (key is not None and not unresolved_span
                    and key == cache_key(cited_source, candidate_source, claim)):
                with control.lock:
                    cache[key] = resolved
            return resolved
        finally:
            if key is not None and owner:
                with control.lock:
                    event = control.inflight.pop(key, None)
                    if event is not None:
                        event.set()
    judge_contradiction.calls = 0
    judge_contradiction.model_calls = 0
    judge_contradiction.cache_hits = 0
    judge_contradiction.cache = cache
    judge_contradiction.model_id = resolved_model_id
    judge_contradiction.model_settings = resolved_settings
    judge_contradiction.counter_lock = counters
    # DECLARED, not assumed, and only as far as the transport will go: the cache
    # single-flight above and the locked counters are what make this wrapper safe,
    # so the remaining question is whether the injected transport is.
    judge_contradiction.thread_safe = (
        getattr(complete, "thread_safe", False) is True)
    # CARRIED, NOT COPIED. judgment_run reads its F5 counters off this seam by
    # attribute (``_judge_counter_source``), and the token ledger lives on the
    # transport this closure captured -- unreachable from the call site. Exposing
    # the same object here is what lets the manifest's declared
    # ``cost_counters.input_tokens`` / ``output_tokens`` / ``cost_usd`` slots be
    # filled from response.usage instead of holding "not_collected" forever.
    # None when the transport carries no ledger, which the manifest records as
    # "not_collected" rather than as zero.
    judge_contradiction.token_ledger = getattr(complete, "token_ledger", None)
    return judge_contradiction


def make_verify_contradiction(complete, *, model_id: "str | None" = None,
                             counter_lock=None):
    """Wrap the independent positive-only F5 verifier transport.

    The core renders and strictly parses the versioned verifier prompt because
    its source-bound evidence hashes are part of the replay contract.  This
    wrapper records only execution metadata and never repairs model output.
    """
    if not callable(complete):
        raise ValueError("F5 verifier complete must be callable")
    resolved_model_id = str(
        model_id or getattr(complete, "model_id", "") or "").strip()
    counters = counter_lock if counter_lock is not None else threading.Lock()

    def verify_contradiction(prompt: str) -> str:
        with counters:
            verify_contradiction.calls += 1
            verify_contradiction.model_calls += 1
        return complete(prompt)

    verify_contradiction.calls = 0
    verify_contradiction.model_calls = 0
    verify_contradiction.model_id = resolved_model_id
    verify_contradiction.counter_lock = counters
    verify_contradiction.thread_safe = (
        getattr(complete, "thread_safe", False) is True)
    return verify_contradiction


# --------------------------------------------------------------------------
# The bundle the runner passes to decide_f5.
# --------------------------------------------------------------------------
class F5SeamBundle(dict):
    """Typed F5 runtime bundle, with an EARNED Phase 2 parallel claim.

    ``thread_safe`` was a class-level False, which kept F5 Phase 2 serial
    whenever the bundle was wired at all -- 1,720 s of one measured run's busy
    time pinned to one thread. It is now an INSTANCE attribute, computed from
    what the bundle actually holds, so it can only be True when the two seams
    that reach a model both declare it. The bundle's own mutable state is what
    the rest of this module now guards: the judgment cache was already a
    single-flight under a shared lock, the counters are locked, and the audit
    logs are appended and then SORTED rather than assumed to arrive in order.

    ``sort_audit_logs`` is not optional housekeeping. A log whose order depends
    on thread timing must never reach a manifest hash or a persisted artifact,
    so the run calls this before it reads any of them.
    """

    def __init__(self, values, *, thread_safe: bool = False,
                 counter_lock=None, audit_logs=None):
        super().__init__(values)
        self.thread_safe = thread_safe is True
        self.counter_lock = counter_lock
        self.audit_logs = {
            name: rows for name, rows in (audit_logs or {}).items()
            if isinstance(rows, list)}

    def sort_audit_logs(self) -> None:
        """Put every audit log this bundle owns into its deterministic order."""
        for name, rows in self.audit_logs.items():
            sort_audit_log(name, rows)


def f5_seams_thread_safe(seams) -> bool:
    """True only when every model-reaching F5 seam declares thread safety.

    Mirrors ``F7SeamBundle``'s all-seams check. A missing verifier is not a
    pass: an unwired seam has made no claim, and Phase 2 does not get to assume
    one on its behalf. The candidate screen is checked only when it is WIRED --
    it is optional by contract, and an absent screen is not an unanswered
    question.
    """
    seams = seams or {}
    required = ["judge_contradiction", "verify_contradiction"]
    if seams.get("screen_candidates") is not None:
        required.append("screen_candidates")
    return all(
        getattr(seams.get(name), "thread_safe", False) is True
        for name in required)


def build_f5_seams(*, fetch_meta, fetch_abstract, search_candidates, complete,
                   verifier_complete=None,
                   fetch_fulltext=None, cap: int = CANDIDATE_CAP,
                   screen_candidates=None,
                   assess_missing_facts=None, fact_assessor_version="none",
                   source_packet_log=None, retrieved_at=None,
                   thin_source_log=None, span_miss_log=None,
                   protocol_log=None, judgment_cache=None,
                   judgment_model_id: "str | None" = None,
                   verifier_model_id: "str | None" = None,
                   judgment_model_settings=None) -> dict:
    """All F5 seams, ready for ``decide_f5(..., f5_seams=...)``.

    ``screen_candidates`` is optional.  With no screen the original path is
    preserved and every structurally admissible candidate reaches deep review.
    """
    if screen_candidates is not None and not callable(screen_candidates):
        raise ValueError("screen_candidates must be callable or None")
    if verifier_complete is complete and verifier_complete is not None:
        raise ValueError("F5 generator and verifier transports must be distinct")
    # ONE lock for every counter in this bundle. Shared rather than per-seam so a
    # reader of the manifest's F5 tallies is reading numbers guarded by the same
    # thing, and so the bundle can hand it out (``bundle.counter_lock``) to
    # anything that wraps these seams later.
    counter_lock = threading.Lock()
    notice_resolver = make_check_formal_notice(fetch_meta)

    def packet_notice(work_id: str, *, as_of_date: str) -> dict:
        status = notice_resolver(work_id, as_of_date=as_of_date)
        return {
            "notice_kind": status.notice_kind,
            "notice_resolution": status.notice_resolution,
            "date": status.date,
            "lookup_status": status.lookup_status,
            "date_status": status.date_status,
            "date_raw": status.date_raw,
            "source_role": status.source_role,
            "linked_notice_work_id": status.linked_notice_work_id,
            "relationship": status.relationship,
        }

    def observed_attestation(cited_meta: dict, claim: str, candidate_id: str,
                             *, as_of_date: str):
        with counter_lock:
            observed_attestation.calls += 1
        return find_supersession_attestation(
            cited_meta, claim, candidate_id, as_of_date=as_of_date)
    observed_attestation.calls = 0
    observed_attestation.thread_safe = True   # a declared stub, and now a locked one

    fetch_source = make_fetch_comparability_source(
        fetch_abstract, fetch_fulltext, fetch_meta=fetch_meta,
        assess_missing_facts=assess_missing_facts,
        fact_assessor_version=fact_assessor_version,
        fetch_notice=packet_notice,
        source_packet_log=source_packet_log,
        thin_source_log=thin_source_log, retrieved_at=retrieved_at)
    judge = make_judge_contradiction(
        complete, span_miss_log=span_miss_log,
        judgment_cache=judgment_cache, model_id=judgment_model_id,
        model_settings=judgment_model_settings, counter_lock=counter_lock)
    verifier = (
        make_verify_contradiction(
            verifier_complete, model_id=verifier_model_id,
            counter_lock=counter_lock)
        if verifier_complete is not None else None)
    seams = {
        "check_formal_notice": notice_resolver,
        "classify_evidence_tier": classify_evidence_tier,
        "fetch_comparability_source": fetch_source,
        "retrieve_superseding_candidates": make_retrieve_superseding_candidates(
            search_candidates, cap=cap, protocol_log=protocol_log),
        "find_supersession_attestation": observed_attestation,
        "judge_contradiction": judge,
        "verify_contradiction": verifier,
        "screen_candidates": screen_candidates,
    }
    return F5SeamBundle(
        seams,
        thread_safe=f5_seams_thread_safe(seams),
        counter_lock=counter_lock,
        audit_logs={
            # The log the fetch seam ACTUALLY writes: it substitutes its own
            # list when the caller passes none, and the substitute is the one
            # that has rows in it.
            "source_packet_log": fetch_source.source_packet_log,
            "thin_source_log": thin_source_log,
            "span_miss_log": span_miss_log,
            "protocol_log": protocol_log,
        })


def make_f5_evidence_builder(fetch_meta, *, as_of_date: str):
    """Build the ``item -> evidence`` half of the live F5 runner contract.

    The assessment cutoff is explicit and fixed for the entire run. PubMed's
    latest possible publication date is used for an imprecise cited date, so a
    candidate is never called "later" merely because both papers share an
    unresolved year/month boundary.
    """
    cutoff = _parse_date(as_of_date, "as_of_date")

    def build(item: dict) -> dict:
        if not isinstance(item, dict):
            raise ValueError("F5 evidence item must be a dict")
        cited_work_id = str(item.get("cited_pmid") or "").strip()
        if not cited_work_id or not cited_work_id.isdigit():
            raise ValueError("F5 evidence requires a decimal cited_pmid")
        meta = fetch_meta(cited_work_id)
        if not isinstance(meta, dict):
            raise ValueError(
                f"F5 cited metadata unavailable for PMID {cited_work_id}")
        metadata_id = str(meta.get("id") or meta.get("pmid") or cited_work_id).strip()
        if metadata_id != cited_work_id:
            raise ValueError(
                f"F5 cited metadata id {metadata_id!r} does not match "
                f"PMID {cited_work_id!r}")
        cited_date = str(meta.get("pub_date_latest") or meta.get("pub_date") or "").strip()
        if not cited_date:
            raise ValueError(
                f"F5 cited metadata has no publication date for PMID {cited_work_id}")
        cited_day = _parse_date(cited_date, "cited publication date")
        if cited_day >= cutoff:
            raise ValueError(
                "F5 cited publication date must be strictly before as_of_date")
        cited_meta = dict(meta)
        cited_meta["pmid"] = cited_work_id
        cited_meta["cited_work_id"] = cited_work_id
        evidence = {
            "cited_work_id": cited_work_id,
            "cited_meta": cited_meta,
            "cited_date": cited_date,
            "as_of_date": as_of_date,
        }
        # Activation ownership/section facts are optional and claim-indexed.  The
        # runner does not currently possess a trustworthy source-section field,
        # so it must never infer one from sentence text or fabricate a default.
        # A parser/fixture that does possess those facts supplies this explicitly;
        # absent means the activation gate returns ``uncertain`` and continues.
        claim_meta = item.get("f5_claim_meta")
        source_section = item.get("citing_source_section")
        if source_section is not None and (
                not isinstance(source_section, str) or not source_section.strip()):
            raise ValueError(
                "item['citing_source_section'] must be a nonblank string when supplied")
        if claim_meta is None and isinstance(source_section, str):
            claims = item.get("atomic_claims") or []
            if not isinstance(claims, list):
                raise ValueError("item['atomic_claims'] must be a list")
            claim_meta = {
                index: {"source_section": source_section.strip()}
                for index in range(len(claims))
            }
        if claim_meta is not None:
            if not isinstance(claim_meta, dict):
                raise ValueError("item['f5_claim_meta'] must be a dict when supplied")
            copied = {}
            for key, value in claim_meta.items():
                if not isinstance(value, dict):
                    raise ValueError(
                        "item['f5_claim_meta'] values must be dicts")
                copied[key] = dict(value)
                if isinstance(source_section, str):
                    copied[key].setdefault("source_section", source_section.strip())
            evidence["claim_meta"] = copied
        return evidence

    build.production_f5_evidence_builder = True
    build.builder_version = "f5_pubmed_evidence_v1"
    return build


def validate_production_f5_configuration(*, seams, evidence_builder,
                                         policy, run_model: str) -> None:
    """Reject incomplete or falsely attributed formal F5 wiring pre-output."""
    from .f5_supersession import F5Policy, validate_f5_policy

    if not isinstance(seams, F5SeamBundle):
        raise ValueError("production F5 seams must be an F5SeamBundle")
    expected = {
        "check_formal_notice", "classify_evidence_tier",
        "fetch_comparability_source", "retrieve_superseding_candidates",
        "find_supersession_attestation", "judge_contradiction",
        "verify_contradiction", "screen_candidates",
    }
    if set(seams) != expected:
        raise ValueError("production F5 seam bundle is incomplete")
    if not callable(evidence_builder) or getattr(
            evidence_builder, "production_f5_evidence_builder", False) is not True:
        raise ValueError("production F5 requires the PubMed evidence builder")
    if not isinstance(policy, F5Policy):
        raise ValueError("production F5 requires an explicit F5Policy")
    validate_f5_policy(policy)
    if policy.mode != "deployment" or policy.deploy_path_a is not False:
        raise ValueError(
            "production F5 requires deployment detection with Path A disabled")
    generator = seams.get("judge_contradiction")
    verifier = seams.get("verify_contradiction")
    if not callable(generator) or not callable(verifier):
        raise ValueError("production F5 requires generator and verifier callables")
    if generator is verifier:
        raise ValueError("production F5 generator and verifier must be distinct")
    if getattr(generator, "model_id", "") != policy.generator_model_id:
        raise ValueError("production F5 generator model does not match policy")
    if getattr(verifier, "model_id", "") != policy.verifier_model_id:
        raise ValueError("production F5 verifier model does not match policy")
    if policy.generator_model_id != str(run_model or "").strip():
        raise ValueError("production F5 generator model must equal the run model")


def build_pubmed_f5_runtime(*, complete, as_of_date: str, api_key: str = "",
                            verifier_complete=None,
                            email: "str | None" = None, session=None,
                            cache_dir: "str | None" = None,
                            timeout: float = 30.0, max_retries: int = 4,
                            fetch_fulltext=None, cap: int = CANDIDATE_CAP,
                            screen_candidates=None,
                            assess_missing_facts=None,
                            fact_assessor_version="none",
                            source_packet_log=None, retrieved_at=None,
                            thin_source_log=None, span_miss_log=None,
                            protocol_log=None, judgment_cache=None,
                            judgment_model_id: "str | None" = None,
                            verifier_model_id: "str | None" = None,
                            judgment_model_settings=None) -> dict:
    """Return both live F5 arguments accepted by ``run_natural_judgment``.

    This is the concrete production wiring for the PubMed finder. It performs no
    I/O until the returned seams/evidence builder are invoked, and callers can
    pass the result directly as ``**build_pubmed_f5_runtime(...)``. Path A
    remains disabled by ``F5Policy``; this only wires detection and Path B.
    """
    from .f5_candidate_finder import PubMedCandidateFinder
    from .ncbi_meta import DEFAULT_EMAIL

    finder = PubMedCandidateFinder(
        api_key=api_key, email=email or DEFAULT_EMAIL, session=session,
        cache_dir=cache_dir, timeout=timeout, max_retries=max_retries)
    memory: dict[str, "dict | None"] = {}
    # Per-PMID, not global: two workers looking up DIFFERENT works must not wait
    # on each other, and two looking up the SAME one must not both pay for it.
    memory_guard = threading.Lock()
    memory_locks: dict[str, threading.Lock] = {}

    def fetch_meta(work_id: str):
        key = str(work_id or "").strip()
        with memory_guard:
            lock = memory_locks.setdefault(key, threading.Lock())
        with lock:
            if key not in memory:
                memory[key] = finder.fetch_metadata(key)
            value = memory[key]
        return dict(value) if isinstance(value, dict) else None

    def fetch_abstract(work_id: str) -> str:
        meta = fetch_meta(work_id)
        return str((meta or {}).get("abstract") or "")

    seams = build_f5_seams(
        fetch_meta=fetch_meta, fetch_abstract=fetch_abstract,
        search_candidates=finder.search_candidates, complete=complete,
        verifier_complete=verifier_complete,
        fetch_fulltext=fetch_fulltext, cap=cap,
        screen_candidates=screen_candidates,
        assess_missing_facts=assess_missing_facts,
        fact_assessor_version=fact_assessor_version,
        source_packet_log=source_packet_log, retrieved_at=retrieved_at,
        thin_source_log=thin_source_log, span_miss_log=span_miss_log,
        protocol_log=protocol_log, judgment_cache=judgment_cache,
        judgment_model_id=judgment_model_id,
        verifier_model_id=verifier_model_id,
        judgment_model_settings=judgment_model_settings)
    return {
        "f5_seams": seams,
        "f5_evidence_builder": make_f5_evidence_builder(
            fetch_meta, as_of_date=as_of_date),
    }
