"""Adversarial code-path probes for the F3--F7 judgment band.

All model and HTTP seams are injected.  The strings in this file are malformed
transport payloads or parser fixtures, never gold labels or evaluation data.
"""
from __future__ import annotations

import json
import time

import pytest

from cre.f1 import band_prompts as bp
from cre.f1 import coverage_aggregate as ca
from cre.f1 import evidence_reader as er
from cre.f1 import judgment_band as jb
from cre.f1.parser import parse_pmc_xml


_XML = """<article><front><article-meta><article-id pub-id-type="pmid">9</article-id>
<title-group><article-title>T</article-title></title-group></article-meta></front>
<body><p>A finding <xref ref-type="bibr" rid="R1">1</xref>.</p></body><back><ref-list>
<ref id="R1"><element-citation><article-title>R</article-title><pub-id pub-id-type="pmid">1</pub-id></element-citation></ref>
</ref-list></back></article>"""


def _write_xml(tmp_path):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC9.xml").write_text(_XML, encoding="utf-8")
    return str(xml_dir)


def _records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _patch_pubtypes(monkeypatch):
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *args, **kwargs: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)


def test_unescaped_abstract_quote_in_model_evidence_span_is_quarantined(
        tmp_path, monkeypatch):
    """Reproduces the live failure: raw abstract text can induce an unescaped
    quote in a model reply.  JSON grammar must reject it and run_band must keep
    the bad row out of the blind queue rather than aborting the document."""
    _patch_pubtypes(monkeypatch)
    xml_dir = _write_xml(tmp_path)
    out = tmp_path / "out"
    prompts = []

    def call_llm(prompt):
        prompts.append(prompt)
        if "CITED-PAPER ABSTRACT" not in prompt:
            return '{"claims":["A finding"]}'
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok",'
                '"evidence_span":"The abstract calls it "effective"."}')

    man = jb.run_band(
        xml_dir, str(out), extractor=bp.make_extractor(call_llm),
        coverage_judge=ca.make_coverage_judge(call_llm),
        fetch_abstract=lambda pmid: 'The abstract calls it "effective".',
        session=object())
    assert any('"effective"' in prompt for prompt in prompts)
    assert man["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert man["counts"]["items_built"] == 0
    item = _records(out / "judgment_band_items.jsonl")[0]
    assert item["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE
    assert "Expecting ',' delimiter" in item["parse_error"]
    assert _records(out / "judgment_band_annotation_queue.jsonl") == []


@pytest.mark.parametrize("raw", [
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"x","evidence_span":"a\\q"}',
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"x","evidence_span":"x",}',
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"bad "quote","evidence_span":"x"}',
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"unterminated',
    '```json\n{}\n```', 'prefix {}', '{} suffix', '{}{}', '', ' \t\n ', '\ufeff{}', b'{}',
])
def test_strict_loader_rejects_non_bare_or_malformed_transport(raw):
    with pytest.raises(ValueError):
        bp._loads_strict(raw, frozenset())


def test_strict_loader_accepts_escaped_nested_braces_inside_string():
    raw = json.dumps({"engages_subject": True, "contradicts": False,
                      "unconfirmed_specifics": [], "rationale": "uses {nested}",
                      "evidence_span": 'quote: " and slash: \\'})
    verdict = bp.parse_coverage(raw)
    assert verdict.rationale == "uses {nested}"
    assert verdict.evidence_span == 'quote: " and slash: \\'


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("rationale"), lambda d: d.update(extra=True),
    lambda d: d.update(engages_subject="true"), lambda d: d.update(engages_subject=0),
    lambda d: d.update(engages_subject=1), lambda d: d.update(contradicts=0),
    lambda d: d.update(unconfirmed_specifics=[""]),
    lambda d: d.update(unconfirmed_specifics=["x", 1]),
    lambda d: d.update(unconfirmed_specifics=["x", "x"]),
    lambda d: d.update(rationale=[]),
])
def test_coverage_schema_rejects_bad_types_and_values(mutate):
    obj = {"engages_subject": True, "contradicts": False,
           "unconfirmed_specifics": [], "rationale": "ok", "evidence_span": "x"}
    mutate(obj)
    with pytest.raises(ValueError):
        bp.parse_coverage(json.dumps(obj))


def test_coverage_rationale_null_is_non_load_bearing_but_key_is_required():
    raw = {"engages_subject": True, "contradicts": False,
           "unconfirmed_specifics": [], "rationale": None, "evidence_span": "x"}
    assert bp.parse_coverage(json.dumps(raw)).rationale == ""


@pytest.mark.parametrize("engages,contradicts,specifics,expected", [
    (True, False, [], True), (True, True, [], False),
    (True, False, ["dose"], None), (False, False, [], None),
])
def test_tristate_table_uses_identity_not_truthiness(engages, contradicts, specifics, expected):
    assert ca.aggregate_coverage(engages, contradicts, specifics) is expected
    assert jb.route([{"established": expected}]) == (
        jb.ROUTE_F6_FLAGGED if expected is False else
        jb.ROUTE_FULL_COVERAGE if expected is True else jb.ROUTE_HELD)


@pytest.mark.parametrize("contradicts,specifics", [(True, []), (False, ["x"])])
def test_off_topic_contradiction_or_specifics_is_rejected(contradicts, specifics):
    with pytest.raises(ValueError, match="engages_subject=false"):
        ca.aggregate_coverage(False, contradicts, specifics)


@pytest.mark.parametrize("abstract", ["N/A", "n/a", "None", "not available",
                                      "(no abstract available)", " \u00a0\t "])
def test_abstract_sentinels_and_whitespace_never_reach_model(abstract):
    calls = []
    judge = ca.make_coverage_judge(lambda prompt: calls.append(prompt))
    result = judge(["claim"], {"cited_abstract": abstract})
    assert calls == []
    assert result[0]["established"] is None


def test_unicode_and_extreme_citance_survive_extraction_guard():
    text = "\u201cCaf\u00e9\u0301\u201d\u2014\u0646\u0635\u200d\u05e9\u05dc\u05d5\u05dd\u00a0" + "x" * 10_000 + " [1]"
    assert jb.extract_atomic_claims(text, extractor=lambda _: [text, "", 7]) == [text]
    assert jb.extract_atomic_claims("[1]", extractor=lambda _: ["123"]) == ["123"]


class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text, self.headers = status, text, {}


class _SequenceSession:
    def __init__(self, responses): self.responses, self.calls = list(responses), 0
    def get(self, *args, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_evidence_reader_retries_429_then_reads_abstract(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    xml = "<x><AbstractText>Recovered.</AbstractText></x>"
    sess = _SequenceSession([_Resp(429, "busy"), _Resp(200, xml)])
    assert er.fetch_abstract("9", session=sess) == "Recovered."
    assert sess.calls == 2


def test_known_no_abstract_pmid_returns_none_from_injected_empty_record():
    """PMID 6188926 is a real no-abstract example; fixture data keeps this
    code-path test offline."""
    sess = _SequenceSession([_Resp(200, "<PubmedArticleSet><PubmedArticle/></PubmedArticleSet>")])
    assert er.fetch_abstract("6188926", session=sess) is None


def test_repeated_structured_abstract_labels_are_retained_in_document_order():
    xml = ("<x><AbstractText Label=\"RESULTS\">first</AbstractText>"
           "<AbstractText Label=\"RESULTS\">second</AbstractText></x>")
    assert er._parse_abstract(xml) == "RESULTS: first\n\nRESULTS: second"


def test_corrupt_abstract_cache_refetches_instead_of_crashing(tmp_path):
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "abstract_pmid_1.json").write_text("not json", encoding="utf-8")
    sess = _SequenceSession([_Resp(200, "<x><AbstractText>Fresh.</AbstractText></x>")])
    assert er.fetch_abstract("1", session=sess, cache_dir=str(cache)) == "Fresh."
    assert sess.calls == 1


def test_annotation_payload_never_leaks_proposed_fields_under_any_input():
    item = {"item_key": "k", "citing_sentence": "s", "cited_pmid": "1",
            "atomic_claims": ["c"], "evidence": {"proposed_route": "leak"},
            "proposed_route": "F6_FLAGGED", "proposed_verdict": "F6",
            "rationale": "secret"}
    payload = jb.annotation_payload(item)
    assert "proposed_route" not in payload and "proposed_verdict" not in payload
    assert "proposed_route" not in payload["evidence"]


def test_parse_pmc_xml_preserves_unicode_citance_and_numeric_only_claim(tmp_path):
    path = tmp_path / "unicode.xml"
    path.write_text(_XML.replace("A finding", "\u201cCaf\u00e9\u0301\u201d\u2014\u05e9\u05dc\u05d5\u05dd 123"), encoding="utf-8")
    refs = parse_pmc_xml(str(path), source_pmcid="PMC9")
    assert len(refs) == 1
    assert refs[0].citance == "\u201cCaf\u00e9\u0301\u201d\u2014\u05e9\u05dc\u05d5\u05dd 123 1."
    assert jb.extract_atomic_claims(refs[0].citance, extractor=lambda _: ["123"]) == ["123"]


def test_run_band_empty_and_unparseable_dirs_write_consistent_empty_outputs(
        tmp_path, monkeypatch):
    _patch_pubtypes(monkeypatch)
    empty = tmp_path / "empty"; empty.mkdir()
    out_empty = tmp_path / "out-empty"
    man = jb.run_band(str(empty), str(out_empty), extractor=lambda _: [],
                      coverage_judge=lambda *_: [], fetch_abstract=lambda _: None,
                      session=object())
    assert man["counts"]["docs_processed"] == man["counts"]["refs_seen"] == 0
    bad = tmp_path / "bad"; bad.mkdir()
    (bad / "PMCbad.xml").write_text("<article>", encoding="utf-8")
    out_bad = tmp_path / "out-bad"
    man = jb.run_band(str(bad), str(out_bad), extractor=lambda _: [],
                      coverage_judge=lambda *_: [], fetch_abstract=lambda _: None,
                      session=object())
    assert man["counts"]["docs_processed"] == 1 and man["counts"]["refs_seen"] == 0
    assert _records(out_bad / "judgment_band_checkpoint.jsonl")[0]["pmcid"] == "PMCbad"


def test_zero_claim_item_routes_no_claims_and_queue_is_blind(tmp_path, monkeypatch):
    """REPINNED 2026-08-11 (ZD calibration item 1). This test used to assert
    FULL_COVERAGE for a claim-less reference and thereby pinned the defect as the
    contract: ``all()`` over an empty list is vacuously True, so a reference with
    no atomic claims entered the stage that feeds the F3 discriminator as a false
    clear. It is now its own terminal route, counted separately and EXCLUDED from
    the scoreable denominator.

    What did NOT change: no model call is made (the judge would fail the test if
    called), items_built still counts the reference, and the queued payload is
    still blind with an empty claim list."""
    _patch_pubtypes(monkeypatch)
    out = tmp_path / "out"
    man = jb.run_band(_write_xml(tmp_path), str(out), extractor=lambda _: [],
                      coverage_judge=lambda *_: pytest.fail("no claims to judge"),
                      fetch_abstract=lambda _: "abstract", session=object())
    assert man["counts"][jb.ROUTE_NO_CLAIMS] == man["counts"]["items_built"] == 1
    assert man["counts"][jb.ROUTE_FULL_COVERAGE] == 0
    assert _records(out / "judgment_band_annotation_queue.jsonl")[0]["atomic_claims"] == []


def test_resume_after_mid_document_interrupt_does_not_duplicate_prior_rows(
        tmp_path, monkeypatch):
    """A process stop after the first row but before the document checkpoint
    must not replay that row.  The document is the unit of durability: its rows
    are buffered and written only once it completes, just before its checkpoint."""
    _patch_pubtypes(monkeypatch)
    xml = _XML.replace(
        "</p></body>",
        ' Second <xref ref-type="bibr" rid="R2">2</xref>.</p></body>').replace(
        "</ref-list>",
        '<ref id="R2"><element-citation><article-title>R2</article-title><pub-id pub-id-type="pmid">2</pub-id></element-citation></ref></ref-list>')
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC9.xml").write_text(xml, encoding="utf-8")
    out = tmp_path / "out"
    calls = 0
    def interrupted(sentence):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt
        return [sentence]
    with pytest.raises(KeyboardInterrupt):
        jb.run_band(str(xml_dir), str(out), extractor=interrupted,
                    coverage_judge=lambda c, e: [{"established": True}] * len(c),
                    fetch_abstract=lambda _: "abstract", session=object())
    jb.run_band(str(xml_dir), str(out), extractor=lambda s: [s],
                coverage_judge=lambda c, e: [{"established": True}] * len(c),
                fetch_abstract=lambda _: "abstract", session=object())
    ids = [r["citation_id"] for r in _records(out / "judgment_band_items.jsonl")]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("suffix", [
    "\nmore valid-looking prose",
    '{"engages_subject":true,"contradicts":false,"unconfirmed_specifics":[],"rationale":"again","evidence_span":"x"}',
])
def test_valid_coverage_json_followed_by_extra_data_is_quarantined(
        tmp_path, monkeypatch, suffix):
    """A syntactically valid first object must not hide trailing prose or a
    second object.  This is distinct from an unescaped quote within one object."""
    _patch_pubtypes(monkeypatch)
    valid = ('{"engages_subject":true,"contradicts":false,'
             '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')
    with pytest.raises(ValueError, match="Extra data"):
        bp.parse_coverage(valid + suffix)

    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            return '{"claims":["A finding"]}'
        return valid + suffix

    out = tmp_path / "out"
    man = jb.run_band(_write_xml(tmp_path), str(out),
                      extractor=bp.make_extractor(call_llm),
                      coverage_judge=ca.make_coverage_judge(call_llm),
                      fetch_abstract=lambda _: "abstract", session=object())
    assert man["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
    assert _records(out / "judgment_band_annotation_queue.jsonl") == []


@pytest.mark.parametrize("sentence,n_claims", [
    ("A and B are active [1].", 2),
    ("A, B, C, D, and E are active [1].", 5),
    ("A, B, C, D, E, F, G, H, I, J, K, and L are active [1].", 12),
    ("A, (B and C), D, (E, F), and G are active [1].", 7),
    ("A, B, and C are active [1]. A and B are active [1].", 5),
])
def test_claim_explosion_has_no_item_cap_or_counter_deduplication(
        tmp_path, monkeypatch, sentence, n_claims):
    """Model-produced, distinct claims are all coverage work.  This exercises
    the frozen extractor plumbing, not the prompt's linguistic quality."""
    _patch_pubtypes(monkeypatch)
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC9.xml").write_text(
        _XML.replace("A finding", sentence.replace(" [1]", "")), encoding="utf-8")
    claims = [f"subject-{i} has the shared property" for i in range(n_claims)]
    calls = {"extract": 0, "coverage": 0}

    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            calls["extract"] += 1
            return json.dumps({"claims": claims})
        calls["coverage"] += 1
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')

    out = tmp_path / "out"
    man = jb.run_band(str(xml_dir), str(out), extractor=bp.make_extractor(call_llm),
                      coverage_judge=ca.make_coverage_judge(call_llm),
                      fetch_abstract=lambda _: "abstract", session=object())
    assert calls == {"extract": 1, "coverage": n_claims}
    assert man["counts"][jb.COVERAGE_ESTABLISHED] == n_claims
    assert len(_records(out / "judgment_band_items.jsonl")[0]["atomic_claims"]) == n_claims


def test_one_sentence_four_references_extracts_once_but_judges_each_ref(
        tmp_path, monkeypatch):
    """A shared citance is four distinct cited-work judgments.  Extraction is a
    pure function of the sentence, so it is cached and runs once; coverage cannot
    be shared because evidence is reference-specific, and its per-claim tally is
    correspondingly per ref."""
    _patch_pubtypes(monkeypatch)
    refs = "".join(
        f'<ref id="R{i}"><element-citation><article-title>R{i}</article-title>'
        f'<pub-id pub-id-type="pmid">{i}</pub-id></element-citation></ref>'
        for i in range(1, 5))
    xml = ("<article><body><p>Shared finding <xref ref-type=\"bibr\" "
           "rid=\"R1 R2 R3 R4\">5,6,7,8</xref>.</p></body><back><ref-list>" +
           refs + "</ref-list></back></article>")
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC9.xml").write_text(xml, encoding="utf-8")
    calls = {"extract": 0, "coverage": 0}
    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            calls["extract"] += 1
            return '{"claims":["Shared finding"]}'
        calls["coverage"] += 1
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')
    out = tmp_path / "out"
    man = jb.run_band(str(xml_dir), str(out), extractor=bp.make_extractor(call_llm),
                      coverage_judge=ca.make_coverage_judge(call_llm),
                      fetch_abstract=lambda pmid: f"abstract {pmid}", session=object())
    items = _records(out / "judgment_band_items.jsonl")
    assert len(items) == 4
    assert len({i["citing_sentence"] for i in items}) == 1
    assert all(i["atomic_claims"] == ["Shared finding"] for i in items)
    assert calls == {"extract": 1, "coverage": 4}
    assert man["counts"][jb.COVERAGE_ESTABLISHED] == 4
    # Every per-reference counter is untouched by the cache.
    assert man["counts"]["refs_seen"] == man["counts"]["items_built"] == 4
    assert man["counts"][jb.ROUTE_FULL_COVERAGE] == 4


def _two_sentence_dir(tmp_path, first="First finding", second="Second finding"):
    """One doc, two citances, one distinct cited reference each."""
    refs = "".join(
        f'<ref id="R{i}"><element-citation><article-title>R{i}</article-title>'
        f'<pub-id pub-id-type="pmid">{i}</pub-id></element-citation></ref>'
        for i in (1, 2))
    xml = ('<article><body>'
           f'<p>{first} <xref ref-type="bibr" rid="R1">1</xref>.</p>'
           f'<p>{second} <xref ref-type="bibr" rid="R2">2</xref>.</p>'
           '</body><back><ref-list>' + refs + "</ref-list></back></article>")
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC9.xml").write_text(xml, encoding="utf-8")
    return str(xml_dir)


def test_distinct_sentences_extract_once_each_and_leave_counters_unchanged(
        tmp_path, monkeypatch):
    """The cache must be inert on a corpus with no repeated sentence: one
    extraction per reference, and every counter exactly as before."""
    _patch_pubtypes(monkeypatch)
    calls = {"extract": 0, "coverage": 0}
    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            calls["extract"] += 1
            return '{"claims":["One claim"]}'
        calls["coverage"] += 1
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')
    out = tmp_path / "out"
    man = jb.run_band(_two_sentence_dir(tmp_path), str(out),
                      extractor=bp.make_extractor(call_llm),
                      coverage_judge=ca.make_coverage_judge(call_llm),
                      fetch_abstract=lambda pmid: f"abstract {pmid}",
                      session=object())
    assert calls == {"extract": 2, "coverage": 2}
    assert man["counts"] == {
        "docs_processed": 1, "refs_seen": 2, "items_built": 2,
        jb.EXCLUDED_NO_CITANCE: 0, jb.EXCLUDED_NO_CITED_PMID: 0,
        "no_usable_abstract": 0, "cited_is_review": 0,
        jb.ROUTE_F6_FLAGGED: 0, jb.ROUTE_FULL_COVERAGE: 2, jb.ROUTE_HELD: 0,
        jb.ROUTE_PARSE_QUARANTINE: 0,
        "parse_quarantine_" + jb.QUARANTINE_CAUSE_UNENCODABLE_TEXT: 0,
        "parse_quarantine_" + jb.QUARANTINE_CAUSE_NOT_SERIALIZABLE: 0,
        jb.COVERAGE_ESTABLISHED: 2, jb.COVERAGE_CONTRADICTED: 0,
        jb.COVERAGE_UNCONFIRMED_SPECIFIC: 0, jb.COVERAGE_OFF_TOPIC: 0,
    }


def test_extraction_cache_does_not_span_documents(tmp_path, monkeypatch):
    """The cache is scoped to a document, so an identical sentence in a second
    paper is extracted again rather than inherited across the doc boundary."""
    _patch_pubtypes(monkeypatch)
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    for pmcid in ("PMC1", "PMC2"):
        (xml_dir / f"{pmcid}.xml").write_text(_XML, encoding="utf-8")
    calls = {"extract": 0}
    def extractor(sentence):
        calls["extract"] += 1
        return [sentence]
    out = tmp_path / "out"
    man = jb.run_band(str(xml_dir), str(out), extractor=extractor,
                      coverage_judge=lambda c, e: [{"established": True}] * len(c),
                      fetch_abstract=lambda _: "abstract", session=object())
    assert man["counts"]["docs_processed"] == 2
    assert calls["extract"] == 2


def test_failed_extraction_is_not_cached_so_each_reference_quarantines(
        tmp_path, monkeypatch):
    """A malformed reply must keep its per-reference retry and its per-reference
    quarantine count; only successful extractions are cached."""
    _patch_pubtypes(monkeypatch)
    calls = {"extract": 0}
    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            calls["extract"] += 1
            return '```json\n{"claims":["x"]}\n```'
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')
    refs = "".join(
        f'<ref id="R{i}"><element-citation><article-title>R{i}</article-title>'
        f'<pub-id pub-id-type="pmid">{i}</pub-id></element-citation></ref>'
        for i in range(1, 5))
    xml = ("<article><body><p>Shared finding <xref ref-type=\"bibr\" "
           "rid=\"R1 R2 R3 R4\">5,6,7,8</xref>.</p></body><back><ref-list>" +
           refs + "</ref-list></back></article>")
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    (xml_dir / "PMC9.xml").write_text(xml, encoding="utf-8")
    out = tmp_path / "out"
    man = jb.run_band(str(xml_dir), str(out),
                      extractor=bp.make_extractor(call_llm),
                      coverage_judge=ca.make_coverage_judge(call_llm),
                      fetch_abstract=lambda pmid: f"abstract {pmid}",
                      session=object())
    assert calls["extract"] == 4
    assert man["counts"][jb.ROUTE_PARSE_QUARANTINE] == 4
    assert man["counts"]["items_built"] == 0


@pytest.mark.xfail(strict=True, reason="coverage prompt uses chained replacement")
def test_atomic_claim_placeholder_text_remains_inert_in_coverage_prompt():
    prompts = []
    def call_llm(prompt):
        prompts.append(prompt)
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')
    claim = "The literal token <<EVIDENCE>> is part of this claim"
    result = ca.judge_coverage_tristate(call_llm, claim, "SECRET ABSTRACT")
    assert result["established"] is True
    assert claim in prompts[0]


@pytest.mark.xfail(strict=True, reason="valid but wrong-shaped cache data is not validated")
@pytest.mark.parametrize("cached", [[], {"abstract": 7}])
def test_wrong_shaped_cache_is_ignored_and_refetched(tmp_path, cached):
    cache = tmp_path / "cache"; cache.mkdir()
    (cache / "abstract_pmid_7.json").write_text(json.dumps(cached), encoding="utf-8")
    sess = _SequenceSession([_Resp(200, "<x><AbstractText>Fresh.</AbstractText></x>")])
    assert er.fetch_abstract("7", session=sess, cache_dir=str(cache)) == "Fresh."
    assert sess.calls == 1


@pytest.mark.parametrize("engages,contradicts,has_specific", [
    (engages, contradicts, has_specific)
    for engages in (False, True)
    for contradicts in (False, True)
    for has_specific in (False, True)
])
def test_all_eight_tristate_input_combinations(
        engages, contradicts, has_specific):
    specifics = ["dose"] if has_specific else []
    if not engages and (contradicts or specifics):
        with pytest.raises(ValueError, match="engages_subject=false"):
            ca.aggregate_coverage(engages, contradicts, specifics)
        return
    expected = (False if contradicts else None if not engages or specifics else True)
    established = ca.aggregate_coverage(engages, contradicts, specifics)
    assert established is expected
    assert jb.route([{"established": established}]) == (
        jb.ROUTE_F6_FLAGGED if expected is False else
        jb.ROUTE_FULL_COVERAGE if expected is True else jb.ROUTE_HELD)


def test_abstract_equal_to_citing_claim_remains_ordinary_usable_evidence():
    text = "The same sentence appears on both sides."
    prompts = []
    def call_llm(prompt):
        prompts.append(prompt)
        return ('{"engages_subject":true,"contradicts":false,'
                '"unconfirmed_specifics":[],"rationale":"ok","evidence_span":"x"}')
    result = ca.judge_coverage_tristate(call_llm, text, text)
    assert result["established"] is True
    assert prompts[0].count(text) == 2


@pytest.mark.xfail(
    strict=True,
    reason="a NO_CLAIMS item is still queued with nothing to annotate")
def test_zero_claim_item_is_held_out_of_annotation_queue(tmp_path, monkeypatch):
    """STILL FAILING, and the reason narrowed on 2026-08-11.

    The ROUTE half of this defect is fixed: a claim-less reference no longer routes
    FULL_COVERAGE. This row's original expectation was ROUTE_HELD, which ZD's
    calibration spec superseded with a dedicated terminal route -- asserted here so
    the remaining failure is ONLY the queue half.

    What remains: the reference is still appended to the blind annotation queue
    with an empty claim list, so the annotator is handed a unit with nothing to
    judge. The band already has the pattern for this (an item held for an
    unretrieved body is recorded durably but NOT queued, because it is "not
    answerable yet"), and a claim-less reference is the same shape of thing. ZD's
    spec enumerated the route, the counter and the denominator ruling and did NOT
    include queue membership, so it was left alone rather than changed on the
    builder's initiative. Backlog, not silence."""
    _patch_pubtypes(monkeypatch)
    out = tmp_path / "out"
    man = jb.run_band(_write_xml(tmp_path), str(out), extractor=lambda _: [],
                      coverage_judge=lambda *_: [], fetch_abstract=lambda _: "abstract",
                      session=object())
    assert man["counts"][jb.ROUTE_NO_CLAIMS] == 1
    assert _records(out / "judgment_band_annotation_queue.jsonl") == []


@pytest.mark.xfail(strict=True, reason="resume manifest reports only the latest invocation")
def test_resume_manifest_counts_match_all_durable_rows(tmp_path, monkeypatch):
    _patch_pubtypes(monkeypatch)
    out = tmp_path / "out"
    common = dict(extractor=lambda s: [s],
                  coverage_judge=lambda c, e: [{"established": True}] * len(c),
                  fetch_abstract=lambda _: "abstract", session=object())
    jb.run_band(_write_xml(tmp_path), str(out), **common)
    resumed = jb.run_band(str(tmp_path / "xml"), str(out), **common)
    rows = _records(out / "judgment_band_items.jsonl")
    assert resumed["counts"]["items_built"] == len(rows)


def test_document_with_every_reference_excluded_writes_no_item_rows(
        tmp_path, monkeypatch):
    _patch_pubtypes(monkeypatch)
    xml_dir = tmp_path / "xml"; xml_dir.mkdir()
    xml = ("<article><body><p>Cited <xref ref-type=\"bibr\" rid=\"R1\">1</xref>.</p>"
           "</body><back><ref-list>"
           "<ref id=\"R1\"><element-citation><article-title>No PMID</article-title>"
           "</element-citation></ref>"
           "<ref id=\"R2\"><element-citation><article-title>Never cited</article-title>"
           "<pub-id pub-id-type=\"pmid\">2</pub-id></element-citation></ref>"
           "</ref-list></back></article>")
    (xml_dir / "PMCexcluded.xml").write_text(xml, encoding="utf-8")
    out = tmp_path / "out"
    man = jb.run_band(str(xml_dir), str(out), extractor=lambda _: ["x"],
                      coverage_judge=lambda *_: [], fetch_abstract=lambda _: "abstract",
                      session=object())
    assert man["counts"][jb.EXCLUDED_NO_CITED_PMID] == 1
    assert man["counts"][jb.EXCLUDED_NO_CITANCE] == 1
    assert man["counts"]["items_built"] == 0
    assert _records(out / "judgment_band_items.jsonl") == []


@pytest.mark.xfail(strict=True, reason="parse_pmc_xml does not traverse namespaced ref tags")
def test_parse_pmc_xml_accepts_default_namespaced_jats(tmp_path):
    path = tmp_path / "namespaced.xml"
    path.write_text(
        '<article xmlns="urn:jats"><body><p>Finding '
        '<xref ref-type="bibr" rid="R1">1</xref>.</p></body><back><ref-list>'
        '<ref id="R1"><element-citation><article-title>Paper</article-title>'
        '<pub-id pub-id-type="pmid">1</pub-id></element-citation></ref>'
        '</ref-list></back></article>', encoding="utf-8")
    refs = parse_pmc_xml(str(path), source_pmcid="PMCNS")
    assert len(refs) == 1
    assert refs[0].claimed.claimed_pmid == "1"
