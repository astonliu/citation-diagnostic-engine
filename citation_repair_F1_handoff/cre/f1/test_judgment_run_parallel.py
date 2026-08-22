"""Concurrency invariants for the natural-paper judgment orchestrator.

These tests use only injected offline seams.  They deliberately force workers
to complete out of order, then prove that durable output, co-citation state,
scope accounting and the hash-chain domain still commit in parser order.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from . import judgment_run as jr
from .schema import ClaimedRef, Reference


def _ref(index: int, *, sentence: str | None = None) -> Reference:
    return Reference(
        citation_id=f"c{index}",
        citance=sentence or f"Treatment {index} improves outcome {index} [{index}].",
        claimed=ClaimedRef(
            claimed_pmid=str(1000 + index), title=f"Cited title {index}"),
        cited_reference_marker=str(index),
        source_pmcid="PMC1", source_pmid="900", source_title="Citing title",
    )


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _run(tmp_path, monkeypatch, refs, *, max_workers, extractor,
    coverage_judge):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir(parents=True)
    (xml_dir / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: refs)
    out = tmp_path / "out"
    manifest = jr.run_natural_judgment(
        str(xml_dir), str(out), extractor=extractor,
        coverage_judge=coverage_judge,
        fetch_abstract=lambda pmid: f"Abstract for {pmid}.",
        preband_disposition={ref.citation_id: "cleared" for ref in refs},
        model="test-model", max_workers=max_workers)
    return manifest, _rows(out / "judgment_predictions.jsonl"), out


def test_parallel_workers_overlap_but_records_and_chain_commit_in_order(
        tmp_path, monkeypatch):
    refs = [_ref(i) for i in range(1, 7)]
    lock = threading.Lock()
    active = 0
    peak = 0
    finish_active = 0
    finish_peak = 0

    def extractor(sentence):
        return [sentence.split("[")[0].strip()]

    def coverage(claims, _evidence):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        # Earlier references finish later, forcing completion order away from
        # parser order if the orchestrator accidentally commits in workers.
        number = int(claims[0].split()[1])
        time.sleep((7 - number) * 0.004)
        with lock:
            active -= 1
        return [{"established": True, "rationale": "r",
                 "evidence_span": "supported"}]

    original_finish = jr.judge_pair_finish

    def delayed_finish(rec, *args, **kwargs):
        nonlocal finish_active, finish_peak
        with lock:
            finish_active += 1
            finish_peak = max(finish_peak, finish_active)
        time.sleep(0.008)
        try:
            return original_finish(rec, *args, **kwargs)
        finally:
            with lock:
                finish_active -= 1

    monkeypatch.setattr(jr, "judge_pair_finish", delayed_finish)

    manifest, rows, out = _run(
        tmp_path, monkeypatch, refs, max_workers=4,
        extractor=extractor, coverage_judge=coverage)

    expected_ids = [ref.citation_id for ref in refs]
    assert peak >= 2
    assert finish_peak >= 2
    assert [row["citation_id"] for row in rows] == expected_ids
    assert [row["citation_id"] for row in _rows(
        out / "judgment_run_record_hashes.jsonl")] == expected_ids
    assert manifest["chain_record_count"] == len(refs)
    assert manifest["queue_audit"]["matches"] is True
    assert manifest["parallel_execution"] == {
        "max_workers": 4,
        "phase1_workers": 4,
        "phase2_workers": 4,
        "ordered_commit": True,
        "cocitation_barrier_preserved": True,
        "same_sentence_extraction": "ordered_single_flight",
        "f5_f7_phase2_serialized": False,
        "quality_invariants": (
            "Prompts, models, evidence scope, parsers, policies, verifier "
            "gates, per-sentence extraction reuse, terminal filtering, "
            "record order and hash-chain order are unchanged."),
    }


def test_parallel_and_serial_decisions_are_semantically_identical(
        tmp_path, monkeypatch):
    refs = [_ref(i) for i in range(1, 6)]

    def extractor(sentence):
        return [sentence.split("[")[0].strip()]

    def coverage(claims, _evidence):
        number = int(claims[0].split()[1])
        established = number % 2 == 0
        return [{"established": established, "rationale": f"r{number}",
                 "evidence_span": "supported" if established else ""}]

    serial_manifest, serial_rows, _ = _run(
        tmp_path / "serial", monkeypatch, refs, max_workers=1,
        extractor=extractor, coverage_judge=coverage)
    parallel_manifest, parallel_rows, _ = _run(
        tmp_path / "parallel", monkeypatch, refs, max_workers=4,
        extractor=extractor, coverage_judge=coverage)

    def without_wall_clock(rows):
        return [{k: v for k, v in row.items() if k != "ts"} for row in rows]

    assert without_wall_clock(parallel_rows) == without_wall_clock(serial_rows)
    for key in (
            "counts", "marker_scope", "emitted_labels",
            "finding_labels", "scoreable_records", "accounting_ok"):
        assert parallel_manifest[key] == serial_manifest[key]
    assert {
        k: v for k, v in parallel_manifest["cocitation"].items()
        if k != "groups_path"
    } == {
        k: v for k, v in serial_manifest["cocitation"].items()
        if k != "groups_path"
    }


def test_parallel_same_sentence_reuses_success_and_preserves_failure_owner(
        tmp_path, monkeypatch):
    shared = "One shared claim is made [1,2]."
    refs = [_ref(1, sentence=shared), _ref(2, sentence=shared)]
    calls = 0
    lock = threading.Lock()

    def extractor(_sentence):
        nonlocal calls
        with lock:
            calls += 1
            attempt = calls
        if attempt == 1:
            raise ValueError("malformed first extraction")
        return ["One shared claim is made"]

    manifest, rows, _out = _run(
        tmp_path, monkeypatch, refs, max_workers=2, extractor=extractor,
        coverage_judge=lambda claims, _evidence: [
            {"established": True, "rationale": "r", "evidence_span": "s"}
            for _ in claims])

    assert calls == 2
    assert [row["citation_id"] for row in rows] == ["c1", "c2"]
    assert rows[0]["disposition"] == jr.DISP_HELD_CLAIM_EXTRACTION_FAILURE
    assert rows[0]["stage_failures"][0]["stage"] == "claim_extraction"
    assert rows[1]["disposition"] == jr.DISP_HELD_FULL_COVERAGE
    assert manifest["counts"][jr.DISP_HELD_CLAIM_EXTRACTION_FAILURE] == 1


@pytest.mark.parametrize("value", [True, False, 0, -1, 33, 1.5, "4"])
def test_parallel_worker_count_is_strictly_bounded_before_outputs(
        tmp_path, value):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    with pytest.raises(ValueError, match="max_workers"):
        jr.run_natural_judgment(
            str(xml_dir), str(tmp_path / "out"),
            extractor=lambda _s: [], coverage_judge=lambda _c, _e: [],
            fetch_abstract=lambda _p: None, max_workers=value)
    assert not (tmp_path / "out").exists()
