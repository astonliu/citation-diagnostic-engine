from __future__ import annotations

import pytest

from .f5_candidate_screen import (
    CandidateScreenBatch, CandidateScreenDecision,
    validate_candidate_screen_batch,
)


def row(work_id="W2", *, decision="plausible", relevance="match",
        relation="uncertain", missing=()):
    return CandidateScreenDecision(
        candidate_work_id=work_id, decision=decision,
        claim_relevance=relevance, possible_relation=relation,
        missing_facts=tuple(missing))


def batch(*rows):
    return CandidateScreenBatch(
        decisions=tuple(rows), prompt_sha256="a" * 64,
        response_sha256="b" * 64)


def test_screen_batch_is_joined_by_exact_candidate_ids_not_position():
    mapped = validate_candidate_screen_batch(
        batch(row("W3"), row("W2")), ("W2", "W3"))
    assert set(mapped) == {"W2", "W3"}
    assert mapped["W2"].candidate_work_id == "W2"


@pytest.mark.parametrize("rows,expected", [
    ((row("W2"),), ("W2", "W3")),
    ((row("W2"), row("W9")), ("W2", "W3")),
])
def test_screen_batch_rejects_missing_or_foreign_ids(rows, expected):
    with pytest.raises(ValueError, match="exactly match"):
        validate_candidate_screen_batch(batch(*rows), expected)


def test_clear_mismatch_cannot_contradict_its_own_uncertainty_fields():
    with pytest.raises(ValueError, match="clear_mismatch"):
        row("W2", decision="clear_mismatch", relevance="mismatch",
            relation="opposes")
    with pytest.raises(ValueError, match="clear_mismatch"):
        row("W2", decision="clear_mismatch", relevance="mismatch",
            relation="neutral", missing=("population",))


def test_batch_rejects_duplicate_ids_and_non_sha_hashes():
    with pytest.raises(ValueError, match="duplicate"):
        batch(row("W2"), row("W2"))
    with pytest.raises(ValueError, match="SHA-256"):
        CandidateScreenBatch(
            decisions=(row("W2"),), prompt_sha256="not-a-hash",
            response_sha256="b" * 64)
