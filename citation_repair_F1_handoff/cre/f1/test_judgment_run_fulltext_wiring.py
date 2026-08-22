"""The opt-in FULL-TEXT coverage path, reachable from run_natural_judgment.

Fully offline: the reader result, both judges and the model call are injected stubs.
Every fixture is a CODE-PATH fixture -- none of it is evaluation data, a gold label,
or an input to any reported number.

THE GAP THIS CLOSES. The full-text coverage path (DEC-030/032, ``coverage_v3``)
landed in ``judgment_band.run_band`` and nowhere else. ``judgment_run.py`` contained
zero occurrences of ``fetch_fulltext``, ``coverage_judge_v3``, ``cited_fulltext`` or
``COVERAGE_PROMPT_VERSION_V3``, so ``run_natural_judgment`` -- the orchestrator that
emits the durable per-pair records, the hash chain and the blind annotation queue --
could only ever judge at ABSTRACT scope. Every artifact the F3-F7 evaluation actually
reads came from the layer that could not see a body.

The organizing rule is the same opt-in guarantee run_band carries: supplying neither
seam leaves every byte unchanged, supplying both moves the scope, and supplying one
raises. What differs here is that ``judgment_run`` also STAMPS provenance per record,
so the scope change has to reach the stamp -- see
``test_the_record_stamp_is_corrected_not_left_lying``.

Tests reuse ``test_judgment_run.run()`` deliberately. A wiring test that rebuilds the
call it is checking tests the reconstruction, not the wiring.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import coverage_prompts_v3 as v3
from cre.f1 import judgment_run as jr
from .test_judgment_run import (CLEARED, extractor_of, judge_established,
                                make_ref, run)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
def section(label="results", text="Drug X reduced outcome Y in treated mice."):
    return {"label": label, "title": label.title(), "text": text,
            "content_sha256": "unused-by-the-orchestrator"}


def reader_result(*, complete=True, sections=None, reasons=None):
    """A ``fulltext_reader.fetch_fulltext``-shaped dict, carried whole."""
    sections = [section()] if sections is None else sections
    return {"pmid": "1", "pmcid": "PMC9", "resolved": True,
            "sections": sections,
            "sections_present": sorted({s["label"] for s in sections}),
            "retrieval_complete": complete,
            "incomplete_reasons": [] if complete else (reasons or ["body_too_small"]),
            "sanitized_paths": [], "source": "live"}


def v3_reply(*, engages=True, contradicts=False, specifics=(), rationale="r",
             spans=None):
    if spans is None:
        spans = ([{"label": "results", "sentence_ids": ["s1"]}] if engages else [])
    return json.dumps({
        "engages_subject": engages, "contradicts": contradicts,
        "unconfirmed_specifics": list(specifics), "rationale": rationale,
        "evidence_spans": spans})


def wired(tmp_path, monkeypatch, *, complete=True, sections=None, reply=None,
          v2_calls=None, v3_calls=None, claims=("Drug X reduces Y",)):
    """One reference through run_natural_judgment with the full-text path ON."""
    def v2_judge(cl, ev):
        if v2_calls is not None:
            v2_calls.append(cl)
        return judge_established(*([True] * len(cl)))(cl, ev)

    def call_llm(prompt):
        if v3_calls is not None:
            v3_calls.append(prompt)
        return reply if reply is not None else v3_reply()

    return run(tmp_path, [make_ref("c")],
               extractor=extractor_of(*claims), coverage_judge=v2_judge,
               disposition=CLEARED, monkeypatch=monkeypatch,
               fetch_fulltext=lambda pmid: reader_result(
                   complete=complete, sections=sections),
               coverage_judge_v3=v3.make_coverage_judge_v3(call_llm))


# ==========================================================================
# The opt-in gate: both seams, or neither
# ==========================================================================
@pytest.mark.parametrize("kwargs", [
    {"fetch_fulltext": lambda pmid: reader_result()},
    {"coverage_judge_v3": lambda claims, evidence: []},
])
def test_supplying_one_seam_alone_raises(tmp_path, monkeypatch, kwargs):
    """Half-enabling is the failure that records nothing. One seam alone either
    judges a body with the abstract-scoped prompt or fetches a body nothing reads,
    and neither shows up in any artifact -- so it raises instead."""
    with pytest.raises(ValueError, match="needs BOTH fetch_fulltext"):
        run(tmp_path, [make_ref("c")], extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True), disposition=CLEARED,
            monkeypatch=monkeypatch, **kwargs)


def test_the_config_error_fires_before_any_output_file_exists(tmp_path, monkeypatch):
    """Validated UP FRONT, like every other config defect in this module: a
    half-wired run must abort whole, never leave a partial output set that reads as
    a per-pair quarantine."""
    with pytest.raises(ValueError, match="needs BOTH fetch_fulltext"):
        run(tmp_path, [make_ref("c")], extractor=extractor_of("Drug X reduces Y"),
            coverage_judge=judge_established(True), disposition=CLEARED,
            monkeypatch=monkeypatch, fetch_fulltext=lambda pmid: reader_result())
    assert not (tmp_path / "out" / "judgment_predictions.jsonl").exists()
    assert not (tmp_path / "out" / "judgment_run_manifest.json").exists()


# ==========================================================================
# Neither seam: the default abstract path does not move
# ==========================================================================
def test_the_default_path_carries_no_fulltext_trace(tmp_path, monkeypatch):
    """The opt-in guarantee at row and manifest level. An abstract-path run must not
    gain a key, a stamp or an evidence field -- absent means the path was never
    wired, never 'unknown'."""
    manifest, rows = run(
        tmp_path, [make_ref("c")], extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True), disposition=CLEARED,
        monkeypatch=monkeypatch)
    assert manifest["coverage_prompt_version"] == "coverage_v2"
    for key in ("response_parser_version", "evidence_scope",
                "fetch_fulltext_wired", "fulltext_counts", "fulltext_note"):
        assert key not in manifest
    assert "coverage_prompts_v3.py" not in manifest["module_sha256"]
    assert "COVERAGE_PROMPT_V3" not in manifest["prompt_sha256"]
    row = rows[0]
    assert row["coverage_prompt_version"] == "coverage_v2"
    assert "response_parser_version" not in row
    assert "evidence_scope" not in row
    assert "cited_fulltext" not in row["evidence"]
    assert "evidence_span" in row["coverage_verdicts"][0]      # the v2 shape
    assert "evidence_spans" not in row["coverage_verdicts"][0]


# ==========================================================================
# Both seams, complete retrieval: coverage moves to the body
# ==========================================================================
def test_a_complete_retrieval_is_judged_by_v3_and_never_by_v2(tmp_path, monkeypatch):
    v2_calls, v3_calls = [], []
    _manifest, rows = wired(tmp_path, monkeypatch,
                            v2_calls=v2_calls, v3_calls=v3_calls)
    assert v2_calls == [], "the abstract judge ran on the full-text path"
    assert len(v3_calls) == 1
    # The claim and the ids the model must point at both reached the prompt.
    assert "Drug X reduces Y" in v3_calls[0]
    assert "[results]\n  s1  Drug X reduced outcome Y in treated mice." in v3_calls[0]
    verdict = rows[0]["coverage_verdicts"][0]
    assert verdict["established"] is True
    assert verdict["evidence_spans"] == [
        {"label": "results", "sentence_ids": ["s1"],
         "text": "Drug X reduced outcome Y in treated mice.",
         "span_source": v3.SPAN_SOURCE_SELECTED}]
    assert verdict["span_status"] == v3.SPAN_STATUS_SELECTED


def test_the_evidence_carries_the_readers_result_whole(tmp_path, monkeypatch):
    """``cited_fulltext`` is carried whole so the completeness signal the DEC-032
    aggregate needs travels WITH the evidence rather than being recomputed."""
    _manifest, rows = wired(tmp_path, monkeypatch)
    fulltext = rows[0]["evidence"]["cited_fulltext"]
    assert fulltext["retrieval_complete"] is True
    assert fulltext["sections_present"] == ["results"]
    assert fulltext["incomplete_reasons"] == []


def test_the_record_stamp_is_corrected_not_left_lying(tmp_path, monkeypatch):
    """The defect that is specific to THIS layer, and the reason the change is more
    than two parameters.

    ``_new_record`` stamps ``coverage_prompt_version = COVERAGE_PROMPT_VERSION``
    (the frozen ABSTRACT version) on every record it builds. That is correct on the
    default path and a FALSE PROVENANCE STAMP on the full-text path: the row would
    say it was judged against the abstract while carrying body-scoped spans. Same
    defect class as DEC-020's omitted temperature -- a number whose governing
    setting the artifact misstates. It is OVERWRITTEN, not supplemented, so a reader
    never has to reconcile two version fields on one row."""
    _manifest, rows = wired(tmp_path, monkeypatch)
    row = rows[0]
    assert row["coverage_prompt_version"] == "coverage_v3"
    assert row["response_parser_version"] == v3.RESPONSE_PARSER_VERSION
    assert row["evidence_scope"] == jr.EVIDENCE_SCOPE_FULLTEXT
    # ...and the per-verdict stamps agree with the record's.
    verdict = row["coverage_verdicts"][0]
    assert verdict["prompt_version"] == "coverage_v3"
    assert verdict["response_parser_version"] == v3.RESPONSE_PARSER_VERSION


def test_the_manifest_records_what_the_number_is_conditional_on(tmp_path,
                                                                monkeypatch):
    """A v3 coverage number is conditional on the reader, the v3 prompt and the span
    resolver, so with the path wired all three are hashed. CONDITIONALLY, because
    ``module_sha256`` is built from a fixed tuple and appending to it unconditionally
    would move every abstract-path manifest."""
    manifest, _rows = wired(tmp_path, monkeypatch)
    assert manifest["coverage_prompt_version"] == "coverage_v3"
    assert manifest["response_parser_version"] == v3.RESPONSE_PARSER_VERSION
    assert manifest["evidence_scope"] == jr.EVIDENCE_SCOPE_FULLTEXT
    assert manifest["fetch_fulltext_wired"] is True
    for module in ("coverage_prompts_v3.py", "coverage_aggregate.py",
                   "fulltext_reader.py", "sentence_spans.py"):
        assert module in manifest["module_sha256"]
    assert "COVERAGE_PROMPT_V3" in manifest["prompt_sha256"]
    # The frozen substrate is still pinned alongside it, not displaced.
    assert "band_prompts.py" in manifest["module_sha256"]


# ==========================================================================
# Both seams, retrieval NOT complete: hold, with no model call
# ==========================================================================
def test_an_incomplete_retrieval_holds_without_calling_either_judge(tmp_path,
                                                                     monkeypatch):
    """DEC-032. An argument from silence needs a complete text, so a partial body
    holds rather than flags -- and it holds DETERMINISTICALLY, with no model call of
    either version, exactly as the no-usable-abstract gate does."""
    v2_calls, v3_calls = [], []
    manifest, rows = wired(tmp_path, monkeypatch, complete=False,
                           v2_calls=v2_calls, v3_calls=v3_calls)
    assert v2_calls == [] and v3_calls == []
    verdict = rows[0]["coverage_verdicts"][0]
    assert verdict["established"] is None
    assert "no usable full text" in verdict["rationale"]
    assert manifest["fulltext_counts"]["no_usable_fulltext"] == 1
    assert rows[0]["disposition"] == jr.DISP_HELD_INSUFFICIENT


@pytest.mark.parametrize("bad", [None, "not-a-dict", 42, [], {"resolved": False}])
def test_mode_comes_from_configuration_never_from_the_fetched_value(
        tmp_path, monkeypatch, bad):
    """Opted in, and the reader returned nothing usable. That is an unretrieved body,
    not a licence to judge at abstract scope: inferring mode from the fetched value
    would let a fetch failure silently drop the run back to v2, and a non-dict would
    crash the batch outright."""
    v2_calls, v3_calls = [], []

    def v2_judge(cl, ev):
        v2_calls.append(cl)
        return judge_established(True)(cl, ev)

    manifest, rows = run(
        tmp_path, [make_ref("c")], extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=v2_judge, disposition=CLEARED, monkeypatch=monkeypatch,
        fetch_fulltext=lambda pmid: bad,
        coverage_judge_v3=v3.make_coverage_judge_v3(
            lambda p: v3_calls.append(p) or v3_reply()))
    assert v2_calls == [] and v3_calls == []
    assert rows[0]["coverage_verdicts"][0]["established"] is None
    assert rows[0]["coverage_prompt_version"] == "coverage_v3"   # never falls back
    assert manifest["fulltext_counts"]["no_usable_fulltext"] == 1


# ==========================================================================
# Invariants the wiring must not disturb
# ==========================================================================
def test_the_record_count_invariant_survives_the_new_tally(tmp_path, monkeypatch):
    """``counts`` is one entry per emitted record and is summed into
    ``chain_record_count``, so the full-text funnel gets its OWN tally. Putting
    ``no_usable_fulltext`` in ``counts`` would silently corrupt the record total
    rather than add a statistic -- caught while writing this, hence the guard."""
    manifest, rows = wired(tmp_path, monkeypatch, complete=False)
    assert "no_usable_fulltext" not in manifest["counts"]
    assert sum(manifest["counts"].values()) == len(rows)
    assert manifest["chain_record_count"] == len(rows)


def test_a_malformed_v3_reply_holds_the_stage_and_queues_the_pair(tmp_path,
                                                                  monkeypatch):
    """A strict reply failure is local to coverage, not the whole pair."""
    manifest, rows = wired(tmp_path, monkeypatch, reply="```json\n{}\n```")
    assert rows[0]["disposition"] == jr.DISP_HELD_INSUFFICIENT
    assert rows[0]["coverage_verdicts"][0]["established"] is None
    assert rows[0]["stage_failures"][0]["stage"] == "coverage"
    assert manifest["counts"][jr.DISP_HELD_INSUFFICIENT] == 1
    queue = tmp_path / "out" / "judgment_band_annotation_queue.jsonl"
    assert len(queue.read_text(encoding="utf-8").splitlines()) == 1


def test_a_claim_less_reference_still_routes_no_claims_on_this_path(tmp_path,
                                                                    monkeypatch):
    """3e5261d's item 1 is not disturbed by the scope change."""
    _manifest, rows = wired(tmp_path, monkeypatch, claims=())
    assert rows[0]["route"] == "NO_CLAIMS"
    assert rows[0]["disposition"] == jr.DISP_HELD_NO_CLAIMS


def test_the_queue_stays_blind_on_the_full_text_path(tmp_path, monkeypatch):
    """Body-scoped spans are richer than abstract spans, so the blindness whitelist
    matters MORE here, not less. Nothing model-judged may reach the annotator."""
    run(tmp_path, [make_ref("c")], extractor=extractor_of("Drug X reduces Y"),
        coverage_judge=judge_established(True), disposition=CLEARED,
        monkeypatch=monkeypatch,
        fetch_fulltext=lambda pmid: reader_result(),
        coverage_judge_v3=v3.make_coverage_judge_v3(lambda p: v3_reply()))
    queue = (tmp_path / "out" / "judgment_band_annotation_queue.jsonl")
    flat = queue.read_text(encoding="utf-8")
    assert flat.strip(), "the scoreable pair was not queued at all"
    for forbidden in ("evidence_spans", "span_status", "proposed_route",
                      "coverage_verdicts", "response_parser_version"):
        assert forbidden not in flat
