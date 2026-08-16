"""The F6 co-citation acceptance matrix.

THE DEFECT. ``parser.link_citances`` attached one citing sentence to every
reference that sentence cites, independently, and recorded nothing about the
others. The band then built one item per reference, each carrying the WHOLE
claim, and asked of each one alone: does this paper support it? Each supports
part -- and F6 is defined as "supports part of the claim but not all of it". F6
therefore fired BY CONSTRUCTION on every member of every co-citation group.
Measured first-party on PMC13295119 (2026-08-15): 80.6% F6 on multi-reference
sentences against 44.9% on single-reference ones, a 36-point gap.

Citing eight papers for one sentence is normal, correct scientific practice.

THE ROWS THAT MATTER are 4, 5, 6 and 14: a naive "if it has siblings, don't
flag it" fix passes rows 1, 2 and 3 and fails all four. Each is marked ROW N
below and each asserts the specific thing that a blanket excuse would lose:

  * ROW 4  -- a member that supported NOTHING is a fault, not a clear.
  * ROW 5  -- a claim NO member covered survives as a group-level finding.
  * ROW 6  -- an unsupported numeric specific survives the same way.
  * ROW 14 -- a wholly off-topic group is a fault on EVERY member, never
              averaged into "partially covered".

No network and no model: the extractor and coverage judge are stubs, the XML is
fabricated, and the pubtype lookup is monkeypatched on the consumer namespace.
"""
from __future__ import annotations

import json

import pytest

from . import cocitation as cc
from . import judgment_band as jb
from . import judgment_run as jr
from .judgment_engine import (ClaimSupport, DiscriminatorContractError,
                              EntityAssessment, EntityState, SupportState,
                              TemporalAssessment, TemporalState,
                              decide_judgment)
from .parser import parse_pmc_xml


# ==========================================================================
# fixtures: fabricated JATS, a scripted extractor, a scripted coverage judge
# ==========================================================================
def _article(refs, paragraphs, pmcid="PMC1000") -> str:
    """A minimal but real JATS article: a ref-list plus body paragraphs whose
    <xref ref-type="bibr"> markers point into it."""
    ref_xml = "".join(
        f'<ref id="{rid}"><element-citation publication-type="journal">'
        f'<article-title>Cited work {rid}</article-title>'
        f'<pub-id pub-id-type="pmid">{pmid}</pub-id>'
        f"</element-citation></ref>"
        for rid, pmid in refs)
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        "<article><front><article-meta>"
        '<article-id pub-id-type="pmid">900</article-id>'
        "<title-group><article-title>Citing paper</article-title></title-group>"
        "</article-meta></front>"
        f"<body>{body}</body>"
        f"<back><ref-list>{ref_xml}</ref-list></back></article>")


def _xref(rids: str, text: str) -> str:
    return f'<xref ref-type="bibr" rid="{rids}">{text}</xref>'


def _parse(tmp_path, refs, paragraphs, pmcid="PMC1000"):
    path = tmp_path / f"{pmcid}.xml"
    path.write_text(_article(refs, paragraphs, pmcid), encoding="utf-8")
    return parse_pmc_xml(str(path), source_pmcid=pmcid)


def _verdict(*, engages: bool, contradicts: bool = False, unconfirmed=()):
    """One coverage verdict, aggregated EXACTLY as band_prompts.aggregate_coverage
    does -- established = engaged AND not contradicted AND nothing unconfirmed --
    so these stubs cannot drift from the frozen contract they stand in for."""
    unconfirmed = list(unconfirmed)
    return {
        "established": bool(engages) and not contradicts and not unconfirmed,
        "engages_subject": bool(engages),
        "contradicts": bool(contradicts),
        "unconfirmed_specifics": unconfirmed,
        "rationale": "stub",
        "evidence_span": "span" if engages else "",
    }


SUPPORTS = _verdict(engages=True)
OFF_TOPIC = _verdict(engages=False)
CONTRADICTS = _verdict(engages=True, contradicts=True)
UNCONFIRMED = _verdict(engages=True, unconfirmed=["34%"])
#: The deterministic no-usable-evidence verdict: no structured fields at all.
UNJUDGED = {"established": None, "rationale": "no usable abstract",
            "evidence_span": ""}


def _band(tmp_path, monkeypatch, *, refs, paragraphs, claims, script,
          missing_abstract=(), pmcid="PMC1000"):
    """Run the real ``run_band`` over a fabricated article.

    ``script`` maps cited PMID -> list of verdicts, one per claim.
    ``missing_abstract`` names PMIDs whose abstract is unretrievable.
    Returns ``(manifest, items_by_citation_id, group_records)``.
    """
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / f"{pmcid}.xml").write_text(
        _article(refs, paragraphs, pmcid), encoding="utf-8")
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: ["Journal Article"])
    out = tmp_path / "out"

    def fetch_abstract(pmid):
        return None if pmid in missing_abstract else f"Abstract for {pmid}."

    def coverage_judge(claim_list, evidence):
        rows = script.get(evidence["cited_pmid"], [])
        return [rows[i] if i < len(rows) else UNJUDGED
                for i in range(len(claim_list))]

    manifest = jb.run_band(
        str(xml_dir), str(out), extractor=lambda _s: list(claims),
        coverage_judge=coverage_judge, fetch_abstract=fetch_abstract,
        session=object())
    items = {}
    for line in (out / "judgment_band_items.jsonl").read_text().splitlines():
        row = json.loads(line)
        items[row["citation_id"]] = row
    groups_file = out / "judgment_band_cocitation_groups.jsonl"
    groups = [json.loads(line) for line in
              groups_file.read_text().splitlines()] if groups_file.exists() else []
    return manifest, items, groups


CLAIM_A = "Drug X reduces outcome A"
CLAIM_B = "Drug X reduces outcome B"


# ==========================================================================
# ROW 1 -- singleton, full support. Regression guard: unchanged from today.
# ==========================================================================
def test_row1_singleton_full_support_is_full_coverage(tmp_path, monkeypatch):
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111")],
        paragraphs=[f"Drug X reduces outcome A {_xref('B1', '1')}."],
        claims=[CLAIM_A], script={"111": [SUPPORTS]})
    row = items["PMC1000:B1"]
    assert row["proposed_route"] == jb.ROUTE_FULL_COVERAGE
    assert "proposed_route_solo" not in row      # nothing was overridden
    assert "cocitation" not in row               # no group machinery
    assert groups == []


# ==========================================================================
# ROW 2 -- singleton, partial. F6 MUST still fire on a solo citation.
# ==========================================================================
def test_row2_singleton_partial_support_still_fires_f6(tmp_path, monkeypatch):
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111")],
        paragraphs=[f"Drug X reduces outcome A and outcome B {_xref('B1', '1')}."],
        claims=[CLAIM_A, CLAIM_B], script={"111": [SUPPORTS, OFF_TOPIC]})
    row = items["PMC1000:B1"]
    assert row["proposed_route"] == jb.ROUTE_F6_FLAGGED
    assert row["proposed_verdict"] == "F6"
    assert groups == []


# ==========================================================================
# ROW 3 -- pair, complementary. THE BUG: neither member is F6.
# ==========================================================================
def test_row3_complementary_pair_is_not_f6(tmp_path, monkeypatch):
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111"), ("B2", "222")],
        paragraphs=[f"Drug X reduces outcome A and outcome B "
                    f"{_xref('B1', '1')},{_xref('B2', '2')}."],
        claims=[CLAIM_A, CLAIM_B],
        script={"111": [SUPPORTS, OFF_TOPIC],     # covers A only
                "222": [OFF_TOPIC, SUPPORTS]})    # covers B only
    for cid in ("PMC1000:B1", "PMC1000:B2"):
        row = items[cid]
        assert row["proposed_route"] != jb.ROUTE_F6_FLAGGED
        assert row["proposed_route"] == cc.ROUTE_GROUP_COVERED
        assert row["proposed_verdict"] is None
        # The solo route it WOULD have taken is retained, so the change is visible.
        assert row["proposed_route_solo"] == jb.ROUTE_F6_FLAGGED
    assert len(groups) == 1
    assert groups[0]["size"] == 2
    assert groups[0]["claims_covered"] == 2
    assert groups[0]["uncovered_claims"] == []


# ==========================================================================
# ROW 4 -- pair, one freeloader. THE ROW THAT MATTERS.
# ==========================================================================
def test_row4_freeloader_is_a_fault_not_cleared_and_not_f6(tmp_path, monkeypatch):
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111"), ("B2", "222")],
        paragraphs=[f"Drug X reduces outcome A and outcome B "
                    f"{_xref('B1', '1')},{_xref('B2', '2')}."],
        claims=[CLAIM_A, CLAIM_B],
        script={"111": [SUPPORTS, SUPPORTS],      # carries the whole sentence
                "222": [OFF_TOPIC, OFF_TOPIC]})   # supports nothing
    clean = items["PMC1000:B1"]
    freeloader = items["PMC1000:B2"]

    # Ref1 established every claim by itself -- FULL_COVERAGE is still the true
    # and more informative statement, and it is the gate into the F3 provenance
    # discriminator, so the group must not downgrade it.
    assert clean["proposed_route"] == jb.ROUTE_FULL_COVERAGE

    # Ref2 is NOT cleared and NOT F6: it supported nothing, so "supports part of
    # the claim" is not even a true description of it.
    assert freeloader["proposed_route"] == cc.ROUTE_UNSUPPORTED_MEMBER
    assert freeloader["proposed_route"] not in (
        jb.ROUTE_F6_FLAGGED, jb.ROUTE_FULL_COVERAGE, cc.ROUTE_GROUP_COVERED)
    assert freeloader["proposed_verdict"] is None
    assert groups[0]["member_routes"]["PMC1000:B2"] == cc.ROUTE_UNSUPPORTED_MEMBER


# ==========================================================================
# ROW 5 -- pair, shared gap. THE ROW THAT MATTERS.
# ==========================================================================
def test_row5_claim_no_member_covered_is_reported(tmp_path, monkeypatch):
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111"), ("B2", "222")],
        paragraphs=[f"Drug X reduces outcome A and outcome B "
                    f"{_xref('B1', '1')},{_xref('B2', '2')}."],
        claims=[CLAIM_A, CLAIM_B],
        script={"111": [SUPPORTS, OFF_TOPIC],
                "222": [SUPPORTS, OFF_TOPIC]})    # nobody covers B
    group = groups[0]
    # B did not vanish because "it's a group".
    assert group["uncovered_claims"] == [CLAIM_B]
    assert group["claims_uncovered"] == 1
    assert group["claims_covered"] == 1
    coverage = {row["claim"]: row for row in group["claim_coverage"]}
    assert coverage[CLAIM_A]["status"] == cc.CLAIM_COVERED
    assert coverage[CLAIM_B]["status"] == cc.CLAIM_UNCOVERED
    assert coverage[CLAIM_B]["judged_by"] == ["PMC1000:B1", "PMC1000:B2"]
    # And every member carries the group's gap rather than being excused.
    for cid in ("PMC1000:B1", "PMC1000:B2"):
        assert items[cid]["proposed_route"] == cc.ROUTE_GROUP_COVERAGE_GAP
        assert items[cid]["cocitation"]["uncovered_claims"] == [CLAIM_B]


# ==========================================================================
# ROW 6 -- group of 8, an unsupported numeric specific. THE ROW THAT MATTERS.
# ==========================================================================
def test_row6_unsupported_specific_survives_as_a_group_finding(tmp_path,
                                                               monkeypatch):
    """The classic review-article failure: eight references all support the
    general direction and none states the number."""
    refs = [(f"B{i}", f"{i}00") for i in range(1, 9)]
    markers = ",".join(_xref(f"B{i}", str(i)) for i in range(1, 9))
    claim_general = "Drug X reduces mortality"
    claim_specific = "Drug X reduces mortality by 34%"
    _m, items, groups = _band(
        tmp_path, monkeypatch, refs=refs,
        paragraphs=[f"Drug X reduces mortality by 34% {markers}."],
        claims=[claim_general, claim_specific],
        script={pmid: [SUPPORTS, UNCONFIRMED] for _rid, pmid in refs})
    group = groups[0]
    assert group["size"] == 8
    assert group["uncovered_claims"] == [claim_specific]
    assert group["claims_uncovered"] == 1
    for rid, _pmid in refs:
        row = items[f"PMC1000:{rid}"]
        assert row["proposed_route"] == cc.ROUTE_GROUP_COVERAGE_GAP
        assert row["proposed_route"] != jb.ROUTE_FULL_COVERAGE   # never cleared


# ==========================================================================
# ROW 7 -- overlapping ranges in one sentence: one group, deduplicated.
# ==========================================================================
def test_row7_overlapping_ranges_dedupe_into_one_group(tmp_path):
    refs = [(f"B{i}", f"{i}00") for i in range(1, 10)]
    rng = _xref(" ".join(f"B{i}" for i in range(1, 9)), "1-8")
    pair = _xref("B3 B9", "3, 9")                 # B3 appears a SECOND time
    parsed = _parse(tmp_path, refs,
                    [f"Collective claim {rng} and {pair}."])
    by_id = {r.citation_id: r for r in parsed}
    b3 = by_id["PMC1000:B3"]
    assert len(b3.citance_group_members) == 9            # not 10
    assert len(set(b3.citance_group_members)) == 9       # no double-judging
    assert b3.citance_group_members.count("PMC1000:B3") == 1
    # One group: every member names the same id.
    assert len({by_id[f"PMC1000:B{i}"].citance_group_id
                for i in range(1, 10)}) == 1


# ==========================================================================
# ROW 8 -- the same reference cited in two sentences. First citance wins.
# ==========================================================================
def test_row8_same_ref_two_sentences_is_judged_per_citance(tmp_path):
    """``link_citances`` is first-citance-wins (a pre-existing limitation), and
    grouping now interacts with it: ref 5 takes sentence X, which comes first in
    document order, and belongs to X's group ONLY. Sentence Y's group is
    {4, 6, 7} WITHOUT ref 5 -- including it would aggregate coverage verdicts
    about sentence Y's claims with a member judged on sentence X's."""
    refs = [(f"B{i}", f"{i}00") for i in (4, 5, 6, 7)]
    parsed = _parse(tmp_path, refs, [
        f"Sentence X about ref five alone {_xref('B5', '5')}.",
        f"Sentence Y about the range {_xref('B4 B5 B6 B7', '4-7')}.",
    ])
    by_id = {r.citation_id: r for r in parsed}
    b5 = by_id["PMC1000:B5"]
    assert b5.citance.startswith("Sentence X")           # first citance won
    assert b5.citance_group_members == ["PMC1000:B5"]    # a group of one
    for rid in ("B4", "B6", "B7"):
        ref = by_id[f"PMC1000:{rid}"]
        assert ref.citance.startswith("Sentence Y")
        assert ref.citance_group_members == [
            "PMC1000:B4", "PMC1000:B6", "PMC1000:B7"]
        assert "PMC1000:B5" not in ref.citance_group_members
    assert b5.citance_group_id != by_id["PMC1000:B4"].citance_group_id


# ==========================================================================
# ROW 9 -- marker format sweep. A missed form silently reverts to the old bug.
# ==========================================================================
@pytest.mark.parametrize("marker_text,wrapper", [
    ("1-8", "{}"),                 # hyphen range
    ("1–8", "{}"),            # en-dash range
    ("1, 8", "{}"),                # comma list
    ("1-8", "[{}]"),               # bracketed
    ("1-8", "({})"),               # parenthesised
    ("1,3-5,9", "{}"),             # mixed list and ranges
])
def test_row9_every_marker_format_parses_to_the_same_member_set(
        tmp_path, marker_text, wrapper):
    """Grouping keys on the SENTENCE OCCURRENCE and the rids the markers resolve
    to -- it never parses marker text -- so every surface form yields the same
    member set by construction. That is the property being pinned: a new marker
    style cannot silently revert a document to per-reference judging."""
    refs = [(f"B{i}", f"{i}00") for i in range(1, 9)]
    rids = " ".join(f"B{i}" for i in range(1, 9))
    marker = wrapper.format(_xref(rids, marker_text))
    parsed = _parse(tmp_path, refs, [f"A collectively cited claim {marker}."])
    groups = {r.citance_group_id for r in parsed if r.citance}
    assert len(groups) == 1
    expected = [f"PMC1000:B{i}" for i in range(1, 9)]
    for ref in parsed:
        assert ref.citance_group_members == expected


def test_row9_split_range_markers_group_by_sentence_not_by_text(tmp_path):
    """The same sweep where the publisher emits one xref PER reference rather
    than one multi-rid xref. Grouping is by sentence, so the answer is identical."""
    refs = [(f"B{i}", f"{i}00") for i in range(1, 9)]
    markers = "-".join(_xref(f"B{i}", str(i)) for i in (1, 8))
    middles = "".join(_xref(f"B{i}", str(i)) for i in range(2, 8))
    parsed = _parse(tmp_path, refs,
                    [f"A collectively cited claim {markers}{middles}."])
    assert len({r.citance_group_id for r in parsed}) == 1
    assert all(len(r.citance_group_members) == 8 for r in parsed)


# ==========================================================================
# ROW 9b -- RANGE EXPANSION: rendered ranges whose interior is never linked.
#
# "9-13" is normally marked up as an xref on 9 and an xref on 13 with a literal
# dash between them; 10, 11 and 12 are cited on the page and carry no link.
# Measured over corpus_frozen_v1: 63 rendered ranges, ALL with unlinked
# interiors, affecting 115 references -- 90 with no citance at all and 23
# silently taking a LATER sentence's citance. Expansion recovers them, but only
# where it is provably safe, and always marks a deduction as a deduction.
# ==========================================================================
def _numbered(n):
    """n references, ids B1..Bn, so bibliography ordinal i == B{i}."""
    return [(f"B{i}", f"{i}00") for i in range(1, n + 1)]


def _dash_range(lo, hi, dash="-"):
    """The two-xref rendering of "lo-hi" -- interior numbers unlinked."""
    return (_xref(f"B{lo}", str(lo)) + dash + _xref(f"B{hi}", str(hi)))


def test_range_expansion_recovers_the_unlinked_interior(tmp_path):
    parsed = _parse(tmp_path, _numbered(5),
                    [f"A collectively cited claim {_dash_range(1, 5)}."])
    by_id = {r.citation_id: r for r in parsed}
    expected = [f"PMC1000:B{i}" for i in range(1, 6)]
    for i in range(1, 6):
        ref = by_id[f"PMC1000:B{i}"]
        assert ref.citance.startswith("A collectively cited claim")
        assert ref.citance_group_members == expected      # size 5, not 2
    assert len({r.citance_group_id for r in parsed}) == 1


def test_inferred_members_are_marked_as_inferred_not_asserted(tmp_path):
    """The whole point: a reader can tell a deduction from a publisher's link,
    and both counts are reportable."""
    parsed = _parse(tmp_path, _numbered(5),
                    [f"A collectively cited claim {_dash_range(1, 5)}."])
    by_id = {r.citation_id: r for r in parsed}
    inferred = [f"PMC1000:B{i}" for i in (2, 3, 4)]
    for cid, ref in by_id.items():
        assert ref.citance_marker_inferred is (cid in inferred)
        assert ref.citance_group_inferred_members == inferred
    # Endpoints keep the marker the publisher wrote; interiors get the number
    # the article renders, flagged as inferred.
    assert by_id["PMC1000:B1"].cited_reference_marker == "1"
    assert by_id["PMC1000:B3"].cited_reference_marker == "3"
    # ...and the group record separates the two counts.
    items = [jb.build_item(r) for r in parsed]
    for it in items:
        it["atomic_claims"] = [CLAIM_A]
        it["coverage_verdicts"] = [SUPPORTS]
        it["proposed_route"] = jb.ROUTE_FULL_COVERAGE
    groups, _counts, _stats = jb.apply_cocitation_routing(items)
    assert groups[0]["size"] == 5
    assert groups[0]["asserted_size"] == 2
    assert groups[0]["inferred_members"] == inferred


def test_expansion_needs_a_dash_a_comma_list_is_not_a_range(tmp_path):
    """"1, 5" cites two references. Reading it as a range would fabricate three."""
    parsed = _parse(tmp_path, _numbered(5),
                    [f"Two references {_xref('B1', '1')}, {_xref('B5', '5')}."])
    by_id = {r.citation_id: r for r in parsed}
    assert by_id["PMC1000:B1"].citance_group_members == [
        "PMC1000:B1", "PMC1000:B5"]
    assert not by_id["PMC1000:B3"].citance          # never claimed
    assert not any(r.citance_marker_inferred for r in parsed)


def test_expansion_accepts_an_en_dash(tmp_path):
    parsed = _parse(tmp_path, _numbered(4),
                    [f"A claim {_dash_range(1, 4, dash='–')}."])
    by_id = {r.citation_id: r for r in parsed}
    assert len(by_id["PMC1000:B1"].citance_group_members) == 4
    assert by_id["PMC1000:B2"].citance_marker_inferred is True


def test_adjacent_endpoints_have_no_interior_to_infer(tmp_path):
    parsed = _parse(tmp_path, _numbered(3), [f"A claim {_dash_range(1, 2)}."])
    by_id = {r.citation_id: r for r in parsed}
    assert by_id["PMC1000:B1"].citance_group_members == [
        "PMC1000:B1", "PMC1000:B2"]
    assert not any(r.citance_marker_inferred for r in parsed)


def test_non_positional_numbering_disables_expansion_article_wide(tmp_path):
    """One marker disagreeing with its bibliography ordinal means the ordinal
    model is wrong, and a model that is wrong anywhere is not trusted anywhere."""
    refs = _numbered(6)
    parsed = _parse(tmp_path, refs, [
        # "2" links B3 -- the article does not number positionally.
        f"A mismatched marker {_xref('B3', '2')}.",
        f"A range that would otherwise expand {_dash_range(1, 5)}.",
    ])
    by_id = {r.citation_id: r for r in parsed}
    assert not any(r.citance_marker_inferred for r in parsed)
    assert by_id["PMC1000:B1"].citance_group_members == [
        "PMC1000:B1", "PMC1000:B5"]                  # endpoints only


def test_an_explicit_link_beats_an_inference(tmp_path):
    """B3 is linked in the same sentence, so it is asserted, not deduced."""
    parsed = _parse(tmp_path, _numbered(5), [
        f"A claim {_dash_range(1, 5)} and also {_xref('B3', '3')}."])
    by_id = {r.citation_id: r for r in parsed}
    assert by_id["PMC1000:B3"].citance_marker_inferred is False
    assert by_id["PMC1000:B1"].citance_group_inferred_members == [
        "PMC1000:B2", "PMC1000:B4"]
    assert len(by_id["PMC1000:B1"].citance_group_members) == 5


def test_expansion_respects_first_citance_wins(tmp_path):
    """An interior already carrying an earlier sentence keeps it -- expansion
    adds members, it never steals them."""
    parsed = _parse(tmp_path, _numbered(5), [
        f"Sentence X cites three alone {_xref('B3', '3')}.",
        f"Sentence Y renders a range {_dash_range(1, 5)}.",
    ])
    by_id = {r.citation_id: r for r in parsed}
    b3 = by_id["PMC1000:B3"]
    assert b3.citance.startswith("Sentence X")
    assert b3.citance_marker_inferred is False
    assert b3.citance_group_members == ["PMC1000:B3"]
    # Y's group gets the rest, without B3.
    y = by_id["PMC1000:B1"].citance_group_members
    assert y == ["PMC1000:B1", "PMC1000:B2", "PMC1000:B4", "PMC1000:B5"]
    assert "PMC1000:B3" not in y


def test_a_range_with_an_unresolvable_interior_is_refused_whole():
    """Never partially expanded: asserting some members of a rendered range
    while silently dropping others is worse than expanding none, because the
    reader cannot see what was left out."""
    from cre.f1.parser import _inferred_interior
    text = "A claim 1-5."
    entries = [(8, ["B1"], "1"), (10, ["B5"], "5")]
    full = {1: "B1", 2: "B2", 3: "B3", 4: "B4", 5: "B5"}
    holed = {**full, 3: None}
    refs_by_id = {k: object() for k in ("B1", "B2", "B3", "B4", "B5")}
    assert _inferred_interior(text, entries, full, refs_by_id) == [
        (8, 2, "B2"), (8, 3, "B3"), (8, 4, "B4")]
    assert _inferred_interior(text, entries, holed, refs_by_id) == []
    # ...and an interior the parser never built a Reference for is the same case.
    assert _inferred_interior(text, entries, full,
                              {"B1": 1, "B2": 2, "B5": 5}) == []


def test_multi_rid_range_needs_no_inference(tmp_path):
    """A publisher who links the whole range in one xref already asserts every
    member; nothing is deduced."""
    rids = " ".join(f"B{i}" for i in range(1, 6))
    parsed = _parse(tmp_path, _numbered(5),
                    [f"A claim {_xref(rids, '1-5')}."])
    by_id = {r.citation_id: r for r in parsed}
    assert len(by_id["PMC1000:B1"].citance_group_members) == 5
    assert not any(r.citance_marker_inferred for r in parsed)


def test_expansion_brings_dropped_references_into_the_band(tmp_path):
    """The measured consequence: 90 of the 115 affected references had NO
    citance at all, so the band never saw them."""
    parsed = _parse(tmp_path, _numbered(6),
                    [f"A claim {_dash_range(1, 6)}."])
    assert all(jb.exclusion_reason(r) is None for r in parsed)
    by_id = {r.citation_id: r for r in parsed}
    assert sum(r.citance_marker_inferred for r in parsed) == 4


# ==========================================================================
# ROW 10 -- a "range" resolving to one reference behaves as a singleton.
# ==========================================================================
def test_row10_group_of_one_has_no_path_divergence(tmp_path, monkeypatch):
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111")],
        # A range marker whose rid list resolves to exactly one reference.
        paragraphs=[f"Drug X reduces outcome A and outcome B "
                    f"{_xref('B1', '1-1')}."],
        claims=[CLAIM_A, CLAIM_B], script={"111": [SUPPORTS, OFF_TOPIC]})
    row = items["PMC1000:B1"]
    assert row["citance_group_id"]                       # it HAS a group id...
    assert row["citance_group_members"] == ["PMC1000:B1"]
    # ...and behaves exactly like row 2's singleton.
    assert row["proposed_route"] == jb.ROUTE_F6_FLAGGED
    assert "proposed_route_solo" not in row
    assert "cocitation" not in row
    assert groups == []


# ==========================================================================
# ROW 11 -- a member whose abstract cannot be fetched.
# ==========================================================================
def test_row11_unfetchable_member_is_neither_covered_nor_uncovered(tmp_path,
                                                                   monkeypatch):
    refs = [(f"B{i}", f"{i}00") for i in range(1, 7)]
    markers = ",".join(_xref(f"B{i}", str(i)) for i in range(1, 7))
    script = {pmid: [SUPPORTS, SUPPORTS] for _rid, pmid in refs}
    script["400"] = [UNJUDGED, UNJUDGED]              # ref 4 of 6: no abstract
    _m, items, groups = _band(
        tmp_path, monkeypatch, refs=refs,
        paragraphs=[f"Drug X reduces outcome A and outcome B {markers}."],
        claims=[CLAIM_A, CLAIM_B], script=script, missing_abstract={"400"})
    group = groups[0]
    assert group["size"] == 6
    # Judged on the five available; the missing one is in NEITHER tally.
    for row in group["claim_coverage"]:
        assert row["status"] == cc.CLAIM_COVERED
        assert "PMC1000:B4" not in row["covered_by"]
        assert "PMC1000:B4" not in row["judged_by"]
        assert len(row["judged_by"]) == 5
    assert group["claims_uncovered"] == 0
    # The unfetchable member keeps its operational hold -- not a freeloader.
    missing = items["PMC1000:B4"]
    assert missing["proposed_route"] == jb.ROUTE_HELD
    assert missing["proposed_route"] != cc.ROUTE_UNSUPPORTED_MEMBER


# ==========================================================================
# ROW 12 -- F4 (overstatement) still fires inside a group.
# ==========================================================================
def _decide(support_states, *, entities=(), cogroup_covered=()):
    claims = [CLAIM_A, CLAIM_B][:len(support_states)]
    support = tuple(ClaimSupport(i, s) for i, s in enumerate(support_states))
    return decide_judgment(
        preband_cleared=True, claims=claims, claim_support=support,
        entity_assessments=entities, provenance=None,
        temporal=TemporalAssessment(TemporalState.NO_QUALIFYING_CONTRADICTION),
        cogroup_covered=cogroup_covered)


def test_row12_f4_still_fires_on_a_group_member():
    """Ref2 of a pair shows correlation where the sentence claims causation, and
    is silent on the claim its sibling covers. The silence is excused; the
    OVERSTATEMENT is not -- F4 is a per-reference property."""
    decision = _decide(
        [SupportState.WEAKER_STRENGTH, SupportState.UNESTABLISHED],
        cogroup_covered=(False, True))
    assert "F4" in decision.findings
    assert "F6" not in decision.findings          # the sibling covered claim B
    assert decision.primary_label == "F4"


def test_row12_f4_is_untouched_by_the_overlay():
    for covered in ((False, False), (True, True)):
        decision = _decide(
            [SupportState.WEAKER_STRENGTH, SupportState.SUPPORTED],
            cogroup_covered=covered)
        assert "F4" in decision.findings


# ==========================================================================
# ROW 13 -- F7 (wrong entity) still fires inside a group.
# ==========================================================================
def test_row13_f7_still_fires_on_a_group_member():
    """Ref3 of four is about a different drug. Entity is per-reference: no
    sibling's coverage says anything about it, and F7 outranks everything."""
    entities = (EntityAssessment(0, EntityState.DIFFERENT_ENTITY_SUPPORTED,
                                 claimed_entity_key="DRUG:X",
                                 evidence_entity_key="DRUG:Y",
                                 relation_supported=True,
                                 rationale="drug Y, not drug X"),)
    decision = _decide(
        [SupportState.UNESTABLISHED, SupportState.UNESTABLISHED],
        entities=entities, cogroup_covered=(True, True))
    assert "F7" in decision.findings
    assert "F6" not in decision.findings
    assert decision.primary_label == "F7"


# ==========================================================================
# ROW 14 -- every member off-topic. THE ROW THAT MATTERS.
# ==========================================================================
def test_row14_all_members_off_topic_is_a_fault_on_every_member(tmp_path,
                                                                monkeypatch):
    refs = [(f"B{i}", f"{i}00") for i in range(1, 5)]
    markers = ",".join(_xref(f"B{i}", str(i)) for i in range(1, 5))
    _m, items, groups = _band(
        tmp_path, monkeypatch, refs=refs,
        paragraphs=[f"Drug X reduces outcome A {markers}."],
        claims=[CLAIM_A],
        script={pmid: [OFF_TOPIC] for _rid, pmid in refs})
    group = groups[0]
    # Not averaged into "partially covered": the claim is uncovered, full stop.
    assert group["claims_covered"] == 0
    assert group["uncovered_claims"] == [CLAIM_A]
    for rid, _pmid in refs:
        row = items[f"PMC1000:{rid}"]
        assert row["proposed_route"] == cc.ROUTE_UNSUPPORTED_MEMBER
        assert row["proposed_route"] != jb.ROUTE_FULL_COVERAGE
        assert row["proposed_route"] != cc.ROUTE_GROUP_COVERED


# ==========================================================================
# Contradiction is per reference and survives grouping
# ==========================================================================
def test_contradicting_member_is_still_f6_however_many_siblings(tmp_path,
                                                                monkeypatch):
    """A sibling covering the claim says nothing about THIS paper's counter-
    evidence. Without this, co-citation would launder a genuine contradiction."""
    _m, items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111"), ("B2", "222")],
        paragraphs=[f"Drug X reduces outcome A "
                    f"{_xref('B1', '1')},{_xref('B2', '2')}."],
        claims=[CLAIM_A],
        script={"111": [SUPPORTS], "222": [CONTRADICTS]})
    assert items["PMC1000:B1"]["proposed_route"] == jb.ROUTE_FULL_COVERAGE
    assert items["PMC1000:B2"]["proposed_route"] == jb.ROUTE_F6_FLAGGED
    assert items["PMC1000:B2"]["proposed_verdict"] == "F6"
    assert groups[0]["claim_coverage"][0]["contradicted_by"] == ["PMC1000:B2"]


# ==========================================================================
# The manifest: group membership and BOTH candidate denominators
# ==========================================================================
def test_manifest_publishes_both_denominators_and_group_membership(tmp_path,
                                                                   monkeypatch):
    refs = [("B1", "111"), ("B2", "222"), ("B3", "333")]
    manifest, _items, groups = _band(
        tmp_path, monkeypatch, refs=refs,
        paragraphs=[
            f"Drug X reduces outcome A and outcome B "
            f"{_xref('B1', '1')},{_xref('B2', '2')}.",
            f"A separate solo claim {_xref('B3', '3')}.",
        ],
        claims=[CLAIM_A, CLAIM_B],
        script={"111": [SUPPORTS, OFF_TOPIC], "222": [OFF_TOPIC, SUPPORTS],
                "333": [SUPPORTS, SUPPORTS]})
    block = manifest["cocitation"]
    # The unit of analysis is now ambiguous, and BOTH candidates are published
    # rather than one being silently adopted.
    assert block["denominator_per_citation"] == 3        # one row per reference
    assert block["denominator_per_citation_group"] == 2  # one row per sentence
    assert block["cocitation_groups"] == 1
    assert block["members_in_cocitation_groups"] == 2
    assert block["group_size_distribution"] == {"1": 1, "2": 1}
    assert block["group_claims_covered"] == 2
    assert block["group_claims_uncovered"] == 0
    # Group membership is on disk, per group and per item.
    assert len(groups) == 1
    assert groups[0]["members"] == ["PMC1000:B1", "PMC1000:B2"]


def test_a_document_with_no_cocitation_adds_no_manifest_counters(tmp_path,
                                                                 monkeypatch):
    """The counts key set is the opt-in guarantee other tests pin; a group route
    counter must appear only when that route actually fires."""
    manifest, _items, groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111")],
        paragraphs=[f"Drug X reduces outcome A {_xref('B1', '1')}."],
        claims=[CLAIM_A], script={"111": [SUPPORTS]})
    for route_name in (cc.ROUTE_GROUP_COVERED, cc.ROUTE_GROUP_COVERAGE_GAP,
                       cc.ROUTE_UNSUPPORTED_MEMBER):
        assert route_name not in manifest["counts"]
    assert groups == []


# ==========================================================================
# The engine overlay: contract and the "never a silent clear" guarantee
# ==========================================================================
def test_overlay_defaults_to_empty_and_changes_nothing():
    decision = _decide([SupportState.UNESTABLISHED, SupportState.SUPPORTED])
    assert "F6" in decision.findings


def test_group_covered_claim_holds_rather_than_clears():
    """Not F6, and NOT a clear either: this reference did not establish the
    claim, the group did, so the pair is held for human adjudication."""
    decision = _decide([SupportState.SUPPORTED, SupportState.UNESTABLISHED],
                       cogroup_covered=(False, True))
    assert "F6" not in decision.findings
    assert decision.primary_label is None
    assert "claim coverage attributed to a co-cited reference" in \
        decision.hold_reasons


def test_a_gap_no_sibling_covered_still_raises_f6():
    decision = _decide([SupportState.UNESTABLISHED, SupportState.UNESTABLISHED],
                       cogroup_covered=(False, True))
    assert "F6" in decision.findings          # claim A is nobody's coverage


@pytest.mark.parametrize("bad", [(True,), (1, 0), ("yes", "no"), (True, None)])
def test_overlay_rejects_a_malformed_flag_vector(bad):
    """A partial or loosely-typed overlay would excuse the WRONG claim, and an F6
    that disappears for the wrong reason is worse than the false positive."""
    with pytest.raises(DiscriminatorContractError):
        _decide([SupportState.UNESTABLISHED, SupportState.UNESTABLISHED],
                cogroup_covered=bad)


# ==========================================================================
# The orchestrator: a co-cited pair holds instead of being predicted F6
# ==========================================================================
def test_orchestrator_holds_a_cocitation_covered_pair(tmp_path, monkeypatch):
    refs = [("B1", "111"), ("B2", "222")]
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC1000.xml").write_text(
        _article(refs, [f"Drug X reduces outcome A and outcome B "
                        f"{_xref('B1', '1')},{_xref('B2', '2')}."]),
        encoding="utf-8")

    def coverage_judge(claims, evidence):
        rows = ({"111": [SUPPORTS, OFF_TOPIC], "222": [OFF_TOPIC, SUPPORTS]}
                [evidence["cited_pmid"]])
        return rows[:len(claims)]

    out = tmp_path / "out"
    manifest = jr.run_natural_judgment(
        str(xml_dir), str(out),
        extractor=lambda _s: [CLAIM_A, CLAIM_B],
        coverage_judge=coverage_judge,
        fetch_abstract=lambda pmid: f"Abstract {pmid}.",
        preband_disposition={"PMC1000:B1": "cleared", "PMC1000:B2": "cleared"})
    rows = {json.loads(line)["citation_id"]: json.loads(line) for line in
            (out / "judgment_predictions.jsonl").read_text().splitlines()}
    for cid in ("PMC1000:B1", "PMC1000:B2"):
        rec = rows[cid]
        assert rec["disposition"] == jr.DISP_HELD_COCITATION_COVERED
        assert rec["label"] is None                 # never predicted F6
        assert rec["citance_group_members"] == ["PMC1000:B1", "PMC1000:B2"]
        assert rec["cocitation"]["size"] == 2
    # Held is still SCOREABLE: it stays in the annotation queue, never dropped.
    queue = (out / "judgment_band_annotation_queue.jsonl").read_text().splitlines()
    assert len(queue) == 2
    assert manifest["cocitation"]["denominator_per_citation"] == 2
    assert manifest["cocitation"]["denominator_per_citation_group"] == 1
    assert manifest["cocitation"]["held_cocitation_covered"] == 2
    groups = [json.loads(line) for line in
              (out / "judgment_run_cocitation_groups.jsonl").read_text().splitlines()]
    assert groups[0]["members"] == ["PMC1000:B1", "PMC1000:B2"]


def test_orchestrator_still_predicts_f6_when_no_sibling_covers(tmp_path,
                                                               monkeypatch):
    refs = [("B1", "111"), ("B2", "222")]
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC1000.xml").write_text(
        _article(refs, [f"Drug X reduces outcome A and outcome B "
                        f"{_xref('B1', '1')},{_xref('B2', '2')}."]),
        encoding="utf-8")
    out = tmp_path / "out"
    jr.run_natural_judgment(
        str(xml_dir), str(out),
        extractor=lambda _s: [CLAIM_A, CLAIM_B],
        coverage_judge=lambda claims, _ev: [SUPPORTS, OFF_TOPIC][:len(claims)],
        fetch_abstract=lambda pmid: f"Abstract {pmid}.",
        preband_disposition={"PMC1000:B1": "cleared", "PMC1000:B2": "cleared"})
    rows = [json.loads(line) for line in
            (out / "judgment_predictions.jsonl").read_text().splitlines()]
    # Nobody covered claim B, so the F6 assertion stands on both members.
    assert {r["label"] for r in rows} == {"F6"}
    assert {r["disposition"] for r in rows} == {jr.DISP_PREDICTED}


# ==========================================================================
# Guardrails
# ==========================================================================
def test_citation_id_is_unchanged_and_the_group_does_not_replace_it(tmp_path):
    parsed = _parse(tmp_path, [("B1", "111"), ("B2", "222")],
                    [f"A claim {_xref('B1 B2', '1,2')}."])
    for ref in parsed:
        assert ref.citation_id.startswith("PMC1000:")
        assert ref.citation_id in ref.citance_group_members
        assert ref.citance_group_id != ref.citation_id
        item = jb.build_item(ref)
        assert item["item_key"] == item["citation_id"] == ref.citation_id


def test_a_reference_built_outside_the_parser_is_a_singleton():
    """An empty group id must read as "no group known" and take the pre-group
    path -- grouping can never be acquired by accident."""
    from .schema import ClaimedRef, Reference
    ref = Reference(citation_id="PMC1:R1", citance="A claim [1].",
                    claimed=ClaimedRef(claimed_pmid="1", title="T"),
                    source_pmcid="PMC1")
    item = jb.build_item(ref)
    assert item["citance_group_id"] == ""
    assert item["citance_group_members"] == []
    assert cc.group_id_of(item) == ""
    item["proposed_route"] = jb.ROUTE_F6_FLAGGED
    item["atomic_claims"] = [CLAIM_A]
    item["coverage_verdicts"] = [OFF_TOPIC]
    groups, counts, stats = jb.apply_cocitation_routing([item])
    assert groups == []
    assert item["proposed_route"] == jb.ROUTE_F6_FLAGGED
    assert counts == {jb.ROUTE_F6_FLAGGED: 1}
    assert stats["cocitation_groups"] == 0


def test_quarantined_member_neither_covers_nor_is_excused():
    """A row whose model output failed strict parsing carries no trustworthy
    verdicts: it cannot cover a claim for a sibling, and a sibling cannot excuse
    it."""
    good = {"citation_id": "PMC1:R1", "citing_pmcid": "PMC1",
            "citing_sentence": "s", "citance_group_id": "PMC1:g01",
            "atomic_claims": [CLAIM_A], "coverage_verdicts": [OFF_TOPIC],
            "proposed_route": jb.ROUTE_F6_FLAGGED}
    bad = {"citation_id": "PMC1:R2", "citing_pmcid": "PMC1",
           "citing_sentence": "s", "citance_group_id": "PMC1:g01",
           "atomic_claims": [CLAIM_A], "coverage_verdicts": [SUPPORTS],
           "proposed_route": jb.ROUTE_PARSE_QUARANTINE}
    groups, _counts, _stats = jb.apply_cocitation_routing([good, bad])
    # The quarantined row's SUPPORTS did not cover the claim for its sibling...
    assert groups == []                       # the group is a singleton of `good`
    assert good["proposed_route"] == jb.ROUTE_F6_FLAGGED
    # ...and the quarantine route is untouched.
    assert bad["proposed_route"] == jb.ROUTE_PARSE_QUARANTINE


def test_a_bucket_list_that_does_not_line_up_takes_the_unjudged_path():
    """``aggregate`` pads a short member out with None (unjudged), so
    ``member_route`` must read a misaligned list the same way. Otherwise a
    truncated list -- carrying no None to see -- would skip rule 3 and be routed
    on a partial view of its own evidence."""
    aggregated = {"claim_coverage": [
        {"claim": CLAIM_A, "status": cc.CLAIM_COVERED, "covered_by": ["x"],
         "contradicted_by": [], "judged_by": ["x"]},
        {"claim": CLAIM_B, "status": cc.CLAIM_COVERED, "covered_by": ["x"],
         "contradicted_by": [], "judged_by": ["x"]},
    ]}
    aligned = [cc.BUCKET_OFF_TOPIC, cc.BUCKET_ESTABLISHED]
    assert cc.member_route(buckets=aligned, solo_route=jb.ROUTE_F6_FLAGGED,
                           aggregated=aggregated, group_size=2) == \
        cc.ROUTE_GROUP_COVERED
    for misaligned in ([cc.BUCKET_OFF_TOPIC],                       # short
                       aligned + [cc.BUCKET_ESTABLISHED]):          # long
        assert cc.member_route(buckets=misaligned,
                               solo_route=jb.ROUTE_F6_FLAGGED,
                               aggregated=aggregated,
                               group_size=2) == jb.ROUTE_F6_FLAGGED


def test_an_excluded_member_keeps_its_solo_route():
    """Two members of one sentence can extract DIFFERENT claim lists of the SAME
    length -- one extractor call per reference, and the model is not
    deterministic -- so the rule-3 length guard cannot see it. ``aggregate``
    excludes such a member, and ``member_route`` must not then route it on
    statuses computed without it: that would judge it against claims it was never
    asked about."""
    covered = {"citation_id": "PMC1:R1", "citing_pmcid": "PMC1",
               "citing_sentence": "s", "citance_group_id": "PMC1:g01",
               "atomic_claims": [CLAIM_A], "coverage_verdicts": [SUPPORTS],
               "proposed_route": jb.ROUTE_FULL_COVERAGE}
    divergent = {"citation_id": "PMC1:R2", "citing_pmcid": "PMC1",
                 "citing_sentence": "s", "citance_group_id": "PMC1:g01",
                 # Same LENGTH, different claim -- the extractor disagreed.
                 "atomic_claims": ["A differently worded claim"],
                 "coverage_verdicts": [OFF_TOPIC],
                 "proposed_route": jb.ROUTE_F6_FLAGGED}
    groups, _counts, _stats = jb.apply_cocitation_routing([covered, divergent])
    aggregated = groups[0]
    assert aggregated["contributing_members"] == ["PMC1:R1"]
    assert aggregated["excluded_members"] == [
        {"citation_id": "PMC1:R2", "reason": cc.EXCLUDED_CLAIMS_DIFFER}]
    # Excluded -> untouched. Without the guard it would have read the group's
    # "CLAIM_A is covered" and become UNSUPPORTED_MEMBER on a claim it never saw.
    assert divergent["proposed_route"] == jb.ROUTE_F6_FLAGGED
    assert "proposed_route_solo" not in divergent
    assert covered["proposed_route"] == jb.ROUTE_FULL_COVERAGE


def test_the_bucket_vocabulary_has_one_source_of_truth():
    assert jb.COVERAGE_ESTABLISHED == cc.BUCKET_ESTABLISHED
    assert jb.COVERAGE_CONTRADICTED == cc.BUCKET_CONTRADICTED
    assert jb.COVERAGE_UNCONFIRMED_SPECIFIC == cc.BUCKET_UNCONFIRMED_SPECIFIC
    assert jb.COVERAGE_OFF_TOPIC == cc.BUCKET_OFF_TOPIC
