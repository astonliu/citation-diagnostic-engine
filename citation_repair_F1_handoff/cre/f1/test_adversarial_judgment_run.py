"""Adversarial type-boundary and blindness tests for the orchestrator."""
from __future__ import annotations

import json

import pytest

from cre.f1 import judgment_run as jr
from cre.f1.judgment_engine import SupportState, from_legacy_coverage
from cre.f1.schema import ClaimedRef, Reference


@pytest.mark.parametrize("value,state", [(True, SupportState.SUPPORTED),
                                           (False, SupportState.UNESTABLISHED),
                                           (None, SupportState.UNJUDGEABLE)])
def test_legacy_coverage_preserves_the_exact_tri_state(value, state):
    rows = from_legacy_coverage(("claim",), ({"established": value},))
    assert rows[0].state is state


@pytest.mark.parametrize("bad", ["true", 0, 1, [], {}])
def test_legacy_coverage_rejects_non_tri_state_values(bad):
    with pytest.raises(ValueError):
        from_legacy_coverage(("claim",), ({"established": bad},))


def test_orchestrator_record_contains_no_annotation_payload_or_proposed_route():
    item = {"citation_id": "PMC1:R1", "citing_sentence": "claim [1]", "cited_pmid": "1",
            "cited_claimed": {}, "proposed_route": "F6_FLAGGED", "proposed_verdict": "F6"}
    rec = jr._new_record(item)
    assert "proposed_route" not in rec
    assert "proposed_verdict" not in rec


def _ref(cid, pmid):
    return Reference(
        citation_id=cid, citance=f"Claim for {pmid} [1].",
        claimed=ClaimedRef(claimed_pmid=pmid, title="T"),
        source_pmcid="PMC1", source_pmid="9", source_title="Citing")


@pytest.mark.xfail(strict=True, reason="checkpoint advances only after the whole document")
def test_mid_document_interrupt_resume_does_not_duplicate_chain_or_queue(
        tmp_path, monkeypatch):
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC1.xml").write_text("<article/>", encoding="utf-8")
    refs = [_ref("PMC1:R1", "1"), _ref("PMC1:R2", "2")]
    monkeypatch.setattr(jr, "parse_pmc_xml", lambda *a, **k: refs)
    out = tmp_path / "out"
    calls = 0
    def interrupted(sentence):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return [sentence]
    common = dict(
        coverage_judge=lambda claims, evidence: [
            {"established": True, "rationale": "r", "evidence_span": "abstract"}
            for _ in claims],
        fetch_abstract=lambda _: "abstract",
        preband_disposition={"PMC1:R1": "cleared", "PMC1:R2": "cleared"})
    with pytest.raises(KeyboardInterrupt):
        jr.run_natural_judgment(str(xml_dir), str(out), extractor=interrupted, **common)
    jr.run_natural_judgment(str(xml_dir), str(out), extractor=lambda s: [s], **common)
    predictions = [json.loads(line) for line in
                   (out / "judgment_predictions.jsonl").read_text().splitlines()]
    queue = [json.loads(line) for line in
             (out / "judgment_band_annotation_queue.jsonl").read_text().splitlines()]
    prediction_ids = [row["citation_id"] for row in predictions]
    queue_ids = [row["item_key"] for row in queue]
    assert len(prediction_ids) == len(set(prediction_ids)) == 2
    assert len(queue_ids) == len(set(queue_ids)) == 2


@pytest.mark.xfail(strict=True, reason="resumed manifest counters cover only the final invocation")
def test_resumed_manifest_counts_cover_the_whole_chain(tmp_path, monkeypatch):
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC1.xml").write_text("<article/>", encoding="utf-8")
    (xml_dir / "PMC2.xml").write_text("<article/>", encoding="utf-8")
    def parse(path, source_pmcid=None):
        return [_ref(f"{source_pmcid}:R1", source_pmcid[-1])]
    monkeypatch.setattr(jr, "parse_pmc_xml", parse)
    out = tmp_path / "out"
    common = dict(
        extractor=lambda s: [s],
        coverage_judge=lambda claims, evidence: [{"established": True} for _ in claims],
        fetch_abstract=lambda _: "abstract",
        preband_disposition={"PMC1:R1": "cleared", "PMC2:R1": "cleared"})
    jr.run_natural_judgment(str(xml_dir), str(out), max_docs=1, **common)
    manifest = jr.run_natural_judgment(str(xml_dir), str(out), **common)
    rows = (out / "judgment_predictions.jsonl").read_text().splitlines()
    assert manifest["total_records"] == manifest["refs_seen"] == len(rows)
