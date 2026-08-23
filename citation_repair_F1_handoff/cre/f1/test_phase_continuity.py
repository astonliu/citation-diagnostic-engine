"""Continuity: what the orchestrator may overlap, and what it may never reorder.

Two barriers used to stand in every document. The first drained the whole pool
at the Phase 1 -> Phase 2 transition even though a reference's Phase 2 needs only
ITS co-citation group's coverage; the second drained it again at the document
boundary even though the next document's Phase 1 is independent of this one's
Phase 2. Both are gone: a citance group is released the moment its last member
clears Phase 1, and one document of Phase 1 is submitted ahead of the document
being resolved.

None of that may be visible in the output. These tests assert the overlap
actually happens AND that everything the run is read by is unchanged: record
order, chain order, co-citation accounting, checkpoint order, and -- the
invariant that makes a resume safe -- that an interrupted document leaves
nothing durable for the replay to write a second time.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

from . import judgment_run as jr
from .schema import ClaimedRef, Reference


def _ref(cid, *, pmcid="PMC1", sentence=None, group="", members=()):
    ref = Reference(
        citation_id=cid,
        citance=sentence or f"Treatment {cid} improves outcome {cid} [1].",
        claimed=ClaimedRef(claimed_pmid=str(abs(hash(cid)) % 9000 + 1000),
                           title=f"Cited title {cid}"),
        cited_reference_marker="1", source_pmcid=pmcid, source_pmid="900",
        source_title="Citing title")
    if group:
        ref.citance_group_id = group
        ref.citance_group_members = list(members)
    return ref


def _rows(path):
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]


def _judge(claims, _evidence):
    return [{"established": True, "rationale": "r",
             "evidence_span": "improves outcome"} for _ in claims]


def _run(out_dir, xml_dir, docs, monkeypatch, *, max_workers=4,
         coverage_judge=_judge, extractor=None, **kwargs):
    """One run over ``docs`` -- a mapping of pmcid -> list[Reference]."""
    xml_dir.mkdir(parents=True, exist_ok=True)
    for pmcid in docs:
        (xml_dir / f"{pmcid}.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(
        jr, "parse_pmc_xml",
        lambda path, source_pmcid=None: list(docs[source_pmcid]))
    disposition = {ref.citation_id: "cleared"
                   for refs in docs.values() for ref in refs}
    return jr.run_natural_judgment(
        str(xml_dir), str(out_dir),
        extractor=extractor or (lambda sentence: [sentence.split("[")[0].strip()]),
        coverage_judge=coverage_judge,
        fetch_abstract=lambda pmid: (
            "Treatment improves outcome in a controlled study."),
        preband_disposition=disposition, model="test-model",
        max_workers=max_workers, **kwargs)


# --------------------------------------------------------------------------
# the overlaps actually happen
# --------------------------------------------------------------------------
def test_a_group_is_released_into_phase_2_before_the_rest_of_phase_1_finishes(
        tmp_path, monkeypatch):
    # Two solo groups. The second reference's coverage call refuses to return
    # until the FIRST reference's Phase 2 has started. Under a document-wide
    # barrier that is a deadlock the timeout catches; with group release it
    # completes immediately.
    phase2_started = threading.Event()
    observed = {}

    def coverage(claims, evidence):
        if claims and "Beta" in claims[0]:
            observed["waited"] = phase2_started.wait(10)
        return _judge(claims, evidence)

    original_finish = jr.judge_pair_finish

    def finish(rec, item, *args, **kwargs):
        phase2_started.set()
        return original_finish(rec, item, *args, **kwargs)

    monkeypatch.setattr(jr, "judge_pair_finish", finish)
    docs = {"PMC1": [_ref("c1", sentence="Alpha improves outcome for c1 [1]."),
                     _ref("c2", sentence="Beta improves outcome for c2 [1].")]}
    manifest = _run(tmp_path / "out", tmp_path / "xml", docs, monkeypatch,
                    coverage_judge=coverage, max_workers=4)
    assert observed.get("waited") is True
    assert manifest["total_records"] == 2


def test_the_next_document_starts_while_this_one_is_still_in_phase_2(
        tmp_path, monkeypatch):
    log: list = []
    lock = threading.Lock()

    def coverage(claims, evidence):
        with lock:
            log.append(("coverage", claims[0], time.monotonic()))
        return _judge(claims, evidence)

    original_finish = jr.judge_pair_finish

    def finish(rec, item, *args, **kwargs):
        time.sleep(0.05)
        out = original_finish(rec, item, *args, **kwargs)
        with lock:
            log.append(("finish", item["citation_id"], time.monotonic()))
        return out

    monkeypatch.setattr(jr, "judge_pair_finish", finish)
    docs = {
        "PMC1": [_ref("PMC1:r1", pmcid="PMC1",
                      sentence="Alpha improves outcome one [1]."),
                 _ref("PMC1:r2", pmcid="PMC1",
                      sentence="Beta improves outcome two [1].")],
        "PMC2": [_ref("PMC2:r1", pmcid="PMC2",
                      sentence="Gamma improves outcome three [1]."),
                 _ref("PMC2:r2", pmcid="PMC2",
                      sentence="Delta improves outcome four [1].")],
    }
    _run(tmp_path / "out", tmp_path / "xml", docs, monkeypatch,
         coverage_judge=coverage, max_workers=4)
    doc2_coverage = min(t for kind, key, t in log
                        if kind == "coverage" and ("Gamma" in key or "Delta" in key))
    doc1_last_finish = max(t for kind, key, t in log
                           if kind == "finish" and key.startswith("PMC1"))
    # Document 2's coverage began before document 1's Phase 2 had finished.
    assert doc2_coverage < doc1_last_finish


# --------------------------------------------------------------------------
# and nothing observable moved
# --------------------------------------------------------------------------
def _cocited(pmcid, n):
    """One citance citing n references: a real co-citation group."""
    cids = [f"{pmcid}:r{index}" for index in range(1, n + 1)]
    sentence = ("Combination therapy improves survival "
                + "".join(f"[{index}]" for index in range(1, n + 1)) + ".")
    return [_ref(cid, pmcid=pmcid, sentence=sentence,
                 group=f"{pmcid}:g01", members=cids) for cid in cids]


def test_serial_and_continuous_runs_agree_on_every_record_and_every_count(
        tmp_path, monkeypatch):
    docs = {
        "PMC1": _cocited("PMC1", 3) + [_ref("PMC1:s1", pmcid="PMC1",
                                            sentence="Solo improves outcome [1].")],
        "PMC2": _cocited("PMC2", 2),
        "PMC3": [_ref("PMC3:s1", pmcid="PMC3",
                      sentence="Another solo improves outcome [1]."),
                 _ref("PMC3:s2", pmcid="PMC3",
                      sentence="A third solo improves outcome [1].")],
    }
    serial = _run(tmp_path / "serial" / "out", tmp_path / "serial" / "xml",
                  docs, monkeypatch, max_workers=1)
    parallel = _run(tmp_path / "par" / "out", tmp_path / "par" / "xml",
                    docs, monkeypatch, max_workers=6)

    def strip(rows):
        return [{k: v for k, v in row.items() if k != "ts"} for row in rows]

    serial_out = tmp_path / "serial" / "out"
    parallel_out = tmp_path / "par" / "out"
    assert strip(_rows(parallel_out / "judgment_predictions.jsonl")) == \
        strip(_rows(serial_out / "judgment_predictions.jsonl"))
    # The chain is the order, hashed: identical links prove identical order.
    assert (parallel_out / "judgment_run_record_hashes.jsonl").read_text() == \
        (serial_out / "judgment_run_record_hashes.jsonl").read_text()
    assert (parallel_out / "judgment_run_cocitation_groups.jsonl").read_text() == \
        (serial_out / "judgment_run_cocitation_groups.jsonl").read_text()
    assert (parallel_out / "judgment_run_checkpoint.jsonl").read_text() == \
        (serial_out / "judgment_run_checkpoint.jsonl").read_text()
    for key in ("counts", "cocitation", "marker_scope", "emitted_labels",
                "finding_labels", "scoreable_records", "accounting_ok",
                "total_records", "refs_seen"):
        left, right = parallel[key], serial[key]
        if key == "cocitation":
            left = {k: v for k, v in left.items() if k != "groups_path"}
            right = {k: v for k, v in right.items() if k != "groups_path"}
        assert left == right, key


def test_the_co_citation_group_of_a_document_is_aggregated_exactly_once(
        tmp_path, monkeypatch):
    docs = {"PMC1": _cocited("PMC1", 4)}
    manifest = _run(tmp_path / "out", tmp_path / "xml", docs, monkeypatch,
                    max_workers=4)
    groups = _rows(tmp_path / "out" / "judgment_run_cocitation_groups.jsonl")
    assert len(groups) == 1
    assert manifest["cocitation"]["cocitation_groups"] == 1
    assert manifest["cocitation"]["members_in_cocitation_groups"] == 4
    assert manifest["cocitation"]["denominator_per_sentence_group"] == 1
    rows = _rows(tmp_path / "out" / "judgment_predictions.jsonl")
    assert [row["citation_id"] for row in rows] == [
        f"PMC1:r{index}" for index in range(1, 5)]
    assert all(row["cocitation"]["size"] == 4 for row in rows)


def test_group_less_references_still_collapse_into_one_sentence_group(
        tmp_path, monkeypatch):
    # Every group-less item carries the same empty sentence id, so the
    # document-wide set collapses them to ONE. Aggregating group by group must
    # not turn that into one per reference.
    docs = {"PMC1": [_ref(f"c{index}", sentence=f"Solo {index} improves it [1].")
                     for index in range(1, 5)]}
    parallel = _run(tmp_path / "par" / "out", tmp_path / "par" / "xml",
                    docs, monkeypatch, max_workers=4)
    serial = _run(tmp_path / "ser" / "out", tmp_path / "ser" / "xml",
                  docs, monkeypatch, max_workers=1)
    assert parallel["cocitation"]["denominator_per_sentence_group"] == \
        serial["cocitation"]["denominator_per_sentence_group"] == 1


# --------------------------------------------------------------------------
# the resume invariant the continuity work depends on
# --------------------------------------------------------------------------
def test_an_interrupt_inside_phase_2_leaves_nothing_durable_to_duplicate(
        tmp_path, monkeypatch):
    docs = {"PMC1": [_ref("c1", sentence="Alpha improves outcome one [1]."),
                     _ref("c2", sentence="Beta improves outcome two [1].")]}
    original_finish = jr.judge_pair_finish
    calls = {"n": 0}

    def interrupted(rec, item, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return original_finish(rec, item, *args, **kwargs)

    monkeypatch.setattr(jr, "judge_pair_finish", interrupted)
    out = tmp_path / "out"
    xml = tmp_path / "xml"
    with pytest.raises(KeyboardInterrupt):
        _run(out, xml, docs, monkeypatch, max_workers=1)
    predictions = out / "judgment_predictions.jsonl"
    # The first record's Phase 2 SUCCEEDED. Under per-reference writing its row
    # was already on disk with no checkpoint covering it, and the resume wrote it
    # again. Buffering to the checkpoint boundary is what closes that.
    assert not predictions.exists() or predictions.read_text() == ""

    monkeypatch.setattr(jr, "judge_pair_finish", original_finish)
    _run(out, xml, docs, monkeypatch, max_workers=1)
    ids = [row["citation_id"] for row in _rows(predictions)]
    assert ids == ["c1", "c2"]
    sidecar = _rows(out / "judgment_run_record_hashes.jsonl")
    assert [row["citation_id"] for row in sidecar] == ["c1", "c2"]


def test_an_unparseable_document_checkpoints_in_document_order(
        tmp_path, monkeypatch):
    xml = tmp_path / "xml"
    xml.mkdir(parents=True)
    for pmcid in ("PMC1", "PMC2"):
        (xml / f"{pmcid}.xml").write_text("<x/>", encoding="utf-8")

    def parse(path, source_pmcid=None):
        if source_pmcid == "PMC1":
            raise ValueError("unparseable")
        return [_ref("PMC2:r1", pmcid="PMC2",
                     sentence="Gamma improves outcome [1].")]

    monkeypatch.setattr(jr, "parse_pmc_xml", parse)
    out = tmp_path / "out"
    jr.run_natural_judgment(
        str(xml), str(out),
        extractor=lambda sentence: [sentence.split("[")[0].strip()],
        coverage_judge=_judge,
        fetch_abstract=lambda pmid: "Gamma improves outcome in a study.",
        preband_disposition={"PMC2:r1": "cleared"}, model="test-model",
        max_workers=4)
    checkpoints = [json.loads(line) for line in
                   (out / "judgment_run_checkpoint.jsonl"
                    ).read_text().splitlines()]
    # A document that could not be judged still takes its turn: its checkpoint
    # may not overtake an earlier one, and it may not be skipped either.
    assert [row["pmcid"] for row in checkpoints] == ["PMC1", "PMC2"]
    assert "error" in checkpoints[0]


def test_an_interrupt_after_the_group_rows_does_not_duplicate_them_on_resume(
        tmp_path, monkeypatch):
    """The co-citation group artifact is durable only at the checkpoint.

    Group rows are computed BEFORE Phase 2 and were written there, so the window
    between "group rows durable" and "checkpoint durable" spanned the whole of
    Phase 2. A crash inside it replayed the document and appended every group
    record a second time -- the duplication path closed for predictions, still
    open for the one artifact left out of the buffer.
    """
    docs = {"PMC1": _cocited("PMC1", 3)}
    original_finish = jr.judge_pair_finish
    calls = {"n": 0}

    def interrupted(rec, item, *args, **kwargs):
        calls["n"] += 1
        # After the group rows exist and after the first record is composed.
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return original_finish(rec, item, *args, **kwargs)

    monkeypatch.setattr(jr, "judge_pair_finish", interrupted)
    out = tmp_path / "out"
    xml = tmp_path / "xml"
    with pytest.raises(KeyboardInterrupt):
        _run(out, xml, docs, monkeypatch, max_workers=1)
    groups_path = out / "judgment_run_cocitation_groups.jsonl"
    assert not groups_path.exists() or groups_path.read_text() == ""

    monkeypatch.setattr(jr, "judge_pair_finish", original_finish)
    manifest = _run(out, xml, docs, monkeypatch, max_workers=1)
    groups = _rows(groups_path)
    assert len(groups) == 1
    assert groups[0]["members"] == [f"PMC1:r{index}" for index in range(1, 4)]
    assert manifest["cocitation"]["cocitation_groups"] == 1
    # Every append-mode artifact keeps the same invariant, so the run's own
    # accounting of the groups file matches the file.
    assert len(_rows(out / "judgment_predictions.jsonl")) == 3
