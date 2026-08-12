"""cre/f1/f5_discovery_queue.py -- the annotation queue F5 exists to produce.

``discovery_disposition`` (surface | do_not_surface | unassessable) is computed by
``f5_supersession``, written to every record and every candidate assessment, and --
before this module -- READ BY NOBODY in either repo. A discovery build whose
disposition has no consumer produces nothing; this is the consumer.

BLIND BY CONSTRUCTION. The annotator-facing row must not carry the detector's own
opinion: ``proposed_route``, ``temporal_state``, ``confidence`` and
``discovery_disposition`` are withheld, and :func:`assert_blind` re-checks that on
the built artifact rather than trusting the builder. The judge is never gold, and
an annotator who can see the proposed route is no longer annotating independently.

RECORDED, NOT QUEUED. ``do_not_surface`` and ``unassessable`` rows are counted and
kept in the record; they are simply not put in front of an annotator. That is the
DEC-045 read-across -- a NO_CLAIMS reference is recorded and counted, never queued
-- and it is why the counts live in the manifest even though the queue does not.

ABSENCE LANGUAGE. Nothing here may say that no superseding paper exists. F5 can
report only "none found under this protocol": SciFact-Open measured that 34.3%
(251/732) of pooled candidates assumed to hold no evidence actually held it and
that 18% (38/209) of known evidence never entered a four-system pool at all, recall
on the disputing class has sat below 0.5 for twenty years (Teufel 2006 CoCo- 0.19
-> scite 2021 0.451), and no dataset of verified negatives exists to validate an
abstention threshold against.
"""
from __future__ import annotations

#: Fields the annotator must NOT see. The detector's conclusion, its confidence,
#: and its own disposition are all withheld.
BLIND_FIELDS = ("proposed_route", "temporal_state", "confidence",
                "discovery_disposition", "discovery_confidence",
                "same_claim_or_outcome", "comparable_population")

QUEUE_VERSION = "f5_discovery_queue_v1"

#: The two reasons a negative can have. They must stay distinguishable: an outage
#: wearing the same reason string as a real absence cost calibration run 1 its
#: entire yield.
NEGATIVE_NO_EVIDENCE_FOUND = "no_admissible_later_evidence_found_under_protocol"
NEGATIVE_RETRIEVAL_FAILED = "retrieval_failed_nothing_was_searched"


def negative_reason(retrieval_status: str, retrieval_adequacy: str) -> str:
    """Which kind of negative this is -- absence, or not having looked.

    ``status`` is the authority: ``failure`` means the search did not complete, so
    the empty candidate list says nothing about the world. Only a completed search
    that returned nothing is an absence, and even then only under this protocol."""
    if retrieval_status != "ok":
        return NEGATIVE_RETRIEVAL_FAILED
    if retrieval_adequacy == "empty":
        return NEGATIVE_NO_EVIDENCE_FOUND
    return NEGATIVE_NO_EVIDENCE_FOUND


def build_queue(records) -> "list[dict]":
    """Annotator-facing rows for every ``surface`` candidate, blind.

    One row per SURFACED CANDIDATE, not per record: the unit an annotator judges is
    a (claim, cited work, candidate work) triple, and a record with two surfaced
    candidates is two independent judgements."""
    queue = []
    for record in records or []:
        for cand in record.get("candidate_assessments") or []:
            if cand.get("discovery_disposition") != "surface":
                continue
            queue.append({
                "queue_version": QUEUE_VERSION,
                "claim_index": record.get("claim_index"),
                "claim_text": record.get("claim_text"),
                "cited_work_id": record.get("cited_work_id"),
                "cited_date": record.get("cited_date"),
                "candidate_work_id": cand.get("candidate_work_id"),
                "candidate_date": cand.get("candidate_date"),
                "cited_finding_span": cand.get("cited_finding_span"),
                "candidate_contradiction_span": cand.get("candidate_contradiction_span"),
                # WHY this pair reached an annotator, in the annotator's terms.
                "scope_mismatch_axis": cand.get("scope_mismatch_axis"),
                "reason": cand.get("reason"),
            })
    return queue


def disposition_counts(records) -> dict:
    """Tally by disposition over every candidate assessment, queued or not.

    ``do_not_surface`` and ``unassessable`` appear HERE and nowhere else: counted,
    never queued."""
    counts = {"surface": 0, "do_not_surface": 0, "unassessable": 0}
    for record in records or []:
        for cand in record.get("candidate_assessments") or []:
            disposition = cand.get("discovery_disposition")
            if disposition in counts:
                counts[disposition] += 1
    return counts


#: The queue gets its OWN artifact. It must NOT be appended to
#: ``judgment_band_annotation_queue.jsonl``: that filename is written by both
#: ``run_band`` and ``judgment_run`` and 24 assertions across 8 test files depend on
#: its exact contents, 8 of them asserting it is EMPTY in specific scenarios. F5
#: rows in there would turn all of those red at once, and the failures would read
#: as F5 logic rather than a filename collision.
QUEUE_FILENAME = "f5_discovery_queue.jsonl"


def _walk_keys(value):
    """Every key at every depth -- a top-level whitelist is not sufficient."""
    if isinstance(value, dict):
        for key, sub in value.items():
            yield key
            yield from _walk_keys(sub)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            yield from _walk_keys(sub)


def assert_blind(queue) -> None:
    """Raise if any queue row leaks a withheld field AT ANY DEPTH.

    Checked on the BUILT artifact, not asserted of the builder: the guarantee that
    matters is about the bytes an annotator receives.

    RECURSIVE on purpose, mirroring ``judgment_band._scrub_annotation_value``. The
    lesson already paid for in that code is that an outer whitelist is necessary but
    NOT sufficient: a nested row smuggles forbidden keys past a top-level filter,
    and a candidate assessment carries its OWN ``discovery_disposition``. Rows here
    are flat by construction, so this is the check that keeps them that way."""
    for index, row in enumerate(queue or []):
        leaked = sorted(set(_walk_keys(row)) & set(BLIND_FIELDS))
        if leaked:
            raise ValueError(
                f"f5 discovery queue row {index} leaks blind field(s) {leaked} "
                "(checked at every depth); the annotator must not see the "
                "detector's own route, confidence or disposition")
