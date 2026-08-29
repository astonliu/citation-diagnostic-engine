#!/usr/bin/env python3
"""mint_v1 — fail-closed minting tool for the F3-F7 finder freeze artifacts.

Derives every MECHANICAL field of a prompt package and a CONFIG from the
repository and the running environment, reads every JUDGMENT field from one
declarative input file (``MINT_INPUTS.json``), and FAILS CLOSED naming the
missing canonical input by number when that file is absent or incomplete.
No judgment field is ever defaulted; no partial artifact is ever written.

    PYTHONPATH=. ../.venv_cre/bin/python cre/f1/freeze/mint_v1.py --prompt-packages
    PYTHONPATH=. ../.venv_cre/bin/python cre/f1/freeze/mint_v1.py --config

Prompt packages depend on nothing from ZD: the template text comes from
cre/f1/band_prompts.py (read-only input; a digest mismatch is a finding to
report, never something to fix by editing the prompt), the blob OID from git,
and every hash is recomputed here and asserted against the frozen acceptance
constants carried by semantic_validator_v1 — no literal digest appears in
this file. The CONFIG additionally needs the canonical ZD inputs plus the
unnumbered per-stage decisions catalogued in MINT_DECISIONS_PENDING_ZD.md;
until MINT_INPUTS.json supplies them, ``--config`` exits non-zero, writes
nothing, and prints every missing field grouped by canonical input number.

MINT_INPUTS.json shape (judgment fields; strict JSON — duplicate keys and
float tokens are rejected pre-parse; real quantities travel as decimal
strings per canon_v1):

    {
      "stages": {
        "claim_extract": {
          "model_snapshot": "...",                       // input #1
          "params": { six state-wrapped params },        // unnumbered
          "endpoint": { ... },                           // unnumbered
          "system_message": {"state": "omitted"|...},    // unnumbered
          "tool_schema": null | "...",                   // unnumbered
          "retry": { ... },                              // unnumbered
          "response_parser_version": "...",              // unnumbered
          "response_schema_path": "..."                  // optional (residual #5)
        },
        "coverage": { same keys, plus
          "evidence_policy": { retrieval, pmid_version_policy,
                               normalization, missing_evidence_sentinel }  // input #3
        }
      },
      "codebook_content": "...",                         // input #2
      "candidate_protocol": { full artifact },           // carries #4 + #5
      "dependency_lockfile_path": "...",                 // input #6
      "module_manifest": { full artifact },              // unnumbered
      "source_commit_ref": "HEAD"                        // optional, mechanical
    }

Mechanical derivations (never supplied): prompt package bindings from the
freshly minted packages; candidate_protocol_sha256 / module_manifest_sha256 /
codebook_sha256 via canon_v1; source.repo_identity + source.canonical_ref
asserted equal to the out-of-band bootstrap trust root (a CONFIG cannot
nominate its own trust root); source_commit_oid resolved from git with
source_tree_oid derived FROM that commit (never independently); the
runtime_profile from the interpreter, platform, installed-distribution
inventory, transport libraries (anthropic + httpx), and the canonicalizer's
own bytes; tool_schema_sha256 from the supplied tool_schema;
response_schema_sha256 null (with a residual-#5 notice) unless a committed
response-schema file path is supplied.
"""
import argparse
import hashlib
import pathlib
import platform
import subprocess
import sys

from cde.claims import band_prompts
from cde.freeze import bootstrap, schema_gate
from cde.freeze.canon_v1 import canon_sha256, canon_v1
from cde.freeze.semantic_validator_v1 import (FROZEN_SOURCE_BLOB_OID,
                                                 FROZEN_TEMPLATE_UTF8_SHA256)
from cde.freeze.strict_loader import StrictLoadError, load_strict

SCHEMA_VERSION = "v14"
PACKAGE_VERSION = 4  # structured-renderer request contract (not .replace())
TEMPLATE_TEXT_VERSIONS = {"claim_extract": 3, "coverage": 2}
TEMPLATE_ATTRS = {"claim_extract": "CLAIM_EXTRACT_PROMPT",
                  "coverage": "COVERAGE_PROMPT"}

FREEZE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_INPUTS_PATH = FREEZE_DIR / "MINT_INPUTS.json"
DEFAULT_CONFIG_OUT_DIR = FREEZE_DIR / "_mint_out"  # gitignored, never committed
DECISIONS_DOC = "cde/freeze/MINT_DECISIONS_PENDING_ZD.md"

E_MINT_EXIT = 2

_ABSENT = object()


class MintError(Exception):
    """Fail-closed minting violation; nothing was written."""


class MintInputsError(MintError):
    """Judgment inputs missing; carries the grouped CANNOT-MINT report."""

    def __init__(self, report, numbered, unnumbered):
        super().__init__(report)
        self.report = report
        self.numbered = numbered
        self.unnumbered = unnumbered


def _sha256_utf8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# prompt packages (mechanical throughout — nothing here is ZD's to decide)
# ---------------------------------------------------------------------------

def derive_source_blob_oid():
    """Committed blob OID of band_prompts.py at HEAD, with the hash-name
    prefix convention of semantic_validator_v1.FROZEN_SOURCE_BLOB_OID.
    A blob OID is stored, never equated to a content hash (§336)."""
    src = pathlib.Path(band_prompts.__file__).resolve()
    r = subprocess.run(["git", "-C", str(src.parent), "rev-parse",
                        f"HEAD:./{src.name}"], capture_output=True, text=True)
    if r.returncode != 0:
        raise MintError(f"E_MINT_GIT: cannot resolve the committed blob OID "
                        f"of {src}: {r.stderr.strip()}")
    bare = r.stdout.strip()
    return ("sha1:" if len(bare) == 40 else "sha256:") + bare


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


def mint_prompt_package(name):
    """Derive one prompt package; abort (computed vs expected printed in the
    exception) if band_prompts drifted from the frozen acceptance constants."""
    if name not in TEMPLATE_TEXT_VERSIONS:
        raise MintError(f"E_MINT_NAME: unknown prompt package {name!r}; "
                        f"expected one of {sorted(TEMPLATE_TEXT_VERSIONS)}")
    template = getattr(band_prompts, TEMPLATE_ATTRS[name])
    tpl_sha = _sha256_utf8(template)
    expected_tpl = FROZEN_TEMPLATE_UTF8_SHA256[name]
    if tpl_sha != expected_tpl:
        raise MintError(
            f"E_MINT_TEMPLATE: {name} template drifted —\n"
            f"  computed template_utf8_sha256 {tpl_sha}\n"
            f"  expected frozen constant      {expected_tpl}\n"
            f"band_prompts.py is read-only input; this mismatch is a finding "
            f"to report, never something to fix by editing the prompt.")
    blob_oid = derive_source_blob_oid()
    if blob_oid != FROZEN_SOURCE_BLOB_OID:
        raise MintError(
            f"E_MINT_BLOB_OID: band_prompts.py blob OID drifted —\n"
            f"  computed source_blob_oid {blob_oid}\n"
            f"  expected frozen constant {FROZEN_SOURCE_BLOB_OID}\n"
            f"(is HEAD the pinned freeze commit?)")
    rc = _render_contract(name)
    pkg = {
        "artifact_type": "prompt_package", "schema_version": SCHEMA_VERSION,
        "name": name,
        "template_text_version": TEMPLATE_TEXT_VERSIONS[name],
        "package_version": PACKAGE_VERSION,
        "template_utf8": template,
        "template_utf8_sha256": tpl_sha,
        "source_blob_oid": blob_oid,
        "render_contract": rc,
        "render_contract_sha256": canon_sha256(rc),
    }
    # Self-seal: canonical hash over the object without the seal field
    # (SV-001's declared preimage; same discipline as fixtures_v1).
    pkg["package_sha256"] = canon_sha256(pkg)
    return pkg


def prompt_package_path(name, out_dir=None):
    out_dir = FREEZE_DIR if out_dir is None else pathlib.Path(out_dir)
    return out_dir / f"PROMPT_{name}_pkg_v{PACKAGE_VERSION}.json"


def write_prompt_packages(out_dir=None):
    """Mint, schema-gate, then write BOTH packages as canonical bytes
    (stored-bytes hash == canon_sha256(object)). Nothing is written unless
    both pass the pinned schema gate."""
    minted = []
    for name in sorted(TEMPLATE_TEXT_VERSIONS):
        pkg = mint_prompt_package(name)
        errors = schema_gate.schema_errors(pkg)
        if errors:
            raise MintError(
                f"E_MINT_SCHEMA: minted prompt package {name!r} fails the "
                f"pinned schema gate; nothing written:\n  " +
                "\n  ".join(errors))
        minted.append((prompt_package_path(name, out_dir), pkg))
    for path, pkg in minted:
        path.write_bytes(canon_v1(pkg))
    return minted


# ---------------------------------------------------------------------------
# CONFIG — judgment fields from MINT_INPUTS.json, mechanical fields derived
# ---------------------------------------------------------------------------

STAGE_NAMES = ("claim_extract", "coverage")
PARAM_KEYS = ("temperature", "max_tokens", "top_p", "top_k",
              "stop_sequences", "seed")
EVIDENCE_POLICY_KEYS = ("retrieval", "pmid_version_policy", "normalization",
                        "missing_evidence_sentinel")
# Unnumbered per-stage judgment keys beyond input #1/#3 (see the decisions
# doc). tool_schema is key-presence checked: null is a LEGAL decided value.
STAGE_DECISION_KEYS = ("params", "endpoint", "system_message", "tool_schema",
                       "retry", "response_parser_version")
KNOWN_TOP_KEYS = frozenset({"stages", "codebook_content", "candidate_protocol",
                            "dependency_lockfile_path", "module_manifest",
                            "source_commit_ref"})
KNOWN_STAGE_KEYS = frozenset({"model_snapshot", "params", "endpoint",
                              "system_message", "tool_schema", "retry",
                              "response_parser_version",
                              "response_schema_path", "evidence_policy"})


def _get(mapping, key):
    if not isinstance(mapping, dict):
        return _ABSENT
    return mapping.get(key, _ABSENT)


def _nonempty_str(v):
    return isinstance(v, str) and bool(v)


def collect_missing(inputs):
    """(numbered, unnumbered): every judgment field absent from `inputs`.

    numbered   -> list of (number, title, [field lines]) for the canonical
                  six, only for inputs with at least one missing field.
    unnumbered -> list of field lines with no canonical number (the
                  MINT_DECISIONS_PENDING_ZD.md catalogue).
    Presence only — shape is enforced by the pinned schema at mint time.
    """
    stages = _get(inputs, "stages")
    proto = _get(inputs, "candidate_protocol")
    numbered = []

    fields = [f"stages.{s}.model_snapshot" for s in STAGE_NAMES
              if not _nonempty_str(_get(_get(stages, s), "model_snapshot"))]
    if fields:
        numbered.append(("#1", "model snapshot string(s)", fields))

    if not _nonempty_str(_get(inputs, "codebook_content")):
        numbered.append(("#2", "codebook content (content-hashed)",
                         ["codebook_sha256"]))

    policy = _get(_get(stages, "coverage"), "evidence_policy")
    fields = [f"stages.coverage.evidence_policy.{k}"
              for k in EVIDENCE_POLICY_KEYS
              if not _nonempty_str(_get(policy, k))]
    if fields:
        numbered.append(("#3", "evidence retrieval / snapshot policy",
                         fields))

    bound = "(binds via config.candidate_protocol_sha256)"
    if not _nonempty_str(_get(proto, "freeze_criterion")):
        numbered.append(("#4", "freeze criterion (promotion criterion — "
                         "recorded for provenance, never a CONFIG field)",
                         [f"candidate_protocol.freeze_criterion {bound}"]))

    cases = _get(proto, "prohibited_cases")
    if not (isinstance(cases, list) and cases):
        numbered.append(("#5", "the two prohibited citing PMCIDs",
                         [f"candidate_protocol.prohibited_cases {bound}"]))

    lock = _get(inputs, "dependency_lockfile_path")
    if not (_nonempty_str(lock) and pathlib.Path(lock).is_file()):
        note = ("" if lock is _ABSENT or not _nonempty_str(lock)
                else f" (declared lockfile {lock!r} not found)")
        numbered.append(("#6", "dependency lockfile (hash-pinned, repo root)",
                         [f"runtime_profile.dependency_lock_sha256{note}"]))

    unnumbered = []
    for s in STAGE_NAMES:
        st = _get(stages, s)
        for key in STAGE_DECISION_KEYS:
            if key == "params":
                params = _get(st, "params")
                unnumbered.extend(
                    f"stages.{s}.params.{p}" for p in PARAM_KEYS
                    if _get(params, p) is _ABSENT)
            elif key == "tool_schema":
                if _get(st, "tool_schema") is _ABSENT:
                    unnumbered.append(f"stages.{s}.tool_schema "
                                      f"(null is a legal decided value)")
            elif key == "response_parser_version":
                if not _nonempty_str(_get(st, key)):
                    unnumbered.append(f"stages.{s}.{key}")
            elif not isinstance(_get(st, key), dict):
                unnumbered.append(f"stages.{s}.{key}")
    if not isinstance(_get(inputs, "module_manifest"), dict):
        unnumbered.append("module_manifest "
                          "(artifact object -> module_manifest_sha256)")
    return numbered, unnumbered


def _cannot_mint_report(numbered, unnumbered):
    lines = [f"CANNOT MINT — {len(numbered)} canonical inputs and "
             f"{len(unnumbered)} unnumbered decisions outstanding."]
    for num, title, fields in numbered:
        lines.append(f"  input {num}  {title}")
        lines.extend(f"              -> {f}" for f in fields)
    for f in unnumbered:
        lines.append(f"  unnumbered  {f}")
    if unnumbered:
        lines.append(f"              (see {DECISIONS_DOC})")
    return "\n".join(lines)


def _reject_unknown_keys(inputs):
    unknown = sorted(set(inputs) - KNOWN_TOP_KEYS)
    if unknown:
        raise MintError(f"E_MINT_UNKNOWN_INPUT: unrecognized MINT_INPUTS "
                        f"keys {unknown}; a typo here would silently drop a "
                        f"decision, so this fails closed")
    stages = inputs.get("stages")
    if isinstance(stages, dict):
        unknown = sorted(set(stages) - set(STAGE_NAMES))
        if unknown:
            raise MintError(f"E_MINT_UNKNOWN_INPUT: unrecognized stages "
                            f"{unknown}")
        for s, st in stages.items():
            if isinstance(st, dict):
                unknown = sorted(set(st) - KNOWN_STAGE_KEYS)
                if unknown:
                    raise MintError(f"E_MINT_UNKNOWN_INPUT: unrecognized "
                                    f"keys {unknown} in stages.{s}")


def _gate_input_artifact(kind, artifact):
    errors = schema_gate.schema_errors(artifact)
    if errors:
        raise MintError(
            f"E_MINT_SCHEMA: supplied {kind} fails the pinned schema gate; "
            f"nothing minted:\n  " + "\n  ".join(errors))


def derive_source_oids(commit_ref="HEAD"):
    """(source_commit_oid, source_tree_oid): the commit resolved from git and
    THAT COMMIT's tree — never derived independently (SV-041, residual #10)."""
    def git(*args):
        r = subprocess.run(["git", "-C", str(FREEZE_DIR)] + list(args),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise MintError(f"E_MINT_GIT: git {' '.join(args)}: "
                            f"{r.stderr.strip()}")
        return r.stdout.strip()

    commit = git("rev-parse", f"{commit_ref}^{{commit}}")
    if git("cat-file", "-t", commit) != "commit":
        raise MintError(f"E_MINT_GIT: {commit} is not a COMMIT object")
    tree = git("rev-parse", f"{commit}^{{tree}}")
    prefix = ("sha1:" if len(commit) == 40 else "sha256:")
    return prefix + commit, prefix + tree


def derive_runtime_profile(dependency_lock_sha256):
    """Runtime profile derived from the running environment — recorded, never
    supplied. Contains no invocation path (that is BATCH's observed_runtime)."""
    import importlib.metadata as md
    inventory = []
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name:
            raise MintError("E_MINT_RUNTIME: an installed distribution has "
                            "no Name metadata; the inventory digest would "
                            "not be reproducible — failing closed")
        inventory.append({"name": name, "version": dist.version})
    inventory.sort(key=lambda d: (d["name"], d["version"]))
    versions = {}
    for dist in ("anthropic", "httpx"):
        try:
            versions[dist] = md.version(dist)
        except md.PackageNotFoundError:
            raise MintError(f"E_MINT_RUNTIME: transport distribution "
                            f"{dist!r} is not installed in this "
                            f"interpreter; install the pinned transport "
                            f"stack before minting a CONFIG")
    constraint = platform.system().lower()
    if not constraint:
        raise MintError("E_MINT_RUNTIME: platform.system() is empty; "
                        "platform_constraint must be non-empty")
    canon_src = (FREEZE_DIR / "canon_v1.py").read_bytes()
    return {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "platform_constraint": constraint,
        "dependency_lock_sha256": dependency_lock_sha256,
        "distribution_inventory_sha256": canon_sha256(inventory),
        "transport_library_version":
            f"anthropic {versions['anthropic']}; httpx {versions['httpx']}",
        # The canonicalizer IS the in-repo canon_v1; version it by its bytes.
        "jcs_library_version":
            f"canon_v1 sha256:{hashlib.sha256(canon_src).hexdigest()[:16]}",
    }


def _derive_stage(name, st_in):
    tool_schema = st_in["tool_schema"]
    schema_path = st_in.get("response_schema_path")
    if schema_path:
        p = pathlib.Path(schema_path)
        if not p.is_file():
            raise MintError(f"E_MINT_INPUT: stages.{name}."
                            f"response_schema_path {schema_path!r} does not "
                            f"name a committed response-schema file")
        response_schema_sha256 = hashlib.sha256(p.read_bytes()).hexdigest()
    else:
        response_schema_sha256 = None  # legal declared value (residual #5)
    stage = {
        "model_snapshot": st_in["model_snapshot"],
        "params": st_in["params"],
        "system_message": st_in["system_message"],
        "endpoint": st_in["endpoint"],
        "retry": st_in["retry"],
        "response_parser_version": st_in["response_parser_version"],
        "tool_schema": tool_schema,
        "tool_schema_sha256": (None if tool_schema is None
                               else _sha256_utf8(tool_schema)),
        "response_schema_sha256": response_schema_sha256,
    }
    if name == "claim_extract":
        stage["evidence_scope"] = "citing_sentence_only"
    else:
        stage["evidence_scope"] = "abstract_snapshot"
        stage["evidence_reader"] = "content_addressed_only"
        stage["evidence_policy"] = st_in["evidence_policy"]
    return stage


def mint_config(inputs):
    """Build the CONFIG object. Raises MintInputsError (grouped, numbered
    report) when any judgment field is missing; MintError on any derivation
    or supplied-artifact failure. Returns the dict — the caller owns the
    schema gate and the write."""
    if isinstance(inputs, dict):
        _reject_unknown_keys(inputs)
    numbered, unnumbered = collect_missing(inputs)
    if numbered or unnumbered:
        raise MintInputsError(_cannot_mint_report(numbered, unnumbered),
                              numbered, unnumbered)

    proto = inputs["candidate_protocol"]
    _gate_input_artifact("candidate_protocol", proto)
    manifest = inputs["module_manifest"]
    _gate_input_artifact("module_manifest", manifest)
    roles = {m.get("role") for m in manifest.get("modules") or []}
    missing_roles = [r for r in bootstrap.TRUST_BOUNDARY_ROLES
                     if r not in roles]
    if missing_roles:
        raise MintError(f"E_MINT_MANIFEST: module manifest role coverage "
                        f"not exhaustive; missing {missing_roles} (SV-110 "
                        f"would fail — nothing minted)")

    packages = {name: mint_prompt_package(name)
                for name in sorted(TEMPLATE_TEXT_VERSIONS)}
    lock_path = pathlib.Path(inputs["dependency_lockfile_path"])
    commit_oid, tree_oid = derive_source_oids(
        inputs.get("source_commit_ref", "HEAD"))
    return {
        "artifact_type": "config", "schema_version": SCHEMA_VERSION,
        "canon": "canon_v1",
        "scope": "finder_frontend_extract_coverage",
        "candidate_protocol_sha256": canon_sha256(proto),
        # SV-002 recomputes the full package hash from committed package
        # content, so the render contract binds transitively through
        # package_sha256 — the CONFIG carries ONLY these two fields.
        "prompt_packages": {
            name: {"package_version": pkg["package_version"],
                   "package_sha256": pkg["package_sha256"]}
            for name, pkg in packages.items()},
        "stages": {name: _derive_stage(name, inputs["stages"][name])
                   for name in STAGE_NAMES},
        "failure_policy": {"continue_after_coverage_terminal_failure": True},
        "source": {
            # Out-of-band trust root (SV-042): asserted from bootstrap,
            # never read from inputs.
            "repo_identity": bootstrap.TRUSTED_REPO_IDENTITY,
            "canonical_ref": bootstrap.TRUSTED_CANONICAL_REF,
            "source_commit_oid": commit_oid,
            "source_tree_oid": tree_oid,
        },
        "module_manifest_sha256": canon_sha256(manifest),
        "runtime_profile": derive_runtime_profile(
            hashlib.sha256(lock_path.read_bytes()).hexdigest()),
        "codebook_sha256": _sha256_utf8(inputs["codebook_content"]),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli_prompt_packages(out_dir):
    for path, pkg in write_prompt_packages(out_dir):
        print(f"minted {path}")
        print(f"  package_sha256      {pkg['package_sha256']}")
        print(f"  template_utf8_sha256 {pkg['template_utf8_sha256']}")
    return 0


def _cli_config(inputs_path, out_dir):
    inputs_path = (pathlib.Path(inputs_path) if inputs_path
                   else DEFAULT_INPUTS_PATH)
    if not inputs_path.exists():
        numbered, unnumbered = collect_missing(None)
        print(f"no canonical input file at {inputs_path}")
        print(_cannot_mint_report(numbered, unnumbered))
        return E_MINT_EXIT
    inputs = load_strict(inputs_path)
    try:
        config = mint_config(inputs)
    except MintInputsError as e:
        print(e.report)
        return E_MINT_EXIT
    errors = schema_gate.schema_errors(config)
    if errors:
        print("CANNOT MINT — built CONFIG fails the pinned schema gate; "
              "nothing written:")
        for e in errors:
            print(f"  {e}")
        return E_MINT_EXIT
    for stage in STAGE_NAMES:
        if config["stages"][stage]["response_schema_sha256"] is None:
            print(f"NOTICE residual #5: stages.{stage}."
                  f"response_schema_sha256 = null — the committed per-stage "
                  f"response schema is pending ZD; SV-024 validates shape "
                  f"presence only meanwhile.")
    config_hash = canon_sha256(config)
    out = pathlib.Path(out_dir) if out_dir else DEFAULT_CONFIG_OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"CONFIG_{config_hash}.json"  # config_hash is the FILENAME digest
    path.write_bytes(canon_v1(config))
    print(f"minted {path}")
    print(f"  config_hash {config_hash}")
    print(f"  provenance (recorded, not a CONFIG field): freeze_criterion = "
          f"{inputs['candidate_protocol']['freeze_criterion']!r}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="mint_v1",
        description="Fail-closed minting of F3-F7 freeze artifacts.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prompt-packages", action="store_true",
                      help="mint PROMPT_<name>_pkg_v%d.json into freeze/"
                           % PACKAGE_VERSION)
    mode.add_argument("--config", action="store_true",
                      help="mint CONFIG_<config_hash>.json from "
                           "MINT_INPUTS.json into freeze/_mint_out/")
    parser.add_argument("--inputs", default=None,
                        help="path to MINT_INPUTS.json (default: freeze/)")
    parser.add_argument("--out", default=None,
                        help="output directory override")
    args = parser.parse_args(argv)
    try:
        if args.prompt_packages:
            return _cli_prompt_packages(args.out)
        return _cli_config(args.inputs, args.out)
    except (MintError, StrictLoadError, schema_gate.SchemaGateError) as e:
        print(str(e), file=sys.stderr)
        return E_MINT_EXIT


if __name__ == "__main__":
    sys.exit(main())
