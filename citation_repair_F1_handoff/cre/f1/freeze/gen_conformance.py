#!/usr/bin/env python3
"""Conformance report generator for F3-F7_FINDER_FREEZE_SCHEMAS.json (format v14).

Extends the seed script _conf_v11.py (supplied with the normative inputs).
Machine-verifies: duplicate keys (byte level), meta-schema, dangling $refs,
one positive instance per root artifact type, negative/discriminator cases.

Updated for the review-round residual schema deltas applied in the first build
commit (from that commit the repo copy is the pin authority):
  #3  selection items: optional `stratum` (PROPOSED, pending ZD approval)
  #5  stage configs: required-nullable `response_schema_sha256`
  #10 config.source.source_commit_oid -> git_commit_oid type

Output: F3-F7_SCHEMA_CONFORMANCE_REPORT.txt (no trailing newline), committed.
"""
import json, re, sys, hashlib, datetime, pathlib
from jsonschema import Draft202012Validator

BASE = pathlib.Path(__file__).resolve().parent
SCHEMA_FILE = "F3-F7_FINDER_FREEZE_SCHEMAS.json"
raw = (BASE / SCHEMA_FILE).read_bytes()
text = raw.decode("utf-8")

# --- duplicate-key check (byte level, before normal parse) ---
dups = []
def dup_hook(pairs):
    ks = [k for k, _ in pairs]
    seen = set()
    for k in ks:
        if k in seen:
            dups.append(k)
        seen.add(k)
    return dict(pairs)
json.loads(text, object_pairs_hook=dup_hook)

schema = json.loads(text)

# --- meta-schema ---
try:
    Draft202012Validator.check_schema(schema)
    meta_ok = True
except Exception as e:
    meta_ok = False
    meta_err = str(e)

# --- dangling $ref audit ---
refs = re.findall(r'"\$ref":\s*"([^"]+)"', text)
def resolve(ptr, doc):
    assert ptr.startswith("#/")
    node = doc
    for part in ptr[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, list):
            node = node[int(part)]
        else:
            if part not in node:
                return False
            node = node[part]
    return True
dangling = sorted({r for r in set(refs) if not resolve(r, schema)})

V = Draft202012Validator(schema)
def valid(inst):
    return not list(V.iter_errors(inst))

H = "0" * 64
H2 = "1" * 64
OID = "sha1:" + "0" * 40
RID = "0" * 32
TS = "2026-07-24T12:00:00Z"

def rp():
    return {"max_attempts": 3, "retryable_status": [429], "retryable_exceptions": ["connect_timeout"],
            "connect_timeout_seconds": "5", "read_timeout_seconds": "60", "total_timeout_seconds": "120",
            "backoff_base_seconds": "1", "backoff_cap_seconds": "30", "jitter_seconds": "1",
            "respect_retry_after": True, "retry_after_cap_seconds": "60",
            "idempotency_preimage_version": "CRE_FINDER_IDEMPOTENCY_V1", "record_attempts": True}
def ep():
    return {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1/messages",
            "host_allowlisted": True, "api_family": "messages", "api_version": "2023-06-01",
            "region": "us", "sdk_version": "1.0", "jcs_library_version": "1.0", "behavior_headers": {}}
def params():
    return {"temperature": {"state": "omitted"}, "max_tokens": {"state": "supplied", "value": 1024},
            "top_p": {"state": "omitted"}, "top_k": {"state": "omitted"},
            "stop_sequences": {"state": "omitted"}, "seed": {"state": "omitted"}}
def stage(extract=True):
    s = {"model_snapshot": "claude-opus-5", "response_parser_version": "v1",
         "tool_schema": None, "tool_schema_sha256": None, "params": params(),
         "system_message": {"state": "omitted"}, "endpoint": ep(), "retry": rp(),
         "response_schema_sha256": None}
    if extract:
        s["evidence_scope"] = "citing_sentence_only"
    else:
        s["evidence_scope"] = "abstract_snapshot"
        s["evidence_reader"] = "content_addressed_only"
        s["evidence_policy"] = {"retrieval": "efetch", "pmid_version_policy": "latest_at_snapshot",
                                "normalization": "nfc", "missing_evidence_sentinel": "EVIDENCE_UNAVAILABLE"}
    return s

CID = "PMC123:ref1"
not_run = {"call_made": False, "status": "not_run", "reason": "extraction_failed", "retry_wal_event_shas": []}
ok_call = {"call_made": True, "status": "ok", "logical_call_id": "lc1", "request_bytes_sha256": H,
           "idempotency_key": H, "retry_wal_event_shas": [H], "raw_response_body_ref": "cas/ab",
           "raw_response_body_sha256": H, "response_headers_allowlisted": {}, "http_status": 200,
           "parsed": {"claims": []}, "provider_model_id": "claude-opus-5", "response_id": "r1",
           "finish_reason": "end_turn"}
wal_prepared = {"artifact_type": "wal_event", "schema_version": "v14", "logical_call_id": "lc1",
                "attempt_id": "a1", "attempt_ordinal": 0, "wal_seq": 0, "prev_wal_event_sha256": None,
                "attempt_event_seq": 0, "prev_attempt_event_sha256": None, "event_type": "prepared",
                "recorded_at": TS, "request_bytes_sha256": H, "idempotency_key": H,
                "idempotency_preimage": {"domain": "CRE_FINDER_IDEMPOTENCY_V1", "config_hash": H,
                                          "run_id": RID, "citation_id": CID, "stage": "claim_extract",
                                          "claim_idx": None, "request_bytes_sha256": H},
                "sent_boundary_crossed": False, "event_sha256": H}

positives = {
  "prompt_package": {"artifact_type": "prompt_package", "schema_version": "v14", "name": "claim_extract",
    "template_text_version": 3, "package_version": 1, "template_utf8": "T <<CITING_SENTENCE>>",
    "template_utf8_sha256": H, "source_blob_oid": OID,
    "render_contract": {"message_construction": "structured", "collision_safe": True,
      "slots": [{"name": "CITING_SENTENCE", "role": "user", "encoding": "utf-8", "order": 0}]},
    "render_contract_sha256": H, "package_sha256": H},
  "config": {"artifact_type": "config", "schema_version": "v14", "canon": "canon_v1",
    "scope": "finder_frontend_extract_coverage", "candidate_protocol_sha256": H,
    "prompt_packages": {"claim_extract": {"package_version": 1, "package_sha256": H},
                        "coverage": {"package_version": 1, "package_sha256": H2}},
    "stages": {"claim_extract": stage(True), "coverage": stage(False)},
    "failure_policy": {"continue_after_coverage_terminal_failure": True},
    "source": {"repo_identity": "github.com/astonliu/citation-repair-engine",
               "canonical_ref": "refs/heads/release", "source_commit_oid": OID, "source_tree_oid": OID},
    "module_manifest_sha256": H,
    "runtime_profile": {"python_implementation": "cpython", "python_version": "3.10.12",
      "platform_constraint": "linux", "dependency_lock_sha256": H, "distribution_inventory_sha256": H,
      "transport_library_version": "1.0", "jcs_library_version": "1.0"},
    "codebook_sha256": H},
  "batch": {"artifact_type": "batch", "schema_version": "v14", "run_id": RID,
    "execution_mode": "candidate", "sample_purpose": "calibration",
    "promotion_payload_sha256_at_start": None, "exclusion_checkpoint_sha256_at_start": H,
    "canonical_ref_commit_observed": OID, "config_hash": H, "selection_artifact_sha256": H,
    "candidate_manifest_sha256": H,
    "genesis_preimage": {"config_hash": H, "run_id": RID, "selection_artifact_sha256": H,
                          "candidate_manifest_sha256": H},
    "genesis": H, "chain_tip": H, "chain_record_count": 1, "chain_hash_version": "canon_v1",
    "wal_tip_sha256": H, "review_dump_sha256": H, "status": "complete",
    "observed_runtime": {"python_implementation": "cpython", "python_version": "3.10.12",
      "platform": "linux", "dependency_lock_sha256": H, "distribution_inventory_sha256": H,
      "transport_library_version": "1.0", "jcs_library_version": "1.0",
      "argv": ["../.venv_cre/bin/python", "-m", "run"], "cwd": "/w", "fresh_interpreter": True,
      "matches_config_runtime_profile": True},
    "funnel": {"input_total": 1, "excluded": 0, "runner_accepted": 1, "flagged": 1, "clear": 0,
               "held": 0, "quarantined": 0},
    "diagnostics": {"refused_items": 0, "truncated_items": 0, "indeterminate_items": 0,
                    "refused_attempts": 0, "truncated_attempts": 0},
    "started": TS, "completed": TS},
  "run_state_manifest": {"artifact_type": "run_state_manifest", "schema_version": "v14", "run_id": RID,
    "execution_mode": "candidate", "sample_purpose": "calibration",
    "promotion_payload_sha256_at_start": None, "exclusion_checkpoint_sha256_at_start": H,
    "canonical_ref_commit_observed": OID, "config_hash": H, "selection_artifact_sha256": H,
    "candidate_manifest_sha256": H, "genesis": H, "status": "in_progress", "chain_tip": H,
    "chain_record_count": 0, "wal_tip_sha256": None, "review_dump_partial_sha256": H,
    "output_dir": "runs/x",
    "observed_runtime": {"python_implementation": "cpython", "python_version": "3.10.12",
      "platform": "linux", "dependency_lock_sha256": H, "distribution_inventory_sha256": H,
      "transport_library_version": "1.0", "jcs_library_version": "1.0", "argv": ["p"], "cwd": "/w",
      "fresh_interpreter": True, "matches_config_runtime_profile": True},
    "observed_runtime_sha256": H},
  "promotion": {"artifact_type": "promotion", "schema_version": "v14",
    "payload": {"config_hash": H, "run_hash": H, "candidate_protocol_sha256": H,
                "exposure_plan_sha256": H, "post_exposure_exclusion_checkpoint_sha256": H,
                "recorded_by": "ZD", "recorded_at": TS},
    "payload_sha256": H},
  "revocation": {"artifact_type": "revocation", "schema_version": "v14",
    "payload": {"target_config_hash": H, "target_promotion_payload_sha256": H,
                "reason": "defect", "recorded_by": "ZD", "recorded_at": TS},
    "payload_sha256": H},
  "selection_artifact": {"artifact_type": "selection_artifact", "schema_version": "v14",
    "sample_purpose": "calibration", "selection_rule": "rule", "min_size": 1,
    "coverage_targets": {"F6": 1},
    "items": [{"citation_id": CID, "citing_pmcid": "PMC123", "ref_id": "ref1",
               "occurrence_identity_version": "occ_id_v1", "normalized_ref_key_type": "resolved_pmid",
               "normalization_contract_sha256": H, "normalized_ref_key": "12345",
               "occurrence_identity": H, "source_xml_sha256": H, "ref_content_utf8": "ref",
               "extraction_contract_version": "v1", "source_occurrence_fingerprint": H,
               "corpus_source_id": "pmc_oa", "retrieval_record_sha256": H,
               "not_detector_sourced_attestation": True, "stratum": "F6"}],
    "recorded_by": "ZD"},
  "candidate_manifest": {"artifact_type": "candidate_manifest", "schema_version": "v14",
    "config_hash": H, "selection_artifact_sha256": H,
    "items": [{"citation_id": CID, "resolved_work_id": "pmid:1", "resolution_provenance_sha256": H,
               "evidence_snapshot_sha256": H, "source_xml_sha256": H}]},
  "exclusion_ledger_entry": {"artifact_type": "exclusion_ledger_entry", "schema_version": "v14",
    "seq": 0, "prev_entry_sha256": None, "entry_sha256": H, "citation_id": CID,
    "occurrence_identity": H, "citing_pmcid": "PMC123", "cluster_id": "c1",
    "scope": "citing_paper", "reason": "calibration", "recorded_at": TS},
  "exclusion_checkpoint": {"artifact_type": "exclusion_checkpoint", "schema_version": "v14",
    "tip_entry_sha256": None, "entry_count": 0, "canonical_ref_commit": OID},
  "review_record": {"artifact_type": "review_record", "schema_version": "v14", "seq": 0,
    "prev_record_sha256": None, "record_sha256": H, "item_key": CID, "citing_pmcid": "PMC123",
    "ref_id": "ref1", "claimed_pmid": "12345", "resolved_work_id": "pmid:12345",
    "citing_sentence": "S.", "config_hash": H, "run_id": RID, "extract": ok_call,
    "coverage": [{"claim_idx": 0, "call": ok_call}], "evidence_snapshot_sha256": H,
    "evidence_snapshot_ref": "cas/ev", "shape_flags": [], "stimulus_sha256": H,
    "finder_disposition": "flagged", "sample_purpose": "calibration",
    "run_facts": {"finder_configuration_attested": True, "finder_freeze_promoted_at_run": False,
                  "config_hash_used": H, "promotion_payload_sha256_at_run": None},
    "cluster_id": "c1"},
  "annotation_release_manifest": {"artifact_type": "annotation_release_manifest",
    "schema_version": "v14", "config_hash": H, "promotion_payload_sha256": H, "source_run_hash": H,
    "source_review_dump_sha256": H, "source_selection_hash": H, "exclusion_checkpoint_sha256": H,
    "codebook_sha256": H, "inventory": []},
  "release_attestation": {"artifact_type": "release_attestation", "schema_version": "v14",
    "annotation_release_manifest_sha256": H, "canonical_ref_commit": OID, "config_hash": H,
    "promotion_payload_sha256": H, "exclusion_checkpoint_sha256": H, "prereg_amendment_sha256": H,
    "valid_at_release": True, "recorded_by": "ZD", "recorded_at": TS, "attestation_sha256": H},
  "module_manifest": {"artifact_type": "module_manifest", "schema_version": "v14",
    "modules": [{"role": "bootstrap", "repo_path": "cre/f1/bootstrap.py", "blob_oid": OID,
                 "content_sha256": H}]},
  "wal_event": wal_prepared,
  "stimulus_object": {"artifact_type": "stimulus_object", "schema_version": "v14",
    "citing_sentence": "S.", "atomic_claims": [{"idx": 0, "text": "c"}],
    "evidence": {"evidence_snapshot_sha256": H, "text": "e"}, "label_space": ["supported", "unsupported"],
    "worksheet_schema": {"questions": [{"id": "q1", "prompt": "?", "answer_type": "single_choice",
                                        "choices": ["yes", "no"]}]},
    "display_instructions": "d", "codebook_content": "cb", "codebook_sha256": H,
    "rendering_settings": {"truncation": "none", "redaction": "none", "ordering": "document"}},
  "candidate_protocol": {"artifact_type": "candidate_protocol", "schema_version": "v14",
    "freeze_criterion": "criterion",
    "prohibited_cases": [{"citing_pmcid": "PMC111"}, {"citing_pmcid": "PMC222"}],
    "schema_sha256": H, "semantic_validator_version": "semantic_validator_v1",
    "evaluation_policy_version": "v1", "recorded_by": "ZD", "recorded_at": TS},
  "exposure_plan": {"artifact_type": "exposure_plan", "schema_version": "v14", "run_hash": H,
    "exposed": [{"citation_id": CID, "review_record_sha256": H}], "recorded_by": "ZD",
    "recorded_at": TS},
}

import copy
pos_results = {}
pos_errors = {}
for name, inst in positives.items():
    ok = valid(inst)
    pos_results[name] = ok
    if not ok:
        pos_errors[name] = [e.message for e in V.iter_errors(inst)][:3]

# release/formal/promo batch also passes
b_rel = copy.deepcopy(positives["batch"])
b_rel.update({"execution_mode": "release", "sample_purpose": "formal",
              "promotion_payload_sha256_at_start": H})
rel_ok = valid(b_rel)

negatives = {}
negatives["root {} (empty object)"] = not valid({})
negatives["root given null"] = not valid(None)
negatives["root given 7"] = not valid(7)
w = copy.deepcopy(wal_prepared); del w["idempotency_preimage"]
negatives["wal prepared missing preimage"] = not valid(w)
w = copy.deepcopy(wal_prepared); w["event_type"] = "sent"; w["sent_boundary_crossed"] = False
del w["idempotency_preimage"]
negatives["wal sent+boundary false"] = not valid(w)
b = copy.deepcopy(positives["batch"]); b["sample_purpose"] = "formal"
negatives["batch candidate+formal"] = not valid(b)
b = copy.deepcopy(positives["batch"]); b["promotion_payload_sha256_at_start"] = H
negatives["batch candidate+promotion"] = not valid(b)
b = copy.deepcopy(b_rel); b["promotion_payload_sha256_at_start"] = None
negatives["batch release+no-promo"] = not valid(b)
b = copy.deepcopy(b_rel); b["sample_purpose"] = "calibration"
negatives["batch release+calibration"] = not valid(b)
b = copy.deepcopy(positives["batch"]); b["execution_mode"] = "development"
negatives["batch development mode"] = not valid(b)
s = copy.deepcopy(positives["stimulus_object"])
del s["worksheet_schema"]["questions"][0]["choices"]
negatives["worksheet choice w/o choices"] = not valid(s)
p = copy.deepcopy(positives["promotion"])
p["payload"] = {"config_hash": H, "run_hash": H, "freeze_criterion": "dup",
                "predeclared_exposure_shas": [H], "recorded_by": "ZD", "recorded_at": TS}
negatives["promotion old opaque shape"] = not valid(p)
cp = copy.deepcopy(positives["candidate_protocol"])
cp["prohibited_cases"] = [{"citing_pmcid": "PMC111"}]
negatives["candidate_protocol 1 prohibited case (<2)"] = not valid(cp)
sm = copy.deepcopy(positives["config"])
sm["stages"]["claim_extract"]["system_message"] = {"state": "omitted", "text_utf8": "x"}
negatives["system_message omitted+text"] = not valid(sm)
tp = copy.deepcopy(positives["config"])
tp["stages"]["claim_extract"]["params"]["temperature"] = {"state": "supplied", "value": "1.0"}
negatives["supplied decimal temperature"] = not valid(tp)
rc = copy.deepcopy(positives["review_record"])
rc["extract"] = {"call_made": True, "status": "not_run", "reason": "evidence_unavailable",
                 "retry_wal_event_shas": []}
negatives["review_call call_made:true + not_run"] = not valid(rc)
rr = copy.deepcopy(positives["review_record"])
rr["finder_disposition"] = "excluded"
negatives["excluded record w/ non-null stimulus"] = not valid(rr)
# locked prompt slots (acceptance matrix: extras/dupes/reorder rejected)
pp = copy.deepcopy(positives["prompt_package"])
pp["render_contract"]["slots"].append({"name": "EVIDENCE", "role": "user", "encoding": "utf-8", "order": 1})
negatives["claim_extract with extra slot"] = not valid(pp)
cov_pkg = copy.deepcopy(positives["prompt_package"])
cov_pkg["name"] = "coverage"; cov_pkg["template_text_version"] = 2
cov_pkg["render_contract"]["slots"] = [
    {"name": "ATOMIC_CLAIM", "role": "user", "encoding": "utf-8", "order": 0},
    {"name": "EVIDENCE", "role": "user", "encoding": "utf-8", "order": 1}]
assert valid(cov_pkg), "coverage package positive must pass"
cov_bad = copy.deepcopy(cov_pkg)
cov_bad["render_contract"]["slots"] = [
    {"name": "EVIDENCE", "role": "user", "encoding": "utf-8", "order": 0},
    {"name": "ATOMIC_CLAIM", "role": "user", "encoding": "utf-8", "order": 1}]
negatives["coverage slots reordered"] = not valid(cov_bad)
# citation_id grammar: empty ref_id component
sel = copy.deepcopy(positives["selection_artifact"])
sel["items"][0]["citation_id"] = "PMC123:"
negatives["citation_id with empty ref_id"] = not valid(sel)
# Codex final-pass regression cases (Q1.1, Q1.2, Q1.3)
rs = copy.deepcopy(positives["run_state_manifest"])
rs["execution_mode"] = "release"  # keeps calibration + null promo
negatives["run_state release+calibration+null-promo"] = not valid(rs)
rs2 = copy.deepcopy(positives["run_state_manifest"])
rs2["promotion_payload_sha256_at_start"] = H
negatives["run_state candidate+promotion"] = not valid(rs2)
rs3 = copy.deepcopy(positives["run_state_manifest"])
rs3.update({"execution_mode": "release", "sample_purpose": "formal",
            "promotion_payload_sha256_at_start": H})
assert valid(rs3), "run_state release/formal/promo positive must pass"
err600 = copy.deepcopy(positives["review_record"])
err600["extract"] = {"call_made": True, "status": "provider_error", "logical_call_id": "e0",
                     "request_bytes_sha256": H, "idempotency_key": H, "retry_wal_event_shas": [H],
                     "http_status": 600, "raw_response_body_ref": "cas/r0",
                     "raw_response_body_sha256": H, "response_headers_allowlisted": {}}
err600["coverage"] = []
negatives["provider_error http_status 600"] = not valid(err600)
mm600 = copy.deepcopy(positives["review_record"])
mm600["extract"] = {"call_made": True, "status": "model_mismatch", "logical_call_id": "e0",
                    "request_bytes_sha256": H, "idempotency_key": H, "retry_wal_event_shas": [H],
                    "provider_model_id": "other-model", "http_status": 600}
mm600["coverage"] = []
negatives["model_mismatch http_status 600"] = not valid(mm600)
tv4 = copy.deepcopy(positives["prompt_package"])
tv4["template_text_version"] = 4
negatives["claim_extract text version != 3"] = not valid(tv4)
cv3 = copy.deepcopy(cov_pkg)
cv3["template_text_version"] = 3
negatives["coverage text version != 2"] = not valid(cv3)
# Codex fix-verification round B-Q1.1: non-UTF-8 slot encoding must reject
enc = copy.deepcopy(positives["prompt_package"])
enc["render_contract"]["slots"][0]["encoding"] = "utf-16"
negatives["slot encoding utf-16"] = not valid(enc)
# Unrestricted-round D13: CONFIG package_version -1 must reject
pv = copy.deepcopy(positives["config"])
pv["prompt_packages"]["claim_extract"]["package_version"] = -1
negatives["config package_version -1"] = not valid(pv)
# Residual #5 delta: response_schema_sha256 is required (nullable) per stage
nrs = copy.deepcopy(positives["config"])
del nrs["stages"]["claim_extract"]["response_schema_sha256"]
negatives["config stage missing response_schema_sha256"] = not valid(nrs)
# Residual #3 delta: stratum, when present, must be a nonempty string
st = copy.deepcopy(positives["selection_artifact"])
st["items"][0]["stratum"] = ""
negatives["selection item empty stratum"] = not valid(st)
# dead-def audit: every $defs member must be referenced (root oneOf, whole-def $ref, or sub-pointer $ref)
root_refs = {r["$ref"].split("/")[-1] for r in schema["oneOf"]}
unused = [d for d in schema["$defs"]
          if d not in root_refs
          and f'"#/$defs/{d}"' not in text
          and f'"#/$defs/{d}/' not in text]
negatives["unused $defs present: " + (",".join(unused) if unused else "none")] = not unused

sha = hashlib.sha256(raw).hexdigest()
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
lines = []
lines.append("CRE F3-F7 finder-freeze — schema conformance report (format v14)")
lines.append(f"generated_utc: {now}")
import importlib.metadata
lines.append(f"validator: python-jsonschema {importlib.metadata.version('jsonschema')} (Draft202012Validator)")
lines.append(f"interpreter: {sys.version.split()[0]}")
lines.append("command: python gen_conformance.py (extends seed _conf_v11.py)")
lines.append(f"schema_file: {SCHEMA_FILE} | bytes: {len(raw)} | sha256: {sha}")
lines.append(f"duplicate_keys: {'NONE' if not dups else dups}")
lines.append(f"meta_schema_draft202012: {'PASS' if meta_ok else 'FAIL ' + meta_err}")
lines.append(f"internal_refs: {len(refs)} occurrences, {len(set(refs))} unique targets, dangling: {'NONE' if not dangling else dangling}")
roots = [r["$ref"].split("/")[-1] for r in schema["oneOf"]]
lines.append(f"root_artifact_types ({len(roots)}): " + ", ".join(roots))
lines.append("per-root positive instance validation:")
for name in roots:
    lines.append(f"  {name:<28} {'PASS' if pos_results.get(name) else 'FAIL ' + str(pos_errors.get(name))}")
lines.append(f"  batch (release/formal/promo)  {'PASS' if rel_ok else 'FAIL'}")
lines.append("negative / discriminator checks:")
for k, v in negatives.items():
    lines.append(f"  {k:<42} {'REJECTED' if v else '!! ACCEPTED (DEFECT)'}")
lines.append(f"all_root_positive_pass: {all(pos_results.values()) and rel_ok}")
lines.append(f"all_negative_reject: {all(negatives.values())}")
lines.append("SCOPE: this report covers SCHEMA executability + discriminator behavior only. The")
lines.append("semantic_validator_v1 SV-* cross-artifact rules and their per-rule fixtures are implemented")
lines.append("WITH the validator during the build; this report does not assert them.")
report = "\n".join(lines)
(BASE / "F3-F7_SCHEMA_CONFORMANCE_REPORT.txt").write_text(report)  # no trailing newline
print(report)
