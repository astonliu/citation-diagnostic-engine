"""Tests for the canonical Band-1 -> Band-2 disposition producer.

Every fail-closed rule gets a concrete synthetic counterexample: the input that
would have produced a wrong published number, and the refusal that now stops it.
"""
from __future__ import annotations

import json
import os

import pytest

from cde.refs import preband_disposition as pd
from cde.refs import schema as S
from cde.refs.schema import Reference, ClaimedRef

COMMIT = "d90196a7be3fe64e1eb9225a17b2ce4a26e0ecd1"


def log_row(cid, label):
    return {"citation_id": cid, "label": label, "confidence": "HIGH",
            "claimed": {}, "retrieved": {}, "log": {}, "rationale": ""}


# ---------------------------------------------------------------- happy path

def test_rows_are_built_from_the_lossless_log_including_unscoreable():
    """The whole point of using the log: operational states SURVIVE.

    The prediction file drops these, which is what makes it an invalid source."""
    rows = pd.build_rows([
        log_row("PMC1:B1", "cleared"),
        log_row("PMC1:B2", "F2"),
        log_row("PMC1:B3", "unscoreable"),
        log_row("PMC1:B4", "unverifiable"),
        log_row("PMC1:B5", "human_review"),
    ])
    assert [r["citation_id"] for r in rows] == [
        "PMC1:B1", "PMC1:B2", "PMC1:B3", "PMC1:B4", "PMC1:B5"]
    assert [r["cleared"] for r in rows] == [True, False, False, False, False]
    assert rows[0]["citing_pmcid"] == "PMC1"


def test_write_binds_bytes_schema_commit_and_accounting(tmp_path):
    out = str(tmp_path / "disp.jsonl")
    manifest = pd.write_disposition(
        [log_row("PMC1:B1", "cleared"), log_row("PMC2:B7", "F2")],
        out, f2_commit=COMMIT, generated_by="test")

    assert manifest["schema"] == pd.DISPOSITION_SCHEMA
    assert manifest["f2_commit"] == COMMIT
    assert manifest["row_count"] == 2
    assert manifest["cleared_count"] == 1
    assert manifest["label_counts"] == {"F2": 1, "cleared": 1}
    assert manifest["citing_pmcids"] == ["PMC1", "PMC2"]
    assert manifest["source"] == "band1_log_records"
    assert set(manifest["check_attestations"]) == {"F1", "F2", "F8"}
    assert all(item["performed"] is False
               for item in manifest["check_attestations"].values())

    # The hash is over the artifact AS WRITTEN, so it can be re-verified.
    import hashlib
    with open(out, "rb") as f:
        assert manifest["artifact_sha256"] == hashlib.sha256(f.read()).hexdigest()

    side = json.load(open(out + pd.MANIFEST_SUFFIX, encoding="utf-8"))
    assert side["artifact_sha256"] == manifest["artifact_sha256"]


def test_write_carries_valid_check_attestations(tmp_path):
    out = str(tmp_path / "disp.jsonl")
    checks = {
        name: {"performed": True, "source": "frozen_check_fixture",
               "snapshot_date": "2026-08-18", "attempted": 2,
               "answered": 2, "transport_failed": 0, "fired": 1,
               "reason": "fixture"}
        for name in ("F1", "F2", "F8")}
    manifest = pd.write_disposition(
        [log_row("PMC1:B1", "cleared")], out, f2_commit=COMMIT,
        check_attestations=checks)
    assert manifest["check_attestations"] == checks


def test_performed_check_requires_source_and_snapshot(tmp_path):
    with pytest.raises(pd.DispositionBuildError, match="performed F8 requires"):
        pd.write_disposition(
            [log_row("PMC1:B1", "cleared")], str(tmp_path / "disp.jsonl"),
            f2_commit=COMMIT,
            check_attestations={"F8": {"performed": True}})


def test_corpus_manifest_is_bound_when_supplied(tmp_path):
    corpus = tmp_path / "frozen_manifest.json"
    corpus.write_text(json.dumps({"documents": ["PMC1"]}), encoding="utf-8")
    out = str(tmp_path / "disp.jsonl")
    m = pd.write_disposition([log_row("PMC1:B1", "cleared")], out,
                             f2_commit=COMMIT, corpus_manifest_path=str(corpus))
    assert m["corpus_manifest_sha256"] == pd._sha256_file(str(corpus))


def test_reads_a_log_jsonl_path(tmp_path):
    src = tmp_path / "logs.jsonl"
    src.write_text("\n".join(json.dumps(log_row(f"PMC1:B{i}", "cleared"))
                             for i in range(3)) + "\n", encoding="utf-8")
    rows = pd.build_rows(str(src))
    assert len(rows) == 3


# ------------------------------------------------------ fail-closed refusals

def test_duplicate_citation_id_is_refused_at_write_time():
    """COUNTEREXAMPLE: the row that would clear a known F2.

    Band 2's old loader was last-write-wins, so 'F2' then 'cleared' for one id
    let a deterministic wrong-paper into the judgment band. The producer must
    never emit the pair in the first place."""
    with pytest.raises(pd.DispositionBuildError, match="duplicate citation_id"):
        pd.build_rows([log_row("PMC1:B1", "F2"), log_row("PMC1:B1", "cleared")])


def test_non_canonical_citation_id_is_refused():
    """COUNTEREXAMPLE: 'doc:B1' -- what parse_pmc_xml emits when source_pmcid is
    empty. It can never join Band 2, which keys on '<citing_pmcid>:<ref_id>'."""
    with pytest.raises(pd.DispositionBuildError, match="not canonical"):
        pd.build_rows([log_row("doc:B1", "cleared")])
    with pytest.raises(pd.DispositionBuildError, match="not canonical"):
        pd.build_rows([log_row("PMC1 B1", "cleared")])


def test_missing_or_none_label_is_refused():
    """An unprocessed Reference has label None. Emitting it would silently
    exclude a pair Band 1 never actually decided."""
    with pytest.raises(pd.DispositionBuildError, match="label is missing"):
        pd.build_rows([log_row("PMC1:B1", None)])


def test_unknown_label_is_refused():
    """COUNTEREXAMPLE: 'verdict'-style or free-text labels. Band 2 casefolds and
    tests membership, so an unknown label silently means NOT CLEARED."""
    with pytest.raises(pd.DispositionBuildError, match="outside schema"):
        pd.build_rows([log_row("PMC1:B1", "match")])
    with pytest.raises(pd.DispositionBuildError, match="outside schema"):
        pd.build_rows([log_row("PMC1:B1", "accurate_probably")])


def test_missing_citation_id_is_refused_not_skipped():
    """The old loader did `if cid:` and silently DROPPED falsy ids."""
    with pytest.raises(pd.DispositionBuildError, match="no usable citation_id"):
        pd.build_rows([{"citation_id": "", "label": "cleared"}])
    with pytest.raises(pd.DispositionBuildError, match="no usable citation_id"):
        pd.build_rows([{"label": "cleared"}])


def test_empty_disposition_is_refused(tmp_path):
    """A zero-row artifact excludes every pair in Band 2 and completes as a
    clean empty run -- the failure mode that matters most."""
    with pytest.raises(pd.DispositionBuildError, match="EMPTY disposition"):
        pd.write_disposition([], str(tmp_path / "d.jsonl"), f2_commit=COMMIT)


def test_unattributed_commit_is_refused(tmp_path):
    for bad in ("", "d90196a", "not-a-commit", "D90196A7BE3FE64E1EB9225A17B2CE4A26E0ECD1"):
        with pytest.raises(pd.DispositionBuildError, match="40-hex commit OID"):
            pd.write_disposition([log_row("PMC1:B1", "cleared")],
                                 str(tmp_path / "d.jsonl"), f2_commit=bad)


def test_malformed_log_line_is_refused(tmp_path):
    src = tmp_path / "logs.jsonl"
    src.write_text('{"citation_id":"PMC1:B1","label":"cleared"}\n{not json\n',
                   encoding="utf-8")
    with pytest.raises(pd.DispositionBuildError, match="not valid JSON"):
        pd.build_rows(str(src))


# ------------------------------------------- the prediction-file trap, proven

def test_prediction_source_would_lose_the_operational_rows():
    """Proves WHY the log is the canonical source, using run.py's own filter.

    An unscoreable reference is absent from the prediction set entirely, so a
    disposition built from it cannot distinguish 'Band 1 could not score this'
    from 'Band 1 never saw this document'."""
    refs = []
    for cid, label in [("PMC1:B1", S.CLEARED), ("PMC1:B2", S.UNSCOREABLE),
                       ("PMC1:B3", S.UNVERIFIABLE)]:
        r = Reference(citation_id=cid, citance="x", claimed=ClaimedRef(title="t"),
                      source_pmcid="PMC1")
        r.label = label
        refs.append(r)

    # run.py's rule, verbatim in behaviour.
    prediction_ids = {r.citation_id for r in refs
                      if r.label not in (S.UNVERIFIABLE, S.UNSCOREABLE)}
    log_ids = {r.citation_id for r in refs}

    assert prediction_ids == {"PMC1:B1"}
    assert log_ids == {"PMC1:B1", "PMC1:B2", "PMC1:B3"}
    # The canonical builder keeps all three.
    rows = pd.build_rows([log_row(r.citation_id, r.label) for r in refs])
    assert {r["citation_id"] for r in rows} == log_ids


# ------------------------------------------------------------- head_commit

def test_head_commit_reads_a_detached_head(tmp_path):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(COMMIT + "\n", encoding="utf-8")
    assert pd.head_commit(str(tmp_path)) == COMMIT


def test_head_commit_follows_a_ref(tmp_path):
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/feat/x\n", encoding="utf-8")
    (git / "refs" / "heads" / "feat").mkdir(exist_ok=True)
    (git / "refs" / "heads" / "feat" / "x").write_text(COMMIT, encoding="utf-8")
    assert pd.head_commit(str(tmp_path)) == COMMIT


def test_head_commit_returns_empty_when_unknown(tmp_path):
    assert pd.head_commit(str(tmp_path)) == ""
