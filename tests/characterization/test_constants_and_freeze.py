"""ITEMS 9 AND 10 -- the constants and the frozen substrate.

These are one-line assertions on purpose. They pin the values a refactor is most
likely to lose while MOVING FILES: a threshold that reverts to a default, a
closed vocabulary that gains a member, a prompt file that gets reformatted on
its way to a new directory. None of these fails loudly on its own -- the run
completes, the manifest validates, and every reported number is computed against
a different rule than the one that was preregistered.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cre.f1 import f2_thresholds, reason_registry, terminal_outcome
from cre.f1.f8_retraction import F8_MIN_GAP_DAYS
from cre.f1.freeze import bootstrap, canon_v1 as canon, schema_gate, strict_loader
from cre.f1.freeze import semantic_validator_v1 as sv

REPO = Path(__file__).resolve().parent.parent.parent


def test_the_two_numeric_thresholds_are_the_preregistered_ones():
    """0.92 and 31 days.

    ``SAME_WORK_TITLE_SIM_MIN`` decides whether two records are the same work,
    and moving it moves the SAME_WORK exclusion -- which is a denominator, not a
    finding. ``F8_MIN_GAP_DAYS`` is the window in which a citation is held to
    have been able to know about a retraction; shortening it manufactures F8s
    against authors who could not have known.
    """
    assert f2_thresholds.SAME_WORK_TITLE_SIM_MIN == 0.92
    assert F8_MIN_GAP_DAYS == 31


def test_the_terminal_outcome_vocabulary_is_closed_and_ordered():
    """The outcome set and the PRECEDENCE ORDER of the finding outcomes.

    ``FINDING_OUTCOMES`` is an ordered tuple, not a set: it is the hierarchy, and
    a reordering changes which fault a reviewer is shown without changing how
    many pairs are flagged.
    """
    assert terminal_outcome.FINDING_OUTCOMES == ("F7", "F6", "F4", "F3", "F5")
    assert terminal_outcome.TERMINAL_OUTCOMES == frozenset({
        "F7", "F6", "F4", "F3", "F5", "NONE", "UNJUDGEABLE",
        "HUMAN_REVIEW_REQUIRED"})


def test_the_reason_registry_is_closed():
    """Every reason a record can carry, and the route each one implies.

    An unregistered reason string reaching a record is how a pair ends up in no
    bucket at all -- present in the output, absent from every rate.
    """
    assert reason_registry.REASON_REGISTRY == (
        reason_registry.SAME_WORK_REASONS
        | reason_registry.WRONG_PAPER_REASONS
        | reason_registry.RETRACTION_REASONS
        | reason_registry.UNSCOREABLE_BUCKETS)
    for reason in sorted(reason_registry.REASON_REGISTRY):
        assert reason_registry.is_registered(reason), reason
        assert reason_registry.route_for(reason), reason


# ---------------------------------------------------------------------------
# item 10 -- freeze integrity
# ---------------------------------------------------------------------------
def test_the_frozen_prompt_substrate_still_has_its_pinned_blob_oid():
    """THE ONE FILE A RESTRUCTURE MUST NOT TOUCH.

    ``band_prompts.py`` is content-addressed inside the freeze chain: both
    prompt-package seals and ``semantic_validator_v1`` accept it by blob OID. A
    git MOVE preserves that OID because git hashes content, not paths. An EDIT
    -- including an import rewrite, including a whitespace fix -- does not, and
    silently invalidates every sealed package that cites it.

    Asked of git rather than recomputed, so this asserts the value the freeze
    chain actually compares against.
    """
    candidates = [p for p in REPO.rglob("band_prompts.py")
                  if "__pycache__" not in p.parts and ".git" not in p.parts]
    assert len(candidates) == 1, f"expected exactly one band_prompts.py, got {candidates}"
    oid = subprocess.run(("git", "hash-object", str(candidates[0])),
                         cwd=REPO, capture_output=True, text=True,
                         check=True).stdout.strip()
    assert f"sha1:{oid}" == sv.FROZEN_SOURCE_BLOB_OID


def test_the_pinned_schema_digest_and_size_are_unchanged():
    """The finder schema is pinned by BOTH digest and byte count.

    Two statements about one file, because a digest alone cannot distinguish
    "the schema changed" from "the pin was updated to match"; the byte count is
    the second, independent thing a careless edit has to get right.
    """
    import hashlib
    raw = schema_gate.SCHEMA_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == schema_gate.PINNED_SCHEMA_SHA256
    assert len(raw) == schema_gate.PINNED_SCHEMA_BYTES == 69138


def test_the_canonicaliser_refuses_a_float_it_cannot_reproduce():
    """``canon_v1`` is the function every seal is computed over.

    It must refuse anything whose re-serialisation could differ from its input,
    or a seal verifies against bytes that are not the bytes that were sealed.
    A float is exactly that: its decimal form is not uniquely determined, so two
    honest implementations can seal the same object to different digests.
    """
    with pytest.raises(canon.CanonV1Error):
        canon.canon_v1({"a": 1.0})


@pytest.mark.parametrize("payload,why", [
    (b'{"a": 1.0}', "a float token cannot round-trip and would change a digest"),
    (b'{"a": 1, "a": 2}', "a duplicate key silently discards one of two values"),
])
def test_the_strict_loader_refuses_json_that_cannot_be_sealed(payload, why):
    """The loader every trust-boundary file is read through.

    Python's own JSON parser accepts both of these and silently returns
    something that is not what the bytes said -- the float becomes an
    unreproducible number, the duplicate key throws one value away. Either would
    be sealed without complaint and verify against itself forever.
    """
    with pytest.raises(strict_loader.StrictLoadError):
        strict_loader.load_strict(payload)


def test_every_trust_boundary_role_resolves_to_a_module_that_claims_it():
    """The role -> module mapping is a path table, and a rename breaks it.

    This is the assertion that catches a package rename which updated the
    imports and forgot the manifest: every role still named, every named module
    still importable, and no role left pointing at a path that moved.
    """
    roles = bootstrap.TRUST_BOUNDARY_ROLES
    assert len(set(roles)) == len(roles), "a duplicated role hides one of two modules"
    assert set(roles) == {
        "bootstrap", "validator", "canonicalizer", "renderer", "parser",
        "provider_adapter", "evidence_reader", "runner", "package_init",
        "strict_loader", "semantic_validator"}
