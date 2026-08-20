"""Offline edge and integration tests for the explicit F5 activation gate."""
from __future__ import annotations

import json

import pytest

from .f5_activation import (
    ACTIVATION_SCHEMA_VERSION,
    F5ActivationDecision,
    decide_f5_activation,
)
from .f5_supersession import (
    CandidateWork,
    ComparabilitySource,
    EvidenceTier,
    F5Policy,
    NoticeStatus,
    RetrievalResult,
    decide_f5,
)
from .f5_seams import make_f5_evidence_builder
from .judgment_engine import ClaimSupport, SupportState, TemporalState
from . import judgment_run as jr
from .f5_supersession import validate_f5_record
from .parser import parse_pmc_xml
from .judgment_band import build_item


def _meta(section, **facts):
    return {"source_section": section, **facts}


def test_supported_external_empirical_claim_activates():
    decision = decide_f5_activation(
        "Drug X reduces mortality in adults",
        _meta("Discussion", externally_sourced=True,
              empirically_contradictable=True, own_study_statement=False))
    assert decision == F5ActivationDecision(
        schema_version=ACTIVATION_SCHEMA_VERSION,
        applicability="eligible",
        reason_code="explicit_external_empirical_claim",
        source_section="Discussion",
        externally_sourced=True,
        empirically_contradictable=True,
        own_study_statement=False,
    )
    assert decision.activates is True


def test_current_study_methods_and_results_do_not_activate():
    methods = decide_f5_activation(
        "Samples were stored at -80 C.", _meta("Methods"))
    results = decide_f5_activation(
        "We observed a 12% increase.", _meta("Results"))
    assert methods.applicability == "not_applicable"
    assert methods.reason_code == "clear_current_study_methods"
    assert results.applicability == "not_applicable"
    assert results.reason_code == "clear_current_study_results"
    assert methods.activates is False and results.activates is False


def test_external_literature_claims_activate_inside_discussion_or_methods():
    discussion = decide_f5_activation(
        "Previous studies found Drug X reduced mortality.",
        _meta("Discussion"))
    methods = decide_f5_activation(
        "Smith et al. reported that Drug X increased survival.",
        _meta("Methods"))
    assert discussion.applicability == "eligible"
    assert methods.applicability == "eligible"
    assert discussion.activates and methods.activates


def test_definition_and_bibliographic_statement_do_not_activate():
    definition = decide_f5_activation(
        "Response is defined as a decrease of at least 20%.",
        _meta("Introduction"))
    bibliographic = decide_f5_activation(
        "The paper was published in 2010.", _meta("Background"))
    assert definition.applicability == "not_applicable"
    assert definition.reason_code == "pure_definition"
    assert bibliographic.applicability == "not_applicable"
    assert bibliographic.reason_code == "bibliographic_statement"


def test_definition_or_bibliographic_prefix_cannot_hide_empirical_clause():
    claims = [
        "The study was published in 2010 with results showing Drug X reduced mortality.",
        "Response was defined as a 20% decrease, and Drug X improved response rates.",
    ]
    for claim in claims:
        decision = decide_f5_activation(claim, _meta("Discussion"))
        assert decision.applicability != "not_applicable"
        assert decision.activates


def test_unknown_section_or_ambiguous_ownership_preserves_recall():
    missing = decide_f5_activation("Drug X reduces mortality")
    ambiguous = decide_f5_activation(
        "Drug X reduces mortality", _meta("Results"))
    assert missing.applicability == "uncertain" and missing.activates
    assert ambiguous.applicability == "uncertain" and ambiguous.activates


def test_prior_trial_first_person_is_not_mistaken_for_current_study():
    claims = [
        "In an earlier trial, we found Drug X reduced mortality.",
        "We found previously that Drug X reduced mortality.",
        "In 2010, we found Drug X reduced mortality.",
        "We found earlier that Drug X reduced mortality.",
        "We found in a prior analysis that Drug X reduced mortality.",
        "We found in an earlier analysis that Drug X reduced mortality.",
        "We found in a 2010 trial that Drug X reduced mortality.",
    ]
    for claim in claims:
        decision = decide_f5_activation(claim, _meta("Methods"))
        assert decision.applicability == "uncertain"
        assert decision.activates


def test_clear_current_study_sentences_stop_but_cited_procedures_do_not_exclude():
    current = [
        ("In this study, mortality was 12%.", "Results"),
        ("The study included 42 participants.", "Methods"),
        ("Analyses used R version 4.2.0.", "Methods"),
    ]
    assert all(decide_f5_activation(text, _meta(section)).applicability
               == "not_applicable" for text, section in current)
    cited_procedures = [
        "We increased the sample size based on previous reports.",
        "We reduced the dose according to previous studies.",
    ]
    assert all(decide_f5_activation(text, _meta("Methods")).applicability
               == "uncertain" for text in cited_procedures)


def test_conflicting_explicit_ownership_facts_preserve_recall():
    decision = decide_f5_activation(
        "Drug X reduced mortality.",
        _meta("Discussion", externally_sourced=True,
              empirically_contradictable=True, own_study_statement=True))
    assert decision.applicability == "uncertain"
    assert decision.reason_code == "explicit_ownership_conflict"
    assert decision.activates


def _seams(calls):
    cited = ComparabilitySource(
        abstract="Drug X reduced mortality in adults.")
    candidate = ComparabilitySource(
        abstract="Drug X did not reduce mortality in adults.")

    def retrieve(_meta, _claim, *, after_date, as_of_date):
        calls["retrieve"] += 1
        return RetrievalResult(
            candidates=(CandidateWork(
                id="W2", pub_date="2020-01-01", authors=("Jones",),
                tier_hint="rct"),),
            adequacy="adequate", status="ok", query_hash="qh")

    def fetch(work_id, *, as_of_date):
        return cited if work_id == "W1" else candidate

    def notice(_work_id, *, as_of_date):
        return NoticeStatus()

    def tier(meta):
        return EvidenceTier(meta.get("tier_hint") or meta.get("cited_tier", "rct"))

    def attest(_meta, _claim, _replacement, *, as_of_date):
        return None

    def judge(_cited, _candidate, _claim):
        calls["judge"] += 1
        return json.dumps({
            "directional_contradiction": True,
            "claim_match": "match",
            "outcome_relation": "same",
            "population_relation": "equivalent",
            "cited_direction": "reduces",
            "candidate_direction": "no effect",
            "magnitude": "reversal",
            "cited_finding_span": "Drug X reduced mortality in adults",
            "candidate_contradiction_span": "Drug X did not reduce mortality in adults",
            "confidence": 0.9,
            "scope_mismatch_axis": "none",
        })

    return {
        "retrieve_superseding_candidates": retrieve,
        "fetch_comparability_source": fetch,
        "check_formal_notice": notice,
        "classify_evidence_tier": tier,
        "find_supersession_attestation": attest,
        "judge_contradiction": judge,
    }


def _evidence(claim_meta):
    return {
        "cited_work_id": "W1",
        "cited_meta": {"authors": ["Smith"], "cited_tier": "rct"},
        "cited_date": "2010-01-01",
        "as_of_date": "2024-01-01",
        "claim_meta": claim_meta,
    }


def test_ineligible_claim_makes_zero_retrieval_and_judge_calls():
    calls = {"retrieve": 0, "judge": 0}
    temporal, records = decide_f5(
        ("We recruited 42 participants.",),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        _evidence({0: _meta("Methods")}),
        policy=F5Policy(), **_seams(calls))
    assert calls == {"retrieve": 0, "judge": 0}
    assert temporal.state is TemporalState.NO_QUALIFYING_CONTRADICTION
    assert temporal.rationale == "no F5-applicable supported claim"
    assert records[0]["assessed"] is False
    assert records[0]["not_applicable"] is True
    assert records[0]["activation"]["applicability"] == "not_applicable"
    assert "retrieval_status" not in records[0]
    validate_f5_record(records[0], F5Policy())


def test_not_applicable_record_activation_is_hash_bound():
    calls = {"retrieve": 0, "judge": 0}
    _temporal, records = decide_f5(
        ("We recruited 42 participants.",),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        _evidence({0: _meta("Methods")}),
        policy=F5Policy(), **_seams(calls))
    forged = dict(records[0])
    forged["activation"] = dict(forged["activation"])
    forged["activation"]["applicability"] = "eligible"
    try:
        validate_f5_record(forged, F5Policy())
    except ValueError as exc:
        assert "not_applicable" in str(exc) or "record_sha256" in str(exc)
    else:  # pragma: no cover - the security invariant
        raise AssertionError("tampered activation record validated")


def test_not_applicable_claim_is_neutral_to_an_applicable_f5_result():
    calls = {"retrieve": 0, "judge": 0}
    claims = ("We recruited 42 participants.",
              "Previous studies found Drug X reduced mortality.")
    support = tuple(
        ClaimSupport(i, SupportState.SUPPORTED) for i in range(len(claims)))
    temporal, records = decide_f5(
        claims, support,
        _evidence({0: _meta("Methods"), 1: _meta("Discussion")}),
        policy=F5Policy(), **_seams(calls))
    assert calls == {"retrieve": 1, "judge": 1}
    assert temporal.state is TemporalState.QUALIFYING_CONTRADICTION
    assert temporal.claim_index == 1
    assert records[0]["not_applicable"] is True
    assert records[1]["assessed"] is True
    assert records[1]["activation"]["applicability"] == "eligible"


def test_json_round_tripped_claim_meta_keys_are_honored():
    calls = {"retrieve": 0, "judge": 0}
    _temporal, records = decide_f5(
        ("We recruited 42 participants.",),
        (ClaimSupport(0, SupportState.SUPPORTED),),
        _evidence({"0": _meta("Methods")}),
        policy=F5Policy(), **_seams(calls))
    assert calls == {"retrieve": 0, "judge": 0}
    assert records[0]["activation"]["reason_code"] == \
        "clear_current_study_methods"


def test_manifest_separates_not_applicable_from_searched_negatives():
    records = [
        {"assessed": False, "activation": {
            "schema_version": ACTIVATION_SCHEMA_VERSION,
            "applicability": "not_applicable",
            "reason_code": "clear_current_study_methods",
        }},
        {"assessed": True, "activation": {
            "schema_version": ACTIVATION_SCHEMA_VERSION,
            "applicability": "uncertain",
            "reason_code": "source_section_unavailable",
        }, "retrieval_status": "ok", "retrieval_adequacy": "adequate",
         "candidate_assessments": []},
    ]
    block = jr._f5_manifest_block(
        None, records,
        {"retrieval_calls": 1, "attestation_calls": 0,
         "judge_calls": 0, "retrieval_protocols": []})
    assert block["activation_schema_version"] == ACTIVATION_SCHEMA_VERSION
    assert block["activation_claims_considered"] == 2
    assert block["activation_claims_searched"] == 1
    assert block["activation_claims_activated"] == 1
    assert block["activation_not_applicable_claims"] == 1
    assert block["activation_applicability_counts"] == {
        "not_applicable": 1, "uncertain": 1}
    assert "clear_current_study_methods" in block["activation_reason_counts"]
    assert block["retrieval_status_counts"] == {"ok": 1}


def test_manifest_does_not_call_activation_only_a_search():
    block = jr._f5_manifest_block(None, [{
        "activation": {
            "schema_version": ACTIVATION_SCHEMA_VERSION,
            "applicability": "eligible",
            "reason_code": "explicit_external_empirical_claim",
        },
        "assessed": True,
        "reason": "cited_retracted_upstream_f8_inconsistency",
        "retrieval_status": None,
        "candidate_assessments": [],
    }], {"retrieval_calls": 0, "attestation_calls": 0,
          "judge_calls": 0, "retrieval_protocols": []})
    assert block["activation_claims_activated"] == 1
    assert block["activation_claims_searched"] == 0
    assert block["retrieval_calls"] == 0


def test_live_evidence_builder_preserves_only_explicit_activation_metadata():
    builder = make_f5_evidence_builder(
        lambda _pmid: {"id": "111", "pub_date": "2010-01-01"},
        as_of_date="2024-01-01")
    without = builder({"cited_pmid": "111"})
    assert "claim_meta" not in without

    supplied = {0: _meta("Methods", own_study_statement=True)}
    with_meta = builder({"cited_pmid": "111", "f5_claim_meta": supplied})
    assert with_meta["claim_meta"] == supplied
    assert with_meta["claim_meta"] is not supplied


def test_parser_section_reaches_live_f5_evidence_shape(tmp_path):
    xml = b"""<article><body><sec sec-type='methods'><title>Materials and Methods</title>
    <p>We recruited 42 participants <xref ref-type='bibr' rid='r1'>1</xref>.</p>
    </sec></body><back><ref-list><ref id='r1'><element-citation>
    <article-title>Cited work</article-title><pub-id pub-id-type='pmid'>111</pub-id>
    </element-citation></ref></ref-list></back></article>"""
    path = tmp_path / "PMC1.xml"
    path.write_bytes(xml)
    ref = parse_pmc_xml(str(path), source_pmcid="PMC1")[0]
    assert ref.citance_source_section == "methods"
    item = build_item(ref)
    item["atomic_claims"] = ["We recruited 42 participants"]
    builder = make_f5_evidence_builder(
        lambda _pmid: {"id": "111", "pub_date": "2010-01-01"},
        as_of_date="2024-01-01")
    evidence = builder(item)
    assert evidence["claim_meta"] == {
        0: {"source_section": "methods"}}


def test_typed_parent_section_survives_untyped_nested_title(tmp_path):
    xml = b"""<article><body><sec sec-type='discussion'><title>Discussion</title>
    <sec><title>Previous Results</title><p>We found previously that Drug X reduced
    mortality <xref ref-type='bibr' rid='r1'>1</xref>.</p></sec></sec></body>
    <back><ref-list><ref id='r1'><element-citation><article-title>Cited</article-title>
    <pub-id pub-id-type='pmid'>111</pub-id></element-citation></ref></ref-list></back>
    </article>"""
    path = tmp_path / "PMC1.xml"
    path.write_bytes(xml)
    ref = parse_pmc_xml(str(path), source_pmcid="PMC1")[0]
    assert ref.citance_source_section == "discussion"


@pytest.mark.parametrize("inner", [
    "Previous Results", "Previous Methods", "Literature Review Results",
])
def test_untyped_external_heading_never_becomes_current_results(
        tmp_path, inner):
    xml = f"""<article><body><sec><title>Discussion</title><sec><title>{inner}</title>
    <p>We found Drug X reduced mortality <xref ref-type='bibr' rid='r1'>1</xref>.</p>
    </sec></sec></body><back><ref-list><ref id='r1'><element-citation>
    <article-title>Cited</article-title><pub-id pub-id-type='pmid'>111</pub-id>
    </element-citation></ref></ref-list></back></article>""".encode()
    path = tmp_path / "PMC1.xml"
    path.write_bytes(xml)
    ref = parse_pmc_xml(str(path), source_pmcid="PMC1")[0]
    assert ref.citance_source_section == f"Discussion > {inner}"
    decision = decide_f5_activation(
        "We found Drug X reduced mortality", _meta(ref.citance_source_section))
    assert decision.applicability != "not_applicable"
    assert decision.activates


@pytest.mark.parametrize("sec_type,title,expected", [
    ("methods", "Study Design", "methods"),
    ("results", "Primary Outcome", "results"),
])
def test_explicit_section_type_beats_noncanonical_title(
        tmp_path, sec_type, title, expected):
    xml = f"""<article><body><sec sec-type='{sec_type}'><title>{title}</title>
    <p>We recruited 42 participants <xref ref-type='bibr' rid='r1'>1</xref>.</p>
    </sec></body><back><ref-list><ref id='r1'><element-citation>
    <article-title>Cited</article-title><pub-id pub-id-type='pmid'>111</pub-id>
    </element-citation></ref></ref-list></back></article>""".encode()
    path = tmp_path / "PMC1.xml"
    path.write_bytes(xml)
    assert parse_pmc_xml(str(path), source_pmcid="PMC1")[0].citance_source_section \
        == expected
