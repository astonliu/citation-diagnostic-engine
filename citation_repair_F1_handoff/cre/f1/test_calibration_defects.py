"""The six calibration defects measured in runs 1 and 2 (ZD 2026-08-11).

Fully offline. Every model call, fetch, HTTP session and reader result is an
injected stub; the three LIVE resolver rows live in
``test_calibration_defects_live.py`` behind an opt-in env var, so this file never
touches the network.

Every fixture here is a CODE-PATH fixture: it exists to drive a branch. None of
it is evaluation data, a gold label, or an input to any reported number.

ONE FILE PER SPEC, one test per acceptance-matrix row. The three defects that
silently corrupt a number -- a claim-less reference reported as fully covered
(item 1), a resolver outage reported as an absence of full text (item 2), and an
``evidence_span`` that is not the evidence the judge reasoned from (item 4) --
were each written here as a STRICT xfail and observed failing before the fix
landed. That is the discipline that caught three defects on 2026-08-06.

WHAT THESE TESTS CAN AND CANNOT PROVE. Items 4-7 change PROMPT TEXT. A prompt
rule is testable here only as a prompt-contract assertion -- the required
sentence is present, and the worked example says what the ruling says. Whether
the judge OBEYS it is a model-behaviour question that only a live calibration run
can answer, and no assertion in this file should be read as evidence that it
does.
"""
from __future__ import annotations

import json
import math

import pytest

from cre.f1 import band_prompts as bp
from cre.f1 import coverage_aggregate as ca
from cre.f1 import coverage_prompts_v3 as v3
from cre.f1 import fulltext_reader as fr
from cre.f1 import judgment_band as jb
from cre.f1 import ncbi_meta as nm


# ==========================================================================
# shared fixtures
# ==========================================================================
ONE_REF_XML = """\
<article><body><p>A finding <xref ref-type="bibr" rid="R1">1</xref>.</p></body>
<back><ref-list><ref id="R1"><element-citation>
<article-title>Paper</article-title><pub-id pub-id-type="pmid">1</pub-id>
</element-citation></ref></ref-list></back></article>
"""


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


def _run_band(tmp_path, monkeypatch, *, extractor=None, **extra):
    _patch_not_review(monkeypatch)
    out_dir = tmp_path / "out"
    manifest = jb.run_band(
        _xml_dir(tmp_path), str(out_dir),
        extractor=extractor or (lambda sentence: ["A finding"]),
        coverage_judge=lambda claims, evidence: [
            {"established": True} for _ in claims],
        fetch_abstract=lambda pmid: "An abstract.",
        session=object(), **extra)
    return manifest, out_dir


# ==========================================================================
# ITEM 1 -- a claim-less reference must not be reported as fully covered
# ==========================================================================
def test_route_of_no_claims_is_its_own_terminal_route():
    """Acceptance row 1. ``all()`` over an empty list is True, so the vacuous
    case fell out of the FULL_COVERAGE branch -- the stage that feeds the F3
    discriminator. A false clear no downstream counter can distinguish from a
    real one."""
    assert jb.route([]) == jb.ROUTE_NO_CLAIMS
    assert jb.route([]) != jb.ROUTE_FULL_COVERAGE


def test_no_claims_route_carries_no_proposed_verdict():
    """Acceptance row 6. Nothing was judged, so the system has no guess."""
    assert jb._proposed_verdict(jb.ROUTE_NO_CLAIMS) is None


@pytest.mark.parametrize("verdicts,expected", [
    ([{"established": True}], jb.ROUTE_FULL_COVERAGE),
    ([{"established": False}, {"established": None}], jb.ROUTE_F6_FLAGGED),
    ([{"established": True}, {"established": None}], jb.ROUTE_HELD),
])
def test_non_empty_routing_is_unchanged(verdicts, expected):
    """Acceptance rows 3-5. The tri-state discipline for NON-EMPTY lists does not
    move: only the vacuous case was wrong."""
    assert jb.route(verdicts) == expected


def test_no_claims_gets_its_own_manifest_counter(tmp_path, monkeypatch):
    """Acceptance row 2. A counter is what makes the case reportable; without one
    a NO_CLAIMS item is as invisible as the FULL_COVERAGE it used to hide in."""
    manifest, out_dir = _run_band(tmp_path, monkeypatch, extractor=lambda s: [])
    assert manifest["counts"][jb.ROUTE_NO_CLAIMS] == 1
    assert manifest["counts"][jb.ROUTE_FULL_COVERAGE] == 0
    assert _rows(out_dir / "judgment_band_items.jsonl")[0][
        "proposed_route"] == jb.ROUTE_NO_CLAIMS


def test_the_no_claims_counter_is_absent_when_it_never_fires(
        tmp_path, monkeypatch):
    """The counter is added ONLY when a claim-less reference actually occurs.

    An unconditional counter -- even zero-valued -- changes the manifest bytes of
    every default run and breaks the opt-in guarantee that
    ``test_default_path_manifest_counter_set_is_unchanged`` pins. Absent means
    absent, exactly as item 3 treats an unsupplied model name."""
    manifest, _out = _run_band(tmp_path, monkeypatch)
    assert jb.ROUTE_NO_CLAIMS not in manifest["counts"]


# ==========================================================================
# ITEM 2 -- a resolver outage is not an absence of full text
# ==========================================================================
class _Resp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = {}
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


class _CountingSession:
    """Serves one canned response and records every request."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self._response


def _idconv_payload(*pairs):
    return {"status": "ok",
            "records": [{"requested-id": pmid, "pmid": int(pmid),
                         **({"pmcid": pmcid} if pmcid else
                            {"status": "error",
                             "errmsg": "Identifier not found in PMC"})}
                        for pmid, pmcid in pairs]}


def test_a_raising_resolver_is_a_resolver_error_not_a_missing_pmcid():
    """Acceptance row 7, and the whole point of item 2(a).

    Measured 2026-08-11: ELink answered HTTP 200 with a body carrying a raw
    newline inside its ``ERROR`` value, ``r.json()`` raised, and all 25 distinct
    cited PMIDs in run 1 came back ``no_pmcid``. That read as an OA-subset
    ceiling. An outage must never again be readable as an absence of full text."""
    def boom(pmid):
        raise nm.ResolverError("ELink returned a malformed body")
    out = fr.fetch_fulltext("1", resolve_pmcid=boom)
    assert out["incomplete_reasons"] == [fr.REASON_RESOLVER_ERROR]
    assert out["retrieval_complete"] is False


def test_a_transport_failure_is_a_resolver_error_not_a_missing_pmcid():
    """A connection that never completed answered nothing at all. Reporting that
    as ``no_pmcid`` asserts a fact about the article from a fact about the wire."""
    import requests

    def boom(pmid):
        raise requests.ConnectionError("network unreachable")
    out = fr.fetch_fulltext("1", resolve_pmcid=boom)
    assert out["incomplete_reasons"] == [fr.REASON_RESOLVER_ERROR]


@pytest.mark.parametrize("response", [
    _Resp(status_code=500, text="upstream is down"),
    _Resp(status_code=200, text="ERROR: NCBI C++ Exception:\n eUnknown"),
    _Resp(status_code=200, payload={"status": "error", "records": None}),
])
def test_non_200_unparseable_and_bad_status_all_raise_through_to_resolver_error(
        response):
    """Acceptance row 7, through the LIVE seam. Non-200, a body that is not JSON,
    and a payload whose top-level ``status`` is neither absent nor ``"ok"`` are
    each a resolver that did not answer."""
    session = _CountingSession(response)
    out = fr.fetch_fulltext("1", session=session)
    assert out["incomplete_reasons"] == [fr.REASON_RESOLVER_ERROR]


def test_a_resolver_that_answers_no_pmc_still_reports_no_pmcid():
    """Acceptance row 8, and the boundary that makes row 7 meaningful.

    ``no_pmcid`` must keep meaning exactly one thing: the resolver answered, and
    this article has no PMC full text. The converter says so with a PER-RECORD
    ``status: "error"`` / ``errmsg: "Identifier not found in PMC"``, which is a
    real answer -- not to be confused with the TOP-LEVEL status that row 7 tests.
    Confusing the two would turn every non-OA article into a resolver_error and
    nothing would ever route."""
    session = _CountingSession(_Resp(payload=_idconv_payload(("111", None))))
    out = fr.fetch_fulltext("111", session=session)
    assert out["incomplete_reasons"] == [fr.REASON_NO_PMCID]


def test_a_resolver_error_is_never_cached(tmp_path):
    """Acceptance row 9. Same policy as ``no_pmcid`` and ``body_unparseable``: a
    transient failure that got written to the cache would be served back as a
    settled fact on every later run."""
    cache = tmp_path / "cache"
    cache.mkdir()

    def boom(pmid):
        raise nm.ResolverError("down")
    fr.fetch_fulltext("1", resolve_pmcid=boom, cache_dir=str(cache))
    assert list(cache.iterdir()) == []


def test_resolver_error_is_in_the_documented_reason_vocabulary():
    """A reason nothing documents is a reason nobody reads."""
    assert fr.REASON_RESOLVER_ERROR in fr.INCOMPLETE_REASONS
    assert "resolver_error" in fr.__doc__


def test_the_converter_batches_at_200_and_costs_one_request_per_batch():
    """Acceptance row 13. 201 PMIDs is TWO requests, not 201. Cost is estimated
    on request count, because that is what NCBI meters."""
    pmids = [str(100000 + i) for i in range(201)]
    session = _CountingSession(_Resp(payload=_idconv_payload(
        *((p, "PMC" + p) for p in pmids))))
    nm.ncbi_pmids_to_pmcids(pmids, session=session)
    assert len(session.calls) == math.ceil(201 / nm.IDCONV_BATCH_MAX) == 2
    assert nm.idconv_request_count(len(pmids)) == 2
    first_batch = session.calls[0][1]["ids"].split(",")
    assert len(first_batch) == nm.IDCONV_BATCH_MAX == 200


def test_the_converter_returns_the_articles_own_pmcid():
    """The ``pubmed_pmc_refs`` hazard that ELink's self-link rule existed to guard
    -- taking the "first link" from the CITING-articles group yields an unrelated
    paper -- does not exist here: the converter answers per requested id."""
    session = _CountingSession(_Resp(payload=_idconv_payload(
        ("30140736", "PMC6105232"), ("111", None))))
    got = nm.ncbi_pmids_to_pmcids(["30140736", "111"], session=session)
    assert got == {"30140736": "PMC6105232", "111": ""}


def test_the_single_pmid_helper_keeps_its_swallowing_contract():
    """``ncbi_pmid_to_pmcid`` stays in place for its existing caller
    (``f3_candidate_collect``), which documents "returns "" on any failure" and
    has no exception handling of its own. It routes through the converter but
    still swallows -- only ``fetch_fulltext``'s seam propagates, because only
    ``fetch_fulltext`` has a reason vocabulary to record the difference in."""
    session = _CountingSession(_Resp(status_code=500))
    assert nm.ncbi_pmid_to_pmcid("30140736", session=session) == ""


# ==========================================================================
# ITEM 3 -- the manifest records the model identity
# ==========================================================================
def test_model_and_prefill_are_recorded_verbatim(tmp_path, monkeypatch):
    """Acceptance row 14. Every coverage number is conditional on the adapter,
    same class as DEC-020's omitted temperature / top_p."""
    manifest, _out = _run_band(
        tmp_path, monkeypatch, model="claude-sonnet-4-5",
        assistant_prefill="{", stop_sequences=("</done>",))
    params = manifest["params"]
    assert params["model"] == "claude-sonnet-4-5"
    assert params["assistant_prefill"] == "{"
    assert params["stop_sequences"] == ["</done>"]


def test_an_unsupplied_model_is_absent_never_guessed(tmp_path, monkeypatch):
    """Acceptance row 15. The notebook passes them; the band does not infer them.
    A defaulted model name would be a fabricated provenance record, and it would
    also change the manifest bytes of every default run."""
    manifest, _out = _run_band(tmp_path, monkeypatch)
    for key in ("model", "assistant_prefill", "stop_sequences"):
        assert key not in manifest["params"]


# ==========================================================================
# ITEM 4 -- evidence_span is the evidence the verdict rests on
# ==========================================================================
def _flat(text: str) -> str:
    """Whitespace-collapsed prompt text.

    The prompt's LINE WRAPPING is not part of any contract -- the sentences are --
    so every prompt-text assertion below reads the flattened form. Asserting on
    wrapped literals made these tests fail on a reflow that changed nothing the
    model reads."""
    return " ".join(text.split())


PROMPT = _flat(v3.COVERAGE_PROMPT_V3)
def test_the_prompt_requires_the_span_to_be_the_complete_basis():
    """Acceptance row 16, as a prompt-contract assertion.

    Measured in run 2: four of twelve graded rows quoted, in their rationale,
    text absent from their own reported span. That is not a cosmetic defect. The
    offline span audit checks the span appears verbatim in the section it names,
    so a span that is only a SAMPLE the judge chose -- rather than the basis it
    reasoned from -- makes the audit STRUCTURALLY unable to detect a false
    ``established``, which is the one failure mode it exists to catch. In run 2
    it passed both of CR4's rows as verbatim while both verdicts are wrong."""
    assert "IS THE COMPLETE EVIDENCE for what you report" in PROMPT
    assert "it must be sufficient on its own" in PROMPT
    # A finding whose justification needs text outside the span is not established.
    assert ("A finding whose justification needs text outside the span is not "
            "established") in PROMPT
    # It is the basis reasoned from, not the most quotable line.
    assert "It is not a sample, an illustration, or the most quotable line" in PROMPT


def test_the_rationale_field_is_told_it_may_only_use_the_span():
    """The rule has to be stated where the model reads it -- in the field's own
    description, not only in a rule the model may skim. CR4's rationale quoted two
    passages it never put in its span; had the field said what it may rely on, the
    reply would have been self-contradictory rather than merely wrong."""
    assert "It may rely ONLY on text you put in evidence_span_text." in PROMPT
    # ...and the escape hatch is named, so "shorten the quote" is not the way out.
    assert "the honest answer is an unconfirmed specific, not a shorter quote" in PROMPT


def test_multiple_load_bearing_passages_are_all_carried():
    """Item 4's second half: the field carries ALL load-bearing passages, and the
    audit still works because every elided segment is checked verbatim and in
    order. The worked example is CR4's own two passages."""
    assert "the field carries ALL of them" in PROMPT
    assert v3.ELISION_MARKER.strip() in PROMPT
    body = ("N-containing molecules are shielded by recalcitrant substrates such "
            "as lignin. Some intervening sentence. This slows SOM decomposition.")
    sections = [{"label": "discussion", "text": body}]
    two = ("N-containing molecules are shielded" + v3.ELISION_MARKER
           + "This slows SOM decomposition")
    assert v3.span_is_verbatim("discussion", two, sections) is True
    # Order matters: the same two segments reversed are not a quote of this text.
    reversed_pair = ("This slows SOM decomposition" + v3.ELISION_MARKER
                     + "N-containing molecules are shielded")
    assert v3.span_is_verbatim("discussion", reversed_pair, sections) is False


# ==========================================================================
# ITEM 5 -- engages_subject tests the claim's own subject
# ==========================================================================
def test_engages_subject_is_defined_on_the_claims_own_subject():
    """Acceptance row 17, as a prompt-contract assertion.

    Measured in run 2, SAME reference and SAME run: the genus Trichocladium is
    absent from the cited paper -> engages_subject false, no span. The genus Mycena
    is EQUALLY absent from the same paper -> engages_subject true, with a span about
    primary colonisers in general, and a second row whose span is a table row for
    Armillaria cepistipes, a different species entirely. Identical facts, opposite
    answers."""
    assert "a test on the CLAIM'S OWN SUBJECT, never on its topic area" in PROMPT
    assert ("a paper discussing the surrounding topic does not engage a subject "
            "it never mentions") in PROMPT
    # The test is on the entity the claim NAMES, not on the finding alone.
    assert ("the specific entity, population, taxon or intervention THE CLAIM "
            "NAMES") in PROMPT


def test_the_absent_genus_pair_is_the_worked_example_and_answers_alike():
    """The two run-2 cases, as the worked pair the spec asks for: absent genus ->
    false, BOTH times. They share one evidence block in the prompt so the "same
    facts, same answer" point cannot be missed."""
    text = v3.COVERAGE_PROMPT_V3
    assert "Trichocladium" in PROMPT and "Mycena" in PROMPT
    assert "the answer is the same both times" in PROMPT
    # Both worked replies are engages_subject false with no span.
    for genus in ("Trichocladium species colonise decaying wood.",
                  "Mycena species are primary colonisers of wood."):
        block = text.split(f'Claim: "{genus}"')[1].split("\n\n")[0]
        assert '"engages_subject": false' in block
        assert '"evidence_span_label": ""' in block
        assert '"evidence_span_text": ""' in block


# ==========================================================================
# ITEM 6 -- label and text are two fields, and the label is validated
# ==========================================================================
def _reply_v3(*, engages=True, contradicts=False, specifics=(), rationale="r",
              label="results", text="Drug X reduced infarct size."):
    return json.dumps({
        "engages_subject": engages, "contradicts": contradicts,
        "unconfirmed_specifics": list(specifics), "rationale": rationale,
        "evidence_span_label": label, "evidence_span_text": text,
    }, ensure_ascii=False)


def _reader_result(sections=None):
    sections = sections or [
        {"label": "results", "title": "Results",
         "text": "Drug X reduced infarct size.", "content_sha256": "unused"}]
    return {"pmid": "1", "pmcid": "PMC9", "resolved": True,
            "sections": sections,
            "sections_present": sorted({s["label"] for s in sections}),
            "retrieval_complete": True, "incomplete_reasons": [],
            "sanitized_paths": [], "source": "live"}


def test_the_span_is_two_fields_and_the_old_packed_field_is_gone():
    """Acceptance row 18, first half. A single ``"label: text"`` string could
    represent a label its own text contradicted; two fields cannot."""
    judge = v3.make_coverage_judge_v3(lambda prompt: _reply_v3())
    out = judge(["claim"], {"cited_fulltext": _reader_result()})[0]
    assert out["evidence_span_label"] == "results"
    assert out["evidence_span_text"] == "Drug X reduced infarct size."
    assert "evidence_span" not in out          # the ambiguous field is retired


def test_a_label_outside_the_supplied_set_is_rejected():
    """Acceptance row 18, second half: label is in the reader's emitted labels.

    Measured in run 2, both misses on CR42: ``intro: Fungal species name | Code |
    Phyllum | Decay type | ...`` -- pipe-delimited TABLE content attributed to
    ``intro``. The reader emits ``table`` as its own label, so the correct label
    was available and unused. A label that names no supplied section cannot be
    audited at all, so it fails closed rather than passing through."""
    judge = v3.make_coverage_judge_v3(lambda prompt: _reply_v3(label="intro"))
    with pytest.raises(ValueError, match="not one of the labels supplied"):
        judge(["claim"], {"cited_fulltext": _reader_result()})


def test_a_table_label_is_accepted_when_the_reader_emitted_one():
    """The other side of the same boundary: ``table`` is a real reader label, and
    quoting a table under it -- pipes and all -- is correct, not malformed."""
    row = "Fungal species name | Code | Phyllum | Decay type"
    sections = [{"label": "table", "title": "Table 1", "text": row,
                 "content_sha256": "unused"}]
    judge = v3.make_coverage_judge_v3(
        lambda prompt: _reply_v3(label="table", text=row))
    out = judge(["claim"], {"cited_fulltext": _reader_result(sections)})[0]
    assert out["evidence_span_label"] == "table"
    assert v3.span_is_verbatim("table", row, sections) is True


def test_an_unpaired_span_half_is_malformed():
    """Splitting the field creates one new failure mode, and it fails closed: a
    label with no text, or text with no label, is exactly the half-state the packed
    string used to be able to hide."""
    for kwargs in ({"label": "results", "text": ""}, {"label": "", "text": "x"}):
        with pytest.raises(ValueError, match="must both be present"):
            v3.parse_coverage_v3(_reply_v3(**kwargs))


def test_the_parser_version_moved_and_is_stamped_on_every_verdict(
        tmp_path, monkeypatch):
    """DEC-022: adding a field is a ``response_parser_version`` bump, and the stamp
    has to be on the ROWS, not only in the module -- at fe769a0 the constant
    existed and was written nowhere. Both the model-judged and the deterministic
    HELD rows carry it: it names the reply contract the row was produced under, so
    a file missing it on some rows could not be read at all."""
    assert v3.RESPONSE_PARSER_VERSION == "strict_coverage_6key_v2"
    _patch_not_review(monkeypatch)
    out_dir = tmp_path / "out"
    for complete in (True, False):
        reader = _reader_result()
        reader["retrieval_complete"] = complete
        reader["incomplete_reasons"] = [] if complete else ["body_too_small"]
        target = out_dir / ("complete" if complete else "held")
        jb.run_band(
            _xml_dir(tmp_path / ("c" if complete else "h")), str(target),
            extractor=lambda sentence: ["A finding"],
            coverage_judge=lambda claims, evidence: [
                {"established": True} for _ in claims],
            fetch_abstract=lambda pmid: "An abstract.",
            fetch_fulltext=lambda pmid: reader,
            coverage_judge_v3=v3.make_coverage_judge_v3(
                lambda prompt: _reply_v3()),
            session=object())
        row = _rows(target / "judgment_band_items.jsonl")[0]
        stamped = row["coverage_verdicts"][0]
        assert stamped["response_parser_version"] == "strict_coverage_6key_v2"
        assert stamped["prompt_version"] == "coverage_v3"      # unchanged in name
        assert "evidence_span" not in stamped


def test_the_default_path_verdict_record_keeps_the_frozen_five_key_shape(
        tmp_path, monkeypatch):
    """The span split belongs to the v3 path ONLY. A default run's verdict records
    keep ``evidence_span`` and gain no parser-version key, or the opt-in guarantee
    is broken at the row level rather than the manifest level."""
    _manifest, out_dir = _run_band(tmp_path, monkeypatch)
    row = _rows(out_dir / "judgment_band_items.jsonl")[0]
    stamped = row["coverage_verdicts"][0]
    assert "evidence_span" in stamped
    assert "evidence_span_label" not in stamped
    assert "response_parser_version" not in stamped


# ==========================================================================
# ITEM 7 -- the specificity boundary, and the existential rule
# ==========================================================================
def test_the_specificity_boundary_is_stated_as_ZD_ruled_it():
    """Acceptance row 20. ZD's standing default (a): KEEP the boundary as written.
    The initial "wider words count" ruling collided with
    TAXONOMY_DECISION_RULES.md's "ACCURATE vs. F6 -- the specificity boundary" and
    with TAXONOMY_AMENDMENT_2026-08-07_FINAL.md SecA, which explicitly preserved
    "'ApoE-deficient mice' vs 'mice' is still F6". Prompt text only; TAXONOMY.md
    and TAXONOMY_DECISION_RULES.md are NOT edited."""
    assert ("A BROADER TERM IN THE SOURCE DOES NOT ESTABLISH A NARROWER TERM IN "
            "THE CLAIM.") in PROMPT
    # Run 2's CR4 rows, as the worked negatives.
    assert ('Source "recalcitrant substrates" does not establish claim "lignin"'
            in PROMPT)
    assert ('"SOM decomposition" does not establish claim "litter decomposition"'
            in PROMPT)
    # The 2026-08-07 amendment's own example, preserved.
    assert '"mice" does not establish claim "ApoE-deficient mice"' in PROMPT
    # CR4's judge CONCEDED the substitution in its rationale and marked it
    # established anyway. Conceding it is not licensing it.
    assert "Naming the substitution in your rationale does not license it" in PROMPT


def test_an_existential_about_a_class_never_transfers_to_a_member():
    """Acceptance row 21, and item 5's protection from the other side: here the
    subject IS engaged, so engages_subject cannot catch it."""
    text = v3.COVERAGE_PROMPT_V3
    assert ("AN EXISTENTIAL STATEMENT ABOUT A CLASS NEVER TRANSFERS TO A MEMBER."
            in PROMPT)
    assert ('"Lignin degradation is caused by certain fungi" does not establish'
            in PROMPT)
    # The worked example keeps engages_subject TRUE -- Mycena is named -- so the
    # existential is caught as an unconfirmed specific, not as non-engagement.
    block = text.split('Claim: "Mycena degrades lignocellulose."')[1].split("\n\n")[0]
    assert '"engages_subject": true' in block
    assert '"unconfirmed_specifics": ["Mycena degrades lignocellulose"]' in block


# ==========================================================================
# The prompt keeps the invariants it already had
# ==========================================================================
def test_the_prompt_still_never_asks_the_model_about_completeness():
    """DEC-032 is a CODE decision, and six new rules must not have leaked it into
    the prompt. Re-asserted here because this spec rewrote the prompt wholesale."""
    lowered = v3.COVERAGE_PROMPT_V3.lower()
    for forbidden in ("retrieval_complete", "complete retrieval",
                      "incomplete", "completeness"):
        assert forbidden not in lowered
    assert 'No "established" field' in v3.COVERAGE_PROMPT_V3


def test_the_prompt_version_name_did_not_move():
    """The evidence SCOPE did not change, so the prompt version must not: these
    fixes change what the prompt says about judging full text, not that it judges
    full text."""
    assert v3.COVERAGE_PROMPT_VERSION_V3 == "coverage_v3"
