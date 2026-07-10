"""Offline coverage for the frozen F3-F7 prompt module."""
from __future__ import annotations

import json

import pytest

from cre.f1 import band_prompts as bp
from cre.f1 import judgment_band as jb


def test_parse_claims_accepts_fenced_json_and_rejects_non_list():
    assert bp.parse_claims('```json\n{"claims": ["A", " B ", ""]}\n```') == ["A", "B"]
    with pytest.raises(ValueError, match="not a list"):
        bp.parse_claims('{"claims": "A"}')


@pytest.mark.parametrize(
    ("token", "expected"),
    [("true", True), ("false", False), ("null", None)],
)
def test_parse_coverage_strict_tristate(token, expected):
    result = bp.parse_coverage(
        json.dumps({"established": token, "rationale": "because", "evidence_span": "span"})
    )
    assert result.established is expected
    assert result.rationale == "because"
    assert result.evidence_span == "span"


def test_parse_coverage_rejects_malformed_tristate():
    with pytest.raises(ValueError, match="unrecognized"):
        bp.parse_coverage('{"established": "maybe"}')


def test_render_evidence_uses_only_cited_abstract():
    evidence = {
        "cited_abstract": "Title: Correct abstract.",
        "review_reflist": [{"title": "Must stay downstream"}],
    }
    assert bp.render_evidence(evidence) == "Title: Correct abstract."
    assert bp.render_evidence({}) == "(no abstract available)"
    assert bp.render_evidence("  bare abstract  ") == "bare abstract"


def test_route_from_verdicts_matches_tri_state_contract():
    assert bp.route_from_verdicts([{"established": True}]) == jb.ROUTE_FULL_COVERAGE
    assert bp.route_from_verdicts([{"established": None}]) == jb.ROUTE_HELD
    assert bp.route_from_verdicts([{"established": None}, {"established": False}]) == jb.ROUTE_F6_FLAGGED


def test_real_band_integration_reads_full_verdict_shape_and_routes():
    """Factories match the real sentence-only/batch band callable contracts."""
    extraction_reply = '{"claims": ["Drug A improves survival", "Drug A reduces tumor size in mice"]}'
    coverage_replies = iter([
        '{"established": true, "rationale": "abstract states survival", "evidence_span": "improved survival"}',
        '{"established": false, "rationale": "model is absent", "evidence_span": ""}',
    ])

    def call_llm(prompt):
        if "Citing sentence:" in prompt:
            return extraction_reply
        return next(coverage_replies)

    extractor = bp.make_extractor(call_llm)
    judge = bp.make_coverage_judge(call_llm)
    claims = jb.extract_atomic_claims("Drug A improves survival and reduces tumor size in mice [1].", extractor=extractor)
    assert claims == ["Drug A improves survival", "Drug A reduces tumor size in mice"]
    verdicts = jb.coverage_verdicts(claims, {"cited_abstract": "Drug A improved survival."}, judge=judge)
    assert verdicts[0]["established"] is True
    assert verdicts[0]["rationale"] == "abstract states survival"
    assert verdicts[0]["evidence_span"] == "improved survival"
    assert verdicts[1]["established"] is False
    assert jb.route(verdicts) == jb.ROUTE_F6_FLAGGED


def test_real_band_integration_all_true_routes_full_coverage():
    def call_llm(prompt):
        if "Citing sentence:" in prompt:
            return '{"claims": ["Drug A improves survival"]}'
        return '{"established": true, "rationale": "stated", "evidence_span": "improved survival"}'

    claims = jb.extract_atomic_claims("Drug A improves survival [1].", extractor=bp.make_extractor(call_llm))
    verdicts = jb.coverage_verdicts(
        claims,
        {"cited_abstract": "Drug A improved survival."},
        judge=bp.make_coverage_judge(call_llm),
    )
    assert jb.route(verdicts) == jb.ROUTE_FULL_COVERAGE
