"""The two coverage_v3 OUTPUT-CONTRACT defects from calibration run 3 (ZD 2026-08-11).

Fully offline. Every model call, fetch and reader result is an injected stub; the
live ``CR42`` row lives in ``test_output_contract_v3_live.py`` behind an opt-in env
var. Every fixture is a CODE-PATH fixture: none of it is evaluation data, a gold
label, or an input to any reported number.

Run 3 (``calib_v3c_PMC10115774``) confirmed 3e5261d's six fixes work and surfaced
two output-contract defects. Both were written here as STRICT xfails and observed
failing at 3e5261d before either fix landed.

ITEM 1 -- the judge emitted a reply containing TWO concatenated JSON objects, and
the strict parser quarantined the whole reference. Quarantine is the SAFETY
PROPERTY and is not being relaxed; what was missing is that the error said nothing
about how many objects arrived, so the failure could not be told from any other
"not one bare JSON object".

ITEM 2 -- the span was one string, so a verdict resting on two non-contiguous
passages could only be recorded by stitching them with ``[...]``. A stitched span
is not verbatim contiguous section text, so the offline audit mismatched on FORMAT
and stopped checking anything. The span audit is the only automated check on a
false ``established``. The drafting failure was the spec's, not the judge's.

WHAT THESE TESTS CANNOT PROVE. Prompt rules are asserted here as prompt-contract
assertions -- the required sentence is present, the worked example says what the
ruling says. Whether the judge OBEYS them is a model-behaviour question that only a
live run can answer, and item 1 in particular is a change in what the model is
ASKED for, whose effect is measurable only live.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import coverage_prompts_v3 as v3
from cre.f1 import judgment_band as jb


# ==========================================================================
# fixtures
# ==========================================================================
ONE_REF_XML = """\
<article><body><p>A finding <xref ref-type="bibr" rid="R1">1</xref>.</p></body>
<back><ref-list><ref id="R1"><element-citation>
<article-title>Paper</article-title><pub-id pub-id-type="pmid">1</pub-id>
</element-citation></ref></ref-list></back></article>
"""

#: The two non-contiguous passages of run 3's CR4 span, and the text between them.
#: Verbatim from the cited paper's discussion, as the reader rendered it.
CR4_LEAD = ("N-containing molecules are often physically and chemically shielded "
            "by recalcitrant substrates such as lignin (25, 26).")
CR4_MIDDLE = " Some intervening sentence the judge did not rely on. "
CR4_TAIL = ("Because these recalcitrant substrates protect the degradation of "
            "more labile material (30) and the degradation of these substrates "
            "constitute the rate-limiting step in soil organic matter (SOM) "
            "decomposition (34, 35), N addition may slow decomposition.")
CR4_DISCUSSION = CR4_LEAD + CR4_MIDDLE + CR4_TAIL


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
    """A coverage_v3 reply under the SPAN-LIST contract."""
    if spans is None:
        spans = ([{"label": "results", "text": "Drug X reduced infarct size."}]
                 if engages else [])
    return json.dumps({
        "engages_subject": engages, "contradicts": contradicts,
        "unconfirmed_specifics": list(specifics), "rationale": rationale,
        "evidence_spans": spans,
    }, ensure_ascii=False)


def _run_v3(tmp_path, monkeypatch, *, reply, sections=None):
    """run_band on the opt-in full-text path with a canned v3 reply."""
    _patch_not_review(monkeypatch)
    out_dir = tmp_path / "out"
    manifest = jb.run_band(
        _xml_dir(tmp_path), str(out_dir),
        extractor=lambda sentence: ["A finding"],
        coverage_judge=lambda claims, evidence: [
            {"established": True} for _ in claims],
        fetch_abstract=lambda pmid: "An abstract.",
        fetch_fulltext=lambda pmid: _reader_result(sections),
        coverage_judge_v3=v3.make_coverage_judge_v3(lambda prompt: reply),
        session=object())
    return manifest, out_dir


# ==========================================================================
# ITEM 1 -- one bare JSON object per reply, and a diagnosable failure
# ==========================================================================
TWO_OBJECTS = _reply() + "\n" + _reply(rationale="second object")


def test_two_concatenated_objects_still_quarantine():
    """The SAFETY PROPERTY, re-pinned. ``_loads_strict_v3`` is NOT being relaxed to
    accept concatenated objects: the strict single-object contract is the only
    reason run 3 noticed this at all. A parser that silently took the first object
    would have scored CR42 on a reply it did not fully read."""
    with pytest.raises(ValueError, match="not one bare JSON object"):
        v3.parse_coverage_v3(TWO_OBJECTS)


def test_the_extra_data_failure_reports_how_many_objects_arrived():
    """Acceptance row 1, second half. ``Extra data`` is valid JSON followed by more
    content -- measured in run 3 as ``Extra data: line 10 column 1 (char 815)``,
    which is a POSITION and says nothing about what was there. Truncation raises
    ``Unterminated string`` instead, so max_tokens was never implicated; the object
    count is what distinguishes the two, and it was absent."""
    with pytest.raises(ValueError) as caught:
        v3.parse_coverage_v3(TWO_OBJECTS)
    message = str(caught.value)
    assert "2 top-level JSON objects" in message
    # The underlying decoder message survives -- the count is added, not swapped in.
    assert "Extra data" in message


def test_the_object_count_is_only_added_for_extra_data():
    """A different JSON failure must not grow a misleading count. Truncation is the
    case that matters: it is the one this diagnosis has to stay distinguishable
    from."""
    truncated = '{"engages_subject": true, "rationale": "unclosed'
    with pytest.raises(ValueError) as caught:
        v3.parse_coverage_v3(truncated)
    message = str(caught.value)
    assert "top-level JSON objects" not in message
    assert "Unterminated string" in message


def test_a_two_object_reply_quarantines_the_reference_not_the_batch(
        tmp_path, monkeypatch):
    """Acceptance row 1, first half. The reference lands in PARSE_QUARANTINE with
    the count on its durable record, and the batch survives."""
    manifest, out_dir = _run_v3(tmp_path, monkeypatch, reply=TWO_OBJECTS)
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    row = _rows(out_dir / "judgment_band_items.jsonl")[0]
    assert row["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert "2 top-level JSON objects" in row["parse_error"]


def test_the_prompt_demands_exactly_one_object_for_the_one_claim_supplied():
    """The prompt-side half of item 1.

    NOTE ON MECHANISM. ZD's spec prescribed "one bare JSON object, whose per-claim
    array carries one entry per claim". There is NO per-claim array in this
    contract and there cannot be one: ``make_coverage_judge_v3`` renders ONE claim
    per prompt and makes one model call per claim
    (``test_v3_judge_calls_the_model_once_per_claim_with_labelled_sections`` pins
    that). So the rule is stated in the form the architecture actually has -- one
    object for the one claim supplied -- and the reply-shape line is what carries
    it."""
    flat = " ".join(v3.COVERAGE_PROMPT_V3.split())
    assert "EXACTLY ONE bare JSON object" in flat
    assert "for the ONE claim above" in flat
    assert "Never emit a second object" in flat


def test_the_examples_block_is_fenced_off_as_a_reference_list():
    """Why the model emitted a second object at all, most likely: the EXAMPLES
    block is a run of ``Claim: ... {object}`` pairs, and continuing that pattern
    IS emitting more objects. The block is now explicitly labelled as a reference
    list that is not a template for the reply's shape."""
    flat = " ".join(v3.COVERAGE_PROMPT_V3.split())
    assert "The examples below are a REFERENCE LIST" in flat
    assert "not a template for the shape of your reply" in flat
    assert "END OF EXAMPLES" in v3.COVERAGE_PROMPT_V3


# ==========================================================================
# ITEM 2 -- evidence_spans is a list of {label, text}
# ==========================================================================
def test_evidence_spans_is_a_list_of_label_text_entries():
    """Acceptance row 3."""
    verdict = v3.parse_coverage_v3(_reply())
    assert verdict.evidence_spans == (
        {"label": "results", "text": "Drug X reduced infarct size."},)


def test_the_retired_single_span_fields_are_gone():
    """The pair is REPLACED, not supplemented. Leaving either field alongside the
    list would keep every downstream reader on the shape that could not record two
    passages."""
    assert v3.SPAN_KEYS == frozenset({"label", "text"})
    assert "evidence_spans" in v3.COVERAGE_KEYS_V3
    assert "evidence_span_label" not in v3.COVERAGE_KEYS_V3
    assert "evidence_span_text" not in v3.COVERAGE_KEYS_V3
    verdict = v3.parse_coverage_v3(_reply())
    assert not hasattr(verdict, "evidence_span_label")
    assert not hasattr(verdict, "evidence_span_text")


def test_two_non_contiguous_passages_are_two_entries_and_both_audit_verbatim():
    """Acceptance rows 3-4, on run 3's ACTUAL CR4 span.

    At 3e5261d this verdict could only be recorded as one string with ``[...]``
    between the passages, and the audit then mismatched on format -- reading False
    for a span that is otherwise honest. Two entries, and both match verbatim."""
    sections = _sections(("discussion", CR4_DISCUSSION))
    spans = [{"label": "discussion", "text": CR4_LEAD},
             {"label": "discussion", "text": CR4_TAIL}]
    verdict = v3.parse_coverage_v3(_reply(spans=spans))
    assert len(verdict.evidence_spans) == 2
    assert v3.spans_are_verbatim(verdict.evidence_spans, sections) is True
    for entry in verdict.evidence_spans:
        assert v3.span_is_verbatim(entry["label"], entry["text"], sections) is True


def test_the_stitched_span_run_3_actually_emitted_is_now_rejected():
    """The same evidence stitched with ``[...]`` -- exactly what run 3's CR4
    emitted -- is malformed, so the format mismatch cannot recur silently. A gap
    means two entries."""
    stitched = CR4_LEAD + " [...] " + CR4_TAIL
    with pytest.raises(ValueError, match="ellipsis"):
        v3.parse_coverage_v3(_reply(
            spans=[{"label": "discussion", "text": stitched}]))


@pytest.mark.parametrize("marker", ["[...]", "...", "…"])
def test_every_ellipsis_form_is_forbidden_inside_a_span_text(marker):
    """Acceptance row 4. All three forms, because the judge reached for two of them
    across runs 2 and 3."""
    assert marker in v3.FORBIDDEN_ELLIPSES
    with pytest.raises(ValueError, match="ellipsis"):
        v3.parse_coverage_v3(_reply(
            spans=[{"label": "results", "text": f"a{marker}b"}]))


def test_a_label_outside_the_readers_vocabulary_is_malformed():
    """Acceptance row 5. The reader emits a closed vocabulary, so a label from
    outside it names no section that could ever be audited."""
    assert v3.READER_SECTION_LABELS == frozenset({
        "results", "methods", "table", "figure", "discussion", "intro", "other"})
    with pytest.raises(ValueError, match="not a reader section label"):
        v3.parse_coverage_v3(_reply(
            spans=[{"label": "abstract", "text": "x"}]))


def test_a_label_outside_the_labels_supplied_to_this_call_is_malformed():
    """Stronger than the vocabulary check and kept from 3e5261d: ``table`` is a
    real reader label, but a span cannot cite a section this call never showed the
    model. Run 2's CR42 attributed table content to ``intro``; both checks now
    stand between that and the record."""
    judge = v3.make_coverage_judge_v3(
        lambda prompt: _reply(spans=[{"label": "table", "text": "x"}]))
    with pytest.raises(ValueError, match="not one of the labels supplied"):
        judge(["claim"], {"cited_fulltext": _reader_result()})


def test_engages_subject_false_means_an_empty_list():
    """Acceptance row 6, both directions: off-topic carries no spans, and an
    engaged verdict must carry at least one. Amendment SecD -- the listed spans
    justify the verdict on their own -- has no meaning if the list can be empty."""
    verdict = v3.parse_coverage_v3(_reply(engages=False))
    assert verdict.evidence_spans == ()
    with pytest.raises(ValueError, match="engages_subject=false requires"):
        v3.parse_coverage_v3(_reply(
            engages=False, spans=[{"label": "results", "text": "x"}]))
    with pytest.raises(ValueError, match="at least one evidence span"):
        v3.parse_coverage_v3(_reply(engages=True, spans=[]))


@pytest.mark.parametrize("bad", [
    "not-a-list",
    ["not-a-dict"],
    [{"label": "results"}],                          # missing text
    [{"text": "x"}],                                 # missing label
    [{"label": "results", "text": "x", "extra": 1}],  # extra key
    [{"label": "results", "text": ""}],              # blank text
    [{"label": "", "text": "x"}],                    # blank label
    [{"label": "results", "text": 7}],               # non-string text
])
def test_a_malformed_span_list_fails_closed(bad):
    """One new failure surface per new field, and every one of them raises rather
    than being coerced -- a coerced span is an unauditable span."""
    with pytest.raises(ValueError):
        v3.parse_coverage_v3(_reply(spans=bad))


def test_duplicate_spans_are_rejected():
    """Same discipline ``unconfirmed_specifics`` already has. A repeated passage is
    not two pieces of evidence, and counting it twice would overstate the basis."""
    entry = {"label": "results", "text": "Drug X reduced infarct size."}
    with pytest.raises(ValueError, match="duplicate"):
        v3.parse_coverage_v3(_reply(spans=[entry, dict(entry)]))


def test_the_verdict_record_carries_the_span_list_and_both_versions(
        tmp_path, monkeypatch):
    """Acceptance rows 3 and 7. Both versions stay stamped on every verdict, and
    the parser version is the one that moved (DEC-022)."""
    _manifest, out_dir = _run_v3(tmp_path, monkeypatch, reply=_reply())
    stamped = _rows(out_dir / "judgment_band_items.jsonl")[0][
        "coverage_verdicts"][0]
    assert stamped["evidence_spans"] == [
        {"label": "results", "text": "Drug X reduced infarct size."}]
    assert stamped["prompt_version"] == "coverage_v3"          # scope, unmoved
    assert stamped["response_parser_version"] == v3.RESPONSE_PARSER_VERSION
    assert v3.RESPONSE_PARSER_VERSION == "strict_coverage_spanlist_v3"
    for retired in ("evidence_span", "evidence_span_label", "evidence_span_text"):
        assert retired not in stamped


def test_the_prompt_states_the_span_list_contract():
    """The prompt-side half of item 2, including the forbidden markers by name."""
    flat = " ".join(v3.COVERAGE_PROMPT_V3.split())
    assert "evidence_spans (JSON list of objects" in flat
    assert "One entry per CONTIGUOUS passage" in flat
    assert "a gap means TWO entries, never an ellipsis" in flat
    assert "[...]" in flat and "…" in flat
    # Amendment SecD survives the reshape: the list must stand on its own.
    assert "must justify your findings ON THEIR OWN" in flat


def test_the_span_list_is_still_blind_to_the_annotator():
    """The reshape must not open a leak. rationale and spans are item-record fields
    only; the annotation payload is a whitelist at both levels."""
    judge = v3.make_coverage_judge_v3(
        lambda prompt: _reply(spans=[
            {"label": "results", "text": "Drug X reduced infarct size."}]))
    out = judge(["claim"], {"cited_fulltext": _reader_result()})
    payload = jb.annotation_payload({
        "item_key": "k", "citing_sentence": "S", "cited_pmid": "1",
        "atomic_claims": ["c"],
        "evidence": {"cited_pmid": "1", "cited_fulltext": _reader_result()},
        "coverage_verdicts": [out[0]],
    })
    flat = json.dumps(payload, ensure_ascii=False)
    assert "evidence_spans" not in flat
    assert "coverage_verdicts" not in payload


# ==========================================================================
# Invariants that must survive both reshapes
# ==========================================================================
def test_route_of_no_claims_is_unchanged():
    """Acceptance row 9. 3e5261d's item 1 is not disturbed by an output-contract
    change."""
    assert jb.route([]) == jb.ROUTE_NO_CLAIMS


def test_the_prompt_still_never_asks_the_model_about_completeness():
    """DEC-032 is a CODE decision. Re-asserted because this spec rewrote the
    output-contract half of the prompt."""
    lowered = v3.COVERAGE_PROMPT_V3.lower()
    for forbidden in ("retrieval_complete", "complete retrieval",
                      "incomplete", "completeness"):
        assert forbidden not in lowered
    assert 'No "established" field' in v3.COVERAGE_PROMPT_V3


def test_the_prompt_version_name_did_not_move():
    """The evidence SCOPE did not change, so the prompt version must not."""
    assert v3.COVERAGE_PROMPT_VERSION_V3 == "coverage_v3"
