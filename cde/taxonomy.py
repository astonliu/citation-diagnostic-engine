"""The one place a taxonomy code is tied to the name a person reads.

WHY THIS FILE EXISTS. The records this project writes speak codes: ``F1``
through ``F8`` and ``accurate``. Those strings are data. They are written into
every prediction file, they are what the freeze artifacts hash, and
``doc/preregistration.md`` is written in them, so renaming one on the wire
would invalidate work that is already finished. The paper, however, speaks
names -- Insufficient Support, not F6 -- and a reader running the tool should
see the same vocabulary the paper uses.

So the split is: **codes on the wire, names on the surfaces a person reads.**
This module owns the mapping between the two and nothing else. It makes no
decision, reads no record and imports nothing from ``cde.refs``,
``cde.claims`` or ``cde.diagnose`` -- every one of those imports this, so it
has to stay dependency-free.

The authority for the mapping is ``doc/taxonomy.md``; this table is the same
table in code, and the test suite asserts it against
``cde.refs.schema.TAXONOMY_LABELS`` so the two cannot drift apart silently.
"""
from __future__ import annotations

import re

#: Code -> the name used in the paper and on every read surface.
#:
#: ``accurate`` is lowercase because that is the literal value
#: ``cde.refs.schema.ACCURATE`` defines. A capitalised key here would simply
#: never match, and would do it silently.
CATEGORY_NAMES: dict[str, str] = {
    "F1": "Unresolvable Reference",
    "F2": "Wrong Reference",
    "F3": "Misattribution",
    "F4": "Overstatement",
    "F5": "Supersession",
    "F6": "Insufficient Support",
    "F7": "Wrong Entity",
    "F8": "Retracted Source",
    "accurate": "Accurate",
}

#: The eight fault names in first-failure order, per ``doc/taxonomy.md``.
#: ``Accurate`` is deliberately absent: it is not a fault and so has no place
#: in a precedence order over faults.
PRECEDENCE: tuple[str, ...] = (
    "Unresolvable Reference",
    "Wrong Reference",
    "Retracted Source",
    "Wrong Entity",
    "Insufficient Support",
    "Overstatement",
    "Misattribution",
    "Supersession",
)

#: Matches a taxonomy code embedded in a larger key.
#:
#: The bounds are on letters AND digits, not ``\b``. A ``\b``-bounded pattern
#: is wrong twice over: ``_`` is a word character, so ``\bF6\b`` does not match
#: ``F6_FLAGGED``, the exact case this exists for; and ``\b`` would let
#: ``SHA256_F1A`` match, annotating a digest fragment as a category. Both were
#: caught by tests rather than by reading, which is why they are written down.
_CODE_RE = re.compile(r"(?<![A-Za-z0-9])F[1-8](?![A-Za-z0-9])")

#: Two spaces before the parenthesis, so an annotated key still reads as one
#: column rather than as a sentence.
_SEPARATOR = "  "


def category_name(label: str) -> str:
    """Return the reader-facing name for ``label``, or ``label`` unchanged.

    Anything that is not a taxonomy code comes back untouched. That is the
    contract, not a fallback: route names, dispositions and pipeline states
    are normal input here, and this function must never raise on one.
    """
    return CATEGORY_NAMES.get(label, label)


def annotate(key: str) -> str:
    """Append the category name to a key that embeds a taxonomy code.

    ``F6_FLAGGED`` becomes ``F6_FLAGGED  (Insufficient Support)``. The
    original key is kept in full and never substituted: the ``report`` output
    has to stay greppable against the record file it summarises, and those
    records carry the code.

    A key with no code in it -- ``FULL_COVERAGE``, ``NO_CLAIMS`` -- comes back
    unchanged, as does an out-of-range lookalike such as ``F9_UNKNOWN`` or a
    digest fragment such as ``SHA256_F1A``.
    """
    match = _CODE_RE.search(key)
    if match is None:
        return key
    name = CATEGORY_NAMES.get(match.group(0))
    if name is None:  # unreachable while the regex and the table agree
        return key
    return f"{key}{_SEPARATOR}({name})"
