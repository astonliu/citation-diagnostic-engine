"""Adversarial transport and control-flow guards for F3 provenance."""
from __future__ import annotations

import json

import pytest

from cre.f1 import f3_provenance as f3
from cre.f1.judgment_engine import ClaimSupport, ProvenanceState, SupportState


V2 = json.dumps({"verdict": "originates", "evidence_span": "span", "rationale": "r"})
V3 = json.dumps({"selected_index": 0, "rationale": "r"})
V4 = json.dumps({"contains_finding": True, "evidence_span": "span", "rationale": "r"})


def _malformed_cases():
    parsers = (
        ("v2", lambda raw: f3._parse_v2(raw), V2),
        ("v3", lambda raw: f3._parse_v3(raw, 1), V3),
        ("v4", lambda raw: f3._parse_v4(raw), V4),
    )
    shapes = (
        ("empty", lambda valid: ""),
        ("whitespace", lambda valid: " \n\t"),
        ("bom", lambda valid: "\ufeff" + valid),
        ("fence", lambda valid: "```json\n" + valid + "\n```"),
        ("leading-prose", lambda valid: "answer: " + valid),
        ("truncated", lambda valid: valid[:-2]),
        ("bytes", lambda valid: b"\xff" + valid.encode("utf-8")),
        ("top-level-array", lambda valid: "[]"),
    )
    return [
        pytest.param(parser, shape(valid), id=f"{name}-{shape_name}")
        for name, parser, valid in parsers
        for shape_name, shape in shapes
    ]


@pytest.mark.parametrize("parser,raw", _malformed_cases())
def test_every_f3_prompt_rejects_nonbare_or_malformed_output(parser, raw):
    with pytest.raises(ValueError):
        parser(raw)


@pytest.mark.parametrize(
    "parser,obj,match",
    [
        (f3._parse_v2, {"verdict": "originates", "evidence_span": "x"}, "keys mismatch"),
        (f3._parse_v2, {"verdict": True, "evidence_span": "x", "rationale": "r"}, "verdict"),
        (f3._parse_v2, {"verdict": "originates", "evidence_span": None, "rationale": "r"}, "string"),
        (f3._parse_v3, {"selected_index": True, "rationale": "r"}, "integer or null"),
        (f3._parse_v3, {"selected_index": "0", "rationale": "r"}, "integer or null"),
        (f3._parse_v3, {"selected_index": 1, "rationale": "r"}, "out of range"),
        (f3._parse_v4, {"contains_finding": "true", "evidence_span": "x", "rationale": "r"}, "boolean"),
        (f3._parse_v4, {"contains_finding": 1, "evidence_span": "x", "rationale": "r"}, "boolean"),
        (f3._parse_v4, {"contains_finding": True, "evidence_span": [], "rationale": "r"}, "string"),
    ],
)
def test_f3_schema_types_are_not_coerced(parser, obj, match):
    args = (json.dumps(obj), 1) if parser is f3._parse_v3 else (json.dumps(obj),)
    with pytest.raises(ValueError, match=match):
        parser(*args)


@pytest.mark.parametrize("parser,obj", [
    (f3._parse_v2, {"verdict": "originates", "evidence_span": "x", "rationale": None}),
    (f3._parse_v3, {"selected_index": None, "rationale": None}),
    (f3._parse_v4, {"contains_finding": False, "evidence_span": "", "rationale": None}),
])
def test_f3_rationale_null_normalizes_but_absence_is_rejected(parser, obj):
    args = (json.dumps(obj), 1) if parser is f3._parse_v3 else (json.dumps(obj),)
    assert parser(*args)[-1] == ""
    del obj["rationale"]
    args = (json.dumps(obj), 1) if parser is f3._parse_v3 else (json.dumps(obj),)
    with pytest.raises(ValueError, match="keys mismatch"):
        parser(*args)


@pytest.mark.parametrize(
    "verdict,expected",
    [
        ("originates", ProvenanceState.PROPER_ORIGIN),
        ("not_origin_sensitive", ProvenanceState.PROPER_ORIGIN),
        ("unclear", ProvenanceState.UNJUDGEABLE),
    ],
)
def test_non_restatement_v2_paths_make_exactly_one_model_call(verdict, expected):
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return json.dumps({"verdict": verdict, "evidence_span": "", "rationale": "r"})

    assessor = f3.make_provenance_assessor(
        call_llm=llm,
        fetch_reflist=lambda _pmcid: (_ for _ in ()).throw(AssertionError("unexpected reflist")),
        fetch_abstract=lambda _pmid: "The cited abstract reports a result.",
        cited_pmid="1",
        cited_pmcid="PMC1",
    )
    result = assessor(
        ("A claim",),
        (ClaimSupport(0, SupportState.SUPPORTED),),
    )
    assert result.state is expected
    assert len(calls) == 1
