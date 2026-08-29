"""Citation Diagnostic Engine -- evidence-backed diagnosis of citation faults.

The package is split by what each part DOES, not by which taxonomy category
motivated it:

  ``cde.refs``      Band 1. Is this reference the work it says it is?
  ``cde.claims``    Band 2 front end: claims, evidence, spans, coverage.
  ``cde.diagnose``  the typed decision engine and the five discriminators.
  ``cde.freeze``    artifact integrity and provenance.
  ``cde.runtime``   transports, rate limits, launchers and wiring.

WHAT THIS FILE EXPORTS, AND WHY SO LITTLE. Only the handful of names a caller
actually reaches for: the two band entry points, the decision core, and the
record types they exchange. Everything else is imported from its own
subpackage. A package root that re-exported all 75 modules would make the
import graph unreadable and would turn any subpackage move into an edit here.

The deliberately absent name is a transport. Building one goes through
``cde.runtime.completer.make_completer``, which is the single place a model id
is turned into a provider, and importing it from the root would invite a call
site to reach for a vendor SDK directly instead.
"""
from .diagnose.engine import (
    ClaimSupport, DecisionStatus, DiscriminatorContractError, EntityAssessment,
    EntityState, JudgmentDecision, ProvenanceAssessment, ProvenanceState,
    SupportState, TemporalAssessment, TemporalState, decide_judgment,
)
from .diagnose.pipeline import judge_pair, run_natural_judgment
from .refs.run import run as run_band1
from .refs.schema import (
    ClaimedRef, PredictionRecord, Reference, RetrievedRecord, StageLog,
    TAXONOMY_LABELS,
)

__all__ = [
    # Band 1
    "run_band1",
    # Band 2
    "run_natural_judgment", "judge_pair",
    # the decision core
    "decide_judgment", "JudgmentDecision", "DecisionStatus",
    "ClaimSupport", "SupportState", "EntityAssessment", "EntityState",
    "ProvenanceAssessment", "ProvenanceState",
    "TemporalAssessment", "TemporalState", "DiscriminatorContractError",
    # the records the two bands exchange
    "Reference", "ClaimedRef", "RetrievedRecord", "StageLog",
    "PredictionRecord", "TAXONOMY_LABELS",
]
