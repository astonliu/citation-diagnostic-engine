"""F7 acceptance tests for the 2026-08-16 identity / authority-lock / reporting spec.

One test per row of that spec's acceptance matrix. Every case here failed before
the fix, and the four defects they cover share one shape: **F7 reported a state
it had no basis for.** A confirmed finding dropped while the disposition said it
was pending; an unreachable configuration publishing a wired seam and a zero; a
stale alias row becoming a wrong-entity accusation against the gene it agrees
with; a section F7 declined to read leaving no trace it existed.

Offline throughout -- the seams are the same injected stubs the rest of the F7
suite uses. Nothing here is evaluation data or an input to a reported number.
"""
from __future__ import annotations

import dataclasses

import pytest

from . import judgment_run as jr
from . import test_f7_entity as f7t
from .f7_entity import (
    HOLD_REASONS,
    PRODUCTION_F7_EVIDENCE_BUILDER,
    R_AUTHORITY_NOT_LOCKED,
    R_SAME_CANONICAL_LABEL,
    R_SECTIONS_EXCLUDED_BY_KIND,
    EntityAssessorRun,
    EvidenceContext,
    ExcludedSection,
    F7Policy,
    f7_reachability,
    hold_reason_histogram,
    validate_f7_policy,
)
from .judgment_engine import DiscriminatorContractError, EntityAssessment, EntityState
from .test_f7_orchestrator_wiring import ORIGINATES, builder, pair, seams
from .test_judgment_run import (
    abstract_ok,
    disc_llm,
    extractor_of,
    f4_json,
    judge_established,
    make_ref,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def normalizer_for(claimed, evidence, *, relation="provably_distinct"):
    """A gene normalizer over one (claimed, evidence) pair, each given as
    ``(id, canonical_label)``, so a test can make the two disagree in exactly
    one respect."""
    ids = {
        ("gene", "BRCA1"): (claimed[0], claimed[1], "exact", "HGNC", "exact_symbol"),
        ("gene", "BRCA2"): (evidence[0], evidence[1], "exact", "HGNC", "exact_symbol"),
    }
    return f7t.DictNormalizer(
        ids=ids,
        relations={(claimed[0], evidence[0]): relation},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )


def f7_full_run(tmp_path, monkeypatch, **kw):
    """``run_natural_judgment`` with F7 wired, for the manifest-level rows."""
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("c")])
    kw.setdefault("f7_seams", seams())
    kw.setdefault("f7_evidence_builder", builder())
    kw.setdefault("f7_policy", f7t.policy())
    return jr.run_natural_judgment(
        str(tmp_path), str(tmp_path / "out"),
        extractor=extractor_of(f7t.CLAIM),
        coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
        preband_disposition={"c": "cleared"}, model="test-model",
        discriminator_call_llm=disc_llm(f4=f4_json(), v2=ORIGINATES), **kw)


# --------------------------------------------------------------------------
# Defect 1 -- the legacy path silently dropped a confirmed F7.
# --------------------------------------------------------------------------
def test_legacy_path_keeps_a_confirmed_f7_label():
    """With both F7 seams wired and NO discriminator_call_llm, judge_pair_finish
    returned before the F7 branch. The finding was already in rec["findings"];
    the label was dropped and the disposition string went out claiming F7 was
    *pending*.

    F7 does not depend on the discriminator seam -- the F7 block runs on both
    paths -- so nothing about an unwired F3/F4 makes a confirmed wrong-entity
    less true. Note the absent ``discriminator_call_llm``: EVERY case in
    test_f7_orchestrator_wiring sets it, which is why this went uncaught.
    """
    rec = pair(f7_seams=seams(), f7_evidence_builder=builder(),
               f7_policy=f7t.policy())
    assert "F7" in rec["findings"]
    assert rec["label"] == "F7"
    assert rec["disposition"] == jr.DISP_PREDICTED


def test_legacy_path_without_an_f7_finding_is_unchanged():
    """The fix must not disturb the legacy route it sits in front of: a pair
    with no F7 finding takes exactly the branch it always did."""
    rec = pair(f7_seams=seams(normalizer=normalizer_for(
                   ("HGNC:1100", "BRCA1"), ("HGNC:1101", "BRCA2"),
                   relation="equivalent")),
               f7_evidence_builder=builder(), f7_policy=f7t.policy())
    assert "F7" not in rec["findings"]
    assert rec.get("label") is None
    assert rec["disposition"] == jr.DISP_HELD_FULL_COVERAGE


# --------------------------------------------------------------------------
# Defect 2 -- the default authority table made F7 structurally unreachable.
# --------------------------------------------------------------------------
def test_default_policy_locks_nothing_and_is_reported_unreachable():
    """``authorities_json`` defaults to "{}" -- valid JSON, a legal empty table,
    accepted without complaint. Every claim then ends at authority_not_locked
    while the seam reports itself wired."""
    report = f7_reachability(F7Policy())
    assert report["locked_types"] == []
    assert report["same_type_reachable"] is False
    assert R_AUTHORITY_NOT_LOCKED in report["unreachable_reason"]


def test_a_run_wiring_f7_under_the_default_policy_refuses(tmp_path, monkeypatch):
    """It raises at CONFIGURATION -- before any output file exists -- exactly
    like the full-text XOR gate. Not a per-pair quarantine: F7's own policy must
    never be filed under quarantine_parse."""
    with pytest.raises(ValueError, match="STRUCTURALLY UNREACHABLE"):
        f7_full_run(tmp_path, monkeypatch, f7_policy=F7Policy())
    assert not (tmp_path / "out" / "judgment_predictions.jsonl").exists()


def test_an_empty_lock_table_burns_zero_model_calls():
    """The lock check precedes _assess_tuple. Previously three generator calls
    (tuples, attribution, evidence) were paid for per claim to reach a verdict
    configuration had already decided."""
    gen, ver = f7t.gen_llm(), f7t.ver_llm()
    (assessment,), _ = f7t.run(
        gen=gen, ver=ver,
        pol=F7Policy(cross_ontology_lock=f7t.CROSS_LOCK))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert assessment.rationale == R_AUTHORITY_NOT_LOCKED
    assert gen.calls == [] and ver.calls == []


def test_a_type_without_a_lock_holds_before_its_attribution_call():
    """Per-tuple half of the same rule: 'gene' is locked, 'drug' is not, so a
    drug tuple holds without paying for attribution or evidence."""
    gen, ver = f7t.gen_llm(tuples=f7t.tuples_json([{
        "tuple_id": 0, "entity_type": "drug", "claim_surface": f7t.SURFACE,
        "clause_span": f7t.CLAUSE, "predicate": "suppresses",
        "object": "tumor growth", "direction": "negative", "population": "mice",
    }])), f7t.ver_llm()
    (assessment,), _ = f7t.run(gen=gen, ver=ver)
    assert assessment.rationale == R_AUTHORITY_NOT_LOCKED
    # One call only: the schema-B tuples call that named the type.
    assert len(gen.calls) == 1 and ver.calls == []


def test_cross_type_f7_is_reported_unreachable_by_design():
    """Not a defect to fix -- the cross comparator's enum has no
    provably_distinct on purpose. Published so the zero reads as a guardrail
    holding rather than as a gap."""
    report = f7_reachability(f7t.policy())
    assert report["same_type_reachable"] is True
    assert report["cross_type_reachable"] is False
    assert "provably_distinct" in report["cross_type_note"]


# --------------------------------------------------------------------------
# Defect 3 -- F7 proposed correcting KRAS to KRAS.
# --------------------------------------------------------------------------
def test_equal_canonical_labels_under_distinct_ids_hold():
    """A stale alias table registers one gene twice. Distinct ids are then not
    proof of distinct entities, and precision-first says ambiguity holds. Before
    the fix this produced DIFFERENT_ENTITY_SUPPORTED with
    proposed_corrected_label='KRAS' against a claim that said KRAS."""
    (assessment,), assessor = f7t.run(normalizer=normalizer_for(
        ("HGNC:6407-OLD", "KRAS"), ("HGNC:6407", "KRAS")))
    assert assessment.state is EntityState.UNJUDGEABLE
    tr = assessor.records[0]["tuple_records"][0]
    assert tr["reason"] == R_SAME_CANONICAL_LABEL
    assert tr["confirmed_mismatch"] is False
    assert tr["proposed_corrected_label"] is None


def test_ids_differing_only_in_case_are_one_id():
    """A comparator calling "hgnc:6407" and "HGNC:6407" provably_distinct has
    contradicted itself -- the same contract violation the exact-equality guard
    already raised on, spelled differently. Quarantine, not an accusation."""
    with pytest.raises(ValueError, match="identical normalized ids"):
        f7t.run(normalizer=normalizer_for(
            ("hgnc:6407", "KRAS"), ("HGNC:6407", "KRAS")))


def test_labels_differing_only_in_case_are_one_label():
    (assessment,), assessor = f7t.run(normalizer=normalizer_for(
        ("HGNC:1100", "Kras"), ("HGNC:1101", "KRAS")))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert assessor.records[0]["tuple_records"][0]["reason"] == R_SAME_CANONICAL_LABEL


def test_genuinely_distinct_entities_still_reach_f7():
    """The guard must not swallow the finding it exists to qualify: distinct
    ids AND distinct labels is still a confirmed wrong-entity."""
    (assessment,), _ = f7t.run(normalizer=normalizer_for(
        ("HGNC:1100", "BRCA1"), ("HGNC:1101", "BRCA2")))
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED


def test_the_engine_contract_rejects_case_variant_keys():
    """Defence in depth at the contract boundary, so the rule holds for any
    future producer and not only for the one in f7_entity."""
    with pytest.raises(DiscriminatorContractError, match="must differ"):
        EntityAssessment(0, EntityState.DIFFERENT_ENTITY_SUPPORTED,
                         claimed_entity_key="HGNC:6407",
                         evidence_entity_key="hgnc:6407",
                         relation_supported=True)


def test_the_engine_accepts_case_variant_keys_as_the_same_entity():
    """The other half: two keys differing only in case are ONE key, so a
    same-entity verdict over them is consistent and must not raise."""
    assessment = EntityAssessment(0, EntityState.SAME_ENTITY,
                                  claimed_entity_key="HGNC:6407",
                                  evidence_entity_key="hgnc:6407")
    assert assessment.state is EntityState.SAME_ENTITY


# --------------------------------------------------------------------------
# Defect 4 -- evidence outside the four admitted section kinds vanished.
# --------------------------------------------------------------------------
def test_a_recorded_section_drop_gets_its_own_reason():
    """A paper whose entity is named only in the Discussion used to be
    INDISTINGUISHABLE from a paper with no body evidence at all: SectionText
    refuses the section, so a builder either quarantined the pair on F7's own
    section policy or dropped it in silence. Now it can say what it dropped."""
    ctx = dataclasses.replace(
        f7t.make_context(), body_sections=(),
        excluded_sections=(ExcludedSection("discussion", "b" * 64),))
    (assessment,), _ = f7t.run(context=ctx)
    assert assessment.rationale == R_SECTIONS_EXCLUDED_BY_KIND


def test_no_sections_and_no_recorded_drop_still_reads_as_no_evidence():
    """The distinction only exists when the builder made it. An unrecorded drop
    is still silence -- which is why the manifest reports whether any builder
    populated the field at all."""
    ctx = dataclasses.replace(f7t.make_context(), body_sections=())
    (assessment,), _ = f7t.run(context=ctx)
    assert assessment.rationale == "evidence_source_insufficient"


def test_an_excluded_section_is_never_readable_as_evidence():
    """ExcludedSection carries no text, by construction: it names a drop and
    binds it by digest, and cannot be mistaken for something F7 may read."""
    assert not hasattr(ExcludedSection("discussion", "b" * 64), "text")
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ExcludedSection("discussion", "not-a-digest")
    with pytest.raises(ValueError, match="nonblank string"):
        ExcludedSection("  ", "b" * 64)


def test_recording_a_drop_binds_it_into_the_context_digest():
    base = f7t.make_context()
    with_drop = dataclasses.replace(
        base, excluded_sections=(ExcludedSection("discussion", "b" * 64),))
    _, a = f7t.run(context=base)
    _, b = f7t.run(context=with_drop)
    assert (a.records[0]["evidence_context_sha256"]
            != b.records[0]["evidence_context_sha256"])


def test_an_empty_exclusion_list_leaves_every_existing_digest_unmoved():
    """The key is CONDITIONAL. An unconditional one would move the digest of
    every context ever hashed, and the replay validator would then reject
    records it produced itself."""
    from .f7_entity import _evidence_context_sha256
    base = f7t.make_context()
    assert (_evidence_context_sha256(base)
            == _evidence_context_sha256(dataclasses.replace(
                base, excluded_sections=())))


# --------------------------------------------------------------------------
# Defect 5 -- the manifest published states, not reasons.
# --------------------------------------------------------------------------
def test_hold_reasons_are_aggregated_at_both_granularities():
    """outcome_counts keys on three EntityState values, so every distinct hold
    reason collapsed into one UNJUDGEABLE tally. The causes were never lost --
    they sat on disk in rec["f7_records"] -- they were simply in no artifact
    anyone reads."""
    _, assessor = f7t.run(normalizer=normalizer_for(
        ("HGNC:6407-OLD", "KRAS"), ("HGNC:6407", "KRAS")))
    hist = hold_reason_histogram(assessor.records)
    assert hist["claim"] == {R_SAME_CANONICAL_LABEL: 1}
    assert hist["tuple"] == {R_SAME_CANONICAL_LABEL: 1}
    assert hist["unrecognised"] == []


def test_every_emitted_hold_reason_is_enumerated():
    """A reason absent from HOLD_REASONS is a reason nothing aggregates, which
    is the gap the histogram exists to close. Pinned against the module's own
    R_* constants, not against a copy of the list."""
    from . import f7_entity
    declared = {v for k, v in vars(f7_entity).items()
                if k.startswith("R_") and isinstance(v, str)}
    assert declared == set(HOLD_REASONS)


def test_an_unenumerated_reason_is_surfaced_not_swallowed():
    hist = hold_reason_histogram([{
        "derived": "UNJUDGEABLE", "reason": "invented_reason",
        "tuple_records": [],
    }])
    assert hist["unrecognised"] == ["invented_reason"]


# --------------------------------------------------------------------------
# Defect 6 -- the f7 block contradicted seam_status.
# --------------------------------------------------------------------------
def test_the_f7_block_no_longer_claims_wired_with_no_seams():
    block = jr._f7_manifest_block(None, [])
    assert block["wired"] is False


def test_f7_wired_agrees_with_seam_status(tmp_path, monkeypatch):
    """Seams supplied, no evidence builder: a real configuration, in which F7
    cannot run. Both fields now come from ONE expression, so the manifest can no
    longer say wired=true and wired=false about the same seam."""
    manifest = f7_full_run(tmp_path, monkeypatch, f7_evidence_builder=None)
    assert manifest["f7"]["wired"] is False
    assert manifest["seam_status"]["F7"]["wired"] is False
    assert manifest["f7"]["evidence_context_supplied"] is False


def test_a_fully_wired_run_agrees_the_other_way(tmp_path, monkeypatch):
    manifest = f7_full_run(tmp_path, monkeypatch)
    assert manifest["f7"]["wired"] is True
    assert manifest["seam_status"]["F7"]["wired"] is True


def test_the_manifest_names_the_locked_types_not_just_their_digest(
        tmp_path, monkeypatch):
    """authorities_sha256 of an empty table is 44136fa3... -- sha256("{}") --
    and nothing compares it to anything, so recognising an unreachable run meant
    already knowing that constant on sight."""
    manifest = f7_full_run(tmp_path, monkeypatch)
    f7 = manifest["f7"]
    assert f7["authorities_locked_types"] == ["gene"]
    assert f7["same_type_reachable"] is True
    assert f7["unreachable_reason"] is None
    assert manifest["seam_status"]["F7"]["authorities_locked_types"] == ["gene"]


def test_the_manifest_states_f7_has_no_production_evidence_builder(
        tmp_path, monkeypatch):
    """EvidenceContext is constructed only in this package's tests. Until that
    changes, every F7 number is a number over fixtures, and the manifest says so
    rather than leaving a reader to infer it from a builder they cannot see."""
    manifest = f7_full_run(tmp_path, monkeypatch)
    assert PRODUCTION_F7_EVIDENCE_BUILDER is False
    assert manifest["f7"]["production_evidence_builder"] is False
    assert "no production evidence builder" in manifest["f7"]["production_note"].lower()
    assert manifest["seam_status"]["F7"]["production_evidence_builder"] is False


def test_reportable_false_is_annotated_as_not_a_fault_report(tmp_path, monkeypatch):
    """An honest run whose claims all matched the paper's own entity gets
    reportable=False too. The field name invites the opposite reading."""
    manifest = f7_full_run(tmp_path, monkeypatch)
    assert "does not mean anything went wrong" in manifest["f7"]["reportable_note"]


def test_the_hold_reason_histogram_reaches_the_manifest(tmp_path, monkeypatch):
    manifest = f7_full_run(tmp_path, monkeypatch, f7_seams=seams(
        normalizer=normalizer_for(("HGNC:6407-OLD", "KRAS"), ("HGNC:6407", "KRAS"))))
    hist = manifest["f7"]["hold_reasons"]
    assert hist["claim"] == {R_SAME_CANONICAL_LABEL: 1}
    assert hist["enumerated_reasons"] == len(HOLD_REASONS)
