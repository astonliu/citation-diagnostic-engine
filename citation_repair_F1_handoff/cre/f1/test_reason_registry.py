"""§5.6 registry ⇄ code equality (the drift guard), + frozen-config + manifest.

The equality test is the one that keeps the spec from drifting behind the code:
it statically scans the emitter modules for reason-string literals and
asserts they equal REASON_REGISTRY exactly. It already caught one omission
(``translated_title_missing_volume_anchors``) in the rev-5.2 draft's §5.6 table.
"""
from __future__ import annotations

import inspect
import re

import pytest

from cre.f1 import (work_identity, biblio_match, unscoreable, decide,
                    reason_registry as rr)


def _emitted_reason_literals() -> set:
    """Every reason-code string literal the emitters can produce, scanned
    from source (so a renamed/added literal is detected without running rules)."""
    wi = inspect.getsource(work_identity)
    bm = inspect.getsource(biblio_match)
    un = inspect.getsource(unscoreable)
    de = inspect.getsource(decide)
    reasons = set()
    # work_identity: WorkIdentityEvidence(True, "reason", (...))
    reasons |= set(re.findall(r'WorkIdentityEvidence\(\s*True,\s*"([^"]+)"', wi))
    # biblio_match: m.same_work_reason = "reason"
    reasons |= set(re.findall(r'same_work_reason\s*=\s*"([^"]+)"', bm))
    # biblio_match, C5 (rev 5.4): the four repair reasons are assigned to
    # FieldAgreement.repair_reason at the point the repair happens, then emitted as
    # same_work_reason through a variable in flag_verdict. The literal never appears
    # next to `same_work_reason`, so the pattern above cannot see it -- scanning the
    # assignment site keeps the drift guard total over what the module can emit.
    reasons |= set(re.findall(r'repair_reason\s*=\s*"([^"]+)"', bm))
    # unscoreable: return ("bucket", ...)
    reasons |= set(re.findall(r'return\s*\(\s*"([a-z0-9_]+)"\s*,', un))
    # decide (rev 5.6): the F8 retraction route. Its reason code names the route
    # the row took, assigned where the branch is taken, so the drift guard covers
    # the fourth emitter the same way it covers the other three.
    reasons |= set(re.findall(r'retraction_reason\s*=\s*"([^"]+)"', de))
    return reasons


def test_registry_equals_emitted_literals_exactly():
    emitted = _emitted_reason_literals()
    assert emitted == set(rr.REASON_REGISTRY), (
        "§5.6 registry drift.\n"
        f"  in code but NOT registered: {sorted(emitted - set(rr.REASON_REGISTRY))}\n"
        f"  registered but NOT in code: {sorted(set(rr.REASON_REGISTRY) - emitted)}")


def test_registry_partitions_cleanly():
    # The four route groups are pairwise disjoint and cover the whole registry.
    groups = (rr.SAME_WORK_REASONS, rr.WRONG_PAPER_REASONS,
              rr.RETRACTION_REASONS, rr.UNSCOREABLE_BUCKETS)
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            assert a.isdisjoint(b), f"overlap: {sorted(a & b)}"
    union = set()
    for g in groups:
        union |= set(g)
    assert union == set(rr.REASON_REGISTRY)


def test_route_mapping_is_total_and_correct():
    assert set(rr.REASON_ROUTE) == set(rr.REASON_REGISTRY)
    assert rr.route_for("physical_location_same_work") == "review_same_work_variant"
    assert rr.route_for("resolved_preprint_target") == "review_wrong_paper"
    assert rr.route_for("retracted_publication") == "f8_retracted"
    assert rr.route_for("retracted_before_citation_31_day_floor_met") == \
        "f8_retracted"
    assert rr.route_for("single_word_title") == "unscoreable"
    assert rr.is_registered("near_identical_title")
    assert not rr.is_registered("not_a_real_reason")
    with pytest.raises(KeyError):
        rr.route_for("not_a_real_reason")


def test_missing_volume_anchors_is_registered():
    # The code the rev-5.2 draft's §5.6 table missed.
    assert "translated_title_missing_volume_anchors" in rr.REASON_REGISTRY


# --- frozen-configuration acceptance (Part B) --------------------------------
def test_journal_authority_is_empty_and_inert():
    from cre.f1.journal_identity import JOURNAL_AUTHORITY, resolve_journal_id
    assert JOURNAL_AUTHORITY.is_empty() is True
    for j in ("JAMA", "Blood", "Blood Adv"):
        assert resolve_journal_id(j) is None


def test_f2d_active_and_f2i_gated_off_in_current_config():
    from cre.f1.biblio_match import _F2D_STRICT_PREFIX_ENABLED
    from cre.f1.journal_identity import JOURNAL_AUTHORITY
    assert _F2D_STRICT_PREFIX_ENABLED is True             # F2-D review-only revival
    assert JOURNAL_AUTHORITY.is_empty() is True            # F2-I inert (§13)
    # The three inert codes are registered but must not be emitted while frozen.
    assert rr.NOT_EMITTED_IN_FROZEN_CONFIG <= set(rr.REASON_REGISTRY)


# --- C.2 manifest contradiction checks ---------------------------------------
def _frozen_manifest(**overrides):
    m = {
        "journal_authority_snapshot": "nlm_J_Medline.txt",
        "journal_authority_sha256": "1576d19a061e91db2237cddfca2aaa6c376c0850d466e2195753e1d35256efe1",
        "journal_authority_records": 37972,
        "journal_authority_retrieved_utc": "2026-07-26",
        "journal_authority_loaded": False,
        "journal_match_authoritative_rate": 0.0,
        "f2i_field_transposition_active": False,
        "f2d_strict_prefix_active": True,
        # Tracks REASON_REGISTRY_VERSION, bumped 5.2 -> 5.3 by the §15.2
        # version-chain code. This fixture's job is "a manifest AGREEING with the
        # loaded registry validates", which is version-independent; the STALE
        # case is asserted explicitly by
        # test_manifest_stale_registry_version_is_contradiction below (it had no
        # coverage while this literal was pinned -- the pin only ever proved the
        # agreeing case, and silently became the stale case on a bump).
        "reason_registry_version": rr.REASON_REGISTRY_VERSION,
    }
    m.update(overrides)
    return m


def test_manifest_frozen_config_validates():
    rr.validate_manifest(_frozen_manifest())          # no raise


def test_manifest_rate_without_loaded_is_contradiction():
    with pytest.raises(ValueError):
        rr.validate_manifest(_frozen_manifest(journal_match_authoritative_rate=0.044))


def test_manifest_active_f2d_flag_agrees_with_live_config():
    rr.validate_manifest(_frozen_manifest(f2d_strict_prefix_active=True))
    with pytest.raises(ValueError):
        rr.validate_manifest(_frozen_manifest(f2d_strict_prefix_active=False))
    with pytest.raises(ValueError):
        rr.validate_manifest(_frozen_manifest(f2i_field_transposition_active=True))


def test_manifest_loaded_true_while_authority_empty_is_contradiction():
    with pytest.raises(ValueError):
        rr.validate_manifest(_frozen_manifest(journal_authority_loaded=True))


def test_manifest_stale_registry_version_is_contradiction():
    """An artifact written under an OLDER reason vocabulary must not validate
    against the loaded one -- its rows can carry codes this registry no longer
    defines, or lack codes it now emits (§5.6 A.4)."""
    with pytest.raises(ValueError):
        rr.validate_manifest(_frozen_manifest(reason_registry_version="5.2"))


def test_manifest_missing_field_is_error():
    m = _frozen_manifest()
    del m["journal_authority_loaded"]
    with pytest.raises(ValueError):
        rr.validate_manifest(m)
