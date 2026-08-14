"""§5.6 reason-code registry -- the closed controlled vocabulary for the
``route_reason`` field of the machine contract (§19.1), plus the §2.3/C.2 manifest
contradiction checks.

Why this exists: ``route`` (§5.1), ``a_resolution_status`` (§5.2),
``a_b_identity_status`` (§5.3), and the per-provider retrieval statuses (§19.1)
were all enumerated in the spec; ``route_reason`` was the only controlled
vocabulary with no enumeration, so a schema author had nothing to close the enum
against and a rule could be renamed with no spec diff. This module is the single
machine-readable source of truth; ``test_reason_registry.py`` statically scans the
three emitter modules and asserts the emitted literals equal ``REASON_REGISTRY``
exactly, so the spec can never drift behind the code again.

The registry is CLOSED (§5.6 A.4): a new reason code requires a spec amendment in
the same commit that introduces it, and a code emitting an unregistered reason is
a schema failure, not a warning. Reason codes carry the spec revision that
introduced them; renaming is a breaking change to the artifact contract.
"""
from __future__ import annotations

REASON_REGISTRY_VERSION = "5.5"

# --- §5.6 A.1: same-work reasons -> route review_same_work_variant ------------
# Rev 5.5 activates strict-prefix review and adds the three corporate/DOI routes
# specified before the seed-47 rescore.  Earlier registry revisions added the
# missing-volume translation and version-chain reasons.
SAME_WORK_REASONS = frozenset({
    # work_identity.py
    "mixed_identity_citation",
    "overwhelming_bibliographic_anchor",
    "authoritative_title_alias",
    "canonical_title_exact",
    "malformed_title_wrapper",
    "translated_title_metadata",
    "translated_title_shared_anchors",
    "translated_title_transliterated_author",
    "translated_title_missing_volume_anchors",
    "historical_republication",
    "conference_abstract_publication",
    "shifted_author_title_artifact",
    "correction_notice",
    "title_stem_same_issue",
    "corporate_title_prefix",
    "living_chapter_revision",
    "corporate_declaration_edition",
    "institutional_document_revision",
    "single_token_metadata_typo",
    "shared_doi_same_work",
    # biblio_match.py
    "near_identical_title",
    "physical_location_same_work",      # F2-C
    "preprint_published_version",       # F2-B, requires version evidence
    "strict_prefix_title",              # F2-D, review-only revival
    "version_chain_same_work",          # §15.2, rev 5.3
    "corporate_all_fields_identical",   # Rule A
    # DELIBERATELY ABSENT, and this comment is the record of why:
    #   "corporate_author_three_anchor"  -- Rule K, CUT 2026-08-14 (two firings
    #       across three frames, zero on the seed that motivated it, against five
    #       documented interaction defects; see flag_verdict).
    #   "shared_doi_first_author_differs" -- Rule B, CUT 2026-08-14 (cleared 2
    #       labelled TRUE_F2 rows of the 6 it touched; no DOI-anchored relaxation
    #       replaces it).
    # A reason absent from this registry is rejected by the schema (§19.1), so
    # re-adding either route means re-adding it here first, on purpose.
    # C1-C5 (rev 5.4). Each names WHICH repair took a row out of the wrong-paper
    # band, so a row that left it can never be read as "cleanly matched".
    "implausible_author_field",         # C2
    "corporate_name_inverted",          # C4
    "page_editorial_suffix",            # C3
    "roman_not_in_series_context",      # C1
})

# --- §5.6 A.2: non-same-work reasons on route review_wrong_paper --------------
WRONG_PAPER_REASONS = frozenset({
    "preprint_shape_unconfirmed",       # preprint shape without version evidence
    "resolved_preprint_target",         # F2-B resolved-side signal
})

# --- §5.6 A.3: unscoreable buckets -> route unscoreable -----------------------
UNSCOREABLE_BUCKETS = frozenset({
    "resolved_book_container",
    "resolved_no_title",
    "field_transposition_journal_holds_title",   # F2-I, authority-gated, inert (§13)
    "field_transposition_authors_hold_title",    # F2-I, authority-gated, inert (§13)
    "no_claimed_title",
    "author_residue_as_title",
    "journal_as_title",
    "journal_author_residue_as_title",
    "single_word_title",
    "numeric_or_year_only_title",
    "regulatory_code",
})

REASON_REGISTRY = SAME_WORK_REASONS | WRONG_PAPER_REASONS | UNSCOREABLE_BUCKETS

# reason -> route (the machine-contract mapping).
REASON_ROUTE = {
    **{r: "review_same_work_variant" for r in SAME_WORK_REASONS},
    **{r: "review_wrong_paper" for r in WRONG_PAPER_REASONS},
    **{r: "unscoreable" for r in UNSCOREABLE_BUCKETS},
}

# §5.6 A.4: registered but NOT emitted in the frozen configuration. Registering
# them is deliberate so activation never requires a schema change.
NOT_EMITTED_IN_FROZEN_CONFIG = frozenset({
    "field_transposition_journal_holds_title",   # F2-I inert (§13)
    "field_transposition_authors_hold_title",    # F2-I inert (§13)
})


def is_registered(reason: str) -> bool:
    """Whether ``reason`` is in the closed §5.6 registry. A schema MUST reject any
    ``route_reason`` value for which this returns False (§19.1)."""
    return reason in REASON_REGISTRY


def route_for(reason: str) -> str:
    """The route a registered reason belongs to; raises for an unregistered one."""
    if reason not in REASON_ROUTE:
        raise KeyError(f"unregistered route_reason {reason!r} (not in §5.6)")
    return REASON_ROUTE[reason]


# =====================================================================
# §2.3 / C.2 manifest contradiction checks
# =====================================================================
_MANIFEST_REQUIRED = (
    "journal_authority_snapshot", "journal_authority_sha256",
    "journal_authority_records", "journal_authority_retrieved_utc",
    "journal_authority_loaded", "journal_match_authoritative_rate",
    "f2i_field_transposition_active", "f2d_strict_prefix_active",
    "reason_registry_version",
)


def validate_manifest(manifest: dict, *, check_live_config: bool = True) -> None:
    """Enforce the C.2 manifest invariants; raise ``ValueError`` on any breach.

    A ``journal_authority_loaded=false`` run cannot have produced authoritative
    journal matches, and an ``_active`` flag cannot be true while its rule is gated
    off in the shipped code. These catch the exact 'wrong configuration looks like
    the right one' failure the artifact contract must prevent."""
    missing = [k for k in _MANIFEST_REQUIRED if k not in manifest]
    if missing:
        raise ValueError(f"manifest missing required §2.3/C.2 fields: {missing}")

    loaded = bool(manifest["journal_authority_loaded"])
    rate = manifest["journal_match_authoritative_rate"] or 0
    if rate > 0 and not loaded:
        raise ValueError(
            "manifest contradiction: journal_match_authoritative_rate "
            f"({rate}) > 0 while journal_authority_loaded is false.")

    f2i_active = bool(manifest["f2i_field_transposition_active"])
    f2d_active = bool(manifest["f2d_strict_prefix_active"])
    if f2i_active and not loaded:
        raise ValueError(
            "manifest contradiction: f2i_field_transposition_active is true while "
            "journal_authority_loaded is false (F2-I is authority-gated).")

    if manifest["reason_registry_version"] != REASON_REGISTRY_VERSION:
        raise ValueError(
            f"manifest reason_registry_version "
            f"{manifest['reason_registry_version']!r} != loaded "
            f"{REASON_REGISTRY_VERSION!r}.")

    if check_live_config:
        # Cross-check the declared F2-D flag in BOTH directions against the
        # shipped configuration. A stale false declaration on active code is as
        # misleading as a true declaration on gated-off code.
        from .biblio_match import _F2D_STRICT_PREFIX_ENABLED
        from .journal_identity import JOURNAL_AUTHORITY
        if f2d_active != _F2D_STRICT_PREFIX_ENABLED:
            raise ValueError(
                "manifest contradiction: f2d_strict_prefix_active does not match "
                f"the loaded F2-D configuration ({_F2D_STRICT_PREFIX_ENABLED}).")
        if f2i_active and JOURNAL_AUTHORITY.is_empty():
            raise ValueError(
                "manifest contradiction: f2i_field_transposition_active is true "
                "while the journal authority is empty (F2-I cannot fire).")
        if loaded and JOURNAL_AUTHORITY.is_empty():
            raise ValueError(
                "manifest contradiction: journal_authority_loaded is true while "
                "the in-process JOURNAL_AUTHORITY is empty.")
