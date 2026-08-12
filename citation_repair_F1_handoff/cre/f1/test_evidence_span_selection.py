"""Evidence spans by SENTENCE SELECTION, not generation (ZD 2026-08-11, DEC-047).

Fully offline. Every model call, fetch and reader result is an injected stub; the
live ``CR42`` row lives in ``test_output_contract_v3_live.py``. Every fixture is a
CODE-PATH fixture: none of it is evaluation data, a gold label, or an input to any
reported number.

THE DESIGN ERROR THIS CLOSES. ``coverage_v3`` asked the model to REPRODUCE source
text verbatim and then rejected the verdict when it could not. Three commits treated
successive symptoms of that as typos: a label that contradicted its own text
(3e5261d item 6), a stitched two-passage span (de3e040 item 2), and finally run 4,
where CR42 STILL quarantined -- now on ``an engaged claim needs at least one
evidence span`` -- losing all six of its claims.

The literature is unanimous that generation is the outlier. MultiVerS classifies
over sentence-boundary tokens; Sarol et al. retrieve candidate sentences and pass
those to the verifier; ReClaim emits sentence-level citations because passage-level
attribution "falls short in verifiability". FullCite measured prompt-based verbatim
generation against post-hoc alignment at Snippet-F1 12.80% -> 61.87% (ASQA) and
6.18% -> 24.23% (BioASQ), in alignment's favour.

So the judge now SELECTS: it returns sentence ids, code resolves them, and spans are
verbatim BY CONSTRUCTION. Tables stop being a special case because a row is one unit
the model never retypes. And an evidence-selection failure becomes a measured recall
miss instead of a destroyed reference.

Items 1 and 3 were written here as STRICT xfails and observed failing at de3e040
before either fix landed.

WHAT THESE TESTS CANNOT PROVE. Whether the model actually returns ids, and whether
CR42 stops quarantining, are model-behaviour questions that only a live run can
answer. Prompt rules are asserted here as prompt-contract assertions only.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import coverage_prompts_v3 as v3
from cre.f1 import judgment_band as jb
from cre.f1 import sentence_spans as ss


# ==========================================================================
# fixtures
# ==========================================================================
ONE_REF_XML = """\
<article><body><p>A finding <xref ref-type="bibr" rid="R1">1</xref>.</p></body>
<back><ref-list><ref id="R1"><element-citation>
<article-title>Paper</article-title><pub-id pub-id-type="pmid">1</pub-id>
</element-citation></ref></ref-list></back></article>
"""

#: Run 3's CR4 discussion, the two load-bearing passages with prose between them.
CR4_LEAD = ("N-containing molecules are often physically and chemically shielded "
            "by recalcitrant substrates such as lignin (25, 26).")
CR4_MIDDLE = "Soil texture varied little across sites."
CR4_TAIL = ("Because the degradation of these substrates constitutes the "
            "rate-limiting step in soil organic matter (SOM) decomposition "
            "(34, 35), N addition may slow decomposition.")
CR4_DISCUSSION = f"{CR4_LEAD} {CR4_MIDDLE} {CR4_TAIL}"

#: PMC8076174's real table shape, as fulltext_reader renders it: a caption block,
#: then one line per row, pipe-delimited. Rows carry '.' inside numbers, which is
#: exactly why a table must not be split on sentence punctuation.
TABLE_TEXT = (
    "Fungal species used in the microcosm experiments.\n\n"
    "Species name | Code | Phyllum | Decay type | NBRC | DDBJ | Source\n"
    "Armillaria cepistipes | Armi | Basidio | White | 110165 | AB907593 | W\n"
    "Mycena galopus | Myce | Basidio | White | 3.14 | AB907594 | W"
)


def _xml_dir(tmp_path):
    d = tmp_path / "xml"
    d.mkdir(parents=True)
    (d / "PMC1.xml").write_text(ONE_REF_XML, encoding="utf-8")
    return str(d)


def _patch_not_review(monkeypatch):
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines()]


def _sections(*pairs):
    pairs = pairs or (("results", "Drug X reduced infarct size."),)
    return [{"label": label, "title": label.title(), "text": text,
             "content_sha256": "unused-by-the-band"}
            for label, text in pairs]


def _reader_result(sections=None, *, complete=True):
    sections = _sections() if sections is None else sections
    return {"pmid": "1", "pmcid": "PMC9", "resolved": True,
            "sections": sections,
            "sections_present": sorted({s["label"] for s in sections}),
            "retrieval_complete": complete,
            "incomplete_reasons": [] if complete else ["body_too_small"],
            "sanitized_paths": [], "source": "live"}


def _reply(*, engages=True, contradicts=False, specifics=(), rationale="r",
           spans=None):
    """A coverage_v3 reply under the SENTENCE-ID contract."""
    if spans is None:
        spans = ([{"label": "results", "sentence_ids": ["s1"]}]
                 if engages else [])
    return json.dumps({
        "engages_subject": engages, "contradicts": contradicts,
        "unconfirmed_specifics": list(specifics), "rationale": rationale,
        "evidence_spans": spans,
    }, ensure_ascii=False)


def _judge_once(reply, sections=None):
    """One claim through the v3 judge, returning its single verdict dict."""
    sections = _sections() if sections is None else sections
    judge = v3.make_coverage_judge_v3(lambda prompt: reply)
    return judge(["claim"], {"cited_fulltext": _reader_result(sections)})[0]


def _run_v3(tmp_path, monkeypatch, *, reply, sections=None, claims=("A finding",),
            **extra):
    _patch_not_review(monkeypatch)
    out_dir = tmp_path / "out"
    manifest = jb.run_band(
        _xml_dir(tmp_path), str(out_dir),
        extractor=lambda sentence: list(claims),
        coverage_judge=lambda cl, ev: [{"established": True} for _ in cl],
        fetch_abstract=lambda pmid: "An abstract.",
        fetch_fulltext=lambda pmid: _reader_result(sections),
        coverage_judge_v3=v3.make_coverage_judge_v3(
            reply if callable(reply) else (lambda prompt: reply)),
        session=object(), **extra)
    return manifest, out_dir


# ==========================================================================
# ITEM 1 -- deterministic sentence segmentation with stable, section-scoped ids
# ==========================================================================
def test_a_prose_section_segments_into_numbered_sentences():
    """Acceptance row 1. Ids are ``s1``..``sN`` in DOCUMENT ORDER and scoped to the
    section, so ``discussion:s2`` and ``table:s2`` are different units."""
    units = ss.segment_section("discussion", CR4_DISCUSSION)
    assert [u["id"] for u in units] == ["s1", "s2", "s3"]
    assert units[0]["text"] == CR4_LEAD
    assert units[1]["text"] == CR4_MIDDLE
    assert units[2]["text"] == CR4_TAIL


def test_a_table_section_segments_one_unit_per_row():
    """Acceptance row 2, and the whole reason selection beats generation. A row is
    ONE unit: the model points at it and never retypes it. ``3.14`` inside a row
    must not split the row, and the pipes survive untouched."""
    units = ss.segment_section("table", TABLE_TEXT)
    assert [u["id"] for u in units] == ["s1", "s2", "s3", "s4"]
    assert units[0]["text"] == "Fungal species used in the microcosm experiments."
    assert units[1]["text"].startswith("Species name | Code | Phyllum")
    assert units[3]["text"].endswith("| AB907594 | W")
    assert "3.14" in units[3]["text"]          # not split on the decimal point
    for unit in units[1:]:
        assert unit["text"].count(" | ") >= 5


@pytest.mark.parametrize("text,expected", [
    # Decimals, p-values and measurements.
    ("Mortality fell by 12.5%. P < 0.05 throughout.",
     ["Mortality fell by 12.5%.", "P < 0.05 throughout."]),
    # Numeric citation groups, which look like sentence ends but are not.
    ("Shielded by lignin (25, 26). This slows decomposition (34, 35).",
     ["Shielded by lignin (25, 26).", "This slows decomposition (34, 35)."]),
    # The abbreviations a biomedical corpus is full of.
    ("Wadden et al. report F1 67.2. We disagree.",
     ["Wadden et al. report F1 67.2.", "We disagree."]),
    ("See Fig. 2 for the layout. Panel A is the control.",
     ["See Fig. 2 for the layout.", "Panel A is the control."]),
    ("E. coli dominated the culture. S. aureus did not.",
     ["E. coli dominated the culture.", "S. aureus did not."]),
    ("Growth was slow, e.g. 2 mm per day. Controls grew faster.",
     ["Growth was slow, e.g. 2 mm per day.", "Controls grew faster."]),
    # Other terminators still terminate.
    ("Does N addition matter? It does! Truly.",
     ["Does N addition matter?", "It does!", "Truly."]),
])
def test_the_segmenter_survives_biomedical_punctuation(text, expected):
    """A segmenter that splits on every '.' would shatter this corpus: decimals,
    p-values, 'et al.', 'Fig. 2' and abbreviated genera all end in a period that
    ends no sentence. Ids are the provenance handle, so a wrong boundary is a wrong
    citation."""
    assert [u["text"] for u in ss.segment_section("results", text)] == expected


def test_segmentation_is_deterministic_across_calls():
    """Acceptance row 3. Ids are the provenance handle: if they moved between the
    prompt and the resolve, a verdict would cite a sentence it never saw. Purity is
    what guarantees that -- same text in, same units out, no clock and no model."""
    for label, text in (("discussion", CR4_DISCUSSION), ("table", TABLE_TEXT)):
        first = ss.segment_section(label, text)
        for _ in range(3):
            assert ss.segment_section(label, text) == first


def test_the_segmenter_is_named_and_versioned():
    """It has to be recorded, because a stored id only means something relative to
    the segmenter that produced it. Re-resolving a run's spans later needs the
    version that made them."""
    assert ss.SEGMENTER_NAME
    assert isinstance(ss.SEGMENTER_VERSION, int)
    assert ss.segmenter_provenance() == {
        "name": ss.SEGMENTER_NAME, "version": ss.SEGMENTER_VERSION}


def test_the_prompt_renders_ids_beside_the_sentences():
    """Acceptance row 1, prompt side. The model can only point at what it can see
    an id for."""
    sections = _sections(("discussion", CR4_DISCUSSION), ("table", TABLE_TEXT))
    rendered = v3.render_evidence_sections(sections)
    assert "[discussion]" in rendered and "[table]" in rendered
    assert "s1" in rendered and "s3" in rendered
    # The full sentence text is still present -- ids are an addition, not a
    # replacement. A judge cannot select evidence it cannot read.
    assert CR4_LEAD in rendered
    assert "Species name | Code | Phyllum" in rendered
    # Document order, and discussion (first in the list) comes first.
    assert rendered.index("[discussion]") < rendered.index("[table]")


def test_selected_ids_resolve_to_text_and_both_are_stored():
    """Acceptance rows 4-5. The record carries the IDS (machine-checkable
    provenance) and the RESOLVED TEXT (a human-readable artifact). Storing only ids
    would make the grading sheet unreadable; storing only text would put us back to
    generation, where the text is whatever the model typed."""
    sections = _sections(("discussion", CR4_DISCUSSION))
    verdict = _judge_once(_reply(spans=[
        {"label": "discussion", "sentence_ids": ["s1", "s3"]}]), sections)
    assert verdict["evidence_spans"] == [
        {"label": "discussion", "sentence_ids": ["s1", "s3"],
         "text": f"{CR4_LEAD} {CR4_TAIL}", "span_source": v3.SPAN_SOURCE_SELECTED}]
    assert verdict["span_status"] == v3.SPAN_STATUS_SELECTED


def test_a_selected_span_is_verbatim_by_construction():
    """The property the whole redesign buys. Under generation, verbatim-ness was a
    prompt rule the model broke three times running; under selection it is not a
    rule at all -- the text comes from the section, so there is nothing to violate."""
    sections = _sections(("table", TABLE_TEXT))
    verdict = _judge_once(_reply(spans=[
        {"label": "table", "sentence_ids": ["s2"]}]), sections)
    span = verdict["evidence_spans"][0]
    assert span["text"] in TABLE_TEXT
    assert v3.spans_are_verbatim(verdict["evidence_spans"], sections) is True


def test_an_id_absent_from_the_named_section_is_a_parse_error():
    """Acceptance row 6, and the one failure that STAYS a quarantine. An id the
    section does not have is a reply that cannot be interpreted at all -- unlike a
    WRONG but existing id, which is a recall/precision miss and is kept."""
    sections = _sections(("discussion", CR4_DISCUSSION))
    with pytest.raises(ValueError, match="no sentence"):
        _judge_once(_reply(spans=[
            {"label": "discussion", "sentence_ids": ["s99"]}]), sections)


def test_a_wrong_but_existing_id_is_kept_as_a_selection_miss():
    """The other side of row 6. Pointing at the wrong sentence is exactly what
    Recall@k measures; discarding those rows would delete the measurement."""
    sections = _sections(("discussion", CR4_DISCUSSION))
    verdict = _judge_once(_reply(spans=[
        {"label": "discussion", "sentence_ids": ["s2"]}]), sections)
    assert verdict["evidence_spans"][0]["text"] == CR4_MIDDLE
    assert verdict["span_status"] == v3.SPAN_STATUS_SELECTED
    assert verdict["established"] is True          # the verdict is not touched


def test_a_label_outside_the_supplied_sections_is_still_a_parse_error():
    """Kept from 3e5261d: a span cannot cite a section this call never showed the
    model, and a label with no section has no id space to resolve against."""
    with pytest.raises(ValueError, match="not one of the labels supplied"):
        _judge_once(_reply(spans=[{"label": "intro", "sentence_ids": ["s1"]}]))


# ==========================================================================
# ITEM 2 -- post-hoc alignment when the reply carries prose instead of ids
# ==========================================================================
def test_quoted_prose_is_aligned_to_the_sentence_it_matches():
    """Acceptance row 7. FullCite's measured-best strategy, and it costs nothing at
    inference time: a reply that quotes instead of pointing is ALIGNED to the
    section rather than rejected."""
    sections = _sections(("discussion", CR4_DISCUSSION))
    # Same sentence, lightly mangled -- a dropped citation group and a case change.
    quoted = ("n-containing molecules are often physically and chemically "
              "shielded by recalcitrant substrates such as lignin")
    verdict = _judge_once(_reply(spans=[
        {"label": "discussion", "text": quoted}]), sections)
    span = verdict["evidence_spans"][0]
    assert span["span_source"] == v3.SPAN_SOURCE_ALIGNED
    assert span["sentence_ids"] == ["s1"]
    assert span["text"] == CR4_LEAD               # the SECTION's text, not the model's
    assert verdict["span_status"] == v3.SPAN_STATUS_ALIGNED


def test_alignment_takes_the_best_match_not_the_first_over_threshold():
    """Best-match, because two sentences can both clear 0.7 and only one is right."""
    a = "N addition increased the recalcitrant carbon pool by 22.7 percent."
    b = "N addition increased the labile carbon pool by 22.7 percent."
    sections = _sections(("results", f"{a} {b}"))
    verdict = _judge_once(_reply(spans=[
        {"label": "results",
         "text": "N addition increased the labile carbon pool by 22.7 percent"}]),
        sections)
    assert verdict["evidence_spans"][0]["sentence_ids"] == ["s2"]
    assert verdict["evidence_spans"][0]["text"] == b


def test_prose_below_the_jaccard_floor_is_unaligned_and_the_verdict_survives():
    """Acceptance row 8. Below 0.7 nothing is claimed -- but the reference is NOT
    destroyed. That is item 3's rule reached through item 2's door."""
    sections = _sections(("discussion", CR4_DISCUSSION))
    verdict = _judge_once(_reply(spans=[
        {"label": "discussion", "text": "something this paper never says at all"}]),
        sections)
    assert verdict["evidence_spans"] == []
    assert verdict["span_status"] == v3.SPAN_STATUS_UNALIGNED
    assert verdict["established"] is True         # DEC-047: spans do not gate
    assert verdict["engages_subject"] is True


def test_the_jaccard_floor_is_word_level_and_pinned_at_0_7():
    """FullCite's threshold, taken as measured rather than tuned here."""
    assert v3.ALIGNMENT_JACCARD_FLOOR == 0.7
    assert ss.word_jaccard("a b c d", "a b c d") == 1.0
    assert ss.word_jaccard("a b c d", "e f g h") == 0.0
    # Order-insensitive and duplicate-insensitive: it is a set measure.
    assert ss.word_jaccard("a b c", "c b a a") == 1.0
    assert ss.word_jaccard("", "a b") == 0.0


# ==========================================================================
# ITEM 3 / DEC-047 -- a missing span is a RECORDED MISS, never a quarantine
# ==========================================================================
def test_an_engaged_claim_with_no_span_is_recorded_not_quarantined():
    """Acceptance row 9, and the defect run 4 measured.

    At de3e040 this raised ``an engaged claim needs at least one evidence span``,
    which propagated out of coverage_judge and quarantined the whole reference.
    CR42 lost all six of its claims that way. Quarantine is per REFERENCE, so
    P(reference lost) = 1 - (1-p)**n_claims: BIASED DELETION concentrated on the
    references carrying the most claims, which are the ones most likely to carry a
    fault. Every system in the literature counts this as a recall miss and keeps
    going -- Sarol reports Recall@20 = 0.54 and retains the item."""
    verdict = _judge_once(_reply(engages=True, spans=[]))
    assert verdict["evidence_spans"] == []
    assert verdict["span_status"] == v3.SPAN_STATUS_NOT_FOUND
    assert verdict["established"] is True
    assert verdict["engages_subject"] is True


def test_the_reference_and_all_its_claims_survive_a_missing_span(
        tmp_path, monkeypatch):
    """The reference-level half: six claims, none with a span, and all six verdicts
    reach the durable record under a real route."""
    six = tuple(f"claim {i}" for i in range(1, 7))
    manifest, out_dir = _run_v3(tmp_path, monkeypatch,
                                reply=_reply(engages=True, spans=[]), claims=six)
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 0
    row = _rows(out_dir / "judgment_band_items.jsonl")[0]
    assert row["proposed_route"] != jb.ROUTE_PARSE_QUARANTINE
    assert len(row["coverage_verdicts"]) == 6
    assert all(v["span_status"] == v3.SPAN_STATUS_NOT_FOUND
               for v in row["coverage_verdicts"])


def test_engages_subject_false_carries_no_spans_and_is_not_a_miss():
    """Acceptance row 11. An off-topic claim has nothing to select, so an empty list
    is correct rather than a failure -- it must not inflate the not_found tally."""
    verdict = _judge_once(_reply(engages=False))
    assert verdict["evidence_spans"] == []
    assert verdict["span_status"] == v3.SPAN_STATUS_NOT_APPLICABLE


def test_spans_no_longer_gate_the_verdict_anywhere(tmp_path, monkeypatch):
    """DEC-047 in one assertion. The §D self-sufficiency gate is GONE: it gated on a
    task with kappa 0.20-0.37 human agreement, so it produced noise, not rigour. The
    verdict is decided by the DEC-032 truth table over engages_subject /
    contradicts / unconfirmed_specifics, and by nothing about the spans."""
    for spans, expected_status in (
            ([], v3.SPAN_STATUS_NOT_FOUND),
            ([{"label": "results", "sentence_ids": ["s1"]}],
             v3.SPAN_STATUS_SELECTED),
            ([{"label": "results", "text": "utterly unrelated prose"}],
             v3.SPAN_STATUS_UNALIGNED)):
        verdict = _judge_once(_reply(engages=True, spans=spans))
        assert verdict["span_status"] == expected_status
        assert verdict["established"] is True, spans
    # ...and a contradiction still decides False regardless of its spans.
    verdict = _judge_once(_reply(engages=True, contradicts=True, spans=[]))
    assert verdict["established"] is False


def test_the_prompt_no_longer_threatens_the_verdict_over_spans():
    """The prompt text has to match DEC-047, or the model keeps self-censoring
    verdicts to protect its spans -- which is the failure mode that produced run 4's
    empty span list in the first place."""
    flat = " ".join(v3.COVERAGE_PROMPT_V3.split())
    # The §D gate, in the exact words de3e040 used, is GONE. Asserted as the specific
    # sentence rather than a bare "not established" substring: the definition of
    # unconfirmed_specifics legitimately contains that phrase ("every load-bearing
    # part of the claim not established anywhere in the supplied sections"), and
    # blanket-banning it would forbid the field's own description.
    assert "needs text outside" not in flat
    assert "is not established -- so if you cannot" not in flat
    assert "not established" not in flat.replace(
        "the claim not established anywhere in the supplied sections", "")
    # ...and DEC-047 is stated positively, where the model reads it.
    assert "They do not affect the verdict" in flat
    assert "never trim, soften or withhold a finding" in flat
    assert "that is a recorded outcome, not a failure" in flat
    assert "sentence_ids" in flat


def test_parse_quarantine_now_means_only_an_unparseable_reply(
        tmp_path, monkeypatch):
    """Item 3's last clause. Quarantine keeps its meaning for genuine parse
    failures -- concatenated objects, the de3e040 case -- and loses it for
    evidence-selection misses."""
    two = _reply() + "\n" + _reply(rationale="second")
    manifest, _out = _run_v3(tmp_path, monkeypatch, reply=two)
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1


# ==========================================================================
# ITEM 5 -- the metrics the redesign makes available
# ==========================================================================
def test_the_manifest_records_the_segmenter_and_the_span_tallies(
        tmp_path, monkeypatch):
    """Acceptance row 12. With ids, evidence selection is measurable with no new
    annotation: these feed Recall@k and sentence-selection F1 against Sarol's
    Recall@20 = 0.54 and MultiVerS's SciFact 67.2 once gold spans exist."""
    manifest, _out = _run_v3(
        tmp_path, monkeypatch, claims=("a", "b", "c"),
        reply=_cycle([
            _reply(spans=[{"label": "results", "sentence_ids": ["s1"]}]),
            _reply(spans=[{"label": "results",
                           "text": "Drug X reduced infarct size"}]),
            _reply(engages=True, spans=[]),
        ]))
    block = manifest["evidence_selection"]
    assert block["segmenter"] == ss.segmenter_provenance()
    assert block["engaged_claims"] == 3
    assert block["engaged_claims_with_span"] == 2
    assert block["span_status"] == {
        v3.SPAN_STATUS_SELECTED: 1, v3.SPAN_STATUS_ALIGNED: 1,
        v3.SPAN_STATUS_NOT_FOUND: 1}
    assert block["span_source"] == {
        v3.SPAN_SOURCE_SELECTED: 1, v3.SPAN_SOURCE_ALIGNED: 1}
    assert block["span_count_distribution"] == {"0": 1, "1": 2}
    assert manifest["counts"]["evidence_span_not_found"] == 1


def test_the_evidence_selection_block_is_absent_on_the_default_path(
        tmp_path, monkeypatch):
    """It is a full-text-path measurement. An unconditional key would change the
    manifest bytes of every default run and break the opt-in guarantee."""
    _patch_not_review(monkeypatch)
    manifest = jb.run_band(
        _xml_dir(tmp_path), str(tmp_path / "out"),
        extractor=lambda sentence: ["A finding"],
        coverage_judge=lambda cl, ev: [{"established": True} for _ in cl],
        fetch_abstract=lambda pmid: "An abstract.", session=object())
    assert "evidence_selection" not in manifest
    assert "evidence_span_not_found" not in manifest["counts"]


def _cycle(replies):
    """A call_llm that returns each reply in turn -- one model call per claim."""
    box = {"i": 0}

    def call_llm(prompt):
        reply = replies[box["i"] % len(replies)]
        box["i"] += 1
        return reply
    return call_llm


# ==========================================================================
# CONTRADICTIONS 41 -- temperature reaches the manifest
# ==========================================================================
def test_temperature_is_recorded_in_the_manifest(tmp_path, monkeypatch):
    """DEC-046 pins temperature=0, and a pinned setting that is not RECORDED is not
    evidenced. Same class as DEC-020's omitted top_p and 3e5261d's omitted model."""
    manifest, _out = _run_v3(tmp_path, monkeypatch, reply=_reply(), temperature=0)
    assert manifest["params"]["temperature"] == 0


def test_an_unsupplied_temperature_is_absent_never_guessed(tmp_path, monkeypatch):
    """Absent means absent. Defaulting it to 0 would record a pin that may not have
    been applied -- a fabricated provenance record, which is worse than a gap."""
    manifest, _out = _run_v3(tmp_path, monkeypatch, reply=_reply())
    assert "temperature" not in manifest["params"]


# ==========================================================================
# Invariants that must survive the reshape
# ==========================================================================
def test_route_of_no_claims_is_unchanged():
    """Acceptance row 14."""
    assert jb.route([]) == jb.ROUTE_NO_CLAIMS


def test_the_parser_version_moved_and_the_prompt_version_did_not():
    """DEC-022. Scope has not moved since DEC-030; the reply contract has now moved
    three times, and this is the fourth version of it."""
    assert v3.COVERAGE_PROMPT_VERSION_V3 == "coverage_v3"
    assert v3.RESPONSE_PARSER_VERSION == "strict_coverage_spanids_v4"


def test_both_versions_are_stamped_on_every_verdict(tmp_path, monkeypatch):
    _manifest, out_dir = _run_v3(tmp_path, monkeypatch, reply=_reply())
    stamped = _rows(out_dir / "judgment_band_items.jsonl")[0][
        "coverage_verdicts"][0]
    assert stamped["prompt_version"] == "coverage_v3"
    assert stamped["response_parser_version"] == v3.RESPONSE_PARSER_VERSION


def test_the_reply_shape_contract_is_still_strict():
    """de3e040's item 1 is not relaxed: one bare JSON object per reply, and
    concatenated objects still quarantine with the object count named."""
    with pytest.raises(ValueError, match="2 top-level JSON objects"):
        v3.parse_coverage_v3(_reply() + "\n" + _reply(rationale="second"))


def test_the_prompt_still_never_asks_the_model_about_completeness():
    """DEC-032 is a CODE decision, re-asserted because this spec rewrote the
    output-contract half of the prompt again."""
    lowered = v3.COVERAGE_PROMPT_V3.lower()
    for forbidden in ("retrieval_complete", "complete retrieval",
                      "incomplete", "completeness"):
        assert forbidden not in lowered
    assert 'No "established" field' in v3.COVERAGE_PROMPT_V3
