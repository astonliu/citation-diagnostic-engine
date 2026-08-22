"""Item 2: surname extraction is deterministic (stable getpath key, not id()).

The id()-keyed dedup could drop a real surname when a released lxml proxy's
address was reused by a later element, so an author array flickered 4<->3 across
identical parses. These assert determinism + that the documented string-name
behaviours are preserved.
"""
from __future__ import annotations

import json
from pathlib import Path

from lxml import etree

from cre.f1.parser import (
    _SENT_RE, _authors_from, _sentence_spans, _surnames_under, parse_pmc_xml)


_REAL_FAILURES = json.loads((
    Path(__file__).with_name("testdata") / "natural_run_failures_20260821.json"
).read_text(encoding="utf-8"))["cases"]


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


def test_sentence_spans_tile_decimal_prose_without_losing_prefix(capsys):
    text = "Dose was 0.5 mg. Next."
    diagnostics = []
    spans = _sentence_spans(text, diagnostics)
    # The exported compatibility regex remains stable, but is no longer used to
    # construct citances because it silently dropped the prefix before ``5 mg``.
    assert _SENT_RE.pattern == r"[^.!?]*[.!?]+(?:\s+|$)|[^.!?]+$"
    assert spans == [(0, 17, "Dose was 0.5 mg. "), (17, 22, "Next.")]
    assert diagnostics == []
    assert capsys.readouterr().err == ""


def test_real_pmc10908279_marker_keeps_complete_sentence(tmp_path):
    case = _REAL_FAILURES["punctuation_adjacent_marker"]
    doc = f"""<article><body><p>{case['paragraph_excerpt']}<xref
    ref-type='bibr' rid='{case['rid']}'>{case['marker']}</xref></p></body>
    <back><ref-list><ref id='{case['rid']}'><element-citation>
    <article-title>{case['cited_title']}</article-title>
    <pub-id pub-id-type='pmid'>{case['cited_pmid']}</pub-id>
    </element-citation></ref></ref-list></back></article>""".encode()
    path = tmp_path / f"{case['pmcid']}.xml"
    path.write_bytes(doc)
    ref = parse_pmc_xml(str(path), source_pmcid=case["pmcid"])[0]
    assert ref.citation_id == case["citation_id"]
    assert ref.citance == case["paragraph_excerpt"] + case["marker"]
    assert ref.citance != case["old_citance"]
    assert ref.citance_sentence_partition_failures == []


def test_real_pmc12903921_table_reference_keeps_entire_row(tmp_path):
    case = _REAL_FAILURES["table_reference_cell"]
    cells = "".join(f"<td>{value}</td>" for value in case["cells"])
    doc = f"""<article><body><sec><title>Dietary organic acids (DOAs)</title>
    <table-wrap><table><tbody><tr>{cells}<td>(<xref ref-type='bibr'
    rid='{case['rid']}'>{case['marker']}</xref>)</td></tr></tbody></table>
    </table-wrap></sec></body><back><ref-list><ref id='{case['rid']}'>
    <element-citation><article-title>{case['cited_title']}</article-title>
    <pub-id pub-id-type='pmid'>{case['cited_pmid']}</pub-id>
    </element-citation></ref></ref-list></back></article>""".encode()
    path = tmp_path / f"{case['pmcid']}.xml"
    path.write_bytes(doc)
    ref = parse_pmc_xml(str(path), source_pmcid=case["pmcid"])[0]
    assert ref.citation_id == case["citation_id"]
    assert ref.citance == " | ".join(case["cells"] + [f"({case['marker']})"])
    assert ref.citance != case["old_citance"]
    assert any(ch.isalpha() for ch in ref.citance)


def test_parser_no_longer_attaches_partition_gap_to_reference(tmp_path):
    doc = b"""<article><body><p>Dose was 0.5 mg <xref ref-type='bibr' rid='r1'>1</xref>.</p></body>
    <back><ref-list><ref id='r1'><element-citation><article-title>A study</article-title>
    <pub-id pub-id-type='pmid'>111</pub-id></element-citation></ref></ref-list></back></article>"""
    path = tmp_path / "PMC1.xml"
    path.write_bytes(doc)
    ref = parse_pmc_xml(str(path))[0]
    assert ref.citance == "Dose was 0.5 mg 1."
    assert ref.citance_sentence_partition_failures == []


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


# ==========================================================================
# TRAVERSAL REACHABILITY -- the block walk must see every cited reference
# ==========================================================================
def test_every_cited_reference_is_reachable_by_the_block_walk(capsys):
    """The whole-document xref scan is the ORACLE; the block walk must match it.

    REGRESSION GUARD. `_innermost_blocks` used to skip any block that contained
    another block, which discarded the markers in the outer block's own text: it
    reached 36 of PMC8544026's 38 cited references and 95 of PMC13449730's 102,
    and those 9 references then had no citing sentence at all and reached human
    review as "empty_claim_input" -- reading as a reference nothing cites, when
    the document cites it and the walk could not see the marker.

    Prints per-document reached/total so a partial regression is visible as a
    number rather than only as a boolean.
    """
    import xml.etree.ElementTree as ET
    from pathlib import Path as _Path
    from cre.f1 import parser as P

    fixtures = sorted(_Path(__file__).parent.joinpath("fixtures").glob("*.xml"))
    assert fixtures, "no XML fixtures found; this guard would pass vacuously"
    total_cited = 0
    for path in fixtures:
        root = ET.parse(str(path)).getroot()
        cited = P._cited_rids(root)
        unreached = P.unreached_cited_rids(root)
        total_cited += len(cited)
        print(f"  {path.name:28} {len(cited) - len(unreached):3d}/{len(cited):3d}")
        assert not unreached, (
            f"{path.name}: the block walk cannot reach cited reference(s) "
            f"{sorted(unreached)}; a traversal defect makes them look uncited")
    # A guard that cannot fail is not a guard: fixtures must actually cite things.
    assert total_cited > 0, "fixtures contain no bibr xrefs; guard is vacuous"
    print(f"  TOTAL cited references across fixtures: {total_cited}")
