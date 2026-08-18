"""Pure, offline-testable decision core for the CRE F3--F7 band.

This module does not retrieve evidence and does not ask an LLM to make a
taxonomy decision. Injected assessors produce strictly typed evidence
assessments; decide_judgment applies the category hierarchy deterministically.

The existing judgment_band coverage substrate remains unchanged. Its tri-state
established results can enter this engine through from_legacy_coverage. That
adapter cannot produce F4 because the legacy prompt intentionally judges
presence rather than strength.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Sequence


class DiscriminatorContractError(ValueError):
    """An injected assessor returned malformed or contradictory evidence."""


class SupportState(str, Enum):
    SUPPORTED = "SUPPORTED"
    WEAKER_STRENGTH = "WEAKER_STRENGTH"
    UNESTABLISHED = "UNESTABLISHED"
    UNJUDGEABLE = "UNJUDGEABLE"


class EntityState(str, Enum):
    SAME_ENTITY = "SAME_ENTITY"
    DIFFERENT_ENTITY_SUPPORTED = "DIFFERENT_ENTITY_SUPPORTED"
    UNJUDGEABLE = "UNJUDGEABLE"


class ProvenanceState(str, Enum):
    PROPER_ORIGIN = "PROPER_ORIGIN"
    MISATTRIBUTED_CONFIRMED = "MISATTRIBUTED_CONFIRMED"
    UNJUDGEABLE = "UNJUDGEABLE"


class TemporalState(str, Enum):
    NO_QUALIFYING_CONTRADICTION = "NO_QUALIFYING_CONTRADICTION"
    QUALIFYING_CONTRADICTION = "QUALIFYING_CONTRADICTION"
    UNJUDGEABLE = "UNJUDGEABLE"


class DecisionStatus(str, Enum):
    TERMINAL = "TERMINAL"
    HELD_UNJUDGEABLE = "HELD_UNJUDGEABLE"
    OUTSIDE_BAND = "OUTSIDE_BAND"


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_tuple(name: str, values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple) or any(not _nonblank(v) for v in values):
        raise DiscriminatorContractError(
            f"{name} must be a tuple of nonblank strings"
        )


@dataclass(frozen=True)
class ClaimSupport:
    claim_index: int
    state: SupportState
    rationale: str = ""
    evidence_spans: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.claim_index) is not int or self.claim_index < 0:
            raise DiscriminatorContractError("claim_index must be a nonnegative int")
        if not isinstance(self.state, SupportState):
            raise DiscriminatorContractError("state must be a SupportState")
        if not isinstance(self.rationale, str):
            raise DiscriminatorContractError("rationale must be a string")
        _string_tuple("evidence_spans", self.evidence_spans)


@dataclass(frozen=True)
class EntityAssessment:
    claim_index: int
    state: EntityState
    claimed_entity_key: Optional[str] = None
    evidence_entity_key: Optional[str] = None
    relation_supported: Optional[bool] = None
    rationale: str = ""

    def __post_init__(self) -> None:
        if type(self.claim_index) is not int or self.claim_index < 0:
            raise DiscriminatorContractError("claim_index must be a nonnegative int")
        if not isinstance(self.state, EntityState):
            raise DiscriminatorContractError("state must be an EntityState")
        if (
            self.relation_supported is not None
            and type(self.relation_supported) is not bool
        ):
            raise DiscriminatorContractError(
                "relation_supported must be an exact bool or None"
            )
        if not isinstance(self.rationale, str):
            raise DiscriminatorContractError("rationale must be a string")
        if self.state is EntityState.DIFFERENT_ENTITY_SUPPORTED:
            if not _nonblank(self.claimed_entity_key) or not _nonblank(
                self.evidence_entity_key
            ):
                raise DiscriminatorContractError(
                    "confirmed F7 evidence requires two normalized entity keys"
                )
            # CASE-FOLDED. "HGNC:6407" and "hgnc:6407" are one entity key, and a
            # raw .strip() compare accepted that pair as two -- letting a
            # normalizer whose case drifted produce a confirmed wrong-entity
            # accusation against the very same gene. This is the contract
            # boundary, so it holds the line for every future producer, not only
            # for the one in ``f7_entity`` that also checks it now.
            if (self.claimed_entity_key.strip().casefold()
                    == self.evidence_entity_key.strip().casefold()):
                raise DiscriminatorContractError(
                    "confirmed F7 entity keys must differ"
                )
            if self.relation_supported is not True:
                raise DiscriminatorContractError(
                    "an entity mismatch is F7 only when the relation is supported"
                )
        # Case-folded for the same reason and in the same direction as the guard
        # above: two keys differing only in case are ONE key, so SAME_ENTITY over
        # them is consistent and must not raise. Folding only one of the two
        # comparisons would leave a pair that is neither a legal SAME_ENTITY nor
        # a legal mismatch.
        if (
            self.state is EntityState.SAME_ENTITY
            and _nonblank(self.claimed_entity_key)
            and _nonblank(self.evidence_entity_key)
            and (self.claimed_entity_key.strip().casefold()
                 != self.evidence_entity_key.strip().casefold())
        ):
            raise DiscriminatorContractError(
                "SAME_ENTITY conflicts with different normalized keys"
            )


@dataclass(frozen=True)
class ProvenanceAssessment:
    state: ProvenanceState
    origin_chain: tuple[str, ...] = ()
    evidence_spans: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.state, ProvenanceState):
            raise DiscriminatorContractError("state must be a ProvenanceState")
        _string_tuple("origin_chain", self.origin_chain)
        _string_tuple("evidence_spans", self.evidence_spans)
        if not isinstance(self.rationale, str):
            raise DiscriminatorContractError("rationale must be a string")
        if self.state is ProvenanceState.MISATTRIBUTED_CONFIRMED and (
            not self.origin_chain or not self.evidence_spans
        ):
            raise DiscriminatorContractError(
                "confirmed F3 requires an origin chain and evidence spans"
            )


@dataclass(frozen=True)
class TemporalAssessment:
    state: TemporalState
    claim_index: Optional[int] = None
    newer_work_id: Optional[str] = None
    same_claim_or_outcome: Optional[bool] = None
    comparable_population: Optional[bool] = None
    f8_notice: Optional[bool] = None
    evidence_spans: tuple[str, ...] = ()
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.state, TemporalState):
            raise DiscriminatorContractError("state must be a TemporalState")
        if self.claim_index is not None and (
            type(self.claim_index) is not int or self.claim_index < 0
        ):
            raise DiscriminatorContractError(
                "temporal claim_index must be a nonnegative int or None"
            )
        for name in ("same_claim_or_outcome", "comparable_population", "f8_notice"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise DiscriminatorContractError(
                    f"{name} must be an exact bool or None"
                )
        _string_tuple("evidence_spans", self.evidence_spans)
        if not isinstance(self.rationale, str):
            raise DiscriminatorContractError("rationale must be a string")
        if self.state is TemporalState.QUALIFYING_CONTRADICTION:
            if (
                self.claim_index is None
                or not _nonblank(self.newer_work_id)
                or self.same_claim_or_outcome is not True
                or self.comparable_population is not True
                or self.f8_notice is not False
                or not self.evidence_spans
            ):
                raise DiscriminatorContractError(
                    "confirmed F5 requires a newer work, same outcome, comparable "
                    "population, explicit F8 clearance, and evidence"
                )


@dataclass(frozen=True)
class JudgmentDecision:
    status: DecisionStatus
    primary_label: Optional[str]
    findings: tuple[str, ...]
    claim_support: tuple[ClaimSupport, ...]
    entity_assessments: tuple[EntityAssessment, ...]
    provenance: Optional[ProvenanceAssessment]
    temporal: Optional[TemporalAssessment]
    hold_reasons: tuple[str, ...] = ()


SupportAssessor = Callable[[tuple[str, ...]], Sequence[ClaimSupport]]
EntityAssessor = Callable[
    [tuple[str, ...], tuple[ClaimSupport, ...]], Sequence[EntityAssessment]
]
ProvenanceAssessor = Callable[
    [tuple[str, ...], tuple[ClaimSupport, ...]], ProvenanceAssessment
]
TemporalAssessor = Callable[
    [tuple[str, ...], tuple[ClaimSupport, ...]], TemporalAssessment
]


def _validate_claims(claims: Sequence[str]) -> tuple[str, ...]:
    if isinstance(claims, (str, bytes)):
        raise DiscriminatorContractError("claims must be a sequence of strings")
    out = tuple(claims)
    if any(not _nonblank(claim) for claim in out):
        raise DiscriminatorContractError("every claim must be a nonblank string")
    return out


def _validate_support(
    claims: tuple[str, ...], assessments: Sequence[ClaimSupport]
) -> tuple[ClaimSupport, ...]:
    if isinstance(assessments, (str, bytes)):
        raise DiscriminatorContractError("support output must be a sequence")
    try:
        out = tuple(assessments)
    except TypeError:
        raise DiscriminatorContractError(
            "support assessor must return a sequence of ClaimSupport objects"
        ) from None
    if any(not isinstance(row, ClaimSupport) for row in out):
        raise DiscriminatorContractError(
            "support assessor must return ClaimSupport objects"
        )
    if len(out) != len(claims):
        raise DiscriminatorContractError(
            "support assessor must return exactly one row per claim"
        )
    if tuple(row.claim_index for row in out) != tuple(range(len(claims))):
        raise DiscriminatorContractError(
            "support rows must have unique contiguous indices in claim order"
        )
    return out


def _validate_entities(
    claims: tuple[str, ...], assessments: Sequence[EntityAssessment]
) -> tuple[EntityAssessment, ...]:
    if isinstance(assessments, (str, bytes)):
        raise DiscriminatorContractError("entity output must be a sequence")
    try:
        out = tuple(assessments)
    except TypeError:
        raise DiscriminatorContractError(
            "entity assessor must return a sequence of EntityAssessment objects"
        ) from None
    if any(not isinstance(row, EntityAssessment) for row in out):
        raise DiscriminatorContractError(
            "entity assessor must return EntityAssessment objects"
        )
    indices = [row.claim_index for row in out]
    if len(indices) != len(set(indices)):
        raise DiscriminatorContractError("entity rows contain duplicate claim indices")
    if any(index >= len(claims) for index in indices):
        raise DiscriminatorContractError("entity claim_index is out of range")
    return out


def _validate_cogroup_covered(
    claims: tuple[str, ...], covered: Sequence[bool]
) -> tuple[bool, ...]:
    """Validate the optional co-citation coverage overlay.

    Empty -- the default -- means "no co-citation context supplied" and leaves
    every decision below byte-identical to what it was before this parameter
    existed. Anything else must be exactly one EXACT bool per claim: a partial or
    loosely-typed overlay would silently excuse the wrong claim, and an F6 that
    disappears for the wrong reason is worse than the false positive it fixes.
    """
    if isinstance(covered, (str, bytes)):
        raise DiscriminatorContractError("cogroup_covered must be a sequence")
    try:
        out = tuple(covered)
    except TypeError:
        raise DiscriminatorContractError(
            "cogroup_covered must be a sequence"
        ) from None
    if not out:
        return ()
    if any(type(flag) is not bool for flag in out):
        raise DiscriminatorContractError(
            "cogroup_covered must contain exact booleans")
    if len(out) != len(claims):
        raise DiscriminatorContractError(
            "cogroup_covered must have exactly one flag per claim")
    return out


def from_legacy_coverage(
    claims: Sequence[str], verdicts: Sequence[dict]
) -> tuple[ClaimSupport, ...]:
    """Strictly map the frozen binary coverage substrate into support states."""
    claim_values = _validate_claims(claims)
    if isinstance(verdicts, (str, bytes)):
        raise DiscriminatorContractError("legacy verdicts must be a sequence")
    try:
        rows = tuple(verdicts)
    except TypeError:
        raise DiscriminatorContractError(
            "legacy verdicts must be a sequence"
        ) from None
    if len(rows) != len(claim_values):
        raise DiscriminatorContractError(
            "legacy coverage must return exactly one row per claim"
        )
    mapped = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DiscriminatorContractError("legacy coverage rows must be objects")
        if "established" not in row:
            raise DiscriminatorContractError(
                "legacy coverage rows must carry an established field"
            )
        value = row["established"]
        if value is True:
            state = SupportState.SUPPORTED
        elif value is False:
            state = SupportState.UNESTABLISHED
        elif value is None:
            state = SupportState.UNJUDGEABLE
        else:
            raise DiscriminatorContractError(
                "legacy established must be exact True, False, or None"
            )
        span = row.get("evidence_span", "")
        if span is None:
            spans = ()
        elif isinstance(span, str):
            spans = (span.strip(),) if _nonblank(span) else ()
        else:
            raise DiscriminatorContractError(
                "legacy evidence_span must be a string or null"
            )
        # rationale is a log-only field; an explicit-null value normalizes to ""
        # (parallel to evidence_span above, which _nonblank already tolerates)
        # rather than rejecting an otherwise-valid legacy row. A non-string,
        # non-null value is still malformed.
        rationale = row.get("rationale", "")
        if rationale is None:
            rationale = ""
        if not isinstance(rationale, str):
            raise DiscriminatorContractError(
                "legacy rationale must be a string or null")
        mapped.append(ClaimSupport(index, state, rationale, spans))
    return tuple(mapped)


def decide_judgment(
    *,
    preband_cleared: bool,
    claims: Sequence[str],
    claim_support: Sequence[ClaimSupport],
    entity_assessments: Sequence[EntityAssessment] = (),
    provenance: Optional[ProvenanceAssessment],
    temporal: Optional[TemporalAssessment],
    cogroup_covered: Sequence[bool] = (),
) -> JudgmentDecision:
    """Apply the reviewed F3--F7 hierarchy to typed assessments.

    ``cogroup_covered`` is the CO-CITATION OVERLAY: one flag per claim, True when
    some OTHER reference cited in the same sentence established that claim. A
    sentence citing eight references cites them collectively, so a claim a
    sibling established is not this reference's coverage gap and must not raise
    F6 against it -- otherwise F6 fires by construction on every member of every
    co-citation group.

    It is the ONLY thing the engine is told about the group, and it touches
    exactly one finding:

      * F6 is not raised for a claim whose flag is True. Instead a hold reason
        records that the coverage was attributed elsewhere, so the reference is
        never silently CLEARED either -- it did not establish the claim, the
        group did.
      * F4 (strength) and F7 (entity) are PER-REFERENCE properties and are
        untouched. A cited paper making a weaker claim than the citing sentence
        is F4 whether or not siblings exist, and a paper about a different entity
        is F7 the same way.
      * ``all_supported`` still requires every claim strictly SUPPORTED by THIS
        reference, so the F3 provenance gate is unchanged: a claim covered only
        by a sibling never opens provenance on this paper.

    Empty (the default) means no co-citation context and reproduces the previous
    behaviour exactly."""
    if type(preband_cleared) is not bool:
        raise DiscriminatorContractError("preband_cleared must be an exact bool")
    claim_values = _validate_claims(claims)
    support = _validate_support(claim_values, claim_support)
    entities = _validate_entities(claim_values, entity_assessments)
    covered = _validate_cogroup_covered(claim_values, cogroup_covered)
    if provenance is not None and not isinstance(provenance, ProvenanceAssessment):
        raise DiscriminatorContractError("provenance has the wrong contract type")
    if temporal is not None and not isinstance(temporal, TemporalAssessment):
        raise DiscriminatorContractError("temporal has the wrong contract type")

    if not preband_cleared:
        return JudgmentDecision(
            DecisionStatus.OUTSIDE_BAND,
            None,
            (),
            support,
            entities,
            provenance,
            temporal,
            ("F1/F2/F8 pre-band checks did not clear",),
        )

    findings: list[str] = []
    hold_reasons: list[str] = []
    if not claim_values:
        hold_reasons.append("no atomic claims")
    if any(row.state is EntityState.DIFFERENT_ENTITY_SUPPORTED for row in entities):
        findings.append("F7")
    # An UNESTABLISHED claim that a CO-CITED reference established is the group's
    # coverage, not this reference's gap: no F6. It is still not this reference's
    # support either, so it holds rather than clears -- a co-citation group must
    # never become a blanket excuse.
    unestablished = [row for row in support
                     if row.state is SupportState.UNESTABLISHED]
    own_gaps = [row for row in unestablished
                if not (covered and covered[row.claim_index])]
    if own_gaps:
        findings.append("F6")
    # Deferred to the end of the hierarchy, and raised ONLY if nothing was found.
    # Its whole job is to stop a group-covered claim from producing a silent
    # "accurate", which can happen only when findings AND hold_reasons are both
    # empty. Appending it unconditionally would instead downgrade a CONFIRMED
    # fault -- an F4 overstatement, an F7 wrong entity -- from terminal to held,
    # hiding exactly the per-reference faults co-citation must never touch.
    cogroup_attributed = len(own_gaps) != len(unestablished)
    if any(row.state is SupportState.WEAKER_STRENGTH for row in support):
        findings.append("F4")

    all_supported = bool(support) and all(
        row.state is SupportState.SUPPORTED for row in support
    )
    if all_supported:
        if provenance is None:
            hold_reasons.append("provenance assessment missing after full support")
        elif provenance.state is ProvenanceState.MISATTRIBUTED_CONFIRMED:
            findings.append("F3")
        elif provenance.state is ProvenanceState.UNJUDGEABLE:
            hold_reasons.append("provenance is unjudgeable")
    elif provenance is not None:
        raise DiscriminatorContractError(
            "provenance assessment is allowed only after full support"
        )

    if any(row.state is SupportState.UNJUDGEABLE for row in support):
        hold_reasons.append("claim support is unjudgeable")
    if any(row.state is EntityState.UNJUDGEABLE for row in entities):
        hold_reasons.append("entity evidence is unjudgeable")

    if temporal is None:
        hold_reasons.append("temporal assessment missing")
    elif temporal.state is TemporalState.UNJUDGEABLE:
        hold_reasons.append("temporal evidence is unjudgeable")
    elif temporal.state is TemporalState.QUALIFYING_CONTRADICTION:
        if temporal.claim_index is None or temporal.claim_index >= len(support):
            raise DiscriminatorContractError("F5 target claim_index is out of range")
        # UNRESOLVED TAXONOMY DECISION (2026-07-16): F5 currently requires the
        # target claim to be strictly SUPPORTED. Whether a WEAKER_STRENGTH (F4)
        # claim -- which the cited paper DID make, only weakly -- should also be a
        # valid F5 target (yielding F4 + F5) is an open question for the taxonomy
        # owner. Left unchanged and deliberately NOT pinned by a test yet.
        if support[temporal.claim_index].state is not SupportState.SUPPORTED:
            raise DiscriminatorContractError(
                "F5 contradiction must target a claim the cited paper supported"
            )
        findings.append("F5")

    ordered_findings = tuple(
        label for label in ("F7", "F6", "F4", "F3", "F5") if label in findings
    )
    if cogroup_attributed and not ordered_findings:
        # See the note at the F6 stage: this is the "never a silent clear" guard,
        # and it fires only where a clear was actually possible.
        hold_reasons.append("claim coverage attributed to a co-cited reference")
    if hold_reasons:
        return JudgmentDecision(
            DecisionStatus.HELD_UNJUDGEABLE,
            None,
            ordered_findings,
            support,
            entities,
            provenance,
            temporal,
            tuple(dict.fromkeys(hold_reasons)),
        )
    primary = ordered_findings[0] if ordered_findings else "accurate"
    return JudgmentDecision(
        DecisionStatus.TERMINAL,
        primary,
        ordered_findings,
        support,
        entities,
        provenance,
        temporal,
        (),
    )


def evaluate_judgment(
    *,
    preband_cleared: bool,
    claims: Sequence[str],
    support_assessor: SupportAssessor,
    entity_assessor: EntityAssessor,
    provenance_assessor: ProvenanceAssessor,
    temporal_assessor: TemporalAssessor,
    cogroup_covered: Sequence[bool] = (),
) -> JudgmentDecision:
    """Run injected assessors, then invoke the deterministic decision core.

    ``cogroup_covered`` passes straight through to :func:`decide_judgment`; see
    its docstring for the co-citation overlay's exact effect. Empty (the default)
    reproduces the previous behaviour exactly."""
    claim_values = _validate_claims(claims)
    support = _validate_support(claim_values, support_assessor(claim_values))
    entities = _validate_entities(
        claim_values, entity_assessor(claim_values, support)
    )
    all_supported = bool(support) and all(
        row.state is SupportState.SUPPORTED for row in support
    )
    provenance = (
        provenance_assessor(claim_values, support) if all_supported else None
    )
    # Symmetric with the temporal guard below: when all claims are supported the
    # provenance assessor IS invoked and must return a ProvenanceAssessment; a
    # None (or other-shaped) return is malformed assessor output and must raise.
    # When not all supported the assessor is not called and provenance stays None
    # by design. A DIRECT decide_judgment(provenance=None) under full support
    # remains an intentional omission (-> held), unchanged.
    if all_supported and not isinstance(provenance, ProvenanceAssessment):
        raise DiscriminatorContractError(
            "provenance_assessor must return a ProvenanceAssessment when all "
            "claims are supported (a None return is malformed assessor output, "
            "not an intentionally omitted assessment)")
    temporal = temporal_assessor(claim_values, support)
    # An injected assessor is REQUIRED to return a TemporalAssessment. A None (or
    # other-shaped) return is malformed assessor output and must raise, not be
    # silently swallowed. decide_judgment still accepts temporal=None from a
    # DIRECT caller as an intentionally-omitted assessment (-> held); the two are
    # distinguished here so an assessor bug cannot masquerade as an intentional
    # hold and mask an otherwise-terminal verdict.
    if not isinstance(temporal, TemporalAssessment):
        raise DiscriminatorContractError(
            "temporal_assessor must return a TemporalAssessment "
            "(a None return is malformed assessor output, not an "
            "intentionally omitted assessment)")
    return decide_judgment(
        preband_cleared=preband_cleared,
        claims=claim_values,
        claim_support=support,
        entity_assessments=entities,
        provenance=provenance,
        temporal=temporal,
        cogroup_covered=cogroup_covered,
    )
