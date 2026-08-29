"""Adversarial transport, verifier-matrix, and state-boundary guards for F7."""
from __future__ import annotations

import itertools
import json

import pytest

from cde.diagnose import entity as f7
from cde.diagnose.engine import ClaimSupport, EntityState, SupportState
from .test_f7_entity import CLAIM, build, verifier_json


TUPLE = json.dumps([{
    "tuple_id": 0,
    "entity_type": "gene",
    "claim_surface": "BRCA1",
    "clause_span": CLAIM,
    "predicate": "suppresses",
    "object": "tumor growth",
    "direction": "negative",
    "population": "mice",
}])
ATTRIBUTION = json.dumps({
    "attribution": "direct",
    "target_supported": True,
    "sibling_reference_possible": False,
    "rationale": "r",
})
EVIDENCE = json.dumps({
    "entity_type": "gene",
    "evidence_surface": "BRCA2",
    "entity_section_sha256": "a",
    "entity_span": "BRCA2",
    "relation_section_sha256": "a",
    "relation_span": "tumor growth",
    "predicate": "suppresses",
    "object": "tumor growth",
    "direction": "negative",
    "population": "mice",
    "papers_own_finding": True,
    "rationale": "r",
})
VERIFIER = json.dumps({key: True for key in f7._SCHEMA_E_BOOL_KEYS} | {"rationale": "r"})


def _malformed_cases():
    parsers = (
        ("tuples", lambda raw: f7._parse_claimed_tuples(raw, CLAIM), TUPLE),
        ("attribution", f7._parse_attribution, ATTRIBUTION),
        ("evidence", f7._parse_evidence, EVIDENCE),
        ("verifier", f7._parse_verifier, VERIFIER),
    )
    shapes = (
        ("empty", lambda valid: ""),
        ("whitespace", lambda valid: " \n"),
        ("bom", lambda valid: "\ufeff" + valid),
        ("fence", lambda valid: "```json\n" + valid + "\n```"),
        ("leading-prose", lambda valid: "answer: " + valid),
        ("truncated", lambda valid: valid[:-3]),
        ("bytes", lambda valid: valid.encode("utf-8")),
    )
    return [
        pytest.param(parser, shape(valid), id=f"{name}-{shape_name}")
        for name, parser, valid in parsers
        for shape_name, shape in shapes
    ]


@pytest.mark.parametrize("parser,raw", _malformed_cases())
def test_all_four_f7_prompts_reject_malformed_or_wrapped_output(parser, raw):
    with pytest.raises(ValueError):
        parser(raw)


@pytest.mark.parametrize("values", list(itertools.product((False, True), repeat=5)))
def test_every_f7_verifier_agreement_combination(values):
    prompts = []

    def verifier(prompt):
        prompts.append(prompt)
        kwargs = dict(zip(
            ("differ", "own", "direct", "equivalent", "enumerated"), values))
        return verifier_json(**kwargs, rationale="verifier-private")

    assessor = build(ver=verifier)
    assessment = assessor((CLAIM,))[0]
    # `equivalent` (index 3) is answered and recorded but does NOT gate: F7 owns
    # the entity question only (decision_rule_version f7_entity_scope_v2). The
    # other four still all have to hold.
    gating = [values[i] for i in (0, 1, 2, 4)]
    expected = EntityState.DIFFERENT_ENTITY_SUPPORTED if all(gating) else EntityState.UNJUDGEABLE
    assert assessment.state is expected
    assert len(prompts) == 1
    assert "DIFFERENT_ENTITY_SUPPORTED" not in prompts[0]
    assert "proposed_corrected" not in prompts[0]


@pytest.mark.parametrize("state", list(SupportState))
def test_f7_entity_evidence_is_not_truthiness_gated_by_support_state(state):
    """F7 may coexist with F6/F4, so support is context, not an F7 veto."""
    assessor = build()
    result = assessor((CLAIM,), (ClaimSupport(0, state),))
    assert result[0].state is EntityState.DIFFERENT_ENTITY_SUPPORTED


@pytest.mark.parametrize("parser,obj,match", [
    (f7._parse_attribution,
     {"attribution": "direct", "target_supported": 1,
      "sibling_reference_possible": False, "rationale": "r"}, "boolean"),
    (f7._parse_attribution,
     {"attribution": "direct", "target_supported": True,
      "sibling_reference_possible": "false", "rationale": "r"}, "boolean"),
    (f7._parse_verifier,
     {key: ("true" if key == f7._SCHEMA_E_BOOL_KEYS[0] else True)
      for key in f7._SCHEMA_E_BOOL_KEYS} | {"rationale": "r"}, "boolean"),
])
def test_f7_boolean_fields_are_never_coerced(parser, obj, match):
    with pytest.raises(ValueError, match=match):
        parser(json.dumps(obj))
