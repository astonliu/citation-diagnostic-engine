"""Offline acceptance tests for the F7 wrong-entity discriminator (spec v5.1).

No network, no model: ``call_llm`` / ``verifier_call_llm`` / the normalizer /
the comparators are injected stubs. Covers every row of the Sec 11 acceptance
matrix -- the genuine same-type sibling F7, multi-tuple roll-ups with
deterministic lowest-``tuple_id`` keys, the no-cherry-picking hold, granularity
zoom, cross-type-never-F7, accepted synonyms, relation comparability + mismatch,
the span / own-finding / section-kind / verifier holds, both-type authority
locks, normalization ambiguity, the same-claim ``("F7","F6")`` engine
derivation, and every ``ValueError`` provenance/replay case (content hash,
wrong work, lock mismatch, malformed output, bad ``tuple_id``,
``validate_f7_record`` tamper detection). Stub seams validate CONTROL FLOW only
(spec Sec 14): biomedical accuracy is a separate benchmark.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from .f7_entity import (
    ClaimClauseRef,
    EntityAssessorRun,
    EvidenceContext,
    F7Authority,
    F7Policy,
    SectionText,
    make_entity_assessor,
    record_sha256,
    tuple_record_sha256,
    validate_f7_record,
)
from .judgment_engine import (
    ClaimSupport,
    DecisionStatus,
    EntityAssessment,
    EntityState,
    ProvenanceAssessment,
    ProvenanceState,
    SupportState,
    TemporalAssessment,
    TemporalState,
    decide_judgment,
)


# --------------------------------------------------------------------------
# Fixtures / builders.
# --------------------------------------------------------------------------
CLAIM = "BRCA1 suppresses tumor growth in mice"
CLAUSE = "BRCA1 suppresses tumor growth"
SURFACE = "BRCA1"
CITING = "BRCA1 suppresses tumor growth in mice [12]."
TARGET = "ref12"

RESULTS_TEXT = "In our knockouts, BRCA2 loss accelerated tumor growth in the cohort."
METHODS_TEXT = "Mice were housed and BRCA2 alleles were genotyped by PCR."

WORK = "PMCID:W1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


RESULTS = SectionText("results", RESULTS_TEXT, WORK, _sha(RESULTS_TEXT))
METHODS = SectionText("methods", METHODS_TEXT, WORK, _sha(METHODS_TEXT))


def make_context(
    *,
    paper_resolved=True,
    resolved_work_id=WORK,
    citing_sentence=CITING,
    target_reference_id=TARGET,
    bundled=("ref12",),
    clause_refs=None,
    sections=None,
):
    if clause_refs is None:
        clause_refs = (ClaimClauseRef(0, CLAUSE, ("ref12",)),)
    if sections is None:
        sections = (RESULTS, METHODS)
    return EvidenceContext(
        paper_resolved=paper_resolved,
        resolved_work_id=resolved_work_id,
        citing_sentence=citing_sentence,
        target_reference_id=target_reference_id,
        bundled_reference_ids=bundled,
        claim_clause_refs=clause_refs,
        body_sections=sections,
    )


GENE_LOCK = {
    "authority": "HGNC", "version": "2026-01", "lookup_date": "2026-07-16",
    "accept_synonym_as_equivalent": True, "cross_db_equivalences": [],
}
VARIANT_LOCK = {
    "authority": "ClinVar", "version": "2026-01", "lookup_date": "2026-07-16",
    "accept_synonym_as_equivalent": False, "cross_db_equivalences": [],
}
CROSS_LOCK = "cross-ontology|2026-01|2026-07-16"


def policy(*, authorities=None, cross_lock=CROSS_LOCK):
    if authorities is None:
        authorities = {"gene": GENE_LOCK}
    return F7Policy(
        authorities_json=json.dumps(authorities),
        cross_ontology_lock=cross_lock,
        generator_model_id="gen", verifier_model_id="ver",
    )


# -- schema JSON builders --------------------------------------------------
def tuples_json(rows=None):
    if rows is None:
        rows = [{
            "tuple_id": 0, "entity_type": "gene", "claim_surface": SURFACE,
            "clause_span": CLAUSE, "predicate": "suppresses", "object": "tumor growth",
            "direction": "negative", "population": "mice",
        }]
    return json.dumps(rows)


def attribution_json(*, attribution="direct", target_supported=True,
                     sibling_reference_possible=False, rationale="a"):
    return json.dumps({
        "attribution": attribution, "target_supported": target_supported,
        "sibling_reference_possible": sibling_reference_possible, "rationale": rationale,
    })


def evidence_json(*, entity_type="gene", evidence_surface="BRCA2",
                  entity_section=None, entity_span="BRCA2 loss",
                  relation_section=None, relation_span="accelerated tumor growth",
                  predicate="suppresses", object_="tumor growth",
                  direction="negative", population="mice", papers_own_finding=True):
    return json.dumps({
        "entity_type": entity_type, "evidence_surface": evidence_surface,
        "entity_section_sha256": entity_section if entity_section is not None else RESULTS.content_sha256,
        "entity_span": entity_span,
        "relation_section_sha256": relation_section if relation_section is not None else RESULTS.content_sha256,
        "relation_span": relation_span,
        "predicate": predicate, "object": object_, "direction": direction,
        "population": population, "papers_own_finding": papers_own_finding, "rationale": "c",
    })


def verifier_json(*, differ=True, own=True, direct=True, equivalent=True,
                  enumerated=True, rationale="v"):
    return json.dumps({
        "entities_genuinely_differ": differ, "papers_own_finding": own,
        "direct_attribution": direct, "relation_tuple_equivalent": equivalent,
        "all_load_bearing_tuples_enumerated": enumerated, "rationale": rationale,
    })


def relation_all_match(**kw):
    out = {k: "match" for k in ("predicate", "object", "direction", "population")}
    out["rationale"] = "d"
    out.update(kw)
    return out


# -- injected seams --------------------------------------------------------
class ScriptedLLM:
    """Routes prompts to canned responses by a substring marker, so the
    generator and verifier are deterministic and order-independent."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, prompt):
        self.calls.append(prompt)
        for marker, response in self.mapping.items():
            if marker in prompt:
                return response(prompt) if callable(response) else response
        raise AssertionError(f"no scripted response for prompt: {prompt[:60]!r}")


def gen_llm(*, tuples=None, attribution=None, evidence=None):
    return ScriptedLLM({
        "Return ONLY a JSON array": tuples if tuples is not None else tuples_json(),
        "how ONE clause of a citing sentence": attribution if attribution is not None else attribution_json(),
        "read the cited work's OWN body sections": evidence if evidence is not None else evidence_json(),
    })


def ver_llm(response=None):
    return ScriptedLLM({"independently verify ONE proposed": response if response is not None else verifier_json()})


class DictNormalizer:
    """Maps (type, surface) -> normalize dict and (id_a,id_b) -> relation."""

    def __init__(self, *, ids, relations, lock_map):
        self.ids = ids            # (type, surface) -> (id, canonical, status, source_db, method)
        self.relations = relations  # (id_a, id_b) -> relation
        self.lock_map = lock_map    # type -> (authority, version, lookup_date)

    def normalize(self, entity_type, surface, *, lock):
        cid, canonical, status, source_db, method = self.ids[(entity_type, surface)]
        auth, ver, date = self.lock_map[entity_type]
        return {
            "authority": auth, "version": ver, "lookup_date": date,
            "source_db": source_db, "mapping_method": method, "id": cid,
            "canonical_label": canonical, "mapping_status": status, "evidence": "e",
        }

    def compare(self, id_a, id_b, entity_type, *, lock):
        auth, ver, date = self.lock_map[entity_type]
        return {
            "relation": self.relations[(id_a, id_b)], "authority": auth,
            "version": ver, "lookup_date": date, "evidence": "e",
        }


class DictCross:
    def __init__(self, relation, lock=CROSS_LOCK):
        self.relation = relation
        auth, ver, date = lock.split("|")
        self._fields = (auth, ver, date)

    def compare(self, id_a, type_a, id_b, type_b, *, cross_ontology_lock):
        auth, ver, date = self._fields
        return {"relation": self.relation, "authority": auth, "version": ver,
                "lookup_date": date, "evidence": "e"}


# Default gene normalizer: BRCA1 -> HGNC:1100, BRCA2 -> HGNC:1101, distinct.
def gene_normalizer(relation="provably_distinct", *, brca1_status="exact",
                    brca2_status="exact"):
    return DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", brca1_status, "HGNC", "exact_symbol"),
            ("gene", "BRCA2"): ("HGNC:1101", "BRCA2", brca2_status, "HGNC", "exact_symbol"),
        },
        relations={("HGNC:1100", "HGNC:1101"): relation},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )


def build(*, gen=None, ver=None, normalizer=None, cross=None,
          relation_comparator=None, context=None, pol=None):
    return make_entity_assessor(
        call_llm=gen if gen is not None else gen_llm(),
        verifier_call_llm=ver if ver is not None else ver_llm(),
        normalizer=normalizer if normalizer is not None else gene_normalizer(),
        cross_comparator=cross,
        relation_comparator=relation_comparator if relation_comparator is not None
        else (lambda c, e, *, call_llm: relation_all_match()),
        evidence_context=context if context is not None else make_context(),
        policy=pol if pol is not None else policy(),
    )


def run(claims=(CLAIM,), **kw):
    assessor = build(**kw)
    return assessor(claims), assessor


# --------------------------------------------------------------------------
# Confirmed F7 (DIFFERENT_ENTITY_SUPPORTED) + engine derivation.
# --------------------------------------------------------------------------
def test_genuine_same_type_sibling_is_f7():
    (assessment,), assessor = run()
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    assert assessment.claimed_entity_key == "HGNC:1100"
    assert assessment.evidence_entity_key == "HGNC:1101"
    assert assessment.relation_supported is True
    rec = assessor.records[0]
    assert rec["derived"] == "DIFFERENT_ENTITY_SUPPORTED"
    tr = rec["tuple_records"][0]
    assert tr["confirmed_mismatch"] is True
    assert tr["proposed_corrected_id"] == "HGNC:1101"
    assert tr["proposed_corrected_label"] == "BRCA2"


def test_same_claim_yields_engine_f7_and_f6():
    # The as-written claim (BRCA1) is UNESTABLISHED by coverage -> F6; the entity
    # assessor confirms the paper supports a distinct sibling -> F7. Engine locks
    # primary F7 with findings ("F7","F6") on the SAME claim.
    (assessment,), _ = run()
    support = (ClaimSupport(0, SupportState.UNESTABLISHED),)
    decision = decide_judgment(
        preband_cleared=True, claims=(CLAIM,), claim_support=support,
        entity_assessments=(assessment,), provenance=None,
        temporal=TemporalAssessment(TemporalState.NO_QUALIFYING_CONTRADICTION))
    assert decision.status is DecisionStatus.TERMINAL
    assert decision.primary_label == "F7"
    assert decision.findings == ("F7", "F6")


# --------------------------------------------------------------------------
# Multi-tuple roll-ups (deterministic keys; no cherry-picking).
# --------------------------------------------------------------------------
def _two_gene_claim():
    claim = "BRCA1 suppresses tumor growth and TP53 drives apoptosis"
    clause1 = "BRCA1 suppresses tumor growth"
    clause2 = "TP53 drives apoptosis"
    context = make_context(
        citing_sentence=claim + " [12].",
        clause_refs=(ClaimClauseRef(0, clause1, ("ref12",)),
                     ClaimClauseRef(0, clause2, ("ref12",))),
    )
    rows = [
        {"tuple_id": 1, "entity_type": "gene", "claim_surface": "TP53",
         "clause_span": clause2, "predicate": "drives", "object": "apoptosis",
         "direction": "positive", "population": "cells"},
        {"tuple_id": 0, "entity_type": "gene", "claim_surface": "BRCA1",
         "clause_span": clause1, "predicate": "suppresses", "object": "tumor growth",
         "direction": "negative", "population": "mice"},
    ]
    return claim, context, rows


def test_two_confirmed_mismatches_keys_from_lowest_tuple_id():
    claim, context, rows = _two_gene_claim()
    # Both clauses resolve to distinct siblings; verifier confirms both.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1101", "BRCA2", "exact", "HGNC", "s"),
            ("gene", "TP53"): ("HGNC:11998", "TP53", "exact", "HGNC", "s"),
            ("gene", "MDM2"): ("HGNC:6973", "MDM2", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1101"): "provably_distinct",
                   ("HGNC:11998", "HGNC:6973"): "provably_distinct"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )
    # Evidence differs per clause: BRCA1-clause -> BRCA2, TP53-clause -> MDM2.
    def evidence_router(prompt):
        if "BRCA1" in prompt:
            return evidence_json(evidence_surface="BRCA2", entity_span="BRCA2 loss",
                                 relation_span="accelerated tumor growth")
        return evidence_json(evidence_surface="MDM2", entity_span="BRCA2 loss",
                             relation_span="accelerated tumor growth",
                             predicate="drives", object_="apoptosis",
                             direction="positive", population="cells")
    gen = ScriptedLLM({
        "Return ONLY a JSON array": tuples_json(rows),
        "how ONE clause of a citing sentence": attribution_json(),
        "read the cited work's OWN body sections": evidence_router,
    })
    (assessment,), assessor = run(
        claims=(claim,), gen=gen, ver=ver_llm(), normalizer=norm, context=context)
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    # Lowest tuple_id (0 == BRCA1) supplies the emitted keys.
    assert assessment.claimed_entity_key == "HGNC:1100"
    assert assessment.evidence_entity_key == "HGNC:1101"
    confirmed = [t for t in assessor.records[0]["tuple_records"] if t["confirmed_mismatch"]]
    assert len(confirmed) == 2


def test_one_mismatch_one_equivalent_records_same_entity():
    claim, context, rows = _two_gene_claim()
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1101", "BRCA2", "exact", "HGNC", "s"),
            ("gene", "TP53"): ("HGNC:11998", "TP53", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1101"): "provably_distinct",
                   ("HGNC:11998", "HGNC:11998"): "equivalent"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )

    def evidence_router(prompt):
        if "BRCA1" in prompt:
            return evidence_json(evidence_surface="BRCA2", entity_span="BRCA2 loss",
                                 relation_span="accelerated tumor growth")
        return evidence_json(evidence_surface="TP53", entity_span="BRCA2 loss",
                             relation_span="accelerated tumor growth",
                             predicate="drives", object_="apoptosis",
                             direction="positive", population="cells")
    gen = ScriptedLLM({
        "Return ONLY a JSON array": tuples_json(rows),
        "how ONE clause of a citing sentence": attribution_json(),
        "read the cited work's OWN body sections": evidence_router,
    })
    (assessment,), assessor = run(
        claims=(claim,), gen=gen, ver=ver_llm(), normalizer=norm, context=context)
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    outcomes = {t["tuple_id"]: t["derived"] for t in assessor.records[0]["tuple_records"]}
    assert outcomes[0] == "CONFIRMED_MISMATCH"
    assert outcomes[1] == "SAME_ENTITY"


def test_one_mismatch_one_unjudgeable_holds_no_cherry_picking():
    claim, context, rows = _two_gene_claim()
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1101", "BRCA2", "exact", "HGNC", "s"),
            ("gene", "TP53"): ("HGNC:11998", "TP53", "exact", "HGNC", "s"),
            ("gene", "MDM2"): ("HGNC:6973", "MDM2", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1101"): "provably_distinct",
                   ("HGNC:11998", "HGNC:6973"): "unknown"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )

    def evidence_router(prompt):
        if "BRCA1" in prompt:
            return evidence_json(evidence_surface="BRCA2", entity_span="BRCA2 loss",
                                 relation_span="accelerated tumor growth")
        return evidence_json(evidence_surface="MDM2", entity_span="BRCA2 loss",
                             relation_span="accelerated tumor growth",
                             predicate="drives", object_="apoptosis",
                             direction="positive", population="cells")
    gen = ScriptedLLM({
        "Return ONLY a JSON array": tuples_json(rows),
        "how ONE clause of a citing sentence": attribution_json(),
        "read the cited work's OWN body sections": evidence_router,
    })
    (assessment,), _ = run(
        claims=(claim,), gen=gen, ver=ver_llm(), normalizer=norm, context=context)
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "relation_unknown" in assessment.rationale


# --------------------------------------------------------------------------
# Lineage: zoom / cross-type / synonym / equivalent.
# --------------------------------------------------------------------------
def test_cross_type_subsumes_is_granularity_zoom():
    # Claim gene BRCA1 vs paper BRCA1 variant: cross-type claim_subsumes_evidence.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("variant", "BRCA2"): ("VCV0001", "BRCA1 c.68A>G", "exact", "ClinVar", "s"),
        },
        relations={},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16"),
                  "variant": ("ClinVar", "2026-01", "2026-07-16")},
    )
    pol = policy(authorities={"gene": GENE_LOCK, "variant": VARIANT_LOCK})
    (assessment,), _ = run(
        gen=gen_llm(evidence=evidence_json(entity_type="variant")),
        normalizer=norm, cross=DictCross("claim_subsumes_evidence"), pol=pol)
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "granularity_zoom" in assessment.rationale


def test_cross_type_provably_distinct_never_f7():
    # Even a non-equivalent cross-type pair can only hold (cross enum has no
    # provably_distinct); Codex-3 #6.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("variant", "BRCA2"): ("VCV0001", "v", "exact", "ClinVar", "s"),
        },
        relations={},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16"),
                  "variant": ("ClinVar", "2026-01", "2026-07-16")},
    )
    pol = policy(authorities={"gene": GENE_LOCK, "variant": VARIANT_LOCK})
    (assessment,), _ = run(
        gen=gen_llm(evidence=evidence_json(entity_type="variant")),
        normalizer=norm, cross=DictCross("unknown"), pol=pol)
    assert assessment.state is EntityState.UNJUDGEABLE
    assert assessment.state is not EntityState.DIFFERENT_ENTITY_SUPPORTED


def test_cross_type_without_comparator_holds():
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("variant", "BRCA2"): ("VCV0001", "v", "exact", "ClinVar", "s"),
        },
        relations={},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16"),
                  "variant": ("ClinVar", "2026-01", "2026-07-16")},
    )
    pol = policy(authorities={"gene": GENE_LOCK, "variant": VARIANT_LOCK})
    (assessment,), _ = run(
        gen=gen_llm(evidence=evidence_json(entity_type="variant")),
        normalizer=norm, cross=None, pol=pol)
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "cross_comparator_unavailable" in assessment.rationale


def test_previous_symbol_synonym_accepted_is_same_entity():
    # Old symbol resolves to the SAME HGNC id via an accepted synonym.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "synonym", "HGNC", "prev_symbol"),
            ("gene", "BRCA2"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1100"): "equivalent"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )
    (assessment,), _ = run(normalizer=norm)
    assert assessment.state is EntityState.SAME_ENTITY


def test_equivalent_relation_is_same_entity():
    norm = gene_normalizer(relation="equivalent")
    # equivalent ids: rewire so BRCA1==BRCA2 map to the same id.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1100"): "equivalent"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )
    (assessment,), _ = run(normalizer=norm)
    assert assessment.state is EntityState.SAME_ENTITY


# --------------------------------------------------------------------------
# Relation comparability (schema D).
# --------------------------------------------------------------------------
def test_lexically_different_but_equivalent_relation_proceeds():
    # "suppresses" vs "inhibits" but comparator returns all-match.
    (assessment,), _ = run(
        relation_comparator=lambda c, e, *, call_llm: relation_all_match())
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED


# THE RELATION TUPLE NO LONGER GATES F7 (decision_rule_version
# f7_entity_scope_v2). These three cases pinned the opposite contract, and that
# contract is the reason a citation naming the wrong drug went unreported
# whenever it also paraphrased the finding or dropped a hedge. F7 answers one
# question -- does the paper's finding concern a different entity -- and a
# magnitude or wording difference is not evidence about that. Magnitude is F6's,
# strength and direction are F4's. The components are still COMPARED and still
# recorded; they just no longer veto.
def test_relation_component_mismatch_does_not_gate():
    (assessment,), assessor = run(
        relation_comparator=lambda c, e, *, call_llm: relation_all_match(direction="mismatch"))
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    # ...and the mismatch is still on the record for F4/F6 to read.
    tr = assessor.records[0]["tuple_records"][0]
    assert tr["relation_component_result"]["direction"] == "mismatch"


def test_relation_component_unknown_does_not_gate():
    (assessment,), _ = run(
        relation_comparator=lambda c, e, *, call_llm: relation_all_match(object="unknown"))
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED


def test_verifier_relation_equivalence_does_not_gate():
    # Schema E still ANSWERS relation_tuple_equivalent and the answer is kept;
    # it is simply not one of the four booleans that must hold to accuse.
    (assessment,), assessor = run(ver=ver_llm(verifier_json(equivalent=False)))
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    assert assessor.records[0]["tuple_records"][0]["verifier"][
        "relation_tuple_equivalent"] is False


# --------------------------------------------------------------------------
# Evidence-span / own-finding / section-kind holds.
# --------------------------------------------------------------------------
def test_relation_span_from_methods_only_holds():
    # Relation span pointed at the methods section -> not a valid outcome span.
    ev = evidence_json(relation_section=METHODS.content_sha256,
                       relation_span="genotyped by PCR")
    (assessment,), _ = run(gen=gen_llm(evidence=ev))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "no_valid_relation_span" in assessment.rationale


def test_relation_span_not_own_finding_holds():
    (assessment,), _ = run(gen=gen_llm(evidence=evidence_json(papers_own_finding=False)))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "papers_own_finding_false" in assessment.rationale


def test_identical_entity_and_relation_span_holds():
    ev = evidence_json(entity_span="accelerated tumor growth",
                       relation_span="accelerated tumor growth")
    (assessment,), _ = run(gen=gen_llm(evidence=ev))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "entity_relation_span_not_distinct" in assessment.rationale


# --------------------------------------------------------------------------
# Context gates.
# --------------------------------------------------------------------------
def test_paper_not_resolved_holds():
    (assessment,), _ = run(context=make_context(paper_resolved=False))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "paper_not_resolved" in assessment.rationale


def test_evidence_source_insufficient_holds():
    # Only sections are excluded at construction; a context with no body sections
    # trips the evidence gate. (SectionText forbids abstract/discussion, so
    # "no usable section" is modeled as an empty body_sections tuple.)
    (assessment,), _ = run(context=make_context(sections=()))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "evidence_source_insufficient" in assessment.rationale


def test_target_reference_missing_holds():
    context = make_context(bundled=("ref99",),
                           clause_refs=(ClaimClauseRef(0, CLAUSE, ("ref99",)),))
    (assessment,), _ = run(context=context)
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "target_reference_missing" in assessment.rationale


# --------------------------------------------------------------------------
# Attribution truth-table precedence (schema A).
# --------------------------------------------------------------------------
def test_sibling_reference_possible_precedence():
    # sibling_reference_possible True dominates every other field.
    attr = attribution_json(sibling_reference_possible=True, attribution="direct",
                            target_supported=True)
    (assessment,), _ = run(gen=gen_llm(attribution=attr))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "multi_reference_attribution_ambiguous" in assessment.rationale


def test_analogical_citation_holds():
    (assessment,), _ = run(gen=gen_llm(attribution=attribution_json(attribution="analogy")))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "analogical_citation" in assessment.rationale


def test_attribution_inconsistent_holds():
    (assessment,), _ = run(
        gen=gen_llm(attribution=attribution_json(target_supported=False)))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "attribution_inconsistent" in assessment.rationale


# --------------------------------------------------------------------------
# Authority lock / normalization ambiguity.
# --------------------------------------------------------------------------
def test_evidence_type_lacks_lock_holds():
    # Evidence is a variant but only gene is locked.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("variant", "BRCA2"): ("VCV1", "v", "exact", "ClinVar", "s"),
        },
        relations={},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16"),
                  "variant": ("ClinVar", "2026-01", "2026-07-16")},
    )
    (assessment,), _ = run(
        gen=gen_llm(evidence=evidence_json(entity_type="variant")),
        normalizer=norm, cross=DictCross("unknown"))  # gene-only policy
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "authority_not_locked" in assessment.rationale


def test_normalization_ambiguous_holds():
    (assessment,), _ = run(normalizer=gene_normalizer(brca1_status="ambiguous"))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "normalization_ambiguous" in assessment.rationale


def test_synonym_not_accepted_is_ambiguous():
    # A synonym mapping when the lock does not accept synonyms is not confident.
    lock = dict(GENE_LOCK, accept_synonym_as_equivalent=False)
    (assessment,), _ = run(
        normalizer=gene_normalizer(brca1_status="synonym"),
        pol=policy(authorities={"gene": lock}))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "normalization_ambiguous" in assessment.rationale


def test_cross_db_equivalence_accepts_registered_crosswalk():
    lock = dict(GENE_LOCK, accept_synonym_as_equivalent=False,
                cross_db_equivalences=[["Ensembl", "HGNC", "ensembl_xref", "2026-01"]])
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "synonym", "Ensembl", "ensembl_xref"),
            ("gene", "BRCA2"): ("HGNC:1101", "BRCA2", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1101"): "provably_distinct"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )
    (assessment,), _ = run(normalizer=norm, pol=policy(authorities={"gene": lock}))
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED


# --------------------------------------------------------------------------
# Verifier (schema E).
# --------------------------------------------------------------------------
def test_verifier_disagreement_holds():
    (assessment,), _ = run(ver=ver_llm(verifier_json(differ=False)))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "verifier_disagreement" in assessment.rationale


def test_verifier_incomplete_enumeration_holds():
    (assessment,), _ = run(ver=ver_llm(verifier_json(enumerated=False)))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "verifier_disagreement" in assessment.rationale


def test_verifier_never_called_for_negatives():
    # When lineage already holds (unknown), the verifier is never consulted.
    ver = ver_llm()
    run(normalizer=gene_normalizer(relation="unknown"), ver=ver)
    assert ver.calls == []


# --------------------------------------------------------------------------
# no_assessable_entity.
# --------------------------------------------------------------------------
def test_empty_tuple_array_is_no_assessable_entity():
    (assessment,), _ = run(gen=gen_llm(tuples="[]"))
    assert assessment.state is EntityState.UNJUDGEABLE
    assert "no_assessable_entity" in assessment.rationale


# --------------------------------------------------------------------------
# ValueError provenance / malformed cases.
# --------------------------------------------------------------------------
def test_content_hash_mismatch_raises_at_construction():
    with pytest.raises(ValueError):
        SectionText("results", "hello world", WORK, "0" * 64)


def test_wrong_work_section_raises_at_construction():
    good = SectionText("results", RESULTS_TEXT, "PMCID:OTHER", _sha(RESULTS_TEXT))
    with pytest.raises(ValueError):
        make_context(sections=(good,))


def test_duplicate_tuple_id_raises():
    rows = [
        {"tuple_id": 0, "entity_type": "gene", "claim_surface": "BRCA1",
         "clause_span": CLAUSE, "predicate": "p", "object": "o",
         "direction": "d", "population": "pop"},
        {"tuple_id": 0, "entity_type": "gene", "claim_surface": "BRCA1",
         "clause_span": CLAUSE, "predicate": "p", "object": "o",
         "direction": "d", "population": "pop"},
    ]
    with pytest.raises(ValueError):
        run(gen=gen_llm(tuples=tuples_json(rows)))


def test_non_int_tuple_id_raises():
    rows = [{"tuple_id": "0", "entity_type": "gene", "claim_surface": "BRCA1",
             "clause_span": CLAUSE, "predicate": "p", "object": "o",
             "direction": "d", "population": "pop"}]
    with pytest.raises(ValueError):
        run(gen=gen_llm(tuples=tuples_json(rows)))


def test_off_enum_entity_type_raises():
    rows = [{"tuple_id": 0, "entity_type": "protein", "claim_surface": "BRCA1",
             "clause_span": CLAUSE, "predicate": "p", "object": "o",
             "direction": "d", "population": "pop"}]
    with pytest.raises(ValueError):
        run(gen=gen_llm(tuples=tuples_json(rows)))


def test_clause_span_not_verbatim_raises():
    rows = [{"tuple_id": 0, "entity_type": "gene", "claim_surface": "BRCA1",
             "clause_span": "NOT IN CLAIM", "predicate": "p", "object": "o",
             "direction": "d", "population": "pop"}]
    with pytest.raises(ValueError):
        run(gen=gen_llm(tuples=tuples_json(rows)))


def test_span_bound_to_unknown_section_raises():
    with pytest.raises(ValueError):
        run(gen=gen_llm(evidence=evidence_json(entity_section="f" * 64)))


def test_non_verbatim_relation_span_raises():
    with pytest.raises(ValueError):
        run(gen=gen_llm(evidence=evidence_json(relation_span="not in section")))


def test_normalize_lock_mismatch_raises():
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1101", "BRCA2", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1101"): "provably_distinct"},
        lock_map={"gene": ("HGNC", "WRONG-VERSION", "2026-07-16")},
    )
    with pytest.raises(ValueError):
        run(normalizer=norm)


def test_malformed_verifier_json_raises():
    with pytest.raises(ValueError):
        run(ver=ver_llm("{not json"))


def test_malformed_relation_comparator_raises():
    with pytest.raises(ValueError):
        run(relation_comparator=lambda c, e, *, call_llm: {"predicate": "match"})


def test_cross_relation_provably_distinct_is_malformed():
    # provably_distinct is off-enum for the cross comparator.
    norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("variant", "BRCA2"): ("VCV1", "v", "exact", "ClinVar", "s"),
        },
        relations={},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16"),
                  "variant": ("ClinVar", "2026-01", "2026-07-16")},
    )
    pol = policy(authorities={"gene": GENE_LOCK, "variant": VARIANT_LOCK})
    with pytest.raises(ValueError):
        run(gen=gen_llm(evidence=evidence_json(entity_type="variant")),
            normalizer=norm, cross=DictCross("provably_distinct"), pol=pol)


def test_verifier_must_be_distinct_callable():
    shared = gen_llm()
    with pytest.raises(ValueError):
        make_entity_assessor(
            call_llm=shared, verifier_call_llm=shared, normalizer=gene_normalizer(),
            cross_comparator=None,
            relation_comparator=lambda c, e, *, call_llm: relation_all_match(),
            evidence_context=make_context(), policy=policy())


def test_malformed_authorities_json_raises():
    with pytest.raises(ValueError):
        build(pol=F7Policy(authorities_json="{not json", cross_ontology_lock=CROSS_LOCK))


# --------------------------------------------------------------------------
# Shared-span provenance disambiguation (Codex-3 precision).
# --------------------------------------------------------------------------
def test_two_sections_share_span_bound_by_sha256():
    # Two results sections contain the same relation phrase; schema C names one
    # by its content_sha256, and the span validates against THAT exact section.
    shared = "accelerated tumor growth"
    text_a = "Section A: BRCA2 loss " + shared + " in cohort A."
    text_b = "Section B: control " + shared + " differently in cohort B."
    sec_a = SectionText("results", text_a, WORK, _sha(text_a))
    sec_b = SectionText("results", text_b, WORK, _sha(text_b))
    context = make_context(sections=(sec_a, sec_b, METHODS))
    ev = evidence_json(entity_section=sec_a.content_sha256, entity_span="BRCA2 loss",
                       relation_section=sec_b.content_sha256, relation_span=shared)
    (assessment,), assessor = run(gen=gen_llm(evidence=ev), context=context)
    assert assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    tr = assessor.records[0]["tuple_records"][0]
    assert sec_b.content_sha256 in tr["used_section_sha256s"]


# --------------------------------------------------------------------------
# Durable records + replay/tamper validator.
# --------------------------------------------------------------------------
def test_records_are_hash_bound_and_validate():
    context = make_context()
    (_,), assessor = run(context=context)
    rec = assessor.records[0]
    # Every hash recomputes cleanly.
    validate_f7_record(rec, context)
    assert rec["policy_sha256"] == assessor.policy_sha256
    assert rec["record_sha256"] == record_sha256(rec)


def test_validate_detects_tampered_claim():
    context = make_context()
    (_,), assessor = run(context=context)
    rec = copy.deepcopy(assessor.records[0])
    rec["claim_text"] = rec["claim_text"] + " TAMPERED"
    with pytest.raises(ValueError):
        validate_f7_record(rec, context)


def test_validate_detects_tampered_tuple_record():
    context = make_context()
    (_,), assessor = run(context=context)
    rec = copy.deepcopy(assessor.records[0])
    rec["tuple_records"][0]["claimed_id"] = "HGNC:9999"
    with pytest.raises(ValueError):
        validate_f7_record(rec, context)


def test_validate_detects_tampered_record_field():
    context = make_context()
    (_,), assessor = run(context=context)
    rec = copy.deepcopy(assessor.records[0])
    rec["derived"] = "SAME_ENTITY"
    with pytest.raises(ValueError):
        validate_f7_record(rec, context)


def test_validate_detects_replay_against_different_context():
    context = make_context()
    (_,), assessor = run(context=context)
    rec = assessor.records[0]
    other = make_context(citing_sentence="A totally different citing sentence [12].")
    with pytest.raises(ValueError):
        validate_f7_record(rec, other)


# --------------------------------------------------------------------------
# Control-flow split: same normalizer wiring, F7 vs SAME_ENTITY by relation.
# --------------------------------------------------------------------------
def test_gene_hgnc_fixture_exercises_f7_vs_same_split():
    (f7_assessment,), _ = run(normalizer=gene_normalizer(relation="provably_distinct"))
    assert f7_assessment.state is EntityState.DIFFERENT_ENTITY_SUPPORTED
    same_norm = DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1100"): "equivalent"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )
    (same_assessment,), _ = run(normalizer=same_norm)
    assert same_assessment.state is EntityState.SAME_ENTITY
