#!/usr/bin/env python3
"""bootstrap — stdlib-only fresh-interpreter launcher + out-of-band trust root.

Freeze spec v17 §11 / build-spec residual #8: verification-before-import is
circular unless something trusted does the first check, so this stdlib-only
launcher is byte-verified BY THE PARENT before it spawns the fresh child; the
child then byte-verifies every trust-boundary module on disk against the pinned
module manifest BEFORE importing any of them. If any trust-boundary module is
already present in sys.modules before verification, the run FAILS CLOSED
(SV-110 — the project's stale-sys.modules bug). Runtime sys.path/import-origin
sandboxing is out of scope per the threat model.

This module is the OUT-OF-BAND trust root: the constants below are compared
against artifact fields by semantic_validator_v1 (SV-042, SV-090, SV-091,
SV-100, SV-043); an artifact can never nominate its own trust root.

stdlib-only. Must not import any other freeze module at module level — those
are the modules it exists to verify.

Fail-closed exit code: 3 (E_BOOTSTRAP), before any trust-boundary import.
"""
import hashlib
import json
import os
import pathlib
import posixpath
import subprocess
import sys

# ---------------------------------------------------------------------------
# Out-of-band trusted constants (SV-042). A CONFIG cannot self-nominate these.
# Amending them is a code change on the canonical ref, pinned by the module
# manifest like any other trust-boundary byte change.
# ---------------------------------------------------------------------------
TRUSTED_REPO_IDENTITY = "github.com/astonliu/citation-repair-engine"
TRUSTED_CANONICAL_REF = "refs/heads/main"

# SV-091: endpoint.base_url host must be in this pinned allowlist;
# `host_allowlisted` is recomputed from it, never trusted from CONFIG.
TRUSTED_ENDPOINT_HOSTS = ("api.anthropic.com",)

# Residual #6 / SV-090: pinned case-normalized (lowercase) response-header
# allowlist — behavior-relevant, non-secret. Credential-bearing headers are
# additionally hard-denied even if a future edit added them here.
TRUSTED_RESPONSE_HEADERS = frozenset({
    "content-type",
    "content-length",
    "date",
    "request-id",
    "retry-after",
    "anthropic-version",
    "anthropic-ratelimit-requests-limit",
    "anthropic-ratelimit-requests-remaining",
    "anthropic-ratelimit-requests-reset",
    "anthropic-ratelimit-tokens-limit",
    "anthropic-ratelimit-tokens-remaining",
    "anthropic-ratelimit-tokens-reset",
})
CREDENTIAL_HEADER_DENYLIST = frozenset({
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
})

# Residual #6 / SV-100: CAS reference grammar + storage-root confinement.
# A CAS ref is repo-relative, rooted at CAS_ROOT, and names content by its
# SHA-256 — no absolute paths, no '..', no symlink hops expressible.
CAS_ROOT = "cas/"
CAS_REF_GRAMMAR = r"^cas/[0-9a-f]{64}$"

# SV-043: the two known prohibited F3 citing papers (Seeman/DNA-nanotech,
# idelalisib). The citing PMCIDs are ZD input #6 — supplied, never guessed
# (freeze spec, Inputs). Empty until ZD supplies them; while empty the
# superset check is vacuous and SV-043 says so in its violation message.
KNOWN_PROHIBITED_CITING_PMCIDS = ()

# ---------------------------------------------------------------------------
# Module manifest contract (freeze spec §11, SV-110)
# ---------------------------------------------------------------------------
TRUST_BOUNDARY_ROLES = (
    "bootstrap", "validator", "canonicalizer", "renderer", "parser",
    "provider_adapter", "evidence_reader", "runner", "package_init",
    "strict_loader", "semantic_validator",
)

E_BOOTSTRAP_EXIT = 3


class BootstrapError(Exception):
    """Fail-closed bootstrap violation (E_BOOTSTRAP)."""

    def __init__(self, message):
        super().__init__(f"E_BOOTSTRAP: {message}")


# --- inline strict JSON load (duplicate keys / float tokens rejected) -------
# Deliberately duplicated from strict_loader: the bootstrap may not import a
# trust-boundary module before verifying it, and strict_loader is one.

def _reject_float(token):
    raise BootstrapError(f"float token {token!r} in module manifest")


def _pairs_hook(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise BootstrapError(f"duplicate key {k!r} in module manifest")
        seen.add(k)
    return dict(pairs)


def load_manifest(path):
    raw = pathlib.Path(path).read_bytes()
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        raise BootstrapError(f"module manifest is not strict UTF-8: {e}")
    try:
        manifest = json.loads(text, object_pairs_hook=_pairs_hook,
                              parse_float=_reject_float,
                              parse_constant=_reject_float)
    except BootstrapError:
        raise
    except ValueError as e:
        raise BootstrapError(f"module manifest is not valid JSON: {e}")
    if not isinstance(manifest, dict) or manifest.get("artifact_type") != "module_manifest":
        raise BootstrapError("manifest is not a module_manifest artifact")
    if not isinstance(manifest.get("modules"), list) or not manifest["modules"]:
        raise BootstrapError("module manifest carries no modules")
    return manifest


def normalize_repo_path(repo_path):
    """Return the normalized repo-relative POSIX path, or raise (SV-110)."""
    if not isinstance(repo_path, str) or not repo_path:
        raise BootstrapError(f"module repo_path {repo_path!r} is not a nonempty string")
    if "\\" in repo_path:
        raise BootstrapError(f"module repo_path {repo_path!r} contains a backslash")
    if posixpath.isabs(repo_path) or (len(repo_path) > 1 and repo_path[1] == ":"):
        raise BootstrapError(f"module repo_path {repo_path!r} is absolute")
    norm = posixpath.normpath(repo_path)
    if norm != repo_path:
        raise BootstrapError(
            f"module repo_path {repo_path!r} is not normalized (normalizes to {norm!r})")
    parts = norm.split("/")
    if ".." in parts or "." in parts:
        raise BootstrapError(f"module repo_path {repo_path!r} escapes the pinned tree")
    return norm


def check_fresh_interpreter(manifest, repo_root):
    """Fail closed if any trust-boundary module is already imported (SV-110).

    The running bootstrap itself is exempt: its bytes were verified by the
    PARENT before this child was spawned (§11 — that is what breaks the
    verification-before-import circularity).
    """
    repo_root = pathlib.Path(repo_root).resolve()
    self_path = pathlib.Path(__file__).resolve()
    stdlib_root = pathlib.Path(sys.base_prefix).resolve()
    boundary_files = set()
    boundary_basenames = set()
    for m in manifest["modules"]:
        norm = normalize_repo_path(m.get("repo_path"))
        boundary_files.add((repo_root / norm).resolve())
        boundary_basenames.add(posixpath.basename(norm))
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if not f:
            continue
        try:
            fp = pathlib.Path(f).resolve()
        except OSError:
            continue
        if fp == self_path:
            continue
        if fp in boundary_files:
            raise BootstrapError(
                f"trust-boundary module {name!r} ({f}) already in sys.modules "
                f"before byte verification — not a fresh interpreter")
        if fp.name in boundary_basenames and stdlib_root not in fp.parents:
            # A same-named module from ANY other tree (another checkout,
            # site-packages) is exactly the stale-copy drift this gate
            # exists for; only the interpreter's own stdlib is exempt.
            raise BootstrapError(
                f"module {name!r} ({f}) shadows a trust-boundary basename "
                f"from outside the pinned tree before verification")


def verify_manifest(manifest, repo_root):
    """Role coverage, uniqueness, path discipline, on-disk byte verification.

    Runs BEFORE any trust-boundary import. Raises BootstrapError (fail closed,
    zero side effects) on the first violation.
    """
    repo_root = pathlib.Path(repo_root).resolve()
    seen_role_path = set()
    path_hash = {}
    roles_present = set()
    for m in manifest["modules"]:
        if not isinstance(m, dict):
            raise BootstrapError("module entry is not an object")
        role = m.get("role")
        if role not in TRUST_BOUNDARY_ROLES:
            raise BootstrapError(f"unknown module role {role!r}")
        norm = normalize_repo_path(m.get("repo_path"))
        key = (role, norm)
        if key in seen_role_path:
            raise BootstrapError(f"duplicate (role, repo_path) {key!r}")
        seen_role_path.add(key)
        declared = m.get("content_sha256")
        if not isinstance(declared, str) or len(declared) != 64:
            raise BootstrapError(f"module {norm!r} has no usable content_sha256")
        prior = path_hash.get(norm)
        if prior is not None and prior != declared:
            raise BootstrapError(
                f"module path {norm!r} listed twice with differing hashes")
        path_hash[norm] = declared
        roles_present.add(role)
    missing = [r for r in TRUST_BOUNDARY_ROLES if r not in roles_present]
    if missing:
        raise BootstrapError(f"module manifest role coverage not exhaustive; "
                             f"missing roles: {missing}")
    for norm, declared in sorted(path_hash.items()):
        unresolved = repo_root / norm
        if unresolved.is_symlink():
            # Checked BEFORE resolve() — a resolved path is never a symlink.
            raise BootstrapError(f"module {norm!r} is a symlink")
        target = unresolved.resolve()
        if repo_root not in target.parents and target != repo_root:
            raise BootstrapError(f"module {norm!r} resolves outside the pinned tree")
        if not target.is_file():
            raise BootstrapError(f"module {norm!r} missing on disk")
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != declared:
            raise BootstrapError(
                f"module {norm!r} bytes on disk ({actual}) != manifest "
                f"content_sha256 ({declared}) — aborting before import")


def parent_verify_bootstrap(manifest, repo_root):
    """PARENT-side: verify this launcher's own bytes against the manifest."""
    repo_root = pathlib.Path(repo_root).resolve()
    self_path = pathlib.Path(__file__).resolve()
    for m in manifest["modules"]:
        if m.get("role") == "bootstrap":
            norm = normalize_repo_path(m.get("repo_path"))
            target = (repo_root / norm).resolve()
            if target != self_path:
                continue
            actual = hashlib.sha256(self_path.read_bytes()).hexdigest()
            if actual != m.get("content_sha256"):
                raise BootstrapError(
                    f"bootstrap bytes ({actual}) != manifest pin "
                    f"({m.get('content_sha256')})")
            return norm
    raise BootstrapError("no bootstrap manifest entry matches this launcher's path")


def spawn_verified(python_exe, manifest_path, repo_root, extra_argv=()):
    """PARENT-side: byte-verify the launcher, then spawn the fresh child.

    The child runs this file in isolated mode (-I: no site, no PYTHONPATH, no
    cwd on sys.path) — a fresh interpreter by construction — and re-runs the
    full verification itself before any trust-boundary import.
    """
    manifest = load_manifest(manifest_path)
    parent_verify_bootstrap(manifest, repo_root)
    argv = [str(python_exe), "-I", str(pathlib.Path(__file__).resolve()),
            "--manifest", str(manifest_path), "--repo-root", str(repo_root)]
    argv.extend(extra_argv)
    return subprocess.run(argv, capture_output=True, text=True)


def child_main(argv=None):
    """CHILD-side entrypoint: verify everything, only then hand off."""
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {}
    it = iter(argv)
    for a in it:
        if a in ("--manifest", "--repo-root", "--entry"):
            opts[a] = next(it, None)
        else:
            print(f"E_BOOTSTRAP: unknown argument {a!r}", file=sys.stderr)
            return E_BOOTSTRAP_EXIT
    if not opts.get("--manifest") or not opts.get("--repo-root"):
        print("E_BOOTSTRAP: --manifest and --repo-root are required", file=sys.stderr)
        return E_BOOTSTRAP_EXIT
    try:
        manifest = load_manifest(opts["--manifest"])
        # Self-check (honest-drift defense in depth): the RUNNING launcher's
        # bytes must match a manifest bootstrap pin. The sound gate is the
        # parent's pre-spawn verification (a drifted copy's old code cannot
        # be trusted to run this check) — this catches a stray stale copy
        # invoked directly.
        self_sha = hashlib.sha256(
            pathlib.Path(__file__).resolve().read_bytes()).hexdigest()
        boot_pins = {m.get("content_sha256") for m in manifest["modules"]
                     if m.get("role") == "bootstrap"}
        if self_sha not in boot_pins:
            raise BootstrapError(
                f"running launcher bytes ({self_sha}) match no manifest "
                f"bootstrap pin — stale or stray copy; aborting before import")
        check_fresh_interpreter(manifest, opts["--repo-root"])
        verify_manifest(manifest, opts["--repo-root"])
    except BootstrapError as e:
        print(str(e), file=sys.stderr)
        return E_BOOTSTRAP_EXIT
    # Verification passed. Only now may trust-boundary modules be imported.
    entry = opts.get("--entry")
    if entry:
        repo_root = str(pathlib.Path(opts["--repo-root"]).resolve())
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        mod_name, _, func_name = entry.partition(":")
        import importlib
        mod = importlib.import_module(mod_name)
        if func_name:
            getattr(mod, func_name)()
    else:
        print("BOOTSTRAP_OK fresh_interpreter=true modules_verified="
              + str(len(manifest["modules"])))
    return 0


if __name__ == "__main__":
    sys.exit(child_main())
