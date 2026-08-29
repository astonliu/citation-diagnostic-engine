"""semantic_validator_v1 — the §12 SV-table of the F3-F7 finder freeze (v17).

Mechanical, versioned semantic validation beyond JSON Schema: hash agreement,
funnel/reportability equations, canonical ordering + uniqueness, chain/WAL
linkage, git reachability + ancestry, RFC 3339 semantics, percent-encode
round-trip, numeric ranges, cross-field timeout/backoff, candidate==selection
correspondence, module role/path exhaustiveness, and trusted-constant
comparison. No model assigns semantic labels — every rule here is a
recompute-never-trust predicate over artifact bytes and git facts.

API (build spec change item 3):
    validate(artifacts: dict, repo_ctx=None, trusted=None) -> list[Violation]
    Violation = (rule_id, fail_code, path, message)

`artifacts` is a keyed universe of parsed artifacts (see ARTIFACT_KEYS).
Every rule no-ops when the inputs it needs are absent, so targeted fixtures
can exercise one rule at a time; the full positive universe exercises all.
Artifact stores are canonical bytes: a reference hash (e.g.
`selection_artifact_sha256`) is SHA-256 over the referenced artifact's stored
bytes, which for canonically-stored JSON equals canon_sha256(object). Where
the spec demands the committed bytes themselves (SV-034), raw bytes may be
supplied under `<name>_bytes` and take precedence.

`repo_ctx` (GitContext) supplies git facts for SV-041/042/043/044; those
ancestry clauses are evaluated only when it is provided — the runner spec
wires it permanently.

`trusted` (TrustedConstants) defaults to the out-of-band bootstrap constants;
tests may construct their own trust context (that is what "out-of-band"
means: the artifact under test never nominates it).

Residual resolutions carried here (build spec, "Review-round residuals"):
  #1 SV-002 asserts the frozen acceptance constants (template hashes + blob OID)
  #2 SV-005 recomputes observed_runtime == CONFIG runtime_profile
  #3 SV-033 coverage_targets via per-item `stratum` — PROPOSED, pending ZD
  #4 SV-034 binds full rows (source_xml_sha256) + forward into review records
  #5 SV-024 shape-presence-only while response schemas pending ZD (null pin)
  #7 SV-072 recomputes stimulus_sha256; stimulus.codebook == CONFIG.codebook
  #9 SV-042 = trusted-constant equality + ancestry continuity of successive
     observed canonical-ref states (force-push protection is hosting policy)
  #10 SV-041 proves source_tree_oid IS source_commit_oid's tree
"""
import hashlib
import re
import subprocess
from collections import namedtuple
from dataclasses import dataclass, field
from decimal import Decimal
from datetime import date
from urllib.parse import urlsplit

from cde.freeze import bootstrap, schema_gate
from cde.freeze.canon_v1 import CanonV1Error, canon_sha256
from cde.freeze.strict_loader import StrictLoadError, load_strict

SEMANTIC_VALIDATOR_VERSION = "semantic_validator_v1"

Violation = namedtuple("Violation", "rule_id fail_code path message")

# Frozen acceptance constants (freeze spec "Repository target"; residual #1).
# Different object kinds, never equated: content hashes vs a git blob OID.
FROZEN_TEMPLATE_UTF8_SHA256 = {
    "claim_extract": "25f7de6267de4d638d1a5fc0c778b852d3efba4865c35c40ff1a0f980a6a4507",
    "coverage": "1a24d13be0e817a757c8fc5ea1ab40f059c11c580990b09bd5c2fe2d1125421a",
}
FROZEN_SOURCE_BLOB_OID = "sha1:fa01126e2b9482d450065fd70cd0eb1fea816f5c"

# The complete §12 rule contract: stable IDs -> exact fail codes.
RULES = {
    "SV-001": "E_SELF_HASH",
    "SV-002": "E_TEMPLATE",
    "SV-003": "E_FILENAME_HASH",
    "SV-005": "E_RUNTIME_MATCH",
    "SV-010": "E_FUNNEL",
    "SV-011": "E_DISPOSITION",
    "SV-020": "E_CHAIN",
    "SV-021": "E_WAL_CHAIN",
    "SV-022": "E_SEND_BOUNDARY",
    "SV-023": "E_IDEMPOTENCY",
    "SV-024": "E_PARSED",
    "SV-025": "E_DERIVATION",
    "SV-026": "E_WAL_MATCH",
    "SV-030": "E_MANIFEST_RUN",
    "SV-031": "E_MANIFEST_UNIQ",
    "SV-032": "E_ELIGIBLE",
    "SV-033": "E_SELECTION",
    "SV-034": "E_CANDIDATE_BIND",
    "SV-040": "E_EXCLUSION",
    "SV-041": "E_GIT_ANCHOR",
    "SV-042": "E_TRUST_ROOT",
    "SV-043": "E_PROTOCOL",
    "SV-044": "E_EXPOSURE",
    "SV-045": "E_MODE",
    "SV-050": "E_REPORTABLE",
    "SV-060": "E_CITATION_ID",
    "SV-061": "E_OCCURRENCE",
    "SV-070": "E_LEAKAGE",
    "SV-071": "E_STIMULUS",
    "SV-072": "E_STIMULUS_HASH",
    "SV-090": "E_HEADER",
    "SV-091": "E_HOST",
    "SV-100": "E_FORMAT",
    "SV-101": "E_RETRY_POLICY",
    "SV-110": "E_BOOTSTRAP",
}

ARTIFACT_KEYS = (
    "prompt_packages",          # list[prompt_package]
    "runtime_templates",        # {name: template text} (SV-002; defaults to band_prompts)
    "config", "config_hash",    # config object + its filename digest
    "batch", "run_hash",        # batch object + its filename digest
    "run_state_manifest",
    "promotion", "revocations",  # promotion object, list[revocation]
    "selection_artifact", "selection_artifact_bytes",
    "candidate_manifest",
    "candidate_protocol",
    "exposure_plan",
    "exclusion_ledger",         # list[exclusion_ledger_entry], seq order
    "exclusion_checkpoints",    # list[exclusion_checkpoint]
    "review_records",           # list[review_record], seq order
    "wal_events",               # list[wal_event], wal_seq order
    "annotation_release_manifest",
    "release_attestation",
    "module_manifest",
    "stimulus_objects",         # {citation_id: stimulus_object}
    "evidence_snapshots",       # {sha256: bytes}
    "source_xml",               # {citing_pmcid: str} (SV-025, optional)
    "response_schemas",         # {sha256: schema object} (SV-024, post-ZD)
    "reportability_claims",     # {finder/discriminator/composite_result_reportable}
)

# Forbidden provenance keys anywhere in an exported stimulus (SV-070; schema
# `not` clause plus §9's leakage-audit list, calibration status included).
LEAKAGE_KEYS = frozenset({
    "route", "proposed_category", "confidence", "coverage_verdict",
    "source_frame", "sampling_stratum", "raw_model_output", "internal_id",
    "calibration_status",
})

TERMINAL_ERROR_STATUSES = frozenset({
    "malformed", "refusal", "truncated", "provider_error", "timeout",
    "transport_error", "empty", "model_mismatch", "indeterminate",
})

_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")

_RFC3339_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(Z|[+-]\d{2}:\d{2})$")

I_JSON_MAX = 9007199254740991


@dataclass(frozen=True)
class TrustedConstants:
    """Out-of-band trust context; defaults are the bootstrap constants."""
    repo_identity: str = bootstrap.TRUSTED_REPO_IDENTITY
    canonical_ref: str = bootstrap.TRUSTED_CANONICAL_REF
    endpoint_hosts: tuple = bootstrap.TRUSTED_ENDPOINT_HOSTS
    response_headers: frozenset = bootstrap.TRUSTED_RESPONSE_HEADERS
    credential_denylist: frozenset = bootstrap.CREDENTIAL_HEADER_DENYLIST
    cas_root: str = bootstrap.CAS_ROOT
    cas_ref_grammar: str = bootstrap.CAS_REF_GRAMMAR
    known_prohibited_citing_pmcids: tuple = bootstrap.KNOWN_PROHIBITED_CITING_PMCIDS
    # sha256 of the schema file actually used (SV-043) — defaults to the
    # committed pin so the clause binds under the default trust context.
    schema_sha256: str = schema_gate.PINNED_SCHEMA_SHA256


class GitContext:
    """Git facts for the anchoring rules (SV-041/042/043/044).

    artifact_commits maps artifact keys (the ARTIFACT_KEYS names) to the
    introducing commit OID (``sha1:<hex>`` form). observed_ref_states is the
    ordered list of canonical-ref commits observed across validations
    (residual #9: ancestry continuity, no rewind between observations).
    """

    def __init__(self, repo_dir, canonical_ref="refs/heads/main",
                 artifact_commits=None, observed_ref_states=None):
        self.repo_dir = str(repo_dir)
        self.canonical_ref = canonical_ref
        self.artifact_commits = dict(artifact_commits or {})
        self.observed_ref_states = list(observed_ref_states or [])

    @staticmethod
    def bare(oid):
        return oid.split(":", 1)[1] if isinstance(oid, str) and ":" in oid else oid

    def _git(self, *args, check=True):
        r = subprocess.run(["git", "-C", self.repo_dir] + list(args),
                           capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
        return r.stdout.strip()

    def ref_tip(self):
        return self._git("rev-parse", self.canonical_ref)

    def object_type(self, oid):
        try:
            return self._git("cat-file", "-t", self.bare(oid))
        except RuntimeError:
            return None

    def tree_of(self, commit_oid):
        try:
            return self._git("rev-parse", self.bare(commit_oid) + "^{tree}")
        except RuntimeError:
            return None

    def parent_of(self, commit_oid):
        out = self._git("rev-list", "--parents", "-n", "1", self.bare(commit_oid))
        parts = out.split()
        return parts[1] if len(parts) > 1 else None

    def is_ancestor(self, a, b):
        """True iff commit a is an ancestor of (or equal to) commit b."""
        r = subprocess.run(["git", "-C", self.repo_dir, "merge-base",
                            "--is-ancestor", self.bare(a), self.bare(b)],
                           capture_output=True, text=True)
        return r.returncode == 0

    def reachable_from_ref(self, oid):
        return self.is_ancestor(oid, self.ref_tip())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _sha256_utf8(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_canon_sha256(obj, out, rule_id, path):
    try:
        return canon_sha256(obj)
    except CanonV1Error as e:
        out.append(Violation(rule_id, RULES[rule_id], path,
                             f"canonicalization failed: {e}"))
        return None


def _without(obj, key):
    return {k: v for k, v in obj.items() if k != key}


def pctencode(text):
    """Canonical percent-encoding: unreserved kept, else %XX uppercase UTF-8."""
    out = []
    for b in text.encode("utf-8"):
        ch = chr(b)
        if ch in _UNRESERVED:
            out.append(ch)
        else:
            out.append(f"%{b:02X}")
    return "".join(out)


def pctdecode(text):
    """Strict percent-decoding; raises ValueError on malformed escapes."""
    out = bytearray()
    i = 0
    while i < len(text):
        c = text[i]
        if c == "%":
            if i + 2 >= len(text) + 1 or len(text) < i + 3:
                raise ValueError(f"truncated percent escape at {i}")
            hexpair = text[i + 1:i + 3]
            if not re.fullmatch(r"[0-9A-Fa-f]{2}", hexpair):
                raise ValueError(f"invalid percent escape %{hexpair}")
            out.append(int(hexpair, 16))
            i += 3
        else:
            out.extend(c.encode("utf-8"))
            i += 1
    return out.decode("utf-8", "strict")


def _split_citation_id(cid):
    pmcid, _, ref = cid.partition(":")
    return pmcid, ref


def _rfc3339_ok(s):
    m = _RFC3339_RE.match(s)
    if not m:
        return False
    y, mo, d, h, mi, sec = (int(m.group(i)) for i in range(1, 7))
    try:
        date(y, mo, d)
    except ValueError:
        return False
    if h > 23 or mi > 59 or sec > 60:
        # sec == 60 accepted: RFC 3339 permits the leap second (whether the
        # instant existed needs an IERS table — out of scope here).
        return False
    off = m.group(8)
    if off != "Z":
        oh, om = int(off[1:3]), int(off[4:6])
        if oh > 23 or om > 59:
            return False
    return True


def _walk(obj, path, fn):
    fn(obj, path)
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk(v, f"{path}.{k}", fn)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _walk(v, f"{path}[{i}]", fn)


def _extract_claims(record):
    """Readable extracted-claims list, or None if the parsed shape is opaque
    (response schemas arrive with ZD input #1, pending — residual #5)."""
    extract = record.get("extract") or {}
    if extract.get("status") != "ok":
        return None
    parsed = extract.get("parsed")
    if isinstance(parsed, dict) and isinstance(parsed.get("claims"), list):
        return parsed["claims"]
    return None


def _coverage_verdict(call):
    """Readable coverage verdict ('supported'/'unsupported') or None (interim
    convention while the pinned response schemas are pending — residual #5)."""
    parsed = call.get("parsed")
    if isinstance(parsed, dict) and parsed.get("verdict") in ("supported",
                                                              "unsupported"):
        return parsed["verdict"]
    return None


def derive_disposition(record, excluded_pmcids):
    """§9 precedence, recomputed from record facts: excluded -> held ->
    quarantined -> flagged -> clear. Returns (disposition, certain)."""
    if record.get("citing_pmcid") in excluded_pmcids:
        return "excluded", True
    extract = record.get("extract") or {}
    coverage = record.get("coverage") or []
    if extract.get("status") == "indeterminate":
        # Crash policy (§6): an ambiguous send quarantines the item — this
        # outranks the extraction-failure -> held tier.
        return "quarantined", True
    if extract.get("status") != "ok":
        return "held", True
    claims = _extract_claims(record)
    if claims is not None and len(claims) == 0:
        return "held", True
    calls = [ci.get("call") or {} for ci in coverage]
    if calls and all(c.get("call_made") is False
                     and c.get("reason") == "evidence_unavailable"
                     for c in calls):
        return "held", True
    if any(c.get("call_made") is True and c.get("status") in
           TERMINAL_ERROR_STATUSES for c in calls):
        return "quarantined", True
    verdicts = [_coverage_verdict(c) for c in calls if c.get("status") == "ok"]
    if any(v is None for v in verdicts) or not calls:
        return None, False  # flagged/clear indistinguishable without verdicts
    if any(v == "unsupported" for v in verdicts):
        return "flagged", True
    return "clear", True


def derive_reportability(artifacts, trusted):
    """§10 derivation function, computed live — never persisted (SV-050)."""
    config_hash = artifacts.get("config_hash")
    config = artifacts.get("config") or {}
    batch = artifacts.get("batch") or {}
    rsm = artifacts.get("run_state_manifest") or {}
    promotion = artifacts.get("promotion")
    revocations = artifacts.get("revocations") or []
    record = (artifacts.get("review_records") or [None])[0]
    promoted = bool(
        promotion and (config_hash is None or
                       promotion.get("payload", {}).get("config_hash") == config_hash))
    eligible = bool(record and record.get("sample_purpose") == "formal"
                    and record.get("finder_disposition") == "flagged")
    # development ⇒ never eligible/promotable/reportable (SV-045 matrix);
    # a reportable finder result comes from a release run.
    mode = batch.get("execution_mode") or rsm.get("execution_mode")
    if mode is not None and mode != "release":
        eligible = False
    unrevoked = True
    if promotion:
        try:
            payload_sha = canon_sha256(promotion.get("payload", {}))
        except CanonV1Error:
            payload_sha = None
            unrevoked = False  # unhashable promotion payload: fail closed
        if payload_sha is not None:
            unrevoked = not any(
                r.get("payload", {}).get("target_promotion_payload_sha256")
                == payload_sha for r in revocations)
    # in-scope = the finder-frontend scope this freeze certifies (Decision 3).
    in_scope = (config.get("scope") == "finder_frontend_extract_coverage"
                if config else True)
    finder = promoted and eligible and unrevoked and in_scope
    claims = artifacts.get("reportability_claims") or {}
    discriminator = bool(claims.get("discriminator_result_reportable", False))
    return {
        "finder_result_reportable": finder,
        "discriminator_result_reportable": discriminator,
        "composite_result_reportable": finder and discriminator,
    }


# ---------------------------------------------------------------------------
# rule implementations
# ---------------------------------------------------------------------------

def _v(out, rule_id, path, message):
    out.append(Violation(rule_id, RULES[rule_id], path, message))


def _iter_self_hashed(artifacts):
    """(kind, path, obj, hash_field, preimage) per SV-001's declared preimages."""
    items = []
    for i, pkg in enumerate(artifacts.get("prompt_packages") or []):
        items.append(("prompt_package", f"prompt_packages[{i}].package_sha256",
                      pkg, "package_sha256", _without(pkg, "package_sha256")))
    promo = artifacts.get("promotion")
    if promo:
        # Envelope: payload ONLY — envelope fields incl. signed_tag_oid are
        # outside the hash by design (anchored by the introducing commit).
        items.append(("promotion", "promotion.payload_sha256",
                      promo, "payload_sha256", promo.get("payload")))
    for i, rev in enumerate(artifacts.get("revocations") or []):
        items.append(("revocation", f"revocations[{i}].payload_sha256",
                      rev, "payload_sha256", rev.get("payload")))
    for i, e in enumerate(artifacts.get("exclusion_ledger") or []):
        items.append(("exclusion_ledger_entry",
                      f"exclusion_ledger[{i}].entry_sha256",
                      e, "entry_sha256", _without(e, "entry_sha256")))
    for i, r in enumerate(artifacts.get("review_records") or []):
        items.append(("review_record", f"review_records[{i}].record_sha256",
                      r, "record_sha256", _without(r, "record_sha256")))
    att = artifacts.get("release_attestation")
    if att:
        items.append(("release_attestation", "release_attestation.attestation_sha256",
                      att, "attestation_sha256", _without(att, "attestation_sha256")))
    for i, ev in enumerate(artifacts.get("wal_events") or []):
        items.append(("wal_event", f"wal_events[{i}].event_sha256",
                      ev, "event_sha256", _without(ev, "event_sha256")))
    return items


def check_sv001(artifacts, out):
    for kind, path, obj, hash_field, preimage in _iter_self_hashed(artifacts):
        declared = obj.get(hash_field)
        if preimage is None or declared is None:
            _v(out, "SV-001", path, f"{kind} lacks {hash_field} or its preimage")
            continue
        actual = _safe_canon_sha256(preimage, out, "SV-001", path)
        if actual is not None and actual != declared:
            _v(out, "SV-001", path,
               f"{kind} {hash_field} {declared} != canon_sha256(declared "
               f"preimage) {actual}")
    rsm = artifacts.get("run_state_manifest")
    if rsm and "observed_runtime" in rsm:
        actual = _safe_canon_sha256(rsm["observed_runtime"], out, "SV-001",
                                    "run_state_manifest.observed_runtime_sha256")
        if actual is not None and actual != rsm.get("observed_runtime_sha256"):
            _v(out, "SV-001", "run_state_manifest.observed_runtime_sha256",
               f"observed_runtime_sha256 {rsm.get('observed_runtime_sha256')} "
               f"!= recomputed {actual}")


def _default_runtime_templates():
    try:
        from cde.claims import band_prompts
        return {"claim_extract": band_prompts.CLAIM_EXTRACT_PROMPT,
                "coverage": band_prompts.COVERAGE_PROMPT}
    except Exception:
        return {}


def check_sv002(artifacts, out):
    packages = artifacts.get("prompt_packages") or []
    runtime = artifacts.get("runtime_templates")
    if runtime is None and packages:
        runtime = _default_runtime_templates()
    for i, pkg in enumerate(packages):
        path = f"prompt_packages[{i}]"
        name = pkg.get("name")
        # Recompute, never trust: the embedded text hashes to the stored digest.
        actual_tpl = _sha256_utf8(pkg.get("template_utf8", ""))
        if actual_tpl != pkg.get("template_utf8_sha256"):
            _v(out, "SV-002", f"{path}.template_utf8_sha256",
               f"embedded template_utf8 hashes to {actual_tpl}, stored digest "
               f"is {pkg.get('template_utf8_sha256')} (drifted template with a "
               f"pasted-in digest fails here)")
        rc = pkg.get("render_contract")
        actual_rc = _safe_canon_sha256(rc, out, "SV-002",
                                       f"{path}.render_contract_sha256")
        if actual_rc is not None and actual_rc != pkg.get("render_contract_sha256"):
            _v(out, "SV-002", f"{path}.render_contract_sha256",
               f"render_contract recomputes to {actual_rc}, stored "
               f"{pkg.get('render_contract_sha256')}")
        # runtime_constant_utf8_sha256 == template_utf8_sha256
        if runtime and name in runtime:
            rt_sha = _sha256_utf8(runtime[name])
            if rt_sha != pkg.get("template_utf8_sha256"):
                _v(out, "SV-002", f"{path}.template_utf8_sha256",
                   f"runtime constant for {name!r} hashes to {rt_sha}, package "
                   f"declares {pkg.get('template_utf8_sha256')}")
        # Residual #1: the frozen acceptance constants — internal consistency
        # alone is not a freeze.
        frozen = FROZEN_TEMPLATE_UTF8_SHA256.get(name)
        if frozen and pkg.get("template_utf8_sha256") != frozen:
            _v(out, "SV-002", f"{path}.template_utf8_sha256",
               f"{name} template_utf8_sha256 {pkg.get('template_utf8_sha256')} "
               f"!= frozen acceptance constant {frozen}")
        if pkg.get("source_blob_oid") != FROZEN_SOURCE_BLOB_OID:
            # Blob OID compared by string equality with the frozen constant;
            # NEVER compared to (or derived from) a content hash.
            _v(out, "SV-002", f"{path}.source_blob_oid",
               f"source_blob_oid {pkg.get('source_blob_oid')} != frozen "
               f"acceptance constant {FROZEN_SOURCE_BLOB_OID}")
    # §1: the CONFIG reference binds package_version + the full package_sha256
    # (recomputed — covering render_contract_sha256 transitively).
    config = artifacts.get("config")
    if config and packages:
        refs = config.get("prompt_packages") or {}
        for i, pkg in enumerate(packages):
            ref = refs.get(pkg.get("name"))
            if ref is None:
                continue
            recomputed = _safe_canon_sha256(_without(pkg, "package_sha256"),
                                            out, "SV-002",
                                            f"prompt_packages[{i}]")
            if recomputed is not None and \
                    ref.get("package_sha256") != recomputed:
                _v(out, "SV-002",
                   f"config.prompt_packages.{pkg.get('name')}.package_sha256",
                   f"CONFIG binds package_sha256 {ref.get('package_sha256')} "
                   f"but the committed package recomputes to {recomputed}")
            if ref.get("package_version") != pkg.get("package_version"):
                _v(out, "SV-002",
                   f"config.prompt_packages.{pkg.get('name')}.package_version",
                   f"CONFIG package_version {ref.get('package_version')} != "
                   f"package's {pkg.get('package_version')}")


def check_sv003(artifacts, out):
    config = artifacts.get("config")
    declared_ch = artifacts.get("config_hash")
    if config is not None and declared_ch is not None:
        actual = _safe_canon_sha256(config, out, "SV-003", "config_hash")
        if actual is not None and actual != declared_ch:
            _v(out, "SV-003", "config_hash",
               f"filename config_hash {declared_ch} != canon_sha256(CONFIG) {actual}")
    batch = artifacts.get("batch")
    declared_rh = artifacts.get("run_hash")
    if batch is not None and declared_rh is not None:
        # run_hash is a filename digest, not a field: BATCH ∖ run_hash == BATCH.
        actual = _safe_canon_sha256(batch, out, "SV-003", "run_hash")
        if actual is not None and actual != declared_rh:
            _v(out, "SV-003", "run_hash",
               f"filename run_hash {declared_rh} != canon_sha256(BATCH) {actual}")
    if batch is not None and declared_ch is not None and \
            batch.get("config_hash") != declared_ch:
        _v(out, "SV-003", "batch.config_hash",
           f"batch.config_hash {batch.get('config_hash')} != the filename "
           f"config_hash {declared_ch} the run claims to be under")


_RUNTIME_MATCH_FIELDS = (
    "python_implementation", "python_version", "dependency_lock_sha256",
    "distribution_inventory_sha256", "transport_library_version",
    "jcs_library_version",
)


def check_sv005(artifacts, out):
    config = artifacts.get("config")
    if not config:
        return
    profile = config.get("runtime_profile") or {}
    for holder_name in ("batch", "run_state_manifest"):
        holder = artifacts.get(holder_name)
        if not holder:
            continue
        observed = holder.get("observed_runtime") or {}
        mismatches = [f for f in _RUNTIME_MATCH_FIELDS
                      if observed.get(f) != profile.get(f)]
        # matches_config_runtime_profile is RECOMPUTED from this comparison,
        # never trusted from the artifact (residual #2).
        if mismatches:
            _v(out, "SV-005", f"{holder_name}.observed_runtime",
               f"observed_runtime differs from CONFIG runtime_profile on "
               f"{mismatches}; recomputed matches_config_runtime_profile=false "
               f"regardless of the stored flag")


def check_sv010(artifacts, out):
    batch = artifacts.get("batch")
    if not batch:
        return
    f = batch.get("funnel") or {}
    try:
        if f["input_total"] != f["excluded"] + f["runner_accepted"]:
            _v(out, "SV-010", "batch.funnel",
               f"input_total {f['input_total']} != excluded {f['excluded']} + "
               f"runner_accepted {f['runner_accepted']}")
        acc = f["flagged"] + f["clear"] + f["held"] + f["quarantined"]
        if f["runner_accepted"] != acc:
            _v(out, "SV-010", "batch.funnel",
               f"runner_accepted {f['runner_accepted']} != flagged+clear+held+"
               f"quarantined {acc}")
    except KeyError as e:
        _v(out, "SV-010", "batch.funnel", f"funnel missing field {e}")


def _excluded_pmcids_at_start(artifacts):
    ledger = artifacts.get("exclusion_ledger") or []
    batch = artifacts.get("batch") or {}
    n = None
    at_start = batch.get("exclusion_checkpoint_sha256_at_start")
    for cp in artifacts.get("exclusion_checkpoints") or []:
        try:
            if canon_sha256(cp) == at_start:
                n = cp.get("entry_count")
                break
        except CanonV1Error:
            continue
    entries = ledger if n is None else [e for e in ledger if e.get("seq", 0) < n]
    return {e.get("citing_pmcid") for e in entries
            if e.get("scope") == "citing_paper"}


def check_sv011(artifacts, out):
    records = artifacts.get("review_records") or []
    if not records:
        return
    excluded = _excluded_pmcids_at_start(artifacts)
    for i, rec in enumerate(records):
        derived, certain = derive_disposition(rec, excluded)
        stored = rec.get("finder_disposition")
        if certain and derived != stored:
            _v(out, "SV-011", f"review_records[{i}].finder_disposition",
               f"stored disposition {stored!r} != derived {derived!r} under "
               f"precedence excluded->held->quarantined->flagged->clear")
        elif not certain and stored not in ("flagged", "clear"):
            _v(out, "SV-011", f"review_records[{i}].finder_disposition",
               f"stored disposition {stored!r}, but derivation reaches the "
               f"flagged/clear tier (no higher-precedence state applies)")


def check_sv020(artifacts, out):
    batch = artifacts.get("batch")
    records = artifacts.get("review_records")
    if not batch:
        return
    gp = batch.get("genesis_preimage") or {}
    actual_gen = _safe_canon_sha256(gp, out, "SV-020", "batch.genesis")
    if actual_gen is not None and actual_gen != batch.get("genesis"):
        _v(out, "SV-020", "batch.genesis",
           f"genesis {batch.get('genesis')} != canon_sha256(genesis_preimage) "
           f"{actual_gen}")
    for fld in ("config_hash", "run_id", "selection_artifact_sha256",
                "candidate_manifest_sha256"):
        if gp.get(fld) != batch.get(fld):
            _v(out, "SV-020", f"batch.genesis_preimage.{fld}",
               f"genesis_preimage.{fld} {gp.get(fld)} != batch.{fld} "
               f"{batch.get(fld)}")
    if records is None:
        return
    if batch.get("review_dump_sha256") is not None:
        # Canonical-store convention: the committed dump is the canonical
        # bytes of the record sequence; the chain must be recomputable from
        # exactly the dump the BATCH names.
        actual_dump = _safe_canon_sha256(records, out, "SV-020",
                                         "batch.review_dump_sha256")
        if actual_dump is not None and \
                actual_dump != batch.get("review_dump_sha256"):
            _v(out, "SV-020", "batch.review_dump_sha256",
               f"review_dump_sha256 {batch.get('review_dump_sha256')} != "
               f"canonical hash of the record sequence {actual_dump}")
    prev = batch.get("genesis")
    for i, rec in enumerate(records):
        if rec.get("config_hash") != batch.get("config_hash"):
            _v(out, "SV-020", f"review_records[{i}].config_hash",
               f"record config_hash {rec.get('config_hash')} != the run's "
               f"{batch.get('config_hash')}")
        if rec.get("run_id") != batch.get("run_id"):
            _v(out, "SV-020", f"review_records[{i}].run_id",
               f"record run_id {rec.get('run_id')} != the run's "
               f"{batch.get('run_id')}")
        rf = rec.get("run_facts") or {}
        if rf.get("config_hash_used") != rec.get("config_hash"):
            _v(out, "SV-020", f"review_records[{i}].run_facts.config_hash_used",
               "run_facts.config_hash_used != the record's config_hash")
        if rec.get("seq") != i:
            _v(out, "SV-020", f"review_records[{i}].seq",
               f"chain seq not contiguous from 0: expected {i}, got "
               f"{rec.get('seq')}")
        expected_prev = None if i == 0 else records[i - 1].get("record_sha256")
        link = rec.get("prev_record_sha256")
        if i == 0:
            # seq 0 links to genesis (genesis is seq -1's prev).
            if link != prev:
                _v(out, "SV-020", "review_records[0].prev_record_sha256",
                   f"seq 0 prev_record_sha256 {link} != genesis {prev}")
        elif link != expected_prev:
            _v(out, "SV-020", f"review_records[{i}].prev_record_sha256",
               f"prev_record_sha256 {link} != preceding record_sha256 "
               f"{expected_prev}")
    if records:
        tip = records[-1].get("record_sha256")
        if tip != batch.get("chain_tip"):
            _v(out, "SV-020", "batch.chain_tip",
               f"chain_tip {batch.get('chain_tip')} != recomputed tip {tip}")
    if batch.get("chain_record_count") != len(records):
        _v(out, "SV-020", "batch.chain_record_count",
           f"chain_record_count {batch.get('chain_record_count')} != "
           f"{len(records)} records in dump")


def check_sv021(artifacts, out):
    events = artifacts.get("wal_events")
    if not events:
        return
    prev_sha = None
    for i, ev in enumerate(events):
        if ev.get("wal_seq") != i:
            _v(out, "SV-021", f"wal_events[{i}].wal_seq",
               f"global wal_seq not contiguous: expected {i}, got "
               f"{ev.get('wal_seq')}")
        if ev.get("prev_wal_event_sha256") != prev_sha:
            _v(out, "SV-021", f"wal_events[{i}].prev_wal_event_sha256",
               f"prev_wal_event_sha256 {ev.get('prev_wal_event_sha256')} != "
               f"{prev_sha}")
        prev_sha = ev.get("event_sha256")
    by_attempt = {}
    for i, ev in enumerate(events):
        by_attempt.setdefault(ev.get("attempt_id"), []).append((i, ev))
    for attempt_id, evs in by_attempt.items():
        prev_att_sha = None
        for j, (i, ev) in enumerate(evs):
            if ev.get("attempt_event_seq") != j:
                _v(out, "SV-021", f"wal_events[{i}].attempt_event_seq",
                   f"attempt {attempt_id!r} event seq not contiguous: expected "
                   f"{j}, got {ev.get('attempt_event_seq')}")
            if ev.get("prev_attempt_event_sha256") != prev_att_sha:
                _v(out, "SV-021", f"wal_events[{i}].prev_attempt_event_sha256",
                   f"attempt link {ev.get('prev_attempt_event_sha256')} != "
                   f"{prev_att_sha}")
            prev_att_sha = ev.get("event_sha256")
        for i, ev in evs:
            if ev.get("event_type") == "indeterminate":
                sent = [e for _, e in evs
                        if e.get("event_type") == "sent"
                        and e.get("wal_seq", 0) < ev.get("wal_seq", 0)]
                if not sent:
                    _v(out, "SV-021", f"wal_events[{i}]",
                       f"indeterminate event in attempt {attempt_id!r} has no "
                       f"prior durable 'sent' event in the same attempt")
                elif ev.get("prev_attempt_terminal_sha256") not in {
                        e.get("event_sha256") for e in sent}:
                    _v(out, "SV-021", f"wal_events[{i}].prev_attempt_terminal_sha256",
                       f"indeterminate does not link a prior durable 'sent' "
                       f"event of its attempt")
    batch = artifacts.get("batch")
    if batch and batch.get("wal_tip_sha256") is not None:
        tip = events[-1].get("event_sha256")
        if batch["wal_tip_sha256"] != tip:
            _v(out, "SV-021", "batch.wal_tip_sha256",
               f"wal_tip_sha256 {batch['wal_tip_sha256']} != last event "
               f"{tip}")


def _calls_by_logical_id(artifacts):
    calls = {}
    for i, rec in enumerate(artifacts.get("review_records") or []):
        ex = rec.get("extract") or {}
        if ex.get("call_made") is True:
            calls[ex.get("logical_call_id")] = (f"review_records[{i}].extract",
                                                ex, rec, None)
        for j, ci in enumerate(rec.get("coverage") or []):
            c = ci.get("call") or {}
            if c.get("call_made") is True:
                calls[c.get("logical_call_id")] = (
                    f"review_records[{i}].coverage[{j}].call", c, rec,
                    ci.get("claim_idx"))
    return calls


def check_sv022(artifacts, out):
    events = artifacts.get("wal_events")
    if not events:
        return
    calls = _calls_by_logical_id(artifacts)
    by_call = {}
    for ev in events:
        by_call.setdefault(ev.get("logical_call_id"), {}).setdefault(
            ev.get("attempt_ordinal"), []).append(ev)
    for lcid, attempts in by_call.items():
        ordinals = sorted(attempts)
        for k in ordinals:
            evs = attempts[k]
            terminal = evs[-1]
            has_next = (k + 1) in attempts
            etype = terminal.get("event_type")
            # Retry only when NOT sent_boundary_crossed: permitted after any
            # attempt that never crossed the boundary (dangling 'prepared' /
            # 'transport_failed' — safe pre-send states), or after a
            # PERSISTED response (a definite outcome; retryable_status is
            # what makes post-response retries meaningful). An attempt whose
            # boundary was crossed without a persisted response ('sent'
            # dangling, 'indeterminate') is never silently retried (crash
            # policy, review round 6, item 6).
            boundary_crossed = any(e.get("sent_boundary_crossed") is True
                                   for e in evs)
            if has_next and boundary_crossed and \
                    etype != "response_persisted":
                _v(out, "SV-022", f"wal_events(logical_call={lcid!r}, attempt={k})",
                   f"retry (attempt {k + 1}) after attempt {k} crossed the "
                   f"send boundary without a persisted response (terminal "
                   f"event {etype!r})")
            if not has_next and boundary_crossed and \
                    etype in ("sent", "indeterminate", "transport_failed"):
                entry = calls.get(lcid)
                if entry and entry[1].get("status") != "indeterminate":
                    _v(out, "SV-022", entry[0],
                       f"logical call {lcid!r} crossed the send boundary with "
                       f"no persisted response; review_call status "
                       f"{entry[1].get('status')!r} must be 'indeterminate' "
                       f"(item quarantined)")


def check_sv023(artifacts, out):
    events = artifacts.get("wal_events") or []
    attempts = {}
    for ev in events:
        attempts.setdefault(ev.get("attempt_id"), []).append(ev)
    for attempt_id, evs in attempts.items():
        if not any(e.get("event_type") == "prepared" for e in evs):
            _v(out, "SV-023", f"wal_events(attempt={attempt_id!r})",
               "attempt carries no 'prepared' event — the idempotency "
               "preimage was never persisted, so the key is unrecomputable")
    for i, ev in enumerate(events):
        if ev.get("event_type") != "prepared":
            continue
        pre = ev.get("idempotency_preimage")
        if pre is None:
            _v(out, "SV-023", f"wal_events[{i}]",
               "prepared event lacks the persisted idempotency_preimage")
            continue
        actual = _safe_canon_sha256(pre, out, "SV-023",
                                    f"wal_events[{i}].idempotency_key")
        if actual is not None and actual != ev.get("idempotency_key"):
            _v(out, "SV-023", f"wal_events[{i}].idempotency_key",
               f"idempotency_key {ev.get('idempotency_key')} != "
               f"canon_sha256(idempotency_preimage) {actual}")
        if pre.get("request_bytes_sha256") != ev.get("request_bytes_sha256"):
            _v(out, "SV-023", f"wal_events[{i}].idempotency_preimage",
               "preimage request_bytes_sha256 differs from the event's")


def check_sv024(artifacts, out):
    config = artifacts.get("config") or {}
    stages = config.get("stages") or {}
    response_schemas = artifacts.get("response_schemas") or {}

    def _check_parsed(call, stage, path):
        pinned = (stages.get(stage) or {}).get("response_schema_sha256")
        parsed = call.get("parsed")
        if parsed is None:
            _v(out, "SV-024", f"{path}.parsed",
               f"ok call carries no parsed response shape"
               + ("" if pinned else " (response schema pending ZD approval — "
                  "residual #5; shape presence only)"))
            return
        if pinned:
            schema = response_schemas.get(pinned)
            if schema is None:
                _v(out, "SV-024", f"{path}.parsed",
                   f"stage {stage!r} pins response schema {pinned} but no such "
                   f"committed schema was supplied")
            else:
                try:
                    from jsonschema import Draft202012Validator
                    errs = list(Draft202012Validator(schema).iter_errors(parsed))
                except Exception as e:  # pragma: no cover - schema itself bad
                    errs = [e]
                if errs:
                    _v(out, "SV-024", f"{path}.parsed",
                       f"parsed does not conform to the stage's pinned "
                       f"response schema: {errs[0]}")

    for i, rec in enumerate(artifacts.get("review_records") or []):
        extract = rec.get("extract") or {}
        coverage = rec.get("coverage") or []
        if extract.get("status") == "ok":
            _check_parsed(extract, "claim_extract", f"review_records[{i}].extract")
        elif coverage:
            _v(out, "SV-024", f"review_records[{i}].coverage",
               f"extraction did not succeed but {len(coverage)} coverage "
               f"items exist (must be 0)")
        claims = _extract_claims(rec)
        if claims is not None and len(coverage) != len(claims):
            _v(out, "SV-024", f"review_records[{i}].coverage",
               f"coverage cardinality {len(coverage)} != {len(claims)} "
               f"extracted claims (one coverage_item per claim)")
        for j, ci in enumerate(coverage):
            if ci.get("claim_idx") != j:
                _v(out, "SV-024", f"review_records[{i}].coverage[{j}].claim_idx",
                   f"claim_idx {ci.get('claim_idx')} breaks contiguous 0..n-1 "
                   f"correspondence")
            call = ci.get("call") or {}
            if call.get("status") == "ok":
                _check_parsed(call, "coverage",
                              f"review_records[{i}].coverage[{j}].call")


def check_sv025(artifacts, out):
    stimuli = artifacts.get("stimulus_objects") or {}
    snapshots = artifacts.get("evidence_snapshots")
    config = artifacts.get("config") or {}
    sentinel = (((config.get("stages") or {}).get("coverage") or {})
                .get("evidence_policy") or {}).get("missing_evidence_sentinel")
    records = {r.get("item_key"): r for r in artifacts.get("review_records") or []}
    xml = artifacts.get("source_xml") or {}
    for cid, stim in stimuli.items():
        path = f"stimulus_objects[{cid!r}]"
        ev = stim.get("evidence") or {}
        sha = ev.get("evidence_snapshot_sha256")
        rec0 = records.get(cid)
        if rec0 is not None and rec0.get("evidence_snapshot_sha256") is not None \
                and sha != rec0.get("evidence_snapshot_sha256"):
            # Never trust the stimulus's self-declared snapshot: it must be
            # the record's pinned snapshot, not merely *a* valid snapshot.
            _v(out, "SV-025", f"{path}.evidence.evidence_snapshot_sha256",
               f"stimulus evidence snapshot {sha} != the review record's "
               f"pinned evidence_snapshot_sha256 "
               f"{rec0.get('evidence_snapshot_sha256')}")
        if snapshots is not None:
            blob = snapshots.get(sha)
            if blob is None:
                _v(out, "SV-025", f"{path}.evidence",
                   f"evidence_snapshot_sha256 {sha} resolves to no supplied "
                   f"snapshot")
            else:
                actual = hashlib.sha256(blob).hexdigest()
                if actual != sha:
                    _v(out, "SV-025", f"{path}.evidence",
                       f"snapshot bytes hash {actual} != declared {sha}")
                else:
                    text = blob.decode("utf-8", "replace")
                    if ev.get("text") != sentinel and ev.get("text") not in text:
                        _v(out, "SV-025", f"{path}.evidence.text",
                           "evidence.text does not derive from the pinned "
                           "snapshot under the CONFIG policy (not a snapshot "
                           "substring and not the missing-evidence sentinel)")
        rec = records.get(cid)
        if rec is not None and stim.get("citing_sentence") != rec.get("citing_sentence"):
            _v(out, "SV-025", f"{path}.citing_sentence",
               "stimulus citing_sentence != review record citing_sentence "
               "(must derive from the selected source-XML occurrence)")
        pmcid = _split_citation_id(cid)[0]
        if pmcid in xml and stim.get("citing_sentence") not in xml[pmcid]:
            _v(out, "SV-025", f"{path}.citing_sentence",
               "citing_sentence not present in the source XML occurrence")


def check_sv026(artifacts, out):
    events = artifacts.get("wal_events")
    if events is None:
        return
    by_call = {}
    for ev in events:
        by_call.setdefault(ev.get("logical_call_id"), []).append(ev)
    for lcid, path, call, rec, claim_idx in [
            (lc, p, c, r, ci) for lc, (p, c, r, ci)
            in _calls_by_logical_id(artifacts).items()]:
        evs = sorted(by_call.get(lcid, []), key=lambda e: e.get("wal_seq", 0))
        if not evs:
            _v(out, "SV-026", path,
               f"review_call logical_call_id {lcid!r} has no WAL events")
            continue
        expected = [e.get("event_sha256") for e in evs]
        if call.get("retry_wal_event_shas") != expected:
            _v(out, "SV-026", f"{path}.retry_wal_event_shas",
               "retry_wal_event_shas != every WAL event hash of the logical "
               "call in global order")
        for ev in evs:
            if ev.get("request_bytes_sha256") != call.get("request_bytes_sha256"):
                _v(out, "SV-026", path,
                   f"WAL event wal_seq={ev.get('wal_seq')} request hash "
                   f"differs from the review_call's")
            if ev.get("idempotency_key") != call.get("idempotency_key"):
                _v(out, "SV-026", path,
                   f"WAL event wal_seq={ev.get('wal_seq')} idempotency_key "
                   f"differs from the review_call's")
        ordinals = sorted({e.get("attempt_ordinal") for e in evs})
        if ordinals != list(range(len(ordinals))):
            _v(out, "SV-026", path,
               f"attempt ordinals {ordinals} not contiguous from 0")
        terminal = evs[-1]
        status = call.get("status")
        if status in ("ok", "malformed", "refusal", "truncated",
                      "provider_error", "empty"):
            if terminal.get("event_type") != "response_persisted":
                _v(out, "SV-026", path,
                   f"status {status!r} but terminal WAL event is "
                   f"{terminal.get('event_type')!r}, not response_persisted")
            else:
                pairs = [("http_status", "http_status"),
                         ("raw_response_body_sha256", "response_body_sha256"),
                         ("raw_response_body_ref", "response_body_ref"),
                         ("response_headers_allowlisted",
                          "response_headers_allowlisted"),
                         ("provider_model_id", "provider_model_id"),
                         ("response_id", "response_id"),
                         ("finish_reason", "finish_reason")]
                for call_f, ev_f in pairs:
                    if call_f in call and call.get(call_f) != terminal.get(ev_f):
                        _v(out, "SV-026", f"{path}.{call_f}",
                           f"review_call {call_f} != terminal WAL event {ev_f}")
        elif status == "indeterminate":
            if (terminal.get("event_type") != "indeterminate"
                    or call.get("indeterminate_wal_event_sha256")
                    != terminal.get("event_sha256")):
                _v(out, "SV-026", path,
                   "indeterminate review_call must name the WAL event proving "
                   "the ambiguous send")
        elif status == "transport_error":
            if (terminal.get("event_type") != "transport_failed"
                    or call.get("terminal_wal_event_sha256")
                    != terminal.get("event_sha256")):
                _v(out, "SV-026", path,
                   "transport_error review_call must name its terminal "
                   "transport_failed WAL event")
        elif status == "model_mismatch":
            # A mismatch is diagnosed FROM a persisted response.
            if terminal.get("event_type") != "response_persisted":
                _v(out, "SV-026", path,
                   f"model_mismatch review_call's terminal WAL event is "
                   f"{terminal.get('event_type')!r}, not response_persisted")
            elif call.get("provider_model_id") != terminal.get("provider_model_id"):
                _v(out, "SV-026", f"{path}.provider_model_id",
                   "model_mismatch provider_model_id != terminal WAL event's")
        elif status == "timeout":
            # Terminal timeout is pre-send connect ONLY (v17); its WAL
            # evidence is a pre-send failure, never a crossed boundary.
            if terminal.get("event_type") != "transport_failed" or \
                    terminal.get("sent_boundary_crossed") is not False:
                _v(out, "SV-026", path,
                   f"terminal 'timeout' (connect-only) must end in a pre-send "
                   f"transport_failed WAL event; got "
                   f"{terminal.get('event_type')!r}")
        prepared = [e for e in evs if e.get("event_type") == "prepared"]
        for ev in prepared:
            pre = ev.get("idempotency_preimage") or {}
            stage = pre.get("stage")
            if claim_idx is None and (stage != "claim_extract"
                                      or pre.get("claim_idx") is not None):
                _v(out, "SV-026", path,
                   "extraction call preimage must have stage=claim_extract and "
                   "claim_idx null")
            if claim_idx is not None and (stage != "coverage"
                                          or pre.get("claim_idx") != claim_idx):
                _v(out, "SV-026", path,
                   f"coverage call preimage must have stage=coverage and "
                   f"claim_idx == {claim_idx}")
            if rec is not None and pre.get("citation_id") != rec.get("item_key"):
                _v(out, "SV-026", path,
                   "preimage citation_id differs from the record's item_key")


def check_sv030(artifacts, out):
    manifest = artifacts.get("annotation_release_manifest")
    if not manifest:
        return
    batch = artifacts.get("batch")
    records = artifacts.get("review_records") or []
    by_sha = {r.get("record_sha256"): r for r in records}
    if batch:
        if not (batch.get("execution_mode") == "release"
                and batch.get("sample_purpose") == "formal"):
            _v(out, "SV-030", "annotation_release_manifest",
               f"source BATCH is {batch.get('execution_mode')}/"
               f"{batch.get('sample_purpose')}, not release/formal")
        run_hash = artifacts.get("run_hash")
        if run_hash is not None and manifest.get("source_run_hash") != run_hash:
            _v(out, "SV-030", "annotation_release_manifest.source_run_hash",
               f"source_run_hash {manifest.get('source_run_hash')} != BATCH "
               f"run_hash {run_hash}")
        if manifest.get("source_review_dump_sha256") != batch.get("review_dump_sha256"):
            _v(out, "SV-030", "annotation_release_manifest.source_review_dump_sha256",
               "source_review_dump_sha256 != BATCH review_dump_sha256")
        if manifest.get("source_selection_hash") != batch.get("selection_artifact_sha256"):
            _v(out, "SV-030", "annotation_release_manifest.source_selection_hash",
               "source_selection_hash != BATCH selection_artifact_sha256")
    # The manifest's own references are recomputed, never trusted (same
    # class as the row bindings below — audit round 3).
    promo = artifacts.get("promotion")
    payload_sha = None
    if promo is not None:
        payload_sha = _safe_canon_sha256(promo.get("payload") or {}, out,
                                         "SV-030", "promotion.payload")
        if payload_sha is not None and \
                manifest.get("promotion_payload_sha256") != payload_sha:
            _v(out, "SV-030", "annotation_release_manifest.promotion_payload_sha256",
               f"manifest promotion_payload_sha256 "
               f"{manifest.get('promotion_payload_sha256')} != committed "
               f"promotion payload hash {payload_sha}")
    if batch and manifest.get("config_hash") != batch.get("config_hash"):
        _v(out, "SV-030", "annotation_release_manifest.config_hash",
           "manifest and source BATCH are not under the same CONFIG")
    if artifacts.get("exclusion_checkpoints") is not None and \
            _resolve_checkpoint(artifacts,
                                manifest.get("exclusion_checkpoint_sha256")) is None:
        _v(out, "SV-030", "annotation_release_manifest.exclusion_checkpoint_sha256",
           "manifest exclusion_checkpoint_sha256 resolves to no committed "
           "checkpoint artifact")
    att = artifacts.get("release_attestation")
    if att is not None:
        actual_m = _safe_canon_sha256(manifest, out, "SV-030",
                                      "annotation_release_manifest")
        if actual_m is not None and \
                att.get("annotation_release_manifest_sha256") != actual_m:
            _v(out, "SV-030", "release_attestation.annotation_release_manifest_sha256",
               f"attestation binds manifest {att.get('annotation_release_manifest_sha256')} "
               f"but the committed manifest recomputes to {actual_m}")
        if payload_sha is not None and \
                att.get("promotion_payload_sha256") != payload_sha:
            _v(out, "SV-030", "release_attestation.promotion_payload_sha256",
               "attestation promotion_payload_sha256 != committed promotion "
               "payload hash")
        if batch and att.get("config_hash") != batch.get("config_hash"):
            _v(out, "SV-030", "release_attestation.config_hash",
               "attestation and source BATCH are not under the same CONFIG")
        if artifacts.get("exclusion_checkpoints") is not None and \
                _resolve_checkpoint(artifacts,
                                    att.get("exclusion_checkpoint_sha256")) is None:
            _v(out, "SV-030", "release_attestation.exclusion_checkpoint_sha256",
               "attestation exclusion_checkpoint_sha256 resolves to no "
               "committed checkpoint artifact")
    sel_items = {i.get("citation_id"): i for i in
                 (artifacts.get("selection_artifact") or {}).get("items") or []}
    for i, row in enumerate(manifest.get("inventory") or []):
        rec = by_sha.get(row.get("review_record_sha256"))
        path = f"annotation_release_manifest.inventory[{i}]"
        if rec is None:
            _v(out, "SV-030", path,
               f"row review_record_sha256 {row.get('review_record_sha256')} "
               f"not in the source BATCH review chain")
            continue
        if rec.get("seq") != row.get("review_record_seq"):
            _v(out, "SV-030", path,
               f"row review_record_seq {row.get('review_record_seq')} != "
               f"chain record seq {rec.get('seq')}")
        # A row is not merely a pointer: its content must be the record's
        # (fabricated row fields must not survive a valid pointer).
        for row_f, rec_f in (("citation_id", "item_key"),
                             ("resolved_work_id", "resolved_work_id"),
                             ("stimulus_sha256", "stimulus_sha256"),
                             ("evidence_snapshot_sha256",
                              "evidence_snapshot_sha256")):
            if row.get(row_f) != rec.get(rec_f):
                _v(out, "SV-030", f"{path}.{row_f}",
                   f"row {row_f} {row.get(row_f)!r} != chain record's "
                   f"{rec_f} {rec.get(rec_f)!r}")
        sel_item = sel_items.get(row.get("citation_id"))
        if sel_item is not None and row.get("occurrence_identity") != \
                sel_item.get("occurrence_identity"):
            _v(out, "SV-030", f"{path}.occurrence_identity",
               "row occurrence_identity != the selection item's")


def check_sv031(artifacts, out):
    manifest = artifacts.get("annotation_release_manifest")
    if not manifest:
        return
    inv = manifest.get("inventory") or []
    cids = [row.get("citation_id") for row in inv]
    if cids != sorted(cids):
        _v(out, "SV-031", "annotation_release_manifest.inventory",
           "inventory not sorted by citation_id")
    if len(set(cids)) != len(cids):
        _v(out, "SV-031", "annotation_release_manifest.inventory",
           "inventory citation_id values not unique")
    occs = [row.get("occurrence_identity") for row in inv]
    if len(set(occs)) != len(occs):
        _v(out, "SV-031", "annotation_release_manifest.inventory",
           "inventory occurrence_identity values not unique")


def check_sv032(artifacts, out):
    manifest = artifacts.get("annotation_release_manifest")
    if not manifest:
        return
    records = {r.get("record_sha256"): r for r in artifacts.get("review_records") or []}
    for i, row in enumerate(manifest.get("inventory") or []):
        rec = records.get(row.get("review_record_sha256"))
        if rec is None:
            continue  # SV-030's finding
        # finder_evaluation_eligible is DERIVED: calibration => ineligible.
        if rec.get("sample_purpose") == "calibration":
            _v(out, "SV-032", f"annotation_release_manifest.inventory[{i}]",
               "calibration item present in the release inventory — "
               "recomputed finder_evaluation_eligible is false")
        if rec.get("finder_disposition") != "flagged":
            _v(out, "SV-032", f"annotation_release_manifest.inventory[{i}]",
               f"inventory row backed by a {rec.get('finder_disposition')!r} "
               f"record; only flagged rows are eligible")


def check_sv033(artifacts, out):
    sel = artifacts.get("selection_artifact")
    if not sel:
        return
    items = sel.get("items") or []
    cids = [i.get("citation_id") for i in items]
    if cids != sorted(cids):
        _v(out, "SV-033", "selection_artifact.items",
           "items not canonically sorted by citation_id")
    if len(set(cids)) != len(cids):
        _v(out, "SV-033", "selection_artifact.items",
           "citation_id values not unique")
    occs = [i.get("occurrence_identity") for i in items]
    if len(set(occs)) != len(occs):
        _v(out, "SV-033", "selection_artifact.items",
           "occurrence_identity values not unique")
    if len(items) < sel.get("min_size", 0):
        _v(out, "SV-033", "selection_artifact.items",
           f"len(items) {len(items)} < min_size {sel.get('min_size')}")
    targets = sel.get("coverage_targets") or {}
    if targets:
        # Residual #3 (PROPOSED, pending ZD approval): coverage_targets are
        # evaluable only through per-item strata. Fail closed when strata are
        # missing; ZD may instead demote coverage_targets to informational.
        missing = [i for i, it in enumerate(items) if "stratum" not in it]
        if missing:
            _v(out, "SV-033", "selection_artifact.items",
               f"coverage_targets present but items {missing} carry no "
               f"stratum — unevaluable; failing closed pending ZD decision "
               f"(residual #3 proposal)")
            return
        counts = {}
        for i, it in enumerate(items):
            s = it.get("stratum")
            if s not in targets:
                _v(out, "SV-033", f"selection_artifact.items[{i}].stratum",
                   f"stratum {s!r} is not a coverage_targets key")
            counts[s] = counts.get(s, 0) + 1
        for key, target in targets.items():
            if counts.get(key, 0) < target:
                _v(out, "SV-033", "selection_artifact.coverage_targets",
                   f"coverage target {key!r} unmet: {counts.get(key, 0)} < "
                   f"{target}")


def _resolve_selection(artifacts, out, rule_id, expected_sha_fields):
    """Resolve the committed selection artifact via its sha256 (SV-034)."""
    raw = artifacts.get("selection_artifact_bytes")
    if raw is not None:
        actual = hashlib.sha256(raw).hexdigest()
        for path, expected in expected_sha_fields:
            if expected is not None and expected != actual:
                _v(out, rule_id, path,
                   f"{path} {expected} != committed selection bytes hash "
                   f"{actual}")
        try:
            return load_strict(raw)
        except StrictLoadError as e:
            _v(out, rule_id, "selection_artifact_bytes",
               f"committed selection bytes do not strictly load: {e}")
            return None
    sel = artifacts.get("selection_artifact")
    if sel is not None:
        # Fail CLOSED: an uncanonicalizable selection cannot prove the
        # binding, so the mismatch is reported rather than skipped.
        actual = _safe_canon_sha256(sel, out, rule_id, "selection_artifact")
        if actual is not None:
            for path, expected in expected_sha_fields:
                if expected is not None and expected != actual:
                    _v(out, rule_id, path,
                       f"{path} {expected} != selection canonical hash "
                       f"{actual}")
    return sel


def check_sv034(artifacts, out):
    cm = artifacts.get("candidate_manifest")
    if not cm:
        return
    batch = artifacts.get("batch") or {}
    sel = _resolve_selection(
        artifacts, out, "SV-034",
        [("candidate_manifest.selection_artifact_sha256",
          cm.get("selection_artifact_sha256")),
         ("batch.selection_artifact_sha256",
          batch.get("selection_artifact_sha256"))])
    if sel is None:
        return
    sel_items = sel.get("items") or []
    cand_items = cm.get("items") or []
    sel_ids = [i.get("citation_id") for i in sel_items]
    cand_ids = [i.get("citation_id") for i in cand_items]
    if sel_ids != cand_ids:
        _v(out, "SV-034", "candidate_manifest.items",
           f"candidate citation_id sequence != committed selection's ordered "
           f"set (selection {len(sel_ids)} ids, candidate {len(cand_ids)}; "
           f"same ids, same order, same count required)")
        return
    if batch:
        actual_cm = _safe_canon_sha256(cm, out, "SV-034", "candidate_manifest")
        if actual_cm is not None and \
                batch.get("candidate_manifest_sha256") != actual_cm:
            _v(out, "SV-034", "batch.candidate_manifest_sha256",
               f"batch candidate_manifest_sha256 "
               f"{batch.get('candidate_manifest_sha256')} != committed "
               f"candidate manifest hash {actual_cm}")
        if cm.get("config_hash") != batch.get("config_hash"):
            _v(out, "SV-034", "candidate_manifest.config_hash",
               "candidate manifest and BATCH are not under the same CONFIG")
    sel_by_id = {i.get("citation_id"): i for i in sel_items}
    records = {r.get("item_key"): r for r in artifacts.get("review_records") or []}
    manifest_rows = {row.get("citation_id"): row
                     for row in (artifacts.get("annotation_release_manifest")
                                 or {}).get("inventory") or []}
    for j, ci in enumerate(cand_items):
        cid = ci.get("citation_id")
        # Residual #4: bind FULL rows, not the id set — fail closed on
        # conflicting source snapshots (§7).
        if ci.get("source_xml_sha256") != sel_by_id[cid].get("source_xml_sha256"):
            _v(out, "SV-034", f"candidate_manifest.items[{j}].source_xml_sha256",
               f"candidate source_xml_sha256 conflicts with the selection's "
               f"for {cid!r} — conflicting source snapshots fail closed")
        rec = records.get(cid)
        if rec is not None and rec.get("finder_disposition") != "excluded":
            if rec.get("resolved_work_id") != ci.get("resolved_work_id"):
                _v(out, "SV-034", f"candidate_manifest.items[{j}].resolved_work_id",
                   f"review record resolved_work_id does not bind forward from "
                   f"the candidate row for {cid!r}")
            if rec.get("evidence_snapshot_sha256") != ci.get("evidence_snapshot_sha256"):
                _v(out, "SV-034",
                   f"candidate_manifest.items[{j}].evidence_snapshot_sha256",
                   f"review record evidence_snapshot_sha256 does not bind "
                   f"forward from the candidate row for {cid!r}")
        row = manifest_rows.get(cid)
        if row is not None and row.get("resolution_provenance_sha256") != \
                ci.get("resolution_provenance_sha256"):
            _v(out, "SV-034",
               f"candidate_manifest.items[{j}].resolution_provenance_sha256",
               f"release-manifest row resolution_provenance_sha256 does not "
               f"bind forward from the candidate row for {cid!r}")


def _resolve_checkpoint(artifacts, sha):
    for cp in artifacts.get("exclusion_checkpoints") or []:
        try:
            if canon_sha256(cp) == sha:
                return cp
        except CanonV1Error:
            continue
    return None


def check_sv040(artifacts, out):
    batch = artifacts.get("batch")
    ledger = artifacts.get("exclusion_ledger") or []
    if batch:
        excluded = _excluded_pmcids_at_start(artifacts)
        for i, rec in enumerate(artifacts.get("review_records") or []):
            if rec.get("citing_pmcid") in excluded and \
                    rec.get("finder_disposition") != "excluded":
                _v(out, "SV-040", f"review_records[{i}]",
                   f"citing_pmcid {rec.get('citing_pmcid')} is excluded "
                   f"citing-paper-wide at the start checkpoint but the item "
                   f"was not excluded")
    promo = artifacts.get("promotion")
    if promo and batch:
        n1_sha = promo.get("payload", {}).get(
            "post_exposure_exclusion_checkpoint_sha256")
        n1 = _resolve_checkpoint(artifacts, n1_sha)
        start_sha = batch.get("exclusion_checkpoint_sha256_at_start")
        start_cp = _resolve_checkpoint(artifacts, start_sha)
        if n1 is None or start_cp is None:
            _v(out, "SV-040", "promotion.payload",
               "cannot resolve the exclusion checkpoints (batch start, "
               "promotion N+1) to committed checkpoint artifacts")
            return
        if batch.get("execution_mode") == "candidate":
            # Chronology §8b: the candidate run consulted N; PROMOTION pins
            # the post-exposure N+1, strictly newer.
            if n1.get("entry_count", 0) <= start_cp.get("entry_count", 0):
                _v(out, "SV-040", "promotion.payload."
                   "post_exposure_exclusion_checkpoint_sha256",
                   f"post-exposure checkpoint N+1 (entry_count "
                   f"{n1.get('entry_count')}) is not strictly newer than the "
                   f"candidate BATCH's N ({start_cp.get('entry_count')})")
        else:
            # Every later formal selection consults N+1 or later.
            if start_cp.get("entry_count", 0) < n1.get("entry_count", 0):
                _v(out, "SV-040", "batch.exclusion_checkpoint_sha256_at_start",
                   f"formal run consulted checkpoint entry_count "
                   f"{start_cp.get('entry_count')} older than the promotion's "
                   f"post-exposure N+1 ({n1.get('entry_count')})")
        if ledger:
            for cp, label in ((start_cp, "at_start"), (n1, "N+1")):
                cnt = cp.get("entry_count", 0)
                tip = ledger[cnt - 1].get("entry_sha256") if cnt else None
                if cnt > len(ledger) or tip != cp.get("tip_entry_sha256"):
                    _v(out, "SV-040", f"exclusion_checkpoint[{label}]",
                       f"checkpoint {label} tip/count does not match the "
                       f"ledger prefix (rewritten or forked ledger)")
        plan = artifacts.get("exposure_plan")
        if plan and ledger:
            n1_cnt = n1.get("entry_count", 0)
            covered = {e.get("citing_pmcid") for e in ledger[:n1_cnt]
                       if e.get("scope") == "citing_paper"}
            for i, row in enumerate(plan.get("exposed") or []):
                pmcid = _split_citation_id(row.get("citation_id", ""))[0]
                if pmcid not in covered:
                    _v(out, "SV-040", f"exposure_plan.exposed[{i}]",
                       f"exposed citing paper {pmcid} is not covered "
                       f"citing-paper-wide by checkpoint N+1")


def check_sv041(artifacts, repo_ctx, out):
    if repo_ctx is None:
        return
    tip = repo_ctx.ref_tip()
    for key, commit in repo_ctx.artifact_commits.items():
        if not repo_ctx.is_ancestor(commit, tip):
            _v(out, "SV-041", key,
               f"artifact introducing commit {commit} not reachable from the "
               f"trusted canonical ref")
    att = artifacts.get("release_attestation")
    if att and "release_attestation" in repo_ctx.artifact_commits:
        intro = repo_ctx.artifact_commits["release_attestation"]
        parent = repo_ctx.parent_of(intro)
        expected = GitContext.bare(att.get("canonical_ref_commit", ""))
        if parent != expected:
            _v(out, "SV-041", "release_attestation.canonical_ref_commit",
               f"introducing commit's immediate parent {parent} != "
               f"validated-state commit {expected} (not merely a descendant)")
    for i, cp in enumerate(artifacts.get("exclusion_checkpoints") or []):
        key = f"exclusion_checkpoints[{i}]"
        if key in repo_ctx.artifact_commits:
            intro = repo_ctx.artifact_commits[key]
            parent = repo_ctx.parent_of(intro)
            expected = GitContext.bare(cp.get("canonical_ref_commit", ""))
            if parent != expected:
                _v(out, "SV-041", f"{key}.canonical_ref_commit",
                   f"introducing commit's immediate parent {parent} != "
                   f"validated-state commit {expected}")
    rev_keys = sorted((k for k in repo_ctx.artifact_commits
                       if k.startswith("revocations[")),
                      key=lambda k: int(k[len("revocations["):-1]))
    rev_commits = [repo_ctx.artifact_commits[k] for k in rev_keys]
    for a, b in zip(rev_commits, rev_commits[1:]):
        # Revocation ORDER is commit ancestry, never timestamps: each
        # revocation's introducing commit must be a strict ancestor of the
        # next one's (incomparable commits have no defined order).
        if a == b or not repo_ctx.is_ancestor(a, b):
            _v(out, "SV-041", "revocations",
               f"revocation commits {a} and {b} are not ancestry-ordered")
    config = artifacts.get("config")
    if config:
        src = config.get("source") or {}
        commit_oid = src.get("source_commit_oid")
        if commit_oid:
            if repo_ctx.object_type(commit_oid) != "commit":
                _v(out, "SV-041", "config.source.source_commit_oid",
                   f"{commit_oid} does not name a COMMIT object")
            else:
                tree = repo_ctx.tree_of(commit_oid)
                if tree != GitContext.bare(src.get("source_tree_oid", "")):
                    _v(out, "SV-041", "config.source.source_tree_oid",
                       f"source_tree_oid {src.get('source_tree_oid')} is not "
                       f"source_commit_oid's tree ({tree}) — residual #10")


def check_sv042(artifacts, repo_ctx, trusted, out):
    config = artifacts.get("config")
    if config:
        src = config.get("source") or {}
        if src.get("repo_identity") != trusted.repo_identity:
            _v(out, "SV-042", "config.source.repo_identity",
               f"repo_identity {src.get('repo_identity')!r} != out-of-band "
               f"trusted constant {trusted.repo_identity!r} — a CONFIG cannot "
               f"nominate its own trust root")
        if src.get("canonical_ref") != trusted.canonical_ref:
            _v(out, "SV-042", "config.source.canonical_ref",
               f"canonical_ref {src.get('canonical_ref')!r} != trusted "
               f"constant {trusted.canonical_ref!r}")
    if repo_ctx is not None:
        # Residual #9: ancestry continuity between successive observed
        # canonical-ref states (no rewind/rewrite between observations).
        # Force-push/deletion protection is hosting policy, not a predicate.
        states = repo_ctx.observed_ref_states
        for a, b in zip(states, states[1:]):
            if not repo_ctx.is_ancestor(a, b):
                _v(out, "SV-042", "observed_ref_states",
                   f"canonical ref moved from {a} to {b} without ancestry "
                   f"continuity (rewind/rewrite between observations)")


def check_sv043(artifacts, repo_ctx, trusted, out):
    proto = artifacts.get("candidate_protocol")
    config = artifacts.get("config")
    if proto is None:
        return
    if config:
        actual = _safe_canon_sha256(proto, out, "SV-043",
                                    "candidate_protocol")
        if actual is not None and \
                config.get("candidate_protocol_sha256") != actual:
            _v(out, "SV-043", "config.candidate_protocol_sha256",
               f"CONFIG candidate_protocol_sha256 "
               f"{config.get('candidate_protocol_sha256')} != committed "
               f"protocol hash {actual}")
    if trusted.schema_sha256 and \
            proto.get("schema_sha256") != trusted.schema_sha256:
        _v(out, "SV-043", "candidate_protocol.schema_sha256",
           f"protocol schema_sha256 {proto.get('schema_sha256')} != the "
           f"schema actually used {trusted.schema_sha256}")
    cases = proto.get("prohibited_cases") or []
    pmcids = [c.get("citing_pmcid") for c in cases]
    if len(set(pmcids)) != len(pmcids):
        _v(out, "SV-043", "candidate_protocol.prohibited_cases",
           "prohibited_cases not unique by citing_pmcid")
    known = set(trusted.known_prohibited_citing_pmcids)
    if known:
        missing = known - set(pmcids)
        if missing:
            _v(out, "SV-043", "candidate_protocol.prohibited_cases",
               f"prohibited_cases missing known citing papers: "
               f"{sorted(missing)}")
    # (While ZD input #5 is unsupplied the superset check is vacuous; the
    # uniqueness and exclusion checks below still bind.)
    promo = artifacts.get("promotion")
    if promo and config and promo.get("payload", {}).get(
            "candidate_protocol_sha256") != config.get("candidate_protocol_sha256"):
        _v(out, "SV-043", "promotion.payload.candidate_protocol_sha256",
           "PROMOTION's candidate_protocol_sha256 != CONFIG's (§12 promotion "
           "walk: the freeze criterion is the protocol's)")
    prohibited = set(pmcids)
    sel = artifacts.get("selection_artifact")
    if sel:
        for i, item in enumerate(sel.get("items") or []):
            if item.get("citing_pmcid") in prohibited:
                _v(out, "SV-043", f"selection_artifact.items[{i}]",
                   f"selection includes prohibited citing_pmcid "
                   f"{item.get('citing_pmcid')}")
    cm = artifacts.get("candidate_manifest")
    if cm:
        # SV-034's cross-reference: the prohibited-case check applies to BOTH.
        for i, item in enumerate(cm.get("items") or []):
            pmcid = _split_citation_id(item.get("citation_id", ""))[0]
            if pmcid in prohibited:
                _v(out, "SV-043", f"candidate_manifest.items[{i}]",
                   f"candidate manifest includes prohibited citing_pmcid "
                   f"{pmcid}")
    if repo_ctx is not None and "candidate_protocol" in repo_ctx.artifact_commits:
        proto_commit = repo_ctx.artifact_commits["candidate_protocol"]
        for key in ("config", "batch"):
            if key in repo_ctx.artifact_commits:
                other = repo_ctx.artifact_commits[key]
                if proto_commit == other or not repo_ctx.is_ancestor(
                        proto_commit, other):
                    _v(out, "SV-043", "candidate_protocol",
                       f"protocol introducing commit {proto_commit} was not "
                       f"committed strictly before the {key}'s ({other})")


def check_sv044(artifacts, repo_ctx, out):
    promo = artifacts.get("promotion")
    if not promo:
        return
    payload = promo.get("payload") or {}
    plan = artifacts.get("exposure_plan")
    run_hash = artifacts.get("run_hash")
    batch = artifacts.get("batch")
    # The promotion targets its CANDIDATE batch; enforce the target bindings
    # only when the supplied batch IS that run (a later release run carries
    # the same promotion but its own run_hash).
    is_target_run = bool(batch) and payload.get("run_hash") == run_hash
    if plan is not None:
        actual = _safe_canon_sha256(plan, out, "SV-044", "exposure_plan")
        if actual is not None and payload.get("exposure_plan_sha256") != actual:
            _v(out, "SV-044", "promotion.payload.exposure_plan_sha256",
               f"exposure_plan_sha256 {payload.get('exposure_plan_sha256')} "
               f"!= committed exposure plan hash {actual}")
        if plan.get("run_hash") != payload.get("run_hash"):
            _v(out, "SV-044", "exposure_plan.run_hash",
               f"exposure_plan.run_hash {plan.get('run_hash')} != promotion "
               f"payload run_hash {payload.get('run_hash')}")
        seen_cid, seen_sha = set(), set()
        records = {(r.get("item_key"), r.get("record_sha256"))
                   for r in artifacts.get("review_records") or []}
        for i, row in enumerate(plan.get("exposed") or []):
            key = (row.get("citation_id"), row.get("review_record_sha256"))
            if is_target_run and artifacts.get("review_records") is not None \
                    and key not in records:
                _v(out, "SV-044", f"exposure_plan.exposed[{i}]",
                   f"exposure row {key} not in the run's review chain")
            # Unique by citation_id AND review_record_sha256 = each key
            # individually unique (as SV-031/SV-033 read the same phrase).
            if key[0] in seen_cid or key[1] in seen_sha:
                _v(out, "SV-044", f"exposure_plan.exposed[{i}]",
                   f"exposure rows not unique by citation_id AND "
                   f"review_record_sha256: {key}")
            seen_cid.add(key[0])
            seen_sha.add(key[1])
    if batch and batch.get("execution_mode") == "candidate":
        if payload.get("run_hash") != run_hash:
            _v(out, "SV-044", "promotion.payload.run_hash",
               f"promotion run_hash {payload.get('run_hash')} != candidate "
               f"run_hash {run_hash}")
    if is_target_run:
        if not (batch.get("execution_mode") == "candidate"
                and batch.get("sample_purpose") == "calibration"):
            _v(out, "SV-044", "promotion.payload.run_hash",
               f"promotion targets a {batch.get('execution_mode')}/"
               f"{batch.get('sample_purpose')} BATCH, not candidate/calibration")
        if payload.get("config_hash") != batch.get("config_hash"):
            _v(out, "SV-044", "promotion.payload.config_hash",
               "promotion and target BATCH are not under the same CONFIG")
    if repo_ctx is not None and \
            "exposure_plan" in repo_ctx.artifact_commits and \
            "promotion" in repo_ctx.artifact_commits:
        pc = repo_ctx.artifact_commits["exposure_plan"]
        mc = repo_ctx.artifact_commits["promotion"]
        if pc == mc or not repo_ctx.is_ancestor(pc, mc):
            _v(out, "SV-044", "exposure_plan",
               f"exposure_plan introducing commit {pc} is not a strict "
               f"ancestor of PROMOTION's ({mc}) — committed-before-reveal "
               f"proxy fails")


def check_sv045(artifacts, out):
    promo = artifacts.get("promotion")
    for holder_name in ("batch", "run_state_manifest"):
        holder = artifacts.get(holder_name)
        if not holder:
            continue
        mode = holder.get("execution_mode")
        purpose = holder.get("sample_purpose")
        at_start = holder.get("promotion_payload_sha256_at_start")
        if mode == "candidate":
            if purpose != "calibration" or at_start is not None:
                _v(out, "SV-045", holder_name,
                   f"candidate mode requires calibration purpose and null "
                   f"promotion; got {purpose!r} / {at_start!r}")
        elif mode == "release":
            if purpose != "formal" or at_start is None:
                _v(out, "SV-045", holder_name,
                   f"release mode requires formal purpose and a promotion at "
                   f"start; got {purpose!r} / {at_start!r}")
            elif promo is not None:
                try:
                    payload_sha = canon_sha256(promo.get("payload") or {})
                except CanonV1Error:
                    payload_sha = None
                if payload_sha is not None and at_start != payload_sha:
                    _v(out, "SV-045", f"{holder_name}."
                       f"promotion_payload_sha256_at_start",
                       f"release run's promotion-at-start {at_start} does not "
                       f"match the valid PROMOTION payload {payload_sha}")
        elif mode == "development":
            if holder_name == "batch":
                _v(out, "SV-045", "batch",
                   "a development run never produces a BATCH artifact "
                   "(unattested, nonreportable)")
    batch = artifacts.get("batch")
    rsm = artifacts.get("run_state_manifest")
    if batch and rsm:
        # Resume cannot switch the dataset, mode, or checkpoint: the
        # run-state manifest binds every immutable input the BATCH binds.
        for fld in ("run_id", "execution_mode", "sample_purpose",
                    "config_hash", "selection_artifact_sha256",
                    "candidate_manifest_sha256", "genesis",
                    "exclusion_checkpoint_sha256_at_start",
                    "promotion_payload_sha256_at_start"):
            if rsm.get(fld) != batch.get(fld):
                _v(out, "SV-045", f"run_state_manifest.{fld}",
                   f"run-state {fld} {rsm.get(fld)!r} != BATCH's "
                   f"{batch.get(fld)!r} — resume cannot switch the dataset")


def check_sv050(artifacts, trusted, out):
    claims = artifacts.get("reportability_claims")
    if claims is None:
        return
    derived = derive_reportability(artifacts, trusted)
    for key in ("finder_result_reportable", "composite_result_reportable"):
        if key in claims and bool(claims[key]) != derived[key]:
            _v(out, "SV-050", f"reportability_claims.{key}",
               f"claimed {key}={claims[key]} but the derivation function "
               f"computes {derived[key]} from canonical-ref state + artifacts")
    if claims.get("composite_result_reportable") and not (
            claims.get("finder_result_reportable")
            and claims.get("discriminator_result_reportable")):
        _v(out, "SV-050", "reportability_claims.composite_result_reportable",
           "composite => finder AND discriminator violated")


def _check_citation_id(cid, path, out, ref_id=None):
    pmcid, ref = _split_citation_id(cid)
    if not re.fullmatch(r"PMC[0-9]+", pmcid) or not ref:
        _v(out, "SV-060", path, f"citation_id {cid!r} grammar invalid")
        return
    try:
        decoded = pctdecode(ref)
    except (ValueError, UnicodeDecodeError) as e:
        _v(out, "SV-060", path, f"citation_id {cid!r} percent-decoding fails: {e}")
        return
    reencoded = pctencode(decoded)
    if reencoded != ref:
        _v(out, "SV-060", path,
           f"citation_id {cid!r} is not canonical: decode/re-encode yields "
           f"{pmcid}:{reencoded}")
    if ref_id is not None and pctencode(ref_id) != ref:
        _v(out, "SV-060", path,
           f"item_key {cid!r} != citing_pmcid + ':' + pctencode(ref_id) "
           f"({pmcid}:{pctencode(ref_id)})")


def check_sv060(artifacts, out):
    for i, rec in enumerate(artifacts.get("review_records") or []):
        _check_citation_id(rec.get("item_key", ""),
                           f"review_records[{i}].item_key", out,
                           ref_id=rec.get("ref_id"))
        expected_pmcid = _split_citation_id(rec.get("item_key", ""))[0]
        if rec.get("citing_pmcid") != expected_pmcid:
            _v(out, "SV-060", f"review_records[{i}].citing_pmcid",
               f"citing_pmcid {rec.get('citing_pmcid')} != item_key prefix "
               f"{expected_pmcid}")
    sel = artifacts.get("selection_artifact")
    for i, item in enumerate((sel or {}).get("items") or []):
        _check_citation_id(item.get("citation_id", ""),
                           f"selection_artifact.items[{i}].citation_id", out,
                           ref_id=item.get("ref_id"))
    cm = artifacts.get("candidate_manifest")
    for i, item in enumerate((cm or {}).get("items") or []):
        _check_citation_id(item.get("citation_id", ""),
                           f"candidate_manifest.items[{i}].citation_id", out)
    manifest = artifacts.get("annotation_release_manifest")
    for i, row in enumerate((manifest or {}).get("inventory") or []):
        _check_citation_id(row.get("citation_id", ""),
                           f"annotation_release_manifest.inventory[{i}]."
                           f"citation_id", out)
    plan = artifacts.get("exposure_plan")
    for i, row in enumerate((plan or {}).get("exposed") or []):
        _check_citation_id(row.get("citation_id", ""),
                           f"exposure_plan.exposed[{i}].citation_id", out)
    for i, entry in enumerate(artifacts.get("exclusion_ledger") or []):
        _check_citation_id(entry.get("citation_id", ""),
                           f"exclusion_ledger[{i}].citation_id", out)
    for cid in artifacts.get("stimulus_objects") or {}:
        _check_citation_id(cid, f"stimulus_objects[{cid!r}]", out)


def check_sv061(artifacts, out):
    sel = artifacts.get("selection_artifact")
    if not sel:
        return
    for i, item in enumerate(sel.get("items") or []):
        # These four members EXACTLY, keyed by these field names; the
        # normalization contract hash is deliberately NOT in the preimage.
        preimage = {
            "occurrence_identity_version": item.get("occurrence_identity_version"),
            "citing_pmcid": item.get("citing_pmcid"),
            "normalized_ref_key_type": item.get("normalized_ref_key_type"),
            "normalized_ref_key": item.get("normalized_ref_key"),
        }
        actual = _safe_canon_sha256(preimage, out, "SV-061",
                                    f"selection_artifact.items[{i}]")
        if actual is not None and actual != item.get("occurrence_identity"):
            _v(out, "SV-061",
               f"selection_artifact.items[{i}].occurrence_identity",
               f"occurrence_identity {item.get('occurrence_identity')} != "
               f"canon_sha256 of the schema-named four-member preimage "
               f"{actual}")
        fp_pre = {
            "source_xml_sha256": item.get("source_xml_sha256"),
            "ref_content_utf8": item.get("ref_content_utf8"),
            "extraction_contract_version": item.get("extraction_contract_version"),
        }
        actual_fp = _safe_canon_sha256(fp_pre, out, "SV-061",
                                       f"selection_artifact.items[{i}]")
        if actual_fp is not None and \
                actual_fp != item.get("source_occurrence_fingerprint"):
            _v(out, "SV-061",
               f"selection_artifact.items[{i}].source_occurrence_fingerprint",
               f"source_occurrence_fingerprint "
               f"{item.get('source_occurrence_fingerprint')} != recomputed "
               f"{actual_fp}")


def check_sv070(artifacts, out):
    for cid, stim in (artifacts.get("stimulus_objects") or {}).items():
        def scan(obj, path):
            if isinstance(obj, dict):
                for k in obj:
                    if k in LEAKAGE_KEYS:
                        _v(out, "SV-070", path + "." + k,
                           f"forbidden provenance field {k!r} inside the "
                           f"exported stimulus")
        _walk(stim, f"stimulus_objects[{cid!r}]", scan)


def check_sv071(artifacts, out):
    for cid, stim in (artifacts.get("stimulus_objects") or {}).items():
        path = f"stimulus_objects[{cid!r}]"
        actual = _sha256_utf8(stim.get("codebook_content", ""))
        if actual != stim.get("codebook_sha256"):
            _v(out, "SV-071", f"{path}.codebook_sha256",
               f"codebook_sha256 {stim.get('codebook_sha256')} != "
               f"SHA256(codebook_content) {actual}")
        idxs = [c.get("idx") for c in stim.get("atomic_claims") or []]
        if idxs != list(range(len(idxs))):
            _v(out, "SV-071", f"{path}.atomic_claims",
               f"atomic-claim idx {idxs} not contiguous+unique 0..n-1")
        labels = stim.get("label_space") or []
        if len(set(labels)) != len(labels):
            _v(out, "SV-071", f"{path}.label_space", "label_space not unique")
        qids = [q.get("id") for q in
                (stim.get("worksheet_schema") or {}).get("questions") or []]
        if len(set(qids)) != len(qids):
            _v(out, "SV-071", f"{path}.worksheet_schema",
               "worksheet question ids not unique")


def check_sv072(artifacts, out):
    stimuli = artifacts.get("stimulus_objects")
    if stimuli is None:
        return
    config = artifacts.get("config") or {}
    records = {r.get("item_key"): (i, r)
               for i, r in enumerate(artifacts.get("review_records") or [])}
    for cid, stim in stimuli.items():
        if config and stim.get("codebook_sha256") != config.get("codebook_sha256"):
            _v(out, "SV-072", f"stimulus_objects[{cid!r}].codebook_sha256",
               f"stimulus codebook_sha256 {stim.get('codebook_sha256')} != "
               f"CONFIG codebook_sha256 {config.get('codebook_sha256')} — "
               f"breaks label reuse (Decision 1)")
        entry = records.get(cid)
        if entry is None:
            continue
        i, rec = entry
        if rec.get("stimulus_sha256") is None:
            continue
        actual = _safe_canon_sha256(stim, out, "SV-072",
                                    f"review_records[{i}].stimulus_sha256")
        if actual is not None and actual != rec.get("stimulus_sha256"):
            _v(out, "SV-072", f"review_records[{i}].stimulus_sha256",
               f"record stimulus_sha256 {rec.get('stimulus_sha256')} != "
               f"recomputed canon_sha256(stimulus_object) {actual} — "
               f"RECOMPUTED, never trusted")


def _check_headers(headers, path, trusted, out, allowlist=True):
    for name in headers or {}:
        low = name.lower()
        if low in trusted.credential_denylist:
            _v(out, "SV-090", f"{path}.{name}",
               f"credential-bearing header {name!r} must never be persisted")
        elif allowlist and low not in trusted.response_headers:
            _v(out, "SV-090", f"{path}.{name}",
               f"header {name!r} not in the pinned case-normalized allowlist")


def check_sv090(artifacts, trusted, out):
    for i, rec in enumerate(artifacts.get("review_records") or []):
        ex = rec.get("extract") or {}
        _check_headers(ex.get("response_headers_allowlisted"),
                       f"review_records[{i}].extract.response_headers_allowlisted",
                       trusted, out)
        for j, ci in enumerate(rec.get("coverage") or []):
            call = ci.get("call") or {}
            _check_headers(call.get("response_headers_allowlisted"),
                           f"review_records[{i}].coverage[{j}].call."
                           f"response_headers_allowlisted", trusted, out)
    for i, ev in enumerate(artifacts.get("wal_events") or []):
        _check_headers(ev.get("response_headers_allowlisted"),
                       f"wal_events[{i}].response_headers_allowlisted",
                       trusted, out)
    config = artifacts.get("config")
    if config:
        for stage, cfg in (config.get("stages") or {}).items():
            # Request-side behavior headers: credential denylist only (the
            # response allowlist is response-scoped; auth is added by the
            # transport AFTER hashing and never persisted).
            _check_headers((cfg.get("endpoint") or {}).get("behavior_headers"),
                           f"config.stages.{stage}.endpoint.behavior_headers",
                           trusted, out, allowlist=False)


def check_sv091(artifacts, trusted, out):
    config = artifacts.get("config")
    if not config:
        return
    for stage, cfg in (config.get("stages") or {}).items():
        endpoint = cfg.get("endpoint") or {}
        base_url = endpoint.get("base_url", "")
        host = urlsplit(base_url).hostname
        # host_allowlisted is RECOMPUTED from the pinned out-of-band list,
        # never trusted from the CONFIG.
        if host not in trusted.endpoint_hosts:
            _v(out, "SV-091", f"config.stages.{stage}.endpoint.base_url",
               f"endpoint host {host!r} not in the out-of-band pinned "
               f"allowlist {list(trusted.endpoint_hosts)}; recomputed "
               f"host_allowlisted=false")


_TIMESTAMP_KEYS = frozenset({"recorded_at", "started", "completed"})
_CAS_REF_KEYS = frozenset({"raw_response_body_ref", "response_body_ref",
                           "evidence_snapshot_ref"})


def _iter_all_artifacts(artifacts):
    for key in ARTIFACT_KEYS:
        val = artifacts.get(key)
        if val is None or key in ("selection_artifact_bytes",
                                  "evidence_snapshots", "source_xml",
                                  "runtime_templates", "response_schemas",
                                  "config_hash", "run_hash",
                                  "reportability_claims"):
            continue
        if key in ("stimulus_objects",):
            for cid, obj in val.items():
                yield f"{key}[{cid!r}]", obj
        elif isinstance(val, list):
            for i, obj in enumerate(val):
                yield f"{key}[{i}]", obj
        else:
            yield key, val


def check_sv100(artifacts, trusted, out):
    grammar = re.compile(trusted.cas_ref_grammar)

    def scan(obj, path):
        if isinstance(obj, bool):
            return
        if isinstance(obj, int):
            if abs(obj) > I_JSON_MAX:
                _v(out, "SV-100", path,
                   f"integer {obj} outside the I-JSON safe range")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in _TIMESTAMP_KEYS and isinstance(v, str):
                    if not _rfc3339_ok(v):
                        _v(out, "SV-100", f"{path}.{k}",
                           f"timestamp {v!r} is not semantically valid "
                           f"RFC 3339")
                if k in _CAS_REF_KEYS and isinstance(v, str):
                    parts = v.split("/")
                    if (v.startswith("/") or ".." in parts or "." in parts
                            or "\\" in v or not v.startswith(trusted.cas_root)
                            or not grammar.match(v)):
                        _v(out, "SV-100", f"{path}.{k}",
                           f"CAS reference {v!r} violates the pinned grammar "
                           f"{trusted.cas_ref_grammar!r} / storage-root "
                           f"confinement under {trusted.cas_root!r}")

    for path, obj in _iter_all_artifacts(artifacts):
        _walk(obj, path, lambda o, p: scan(o, p))


def check_sv101(artifacts, out):
    config = artifacts.get("config")
    if not config:
        return
    for stage, cfg in (config.get("stages") or {}).items():
        retry = cfg.get("retry") or {}
        path = f"config.stages.{stage}.retry"
        try:
            connect = Decimal(retry["connect_timeout_seconds"])
            read = Decimal(retry["read_timeout_seconds"])
            total = Decimal(retry["total_timeout_seconds"])
            base = Decimal(retry["backoff_base_seconds"])
            cap = Decimal(retry["backoff_cap_seconds"])
        except (KeyError, ArithmeticError, TypeError) as e:
            _v(out, "SV-101", path, f"retry policy unreadable: {e!r}")
            continue
        if total < max(connect, read):
            _v(out, "SV-101", f"{path}.total_timeout_seconds",
               f"total_timeout_seconds {total} < max(connect {connect}, "
               f"read {read})")
        if cap < base:
            _v(out, "SV-101", f"{path}.backoff_cap_seconds",
               f"backoff_cap_seconds {cap} < backoff_base_seconds {base}")
        # retry_after_cap_seconds participates only when respect_retry_after;
        # with respect_retry_after false it is deliberately unconstrained.


def check_sv110(artifacts, out):
    """Artifact-side SV-110 checks. The rule is a RUNTIME GATE: the byte
    verification and fresh-interpreter enforcement are evidenced by the
    bootstrap's subprocess fixtures, not by a post-hoc artifact audit —
    what IS auditable on artifacts is checked here."""
    mm = artifacts.get("module_manifest")
    if mm is not None:
        roles = set()
        seen = set()
        path_hash = {}
        for i, m in enumerate(mm.get("modules") or []):
            role = m.get("role")
            roles.add(role)
            try:
                norm = bootstrap.normalize_repo_path(m.get("repo_path"))
            except bootstrap.BootstrapError as e:
                _v(out, "SV-110", f"module_manifest.modules[{i}].repo_path",
                   str(e))
                continue
            key = (role, norm)
            if key in seen:
                _v(out, "SV-110", f"module_manifest.modules[{i}]",
                   f"duplicate (role, repo_path) {key!r}")
            seen.add(key)
            prior = path_hash.get(norm)
            if prior is not None and prior != m.get("content_sha256"):
                _v(out, "SV-110", f"module_manifest.modules[{i}]",
                   f"path {norm!r} listed with differing content hashes")
            path_hash[norm] = m.get("content_sha256")
        missing = [r for r in bootstrap.TRUST_BOUNDARY_ROLES if r not in roles]
        if missing:
            _v(out, "SV-110", "module_manifest.modules",
               f"module manifest role coverage not exhaustive; missing "
               f"{missing}")
        config = artifacts.get("config")
        if config is not None:
            # CONFIG pins module_manifest_sha256 (§11) — recompute, never
            # trust: this is the artifact the bootstrap verifies real module
            # bytes against, so a silent mismatch here means the artifact
            # record and the runtime gate disagree.
            actual = _safe_canon_sha256(mm, out, "SV-110", "module_manifest")
            if actual is not None and \
                    config.get("module_manifest_sha256") != actual:
                _v(out, "SV-110", "config.module_manifest_sha256",
                   f"CONFIG pins module_manifest_sha256 "
                   f"{config.get('module_manifest_sha256')} but the committed "
                   f"module manifest recomputes to {actual}")
    batch = artifacts.get("batch")
    if batch:
        observed = batch.get("observed_runtime") or {}
        if observed.get("fresh_interpreter") is not True:
            _v(out, "SV-110", "batch.observed_runtime.fresh_interpreter",
               "BATCH does not record fresh_interpreter=true")


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------

def validate(artifacts, repo_ctx=None, trusted=None):
    """Run every §12 rule over the supplied artifact universe.

    Always returns list[Violation], never an exception: a non-dict input
    yields a single SV-000/E_INPUT violation (the instrument's own input
    guard, not a §12 rule); a canonicalization/strict-load failure inside a
    rule is that rule's violation (an artifact that cannot be canonicalized
    violates the canon_v1 contract); any other error a malformed artifact
    provokes inside a rule aborts THAT rule fail-closed as its violation —
    the zero-violation positive fixtures keep this from masking real
    implementation bugs.
    """
    if not isinstance(artifacts, dict):
        return [Violation("SV-000", "E_INPUT", "<artifacts>",
                          f"artifacts must be a dict keyed by ARTIFACT_KEYS; "
                          f"got {type(artifacts).__name__} — fail closed, "
                          f"nothing validated")]
    if trusted is None:
        trusted = TrustedConstants()
    out = []
    dispatch = [
        ("SV-001", lambda: check_sv001(artifacts, out)),
        ("SV-002", lambda: check_sv002(artifacts, out)),
        ("SV-003", lambda: check_sv003(artifacts, out)),
        ("SV-005", lambda: check_sv005(artifacts, out)),
        ("SV-010", lambda: check_sv010(artifacts, out)),
        ("SV-011", lambda: check_sv011(artifacts, out)),
        ("SV-020", lambda: check_sv020(artifacts, out)),
        ("SV-021", lambda: check_sv021(artifacts, out)),
        ("SV-022", lambda: check_sv022(artifacts, out)),
        ("SV-023", lambda: check_sv023(artifacts, out)),
        ("SV-024", lambda: check_sv024(artifacts, out)),
        ("SV-025", lambda: check_sv025(artifacts, out)),
        ("SV-026", lambda: check_sv026(artifacts, out)),
        ("SV-030", lambda: check_sv030(artifacts, out)),
        ("SV-031", lambda: check_sv031(artifacts, out)),
        ("SV-032", lambda: check_sv032(artifacts, out)),
        ("SV-033", lambda: check_sv033(artifacts, out)),
        ("SV-034", lambda: check_sv034(artifacts, out)),
        ("SV-040", lambda: check_sv040(artifacts, out)),
        ("SV-041", lambda: check_sv041(artifacts, repo_ctx, out)),
        ("SV-042", lambda: check_sv042(artifacts, repo_ctx, trusted, out)),
        ("SV-043", lambda: check_sv043(artifacts, repo_ctx, trusted, out)),
        ("SV-044", lambda: check_sv044(artifacts, repo_ctx, out)),
        ("SV-045", lambda: check_sv045(artifacts, out)),
        ("SV-050", lambda: check_sv050(artifacts, trusted, out)),
        ("SV-060", lambda: check_sv060(artifacts, out)),
        ("SV-061", lambda: check_sv061(artifacts, out)),
        ("SV-070", lambda: check_sv070(artifacts, out)),
        ("SV-071", lambda: check_sv071(artifacts, out)),
        ("SV-072", lambda: check_sv072(artifacts, out)),
        ("SV-090", lambda: check_sv090(artifacts, trusted, out)),
        ("SV-091", lambda: check_sv091(artifacts, trusted, out)),
        ("SV-100", lambda: check_sv100(artifacts, trusted, out)),
        ("SV-101", lambda: check_sv101(artifacts, out)),
        ("SV-110", lambda: check_sv110(artifacts, out)),
    ]
    for rule_id, run in dispatch:
        try:
            run()
        except (CanonV1Error, StrictLoadError) as e:
            _v(out, rule_id, "<canonicalization>",
               f"rule aborted by canonicalization/strict-load failure "
               f"(fail closed): {e}")
        except Exception as e:
            _v(out, rule_id, "<malformed-input>",
               f"rule aborted by malformed input (fail closed): {e!r}")
    return out
