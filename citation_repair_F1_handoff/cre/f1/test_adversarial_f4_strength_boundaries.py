"""Adversarial parser, tri-state, and verifier-matrix guards for F4."""
from __future__ import annotations

import itertools
import json

import pytest

from cre.f1 import f4_strength as f4
from cre.f1.judgment_engine import ClaimSupport, SupportState


CLAIM = "Drug X prevents disease Y in adults"
ABSTRACT = "Drug X was associated with a reduced risk of disease Y in adults."


def _generator(*, candidate=True, rationale="generator-private-rationale"):
    dims = {name: {"citing": "none", "cited": "none"} for name in f4._LADDER_DIMS}
    if candidate:
        dims["causal_force"] = {"citing": "causation", "cited": "association"}
    return json.dumps({
        "subject_addressed": "yes",
        "dimensions": dims,
        "population_relation": "equivalent",
        "load_bearing_dimension": "causal_force" if candidate else "none",
        "f6_owned_escalation": False,
        "citing_strength_span": "prevents disease Y" if candidate else "",
        "cited_strength_span": "associated with a reduced risk" if candidate else "",
        "rationale": rationale,
    })


def _verifier(values=None):
    if values is None:
        values = (True,) * len(f4._VERIFIER_BOOL_KEYS)
    return json.dumps(dict(zip(f4._VERIFIER_BOOL_KEYS, values)) | {"rationale": "v"})


def _malformed_cases():
    parsers = (("generator", f4._parse_f4, _generator()),
               ("verifier", f4._parse_verifier, _verifier()))
    shapes = (
        ("empty", lambda valid: ""),
        ("whitespace", lambda valid: " \t"),
        ("bom", lambda valid: "\ufeff" + valid),
        ("fence", lambda valid: "```json\n" + valid + "\n```"),
        ("leading-prose", lambda valid: "result=" + valid),
        ("truncated", lambda valid: valid[:-3]),
        ("bytes", lambda valid: valid.encode("utf-8")),
        ("top-level-array", lambda valid: "[]"),
    )
    return [
        pytest.param(parser, shape(valid), id=f"{name}-{shape_name}")
        for name, parser, valid in parsers
        for shape_name, shape in shapes
    ]


@pytest.mark.parametrize("parser,raw", _malformed_cases())
def test_both_f4_prompts_reject_malformed_or_wrapped_output(parser, raw):
    with pytest.raises(ValueError):
        parser(raw)


@pytest.mark.parametrize("values", list(itertools.product((False, True), repeat=4)))
def test_all_generator_verifier_agreement_combinations(values):
    generator_prompts = []
    verifier_prompts = []

    def generator(prompt):
        generator_prompts.append(prompt)
        return _generator()

    def verifier(prompt):
        verifier_prompts.append(prompt)
        return _verifier(values)

    refined, records = f4.refine_support_strength(
        (CLAIM,),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        {"cited_abstract": ABSTRACT},
        call_llm=generator,
        verifier_call_llm=verifier,
        policy=f4.F4Policy(mode="formal", generator_model_id="g", verifier_model_id="v"),
    )
    expected = SupportState.WEAKER_STRENGTH if all(values) else SupportState.UNJUDGEABLE
    assert refined[0].state is expected
    assert records[0]["derived"] == ("F4" if all(values) else "UNJUDGEABLE")
    assert len(generator_prompts) == len(verifier_prompts) == 1
    assert "generator-private-rationale" not in verifier_prompts[0]
    assert "WEAKER_STRENGTH" not in verifier_prompts[0]


def test_non_candidate_generator_never_invokes_verifier():
    def verifier(_prompt):
        raise AssertionError("verifier must be candidate-only")

    refined, records = f4.refine_support_strength(
        (CLAIM,),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        {"cited_abstract": ABSTRACT},
        call_llm=lambda _prompt: _generator(candidate=False),
        verifier_call_llm=verifier,
        policy=f4.F4Policy(mode="formal", generator_model_id="g", verifier_model_id="v"),
    )
    assert refined[0].state is SupportState.SUPPORTED
    assert records[0]["derived"] == "NOT_F4"
    assert "verifier_response" not in records[0]


def test_non_supported_tristates_pass_through_without_any_model_call():
    rows = (
        ClaimSupport(0, SupportState.UNESTABLISHED, "no"),
        ClaimSupport(1, SupportState.UNJUDGEABLE, "unknown"),
    )

    def never(_prompt):
        raise AssertionError("non-supported rows must not reach a model")

    refined, records = f4.refine_support_strength(
        ("claim zero", "claim one"), rows, {"cited_abstract": ABSTRACT},
        call_llm=never,
        policy=f4.F4Policy(mode="development", generator_model_id="g"),
    )
    assert refined[0] is rows[0]
    assert refined[1] is rows[1]
    assert records == ({"claim_index": 0, "assessed": False},
                       {"claim_index": 1, "assessed": False})


@pytest.mark.parametrize("parser,raw", [
    (f4._parse_f4, _generator(rationale=None)),
    (f4._parse_verifier, _verifier()),
])
def test_f4_rationale_is_required_even_though_null_is_allowed(parser, raw):
    obj = json.loads(raw)
    obj["rationale"] = None
    parsed = parser(json.dumps(obj))
    assert parsed["rationale"] == ""
    del obj["rationale"]
    with pytest.raises(ValueError, match="keys mismatch"):
        parser(json.dumps(obj))
