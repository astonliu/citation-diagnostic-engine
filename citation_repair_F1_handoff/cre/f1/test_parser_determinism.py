"""Item 2: surname extraction is deterministic (stable getpath key, not id()).

The id()-keyed dedup could drop a real surname when a released lxml proxy's
address was reused by a later element, so an author array flickered 4<->3 across
identical parses. These assert determinism + that the documented string-name
behaviours are preserved.
"""
from __future__ import annotations

from lxml import etree

from cre.f1.parser import (
    _SENT_RE, _authors_from, _sentence_spans, _surnames_under, parse_pmc_xml)


_FOUR_AUTHOR = (
    b'<element-citation><person-group person-group-type="author">'
    b'<name><surname>Phelps</surname></name>'
    b'<name><surname>Huang</surname></name>'
    b'<name><surname>Hoffman</surname></name>'
    b'<name><surname>Selin</surname></name>'
    b'</person-group>'
    b'<article-title>A study</article-title></element-citation>')


def test_multi_author_extraction_is_complete_and_deterministic():
    # 200 parses (fresh element each time, so proxies churn) -> always all four,
    # same order. Under the old id() key a GC'd proxy's reused address could drop
    # the 4th; getpath cannot.
    results = set()
    for _ in range(200):
        authors = _authors_from(etree.fromstring(_FOUR_AUTHOR))
        results.add(tuple(authors))
    assert results == {("Phelps", "Huang", "Hoffman", "Selin")}


def test_surnames_under_stable_across_repeat_calls_same_tree():
    el = etree.fromstring(_FOUR_AUTHOR)
    first = _surnames_under(el)
    for _ in range(50):
        assert _surnames_under(el) == first
    assert first == ["Phelps", "Huang", "Hoffman", "Selin"]


# --- the four documented string-name behaviours still hold (Defect-A intent) ---
def test_string_name_surname_still_parses():
    xml = b'<element-citation><string-name><surname>Pannu</surname></string-name>' \
          b'<article-title>t</article-title></element-citation>'
    assert _authors_from(etree.fromstring(xml)) == ["Pannu"]


def test_mixed_name_and_string_name_document_order():
    xml = (b'<element-citation><person-group person-group-type="author">'
           b'<name><surname>Alpha</surname></name>'
           b'<string-name><surname>Beta</surname></string-name>'
           b'<name><surname>Gamma</surname></name>'
           b'</person-group></element-citation>')
    assert _authors_from(etree.fromstring(xml)) == ["Alpha", "Beta", "Gamma"]


def test_nested_name_in_string_name_counted_once():
    xml = (b'<element-citation><person-group person-group-type="author">'
           b'<string-name><name><surname>Delta</surname></name></string-name>'
           b'</person-group></element-citation>')
    assert _authors_from(etree.fromstring(xml)) == ["Delta"]


def test_pure_name_ref_unchanged():
    xml = (b'<element-citation><person-group person-group-type="author">'
           b'<name><surname>Solo</surname></name></person-group></element-citation>')
    assert _authors_from(etree.fromstring(xml)) == ["Solo"]


def test_parse_pmc_xml_multi_author_deterministic(tmp_path):
    doc = (b'<article><back><ref-list><ref id="r1">' + _FOUR_AUTHOR +
           b'</ref></ref-list></back></article>')
    p = tmp_path / "PMCX.xml"; p.write_bytes(doc)
    runs = {tuple(parse_pmc_xml(str(p))[0].claimed.authors) for _ in range(50)}
    assert runs == {("Phelps", "Huang", "Hoffman", "Selin")}


def test_legacy_sentence_regex_is_unchanged_and_partition_gap_is_counted(capsys):
    text = "Dose was 0.5 mg. Next."
    diagnostics = []
    spans = _sentence_spans(text, diagnostics)
    assert _SENT_RE.pattern == r"[^.!?]*[.!?]+(?:\s+|$)|[^.!?]+$"
    assert spans == [(11, 17, "5 mg. "), (17, 22, "Next.")]
    assert diagnostics == [{
        "kind": "sentence_spans_do_not_tile_input",
        "text_sha256": "96cae425479f8888f234ef1a6ee52bfeee7cd3e1590329f5923703f16de14fb0",
        "text_length": 22, "span_count": 2, "covered_chars": 11,
        "uncovered_chars": 11, "uncovered_ranges": [[0, 11]],
    }]
    assert "sentence-partition-gap" in capsys.readouterr().err


def test_parser_attaches_partition_gap_to_affected_reference(tmp_path):
    doc = b"""<article><body><p>Dose was 0.5 mg <xref ref-type='bibr' rid='r1'>1</xref>.</p></body>
    <back><ref-list><ref id='r1'><element-citation><article-title>A study</article-title>
    <pub-id pub-id-type='pmid'>111</pub-id></element-citation></ref></ref-list></back></article>"""
    path = tmp_path / "PMC1.xml"
    path.write_bytes(doc)
    ref = parse_pmc_xml(str(path))[0]
    assert ref.citance_sentence_partition_failures
    failure = ref.citance_sentence_partition_failures[0]
    assert failure["kind"] == "sentence_spans_do_not_tile_input"
    assert failure["uncovered_chars"] > 0


def test_two_consecutive_rebands_byte_identical(tmp_path):
    # Item 2 acceptance shape: the records artifact of two consecutive rebands over
    # the same manifest-scoped corpus is byte-identical. (Small synthetic corpus --
    # the real 700-paper reband needs the Drive corpus, absent here.)
    from cre.f1.f2_run_v3 import reband_from_cache
    xml = tmp_path / "xml"; xml.mkdir()
    (xml / "PMC1.xml").write_bytes(
        b'<article><back><ref-list><ref id="r1">' + _FOUR_AUTHOR.replace(
            b'<article-title>A study</article-title>',
            b'<article-title>A study of things</article-title>'
            b'<source>J Test</source><year>2019</year>'
            b'<pub-id pub-id-type="pmid">111</pub-id>')
        + b'</ref></ref-list></back></article>')
    cache = tmp_path / "c.jsonl"
    cache.write_text('{"src_pmcid": "PMC1", "pmid": "111", "rec": {"resolved": true, '
                     '"title": "A different resolved title", "authors": ["Zeta"], '
                     '"year": 2019, "journal": "J Test", "pmid": "111"}}\n')

    def _run(out):
        out.mkdir()
        reband_from_cache(str(xml), str(cache), out_dir=str(out), version="v3_1",
                          seed=37, src_pmcids=["PMC1"])
        return next(out.glob("*_seed37_v3_1.jsonl")).read_bytes()

    a = _run(tmp_path / "run_a")
    b = _run(tmp_path / "run_b")
    assert a == b and len(a) > 0
