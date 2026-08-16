"""Co-citation groups: the unit a collectively-cited sentence is actually judged as.

THE DEFECT THIS EXISTS TO FIX
-----------------------------
``parser.link_citances`` attaches one citing sentence to every reference that
sentence cites, independently. The band then builds one item per reference, each
carrying the WHOLE claim, and asks of each one alone: does this paper support it?
Each supports part. F6 is defined as "supports part of the claim but not all of
it", so F6 fired BY CONSTRUCTION on every member of every co-citation group.

Measured first-party on PMC13295119 (2026-08-15, claude-opus-5, abstract scope):
F6 on multi-reference sentences 100/124 = 80.6%, on single-reference sentences
44/98 = 44.9% -- a 36-point gap that is an artifact of the unit of analysis, not
a property of the citations.

Citing eight papers for one sentence is normal, correct scientific practice. The
eight are cited COLLECTIVELY. No single one is expected to carry the whole claim.

WHAT THIS MODULE IS NOT ALLOWED TO BECOME
-----------------------------------------
A blanket excuse. Three things must survive, and each is a named outcome here:

  1. A reference cited alongside seven others that supports NOTHING is still a
     fault -- :data:`ROUTE_UNSUPPORTED_MEMBER`, never a clear. "Someone else
     covered it" must not clear a reference that covered none of it.
  2. A claim NO member supports is still a real defect. It belongs to the group,
     so it is reported on the group record as an uncovered claim and every
     contributing member routes :data:`ROUTE_GROUP_COVERAGE_GAP`. Trading a
     false-positive problem for a false-negative one is worse, and a silently
     dropped true fault is a scope reduction, not a precision improvement.
  3. CONTRADICTION IS PER-REFERENCE AND SURVIVES GROUPING. A cited paper whose
     abstract contradicts the claim is a fault whether or not siblings exist; a
     sibling covering the claim says nothing about this paper's counter-evidence.

DESIGN
------
Judge per reference (unchanged, one judge call per reference, PROMPTS UNTOUCHED),
then aggregate deterministically across the group. That is possible because the
band already extracts atomic claims ONCE per citing sentence and copies the list
to each member (``run_band``'s ``claims_cache``), so claim ``i`` means the same
thing for every member and the aggregation is an index-wise join, not a matching
problem.

A group of ONE is not a group: :func:`member_route` returns the solo route
unchanged, so a singleton citation takes byte-identically the path it took
before. Same for a member whose evidence was never judged.

This module is PURE: no network, no model call, no I/O. It takes buckets that the
caller has already computed and returns routes and a record.
"""
from __future__ import annotations

from typing import Sequence

# --------------------------------------------------------------------------
# The per-claim coverage vocabulary. SINGLE SOURCE OF TRUTH: judgment_band
# imports these under its own long-standing public names, so the two cannot
# drift and no byte of the existing manifest changes.
# --------------------------------------------------------------------------
BUCKET_ESTABLISHED = "coverage_established"
BUCKET_CONTRADICTED = "coverage_contradicted"
BUCKET_UNCONFIRMED_SPECIFIC = "coverage_unconfirmed_specific"
BUCKET_OFF_TOPIC = "coverage_off_topic"
#: A verdict carrying no structured judgment at all -- the deterministic
#: no-usable-abstract / no-usable-fulltext path. Represented as ``None``, and it
#: is NEITHER evidence of coverage NOR evidence of a gap.
BUCKET_UNJUDGED = None

# --- per-claim status of the GROUP ----------------------------------------
#: At least one member's evidence ESTABLISHED this claim.
CLAIM_COVERED = "covered"
#: At least one member judged this claim and NONE established it. A real defect,
#: owned by the group.
CLAIM_UNCOVERED = "uncovered"
#: No member could be judged on this claim (every member's evidence was
#: unretrievable). Not covered, not uncovered -- unknown. Precision-first.
CLAIM_UNKNOWN = "unknown"

# --- group-aware member routes (added to the band's route vocabulary) ------
#: Member of a group whose evidence covers every claim; this member contributed.
#: NOT a fault, and NOT the same thing as FULL_COVERAGE (which means this one
#: reference established every claim by itself).
ROUTE_GROUP_COVERED = "GROUP_COVERED"
#: Member of a group where at least one claim was covered by NO member. The gap
#: is the group's; every contributing member carries it.
ROUTE_GROUP_COVERAGE_GAP = "GROUP_COVERAGE_GAP"
#: Member that engaged NOTHING -- every claim its evidence was judged on came
#: back off-topic. The freeloader. A fault, and deliberately distinct from F6:
#: F6 asserts a partial-support relationship this reference does not even have.
ROUTE_UNSUPPORTED_MEMBER = "UNSUPPORTED_MEMBER"

GROUP_ROUTES = frozenset({
    ROUTE_GROUP_COVERED, ROUTE_GROUP_COVERAGE_GAP, ROUTE_UNSUPPORTED_MEMBER,
})

#: Why a member of a co-citation group was aggregated as a singleton anyway.
EXCLUDED_NO_VERDICTS = "no_coverage_verdicts"
EXCLUDED_CLAIMS_DIFFER = "atomic_claims_differ_from_group"


def group_id_of(item: dict) -> str:
    """The co-citation group id on an item, or "" when it has none.

    Empty is read as "no group known" everywhere, and every consumer treats that
    as a singleton -- i.e. exactly the pre-group behaviour. An item built outside
    ``parser.link_citances`` therefore cannot silently acquire group semantics.
    """
    value = item.get("citance_group_id")
    return value.strip() if isinstance(value, str) else ""


def partition(items: "Sequence[dict]") -> dict:
    """Group items by ``citance_group_id``, in first-appearance order.

    Items with no group id are each returned as their OWN single-member group
    keyed by their citation_id, so a caller can treat every item uniformly
    without a second code path (acceptance row 10: no path divergence).
    """
    groups: dict = {}
    for item in items:
        gid = group_id_of(item) or f"~solo~{item.get('citation_id')}"
        groups.setdefault(gid, []).append(item)
    return groups


def _claims_of(item: dict) -> tuple:
    claims = item.get("atomic_claims")
    return tuple(claims) if isinstance(claims, list) else ()


def aggregate(items: "Sequence[dict]", *, buckets_of) -> dict:
    """Aggregate one co-citation group's per-member coverage into a group view.

    ``buckets_of(item) -> list`` yields this member's per-claim bucket, aligned
    index-wise with its ``atomic_claims`` (``None`` for a verdict carrying no
    structured judgment).

    Returns a dict with the group's claim list, a per-claim status + attribution,
    and the two exclusion lists. Members are EXCLUDED from the aggregation (and
    listed with a reason) when they carry no verdicts -- a parse quarantine -- or
    when their claim list differs from the group's, which would otherwise join
    verdicts about two different claims on one index. An excluded member is not
    silently dropped: it keeps its solo route and is named in the record.
    """
    contributing, excluded = [], []
    group_claims: tuple = ()
    for item in items:
        claims = _claims_of(item)
        buckets = list(buckets_of(item) or [])
        if not buckets:
            excluded.append({"citation_id": item.get("citation_id"),
                             "reason": EXCLUDED_NO_VERDICTS})
            continue
        if not group_claims:
            group_claims = claims
        elif claims != group_claims:
            excluded.append({"citation_id": item.get("citation_id"),
                             "reason": EXCLUDED_CLAIMS_DIFFER})
            continue
        contributing.append((item, buckets))

    coverage = []
    for i, claim in enumerate(group_claims):
        covered_by, contradicted_by, judged_by = [], [], []
        for item, buckets in contributing:
            bucket = buckets[i] if i < len(buckets) else None
            if bucket is None:
                continue
            cid = item.get("citation_id")
            judged_by.append(cid)
            if bucket == BUCKET_ESTABLISHED:
                covered_by.append(cid)
            elif bucket == BUCKET_CONTRADICTED:
                contradicted_by.append(cid)
        if covered_by:
            status = CLAIM_COVERED
        elif judged_by:
            status = CLAIM_UNCOVERED
        else:
            status = CLAIM_UNKNOWN
        coverage.append({
            "claim": claim,
            "status": status,
            "covered_by": covered_by,
            "contradicted_by": contradicted_by,
            "judged_by": judged_by,
        })
    return {
        "atomic_claims": list(group_claims),
        "claim_coverage": coverage,
        "contributing_members": [i.get("citation_id") for i, _b in contributing],
        "excluded_members": excluded,
    }


def cogroup_covered_flags(aggregated: dict) -> tuple:
    """Per-claim "a sibling established this" flags, for the typed engine.

    This is the ONLY thing the engine is told about the group: a claim flagged
    True is not this member's coverage gap, so it must not raise F6 against this
    member. It says nothing about strength (F4) or entity (F7), which are
    per-reference properties and stay untouched.
    """
    return tuple(row["status"] == CLAIM_COVERED
                 for row in aggregated.get("claim_coverage", []))


def member_route(*, buckets, solo_route: str, aggregated: dict,
                 group_size: int) -> str:
    """The group-aware route for ONE member. ``solo_route`` is today's route.

    THE GROUP CAN ONLY EXPLAIN A GAP OR NAME A FAULT. It never downgrades a route
    the member earned on its own evidence, and it never invents a clear. Every
    early return below hands back ``solo_route`` untouched.

    Order is load-bearing:
      1. A group of one is not a group -- singletons take byte-identically the
         path they took before.
      2. Nothing judged at all -> ``solo_route``. A member whose abstract could
         not be retrieved is an OPERATIONAL exclusion: neither a freeloader nor
         covered, and it must be counted as neither.
      3. ANY claim this member's own evidence could not be judged on ->
         ``solo_route`` (HELD). Grouping acts only on a member whose every claim
         was actually judged; excusing an unjudged claim would be an argument
         from missing evidence. A bucket list that does not LINE UP with the
         group's claim list takes this same path: :func:`aggregate` pads a short
         member out with ``None`` (unjudged), so anything else here would let a
         truncated list skip the very guard the padding exists to trigger.
      4. Every claim ESTABLISHED by this member alone -> ``solo_route``
         (FULL_COVERAGE). It is strictly more informative than any group route,
         and it is the gate into the F3 provenance discriminator: downgrading it
         would quietly remove a fully-supporting reference from that stage.
      5. CONTRADICTION -> ``solo_route`` (F6_FLAGGED). Per-reference
         counter-evidence is a fault no sibling can absolve; a sibling covering
         the claim says nothing about THIS paper's contradiction of it.
      6. Engaged nothing -> ``ROUTE_UNSUPPORTED_MEMBER``. Every judged claim came
         back off-topic: it supported none of the sentence. A fault. Decided on
         the MEMBER's own evidence and NOT on what the siblings did, so the group
         cannot launder it -- a wholly off-topic group yields a fault on every
         member rather than averaging into "partially covered".
      7. Any claim uncovered by the WHOLE group -> ``ROUTE_GROUP_COVERAGE_GAP``.
         The defect is real and belongs to the group; every contributing member
         carries it so it cannot vanish because "it's a group".
      8. Any claim no member could judge -> ``solo_route``. Unknown is not
         coverage.
      9. Otherwise the group covers the sentence and this member contributed ->
         ``ROUTE_GROUP_COVERED``.
    """
    if group_size <= 1:
        return solo_route
    all_buckets = list(buckets or [])
    statuses = [row["status"] for row in aggregated.get("claim_coverage", [])]
    judged = [b for b in all_buckets if b is not None]
    if not judged:
        return solo_route
    if any(b is None for b in all_buckets) or len(all_buckets) != len(statuses):
        return solo_route
    if all(b == BUCKET_ESTABLISHED for b in all_buckets):
        return solo_route
    if any(b == BUCKET_CONTRADICTED for b in judged):
        return solo_route
    if all(b == BUCKET_OFF_TOPIC for b in judged):
        return ROUTE_UNSUPPORTED_MEMBER
    if any(s == CLAIM_UNCOVERED for s in statuses):
        return ROUTE_GROUP_COVERAGE_GAP
    if any(s == CLAIM_UNKNOWN for s in statuses):
        return solo_route
    return ROUTE_GROUP_COVERED


def group_record(group_id: str, items: "Sequence[dict]", aggregated: dict,
                 routes: dict) -> dict:
    """The durable group-level record.

    It answers exactly the three questions a reader must be able to ask of a
    co-cited sentence: (a) which references shared this citance, (b) which claims
    the group as a whole covered, (c) which claims NO member covered. (c) is
    materialized as its own list rather than left to be derived, because an
    uncovered claim is a real defect and a defect a reader has to compute is a
    defect that goes unread.
    """
    first = items[0] if items else {}
    coverage = aggregated.get("claim_coverage", [])
    uncovered = [row["claim"] for row in coverage
                 if row["status"] == CLAIM_UNCOVERED]
    unknown = [row["claim"] for row in coverage
               if row["status"] == CLAIM_UNKNOWN]
    return {
        "citance_group_id": group_id,
        "citing_pmcid": first.get("citing_pmcid"),
        "citing_sentence": first.get("citing_sentence"),
        "size": len(items),
        "members": [i.get("citation_id") for i in items],
        "atomic_claims": list(aggregated.get("atomic_claims", [])),
        "claim_coverage": coverage,
        "claims_covered": sum(1 for r in coverage if r["status"] == CLAIM_COVERED),
        "claims_uncovered": len(uncovered),
        "claims_unknown": len(unknown),
        "uncovered_claims": uncovered,
        "unknown_claims": unknown,
        "contributing_members": list(aggregated.get("contributing_members", [])),
        "excluded_members": list(aggregated.get("excluded_members", [])),
        "member_routes": dict(routes),
    }
