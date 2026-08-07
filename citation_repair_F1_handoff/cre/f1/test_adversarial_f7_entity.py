"""Adversarial transport-only tests for every F7 prompt parser."""
from __future__ import annotations

import json

import pytest

from cre.f1 import f7_entity as f7


CLAIM = "BRCA1 suppresses tumor growth"


def _tuple():
    return json.dumps([{"tuple_id": 0, "entity_type": "gene", "claim_surface": "BRCA1",
                        "clause_span": CLAIM, "predicate": "suppresses", "object": "tumor growth",
                        "direction": "negative", "population": "mice"}])


def _attribution():
    return json.dumps({"attribution": "direct", "target_supported": True,
                       "sibling_reference_possible": False, "rationale": "r"})


def _evidence():
    return json.dumps({"entity_type": "gene", "evidence_surface": "BRCA2", "entity_section_sha256": "a",
                       "entity_span": "BRCA2", "relation_section_sha256": "a", "relation_span": "tumor growth",
                       "predicate": "suppresses", "object": "tumor growth", "direction": "negative",
                       "population": "mice", "papers_own_finding": True, "rationale": "r"})


def _verifier():
    return json.dumps({key: True for key in f7._SCHEMA_E_BOOL_KEYS} | {"rationale": "r"})


@pytest.mark.parametrize("parser,raw", [
    (f7._parse_claimed_tuples, _tuple() + " trailing"),
    (f7._parse_attribution, _attribution() + _attribution()),
    (f7._parse_evidence, _evidence() + "\nprose"),
    (f7._parse_verifier, _verifier() + "{}"),
])
def test_each_f7_prompt_rejects_trailing_model_output(parser, raw):
    with pytest.raises(ValueError, match="Extra data"):
        parser(raw, CLAIM) if parser is f7._parse_claimed_tuples else parser(raw)


def test_tuple_parser_rejects_bool_id_and_nonverbatim_surface():
    obj = json.loads(_tuple()); obj[0]["tuple_id"] = True
    with pytest.raises(ValueError, match="exact int"):
        f7._parse_claimed_tuples(json.dumps(obj), CLAIM)
    obj = json.loads(_tuple()); obj[0]["claim_surface"] = "made up"
    with pytest.raises(ValueError, match="verbatim"):
        f7._parse_claimed_tuples(json.dumps(obj), CLAIM)
