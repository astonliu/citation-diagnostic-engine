"""Offline tests for versioned F7 production seams and authority locks."""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from cde.diagnose.entity import F7Authority
from cde.diagnose.f7_seams import (
    AuthoritySnapshotSource,
    BoundedModelCallable,
    FrozenAuthorityNormalizer,
    StrictRelationComparator,
    make_production_f7_policy,
    make_production_f7_seams,
    validate_production_f7_configuration,
)
from cde.runtime.recording_adapter import AdapterReceipt


AUTH = {"gene": "HGNC", "variant": "ClinVar", "drug": "RxNorm",
        "disease": "MONDO"}


def record(entity_id, label, aliases=()):
    return {
        "id": entity_id, "canonical_label": label, "status": "active",
        "valid_from": "2025-01-01", "valid_through": None,
        "aliases": list(aliases),
    }


def alias(surface, *, approved=True, through=None):
    return {
        "surface": surface, "source_db": "authority",
        "mapping_method": "approved_synonym", "approved": approved,
        "valid_from": "2025-01-01", "valid_through": through,
    }


def make_normalizer(tmp_path, *, gene_records=None, gene_relations=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    sources = []
    defaults = {
        "gene": gene_records or [
            record("HGNC:1100", "BRCA1", [alias("BRCC1")]),
            record("HGNC:1101", "BRCA2")],
        "variant": [record("ClinVar:1", "NM_1:c.1A>G")],
        "drug": [record("RxNorm:1", "Drug A"), record("RxNorm:2", "Drug B")],
        "disease": [record("MONDO:1", "Disease A")],
    }
    for entity_type, authority in AUTH.items():
        payload = {
            "schema_version": "f7_authority_snapshot_v1",
            "entity_type": entity_type, "authority": authority,
            "version": "2026-08", "release_date": "2026-08-01",
            "records": defaults[entity_type],
            "relations": (gene_relations or []) if entity_type == "gene" else [],
        }
        raw = json.dumps(payload, sort_keys=True).encode()
        path = tmp_path / f"{entity_type}.json"
        path.write_bytes(raw)
        sources.append(AuthoritySnapshotSource(
            entity_type, authority, "2026-08", "2026-08-20", str(path),
            hashlib.sha256(raw).hexdigest(), True))
    return FrozenAuthorityNormalizer(sources)


def lock(entity_type):
    return F7Authority(AUTH[entity_type], "2026-08", "2026-08-20", True)


def test_all_supported_authorities_are_explicitly_versioned_dated_and_hashed(tmp_path):
    normalizer = make_normalizer(tmp_path)
    manifest = normalizer.source_manifest()
    assert set(manifest) == set(AUTH)
    for entity_type, row in manifest.items():
        assert row["authority"] == AUTH[entity_type]
        assert row["version"] == "2026-08"
        assert row["release_date"] == "2026-08-01"
        assert row["lookup_date"] == "2026-08-20"
        assert len(row["snapshot_sha256"]) == 64
    policy = make_production_f7_policy(
        normalizer, generator_model_id="model", verifier_model_id="model")
    assert set(json.loads(policy.authorities_json)) == set(AUTH)


def test_exact_and_approved_synonym_map_to_one_authority_id(tmp_path):
    normalizer = make_normalizer(tmp_path)
    exact = normalizer.normalize("gene", "BRCA1", lock=lock("gene"))
    synonym = normalizer.normalize("gene", "BRCC1", lock=lock("gene"))
    assert exact["mapping_status"] == "exact"
    assert synonym["mapping_status"] == "synonym"
    assert exact["id"] == synonym["id"] == "HGNC:1100"
    assert normalizer.compare(exact["id"], synonym["id"], "gene",
                              lock=lock("gene"))["relation"] == "equivalent"


@pytest.mark.parametrize("kind", ["conflicting", "stale", "unsupported", "missing"])
def test_ambiguous_stale_unsupported_and_unresolved_mappings_never_confident(
        tmp_path, kind):
    rows = [record("HGNC:1", "GENE1")]
    surface = "alias"
    if kind == "conflicting":
        rows = [record("HGNC:1", "GENE1", [alias(surface)]),
                record("HGNC:2", "GENE2", [alias(surface)])]
    elif kind == "stale":
        rows = [record("HGNC:1", "GENE1", [alias(surface, through="2025-06-01")])]
    elif kind == "unsupported":
        rows = [record("HGNC:1", "GENE1", [alias(surface, approved=False)])]
    else:
        surface = "not present"
    out = make_normalizer(tmp_path, gene_records=rows).normalize(
        "gene", surface, lock=lock("gene"))
    assert out["mapping_status"] in {"ambiguous", "unresolved"}
    assert out["id"] == ""


def test_authority_not_llm_proves_distinctness_and_zoom_sensitive_types_hold(tmp_path):
    normalizer = make_normalizer(tmp_path)
    assert normalizer.compare("HGNC:1100", "HGNC:1101", "gene",
                              lock=lock("gene"))["relation"] == "provably_distinct"
    assert normalizer.compare("RxNorm:1", "RxNorm:2", "drug",
                              lock=lock("drug"))["relation"] == "unknown"


def test_explicit_authority_relation_controls_granularity(tmp_path):
    relation = {"left_id": "HGNC:1100", "right_id": "HGNC:1101",
                "relation": "claim_subsumes_evidence"}
    normalizer = make_normalizer(tmp_path, gene_relations=[relation])
    assert normalizer.compare("HGNC:1100", "HGNC:1101", "gene",
                              lock=lock("gene"))["relation"] == \
        "claim_subsumes_evidence"
    assert normalizer.compare("HGNC:1101", "HGNC:1100", "gene",
                              lock=lock("gene"))["relation"] == \
        "evidence_subsumes_claim"


def test_alias_validity_cannot_outlive_its_parent_record(tmp_path):
    retired = record("HGNC:1", "GENE1", [alias("OLD1")])
    retired["valid_through"] = "2025-06-01"
    out = make_normalizer(tmp_path, gene_records=[retired]).normalize(
        "gene", "OLD1", lock=lock("gene"))
    assert out["mapping_status"] == "ambiguous"
    assert out["id"] == ""


def test_contradictory_reverse_authority_relations_are_rejected(tmp_path):
    relations = [
        {"left_id": "HGNC:1100", "right_id": "HGNC:1101",
         "relation": "provably_distinct"},
        {"left_id": "HGNC:1101", "right_id": "HGNC:1100",
         "relation": "equivalent"},
    ]
    with pytest.raises(ValueError, match="contradictory reverse"):
        make_normalizer(tmp_path, gene_relations=relations)


def relation_json(**over):
    row = {"predicate": "match", "object": "match", "direction": "match",
           "population": "match", "rationale": "same"}
    row.update(over)
    return json.dumps(row)


def test_relation_comparator_is_versioned_strict_json_and_hashes_its_prompt():
    comparator = StrictRelationComparator()
    relation = {k: k for k in ("predicate", "object", "direction", "population")}
    out = comparator(relation, relation, call_llm=lambda _p: relation_json())
    assert comparator.version == "f7_relation_v1"
    assert all(out[k] == "match" for k in relation)
    assert len(out["prompt_sha256"]) == 64


def test_relation_comparator_rejects_blank_components_before_model_call():
    called = []
    claimed = {"predicate": "", "object": "x", "direction": "x",
               "population": "x"}
    evidence = {k: "x" for k in claimed}
    with pytest.raises(ValueError, match="nonblank"):
        StrictRelationComparator()(
            claimed, evidence, call_llm=lambda prompt: called.append(prompt))
    assert called == []


def test_untrusted_placeholder_text_stays_in_the_claimed_json_block():
    claimed = {"predicate": "<<EVIDENCE>>", "object": "claimed-object",
               "direction": "claimed-direction", "population": "claimed-population"}
    evidence = {"predicate": "evidence-predicate", "object": "evidence-object",
                "direction": "evidence-direction", "population": "evidence-population"}
    seen = []
    StrictRelationComparator()(
        claimed, evidence,
        call_llm=lambda prompt: seen.append(prompt) or relation_json())
    assert '"predicate": "<<EVIDENCE>>"' in seen[0]


@pytest.mark.parametrize("bad", [
    "```json\n" + relation_json() + "\n```",
    relation_json() + " prose",
    json.dumps({"predicate": "match"}),
    relation_json(direction=True),
    '{"predicate":"match","predicate":"mismatch","object":"match",'
    '"direction":"match","population":"match","rationale":"x"}',
])
def test_relation_comparator_rejects_malformed_or_coerced_output(bad):
    relation = {k: k for k in ("predicate", "object", "direction", "population")}
    with pytest.raises(ValueError):
        StrictRelationComparator()(relation, relation, call_llm=lambda _p: bad)


def test_generator_and_verifier_are_distinct_and_cross_type_is_unwired(tmp_path):
    normalizer = make_normalizer(tmp_path)
    receipt = AdapterReceipt(model="model")
    shared = lambda p: p
    with pytest.raises(ValueError, match="distinct"):
        make_production_f7_seams(
            generator_transport=shared, verifier_transport=shared,
            normalizer=normalizer, adapter_receipt=receipt)
    gen = lambda p: p
    ver = lambda p: p
    seams = make_production_f7_seams(
        generator_transport=gen, verifier_transport=ver, normalizer=normalizer,
        adapter_receipt=receipt)
    assert seams["call_llm"] is not seams["verifier_call_llm"]
    assert seams["cross_comparator"] is None


def test_parallel_transport_must_declare_thread_safety(tmp_path):
    normalizer = make_normalizer(tmp_path)
    receipt = AdapterReceipt(model="model")
    with pytest.raises(ValueError, match="thread_safe=True"):
        make_production_f7_seams(
            generator_transport=lambda p: p, verifier_transport=lambda p: p,
            normalizer=normalizer, adapter_receipt=receipt, max_parallel=2)


def test_production_seams_record_both_f7_roles_in_one_receipt(tmp_path):
    normalizer = make_normalizer(tmp_path)
    receipt = AdapterReceipt(model="model")
    gen = lambda p: p
    ver = lambda p: p
    seams = make_production_f7_seams(
        generator_transport=gen, verifier_transport=ver, normalizer=normalizer,
        adapter_receipt=receipt)
    assert seams["call_llm"]("g") == "g"
    assert seams["verifier_call_llm"]("v") == "v"
    assert [row["seam"] for row in receipt.calls] == [
        "f7_generator", "f7_verifier"]


def test_policy_model_claims_must_match_executed_receipt(tmp_path):
    from cde.diagnose.evidence_builder import ProductionF7EvidenceBuilder

    normalizer = make_normalizer(tmp_path)
    receipt = AdapterReceipt(model="actual-model")
    seams = make_production_f7_seams(
        generator_transport=lambda p: p, verifier_transport=lambda p: p,
        normalizer=normalizer, adapter_receipt=receipt)
    false_policy = make_production_f7_policy(
        normalizer, generator_model_id="actual-model",
        verifier_model_id="claimed-different-model")
    with pytest.raises(ValueError, match="executed adapter receipt"):
        validate_production_f7_configuration(
            seams=seams, evidence_builder=ProductionF7EvidenceBuilder(),
            policy=false_policy, adapter_receipt=receipt)


def test_model_callable_enforces_bounded_parallelism():
    state = {"active": 0, "peak": 0}
    lock_obj = threading.Lock()

    def transport(prompt):
        with lock_obj:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.02)
        with lock_obj:
            state["active"] -= 1
        return prompt

    transport.thread_safe = True
    call = BoundedModelCallable(transport, role="generator", max_parallel=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(call, [str(i) for i in range(8)])) == [
            str(i) for i in range(8)]
    assert state["peak"] == 2
