"""The F6 MARKER ATTRIBUTION acceptance matrix.

THE DEFECT. ``parser.link_citances`` builds one co-citation group per SENTENCE
OCCURRENCE, and the band asks every member of that group about every atomic
claim in the whole sentence -- including claims whose citation markers point at a
different reference.

Measured on the ``PMC13294812`` adjudication packet (2026-08-16, claude-opus-5,
effort high, full text, tri-state aggregation): of 53 flagged claims over 20
flagged references, 17 (32%) were flagged "the retrieved text never addresses
this claim", 16 of them on a co-cited reference, and all 17 emitted NO evidence
span at all. An adjudicator cannot audit those rows -- there is nothing to point
at.

Packet row 11 is the clean case, and it is reproduced verbatim below: ``B55``
(*PET imaging of occult tumours ... pH-sensitive 64Cu-labelled polymers*) flagged
F6 on "fluorophore-labelled antibodies were successfully clinically translated".
The verdict was correct. The QUESTION was wrong -- ``B55`` was cited for micelles.

THE ROWS THAT MATTER are the fail-closed ones. A naive "split on every comma and
narrow" passes the happy path and fails all of these, each of which asserts the
specific thing a careless narrowing would lose:

  * SINGLE CLUSTER  -- one cluster is byte-identically today. The load-bearing
                       regression guard: most sentences have one cluster.
  * AUTHOR-YEAR     -- the positional rule is undefined there and must not run.
  * AMBIGUOUS       -- a claim spanning two clusters reverts the WHOLE sentence.
  * NOT_ASKED       -- a question never put must never read as an answer failed.
  * CROSS-CLUSTER   -- a sibling in ANOTHER clause must not cover this clause's
                       claim, or narrowing the question silently widens the alibi.

No network and no model: the extractor and coverage judge are stubs, the XML is
fabricated, and the pubtype lookup is monkeypatched on the consumer namespace.
"""
from __future__ import annotations

import json
import re

from . import cocitation as cc
from . import judgment_band as jb
from . import judgment_run as jr
from . import marker_scope as ms
from .test_cocitation_f6 import (OFF_TOPIC, SUPPORTS, UNCONFIRMED, _article,
                                 _parse, _xref)


def _band(tmp_path, monkeypatch, *, refs, paragraphs, claims, script,
          pmcid="PMC1000"):
    """Run the real ``run_band`` over a fabricated article.

    ``script`` maps cited PMID -> {claim text -> verdict}. Keyed on the CLAIM,
    not on its index, which is the whole point: marker attribution changes which
    claims a reference is asked, so a positional script would silently answer the
    wrong question and the test would pass for the wrong reason.

    Returns ``(manifest, items_by_citation_id, group_records)``.
    """
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / f"{pmcid}.xml").write_text(
        _article(refs, paragraphs, pmcid), encoding="utf-8")
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: ["Journal Article"])
    out = tmp_path / "out"

    def coverage_judge(claim_list, evidence):
        rows = script.get(evidence["cited_pmid"], {})
        return [rows[claim] for claim in claim_list]

    manifest = jb.run_band(
        str(xml_dir), str(out), extractor=lambda _s: list(claims),
        coverage_judge=coverage_judge,
        fetch_abstract=lambda pmid: f"Abstract for {pmid}.", session=object())
    items = {}
    for line in (out / "judgment_band_items.jsonl").read_text().splitlines():
        row = json.loads(line)
        items[row["citation_id"]] = row
    groups_file = out / "judgment_band_cocitation_groups.jsonl"
    groups = [json.loads(line) for line in
              groups_file.read_text().splitlines()] if groups_file.exists() else []
    return manifest, items, groups


def _covers(*claims) -> dict:
    """A per-reference script: these claims supported, every other one off-topic."""
    return {claim: (SUPPORTS if claim in claims else OFF_TOPIC)
            for claim in PACKET_CLAIMS}


# ==========================================================================
# The packet's own sentence, and a claim list of the shape extraction returns
# ==========================================================================
PACKET_SENTENCE = (
    "Notably, such detailed cellular characterisations were not necessary for "
    "the successful clinical translation of fluorophore-labelled antibodies "
    f"{_xref('B52', '52')},{_xref('B53', '53')} and pH sensitive fluorescent "
    f"micelles {_xref('B54', '54')},{_xref('B55', '55')} for intraoperative FL "
    "imaging of oral SCC disease.")
PACKET_REFS = [("B52", "552"), ("B53", "553"), ("B54", "554"), ("B55", "555")]

ANTIBODY_CLAIM = ("Fluorophore-labelled antibodies were successfully clinically "
                  "translated for intraoperative FL imaging of oral SCC disease")
ANTIBODY_CLAIM_2 = ("Detailed cellular characterisations were not necessary for "
                    "the clinical translation of fluorophore-labelled antibodies")
MICELLE_CLAIM = ("pH sensitive fluorescent micelles were successfully clinically "
                 "translated for intraoperative FL imaging of oral SCC disease")
MICELLE_CLAIM_2 = ("Detailed cellular characterisations were not necessary for "
                   "the clinical translation of pH sensitive fluorescent micelles")
PACKET_CLAIMS = [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2, MICELLE_CLAIM, MICELLE_CLAIM_2]


def _packet_refs(tmp_path):
    return _parse(tmp_path, PACKET_REFS, [PACKET_SENTENCE], pmcid="PMC13294812")


def _by_id(refs) -> dict:
    return {r.citation_id: r for r in refs}


def _scope(ref, claims=PACKET_CLAIMS) -> dict:
    return ms.scope_item_claims(jb.build_item(ref), claims)


# ==========================================================================
# ROW -- marker positions come from the xref offsets, never a regex
# ==========================================================================
def test_marker_offsets_come_from_xref_positions_not_a_digit_regex(tmp_path):
    """A digit regex over the rendered citance matched years inside author-year
    citations, COVID-19 and "10 + years", and left 226 printed markers
    unlocatable across the corpus. The xref offsets are the only source."""
    refs = _parse(
        tmp_path, [("B1", "111"), ("B2", "222")],
        [f"COVID-19 studies over 10 + years, per Lang, 2024, found an effect "
         f"{_xref('B1', '1')},{_xref('B2', '2')}."])
    ref = _by_id(refs)["PMC1000:B1"]
    # One cluster: every digit in the prose is prose, not a marker.
    assert ref.citance_marker_clusters == []
    # No cluster is RECORDED for a single-cluster sentence, so no cluster is
    # named on the reference either -- the two must never disagree.
    assert ref.citance_marker_cluster_index == -1


def test_cluster_offsets_index_the_citance_string_exactly(tmp_path):
    """The offsets are recorded against the whitespace-collapsed citance -- the
    string the extractor and the judge were given -- not the raw XML text."""
    ref = _by_id(_packet_refs(tmp_path))["PMC13294812:B52"]
    clusters = ref.citance_marker_clusters
    assert [ref.citance[c["offset"]:c["end"]] for c in clusters] == ["52,53",
                                                                    "54,55"]
    assert [c["marker_text"] for c in clusters] == ["52,53", "54,55"]


def test_normalization_map_reproduces_the_citance_transform():
    """``normalize_with_offsets`` must be the SAME transform ``_sentence_for``
    applies, or every offset is silently wrong by the XML's whitespace."""
    for raw in ("  a   b \n c  ", "x", "", "  ", "a\t\tb", "trailing   "):
        text, mapping = ms.normalize_with_offsets(raw)
        assert text == re.sub(r"\s+", " ", raw).strip()
        assert len(mapping) == len(raw)


# ==========================================================================
# ROW -- clusters, and the reference-to-cluster assignment
# ==========================================================================
def test_two_marker_clusters_are_resolved_and_members_split(tmp_path):
    refs = _by_id(_packet_refs(tmp_path))
    assert [refs[f"PMC13294812:B{n}"].citance_marker_cluster_index
            for n in (52, 53, 54, 55)] == [0, 0, 1, 1]
    clusters = refs["PMC13294812:B52"].citance_marker_clusters
    assert clusters[0]["members"] == ["PMC13294812:B52", "PMC13294812:B53"]
    assert clusters[1]["members"] == ["PMC13294812:B54", "PMC13294812:B55"]
    # The cluster id EXTENDS the group id; it does not replace it.
    assert refs["PMC13294812:B55"].citance_group_id == "PMC13294812:g01"
    assert refs["PMC13294812:B55"].citance_marker_cluster_id == "PMC13294812:g01:c01"


def test_citation_id_and_group_id_semantics_are_untouched(tmp_path):
    """Band 1's preband_contract joins on citation_id and the co-citation record
    keys on citance_group_id. Marker clusters are ADDITIONAL provenance."""
    refs = _by_id(_packet_refs(tmp_path))
    for n in (52, 53, 54, 55):
        ref = refs[f"PMC13294812:B{n}"]
        assert ref.citation_id == f"PMC13294812:B{n}"
        assert ref.citance_group_id == "PMC13294812:g01"
        assert ref.citance_group_members == [f"PMC13294812:B{k}"
                                             for k in (52, 53, 54, 55)]


# ==========================================================================
# ROW -- the acceptance table's headline: B55 is asked the micelle claims only
# ==========================================================================
def test_b55_is_judged_against_the_micelle_claims_only(tmp_path):
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B55"])
    assert scope["status"] == ms.SCOPE_SCOPED
    assert scope["claims"] == [MICELLE_CLAIM, MICELLE_CLAIM_2]


def test_b55_emits_no_verdict_on_the_antibody_claim_and_says_not_asked(tmp_path):
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B55"])
    assert ANTIBODY_CLAIM not in scope["claims"]
    skipped = [row for row in scope["attribution"]
               if row["disposition"] == ms.DISPOSITION_NOT_ASKED]
    assert [row["claim"] for row in skipped] == [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2]
    for row in skipped:
        assert row["matched"] is False
        assert row["claim_cluster_index"] == 0
        assert row["reference_cluster_index"] == 1


def test_b52_still_gets_the_antibody_claim_unchanged(tmp_path):
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B52"])
    assert scope["claims"] == [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2]
    asked = [row for row in scope["attribution"] if row["matched"]]
    assert [row["claim"] for row in asked] == [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2]


def test_every_claim_records_both_clusters_and_whether_they_matched(tmp_path):
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B54"])
    assert len(scope["attribution"]) == len(PACKET_CLAIMS)
    for row in scope["attribution"]:
        assert row["reference_cluster_index"] == 1
        assert row["claim_cluster_index"] in (0, 1)
        assert row["matched"] is (row["claim_cluster_index"] == 1)
    assert scope["claims_asked"] == 2
    assert scope["claims_not_asked"] == 2


# ==========================================================================
# ROW -- the load-bearing regression guard: ONE cluster changes nothing
# ==========================================================================
def test_single_cluster_sentence_citing_four_refs_is_unchanged(tmp_path):
    refs = _parse(
        tmp_path, [("B1", "111"), ("B2", "222"), ("B3", "333"), ("B4", "444")],
        ["Drug X reduces outcome A and outcome B "
         f"{_xref('B1', '1')},{_xref('B2', '2')},{_xref('B3', '3')},"
         f"{_xref('B4', '4')}."])
    for ref in refs:
        assert ref.citance_marker_clusters == []       # one cluster: recorded as none
        item = jb.build_item(ref)
        # No marker-scope key reaches the item at all, so its row is byte-identical.
        assert "citance_marker_clusters" not in item
        assert "citance_citation_style" not in item
        scope = ms.scope_item_claims(item, ["claim one", "claim two"])
        assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE
        assert scope["reason"] == ms.REASON_SINGLE_CLUSTER
        assert scope["claims"] == ["claim one", "claim two"]
        assert ms.should_record(scope) is False


def test_a_separated_marker_run_is_one_cluster_not_four(tmp_path):
    """Joining is the fail-closed direction: over-joining reproduces today,
    over-splitting stops asking a reference about a claim it WAS cited for."""
    for rendered in ("{a},{b}", "{a}, {b}", "{a};{b}", "[{a}, {b}]",
                     "{a}-{b}", "{a} and {b}", "{a}{b}"):
        refs = _parse(
            tmp_path, [("B1", "111"), ("B2", "222")],
            ["Drug X reduces outcome A " + rendered.format(
                a=_xref("B1", "1"), b=_xref("B2", "2")) + "."])
        assert refs[0].citance_marker_clusters == [], rendered
        assert refs[0].citance_marker_cluster_index == -1, rendered


# ==========================================================================
# ROW -- author-year documents keep whole-sentence behavior AND say so
# ==========================================================================
AUTHOR_YEAR_SENTENCE = (
    "Antibodies were translated "
    f"{_xref('B1', 'Lang, 2024a')} and micelles were translated "
    f"{_xref('B2', 'Voskuil et al., 2020')}.")


def test_author_year_document_is_never_clustered(tmp_path):
    refs = _parse(tmp_path, [("B1", "111"), ("B2", "222")],
                  [AUTHOR_YEAR_SENTENCE])
    for ref in refs:
        assert ref.citance_citation_style == ms.CITATION_STYLE_AUTHOR_YEAR
        assert ref.citance_marker_clusters == []
        scope = ms.scope_item_claims(jb.build_item(ref), PACKET_CLAIMS)
        assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE
        assert scope["reason"] == ms.REASON_NOT_NUMERIC
        assert scope["claims"] == PACKET_CLAIMS


def test_author_year_record_states_the_style_that_applied(tmp_path):
    """"Nothing was narrowed" and "nothing was tried" must be distinguishable."""
    ref = _parse(tmp_path, [("B1", "111"), ("B2", "222")],
                 [AUTHOR_YEAR_SENTENCE])[0]
    item = jb.build_item(ref)
    assert item["citance_citation_style"] == ms.CITATION_STYLE_AUTHOR_YEAR
    scope = ms.scope_item_claims(item, PACKET_CLAIMS)
    assert ms.should_record(scope) is True
    assert scope["citation_style"] == ms.CITATION_STYLE_AUTHOR_YEAR


def test_one_author_year_marker_refuses_the_whole_document(tmp_path):
    """All-or-nothing per document, exactly like _positional_numbering: a marker
    model that is wrong anywhere is not trusted anywhere."""
    refs = _parse(
        tmp_path, [("B1", "111"), ("B2", "222"), ("B3", "333")],
        ["Antibodies were translated "
         f"{_xref('B1', '1')} and micelles were translated {_xref('B2', '2')}.",
         f"A later sentence cites by name {_xref('B3', 'Lang, 2024a')}."])
    for ref in refs:
        assert ref.citance_citation_style == ms.CITATION_STYLE_AUTHOR_YEAR
        assert ref.citance_marker_clusters == []


# ==========================================================================
# ROW -- ambiguity reverts the WHOLE sentence, and the record says so
# ==========================================================================
def test_a_claim_spanning_two_clusters_reverts_the_whole_sentence(tmp_path):
    spanning = "Both antibodies and micelles were clinically translated"
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B55"],
                   [ANTIBODY_CLAIM, spanning, MICELLE_CLAIM])
    assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE
    assert scope["reason"] == ms.REASON_AMBIGUOUS
    assert scope["claims"] == [ANTIBODY_CLAIM, spanning, MICELLE_CLAIM]
    assert scope["claims_not_asked"] == 0
    assert ms.should_record(scope) is True      # visible, not silent


def test_an_unattributable_claim_reverts_the_sentence_for_every_member(tmp_path):
    """All-or-nothing per SENTENCE. Per-claim fallback would aggregate a shared
    claim inside each cluster separately, so a sibling that established it would
    stop excusing the other cluster -- a silent narrowing of exculpation."""
    refs = _packet_refs(tmp_path)
    claims = [ANTIBODY_CLAIM, "Something else entirely was reported"]
    for ref in refs:
        scope = _scope(ref, claims)
        assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE
        assert scope["reason"] == ms.REASON_AMBIGUOUS
        assert scope["claims"] == claims


def test_a_cluster_left_with_no_claim_reverts_the_sentence(tmp_path):
    """A cluster with no claim would leave its members judged against an EMPTY
    claim list -- held out of the coverage substrate rather than judged narrowly.
    That is a scope reduction, not a precision gain."""
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B52"],
                   [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2])
    assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE
    assert scope["reason"] == ms.REASON_CLUSTER_WITHOUT_CLAIMS
    assert scope["claims"] == [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2]


def test_a_marker_with_no_anchor_phrase_reverts_the_sentence(tmp_path):
    """"as shown in 1,2 and as reported by 3,4" has no noun phrase attached to
    either marker, so nothing here can separate the clusters."""
    refs = _parse(
        tmp_path, [("B1", "111"), ("B2", "222")],
        [f"This was shown in {_xref('B1', '1')} and also in {_xref('B2', '2')}."])
    assert len(refs[0].citance_marker_clusters) == 2       # clusters resolve
    scope = ms.scope_item_claims(jb.build_item(refs[0]),
                                 ["Something was shown", "Something was found"])
    assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE      # attribution does not
    assert scope["reason"] == ms.REASON_AMBIGUOUS


def test_no_claims_at_all_is_not_an_attribution_failure(tmp_path):
    scope = _scope(_by_id(_packet_refs(tmp_path))["PMC13294812:B55"], [])
    assert scope["status"] == ms.SCOPE_WHOLE_SENTENCE
    assert scope["reason"] == ms.REASON_NO_CLAIMS
    assert scope["claims"] == []


# ==========================================================================
# ROW -- range expansion: an inferred interior inherits its endpoint's cluster
# ==========================================================================
def test_an_inferred_range_member_takes_the_opening_endpoints_cluster(tmp_path):
    refs = _parse(
        tmp_path,
        [("B1", "111"), ("B2", "222"), ("B3", "333"), ("B4", "444"),
         ("B5", "555")],
        [f"Margins were assessed {_xref('B1', '1')}-{_xref('B3', '3')} and "
         f"lymph nodes were assessed {_xref('B4', '4')},{_xref('B5', '5')}."])
    by_id = _by_id(refs)
    assert by_id["PMC1000:B2"].citance_marker_inferred is True
    # 1-3 is one adjacent run; the recovered interior 2 is cited exactly where
    # 1 and 3 are, so it belongs to their cluster and not to 4,5's.
    assert [by_id[f"PMC1000:B{n}"].citance_marker_cluster_index
            for n in (1, 2, 3, 4, 5)] == [0, 0, 0, 1, 1]


# ==========================================================================
# ROW -- end to end through run_band: the fault that must stop firing,
#        and the three that must not stop firing
# ==========================================================================
def _packet_band(tmp_path, monkeypatch, script, claims=PACKET_CLAIMS):
    return _band(tmp_path, monkeypatch, refs=PACKET_REFS,
                 paragraphs=[PACKET_SENTENCE], claims=claims, script=script,
                 pmcid="PMC13294812")


ALL_SUPPORTED = {pmid: None for pmid in ("552", "553", "554", "555")}


def _all_supported() -> dict:
    return {pmid: _covers(*PACKET_CLAIMS) for pmid in ALL_SUPPORTED}


def test_b55_no_longer_routes_f6_on_a_claim_it_was_never_cited_for(
        tmp_path, monkeypatch):
    """Packet row 11, end to end. B55's evidence covers the micelles and is
    off-topic on the antibodies -- which used to be an F6 route against it."""
    micelles = _covers(MICELLE_CLAIM, MICELLE_CLAIM_2)
    antibodies = _covers(ANTIBODY_CLAIM, ANTIBODY_CLAIM_2)
    _m, items, _groups = _packet_band(tmp_path, monkeypatch, {
        "552": antibodies, "553": antibodies,
        "554": micelles, "555": micelles})
    row = items["PMC13294812:B55"]
    assert row["atomic_claims"] == [MICELLE_CLAIM, MICELLE_CLAIM_2]
    assert row["proposed_route"] == jb.ROUTE_FULL_COVERAGE
    assert row["proposed_verdict"] is None
    assert [v["claim"] for v in row["coverage_verdicts"]] == [MICELLE_CLAIM,
                                                             MICELLE_CLAIM_2]


def test_the_skipped_pairs_are_named_on_the_row_not_merely_dropped(
        tmp_path, monkeypatch):
    _m, items, _groups = _packet_band(tmp_path, monkeypatch, _all_supported())
    scope = items["PMC13294812:B55"]["marker_scope"]
    assert scope["status"] == ms.SCOPE_SCOPED
    assert scope["cluster_marker"] == "54,55"
    assert scope["claims_not_asked"] == 2
    skipped = [r["claim"] for r in scope["attribution"]
               if r["disposition"] == ms.DISPOSITION_NOT_ASKED]
    assert skipped == [ANTIBODY_CLAIM, ANTIBODY_CLAIM_2]


def test_each_verdict_carries_the_cluster_match(tmp_path, monkeypatch):
    _m, items, _groups = _packet_band(tmp_path, monkeypatch, _all_supported())
    for verdict in items["PMC13294812:B55"]["coverage_verdicts"]:
        assert verdict["reference_cluster_index"] == 1
        assert verdict["claim_cluster_index"] == 1
        assert verdict["cluster_matched"] is True


def test_a_member_that_supports_nothing_in_its_own_clause_is_still_a_fault(
        tmp_path, monkeypatch):
    """Narrowing the question must not become a blanket excuse: B55 is asked
    only its own two claims and engages NEITHER."""
    _m, items, _groups = _packet_band(tmp_path, monkeypatch, {
        "552": _covers(*PACKET_CLAIMS), "553": _covers(*PACKET_CLAIMS),
        "554": _covers(MICELLE_CLAIM, MICELLE_CLAIM_2), "555": _covers()})
    row = items["PMC13294812:B55"]
    assert row["proposed_route"] == cc.ROUTE_UNSUPPORTED_MEMBER
    assert row["proposed_route"] != jb.ROUTE_FULL_COVERAGE


def test_a_sibling_in_another_cluster_cannot_cover_this_clusters_claim(
        tmp_path, monkeypatch):
    """THE ROW THAT MATTERS. B52/B53 cover the antibody claims and nothing else;
    B54/B55 engage their own claims but establish neither. A group keyed on the
    SENTENCE would let the antibody coverage clear the micelle gap. Keyed on the
    CLUSTER -- the references actually co-cited for these claims -- it cannot."""
    shaky = {claim: UNCONFIRMED for claim in PACKET_CLAIMS}
    _m, items, groups = _packet_band(tmp_path, monkeypatch, {
        "552": _covers(*PACKET_CLAIMS), "553": _covers(*PACKET_CLAIMS),
        "554": shaky, "555": shaky})
    assert items["PMC13294812:B54"]["proposed_route"] == cc.ROUTE_GROUP_COVERAGE_GAP
    assert items["PMC13294812:B55"]["proposed_route"] == cc.ROUTE_GROUP_COVERAGE_GAP
    micelle_group = [g for g in groups
                     if g.get("marker_scope_id") == "PMC13294812:g01:c01"]
    assert len(micelle_group) == 1
    assert micelle_group[0]["uncovered_claims"] == [MICELLE_CLAIM, MICELLE_CLAIM_2]


def test_the_group_record_keeps_the_sentence_id_and_names_the_cluster(
        tmp_path, monkeypatch):
    """citance_group_id must keep meaning "the sentence occurrence" even when the
    aggregation unit is a clause of it."""
    _m, _items, groups = _packet_band(tmp_path, monkeypatch, _all_supported())
    assert len(groups) == 2
    assert {g["citance_group_id"] for g in groups} == {"PMC13294812:g01"}
    assert sorted(g["marker_scope_id"] for g in groups) == [
        "PMC13294812:g01:c00", "PMC13294812:g01:c01"]
    assert sorted(g["marker_cluster_text"] for g in groups) == ["52,53", "54,55"]
    for group in groups:
        assert group["size"] == 2
        assert len(group["atomic_claims"]) == 2


def test_a_reverted_sentence_aggregates_as_one_group_exactly_as_today(
        tmp_path, monkeypatch):
    """When attribution reverts, the aggregation unit must revert with it --
    otherwise the fallback silently splits the group it was supposed to preserve."""
    spanning = "Both antibodies and micelles were clinically translated"
    _m, items, groups = _packet_band(
        tmp_path, monkeypatch,
        {pmid: {ANTIBODY_CLAIM: SUPPORTS, spanning: SUPPORTS}
         for pmid in ALL_SUPPORTED},
        claims=[ANTIBODY_CLAIM, spanning])
    assert len(groups) == 1
    assert groups[0]["size"] == 4
    assert "marker_scope_id" not in groups[0]
    assert items["PMC13294812:B55"]["atomic_claims"] == [ANTIBODY_CLAIM, spanning]


# ==========================================================================
# ROW -- the manifest: not_asked is countable and distinct
# ==========================================================================
def test_manifest_counts_the_pairs_that_were_never_asked(tmp_path, monkeypatch):
    manifest, _items, _groups = _packet_band(tmp_path, monkeypatch, _all_supported())
    scope = manifest["marker_scope"]
    # Four references, two claims each never put to them.
    assert scope["pairs_skipped_not_asked"] == 8
    assert scope["claims_not_asked"] == 8
    assert scope["claims_asked"] == 8
    assert scope["scoped_pairs"] == 4
    assert scope["whole_sentence_pairs"] == 0
    assert "not_asked" in scope["note"]
    assert "assessed_negative" in scope["note"]


def test_never_asked_and_answered_and_failed_are_separate_numbers(
        tmp_path, monkeypatch):
    """DEC-079's class. B54/B55 are asked their two micelle claims and engage
    NEITHER; B52/B53 are asked their two and support both. Four negatives, eight
    never-asked, and the manifest must not merge them."""
    manifest, _items, _groups = _packet_band(tmp_path, monkeypatch, {
        "552": _covers(*PACKET_CLAIMS), "553": _covers(*PACKET_CLAIMS),
        "554": _covers(), "555": _covers()})
    scope = manifest["marker_scope"]
    assert scope["pairs_skipped_not_asked"] == 8
    assert scope["claims_assessed_negative"] == 4
    assert scope["pairs_skipped_by_document"] == {"PMC13294812": 8}


def test_the_negative_bucket_names_match_the_coverage_vocabulary():
    """``marker_scope`` restates them to avoid a circular import, so they are
    pinned here rather than left to drift out of the manifest contract."""
    assert ms._NEGATIVE_BUCKETS == (cc.BUCKET_CONTRADICTED, cc.BUCKET_OFF_TOPIC)


def test_manifest_reports_its_own_cluster_sizing(tmp_path, monkeypatch):
    """The spec measured 76 of 274 multi-reference sentences (27.7%) splitting
    into 2+ clusters over corpus_frozen_v1. The run reports the same quantities
    so the two can be compared instead of taken on trust."""
    manifest, _items, _groups = _packet_band(tmp_path, monkeypatch, _all_supported())
    scope = manifest["marker_scope"]
    assert scope["marker_bearing_sentences"] == 1
    assert scope["multi_reference_sentences"] == 1
    assert scope["multi_cluster_sentences"] == 1
    assert scope["cluster_count_distribution"] == {"2": 1}
    assert scope["citation_style_documents"] == {ms.CITATION_STYLE_NUMERIC: 1}


def test_manifest_records_why_a_sentence_stayed_whole(tmp_path, monkeypatch):
    spanning = "Both antibodies and micelles were clinically translated"
    manifest, _items, _groups = _packet_band(
        tmp_path, monkeypatch,
        {pmid: {ANTIBODY_CLAIM: SUPPORTS, spanning: SUPPORTS}
         for pmid in ALL_SUPPORTED},
        claims=[ANTIBODY_CLAIM, spanning])
    scope = manifest["marker_scope"]
    assert scope["scoped_pairs"] == 0
    assert scope["whole_sentence_pairs"] == 4
    assert scope["fallback_reasons"] == {ms.REASON_AMBIGUOUS: 4}
    assert scope["pairs_skipped_not_asked"] == 0


def test_a_single_cluster_run_reports_no_narrowing_at_all(tmp_path, monkeypatch):
    manifest, _items, _groups = _band(
        tmp_path, monkeypatch,
        refs=[("B1", "111"), ("B2", "222")],
        paragraphs=["Drug X reduces outcome A and outcome B "
                    f"{_xref('B1', '1')},{_xref('B2', '2')}."],
        claims=["Drug X reduces outcome A", "Drug X reduces outcome B"],
        script={"111": {"Drug X reduces outcome A": SUPPORTS,
                        "Drug X reduces outcome B": OFF_TOPIC},
                "222": {"Drug X reduces outcome A": OFF_TOPIC,
                        "Drug X reduces outcome B": SUPPORTS}})
    scope = manifest["marker_scope"]
    assert scope["scoped_pairs"] == 0
    assert scope["pairs_skipped_not_asked"] == 0
    assert scope["multi_cluster_sentences"] == 0
    assert scope["fallback_reasons"] == {ms.REASON_SINGLE_CLUSTER: 2}


# ==========================================================================
# ROW (Change 4) -- one sentence citing N references extracts ONCE
# ==========================================================================
class _CountingExtractor:
    def __init__(self, claims):
        self.claims = list(claims)
        self.calls = 0

    def __call__(self, _sentence):
        self.calls += 1
        return list(self.claims)


def test_run_band_extracts_once_per_sentence(tmp_path, monkeypatch):
    extractor = _CountingExtractor(PACKET_CLAIMS)
    xml_dir = tmp_path / "xml"
    xml_dir.mkdir()
    (xml_dir / "PMC13294812.xml").write_text(
        _article(PACKET_REFS, [PACKET_SENTENCE], "PMC13294812"),
        encoding="utf-8")
    monkeypatch.setattr(jb, "ncbi_pubtypes", lambda *a, **k: ["Journal Article"])
    jb.run_band(str(xml_dir), str(tmp_path / "out"), extractor=extractor,
                coverage_judge=lambda claims, _e: [SUPPORTS for _ in claims],
                fetch_abstract=lambda pmid: f"Abstract {pmid}.",
                session=object())
    assert extractor.calls == 1


def test_orchestrator_extracts_once_per_sentence(tmp_path, monkeypatch):
    """``run_natural_judgment`` is the PRODUCTION path and had no cache at all,
    so the B52-B55 group paid four identical extraction calls for one sentence."""
    refs = _packet_refs(tmp_path)
    extractor = _CountingExtractor(PACKET_CLAIMS)
    (tmp_path / "PMC13294812.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: refs)
    manifest = jr.run_natural_judgment(
        str(tmp_path), str(tmp_path / "out"), extractor=extractor,
        coverage_judge=lambda claims, _e: [
            {"established": True, "rationale": "r", "evidence_span": "s"}
            for _ in claims],
        fetch_abstract=lambda _pmid: "An abstract sentence.",
        preband_disposition={r.citation_id: "cleared" for r in refs},
        model="test-model")
    assert extractor.calls == 1
    assert manifest["marker_scope"]["pairs_skipped_not_asked"] == 8


def test_orchestrator_records_the_scope_on_the_durable_record(tmp_path,
                                                              monkeypatch):
    refs = _packet_refs(tmp_path)
    (tmp_path / "PMC13294812.xml").write_text("<x/>", encoding="utf-8")
    monkeypatch.setattr(jr, "parse_pmc_xml",
                        lambda path, source_pmcid=None: refs)
    out_dir = tmp_path / "out"
    jr.run_natural_judgment(
        str(tmp_path), str(out_dir), extractor=lambda _s: list(PACKET_CLAIMS),
        coverage_judge=lambda claims, _e: [
            {"established": True, "rationale": "r", "evidence_span": "s"}
            for _ in claims],
        fetch_abstract=lambda _pmid: "An abstract sentence.",
        preband_disposition={r.citation_id: "cleared" for r in refs},
        model="test-model")
    rows = {json.loads(line)["citation_id"]: json.loads(line)
            for line in (out_dir / "judgment_predictions.jsonl"
                         ).read_text().splitlines()}
    row = rows["PMC13294812:B55"]
    assert row["atomic_claims"] == [MICELLE_CLAIM, MICELLE_CLAIM_2]
    assert row["marker_scope"]["cluster_id"] == "PMC13294812:g01:c01"
    assert row["marker_scope"]["claims_not_asked"] == 2
    # citation_id and the co-citation group id are untouched by the narrowing.
    assert row["citation_id"] == "PMC13294812:B55"
    assert row["citance_group_id"] == "PMC13294812:g01"


# ==========================================================================
# ROW -- a group with one evaluable member is unchanged (coverage, not this bug)
# ==========================================================================
def test_a_cluster_of_one_evaluable_member_gets_no_group_credit(tmp_path,
                                                               monkeypatch):
    """Measured over corpus_frozen_v1: 79 of 170 groups with any evaluable member
    have exactly one, so group credit is structurally impossible there. That is
    coverage, not marker attribution, and it must stay that way."""
    _m, items, _groups = _packet_band(tmp_path, monkeypatch, {
        "552": _covers(*PACKET_CLAIMS), "553": _covers(*PACKET_CLAIMS),
        "554": _covers(MICELLE_CLAIM), "555": _covers()})
    # B54 covers one of its two claims, B55 engages neither: no sibling can
    # supply the missing coverage inside this cluster.
    assert items["PMC13294812:B54"]["proposed_route"] != cc.ROUTE_GROUP_COVERED
    assert items["PMC13294812:B55"]["proposed_route"] == cc.ROUTE_UNSUPPORTED_MEMBER


# ==========================================================================
# ROW -- the frozen prompt package is untouched by all of the above
# ==========================================================================
def test_band_prompts_blob_oid_is_unchanged():
    """The spec pins band_prompts.py by whole blob OID and the frozen prompt
    packages seal that OID. Route (b) -- asking the extractor for a marker
    attribution -- would have changed it, and is deliberately NOT taken."""
    import hashlib
    import os
    path = os.path.join(os.path.dirname(__file__), "band_prompts.py")
    with open(path, "rb") as fh:
        payload = fh.read()
    blob = hashlib.sha1(b"blob %d\0" % len(payload) + payload).hexdigest()
    assert blob == "fa01126e2b9482d450065fd70cd0eb1fea816f5c"
