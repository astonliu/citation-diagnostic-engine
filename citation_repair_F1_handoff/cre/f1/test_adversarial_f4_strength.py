"""Adversarial generator/verifier tests for F4 strength refinement."""
from __future__ import annotations

import json

import pytest

from cre.f1 import f4_strength as f4
from cre.f1.judgment_engine import ClaimSupport, SupportState


CLAIM = "Drug X prevents disease Y in adults"
ABSTRACT = "Drug X was associated with a reduced risk of disease Y in older adults."


def _generator():
    dims = {d: {"citing": "none", "cited": "none"} for d in f4._LADDER_DIMS}
    dims["causal_force"] = {"citing": "causation", "cited": "association"}
    return json.dumps({"subject_addressed": "yes", "dimensions": dims,
                       "population_relation": "equivalent", "load_bearing_dimension": "causal_force",
                       "f6_owned_escalation": False, "citing_strength_span": "prevents disease Y",
                       "cited_strength_span": "associated with a reduced risk", "rationale": "r"})


def _verifier(value=True):
    return json.dumps({key: value for key in f4._VERIFIER_BOOL_KEYS} | {"rationale": "v"})


@pytest.mark.parametrize("raw", [_generator() + " trailing", _generator() + _generator()])
def test_generator_rejects_trailing_model_output(raw):
    with pytest.raises(ValueError, match="Extra data"):
        f4._parse_f4(raw)


def test_verifier_rejects_trailing_model_output():
    with pytest.raises(ValueError, match="Extra data"):
        f4._parse_verifier(_verifier() + "\ncomment")


def test_verifier_disagreement_holds_after_generator_candidate():
    policy = f4.F4Policy(mode="formal", generator_model_id="g", verifier_model_id="v")
    refined, records = f4.refine_support_strength(
        (CLAIM,), (ClaimSupport(0, SupportState.SUPPORTED),), {"cited_abstract": ABSTRACT},
        call_llm=lambda _: _generator(), verifier_call_llm=lambda _: _verifier(False), policy=policy)
    assert refined[0].state is SupportState.UNJUDGEABLE
    assert records[0]["derived"] == "UNJUDGEABLE"
    assert records[0]["reason"] == "verifier_disagreement"


def test_malformed_verifier_is_not_treated_as_disagreement():
    policy = f4.F4Policy(mode="formal", generator_model_id="g", verifier_model_id="v")
    with pytest.raises(ValueError, match="Extra data"):
        f4.refine_support_strength(
            (CLAIM,), (ClaimSupport(0, SupportState.SUPPORTED),), {"cited_abstract": ABSTRACT},
            call_llm=lambda _: _generator(), verifier_call_llm=lambda _: _verifier() + "{}", policy=policy)
