"""Acceptance tests for mint_v1 — the F3-F7 mint spec, PART 5 rows 1-21.

Every expected digest below is a literal measured AHEAD of the build at
2d69f44 (spec 1.6/PART 5), so a minter that agrees with the fixture builder
on the WRONG bytes is still caught (row 19 vs row 4).

Synthetic CONFIG inputs used here are TEST-ONLY values (every judgment string
is prefixed TEST). They exist so the machinery is provable; they are not
proposals, and nothing here writes a CONFIG outside pytest tmp dirs.

Verification command:
    PYTHONPATH=. ../.venv_cre/bin/python -m pytest cre/f1/test_mint_v1.py -q
"""
import copy
import hashlib
import json
import os
import pathlib
import subprocess
import sys

import pytest

from cde.claims import band_prompts
from cde.freeze import bootstrap, fixtures_v1 as fx, mint_v1, schema_gate
from cde.freeze.canon_v1 import canon_sha256, canon_v1
from cde.freeze.semantic_validator_v1 import (FROZEN_SOURCE_BLOB_OID,
                                                 RULES, validate)
from cde.freeze.strict_loader import load_strict

PYTHON = sys.executable
# The package root IS the repo root now: `cde/` sits directly under it.
REPO_HANDOFF = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = REPO_HANDOFF
FREEZE = REPO_HANDOFF / "cde" / "freeze"
MINT = FREEZE / "mint_v1.py"

# Frozen acceptance constants, as literals (rows 1-3, 19).
EXPECTED_TEMPLATE_SHA = {
    "claim_extract":
        "25f7de6267de4d638d1a5fc0c778b852d3efba4865c35c40ff1a0f980a6a4507",
    "coverage":
        "1a24d13be0e817a757c8fc5ea1ab40f059c11c580990b09bd5c2fe2d1125421a",
}
EXPECTED_BLOB_OID = "sha1:fa01126e2b9482d450065fd70cd0eb1fea816f5c"
EXPECTED_PACKAGE_SHA = {
    "claim_extract":
        "ac7b51723e698040f139dbb29470b1004269bc5601bdf8b008d39896ba385c65",
    "coverage":
        "b4d82f7995f69af3e13da23f6135d09be8ba55652797d9674ef38cac9a809928",
}
PACKAGE_FIELDS = frozenset({
    "artifact_type", "schema_version", "name", "template_text_version",
    "package_version", "template_utf8", "template_utf8_sha256",
    "source_blob_oid", "render_contract", "render_contract_sha256",
    "package_sha256"})

NAMES = ("claim_extract", "coverage")


def _sha256_utf8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_mint(*args):
    env = {**os.environ, "PYTHONPATH": str(REPO_HANDOFF)}
    return subprocess.run([PYTHON, str(MINT)] + list(args),
                          capture_output=True, text=True,
                          cwd=str(REPO_HANDOFF), env=env)


def _reseal_package(pkg):
    pkg["render_contract_sha256"] = canon_sha256(pkg["render_contract"])
    pkg["package_sha256"] = canon_sha256(
        {k: v for k, v in pkg.items() if k != "package_sha256"})
    return pkg


def synthetic_inputs(tmp_path):
    """Complete TEST-ONLY MINT_INPUTS content (judgment values are not
    proposals; every string says so)."""
    lock = tmp_path / "requirements.lock"
    lock.write_text("TEST-ONLY-lock==0.0.0 --hash=sha256:0\n")
    protocol = {
        "artifact_type": "candidate_protocol", "schema_version": "v14",
        "freeze_criterion": "TEST-ONLY synthetic criterion (not a proposal)",
        "prohibited_cases": [{"citing_pmcid": "PMC901"},
                             {"citing_pmcid": "PMC902"}],
        "schema_sha256": schema_gate.PINNED_SCHEMA_SHA256,
        "semantic_validator_version": "semantic_validator_v1",
        "evaluation_policy_version": "v1",
        "recorded_by": "ZD", "recorded_at": "2026-07-27T00:00:00Z"}
    manifest = {
        "artifact_type": "module_manifest", "schema_version": "v14",
        "modules": [{"role": r, "repo_path": f"cre/f1/freeze/mod_{r}.py",
                     "blob_oid": "sha1:" + "0" * 40,
                     "content_sha256": _sha256_utf8(f"TEST module {r}")}
                    for r in bootstrap.TRUST_BOUNDARY_ROLES]}
    params = {"temperature": {"state": "omitted"},
              "max_tokens": {"state": "supplied", "value": 2048},
              "top_p": {"state": "omitted"}, "top_k": {"state": "omitted"},
              "stop_sequences": {"state": "omitted"},
              "seed": {"state": "omitted"}}
    endpoint = {"provider": "anthropic",
                "base_url": "https://api.anthropic.com/v1/messages",
                "host_allowlisted": True, "api_family": "messages",
                "api_version": "2023-06-01", "region": "us",
                "sdk_version": "TEST-sdk-0",
                "jcs_library_version": "TEST-jcs-0", "behavior_headers": {}}
    retry = {"max_attempts": 2, "retryable_status": [429],
             "retryable_exceptions": ["connect_timeout"],
             "connect_timeout_seconds": "5", "read_timeout_seconds": "60",
             "total_timeout_seconds": "120", "backoff_base_seconds": "1",
             "backoff_cap_seconds": "30", "jitter_seconds": "1",
             "respect_retry_after": True, "retry_after_cap_seconds": "60",
             "idempotency_preimage_version": "CRE_FINDER_IDEMPOTENCY_V1",
             "record_attempts": True}

    def stage():
        return {"model_snapshot": "TEST-snapshot-0",
                "params": copy.deepcopy(params),
                "endpoint": copy.deepcopy(endpoint),
                "system_message": {"state": "omitted"},
                "tool_schema": None, "retry": copy.deepcopy(retry),
                "response_parser_version": "TEST-parser-v0"}

    coverage = stage()
    coverage["evidence_policy"] = {
        "retrieval": "TEST-efetch",
        "pmid_version_policy": "TEST-latest",
        "normalization": "TEST-nfc",
        "missing_evidence_sentinel": "TEST_EVIDENCE_UNAVAILABLE"}
    return {"stages": {"claim_extract": stage(), "coverage": coverage},
            "codebook_content": "TEST-ONLY synthetic codebook content.",
            "candidate_protocol": protocol,
            "dependency_lockfile_path": str(lock),
            "module_manifest": manifest}


def _universe(cfg, inputs):
    return {"prompt_packages": [mint_v1.mint_prompt_package(n)
                                for n in NAMES],
            "config": cfg, "config_hash": canon_sha256(cfg),
            "candidate_protocol": inputs["candidate_protocol"],
            "module_manifest": inputs["module_manifest"]}


# ---------------------------------------------------------------------------
# prompt packages (rows 1-9, 19, 21 + committed-file identity)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_row01_02_template_utf8_sha256(name):
    pkg = mint_v1.mint_prompt_package(name)
    assert pkg["template_utf8_sha256"] == EXPECTED_TEMPLATE_SHA[name]
    assert _sha256_utf8(pkg["template_utf8"]) == EXPECTED_TEMPLATE_SHA[name]


@pytest.mark.parametrize("name", NAMES)
def test_row03_source_blob_oid_prefix_convention(name):
    pkg = mint_v1.mint_prompt_package(name)
    assert pkg["source_blob_oid"] == EXPECTED_BLOB_OID
    assert pkg["source_blob_oid"] == FROZEN_SOURCE_BLOB_OID
    assert pkg["source_blob_oid"].startswith("sha1:")


@pytest.mark.parametrize("name", NAMES)
def test_row04_byte_identical_to_fixture_builder(name):
    pkg = mint_v1.mint_prompt_package(name)
    ref = fx.build_prompt_package(name)
    assert pkg == ref
    assert canon_v1(pkg) == canon_v1(ref)


@pytest.mark.parametrize("name", NAMES)
def test_row05_schema_valid(name):
    assert schema_gate.schema_errors(mint_v1.mint_prompt_package(name)) == []


def test_row06_claim_extract_slots():
    pkg = mint_v1.mint_prompt_package("claim_extract")
    assert pkg["render_contract"]["slots"] == [
        {"name": "CITING_SENTENCE", "role": "user", "encoding": "utf-8",
         "order": 0}]


def test_row07_coverage_slots():
    pkg = mint_v1.mint_prompt_package("coverage")
    assert pkg["render_contract"]["slots"] == [
        {"name": "ATOMIC_CLAIM", "role": "user", "encoding": "utf-8",
         "order": 0},
        {"name": "EVIDENCE", "role": "user", "encoding": "utf-8",
         "order": 1}]


def test_row08_template_drift_aborts_with_both_digests(monkeypatch):
    drifted = band_prompts.CLAIM_EXTRACT_PROMPT + "x"
    monkeypatch.setattr(band_prompts, "CLAIM_EXTRACT_PROMPT", drifted)
    with pytest.raises(mint_v1.MintError) as e:
        mint_v1.mint_prompt_package("claim_extract")
    msg = str(e.value)
    assert _sha256_utf8(drifted) in msg              # computed
    assert EXPECTED_TEMPLATE_SHA["claim_extract"] in msg  # expected


def test_row09_third_slot_rejected_by_items_false():
    pkg = mint_v1.mint_prompt_package("coverage")
    pkg["render_contract"]["slots"].append(
        {"name": "EXTRA", "role": "user", "encoding": "utf-8", "order": 2})
    _reseal_package(pkg)
    assert len(schema_gate.schema_errors(pkg)) >= 1


@pytest.mark.parametrize("name", NAMES)
def test_row19_package_sha256_measured_ahead_pins(name):
    pkg = mint_v1.mint_prompt_package(name)
    assert pkg["package_sha256"] == EXPECTED_PACKAGE_SHA[name]


@pytest.mark.parametrize("name", NAMES)
def test_row21_packages_carry_exactly_the_eleven_fields(name):
    pkg = mint_v1.mint_prompt_package(name)
    assert set(pkg) == PACKAGE_FIELDS
    assert "params" not in pkg


@pytest.mark.parametrize("name", NAMES)
def test_committed_package_files_are_the_minted_canonical_bytes(name):
    path = mint_v1.prompt_package_path(name)
    pkg = mint_v1.mint_prompt_package(name)
    assert path.is_file(), f"{path} is not committed"
    assert path.read_bytes() == canon_v1(pkg)
    assert load_strict(path) == pkg


# ---------------------------------------------------------------------------
# CONFIG fail-closed behavior (rows 10-12 + grouping/counts)
# ---------------------------------------------------------------------------

def _freeze_snapshot():
    return {p for p in FREEZE.rglob("*")
            if "__pycache__" not in p.parts and p.suffix != ".pyc"}


def test_rows10_11_12_cli_config_without_inputs_fails_closed():
    # Tested behavior: the canonical input file does not exist (PART 9).
    assert not (FREEZE / "MINT_INPUTS.json").exists()
    before = _freeze_snapshot()
    r = _run_mint("--config")
    assert r.returncode != 0                                   # row 10
    assert _freeze_snapshot() == before                        # row 12
    out = r.stdout
    assert "CANNOT MINT" in out                                # row 11
    for num in ("#1", "#2", "#3", "#4", "#5", "#6"):
        assert f"input {num}" in out
    expected_fields = (
        ["stages.claim_extract.model_snapshot",
         "stages.coverage.model_snapshot", "codebook_sha256",
         "candidate_protocol.freeze_criterion",
         "candidate_protocol.prohibited_cases",
         "runtime_profile.dependency_lock_sha256", "module_manifest"]
        + [f"stages.coverage.evidence_policy.{k}"
           for k in mint_v1.EVIDENCE_POLICY_KEYS]
        + [f"stages.{s}.params.{p}" for s in NAMES
           for p in mint_v1.PARAM_KEYS]
        + [f"stages.{s}.{k}" for s in NAMES
           for k in ("endpoint", "system_message", "tool_schema", "retry",
                     "response_parser_version")])
    for field in expected_fields:
        assert field in out, f"missing field {field} in the report"
    # The refusal points a reader somewhere that still exists.
    assert mint_v1.DECISIONS_DOC in out
    assert "README" in mint_v1.DECISIONS_DOC


def test_empty_inputs_counts_six_and_twentythree():
    with pytest.raises(mint_v1.MintInputsError) as e:
        mint_v1.mint_config({})
    assert len(e.value.numbered) == 6
    assert len(e.value.unnumbered) == 23
    assert ("CANNOT MINT — 6 canonical inputs and 23 unnumbered decisions "
            "outstanding.") in e.value.report


def test_partial_inputs_report_names_only_the_gap(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    del inputs["stages"]["coverage"]["evidence_policy"]["retrieval"]
    with pytest.raises(mint_v1.MintInputsError) as e:
        mint_v1.mint_config(inputs)
    assert [(num, fields) for num, _, fields in e.value.numbered] == \
        [("#3", ["stages.coverage.evidence_policy.retrieval"])]
    assert e.value.unnumbered == []


def test_unknown_input_key_fails_closed(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    inputs["stages"]["claim_extract"]["temprature"] = {"state": "omitted"}
    with pytest.raises(mint_v1.MintError) as e:
        mint_v1.mint_config(inputs)
    assert "E_MINT_UNKNOWN_INPUT" in str(e.value)


# ---------------------------------------------------------------------------
# CONFIG derivation (rows 13-17, 20)
# ---------------------------------------------------------------------------

def test_row13_bad_model_snapshot_fails_the_schema_gate(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    inputs["stages"]["claim_extract"]["model_snapshot"] = "-bad"
    cfg = mint_v1.mint_config(inputs)
    assert len(schema_gate.schema_errors(cfg)) >= 1


def test_row14_minted_config_validates_clean(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    cfg = mint_v1.mint_config(inputs)
    assert schema_gate.schema_errors(cfg) == []
    assert validate(_universe(cfg, inputs)) == []


def test_row15_flipped_module_manifest_sha_fires_sv110_only(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    cfg = mint_v1.mint_config(inputs)
    h = cfg["module_manifest_sha256"]
    cfg["module_manifest_sha256"] = \
        ("1" if h[0] != "1" else "2") + h[1:]
    violations = validate(_universe(cfg, inputs))
    assert violations, "SV-110 did not fire"
    assert {v.rule_id for v in violations} == {"SV-110"}
    assert all(v.fail_code == RULES["SV-110"] for v in violations)


def test_row16_foreign_repo_identity_fires_the_trust_root_rule(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    cfg = mint_v1.mint_config(inputs)
    cfg["source"]["repo_identity"] = "other/repo"
    violations = validate(_universe(cfg, inputs))
    assert violations, "SV-042 did not fire"
    assert {v.rule_id for v in violations} == {"SV-042"}
    assert all(v.fail_code == RULES["SV-042"] for v in violations)


def test_row17_cli_complete_inputs_emits_null_with_residual5_notice(
        tmp_path):
    inputs = synthetic_inputs(tmp_path)
    inputs_path = tmp_path / "MINT_INPUTS.json"
    inputs_path.write_text(json.dumps(inputs, indent=1))
    out_dir = tmp_path / "out"
    r = _run_mint("--config", "--inputs", str(inputs_path),
                  "--out", str(out_dir))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "residual #5" in r.stdout
    written = sorted(out_dir.glob("CONFIG_*.json"))
    assert len(written) == 1
    cfg = load_strict(written[0])
    for stage in NAMES:
        assert cfg["stages"][stage]["response_schema_sha256"] is None
    # config_hash is the FILENAME digest over the stored canonical bytes.
    config_hash = written[0].stem[len("CONFIG_"):]
    assert canon_sha256(cfg) == config_hash
    assert hashlib.sha256(written[0].read_bytes()).hexdigest() == config_hash


def test_row20_prompt_package_refs_must_not_be_empty(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    cfg = mint_v1.mint_config(inputs)
    for name in NAMES:
        assert cfg["prompt_packages"][name] == {
            "package_version": 4,
            "package_sha256": EXPECTED_PACKAGE_SHA[name]}
    hollow = copy.deepcopy(cfg)
    hollow["prompt_packages"]["claim_extract"] = {}
    assert len(schema_gate.schema_errors(hollow)) >= 1


def test_config_source_and_trust_root_are_derived_not_supplied(tmp_path):
    inputs = synthetic_inputs(tmp_path)
    cfg = mint_v1.mint_config(inputs)
    assert cfg["source"]["repo_identity"] == bootstrap.TRUSTED_REPO_IDENTITY
    assert cfg["source"]["canonical_ref"] == bootstrap.TRUSTED_CANONICAL_REF
    # source_tree_oid is THE commit's tree, never derived independently.
    commit = cfg["source"]["source_commit_oid"].split(":", 1)[1]
    tree = subprocess.run(
        ["git", "-C", str(FREEZE), "rev-parse", commit + "^{tree}"],
        capture_output=True, text=True).stdout.strip()
    assert cfg["source"]["source_tree_oid"] == "sha1:" + tree
    assert cfg["codebook_sha256"] == \
        _sha256_utf8(inputs["codebook_content"])


# ---------------------------------------------------------------------------
# row 18 — worktree hygiene
# ---------------------------------------------------------------------------

def test_row18_mint_run_leaves_the_worktree_clean():
    r = _run_mint("--prompt-packages")
    assert r.returncode == 0, r.stdout + r.stderr
    status = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "status", "--porcelain", "--",
         "cde/freeze"],
        capture_output=True, text=True)
    assert status.stdout.strip() == "", status.stdout
    ignored = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "check-ignore", "-q",
         "cde/freeze/_mint_out/CONFIG_x.json"],
        capture_output=True, text=True)
    assert ignored.returncode == 0, "_mint_out/ is not gitignored"
