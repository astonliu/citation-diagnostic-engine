"""Offline coverage for the F3-F7 prompt module and real band interface."""
from __future__ import annotations

import json

import pytest

from cde.claims import band_prompts as bp
from cde.claims import band as jb


def _coverage_reply(
    *, engages=True, contradicts=False, unconfirmed=None,
    rationale="supported", evidence_span="span",
):
    return json.dumps({
        "engages_subject": engages,
        "contradicts": contradicts,
        "unconfirmed_specifics": unconfirmed or [],
        "rationale": rationale,
        "evidence_span": evidence_span,
    })


def test_prompt_versions_are_single_sourced():
    assert bp.CLAIM_EXTRACT_PROMPT_VERSION == "claim_extract_v3"
    assert bp.COVERAGE_PROMPT_VERSION == "coverage_v2"
    assert jb.CLAIM_EXTRACT_PROMPT_VERSION == bp.CLAIM_EXTRACT_PROMPT_VERSION
    assert jb.COVERAGE_PROMPT_VERSION == bp.COVERAGE_PROMPT_VERSION


def test_parse_claims_accepts_only_strict_schema():
    assert bp.parse_claims('{"claims": ["A", " B "]}') == ["A", "B"]
    assert bp.parse_claims('{"claims": []}') == []

    malformed = [
        '```json\n{"claims": ["A"]}\n```',
        'prefix {"claims": ["A"]}',
        '{"claims": "A"}',
        '{"claims": ["A"], "extra": true}',
        '{"claims": ["A", "A"]}',
        '{"claims": [""]}',
        '{"claims": [1]}',
        '{"claims": ["Claim [1]"]}',
        '{"claims": ["A"], "claims": ["B"]}',
    ]
    for raw in malformed:
        with pytest.raises(ValueError):
            bp.parse_claims(raw)


def test_parse_coverage_strict_fields_and_deterministic_aggregation():
    supported = bp.parse_coverage(_coverage_reply())
    assert supported.established is True
    assert supported.engages_subject is True
    assert supported.contradicts is False
    assert supported.unconfirmed_specifics == ()

    specificity_gap = bp.parse_coverage(
        _coverage_reply(unconfirmed=["ApoE-deficient model"])
    )
    assert specificity_gap.established is False
    assert specificity_gap.unconfirmed_specifics == ("ApoE-deficient model",)

    off_topic = bp.parse_coverage(
        _coverage_reply(engages=False, rationale="off topic", evidence_span="")
    )
    assert off_topic.established is False

    assert bp.aggregate_coverage(True, False, []) is True
    assert bp.aggregate_coverage(True, True, []) is False
    assert bp.aggregate_coverage(True, False, ["missing"]) is False


@pytest.mark.parametrize(
    "raw",
    [
        '{"engages_subject":"true","contradicts":false,"unconfirmed_specifics":[],"rationale":"x","evidence_span":""}',
        '{"engages_subject":true,"contradicts":"false","unconfirmed_specifics":[],"rationale":"x","evidence_span":""}',
        '{"engages_subject":false,"contradicts":true,"unconfirmed_specifics":[],"rationale":"x","evidence_span":""}',
        '{"engages_subject":false,"contradicts":false,"unconfirmed_specifics":["x"],"rationale":"x","evidence_span":""}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":"x","rationale":"x","evidence_span":""}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[""],"rationale":"x","evidence_span":""}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":["x","x"],"rationale":"x","evidence_span":""}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"x","evidence_span":null}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":1,"evidence_span":""}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"x","evidence_span":"","established":true}',
        '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"x"}',
        '```json\n{"engages_subject":true}\n```',
        '{"engages_subject":true,"engages_subject":false,"contradicts":false,"unconfirmed_specifics":[],"rationale":"x","evidence_span":""}',
    ],
)
def test_parse_coverage_rejects_malformed_or_coercible_output(raw):
    with pytest.raises(ValueError):
        bp.parse_coverage(raw)


def test_parse_coverage_normalizes_blank_or_null_log_only_rationale():
    """rationale is log-only: a blank or explicit-null value must NOT discard an
    otherwise-valid coverage decision (finding #2). It normalizes to "". The
    exact-5-key schema is still enforced (a MISSING rationale key still fails),
    and a non-string, non-null rationale is still malformed."""
    blank = bp.parse_coverage(_coverage_reply(rationale=""))
    assert blank.established is True and blank.rationale == ""

    nulled = bp.parse_coverage(
        '{"engages_subject":true,"contradicts":false,'
        '"unconfirmed_specifics":[],"rationale":null,"evidence_span":"y"}')
    assert nulled.established is True and nulled.rationale == ""
    assert nulled.evidence_span == "y"

    # A stray "established" key (extra key) still fails closed -- strict schema.
    with pytest.raises(ValueError):
        bp.parse_coverage(
            '{"engages_subject":true,"contradicts":false,'
            '"unconfirmed_specifics":[],"rationale":"x","evidence_span":"",'
            '"established":true}')
    # A missing rationale KEY still fails closed (only its VALUE is lenient).
    with pytest.raises(ValueError):
        bp.parse_coverage(
            '{"engages_subject":true,"contradicts":false,'
            '"unconfirmed_specifics":[],"evidence_span":""}')


@pytest.mark.parametrize("marker_claim,rejected", [
    ("A binds B [1]", True),        # bracketed marker -> rejected (crashes extraction)
    ("A binds B [1,2,3]", True),    # bracketed list -> rejected
    ("A binds B 102", False),       # superscript/bare-number form -> NOT caught
])
def test_citation_marker_policy_is_current_behavior(marker_claim, rejected):
    """Documents CURRENT citation-marker behavior (finding #5); policy unchanged.
    _CITATION_MARKER_RE matches only bracketed forms, so a bracketed marker in a
    claim raises (aborting extraction for the whole sentence) while a superscript
    / bare-number marker -- a form the prompt itself lists -- passes through."""
    raw = json.dumps({"claims": [marker_claim]})
    if rejected:
        with pytest.raises(ValueError, match="citation marker"):
            bp.parse_claims(raw)
    else:
        assert bp.parse_claims(raw) == [marker_claim]


def test_evidence_sufficiency_is_deterministic_and_render_is_abstract_only():
    usable = {
        "cited_abstract": "Title: Correct abstract.",
        "review_reflist": [{"title": "Must stay downstream"}],
    }
    assert bp.evidence_is_usable(usable) is True
    assert bp.render_evidence(usable) == "Title: Correct abstract."
    assert bp.evidence_is_usable("  bare abstract  ") is True
    assert bp.render_evidence("  bare abstract  ") == "bare abstract"

    for missing in [{}, {"cited_abstract": None}, {"cited_abstract": ""},
                    {"cited_abstract": "N/A"}, "(no abstract available)"]:
        assert bp.evidence_is_usable(missing) is False
        assert bp.render_evidence(missing) == "(no abstract available)"


def test_route_from_verdicts_matches_tri_state_contract():
    assert bp.route_from_verdicts([{"established": True}]) == jb.ROUTE_FULL_COVERAGE
    assert bp.route_from_verdicts([{"established": None}]) == jb.ROUTE_HELD
    assert bp.route_from_verdicts(
        [{"established": None}, {"established": False}]
    ) == jb.ROUTE_F6_FLAGGED


def test_prompts_contain_agreed_general_rules_without_natural_citances():
    claim_prompt = bp.CLAIM_EXTRACT_PROMPT
    coverage_prompt = bp.COVERAGE_PROMPT

    assert "result-bearing source descriptions" in claim_prompt
    assert "independent reporting meta-properties" in claim_prompt
    assert "developed for" in claim_prompt
    assert "approved for" in claim_prompt
    assert "used for" in claim_prompt
    assert "Study Q was the first to report" in claim_prompt
    assert 'Do not split alternatives joined by "or"' in claim_prompt
    assert "Treatment A may slow or stop disease progression in mice" in claim_prompt
    assert '{"claims": ["Treatment A may slow or stop disease progression in mice"]}' in claim_prompt

    assert "Semantic paraphrase is allowed" in coverage_prompt
    assert "Do not compose an unstated causal pathway" in coverage_prompt
    assert "Separately establishing the outcome" in coverage_prompt
    assert "normal gut commensals" in coverage_prompt
    assert "intestinal microbiota" in coverage_prompt
    assert "Do NOT output an" in coverage_prompt
    assert '"established" field' in coverage_prompt

    # Natural calibration sentences are not copied into the prompt.
    assert "ADGC" not in claim_prompt
    assert "Prevotella-dominated" not in claim_prompt
    assert "Dietary L-carnitine and choline" not in coverage_prompt


def test_real_band_integration_reads_structured_verdicts_and_routes():
    extraction_reply = (
        '{"claims": ["Drug A improves survival", '
        '"Drug A reduces tumor size in mice"]}'
    )
    coverage_replies = iter([
        _coverage_reply(rationale="abstract states survival",
                        evidence_span="improved survival"),
        _coverage_reply(unconfirmed=["mouse model"],
                        rationale="model is absent", evidence_span=""),
    ])

    def call_llm(prompt):
        if "Citing sentence:" in prompt:
            return extraction_reply
        return next(coverage_replies)

    claims = jb.extract_atomic_claims(
        "Drug A improves survival and reduces tumor size in mice [1].",
        extractor=bp.make_extractor(call_llm),
    )
    verdicts = jb.coverage_verdicts(
        claims,
        {"cited_abstract": "Drug A improved survival."},
        judge=bp.make_coverage_judge(call_llm),
    )
    assert [v["established"] for v in verdicts] == [True, False]
    assert verdicts[0]["rationale"] == "abstract states survival"
    assert verdicts[0]["evidence_span"] == "improved survival"
    assert jb.route(verdicts) == jb.ROUTE_F6_FLAGGED


def test_no_usable_abstract_makes_no_llm_call_and_routes_held():
    calls = []

    def call_llm(prompt):
        calls.append(prompt)
        raise AssertionError("LLM must not be called")

    claims = ["Metformin activates AMPK"]
    verdicts = jb.coverage_verdicts(
        claims,
        {"cited_abstract": None},
        judge=bp.make_coverage_judge(call_llm),
    )
    assert calls == []
    assert [v["established"] for v in verdicts] == [None]
    assert jb.route(verdicts) == jb.ROUTE_HELD

    verbose = bp.judge_coverage_verbose(
        call_llm, claims[0], {"cited_abstract": "N/A"}
    )
    assert verbose.established is None
    assert calls == []


def test_s09_three_claim_contract_and_ordered_vector_through_real_band():
    expected_claims = [
        "Study Q was the first to report that protein P binds receptor R",
        "Study Q reported in 2012 that protein P binds receptor R",
        "Protein P binds receptor R",
    ]
    replies = iter([
        _coverage_reply(unconfirmed=["priority", "Study Q"]),
        _coverage_reply(unconfirmed=["2012", "Study Q"]),
        _coverage_reply(),
    ])

    def call_llm(prompt):
        if "Citing sentence:" in prompt:
            return json.dumps({"claims": expected_claims})
        return next(replies)

    claims = jb.extract_atomic_claims(
        "Study Q was the first to report in 2012 that protein P binds receptor R [1].",
        extractor=bp.make_extractor(call_llm),
    )
    verdicts = jb.coverage_verdicts(
        claims,
        {"cited_abstract": "Protein P binds receptor R."},
        judge=bp.make_coverage_judge(call_llm),
    )
    assert claims == expected_claims
    assert [v["established"] for v in verdicts] == [False, False, True]
    assert jb.route(verdicts) == jb.ROUTE_F6_FLAGGED


def test_anthropic_adapter_omits_temperature_parameter():
    captured = {}

    class Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"content": []})()

    client = type("Client", (), {"messages": Messages()})()
    assert bp.make_anthropic_call(client, "claude-opus-4-8")("prompt") == ""
    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 1024
    assert "temperature" not in captured
