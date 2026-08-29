"""Typed, conservative applicability gate for F5 temporal supersession.

Only a clear ``not_applicable`` decision suppresses F5.  Missing section or
ownership evidence returns ``uncertain`` so discovery continues.  The gate is
pure and deterministic: it performs no retrieval and makes no model call.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional


ACTIVATION_SCHEMA_VERSION = "f5_activation_v1"
APPLICABILITIES = frozenset({"eligible", "not_applicable", "uncertain"})

_METHODS_SECTIONS = frozenset({
    "method", "methods", "materials and methods", "patients and methods",
    "subjects and methods", "methodology", "experimental procedures",
})
_RESULTS_SECTIONS = frozenset({"result", "results", "findings"})
_EXTERNAL_SECTIONS = frozenset({
    "introduction", "background", "literature review", "related work",
    "discussion", "conclusion", "conclusions",
})

_EXTERNAL_ATTRIBUTION = re.compile(
    r"(?:\b(?:previous|prior|earlier|published)\s+"
    r"(?:research|studies?|reports?|trials?|cohorts?|experiments?)\b"
    r"|\b(?:studies?|researchers?|authors?)\s+(?:have\s+)?(?:found|reported|shown|observed|demonstrated)\b"
    r"|\baccording\s+to\b|\b[A-Z][A-Za-z'’-]+\s+et\s+al\.)",
    re.IGNORECASE,
)
_PRIOR_OWNERSHIP_CUE = re.compile(
    r"\b(?:previously|previous|prior|earlier|formerly|before)\b|"
    r"\b(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_EMPIRICAL_RELATION = re.compile(
    r"\b(?:increase(?:d|s)?|decrease(?:d|s)?|reduce(?:d|s)?|improve(?:d|s)?|"
    r"worsen(?:ed|s)?|prevent(?:ed|s)?|cause(?:d|s)?|predict(?:ed|s)?|"
    r"associat(?:e|ed|es|ion)|correlat(?:e|ed|es|ion)|risk|odds|hazard|"
    r"prevalence|incidence|mortality|survival|sensitivity|specificity|"
    r"effective(?:ness)?|efficacy|harm|benefit|effect|outcome)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_CURRENT_STUDY = re.compile(
    r"\bwe\s+(?:recruited|enrolled|randomi[sz]ed|collected|stored|measured|"
    r"assessed|performed|conducted|used|included|excluded|administered|"
    r"observed|found|detected|recorded|analysed|analyzed|increased|decreased|"
    r"reduced)\b",
    re.IGNORECASE,
)
_PASSIVE_CURRENT_METHOD = re.compile(
    r"^(?:the\s+)?(?:samples?|specimens?|participants?|patients?|subjects?|data)\s+"
    r"(?:was|were)\s+(?:recruited|enrolled|randomi[sz]ed|collected|stored|"
    r"measured|assessed|included|excluded|analysed|analyzed)\b",
    re.IGNORECASE,
)
_CURRENT_PROCEDURE = re.compile(
    r"^(?:the\s+)?(?:participants?|patients?|subjects?)\s+"
    r"(?:completed|performed|received|underwent)\b|"
    r"^(?:the\s+)?study\s+(?:included|enrolled|recruited|randomi[sz]ed)\b|"
    r"^(?:the\s+)?analys(?:is|es)\s+(?:used|were\s+performed|were\s+conducted)\b",
    re.IGNORECASE,
)
_EXPLICIT_CURRENT_CONTEXT = re.compile(
    r"\b(?:in|for|within)\s+(?:the|this|our)\s+"
    r"(?:study|trial|cohort|analysis|experiment)\b",
    re.IGNORECASE,
)
_PURE_DEFINITION = re.compile(
    r"^(?:for\s+(?:this|the)\s+(?:study|analysis),?\s+)?"
    r".{1,100}?\s+(?:is|are|was|were)\s+defined\s+as\b"
    r"(?:(?!\b(?:and|but|while|whereas)\b)[^,;])*[.]?$|"
    r"^.{1,100}?\s+(?:refers?|referred)\s+to\b"
    r"(?:(?!\b(?:and|but|while|whereas)\b)[^,;])*[.]?$",
    re.IGNORECASE,
)
_BIBLIOGRAPHIC = re.compile(
    r"^(?:the\s+)?(?:article|paper|study|report|review|book)\s+"
    r"(?:was|is)\s+(?:published|written|authored)\b"
    r"(?:\s+in\s+(?:19|20)\d{2})?[.]?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class F5ActivationDecision:
    schema_version: str
    applicability: str
    reason_code: str
    source_section: Optional[str]
    externally_sourced: Optional[bool]
    empirically_contradictable: Optional[bool]
    own_study_statement: Optional[bool]

    def __post_init__(self) -> None:
        if self.schema_version != ACTIVATION_SCHEMA_VERSION:
            raise ValueError("unsupported F5 activation schema_version")
        if self.applicability not in APPLICABILITIES:
            raise ValueError(
                f"applicability must be one of {sorted(APPLICABILITIES)}")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("reason_code must be a nonblank string")
        if self.source_section is not None and (
                not isinstance(self.source_section, str)
                or not self.source_section.strip()):
            raise ValueError("source_section must be a nonblank string or None")
        for name in ("externally_sourced", "empirically_contradictable",
                     "own_study_statement"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise ValueError(f"{name} must be bool or None")

    @property
    def activates(self) -> bool:
        """Discovery is recall-preserving: eligible and uncertain both run."""
        return self.applicability != "not_applicable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def activation_decision_from_dict(value: Any) -> F5ActivationDecision:
    """Strictly reconstruct a stored activation decision for replay checks."""
    if not isinstance(value, dict):
        raise ValueError("activation must be a dict")
    expected = {
        "schema_version", "applicability", "reason_code", "source_section",
        "externally_sourced", "empirically_contradictable",
        "own_study_statement",
    }
    if set(value) != expected:
        raise ValueError(
            "activation keys must be exactly " + repr(sorted(expected)))
    return F5ActivationDecision(**value)


def _optional_bool(meta: Mapping[str, Any], name: str) -> Optional[bool]:
    value = meta.get(name)
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"claim_meta['{name}'] must be bool or None")
    return value


def _section_kind(section: Optional[str]) -> str:
    if section is None:
        return "unknown"
    normalized = re.sub(r"\s+", " ", section.strip().casefold())
    parts = [re.sub(r"^\d+(?:\.\d+)*\s*", "", part.strip())
             for part in normalized.split(">")]
    # Literature/prior-study headings are external territory even when their
    # final word is "Methods" or "Results".  Check this before suffix rules.
    if any(part in _EXTERNAL_SECTIONS or re.search(
            r"\b(?:previous|prior|earlier|literature\s+review)\b", part)
           for part in parts):
        return "external"
    if any(part in _METHODS_SECTIONS or part.endswith(" methods")
           for part in parts):
        return "methods"
    if any(part in _RESULTS_SECTIONS or part.endswith(" results")
           for part in parts):
        return "results"
    return "other"


def decide_f5_activation(
    claim: str,
    claim_meta: Optional[Mapping[str, Any]] = None,
) -> F5ActivationDecision:
    """Return a typed applicability decision without retrieving anything.

    ``claim_meta`` may carry explicit, upstream-derived facts.  Text rules only
    make exclusions where ownership is unusually clear; all ambiguous cases are
    ``uncertain`` and therefore activate discovery.
    """
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("claim must be a nonblank string")
    if claim_meta is None:
        meta: Mapping[str, Any] = {}
    elif isinstance(claim_meta, Mapping):
        meta = claim_meta
    else:
        raise ValueError("claim_meta must be a mapping or None")

    section_value = meta.get("source_section")
    if section_value is not None and (
            not isinstance(section_value, str) or not section_value.strip()):
        raise ValueError("claim_meta['source_section'] must be a nonblank string or None")
    source_section = section_value.strip() if isinstance(section_value, str) else None
    external = _optional_bool(meta, "externally_sourced")
    empirical = _optional_bool(meta, "empirically_contradictable")
    own = _optional_bool(meta, "own_study_statement")
    section_kind = _section_kind(source_section)
    text = claim.strip()

    def decision(applicability: str, reason: str, *,
                 inferred_external=external, inferred_empirical=empirical,
                 inferred_own=own) -> F5ActivationDecision:
        return F5ActivationDecision(
            schema_version=ACTIVATION_SCHEMA_VERSION,
            applicability=applicability,
            reason_code=reason,
            source_section=source_section,
            externally_sourced=inferred_external,
            empirically_contradictable=inferred_empirical,
            own_study_statement=inferred_own,
        )

    # Contradictory ownership facts cannot safely authorize exclusion.
    if own is True and external is True:
        return decision("uncertain", "explicit_ownership_conflict")

    # Consistent explicit upstream facts have priority over language heuristics.
    if own is True:
        return decision("not_applicable", "explicit_own_study_statement")
    if external is False:
        return decision("not_applicable", "explicit_not_externally_sourced")
    if empirical is False:
        return decision("not_applicable", "explicit_not_empirical")
    if external is True and empirical is True and own is False:
        return decision("eligible", "explicit_external_empirical_claim")

    if _PURE_DEFINITION.search(text):
        return decision("not_applicable", "pure_definition",
                        inferred_empirical=False)
    if _BIBLIOGRAPHIC.search(text):
        return decision("not_applicable", "bibliographic_statement",
                        inferred_empirical=False)

    if section_kind in {"methods", "results"}:
        current_cue = bool(
            _FIRST_PERSON_CURRENT_STUDY.search(text)
            or _PASSIVE_CURRENT_METHOD.search(text)
            or _CURRENT_PROCEDURE.search(text)
            or _EXPLICIT_CURRENT_CONTEXT.search(text))
        external_cue = bool(_EXTERNAL_ATTRIBUTION.search(text))
        empirical_cue = bool(_EMPIRICAL_RELATION.search(text))
        prior_ownership_cue = bool(_PRIOR_OWNERSHIP_CUE.search(text))
        if current_cue and (external_cue or prior_ownership_cue):
            # A Methods/Results sentence can describe a current procedure while
            # citing earlier work.  Neither ownership reading is safe enough to
            # exclude it, and lexical outcome words such as "reduced the dose"
            # are not enough to call it an external empirical finding.
            return decision("uncertain", "methods_results_ownership_conflict")
        if current_cue:
            return decision(
                "not_applicable",
                "clear_current_study_methods" if section_kind == "methods"
                else "clear_current_study_results",
                inferred_external=False, inferred_own=True,
            )
        # Methods/Results are not excluded by section alone.
        if external_cue and empirical_cue:
            return decision("eligible", "attributed_external_empirical_claim",
                            inferred_external=True, inferred_empirical=True,
                            inferred_own=False)
        return decision("uncertain", "methods_results_ownership_uncertain")

    external_cue = bool(_EXTERNAL_ATTRIBUTION.search(text))
    empirical_cue = bool(_EMPIRICAL_RELATION.search(text))
    if external_cue and empirical_cue:
        return decision("eligible", "attributed_external_empirical_claim",
                        inferred_external=True, inferred_empirical=True,
                        inferred_own=False)

    if source_section is None:
        return decision("uncertain", "source_section_unavailable")
    if section_kind == "external" and empirical_cue:
        # Normal F5 territory, but no ownership cue is strong enough to call the
        # claim definitely external.  Discovery still runs through uncertainty.
        return decision("uncertain", "external_section_ownership_uncertain",
                        inferred_empirical=True)
    return decision("uncertain", "applicability_ambiguous")
