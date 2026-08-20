"""Typed, injectable batched abstract screening for F5.

The screen is an optional cost gate, never an evidence adjudicator.  Only an
explicit ``clear_mismatch`` may avoid deep comparison, and the detector retains
that candidate as unassessable so the screen can never manufacture a confident
negative.  Malformed batches are rejected here; the detector responds by sending
every candidate to deep comparison.
"""
from __future__ import annotations

from dataclasses import dataclass


CANDIDATE_SCREEN_VERSION = "f5_candidate_screen_v1"
SCREEN_DECISIONS = frozenset({"plausible", "clear_mismatch", "uncertain"})
CLAIM_RELEVANCE = frozenset({"match", "mismatch", "uncertain"})
POSSIBLE_RELATIONS = frozenset(
    {"opposes", "confirms", "mixed", "neutral", "uncertain"})


def _sha256(value: str, field: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{field} must be a lowercase SHA-256 hex digest")
    return value


@dataclass(frozen=True)
class CandidateScreenDecision:
    candidate_work_id: str
    decision: str
    claim_relevance: str
    possible_relation: str
    missing_facts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_work_id, str) or not self.candidate_work_id.strip():
            raise ValueError("candidate_work_id must be a nonblank string")
        if self.decision not in SCREEN_DECISIONS:
            raise ValueError("candidate screen decision is off-enum")
        if self.claim_relevance not in CLAIM_RELEVANCE:
            raise ValueError("candidate screen claim_relevance is off-enum")
        if self.possible_relation not in POSSIBLE_RELATIONS:
            raise ValueError("candidate screen possible_relation is off-enum")
        if (not isinstance(self.missing_facts, tuple)
                or any(not isinstance(value, str) or not value.strip()
                       for value in self.missing_facts)):
            raise ValueError("candidate screen missing_facts must be nonblank strings")
        if (self.decision == "clear_mismatch"
                and (self.claim_relevance != "mismatch"
                     or self.possible_relation != "neutral"
                     or self.missing_facts)):
            raise ValueError(
                "clear_mismatch requires mismatch relevance, neutral relation, "
                "and no missing facts")


@dataclass(frozen=True)
class CandidateScreenBatch:
    decisions: tuple[CandidateScreenDecision, ...]
    prompt_sha256: str
    response_sha256: str
    version: str = CANDIDATE_SCREEN_VERSION

    def __post_init__(self) -> None:
        if self.version != CANDIDATE_SCREEN_VERSION:
            raise ValueError("candidate screen version is unsupported")
        if (not isinstance(self.decisions, tuple)
                or any(not isinstance(row, CandidateScreenDecision)
                       for row in self.decisions)):
            raise ValueError(
                "candidate screen decisions must be CandidateScreenDecision rows")
        identifiers = [row.candidate_work_id for row in self.decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("candidate screen returned duplicate candidate IDs")
        _sha256(self.prompt_sha256, "prompt_sha256")
        _sha256(self.response_sha256, "response_sha256")


def validate_candidate_screen_batch(
        batch: CandidateScreenBatch, expected_candidate_ids) -> dict[str, CandidateScreenDecision]:
    """Validate an ID-keyed batch and return its exact decision mapping."""
    if not isinstance(batch, CandidateScreenBatch):
        raise ValueError("candidate screen must return CandidateScreenBatch")
    expected = tuple(str(value) for value in expected_candidate_ids)
    if len(expected) != len(set(expected)):
        raise ValueError("candidate screen input IDs must be unique")
    observed = tuple(row.candidate_work_id for row in batch.decisions)
    if set(observed) != set(expected) or len(observed) != len(expected):
        raise ValueError("candidate screen IDs do not exactly match candidate IDs")
    return {row.candidate_work_id: row for row in batch.decisions}
