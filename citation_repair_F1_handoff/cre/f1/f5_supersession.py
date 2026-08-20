"""F5 (stale / superseded) discriminator -- the temporal-supersession detector
for the typed F3--F7 judgment engine (blueprint ``F5_BLUEPRINT.md`` sha256
``663edd690796155bb260e42117c93704b4c58698a0ef5d287cf931f45a04fdea``;
implementation spec ``F5_SUPERSESSION_SPEC.md``, sha256
``f592f726f61cbdfc30fb5ec2589e9505b6440fecc89b6e70e581705d2a1f1c91``).

Both digests are over the blueprint's own canonical form (UTF-8, LF, exactly one
trailing newline -- see its "Freeze canonicalization" section; a copy with the final
newline stripped differs by that one byte). The blueprint digest agrees three ways:
the tracked blob at ``07477e3``, the working-tree file, and the head/tail
(``663edd69`` / ``fdea``) recorded independently in that commit's own message.

F5 is the citation-level instance of *medical reversal*: the cited paper is real,
carries no formal retraction, and ONCE supported the claim, but its central
finding has since been directionally contradicted by more recent, independent
work. F5 is the ONLY discriminator that must retrieve a SECOND paper (the
superseder), so all retrieval and model access are INJECTED ``Callable`` seams;
this module and its tests make no network or paid call.

TWO LAYERS (blueprint Sec 4)
    * DETECTION == the frozen engine ``QUALIFYING_CONTRADICTION`` == the ``F5``
      finding == a CANDIDATE. A newer INDEPENDENT paper DIRECTIONALLY contradicts
      the cited finding on the SAME outcome and a COMPARABLE population, with two
      separately-verified verbatim spans, while the cited work is retraction-clear
      (``f8_notice=False``). Independence, recency, direction, comparability, and
      confidence are DETECTOR-layer preconditions that map into the narrower
      frozen ``TemporalAssessment`` contract (which itself enforces only
      ``same_claim_or_outcome=True`` / ``comparable_population=True`` /
      ``f8_notice=False`` / nonblank ``newer_work_id`` / nonempty verbatim
      ``evidence_spans`` / an in-range ``claim_index`` on a ``SUPPORTED`` claim).
    * REPAIR ROUTING (outside the engine, recorded in the F5 record):
        - Path B -- escalate (DEFAULT, and the ONLY path shipped): surface both
          papers; no autonomous replacement. Driven by ABSENT attestation, not by
          low confidence.
        - Path A -- autonomous replacement (DEFERRED, attestation-gated): proposed
          only with a bound field-level attestation (guideline revision or
          systematic review / meta-analysis) plus the tier + date gate.
          ``path_a_eligible`` may be True, but while ``deploy_path_a=False`` the
          emitted ``f5_path`` STAYS ``B`` and ``path_a_deployed=False``.

OPERATING MODES (blueprint Sec 8a)
    * ``discovery`` (default, the preprint's mode; ``deploy_path_a=False``):
      high-recall candidate generation feeding a human annotation queue. The
      engine verdict (``TemporalState``) still derives from the COMPLETE detector
      contract; a parallel ``discovery_disposition`` (surface / do_not_surface /
      unassessable) derives from the recall policy. Ordinary uncertainty holds as
      ``UNJUDGEABLE`` (never an emitted F5) and may still surface for a human.
    * ``deployment`` (future; requires the Roberts locks + ``deploy_path_a=True``):
      full precision-first rules, reportable verdicts. Nothing autonomous ships
      until then.

SCOPE GATE (blueprint Sec 13, Sec 21)
    Development-mode build ONLY. Advisor locks (comparability v1, the independence
    AND/OR combinator, the tier mapping, the larger-n threshold, the confidence
    floor) are NOT frozen. Any derivation that depends on an unfrozen lock FAILS
    CLOSED (holds ``UNJUDGEABLE``); no reportable F5 / Path-A verdict is derived
    and Path A is never deployed (``reportable=False``, ``deploy_path_a=False`` by
    construction). SUPPORTED-only F5 target (engine L397); ``WEAKER_STRENGTH`` is a
    documented deferred limitation.

FAIL-CLOSED, TWO WAYS (blueprint Sec 12)
    * Malformed / off-enum model JSON or a malformed seam payload -> ``ValueError``
      (the orchestrator quarantines).
    * Well-formed but unknown / unverifiable / low-confidence / comparability- or
      independence-uncertain -> ``UNJUDGEABLE`` (a hold, never a fabricated F5 and
      never a fabricated confident negative). A confident negative
      (``NO_QUALIFYING_CONTRADICTION``) requires an ADEQUATE, NONEMPTY, FULLY
      JUDGEABLE candidate set in which every candidate is nonqualifying.

Strict-JSON parsing of the contradiction judgment mirrors ``band_prompts`` /
``f3_provenance`` / ``f4_strength`` / ``f7_entity`` (duplicate-key rejection,
exact key set, no fences/prose, no coercion). It is replicated here so this module
stays a self-contained leaf. This module never touches the engine, coverage, F2,
F3, F4, or F7.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional, Sequence

from .f5_activation import activation_decision_from_dict, decide_f5_activation
from .judgment_engine import ClaimSupport, SupportState, TemporalAssessment, TemporalState

# Seam type aliases (documentation only). ``judge_contradiction`` returns the
# model's strict-JSON text (blueprint Sec 5: "strict-JSON the model emits"); this
# module parses it, so a malformed / off-enum payload fails closed here.
CallJudgeContradiction = Callable[..., str]

# Development F5 is deliberately not reportable. One named authority is used by
# records, manifests and the reportability gate so those surfaces cannot drift.
F5_REPORTABLE = False
F5_CONTRADICTION_PROMPT_VERSION = "f5_contradiction_v2"
F5_RESPONSE_PARSER_VERSION = "strict_f5_contradiction_spanids_v1"


# --------------------------------------------------------------------------
# Enumerations (blueprint Sec 5, Sec 10, Sec 18a).
# --------------------------------------------------------------------------
class EvidenceTier(str, Enum):
    SYSTEMATIC_REVIEW_OR_META_ANALYSIS = "systematic_review_or_meta_analysis"
    RCT = "rct"
    PROSPECTIVE_COHORT = "prospective_cohort"
    RETROSPECTIVE_COHORT = "retrospective_cohort"
    CASE_CONTROL = "case_control"
    CROSS_SECTIONAL = "cross_sectional"
    CASE_SERIES_OR_REPORT = "case_series_or_report"
    PREPRINT_UNREVIEWED = "preprint_unreviewed"


# High -> low (OCEBM 2011). Higher rank == higher tier; ties break downward.
_TIER_ORDER: tuple[EvidenceTier, ...] = (
    EvidenceTier.SYSTEMATIC_REVIEW_OR_META_ANALYSIS,
    EvidenceTier.RCT,
    EvidenceTier.PROSPECTIVE_COHORT,
    EvidenceTier.RETROSPECTIVE_COHORT,
    EvidenceTier.CASE_CONTROL,
    EvidenceTier.CROSS_SECTIONAL,
    EvidenceTier.CASE_SERIES_OR_REPORT,
    EvidenceTier.PREPRINT_UNREVIEWED,
)
_TIER_RANK: dict[EvidenceTier, int] = {
    tier: (len(_TIER_ORDER) - 1 - i) for i, tier in enumerate(_TIER_ORDER)
}

_CLAIM_MATCH = frozenset({"match", "mismatch", "uncertain"})
_OUTCOME_RELATION = frozenset({"same", "not_same", "uncertain"})
_POPULATION_RELATION = frozenset(
    {"equivalent", "encompassing_direct",
     "encompassing_without_qualifying_direct_evidence", "narrower", "disjoint",
     "unclear"}
)
#: Which scope axis explains a non-comparable decision. A CLOSED checklist -- three
#: independent taxonomies converge on this list -- so it is a constrained enum the
#: strict parser can validate, not open-ended reasoning. Recorded per candidate;
#: feeds NO routing decision.
_SCOPE_MISMATCH_AXES = frozenset({
    "species_or_strain", "population_subgroup", "dose_or_duration",
    "route_or_administration", "endpoint_definition", "assay_or_study_design",
    "clinical_setting", "time_period_new_knowledge", "endogenous_vs_exogenous",
    "none", "unclear",
})
_COMPARABILITY = frozenset({"comparable", "not_comparable", "uncertain"})
_NOTICE_KIND = frozenset({"none", "retraction", "correction", "eoc"})
_NOTICE_RESOLUTION = frozenset({"resolved_clear", "flagged", "unresolved"})
# Did the metadata lookup ANSWER? `meta = fetch_meta(w) or {}` collapsed a
# transport failure and a record carrying no notice into one value, and both came
# back resolved_clear -- an outage reading as "not retracted". Same defect class
# this file already names and guards for retrieval.
_NOTICE_LOOKUP_STATUS = frozenset({"ok", "no_record", "not_performed"})
# WHY the as_of_date comparison did or did not happen. A notice whose date could
# not be compared is not a notice that was checked and cleared.
_NOTICE_DATE_STATUS = frozenset(
    {"not_applicable", "compared", "absent", "unparseable", "as_of_unavailable"})
# WHICH SIDE OF THE RETRACTION a publication type puts this work on. PubMed's
# "Retracted Publication" (this article WAS retracted) and "Retraction of
# Publication" / "Retraction Notice" (this article IS the notice) mean OPPOSITE
# things, and conflating them flags every notice while missing every retracted
# paper -- an inversion that still looks like a working detector. ``ncbi_meta``
# gets this right for F8; this vocabulary is how the F5 seam records the same
# distinction instead of discarding it.
_NOTICE_SOURCE_ROLE = frozenset(
    {"unknown", "retracted_article", "retraction_notice", "correction_notice",
     "eoc_notice", "no_notice_type"})
_ADEQUACY = frozenset({"adequate", "inadequate", "empty"})
_STATUS = frozenset({"ok", "failure", "partial"})
_ATTESTATION_TYPES = frozenset(
    {"major_guideline_revision", "systematic_review", "meta_analysis"})
_DISPOSITION = frozenset({"surface", "do_not_surface", "unassessable"})
_F5_PATH = frozenset({"A", "B", "not_F5", "unknown"})
_MODES = frozenset({"discovery", "deployment"})
_PATH_A_RULES = frozenset({"all_must_fire", "any_sufficient"})
_INDEPENDENCE = frozenset({"independent", "not_independent", "unknown"})

# Population relations that definitively support ``comparable_population``.
_POPULATION_COMPARABLE = frozenset({"equivalent", "encompassing_direct"})
# Hard population mismatches (blueprint Sec 18a.6 step 1).
_POPULATION_HARD_MISMATCH = frozenset({"narrower", "disjoint"})
# Population uncertainties (blueprint Sec 18a.6 step 2).
_POPULATION_UNCERTAIN = frozenset(
    {"encompassing_without_qualifying_direct_evidence", "unclear"})


# --------------------------------------------------------------------------
# Policy (blueprint Sec 13). Frozen at runtime. Any field whose value depends on
# an unfrozen Roberts lock is ``None`` -> the derivation using it fails closed.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class F5Policy:
    mode: str = "discovery"                       # discovery | deployment
    path_a_rule: str = "all_must_fire"            # all_must_fire | any_sufficient
    date_gap_years: float = 2.0                   # agreed (ZD 2026-07-17)
    tier_rule: Optional[str] = "equal_or_higher"  # agreed rule; mapping deferred
    require_attestation_for_path_a: bool = True
    attestation_types: frozenset = _ATTESTATION_TYPES
    independence_rule: Optional[str] = None       # Lock D combinator: UNFROZEN
    comparability_rule: Optional[str] = "v1"      # Sec 18a recommended v1
    confidence_floor: Optional[float] = 0.25      # discovery: low / high-recall
    eoc_caps_at_path_b: bool = True
    deploy_path_a: bool = False                   # LOCKED off in this build
    # v1 -> v2 (2026-08-12): the contradiction contract gained
    # ``scope_mismatch_axis``. A key-set change is exactly what this version
    # exists to signal, and the prompt text itself first shipped at v2.
    contradiction_prompt_version: str = F5_CONTRADICTION_PROMPT_VERSION
    comparability_policy_version: str = "f5_comparability_v1"
    generator_model_id: str = ""
    verifier_model_id: str = ""
    policy_version: str = "f5_policy_v1"


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"),
                   ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def validate_f5_policy(policy: F5Policy) -> None:
    """Fail-closed policy validation, run BEFORE any seam call."""
    if not isinstance(policy, F5Policy):
        raise TypeError("policy must be an F5Policy")
    if policy.mode not in _MODES:
        raise ValueError(f"policy.mode must be one of {sorted(_MODES)}")
    if policy.path_a_rule not in _PATH_A_RULES:
        raise ValueError(f"policy.path_a_rule must be one of {sorted(_PATH_A_RULES)}")
    if not isinstance(policy.date_gap_years, (int, float)) or isinstance(
            policy.date_gap_years, bool) or policy.date_gap_years < 0:
        raise ValueError("policy.date_gap_years must be a nonnegative number")
    if not isinstance(policy.attestation_types, (frozenset, set)) or not (
            set(policy.attestation_types) <= set(_ATTESTATION_TYPES)):
        raise ValueError(
            f"policy.attestation_types must be a subset of {sorted(_ATTESTATION_TYPES)}")
    if type(policy.require_attestation_for_path_a) is not bool:
        raise ValueError("policy.require_attestation_for_path_a must be a bool")
    if type(policy.eoc_caps_at_path_b) is not bool:
        raise ValueError("policy.eoc_caps_at_path_b must be a bool")
    if type(policy.deploy_path_a) is not bool:
        raise ValueError("policy.deploy_path_a must be a bool")
    # HARD GATE (blueprint Sec 13): this development-mode build runs under a hard
    # deploy_path_a=False. Path-A autonomous replacement is not derivable until the
    # Roberts advisor locks are frozen; enabling it here is rejected outright so a
    # mere policy flip can never deploy an irreversible replacement.
    if policy.deploy_path_a:
        raise ValueError(
            "deploy_path_a must be False in this build: Path-A autonomous "
            "replacement is hard-gated off until the Roberts advisor locks are "
            "frozen (blueprint Sec 13, Sec 21)")
    # The advisor-locked / not-yet-implemented policy knobs may ONLY hold their
    # single implemented value, so a stored record can never claim a configuration
    # the code did not actually apply (implementing an alternative would derive an
    # unfrozen advisor lock, which is out of scope). Reject anything else.
    if policy.path_a_rule != "all_must_fire":
        raise ValueError(
            "policy.path_a_rule: only 'all_must_fire' (the adopted conjunctive "
            "Rec A gate) is implemented; 'any_sufficient' is not derivable until "
            "Roberts freezes it")
    if policy.tier_rule != "equal_or_higher":
        raise ValueError(
            "policy.tier_rule: only 'equal_or_higher' is implemented (the tier "
            "MAPPING is deferred to the classify_evidence_tier seam)")
    if policy.independence_rule is not None:
        raise ValueError(
            "policy.independence_rule must be None: the Lock-D AND/OR combinator "
            "is unfrozen and no alternative is implemented (the detector fails "
            "closed at the combinator cell)")
    if policy.comparability_rule != "v1":
        raise ValueError(
            "policy.comparability_rule: only 'v1' (blueprint Sec 18a) is implemented")
    if policy.confidence_floor is not None and (
            not isinstance(policy.confidence_floor, (int, float))
            or isinstance(policy.confidence_floor, bool)
            or not 0.0 <= policy.confidence_floor <= 1.0):
        raise ValueError("policy.confidence_floor must be None or in [0, 1]")
    for name in ("contradiction_prompt_version", "comparability_policy_version",
                 "policy_version", "generator_model_id", "verifier_model_id"):
        if not isinstance(getattr(policy, name), str):
            raise ValueError(f"policy.{name} must be a string")
    for name in ("contradiction_prompt_version", "comparability_policy_version",
                 "policy_version"):
        if not getattr(policy, name).strip():
            raise ValueError(f"policy.{name} must be nonblank")
    if policy.contradiction_prompt_version != F5_CONTRADICTION_PROMPT_VERSION:
        raise ValueError(
            "policy.contradiction_prompt_version does not identify the prompt "
            f"this build can render ({F5_CONTRADICTION_PROMPT_VERSION!r})")


# --------------------------------------------------------------------------
# Injected-seam payload types (blueprint Sec 5). All are validated fail-closed.
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CandidateWork:
    id: str
    title: str = ""
    abstract: str = ""
    pub_date: str = ""             # ISO YYYY-MM-DD
    authors: tuple[str, ...] = ()
    mesh: tuple[str, ...] = ()
    tier_hint: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("CandidateWork.id must be a nonblank string")
        if not isinstance(self.pub_date, str) or not self.pub_date.strip():
            raise ValueError("CandidateWork.pub_date must be a nonblank ISO date")
        _parse_date(self.pub_date, "CandidateWork.pub_date")
        if not isinstance(self.authors, tuple):
            raise ValueError("CandidateWork.authors must be a tuple")
        if not isinstance(self.mesh, tuple):
            raise ValueError("CandidateWork.mesh must be a tuple")


@dataclass(frozen=True)
class RetrievalResult:
    candidates: tuple[CandidateWork, ...]
    adequacy: str                  # adequate | inadequate | empty
    status: str                    # ok | failure | partial
    query_hash: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, tuple) or any(
                not isinstance(c, CandidateWork) for c in self.candidates):
            raise ValueError("RetrievalResult.candidates must be a tuple of CandidateWork")
        if self.adequacy not in _ADEQUACY:
            raise ValueError(f"RetrievalResult.adequacy must be one of {sorted(_ADEQUACY)}")
        if self.status not in _STATUS:
            raise ValueError(f"RetrievalResult.status must be one of {sorted(_STATUS)}")
        # One work must appear at most ONCE. A duplicate id is assessed twice, and
        # two assessments of the same paper agreeing with each other reads as two
        # independent candidates agreeing -- inflating apparent agreement exactly
        # where the module is trying to measure it.
        ids = [c.id for c in self.candidates]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(
                f"RetrievalResult.candidates has duplicate work ids: {duplicates}")
        # Blueprint Sec 5: empty candidate list <=> adequacy=empty. An empty list
        # with any other adequacy (adequate OR inadequate), or adequacy=empty with
        # a nonempty list, is not a valid combination.
        if not self.candidates and self.adequacy != "empty":
            raise ValueError(
                "invalid RetrievalResult: an empty candidate list requires adequacy='empty' "
                f"(got adequacy={self.adequacy!r})")
        if self.candidates and self.adequacy == "empty":
            raise ValueError(
                "invalid RetrievalResult: adequacy='empty' with a nonempty candidate list")


@dataclass(frozen=True)
class ComparabilitySource:
    abstract: Optional[str] = None
    methods: Optional[str] = None
    results: Optional[str] = None
    protocol: Optional[str] = None
    registry_record: Optional[str] = None
    publication_type: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("abstract", "methods", "results", "protocol",
                     "registry_record", "publication_type"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"ComparabilitySource.{name} must be a string or None")


@dataclass(frozen=True)
class NoticeStatus:
    notice_kind: str = "none"          # none | retraction | correction | eoc
    notice_resolution: str = "resolved_clear"  # resolved_clear | flagged | unresolved
    date: Optional[str] = None
    # WHY this status says what it says. All defaulted, so every existing
    # construction is unchanged, and every one of them is honest about having
    # asserted nothing: a hand-built NoticeStatus did not perform a lookup.
    lookup_status: str = "not_performed"
    date_status: str = "not_applicable"
    # The notice date EXACTLY as the metadata gave it, never parsed. ``date``
    # above is contractually ISO and raises on anything else, so a malformed
    # value had nowhere to live and was discarded -- taking with it the only
    # evidence of why the timing gate did not run.
    date_raw: Optional[str] = None
    source_role: str = "unknown"

    def __post_init__(self) -> None:
        if self.notice_kind not in _NOTICE_KIND:
            raise ValueError(f"NoticeStatus.notice_kind must be one of {sorted(_NOTICE_KIND)}")
        if self.notice_resolution not in _NOTICE_RESOLUTION:
            raise ValueError(
                f"NoticeStatus.notice_resolution must be one of {sorted(_NOTICE_RESOLUTION)}")
        if self.lookup_status not in _NOTICE_LOOKUP_STATUS:
            raise ValueError(
                f"NoticeStatus.lookup_status must be one of {sorted(_NOTICE_LOOKUP_STATUS)}")
        if self.date_status not in _NOTICE_DATE_STATUS:
            raise ValueError(
                f"NoticeStatus.date_status must be one of {sorted(_NOTICE_DATE_STATUS)}")
        if self.source_role not in _NOTICE_SOURCE_ROLE:
            raise ValueError(
                f"NoticeStatus.source_role must be one of {sorted(_NOTICE_SOURCE_ROLE)}")
        if self.date_raw is not None and not isinstance(self.date_raw, str):
            raise ValueError("NoticeStatus.date_raw must be a string or None")
        if self.date is not None and not isinstance(self.date, str):
            raise ValueError("NoticeStatus.date must be a string or None")
        # PARSE it, do not merely type-check it. This date decides whether a notice
        # is in force at as_of_date -- Bakker et al. document papers being retracted
        # while reviews are in press -- so an unvalidated notice date silently
        # disables that comparison rather than failing closed.
        if self.date is not None:
            _parse_date(self.date, "NoticeStatus.date")


@dataclass(frozen=True)
class Attestation:
    attestation_type: str              # one of _ATTESTATION_TYPES
    source_id: str
    attestation_date: str              # ISO YYYY-MM-DD
    replacement_work_id: str           # binds attestation <-> replacement candidate
    attestation_conclusion_span: str   # verbatim reversal/supersession conclusion
    replacement_date: Optional[str] = None  # defaults to candidate pub_date
    # Attestation source text the conclusion span is validated against. REQUIRED
    # for Path-A eligibility (Sec 5 / Sec 10): the span must be verbatim in it. An
    # Attestation may be constructed without it, but then it can never gate Path A.
    source_text: Optional[str] = None

    def __post_init__(self) -> None:
        if self.attestation_type not in _ATTESTATION_TYPES:
            raise ValueError(
                f"Attestation.attestation_type must be one of {sorted(_ATTESTATION_TYPES)}")
        for name in ("source_id", "attestation_date", "replacement_work_id",
                     "attestation_conclusion_span"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Attestation.{name} must be a nonblank string")
        _parse_date(self.attestation_date, "Attestation.attestation_date")
        if self.replacement_date is not None:
            _parse_date(self.replacement_date, "Attestation.replacement_date")
        if self.source_text is not None and not isinstance(self.source_text, str):
            raise ValueError("Attestation.source_text must be a string or None")


@dataclass(frozen=True)
class ContradictionJudgment:
    """Parsed, validated contradiction judgment (blueprint Sec 5). The model
    emits the three relation axes + two directions + magnitude + two spans +
    confidence; CODE (not the model) derives ``comparability_decision`` and the
    frozen-engine booleans."""
    directional_contradiction: bool
    claim_match: str
    outcome_relation: str
    population_relation: str
    cited_direction: str
    candidate_direction: str
    magnitude: str
    cited_finding_span: str
    candidate_contradiction_span: str
    confidence: float
    #: Which scope axis explains a non-comparable decision (or ``none``). RECORDED
    #: only -- it feeds no routing decision, so a wrong axis cannot change a
    #: verdict, only the explanation attached to it.
    scope_mismatch_axis: str = "unclear"


# --------------------------------------------------------------------------
# Deterministic date + comparability + independence primitives.
# --------------------------------------------------------------------------
#: YYYY-MM-DD and nothing else. ``date.fromisoformat`` is WIDER than its own error
#: message here: on 3.11+ it also accepts ``20240101`` (basic format) and
#: ``2024-W01-1`` (ISO week dates), so the gate advertised YYYY-MM-DD while letting
#: two other calendars through. Dates decide the after_date window and whether a
#: retraction is in force at as_of_date, so a silently reinterpreted one is a
#: correctness bug, not a formatting nicety.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date(value: str, name: str) -> datetime.date:
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD date: {value!r}")
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD date: {value!r}") from exc


def _date_gap_years(earlier: str, later: str) -> float:
    days = (_parse_date(later, "date") - _parse_date(earlier, "date")).days
    return days / 365.25


def derive_comparability_decision(
    claim_match: str, outcome_relation: str, population_relation: str
) -> str:
    """The authoritative Sec 18a.6 deterministic combination. A hard mismatch
    dominates uncertainty (step 1 before step 2)."""
    if claim_match not in _CLAIM_MATCH:
        raise ValueError(f"claim_match must be one of {sorted(_CLAIM_MATCH)}")
    if outcome_relation not in _OUTCOME_RELATION:
        raise ValueError(f"outcome_relation must be one of {sorted(_OUTCOME_RELATION)}")
    if population_relation not in _POPULATION_RELATION:
        raise ValueError(
            f"population_relation must be one of {sorted(_POPULATION_RELATION)}")
    # 1. Any hard mismatch -> not_comparable.
    if (claim_match == "mismatch" or outcome_relation == "not_same"
            or population_relation in _POPULATION_HARD_MISMATCH):
        return "not_comparable"
    # 2. Otherwise, any uncertainty -> uncertain.
    if (claim_match == "uncertain" or outcome_relation == "uncertain"
            or population_relation in _POPULATION_UNCERTAIN):
        return "uncertain"
    # 3. Otherwise -> comparable.
    return "comparable"


def _norm_authors(authors: Any) -> Optional[frozenset]:
    """Normalized author-name set, or None when author info is absent (unknown)."""
    if authors is None:
        return None
    if isinstance(authors, (str, bytes)):
        return None
    try:
        names = frozenset(
            a.strip().casefold() for a in authors
            if isinstance(a, str) and a.strip())
    except TypeError:
        return None
    return names or None


def _assess_independence(cited_meta: dict, candidate: CandidateWork) -> tuple[str, str]:
    """Authorship/cohort-based independence (blueprint Sec 6-D, Sec 9-6, Sec 9-26).

    The Lock-D AND/OR combinator (how author-overlap and data-source signals
    combine) is UNFROZEN, so this fails closed exactly where the combinator would
    decide: a confirmed same-cohort re-analysis is definitively NOT independent;
    disjoint authorship (no shared authors, both known) is definitively
    independent; author overlap or missing author info -> ``unknown`` (queued for
    a human in discovery mode). Returns ``(independence, basis)``.
    """
    same_cohort_ids = cited_meta.get("same_cohort_work_ids")
    if same_cohort_ids is not None:
        # Accept ANY iterable of work ids (list/tuple/set/frozenset/dict_keys/
        # generator/...) via duck-typed membership so a confirmed same-cohort
        # re-analysis can never fail OPEN to "independent" through a type the
        # guard failed to whitelist. A string or a non-iterable is a malformed
        # cited_meta payload -> fail closed (quarantine), never silently ignored.
        if isinstance(same_cohort_ids, (str, bytes)):
            raise ValueError(
                "cited_meta['same_cohort_work_ids'] must be an iterable of work ids, "
                "not a string")
        try:
            is_same_cohort = candidate.id in same_cohort_ids
        except TypeError as exc:
            raise ValueError(
                "cited_meta['same_cohort_work_ids'] must be an iterable of work ids"
            ) from exc
        if is_same_cohort:
            return "not_independent", "same_cohort_reanalysis"
    cited_authors = _norm_authors(cited_meta.get("authors"))
    cand_authors = _norm_authors(candidate.authors)
    if cited_authors is None or cand_authors is None:
        return "unknown", "author_info_missing"
    if cited_authors & cand_authors:
        # Author overlap WITHOUT a confirmed shared cohort is exactly the open
        # AND/OR combinator cell -> fail closed to unknown until Lock D freezes.
        return "unknown", "author_overlap_open_combinator"
    return "independent", "disjoint_authorship"


# --------------------------------------------------------------------------
# Strict-JSON parsing of the contradiction judgment (mirrors the frozen
# band_prompts pattern; replicated so this module is a self-contained leaf).
# --------------------------------------------------------------------------
def _reject_duplicate_keys(pairs) -> dict:
    obj: dict = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError(f"duplicate JSON key: {key}")
        obj[key] = value
    return obj


_CONTRADICTION_KEYS = frozenset(
    {"directional_contradiction", "claim_match", "outcome_relation",
     "population_relation", "cited_direction", "candidate_direction", "magnitude",
     "cited_finding_span", "candidate_contradiction_span", "confidence",
     # Eleventh key (2026-08-12). The module already ROUTES scope mismatch
     # correctly -- population_relation -> derive_comparability_decision ->
     # not_comparable -> non-qualifying -- but nothing recorded WHICH axis fired,
     # so a run could not answer "why did this pair not qualify", the one question
     # the annotation queue exists to support. Rosemblat et al. 2019 (PMID
     # 31473364) funnelled 2,236 candidate pairs to 58 apparent and 4 genuine with
     # 42.6% lost to generic subjects; the axis makes that funnel auditable.
     # It is carried on the CONTRACT rather than derived, because the axis list
     # distinguishes species from dose from endpoint and population_relation's
     # four values cannot encode that -- only the reading that produced the
     # judgment knows it. Adding it bumps contradiction_prompt_version off
     # f5_contradiction_v1, per the version's own purpose.
     "scope_mismatch_axis"}
)


def _parse_contradiction(text: str) -> ContradictionJudgment:
    """Strict-JSON -> validated ContradictionJudgment. Malformed / off-enum /
    out-of-range fails closed (``ValueError`` -> quarantine)."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty contradiction-judgment output")
    try:
        obj = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"contradiction judgment is not one bare JSON object: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError(f"top-level JSON must be an object: {type(obj).__name__}")
    keys = frozenset(obj)
    if keys != _CONTRADICTION_KEYS:
        raise ValueError(
            "contradiction keys mismatch: "
            f"missing={sorted(_CONTRADICTION_KEYS - keys)} "
            f"extra={sorted(keys - _CONTRADICTION_KEYS)}")
    directional = obj["directional_contradiction"]
    if type(directional) is not bool:  # bool subclasses int; require an actual JSON bool
        raise ValueError("directional_contradiction must be an actual JSON boolean")
    claim_match = obj["claim_match"]
    if claim_match not in _CLAIM_MATCH:
        raise ValueError(f"claim_match must be one of {sorted(_CLAIM_MATCH)}")
    outcome_relation = obj["outcome_relation"]
    if outcome_relation not in _OUTCOME_RELATION:
        raise ValueError(f"outcome_relation must be one of {sorted(_OUTCOME_RELATION)}")
    population_relation = obj["population_relation"]
    if population_relation not in _POPULATION_RELATION:
        raise ValueError(
            f"population_relation must be one of {sorted(_POPULATION_RELATION)}")
    for name in ("cited_direction", "candidate_direction", "magnitude",
                 "cited_finding_span", "candidate_contradiction_span"):
        if not isinstance(obj[name], str):
            raise ValueError(f"{name} must be a string")
    confidence = obj["confidence"]
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    scope_axis = obj["scope_mismatch_axis"]
    if scope_axis not in _SCOPE_MISMATCH_AXES:
        raise ValueError(
            f"scope_mismatch_axis must be one of {sorted(_SCOPE_MISMATCH_AXES)}")
    return ContradictionJudgment(
        directional_contradiction=directional,
        claim_match=claim_match,
        outcome_relation=outcome_relation,
        population_relation=population_relation,
        cited_direction=obj["cited_direction"],
        candidate_direction=obj["candidate_direction"],
        magnitude=obj["magnitude"],
        cited_finding_span=obj["cited_finding_span"],
        candidate_contradiction_span=obj["candidate_contradiction_span"],
        confidence=float(confidence),
        scope_mismatch_axis=scope_axis,
    )


def _source_text(src: ComparabilitySource) -> str:
    """Concatenated evidence text used for verbatim span verification (blueprint
    Sec 5: the abstract/full-text within the bundle serves the span check)."""
    parts = [src.abstract, src.methods, src.results, src.protocol, src.registry_record]
    return "\n".join(p for p in parts if isinstance(p, str) and p)


def _tier_from(value: object, name: str) -> EvidenceTier:
    if isinstance(value, EvidenceTier):
        return value
    if isinstance(value, str):
        try:
            return EvidenceTier(value)
        except ValueError:
            raise ValueError(
                f"{name} is not a valid EvidenceTier: {value!r}") from None
    raise ValueError(f"{name} must be an EvidenceTier or its string value")


# --------------------------------------------------------------------------
# Record hashing (mirrors f4_strength / f7_entity).
# --------------------------------------------------------------------------
def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_sha256(record: dict) -> str:
    body = {k: v for k, v in record.items() if k != "record_sha256"}
    return _canonical_sha256(body)


# Per-candidate outcome categories (internal).
_QUALIFYING = "QUALIFYING"
_HARD_NONQUALIFYING = "HARD_NONQUALIFYING"   # judgeable + clearly nonqualifying
_UNASSESSABLE = "UNASSESSABLE"               # could not judge (blocks negative)
_BORDERLINE = "BORDERLINE"                   # ordinary uncertainty (blocks negative)


@dataclass
class _CandResult:
    assessment: dict
    category: str
    candidate: CandidateWork


class TemporalAssessorRun:
    """Callable temporal assessor. ``__call__(claims, support)`` returns ONE frozen
    ``TemporalAssessment`` for ``decide_judgment``; ``.records`` carries the rich
    per-claim ``F5Record`` audit dicts (indexed in claim order)."""

    def __init__(self, *, retrieve_superseding_candidates, fetch_comparability_source,
                 check_formal_notice, classify_evidence_tier,
                 find_supersession_attestation, judge_contradiction, evidence: dict,
                 policy: F5Policy):
        self.retrieve = retrieve_superseding_candidates
        self.fetch_source = fetch_comparability_source
        self.check_notice = check_formal_notice
        self.classify_tier = classify_evidence_tier
        self.find_attestation = find_supersession_attestation
        self.judge = judge_contradiction
        self.evidence = evidence
        self.policy = policy
        self.records: list[dict] = []

    def _claim_meta(self, claim_index: int) -> dict:
        """Read either in-memory integer keys or JSON-round-tripped string keys."""
        rows = self.evidence.get("claim_meta") or {}
        integer_value = rows.get(claim_index)
        string_value = rows.get(str(claim_index))
        if integer_value is not None and string_value is not None:
            if integer_value != string_value:
                raise ValueError(
                    "evidence['claim_meta'] has conflicting integer/string keys "
                    f"for claim {claim_index}")
            return dict(integer_value)
        value = integer_value if integer_value is not None else string_value
        return dict(value) if value is not None else {}

    # -- helpers ------------------------------------------------------------
    def _cited_tier(self, cited_meta: dict) -> EvidenceTier:
        return _tier_from(self.classify_tier(cited_meta), "cited tier")

    def _candidate_tier(self, candidate: CandidateWork) -> EvidenceTier:
        meta = {
            "work_id": candidate.id,
            "title": candidate.title,
            "pub_date": candidate.pub_date,
            "mesh": list(candidate.mesh),
            "tier_hint": candidate.tier_hint,
        }
        return _tier_from(self.classify_tier(meta), "candidate tier")

    # -- per-candidate ------------------------------------------------------
    def _assess_candidate(self, *, claim: str, cited_work_id: str, cited_meta: dict,
                          cited_date: str, as_of_date: str, cited_source: ComparabilitySource,
                          cited_tier: EvidenceTier, cited_eoc_caps: bool,
                          candidate: CandidateWork) -> _CandResult:
        policy = self.policy
        cand = {
            "candidate_work_id": candidate.id,
            "candidate_date": candidate.pub_date,
            "candidate_tier": None,
            "candidate_notice_kind": None,
            "candidate_notice_resolution": None,
            "claim_match": None,
            "outcome_relation": None,
            "population_relation": None,
            "comparability_decision": None,
            "independent": None,
            "independence_basis": None,
            "directional_contradiction": None,
            "cited_direction": None,
            "candidate_direction": None,
            "contradiction_magnitude": None,
            "date_gap_years": None,
            "tier_relation": None,
            "confidence": None,
            "cited_finding_span": None,
            "candidate_contradiction_span": None,
            "discovery_disposition": None,
            "attestation": "none",
            "attestation_source_id": None,
            "attestation_date": None,
            "attestation_replacement_work_id": None,
            "attestation_conclusion_span": None,
            # ``failed_replication_evidence`` was here, initialised False and never
            # written or read anywhere in either repo. REMOVED 2026-08-12 rather
            # than kept: nothing computes it, so a hard False asserted "we looked
            # for failed-replication evidence and found none" when nothing had
            # looked. Every sibling field is either genuinely written or None for
            # unknown. If the signal is wanted it returns with the code that
            # produces it.
            "path_a_eligible": False,
            "criteria_fired": [],
            "reason": None,
            "contradiction_response": None,
            "contradiction_response_sha256": None,
        }

        def finish(category: str, reason: str, disposition: str) -> _CandResult:
            cand["reason"] = reason
            cand["discovery_disposition"] = disposition
            return _CandResult(assessment=cand, category=category, candidate=candidate)

        # Date window: cited_date < candidate_date <= as_of_date (blueprint Sec 5,
        # Sec 9-19). A predating or post-cutoff candidate is a clear negative.
        cand_date = _parse_date(candidate.pub_date, "candidate.pub_date")
        if cand_date <= _parse_date(cited_date, "cited_date"):
            return finish(_HARD_NONQUALIFYING, "candidate_predates_cited", "do_not_surface")
        if cand_date > _parse_date(as_of_date, "as_of_date"):
            return finish(_HARD_NONQUALIFYING, "candidate_after_as_of_date", "do_not_surface")
        cand["date_gap_years"] = _date_gap_years(cited_date, candidate.pub_date)

        # Candidate formal notice: any notice / non-clear resolution disqualifies
        # it as a replacement and makes it an unjudgeable audit row (Sec 9-12).
        cand_notice = self.check_notice(candidate.id, as_of_date=as_of_date)
        if not isinstance(cand_notice, NoticeStatus):
            raise ValueError("check_formal_notice must return a NoticeStatus")
        cand["candidate_notice_kind"] = cand_notice.notice_kind
        cand["candidate_notice_resolution"] = cand_notice.notice_resolution
        if cand_notice.notice_kind != "none" or cand_notice.notice_resolution != "resolved_clear":
            return finish(_UNASSESSABLE, "candidate_flagged_notice", "unassessable")

        cand["candidate_tier"] = self._candidate_tier(candidate).value

        # Contradiction judgment (strict-JSON; malformed -> ValueError quarantine).
        candidate_source = self.fetch_source(candidate.id, as_of_date=as_of_date)
        if not isinstance(candidate_source, ComparabilitySource):
            raise ValueError("fetch_comparability_source must return a ComparabilitySource")
        raw = self.judge(cited_source, candidate_source, claim)
        judgment = _parse_contradiction(raw)
        cand["contradiction_response"] = raw
        cand["contradiction_response_sha256"] = _sha256_text(raw)
        cand["claim_match"] = judgment.claim_match
        cand["outcome_relation"] = judgment.outcome_relation
        cand["population_relation"] = judgment.population_relation
        cand["directional_contradiction"] = judgment.directional_contradiction
        cand["cited_direction"] = judgment.cited_direction
        cand["candidate_direction"] = judgment.candidate_direction
        cand["contradiction_magnitude"] = judgment.magnitude
        cand["confidence"] = judgment.confidence
        cand["cited_finding_span"] = judgment.cited_finding_span
        cand["candidate_contradiction_span"] = judgment.candidate_contradiction_span

        comparability = derive_comparability_decision(
            judgment.claim_match, judgment.outcome_relation, judgment.population_relation)
        cand["comparability_decision"] = comparability
        # WHY this pair did not qualify, recorded next to the decision it explains.
        # Read-only: no branch below reads it, so a wrong axis can change the
        # explanation attached to a routing decision but never the decision.
        cand["scope_mismatch_axis"] = judgment.scope_mismatch_axis

        # Verbatim span verification (each span against its OWN source; Sec 9-17).
        # Both spans must be NONBLANK (a blank/whitespace-only span would satisfy
        # the substring test yet fail the frozen engine's nonblank _string_tuple
        # guard on evidence_spans -- hold as unverifiable instead of building an
        # assessment that trips DiscriminatorContractError).
        cited_text = _source_text(cited_source)
        cand_text = _source_text(candidate_source)
        if (not judgment.cited_finding_span.strip()
                or judgment.cited_finding_span not in cited_text
                or not judgment.candidate_contradiction_span.strip()
                or judgment.candidate_contradiction_span not in cand_text):
            return finish(_UNASSESSABLE, "span_unverifiable", "unassessable")

        # Independence (authorship/cohort; Lock D combinator UNFROZEN -> fail
        # closed at the combinator cell).
        independence, basis = _assess_independence(cited_meta, candidate)
        cand["independent"] = independence
        cand["independence_basis"] = basis

        # Hard-nonqualifying clear negatives (do_not_surface).
        if judgment.directional_contradiction is not True:
            return finish(_HARD_NONQUALIFYING, "not_directional_contradiction", "do_not_surface")
        if comparability == "not_comparable":
            return finish(_HARD_NONQUALIFYING, "not_comparable", "do_not_surface")
        if independence == "not_independent":
            return finish(_HARD_NONQUALIFYING, "not_independent", "do_not_surface")
        floor = policy.confidence_floor
        if floor is None:
            # Confidence floor is an unfrozen deployment lock -> fail closed.
            return finish(_BORDERLINE, "confidence_floor_unfrozen", "surface")
        if judgment.confidence < floor:
            # Discovery (Sec 8a) treats a below-a-low-floor confidence as a clear
            # negative for that candidate (do_not_surface); deployment's
            # precision-first posture (Sec 9-22) HOLDS low confidence as a
            # borderline UNJUDGEABLE instead of a confident negative.
            if policy.mode == "deployment":
                return finish(_BORDERLINE, "below_confidence_floor", "surface")
            return finish(_HARD_NONQUALIFYING, "below_confidence_floor", "do_not_surface")

        # Ordinary uncertainty (borderline; blocks a confident negative; may surface).
        if comparability == "uncertain":
            return finish(_BORDERLINE, "comparability_uncertain", "surface")
        if independence == "unknown":
            return finish(_BORDERLINE, "independence_unknown", "surface")

        # QUALIFYING: full detector contract passes. Both engine booleans are
        # definitively positive here (comparability == comparable implies
        # claim_match=match, outcome=same, population in {equivalent,
        # encompassing_direct}); assert to guard against drift.
        same_claim_or_outcome = (
            judgment.claim_match == "match" and judgment.outcome_relation == "same")
        comparable_population = judgment.population_relation in _POPULATION_COMPARABLE
        if not (same_claim_or_outcome and comparable_population):
            raise ValueError(
                "internal invariant: comparable decision without both engine booleans positive")

        criteria: list[str] = ["directional_contradiction", "comparable", "independent",
                               "spans_verbatim", "confidence_ok", "notice_clear"]

        # Path-A eligibility (hypothetical; deferred). Never deployed while
        # deploy_path_a=False. A cited correction/EoC caps at Path B (Sec 9-21).
        tier_ok = _TIER_RANK[self._candidate_tier(candidate)] >= _TIER_RANK[cited_tier]
        cand["tier_relation"] = (
            "higher_or_equal" if tier_ok else "lower")
        gap_ok = cand["date_gap_years"] >= policy.date_gap_years
        path_a_eligible = False
        if not cited_eoc_caps and policy.require_attestation_for_path_a:
            attestation = self.find_attestation(
                cited_meta, claim, candidate.id, as_of_date=as_of_date)
            if attestation is not None:
                if not isinstance(attestation, Attestation):
                    raise ValueError(
                        "find_supersession_attestation must return an Attestation or None")
                cand["attestation"] = attestation.attestation_type
                cand["attestation_source_id"] = attestation.source_id
                cand["attestation_date"] = attestation.attestation_date
                cand["attestation_replacement_work_id"] = attestation.replacement_work_id
                cand["attestation_conclusion_span"] = attestation.attestation_conclusion_span
                if self._attestation_valid(attestation, candidate, as_of_date):
                    if tier_ok and gap_ok:
                        path_a_eligible = True
                        criteria.extend(["attestation_bound", "tier_gate", "date_gap"])
        cand["path_a_eligible"] = path_a_eligible
        cand["criteria_fired"] = criteria
        return finish(_QUALIFYING, "qualifying_contradiction", "surface")

    def _attestation_valid(self, attestation: Attestation, candidate: CandidateWork,
                           as_of_date: str) -> bool:
        """Bound, admissibly-typed, temporally-bounded, span-verified attestation."""
        if attestation.attestation_type not in self.policy.attestation_types:
            return False
        if attestation.replacement_work_id != candidate.id:
            return False
        # Sec 6-I: attestation<->replacement coincidence (same document) is
        # admissible ONLY for a systematic review / meta-analysis -- never a
        # guideline. A guideline that IS the replacement cannot gate Path A.
        if (attestation.source_id == candidate.id
                and attestation.attestation_type == "major_guideline_revision"):
            return False
        replacement_date = attestation.replacement_date or candidate.pub_date
        rep = _parse_date(replacement_date, "replacement_date")
        att = _parse_date(attestation.attestation_date, "attestation_date")
        aod = _parse_date(as_of_date, "as_of_date")
        # replacement_date <= attestation_date <= as_of_date (on-or-after; Sec 6-I).
        if not (rep <= att <= aod):
            return False
        # Sec 5 / Sec 10: a verbatim attestation_conclusion_span that explicitly
        # concludes reversal/supersession is REQUIRED for EVERY attestation and
        # must be validated against the attestation source (fetched as of
        # as_of_date). No source text to verify against, or a non-verbatim span,
        # cannot gate Path A -> fail closed.
        if not attestation.attestation_conclusion_span.strip():
            return False
        if not (isinstance(attestation.source_text, str)
                and attestation.source_text.strip()):
            return False
        if attestation.attestation_conclusion_span not in attestation.source_text:
            return False
        return True

    # -- per-claim ----------------------------------------------------------
    def _assess_claim(self, claim: str, claim_index: int, activation) -> dict:
        policy = self.policy
        cited_work_id = self.evidence["cited_work_id"]
        cited_meta = self.evidence["cited_meta"]
        cited_date = self.evidence["cited_date"]
        as_of_date = self.evidence["as_of_date"]
        claim_meta = self._claim_meta(claim_index)

        record: dict = {
            "claim_index": claim_index,
            "claim_text": claim,
            "activation": activation.to_dict(),
            "claim_population_text": claim_meta.get("claim_population_text"),
            "intervention_or_exposure": claim_meta.get("intervention_or_exposure"),
            "comparator": claim_meta.get("comparator"),
            "cited_work_id": cited_work_id,
            "cited_date": cited_date,
            "as_of_date": as_of_date,
            "cited_tier": None,
            "cited_notice_kind": None,
            "cited_notice_resolution": None,
            "cited_eoc_caps": False,
            "retrieval_adequacy": None,
            "retrieval_status": None,
            "retrieval_query_hash": None,
            "candidate_assessments": [],
            "selected_contradiction_work_id": None,
            "selected_replacement_work_id": None,
            "selected_surfaced_candidate_work_id": None,
            "discovery_confidence": None,
            "same_claim_or_outcome": None,
            "comparable_population": None,
            "cited_finding_span": None,
            "candidate_contradiction_span": None,
            "confidence": None,
            "path_a_eligible": False,
            "path_a_deployed": False,
            "discovery_disposition": None,
            "f5_path": "unknown",
            "temporal_state": None,
            "reason": None,
            "assessed": True,
            "mode": policy.mode,
            "model_version": policy.generator_model_id,
            "f5_policy_version": policy.policy_version,
            "comparability_policy_version": policy.comparability_policy_version,
            "contradiction_prompt_version": policy.contradiction_prompt_version,
            "response_parser_version": F5_RESPONSE_PARSER_VERSION,
            "verifier_result": "not_run",
            "verifier_model_version": policy.verifier_model_id,
            "verifier_evidence_hash": None,
            "reportable": F5_REPORTABLE,
        }

        def finalize(temporal_state: str, reason: str) -> dict:
            record["temporal_state"] = temporal_state
            record["reason"] = reason
            record["f5_path"] = _derive_f5_path(
                temporal_state=temporal_state,
                path_a_eligible=record["path_a_eligible"],
                cited_eoc_caps=record["cited_eoc_caps"],
                deploy_path_a=policy.deploy_path_a,
            )
            record["record_sha256"] = record_sha256(record)
            return record

        # Cited-work formal notice (Sec 9-20 / Sec 9-21).
        cited_notice = self.check_notice(cited_work_id, as_of_date=as_of_date)
        if not isinstance(cited_notice, NoticeStatus):
            raise ValueError("check_formal_notice must return a NoticeStatus")
        record["cited_notice_kind"] = cited_notice.notice_kind
        record["cited_notice_resolution"] = cited_notice.notice_resolution
        if cited_notice.notice_kind == "retraction":
            # An F8 that should have been removed upstream: refuse F5, flag the
            # routing inconsistency, hold. (Data precondition, not an F5/F8 boundary.)
            record["discovery_disposition"] = "unassessable"
            return finalize("UNJUDGEABLE", "cited_retracted_upstream_f8_inconsistency")
        cited_eoc_caps = (
            cited_notice.notice_kind in ("correction", "eoc") and policy.eoc_caps_at_path_b)
        record["cited_eoc_caps"] = cited_eoc_caps

        cited_tier = self._cited_tier(cited_meta)
        record["cited_tier"] = cited_tier.value

        # Retrieval. ``cited_work_id`` is an evidence-level field, while the
        # retrieval seam historically received only ``cited_meta``.  A live
        # finder therefore could not run its forward-citation stream unless a
        # caller happened to duplicate the PMID inside the metadata dict.  Make
        # the identifier explicit at the seam boundary without mutating the
        # caller's evidence object.  A conflicting metadata value is replaced by
        # the validated evidence-level identifier; there is one cited work for
        # this assessment, not two competing authorities.
        retrieval_meta = dict(cited_meta)
        retrieval_meta["cited_work_id"] = cited_work_id
        result = self.retrieve(
            retrieval_meta, claim, after_date=cited_date, as_of_date=as_of_date)
        if not isinstance(result, RetrievalResult):
            raise ValueError("retrieve_superseding_candidates must return a RetrievalResult")
        record["retrieval_adequacy"] = result.adequacy
        record["retrieval_status"] = result.status
        record["retrieval_query_hash"] = result.query_hash

        cited_source = self.fetch_source(cited_work_id, as_of_date=as_of_date)
        if not isinstance(cited_source, ComparabilitySource):
            raise ValueError("fetch_comparability_source must return a ComparabilitySource")

        cand_results: list[_CandResult] = []
        for candidate in result.candidates:
            cand_results.append(self._assess_candidate(
                claim=claim, cited_work_id=cited_work_id, cited_meta=cited_meta,
                cited_date=cited_date, as_of_date=as_of_date, cited_source=cited_source,
                cited_tier=cited_tier, cited_eoc_caps=cited_eoc_caps, candidate=candidate))
        record["candidate_assessments"] = [cr.assessment for cr in cand_results]

        # Claim-level discovery rollup (Sec 10 rollups): surface if any candidate
        # surfaces; else unassessable if RETRIEVAL is unassessable (not fully
        # adequate: empty / inadequate / failed / partial) OR any candidate is
        # unassessable; else do_not_surface (retrieval was adequate and every
        # candidate was a clear negative).
        retrieval_fully_adequate = (
            result.status == "ok" and result.adequacy == "adequate"
            and bool(result.candidates))
        surfaced = [cr for cr in cand_results
                    if cr.assessment["discovery_disposition"] == "surface"]
        if surfaced:
            record["discovery_disposition"] = "surface"
            chosen = min(
                surfaced,
                key=lambda cr: (-(cr.assessment["confidence"] or 0.0),
                                cr.candidate.id))
            record["selected_surfaced_candidate_work_id"] = chosen.candidate.id
            record["discovery_confidence"] = chosen.assessment["confidence"]
        elif (not retrieval_fully_adequate) or any(
                cr.assessment["discovery_disposition"] == "unassessable"
                for cr in cand_results):
            record["discovery_disposition"] = "unassessable"
        else:
            record["discovery_disposition"] = "do_not_surface"

        qualifying = [cr for cr in cand_results if cr.category == _QUALIFYING]
        if qualifying:
            rep = self._select_representative(qualifying)
            ra = rep.assessment
            record["selected_contradiction_work_id"] = rep.candidate.id
            record["same_claim_or_outcome"] = True
            record["comparable_population"] = True
            record["cited_finding_span"] = ra["cited_finding_span"]
            record["candidate_contradiction_span"] = ra["candidate_contradiction_span"]
            record["confidence"] = ra["confidence"]
            eligible = [cr for cr in qualifying if cr.assessment["path_a_eligible"]]
            if eligible and not cited_eoc_caps:
                record["path_a_eligible"] = True
                replacement = self._select_representative(eligible)
                record["selected_replacement_work_id"] = replacement.candidate.id
            record["path_a_deployed"] = bool(
                record["path_a_eligible"] and policy.deploy_path_a)
            return finalize("QUALIFYING_CONTRADICTION", "qualifying_contradiction")

        # No qualifying candidate. A confident negative needs an adequate,
        # nonempty, fully judgeable set with every candidate nonqualifying.
        blocked = any(cr.category in (_UNASSESSABLE, _BORDERLINE) for cr in cand_results)
        confident_negative = (
            result.status == "ok" and result.adequacy == "adequate"
            and bool(cand_results) and not blocked)
        if confident_negative:
            return finalize("NO_QUALIFYING_CONTRADICTION", "all_candidates_nonqualifying")
        return finalize("UNJUDGEABLE", _held_reason(result, blocked))

    def _select_representative(self, results: list[_CandResult]) -> _CandResult:
        """Deterministic pick: highest OCEBM tier -> most recent -> stable work_id.
        (The larger-n selection preference is a Roberts-deferred lock and is
        skipped until ratified.)"""
        def key(cr: _CandResult):
            tier_rank = _TIER_RANK[_tier_from(cr.assessment["candidate_tier"], "tier")]
            date = _parse_date(cr.candidate.pub_date, "pub_date")
            return (-tier_rank, -date.toordinal(), cr.candidate.id)
        return min(results, key=key)

    # -- public entry -------------------------------------------------------
    def __call__(self, claims, support) -> TemporalAssessment:
        claim_values, support_rows = _validate_inputs(claims, support)
        self.records = []
        chosen: Optional[TemporalAssessment] = None
        any_unjudgeable = False
        any_assessed = False
        any_not_applicable = False
        for index, (claim, row) in enumerate(zip(claim_values, support_rows)):
            if row.state is not SupportState.SUPPORTED:
                # SUPPORTED-only F5 target (Rec D). Passthrough, not assessed.
                self.records.append({"claim_index": index, "assessed": False})
                continue
            activation = decide_f5_activation(claim, self._claim_meta(index))
            if not activation.activates:
                any_not_applicable = True
                record = {
                    "claim_index": index,
                    "claim_text": claim,
                    "assessed": False,
                    "not_applicable": True,
                    "activation": activation.to_dict(),
                    "temporal_state": None,
                    "reason": f"not_applicable:{activation.reason_code}",
                    "reportable": F5_REPORTABLE,
                }
                record["record_sha256"] = record_sha256(record)
                self.records.append(record)
                continue
            any_assessed = True
            record = self._assess_claim(claim, index, activation)
            self.records.append(record)
            state = record["temporal_state"]
            if state == "QUALIFYING_CONTRADICTION" and chosen is None:
                chosen = TemporalAssessment(
                    state=TemporalState.QUALIFYING_CONTRADICTION,
                    claim_index=index,
                    newer_work_id=record["selected_contradiction_work_id"],
                    same_claim_or_outcome=True,
                    comparable_population=True,
                    f8_notice=False,
                    evidence_spans=(record["cited_finding_span"],
                                    record["candidate_contradiction_span"]),
                    rationale=record["reason"],
                )
            elif state == "UNJUDGEABLE":
                any_unjudgeable = True
        if chosen is not None:
            return chosen
        if any_unjudgeable:
            return TemporalAssessment(
                state=TemporalState.UNJUDGEABLE,
                rationale="at least one supported claim held for temporal review")
        if not any_assessed and any_not_applicable:
            # The frozen engine has no NOT_APPLICABLE temporal state.  Its neutral
            # carrier is NO_QUALIFYING_CONTRADICTION, while the durable records and
            # manifest explicitly distinguish this from a searched negative.
            return TemporalAssessment(
                state=TemporalState.NO_QUALIFYING_CONTRADICTION,
                rationale="no F5-applicable supported claim")
        return TemporalAssessment(
            state=TemporalState.NO_QUALIFYING_CONTRADICTION,
            rationale="no qualifying temporal contradiction on any supported claim")


def _derive_f5_path(*, temporal_state: str, path_a_eligible: bool,
                    cited_eoc_caps: bool, deploy_path_a: bool) -> str:
    if temporal_state == "QUALIFYING_CONTRADICTION":
        if cited_eoc_caps:
            return "B"
        # The "A" branch is UNREACHABLE in this build: validate_f5_policy rejects
        # deploy_path_a=True (hard gate, Sec 13). It is retained so the route logic
        # is already correct for the future frozen-lock build, and as defense in
        # depth for the validate_f5_record replay guard.
        if path_a_eligible and deploy_path_a:
            return "A"
        return "B"
    if temporal_state == "NO_QUALIFYING_CONTRADICTION":
        return "not_F5"
    return "unknown"


def _held_reason(result: RetrievalResult, blocked: bool) -> str:
    if result.status == "failure":
        return "retrieval_failure"
    if result.status == "partial":
        return "retrieval_partial"
    if result.adequacy == "empty" or not result.candidates:
        return "retrieval_empty"
    if result.adequacy == "inadequate":
        return "retrieval_inadequate"
    if blocked:
        return "candidate_set_not_fully_judgeable"
    return "held"


def _validate_inputs(claims, support) -> tuple:
    """Fail-closed input validation, complete BEFORE any seam call."""
    if isinstance(claims, (str, bytes)):
        raise ValueError("claims must be a sequence of claim strings, not a single string")
    claim_values = tuple(claims)
    for claim in claim_values:
        if not isinstance(claim, str) or not claim.strip():
            raise ValueError("every claim must be a nonblank string")
    support_rows = tuple(support)
    if len(support_rows) != len(claim_values):
        raise ValueError("support must have exactly one row per claim")
    for index, row in enumerate(support_rows):
        if not isinstance(row, ClaimSupport):
            raise ValueError("support rows must be ClaimSupport objects")
        if row.claim_index != index:
            raise ValueError("support rows must be in claim order with matching indices")
    return claim_values, support_rows


def _validate_evidence(evidence: dict) -> None:
    if not isinstance(evidence, dict):
        raise ValueError("evidence must be a dict")
    for key in ("cited_work_id", "cited_date", "as_of_date"):
        value = evidence.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"evidence['{key}'] must be a nonblank string")
    cited_meta = evidence.get("cited_meta")
    if not isinstance(cited_meta, dict):
        raise ValueError("evidence['cited_meta'] must be a dict")
    cited = _parse_date(evidence["cited_date"], "evidence['cited_date']")
    aod = _parse_date(evidence["as_of_date"], "evidence['as_of_date']")
    if cited >= aod:
        raise ValueError("evidence: cited_date must be strictly before as_of_date")
    claim_meta = evidence.get("claim_meta", {})
    if not isinstance(claim_meta, dict):
        raise ValueError("evidence['claim_meta'] must be a dict when supplied")
    for key, value in claim_meta.items():
        if not ((isinstance(key, int) and not isinstance(key, bool) and key >= 0)
                or (isinstance(key, str) and key.isdigit())):
            raise ValueError(
                "evidence['claim_meta'] keys must be nonnegative claim indices")
        if not isinstance(value, dict):
            raise ValueError("evidence['claim_meta'] values must be dicts")


# --------------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------------
def make_temporal_assessor(
    *,
    retrieve_superseding_candidates: Callable,
    fetch_comparability_source: Callable,
    check_formal_notice: Callable,
    classify_evidence_tier: Callable,
    find_supersession_attestation: Callable,
    judge_contradiction: CallJudgeContradiction,
    evidence: dict,
    policy: F5Policy,
) -> TemporalAssessorRun:
    """Build a fail-closed F5 temporal assessor for one citation. Configuration
    defects raise BEFORE any seam call; every I/O seam is injected (no network /
    paid call in this module or its tests)."""
    validate_f5_policy(policy)
    _validate_evidence(evidence)
    for name, fn in (
        ("retrieve_superseding_candidates", retrieve_superseding_candidates),
        ("fetch_comparability_source", fetch_comparability_source),
        ("check_formal_notice", check_formal_notice),
        ("classify_evidence_tier", classify_evidence_tier),
        ("find_supersession_attestation", find_supersession_attestation),
        ("judge_contradiction", judge_contradiction),
    ):
        if not callable(fn):
            raise ValueError(f"{name} must be callable")
    return TemporalAssessorRun(
        retrieve_superseding_candidates=retrieve_superseding_candidates,
        fetch_comparability_source=fetch_comparability_source,
        check_formal_notice=check_formal_notice,
        classify_evidence_tier=classify_evidence_tier,
        find_supersession_attestation=find_supersession_attestation,
        judge_contradiction=judge_contradiction,
        evidence=evidence,
        policy=policy,
    )


def decide_f5(
    claims,
    support,
    evidence: dict,
    *,
    retrieve_superseding_candidates: Callable,
    fetch_comparability_source: Callable,
    check_formal_notice: Callable,
    classify_evidence_tier: Callable,
    find_supersession_attestation: Callable,
    judge_contradiction: CallJudgeContradiction,
    policy: F5Policy,
) -> tuple[TemporalAssessment, tuple[dict, ...]]:
    """Run the F5 detector over the SUPPORTED claims and return the single frozen
    ``TemporalAssessment`` for ``decide_judgment`` plus the per-claim ``F5Record``
    audit dicts (one per claim, in claim order; non-SUPPORTED claims recorded
    ``assessed=False``)."""
    assessor = make_temporal_assessor(
        retrieve_superseding_candidates=retrieve_superseding_candidates,
        fetch_comparability_source=fetch_comparability_source,
        check_formal_notice=check_formal_notice,
        classify_evidence_tier=classify_evidence_tier,
        find_supersession_attestation=find_supersession_attestation,
        judge_contradiction=judge_contradiction,
        evidence=evidence,
        policy=policy,
    )
    temporal = assessor(claims, support)
    return temporal, tuple(assessor.records)


# --------------------------------------------------------------------------
# Replay / tamper validator (mirrors validate_f7_record). Re-derives the
# comparability decision + engine booleans + f5_path from stored facts + policy
# and raises on ANY drift.
# --------------------------------------------------------------------------
def validate_f5_record(record: dict, policy: F5Policy) -> None:
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    if record.get("assessed") is False:
        if "activation" not in record:
            return  # legacy non-SUPPORTED passthrough has no activation decision
        activation = activation_decision_from_dict(record["activation"])
        if activation.applicability != "not_applicable":
            raise ValueError(
                "an activation-bearing assessed=False record must be not_applicable")
        if record.get("not_applicable") is not True:
            raise ValueError("not-applicable activation record missing marker")
        if record.get("record_sha256") != record_sha256(record):
            raise ValueError("record_sha256 mismatch (tampered)")
        return
    if not isinstance(policy, F5Policy):
        raise ValueError("policy must be an F5Policy")
    for key in ("temporal_state", "f5_path", "candidate_assessments",
                "path_a_eligible", "cited_eoc_caps", "record_sha256",
                "f5_policy_version", "reportable"):
        if key not in record:
            raise ValueError(f"record is missing field: {key}")
    if record["f5_policy_version"] != policy.policy_version:
        raise ValueError("record f5_policy_version does not match the supplied policy")
    if record.get("comparability_policy_version") != policy.comparability_policy_version:
        raise ValueError(
            "record comparability_policy_version does not match the supplied policy")
    # Development-mode invariant: reportable=True requires a confirmed verifier.
    if record["reportable"] and record.get("verifier_result") != "confirmed":
        raise ValueError("reportable=True requires verifier_result=confirmed")
    # Re-derive each candidate's comparability decision from its stored axes.
    for cand in record["candidate_assessments"]:
        cm, orl, prl = cand.get("claim_match"), cand.get("outcome_relation"), cand.get("population_relation")
        if cm is None and orl is None and prl is None:
            continue  # candidate short-circuited before the contradiction judgment
        expected = derive_comparability_decision(cm, orl, prl)
        if cand.get("comparability_decision") != expected:
            raise ValueError(
                "candidate comparability_decision drifted from its stored axes")
    # Re-derive the frozen-engine booleans from the SELECTED contradiction's
    # stored axes: on a QUALIFYING record they must both be definitively positive
    # and match what was stored (a tamper that flips them past record_sha256 is
    # caught here).
    if record["temporal_state"] == "QUALIFYING_CONTRADICTION":
        selected_id = record.get("selected_contradiction_work_id")
        selected = next(
            (c for c in record["candidate_assessments"]
             if c.get("candidate_work_id") == selected_id), None)
        if selected is None:
            raise ValueError(
                "selected_contradiction_work_id is absent from candidate_assessments")
        expected_same = (selected.get("claim_match") == "match"
                         and selected.get("outcome_relation") == "same")
        expected_pop = selected.get("population_relation") in _POPULATION_COMPARABLE
        if not (expected_same and expected_pop):
            raise ValueError(
                "QUALIFYING record whose selected contradiction is not comparable")
        if (record.get("same_claim_or_outcome") != expected_same
                or record.get("comparable_population") != expected_pop):
            raise ValueError(
                "engine booleans drifted from the selected contradiction's axes")
    # Re-derive the route.
    expected_path = _derive_f5_path(
        temporal_state=record["temporal_state"],
        path_a_eligible=record["path_a_eligible"],
        cited_eoc_caps=record["cited_eoc_caps"],
        deploy_path_a=policy.deploy_path_a,
    )
    if record["f5_path"] != expected_path:
        raise ValueError("record f5_path drifted from its stored facts + policy")
    # Path A is never deployed while deploy_path_a=False.
    if record.get("path_a_deployed") and not policy.deploy_path_a:
        raise ValueError("path_a_deployed=True while deploy_path_a=False")
    if record["f5_path"] == "A" and not policy.deploy_path_a:
        raise ValueError("f5_path=A while deploy_path_a=False")
    if record_sha256(record) != record["record_sha256"]:
        raise ValueError("record_sha256 mismatch (tampered)")
