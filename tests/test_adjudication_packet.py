"""Regression guards for the offline F3--F7 adjudication packet."""
from __future__ import annotations

import copy
import json

import pytest

from cde.claims import adjudication_packet as ap


def manifest(**overrides):
    value = {
        "status": "complete",
        "model": "model-x",
        "effort": "high",
        "evidence_scope_effective": "fulltext_sections",
        "claim_extract_prompt_version": "claims-v1",
        "coverage_prompt_version": "coverage-v3",
        "f3": {"origin_sensitive_prompt_version": "f3-v2"},
        "f4": {"strength_prompt_version": "f4-v1"},
        "code_commit": "abc123",
        "chain_tip": "tip123",
        # Deliberately false/empty: the packet must not treat these as truth.
        "emitted_labels": {},
        "seam_status": {},
    }
    value.update(overrides)
    return value


def base_record(cid, claim, *, findings, verdict=None):
    return {
        "citation_id": cid,
        "citing_sentence": f"Sentence for {cid} [1].",
        "cited_pmid": cid.rsplit(":", 1)[-1].replace("B", "") or "1",
        "cited_claimed": {"title": f"Cited title {cid}"},
        "findings": list(findings),
        "atomic_claims": [claim],
        "coverage_verdicts": [verdict or {
            "claim": claim, "established": True, "rationale": "covered",
            "evidence_spans": [{"label": "results", "sentence_ids": ["s1"],
                                "text": "Evidence text."}],
        }],
        "strength_records": [{"claim_index": 0, "assessed": False}],
    }


def all_label_records():
    f3 = base_record("PMC1:B3", "claim f3", findings=["F3"])
    f3["provenance"] = {
        "state": "MISATTRIBUTED_CONFIRMED", "rationale": "wrong origin",
        "evidence_spans": ["Review restates the finding."],
    }

    f4 = base_record("PMC1:B4", "claim f4", findings=[])
    # The dropped-F4 regression: the record-level label stream need not save it.
    f4["strength_records"] = [{
        "claim_index": 0, "assessed": True, "derived": "F4",
        "reason": "weaker_strength", "model_rationale": "claim is stronger",
        "citing_strength_span": "strongly prevents",
        "cited_strength_span": "may reduce",
    }]

    f5 = base_record("PMC1:B5", "claim f5", findings=["F5"])
    f5["f5_records"] = [{
        "claim_index": 0, "temporal_state": "QUALIFYING_CONTRADICTION",
        "reason": "qualifying_contradiction",
        "cited_finding_span": "Earlier finding.",
        "candidate_contradiction_span": "Later contradiction.",
    }]

    f6 = base_record("PMC1:B6", "claim f6", findings=["F6"], verdict={
        "claim": "claim f6", "established": False,
        "engages_subject": False, "contradicts": False,
        "rationale": "the paper does not engage the subject",
        "evidence_spans": [],
    })
    f6["citance_group_members"] = ["PMC1:B6", "PMC1:B60", "PMC1:B61"]
    f6["citance_group_inferred_members"] = ["PMC1:B61"]

    f7 = base_record("PMC1:B7", "claim f7", findings=["F7"])
    f7["f7_records"] = [{
        "claim_index": 0, "derived": "DIFFERENT_ENTITY_SUPPORTED",
        "reason": "different_entity_supported",
        "tuple_records": [{
            "derived": "CONFIRMED_MISMATCH", "confirmed_mismatch": True,
            "reason": "confirmed_mismatch", "entity_span": "Entity Y",
            "entity_section_label": "results",
            "relation_span": "Entity Y reduced outcome Z",
            "relation_section_label": "results",
            # These must never be copied into the packet.
            "proposed_corrected_id": "Y", "proposed_corrected_label": "Entity Y",
        }],
    }]
    return [f3, f4, f5, f6, f7]


def test_every_actual_f3_f7_finding_appears_even_when_emitted_labels_are_empty():
    packet = ap.build_packet(all_label_records(), manifest())
    assert [row["labels"] for row in packet["rows"]] == [
        ["F3"], ["F4"], ["F5"], ["F6"], ["F7"]]


def test_no_span_is_visible_and_inferred_siblings_are_distinct():
    packet = ap.build_packet(all_label_records(), manifest())
    row = next(row for row in packet["rows"] if row["labels"] == ["F6"])
    assert row["findings"]["F6"]["evidence_spans"] == []
    assert row["co_cited_siblings"] == [
        {"citation_id": "PMC1:B60", "provenance": "asserted"},
        {"citation_id": "PMC1:B61", "provenance": "inferred"},
    ]
    rendered = ap.render_markdown(packet)
    assert ap.NO_SPAN_RECORDED in rendered
    assert "`PMC1:B61` (inferred)" in rendered


def test_row_id_is_stable_across_rerun_metadata_and_record_position():
    records = all_label_records()
    first = ap.build_packet(records, manifest())
    changed = copy.deepcopy(records)
    for record in changed:
        record["ts"] = 999999999
    second = ap.build_packet(list(reversed(changed)), manifest(chain_tip="other-tip"))
    first_ids = {(row["citation_id"], row["claim_index"]): row["row_id"]
                 for row in first["rows"]}
    second_ids = {(row["citation_id"], row["claim_index"]): row["row_id"]
                  for row in second["rows"]}
    assert first_ids == second_ids
    assert len(set(first_ids.values())) == len(first_ids)


def test_rows_preserve_prediction_then_claim_order_and_merge_multiple_labels():
    rec = base_record("PMC2:B1", "claim zero", findings=["F6", "F4"], verdict={
        "claim": "claim zero", "established": False, "contradicts": True,
        "rationale": "contradicted", "evidence_span": "Counterevidence.",
    })
    # This synthetic collision proves one claim stays one stable row. F4's real
    # engine path normally refines only supported claims, but packet assembly
    # must not duplicate an id if an artifact ever carries two findings.
    rec["strength_records"] = [{
        "claim_index": 0, "assessed": True, "derived": "F4",
        "reason": "weaker_strength", "cited_strength_span": "weaker",
    }]
    rec2 = copy.deepcopy(rec)
    rec2["citation_id"] = "PMC2:B2"
    rec2["atomic_claims"] = ["first", "second"]
    rec2["coverage_verdicts"] = [
        {"claim": "first", "established": True, "rationale": "ok"},
        {"claim": "second", "established": False, "contradicts": True,
         "rationale": "bad"},
    ]
    rec2["findings"] = ["F6"]
    rec2["strength_records"] = []
    packet = ap.build_packet([rec, rec2], manifest())
    assert [(row["citation_id"], row["claim_index"]) for row in packet["rows"]] == [
        ("PMC2:B1", 0), ("PMC2:B2", 1)]
    assert packet["rows"][0]["labels"] == ["F6", "F4"]


@pytest.mark.parametrize("mutate,match", [
    (lambda row: row.update(findings=["F5"]), "F5"),
    (lambda row: row.update(findings=["F7"]), "F7"),
    (lambda row: row.update(findings=["F9"]), "unsupported finding"),
])
def test_a_finding_that_cannot_make_a_row_raises(mutate, match):
    row = base_record("PMC3:B1", "claim", findings=[])
    mutate(row)
    with pytest.raises(ap.PacketBuildError, match=match):
        ap.build_packet([row], manifest())


def test_manifest_identity_and_completed_state_are_enforced():
    with pytest.raises(ap.PacketBuildError, match="completed"):
        ap.build_packet([], manifest(status="in_progress"))
    with pytest.raises(ap.PacketBuildError, match="code commit"):
        ap.build_packet([], manifest(code_commit=""))
    packet = ap.build_packet([], manifest(effort=None))
    assert packet["header"]["effort"] == ap.EFFORT_NOT_RECORDED


def test_renderer_has_no_bias_fields_or_values_copied_from_detector_records():
    packet = ap.build_packet(all_label_records(), manifest())
    rendered = ap.render_markdown(packet).casefold()
    for forbidden in (
            "proposed_corrected_id", "proposed_corrected_label",
            "proposed_route", "proposed_verdict", "confidence", "score",
            "rank"):
        assert forbidden not in rendered
    assert "entity y reduced outcome z" in rendered


def test_file_api_reads_jsonl_and_writes_markdown(tmp_path):
    predictions = tmp_path / "judgment_predictions.jsonl"
    run_manifest = tmp_path / "judgment_run_manifest.json"
    output = tmp_path / "packet.md"
    predictions.write_text(
        "".join(json.dumps(row) + "\n" for row in all_label_records()),
        encoding="utf-8")
    run_manifest.write_text(json.dumps(manifest()), encoding="utf-8")
    packet = ap.write_packet(predictions, run_manifest, output)
    assert len(packet["rows"]) == 5
    assert output.read_text(encoding="utf-8").startswith(
        "# F3-F7 adjudication packet\n")
