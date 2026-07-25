"""strict_loader — strict byte loader for freeze artifacts.

Freeze spec v17 (Normative references): "A strict byte loader (reject duplicate
keys and any float token such as `1.0` *before* parsing)" is REQUIRED for every
freeze artifact read. Rejections happen before any parsed object is returned:

  - duplicate keys at ANY depth (via json's object_pairs_hook, which fires per
    object during lexing, before the document object is constructed)
  - any float token: `1.0`, `1e3`, `NaN`, `Infinity`, `-Infinity` (parse_float
    and parse_constant hooks fire on the token, before value construction)
  - bytes that are not strict UTF-8

Raises typed StrictLoadError(code=...). stdlib-only.
"""
import json
import pathlib

E_DUPLICATE_KEY = "E_DUPLICATE_KEY"
E_FLOAT_TOKEN = "E_FLOAT_TOKEN"
E_ENCODING = "E_ENCODING"
E_PARSE = "E_PARSE"


class StrictLoadError(ValueError):
    """Typed strict-load failure; `code` names the exact violation."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def _reject_float(token):
    raise StrictLoadError(E_FLOAT_TOKEN, f"float token {token!r} rejected pre-parse")


def _reject_constant(token):
    raise StrictLoadError(E_FLOAT_TOKEN, f"non-finite token {token!r} rejected pre-parse")


def _pairs_hook(pairs):
    seen = set()
    for k, _ in pairs:
        if k in seen:
            raise StrictLoadError(E_DUPLICATE_KEY, f"duplicate key {k!r}")
        seen.add(k)
    return dict(pairs)


def _assert_strict_strings(obj):
    """Reject escaped lone surrogates (\\ud800...) json.loads lets through —
    'every stored string declares strict-UTF-8' (Vocabulary rule 2)."""
    if isinstance(obj, str):
        try:
            obj.encode("utf-8", "strict")
        except UnicodeEncodeError as e:
            raise StrictLoadError(E_ENCODING,
                                  f"string is not strict UTF-8 (lone "
                                  f"surrogate?): {e}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _assert_strict_strings(k)
            _assert_strict_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            _assert_strict_strings(v)


def load_strict(path_or_bytes):
    """Load JSON from a filesystem path (str/Path) or raw bytes, strictly.

    Rejects duplicate keys at any depth and any float token before the parsed
    document is returned. Returns the parsed object.
    """
    if isinstance(path_or_bytes, (bytes, bytearray)):
        raw = bytes(path_or_bytes)
    elif isinstance(path_or_bytes, (str, pathlib.Path)):
        raw = pathlib.Path(path_or_bytes).read_bytes()
    else:
        raise StrictLoadError(E_PARSE,
                              f"unsupported input type {type(path_or_bytes).__name__}")
    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError as e:
        raise StrictLoadError(E_ENCODING, f"input is not strict UTF-8: {e}")
    try:
        parsed = json.loads(text,
                            object_pairs_hook=_pairs_hook,
                            parse_float=_reject_float,
                            parse_constant=_reject_constant)
    except StrictLoadError:
        raise
    except ValueError as e:
        raise StrictLoadError(E_PARSE, f"invalid JSON: {e}")
    _assert_strict_strings(parsed)
    return parsed
