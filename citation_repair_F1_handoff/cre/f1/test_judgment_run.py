"""Offline tests for the F3-F7 natural-paper orchestrator (judgment_run).

All model/network work is injected as stubs. No paid call, no network. Covers
the three live coverage routes, fail-closed pre-band behavior, the wired F3/F4
path in development AND formal modes, the up-front configuration abort, the
whole-record hash chain + sidecar, and the manifest lifecycle
(in_progress -> complete, immutable complete, chain-validated resume, torn-tail
preservation).
"""
from __future__ import annotations

import json

import pytest

from . import judgment_run as jr
from .f4_strength import F4Policy
from .schema import Reference, ClaimedRef


# --------------------------------------------------------------------------
# fixtures / helpers
# --------------------------------------------------------------------------
def make_ref(cid, *, citance="Drug X reduces outcome Y [1].", pmid="111",
             src="PMC1"):
    return Reference(
        citation_id=cid, citance=citance,
        claimed=ClaimedRef(claimed_pmid=pmid, title="Cited title"),
        cited_reference_marker="1", source_pmcid=src, source_pmid="900",
        source_title="Citing title",
    )


ABSTRACT = "Drug X reduced outcome Y in a controlled study."


def abstract_ok(_pmid):
    return ABSTRACT


def abstract_missing(_pmid):
    return None


def judge_established(*values):
    """Return a coverage_judge stub yielding one verdict per claim in order."""
    def judge(claims, evidence):
        out = []
        for i, c in enumerate(claims):
            v = values[i] if i < len(values) else None
            out.append({"established": v, "rationale": f"r{i}",
                        "evidence_span": ABSTRACT if v else ""})
        return out
    return judge


def extractor_of(*claims):
    def extractor(_sentence):
        return list(claims)
    return extractor


def run(tmp_path, refs, *, extractor, coverage_judge, fetch_abstract=abstract_ok,
        disposition, monkeypatch, pubtypes_lookup=None):
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml", lambda path, source_pmcid=None: refs)
    out_dir = tmp_path / "out"
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(out_dir), extractor=extractor,
        coverage_judge=coverage_judge, fetch_abstract=fetch_abstract,
        preband_disposition=disposition, pubtypes_lookup=pubtypes_lookup,
        model="test-model",
    )
    rows = [json.loads(l) for l in
            (out_dir / "judgment_predictions.jsonl").read_text().splitlines()]
    return manifest, rows


CLEARED = {"c": "cleared"}


# --------------------------------------------------------------------------
# the three live coverage routes
# --------------------------------------------------------------------------
def test_coverage_gap_is_terminal_f6(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y", "Drug X cures Z"),
        coverage_judge=judge_established(False, True),
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert len(rows) == 1
    r = rows[0]
    assert r["disposition"] == jr.DISP_PREDICTED
    assert r["label"] == "F6"
    assert "F6" in r["findings"]
    assert r["route"] == "F6_FLAGGED"


def test_full_coverage_is_held_never_accurate(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition=CLEARED, monkeypatch=monkeypatch)
    r = rows[0]
    assert r["disposition"] == jr.DISP_HELD_FULL_COVERAGE
    assert r["label"] is None
    assert r["label"] != "accurate"


def test_some_unknown_is_held_insufficient(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y", "Drug X cures Z"),
        coverage_judge=judge_established(True, None),
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert rows[0]["disposition"] == jr.DISP_HELD_INSUFFICIENT


def test_no_atomic_claims_is_held(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of(),                 # sentence yields no atomic claim
        coverage_judge=judge_established(),
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert rows[0]["disposition"] == jr.DISP_HELD_NO_CLAIMS


# --------------------------------------------------------------------------
# fail-closed behaviors
# --------------------------------------------------------------------------
def test_malformed_model_output_quarantines_and_continues(tmp_path, monkeypatch):
    def bad_extractor(_s):
        raise ValueError("```json fence / strict-schema failure")

    _, rows = run(
        tmp_path, [make_ref("c"), make_ref("c")],   # two pairs; first bad
        extractor=bad_extractor, coverage_judge=judge_established(),
        disposition={"c": "cleared"}, monkeypatch=monkeypatch)
    assert all(r["disposition"] == jr.DISP_QUARANTINE_PARSE for r in rows)
    assert "parse_error" in rows[0] and rows[0]["parse_error"]


def test_preband_f2_is_excluded_without_coverage_call(tmp_path, monkeypatch):
    calls = {"n": 0}

    def counting_judge(claims, evidence):
        calls["n"] += 1
        return judge_established(True)(claims, evidence)

    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=counting_judge,
        disposition={"c": "F2"}, monkeypatch=monkeypatch)
    r = rows[0]
    assert r["disposition"] == jr.DISP_EXCLUDED_PREBAND
    assert r["preband_label"] == "F2"
    assert calls["n"] == 0                          # coverage never ran


def test_missing_disposition_fails_closed(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition={}, monkeypatch=monkeypatch)     # id absent
    assert rows[0]["disposition"] == jr.DISP_EXCLUDED_PREBAND_MISSING


def test_none_disposition_excludes_everything(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition=None, monkeypatch=monkeypatch)
    assert rows[0]["disposition"] == jr.DISP_EXCLUDED_PREBAND_MISSING


def test_structural_exclusions(tmp_path, monkeypatch):
    refs = [
        make_ref("no_citance", citance=""),
        make_ref("no_pmid", pmid=""),
    ]
    _, rows = run(
        tmp_path, refs, extractor=extractor_of("x"),
        coverage_judge=judge_established(True),
        disposition={"no_citance": "cleared", "no_pmid": "cleared"},
        monkeypatch=monkeypatch)
    by = {r["citation_id"]: r["disposition"] for r in rows}
    assert by["no_citance"] == jr.DISP_EXCLUDED_NO_CITANCE
    assert by["no_pmid"] == jr.DISP_EXCLUDED_NO_CITED_PMID


# --------------------------------------------------------------------------
# accounting + provenance discipline
# --------------------------------------------------------------------------
def test_accounting_identity(tmp_path, monkeypatch):
    refs = [make_ref("a"), make_ref("b"), make_ref("c"),
            make_ref("d", citance=""), make_ref("e")]
    disposition = {"a": "cleared", "b": "cleared", "c": "F2", "e": "cleared"}
    manifest, rows = run(
        tmp_path, refs,
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition=disposition, monkeypatch=monkeypatch)
    assert manifest["refs_seen"] == 5
    assert manifest["total_records"] == 5
    assert manifest["accounting_ok"] is True
    assert len(rows) == 5
    assert sum(v for k, v in manifest["counts"].items()
               if not k.startswith(jr.DISP_EXCLUDED_PREBAND + ":")) == 5


def test_evidence_and_rationale_preserved(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition=CLEARED, monkeypatch=monkeypatch)
    r = rows[0]
    assert r["evidence"]["cited_abstract"] == ABSTRACT
    assert r["evidence_usable"] is True
    assert r["coverage_verdicts"][0]["rationale"] == "r0"
    assert r["coverage_verdicts"][0]["evidence_span"] == ABSTRACT


def test_unusable_abstract_holds(tmp_path, monkeypatch):
    _, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(None),      # judge yields unknown
        fetch_abstract=abstract_missing,
        disposition=CLEARED, monkeypatch=monkeypatch)
    r = rows[0]
    assert r["evidence_usable"] is False
    assert r["disposition"] == jr.DISP_HELD_INSUFFICIENT


def test_manifest_pins_versions_hashes_and_prompts(tmp_path, monkeypatch):
    manifest, _ = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert manifest["claim_extract_prompt_version"] == "claim_extract_v3"
    assert manifest["coverage_prompt_version"] == "coverage_v2"
    # Coverage substrate AND the discriminator implementation are pinned.
    for name in ("judgment_engine.py", "band_prompts.py",
                 "f4_strength.py", "f3_provenance.py", "judgment_run.py"):
        assert name in manifest["module_sha256"], name
    assert set(manifest["prompt_sha256"]) >= {
        "F4_STRENGTH_PROMPT", "F4_VERIFIER_PROMPT",
        "F3_V2_ORIGIN_PROMPT", "F3_V3_SELECT_PROMPT", "F3_V4_LOOPCLOSE_PROMPT",
    }
    assert all(len(v) == 64 for v in manifest["prompt_sha256"].values())
    assert manifest["model"] == "test-model"


def test_source_never_asserts_confident_negatives_for_unbuilt_gates():
    """The orchestrator stays a THIN wiring layer: it must never inline-construct a
    temporal/entity verdict. F5 is delegated to ``decide_f5`` and F7 to
    ``make_entity_assessor`` (both built leaves) -- in neither case does this module
    itself name a confident verdict enum. Its only inline temporal seam is the
    not-evaluated UNJUDGEABLE default; F3/F4 states are read from their leaves."""
    import inspect
    src = inspect.getsource(jr)
    for forbidden in ("NO_QUALIFYING_CONTRADICTION", "QUALIFYING_CONTRADICTION",
                      "SAME_ENTITY", "DIFFERENT_ENTITY_SUPPORTED"):
        assert forbidden not in src, f"module must not inline-assert {forbidden}"
    # The only inline temporal seam is the not-evaluated UNJUDGEABLE default. F5 and
    # F7 verdicts arrive via their leaves, not inline.
    assert "TemporalState.UNJUDGEABLE" in src
    assert "decide_f5" in src              # F5 is wired (delegated), not hardcoded
    assert "make_entity_assessor" in src   # F7 is wired (delegated), not hardcoded


def test_f5_wired_through_runner_emits_temporal_finding(tmp_path, monkeypatch):
    """F5 is WIRED: with offline seams + an evidence builder, decide_f5 runs inside
    the runner and a qualifying contradiction surfaces as an F5 finding + per-claim
    F5 records on the emitted record (fail-closed to UNJUDGEABLE when unwired)."""
    from .f5_supersession import (
        CandidateWork, ComparabilitySource, EvidenceTier, NoticeStatus,
        RetrievalResult,
    )

    cited_src = ComparabilitySource(abstract="Drug X reduced outcome Y in adults.")
    cand_src = ComparabilitySource(
        abstract="A larger trial found Drug X did NOT reduce outcome Y in adults.")

    def f5_retrieve(cited_meta, claim, *, after_date, as_of_date):
        return RetrievalResult(
            candidates=(CandidateWork(id="W2", pub_date="2020-01-01",
                                      authors=("Jones",), tier_hint="rct"),),
            adequacy="adequate", status="ok", query_hash="qh")

    def f5_fetch(work_id, *, as_of_date):
        return cand_src if work_id == "W2" else cited_src

    def f5_notice(work_id, *, as_of_date):
        return NoticeStatus()

    def f5_tier(meta):
        hint = meta.get("tier_hint") if isinstance(meta, dict) else None
        return EvidenceTier(hint) if hint else EvidenceTier("rct")

    def f5_attest(cited_meta, claim, rid, *, as_of_date):
        return None

    def f5_judge(cited_source, cand_source, claim):
        return json.dumps({
            "directional_contradiction": True, "claim_match": "match",
            "outcome_relation": "same", "population_relation": "equivalent",
            "cited_direction": "reduces", "candidate_direction": "no effect",
            "magnitude": "reversal",
            "cited_finding_span": "Drug X reduced outcome Y in adults",
            "candidate_contradiction_span": "Drug X did NOT reduce outcome Y in adults",
            "confidence": 0.9, "scope_mismatch_axis": "none"})

    f5_seams = dict(
        retrieve_superseding_candidates=f5_retrieve,
        fetch_comparability_source=f5_fetch,
        check_formal_notice=f5_notice,
        classify_evidence_tier=f5_tier,
        find_supersession_attestation=f5_attest,
        judge_contradiction=f5_judge,
    )

    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml", lambda path, source_pmcid=None: [make_ref("c")])
    out_dir = tmp_path / "out"
    jr.run_natural_judgment(
        str(tmp_path), str(out_dir),
        extractor=extractor_of("Drug X reduces outcome Y"),
        coverage_judge=judge_established(True),
        fetch_abstract=abstract_ok,
        preband_disposition=CLEARED, model="test-model",
        f5_seams=f5_seams,
        f5_evidence_builder=lambda item: {
            "cited_work_id": "W1",
            "cited_meta": {"authors": ["Smith"], "cited_tier": "rct"},
            "cited_date": "2010-01-01", "as_of_date": "2024-01-01"},
    )
    rows = [json.loads(l) for l in
            (out_dir / "judgment_predictions.jsonl").read_text().splitlines()]
    row = rows[0]
    assert "F5" in row["findings"]
    assert row["f5_records"][0]["temporal_state"] == "QUALIFYING_CONTRADICTION"
    assert row["f5_records"][0]["f5_path"] == "B"


def test_f5_unwired_holds_temporal_unjudgeable(tmp_path, monkeypatch):
    """Without F5 seams the temporal seam stays UNJUDGEABLE and no F5 finding or
    f5_records appear -- byte-identical to the pre-wiring behavior."""
    _manifest, rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces outcome Y"),
        coverage_judge=judge_established(True),
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert "F5" not in rows[0]["findings"]
    assert "f5_records" not in rows[0]


# --------------------------------------------------------------------------
# checkpoint resume + hash chain + manifest lifecycle
# --------------------------------------------------------------------------
def _two_docs(tmp_path, monkeypatch):
    """Two one-ref docs; returns (out_dir, common kwargs)."""
    (tmp_path / "PMC1.xml").write_text("<x/>")
    (tmp_path / "PMC2.xml").write_text("<x/>")
    monkeypatch.setattr(
        jr, "parse_pmc_xml",
        lambda path, source_pmcid=None: [make_ref(f"{source_pmcid}:B1", src=source_pmcid)])
    disp = {"PMC1:B1": "cleared", "PMC2:B1": "cleared"}
    common = dict(extractor=extractor_of("Drug X reduces Y"),
                  coverage_judge=judge_established(True),
                  fetch_abstract=abstract_ok, preband_disposition=disp, model="m")
    return tmp_path / "out", common


def _replay_chain(out_dir, genesis=""):
    """Recompute the whole-record chain exactly as the module pins it."""
    preds = (out_dir / "judgment_predictions.jsonl").read_text().splitlines()
    side = (out_dir / "judgment_run_record_hashes.jsonl").read_text().splitlines()
    assert len(preds) == len(side)
    prev = genesis
    for pline, sline in zip(preds, side):
        psha = jr._canonical_sha256(json.loads(pline))
        prev = jr._chain_link(prev, psha)
        srec = json.loads(sline)
        assert srec["prediction_sha256"] == psha
        assert srec["link"] == prev
    return prev, len(preds)


def test_checkpoint_resume_skips_done_docs(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    m1 = jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    assert m1["docs_processed"] == 1
    assert m1["status"] == "in_progress"          # input not exhausted: a pause
    m2 = jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)  # resume
    assert m2["docs_processed"] == 1                       # only the remaining doc
    assert m2["status"] == "complete"

    rows = [json.loads(l) for l in
            (out_dir / "judgment_predictions.jsonl").read_text().splitlines()]
    assert {r["citation_id"] for r in rows} == {"PMC1:B1", "PMC2:B1"}   # no dup, no loss
    ckpt = [json.loads(l) for l in
            (out_dir / "judgment_run_checkpoint.jsonl").read_text().splitlines()]
    assert {c["pmcid"] for c in ckpt} == {"PMC1", "PMC2"}


def test_resume_continues_one_consistent_chain(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    m2 = jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)
    tip, count = _replay_chain(out_dir)
    assert count == 2
    assert m2["chain_tip"] == tip
    assert m2["chain_record_count"] == 2


def test_manifest_in_progress_during_run_complete_after(tmp_path, monkeypatch):
    seen = {}

    def observing_judge(claims, evidence):
        m = json.loads((tmp_path / "out" / "judgment_run_manifest.json").read_text())
        seen["status"] = m["status"]
        return judge_established(True)(claims, evidence)

    manifest, _rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=observing_judge,
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert seen["status"] == "in_progress"       # atomically present mid-run
    assert manifest["status"] == "complete"      # flipped at clean (exhausted) end


def test_resume_with_tampered_prediction_aborts_no_fork(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    pred = out_dir / "judgment_predictions.jsonl"
    lines = pred.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["citing_sentence"] = "TAMPERED SENTENCE"   # a field OUTSIDE strength_records
    lines[0] = json.dumps(rec, ensure_ascii=False)
    pred.write_text("\n".join(lines) + "\n")
    before = pred.read_text()
    with pytest.raises(ValueError):
        jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)
    assert pred.read_text() == before              # refused to append: no fork


def test_chain_covers_whole_record_any_field(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    pred = out_dir / "judgment_predictions.jsonl"
    lines = pred.read_text().splitlines()
    rec = json.loads(lines[0])
    rec["disposition"] = "predicted"               # any field breaks the chain
    pred.write_text(json.dumps(rec, ensure_ascii=False) + "\n")
    with pytest.raises(ValueError):
        jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)


def test_rewritten_predictions_and_sidecar_abort_on_manifest_anchor(tmp_path, monkeypatch):
    # Predictions + sidecar rewritten self-consistently, manifest tip unchanged
    # -> the manifest is the frozen anchor -> abort.
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    pred = out_dir / "judgment_predictions.jsonl"
    side = out_dir / "judgment_run_record_hashes.jsonl"
    recs = [json.loads(l) for l in pred.read_text().splitlines()]
    recs[0]["citing_sentence"] = "FORGED"
    prev = ""
    side_rows = []
    for r in recs:
        psha = jr._canonical_sha256(r)
        prev = jr._chain_link(prev, psha)
        side_rows.append({"citation_id": r["citation_id"],
                          "prediction_sha256": psha, "link": prev})
    pred.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs))
    side.write_text("".join(json.dumps(r) + "\n" for r in side_rows))
    with pytest.raises(ValueError):
        jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)


def test_torn_prediction_tail_preserved_never_silently_truncated(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    pred = out_dir / "judgment_predictions.jsonl"
    torn_line = json.dumps({"citation_id": "PMCX:B9", "disposition": "predicted"})
    pred.write_text(pred.read_text() + torn_line + "\n")
    with pytest.raises(ValueError):
        jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)
    tail = (out_dir / "judgment_run_torn_tail.jsonl").read_text()
    assert torn_line in tail                       # preserved as evidence
    assert torn_line in pred.read_text()           # never silently truncated


def test_manifest_lag_advances_only_over_validated_records(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    jr.run_natural_judgment(str(tmp_path), str(out_dir), max_docs=1, **common)
    mpath = out_dir / "judgment_run_manifest.json"
    m = json.loads(mpath.read_text())
    assert m["chain_record_count"] == 1
    # Simulate the manifest lagging the sidecar (torn manifest update).
    m["chain_record_count"] = 0
    m["chain_tip"] = ""
    mpath.write_text(json.dumps(m))
    m2 = jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)
    assert m2["status"] == "complete"
    tip, count = _replay_chain(out_dir)
    assert count == 2 and m2["chain_tip"] == tip   # advanced only over validated


def test_complete_manifest_is_immutable_new_segment_chains_from_tip(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    m1 = jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)
    assert m1["status"] == "complete"
    with pytest.raises(ValueError):                # complete is never reopened
        jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)
    # Extension = a NEW segment (fresh out_dir) chained from the frozen tip.
    seg2 = tmp_path / "out_segment2"
    m2 = jr.run_natural_judgment(str(tmp_path), str(seg2),
                                 chain_genesis=m1["chain_tip"], **common)
    assert m2["chain_genesis"] == m1["chain_tip"]
    first = json.loads((seg2 / "judgment_run_record_hashes.jsonl")
                       .read_text().splitlines()[0])
    assert first["link"] == jr._chain_link(m1["chain_tip"],
                                           first["prediction_sha256"])
    _replay_chain(seg2, genesis=m1["chain_tip"])


def test_predictions_without_manifest_refuse_to_append(tmp_path, monkeypatch):
    out_dir, common = _two_docs(tmp_path, monkeypatch)
    out_dir.mkdir()
    (out_dir / "judgment_predictions.jsonl").write_text('{"citation_id": "x"}\n')
    with pytest.raises(ValueError):                # unaccounted prior state
        jr.run_natural_judgment(str(tmp_path), str(out_dir), **common)


# --------------------------------------------------------------------------
# wired F3 + F4 discriminator path
# --------------------------------------------------------------------------
_F4_DIMS = ["causal_force", "epistemic_certainty", "recommendation_force", "qualitative_scope"]

F4_VERIFIER_TRUE = json.dumps({
    "cited_span_expresses_weaker_on_dimension": True, "same_relation": True,
    "papers_own_finding": True, "citing_span_asserts_stronger": True,
    "rationale": "v"})


def f4_json(load="none", pop="equivalent", f6=False, citing_span="",
            cited_span="", dims=None):
    base = {d: {"citing": "none", "cited": "none"} for d in _F4_DIMS}
    if dims:
        base.update(dims)
    return json.dumps({"subject_addressed": "yes", "dimensions": base,
                       "population_relation": pop, "load_bearing_dimension": load,
                       "f6_owned_escalation": f6,
                       "citing_strength_span": citing_span,
                       "cited_strength_span": cited_span,
                       "rationale": "x"})


def f4_fires():
    """Generator response proposing a verifiable causal overstatement against
    the extracted claim 'Drug X reduces outcome Y' and the test ABSTRACT."""
    return f4_json(load="causal_force", citing_span="reduces outcome Y",
                   cited_span="controlled study",
                   dims={"causal_force": {"citing": "causation", "cited": "association"}})


def disc_llm(f4=None, fv=F4_VERIFIER_TRUE, v2=None, v3=None, v4=None):
    def call(p):
        if "verify ONE proposed overstatement" in p:   # F4 verifier (dev-mode reuse)
            return fv
        if "STRENGTH of ONE atomic claim" in p:
            return f4
        if "PROVENANCE of ONE atomic claim" in p:
            return v2
        if "reference list" in p:
            return v3
        if "candidate PRIMARY" in p:
            return v4
        return "{}"
    return call


def run_wired(tmp_path, monkeypatch, *, coverage, call, extractor=None,
              f3_fetch_reflist=None, f3_resolve_pmcid=None,
              f4_verifier_call_llm=None, f4_verifier_model_id="", f4_policy=None):
    (tmp_path / "PMC1.xml").write_text("<x/>")
    refs = [make_ref("c")]
    monkeypatch.setattr(jr, "parse_pmc_xml", lambda path, source_pmcid=None: refs)
    out_dir = tmp_path / "out"
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(out_dir),
        extractor=extractor or extractor_of("Drug X reduces outcome Y"),
        coverage_judge=coverage, fetch_abstract=abstract_ok,
        preband_disposition={"c": "cleared"}, discriminator_call_llm=call,
        f4_verifier_call_llm=f4_verifier_call_llm,
        f4_verifier_model_id=f4_verifier_model_id, f4_policy=f4_policy,
        f3_fetch_reflist=f3_fetch_reflist, f3_resolve_pmcid=f3_resolve_pmcid, model="m")
    rows = [json.loads(l) for l in
            (out_dir / "judgment_predictions.jsonl").read_text().splitlines()]
    return rows, manifest


def test_wired_f4_fires_weaker_strength(tmp_path, monkeypatch):
    # coverage SUPPORTED; F4 finds a causal overstatement with verbatim spans;
    # the (development-mode) verifier confirms.
    rows, _m = run_wired(tmp_path, monkeypatch,
                         coverage=judge_established(True), call=disc_llm(f4=f4_fires()))
    assert rows[0]["disposition"] == jr.DISP_PREDICTED
    assert rows[0]["label"] == "F4"
    assert "F4" in rows[0]["findings"]


def test_wired_proper_origin_holds_pending_f5_f7(tmp_path, monkeypatch):
    # SUPPORTED + NOT_F4 (consistent none) + F3 originates -> proper origin.
    rows, _m = run_wired(tmp_path, monkeypatch, coverage=judge_established(True),
                         call=disc_llm(f4=f4_json(), v2=json.dumps(
                             {"verdict": "originates", "evidence_span": "", "rationale": "x"})))
    assert rows[0]["disposition"] == jr.DISP_HELD_PENDING_F5_F7
    assert rows[0]["label"] is None
    assert rows[0]["provenance"]["state"] == "PROPER_ORIGIN"


def test_wired_f3_confirmed_misattribution(tmp_path, monkeypatch):
    rows, _m = run_wired(
        tmp_path, monkeypatch, coverage=judge_established(True),
        call=disc_llm(
            f4=f4_json(),
            v2=json.dumps({"verdict": "restatement",
                           "evidence_span": "as reported previously", "rationale": "x"}),
            v3=json.dumps({"selected_index": 0, "rationale": "x"}),
            v4=json.dumps({"contains_finding": True,
                           "evidence_span": "reduced outcome Y", "rationale": "x"})),
        f3_fetch_reflist=lambda pmcid: ([{"title": "Primary", "claimed_pmid": "222", "year": 2010}], True),
        f3_resolve_pmcid=lambda pmid: "PMC999")
    assert rows[0]["disposition"] == jr.DISP_PREDICTED
    assert rows[0]["label"] == "F3"
    assert rows[0]["provenance"]["state"] == "MISATTRIBUTED_CONFIRMED"
    assert len(rows[0]["provenance"]["origin_chain"]) == 2


def test_wired_f4_unjudgeable_holds_strength(tmp_path, monkeypatch):
    rows, _m = run_wired(tmp_path, monkeypatch, coverage=judge_established(True),
                         call=disc_llm(f4=f4_json(load="unknown")))
    assert rows[0]["disposition"] == jr.DISP_HELD_STRENGTH_UNJUDGEABLE


def test_wired_f4_verifier_disagreement_holds_strength(tmp_path, monkeypatch):
    disagree = json.dumps({
        "cited_span_expresses_weaker_on_dimension": True, "same_relation": False,
        "papers_own_finding": True, "citing_span_asserts_stronger": True,
        "rationale": "different relation"})
    rows, _m = run_wired(tmp_path, monkeypatch, coverage=judge_established(True),
                         call=disc_llm(f4=f4_fires(), fv=disagree))
    assert rows[0]["disposition"] == jr.DISP_HELD_STRENGTH_UNJUDGEABLE
    assert rows[0]["strength_records"][0]["reason"] == "verifier_disagreement"


def test_wired_f6_still_dominant(tmp_path, monkeypatch):
    # a coverage gap is F6 regardless of the wired discriminators (F4 not run on it).
    rows, _m = run_wired(tmp_path, monkeypatch,
                         extractor=extractor_of("c1", "c2"),
                         coverage=judge_established(False, True), call=disc_llm(f4=f4_json()))
    assert rows[0]["disposition"] == jr.DISP_PREDICTED
    assert rows[0]["label"] == "F6"


# --------------------------------------------------------------------------
# F4 modes through the orchestrator: development default, formal reportable,
# config defects abort BEFORE any output (never per-pair quarantine).
# --------------------------------------------------------------------------
def test_wired_default_is_development_mode_not_reportable(tmp_path, monkeypatch):
    rows, m = run_wired(tmp_path, monkeypatch, coverage=judge_established(True),
                        call=disc_llm(f4=f4_fires()))
    assert m["f4"]["mode"] == "development"
    assert m["f4"]["reportable"] is False
    sr = rows[0]["strength_records"][0]
    assert sr["mode"] == "development"
    assert sr["reportable"] is False


def test_wired_formal_mode_is_reportable_with_model_ids(tmp_path, monkeypatch):
    verifier_calls = {"n": 0}

    def distinct_verifier(_prompt):
        verifier_calls["n"] += 1
        return F4_VERIFIER_TRUE

    rows, m = run_wired(tmp_path, monkeypatch, coverage=judge_established(True),
                        call=disc_llm(f4=f4_fires()),
                        f4_verifier_call_llm=distinct_verifier,
                        f4_verifier_model_id="ver-model")
    assert rows[0]["label"] == "F4"
    assert verifier_calls["n"] == 1                # the distinct verifier ran
    assert m["f4"]["mode"] == "formal"
    assert m["f4"]["reportable"] is True
    assert m["f4"]["generator_model_id"] == "m"
    assert m["f4"]["verifier_model_id"] == "ver-model"
    sr = rows[0]["strength_records"][0]
    assert sr["mode"] == "formal" and sr["reportable"] is True
    assert sr["generator_model_id"] == "m"
    assert sr["verifier_model_id"] == "ver-model"


def test_f4_counters_define_actually_evaluated(tmp_path, monkeypatch):
    rows, m = run_wired(tmp_path, monkeypatch, coverage=judge_established(True),
                        call=disc_llm(f4=f4_fires()))
    assert m["f4"]["eligible_claims"] == 1
    assert m["f4"]["generator_calls"] == 1
    assert m["f4"]["verifier_calls"] == 1
    # NOT_F4 candidate: generator runs, verifier never called.
    second = tmp_path / "b"
    second.mkdir()
    rows2, m2 = run_wired(second, monkeypatch, coverage=judge_established(True),
                          call=disc_llm(f4=f4_json(), v2=json.dumps(
                              {"verdict": "originates", "evidence_span": "", "rationale": "x"})))
    assert m2["f4"]["generator_calls"] == 1
    assert m2["f4"]["verifier_calls"] == 0


def test_formal_mode_with_zero_generator_calls_not_reportable(tmp_path, monkeypatch):
    # Fully-configured formal mode, but no coverage-SUPPORTED claim ever reaches
    # the generator: "configured" is not "actually evaluated" -> not reportable.
    rows, m = run_wired(tmp_path, monkeypatch,
                        coverage=judge_established(False),   # UNESTABLISHED claim
                        call=disc_llm(f4=f4_fires()),
                        f4_verifier_call_llm=lambda _p: F4_VERIFIER_TRUE,
                        f4_verifier_model_id="ver-model")
    assert m["f4"]["mode"] == "formal"
    assert m["f4"]["eligible_claims"] == 0
    assert m["f4"]["generator_calls"] == 0
    assert m["f4"]["reportable"] is False


def test_unwired_generator_never_reportable(tmp_path, monkeypatch):
    manifest, _rows = run(
        tmp_path, [make_ref("c")],
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True),
        disposition=CLEARED, monkeypatch=monkeypatch)
    assert manifest["discriminators_wired"] is False
    assert manifest["f4"]["reportable"] is False
    assert manifest["f4"]["generator_calls"] == 0


def test_formal_without_verifier_aborts_before_any_output(tmp_path, monkeypatch):
    (tmp_path / "PMC1.xml").write_text("<x/>")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("c")])
    out_dir = tmp_path / "out"
    with pytest.raises(ValueError):
        jr.run_natural_judgment(
            str(tmp_path), str(out_dir),
            extractor=extractor_of("Drug X reduces outcome Y"),
            coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
            preband_disposition={"c": "cleared"},
            discriminator_call_llm=disc_llm(f4=f4_json()),
            f4_policy=F4Policy(mode="formal", generator_model_id="g",
                               verifier_model_id="v"),
            model="m")
    # Aborted the WHOLE run before any output file (not per-pair quarantine).
    assert not out_dir.exists()


def test_formal_with_identical_verifier_callable_aborts(tmp_path, monkeypatch):
    (tmp_path / "PMC1.xml").write_text("<x/>")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("c")])
    out_dir = tmp_path / "out"
    call = disc_llm(f4=f4_json())
    with pytest.raises(ValueError):
        jr.run_natural_judgment(
            str(tmp_path), str(out_dir),
            extractor=extractor_of("Drug X reduces outcome Y"),
            coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
            preband_disposition={"c": "cleared"},
            discriminator_call_llm=call,
            f4_verifier_call_llm=call,             # same callable -> formal defect
            f4_verifier_model_id="ver-model",
            model="m")
    assert not out_dir.exists()


def test_module_imports_no_network_client():
    """judgment_run must not import a network/model client at module scope."""
    import inspect
    src = inspect.getsource(jr)
    assert "import requests" not in src
    assert "import anthropic" not in src
    assert "from anthropic" not in src
