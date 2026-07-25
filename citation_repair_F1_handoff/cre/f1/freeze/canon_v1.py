"""canon_v1 — RFC 8785 / JCS canonicalization for the F3-F7 finder freeze.

Vocabulary rule 4 (freeze spec v17): `canon_v1` = RFC 8785 / JCS with floats
PROHIBITED (real quantities travel as decimal strings), duplicate keys / NaN /
Infinity rejected, native JSON booleans, and the I-JSON safe integer range
enforced. Because floats are prohibited, RFC 8785's ES-number serialization
reduces to integer decimal form; everything else (UTF-16 code-unit key sort,
minimal string escaping, no insignificant whitespace, UTF-8 bytes) is full JCS.

This is deliberately NOT the legacy `_canonical_sha256` (json.dumps-based,
float-permitting); legacy code is untouched and legacy chains are rejected or
bridged via `legacy_tip_anchor` (freeze spec §3).

stdlib-only.
"""
import hashlib

I_JSON_MAX = 9007199254740991  # 2**53 - 1
I_JSON_MIN = -9007199254740991


class CanonV1Error(ValueError):
    """Typed canonicalization failure; `code` names the exact violation."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


# RFC 8785 §3.2.2.2: two-character escapes for these controls, \u00xx otherwise.
_SHORT_ESCAPES = {
    0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r",
    0x22: '\\"', 0x5C: "\\\\",
}


def _serialize_string(s):
    out = ['"']
    for ch in s:
        cp = ord(ch)
        esc = _SHORT_ESCAPES.get(cp)
        if esc is not None:
            out.append(esc)
        elif cp < 0x20:
            out.append(f"\\u{cp:04x}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _serialize(obj, out):
    if obj is None:
        out.append("null")
    elif obj is True:
        out.append("true")
    elif obj is False:
        out.append("false")
    elif isinstance(obj, float):
        raise CanonV1Error("E_FLOAT", "floats are prohibited under canon_v1; "
                           "real quantities must be decimal strings")
    elif isinstance(obj, int):
        if obj > I_JSON_MAX or obj < I_JSON_MIN:
            raise CanonV1Error("E_INT_RANGE",
                               f"integer {obj} outside I-JSON safe range")
        out.append(str(obj))
    elif isinstance(obj, str):
        out.append(_serialize_string(obj))
    elif isinstance(obj, (list, tuple)):
        out.append("[")
        for i, v in enumerate(obj):
            if i:
                out.append(",")
            _serialize(v, out)
        out.append("]")
    elif isinstance(obj, dict):
        keys = []
        for k in obj:
            if not isinstance(k, str):
                raise CanonV1Error("E_KEY_TYPE",
                                   f"object key {k!r} is not a string")
            keys.append(k)
        # RFC 8785 §3.2.3: sort property names by UTF-16 code units.
        try:
            keys.sort(key=lambda k: k.encode("utf-16-be", "strict"))
        except UnicodeEncodeError as e:
            raise CanonV1Error("E_ENCODING", f"unencodable object key: {e}")
        out.append("{")
        for i, k in enumerate(keys):
            if i:
                out.append(",")
            out.append(_serialize_string(k))
            out.append(":")
            _serialize(obj[k], out)
        out.append("}")
    else:
        raise CanonV1Error("E_TYPE",
                           f"type {type(obj).__name__} not representable in canon_v1")


def canon_v1(obj):
    """Serialize `obj` to canonical RFC 8785 / JCS bytes (floats prohibited)."""
    out = []
    _serialize(obj, out)
    try:
        return "".join(out).encode("utf-8", "strict")
    except UnicodeEncodeError as e:
        raise CanonV1Error("E_ENCODING", f"string not strict UTF-8 encodable: {e}")


def canon_sha256(obj):
    """SHA-256 hex digest of canon_v1(obj) — the canonical-object hash (Vocab rule 4)."""
    return hashlib.sha256(canon_v1(obj)).hexdigest()
