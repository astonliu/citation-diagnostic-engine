"""The OPT-IN full-text coverage path: reader -> v3 judge -> DEC-032 aggregate.

Fully offline. Every model call, fetch and reader result is an injected stub.
Every fixture is a CODE-PATH fixture: none of it is evaluation data, a gold
label, or an input to any reported number.

The organizing rule of this file is the opt-in guarantee. Supplying neither
``fetch_fulltext`` nor ``coverage_judge_v3`` must leave the band's output
unchanged to the byte; supplying both moves coverage from the cited abstract to
the retrieved body. Both halves are tested, and the byte-identity half is pinned
against a golden captured from base 539bea2.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import band_prompts as bp
from cre.f1 import coverage_aggregate as ca
from cre.f1 import coverage_prompts_v3 as v3
from cre.f1 import judgment_band as jb


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
ONE_REF_XML = """\
<article><body><p>A finding <xref ref-type="bibr" rid="R1">1</xref>.</p></body>
<back><ref-list><ref id="R1"><element-citation>
<article-title>Paper</article-title><pub-id pub-id-type="pmid">1</pub-id>
</element-citation></ref></ref-list></back></article>
"""
SURROGATE = "\ud800"


def _xml_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir(parents=True)
    (d / "PMC1.xml").write_text(ONE_REF_XML, encoding="utf-8")
    return str(d)


def _patch_not_review(monkeypatch):
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)


def section(label="results", title="Results", text="Drug X reduced infarct size."):
    return {"label": label, "title": title, "text": text,
            "content_sha256": "unused-by-the-band"}


def reader_result(*, complete=True, sections=None, reasons=None, **extra):
    """A fulltext_reader.fetch_fulltext-shaped dict, carried whole (C2)."""
    sections = [section()] if sections is None else sections
    out = {
        "pmid": "1", "pmcid": "PMC9", "resolved": True,
        "sections": sections,
        "sections_present": sorted({s["label"] for s in sections}),
        "retrieval_complete": complete,
        "incomplete_reasons": [] if complete else (reasons or ["body_too_small"]),
        "sanitized_paths": [],
        "source": "live",
    }
    out.update(extra)
    return out


def coverage_reply(*, engages=True, contradicts=False, specifics=(),
                   rationale="r", span_label="results", span_ids=("s1",),
                   span_text=None, spans=None):
    """A coverage_v3 reply under the SENTENCE-SELECTION contract.

    The span field has been reshaped four times, each removing one way for the model
    to misreport source text: one ``"label: text"`` string; a ``label``/``text``
    PAIR, so a label could not contradict its own text; a LIST of ``{label, text}``,
    so two non-contiguous passages could both be recorded; and now ``{label,
    sentence_ids}``, so the model POINTS instead of retyping and the text is exact by
    construction (ZD 2026-08-11, DEC-047).

    ``span_ids`` is the one-entry shorthand most rows want. ``span_text`` produces
    the DRIFT-FALLBACK shape, which the judge aligns post hoc rather than rejecting;
    pass ``spans`` for anything else."""
    if spans is None:
        if not engages:
            spans = []
        elif span_text is not None:
            spans = [{"label": span_label, "text": span_text}]
        else:
            spans = [{"label": span_label, "sentence_ids": list(span_ids)}]
    return json.dumps({
        "engages_subject": engages, "contradicts": contradicts,
        "unconfirmed_specifics": list(specifics),
        "rationale": rationale,
        "evidence_spans": spans,
    }, ensure_ascii=False)


def verdict(*, engages=True, contradicts=False, specifics=()):
    """A parsed v3 verdict. ``engages=False`` carries no spans, which
    ``parse_coverage_v3`` enforces in BOTH directions -- an off-topic verdict cites
    nothing, and an engaged one must cite at least one passage."""
    return v3.parse_coverage_v3(coverage_reply(
        engages=engages, contradicts=contradicts, specifics=specifics))


def run(tmp_path, monkeypatch, *, fulltext=None, reply=None, extractor=None,
        capture=None):
    """run_band on the opt-in path when `fulltext` is given, else the default."""
    _patch_not_review(monkeypatch)
    out_dir = tmp_path / "out"
    kwargs = {}
    if fulltext is not None:
        def call_llm(prompt):
            if capture is not None:
                capture.append(prompt)
            return reply if reply is not None else coverage_reply()
        kwargs = {
            "fetch_fulltext": lambda pmid: fulltext,
            "coverage_judge_v3": v3.make_coverage_judge_v3(call_llm),
        }
    manifest = jb.run_band(
        _xml_dir(tmp_path), str(out_dir),
        extractor=extractor or (lambda sentence: ["A finding"]),
        coverage_judge=lambda claims, evidence: [
            ca._no_usable_abstract_dict() for _ in claims],
        fetch_abstract=lambda pmid: "An abstract.",
        session=object(), **kwargs)
    return manifest, out_dir


def rows(out_dir, name):
    path = out_dir / name
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]


# ==========================================================================
# C4 / DEC-032 -- the truth table
# ==========================================================================
@pytest.mark.parametrize("engages,contradicts,specifics,complete,expected", [
    # A contradiction is PRESENT evidence: decides either way.
    (True, True, (), True, False),
    (True, True, (), False, False),
    # Engaged, uncontradicted, nothing unconfirmed: present evidence, established.
    (True, False, (), True, True),
    # Arguments from SILENCE need a complete text to become False.
    (True, False, ("dose",), True, False),
    (False, False, (), True, False),
    (True, False, ("dose",), False, None),
    (False, False, (), False, None),
])
def test_dec032_truth_table(engages, contradicts, specifics, complete, expected):
    got = ca.aggregate_fulltext_coverage(
        verdict(engages=engages, contradicts=contradicts, specifics=specifics),
        complete)
    # Tri-state discipline: identity, never truthiness.
    if expected is None:
        assert got is None
    elif expected is True:
        assert got is True
    else:
        assert got is False


def test_positive_finding_survives_incomplete_retrieval():
    """Sol flag 2. The row the spec's table does not list: engaged, uncontradicted,
    nothing unconfirmed, retrieval INCOMPLETE. What we retrieved established the
    claim, and the pages we did not retrieve cannot un-establish it -- only
    arguments from silence depend on completeness."""
    assert ca.aggregate_fulltext_coverage(verdict(), False) is True
    assert ca.aggregate_fulltext_coverage(verdict(), None) is True


@pytest.mark.parametrize("complete", [1, "true", "True", None, 0, [], object()])
def test_completeness_is_compared_by_identity_not_truthiness(complete):
    """Sol flag 3. retrieval_complete=1 is not retrieval_complete=True. Anything
    that is not exactly True holds rather than flags -- fail-closed per DEC-032."""
    assert ca.aggregate_fulltext_coverage(
        verdict(engages=False), complete) is None
    # ... while the real True still decides.
    assert ca.aggregate_fulltext_coverage(verdict(engages=False), True) is False


def test_off_topic_with_contradiction_is_rejected_as_malformed():
    with pytest.raises(ValueError, match="engages_subject=false requires"):
        ca.aggregate_fulltext_coverage(
            bp.CoverageVerdict(established=None, engages_subject=False,
                               contradicts=True, unconfirmed_specifics=()),
            True)


def test_the_abstract_scoped_aggregate_is_untouched():
    """The default path's mapping must not move: silence in an ABSTRACT is still
    unknown, even though silence in a complete full text is now absence."""
    assert ca.aggregate_coverage(False, False, []) is None
    assert ca.aggregate_coverage(True, False, ["dose"]) is None
    assert ca.aggregate_coverage(True, False, []) is True
    assert ca.aggregate_coverage(True, True, []) is False


# ==========================================================================
# C1 -- the v3 prompt, renderer and judge
# ==========================================================================
def test_prompt_and_parser_versions_move_independently():
    """DEC-022, now demonstrated in both directions and THREE contract versions.

    At the v3 introduction the SCOPE moved and the five-key contract did not, so the
    prompt version moved alone. THREE times since, the opposite: the scope has NOT
    moved -- ``coverage_v3`` still means full-text sections -- while the output
    contract changed shape, splitting the span into a label/text pair (``_6key_v2``),
    replacing that pair with a span LIST (``_spanlist_v3``), and finally replacing
    generated text with SELECTED sentence ids (``_spanids_v4``, DEC-047). Each time
    the PARSER version moved alone."""
    assert v3.COVERAGE_PROMPT_VERSION_V3 == "coverage_v3"    # scope, unmoved
    assert v3.RESPONSE_PARSER_VERSION == "strict_coverage_spanids_v4"
    assert bp.COVERAGE_PROMPT_VERSION == "coverage_v2"       # frozen, unmoved
    # The frozen five-key parser stays exactly where it was: the v3 path no longer
    # calls it, and band_prompts.py is not touched (blob OID fa01126e...).
    assert bp._COVERAGE_KEYS == frozenset({
        "engages_subject", "contradicts", "unconfirmed_specifics",
        "rationale", "evidence_span"})


def test_prompt_never_asks_the_model_about_completeness():
    """DEC-032 is a CODE decision. The model must not see, or reason about,
    whether retrieval was complete -- otherwise the hold becomes a model opinion."""
    lowered = v3.COVERAGE_PROMPT_V3.lower()
    for forbidden in ("retrieval_complete", "complete retrieval",
                      "incomplete", "completeness"):
        assert forbidden not in lowered
    assert "established" in lowered            # only to FORBID emitting it
    assert 'No "established" field' in v3.COVERAGE_PROMPT_V3


def test_renderer_groups_repeated_labels_into_one_id_space():
    """Sol flag 5, REVISED for sentence ids (DEC-047).

    A paper has more than one methods block, and the old renderer emitted each as its
    own ``methods:`` block in document order. It cannot any more, and the reason is
    forced rather than chosen: a span cites ``(label, id)``, so two blocks each
    starting at ``s1`` would make ``methods:s1`` ambiguous and unresolvable. Repeated
    labels are therefore concatenated into ONE block with ONE id space.

    WHAT IS PRESERVED: document order WITHIN a label (M1 is s1, M2 is s2), and
    first-appearance order across labels. WHAT IS LOST: the interleaving of M1, R1, M2
    -- and that is acceptable because rule 7 makes the whole retrieved text one
    evidence pool, so cross-label adjacency was never load-bearing for judging."""
    rendered = v3.render_evidence_sections([
        section("methods", "M", "M1"),
        section("results", "R", "R1"),
        section("methods", "M", "M2"),
    ])
    assert rendered == "[methods]\n  s1  M1\n  s2  M2\n\n[results]\n  s1  R1"
    assert rendered.index("M1") < rendered.index("M2")
    assert rendered.index("[methods]") < rendered.index("[results]")


def test_renderer_skips_sections_with_no_text():
    assert v3.render_evidence_sections([section(text="  "), section(text="A")]) \
        == "[results]\n  s1  A"
    assert v3.render_evidence_sections([]) == ""
    assert v3.render_evidence_sections(None) == ""


def test_both_slots_are_filled_and_nothing_is_left_behind():
    prompt = v3.render_prompt("My claim", [section(text="Body text.")])
    assert "<<ATOMIC_CLAIM>>" not in prompt
    assert "<<EVIDENCE_SECTIONS>>" not in prompt
    assert "My claim" in prompt
    assert "[results]\n  s1  Body text." in prompt


@pytest.mark.parametrize("poison", ["<<EVIDENCE_SECTIONS>>", "<<ATOMIC_CLAIM>>"])
def test_placeholder_text_in_the_claim_is_preserved_verbatim(poison):
    """Sol flag 1. Chained str.replace rescans its own output, so a claim
    containing the evidence placeholder would have that text overwritten BY the
    evidence -- corrupting the claim under judgment. Both inputs are untrusted:
    the claim comes from a model, the sections from a fetched paper."""
    claim = f"Claim mentioning {poison} literally"
    prompt = v3.render_prompt(claim, [section(text="Body text.")])
    assert claim in prompt, "the claim was corrupted by slot substitution"
    assert "[results]\n  s1  Body text." in prompt
    # Exactly one evidence block: the placeholder inside the claim is inert.
    assert prompt.count("[results]\n  s1  Body text.") == 1


def test_placeholder_text_in_a_section_cannot_forge_a_claim_slot():
    prompt = v3.render_prompt("Real claim", [section(text="<<ATOMIC_CLAIM>>")])
    assert "Real claim" in prompt
    assert prompt.count("Real claim") == 1
    assert "[results]\n  s1  <<ATOMIC_CLAIM>>" in prompt


def test_v3_judge_calls_the_model_once_per_claim_with_labelled_sections():
    prompts = []

    def call_llm(prompt):
        prompts.append(prompt)
        return coverage_reply()

    judge = v3.make_coverage_judge_v3(call_llm)
    out = judge(["claim one", "claim two"],
                {"cited_fulltext": reader_result(sections=[
                    section("methods", "M", "How."), section("results", "R", "What.")])})
    assert len(prompts) == 2
    assert len(out) == 2
    assert ("[methods]\n  s1  How.\n\n[results]\n  s1  What."
            in prompts[0])
    assert "claim one" in prompts[0] and "claim two" in prompts[1]
    assert out[0]["established"] is True


def test_complete_with_no_sections_fails_closed():
    """Sol flag 6. The reader cannot emit complete-with-no-sections, but this seam
    is INJECTED. Without this guard the model would judge an empty <evidence>
    block, answer engages_subject=false, and -- because complete makes silence
    mean absence -- yield a confident established=False and an F6 out of no
    evidence whatsoever."""
    calls = []

    def call_llm(prompt):
        calls.append(prompt)
        return coverage_reply(engages=False, specifics=())

    judge = v3.make_coverage_judge_v3(call_llm)
    out = judge(["claim"], {"cited_fulltext": reader_result(
        complete=True, sections=[])})
    assert calls == [], "the model was asked to judge an empty evidence block"
    assert out[0]["established"] is None
    assert "no usable full text" in out[0]["rationale"]


def test_sections_that_are_all_blank_also_fail_closed():
    calls = []
    judge = v3.make_coverage_judge_v3(lambda p: calls.append(p) or coverage_reply())
    out = judge(["claim"], {"cited_fulltext": reader_result(
        complete=True, sections=[section(text="   ")])})
    assert calls == []
    assert out[0]["established"] is None


# ==========================================================================
# Sol flag 4 -- the strict parser, exercised THROUGH v3
# ==========================================================================
_SPANS = '"evidence_spans":[{"label":"results","text":"s"}]'


@pytest.mark.parametrize("reply", [
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],'
    '"rationale":"r",' + _SPANS + ',"established":true}',               # extra key
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],'
    + _SPANS + '}',                                                     # missing rationale
    '```json\n{"engages_subject":true,"contradicts":false,'
    '"unconfirmed_specifics":[],"rationale":"r",' + _SPANS + '}\n```',  # fenced
    '',                                                                 # empty
    '{"engages_subject":"true","contradicts":false,"unconfirmed_specifics":[],'
    '"rationale":"r",' + _SPANS + '}',                                  # string bool
    # Added for run 3 item 1: two concatenated objects. Quarantine is the SAFETY
    # PROPERTY and the parser is not being relaxed to accept them.
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],'
    '"rationale":"r",' + _SPANS + '}\n'
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],'
    '"rationale":"second",' + _SPANS + '}',
])
def test_v3_replies_go_through_the_unchanged_strict_parser(reply):
    judge = v3.make_coverage_judge_v3(lambda prompt: reply)
    with pytest.raises(ValueError):
        judge(["claim"], {"cited_fulltext": reader_result()})


def test_null_rationale_normalizes_to_empty_string_through_v3():
    """rationale is log-only, so an explicit null must not discard an otherwise
    valid coverage decision -- but a MISSING key still fails closed (above)."""
    reply = ('{"engages_subject":true,"contradicts":false,'
             '"unconfirmed_specifics":[],"rationale":null,'
             '"evidence_spans":[{"label":"results","text":"s"}]}')
    judge = v3.make_coverage_judge_v3(lambda prompt: reply)
    out = judge(["claim"], {"cited_fulltext": reader_result()})
    assert out[0]["rationale"] == ""
    assert out[0]["established"] is True


def test_a_malformed_v3_reply_quarantines_the_reference(tmp_path, monkeypatch):
    manifest, out_dir = run(tmp_path, monkeypatch, fulltext=reader_result(),
                            reply="```json\n{}\n```")
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert rows(out_dir, "judgment_band_annotation_queue.jsonl") == []
    assert rows(out_dir, "judgment_band_items.jsonl")[0]["proposed_route"] \
        == jb.ROUTE_PARSE_QUARANTINE


# ==========================================================================
# C2 / C3 -- evidence assembly and the band's routing
# ==========================================================================
def test_evidence_gains_cited_fulltext_only_when_the_seam_is_supplied():
    item = {"cited_pmid": "1"}
    without = jb.assemble_evidence(item, fetch_abstract=lambda p: "A.")
    assert "cited_fulltext" not in without
    with_ft = jb.assemble_evidence(item, fetch_abstract=lambda p: "A.",
                                   fetch_fulltext=lambda p: reader_result())
    assert with_ft["cited_fulltext"]["retrieval_complete"] is True
    # Abstract fields are assembled either way: the two scopes COEXIST.
    assert without["cited_abstract"] == with_ft["cited_abstract"] == "A."


def test_reader_result_is_carried_whole():
    evidence = jb.assemble_evidence(
        {"cited_pmid": "1"}, fetch_abstract=lambda p: "A.",
        fetch_fulltext=lambda p: reader_result(complete=False))
    carried = evidence["cited_fulltext"]
    for key in ("resolved", "retrieval_complete", "incomplete_reasons",
                "sections_present", "sections", "sanitized_paths"):
        assert key in carried, key


def test_complete_retrieval_routes_through_v3(tmp_path, monkeypatch):
    capture = []
    manifest, out_dir = run(tmp_path, monkeypatch,
                            fulltext=reader_result(sections=[
                                section("methods", "M", "How."),
                                section("results", "R", "What.")]),
                            capture=capture)
    assert len(capture) == 1
    assert "[methods]\n  s1  How.\n\n[results]\n  s1  What." in capture[0]
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["coverage_verdicts"][0]["prompt_version"] == "coverage_v3"
    assert row["coverage_verdicts"][0]["established"] is True
    assert row["proposed_route"] == jb.ROUTE_FULL_COVERAGE


def test_incomplete_retrieval_holds_without_a_model_call(tmp_path, monkeypatch):
    capture = []
    manifest, out_dir = run(tmp_path, monkeypatch,
                            fulltext=reader_result(complete=False), capture=capture)
    assert capture == [], "the v3 judge was called on an incomplete retrieval"
    assert manifest["counts"]["no_usable_fulltext"] == 1
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["coverage_verdicts"][0]["established"] is None
    assert row["proposed_route"] == jb.ROUTE_HELD
    # Held for want of a body: durable, but not answerable, so not queued.
    assert rows(out_dir, "judgment_band_annotation_queue.jsonl") == []


def test_no_usable_fulltext_counter_is_absent_on_the_default_path(
        tmp_path, monkeypatch):
    manifest, _out = run(tmp_path, monkeypatch)
    assert "no_usable_fulltext" not in manifest["counts"], (
        "an unconditional counter would change every default run's manifest bytes")


def test_half_wiring_is_a_configuration_error(tmp_path, monkeypatch):
    _patch_not_review(monkeypatch)
    for kwargs in ({"fetch_fulltext": lambda p: reader_result()},
                   {"coverage_judge_v3": v3.make_coverage_judge_v3(
                       lambda p: coverage_reply())}):
        # The check runs BEFORE any directory is created or read, so a config
        # defect aborts the run rather than half-enabling a scope change after
        # output already exists.
        with pytest.raises(ValueError, match="BOTH fetch_fulltext"):
            jb.run_band(str(tmp_path / "never-created"), str(tmp_path / "o"),
                        extractor=lambda s: ["A finding"],
                        coverage_judge=lambda c, e: [],
                        fetch_abstract=lambda p: "A.", session=object(), **kwargs)
    assert not (tmp_path / "o").exists(), "output dir created before the abort"


# ==========================================================================
# C5 -- annotation blindness
# ==========================================================================
def _walk_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _walk_keys(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from _walk_keys(value)


def test_contaminated_cited_fulltext_is_scrubbed_at_every_depth():
    """Sol flag 7. The outer whitelist admits cited_fulltext; the RECURSIVE scrub
    is what keeps it blind once it is admitted."""
    contaminated = reader_result(sections=[{
        "label": "results", "title": "R", "text": "T",
        "content_sha256": "h",
        "proposed_route": "F6_FLAGGED",
        "nested": {"proposed_verdict": "F6", "rationale": "leak", "keep": 1},
    }])
    contaminated["rationale"] = "top-level leak"
    contaminated["proposed_route"] = "F6_FLAGGED"
    payload = jb.annotation_payload({
        "item_key": "PMC1:R1", "citing_sentence": "S", "cited_pmid": "1",
        "atomic_claims": ["c"],
        "evidence": {"cited_pmid": "1", "cited_fulltext": contaminated},
    })
    keys = set(_walk_keys(payload))
    for forbidden in ("proposed_route", "proposed_verdict", "rationale"):
        assert forbidden not in keys, f"blind payload leaked {forbidden}"
    carried = payload["evidence"]["cited_fulltext"]
    assert carried["sections"][0]["text"] == "T"        # useful content survives
    assert carried["sections"][0]["nested"]["keep"] == 1
    assert carried["sanitized_paths"] == []             # preserved, not scrubbed
    assert carried["retrieval_complete"] is True


def test_cited_fulltext_is_whitelisted_for_the_annotator():
    assert "cited_fulltext" in jb.ANNOTATION_EVIDENCE_KEYS


def test_scrubbing_does_not_mutate_the_item_record():
    contaminated = reader_result()
    contaminated["proposed_route"] = "F6_FLAGGED"
    item = {"item_key": "k", "citing_sentence": "S", "cited_pmid": "1",
            "atomic_claims": [], "evidence": {"cited_fulltext": contaminated}}
    jb.annotation_payload(item)
    assert item["evidence"]["cited_fulltext"]["proposed_route"] == "F6_FLAGGED"


# ==========================================================================
# C6 -- encodability
# ==========================================================================
@pytest.mark.parametrize("where", ["title", "text"])
def test_unencodable_section_quarantines_the_reference(tmp_path, monkeypatch, where):
    """Sol flag 8. A lone surrogate anywhere in the carried reader dict must
    quarantine the reference, not kill the batch at the JSONL write."""
    sec = section()
    sec[where] = "Bad " + SURROGATE + " here"
    manifest, out_dir = run(tmp_path, monkeypatch,
                            fulltext=reader_result(sections=[sec]))
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert rows(out_dir, "judgment_band_annotation_queue.jsonl") == []
    item = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert item["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert SURROGATE not in json.dumps(item, ensure_ascii=False)
    # The batch completed: the document checkpointed.
    assert [r["pmcid"] for r in rows(out_dir, "judgment_band_checkpoint.jsonl")
            if "pmcid" in r] == ["PMC1"]


def test_unencodable_v3_model_text_quarantines_the_reference(
        tmp_path, monkeypatch):
    """The surrogate arrives from the MODEL this time, through the v3 reply, and
    lands on the record via the coverage verdict rather than the reader.

    IT NOW ARRIVES THROUGH THE RATIONALE, not the span, and that narrowing is worth
    recording. Under sentence SELECTION the model never supplies span text -- the
    resolver reads it out of the section -- so a span cannot carry a code point the
    section did not already contain, and the reader sanitizes sections at its own
    boundary. A surrogate offered in a drift-fallback quote simply fails to align and
    is dropped. The rationale is free text and remains an ingress, so this guard is
    still load-bearing; it now has one door instead of two."""
    manifest, out_dir = run(
        tmp_path, monkeypatch, fulltext=reader_result(),
        reply=coverage_reply(rationale="bad " + SURROGATE))
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert rows(out_dir, "judgment_band_annotation_queue.jsonl") == []
    item = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert item["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert SURROGATE not in json.dumps(item, ensure_ascii=False)
    assert [r["pmcid"] for r in rows(out_dir, "judgment_band_checkpoint.jsonl")
            if "pmcid" in r] == ["PMC1"]


# ==========================================================================
# The opt-in guarantee: the default path does not move
# ==========================================================================
# Golden captured by running an 8-route fixture through run_band at base
# 539bea2 and at this commit, under a frozen environment (TZ, LC_ALL,
# PYTHONHASHSEED, SOURCE_DATE_EPOCH, pinned clock), with the baseline
# materialized read-only via `git archive` -- the live worktree was never
# switched. items/queue/checkpoint/manifest and BOTH stdout passes compared
# equal on raw bytes AND byte length, and a second pass over each existing
# out_dir proved resume identity. These are the observable invariants.
_DEFAULT_PATH_GOLDEN_COUNTS = {
    "docs_processed", "refs_seen", "items_built", "excluded_no_citance",
    "excluded_no_cited_pmid", "no_usable_abstract", "cited_is_review",
    "F6_FLAGGED", "FULL_COVERAGE", "HELD_LOW_CONFIDENCE", "PARSE_QUARANTINE",
    "parse_quarantine_unencodable_text", "parse_quarantine_not_serializable",
    "coverage_established", "coverage_contradicted",
    "coverage_unconfirmed_specific", "coverage_off_topic",
}


def test_default_path_manifest_counter_set_is_unchanged(tmp_path, monkeypatch):
    """Any NEW unconditional counter -- even zero-valued -- changes the manifest
    bytes of every default run and breaks the opt-in guarantee. This pins the
    exact key set, so adding one cannot pass unnoticed."""
    manifest, _out = run(tmp_path, monkeypatch)
    assert set(manifest["counts"]) == _DEFAULT_PATH_GOLDEN_COUNTS


def test_default_path_stamps_the_abstract_prompt_version(tmp_path, monkeypatch):
    manifest, out_dir = run(tmp_path, monkeypatch)
    assert manifest["coverage_prompt_version"] == "coverage_v2"
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["coverage_verdicts"][0]["prompt_version"] == "coverage_v2"
    assert "cited_fulltext" not in row["evidence"]


def test_default_path_still_queues_its_held_items(tmp_path, monkeypatch):
    """The no-queue rule belongs to the incomplete-FULL-TEXT hold only. A default
    run's held items keep reaching the annotator exactly as before."""
    manifest, out_dir = run(tmp_path, monkeypatch)
    assert manifest["counts"]["no_usable_abstract"] == 0
    assert len(rows(out_dir, "judgment_band_annotation_queue.jsonl")) == 1


# ==========================================================================
# Mode comes from CONFIGURATION, never from the fetched value
# ==========================================================================
@pytest.mark.parametrize("bad", [None, "not-a-dict", 42, [], {"resolved": False}])
def test_a_failed_or_malformed_fetch_holds_and_never_falls_back_to_v2(
        tmp_path, monkeypatch, bad):
    """Opted in, the reader returned nothing usable. That is an unretrieved body,
    not a licence to judge at abstract scope: inferring mode from the fetched
    value let a fetch failure silently drop the run back to v2, and a non-dict
    crashed the batch outright with an AttributeError the guard cannot catch."""
    v2_calls, v3_calls = [], []

    def v2_judge(claims, evidence):
        v2_calls.append(1)
        return [ca._no_usable_abstract_dict() for _ in claims]

    _patch_not_review(monkeypatch)
    out_dir = tmp_path / "out"
    manifest = jb.run_band(
        _xml_dir(tmp_path), str(out_dir),
        extractor=lambda sentence: ["A finding"], coverage_judge=v2_judge,
        fetch_abstract=lambda pmid: "An abstract.",
        fetch_fulltext=lambda pmid: bad,
        coverage_judge_v3=v3.make_coverage_judge_v3(
            lambda prompt: v3_calls.append(prompt) or coverage_reply()),
        session=object())

    assert v2_calls == [], "opted-in run fell back to the abstract judge"
    assert v3_calls == [], "the v3 judge was called without a usable body"
    assert manifest["counts"]["no_usable_fulltext"] == 1
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["proposed_route"] == jb.ROUTE_HELD
    assert row["coverage_verdicts"][0]["established"] is None
    assert rows(out_dir, "judgment_band_annotation_queue.jsonl") == []


# ==========================================================================
# Provenance is truthful about the scope a row was judged at
# ==========================================================================
def test_fulltext_rows_and_manifest_carry_v3_provenance(tmp_path, monkeypatch):
    manifest, out_dir = run(tmp_path, monkeypatch, fulltext=reader_result())
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["coverage_prompt_version"] == "coverage_v3"
    assert row["coverage_verdicts"][0]["prompt_version"] == "coverage_v3"
    assert manifest["coverage_prompt_version"] == "coverage_v3"
    assert manifest["params"]["fetch_fulltext_wired"] is True
    assert manifest["params"]["band_mode"] == jb.BAND_MODE_FULLTEXT
    assert manifest["params"]["evidence_scope"] == "fulltext_sections"
    assert "abstract-scoped" not in manifest["coverage_distribution"]["note"]


def test_a_quarantined_fulltext_row_also_stamps_v3(tmp_path, monkeypatch):
    _manifest, out_dir = run(tmp_path, monkeypatch, fulltext=reader_result(),
                             reply="```json\n{}\n```")
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert row["coverage_prompt_version"] == "coverage_v3"


def test_default_path_provenance_is_untouched(tmp_path, monkeypatch):
    manifest, out_dir = run(tmp_path, monkeypatch)
    row = rows(out_dir, "judgment_band_items.jsonl")[0]
    assert row["coverage_prompt_version"] == "coverage_v2"
    assert manifest["coverage_prompt_version"] == "coverage_v2"
    assert "abstract-scoped" in manifest["coverage_distribution"]["note"]
    for absent in ("fetch_fulltext_wired", "band_mode", "evidence_scope"):
        assert absent not in manifest["params"]


# ==========================================================================
# An out_dir belongs to ONE coverage mode
# ==========================================================================
def _checkpoint_lines(out_dir):
    return (out_dir / "judgment_band_checkpoint.jsonl").read_text(
        encoding="utf-8").splitlines()


def test_fulltext_run_marks_its_checkpoint_and_abstract_run_does_not(
        tmp_path, monkeypatch):
    _m, ft_out = run(tmp_path / "ft", monkeypatch, fulltext=reader_result())
    assert json.loads(_checkpoint_lines(ft_out)[0]) == {
        "band_mode": jb.BAND_MODE_FULLTEXT}
    _m2, ab_out = run(tmp_path / "ab", monkeypatch)
    assert all("band_mode" not in line for line in _checkpoint_lines(ab_out))


@pytest.mark.parametrize("first_ft,second_ft", [(True, False), (False, True)])
def test_resuming_across_a_mode_switch_raises(tmp_path, monkeypatch, first_ft,
                                              second_ft):
    """The checkpoint keys on pmcid alone, so a mode switch would skip completed
    documents and leave one output set holding rows judged at two evidence
    scopes -- undetectable after the fact. Raises in BOTH directions."""
    _patch_not_review(monkeypatch)
    xml_dir = _xml_dir(tmp_path)
    out_dir = str(tmp_path / "out")

    def go(use_fulltext):
        kwargs = {}
        if use_fulltext:
            kwargs = {"fetch_fulltext": lambda pmid: reader_result(),
                      "coverage_judge_v3": v3.make_coverage_judge_v3(
                          lambda prompt: coverage_reply())}
        return jb.run_band(
            xml_dir, out_dir, extractor=lambda sentence: ["A finding"],
            coverage_judge=lambda claims, evidence: [
                ca._no_usable_abstract_dict() for _ in claims],
            fetch_abstract=lambda pmid: "An abstract.", session=object(), **kwargs)

    go(first_ft)
    with pytest.raises(ValueError, match="fresh out_dir"):
        go(second_ft)


def test_resuming_in_the_same_mode_is_allowed(tmp_path, monkeypatch):
    _patch_not_review(monkeypatch)
    xml_dir = _xml_dir(tmp_path)
    out_dir = str(tmp_path / "out")
    for _ in range(2):
        jb.run_band(
            xml_dir, out_dir, extractor=lambda sentence: ["A finding"],
            coverage_judge=lambda claims, evidence: [],
            fetch_abstract=lambda pmid: "An abstract.",
            fetch_fulltext=lambda pmid: reader_result(),
            coverage_judge_v3=v3.make_coverage_judge_v3(
                lambda prompt: coverage_reply()), session=object())
    # One marker, one document, no duplication.
    lines = _checkpoint_lines(tmp_path / "out")
    assert sum("band_mode" in line for line in lines) == 1
    assert sum("pmcid" in line for line in lines) == 1


# ==========================================================================
# Q1 disposition SUPERSEDED by DEC-047: the span IS bound at runtime now
# ==========================================================================
def test_span_text_is_bound_at_runtime_and_payload_stays_blind():
    """Q1=(a) is SUPERSEDED, and in the direction it was arguing against.

    Q1=(a) held that span text must not be verified against the section at runtime:
    the prompt would require verbatim-ness and an offline audit would check it,
    because quarantining a non-verbatim span would drop exactly the rows the audit
    exists to surface. That reasoning was sound GIVEN GENERATION -- and generation is
    what DEC-047 removed. There is no longer a model-supplied text to verify or to
    reject: the resolver reads the sentence out of the section, so the span is bound
    at runtime and correct by construction. The dilemma Q1 adjudicated is gone rather
    than decided.

    What the fabricated-span case becomes: the model quotes something the paper never
    says, nothing clears the alignment floor, and the record shows an honest MISS
    instead of a fabricated span. No quarantine, and no false provenance either.

    The blindness half survives untouched -- rationale and spans are item-record
    fields only, and the annotation payload is a whitelist at both levels."""
    judge = v3.make_coverage_judge_v3(
        lambda prompt: coverage_reply(span_text="never in any section"))
    out = judge(["claim"], {"cited_fulltext": reader_result()})
    assert out[0]["evidence_spans"] == []
    assert out[0]["span_status"] == v3.SPAN_STATUS_UNALIGNED
    assert out[0]["established"] is True          # DEC-047: spans do not gate
    # A SELECTED span, by contrast, cannot fail the audit at all.
    selected = v3.make_coverage_judge_v3(lambda prompt: coverage_reply())(
        ["claim"], {"cited_fulltext": reader_result()})
    assert v3.spans_are_verbatim(selected[0]["evidence_spans"],
                                 reader_result()["sections"]) is True

    payload = jb.annotation_payload({
        "item_key": "k", "citing_sentence": "S", "cited_pmid": "1",
        "atomic_claims": ["c"],
        "evidence": {"cited_pmid": "1", "cited_fulltext": reader_result()},
        "coverage_verdicts": [out[0]],
    })
    flat = json.dumps(payload, ensure_ascii=False)
    assert "never in any section" not in flat
    assert "evidence_spans" not in set(_walk_keys(payload))
    assert "coverage_verdicts" not in payload
