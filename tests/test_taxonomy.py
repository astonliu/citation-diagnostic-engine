"""Tests for ``cde.taxonomy`` -- the code-to-name table and the two helpers.

The guarantee under test is the split this project depends on: **codes on the
wire, names on the surfaces a person reads.** So these assert two different
things. First, that the table is exactly the label space
``cde.refs.schema.TAXONOMY_LABELS`` defines -- no missing category, no stray
key, no two categories sharing a name. Second, that :func:`annotate` appends a
name only where a real code is present, and leaves everything else byte-identical,
because the ``report`` output has to stay greppable against the record file it
summarises.

The last point is where the bugs were. A ``\\b``-bounded pattern fails twice:
``\\bF6\\b`` does not match ``F6_FLAGGED``, since ``_`` is a word character, and
it does match the ``F1`` inside ``SHA256_F1A``, annotating a digest fragment as a
category. Both cases have a test below.

Run:  PYTHONPATH=<repo> python -m pytest tests/test_taxonomy.py -q
"""
from __future__ import annotations

from cde.refs.schema import TAXONOMY_LABELS
from cde.taxonomy import CATEGORY_NAMES, PRECEDENCE, annotate, category_name


# ======================================================================
# 1. The table matches the label space, exactly.
# ======================================================================
def test_every_taxonomy_label_has_a_name():
    assert TAXONOMY_LABELS - set(CATEGORY_NAMES) == set()


def test_no_name_exists_for_a_label_outside_the_space():
    assert set(CATEGORY_NAMES) - TAXONOMY_LABELS == set()


def test_names_are_distinct():
    """Two categories sharing a name would make an annotated key ambiguous."""
    assert len(set(CATEGORY_NAMES.values())) == len(CATEGORY_NAMES) == 9


def test_accurate_key_is_the_literal_wire_value():
    """Lowercase, because ``schema.ACCURATE`` is. A capitalised key just misses."""
    assert "accurate" in CATEGORY_NAMES
    assert "Accurate" not in CATEGORY_NAMES
    assert CATEGORY_NAMES["accurate"] == "Accurate"


# ======================================================================
# 2. Precedence: the eight faults, in order, and nothing else.
# ======================================================================
def test_precedence_is_the_eight_fault_names():
    assert PRECEDENCE == (
        "Unresolvable Reference",
        "Wrong Reference",
        "Retracted Source",
        "Wrong Entity",
        "Insufficient Support",
        "Overstatement",
        "Misattribution",
        "Supersession",
    )


def test_precedence_excludes_accurate():
    """ACCURATE is the absence of a finding, so it cannot sit in a fault order."""
    assert "Accurate" not in PRECEDENCE
    assert set(PRECEDENCE) == set(CATEGORY_NAMES.values()) - {"Accurate"}
    assert len(PRECEDENCE) == len(set(PRECEDENCE)) == 8


# ======================================================================
# 3. annotate: appends on a real code, otherwise byte-identical.
# ======================================================================
def test_annotate_route_key_keeps_the_code_and_appends_the_name():
    assert annotate("F6_FLAGGED") == "F6_FLAGGED  (Insufficient Support)"


def test_annotate_bare_code():
    assert annotate("F6") == "F6  (Insufficient Support)"


def test_annotate_leaves_a_key_with_no_code_alone():
    assert annotate("FULL_COVERAGE") == "FULL_COVERAGE"


def test_annotate_leaves_an_out_of_range_code_alone():
    """F9 is not a category, so nothing may be claimed about it."""
    assert annotate("F9_UNKNOWN") == "F9_UNKNOWN"


def test_annotate_leaves_a_digest_fragment_alone():
    """The bound is on digits too; ``\\b`` would annotate this as F1."""
    assert annotate("SHA256_F1A") == "SHA256_F1A"


def test_annotate_covers_every_code():
    for code, name in CATEGORY_NAMES.items():
        if code == "accurate":
            continue
        assert annotate(code) == f"{code}  ({name})"
        assert annotate(f"{code}_FLAGGED") == f"{code}_FLAGGED  ({name})"


# ======================================================================
# 4. category_name: a name for a category, the input back for anything else.
# ======================================================================
def test_category_name_maps_a_code():
    assert category_name("F6") == "Insufficient Support"


def test_category_name_returns_a_non_category_unchanged():
    assert category_name("NO_CLAIMS") == "NO_CLAIMS"


def test_category_name_never_raises_on_pipeline_values():
    """Route names, dispositions and pipeline states are normal input here."""
    for value in ("cleared", "unverifiable", "human_review", "same_work",
                  "unscoreable", "F6_FLAGGED", "HELD_LOW_CONFIDENCE", ""):
        assert category_name(value) == value


# ======================================================================
# 5. The wire values are NOT renamed. This is the guardrail, as a test.
# ======================================================================
def test_taxonomy_labels_still_hold_the_short_codes():
    assert TAXONOMY_LABELS == {"accurate", "F1", "F2", "F3", "F4", "F5", "F6",
                               "F7", "F8"}


def test_taxonomy_module_imports_no_cde_subpackage():
    """Every subpackage imports this one, so it has to stay dependency-free.

    Read off the parsed import statements, not the file text: prose in the
    docstring names those subpackages on purpose, and a substring check would
    fail on the explanation rather than on a real import.
    """
    import ast
    import pathlib
    import cde.taxonomy

    tree = ast.parse(pathlib.Path(cde.taxonomy.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported == {"__future__", "re"}, imported
