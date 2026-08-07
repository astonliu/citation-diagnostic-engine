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


@pytest.mark.xfail(strict=True, reason="schema B relation components are type-checked but may be blank")
@pytest.mark.parametrize("field", ["predicate", "object", "direction", "population"])
def test_claimed_tuple_relation_components_must_be_nonblank(field):
    obj = json.loads(_tuple()); obj[0][field] = "  "
    with pytest.raises(ValueError, match="nonblank"):
        f7._parse_claimed_tuples(json.dumps(obj), CLAIM)


@pytest.mark.xfail(strict=True, reason="schema C evidence spans are type-checked but may be blank")
@pytest.mark.parametrize("field", ["entity_span", "relation_span"])
def test_evidence_spans_must_be_nonblank(field):
    obj = json.loads(_evidence()); obj[field] = ""
    with pytest.raises(ValueError, match="nonblank"):
        f7._parse_evidence(json.dumps(obj))


@pytest.mark.xfail(strict=True, reason="schema C relation components are type-checked but may be blank")
@pytest.mark.parametrize("field", ["predicate", "object", "direction", "population"])
def test_evidence_relation_components_must_be_nonblank(field):
    obj = json.loads(_evidence()); obj[field] = ""
    with pytest.raises(ValueError, match="nonblank"):
        f7._parse_evidence(json.dumps(obj))


@pytest.mark.xfail(strict=True, reason="authorities_json uses a duplicate-tolerant loader")
def test_authority_lock_rejects_duplicate_json_keys():
    raw = ('{"gene":{"authority":"HGNC","authority":"OTHER",'
           '"version":"1","lookup_date":"2026-01-01",'
           '"accept_synonym_as_equivalent":true,"cross_db_equivalences":[]}}')
    with pytest.raises(ValueError, match="duplicate"):
        f7._parse_authorities(raw)
