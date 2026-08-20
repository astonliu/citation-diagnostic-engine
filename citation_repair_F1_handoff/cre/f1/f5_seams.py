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

import dataclasses
import json
from typing import Optional

from . import f5_contradiction_prompt as fcp
from .f5_supersession import (
    Attestation, CandidateWork, ComparabilitySource, EvidenceTier, NoticeStatus,
    RetrievalResult,
    # The ONE date parser, borrowed rather than reimplemented: a second
    # implementation is a second thing that can disagree about what a date is.
    _parse_date,
)

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
# THE PT INVERSION. These two mean OPPOSITE things:
#   "Retracted Publication"     -- THIS article was retracted
#   "Retraction of Publication" / "Retraction Notice"
#                               -- this article IS the notice that retracts
#                                  some OTHER article
# ``_RETRACTION_TYPES`` used to carry both, so citing a retraction notice --
# legitimate, and routine in meta-research -- read as citing a retracted paper.
# Matching is EXACT rather than substring for the same reason: "retracted
# publication" is not a substring of "retraction of publication", but a looser
# pattern like "retract" matches both and inverts the detector while still
# looking like it works. ``ncbi_meta.RETRACTED_PUBTYPE`` is the F1/F8 layer's
# statement of the same rule; this is deliberately the same shape.
_RETRACTION_TYPES = ("retracted publication",)
_RETRACTION_NOTICE_TYPES = ("retraction of publication", "retraction notice")
_CORRECTION_TYPES = ("published erratum", "erratum")
_EOC_TYPES = ("expression of concern",)


def _notice_from_pubtypes(pubtypes) -> tuple:
    """Return ``(notice_kind, source_role)``.

    The role is what the old single return value threw away: a work carrying a
    retraction-NOTICE type is not a retracted work, so its kind is "none" -- and
    saying only "none" would make it indistinguishable from a paper with no
    notice types at all, which is how the inversion hid.
    """
    lowered = [str(p).strip().lower() for p in (pubtypes or [])]
    if any(p in _RETRACTION_TYPES for p in lowered):
        return "retraction", "retracted_article"
    # Checked BEFORE the softer categories and reported explicitly: this work is
    # the notice, not its subject, and F5 has no quarrel with it.
    if any(p in _RETRACTION_NOTICE_TYPES for p in lowered):
        return "none", "retraction_notice"
    if any(any(t in p for t in _EOC_TYPES) for p in lowered):
        return "eoc", "eoc_notice"
    if any(any(t in p for t in _CORRECTION_TYPES) for p in lowered):
        return "correction", "correction_notice"
    return "none", "no_notice_type"


def make_check_formal_notice(fetch_meta):
    """``check_formal_notice(work_id, *, as_of_date)`` over an injected metadata
    reader, so the network call is testable without one.

    ``as_of_date`` is LOAD-BEARING, not decorative: Bakker et al. document papers
    being retracted while reviews are in press, so notice status is a function of
    the date you check. A notice dated AFTER as_of_date is not applied -- at that
    moment it did not yet exist.

    DATES ARE PARSED, NEVER COMPARED AS STRINGS. The comparison used to be
    ``str(date) > str(as_of_date)``, which is lexicographic: "2024/01/15" sorts
    ABOVE "2024-06-01" because "/" (0x2F) is above "-" (0x2D), so a real January
    retraction read as CLEAR in June. The failure was also ASYMMETRIC -- a
    non-ISO date that happened to sort earlier fell through into
    ``NoticeStatus``, whose validator raises, so "15 Jan 2024" crashed the run
    while "2024/01/15" silently cleared it. This file already states the
    principle for the other direction ("a silently reinterpreted [date] is a
    correctness bug, not a formatting nicety"); it is now applied here.

    Every way the comparison can fail to happen is NAMED on the returned status
    (``lookup_status`` / ``date_status`` / ``date_raw`` / ``source_role``) rather
    than folded into a clear, and every one of them fails CLOSED: an
    uncomparable notice stays in force.
    """
    def check_formal_notice(work_id: str, *, as_of_date: str) -> NoticeStatus:
        raw_meta = fetch_meta(work_id)
        if not raw_meta:
            # A LOOKUP THAT DID NOT ANSWER IS NOT A CLEAN RECORD. ``or {}``
            # collapsed None (the reader failed) and {} (it answered, nothing to
            # report) into one value, and both returned resolved_clear -- an
            # outage wearing the same string as a verified absence, which is the
            # confusion this module guards against for retrieval and did not
            # guard against here. Unresolved, so it holds rather than clears.
            return NoticeStatus(
                notice_kind="none", notice_resolution="unresolved",
                lookup_status="no_record", source_role="unknown")
        kind, source_role = _notice_from_pubtypes(raw_meta.get("publication_types"))
        if kind == "none":
            return NoticeStatus(
                notice_kind="none", notice_resolution="resolved_clear",
                lookup_status="ok", source_role=source_role)

        resolution = "unresolved" if kind == "eoc" else "flagged"
        raw_date = raw_meta.get("notice_date") or None
        in_force = NoticeStatus(
            notice_kind=kind, notice_resolution=resolution,
            lookup_status="ok", source_role=source_role,
            date_raw=str(raw_date) if raw_date is not None else None,
            date_status="absent")
        if raw_date is None:
            # AN UNDATED NOTICE CANNOT BE TIMED. It stays in force -- the
            # fail-closed direction -- but the gate did NOT run, and date_status
            # says so instead of leaving "in force at every as_of_date" looking
            # like a comparison that was made. Worth knowing how common this is:
            # nothing in production populates notice_date today.
            return in_force
        try:
            notice_day = _parse_date(str(raw_date), "notice_date")
        except ValueError:
            # Malformed, symmetrically: it never clears and it never crashes.
            return dataclasses.replace(in_force, date_status="unparseable")
        try:
            as_of_day = _parse_date(str(as_of_date), "as_of_date")
        except ValueError:
            return dataclasses.replace(in_force, date_status="as_of_unavailable")
        if notice_day > as_of_day:
            # Not yet in force at the moment being assessed.
            return NoticeStatus(
                notice_kind="none", notice_resolution="resolved_clear",
                lookup_status="ok", source_role=source_role,
                date=str(raw_date), date_raw=str(raw_date),
                date_status="compared")
        return dataclasses.replace(
            in_force, date=str(raw_date), date_status="compared")
    return check_formal_notice


# --------------------------------------------------------------------------
# 3c. fetch_comparability_source -- abstract first, escalate only if needed.
# --------------------------------------------------------------------------
def make_fetch_comparability_source(fetch_abstract, fetch_fulltext=None,
                                    *, thin_source_log=None):
    """``fetch_comparability_source(work_id, *, as_of_date)``.

    ABSTRACT FIRST. Rosemblat measured that for the species axis the disambiguating
    fact was in the evidence sentence in 6 of 24 cases but required the FULL
    ABSTRACT in 17 of 24 -- so the abstract is the floor, not the sentence.
    DeepSciVerify's escalation resolved 67% of instances without full-text
    retrieval, so full text is fetched only when the abstract is thin.

    A THIN SOURCE MUST BE VISIBLE. ``_source_text`` is the only thing span
    verification checks against, so an empty source silently turns every candidate
    into UNASSESSABLE. Anything thin is appended to ``thin_source_log`` so the run
    can report it instead of reporting a quiet zero."""
    def fetch_comparability_source(work_id: str, *, as_of_date: str) -> ComparabilitySource:
        abstract = (fetch_abstract(work_id) or "").strip()
        methods = results = None
        if len(abstract) < 200 and fetch_fulltext is not None:
            full = fetch_fulltext(work_id) or {}
            methods = (full.get("methods") or None)
            results = (full.get("results") or None)
        source = ComparabilitySource(abstract=abstract or None, methods=methods,
                                     results=results)
        if thin_source_log is not None and not (abstract or methods or results):
            thin_source_log.append(work_id)
        return source
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
def make_judge_contradiction(complete, *, span_miss_log=None):
    """``judge_contradiction(cited_source, candidate_source, claim) -> str``.

    ``complete(prompt) -> str`` is the model call, injected so this is testable
    offline. The judge SELECTS sentence ids; this resolves them back to text before
    the detector's verbatim check ever runs, so a selected span passes that check
    by construction. An unresolvable span becomes an empty string, which the
    detector records as ``span_unverifiable`` -- a RECORDED MISS, which is the
    DEC-047 rule, and it is appended to ``span_miss_log`` so misses are counted
    rather than inferred."""
    def judge_contradiction(cited_source, candidate_source, claim: str) -> str:
        judge_contradiction.calls += 1
        prompt = fcp.render_prompt(cited_source, candidate_source, claim)
        raw = complete(prompt)
        obj = json.loads(raw)

        cited_units = fcp.source_units(cited_source)
        cand_units = fcp.source_units(candidate_source)
        for key, units in (("cited_finding_span", cited_units),
                           ("candidate_contradiction_span", cand_units)):
            entry = obj.get(key)
            if isinstance(entry, str):
                # A judge that ignored the instruction and quoted prose: align it
                # rather than discard it, same floor as the coverage judge.
                entry = {"label": "abstract", "text": entry}
            text, span_source = fcp.resolve_span(entry, units)
            if span_source == fcp.SPAN_SOURCE_UNRESOLVED and span_miss_log is not None:
                span_miss_log.append({"key": key, "entry": entry})
            obj[key] = text
        return json.dumps(obj)
    judge_contradiction.calls = 0
    return judge_contradiction


# --------------------------------------------------------------------------
# The bundle the runner passes to decide_f5.
# --------------------------------------------------------------------------
def build_f5_seams(*, fetch_meta, fetch_abstract, search_candidates, complete,
                   fetch_fulltext=None, cap: int = CANDIDATE_CAP,
                   thin_source_log=None, span_miss_log=None,
                   protocol_log=None) -> dict:
    """All six seams, ready for ``decide_f5(..., f5_seams=...)``."""
    def observed_attestation(cited_meta: dict, claim: str, candidate_id: str,
                             *, as_of_date: str):
        observed_attestation.calls += 1
        return find_supersession_attestation(
            cited_meta, claim, candidate_id, as_of_date=as_of_date)
    observed_attestation.calls = 0

    return {
        "check_formal_notice": make_check_formal_notice(fetch_meta),
        "classify_evidence_tier": classify_evidence_tier,
        "fetch_comparability_source": make_fetch_comparability_source(
            fetch_abstract, fetch_fulltext, thin_source_log=thin_source_log),
        "retrieve_superseding_candidates": make_retrieve_superseding_candidates(
            search_candidates, cap=cap, protocol_log=protocol_log),
        "find_supersession_attestation": observed_attestation,
        "judge_contradiction": make_judge_contradiction(
            complete, span_miss_log=span_miss_log),
    }


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

    return build


def build_pubmed_f5_runtime(*, complete, as_of_date: str, api_key: str = "",
                            email: "str | None" = None, session=None,
                            cache_dir: "str | None" = None,
                            timeout: float = 30.0, max_retries: int = 4,
                            fetch_fulltext=None, cap: int = CANDIDATE_CAP,
                            thin_source_log=None, span_miss_log=None,
                            protocol_log=None) -> dict:
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

    def fetch_meta(work_id: str):
        key = str(work_id or "").strip()
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
        fetch_fulltext=fetch_fulltext, cap=cap,
        thin_source_log=thin_source_log, span_miss_log=span_miss_log,
        protocol_log=protocol_log)
    return {
        "f5_seams": seams,
        "f5_evidence_builder": make_f5_evidence_builder(
            fetch_meta, as_of_date=as_of_date),
    }
