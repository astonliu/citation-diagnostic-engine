"""Round 1 remediation: everything that governs a published number is RECORDED.

The rule under test throughout: a setting that governs a number and is recorded
nowhere is a defect. Each test names the setting and pins where it lands.
"""
from __future__ import annotations

import hashlib
import json
import os

import pytest

from . import judgment_run as jr
from . import test_f7_entity as f7t
from .f3_provenance import F3Policy
from .parser_versions import CLAIM_PARSER_VERSION, COVERAGE_PARSER_VERSION
from .test_f7_orchestrator_wiring import ORIGINATES, builder, seams
from .test_judgment_run import (
    abstract_ok,
    disc_llm,
    extractor_of,
    f4_json,
    judge_established,
    make_ref,
    run,
)

CLEARED = {"c": "cleared"}


# --------------------------------------------------------------- adapter

def test_temperature_zero_reaches_the_manifest(tmp_path, monkeypatch):
    """DEC-046 pins temperature=0. 0 is FALSY, so a truthiness guard drops
    exactly the pinned value it exists to record. run_band was fixed on
    2026-08-12; this entry point -- the one that produces every published
    F3-F7 number -- had no temperature parameter at all."""
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch,
                      temperature=0)
    assert manifest["adapter"]["temperature"] == 0


def test_unsupplied_temperature_is_absent_never_guessed(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    assert "temperature" not in manifest["adapter"]


def test_prefill_and_stop_sequences_reach_the_manifest(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch,
                      assistant_prefill="{", stop_sequences=("</done>",))
    assert manifest["adapter"]["assistant_prefill"] == "{"
    assert manifest["adapter"]["stop_sequences"] == ["</done>"]
    assert manifest["adapter"]["model"] == "test-model"


# ------------------------------------------------------ parser contracts

def test_both_parser_contracts_are_stamped_on_the_default_path(tmp_path, monkeypatch):
    """DEC-022: prompt version and parser version are independent axes. The
    abstract path stamped NEITHER parser version, so a contract move was
    invisible in every artifact."""
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    assert manifest["claim_extract_parser_version"] == CLAIM_PARSER_VERSION
    assert manifest["coverage_parser_version"] == COVERAGE_PARSER_VERSION
    assert manifest["evidence_scope_effective"] == jr.EVIDENCE_SCOPE_ABSTRACT


# --------------------------------------------------- population provenance

def test_disposition_provenance_is_bound_to_bytes_schema_and_commit(tmp_path, monkeypatch):
    """A result must be tie-able to the population it was computed over. The
    manifest previously held only 'supplied: true' and a collapsed size."""
    from . import preband_contract as pc
    commit = "d90196a7be3fe64e1eb9225a17b2ce4a26e0ecd1"
    body = json.dumps({"citation_id": "PMC1:B1", "label": "cleared"},
                      sort_keys=True) + "\n"
    art = tmp_path / "disp.jsonl"
    art.write_text(body, encoding="utf-8")
    (tmp_path / ("disp.jsonl" + pc.MANIFEST_SUFFIX)).write_text(json.dumps({
        "schema": pc.DISPOSITION_SCHEMA,
        "artifact_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "f2_commit": commit, "row_count": 1}), encoding="utf-8")

    manifest, _ = run(tmp_path, [make_ref("PMC1:B1")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=str(art), monkeypatch=monkeypatch)
    pb = manifest["preband"]
    assert pb["canonical"] is True
    assert pb["schema"] == pc.DISPOSITION_SCHEMA
    assert pb["f2_commit"] == commit
    assert pb["artifact_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert pb["join"]["matched"] == 1
    assert pb["join"]["coverage"] == 1.0


def test_an_injected_dict_is_stamped_non_canonical(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    assert manifest["preband"]["canonical"] is False
    assert manifest["preband"]["source"] == "dict_injection"


def test_corpus_manifest_and_code_commit_are_bound(tmp_path, monkeypatch):
    corpus = tmp_path / "frozen_manifest.json"
    corpus.write_text(json.dumps({"documents": ["PMC1"]}), encoding="utf-8")
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch,
                      corpus_manifest_path=str(corpus),
                      code_commit="8e1737163b9a43cc0f445d238fe04406a659c6f6")
    assert manifest["code_commit"] == "8e1737163b9a43cc0f445d238fe04406a659c6f6"
    assert manifest["corpus"]["manifest_sha256"] == hashlib.sha256(
        corpus.read_bytes()).hexdigest()
    assert manifest["corpus"]["documents_in_scope"] == 1


# ------------------------------------------------------- module identity

def test_module_hashes_are_captured_before_execution(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    assert manifest["module_sha256_capture"] == "before_execution"
    assert manifest["module_sha256_stable"] is True
    assert "module_sha256_after" not in manifest
    # The join validator governs which pairs are judged at all, so it is
    # load-bearing and must be hashed.
    assert "preband_contract.py" in manifest["module_sha256"]


# ------------------------------------------------------------- F3 block

def test_f3_effective_policy_is_recorded(tmp_path, monkeypatch):
    """F3's hop limit and trace sources govern whether a claim can reach an F3
    finding at all, and were recorded nowhere."""
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    f3 = manifest["f3"]
    assert f3["max_hop_count"] == 1
    assert f3["trace_sources"] == ["cited_reflist"]
    assert f3["unresolved_state"] == "UNJUDGEABLE"
    assert len(f3["policy_sha256"]) == 64


def test_a_non_default_f3_policy_changes_the_recorded_digest(tmp_path, monkeypatch):
    """The digest must track the EFFECTIVE policy, not the default."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    base, _ = run(a, [make_ref("c")],
                  extractor=extractor_of("Drug X reduces Y"),
                  coverage_judge=judge_established(True),
                  disposition=CLEARED, monkeypatch=monkeypatch)
    other, _ = run(b, [make_ref("c")],
                   extractor=extractor_of("Drug X reduces Y"),
                   coverage_judge=judge_established(True),
                   disposition=CLEARED, monkeypatch=monkeypatch,
                   f3_policy=F3Policy(max_hop_count=2))
    assert other["f3"]["max_hop_count"] == 2
    assert other["f3"]["policy_sha256"] != base["f3"]["policy_sha256"]


# ------------------------------------------------------------- F7 block

def f7_run(tmp_path, monkeypatch, *, relation_comparator=None):
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("c")])
    return jr.run_natural_judgment(
        str(tmp_path), str(tmp_path / "out"),
        extractor=extractor_of(f7t.CLAIM),
        coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
        preband_disposition={"c": "cleared"}, model="test-model",
        discriminator_call_llm=disc_llm(f4=f4_json(), v2=ORIGINATES),
        f7_seams=seams(relation_comparator=relation_comparator),
        f7_evidence_builder=builder(), f7_policy=f7t.policy())


def test_f7_provenance_block_exists_when_f7_is_wired(tmp_path, monkeypatch):
    """F7 rides HIGHEST in the engine ordering, so it can own the published
    label -- and it previously emitted no module hash, no prompt hash, no policy
    block and no model ids."""
    manifest = f7_run(tmp_path, monkeypatch)
    f7 = manifest["f7"]
    assert f7["wired"] is True
    assert f7["tuples_prompt_version"] == "f7_tuples_v1"
    assert f7["relation_prompt_version"] == "f7_relation_v1"
    for k in ("attribution", "tuples", "evidence", "verifier"):
        assert len(f7["prompt_sha256"][k]) == 64
    assert len(f7["authorities_sha256"]) == 64
    # The governing module is now hashed.
    assert "f7_entity.py" in manifest["module_sha256"]


def test_f7_is_absent_when_unwired(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    assert "f7" not in manifest
    assert "f7_entity.py" not in manifest["module_sha256"]


# ------------------------------ the relation prompt: a version is not a digest

DIGEST = "a" * 64


def test_relation_prompt_digest_is_recorded_when_the_comparator_reports_one(
        tmp_path, monkeypatch):
    manifest = f7_run(
        tmp_path, monkeypatch,
        relation_comparator=lambda claimed, evidence, *, call_llm:
            f7t.relation_all_match(prompt_sha256=DIGEST))
    f7 = manifest["f7"]
    assert f7["relation_prompt_sha256"] == [DIGEST]
    assert f7["relation_prompt_digest_present"] is True


def test_relation_digest_stays_none_when_the_comparator_reports_none(
        tmp_path, monkeypatch):
    """It is NEVER filled with the version string. Storing 'f7_relation_v1'
    under a `_sha256` name is a false provenance record: four schemas got a real
    64-hex digest and the fifth got a version, in one dict."""
    manifest = f7_run(tmp_path, monkeypatch)
    f7 = manifest["f7"]
    assert f7["relation_prompt_sha256"] is None
    assert f7["relation_prompt_digest_present"] is False
    assert f7["reportable"] is False


def test_a_version_string_in_the_digest_slot_is_refused():
    """COUNTEREXAMPLE: the exact old value. A comparator that reports
    'f7_relation_v1' as its prompt_sha256 is now rejected outright."""
    from .f7_entity import _validate_relation_comparison
    with pytest.raises(ValueError, match="not a prompt digest"):
        _validate_relation_comparison(
            f7t.relation_all_match(prompt_sha256="f7_relation_v1"))


def test_a_malformed_digest_is_refused():
    from .f7_entity import _validate_relation_comparison
    for bad in ("A" * 64, "abc", "z" * 64, 12345):
        with pytest.raises(ValueError, match="64-hex sha256"):
            _validate_relation_comparison(
                f7t.relation_all_match(prompt_sha256=bad))


def test_the_trace_separates_version_from_digest(tmp_path, monkeypatch):
    """Per-record: prompt_version carries the version, prompt_sha256 the digest."""
    f7_run(tmp_path, monkeypatch,
           relation_comparator=lambda claimed, evidence, *, call_llm:
               f7t.relation_all_match(prompt_sha256=DIGEST))
    rows = [json.loads(l) for l in
            (tmp_path / "out" / "judgment_predictions.jsonl")
            .read_text().splitlines()]
    traces = [tr for row in rows for r in (row.get("f7_records") or [])
              for tr in (r.get("tuple_records") or [])
              if tr.get("relation_component_result") is not None]
    assert traces, "expected at least one relation comparison trace"
    tr = traces[0]
    assert tr["prompt_version"]["relation"] == "f7_relation_v1"
    assert tr["prompt_sha256"]["relation"] == DIGEST


# ------------------------------------ corpus fails closed, before any output

def _run_raw(tmp_path, monkeypatch, *, parse=None, disposition=CLEARED):
    from . import preband_contract as pc
    if parse is not None:
        monkeypatch.setattr(jr, "parse_pmc_xml", parse)
    out_dir = tmp_path / "out"
    with pytest.raises(pc.PrebandContractError) as exc:
        jr.run_natural_judgment(
            str(tmp_path), str(out_dir),
            extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
            preband_disposition=disposition, model="test-model")
    # The abort happens BEFORE any output file exists, so it can never be
    # mistaken for a completed run.
    assert not out_dir.exists()
    return exc.value


def test_an_empty_corpus_aborts_before_any_output(tmp_path, monkeypatch):
    """Old behaviour: zero records, status 'complete', accounting_ok true."""
    err = _run_raw(tmp_path, monkeypatch)
    assert "no .xml/.nxml documents" in str(err)


def test_a_wholly_unparseable_corpus_aborts_before_any_output(tmp_path, monkeypatch):
    (tmp_path / "PMC1.xml").write_text("not xml", encoding="utf-8")
    (tmp_path / "PMC2.xml").write_text("not xml", encoding="utf-8")

    def boom(path, source_pmcid=None):
        raise ValueError("junk before document element")

    err = _run_raw(tmp_path, monkeypatch, parse=boom)
    assert "every document" in str(err)


def test_a_corpus_yielding_zero_references_aborts(tmp_path, monkeypatch):
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    err = _run_raw(tmp_path, monkeypatch,
                   parse=lambda path, source_pmcid=None: [])
    assert "ZERO references" in str(err)


# ------------------------------------------- the reportability gate, end to end

def test_codex_b2_bypass_is_blocked_end_to_end(tmp_path, monkeypatch):
    """A has no citance (structurally excluded before the pre-band gate);
    B is eligible but absent from the disposition, which contains only A.

    Old behaviour: join matched 1/2, status=complete, accounting_ok=true,
    queue=0, ZERO pairs judged -- a publishable-looking clean empty run."""
    from . import preband_contract as pc
    refs = [make_ref("PMC1:A", citance=""), make_ref("PMC1:B")]
    with pytest.raises(pc.PrebandContractError, match="clean empty run"):
        run(tmp_path, refs, extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True),
            disposition={"PMC1:A": "cleared"}, monkeypatch=monkeypatch)


def test_a_dict_run_records_itself_as_not_reportable(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch)
    rep = manifest["reportability"]
    assert rep["reportable"] is False
    assert rep["checks"]["canonical_disposition"] is False
    assert rep["checks"]["pairs_judged"] is True          # the run itself is fine


def test_require_reportable_aborts_a_development_run(tmp_path, monkeypatch):
    from . import preband_contract as pc
    with pytest.raises(pc.PrebandContractError, match="NOT reportable"):
        run(tmp_path, [make_ref("c")],
            extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True),
            disposition=CLEARED, monkeypatch=monkeypatch,
            require_reportable=True)


# ========================================================================
# The MANDATORY production preflight -- refuses before any output exists
# ========================================================================

def _canonical_disp(tmp_path, ids, *, corpus_sha=""):
    """Write a real canonical artifact + sidecar."""
    from . import preband_contract as pc
    body = "".join(json.dumps({"citation_id": c, "label": "cleared"},
                              sort_keys=True) + "\n" for c in ids)
    art = tmp_path / "disp.jsonl"
    art.write_text(body, encoding="utf-8")
    side = {"schema": pc.DISPOSITION_SCHEMA,
            "artifact_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "f2_commit": "d90196a7be3fe64e1eb9225a17b2ce4a26e0ecd1",
            "row_count": len(ids)}
    if corpus_sha:
        side["corpus_manifest_sha256"] = corpus_sha
    (tmp_path / ("disp.jsonl" + pc.MANIFEST_SUFFIX)).write_text(
        json.dumps(side), encoding="utf-8")
    return str(art)


def _corpus(tmp_path, payload=None):
    """A frozen manifest that declares its CONTENTS: every XML and its sha256."""
    if payload is None:
        inv = {}
        for fn in sorted(os.listdir(tmp_path)):
            if fn.endswith((".xml", ".nxml")):
                inv[fn] = hashlib.sha256(
                    (tmp_path / fn).read_bytes()).hexdigest()
        payload = {"schema": "frozen_corpus_v1", "documents": inv}
    p = tmp_path / "frozen_manifest.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p), hashlib.sha256(p.read_bytes()).hexdigest()


def _seed_xml(tmp_path, names=("PMC1.xml",)):
    for n in names:
        (tmp_path / n).write_text("<x/>", encoding="utf-8")


def _prod(tmp_path, monkeypatch, refs, disposition, **over):
    kw = dict(extractor=extractor_of("Drug X reduces Y"),
              coverage_judge=judge_established(True),
              disposition=disposition, monkeypatch=monkeypatch,
              production=True, temperature=0,
              code_commit="8e1737163b9a43cc0f445d238fe04406a659c6f6")
    kw.update(over)
    return run(tmp_path, refs, **kw)


def test_a_fully_bound_production_run_succeeds(tmp_path, monkeypatch):
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    manifest, _ = _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
                        corpus_manifest_path=cpath)
    assert manifest["reportability"]["reportable"] is True, \
        manifest["reportability"]["failures"]


def test_production_refuses_a_dict_disposition(tmp_path, monkeypatch):
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, _ = _corpus(tmp_path)
    out = tmp_path / "out"
    with pytest.raises(pc.PrebandContractError, match="canonical preband_disposition_v1"):
        _prod(tmp_path, monkeypatch, [make_ref("c")], {"c": "cleared"},
              corpus_manifest_path=cpath)
    assert not out.exists()          # refused BEFORE any output


def test_production_refuses_incomplete_id_coverage(tmp_path, monkeypatch):
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    with pytest.raises(pc.PrebandContractError, match="absent from the disposition"):
        _prod(tmp_path, monkeypatch,
              [make_ref("PMC1:B1"), make_ref("PMC1:B2")], art,
              corpus_manifest_path=cpath)


def test_production_refuses_a_corpus_mismatch(tmp_path, monkeypatch):
    """The disposition was built over corpus A; this run judges corpus B."""
    from . import preband_contract as pc
    cpath, _ = _corpus(tmp_path, {"documents": ["PMC999"]})
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha="f" * 64)
    with pytest.raises(pc.PrebandContractError, match="corpus mismatch"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)


def test_production_refuses_a_missing_corpus_binding(tmp_path, monkeypatch):
    from . import preband_contract as pc
    art = _canonical_disp(tmp_path, ["PMC1:B1"])
    with pytest.raises(pc.PrebandContractError, match="no corpus_manifest_path"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art)


def test_production_refuses_any_parse_failure(tmp_path, monkeypatch):
    from . import preband_contract as pc
    _seed_xml(tmp_path, ("PMC1.xml", "PMC2.xml"))
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)

    def parse(path, source_pmcid=None):
        if source_pmcid == "PMC2":
            raise ValueError("junk before document element")
        return [make_ref("PMC1:B1")]

    monkeypatch.setattr(jr, "parse_pmc_xml", parse)
    with pytest.raises(pc.PrebandContractError, match="ZERO parse"):
        jr.run_natural_judgment(
            str(tmp_path), str(tmp_path / "out"),
            extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
            preband_disposition=art, model="test-model", production=True,
            temperature=0, corpus_manifest_path=cpath,
            code_commit="8e1737163b9a43cc0f445d238fe04406a659c6f6")


def test_production_refuses_an_empty_code_commit(tmp_path, monkeypatch):
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    with pytest.raises(pc.PrebandContractError, match="no code_commit"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath, code_commit="")


def test_production_refuses_an_empty_model(tmp_path, monkeypatch):
    """The run() helper always supplies a model, so this drives the entry point
    directly -- an unnamed model is exactly the governance gap being closed."""
    from . import preband_contract as pc
    (tmp_path / "PMC1.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: [make_ref("PMC1:B1")])
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    with pytest.raises(pc.PrebandContractError, match="no model"):
        jr.run_natural_judgment(
            str(tmp_path), str(tmp_path / "out"),
            extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
            preband_disposition=art, model="", production=True, temperature=0,
            corpus_manifest_path=cpath,
            code_commit="8e1737163b9a43cc0f445d238fe04406a659c6f6")


def test_production_refuses_max_docs(tmp_path, monkeypatch):
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    with pytest.raises(pc.PrebandContractError, match="max_docs"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath, max_docs=1)


def test_production_refuses_resume_into_an_existing_out_dir(tmp_path, monkeypatch):
    """Resume can duplicate rows, miscount the population, and combine different
    models/settings/commits under one manifest. Recovery is a FRESH out_dir."""
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
          corpus_manifest_path=cpath)                       # first run: fine
    with pytest.raises(pc.PrebandContractError, match="COMPLETELY EMPTY out_dir"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)                   # second: refused


def test_production_refuses_a_chained_segment(tmp_path, monkeypatch):
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    with pytest.raises(pc.PrebandContractError, match="chain_genesis"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath, chain_genesis="a" * 64)


def test_production_refuses_a_nonempty_out_dir_without_run_state(tmp_path, monkeypatch):
    """CODEX 3: "no checkpoint/predictions" was too weak. A leftover manifest,
    sidecar, torn tail, queue or F5 discovery file from a prior attempt is state
    this run would append to or silently sit beside."""
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    out = tmp_path / "out"
    out.mkdir()
    (out / "judgment_run_torn_tail.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(pc.PrebandContractError, match="COMPLETELY EMPTY out_dir"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)


# --------------------------------- frozen corpus CONTENTS, not just its bytes

def test_production_refuses_a_manifest_with_no_inventory(tmp_path, monkeypatch):
    """CODEX 1: hashing the manifest file proves only that the manifest did not
    change. It says nothing about the XML on disk."""
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path, {"schema": "frozen_corpus_v1", "n": 1})
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    with pytest.raises(pc.PrebandContractError, match="declares no document inventory"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)


def test_production_refuses_an_edited_xml(tmp_path, monkeypatch):
    """The manifest is untouched and its own digest still matches; the DOCUMENT
    changed. Only a contents check catches this."""
    from . import preband_contract as pc
    # NOT PMC1.xml: the run() helper rewrites that file on every call.
    _seed_xml(tmp_path, ("PMC1.xml", "PMC5.xml"))
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    (tmp_path / "PMC5.xml").write_text("<x>edited</x>", encoding="utf-8")
    with pytest.raises(pc.PrebandContractError, match="content differs from the frozen digest"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)


def test_production_refuses_an_undeclared_extra_xml(tmp_path, monkeypatch):
    """An extra XML silently ENLARGES the population."""
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    (tmp_path / "PMC7.xml").write_text("<x/>", encoding="utf-8")
    with pytest.raises(pc.PrebandContractError, match="not declared"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)


def test_production_refuses_a_declared_but_absent_xml(tmp_path, monkeypatch):
    """A missing XML silently SHRINKS the population."""
    from . import preband_contract as pc
    _seed_xml(tmp_path, ("PMC1.xml", "PMC5.xml"))
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    os.remove(tmp_path / "PMC5.xml")
    with pytest.raises(pc.PrebandContractError, match="declared but absent"):
        _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
              corpus_manifest_path=cpath)


def test_a_production_run_records_every_document_digest(tmp_path, monkeypatch):
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    manifest, _ = _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
                        corpus_manifest_path=cpath)
    digests = manifest["corpus_document_sha256"]
    assert set(digests) == {"PMC1.xml"}
    assert digests["PMC1.xml"] == hashlib.sha256(
        (tmp_path / "PMC1.xml").read_bytes()).hexdigest()


# ------------------------------------- the double-parse loss (CODEX 2)

def test_an_execution_parse_failure_is_fatal_in_production(tmp_path, monkeypatch):
    """The preflight parsed it; the execution pass did not. Skipping would
    silently shrink the validated population."""
    from . import preband_contract as pc
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    calls = {"n": 0}

    def flaky(path, source_pmcid=None):
        calls["n"] += 1
        if calls["n"] > 1:                      # preflight ok, execution fails
            raise ValueError("junk before document element")
        return [make_ref("PMC1:B1")]

    monkeypatch.setattr(jr, "parse_pmc_xml", flaky)
    with pytest.raises(pc.PrebandContractError, match="FAILED during execution"):
        jr.run_natural_judgment(
            str(tmp_path), str(tmp_path / "out"),
            extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
            preband_disposition=art, model="test-model", production=True,
            temperature=0, corpus_manifest_path=cpath,
            code_commit="8e1737163b9a43cc0f445d238fe04406a659c6f6")


def test_a_diverging_executed_domain_blocks_reportability(tmp_path, monkeypatch):
    """Both passes succeed but yield DIFFERENT ids -- the validated population is
    not the judged one."""
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1", "PMC1:B2"], corpus_sha=csha)
    calls = {"n": 0}

    def drifting(path, source_pmcid=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [make_ref("PMC1:B1"), make_ref("PMC1:B2")]
        return [make_ref("PMC1:B1")]            # execution loses one

    monkeypatch.setattr(jr, "parse_pmc_xml", drifting)
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(tmp_path / "out"),
        extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True), fetch_abstract=abstract_ok,
        preband_disposition=art, model="test-model", temperature=0,
        corpus_manifest_path=cpath,
        code_commit="8e1737163b9a43cc0f445d238fe04406a659c6f6")
    dom = manifest["executed_domain"]
    assert dom["matches_preflight"] is False
    assert dom["only_in_preflight"] == ["PMC1:B2"]
    assert manifest["reportability"]["checks"][
        "executed_domain_matches_preflight"] is False


# --------------------- global reportability across category blocks (CODEX 4)

def test_an_emitted_f5_label_makes_the_whole_run_unreportable(tmp_path):
    """F5 is unreportable BY CONSTRUCTION -- deploy_path_a is hard-gated off and
    it has no verifier. An F5 verdict must not ride out inside a run that calls
    itself reportable."""
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    m = good_manifest()
    m["emitted_labels"] = {"F5": 2}
    m["f5"] = {"reportable": False}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["F5_reportable"] is False
    assert r["reportable"] is False


def test_an_emitted_f4_label_needs_a_reportable_f4_block(tmp_path):
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    m = good_manifest()
    m["emitted_labels"] = {"F4": 1}
    m["f4"] = {"reportable": False, "mode": "development"}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["F4_reportable"] is False


def test_an_emitted_f7_label_without_a_block_is_rejected(tmp_path):
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    m = good_manifest()
    m["emitted_labels"] = {"F7": 3}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["F7_provenance"] is False


def test_an_emitted_f3_label_needs_a_wired_policy_digest(tmp_path):
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    m = good_manifest()
    m["emitted_labels"] = {"F3": 1}
    m["f3"] = {"wired": False, "policy_sha256": ""}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["F3_provenance"] is False
    m["f3"] = {"wired": True, "policy_sha256": "a" * 64}
    assert pc.reportability_report(
        m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))["checks"]["F3_provenance"] is True


def test_f6_only_run_is_unaffected(tmp_path):
    """F6 is the always-live discriminator and has no category block."""
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    m = good_manifest()
    m["emitted_labels"] = {"F6": 5}
    assert pc.reportability_report(
        m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))["reportable"] is True


# ------------------------------------- the queue audit (CODEX 3, second half)

def test_a_queue_short_of_the_scoreable_set_blocks_reportability(tmp_path):
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    m = good_manifest()
    m["queue_audit"] = {"matches": False, "queue_rows": 1, "scoreable_rows": 2,
                        "symmetric_difference": ["PMC1:B2"]}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["queue_matches_scoreable"] is False


def test_a_real_run_queue_matches_its_scoreable_predictions(tmp_path, monkeypatch):
    _seed_xml(tmp_path)
    cpath, csha = _corpus(tmp_path)
    art = _canonical_disp(tmp_path, ["PMC1:B1"], corpus_sha=csha)
    manifest, _ = _prod(tmp_path, monkeypatch, [make_ref("PMC1:B1")], art,
                        corpus_manifest_path=cpath)
    q = manifest["queue_audit"]
    assert q["matches"] is True
    assert q["queue_rows"] == q["scoreable_rows"] == manifest["scoreable_records"]


# ------------------- the queue audit reads FILES, not its own bookkeeping

def test_queue_audit_detects_a_short_queue_file(tmp_path):
    """REGRESSION. The first version compared two in-memory lists appended in
    the same branch, so it agreed with itself by construction and could never
    fail. Both sides are now re-read from disk."""
    pred = tmp_path / "p.jsonl"
    queue = tmp_path / "q.jsonl"
    pred.write_text("".join(json.dumps(
        {"citation_id": c, "disposition": jr.DISP_PREDICTED}) + "\n"
        for c in ("PMC1:B1", "PMC1:B2")), encoding="utf-8")
    queue.write_text(json.dumps({"item_key": "PMC1:B1"}) + "\n",
                     encoding="utf-8")   # one row short
    audit = jr._queue_audit(str(pred), str(queue))
    assert audit["matches"] is False
    assert audit["queue_rows"] == 1
    assert audit["scoreable_rows"] == 2
    assert audit["symmetric_difference"] == ["PMC1:B2"]
    assert audit["source"] == "files_on_disk"


def test_queue_audit_ignores_non_scoreable_predictions(tmp_path):
    pred = tmp_path / "p.jsonl"
    queue = tmp_path / "q.jsonl"
    pred.write_text(
        json.dumps({"citation_id": "PMC1:B1",
                    "disposition": jr.DISP_PREDICTED}) + "\n"
        + json.dumps({"citation_id": "PMC1:B2",
                      "disposition": jr.DISP_EXCLUDED_PREBAND}) + "\n",
        encoding="utf-8")
    queue.write_text(json.dumps({"item_key": "PMC1:B1"}) + "\n",
                     encoding="utf-8")
    assert jr._queue_audit(str(pred), str(queue))["matches"] is True


def test_queue_audit_detects_an_id_swap(tmp_path):
    """Same COUNT, different ids -- a count-only check would pass this."""
    pred = tmp_path / "p.jsonl"
    queue = tmp_path / "q.jsonl"
    pred.write_text(json.dumps(
        {"citation_id": "PMC1:B1", "disposition": jr.DISP_PREDICTED}) + "\n",
        encoding="utf-8")
    queue.write_text(json.dumps({"item_key": "PMC9:ZZ"}) + "\n",
                     encoding="utf-8")
    audit = jr._queue_audit(str(pred), str(queue))
    assert audit["queue_rows"] == audit["scoreable_rows"] == 1
    assert audit["matches"] is False


# --------------------- DEC-070: three distinguishable temperature states

def test_unsupported_is_recorded_as_a_string_never_as_zero(tmp_path, monkeypatch):
    manifest, _ = run(tmp_path, [make_ref("c")],
                      extractor=extractor_of("Drug X reduces Y"),
                      coverage_judge=judge_established(True),
                      disposition=CLEARED, monkeypatch=monkeypatch,
                      temperature="unsupported")
    assert manifest["adapter"]["temperature"] == "unsupported"
    assert manifest["adapter"]["temperature"] != 0


def test_the_three_temperature_states_are_distinguishable(tmp_path, monkeypatch):
    """0 (sent), "unsupported" (not sent, provider rejects), key absent (never
    recorded). A reader must be able to tell which of the three happened."""
    states = {}
    for name, kw in (("pinned", {"temperature": 0}),
                     ("unsupported", {"temperature": "unsupported"}),
                     ("unrecorded", {})):
        d = tmp_path / name
        d.mkdir()
        m, _ = run(d, [make_ref("c")],
                   extractor=extractor_of("Drug X reduces Y"),
                   coverage_judge=judge_established(True),
                   disposition=CLEARED, monkeypatch=monkeypatch, **kw)
        states[name] = m["adapter"]
    assert states["pinned"]["temperature"] == 0
    assert states["unsupported"]["temperature"] == "unsupported"
    assert "temperature" not in states["unrecorded"]


@pytest.mark.parametrize("bad", ["0", "unsupportd", "none", []])
def test_a_fourth_temperature_state_is_refused(tmp_path, monkeypatch, bad):
    """A typo would read as a fourth state nobody can interpret."""
    with pytest.raises(ValueError, match="temperature must be a number"):
        run(tmp_path, [make_ref("c")],
            extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True),
            disposition=CLEARED, monkeypatch=monkeypatch, temperature=bad)


def test_reportability_accepts_unsupported_but_not_a_stray_string(tmp_path):
    from . import preband_contract as pc
    from .test_preband_contract import good_manifest, preds
    p = preds(tmp_path, ["PMC1:B1", "PMC1:B2"])
    m = good_manifest()
    m["adapter"] = {"model": "claude-opus-5", "temperature": "unsupported"}
    assert pc.reportability_report(m, p)["checks"]["temperature_legal"] is True
    m["adapter"] = {"model": "claude-opus-5", "temperature": "0"}
    assert pc.reportability_report(m, p)["checks"]["temperature_legal"] is False
