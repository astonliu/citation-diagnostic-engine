"""Additional adversarial code-path tests for the F3--F7 judgment band.

All model and HTTP boundaries are injected. The strings here are transport and
parser fixtures only; they are never evaluation examples or gold labels.
"""
from __future__ import annotations

import json

import pytest

from cre.f1 import band_prompts as bp
from cre.f1 import coverage_aggregate as ca
from cre.f1 import judgment_band as jb
from cre.f1.parser import parse_pmc_xml


_ONE_REF_XML = """\
<article><body><p>A finding <xref ref-type="bibr" rid="R1">1</xref>.</p></body>
<back><ref-list><ref id="R1"><element-citation>
<article-title>Paper</article-title><pub-id pub-id-type="pmid">1</pub-id>
</element-citation></ref></ref-list></back></article>
"""


def _write_one_ref_xml(tmp_path):
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC1.xml").write_text(_ONE_REF_XML, encoding="utf-8")
    return str(xml_dir)


def _patch_not_review(monkeypatch):
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *args, **kwargs: [])
    monkeypatch.setattr(jb, "is_review", lambda value: False)


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _forbidden_key_paths(value, path=()):
    forbidden = {"proposed_route", "proposed_verdict", "rationale"}
    found = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in forbidden:
                found.append(path + (key,))
            found.extend(_forbidden_key_paths(nested, path + (str(key),)))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            found.extend(_forbidden_key_paths(nested, path + (str(index),)))
    return found


def test_annotation_payload_recursively_removes_every_blinded_key():
    """The evidence whitelist must also hold inside nested reflist metadata."""
    item = {
        "item_key": "PMC1:R1",
        "citing_sentence": "A finding [1].",
        "cited_pmid": "1",
        "atomic_claims": [
            "A finding",
            {"safe": "retained", "rationale": "nested claim leak"},
        ],
        "evidence": {
            "cited_pmid": "1",
            "cited_abstract": "Abstract text.",
            "cited_is_review": True,
            "review_reflist": [{
                "title": "Primary paper",
                "proposed_route": "F6_FLAGGED",
                "nested": {
                    "year": 2020,
                    "proposed_verdict": "F6",
                    "rationale": "nested evidence leak",
                },
            }],
            "review_fulltext_available": True,
            "rationale": "outer evidence leak",
        },
        "proposed_route": "F6_FLAGGED",
        "proposed_verdict": "F6",
        "rationale": "outer item leak",
    }

    payload = jb.annotation_payload(item)

    assert _forbidden_key_paths(payload) == []
    assert payload["atomic_claims"][1] == {"safe": "retained"}
    assert payload["evidence"]["review_reflist"][0]["title"] == "Primary paper"
    assert payload["evidence"]["review_reflist"][0]["nested"] == {"year": 2020}
    # Scrubbing returns detached containers and does not mutate the item record.
    assert item["evidence"]["review_reflist"][0]["proposed_route"] == "F6_FLAGGED"


@pytest.mark.parametrize("value", [None, 0, 1, "", "false", [], {}])
def test_route_treats_every_non_boolean_falsy_or_truthy_value_as_unknown(value):
    """Only the singleton booleans decide a route; coercion would misroute 0."""
    assert jb.route([{"established": value}]) == jb.ROUTE_HELD


@pytest.mark.parametrize("value,expected", [
    (False, jb.ROUTE_F6_FLAGGED),
    (True, jb.ROUTE_FULL_COVERAGE),
])
def test_route_reserves_decisions_for_actual_booleans(value, expected):
    assert jb.route([{"established": value}]) == expected


@pytest.mark.parametrize("verdict,expected", [
    ({"engages_subject": True, "contradicts": False,
      "unconfirmed_specifics": []}, jb.COVERAGE_ESTABLISHED),
    ({"engages_subject": True, "contradicts": True,
      "unconfirmed_specifics": []}, jb.COVERAGE_CONTRADICTED),
    ({"engages_subject": True, "contradicts": False,
      "unconfirmed_specifics": ["dose"]}, jb.COVERAGE_UNCONFIRMED_SPECIFIC),
    ({"engages_subject": False, "contradicts": False,
      "unconfirmed_specifics": []}, jb.COVERAGE_OFF_TOPIC),
    ({"engages_subject": None, "contradicts": None,
      "unconfirmed_specifics": []}, None),
])
def test_coverage_bucket_exhausts_the_canonical_structured_states(verdict, expected):
    assert jb.coverage_bucket(verdict) == expected


@pytest.mark.parametrize("established", [0, 1])
def test_coverage_parser_rejects_numeric_established_as_an_extra_key(established):
    raw = {
        "engages_subject": True,
        "contradicts": False,
        "unconfirmed_specifics": [],
        "rationale": "ok",
        "evidence_span": "span",
        "established": established,
    }
    with pytest.raises(ValueError, match="extra=\\['established'\\]"):
        bp.parse_coverage(json.dumps(raw))


def test_valid_json_escaping_accepts_quotes_backslashes_and_nested_braces():
    """The live quote case is safe when the producer emits actual JSON escapes."""
    rationale = 'The phrase "effective" appears under {RESULTS} at C:\\trial.'
    span = 'The abstract calls it "effective"; path C:\\trial\\arm.'
    raw = json.dumps({
        "engages_subject": True,
        "contradicts": False,
        "unconfirmed_specifics": [],
        "rationale": rationale,
        "evidence_span": span,
    })

    verdict = bp.parse_coverage(raw)

    assert verdict.rationale == rationale
    assert verdict.evidence_span == span
    assert verdict.established is True


def test_make_extractor_preserves_all_requested_unicode_and_long_input():
    prefix = "“smart” – en — em\u00a0Cafe\u0301 العربية אבג\u200d"
    sentence = prefix + ("x" * 10_000) + " [17]"
    claim = prefix + ("x" * 10_000)
    prompts = []

    def call_llm(prompt):
        prompts.append(prompt)
        return json.dumps({"claims": [claim]}, ensure_ascii=False)

    assert bp.make_extractor(call_llm)(sentence) == [claim]
    assert len(prompts) == 1
    assert sentence in prompts[0]


def test_parser_and_extractor_handle_a_citation_marker_only_sentence(tmp_path):
    path = tmp_path / "marker.xml"
    path.write_text(
        _ONE_REF_XML.replace(
            'A finding <xref ref-type="bibr" rid="R1">1</xref>.',
            '<xref ref-type="bibr" rid="R1">[1]</xref>.'),
        encoding="utf-8",
    )
    refs = parse_pmc_xml(str(path), source_pmcid="PMC1")
    prompts = []
    extractor = bp.make_extractor(
        lambda prompt: prompts.append(prompt) or '{"claims": []}')

    assert len(refs) == 1
    assert refs[0].citance == "[1]."
    assert jb.extract_atomic_claims(refs[0].citance, extractor=extractor) == []
    assert "[1]." in prompts[0]
    assert bp.parse_claims('{"claims": ["123"]}') == ["123"]


def test_mixed_run_counts_partition_every_reference_and_written_row(
        tmp_path, monkeypatch):
    _patch_not_review(monkeypatch)
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    refs = "".join(
        f'<ref id="R{i}"><element-citation><article-title>R{i}</article-title>'
        f'<pub-id pub-id-type="pmid">{i}</pub-id></element-citation></ref>'
        for i in (1, 2, 3, 4, 6)
    )
    refs += ('<ref id="R5"><element-citation>'
             '<article-title>No PMID</article-title></element-citation></ref>')
    body = "".join(
        f'<p>Finding {i} <xref ref-type="bibr" rid="R{i}">{i}</xref>.</p>'
        for i in (1, 2, 3, 4, 5)
    )
    (xml_dir / "PMCmix.xml").write_text(
        "<article><body>" + body + "</body><back><ref-list>" + refs
        + "</ref-list></back></article>",
        encoding="utf-8",
    )

    def judge(claims, evidence):
        pmid = evidence["cited_pmid"]
        if pmid == "4":
            raise ValueError("malformed coverage reply")
        if pmid == "1":
            finding = (True, True, False, [])
        elif pmid == "2":
            finding = (False, True, True, [])
        else:
            finding = (None, False, False, [])
        established, engages, contradicts, specifics = finding
        return [{
            "established": established,
            "engages_subject": engages,
            "contradicts": contradicts,
            "unconfirmed_specifics": specifics,
            "rationale": "log only",
            "evidence_span": "span",
        } for _ in claims]

    out = tmp_path / "out"
    manifest = jb.run_band(
        str(xml_dir), str(out),
        extractor=lambda sentence: [sentence],
        coverage_judge=judge,
        fetch_abstract=lambda pmid: f"abstract {pmid}",
        session=object(),
    )
    counts = manifest["counts"]
    items = _jsonl(out / "judgment_band_items.jsonl")
    queue = _jsonl(out / "judgment_band_annotation_queue.jsonl")
    exclusions = (counts[jb.EXCLUDED_NO_CITANCE]
                  + counts[jb.EXCLUDED_NO_CITED_PMID])
    routed = (counts[jb.ROUTE_F6_FLAGGED]
              + counts[jb.ROUTE_FULL_COVERAGE]
              + counts[jb.ROUTE_HELD])
    buckets = sum(counts[name] for name in (
        jb.COVERAGE_ESTABLISHED,
        jb.COVERAGE_CONTRADICTED,
        jb.COVERAGE_UNCONFIRMED_SPECIFIC,
        jb.COVERAGE_OFF_TOPIC,
    ))

    assert counts["refs_seen"] == exclusions + counts["items_built"] \
        + counts[jb.ROUTE_PARSE_QUARANTINE]
    assert counts["items_built"] == routed == len(queue) == 3
    assert len(items) == counts["items_built"] \
        + counts[jb.ROUTE_PARSE_QUARANTINE] == 4
    assert buckets == 3
    assert counts[jb.ROUTE_FULL_COVERAGE] == 1
    assert counts[jb.ROUTE_F6_FLAGGED] == 1
    assert counts[jb.ROUTE_HELD] == 1


@pytest.mark.xfail(
    strict=True,
    reason="an interrupt after queue publication precedes the document checkpoint",
)
def test_resume_after_publish_interrupt_has_no_duplicate_items_or_queue(
        tmp_path, monkeypatch):
    """Publishing two JSONL files plus a checkpoint is not an atomic commit."""
    _patch_not_review(monkeypatch)
    xml_dir = _write_one_ref_xml(tmp_path)
    out = tmp_path / "out"
    original_append = jb._append_jsonl
    interrupted = False

    def interrupt_after_queue_write(fh, obj):
        nonlocal interrupted
        original_append(fh, obj)
        if (not interrupted
                and fh.name.endswith("judgment_band_annotation_queue.jsonl")):
            interrupted = True
            raise KeyboardInterrupt

    common = {
        "extractor": lambda sentence: ["A finding"],
        "coverage_judge": lambda claims, evidence: [
            {"established": True} for _ in claims],
        "fetch_abstract": lambda pmid: "abstract",
        "session": object(),
    }
    monkeypatch.setattr(jb, "_append_jsonl", interrupt_after_queue_write)
    with pytest.raises(KeyboardInterrupt):
        jb.run_band(xml_dir, str(out), **common)
    monkeypatch.setattr(jb, "_append_jsonl", original_append)

    jb.run_band(xml_dir, str(out), **common)
    item_ids = [row["citation_id"] for row in
                _jsonl(out / "judgment_band_items.jsonl")]
    queue_ids = [row["item_key"] for row in
                 _jsonl(out / "judgment_band_annotation_queue.jsonl")]
    assert len(item_ids) == len(set(item_ids)) == 1
    assert len(queue_ids) == len(set(queue_ids)) == 1


def test_lone_surrogate_model_text_is_quarantined_instead_of_crashing(
        tmp_path, monkeypatch):
    _patch_not_review(monkeypatch)

    def call_llm(prompt):
        if "CITED-PAPER ABSTRACT" not in prompt:
            return '{"claims": ["A finding"]}'
        return (
            '{"engages_subject":true,"contradicts":false,'
            '"unconfirmed_specifics":[],"rationale":"ok",'
            '"evidence_span":"\\ud800"}'
        )

    manifest = jb.run_band(
        _write_one_ref_xml(tmp_path), str(tmp_path / "out"),
        extractor=bp.make_extractor(call_llm),
        coverage_judge=ca.make_coverage_judge(call_llm),
        fetch_abstract=lambda pmid: "abstract",
        session=object(),
    )
    assert manifest["counts"][jb.ROUTE_PARSE_QUARANTINE] == 1
