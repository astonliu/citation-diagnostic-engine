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


def _scripted_trace(v2_span="cited attribution", v4_span="primary finding"):
    def call_llm(prompt):
        if "CANDIDATE PRIMARY ABSTRACT" in prompt:
            return json.dumps({"contains_finding": True, "evidence_span": v4_span,
                               "rationale": "r"})
        if "CANDIDATE REFERENCES" in prompt:
            return json.dumps({"selected_index": 0, "rationale": "r"})
        return json.dumps({"verdict": "restatement", "evidence_span": v2_span,
                           "rationale": "r"})
    return call_llm


def _trace_assessor(call_llm, fetch_reflist=None):
    return f3.make_provenance_assessor(
        call_llm=call_llm,
        fetch_reflist=fetch_reflist or (lambda _: ([
            {"title": "Primary", "claimed_pmid": "2", "year": 2020}], True)),
        fetch_abstract=lambda pmid: (
            "the cited attribution appears here" if str(pmid) == "1"
            else "the primary finding appears here"),
        cited_pmid="1", cited_pmcid="PMC1")


@pytest.mark.xfail(strict=True, reason="V2 evidence_span is never bound to the cited abstract")
def test_restatement_span_must_be_verbatim_in_cited_abstract():
    assessor = _trace_assessor(_scripted_trace(v2_span="hallucinated attribution"))
    result = assessor(("claim",), (ClaimSupport(0, SupportState.SUPPORTED),))
    assert result.state is ProvenanceState.UNJUDGEABLE


@pytest.mark.xfail(strict=True, reason="V4 evidence_span is never bound to the primary abstract")
def test_origin_span_must_be_verbatim_in_primary_abstract():
    assessor = _trace_assessor(_scripted_trace(v4_span="hallucinated finding"))
    result = assessor(("claim",), (ClaimSupport(0, SupportState.SUPPORTED),))
    assert result.state is ProvenanceState.UNJUDGEABLE


@pytest.mark.xfail(strict=True, reason="F3 prompt construction uses chained replacement")
def test_f3_claim_placeholder_text_remains_inert():
    prompts = []
    def call_llm(prompt):
        prompts.append(prompt)
        return _v2()
    assessor = f3.make_provenance_assessor(
        call_llm=call_llm, fetch_reflist=lambda _: ([], False),
        fetch_abstract=lambda _: "SECRET ABSTRACT", cited_pmid="1")
    claim = "The literal token <<ABSTRACT>> is part of the claim"
    assessor((claim,), (ClaimSupport(0, SupportState.SUPPORTED),))
    assert claim in prompts[0]


@pytest.mark.xfail(strict=True, reason="malformed truthy reflist payload is iterated directly")
def test_malformed_reflist_payload_holds_instead_of_crashing():
    assessor = _trace_assessor(
        _scripted_trace(), fetch_reflist=lambda _: (123, True))
    result = assessor(("claim",), (ClaimSupport(0, SupportState.SUPPORTED),))
    assert result.state is ProvenanceState.UNJUDGEABLE


@pytest.mark.xfail(strict=True, reason="F3 assessor ignores its support argument")
def test_f3_cannot_emit_proper_origin_for_unestablished_claim():
    assessor = f3.make_provenance_assessor(
        call_llm=lambda _: _v2(), fetch_reflist=lambda _: ([], False),
        fetch_abstract=lambda _: "abstract", cited_pmid="1")
    result = assessor(("claim",), (ClaimSupport(0, SupportState.UNESTABLISHED),))
    assert result.state is ProvenanceState.UNJUDGEABLE
