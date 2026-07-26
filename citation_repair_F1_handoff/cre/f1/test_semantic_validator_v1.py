"""Fixture-proven tests for the F3-F7 freeze substrate (build spec item 5).

One POSITIVE and one TARGETED NEGATIVE fixture per §12 SV rule — every
negative asserts its rule's EXACT fail code, and mutations are re-sealed so
only the targeted rule fires. Plus strict_loader / canon_v1 / schema_gate
unit tests and the SV-110 bootstrap subprocess fixtures (runtime gate).

Verification command:
    cd citation_repair_F1_handoff
    PYTHONPATH=. ../.venv_cre/bin/python -m pytest cre/f1/test_semantic_validator_v1.py -q
"""
import copy
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

from cre.f1.freeze import bootstrap, fixtures_v1 as fx, schema_gate
from cre.f1.freeze.canon_v1 import CanonV1Error, canon_sha256, canon_v1
from cre.f1.freeze.semantic_validator_v1 import (RULES, TrustedConstants,
                                                 pctencode, validate)
from cre.f1.freeze.strict_loader import StrictLoadError, load_strict

PYTHON = sys.executable
TRUSTED = fx.trusted_for_fixtures()


@pytest.fixture(scope="module")
def candidate_universe():
    return fx.build_candidate_universe()


@pytest.fixture(scope="module")
def release_universe():
    return fx.build_release_universe()


@pytest.fixture(scope="module")
def git_universe(tmp_path_factory):
    return fx.build_git_universe(tmp_path_factory.mktemp("git"))


@pytest.fixture()
def cand(candidate_universe):
    return copy.deepcopy(candidate_universe)


@pytest.fixture()
def rel(release_universe):
    return copy.deepcopy(release_universe)


def fired(violations, rule_id):
    hits = [v for v in violations if v.rule_id == rule_id]
    for v in hits:
        assert v.fail_code == RULES[rule_id], \
            f"{rule_id} must carry exact fail code {RULES[rule_id]}"
    return hits


def assert_fires(violations, rule_id):
    hits = fired(violations, rule_id)
    assert hits, (f"{rule_id} did not fire; got "
                  f"{[(v.rule_id, v.path) for v in violations]}")
    return hits


def assert_only(violations, rule_id):
    hits = assert_fires(violations, rule_id)
    others = [v for v in violations if v.rule_id != rule_id]
    assert not others, (f"collateral violations beyond {rule_id}: "
                        f"{[(v.rule_id, v.path) for v in others]}")
    return hits


# ---------------------------------------------------------------------------
# strict_loader (pre-parse rejections; acceptance matrix rows)
# ---------------------------------------------------------------------------

def test_strict_loader_duplicate_key_rejected_pre_parse():
    with pytest.raises(StrictLoadError) as e:
        load_strict(b'{"a":1,"a":2}')
    assert e.value.code == "E_DUPLICATE_KEY"


def test_strict_loader_duplicate_key_any_depth():
    with pytest.raises(StrictLoadError) as e:
        load_strict(b'{"x":{"deep":[{"a":1,"a":2}]}}')
    assert e.value.code == "E_DUPLICATE_KEY"


@pytest.mark.parametrize("payload", [b'{"t":1.0}', b'{"t":1e3}',
                                     b'{"t":NaN}', b'{"t":Infinity}',
                                     b'{"t":-Infinity}', b'[0.5]'])
def test_strict_loader_float_tokens_rejected_pre_parse(payload):
    with pytest.raises(StrictLoadError) as e:
        load_strict(payload)
    assert e.value.code == "E_FLOAT_TOKEN"


def test_strict_loader_accepts_clean_json_and_paths(tmp_path):
    assert load_strict(b'{"a":1,"b":[true,null,"x"]}') == \
        {"a": 1, "b": [True, None, "x"]}
    p = tmp_path / "x.json"
    p.write_bytes(b'{"k":2}')
    assert load_strict(p) == {"k": 2}
    assert load_strict(str(p)) == {"k": 2}


def test_strict_loader_rejects_non_utf8():
    with pytest.raises(StrictLoadError) as e:
        load_strict(b'{"a":"\xff"}')
    assert e.value.code == "E_ENCODING"


# ---------------------------------------------------------------------------
# canon_v1 (RFC 8785 / JCS, floats prohibited)
# ---------------------------------------------------------------------------

def test_canon_v1_known_vector():
    assert canon_v1({"b": 2, "a": 1, "flag": True, "n": None}) == \
        b'{"a":1,"b":2,"flag":true,"n":null}'


def test_canon_v1_utf16_code_unit_key_sort():
    # U+1F600 (surrogate pair D83D DE00) sorts BEFORE U+FB33 under UTF-16
    # code units, though its code point is larger.
    out = canon_v1({"דּ": 1, "\U0001f600": 2}).decode("utf-8")
    assert out.index("\U0001f600") < out.index("דּ")


def test_canon_v1_string_escapes():
    assert canon_v1({"s": "	\n\r\"\\"}) == \
        b'{"s":"\\b\\t\\n\\u000b\\f\\r\\"\\\\"}'


def test_canon_v1_floats_prohibited():
    with pytest.raises(CanonV1Error) as e:
        canon_v1({"t": 1.0})
    assert e.value.code == "E_FLOAT"


def test_canon_v1_i_json_int_range():
    assert canon_v1(9007199254740991) == b"9007199254740991"
    with pytest.raises(CanonV1Error) as e:
        canon_v1(9007199254740992)
    assert e.value.code == "E_INT_RANGE"


def test_canon_v1_rejects_nonstring_keys():
    with pytest.raises(CanonV1Error):
        canon_v1({1: "x"})


# ---------------------------------------------------------------------------
# schema_gate (fail closed on wrong bytes; root discrimination)
# ---------------------------------------------------------------------------

def test_schema_gate_pin_verifies_and_validates(candidate_universe):
    schema = schema_gate.load_pinned_schema()
    assert schema["$id"] == "cre:f3f7:finder-freeze:schemas:v14"
    assert schema_gate.is_schema_valid(candidate_universe["config"])


def test_schema_gate_wrong_bytes_fail_closed(tmp_path):
    tampered = tmp_path / "schema.json"
    raw = bytearray(schema_gate.SCHEMA_PATH.read_bytes())
    raw[100] ^= 1
    tampered.write_bytes(bytes(raw))
    with pytest.raises(schema_gate.SchemaGateError) as e:
        schema_gate.load_pinned_schema(tampered)
    assert e.value.code == "E_SCHEMA_PIN"
    with pytest.raises(schema_gate.SchemaGateError):
        schema_gate.get_validator(tampered)  # zero side effects: no validator
    assert str(tampered) not in schema_gate._cache


@pytest.mark.parametrize("bad", [{}, None, 7])
def test_schema_root_rejects_non_artifacts(bad):
    assert not schema_gate.is_schema_valid(bad)


def test_all_universe_artifacts_schema_validate(candidate_universe,
                                                release_universe):
    singles = ["config", "batch", "run_state_manifest", "promotion",
               "selection_artifact", "candidate_manifest",
               "candidate_protocol", "exposure_plan", "module_manifest",
               "annotation_release_manifest", "release_attestation"]
    lists = ["prompt_packages", "exclusion_ledger", "exclusion_checkpoints",
             "review_records", "wal_events"]
    for u in (candidate_universe, release_universe):
        for key in singles:
            if u.get(key) is not None:
                assert schema_gate.is_schema_valid(u[key]), key
        for key in lists:
            for art in u.get(key) or []:
                assert schema_gate.is_schema_valid(art), key
        for stim in u["stimulus_objects"].values():
            assert schema_gate.is_schema_valid(stim)


def test_committed_fixture_files_match_builder():
    for name, build in (("candidate_universe.json",
                         fx.build_candidate_universe),
                        ("release_universe.json", fx.build_release_universe)):
        committed = json.loads((fx.FIXTURES_DIR / name).read_text())
        rebuilt = json.loads(json.dumps(fx._jsonable(build())))
        assert committed == rebuilt, f"{name} drifted from the builder"


# ---------------------------------------------------------------------------
# per-rule positives: the coherent universes carry every rule's inputs and
# produce ZERO violations (targeted per-rule assertion below)
# ---------------------------------------------------------------------------

def test_candidate_universe_positive_zero_violations(candidate_universe):
    assert validate(candidate_universe, trusted=TRUSTED) == []


def test_release_universe_positive_zero_violations(release_universe):
    assert validate(release_universe, trusted=TRUSTED) == []


def test_git_universe_positive_zero_violations(git_universe):
    artifacts, repo_ctx, _ = git_universe
    assert validate(artifacts, repo_ctx=repo_ctx, trusted=TRUSTED) == []


@pytest.mark.parametrize("rule_id", sorted(RULES))
def test_rule_positive_fixture_passes(rule_id, candidate_universe,
                                      release_universe, git_universe):
    artifacts, repo_ctx, _ = git_universe
    all_violations = (validate(candidate_universe, trusted=TRUSTED)
                      + validate(release_universe, trusted=TRUSTED)
                      + validate(artifacts, repo_ctx=repo_ctx,
                                 trusted=TRUSTED))
    assert not fired(all_violations, rule_id)


def test_rule_table_is_the_v17_contract():
    assert len(RULES) == 35
    assert RULES["SV-001"] == "E_SELF_HASH"
    assert RULES["SV-110"] == "E_BOOTSTRAP"


# ---------------------------------------------------------------------------
# SV-001 — self-hash preimages
# ---------------------------------------------------------------------------

def test_sv001_positive_promotion_payload_only_preimage(cand):
    promo = cand["promotion"]
    assert promo["payload_sha256"] == canon_sha256(promo["payload"])
    assert not fired(validate(cand, trusted=TRUSTED), "SV-001")


def test_sv001_negative_promotion_wrong_preimage(cand):
    # Acceptance row: payload_sha256 computed as canon(envelope ∖ field) —
    # the WRONG preimage for an envelope artifact.
    promo = cand["promotion"]
    promo["payload_sha256"] = canon_sha256(
        {k: v for k, v in promo.items() if k != "payload_sha256"})
    assert_only(validate(cand, trusted=TRUSTED), "SV-001")


def test_sv001_negative_observed_runtime_recompute(cand):
    cand["run_state_manifest"]["observed_runtime_sha256"] = "f" * 64
    assert_only(validate(cand, trusted=TRUSTED), "SV-001")


# ---------------------------------------------------------------------------
# SV-002 — recompute-never-trust template/render digests + frozen constants
# ---------------------------------------------------------------------------

def test_sv002_negative_drifted_template_with_pasted_digest(cand):
    pkg = cand["prompt_packages"][0]
    pkg["template_utf8"] = pkg["template_utf8"] + "\nDRIFTED"
    fx.seal_self_hash(pkg, "package_sha256")  # keep SV-001 quiet
    cand["config"]["prompt_packages"]["claim_extract"]["package_sha256"] = \
        pkg["package_sha256"]
    fx.seal_config(cand)
    fx.reseal_candidate_universe(cand)
    hits = assert_only(validate(cand, trusted=TRUSTED), "SV-002")
    assert any("template_utf8" in v.path for v in hits)


def test_sv002_negative_config_binding_stale(cand):
    cand["config"]["prompt_packages"]["coverage"]["package_sha256"] = "e" * 64
    fx.seal_config(cand)
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-002")


# ---------------------------------------------------------------------------
# SV-003 — filename digests
# ---------------------------------------------------------------------------

def test_sv003_negative_config_hash(cand):
    cand["config_hash"] = "d" * 64
    violations = validate(cand, trusted=TRUSTED)
    assert_fires(violations, "SV-003")
    assert all(v.rule_id == "SV-003" for v in violations)


def test_sv003_negative_run_hash(cand):
    cand["run_hash"] = "d" * 64
    violations = validate(cand, trusted=TRUSTED)
    assert_fires(violations, "SV-003")


# ---------------------------------------------------------------------------
# SV-005 — observed_runtime == CONFIG runtime_profile (residual #2)
# ---------------------------------------------------------------------------

def test_sv005_negative_runtime_mismatch(cand):
    artifacts = {"config": cand["config"], "batch": cand["batch"]}
    artifacts["batch"]["observed_runtime"]["python_version"] = "9.9.9"
    hits = assert_only(validate(artifacts, trusted=TRUSTED), "SV-005")
    assert "python_version" in hits[0].message


# ---------------------------------------------------------------------------
# SV-010 — funnel equations
# ---------------------------------------------------------------------------

def test_sv010_negative_funnel(cand):
    batch = cand["batch"]
    batch["funnel"]["input_total"] = batch["funnel"]["input_total"] + 1
    assert_only(validate({"batch": batch}, trusted=TRUSTED), "SV-010")


# ---------------------------------------------------------------------------
# SV-011 — disposition precedence recompute
# ---------------------------------------------------------------------------

def test_sv011_negative_terminal_error_claimed_clear(cand):
    rec = cand["review_records"][1]
    rec["finder_disposition"] = "flagged"  # verdicts say clear
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-011")


# ---------------------------------------------------------------------------
# SV-020 — genesis + review chain
# ---------------------------------------------------------------------------

def test_sv020_negative_seq_gap(cand):
    cand["review_records"][1]["seq"] = 5
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-020")


def test_sv020_negative_genesis_preimage_drift(cand):
    cand["batch"]["genesis"] = "9" * 64
    # The batch bytes changed, so rebind the filename digest and the
    # promotion/exposure references to it — only the genesis drift remains.
    cand["run_hash"] = canon_sha256(cand["batch"])
    cand["exposure_plan"]["run_hash"] = cand["run_hash"]
    payload = cand["promotion"]["payload"]
    payload["run_hash"] = cand["run_hash"]
    payload["exposure_plan_sha256"] = canon_sha256(cand["exposure_plan"])
    fx.seal_envelope(cand["promotion"])
    assert_only(validate(cand, trusted=TRUSTED), "SV-020")


# ---------------------------------------------------------------------------
# SV-021 — WAL chain linkage
# ---------------------------------------------------------------------------

def test_sv021_negative_indeterminate_without_prior_sent():
    events = fx.build_wal_triplet(lcid="lc-ind")
    prepared = events[0]
    indet = {k: v for k, v in prepared.items()
             if k != "idempotency_preimage"}
    indet.update({"event_type": "indeterminate", "wal_seq": 1,
                  "attempt_event_seq": 1, "sent_boundary_crossed": True,
                  "transport_exception_class": "ReadTimeout",
                  "prev_attempt_terminal_sha256": prepared["event_sha256"]})
    chain = fx.seal_wal_events([copy.deepcopy(prepared), indet])
    assert_only(validate({"wal_events": chain}, trusted=TRUSTED), "SV-021")


def test_sv021_negative_global_seq_gap():
    events = fx.build_wal_triplet(lcid="lc-gap")
    events[2]["wal_seq"] = 7
    fx.seal_wal_events(events)
    assert_only(validate({"wal_events": events}, trusted=TRUSTED), "SV-021")


# ---------------------------------------------------------------------------
# SV-022 — send-boundary retry rule
# ---------------------------------------------------------------------------

def test_sv022_negative_retry_after_boundary():
    events = fx.build_wal_triplet(lcid="lc-retry")
    dangling = events[:2]  # prepared, sent — boundary crossed, no response
    retry = copy.deepcopy(events[0])
    retry.update({"attempt_id": "a-lc-retry-1", "attempt_ordinal": 1,
                  "wal_seq": 2, "attempt_event_seq": 0})
    chain = fx.seal_wal_events([copy.deepcopy(e) for e in dangling] + [retry])
    assert_only(validate({"wal_events": chain}, trusted=TRUSTED), "SV-022")


# ---------------------------------------------------------------------------
# SV-023 — idempotency key recompute
# ---------------------------------------------------------------------------

def test_sv023_negative_key_mismatch():
    events = fx.build_wal_triplet(lcid="lc-idem")[:1]
    events[0]["idempotency_key"] = "e" * 64
    fx.seal_wal_events(events)
    assert_only(validate({"wal_events": events}, trusted=TRUSTED), "SV-023")


# ---------------------------------------------------------------------------
# SV-024 — parsed shape (interim per residual #5) + coverage cardinality
# ---------------------------------------------------------------------------

def test_sv024_negative_missing_parsed_shape(cand):
    cand["review_records"][0]["extract"]["parsed"] = None
    fx.reseal_candidate_universe(cand)
    hits = assert_only(validate(cand, trusted=TRUSTED), "SV-024")
    assert any("pending ZD approval" in v.message for v in hits), \
        "interim SV-024 must say it validates shape presence only"


def test_sv024_negative_coverage_cardinality(cand):
    rec = cand["review_records"][0]
    rec["extract"]["parsed"] = {"claims": [{"idx": 0, "text": "one"},
                                           {"idx": 1, "text": "two"}]}
    fx.reseal_candidate_universe(cand)
    violations = validate(cand, trusted=TRUSTED)
    assert_fires(violations, "SV-024")


# ---------------------------------------------------------------------------
# SV-025 — evidence/citing-sentence derivation
# ---------------------------------------------------------------------------

def test_sv025_negative_evidence_not_from_snapshot(cand):
    cid = cand["review_records"][0]["item_key"]
    stim = cand["stimulus_objects"][cid]
    stim["evidence"]["text"] = "Fabricated evidence never in the snapshot."
    cand["review_records"][0]["stimulus_sha256"] = canon_sha256(stim)
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-025")


# ---------------------------------------------------------------------------
# SV-026 — review_call == terminal WAL event
# ---------------------------------------------------------------------------

def test_sv026_negative_response_fields_differ(cand):
    cand["review_records"][0]["extract"]["http_status"] = 500
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-026")


# ---------------------------------------------------------------------------
# SV-030 — manifest rows ⊆ source BATCH review chain
# ---------------------------------------------------------------------------

def test_sv030_negative_row_seq_not_in_chain(rel):
    rel["annotation_release_manifest"]["inventory"][0]["review_record_seq"] = 7
    att = rel["release_attestation"]
    att["annotation_release_manifest_sha256"] = \
        canon_sha256(rel["annotation_release_manifest"])
    fx.seal_self_hash(att, "attestation_sha256")
    assert_only(validate(rel, trusted=TRUSTED), "SV-030")


# ---------------------------------------------------------------------------
# SV-031 — manifest sort/uniqueness
# ---------------------------------------------------------------------------

def test_sv031_negative_duplicate_row(rel):
    inv = rel["annotation_release_manifest"]["inventory"]
    inv.append(copy.deepcopy(inv[0]))
    att = rel["release_attestation"]
    att["annotation_release_manifest_sha256"] = \
        canon_sha256(rel["annotation_release_manifest"])
    fx.seal_self_hash(att, "attestation_sha256")
    assert_only(validate(rel, trusted=TRUSTED), "SV-031")


# ---------------------------------------------------------------------------
# SV-032 — calibration ⇒ ineligible (derived, never trusted)
# ---------------------------------------------------------------------------

def test_sv032_negative_calibration_item_in_release_inventory(rel):
    rel["review_records"][0]["sample_purpose"] = "calibration"
    del rel["reportability_claims"]  # SV-050's derivation changes with it
    fx.reseal_release_universe(rel)
    assert_only(validate(rel, trusted=TRUSTED), "SV-032")


# ---------------------------------------------------------------------------
# SV-033 — selection invariants (residual #3 proposal)
# ---------------------------------------------------------------------------

def test_sv033_negative_below_min_size(cand):
    cand["selection_artifact"]["min_size"] = 3
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-033")


def test_sv033_negative_unmet_coverage_target(cand):
    cand["selection_artifact"]["coverage_targets"] = {"F6": 5}
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-033")


def test_sv033_negative_missing_stratum_fails_closed(cand):
    for item in cand["selection_artifact"]["items"]:
        del item["stratum"]
    fx.reseal_candidate_universe(cand)
    hits = assert_only(validate(cand, trusted=TRUSTED), "SV-033")
    assert any("residual #3" in v.message for v in hits), \
        "pending-ZD proposal must be named in the violation"


# ---------------------------------------------------------------------------
# SV-034 — candidate == committed selection binding (residual #4)
# ---------------------------------------------------------------------------

def test_sv034_negative_order_mismatch(cand):
    cand["candidate_manifest"]["items"].reverse()
    fx.reseal_candidate_universe(cand)
    violations = validate(cand, trusted=TRUSTED)
    assert_fires(violations, "SV-034")


def test_sv034_negative_conflicting_source_snapshot(cand):
    cand["candidate_manifest"]["items"][0]["source_xml_sha256"] = "a" * 64
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-034")


# ---------------------------------------------------------------------------
# SV-040 — exclusion chronology (checkpoint N / N+1)
# ---------------------------------------------------------------------------

def test_sv040_negative_promotion_pins_stale_checkpoint(cand):
    cp_empty = cand["exclusion_checkpoints"][0]
    cand["promotion"]["payload"]["post_exposure_exclusion_checkpoint_sha256"] \
        = canon_sha256(cp_empty)
    fx.seal_envelope(cand["promotion"])
    assert_only(validate(cand, trusted=TRUSTED), "SV-040")


# ---------------------------------------------------------------------------
# SV-041 — git anchoring (temp git repo fixture; residual #10)
# ---------------------------------------------------------------------------

def test_sv041_negative_parent_not_validated_state(git_universe):
    artifacts, repo_ctx, commits = git_universe
    artifacts = copy.deepcopy(artifacts)
    att = artifacts["release_attestation"]
    att["canonical_ref_commit"] = "sha1:" + commits["base"]
    fx.seal_self_hash(att, "attestation_sha256")
    assert_only(validate(artifacts, repo_ctx=repo_ctx, trusted=TRUSTED),
                "SV-041")


def test_sv041_negative_source_tree_not_commits_tree(git_universe):
    artifacts, repo_ctx, commits = git_universe
    artifacts = copy.deepcopy(artifacts)
    artifacts["config"]["source"]["source_tree_oid"] = "sha1:" + "1" * 40
    assert_only(validate(artifacts, repo_ctx=repo_ctx, trusted=TRUSTED),
                "SV-041")


def test_sv041_negative_artifact_unreachable_from_ref(git_universe, tmp_path):
    artifacts, repo_ctx, commits = git_universe
    tree = subprocess.run(["git", "-C", repo_ctx.repo_dir, "rev-parse",
                           "HEAD^{tree}"], capture_output=True,
                          text=True).stdout.strip()
    dangling = subprocess.run(["git", "-C", repo_ctx.repo_dir, "commit-tree",
                               tree, "-m", "dangling"], capture_output=True,
                              text=True).stdout.strip()
    ctx = copy.deepcopy(repo_ctx.artifact_commits)
    ctx["promotion"] = dangling
    from cre.f1.freeze.semantic_validator_v1 import GitContext
    repo_ctx2 = GitContext(repo_ctx.repo_dir, repo_ctx.canonical_ref, ctx,
                           repo_ctx.observed_ref_states)
    assert_only(validate(artifacts, repo_ctx=repo_ctx2, trusted=TRUSTED),
                "SV-041")


# ---------------------------------------------------------------------------
# SV-042 — trusted constants + observed-ref ancestry continuity (residual #9)
# ---------------------------------------------------------------------------

def test_sv042_negative_self_nominated_trust_root(cand):
    cfg = copy.deepcopy(cand["config"])
    cfg["source"]["repo_identity"] = "github.com/evil/fork"
    assert_only(validate({"config": cfg}, trusted=TRUSTED), "SV-042")


def test_sv042_negative_ref_rewind(git_universe):
    artifacts, repo_ctx, commits = git_universe
    from cre.f1.freeze.semantic_validator_v1 import GitContext
    rewound = GitContext(repo_ctx.repo_dir, repo_ctx.canonical_ref,
                         repo_ctx.artifact_commits,
                         [commits["attestation"], commits["config"]])
    assert_only(validate(artifacts, repo_ctx=rewound, trusted=TRUSTED),
                "SV-042")


# ---------------------------------------------------------------------------
# SV-043 — candidate protocol binding + prohibited cases
# ---------------------------------------------------------------------------

def test_sv043_negative_missing_known_prohibited_case(cand):
    trusted = TrustedConstants(
        schema_sha256=schema_gate.PINNED_SCHEMA_SHA256,
        known_prohibited_citing_pmcids=("PMC901", "PMC999"))
    hits = assert_only(validate(cand, trusted=trusted), "SV-043")
    assert "PMC999" in hits[0].message


def test_sv043_negative_protocol_committed_after_config(git_universe):
    artifacts, repo_ctx, commits = git_universe
    from cre.f1.freeze.semantic_validator_v1 import GitContext
    ctx = dict(repo_ctx.artifact_commits)
    ctx["candidate_protocol"] = commits["promotion"]  # after config's commit
    late = GitContext(repo_ctx.repo_dir, repo_ctx.canonical_ref, ctx,
                      repo_ctx.observed_ref_states)
    assert_only(validate(artifacts, repo_ctx=late, trusted=TRUSTED), "SV-043")


# ---------------------------------------------------------------------------
# SV-044 — exposure binding
# ---------------------------------------------------------------------------

def test_sv044_negative_exposure_row_not_in_chain(cand):
    cand["exposure_plan"]["exposed"][0]["review_record_sha256"] = "d" * 64
    cand["promotion"]["payload"]["exposure_plan_sha256"] = \
        canon_sha256(cand["exposure_plan"])
    fx.seal_envelope(cand["promotion"])
    assert_only(validate(cand, trusted=TRUSTED), "SV-044")


def test_sv044_negative_exposure_commit_not_before_promotion(git_universe):
    artifacts, repo_ctx, commits = git_universe
    artifacts = copy.deepcopy(artifacts)
    artifacts["promotion"] = {"artifact_type": "promotion",
                              "schema_version": "v14",
                              "payload": {"config_hash": "0" * 64,
                                          "run_hash": "0" * 64,
                                          "candidate_protocol_sha256": "0" * 64,
                                          "exposure_plan_sha256": "0" * 64,
                                          "post_exposure_exclusion_checkpoint_sha256": "0" * 64,
                                          "recorded_by": "ZD",
                                          "recorded_at": fx.TS},
                              "payload_sha256": "0" * 64}
    fx.seal_envelope(artifacts["promotion"])
    from cre.f1.freeze.semantic_validator_v1 import GitContext
    ctx = dict(repo_ctx.artifact_commits)
    ctx["exposure_plan"] = commits["attestation"]  # after promotion's commit
    swapped = GitContext(repo_ctx.repo_dir, repo_ctx.canonical_ref, ctx,
                         repo_ctx.observed_ref_states)
    assert_only(validate(artifacts, repo_ctx=swapped, trusted=TRUSTED),
                "SV-044")


# ---------------------------------------------------------------------------
# SV-045 — execution-mode matrix
# ---------------------------------------------------------------------------

def test_sv045_negative_candidate_with_promotion_at_start(cand):
    batch = copy.deepcopy(cand["batch"])
    batch["promotion_payload_sha256_at_start"] = "a" * 64
    assert_only(validate({"batch": batch}, trusted=TRUSTED), "SV-045")


def test_sv045_negative_development_batch(cand):
    batch = copy.deepcopy(cand["batch"])
    batch["execution_mode"] = "development"
    violations = validate({"batch": batch}, trusted=TRUSTED)
    assert_fires(violations, "SV-045")


# ---------------------------------------------------------------------------
# SV-050 — reportability derivation audit
# ---------------------------------------------------------------------------

def test_sv050_negative_composite_without_discriminator(rel):
    rel["reportability_claims"]["composite_result_reportable"] = True
    assert_only(validate(rel, trusted=TRUSTED), "SV-050")


def test_sv050_negative_finder_claimed_despite_revocation(rel):
    revocation = {"artifact_type": "revocation", "schema_version": "v14",
                  "payload": {"target_config_hash": rel["config_hash"],
                              "target_promotion_payload_sha256":
                                  canon_sha256(rel["promotion"]["payload"]),
                              "reason": "defect found",
                              "recorded_by": "ZD", "recorded_at": fx.TS},
                  "payload_sha256": "0" * 64}
    fx.seal_envelope(revocation)
    rel["revocations"] = [revocation]
    assert_only(validate(rel, trusted=TRUSTED), "SV-050")


# ---------------------------------------------------------------------------
# SV-060 — citation_id canonical percent encoding
# ---------------------------------------------------------------------------

def _minimal_selection(citation_id, ref_id):
    item = fx.build_selection_item("PMC100001", ref_id, "11111", "<x/>")
    item["citation_id"] = citation_id
    return {"artifact_type": "selection_artifact", "schema_version": "v14",
            "sample_purpose": "calibration", "selection_rule": "r",
            "min_size": 1, "coverage_targets": {}, "items": [item],
            "recorded_by": "ZD"}


def test_sv060_positive_canonical_encoding():
    sel = _minimal_selection("PMC100001:ref%2Fa", "ref/a")
    assert not fired(validate({"selection_artifact": sel}, trusted=TRUSTED),
                     "SV-060")


def test_sv060_negative_wrong_hex_case():
    sel = _minimal_selection("PMC100001:ref%2fa", "ref/a")
    assert_only(validate({"selection_artifact": sel}, trusted=TRUSTED),
                "SV-060")


def test_sv060_negative_item_key_mismatch(cand):
    rec = cand["review_records"][0]
    rec["ref_id"] = "other-ref"
    fx.reseal_candidate_universe(cand)
    violations = validate(cand, trusted=TRUSTED)
    assert_fires(violations, "SV-060")


# ---------------------------------------------------------------------------
# SV-061 — occurrence identity preimage (schema member names exactly)
# ---------------------------------------------------------------------------

def test_sv061_positive_schema_member_names(cand):
    item = cand["selection_artifact"]["items"][0]
    assert item["occurrence_identity"] == canon_sha256({
        "occurrence_identity_version": item["occurrence_identity_version"],
        "citing_pmcid": item["citing_pmcid"],
        "normalized_ref_key_type": item["normalized_ref_key_type"],
        "normalized_ref_key": item["normalized_ref_key"]})
    assert not fired(validate(cand, trusted=TRUSTED), "SV-061")


def test_sv061_negative_wrong_member_names(cand):
    # Acceptance row: computed with the superseded occ_id_v1/key_type member
    # names — under canon_v1 different keys are a different hash.
    item = cand["selection_artifact"]["items"][0]
    item["occurrence_identity"] = canon_sha256({
        "version": item["occurrence_identity_version"],
        "citing_pmcid": item["citing_pmcid"],
        "key_type": item["normalized_ref_key_type"],
        "key": item["normalized_ref_key"]})
    fx.reseal_candidate_universe(cand)
    violations = validate(cand, trusted=TRUSTED)
    hits = assert_fires(violations, "SV-061")
    assert all(v.rule_id == "SV-061" for v in violations)


# ---------------------------------------------------------------------------
# SV-070 — stimulus leakage scan
# ---------------------------------------------------------------------------

def test_sv070_negative_route_nested_in_worksheet(cand):
    cid = cand["review_records"][0]["item_key"]
    stim = copy.deepcopy(cand["stimulus_objects"][cid])
    stim["worksheet_schema"]["questions"][0]["route"] = "F3"
    assert_only(validate({"stimulus_objects": {cid: stim}}, trusted=TRUSTED),
                "SV-070")


# ---------------------------------------------------------------------------
# SV-071 — stimulus completeness
# ---------------------------------------------------------------------------

def test_sv071_negative_codebook_hash(cand):
    cid = cand["review_records"][0]["item_key"]
    stim = copy.deepcopy(cand["stimulus_objects"][cid])
    stim["codebook_sha256"] = "b" * 64
    assert_only(validate({"stimulus_objects": {cid: stim}}, trusted=TRUSTED),
                "SV-071")


# ---------------------------------------------------------------------------
# SV-072 — stimulus hash recompute (residual #7)
# ---------------------------------------------------------------------------

def test_sv072_negative_unrecomputed_stimulus_hash(cand):
    cand["review_records"][0]["stimulus_sha256"] = "c" * 64
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-072")


def test_sv072_negative_codebook_not_configs(cand):
    cid = cand["review_records"][0]["item_key"]
    stim = cand["stimulus_objects"][cid]
    stim["codebook_content"] = "different codebook"
    stim["codebook_sha256"] = fx.sha256_utf8("different codebook")
    cand["review_records"][0]["stimulus_sha256"] = canon_sha256(stim)
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-072")


# ---------------------------------------------------------------------------
# SV-090 — header allowlist / credential denylist
# ---------------------------------------------------------------------------

def test_sv090_negative_credential_header_in_allowlisted():
    events = fx.build_wal_triplet(
        lcid="lc-hdr", headers={"authorization": "Bearer sk-x"})
    hits = assert_only(validate({"wal_events": events}, trusted=TRUSTED),
                       "SV-090")
    assert "credential" in hits[0].message


def test_sv090_negative_unlisted_header():
    events = fx.build_wal_triplet(lcid="lc-hdr2",
                                  headers={"x-internal-debug": "1"})
    assert_only(validate({"wal_events": events}, trusted=TRUSTED), "SV-090")


# ---------------------------------------------------------------------------
# SV-091 — endpoint host allowlist (out-of-band)
# ---------------------------------------------------------------------------

def test_sv091_negative_host_outside_allowlist(cand):
    cfg = copy.deepcopy(cand["config"])
    cfg["stages"]["coverage"]["endpoint"]["base_url"] = \
        "https://evil.example.com/v1/messages"
    hits = assert_only(validate({"config": cfg}, trusted=TRUSTED), "SV-091")
    assert "evil.example.com" in hits[0].message


# ---------------------------------------------------------------------------
# SV-100 — RFC 3339 semantics, I-JSON ints, CAS grammar
# ---------------------------------------------------------------------------

def test_sv100_negative_regex_passing_invalid_timestamp(cand):
    proto = copy.deepcopy(cand["candidate_protocol"])
    proto["recorded_at"] = "2026-13-40T99:99:99Z"
    assert_only(validate({"candidate_protocol": proto}, trusted=TRUSTED),
                "SV-100")


def test_sv100_negative_cas_ref_escapes_root(cand):
    rec = cand["review_records"][0]
    rec["evidence_snapshot_ref"] = "cas/../../etc/passwd"
    fx.reseal_candidate_universe(cand)
    violations = validate(cand, trusted=TRUSTED)
    assert_fires(violations, "SV-100")


def test_sv100_negative_unsafe_integer():
    batch_frag = {"artifact_type": "exclusion_checkpoint",
                  "schema_version": "v14", "tip_entry_sha256": None,
                  "entry_count": 2 ** 60,
                  "canonical_ref_commit": fx.OID}
    assert_only(validate({"exclusion_checkpoints": [batch_frag]},
                         trusted=TRUSTED), "SV-100")


# ---------------------------------------------------------------------------
# SV-101 — retry-policy equations
# ---------------------------------------------------------------------------

def test_sv101_negative_total_below_read(cand):
    cfg = copy.deepcopy(cand["config"])
    cfg["stages"]["coverage"]["retry"]["total_timeout_seconds"] = "30"
    hits = assert_only(validate({"config": cfg}, trusted=TRUSTED), "SV-101")
    assert "total_timeout" in hits[0].path


def test_sv101_negative_backoff_cap_below_base(cand):
    cfg = copy.deepcopy(cand["config"])
    cfg["stages"]["claim_extract"]["retry"]["backoff_cap_seconds"] = "0"
    assert_only(validate({"config": cfg}, trusted=TRUSTED), "SV-101")


# ---------------------------------------------------------------------------
# SV-110 — bootstrap runtime gate (artifact-side + subprocess fixtures)
# ---------------------------------------------------------------------------

def test_sv110_negative_role_coverage_gap(cand):
    mm = copy.deepcopy(cand["module_manifest"])
    mm["modules"] = [m for m in mm["modules"] if m["role"] != "runner"]
    hits = assert_only(validate({"module_manifest": mm}, trusted=TRUSTED),
                       "SV-110")
    assert "runner" in hits[0].message


def test_sv110_negative_path_escape(cand):
    mm = copy.deepcopy(cand["module_manifest"])
    mm["modules"][0]["repo_path"] = "../outside.py"
    assert_only(validate({"module_manifest": mm}, trusted=TRUSTED), "SV-110")


# --- bootstrap subprocess fixtures (the runtime-gate evidence) -------------

REPO_HANDOFF = pathlib.Path(__file__).resolve().parents[2]
BOOTSTRAP_SRC = REPO_HANDOFF / "cre" / "f1" / "freeze" / "bootstrap.py"


def _make_boot_tree(tmp_path):
    """Temp pinned tree: the real bootstrap + one dummy module per role."""
    root = tmp_path / "tree"
    boot_rel = "cre/f1/freeze/bootstrap.py"
    boot_dst = root / boot_rel
    boot_dst.parent.mkdir(parents=True)
    shutil.copyfile(BOOTSTRAP_SRC, boot_dst)
    modules = [{"role": "bootstrap", "repo_path": boot_rel,
                "blob_oid": fx.OID,
                "content_sha256":
                    hashlib.sha256(boot_dst.read_bytes()).hexdigest()}]
    for role in bootstrap.TRUST_BOUNDARY_ROLES:
        if role == "bootstrap":
            continue
        rel = f"mod_{role}.py"
        p = root / rel
        p.write_text(f"ROLE = {role!r}\n")
        modules.append({"role": role, "repo_path": rel, "blob_oid": fx.OID,
                        "content_sha256":
                            hashlib.sha256(p.read_bytes()).hexdigest()})
    manifest = {"artifact_type": "module_manifest", "schema_version": "v14",
                "modules": modules}
    mpath = tmp_path / "module_manifest.json"
    mpath.write_text(json.dumps(manifest))
    return root, boot_dst, mpath, manifest


def _run_child(boot_path, manifest_path, repo_root):
    return subprocess.run([PYTHON, "-I", str(boot_path), "--manifest",
                           str(manifest_path), "--repo-root", str(repo_root)],
                          capture_output=True, text=True)


def test_bootstrap_subprocess_fresh_interpreter_ok(tmp_path):
    root, boot, manifest, _ = _make_boot_tree(tmp_path)
    r = _run_child(boot, manifest, root)
    assert r.returncode == 0, r.stderr
    assert "BOOTSTRAP_OK" in r.stdout


def test_bootstrap_subprocess_byte_mismatch_aborts_before_import(tmp_path):
    root, boot, manifest, _ = _make_boot_tree(tmp_path)
    (root / "mod_validator.py").write_text("ROLE = 'validator'  # TAMPERED\n")
    r = _run_child(boot, manifest, root)
    assert r.returncode == bootstrap.E_BOOTSTRAP_EXIT
    assert "aborting before import" in r.stderr


def test_bootstrap_subprocess_role_gap_aborts(tmp_path):
    root, boot, mpath, manifest = _make_boot_tree(tmp_path)
    manifest["modules"] = [m for m in manifest["modules"]
                           if m["role"] != "runner"]
    mpath.write_text(json.dumps(manifest))
    r = _run_child(boot, mpath, root)
    assert r.returncode == bootstrap.E_BOOTSTRAP_EXIT
    assert "role coverage" in r.stderr


def test_bootstrap_subprocess_preimported_module_fails_closed(tmp_path):
    # SV-110: a trust-boundary module already in sys.modules before byte
    # verification must abort the run (the stale-sys.modules bug).
    root, boot, manifest, _ = _make_boot_tree(tmp_path)
    script = (
        f"import sys, runpy\n"
        f"sys.path.insert(0, {str(root)!r})\n"
        f"import mod_strict_loader\n"
        f"sys.argv = [{str(boot)!r}, '--manifest', {str(manifest)!r}, "
        f"'--repo-root', {str(root)!r}]\n"
        f"runpy.run_path({str(boot)!r}, run_name='__main__')\n")
    r = subprocess.run([PYTHON, "-c", script], capture_output=True, text=True)
    assert r.returncode == bootstrap.E_BOOTSTRAP_EXIT
    assert "fresh interpreter" in r.stderr or "sys.modules" in r.stderr


def test_bootstrap_parent_byte_verification(tmp_path):
    root, boot, mpath, manifest = _make_boot_tree(tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location("boot_copy", boot)
    boot_copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot_copy)
    assert boot_copy.parent_verify_bootstrap(manifest, root) == \
        "cre/f1/freeze/bootstrap.py"
    tampered = copy.deepcopy(manifest)
    for m in tampered["modules"]:
        if m["role"] == "bootstrap":
            m["content_sha256"] = "0" * 64
    with pytest.raises(boot_copy.BootstrapError):
        boot_copy.parent_verify_bootstrap(tampered, root)


def test_bootstrap_spawn_verified_end_to_end(tmp_path):
    root, boot, mpath, _ = _make_boot_tree(tmp_path)
    import importlib.util
    spec = importlib.util.spec_from_file_location("boot_copy2", boot)
    boot_copy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boot_copy)
    r = boot_copy.spawn_verified(PYTHON, mpath, root)
    assert r.returncode == 0, r.stderr
    assert "BOOTSTRAP_OK" in r.stdout


def test_bootstrap_manifest_rejects_floats_and_dupes(tmp_path):
    p = tmp_path / "m.json"
    p.write_text('{"artifact_type":"module_manifest","modules":[{"a":1.0}]}')
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.load_manifest(p)


# ---------------------------------------------------------------------------
# pctencode helper sanity (SV-060 substrate)
# ---------------------------------------------------------------------------

def test_pctencode_canonical_uppercase():
    assert pctencode("ref/a b") == "ref%2Fa%20b"
    assert pctencode("ref.~-_") == "ref.~-_"


# ---------------------------------------------------------------------------
# audit-round additions (2026-07-25): fixtures for the adjudicated findings
# ---------------------------------------------------------------------------

def test_strict_loader_rejects_escaped_lone_surrogate():
    with pytest.raises(StrictLoadError) as e:
        load_strict(b'{"s":"\\ud800"}')
    assert e.value.code == "E_ENCODING"


def test_sv011_positive_indeterminate_extract_quarantined():
    # Crash policy (§6): an indeterminate extraction quarantines the item —
    # it never lands in the extraction-failure -> held tier.
    from cre.f1.freeze.semantic_validator_v1 import derive_disposition
    rec = {"citing_pmcid": "PMC1",
           "extract": {"call_made": True, "status": "indeterminate"},
           "coverage": []}
    assert derive_disposition(rec, set()) == ("quarantined", True)


def test_sv022_positive_presend_crash_retry_is_legal():
    # A dangling 'prepared' never crossed the send boundary: retrying it is
    # spec-legal ("retry only when NOT sent_boundary_crossed").
    triplet = fx.build_wal_triplet(lcid="lc-pre")
    dangling_prepared = copy.deepcopy(triplet[0])
    retry = [copy.deepcopy(e) for e in triplet]
    for i, ev in enumerate(retry):
        ev["attempt_id"] = "a-lc-pre-1"
        ev["attempt_ordinal"] = 1
        ev["wal_seq"] = 1 + i
    chain = fx.seal_wal_events([dangling_prepared] + retry)
    assert not fired(validate({"wal_events": chain}, trusted=TRUSTED),
                     "SV-022")


def test_sv023_negative_attempt_without_prepared_event():
    triplet = fx.build_wal_triplet(lcid="lc-nopre")
    chain = [copy.deepcopy(triplet[1]), copy.deepcopy(triplet[2])]
    for i, ev in enumerate(chain):
        ev["wal_seq"] = i
        ev["attempt_event_seq"] = i
    fx.seal_wal_events(chain)
    hits = assert_only(validate({"wal_events": chain}, trusted=TRUSTED),
                       "SV-023")
    assert "unrecomputable" in hits[0].message


def test_sv025_negative_stimulus_snapshot_not_the_records(cand):
    # A DIFFERENT (internally valid) snapshot than the record's pinned one
    # must fail: never trust the stimulus's self-declared snapshot hash.
    cid = cand["review_records"][0]["item_key"]
    other = b"A different but perfectly valid abstract snapshot."
    other_sha = hashlib.sha256(other).hexdigest()
    cand["evidence_snapshots"][other_sha] = other
    stim = cand["stimulus_objects"][cid]
    stim["evidence"]["evidence_snapshot_sha256"] = other_sha
    stim["evidence"]["text"] = "perfectly valid abstract"
    cand["review_records"][0]["stimulus_sha256"] = canon_sha256(stim)
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-025")


def test_sv026_negative_model_mismatch_terminal_binding(cand):
    rec = cand["review_records"][0]
    ok = rec["extract"]
    rec["extract"] = {"call_made": True, "status": "model_mismatch",
                      "logical_call_id": ok["logical_call_id"],
                      "request_bytes_sha256": ok["request_bytes_sha256"],
                      "idempotency_key": ok["idempotency_key"],
                      "retry_wal_event_shas": ok["retry_wal_event_shas"],
                      "provider_model_id": "some-other-model"}
    rec["coverage"] = []
    rec["finder_disposition"] = "held"
    fx.reseal_candidate_universe(cand)
    hits = assert_only(validate(cand, trusted=TRUSTED), "SV-026")
    assert any("provider_model_id" in v.path for v in hits)


def test_sv030_negative_fabricated_row_content(rel):
    # Valid pointer, fabricated content: the row must equal the record.
    rel["annotation_release_manifest"]["inventory"][0]["stimulus_sha256"] = \
        "a" * 64
    att = rel["release_attestation"]
    att["annotation_release_manifest_sha256"] = \
        canon_sha256(rel["annotation_release_manifest"])
    fx.seal_self_hash(att, "attestation_sha256")
    assert_only(validate(rel, trusted=TRUSTED), "SV-030")


def test_sv034_negative_forward_binding_into_record(cand):
    # Residual #4: candidate rows bind FORWARD into the review record.
    cand["review_records"][0]["resolved_work_id"] = "pmid:99999"
    fx.reseal_candidate_universe(cand)
    assert_only(validate(cand, trusted=TRUSTED), "SV-034")


def test_sv041_negative_checkpoint_parent_not_validated_state(git_universe):
    # Acceptance row: the CHECKPOINT variant of two-phase anchoring.
    artifacts, repo_ctx, commits = git_universe
    artifacts = copy.deepcopy(artifacts)
    artifacts["exclusion_checkpoints"][0]["canonical_ref_commit"] = \
        "sha1:" + commits["base"]
    assert_only(validate(artifacts, repo_ctx=repo_ctx, trusted=TRUSTED),
                "SV-041")


def test_sv002_negative_internally_consistent_but_not_frozen(cand):
    # Residual #1: internal consistency alone is not a freeze — a package
    # whose embedded text and digest agree but differ from the frozen
    # acceptance constants must fail.
    pkg = cand["prompt_packages"][0]
    pkg["template_utf8"] = "A perfectly self-consistent replacement prompt."
    pkg["template_utf8_sha256"] = fx.sha256_utf8(pkg["template_utf8"])
    fx.seal_self_hash(pkg, "package_sha256")
    cand["config"]["prompt_packages"]["claim_extract"]["package_sha256"] = \
        pkg["package_sha256"]
    fx.seal_config(cand)
    fx.reseal_candidate_universe(cand)
    hits = assert_only(validate(cand, trusted=TRUSTED), "SV-002")
    assert any("frozen acceptance constant" in v.message for v in hits)


def test_sv044_negative_duplicate_exposure_citation_id(cand):
    rec1 = cand["review_records"][1]
    cand["exposure_plan"]["exposed"][1] = {
        "citation_id": cand["exposure_plan"]["exposed"][0]["citation_id"],
        "review_record_sha256": rec1["record_sha256"]}
    cand["promotion"]["payload"]["exposure_plan_sha256"] = \
        canon_sha256(cand["exposure_plan"])
    fx.seal_envelope(cand["promotion"])
    violations = validate(cand, trusted=TRUSTED)
    hits = assert_fires(violations, "SV-044")
    assert any("unique" in v.message for v in hits)


def test_sv060_negative_exposure_plan_row_id():
    plan = {"artifact_type": "exposure_plan", "schema_version": "v14",
            "run_hash": "0" * 64,
            "exposed": [{"citation_id": "PMC1:ref%2fx",
                         "review_record_sha256": "0" * 64}],
            "recorded_by": "ZD", "recorded_at": fx.TS}
    assert_only(validate({"exposure_plan": plan}, trusted=TRUSTED), "SV-060")


def test_sv050_positive_development_mode_never_reportable(rel):
    from cre.f1.freeze.semantic_validator_v1 import derive_reportability
    rel["batch"]["execution_mode"] = "development"
    derived = derive_reportability(rel, TRUSTED)
    assert derived["finder_result_reportable"] is False
    assert derived["composite_result_reportable"] is False


def test_sv100_positive_leap_second_accepted():
    proto = fx.build_candidate_protocol()
    proto["recorded_at"] = "2016-12-31T23:59:60Z"
    assert not fired(validate({"candidate_protocol": proto},
                              trusted=TRUSTED), "SV-100")


def test_validate_returns_violations_never_raises_on_floats():
    # A float smuggled into a promotion payload must surface as a violation
    # (fail closed), not as an unhandled CanonV1Error from validate().
    promo = {"artifact_type": "promotion", "schema_version": "v14",
             "payload": {"config_hash": "0" * 64, "run_hash": "0" * 64,
                         "candidate_protocol_sha256": "0" * 64,
                         "exposure_plan_sha256": "0" * 64,
                         "post_exposure_exclusion_checkpoint_sha256": "0" * 64,
                         "temperature": 1.0,
                         "recorded_by": "ZD", "recorded_at": fx.TS},
             "payload_sha256": "0" * 64}
    claims = {"finder_result_reportable": False,
              "discriminator_result_reportable": False,
              "composite_result_reportable": False}
    violations = validate({"promotion": promo,
                           "reportability_claims": claims}, trusted=TRUSTED)
    assert violations, "float payload must produce violations"
    assert all(isinstance(v.message, str) for v in violations)


def test_bootstrap_symlink_module_rejects(tmp_path):
    root, boot, mpath, manifest = _make_boot_tree(tmp_path)
    target = root / "mod_runner.py"
    real_bytes = target.read_bytes()
    target.unlink()
    (root / "elsewhere.py").write_bytes(real_bytes)
    target.symlink_to(root / "elsewhere.py")
    r = _run_child(boot, mpath, root)
    assert r.returncode == bootstrap.E_BOOTSTRAP_EXIT
    assert "symlink" in r.stderr


def test_bootstrap_stray_drifted_copy_fails_closed(tmp_path):
    # A drifted launcher copy invoked directly (bypassing the parent's
    # byte verification) must refuse itself.
    root, boot, mpath, _ = _make_boot_tree(tmp_path)
    stray = tmp_path / "stray" / "bootstrap.py"
    stray.parent.mkdir()
    stray.write_bytes(boot.read_bytes() + b"\n# drifted\n")
    r = subprocess.run([PYTHON, "-I", str(stray), "--manifest", str(mpath),
                        "--repo-root", str(root)],
                       capture_output=True, text=True)
    assert r.returncode == bootstrap.E_BOOTSTRAP_EXIT
    assert "stray copy" in r.stderr or "stale" in r.stderr


# ---------------------------------------------------------------------------
# ZD integrity round 2 (2026-07-25): validate() input hardening + report
# canonical-digest reproducibility
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hostile", [None, 7, "", b"", [], float("inf"),
                                     [{"artifact_type": "config"}], "batch",
                                     True, 3.14, (), 0])
def test_validate_returns_violation_not_raise_for_hostile_top_level(hostile):
    out = validate(hostile, trusted=TRUSTED)
    assert isinstance(out, list) and out
    assert out[0].rule_id == "SV-000" and out[0].fail_code == "E_INPUT"


@pytest.mark.parametrize("universe", [
    {"batch": 7}, {"config": []}, {"review_records": 3},
    {"review_records": [None]}, {"wal_events": [None]},
    {"stimulus_objects": ["x"]}, {"promotion": "promo"},
    {"selection_artifact": 0}, {"exclusion_ledger": [7]},
    {"module_manifest": {"modules": [None]}}])
def test_validate_returns_list_for_malformed_artifact_values(universe):
    out = validate(universe, trusted=TRUSTED)
    assert isinstance(out, list)
    from cre.f1.freeze.semantic_validator_v1 import RULES as _rules
    for v in out:
        assert v.rule_id in _rules or v.rule_id == "SV-000"
        assert isinstance(v.message, str)


GEN = REPO_HANDOFF / "cre" / "f1" / "freeze" / "gen_conformance.py"
_IN_GENERATOR = os.environ.get("CRE_GEN_CONFORMANCE_RUNNING") == "1"


def _run_generator(out_path):
    env = {**os.environ, "PYTHONPATH": str(REPO_HANDOFF)}
    r = subprocess.run([PYTHON, str(GEN), "--out", str(out_path)],
                       capture_output=True, text=True,
                       cwd=str(REPO_HANDOFF), env=env)
    assert r.returncode == 0, r.stderr[-2000:]
    text = out_path.read_text()
    head, sep, body = text.partition("\n--- canonical body ---\n")
    m = re.search(r"canonical_body_sha256: ([0-9a-f]{64})", head)
    assert sep and m, "report lacks the canonical-body marker/digest"
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == m.group(1)
    return m.group(1), body


@pytest.mark.skipif(_IN_GENERATOR, reason="generator must not recurse")
def test_report_canonical_digest_reproducible_across_runs(tmp_path):
    # Two consecutive generator runs on identical repo bytes must record the
    # identical canonical digest (wall clock excluded from the hashed region).
    d1, b1 = _run_generator(tmp_path / "run1.txt")
    d2, b2 = _run_generator(tmp_path / "run2.txt")
    assert d1 == d2
    assert b1 == b2


@pytest.mark.skipif(_IN_GENERATOR, reason="generator must not recurse")
def test_report_verify_mode_passes_on_committed_report():
    env = {**os.environ, "PYTHONPATH": str(REPO_HANDOFF)}
    r = subprocess.run([PYTHON, str(GEN), "--verify"], capture_output=True,
                       text=True, cwd=str(REPO_HANDOFF), env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "VERIFY OK" in r.stdout


def test_bootstrap_stale_basename_other_tree_fails_closed(tmp_path):
    # SV-110: a same-named trust-boundary module loaded from a DIFFERENT
    # tree (another checkout, site-packages) is the stale-copy bug itself.
    import types
    root, boot, mpath, manifest = _make_boot_tree(tmp_path)
    other_tree = tmp_path / "other_checkout"
    other_tree.mkdir()
    stale_file = other_tree / "mod_validator.py"
    stale_file.write_text("ROLE = 'validator'  # stale checkout\n")
    fake = types.ModuleType("mod_validator")
    fake.__file__ = str(stale_file)
    sys.modules["mod_validator_stale_test"] = fake
    try:
        with pytest.raises(bootstrap.BootstrapError) as e:
            bootstrap.check_fresh_interpreter(manifest, root)
        assert "outside the pinned tree" in str(e.value)
    finally:
        del sys.modules["mod_validator_stale_test"]
