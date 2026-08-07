"""Adversarial type-boundary and blindness tests for the orchestrator."""
from __future__ import annotations

import pytest

from cre.f1 import judgment_run as jr
from cre.f1.judgment_engine import SupportState, from_legacy_coverage


@pytest.mark.parametrize("value,state", [(True, SupportState.SUPPORTED),
                                           (False, SupportState.UNESTABLISHED),
                                           (None, SupportState.UNJUDGEABLE)])
def test_legacy_coverage_preserves_the_exact_tri_state(value, state):
    rows = from_legacy_coverage(("claim",), ({"established": value},))
    assert rows[0].state is state


@pytest.mark.parametrize("bad", ["true", 0, 1, [], {}])
def test_legacy_coverage_rejects_non_tri_state_values(bad):
    with pytest.raises(ValueError):
        from_legacy_coverage(("claim",), ({"established": bad},))


def test_orchestrator_record_contains_no_annotation_payload_or_proposed_route():
    item = {"citation_id": "PMC1:R1", "citing_sentence": "claim [1]", "cited_pmid": "1",
            "cited_claimed": {}, "proposed_route": "F6_FLAGGED", "proposed_verdict": "F6"}
    rec = jr._new_record(item)
    assert "proposed_route" not in rec
    assert "proposed_verdict" not in rec
