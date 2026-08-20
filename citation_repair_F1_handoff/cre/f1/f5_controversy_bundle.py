"""Deterministic receiver-facing controversy bundles for assessed F5 claims."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


CONTROVERSY_BUNDLE_SCHEMA_VERSION = "f5_controversy_bundle_v1"
FIELD_CONFIDENCE = {
    "status": "not_calibrated",
    "score": None,
    "reason": (
        "raw paper counts and uncalibrated tier weighting cannot establish "
        "field-level confidence"),
}
HUMAN_REVIEW_REASON = (
    "F5 remains non-reportable; review the full controversy evidence")

_CATEGORY_KEYS = (
    "qualifying_contradictions", "plausible_or_uncertain",
    "comparable_confirmations", "mixed_findings",
    "flagged_or_corrected", "same_study_context", "excluded_candidates",
)
_BUNDLE_KEYS = {
    "schema_version", "citation_id", "claim_index", "claim_text",
    "activation", "cited_work", "as_of_date", "search_receipt",
    *_CATEGORY_KEYS, "selected_contradiction_work_id",
    "selected_replacement_work_id", "evidence_profile", "field_confidence",
    "search_complete", "human_review_reason", "bundle_sha256",
}
_CITED_WORK_KEYS = {
    "work_id", "date", "tier", "notice_kind", "notice_resolution",
    "source_status", "source_packet_sha256", "study_cluster_id",
    "finding_spans",
}
_SEARCH_RECEIPT_KEYS = {"status", "adequacy", "query_hash"}
_EVIDENCE_PROFILE_KEYS = {
    "opposing_paper_count", "confirming_paper_count", "mixed_paper_count",
    "uncertain_paper_count", "independent_opposing_cluster_count",
    "independent_confirming_cluster_count", "opposing_evidence_tiers",
    "confirming_evidence_tiers", "sample_sizes",
    "systematic_review_present", "guideline_presence", "search_complete",
    "source_complete", "source_status_counts", "maximum_pairwise_confidence",
    "pairwise_confidence_distribution", "duplicate_or_overlap_warnings",
    "evidence_pattern",
}


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()


def controversy_bundle_sha256(bundle: Mapping[str, Any]) -> str:
    return _canonical_sha256({
        key: value for key, value in bundle.items() if key != "bundle_sha256"})


def validate_controversy_bundle(
        bundle: Mapping[str, Any], *, candidate_assessments=None,
        record: Mapping[str, Any] | None = None) -> None:
    """Strict receiver-boundary validation independent of bundle production."""
    if not isinstance(bundle, Mapping) or set(bundle) != _BUNDLE_KEYS:
        raise ValueError("controversy bundle has incomplete or unexpected fields")
    if bundle.get("schema_version") != CONTROVERSY_BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported controversy bundle schema_version")
    if (not isinstance(bundle.get("claim_index"), int)
            or isinstance(bundle.get("claim_index"), bool)
            or bundle["claim_index"] < 0
            or not isinstance(bundle.get("claim_text"), str)
            or not bundle["claim_text"].strip()):
        raise ValueError("controversy bundle requires a claim index and text")
    if not isinstance(bundle.get("activation"), Mapping):
        raise ValueError("controversy bundle activation must be a mapping")
    cited = bundle.get("cited_work")
    if (not isinstance(cited, Mapping) or set(cited) != _CITED_WORK_KEYS
            or not isinstance(cited.get("work_id"), str)
            or not cited["work_id"].strip()
            or not isinstance(cited.get("finding_spans"), list)):
        raise ValueError("controversy bundle cited_work is incomplete")
    receipt = bundle.get("search_receipt")
    if not isinstance(receipt, Mapping) or set(receipt) != _SEARCH_RECEIPT_KEYS:
        raise ValueError("controversy bundle search_receipt is incomplete")
    profile = bundle.get("evidence_profile")
    if (not isinstance(profile, Mapping)
            or set(profile) != _EVIDENCE_PROFILE_KEYS):
        raise ValueError("controversy bundle evidence_profile is incomplete")
    if bundle.get("field_confidence") != FIELD_CONFIDENCE:
        raise ValueError("controversy bundle field confidence drifted")
    if not isinstance(bundle.get("search_complete"), bool):
        raise ValueError("controversy bundle search_complete must be bool")
    if (not isinstance(bundle.get("human_review_reason"), str)
            or not bundle["human_review_reason"].strip()):
        raise ValueError("controversy bundle requires a human review reason")
    if bundle.get("bundle_sha256") != controversy_bundle_sha256(bundle):
        raise ValueError("controversy bundle hash drifted")

    categorized_rows = []
    for key in _CATEGORY_KEYS:
        rows = bundle.get(key)
        if (not isinstance(rows, list)
                or any(not isinstance(row, Mapping) for row in rows)):
            raise ValueError(f"controversy bundle {key} must be a list of rows")
        categorized_rows.extend(rows)
    if record is not None:
        if not isinstance(record, Mapping):
            raise ValueError("record must be a mapping")
        if candidate_assessments is None:
            candidate_assessments = record.get("candidate_assessments")
    if candidate_assessments is None:
        return
    if (not isinstance(candidate_assessments, list)
            or any(not isinstance(row, Mapping)
                   for row in candidate_assessments)):
        raise ValueError("candidate_assessments must be a list of mappings")
    expected = {}
    for row in candidate_assessments:
        work_id = row.get("candidate_work_id")
        if (not isinstance(work_id, str) or not work_id
                or work_id in expected):
            raise ValueError(
                "candidate_assessments require unique nonblank work IDs")
        expected[work_id] = dict(row)
    seen = set()
    for row in categorized_rows:
        work_id = row.get("candidate_work_id")
        if work_id not in expected or dict(row) != expected[work_id]:
            raise ValueError(
                "controversy bundle candidate row drifted from assessment")
        seen.add(work_id)
    if seen != set(expected):
        raise ValueError(
            "controversy bundle omits a retained candidate assessment")
    for key in ("selected_contradiction_work_id",
                "selected_replacement_work_id"):
        selected = bundle.get(key)
        if selected is not None and selected not in expected:
            raise ValueError(
                f"controversy bundle {key} is not a retained candidate")
    if record is not None:
        expected_categories = _categorize_candidates(candidate_assessments)
        if any(bundle[key] != expected_categories[key] for key in _CATEGORY_KEYS):
            raise ValueError(
                "controversy bundle category membership drifted from assessments")
        expected_profile, expected_search_complete = _derive_evidence_profile(
            record, candidate_assessments, expected_categories)
        if (bundle.get("evidence_profile") != expected_profile
                or bundle.get("search_complete") != expected_search_complete):
            raise ValueError(
                "controversy bundle evidence profile drifted from assessments")
        expected_receipt = {
            "status": record.get("retrieval_status"),
            "adequacy": record.get("retrieval_adequacy"),
            "query_hash": record.get("retrieval_query_hash"),
        }
        if bundle.get("search_receipt") != expected_receipt:
            raise ValueError(
                "controversy bundle search receipt drifted from record")
        expected_cited = {
            "work_id": record.get("cited_work_id"),
            "date": record.get("cited_date"),
            "tier": record.get("cited_tier"),
            "notice_kind": record.get("cited_notice_kind"),
            "notice_resolution": record.get("cited_notice_resolution"),
            "source_status": record.get("cited_source_status"),
            "source_packet_sha256": record.get("cited_source_packet_sha256"),
            "study_cluster_id": record.get("cited_study_cluster_id"),
            "finding_spans": _cited_finding_spans(
                record, candidate_assessments),
        }
        expected_top = {
            "citation_id": record.get("citation_id"),
            "claim_index": record.get("claim_index"),
            "claim_text": record.get("claim_text"),
            "activation": _copy_row(record.get("activation") or {}),
            "cited_work": expected_cited,
            "as_of_date": record.get("as_of_date"),
            "selected_contradiction_work_id": record.get(
                "selected_contradiction_work_id"),
            "selected_replacement_work_id": record.get(
                "selected_replacement_work_id"),
            "human_review_reason": HUMAN_REVIEW_REASON,
        }
        if any(bundle.get(key) != value for key, value in expected_top.items()):
            raise ValueError(
                "controversy bundle claim/cited binding drifted from record")


def _copy_row(row: Mapping[str, Any]) -> dict:
    # A JSON round trip prevents a mutable nested assessment from changing a
    # bundle after its hash has been computed.
    return json.loads(json.dumps(dict(row), ensure_ascii=False))


def _clear_notice(row: Mapping[str, Any]) -> bool:
    return (row.get("candidate_notice_kind") == "none"
            and row.get("candidate_notice_resolution") == "resolved_clear"
            and row.get("candidate_notice_lookup_status") == "ok")


def _is_qualifying(row: Mapping[str, Any]) -> bool:
    return (row.get("reason") == "qualifying_contradiction"
            and row.get("relation_to_cited_finding") == "opposes"
            and row.get("directional_contradiction") is True
            and row.get("comparability_decision") == "comparable"
            and row.get("independent") == "independent"
            and _clear_notice(row))


def _is_confirmation(row: Mapping[str, Any]) -> bool:
    return (row.get("reason") == "comparable_confirmation"
            and row.get("relation_to_cited_finding") == "confirms"
            and row.get("comparability_decision") == "comparable"
            and row.get("candidate_source_status") == "complete"
            and _clear_notice(row))


def _is_mixed(row: Mapping[str, Any]) -> bool:
    return row.get("relation_to_cited_finding") == "mixed"


def _is_plausible(row: Mapping[str, Any]) -> bool:
    if row.get("discovery_disposition") != "surface":
        return False
    return row.get("relation_to_cited_finding") in {"opposes", "uncertain"}


def _cluster_ids(rows: list[dict], *, independent_only: bool) -> list[str]:
    values = set()
    for row in rows:
        if independent_only and row.get("independent") != "independent":
            continue
        value = row.get("candidate_study_cluster_id")
        if isinstance(value, str) and value:
            values.add(value)
    return sorted(values)


def _categorize_candidates(candidates) -> dict[str, list[dict]]:
    categories = {
        "qualifying_contradictions": [],
        "plausible_or_uncertain": [],
        "comparable_confirmations": [],
        "mixed_findings": [],
        "flagged_or_corrected": [],
        "same_study_context": [],
        "excluded_candidates": [],
    }
    for raw_row in candidates:
        row = _copy_row(raw_row)
        if not _clear_notice(row):
            categories["flagged_or_corrected"].append(row)
            continue
        same_study = row.get("independent") == "not_independent"
        if same_study:
            categories["same_study_context"].append(row)
        if _is_qualifying(row):
            categories["qualifying_contradictions"].append(row)
        elif _is_confirmation(row):
            categories["comparable_confirmations"].append(row)
        elif _is_mixed(row):
            categories["mixed_findings"].append(row)
        elif _is_plausible(row) and not same_study:
            categories["plausible_or_uncertain"].append(row)
        elif not same_study:
            categories["excluded_candidates"].append(row)
    return categories


def _derive_evidence_profile(record, candidates, categories):
    opposing = categories["qualifying_contradictions"]
    confirmations = categories["comparable_confirmations"]
    mixed = categories["mixed_findings"]
    plausible = categories["plausible_or_uncertain"]
    flagged = categories["flagged_or_corrected"]
    cited_cluster_id = record.get("cited_study_cluster_id")
    opposing_clusters = _cluster_ids(opposing, independent_only=True)
    confirming_clusters = _cluster_ids(confirmations, independent_only=True)
    if isinstance(cited_cluster_id, str) and cited_cluster_id:
        confirming_clusters = sorted({cited_cluster_id, *confirming_clusters})

    relation_rows = opposing + confirmations + mixed + plausible
    pairwise_confidences = sorted(
        [float(row["confidence"]) for row in relation_rows
         if isinstance(row.get("confidence"), (int, float))
         and not isinstance(row.get("confidence"), bool)])
    candidate_source_statuses = [
        str(row.get("candidate_source_status") or "not_fetched")
        for row in candidates]
    search_complete = (
        record.get("retrieval_status") == "ok"
        and record.get("retrieval_adequacy") in {"adequate", "empty"}
        and record.get("budget_exhausted") is not True
        and not any(
            row.get("reason") == "abstract_screen_clear_mismatch"
            for row in candidates))
    source_complete = (
        record.get("cited_source_status") == "complete"
        and all(status in {"complete", "not_fetched"}
                for status in candidate_source_statuses)
        and not any(
            row.get("reason") == "deep_comparison_budget_exhausted"
            for row in candidates))

    cluster_counts: dict[str, int] = {}
    for row in candidates:
        cluster_id = row.get("candidate_study_cluster_id")
        if isinstance(cluster_id, str) and cluster_id:
            cluster_counts[cluster_id] = cluster_counts.get(cluster_id, 0) + 1
    duplicate_warnings = sorted(
        f"{cluster_id} has {count} candidate reports"
        for cluster_id, count in cluster_counts.items() if count > 1)
    if any(row.get("independent") == "not_independent" for row in candidates):
        duplicate_warnings.append(
            "same-study or same-cohort reports are context, not independent votes")

    if mixed or (opposing and confirmations):
        pattern = "mixed_evidence"
    elif opposing:
        pattern = (
            "multiple_opposition" if len(opposing_clusters) > 1
            else "single_opposition")
    elif confirmations:
        pattern = "confirmation_only_found"
    elif (not search_complete or plausible or flagged
          or record.get("temporal_state") == "UNJUDGEABLE"):
        pattern = "unassessable"
    else:
        pattern = "no_controversy_found"

    evidence_profile = {
        "opposing_paper_count": len(opposing),
        "confirming_paper_count": (1 if record.get("cited_work_id") else 0)
        + len(confirmations),
        "mixed_paper_count": len(mixed),
        "uncertain_paper_count": len(plausible) + len(flagged),
        "independent_opposing_cluster_count": len(opposing_clusters),
        "independent_confirming_cluster_count": len(confirming_clusters),
        "opposing_evidence_tiers": sorted({
            row["candidate_tier"] for row in opposing
            if isinstance(row.get("candidate_tier"), str)}),
        "confirming_evidence_tiers": sorted({
            *([record["cited_tier"]]
              if isinstance(record.get("cited_tier"), str) else []),
            *(row["candidate_tier"] for row in confirmations
              if isinstance(row.get("candidate_tier"), str)),
        }),
        "sample_sizes": [
            {"work_id": row.get("candidate_work_id"),
             "sample_size": row.get("sample_size")}
            for row in relation_rows if row.get("sample_size") is not None],
        "systematic_review_present": any(
            row.get("candidate_tier") == "systematic_review_or_meta_analysis"
            for row in candidates),
        "guideline_presence": "not_collected",
        "search_complete": search_complete,
        "source_complete": source_complete,
        "source_status_counts": {
            status: candidate_source_statuses.count(status)
            for status in sorted(set(candidate_source_statuses))},
        "maximum_pairwise_confidence": (
            max(pairwise_confidences) if pairwise_confidences else None),
        "pairwise_confidence_distribution": pairwise_confidences,
        "duplicate_or_overlap_warnings": duplicate_warnings,
        "evidence_pattern": pattern,
    }
    return evidence_profile, search_complete


def _cited_finding_spans(record, candidates) -> list[str]:
    spans = []
    for row in candidates:
        span = row.get("cited_finding_span")
        if isinstance(span, str) and span.strip() and span not in spans:
            spans.append(span)
    record_span = record.get("cited_finding_span")
    if (isinstance(record_span, str) and record_span.strip()
            and record_span not in spans):
        spans.append(record_span)
    return spans


def build_controversy_bundle(
        record: Mapping[str, Any], *, citation_id: str | None = None) -> dict:
    """Build one complete, versioned bundle without changing detector routing."""
    if not isinstance(record, Mapping):
        raise ValueError("record must be a mapping")
    candidates = record.get("candidate_assessments") or []
    if (not isinstance(candidates, list)
            or any(not isinstance(row, Mapping) for row in candidates)):
        raise ValueError("candidate_assessments must be a list of mappings")

    categories = _categorize_candidates(candidates)
    cited_cluster_id = record.get("cited_study_cluster_id")
    evidence_profile, search_complete = _derive_evidence_profile(
        record, candidates, categories)

    cited_finding_spans = _cited_finding_spans(record, candidates)

    bundle = {
        "schema_version": CONTROVERSY_BUNDLE_SCHEMA_VERSION,
        "citation_id": citation_id,
        "claim_index": record.get("claim_index"),
        "claim_text": record.get("claim_text"),
        "activation": _copy_row(record.get("activation") or {}),
        "cited_work": {
            "work_id": record.get("cited_work_id"),
            "date": record.get("cited_date"),
            "tier": record.get("cited_tier"),
            "notice_kind": record.get("cited_notice_kind"),
            "notice_resolution": record.get("cited_notice_resolution"),
            "source_status": record.get("cited_source_status"),
            "source_packet_sha256": record.get("cited_source_packet_sha256"),
            "study_cluster_id": cited_cluster_id,
            "finding_spans": cited_finding_spans,
        },
        "as_of_date": record.get("as_of_date"),
        "search_receipt": {
            "status": record.get("retrieval_status"),
            "adequacy": record.get("retrieval_adequacy"),
            "query_hash": record.get("retrieval_query_hash"),
        },
        **categories,
        "selected_contradiction_work_id": record.get(
            "selected_contradiction_work_id"),
        "selected_replacement_work_id": record.get("selected_replacement_work_id"),
        "evidence_profile": evidence_profile,
        "field_confidence": dict(FIELD_CONFIDENCE),
        "search_complete": search_complete,
        "human_review_reason": HUMAN_REVIEW_REASON,
    }
    bundle["bundle_sha256"] = controversy_bundle_sha256(bundle)
    return bundle
