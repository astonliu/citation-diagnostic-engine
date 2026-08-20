"""Offline production-shaped tests for the F7 evidence adapter."""
from __future__ import annotations

import hashlib

import pytest

from .f7_evidence_builder import ProductionF7EvidenceBuilder
from .f7_entity import EntityState, F7Policy, make_entity_assessor
from . import test_f7_entity as f7t


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def section(label: str, text: str) -> dict:
    return {"label": label, "title": label.title(), "text": text,
            "content_sha256": sha(text)}


def item(*, sections=None, complete=True, resolved=True, pmid="123",
         source_pmid="123", pmcid="PMC123", group=None, clusters=None,
         cluster_index=None):
    sections = list(sections if sections is not None else [
        section("methods", "We studied BRCA2 in mice."),
        section("results", "BRCA2 suppressed tumor growth in mice."),
        section("discussion", "BRCA1 may behave similarly."),
    ])
    row = {
        "citation_id": "PMC9:ref12", "cited_pmid": pmid,
        "citing_sentence": f7t.CITING, "atomic_claims": [f7t.CLAIM],
        "citance_group_members": list(group or []),
        "evidence": {"cited_fulltext": {
            "pmid": source_pmid, "pmcid": pmcid, "resolved": resolved,
            "sections": sections,
            "sections_present": sorted({s.get("label") for s in sections
                                         if isinstance(s, dict) and s.get("label")}),
            "retrieval_complete": complete, "incomplete_reasons": [],
            "sanitized_paths": [], "source": "fixture",
        }},
    }
    if clusters is not None:
        row["citance_marker_clusters"] = clusters
        row["citance_marker_cluster_index"] = cluster_index
    return row


def test_builder_cross_binds_work_claims_references_and_sections():
    ctx = ProductionF7EvidenceBuilder()(item())
    assert ctx.paper_resolved is True
    assert ctx.resolved_work_id == "PMC123"
    assert ctx.citing_sentence == f7t.CITING
    assert ctx.target_reference_id == "PMC9:ref12"
    assert ctx.bundled_reference_ids == ("PMC9:ref12",)
    assert [(c.claim_index, c.clause_span, c.reference_ids)
            for c in ctx.claim_clause_refs] == [
                (0, f7t.CLAIM, ("PMC9:ref12",))]
    assert [s.section_label for s in ctx.body_sections] == ["methods", "results"]
    assert all(s.source_work_id == "PMC123" for s in ctx.body_sections)
    assert [(s.section_label, s.content_sha256) for s in ctx.excluded_sections] == [
        ("discussion", sha("BRCA1 may behave similarly."))]
    assert not hasattr(ctx.excluded_sections[0], "text")


def test_builder_uses_exact_marker_cluster_members_for_final_scoped_claims():
    clusters = [
        {"members": ["PMC9:ref10", "PMC9:ref11"]},
        {"members": ["PMC9:ref12", "PMC9:ref13"]},
    ]
    ctx = ProductionF7EvidenceBuilder()(item(
        group=["PMC9:ref10", "PMC9:ref11", "PMC9:ref12", "PMC9:ref13"],
        clusters=clusters, cluster_index=1))
    assert ctx.bundled_reference_ids == (
        "PMC9:ref10", "PMC9:ref11", "PMC9:ref12", "PMC9:ref13")
    assert ctx.claim_clause_refs[0].reference_ids == (
        "PMC9:ref12", "PMC9:ref13")


def test_marker_scope_refusal_keeps_whole_group_attribution():
    clusters = [
        {"members": ["PMC9:ref10", "PMC9:ref11"]},
        {"members": ["PMC9:ref12", "PMC9:ref13"]},
    ]
    row = item(
        group=["PMC9:ref10", "PMC9:ref11", "PMC9:ref12", "PMC9:ref13"],
        clusters=clusters, cluster_index=1)
    row["marker_scope"] = {
        "status": "whole_sentence", "reason": "claim_cluster_ambiguous"}
    ctx = ProductionF7EvidenceBuilder()(row)
    assert ctx.claim_clause_refs[0].reference_ids == ctx.bundled_reference_ids


@pytest.mark.parametrize("mutation", ["missing", "unresolved", "partial", "map"])
def test_missing_unresolved_or_incomplete_fulltext_holds(mutation):
    row = item()
    if mutation == "missing":
        row["evidence"].pop("cited_fulltext")
    elif mutation == "unresolved":
        row["evidence"]["cited_fulltext"].update(
            {"resolved": False, "pmcid": None, "retrieval_complete": False})
    elif mutation == "partial":
        row["evidence"]["cited_fulltext"]["retrieval_complete"] = False
    else:
        row["evidence"]["cited_fulltext"]["sections_present"] = ["results"]
    ctx = ProductionF7EvidenceBuilder()(row)
    assessor = make_entity_assessor(
        call_llm=f7t.gen_llm(), verifier_call_llm=f7t.ver_llm(),
        normalizer=f7t.gene_normalizer(), cross_comparator=None,
        relation_comparator=lambda *a, **k: f7t.relation_all_match(),
        evidence_context=ctx, policy=f7t.policy())
    (assessment,) = assessor([f7t.CLAIM])
    assert assessment.state is EntityState.UNJUDGEABLE


def test_excluded_only_is_distinguishable_from_missing_evidence():
    ctx = ProductionF7EvidenceBuilder()(item(sections=[
        section("intro", "Background text."),
        section("discussion", "Interpretation text."),
    ]))
    assert ctx.body_sections == ()
    assert len(ctx.excluded_sections) == 2
    assessor = make_entity_assessor(
        call_llm=f7t.gen_llm(), verifier_call_llm=f7t.ver_llm(),
        normalizer=f7t.gene_normalizer(), cross_comparator=None,
        relation_comparator=lambda *a, **k: f7t.relation_all_match(),
        evidence_context=ctx, policy=f7t.policy())
    (assessment,) = assessor([f7t.CLAIM])
    assert assessment.rationale == "evidence_sections_excluded_by_kind"


def test_wrong_work_binding_and_source_hash_tampering_are_rejected():
    with pytest.raises(ValueError, match="different PMID"):
        ProductionF7EvidenceBuilder()(item(source_pmid="999"))
    row = item()
    row["evidence"]["cited_fulltext"]["sections"][0]["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="does not bind"):
        ProductionF7EvidenceBuilder()(row)


def test_contradictory_complete_metadata_holds_instead_of_exposing_sections():
    row = item()
    row["evidence"]["cited_fulltext"]["incomplete_reasons"] = ["body_unparseable"]
    ctx = ProductionF7EvidenceBuilder()(row)
    assert ctx.paper_resolved is True
    assert ctx.body_sections == ()


def test_abstract_intro_and_discussion_never_become_f7_evidence():
    ctx = ProductionF7EvidenceBuilder()(item(sections=[
        section("abstract", "Abstract text."),
        section("intro", "Intro text."),
        section("discussion", "Discussion text."),
        section("results", "BRCA2 changed the outcome."),
    ]))
    assert [s.section_label for s in ctx.body_sections] == ["results"]
    assert [s.section_label for s in ctx.excluded_sections] == [
        "abstract", "intro", "discussion"]


def test_builder_is_stateless_across_calls():
    builder = ProductionF7EvidenceBuilder()
    first = builder(item())
    second = builder(item())
    assert first == second
    assert builder.version == "f7_production_evidence_v1"


def test_narrower_valid_tuple_clause_inherits_atomic_claim_attribution():
    ctx = ProductionF7EvidenceBuilder()(item(sections=[
        section("results", f7t.RESULTS_TEXT),
        section("methods", f7t.METHODS_TEXT),
    ]))
    assessor = make_entity_assessor(
        call_llm=f7t.gen_llm(), verifier_call_llm=f7t.ver_llm(),
        normalizer=f7t.gene_normalizer(), cross_comparator=None,
        relation_comparator=lambda *a, **k: f7t.relation_all_match(),
        evidence_context=ctx, policy=f7t.policy())
    (assessment,) = assessor([f7t.CLAIM])
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
