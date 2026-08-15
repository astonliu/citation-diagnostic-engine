"""Fail-closed tests for the Band-1 -> Band-2 join contract.

Every rule gets a concrete synthetic counterexample: the exact input that used
to produce a wrong published number, and the refusal that now stops it.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from . import preband_contract as pc
from .schema import Reference, ClaimedRef

COMMIT = "d90196a7be3fe64e1eb9225a17b2ce4a26e0ecd1"


def write_artifact(tmp_path, rows, *, schema=pc.DISPOSITION_SCHEMA,
                   commit=COMMIT, break_digest=False, row_count=None):
    path = tmp_path / "disp.jsonl"
    body = "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
    path.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if break_digest:
        digest = "0" * 64
    manifest = {
        "schema": schema,
        "artifact_sha256": digest,
        "f2_commit": commit,
        "row_count": len(rows) if row_count is None else row_count,
    }
    (tmp_path / ("disp.jsonl" + pc.MANIFEST_SUFFIX)).write_text(
        json.dumps(manifest), encoding="utf-8")
    return str(path)


def row(cid, label):
    return {"citation_id": cid, "label": label}


# ------------------------------------------------------------ happy path

def test_valid_artifact_loads_with_full_provenance(tmp_path):
    path = write_artifact(tmp_path, [row("PMC1:B1", "cleared"),
                                     row("PMC1:B2", "F2")])
    d = pc.load_artifact(path)
    assert d.mapping == {"PMC1:B1": "cleared", "PMC1:B2": "F2"}
    assert d.canonical is True
    assert d.source == pc.SOURCE_ARTIFACT
    assert d.f2_commit == COMMIT
    prov = d.provenance()
    assert prov["schema"] == pc.DISPOSITION_SCHEMA
    assert len(prov["artifact_sha256"]) == 64
    assert prov["cleared_count"] == 1


# --------------------------------------------- the duplicate-id counterexample

def test_duplicate_id_is_rejected_not_last_write_wins(tmp_path):
    """THE counterexample. Input: 'PMC1:B1' -> F2, then 'PMC1:B1' -> cleared.

    Old output: the map holds {'PMC1:B1': 'cleared'}, so a deterministic
    wrong-paper reference is admitted to the judgment band and can be published
    as an F3-F7 result. New output: refusal."""
    path = write_artifact(tmp_path, [row("PMC1:B1", "F2"),
                                     row("PMC1:B1", "cleared")])
    with pytest.raises(pc.PrebandContractError, match="duplicate citation_id"):
        pc.load_artifact(path)


def test_duplicate_id_the_old_loader_behaviour_is_gone(tmp_path):
    """Pins the SPECIFIC harm: last-write-wins clearing an F2."""
    rows = [row("PMC1:B1", "F2"), row("PMC1:B1", "cleared")]
    old = {}
    for r in rows:                      # the old loader, verbatim
        if r.get("citation_id"):
            old[r["citation_id"]] = r.get("label")
    assert old == {"PMC1:B1": "cleared"}        # <- the defect, reproduced
    path = write_artifact(tmp_path, rows)
    with pytest.raises(pc.PrebandContractError):
        pc.load_artifact(path)                   # <- and now refused


# ------------------------------------------------------ row/field validation

def test_falsy_citation_id_is_rejected_not_silently_skipped(tmp_path):
    """Old behaviour: `if cid:` DROPPED the row, so the pair became
    'disposition missing' with no trace of why."""
    path = write_artifact(tmp_path, [row("", "cleared"), row("PMC1:B2", "cleared")])
    with pytest.raises(pc.PrebandContractError, match="no usable citation_id"):
        pc.load_artifact(path)


def test_missing_label_field_is_rejected(tmp_path):
    path = write_artifact(tmp_path, [{"citation_id": "PMC1:B1"}])
    with pytest.raises(pc.PrebandContractError, match="label is missing"):
        pc.load_artifact(path)


def test_unknown_label_is_rejected(tmp_path):
    """COUNTEREXAMPLE: a 'verdict'-vocabulary artifact. Every row reads as NOT
    CLEARED, so the whole corpus is excluded and the run looks clean."""
    path = write_artifact(tmp_path, [row("PMC1:B1", "match")])
    with pytest.raises(pc.PrebandContractError, match="outside schema"):
        pc.load_artifact(path)


def test_non_canonical_id_is_rejected(tmp_path):
    path = write_artifact(tmp_path, [row("doc:B1", "cleared")])
    with pytest.raises(pc.PrebandContractError, match="not canonical"):
        pc.load_artifact(path)


def test_malformed_json_row_is_rejected(tmp_path):
    path = tmp_path / "disp.jsonl"
    path.write_text('{"citation_id":"PMC1:B1","label":"cleared"}\n{oops\n',
                    encoding="utf-8")
    body = path.read_bytes()
    (tmp_path / ("disp.jsonl" + pc.MANIFEST_SUFFIX)).write_text(json.dumps({
        "schema": pc.DISPOSITION_SCHEMA,
        "artifact_sha256": hashlib.sha256(body).hexdigest(),
        "f2_commit": COMMIT}), encoding="utf-8")
    with pytest.raises(pc.PrebandContractError, match="not valid JSON"):
        pc.load_artifact(str(path))


# ----------------------------------------------------- versioning and binding

def test_missing_sidecar_manifest_is_rejected(tmp_path):
    path = tmp_path / "disp.jsonl"
    path.write_text(json.dumps(row("PMC1:B1", "cleared")) + "\n", encoding="utf-8")
    with pytest.raises(pc.PrebandContractError, match="no sidecar manifest"):
        pc.load_artifact(str(path))


def test_wrong_schema_version_is_rejected(tmp_path):
    path = write_artifact(tmp_path, [row("PMC1:B1", "cleared")],
                          schema="preband_disposition_v0")
    with pytest.raises(pc.PrebandContractError, match="schema"):
        pc.load_artifact(path)


def test_digest_mismatch_is_rejected(tmp_path):
    """The artifact changed after its manifest was written."""
    path = write_artifact(tmp_path, [row("PMC1:B1", "cleared")], break_digest=True)
    with pytest.raises(pc.PrebandContractError, match="do not match the manifest"):
        pc.load_artifact(path)


def test_missing_f2_commit_is_rejected(tmp_path):
    path = write_artifact(tmp_path, [row("PMC1:B1", "cleared")], commit="")
    with pytest.raises(pc.PrebandContractError, match="f2_commit"):
        pc.load_artifact(path)


def test_row_count_mismatch_is_rejected(tmp_path):
    path = write_artifact(tmp_path, [row("PMC1:B1", "cleared")], row_count=99)
    with pytest.raises(pc.PrebandContractError, match="row_count mismatch"):
        pc.load_artifact(path)


def test_empty_artifact_is_rejected(tmp_path):
    path = write_artifact(tmp_path, [])
    with pytest.raises(pc.PrebandContractError, match="EMPTY|row_count"):
        pc.load_artifact(path)


# ------------------------------------------------------------ dict injection

def test_injected_dict_is_non_canonical_but_label_checked():
    d = pc.load_injected({"c": "cleared"})
    assert d.canonical is False
    assert d.source == pc.SOURCE_DICT
    with pytest.raises(pc.PrebandContractError, match="outside schema"):
        pc.load_injected({"c": "probably_fine"})


def test_non_path_non_dict_disposition_is_a_config_error():
    with pytest.raises(pc.PrebandContractError, match="must be a path or a dict"):
        pc.load_disposition(["PMC1:B1"])


# --------------------------------------------------------- corpus preflight

def _refs(pmcid, n):
    return [Reference(citation_id=f"{pmcid}:B{i}", citance="c",
                      claimed=ClaimedRef(title="t"), source_pmcid=pmcid)
            for i in range(1, n + 1)]


def test_empty_corpus_fails_closed(tmp_path):
    with pytest.raises(pc.PrebandContractError, match="no .xml/.nxml documents"):
        pc.collect_expected_ids(str(tmp_path), [], lambda p, source_pmcid=None: [],
                                lambda fn: fn)


def test_all_documents_failing_to_parse_fails_closed(tmp_path):
    def boom(path, source_pmcid=None):
        raise ValueError("not XML")
    with pytest.raises(pc.PrebandContractError, match="every document"):
        pc.collect_expected_ids(str(tmp_path), ["PMC1.xml", "PMC2.xml"], boom,
                                lambda fn: fn.replace(".xml", ""))


def test_corpus_that_parses_to_zero_references_fails_closed(tmp_path):
    with pytest.raises(pc.PrebandContractError, match="ZERO references"):
        pc.collect_expected_ids(str(tmp_path), ["PMC1.xml"],
                                lambda p, source_pmcid=None: [],
                                lambda fn: fn.replace(".xml", ""))


def test_partial_parse_failure_is_recorded_not_fatal(tmp_path):
    def half(path, source_pmcid=None):
        if source_pmcid == "PMC2":
            raise ValueError("bad xml")
        return _refs("PMC1", 2)
    ids, per_doc, failures = pc.collect_expected_ids(
        str(tmp_path), ["PMC1.xml", "PMC2.xml"], half,
        lambda fn: fn.replace(".xml", ""))
    assert ids == {"PMC1:B1", "PMC1:B2"}
    assert per_doc == {"PMC1": 2, "PMC2": 0}
    assert "PMC2" in failures


# --------------------------------------------------------- join enforcement

def test_zero_overlap_fails_closed():
    """COUNTEREXAMPLE: corpus emits 'PMC1:R1', disposition holds 'PMC1:r1'.

    Old output: one excluded record, zero queue rows, status 'complete',
    accounting_ok true -- a clean empty run. New output: refusal."""
    disp = pc.load_injected({"PMC1:r1": "cleared"})
    acc = pc.join_accounting(disp, {"PMC1:R1"})
    assert acc["matched"] == 0
    with pytest.raises(pc.PrebandContractError, match="ZERO overlap"):
        pc.enforce_join(acc, disp=disp)


def test_no_disposition_fails_closed():
    acc = pc.join_accounting(None, {"PMC1:R1"})
    with pytest.raises(pc.PrebandContractError, match="no preband_disposition"):
        pc.enforce_join(acc, disp=None)


def test_partial_coverage_is_allowed_and_accounted():
    disp = pc.load_injected({"PMC1:R1": "cleared"})
    acc = pc.join_accounting(disp, {"PMC1:R1", "PMC1:R2"})
    pc.enforce_join(acc, disp=disp)                # no raise
    assert acc["matched"] == 1
    assert acc["missing_from_disposition"] == 1
    assert acc["coverage"] == 0.5


def test_require_full_coverage_rejects_a_gap():
    disp = pc.load_injected({"PMC1:R1": "cleared"})
    acc = pc.join_accounting(disp, {"PMC1:R1", "PMC1:R2"})
    with pytest.raises(pc.PrebandContractError, match="absent from the disposition"):
        pc.enforce_join(acc, disp=disp, require_full_coverage=True)


def test_extra_disposition_ids_are_counted_not_fatal():
    disp = pc.load_injected({"PMC1:R1": "cleared", "PMC9:R9": "cleared"})
    acc = pc.join_accounting(disp, {"PMC1:R1"})
    pc.enforce_join(acc, disp=disp)
    assert acc["extra_in_disposition"] == 1


# ------------------------------------------- the residual all-missing join

def test_all_judged_pairs_missing_disposition_fails_closed():
    """The case the domain check cannot see: the disposition overlaps the corpus
    id domain, but every id it matched was structurally excluded before the
    pre-band gate, so nothing judged had an entry."""
    counts = {"excluded_preband_disposition_missing": 4}
    with pytest.raises(pc.PrebandContractError, match="clean empty run"):
        pc.enforce_join_reached(
            counts, "excluded_preband_disposition_missing",
            ("excluded_no_citance", "excluded_no_cited_pmid"))


def test_a_legitimately_all_excluded_run_is_not_flagged():
    """A corpus whose references are all genuinely F2 is a valid run with an
    empty queue. Failing it would be a false alarm on real data."""
    counts = {"excluded_preband": 4}
    pc.enforce_join_reached(counts, "excluded_preband_disposition_missing",
                            ("excluded_no_citance", "excluded_no_cited_pmid"))


def test_zero_record_run_is_not_double_reported():
    pc.enforce_join_reached({}, "excluded_preband_disposition_missing",
                            ("excluded_no_citance", "excluded_no_cited_pmid"))


# =========================================================================
# The reportability gate (Round 1 go/no-go remediation)
# =========================================================================

def test_the_structural_exclusion_bypass_is_closed():
    """CODEX B2, verbatim. A has no citance (structurally excluded before the
    pre-band gate), B is eligible but absent from the disposition.

    Old arithmetic compared missing(1) to TOTAL records(2) -> 1 != 2 -> no
    raise, status complete, accounting_ok true, queue 0, ZERO pairs judged.
    Any corpus holding one structurally excluded reference bypassed the gate --
    i.e. every real corpus. The denominator is now ELIGIBLE pairs."""
    counts = {"excluded_no_citance": 1,
              "excluded_preband_disposition_missing": 1}
    with pytest.raises(pc.PrebandContractError, match="clean empty run"):
        pc.enforce_join_reached(
            counts, "excluded_preband_disposition_missing",
            ("excluded_no_citance", "excluded_no_cited_pmid"))


def test_structural_exclusions_alone_are_not_flagged():
    """No pair reached the gate at all -> nothing to say about the join."""
    pc.enforce_join_reached({"excluded_no_citance": 3},
                            "excluded_preband_disposition_missing",
                            ("excluded_no_citance", "excluded_no_cited_pmid"))


def test_a_real_run_with_some_missing_is_not_flagged():
    counts = {"excluded_no_citance": 1,
              "excluded_preband_disposition_missing": 1,
              "predicted": 2}
    pc.enforce_join_reached(counts, "excluded_preband_disposition_missing",
                            ("excluded_no_citance", "excluded_no_cited_pmid"))


# ----------------------------------------------------------- the gate itself

DIG_A = "a" * 64
DIG_B = "b" * 64


def good_manifest(**over):
    m = {
        "status": "complete",
        "preband": {"canonical": True, "source": pc.SOURCE_ARTIFACT,
                    "corpus_manifest_sha256": DIG_A,
                    "preflight_parse_failures": {},
                    "join": {"missing_from_disposition": 0}},
        "corpus": {"manifest_sha256": DIG_A},
        "adapter": {"model": "m", "temperature": 0},
        "params": {"chain_genesis": ""},
        "code_commit": "d90196a7be3fe64e1eb9225a17b2ce4a26e0ecd1",
        "scoreable_records": 2,
        "module_sha256_stable": True,
        "total_records": 2,
        "chain_record_count": 2,
        "executed_domain": {"matches_preflight": True},
        "queue_audit": {"matches": True, "queue_rows": 2, "scoreable_rows": 2},
        "emitted_labels": {},
    }
    m.update(over)
    return m


def preds(tmp_path, ids):
    p = tmp_path / "judgment_predictions.jsonl"
    p.write_text("".join(json.dumps({"citation_id": c}) + "\n" for c in ids),
                 encoding="utf-8")
    return str(p)


def test_a_fully_bound_run_is_reportable(tmp_path):
    r = pc.reportability_report(good_manifest(),
                                preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["reportable"] is True, r["failures"]
    pc.assert_reportable_run(good_manifest(),
                             preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))


@pytest.mark.parametrize("over,clause", [
    ({"status": "in_progress"}, "status_complete"),
    ({"code_commit": ""}, "code_commit_recorded"),
    ({"scoreable_records": 0}, "pairs_judged"),
    ({"module_sha256_stable": False}, "modules_stable"),
    ({"adapter": {"model": "", "temperature": 0}}, "model_recorded"),
    ({"adapter": {"model": "m"}}, "temperature_recorded"),
    ({"params": {"chain_genesis": "abc"}}, "single_segment"),
    ({"corpus": {"manifest_sha256": DIG_B}}, "corpus_cross_bound"),
])
def test_each_clause_blocks_reportability(tmp_path, over, clause):
    r = pc.reportability_report(good_manifest(**over),
                                preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["reportable"] is False
    assert r["checks"][clause] is False
    assert any(f.startswith(clause) for f in r["failures"]), r["failures"]


def test_dict_injection_is_never_reportable(tmp_path):
    """CODEX B2 / governance: production accepts ONLY the canonical artifact."""
    m = good_manifest()
    m["preband"] = {**m["preband"], "canonical": False,
                    "source": pc.SOURCE_DICT}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["canonical_disposition"] is False
    with pytest.raises(pc.PrebandContractError, match="canonical_disposition"):
        pc.assert_reportable_run(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))


def test_partial_coverage_is_never_reportable(tmp_path):
    m = good_manifest()
    m["preband"] = {**m["preband"], "join": {"missing_from_disposition": 3}}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["full_coverage"] is False


def test_a_parse_failure_blocks_reportability(tmp_path):
    m = good_manifest()
    m["preband"] = {**m["preband"], "preflight_parse_failures": {"PMC9": "bad"}}
    r = pc.reportability_report(m, preds(tmp_path, ["PMC1:B1", "PMC1:B2"]))
    assert r["checks"]["no_parse_failures"] is False


def test_duplicate_prediction_ids_block_reportability(tmp_path):
    """CODEX B1 detection. A resumed mid-document interrupt replays the
    document and appends its rows a second time, giving [R1, R1, R2]. Those
    duplicates corrupt any precision denominator silently."""
    path = preds(tmp_path, ["PMC1:R1", "PMC1:R1", "PMC1:R2"])
    r = pc.reportability_report(good_manifest(total_records=3,
                                              chain_record_count=3), path)
    assert r["checks"]["unique_citation_ids"] is False
    assert any("PMC1:R1" in f for f in r["failures"])


def test_counters_that_undercount_the_file_block_reportability(tmp_path):
    """CODEX B1, second half: a resumed manifest's total_records covers only the
    final invocation while the predictions file holds every segment's rows."""
    path = preds(tmp_path, ["PMC1:R1", "PMC2:R1"])
    r = pc.reportability_report(good_manifest(total_records=1,
                                              chain_record_count=1), path)
    assert r["checks"]["counters_match_file"] is False
