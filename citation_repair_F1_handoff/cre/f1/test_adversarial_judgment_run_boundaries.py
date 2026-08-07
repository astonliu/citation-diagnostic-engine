"""Adversarial tri-state and blind-output guards for judgment_run."""
from __future__ import annotations

import json

import pytest

from cre.f1 import judgment_run as jr
from cre.f1.judgment_engine import (
    DiscriminatorContractError,
    SupportState,
    from_legacy_coverage,
)
from cre.f1.schema import ClaimedRef, Reference


def _item():
    return {
        "citation_id": "PMC1:R1",
        "citing_pmcid": "PMC1",
        "citing_pmid": "9",
        "citing_sentence": "Drug X changes outcome Y [1].",
        "cited_pmid": "1",
        "cited_claimed": {"title": "Cited", "claimed_pmid": "1"},
    }


@pytest.mark.parametrize("established,state", [
    (True, SupportState.SUPPORTED),
    (False, SupportState.UNESTABLISHED),
    (None, SupportState.UNJUDGEABLE),
])
def test_legacy_adapter_preserves_each_exact_tristate(established, state):
    row = from_legacy_coverage(("claim",), ({"established": established},))[0]
    assert row.state is state


def test_legacy_adapter_rejects_missing_established_instead_of_treating_it_as_none():
    with pytest.raises(DiscriminatorContractError, match="established field"):
        from_legacy_coverage(("claim",), ({"rationale": "missing"},))


@pytest.mark.parametrize("bad", [0, 1, False, [], {}, b"span"])
def test_legacy_adapter_rejects_non_string_evidence_spans(bad):
    with pytest.raises(DiscriminatorContractError, match="evidence_span"):
        from_legacy_coverage(("claim",), ({"established": True, "evidence_span": bad},))


@pytest.mark.parametrize(
    "established,expected_disposition,expected_label",
    [
        (True, jr.DISP_HELD_FULL_COVERAGE, None),
        (False, jr.DISP_PREDICTED, "F6"),
        (None, jr.DISP_HELD_INSUFFICIENT, None),
    ],
)
def test_judge_pair_routes_true_false_and_none_without_truthiness(
        established, expected_disposition, expected_label):
    record = jr.judge_pair(
        _item(),
        extractor=lambda _sentence: ["Drug X changes outcome Y"],
        coverage_judge=lambda _claims, _evidence: [{"established": established}],
        fetch_abstract=lambda _pmid: "An abstract about Drug X and outcome Y.",
    )
    assert record["coverage_verdicts"][0]["established"] is established
    assert record["disposition"] == expected_disposition
    assert record["label"] == expected_label


def _forbidden_paths(value, path=()):
    forbidden = {"proposed_route", "proposed_verdict", "rationale"}
    found = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in forbidden:
                found.append(path + (key,))
            found.extend(_forbidden_paths(nested, path + (str(key),)))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_forbidden_paths(nested, path + (str(index),)))
    return found


def test_orchestrator_queue_cannot_surface_any_stage_proposal(tmp_path, monkeypatch):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC1.xml").write_text("<article/>", encoding="utf-8")
    ref = Reference(
        citation_id="PMC1:R1",
        citance="Drug X changes outcome Y [1].",
        claimed=ClaimedRef(claimed_pmid="1", title="Cited"),
        source_pmcid="PMC1",
        source_pmid="9",
        source_title="Citing",
    )
    monkeypatch.setattr(jr, "parse_pmc_xml", lambda *_args, **_kwargs: [ref])

    def contaminated(item, **_kwargs):
        record = jr._new_record(item)
        record.update({
            "preband_cleared": True,
            "disposition": jr.DISP_PREDICTED,
            "label": "F7",
            "findings": ["F7"],
            "atomic_claims": [{"text": "claim", "proposed_verdict": "F7",
                               "rationale": "claim rationale"}],
            "evidence": {
                "cited_pmid": "1",
                "cited_abstract": "Abstract.",
                "cited_is_review": True,
                "review_reflist": [{
                    "title": "Primary",
                    "proposed_route": "F7_FLAGGED",
                    "nested": {"proposed_verdict": "F7", "rationale": "secret"},
                }],
                "review_fulltext_available": True,
            },
            "strength_records": [{"derived": "F4", "rationale": "F4"}],
            "provenance": {"state": "MISATTRIBUTED_CONFIRMED", "rationale": "F3"},
            "f5_records": [{"temporal_state": "QUALIFYING_CONTRADICTION"}],
            "f7_records": [{"derived": "DIFFERENT_ENTITY_SUPPORTED",
                            "proposed_corrected_label": "other"}],
            "proposed_route": "F7_FLAGGED",
            "proposed_verdict": "F7",
            "rationale": "outer secret",
        })
        return record

    monkeypatch.setattr(jr, "judge_pair", contaminated)
    out = tmp_path / "out"
    jr.run_natural_judgment(
        str(xml_dir), str(out),
        extractor=lambda _sentence: ["claim"],
        coverage_judge=lambda _claims, _evidence: [{"established": True}],
        fetch_abstract=lambda _pmid: "Abstract.",
        preband_disposition={"PMC1:R1": "cleared"},
    )
    payloads = [json.loads(line) for line in
                (out / "judgment_band_annotation_queue.jsonl").read_text().splitlines()]
    assert len(payloads) == 1
    assert _forbidden_paths(payloads[0]) == []
    assert "strength_records" not in payloads[0]
    assert "provenance" not in payloads[0]
    assert "f5_records" not in payloads[0]
    assert "f7_records" not in payloads[0]
    assert payloads[0]["evidence"]["review_reflist"][0]["title"] == "Primary"
