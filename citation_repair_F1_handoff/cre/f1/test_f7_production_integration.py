"""Offline end-to-end tests for production F7 wiring and parallel execution."""
from __future__ import annotations

import json

import pytest

from . import judgment_run as jr
from .f7_evidence_builder import ProductionF7EvidenceBuilder
from .f7_entity import validate_f7_record
from .f7_seams import make_production_f7_policy, make_production_f7_seams
from .schema import ClaimedRef, Reference
from .test_f7_production_seams import alias, make_normalizer, record
from . import production_launcher as pl
from .test_production_launcher import base
from .recording_adapter import AdapterReceipt


CLAIM = "BRCA1 suppresses tumor growth in mice"
RESULTS = "In our study, BRCA2 suppresses tumor growth in mice."
METHODS = "BRCA2 was genotyped before treatment."


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode()).hexdigest()


def fulltext(pmid):
    sections = [
        {"label": "results", "title": "Results", "text": RESULTS,
         "content_sha256": _sha(RESULTS)},
        {"label": "methods", "title": "Methods", "text": METHODS,
         "content_sha256": _sha(METHODS)},
        {"label": "discussion", "title": "Discussion", "text": "Interpretation.",
         "content_sha256": _sha("Interpretation.")},
    ]
    return {
        "pmid": pmid, "pmcid": f"PMC{pmid}", "resolved": True,
        "sections": sections,
        "sections_present": ["discussion", "methods", "results"],
        "retrieval_complete": True, "incomplete_reasons": [],
        "sanitized_paths": [], "source": "fixture",
    }


def ref(index, claim=CLAIM):
    return Reference(
        citation_id=f"c{index}",
        citance=f"{claim} [{index}].",
        claimed=ClaimedRef(claimed_pmid=str(1000 + index), title="Cited"),
        cited_reference_marker=str(index), source_pmcid="PMC9",
        source_pmid="9", source_title="Citing")


def relation_reply():
    return json.dumps({
        "predicate": "match", "object": "match", "direction": "match",
        "population": "match", "rationale": "equivalent relation"})


def transports(*, claim_surface="BRCA1", claim_type="gene",
               evidence_surface="BRCA2", evidence_type="gene",
               sibling=False, verifier_differ=True):
    claim_text = CLAIM.replace("BRCA1", claim_surface)

    def generator(prompt):
        if "Return ONLY a JSON array" in prompt:
            return json.dumps([{
                "tuple_id": 0, "entity_type": claim_type,
                "claim_surface": claim_surface, "clause_span": claim_text,
                "predicate": "suppresses", "object": "tumor growth",
                "direction": "negative", "population": "mice"}])
        if "how ONE clause of a citing sentence" in prompt:
            return json.dumps({
                "attribution": "direct", "target_supported": True,
                "sibling_reference_possible": sibling, "rationale": "direct"})
        if "read the cited work's OWN body sections" in prompt:
            return json.dumps({
                "entity_type": evidence_type,
                "evidence_surface": evidence_surface,
                "entity_section_sha256": _sha(RESULTS),
                "entity_span": evidence_surface,
                "relation_section_sha256": _sha(RESULTS),
                "relation_span": "suppresses tumor growth in mice",
                "predicate": "suppresses", "object": "tumor growth",
                "direction": "negative", "population": "mice",
                "papers_own_finding": True, "rationale": "own result"})
        if "compare the relation tuple" in prompt:
            return relation_reply()
        raise AssertionError(prompt[:100])

    def verifier(_prompt):
        return json.dumps({
            "entities_genuinely_differ": verifier_differ,
            "papers_own_finding": True, "direct_attribution": True,
            "relation_tuple_equivalent": True,
            "all_load_bearing_tuples_enumerated": True,
            "rationale": "verified"})

    generator.thread_safe = True
    verifier.thread_safe = True
    return generator, verifier


def _run(tmp_path, monkeypatch, normalizer, *, refs=None, max_workers=1,
         transport_kw=None, coverage_state=True):
    transport_kw = transport_kw or {}
    claim_text = CLAIM.replace("BRCA1", transport_kw.get("claim_surface", "BRCA1"))
    refs = list(refs or [ref(1, claim=claim_text)])
    xml = tmp_path / "xml"
    xml.mkdir(parents=True)
    (xml / "PMC9.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: refs)
    gen, ver = transports(**transport_kw)
    receipt = AdapterReceipt(model="test-model")
    seams = make_production_f7_seams(
        generator_transport=gen, verifier_transport=ver,
        normalizer=normalizer, adapter_receipt=receipt,
        max_parallel=max_workers)
    policy = make_production_f7_policy(
        normalizer, generator_model_id="test-model", verifier_model_id="test-model")
    out = tmp_path / "out"
    manifest = jr.run_natural_judgment(
        str(xml), str(out), extractor=lambda sentence: [
            sentence.split("[")[0].strip()],
        coverage_judge=lambda claims, ev: [],
        coverage_judge_v3=lambda claims, ev: [
            {"established": coverage_state, "rationale": "coverage",
             "evidence_spans": [], "engages_subject": coverage_state is not None,
             "contradicts": coverage_state is False,
             "unconfirmed_specifics": []} for _ in claims],
        fetch_abstract=lambda pmid: "abstract", fetch_fulltext=fulltext,
        preband_disposition={row.citation_id: "cleared" for row in refs},
        model="test-model", max_workers=max_workers,
        f7_seams=seams,
        f7_evidence_builder=ProductionF7EvidenceBuilder(), f7_policy=policy)
    rows = [json.loads(line) for line in
            (out / "judgment_predictions.jsonl").read_text().splitlines()]
    return manifest, rows, out


def test_genuine_same_type_sibling_mismatch_emits_source_bound_replay_valid_f7(
        tmp_path, monkeypatch):
    normalizer = make_normalizer(tmp_path / "auth")
    manifest, rows, _ = _run(tmp_path / "run", monkeypatch, normalizer)
    row = rows[0]
    assert row["label"] == "F7"
    assert row["findings"][0] == "F7"
    assert manifest["seam_status"]["F7"]["fired"] == 1
    item = {
        "citation_id": "c1", "cited_pmid": "1001",
        "citing_sentence": f"{CLAIM} [1].", "atomic_claims": [CLAIM],
        "citance_group_members": [],
        "evidence": {"cited_fulltext": fulltext("1001")},
    }
    validate_f7_record(row["f7_records"][0], ProductionF7EvidenceBuilder()(item))


def test_replay_rejects_rehashed_wrong_work_binding(tmp_path, monkeypatch):
    import copy
    from .f7_entity import record_sha256

    normalizer = make_normalizer(tmp_path / "auth")
    _manifest, rows, _ = _run(tmp_path / "run", monkeypatch, normalizer)
    record_row = copy.deepcopy(rows[0]["f7_records"][0])
    item = {
        "citation_id": "c1", "cited_pmid": "1001",
        "citing_sentence": f"{CLAIM} [1].", "atomic_claims": [CLAIM],
        "citance_group_members": [],
        "evidence": {"cited_fulltext": fulltext("1001")},
    }
    context = ProductionF7EvidenceBuilder()(item)
    record_row["resolved_work_id"] = "PMC-WRONG-WORK"
    record_row["record_sha256"] = record_sha256(record_row)
    with pytest.raises(ValueError, match="resolved_work_id"):
        validate_f7_record(record_row, context)


def test_exact_synonym_ambiguity_verifier_disagreement_and_cross_type_hold(
        tmp_path, monkeypatch):
    cases = []
    # Accepted HGNC synonym BRCC1 and evidence BRCA1 are equivalent.
    synonym_rows = [record("HGNC:1100", "BRCA1"),
                    record("HGNC:1101", "BRCA2", [alias("BRCC2")])]
    cases.append((make_normalizer(tmp_path / "syn", gene_records=synonym_rows),
                  {"claim_surface": "BRCC2", "evidence_surface": "BRCA2"}))
    # One surface points at two HGNC ids: normalization must hold.
    ambiguous = [record("HGNC:1100", "BRCA1", [alias("AMB")]),
                 record("HGNC:1101", "BRCA2", [alias("AMB")])]
    cases.append((make_normalizer(tmp_path / "amb", gene_records=ambiguous),
                  {"claim_surface": "AMB"}))
    # Independent verifier disagreement holds.
    cases.append((make_normalizer(tmp_path / "ver"),
                  {"verifier_differ": False}))
    # Cross-type difference never emits F7.
    cases.append((make_normalizer(tmp_path / "cross"),
                  {"claim_surface": "Drug A", "claim_type": "drug"}))
    for index, (normalizer, kw) in enumerate(cases):
        _manifest, rows, _ = _run(
            tmp_path / f"case{index}", monkeypatch, normalizer,
            transport_kw=kw)
        assert rows[0].get("label") != "F7"
        assert "F7" not in rows[0]["findings"]
        assert rows[0]["f7_records"][0]["derived"] == "UNJUDGEABLE" or \
            rows[0]["f7_records"][0]["derived"] == "SAME_ENTITY"


@pytest.mark.parametrize("support", [True, False, None])
def test_production_f7_runs_under_coverage_support_states(
        tmp_path, monkeypatch, support):
    normalizer = make_normalizer(tmp_path / "auth")
    _manifest, rows, _ = _run(
        tmp_path / "run", monkeypatch, normalizer, coverage_state=support)
    assert rows[0]["label"] == "F7"


def test_parallel_and_serial_f7_results_order_and_counters_are_identical(
        tmp_path, monkeypatch):
    refs = [ref(i) for i in range(1, 7)]
    serial_norm = make_normalizer(tmp_path / "serial_auth")
    parallel_norm = make_normalizer(tmp_path / "parallel_auth")
    serial_manifest, serial_rows, _ = _run(
        tmp_path / "serial", monkeypatch, serial_norm, refs=refs, max_workers=1)
    parallel_manifest, parallel_rows, _ = _run(
        tmp_path / "parallel", monkeypatch, parallel_norm, refs=refs, max_workers=4)
    clean = lambda rows: [{k: v for k, v in row.items() if k != "ts"} for row in rows]
    assert clean(serial_rows) == clean(parallel_rows)
    assert [r["citation_id"] for r in parallel_rows] == [r.citation_id for r in refs]
    for key in ("counts", "emitted_labels", "finding_labels", "seam_status"):
        assert serial_manifest[key] == parallel_manifest[key]
    assert parallel_manifest["parallel_execution"]["f7_thread_safe_parallel"] is True


def test_half_wired_f7_refuses_before_output_files(tmp_path):
    with pytest.raises(ValueError, match="half-wired F7"):
        jr.run_natural_judgment(
            str(tmp_path), str(tmp_path / "out"), extractor=lambda _s: [],
            coverage_judge=lambda _c, _e: [], fetch_abstract=lambda _p: None,
            f7_seams={})
    assert not (tmp_path / "out").exists()


def test_production_launcher_passes_locked_f7_bundle_to_run(
        tmp_path, monkeypatch):
    normalizer = make_normalizer(tmp_path / "auth")
    gen, ver = transports()
    receipt = AdapterReceipt(model="claude-sonnet-4-5", temperature=0)
    seams = make_production_f7_seams(
        generator_transport=gen, verifier_transport=ver,
        normalizer=normalizer, adapter_receipt=receipt, max_parallel=2)
    policy = make_production_f7_policy(
        normalizer, generator_model_id="claude-sonnet-4-5",
        verifier_model_id="claude-sonnet-4-5")
    builder = ProductionF7EvidenceBuilder()
    captured = {}
    monkeypatch.setattr(pl, "verify_tree", lambda *a, **k: {
        "code_commit": "a" * 40, "runtime_module_sha256": {}})

    def fake_run(*args, **kwargs):
        captured.update(kwargs)
        return {
            "predictions_path": str(tmp_path / "predictions.jsonl"),
            "manifest_path": str(tmp_path / "manifest.json"),
        }

    monkeypatch.setattr(pl, "run_natural_judgment", fake_run)
    monkeypatch.setattr(pl.pc, "assert_reportable_run", lambda *a, **k: None)
    receipt.record(seam="fixture")
    pl.launch(**base(
        adapter_receipt=receipt, f7_seams=seams,
        f7_evidence_builder=builder, f7_policy=policy))
    assert captured["f7_seams"] is seams
    assert captured["f7_evidence_builder"] is builder
    assert captured["f7_policy"] is policy
    assert captured["production"] is True


def test_production_launcher_rejects_partial_f7_before_tree_or_output(
        tmp_path, monkeypatch):
    checked = []
    monkeypatch.setattr(pl, "verify_tree", lambda *a, **k: checked.append(True))
    with pytest.raises(pl.LaunchRefused, match="all three explicitly unwired"):
        pl.launch(**base(f7_evidence_builder=ProductionF7EvidenceBuilder()))
    assert checked == []
    assert not (tmp_path / "out").exists()
