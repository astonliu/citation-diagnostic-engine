"""Offline tests for the strict Band-1 -> Band-2 production entrypoint."""
from __future__ import annotations

import hashlib
import importlib
import json

import pytest

from . import production_launcher as pl
from .recording_adapter import AdapterReceipt


def _corpus(tmp_path):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    raw = b"<article/>"
    (xml_dir / "PMC1.xml").write_bytes(raw)
    manifest = tmp_path / "corpus.json"
    manifest.write_text(json.dumps({
        "documents": {"PMC1.xml": hashlib.sha256(raw).hexdigest()}
    }), encoding="utf-8")
    return xml_dir, manifest


def _run_seams():
    fn = lambda *args, **kwargs: None
    return {
        "extractor": fn,
        "coverage_judge": fn,
        "coverage_judge_v3": fn,
        "fetch_abstract": fn,
        "fetch_fulltext": fn,
        "discriminator_call_llm": fn,
        "f3_fetch_reflist": fn,
        "f3_resolve_pmcid": fn,
        "pubtypes_lookup": fn,
    }


def test_full_launch_refuses_an_unreachable_taxonomy_before_output(tmp_path):
    xml_dir, corpus_manifest = _corpus(tmp_path)
    with pytest.raises(pl.LaunchRefused, match="missing required callable"):
        pl.launch_full(
            repo_dir="/repo", pkg_dir="/pkg", xml_dir=str(xml_dir),
            out_dir=str(tmp_path / "out"),
            corpus_manifest_path=str(corpus_manifest), model="model",
            authorized_models=["model"],
            adapter_receipt=AdapterReceipt(model="model"),
            band1_snapshot_date="2026-08-20")
    assert not (tmp_path / "out").exists()


def test_full_launch_builds_and_consumes_current_band1_artifact(
        tmp_path, monkeypatch):
    xml_dir, corpus_manifest = _corpus(tmp_path)
    out_dir = tmp_path / "out"
    receipt = AdapterReceipt(model="model", temperature=0)
    captured = {}

    monkeypatch.setattr(pl, "verify_tree", lambda *_a, **_k: {
        "code_commit": "a" * 40, "runtime_module_sha256": {}})
    monkeypatch.setattr(pl, "verify_judge_governance", lambda **_k: {})
    f5s = importlib.import_module(pl.__package__ + ".f5_seams")
    f7s = importlib.import_module(pl.__package__ + ".f7_seams")
    monkeypatch.setattr(f5s, "validate_production_f5_configuration",
                        lambda **_k: None)
    monkeypatch.setattr(f7s, "validate_production_f7_configuration",
                        lambda **_k: None)

    band1 = importlib.import_module(pl.__package__ + ".run")

    def fake_band1(_xml, dataset, logs, **kwargs):
        captured["band1"] = kwargs
        kwargs["complete"]("prompt")
        with open(dataset, "w", encoding="utf-8") as fh:
            fh.write("{}\n")
        row = {
            "citation_id": "PMC1:R1", "label": "F2",
            "claimed": {"claimed_pmid": "", "claimed_doi": "10.1/x"},
            "retrieved": {"resolved": True, "pmid": "123"},
            "log": {"decided_by": "exact_doi_metadata_mismatch_f2",
                    "retracted": False, "f8_timing_status": "clear"},
        }
        with open(logs, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        return {"F2": 1}

    monkeypatch.setattr(band1, "run", fake_band1)

    def fake_launch(**kwargs):
        captured["launch"] = kwargs
        manifest_path = out_dir / "judgment" / "judgment_run_manifest.json"
        manifest_path.parent.mkdir(parents=True)
        return {
            "manifest_path": str(manifest_path),
            "predictions_path": str(out_dir / "judgment" / "predictions.jsonl"),
        }

    monkeypatch.setattr(pl, "launch", fake_launch)
    marker = object()
    manifest = pl.launch_full(
        repo_dir="/repo", pkg_dir="/pkg", xml_dir=str(xml_dir),
        out_dir=str(out_dir), corpus_manifest_path=str(corpus_manifest),
        model="model", authorized_models=["model"],
        adapter_receipt=receipt, band1_snapshot_date="2026-08-20",
        judge_model="other-model", temperature=0,
        f1_complete=lambda _p: "{}",
        f5_seams=marker, f5_evidence_builder=marker, f5_policy=marker,
        f7_seams=marker, f7_evidence_builder=marker, f7_policy=marker,
        **_run_seams())

    disposition = captured["launch"]["preband_disposition"]
    assert disposition.startswith(str(out_dir / "band1"))
    assert json.loads(open(disposition, encoding="utf-8").readline())["label"] == "F2"
    assert captured["band1"]["complete"] is not None
    assert any(row["seam"] == "f1_llm_filter" for row in receipt.calls)
    assert manifest["full_launch"]["all_taxonomies_wired"] is True
    durable = json.loads((out_dir / "judgment" /
                          "judgment_run_manifest.json").read_text())
    assert durable["full_launch"]["band1_label_counts"] == {"F2": 1}
