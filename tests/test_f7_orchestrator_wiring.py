"""Acceptance tests for the F7 (entity) seam wiring in ``judgment_run``.

F7 is wired exactly the way F3/F5 are: an injected seam set plus an evidence
builder. Supply both and ``make_entity_assessor`` runs and its assessments reach
``decide_judgment``; supply neither (or only one) and the entity seam stays
empty, so an unwired F7 is never asserted -- positively OR as a confident
negative.

The offline seams here validate CONTROL FLOW only (f7_entity spec Sec 14): every
model call, normalizer, and comparator is a stub. These are code-path fixtures.
None of them is evaluation data, a gold label, or an input to any reported
number.

NOTE (why F7 is wired but not yet live): ``SectionText`` admits only
results/methods/table/figure sections, and the pinned evidence scope is
``abstract_snapshot``. F7 therefore returns nothing on a real run until the
evidence scope moves to full text and an extraction path exists. Wiring the seam
is independent of that decision -- it is what removes the hardcoded ``()`` that
silently zeroed the fault class. ``test_abstract_built_evidence_context_is_quarantined``
pins that blocker so it cannot regress unnoticed.
"""
from __future__ import annotations

import json

import pytest

from cde.claims import band as jb
from cde.diagnose import pipeline as jr
from . import test_f7_entity as f7t
from cde.diagnose.entity import ClaimClauseRef, EvidenceContext, SectionText
from cde.diagnose.engine import EntityState, SupportState
from .test_judgment_run import (
    abstract_ok,
    disc_llm,
    extractor_of,
    f4_json,
    judge_established,
    make_ref,
)


# --------------------------------------------------------------------------
# seam / builder fixtures (stubs only -- no network, no model)
# --------------------------------------------------------------------------
ORIGINATES = json.dumps(
    {"verdict": "originates", "evidence_span": "", "rationale": "x"})


def seams(*, gen=None, ver=None, normalizer=None, cross=None,
          relation_comparator=None):
    """The five ``make_entity_assessor`` callables, as judge_pair expects them."""
    return {
        "call_llm": gen if gen is not None else f7t.gen_llm(),
        "verifier_call_llm": ver if ver is not None else f7t.ver_llm(),
        "normalizer": normalizer if normalizer is not None else f7t.gene_normalizer(),
        "cross_comparator": cross,
        "relation_comparator": relation_comparator if relation_comparator is not None
        else (lambda claimed, evidence, *, call_llm: f7t.relation_all_match()),
    }


def builder(context=None):
    return lambda _item: context if context is not None else f7t.make_context()


def same_entity_normalizer():
    """BRCA1 and BRCA2 both resolve to HGNC:1100 -> equivalent -> SAME_ENTITY."""
    return f7t.DictNormalizer(
        ids={
            ("gene", "BRCA1"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
            ("gene", "BRCA2"): ("HGNC:1100", "BRCA1", "exact", "HGNC", "s"),
        },
        relations={("HGNC:1100", "HGNC:1100"): "equivalent"},
        lock_map={"gene": ("HGNC", "2026-01", "2026-07-16")},
    )


def pair(*, extractor=None, coverage_judge=None, **kw):
    """Type one item through judge_pair with the F7-shaped claim by default."""
    item = jb.build_item(make_ref("c"))
    return jr.judge_pair(
        item,
        extractor=extractor if extractor is not None else extractor_of(f7t.CLAIM),
        coverage_judge=coverage_judge if coverage_judge is not None
        else judge_established(True),
        fetch_abstract=abstract_ok,
        **kw)


def wired(**kw):
    """judge_pair on the WIRED ladder (F4 NOT_F4, F3 originates) so the F7 branch
    is the only thing under test."""
    kw.setdefault("discriminator_call_llm",
                  disc_llm(f4=f4_json(), v2=ORIGINATES))
    return pair(**kw)


def spy_on_decide(monkeypatch):
    """Capture the kwargs judge_pair hands to decide_judgment."""
    seen = {}
    real = jr.decide_judgment

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(jr, "decide_judgment", spy)
    return seen


# --------------------------------------------------------------------------
# default path: no seams -> empty entity seam, unchanged behavior
# --------------------------------------------------------------------------
def test_default_path_hands_the_engine_an_empty_entity_seam(monkeypatch):
    seen = spy_on_decide(monkeypatch)
    pair()
    assert seen["entity_assessments"] == ()


def test_default_path_emits_no_f7_records():
    assert "f7_records" not in pair()
    assert "F7" not in pair()["findings"]


# Golden values captured by running this exact scenario matrix at 31c1939 (the
# base commit) through judge_pair with default arguments. The dumps were compared
# whole and were byte-identical; these rows pin the observable contract so the
# unwired path can never drift.
_LEGACY_GOLDEN = [
    ("legacy_full_coverage", dict(extractor=extractor_of("Drug X reduces outcome Y"),
                                  coverage_judge=judge_established(True)),
     jr.DISP_HELD_FULL_COVERAGE, None, "FULL_COVERAGE", []),
    ("legacy_f6", dict(extractor=extractor_of("Drug X reduces Y", "Drug X cures Z"),
                       coverage_judge=judge_established(False, True)),
     jr.DISP_PREDICTED, "F6", "F6_FLAGGED", ["F6"]),
    ("legacy_insufficient", dict(extractor=extractor_of("Drug X reduces outcome Y"),
                                 coverage_judge=judge_established(None)),
     jr.DISP_HELD_INSUFFICIENT, None, "HELD_LOW_CONFIDENCE", []),
    # ROUTE DELIBERATELY REPINNED, 2026-08-11 (ZD calibration item 1): this row's
    # golden route was "FULL_COVERAGE" -- the vacuous all([]) -- and that was the
    # defect, not the contract. Note what did NOT move: the disposition was
    # ALREADY DISP_HELD_NO_CLAIMS and the label was already None, so the engine
    # downstream had the case right all along while the route lied about it. The
    # repin is the two ends agreeing, not a behavior change here.
    ("legacy_no_claims", dict(extractor=extractor_of(),
                              coverage_judge=judge_established()),
     jr.DISP_HELD_NO_CLAIMS, None, "NO_CLAIMS", []),
    ("wired_pending_f5_f7", dict(extractor=extractor_of("Drug X reduces outcome Y"),
                                 coverage_judge=judge_established(True),
                                 discriminator_call_llm=disc_llm(f4=f4_json(),
                                                                 v2=ORIGINATES)),
     jr.DISP_HELD_PENDING_F5_F7, None, "FULL_COVERAGE", []),
    ("wired_f6", dict(extractor=extractor_of("Drug X reduces Y", "Drug X cures Z"),
                      coverage_judge=judge_established(False, True),
                      discriminator_call_llm=disc_llm(f4=f4_json())),
     jr.DISP_PREDICTED, "F6", "F6_FLAGGED", ["F6"]),
    ("wired_strength_unjudgeable",
     dict(extractor=extractor_of("Drug X reduces outcome Y"),
          coverage_judge=judge_established(True),
          discriminator_call_llm=disc_llm(f4=f4_json(load="unknown"))),
     jr.DISP_HELD_STRENGTH_UNJUDGEABLE, None, "FULL_COVERAGE", []),
]


@pytest.mark.parametrize(
    "name,kwargs,disposition,label,route,findings",
    _LEGACY_GOLDEN, ids=[row[0] for row in _LEGACY_GOLDEN])
def test_unwired_f7_reproduces_the_base_commit_exactly(
        name, kwargs, disposition, label, route, findings):
    rec = pair(**kwargs)
    assert rec["disposition"] == disposition
    assert rec["label"] == ([label] if label else [])
    assert rec["route"] == route
    assert rec["findings"] == findings
    assert "f7_records" not in rec


# --------------------------------------------------------------------------
# half-wired: BOTH the seams and the builder are required
# --------------------------------------------------------------------------
def test_seams_without_an_evidence_builder_do_not_run_f7(monkeypatch):
    seen = spy_on_decide(monkeypatch)
    rec = pair(f7_seams=seams(), f7_evidence_builder=None)
    assert seen["entity_assessments"] == ()
    assert "f7_records" not in rec
    assert "F7" not in rec["findings"]


def test_evidence_builder_without_seams_does_not_run_f7(monkeypatch):
    seen = spy_on_decide(monkeypatch)
    rec = pair(f7_seams=None, f7_evidence_builder=builder())
    assert seen["entity_assessments"] == ()
    assert "f7_records" not in rec
    assert "F7" not in rec["findings"]


def test_a_half_wired_f7_never_calls_its_seams():
    gen = f7t.gen_llm()
    ver = f7t.ver_llm()
    pair(f7_seams=seams(gen=gen, ver=ver), f7_evidence_builder=None)
    assert gen.calls == []
    assert ver.calls == []


# --------------------------------------------------------------------------
# wired: the three entity states
# --------------------------------------------------------------------------
def test_wired_different_entity_supported_is_a_predicted_f7(monkeypatch):
    seen = spy_on_decide(monkeypatch)
    rec = wired(f7_seams=seams(), f7_evidence_builder=builder(),
                f7_policy=f7t.policy())
    assert [a.state for a in seen["entity_assessments"]] == [
        EntityState.DIFFERENT_ENTITY_SUPPORTED]
    assert rec["disposition"] == jr.DISP_PREDICTED
    assert rec["label"] == ["F7"]
    assert "F7" in rec["findings"]
    assert len(rec["f7_records"]) == 1


def test_wired_same_entity_emits_no_f7_and_falls_through_the_ladder(monkeypatch):
    seen = spy_on_decide(monkeypatch)
    rec = wired(f7_seams=seams(normalizer=same_entity_normalizer()),
                f7_evidence_builder=builder(), f7_policy=f7t.policy())
    assert [a.state for a in seen["entity_assessments"]] == [EntityState.SAME_ENTITY]
    assert "F7" not in rec["findings"]
    assert rec["label"] == []
    # Falls through to the pre-existing ladder: full coverage, NOT_F4, proper origin.
    assert rec["disposition"] == jr.DISP_HELD_PENDING_F5_F7


def test_wired_unjudgeable_holds_and_never_fabricates_a_negative(monkeypatch):
    seen = spy_on_decide(monkeypatch)
    rec = wired(
        f7_seams=seams(ver=f7t.ver_llm(f7t.verifier_json(differ=False))),
        f7_evidence_builder=builder(), f7_policy=f7t.policy())
    assert [a.state for a in seen["entity_assessments"]] == [EntityState.UNJUDGEABLE]
    assert "F7" not in rec["findings"]
    assert rec["label"] == []
    assert "entity evidence is unjudgeable" in rec["hold_reasons"]
    # An unjudgeable entity is a HOLD, never a confident SAME_ENTITY.
    assert rec["f7_records"][0]["derived"] == "UNJUDGEABLE"


# --------------------------------------------------------------------------
# precedence + the no-support-gate rule
# --------------------------------------------------------------------------
def test_f7_outranks_f6_when_both_fire():
    rec = wired(coverage_judge=judge_established(False),
                f7_seams=seams(), f7_evidence_builder=builder(),
                f7_policy=f7t.policy())
    assert set(rec["findings"]) == {"F7", "F6"}
    assert rec["findings"][0] == "F7"          # engine order: F7, F6, F4, F3, F5
    # PRECEDENCE STILL DECIDES ROUTING, not the label. The disposition is the
    # single-valued thing F7 outranks F6 for; the label records both faults,
    # because both were established and hiding one behind the other undercounts
    # it in every rate derived from the label.
    assert rec["disposition"] == jr.DISP_PREDICTED
    assert rec["label"] == ["F7", "F6"]


# One wiring per SupportState member. WEAKER_STRENGTH cannot come from a coverage
# verdict -- it is F4's refinement of a SUPPORTED claim -- so it needs a firing F4
# whose spans are verbatim in the F7 claim and in the abstract.
_F4_OVERSTATES_THE_F7_CLAIM = f4_json(
    load="causal_force", citing_span="suppresses tumor growth",
    cited_span="controlled study",
    dims={"causal_force": {"citing": "causation", "cited": "association"}})

_SUPPORT_WIRING = {
    SupportState.SUPPORTED: dict(coverage_judge=judge_established(True)),
    SupportState.WEAKER_STRENGTH: dict(
        coverage_judge=judge_established(True),
        discriminator_call_llm=disc_llm(f4=_F4_OVERSTATES_THE_F7_CLAIM)),
    SupportState.UNESTABLISHED: dict(coverage_judge=judge_established(False)),
    SupportState.UNJUDGEABLE: dict(coverage_judge=judge_established(None)),
}


@pytest.mark.parametrize("state", list(SupportState), ids=lambda s: s.value)
def test_f7_runs_under_every_support_state(state, monkeypatch):
    """F7 is NOT gated on support: an entity mismatch may coexist with F6/F4, so
    support is context, not a veto (f7_entity boundaries pin the same rule at the
    leaf, parametrized over this same enum). Copying F3's all_supported gate here
    would silently zero the class. Parametrizing over ``list(SupportState)`` rather
    than a hand-listed subset means a new member cannot be added without landing
    here."""
    seen = spy_on_decide(monkeypatch)
    gen = f7t.gen_llm()
    rec = wired(f7_seams=seams(gen=gen), f7_evidence_builder=builder(),
                f7_policy=f7t.policy(), **_SUPPORT_WIRING[state])
    # The wiring really did produce the state under test, not a lookalike.
    assert [row.state for row in seen["claim_support"]] == [state]
    assert gen.calls, "F7 seams must be called regardless of support state"
    assert "F7" in rec["findings"]
    # Every finding the pair established, in engine order -- under UNESTABLISHED
    # that is F6 alongside F7, and under WEAKER_STRENGTH it is F4.
    assert rec["label"] == list(rec["findings"])
    assert rec["label"][0] == "F7"
    assert rec["f7_records"][0]["derived"] == "DIFFERENT_ENTITY_SUPPORTED"


# --------------------------------------------------------------------------
# ValueError propagation -> caller quarantine
# --------------------------------------------------------------------------
def test_abstract_built_evidence_context_is_quarantined():
    """The standing blocker: F7 structurally cannot run on an abstract, because
    SectionText admits only results/methods/table/figure. An evidence builder that
    hands it an abstract raises, and the runner quarantines the pair rather than
    emitting a fabricated entity verdict."""
    with pytest.raises(ValueError, match="no abstract/intro/discussion"):
        SectionText("abstract", "text", f7t.WORK, f7t._sha("text"))


def test_judge_pair_does_not_swallow_the_f7_value_error():
    shared = f7t.gen_llm()
    with pytest.raises(ValueError, match="DISTINCT"):
        wired(f7_seams=seams(gen=shared, ver=shared),
              f7_evidence_builder=builder(), f7_policy=f7t.policy())


# --------------------------------------------------------------------------
# blind annotation payload stays blind
# --------------------------------------------------------------------------
def _walk(node):
    """Yield every key and every string value at any nesting depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk(value)
    elif isinstance(node, str):
        yield node


def test_f7_records_never_leak_into_the_blind_annotation_payload(
        tmp_path, monkeypatch):
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("c")])
    out_dir = tmp_path / "out"
    jr.run_natural_judgment(
        str(tmp_path), str(out_dir), extractor=extractor_of(f7t.CLAIM),
        coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
        preband_disposition={"c": "cleared"}, model="test-model",
        discriminator_call_llm=disc_llm(f4=f4_json(), v2=ORIGINATES),
        f7_seams=seams(), f7_evidence_builder=builder(), f7_policy=f7t.policy())

    rows = [json.loads(l) for l in
            (out_dir / "judgment_predictions.jsonl").read_text().splitlines()]
    assert rows[0]["label"] == ["F7"]            # an F7 record IS present
    assert rows[0]["f7_records"]

    payloads = [json.loads(l) for l in
                (out_dir / "judgment_band_annotation_queue.jsonl")
                .read_text().splitlines()]
    assert len(payloads) == 1
    tokens = set(_walk(payloads[0]))
    for forbidden in ("proposed_route", "proposed_verdict", "rationale",
                      "f7_records", "derived", "confirmed_mismatch",
                      "proposed_corrected_label", "proposed_corrected_id",
                      "tuple_records", "raw_responses", "verifier",
                      "label", "disposition", "findings"):
        assert forbidden not in tokens, f"blind payload leaked {forbidden!r}"
