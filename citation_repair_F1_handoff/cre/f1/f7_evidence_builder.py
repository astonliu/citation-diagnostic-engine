"""Production evidence adapter for the existing F7 entity assessor.

The adapter consumes the exact mutable ``item`` that
``judgment_run.judge_pair_coverage`` has already populated.  In particular,
``item["atomic_claims"]`` is the final marker-scoped claim list and
``item["evidence"]["cited_fulltext"]`` is the full-text-reader result for this
specific cited PMID.  It performs no retrieval and no model call.

Only Results, Methods, table, and figure sections become F7 evidence.  Other
body sections are represented by hash-only ``ExcludedSection`` records.  A
missing/incomplete body or incomplete section inventory yields an empty
evidence context (and therefore ``UNJUDGEABLE`` in ``f7_entity``); contradictory
source identity or content hashes are provenance defects and raise.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .f7_entity import (
    ClaimClauseRef,
    EvidenceContext,
    ExcludedSection,
    SectionText,
)


BUILDER_VERSION = "f7_production_evidence_v1"
_F7_SECTION_LABELS = frozenset({"results", "methods", "table", "figure"})


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _nonblank(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"F7 evidence item {name} must be a nonblank string")
    return value.strip()


def _ordered_ids(values) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise ValueError("F7 reference id collection must be a list or tuple")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        rid = _nonblank(value, "reference id")
        if rid not in seen:
            seen.add(rid)
            out.append(rid)
    return tuple(out)


def _unresolved_work_id(cited_pmid: str) -> str:
    # EvidenceContext intentionally requires a nonblank work id even on a hold.
    # This sentinel is item-bound and cannot be confused with a resolved work.
    return f"UNRESOLVED:PMID:{cited_pmid}"


def _bundled_reference_ids(item: dict, target: str) -> tuple[str, ...]:
    raw = item.get("citance_group_members") or []
    bundled = _ordered_ids(raw)
    # The parser omits group metadata for the common singleton case.  Empty is
    # therefore exactly one reference, not an unknown reference set.
    return bundled if bundled else (target,)


def _claim_reference_ids(item: dict, bundled: tuple[str, ...]) -> tuple[str, ...]:
    """References attached to every final scoped atomic claim.

    A successful numeric marker-scope decision has already narrowed
    ``atomic_claims`` to this reference's cluster, so the cluster membership is
    the exact attribution set for each remaining claim.  On every non-scoped
    path the only honest set is the entire sentence co-citation group.
    """
    scope = item.get("marker_scope")
    # A multi-cluster sentence is narrowed only when the upstream marker-scope
    # decision actually succeeded.  On an ambiguity/refusal, atomic_claims is
    # still whole-sentence and using the target cluster would erase the very
    # ambiguity that prevented attribution.
    if isinstance(scope, dict) and scope.get("status") != "scoped":
        return bundled
    clusters = item.get("citance_marker_clusters") or []
    raw_index = item.get("citance_marker_cluster_index")
    if isinstance(raw_index, int) and 0 <= raw_index < len(clusters):
        own = clusters[raw_index]
        if not isinstance(own, dict):
            raise ValueError("F7 marker cluster must be an object")
        members = _ordered_ids(own.get("members") or [])
        if members:
            return members
    return bundled


def _claim_clause_refs(item: dict, reference_ids: tuple[str, ...]) -> tuple:
    claims = item.get("atomic_claims")
    if not isinstance(claims, list):
        raise ValueError(
            "F7 evidence builder requires final item.atomic_claims as a list")
    refs: list[ClaimClauseRef] = []
    for index, claim in enumerate(claims):
        text = _nonblank(claim, f"atomic_claims[{index}]")
        # The upstream extractor contract says these are atomic claims.  Their
        # complete text is therefore the clause whose marker attribution the
        # builder can establish without inventing a second decomposition.
        refs.append(ClaimClauseRef(index, text, reference_ids))
    return tuple(refs)


def _section_inventory_complete(fulltext: dict) -> bool:
    sections = fulltext.get("sections")
    present = fulltext.get("sections_present")
    if not isinstance(sections, list) or not isinstance(present, list):
        return False
    labels: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            return False
        label = section.get("label")
        if not isinstance(label, str) or not label.strip():
            return False
        labels.append(label)
    if any(not isinstance(label, str) or not label.strip() for label in present):
        return False
    return sorted(set(labels)) == sorted(set(present))


def _build_sections(fulltext: dict, work_id: str) -> tuple[tuple, tuple]:
    body: list[SectionText] = []
    excluded: list[ExcludedSection] = []
    for index, raw in enumerate(fulltext["sections"]):
        required = {"label", "text", "content_sha256"}
        if not required.issubset(raw):
            # This is an incomplete mapping, not evidence that the paper lacks
            # an entity.  The caller converts the entire map to an empty hold.
            raise LookupError(f"section {index} mapping is incomplete")
        label = raw["label"]
        text = raw["text"]
        digest = raw["content_sha256"]
        if not isinstance(text, str) or not text.strip():
            raise LookupError(f"section {index} text is missing")
        if not isinstance(digest, str) or digest != _sha256_text(text):
            raise ValueError(
                f"F7 section {index} content_sha256 does not bind its source text")
        if label in _F7_SECTION_LABELS:
            body.append(SectionText(label, text, work_id, digest))
        else:
            excluded.append(ExcludedSection(str(label), digest))
    return tuple(body), tuple(excluded)


@dataclass(frozen=True)
class ProductionF7EvidenceBuilder:
    """Stateless, thread-safe ``item -> EvidenceContext`` production adapter."""

    thread_safe = True
    version: str = BUILDER_VERSION

    def __call__(self, item: dict) -> EvidenceContext:
        if not isinstance(item, dict):
            raise ValueError("F7 evidence builder item must be a dict")
        target = _nonblank(item.get("citation_id"), "citation_id")
        cited_pmid = _nonblank(item.get("cited_pmid"), "cited_pmid")
        sentence = _nonblank(item.get("citing_sentence"), "citing_sentence")
        bundled = _bundled_reference_ids(item, target)
        clause_ids = _claim_reference_ids(item, bundled)
        clause_refs = _claim_clause_refs(item, clause_ids)

        evidence = item.get("evidence")
        fulltext = evidence.get("cited_fulltext") if isinstance(evidence, dict) else None
        if not isinstance(fulltext, dict):
            return EvidenceContext(
                False, _unresolved_work_id(cited_pmid), sentence, target,
                bundled, clause_refs, ())

        source_pmid = fulltext.get("pmid")
        if source_pmid != cited_pmid:
            raise ValueError(
                "F7 cited full text is bound to a different PMID than the run item")

        pmcid = fulltext.get("pmcid")
        resolved = fulltext.get("resolved") is True
        work_id = (pmcid.strip() if resolved and isinstance(pmcid, str)
                   and pmcid.strip() else _unresolved_work_id(cited_pmid))
        if not resolved or work_id.startswith("UNRESOLVED:"):
            return EvidenceContext(
                False, work_id, sentence, target, bundled, clause_refs, ())

        # F7 must never infer absence from a partial body.  Identity may be
        # resolved, but the evidence map is deliberately empty so the assessor
        # holds evidence_source_insufficient.
        if fulltext.get("retrieval_complete") is not True:
            return EvidenceContext(
                True, work_id, sentence, target, bundled, clause_refs, ())
        # The full-text reader defines these as an exact inverse invariant.
        # Contradictory metadata is not a complete body and cannot support an
        # argument from evidence or silence.
        if fulltext.get("incomplete_reasons") != []:
            return EvidenceContext(
                True, work_id, sentence, target, bundled, clause_refs, ())
        if not _section_inventory_complete(fulltext):
            return EvidenceContext(
                True, work_id, sentence, target, bundled, clause_refs, ())

        try:
            body, excluded = _build_sections(fulltext, work_id)
        except LookupError:
            return EvidenceContext(
                True, work_id, sentence, target, bundled, clause_refs, ())
        return EvidenceContext(
            True, work_id, sentence, target, bundled, clause_refs,
            body, excluded)


def make_production_f7_evidence_builder() -> ProductionF7EvidenceBuilder:
    return ProductionF7EvidenceBuilder()
