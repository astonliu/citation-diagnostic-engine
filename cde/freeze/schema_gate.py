"""schema_gate — fail-closed loader/validator for the pinned freeze schema.

Loads the pinned F3-F7_FINDER_FREEZE_SCHEMAS.json via strict_loader (duplicate
keys / float tokens rejected pre-parse), verifies its SHA-256 over the exact
stored bytes equals the pin, then Draft 2020-12-validates artifacts at root
(discriminated oneOf on artifact_type). Wrong schema bytes -> fail closed,
zero side effects (no validator is constructed, nothing is cached).

PIN AUTHORITY: the supplied schema (v14) was pinned b42fae74... (67,897 B) and
was verified byte-for-byte at copy time. The first build commit applied the
review-round residual schema deltas (#3 stratum, #5 response_schema_sha256,
#10 source_commit_oid type); from that commit the committed repo copy is the
pin authority (build spec, "Review-round residuals") and this is its hash:
"""
import hashlib
import pathlib

from cde.freeze.strict_loader import load_strict

PINNED_SCHEMA_SHA256 = "0f30f8cb2046a505d9266d74ff78aa01fc74f0c433955280a67f8371915a7d94"
PINNED_SCHEMA_BYTES = 69286
SCHEMA_FILENAME = "F3-F7_FINDER_FREEZE_SCHEMAS.json"
SCHEMA_PATH = pathlib.Path(__file__).resolve().parent / SCHEMA_FILENAME


class SchemaGateError(Exception):
    """Fail-closed schema-gate violation; nothing was validated."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


_cache = {}


def load_pinned_schema(path=None):
    """Return the parsed pinned schema after byte verification, else raise."""
    p = pathlib.Path(path) if path is not None else SCHEMA_PATH
    raw = p.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    if actual != PINNED_SCHEMA_SHA256:
        raise SchemaGateError(
            "E_SCHEMA_PIN",
            f"schema bytes at {p} hash {actual}, pinned "
            f"{PINNED_SCHEMA_SHA256} — failing closed, nothing validated")
    return load_strict(raw)


def get_validator(path=None):
    """Draft 2020-12 validator over the pinned schema (cached after the pin check)."""
    key = str(path) if path is not None else str(SCHEMA_PATH)
    v = _cache.get(key)
    if v is None:
        schema = load_pinned_schema(path)
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(schema)
        v = Draft202012Validator(schema)
        _cache[key] = v
    return v


def schema_errors(artifact, path=None):
    """Validate an artifact at schema root; return error messages (empty = valid)."""
    v = get_validator(path)
    return [f"{'/'.join(str(x) for x in e.absolute_path) or '<root>'}: {e.message}"
            for e in v.iter_errors(artifact)]


def is_schema_valid(artifact, path=None):
    return not schema_errors(artifact, path)
