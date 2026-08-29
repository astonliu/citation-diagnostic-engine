"""fixtures_v1 — deterministic fixture universes for semantic_validator_v1.

Builds fully hash-coherent artifact universes (every self-hash, chain link,
WAL link, idempotency key, occurrence identity, and cross-artifact reference
recomputed with canon_v1) so the positive fixtures produce ZERO violations,
plus seal helpers so a targeted negative can mutate one fact and re-seal the
dependent hashes — making each negative fire exactly its own rule.

Two artifact universes:
  build_candidate_universe() — candidate/calibration run: protocol, CONFIG,
    selection, candidate manifest, batch + review chain + WAL, exclusion
    ledger/checkpoints (N=empty, N+1=post-exposure), exposure plan, PROMOTION.
  build_release_universe()  — later release/formal run under the same CONFIG
    and PROMOTION: fresh (non-excluded) items, annotation release manifest,
    release attestation, reportability claims.

build_git_universe(tmp_path) — a real temporary git repo exercising the
anchoring rules (SV-041/042/043/044 ancestry clauses, residuals #9/#10).

Timestamps are fixed constants; the same builder output is committed as JSON
under cre/f1/fixtures/freeze/ (write_fixture_files) as byte-stable evidence.

Also used by gen_conformance.py for the per-rule conformance section.
"""
import copy
import hashlib
import json
import pathlib
import subprocess

from cde.claims import band_prompts
from cde.freeze import schema_gate
from cde.freeze.canon_v1 import canon_sha256
from cde.freeze.semantic_validator_v1 import (FROZEN_SOURCE_BLOB_OID,
                                                 GitContext,
                                                 TrustedConstants)

TS = "2026-07-24T12:00:00Z"
OID = "sha1:" + "0" * 40
# These are TEST fixtures and live with the tests, not inside the package: the
# freeze package ships to a run, and a run has no use for a candidate universe
# built to exercise the validator.
FIXTURES_DIR = (pathlib.Path(__file__).resolve().parents[2]
                / "tests" / "fixtures" / "freeze")

KNOWN_PROHIBITED = ("PMC901", "PMC902")


def sha256_utf8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def trusted_for_fixtures():
    """Trust context the fixtures are built against (out-of-band by design)."""
    return TrustedConstants(schema_sha256=schema_gate.PINNED_SCHEMA_SHA256,
                            known_prohibited_citing_pmcids=KNOWN_PROHIBITED)


# ---------------------------------------------------------------------------
# seal helpers (recompute dependent hashes after a targeted mutation)
# ---------------------------------------------------------------------------

def seal_self_hash(obj, field):
    obj[field] = canon_sha256({k: v for k, v in obj.items() if k != field})
    return obj


def seal_envelope(obj):
    obj["payload_sha256"] = canon_sha256(obj["payload"])
    return obj


def seal_wal_events(events):
    """Recompute per-event self-hashes and the global/per-attempt links."""
    prev_global = None
    prev_attempt = {}
    for ev in events:
        ev["prev_wal_event_sha256"] = prev_global
        ev["prev_attempt_event_sha256"] = prev_attempt.get(ev["attempt_id"])
        seal_self_hash(ev, "event_sha256")
        prev_global = ev["event_sha256"]
        prev_attempt[ev["attempt_id"]] = ev["event_sha256"]
    return events


def seal_chain(universe):
    """Re-seal records -> chain links -> batch -> run_hash after a mutation."""
    batch = universe["batch"]
    records = universe["review_records"]
    batch["config_hash"] = universe["config_hash"]
    gp = batch["genesis_preimage"]
    gp["config_hash"] = universe["config_hash"]
    gp["run_id"] = batch["run_id"]
    gp["selection_artifact_sha256"] = batch["selection_artifact_sha256"]
    gp["candidate_manifest_sha256"] = batch["candidate_manifest_sha256"]
    batch["genesis"] = canon_sha256(gp)
    prev = batch["genesis"]
    for rec in records:
        rec["config_hash"] = universe["config_hash"]
        rec["run_id"] = batch["run_id"]
        rec["run_facts"]["config_hash_used"] = universe["config_hash"]
        rec["prev_record_sha256"] = prev
        seal_self_hash(rec, "record_sha256")
        prev = rec["record_sha256"]
    batch["chain_tip"] = prev
    batch["chain_record_count"] = len(records)
    if universe.get("wal_events"):
        batch["wal_tip_sha256"] = universe["wal_events"][-1]["event_sha256"]
    batch["review_dump_sha256"] = canon_sha256(records)
    universe["run_hash"] = canon_sha256(batch)
    return universe


def seal_config(universe):
    universe["config_hash"] = canon_sha256(universe["config"])
    return universe


def reseal_candidate_universe(u):
    """Re-seal the candidate universe after a targeted mutation: chain,
    exposure plan, promotion envelope, and run-state manifest bindings."""
    if "selection_artifact" in u and "candidate_manifest" in u:
        sel_sha = canon_sha256(u["selection_artifact"])
        u["candidate_manifest"]["config_hash"] = u["config_hash"]
        u["candidate_manifest"]["selection_artifact_sha256"] = sel_sha
        u["batch"]["selection_artifact_sha256"] = sel_sha
        u["batch"]["candidate_manifest_sha256"] = \
            canon_sha256(u["candidate_manifest"])
    seal_chain(u)
    if "exposure_plan" in u:
        u["exposure_plan"]["run_hash"] = u["run_hash"]
        u["exposure_plan"]["exposed"] = [
            {"citation_id": r["item_key"],
             "review_record_sha256": r["record_sha256"]}
            for r in u["review_records"]]
        if "promotion" in u:
            p = u["promotion"]["payload"]
            p["config_hash"] = u["config_hash"]
            p["run_hash"] = u["run_hash"]
            p["exposure_plan_sha256"] = canon_sha256(u["exposure_plan"])
            seal_envelope(u["promotion"])
    rsm = u.get("run_state_manifest")
    if rsm:
        rsm["config_hash"] = u["config_hash"]
        rsm["selection_artifact_sha256"] = \
            u["batch"]["selection_artifact_sha256"]
        rsm["candidate_manifest_sha256"] = \
            u["batch"]["candidate_manifest_sha256"]
        rsm["genesis"] = u["batch"]["genesis"]
        rsm["chain_tip"] = u["review_records"][0]["record_sha256"]
        rsm["review_dump_partial_sha256"] = \
            canon_sha256(u["review_records"][:1])
        rsm["observed_runtime_sha256"] = canon_sha256(rsm["observed_runtime"])
    return u


def reseal_release_universe(u):
    """Re-seal the release universe after a targeted mutation: chain, release
    manifest source bindings + rows, and the attestation envelope."""
    seal_chain(u)
    manifest = u.get("annotation_release_manifest")
    if manifest:
        manifest["source_run_hash"] = u["run_hash"]
        manifest["source_review_dump_sha256"] = \
            u["batch"]["review_dump_sha256"]
        manifest["source_selection_hash"] = \
            u["batch"]["selection_artifact_sha256"]
        by_id = {r["item_key"]: r for r in u["review_records"]}
        for row in manifest.get("inventory") or []:
            rec = by_id.get(row["citation_id"])
            if rec:
                row["review_record_sha256"] = rec["record_sha256"]
                row["review_record_seq"] = rec["seq"]
                row["stimulus_sha256"] = rec["stimulus_sha256"]
                row["evidence_snapshot_sha256"] = \
                    rec["evidence_snapshot_sha256"]
        att = u.get("release_attestation")
        if att:
            att["annotation_release_manifest_sha256"] = canon_sha256(manifest)
            seal_self_hash(att, "attestation_sha256")
    return u


def build_wal_triplet(lcid="lc-x", citation_id="PMC1:r1",
                      stage="claim_extract", claim_idx=None, headers=None):
    """A sealed prepared/sent/response_persisted WAL chain for one call."""
    wal = _WalBuilder(sha256_utf8("cfg"), "c" * 32)
    body = sha256_utf8(f"body {lcid}")
    wal.logical_call(lcid, citation_id, stage, claim_idx, body,
                     headers or {"request-id": "req-1"}, "claude-opus-5",
                     "resp-1")
    return wal.seal()


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------

def _render_contract(name):
    if name == "claim_extract":
        slots = [{"name": "CITING_SENTENCE", "role": "user",
                  "encoding": "utf-8", "order": 0}]
    else:
        slots = [{"name": "ATOMIC_CLAIM", "role": "user",
                  "encoding": "utf-8", "order": 0},
                 {"name": "EVIDENCE", "role": "user",
                  "encoding": "utf-8", "order": 1}]
    return {"message_construction": "structured", "collision_safe": True,
            "slots": slots}


def build_prompt_package(name):
    template = (band_prompts.CLAIM_EXTRACT_PROMPT if name == "claim_extract"
                else band_prompts.COVERAGE_PROMPT)
    rc = _render_contract(name)
    pkg = {
        "artifact_type": "prompt_package", "schema_version": "v14",
        "name": name,
        "template_text_version": 3 if name == "claim_extract" else 2,
        "package_version": 4,
        "template_utf8": template,
        "template_utf8_sha256": sha256_utf8(template),
        "source_blob_oid": FROZEN_SOURCE_BLOB_OID,
        "render_contract": rc,
        "render_contract_sha256": canon_sha256(rc),
        "package_sha256": "0" * 64,
    }
    return seal_self_hash(pkg, "package_sha256")


def _retry_policy():
    return {"max_attempts": 3, "retryable_status": [429],
            "retryable_exceptions": ["connect_timeout"],
            "connect_timeout_seconds": "5", "read_timeout_seconds": "60",
            "total_timeout_seconds": "120", "backoff_base_seconds": "1",
            "backoff_cap_seconds": "30", "jitter_seconds": "1",
            "respect_retry_after": True, "retry_after_cap_seconds": "60",
            "idempotency_preimage_version": "CRE_FINDER_IDEMPOTENCY_V1",
            "record_attempts": True}


def _endpoint():
    return {"provider": "anthropic",
            "base_url": "https://api.anthropic.com/v1/messages",
            "host_allowlisted": True, "api_family": "messages",
            "api_version": "2023-06-01", "region": "us", "sdk_version": "1.0",
            "jcs_library_version": "1.0", "behavior_headers": {}}


def _params():
    return {"temperature": {"state": "omitted"},
            "max_tokens": {"state": "supplied", "value": 1024},
            "top_p": {"state": "omitted"}, "top_k": {"state": "omitted"},
            "stop_sequences": {"state": "omitted"},
            "seed": {"state": "omitted"}}


def _stage(extract=True):
    s = {"model_snapshot": "claude-opus-5", "response_parser_version": "v1",
         "tool_schema": None, "tool_schema_sha256": None, "params": _params(),
         "system_message": {"state": "omitted"}, "endpoint": _endpoint(),
         "retry": _retry_policy(), "response_schema_sha256": None}
    if extract:
        s["evidence_scope"] = "citing_sentence_only"
    else:
        s["evidence_scope"] = "abstract_snapshot"
        s["evidence_reader"] = "content_addressed_only"
        s["evidence_policy"] = {"retrieval": "efetch",
                                "pmid_version_policy": "latest_at_snapshot",
                                "normalization": "nfc",
                                "missing_evidence_sentinel": "EVIDENCE_UNAVAILABLE"}
    return s


def build_module_manifest():
    modules = []
    from cde.freeze.bootstrap import TRUST_BOUNDARY_ROLES
    for role in TRUST_BOUNDARY_ROLES:
        modules.append({"role": role,
                        "repo_path": f"cre/f1/freeze/mod_{role}.py",
                        "blob_oid": OID,
                        "content_sha256": sha256_utf8(f"module {role}")})
    return {"artifact_type": "module_manifest", "schema_version": "v14",
            "modules": modules}


def build_candidate_protocol():
    return {"artifact_type": "candidate_protocol", "schema_version": "v14",
            "freeze_criterion": "calibration precision stable across the "
                                "small candidate batch",
            "prohibited_cases": [{"citing_pmcid": "PMC901"},
                                 {"citing_pmcid": "PMC902"}],
            "schema_sha256": schema_gate.PINNED_SCHEMA_SHA256,
            "semantic_validator_version": "semantic_validator_v1",
            "evaluation_policy_version": "v1",
            "recorded_by": "ZD", "recorded_at": TS}


CODEBOOK = "CRE F3-F7 codebook v1: judge coverage of each atomic claim."
CODEBOOK_SHA = sha256_utf8(CODEBOOK)


def build_config(protocol, module_manifest):
    return {
        "artifact_type": "config", "schema_version": "v14",
        "canon": "canon_v1", "scope": "finder_frontend_extract_coverage",
        "candidate_protocol_sha256": canon_sha256(protocol),
        "prompt_packages": {},  # filled by caller with sealed packages
        "stages": {"claim_extract": _stage(True), "coverage": _stage(False)},
        "failure_policy": {"continue_after_coverage_terminal_failure": True},
        "source": {"repo_identity":
                   "github.com/astonliu/citation-repair-engine",
                   "canonical_ref": "refs/heads/main",
                   "source_commit_oid": OID, "source_tree_oid": OID},
        "module_manifest_sha256": canon_sha256(module_manifest),
        "runtime_profile": {"python_implementation": "cpython",
                            "python_version": "3.10.12",
                            "platform_constraint": "linux",
                            "dependency_lock_sha256": sha256_utf8("lock"),
                            "distribution_inventory_sha256": sha256_utf8("dist"),
                            "transport_library_version": "1.0",
                            "jcs_library_version": "1.0"},
        "codebook_sha256": CODEBOOK_SHA,
    }


def _observed_runtime(profile):
    return {"python_implementation": profile["python_implementation"],
            "python_version": profile["python_version"],
            "platform": "linux",
            "dependency_lock_sha256": profile["dependency_lock_sha256"],
            "distribution_inventory_sha256":
                profile["distribution_inventory_sha256"],
            "transport_library_version":
                profile["transport_library_version"],
            "jcs_library_version": profile["jcs_library_version"],
            # RECORDED FIXTURE DATA, not a live module path. It is sealed
            # inside the committed candidate/release universes, so renaming it
            # to match the package would break run_hash on artifacts this
            # project did not re-mint. Left exactly as recorded.
            "argv": ["../.venv_cre/bin/python", "-m", "cre.f1.freeze.run"],
            "cwd": "/work", "fresh_interpreter": True,
            "matches_config_runtime_profile": True}


def build_selection_item(citing_pmcid, ref_id, pmid, source_xml_text,
                         stratum="F6"):
    preimage = {"occurrence_identity_version": "occ_id_v1",
                "citing_pmcid": citing_pmcid,
                "normalized_ref_key_type": "resolved_pmid",
                "normalized_ref_key": pmid}
    source_xml_sha = sha256_utf8(source_xml_text)
    fp = {"source_xml_sha256": source_xml_sha,
          "ref_content_utf8": f"Reference {ref_id} content",
          "extraction_contract_version": "v1"}
    return {"citation_id": f"{citing_pmcid}:{ref_id}",
            "citing_pmcid": citing_pmcid, "ref_id": ref_id,
            "occurrence_identity_version": "occ_id_v1",
            "normalized_ref_key_type": "resolved_pmid",
            "normalization_contract_sha256": sha256_utf8("norm-contract-v1"),
            "normalized_ref_key": pmid,
            "occurrence_identity": canon_sha256(preimage),
            "source_xml_sha256": source_xml_sha,
            "ref_content_utf8": fp["ref_content_utf8"],
            "extraction_contract_version": "v1",
            "source_occurrence_fingerprint": canon_sha256(fp),
            "corpus_source_id": "pmc_oa",
            "retrieval_record_sha256": sha256_utf8(f"retrieval {citing_pmcid}"),
            "not_detector_sourced_attestation": True,
            "stratum": stratum}


def build_stimulus(citing_sentence, claim_text, evidence_sha, evidence_text):
    return {"artifact_type": "stimulus_object", "schema_version": "v14",
            "citing_sentence": citing_sentence,
            "atomic_claims": [{"idx": 0, "text": claim_text}],
            "evidence": {"evidence_snapshot_sha256": evidence_sha,
                         "text": evidence_text},
            "label_space": ["supported", "unsupported"],
            "worksheet_schema": {"questions": [
                {"id": "q1", "prompt": "Is the claim supported by the "
                                       "evidence?", "answer_type":
                 "single_choice", "choices": ["yes", "no"]}]},
            "display_instructions": "Judge each claim against the evidence "
                                    "only.",
            "codebook_content": CODEBOOK, "codebook_sha256": CODEBOOK_SHA,
            "rendering_settings": {"truncation": "none", "redaction": "none",
                                   "ordering": "document"}}


class _WalBuilder:
    def __init__(self, config_hash, run_id):
        self.config_hash = config_hash
        self.run_id = run_id
        self.events = []

    def logical_call(self, lcid, citation_id, stage, claim_idx, body_sha,
                     headers, model_id, response_id):
        request_sha = sha256_utf8(f"request:{lcid}")
        preimage = {"domain": "CRE_FINDER_IDEMPOTENCY_V1",
                    "config_hash": self.config_hash, "run_id": self.run_id,
                    "citation_id": citation_id, "stage": stage,
                    "claim_idx": claim_idx,
                    "request_bytes_sha256": request_sha}
        key = canon_sha256(preimage)
        attempt_id = f"a-{lcid}-0"
        base = {"artifact_type": "wal_event", "schema_version": "v14",
                "logical_call_id": lcid, "attempt_id": attempt_id,
                "attempt_ordinal": 0, "wal_seq": 0,
                "prev_wal_event_sha256": None, "attempt_event_seq": 0,
                "prev_attempt_event_sha256": None, "recorded_at": TS,
                "request_bytes_sha256": request_sha, "idempotency_key": key,
                "event_sha256": "0" * 64}
        prepared = dict(base, event_type="prepared",
                        idempotency_preimage=preimage,
                        sent_boundary_crossed=False)
        sent = dict(base, event_type="sent", attempt_event_seq=1,
                    sent_boundary_crossed=True)
        persisted = dict(base, event_type="response_persisted",
                         attempt_event_seq=2, sent_boundary_crossed=True,
                         http_status=200, response_body_sha256=body_sha,
                         response_body_ref=f"cas/{body_sha}",
                         response_headers_allowlisted=dict(headers),
                         provider_model_id=model_id, response_id=response_id,
                         finish_reason="end_turn")
        start = len(self.events)
        for i, ev in enumerate((prepared, sent, persisted)):
            ev["wal_seq"] = start + i
            self.events.append(ev)
        return {"request_sha": request_sha, "key": key,
                "slice": (start, start + 3)}

    def seal(self):
        seal_wal_events(self.events)
        return self.events

    def shas(self, sl):
        return [e["event_sha256"] for e in self.events[sl[0]:sl[1]]]


def _ok_call(lcid, meta, body_sha, headers, parsed, shas, model_id,
             response_id):
    return {"call_made": True, "status": "ok", "logical_call_id": lcid,
            "request_bytes_sha256": meta["request_sha"],
            "idempotency_key": meta["key"], "retry_wal_event_shas": shas,
            "raw_response_body_ref": f"cas/{body_sha}",
            "raw_response_body_sha256": body_sha,
            "response_headers_allowlisted": dict(headers),
            "http_status": 200, "parsed": parsed,
            "provider_model_id": model_id, "response_id": response_id,
            "finish_reason": "end_turn"}


def _build_universe(items_spec, sample_purpose, execution_mode, run_id,
                    ledger_pmcids):
    """items_spec: list of (citing_pmcid, ref_id, pmid, verdict)."""
    packages = [build_prompt_package("claim_extract"),
                build_prompt_package("coverage")]
    protocol = build_candidate_protocol()
    module_manifest = build_module_manifest()
    config = build_config(protocol, module_manifest)
    config["prompt_packages"] = {
        p["name"]: {"package_version": p["package_version"],
                    "package_sha256": p["package_sha256"]} for p in packages}
    config_hash = canon_sha256(config)

    source_xml = {}
    evidence_snapshots = {}
    stimuli = {}
    sel_items = []
    cand_items = []
    records = []
    wal = _WalBuilder(config_hash, run_id)
    model_id = "claude-opus-5"

    for n, (pmcid, ref_id, pmid, verdict) in enumerate(items_spec):
        cid = f"{pmcid}:{ref_id}"
        sentence = f"Sentence {n} cites the intervention outcome for {pmid}."
        claim_text = f"The intervention improved outcome {n}."
        xml = (f"<article><body><p>{sentence}</p></body>"
               f"<ref id=\"{ref_id}\"/></article>")
        source_xml[pmcid] = xml
        snapshot = (f"Abstract for PMID {pmid}. The intervention improved "
                    f"outcome {n} in the trial.").encode("utf-8")
        ev_sha = hashlib.sha256(snapshot).hexdigest()
        evidence_snapshots[ev_sha] = snapshot
        sel_items.append(build_selection_item(pmcid, ref_id, pmid, xml))
        cand_items.append({"citation_id": cid,
                           "resolved_work_id": f"pmid:{pmid}",
                           "resolution_provenance_sha256":
                               sha256_utf8(f"resolution {cid}"),
                           "evidence_snapshot_sha256": ev_sha,
                           "source_xml_sha256": sha256_utf8(xml)})
        evidence_text = f"The intervention improved outcome {n} in the trial."
        stimuli[cid] = build_stimulus(sentence, claim_text, ev_sha,
                                      evidence_text)
        headers = {"request-id": f"req-{run_id[:4]}-{n}"}
        ex_body = sha256_utf8(f"extract-response {cid}")
        ex_meta = wal.logical_call(f"lc-e{n}-{run_id[:4]}", cid,
                                   "claim_extract", None, ex_body, headers,
                                   model_id, f"resp-e{n}")
        cov_body = sha256_utf8(f"coverage-response {cid}")
        cov_meta = wal.logical_call(f"lc-c{n}-0-{run_id[:4]}", cid,
                                    "coverage", 0, cov_body, headers,
                                    model_id, f"resp-c{n}")
        records.append({
            "artifact_type": "review_record", "schema_version": "v14",
            "seq": n, "prev_record_sha256": None, "record_sha256": "0" * 64,
            "item_key": cid, "citing_pmcid": pmcid, "ref_id": ref_id,
            "claimed_pmid": pmid, "resolved_work_id": f"pmid:{pmid}",
            "citing_sentence": sentence, "config_hash": config_hash,
            "run_id": run_id,
            "extract": None,  # sealed below once WAL shas exist
            "coverage": [],
            "evidence_snapshot_sha256": ev_sha,
            "evidence_snapshot_ref": f"cas/{ev_sha}",
            "shape_flags": [],
            "stimulus_sha256": canon_sha256(stimuli[cid]),
            "finder_disposition": "flagged" if verdict == "unsupported"
                                  else "clear",
            "sample_purpose": sample_purpose,
            "run_facts": {"finder_configuration_attested": True,
                          "finder_freeze_promoted_at_run":
                              execution_mode == "release",
                          "config_hash_used": config_hash,
                          "promotion_payload_sha256_at_run": None},
            "cluster_id": f"cluster-{n}",
        })
        records[-1]["_wal"] = (ex_meta, cov_meta, ex_body, cov_body, headers,
                               claim_text, verdict)

    wal_events = wal.seal()
    for n, rec in enumerate(records):
        ex_meta, cov_meta, ex_body, cov_body, headers, claim_text, verdict = \
            rec.pop("_wal")
        rec["extract"] = _ok_call(
            ex_meta and f"lc-e{n}-{run_id[:4]}", ex_meta, ex_body, headers,
            {"claims": [{"idx": 0, "text": claim_text}]},
            wal.shas(ex_meta["slice"]), model_id, f"resp-e{n}")
        rec["coverage"] = [{"claim_idx": 0, "call": _ok_call(
            f"lc-c{n}-0-{run_id[:4]}", cov_meta, cov_body, headers,
            {"verdict": verdict}, wal.shas(cov_meta["slice"]), model_id,
            f"resp-c{n}")}]

    selection = {"artifact_type": "selection_artifact",
                 "schema_version": "v14", "sample_purpose": sample_purpose,
                 "selection_rule": "first-citance reference-level natural "
                                   "sample", "min_size": 2,
                 "coverage_targets": {"F6": 2}, "items": sel_items,
                 "recorded_by": "ZD"}
    candidate_manifest = {"artifact_type": "candidate_manifest",
                          "schema_version": "v14", "config_hash": config_hash,
                          "selection_artifact_sha256": canon_sha256(selection),
                          "items": cand_items}

    ledger = []
    prev = None
    for i, (pmcid, cid, occ) in enumerate(ledger_pmcids):
        entry = {"artifact_type": "exclusion_ledger_entry",
                 "schema_version": "v14", "seq": i,
                 "prev_entry_sha256": prev, "entry_sha256": "0" * 64,
                 "citation_id": cid, "occurrence_identity": occ,
                 "citing_pmcid": pmcid, "cluster_id": f"cluster-{i}",
                 "scope": "citing_paper", "reason": "human_inspected",
                 "recorded_at": TS}
        seal_self_hash(entry, "entry_sha256")
        prev = entry["entry_sha256"]
        ledger.append(entry)
    cp_empty = {"artifact_type": "exclusion_checkpoint",
                "schema_version": "v14", "tip_entry_sha256": None,
                "entry_count": 0, "canonical_ref_commit": OID}
    cp_post = {"artifact_type": "exclusion_checkpoint",
               "schema_version": "v14",
               "tip_entry_sha256": ledger[-1]["entry_sha256"] if ledger
               else None,
               "entry_count": len(ledger), "canonical_ref_commit": OID}

    profile = config["runtime_profile"]
    batch = {
        "artifact_type": "batch", "schema_version": "v14", "run_id": run_id,
        "execution_mode": execution_mode, "sample_purpose": sample_purpose,
        "promotion_payload_sha256_at_start": None,
        "exclusion_checkpoint_sha256_at_start":
            canon_sha256(cp_empty if execution_mode == "candidate"
                         else cp_post),
        "canonical_ref_commit_observed": OID, "config_hash": config_hash,
        "selection_artifact_sha256": canon_sha256(selection),
        "candidate_manifest_sha256": canon_sha256(candidate_manifest),
        "genesis_preimage": {"config_hash": config_hash, "run_id": run_id,
                             "selection_artifact_sha256":
                                 canon_sha256(selection),
                             "candidate_manifest_sha256":
                                 canon_sha256(candidate_manifest)},
        "genesis": "0" * 64, "chain_tip": "0" * 64, "chain_record_count": 0,
        "chain_hash_version": "canon_v1", "wal_tip_sha256": None,
        "review_dump_sha256": "0" * 64, "status": "complete",
        "observed_runtime": _observed_runtime(profile),
        "funnel": {"input_total": len(records), "excluded": 0,
                   "runner_accepted": len(records),
                   "flagged": sum(1 for r in records
                                  if r["finder_disposition"] == "flagged"),
                   "clear": sum(1 for r in records
                                if r["finder_disposition"] == "clear"),
                   "held": 0, "quarantined": 0},
        "diagnostics": {"refused_items": 0, "truncated_items": 0,
                        "indeterminate_items": 0, "refused_attempts": 0,
                        "truncated_attempts": 0},
        "started": TS, "completed": TS,
    }

    universe = {
        "prompt_packages": packages,
        "config": config, "config_hash": config_hash,
        "candidate_protocol": protocol,
        "module_manifest": module_manifest,
        "selection_artifact": selection,
        "candidate_manifest": candidate_manifest,
        "exclusion_ledger": ledger,
        "exclusion_checkpoints": [cp_empty, cp_post],
        "batch": batch, "run_hash": None,
        "review_records": records,
        "wal_events": wal_events,
        "stimulus_objects": stimuli,
        "evidence_snapshots": evidence_snapshots,
        "source_xml": source_xml,
    }
    seal_chain(universe)
    return universe


def build_candidate_universe():
    items = [("PMC100001", "ref-a", "11111", "unsupported"),
             ("PMC100002", "ref-b", "22222", "supported")]
    # Post-exposure ledger N+1 excludes the inspected calibration papers.
    occ = [build_selection_item(p, r, m, "x")["occurrence_identity"]
           for p, r, m, _ in items]
    ledger_pmcids = [("PMC100001", "PMC100001:ref-a", occ[0]),
                     ("PMC100002", "PMC100002:ref-b", occ[1])]
    u = _build_universe(items, "calibration", "candidate", "a" * 32,
                        ledger_pmcids)
    exposure_plan = {"artifact_type": "exposure_plan", "schema_version": "v14",
                     "run_hash": u["run_hash"],
                     "exposed": [{"citation_id": r["item_key"],
                                  "review_record_sha256": r["record_sha256"]}
                                 for r in u["review_records"]],
                     "recorded_by": "ZD", "recorded_at": TS}
    cp_post = u["exclusion_checkpoints"][1]
    promotion = {"artifact_type": "promotion", "schema_version": "v14",
                 "payload": {"config_hash": u["config_hash"],
                             "run_hash": u["run_hash"],
                             "candidate_protocol_sha256":
                                 canon_sha256(u["candidate_protocol"]),
                             "exposure_plan_sha256":
                                 canon_sha256(exposure_plan),
                             "post_exposure_exclusion_checkpoint_sha256":
                                 canon_sha256(cp_post),
                             "recorded_by": "ZD", "recorded_at": TS},
                 "payload_sha256": "0" * 64}
    seal_envelope(promotion)
    u["exposure_plan"] = exposure_plan
    u["promotion"] = promotion
    rsm = {"artifact_type": "run_state_manifest", "schema_version": "v14",
           "run_id": u["batch"]["run_id"], "execution_mode": "candidate",
           "sample_purpose": "calibration",
           "promotion_payload_sha256_at_start": None,
           "exclusion_checkpoint_sha256_at_start":
               u["batch"]["exclusion_checkpoint_sha256_at_start"],
           "canonical_ref_commit_observed": OID,
           "config_hash": u["config_hash"],
           "selection_artifact_sha256":
               u["batch"]["selection_artifact_sha256"],
           "candidate_manifest_sha256":
               u["batch"]["candidate_manifest_sha256"],
           "genesis": u["batch"]["genesis"], "status": "in_progress",
           "chain_tip": u["review_records"][0]["record_sha256"],
           "chain_record_count": 1,
           "wal_tip_sha256": u["wal_events"][5]["event_sha256"],
           "review_dump_partial_sha256":
               canon_sha256(u["review_records"][:1]),
           "output_dir": "runs/candidate-a",
           "observed_runtime": copy.deepcopy(u["batch"]["observed_runtime"]),
           "observed_runtime_sha256":
               canon_sha256(u["batch"]["observed_runtime"])}
    u["run_state_manifest"] = rsm
    return u


def build_release_universe():
    candidate = build_candidate_universe()
    items = [("PMC200001", "ref-c", "33333", "unsupported"),
             ("PMC200002", "ref-d", "44444", "supported")]
    ledger_pmcids = [(e["citing_pmcid"], e["citation_id"],
                      e["occurrence_identity"])
                     for e in candidate["exclusion_ledger"]]
    u = _build_universe(items, "formal", "release", "b" * 32, ledger_pmcids)
    # Same CONFIG + PROMOTION as the candidate universe (same builder inputs
    # -> identical config bytes -> identical config_hash).
    assert u["config_hash"] == candidate["config_hash"]
    u["promotion"] = copy.deepcopy(candidate["promotion"])
    u["exposure_plan"] = copy.deepcopy(candidate["exposure_plan"])
    u["batch"]["promotion_payload_sha256_at_start"] = \
        canon_sha256(u["promotion"]["payload"])
    seal_chain(u)
    flagged = u["review_records"][0]
    sel_by_id = {i["citation_id"]: i for i in
                 u["selection_artifact"]["items"]}
    cand_by_id = {i["citation_id"]: i for i in
                  u["candidate_manifest"]["items"]}
    manifest = {
        "artifact_type": "annotation_release_manifest",
        "schema_version": "v14", "config_hash": u["config_hash"],
        "promotion_payload_sha256": canon_sha256(u["promotion"]["payload"]),
        "source_run_hash": u["run_hash"],
        "source_review_dump_sha256": u["batch"]["review_dump_sha256"],
        "source_selection_hash": u["batch"]["selection_artifact_sha256"],
        "exclusion_checkpoint_sha256":
            u["batch"]["exclusion_checkpoint_sha256_at_start"],
        "codebook_sha256": CODEBOOK_SHA,
        "inventory": [{
            "citation_id": flagged["item_key"],
            "occurrence_identity":
                sel_by_id[flagged["item_key"]]["occurrence_identity"],
            "source_xml_sha256":
                cand_by_id[flagged["item_key"]]["source_xml_sha256"],
            "resolved_work_id": flagged["resolved_work_id"],
            "resolution_provenance_sha256":
                cand_by_id[flagged["item_key"]]["resolution_provenance_sha256"],
            "stimulus_sha256": flagged["stimulus_sha256"],
            "evidence_snapshot_sha256": flagged["evidence_snapshot_sha256"],
            "finder_disposition": "flagged",
            "review_record_sha256": flagged["record_sha256"],
            "review_record_seq": flagged["seq"]}],
    }
    attestation = {"artifact_type": "release_attestation",
                   "schema_version": "v14",
                   "annotation_release_manifest_sha256":
                       canon_sha256(manifest),
                   "canonical_ref_commit": OID,
                   "config_hash": u["config_hash"],
                   "promotion_payload_sha256":
                       canon_sha256(u["promotion"]["payload"]),
                   "exclusion_checkpoint_sha256":
                       u["batch"]["exclusion_checkpoint_sha256_at_start"],
                   "prereg_amendment_sha256": sha256_utf8("prereg-amendment"),
                   "valid_at_release": True, "recorded_by": "ZD",
                   "recorded_at": TS, "attestation_sha256": "0" * 64}
    seal_self_hash(attestation, "attestation_sha256")
    u["annotation_release_manifest"] = manifest
    u["release_attestation"] = attestation
    u["revocations"] = []
    u["reportability_claims"] = {"finder_result_reportable": True,
                                 "discriminator_result_reportable": False,
                                 "composite_result_reportable": False}
    return u


# ---------------------------------------------------------------------------
# git universe (SV-041 / SV-042 / SV-043 / SV-044 ancestry, residuals #9/#10)
# ---------------------------------------------------------------------------

def build_git_universe(tmp_path):
    """Create a real temp git repo whose commit graph anchors the artifacts.

    Returns (artifacts, repo_ctx, commits) where commits maps stage names to
    commit hex OIDs.
    """
    repo = pathlib.Path(tmp_path) / "anchor_repo"
    repo.mkdir()

    def git(*args):
        r = subprocess.run(["git", "-C", str(repo)] + list(args),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"git {args}: {r.stderr}")
        return r.stdout.strip()

    git("init", "-q", "-b", "main")
    git("config", "user.email", "zd@example.org")
    git("config", "user.name", "ZD")

    def commit(name, content):
        (repo / name).write_text(content)
        git("add", "-A")
        git("commit", "-q", "-m", f"add {name}")
        return git("rev-parse", "HEAD")

    commits = {}
    commits["base"] = commit("source.txt", "pinned source tree\n")
    commits["candidate_protocol"] = commit("protocol.json", "protocol\n")
    commits["config"] = commit("config.json", "config\n")
    commits["batch"] = commit("batch.json", "batch\n")
    commits["exposure_plan"] = commit("exposure_plan.json", "exposure\n")
    commits["promotion"] = commit("promotion.json", "promotion\n")
    commits["checkpoint"] = commit("checkpoint.json", "checkpoint\n")
    commits["attestation"] = commit("attestation.json", "attestation\n")

    tree = git("rev-parse", commits["base"] + "^{tree}")
    protocol = build_candidate_protocol()
    config = {"artifact_type": "config",
              "candidate_protocol_sha256": canon_sha256(protocol),
              "source": {"repo_identity":
                         "github.com/astonliu/citation-repair-engine",
                         "canonical_ref": "refs/heads/main",
                         "source_commit_oid": "sha1:" + commits["base"],
                         "source_tree_oid": "sha1:" + tree}}
    checkpoint = {"artifact_type": "exclusion_checkpoint",
                  "schema_version": "v14", "tip_entry_sha256": None,
                  "entry_count": 0,
                  "canonical_ref_commit": "sha1:" + commits["promotion"]}
    attestation = {"artifact_type": "release_attestation",
                   "schema_version": "v14",
                   "annotation_release_manifest_sha256": "0" * 64,
                   "canonical_ref_commit": "sha1:" + commits["checkpoint"],
                   "config_hash": "0" * 64,
                   "promotion_payload_sha256": "0" * 64,
                   "exclusion_checkpoint_sha256": "0" * 64,
                   "prereg_amendment_sha256": "0" * 64,
                   "valid_at_release": True, "recorded_by": "ZD",
                   "recorded_at": TS, "attestation_sha256": "0" * 64}
    seal_self_hash(attestation, "attestation_sha256")
    artifacts = {"config": config, "candidate_protocol": protocol,
                 "exclusion_checkpoints": [checkpoint],
                 "release_attestation": attestation}
    repo_ctx = GitContext(
        repo, canonical_ref="refs/heads/main",
        artifact_commits={
            "candidate_protocol": commits["candidate_protocol"],
            "config": commits["config"],
            "batch": commits["batch"],
            "exposure_plan": commits["exposure_plan"],
            "promotion": commits["promotion"],
            "exclusion_checkpoints[0]": commits["checkpoint"],
            "release_attestation": commits["attestation"],
        },
        observed_ref_states=[commits["config"], commits["attestation"]])
    return artifacts, repo_ctx, commits


# ---------------------------------------------------------------------------
# committed JSON fixture files
# ---------------------------------------------------------------------------

def _jsonable(universe):
    out = {}
    for k, v in universe.items():
        if k == "evidence_snapshots":
            out[k] = {sha: blob.decode("utf-8") for sha, blob in v.items()}
        else:
            out[k] = v
    return out


def load_universe(path):
    data = json.loads(pathlib.Path(path).read_text())
    if "evidence_snapshots" in data:
        data["evidence_snapshots"] = {
            sha: text.encode("utf-8")
            for sha, text in data["evidence_snapshots"].items()}
    return data


def write_fixture_files(directory=FIXTURES_DIR):
    directory = pathlib.Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {"candidate_universe.json": build_candidate_universe(),
             "release_universe.json": build_release_universe()}
    for name, universe in files.items():
        (directory / name).write_text(
            json.dumps(_jsonable(universe), indent=1, sort_keys=True))
    return sorted(files)


if __name__ == "__main__":
    for name in write_fixture_files():
        print(f"wrote {FIXTURES_DIR / name}")
