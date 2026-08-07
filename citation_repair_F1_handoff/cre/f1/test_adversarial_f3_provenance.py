"""Adversarial transport-only tests for the F3 provenance prompts."""
from __future__ import annotations

import json

import pytest

from cre.f1 import f3_provenance as f3
from cre.f1.judgment_engine import ClaimSupport, ProvenanceState, SupportState


def _v2(verdict="originates"):
    return json.dumps({"verdict": verdict, "evidence_span": "span", "rationale": "r"})


@pytest.mark.parametrize("raw", [_v2() + "\nprose", _v2() + _v2(), "```json\n{}\n``"])
def test_v2_rejects_trailing_or_wrapped_model_output(raw):
    with pytest.raises(ValueError, match="model output is not one bare JSON object"):
        f3._parse_v2(raw)


@pytest.mark.parametrize("parser,raw", [
    (f3._parse_v3, json.dumps({"selected_index": 0, "rationale": "r"}) + " trailing"),
    (f3._parse_v4, json.dumps({"contains_finding": True, "evidence_span": "x", "rationale": None}) + "{}"),
])
def test_every_other_f3_prompt_rejects_extra_json(parser, raw):
    with pytest.raises(ValueError, match="Extra data"):
        parser(raw, 1) if parser is f3._parse_v3 else parser(raw)


def test_malformed_v2_reaches_orchestrator_seam_as_a_loud_failure():
    assessor = f3.make_provenance_assessor(
        call_llm=lambda _: _v2() + "\nextra", fetch_reflist=lambda _: ([], False),
        fetch_abstract=lambda _: "abstract", cited_pmid="1", cited_pmcid="PMC1")
    with pytest.raises(ValueError, match="Extra data"):
        assessor(("claim",), (ClaimSupport(0, SupportState.SUPPORTED),))


def test_unresolved_reflist_path_holds_never_invents_f3():
    assessor = f3.make_provenance_assessor(
        call_llm=lambda _: _v2("restatement"), fetch_reflist=lambda _: None,
        fetch_abstract=lambda _: "abstract", cited_pmid="1", cited_pmcid="PMC1")
    result = assessor(("claim",), (ClaimSupport(0, SupportState.SUPPORTED),))
    assert result.state is ProvenanceState.UNJUDGEABLE
