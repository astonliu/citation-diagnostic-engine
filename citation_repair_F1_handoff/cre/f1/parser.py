"""Phase 1a -- parse PMC Open Access XML into structured References.

Handles both <element-citation> and <mixed-citation>. Extracts the claimed
bibliographic fields, the claimed PMID/DOI, the raw citation string, and links
each reference to its citance (the sentence in the body carrying the in-text
<xref ref-type="bibr"> marker that points at it).

Dependencies: lxml. Falls back to stdlib ElementTree if lxml is absent.
"""
from __future__ import annotations
import hashlib
from typing import Iterator
import os
import re
import sys

try:
    from lxml import etree
    _PARSER = lambda p: etree.parse(p)            # noqa: E731
except ImportError:                               # pragma: no cover
    import xml.etree.ElementTree as etree         # type: ignore
    _PARSER = lambda p: etree.parse(p)            # noqa: E731

from . import marker_scope
from .schema import Reference, ClaimedRef
from .titlefurniture import excise_leading_furniture


def _localname(tag) -> str:
    """Strip any {namespace} prefix; '' for comments / PIs (non-str tags)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _text(el) -> str:
    if el is None:
        return ""
    return " ".join(el.itertext()).strip()


def _first(node, *paths):
    for p in paths:
        found = node.find(p)
        if found is not None:
            return found
    return None


def _year_from(node) -> int | None:
    """The cited work's publication year, or None when ambiguous.

    Reads <year> DIRECT children only (a nested access-date / conference year
    never leaks in). Returns None -- the safe can't-judge value -- rather than a
    guess when distinct 4-digit years disagree (multiple <year> children, or a
    'YYYY-YYYY' range), since a confidently-WRONG written year manufactures a
    spurious year disagreement in the matcher. A single distinct year is returned
    as before."""
    years: set[int] = set()
    for y in node.findall("year"):
        for m in re.findall(r"\d{4}", _text(y)):
            years.add(int(m))
    return next(iter(years)) if len(years) == 1 else None


# person-group-type values whose <name>s are NOT the authors of the cited work.
# These must be excluded so an editor/translator never leaks in as authors[0] --
# the matcher reads authors[0] as the claimed first-author surname, and an editor
# there manufactures a spurious author_match=False (the -0.15 penalty that turns
# correct book-chapter citations into F2 false positives). JATS person-group-type
# vocabulary; lowercased for a case-insensitive compare.
_NON_AUTHOR_PERSON_GROUPS = {
    "editor", "translator", "guest-editor", "transed", "assignee",
    "inventor", "compiler", "allauthors-editor", "editors",
}


def _surnames_under(el) -> list[str]:
    """Surnames of all contributors under ``el``, in document order.

    Reads a <surname> from BOTH <name> and <string-name> contributor elements:
    JATS mixed-citation reference lists format contributors either way, and refs
    that use <string-name><surname> would otherwise lose their author entirely
    (author_match -> None -> a genuine wrong-paper mis-bands, e.g. 31665581).

    De-duped by a stable document path under lxml.  lxml element proxies can be
    garbage-collected and their Python ids reused during one traversal, so
    ``id(surname)`` is not a stable de-duplication key there.  The stdlib
    ElementTree fallback owns stable element objects and may safely use id()."""
    tree = el.getroottree() if hasattr(el, "getroottree") else None
    getpath = getattr(tree, "getpath", None)

    def _key(node):
        return getpath(node) if getpath is not None else id(node)

    out: list[str] = []
    seen: set = set()
    for node in el.iter():
        if _localname(node.tag) not in ("name", "string-name"):
            continue
        sn = node.find("surname")           # direct-child surname only
        if sn is None or _key(sn) in seen:
            continue
        txt = _text(sn)
        if txt:
            seen.add(_key(sn))
            out.append(txt)
    return out


def _authors_from(node) -> list[str]:
    """First-listed AUTHORS of the cited work, in document order.

    Collects <surname>s from author (and untyped) <person-group>s only, plus any
    <collab> consortium name in those groups; editor/translator groups are
    skipped. Falls back to every <name> in the citation when there is no
    <person-group> at all, or when author-group filtering leaves nothing (e.g. an
    edited book whose only listed people are editors -- recall-first: surface
    those rather than nothing, since the matcher needs an authors[0] to compare).

    Top-down only (no getparent()), so it works under both lxml and the stdlib
    ElementTree fallback.
    """
    groups = list(node.iter("person-group"))
    if not groups:
        authors = _surnames_under(node)
        for col in node.iter("collab"):
            value = _text(col)
            if value:
                authors.append(value)
        return authors

    authors: list[str] = []
    for pg in groups:
        ptype = (pg.get("person-group-type") or "").strip().lower()
        if ptype in _NON_AUTHOR_PERSON_GROUPS:
            continue
        authors += _surnames_under(pg)
        for col in pg.iter("collab"):
            t = _text(col)
            if t:
                authors.append(t)
    # Only non-author groups (editor-only edited book): better some signal than
    # none. Returns the editor surnames -- the closest available author proxy.
    return authors or _surnames_under(node)


def _first_author_is_collab(node) -> bool:
    """Whether the first author contributor is a JATS ``<collab>``."""
    groups = list(node.iter("person-group"))
    containers = groups or [node]
    for container in containers:
        if groups:
            ptype = (container.get("person-group-type") or "").strip().lower()
            if ptype in _NON_AUTHOR_PERSON_GROUPS:
                continue
        for contributor in container.iter():
            tag = _localname(contributor.tag)
            if tag == "collab" and _text(contributor):
                return True
            if tag in ("name", "string-name"):
                surname = contributor.find("surname")
                if surname is not None and _text(surname):
                    return False
    return False


def _pub_id(node, id_type: str) -> str:
    for pid in node.iter("pub-id"):
        if pid.get("pub-id-type") == id_type:
            return _text(pid)
    return ""


def _direct_text(node, *tags: str) -> str:
    """Text of the first matching direct child, never a nested citation field."""
    for tag in tags:
        el = node.find(tag)
        if el is not None:
            value = _text(el)
            if value:
                return value
    return ""


def _reference_title(node) -> str:
    """Best direct JATS title, preserving the existing title-tag priority."""
    for tag in ("article-title", "part-title", "chapter-title"):
        candidates = [_text(el) for el in node.findall(tag)]
        candidates = [value for value in candidates if value]
        if candidates:
            return max(candidates, key=len)
    return ""


def _pages_from(node) -> str:
    """JATS page range/eLocator without dropping alphabetic article locators."""
    explicit = _direct_text(node, "page-range")
    if explicit:
        return explicit
    first = _direct_text(node, "fpage")
    last = _direct_text(node, "lpage")
    if first and last:
        return first if first == last else f"{first}-{last}"
    return first or last or _direct_text(node, "elocation-id")


def _citation_node(ref):
    return _first(ref, "element-citation", "mixed-citation", "citation")


# --------------------------------------------------------------------------
# Citance linking (HANDOFF task 3)
# --------------------------------------------------------------------------
# Sentence-bearing blocks we serialize. We process only the innermost such block
# (one with no nested block) so a <td> wrapping a <p> isn't counted twice.
_BLOCK_TAGS = {"p", "title", "caption", "td", "th", "list-item", "disp-quote"}

# Split into sentences while keeping each sentence's start offset (finditer).
#
# KNOWN DEFECT, logged not fixed (found while measuring marker clusters,
# 2026-08-16; out of scope for that change and recorded so it is not re-found).
# This splits on the period in "et al.", so an author-year document yields
# sentence fragments like ", 2006)." -- and that fragment becomes the citance
# every reference on it is judged against. ``sentence_spans.py`` protects
# "Fig.", "Dr." and single-letter initials; neither guards "et al.". It affects
# the three author-year documents of corpus_frozen_v1 (PMC12967000,
# PMC13219232, PMC13295838) and no numeric-marker document.
_SENT_RE = re.compile(r"[^.!?]*[.!?]+(?:\s+|$)|[^.!?]+$")


def _serialize_with_markers(block):
    """Linearize a block's text, recording (char_offset, [rid...], marker_text)
    for every <xref ref-type="bibr"> in document order."""
    parts: list[str] = []
    markers: list[tuple[int, list[str], str]] = []

    def walk(el):
        if el.text:
            parts.append(el.text)
        for child in el:
            if _localname(child.tag) == "xref" and child.get("ref-type") == "bibr":
                pos = sum(len(p) for p in parts)
                rids = (child.get("rid") or "").split()
                mtext = _text(child)
                markers.append((pos, rids, mtext))
                if mtext:
                    parts.append(mtext)       # keep the marker visible in-sentence
            else:
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(block)
    return "".join(parts), markers


def _sentence_spans(text: str, diagnostics: list[dict] | None = None):
    """Return the legacy sentence spans and attest whether they tile ``text``.

    The regex is intentionally unchanged: correcting segmentation changes the
    citing sentence that governed existing F6 labels.  The additive diagnostic
    turns any silent deletion into a durable, countable event without moving a
    boundary or verdict.
    """
    spans = []
    for m in _SENT_RE.finditer(text):
        if m.group().strip():
            spans.append((m.start(), m.end(), m.group()))
    try:
        cursor = 0
        for start, end, _segment in spans:
            assert start == cursor
            assert end >= start
            cursor = end
        assert cursor == len(text)
    except AssertionError:
        uncovered: list[list[int]] = []
        cursor = 0
        for start, end, _segment in spans:
            if start > cursor:
                uncovered.append([cursor, start])
            cursor = max(cursor, end)
        if cursor < len(text):
            uncovered.append([cursor, len(text)])
        diagnostic = {
            "kind": "sentence_spans_do_not_tile_input",
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "text_length": len(text),
            "span_count": len(spans),
            "covered_chars": sum(end - start for start, end, _ in spans),
            "uncovered_chars": sum(end - start for start, end in uncovered),
            "uncovered_ranges": uncovered,
        }
        if diagnostics is not None:
            diagnostics.append(diagnostic)
        print(
            "[sentence-partition-gap] "
            f"sha256={diagnostic['text_sha256']} "
            f"uncovered_chars={diagnostic['uncovered_chars']}",
            file=sys.stderr,
        )
    return spans


def _sentence_for(pos: int, spans) -> str:
    for start, end, seg in spans:
        if start <= pos < end:
            return re.sub(r"\s+", " ", seg).strip()
    # marker past the last boundary: fall back to the final sentence
    if spans:
        return re.sub(r"\s+", " ", spans[-1][2]).strip()
    return ""


# A rendered range is two numeric markers with NOTHING but a dash between them
# in the serialized text: "<xref>9</xref>-<xref>13</xref>" renders as "9-13".
# Every dash Unicode actually uses for ranges, so an en dash is not a miss.
_RANGE_DASH_RE = re.compile(r"^\s*[-‐‑‒–—]\s*$")


def _innermost_blocks(root):
    """Sentence-bearing blocks with no nested block, serialized once each.

    Serializing here rather than inside :func:`link_citances` lets that function
    make TWO passes over the same data -- one to validate the article's
    numbering, one to assign -- without walking or re-serializing the tree
    twice."""
    for block in root.iter():
        if _localname(block.tag) not in _BLOCK_TAGS:
            continue
        nested = 0
        for d in block.iter():
            if _localname(d.tag) in _BLOCK_TAGS:
                nested += 1
                if nested > 1:
                    break
        if nested > 1:
            continue
        text, markers = _serialize_with_markers(block)
        if markers:
            yield text, markers


def _positional_numbering(serialized, refs_by_id, ordered_ref_ids) -> dict:
    """``displayed number -> ref id``, but ONLY if the article numbers its
    references positionally and contiguously. Empty dict disables expansion.

    This is the safety gate for range expansion, and it is deliberately
    all-or-nothing per article: EVERY numeric single-target marker in the
    document must resolve to the reference at that bibliography ordinal. One
    disagreement -- an unnumbered reference shifting the sequence, a
    author-year scheme, a ref the parser skipped -- and the whole article is
    refused, because an ordinal model that is wrong anywhere cannot be trusted
    to infer members anywhere.

    Returns {} when nothing attests the scheme either: with no numeric marker to
    check against, "contiguous" is an assumption rather than an observation.
    """
    by_ordinal = {i + 1: rid for i, rid in enumerate(ordered_ref_ids)}
    if not by_ordinal:
        return {}
    attested = 0
    for _text, markers in serialized:
        for _pos, rids, mtext in markers:
            label = mtext.strip()
            if not label.isdigit() or len(rids) != 1:
                continue
            if by_ordinal.get(int(label)) != rids[0]:
                return {}
            attested += 1
    return by_ordinal if attested else {}


def _inferred_interior(text, entries, numbering, refs_by_id) -> list:
    """References a sentence renders inside a range but never links.

    ``entries`` is one sentence's markers as ``(pos, rids, marker_text)`` in
    document order. Returns ``[(opening_endpoint_pos, number, rid), ...]`` -- the
    position lets the caller restore rendered order -- for the interiors of every
    range that clears all four safety conditions:

      1. both endpoints are numeric single-target markers,
      2. both resolve to a bibliography entry AT their own displayed ordinal,
      3. the numbers strictly between them all exist and are resolvable, and
      4. the endpoints are adjacent in the text with only a dash between them.

    A range that fails ANY of these is skipped whole -- never partially
    expanded. A partial expansion would assert some members and silently drop
    others from the same rendered range, which is worse than expanding none: the
    reader cannot see what was left out.
    """
    out = []
    for k in range(len(entries) - 1):
        pos_a, rids_a, text_a = entries[k]
        pos_b, rids_b, text_b = entries[k + 1]
        label_a, label_b = text_a.strip(), text_b.strip()
        if not (label_a.isdigit() and label_b.isdigit()):
            continue
        if len(rids_a) != 1 or len(rids_b) != 1:
            continue
        first, last = int(label_a), int(label_b)
        if last <= first + 1:
            continue
        if not _RANGE_DASH_RE.match(text[pos_a + len(text_a):pos_b]):
            continue
        if numbering.get(first) != rids_a[0] or numbering.get(last) != rids_b[0]:
            continue
        interior = []
        for n in range(first + 1, last):
            rid = numbering.get(n)
            if rid is None or rid not in refs_by_id:
                interior = None
                break
            interior.append((pos_a, n, rid))
        if interior:
            out.extend(interior)
    return out


def _sentence_clusters(seg: str, seg_start: int, entries) -> list:
    """One sentence's MARKER CLUSTERS, with offsets into the normalized citance.

    ``entries`` is the sentence's markers as ``(pos, rids, marker_text)`` in
    document order, positioned against the raw serialized block; ``seg`` is the
    raw sentence segment starting at ``seg_start``. Returns one dict per cluster
    with its index, its offset and end IN THE CITANCE STRING, and the marker text
    it renders.

    The offsets are translated into citance coordinates here, at the only point
    where both coordinate systems exist. Everything downstream sees the citance
    alone -- the whitespace-collapsed sentence the extractor and the judge were
    given -- so an untranslated offset would be silently wrong by however much
    whitespace the XML happened to carry.
    """
    text, raw_to_norm = marker_scope.normalize_with_offsets(seg)

    def _offset(pos: int) -> int:
        i = pos - seg_start
        if i < 0:
            return 0
        if i >= len(raw_to_norm):
            return len(text)
        return raw_to_norm[i]

    out: list = []
    for k, members in enumerate(marker_scope.cluster_markers(seg, [
            (pos - seg_start, rids, mtext) for pos, rids, mtext in entries])):
        first, last = entries[members[0]], entries[members[-1]]
        rendered = [entries[i][2].strip() for i in members
                    if (entries[i][2] or "").strip()]
        out.append({
            "index": k,
            "offset": _offset(first[0]),
            "end": _offset(last[0] + len(last[2] or "")),
            "marker_text": ",".join(rendered),
            "entries": list(members),
            "members": [],           # citation_ids, filled once the group exists
        })
    return out


def _span_index(pos: int, spans) -> int:
    """Index of the sentence span containing ``pos``.

    The OCCURRENCE, not the text: two sentences in one block can be
    byte-identical, and they are still two separate acts of citation. Mirrors
    :func:`_sentence_for`'s past-the-last-boundary fallback exactly, so the group
    a marker lands in and the sentence it is given can never disagree."""
    for i, (start, end, _seg) in enumerate(spans):
        if start <= pos < end:
            return i
    return len(spans) - 1 if spans else -1


def link_citances(root, refs_by_id: dict, ordered_ref_ids=()) -> None:
    """Attach the citing sentence + marker to each Reference (first hit wins),
    and record the CO-CITATION GROUP that sentence occurrence forms.

    A sentence citing eight references is normal, correct scientific practice:
    the eight are cited COLLECTIVELY and no single one is expected to carry the
    whole claim. The band judged each one alone against the whole sentence, so
    F6 ("supports part of the claim but not all of it") fired by construction on
    every member of every co-citation group. The membership needed to see that is
    resolved right here and used to be discarded.

    GROUP MEMBERSHIP IS "REFERENCES THIS SENTENCE ACTUALLY GAVE ITS CITANCE TO",
    not "references this sentence mentions". Because first-citance-wins, a
    reference already carrying an earlier sentence keeps it and is judged against
    THAT sentence's claims; adding it to this group would aggregate coverage
    verdicts across two different claim lists. Such a reference is simply absent
    from this group, and belongs to the group of the sentence it did take.

    Ordering is document order and members are deduplicated, so one sentence
    carrying overlapping ranges ("1-8" and "3, 9") yields ONE group naming each
    reference once.

    RANGE EXPANSION. A sentence that renders "9-13" is normally marked up as an
    xref on 9 and an xref on 13 with a literal dash between them: references 10,
    11 and 12 are cited on the page and carry no link at all. Measured over
    corpus_frozen_v1, that is not an edge case -- 63 rendered ranges, ALL with
    unlinked interiors, affecting 115 references, 90 of which got no citance
    whatsoever and 23 of which silently took a LATER sentence's citance instead.
    Those interiors are recovered here when, and only when,
    :func:`_positional_numbering` and :func:`_inferred_interior` agree it is
    safe, and each recovered member is recorded as INFERRED so a reader can tell
    a deduction from a link the publisher wrote.

    MARKER CLUSTERS. The group above is the whole SENTENCE, and a sentence can
    cite two different things: "...antibodies 52,53 and pH sensitive fluorescent
    micelles 54,55..." cites 52,53 for the antibodies and 54,55 for the micelles.
    Every marker's position is already resolved here; what was discarded is WHICH
    MARKERS SIT TOGETHER. Each reference now also records the maximal run of
    adjacent markers it belongs to, and that cluster's offset in the citance, so
    the band can stop asking a reference about a clause it was never cited for.
    Clustering is applied ONLY to numeric-marker documents -- the positional rule
    is undefined for author-year markers, whose text is itself a name containing
    letters -- and a sentence with exactly one cluster is left completely
    untouched, which is the regression guard for the whole change.

    Best-effort: any failure here must never break reference extraction, so the
    caller wraps this in try/except.
    """
    serialized = list(_innermost_blocks(root))
    # Validated across the WHOLE article before a single member is inferred: a
    # numbering model that is wrong anywhere is not trusted anywhere.
    numbering = _positional_numbering(serialized, refs_by_id,
                                      ordered_ref_ids or ())
    # Style is a property of the DOCUMENT, decided over every marker it renders
    # before any sentence is clustered -- same all-or-nothing discipline as
    # _positional_numbering, and for the same reason.
    style = marker_scope.detect_citation_style(
        [mtext for _text, markers in serialized for _pos, _rids, mtext in markers])
    clustering = style == marker_scope.CITATION_STYLE_NUMERIC
    group_seq = 0
    for text, markers in serialized:
        partition_failures: list[dict] = []
        spans = _sentence_spans(text, partition_failures)
        if partition_failures:
            # Attach the same immutable facts to every reference mentioned in
            # the affected block.  The list is copied per reference below so
            # no two durable records share mutable state.
            affected = {
                rid for _pos, rids, _mtext in markers for rid in rids
                if rid in refs_by_id
            }
            for rid in affected:
                refs_by_id[rid].citance_sentence_partition_failures.extend(
                    dict(item) for item in partition_failures)
        # Sentence occurrence -> its markers, in document order. Bucketed before
        # assigning so a sentence's rendered ranges can be read as a whole;
        # spans are visited in ascending order, so first-citance-wins resolves
        # exactly as it did when this walked markers directly.
        by_span: dict = {}
        for pos, rids, mtext in markers:
            by_span.setdefault(_span_index(pos, spans), []).append(
                (pos, rids, mtext))

        claimed_by_span: dict = {}
        inferred_by_span: dict = {}
        clusters_by_span: dict = {}
        for span_i in sorted(by_span):
            entries = by_span[span_i]
            sentence = _sentence_for(entries[0][0], spans)
            # Marker clusters for THIS sentence, and the lookup that puts each
            # reference in one. A single cluster is recorded as none at all: the
            # whole point of the single-cluster case is that no byte of it moves.
            clusters = (_sentence_clusters(spans[span_i][2], spans[span_i][0],
                                           entries)
                        if clustering and 0 <= span_i < len(spans) else [])
            if len(clusters) < 2:
                clusters = []
            # A reference rendered in more than one cluster is not partitionable:
            # narrowing it to the first cluster silently drops the later claims.
            # Fail closed for THAT REFERENCE, not for every uniquely placed
            # reference in the sentence.  A repeated range endpoint such as 2 in
            # ``(2-9) ... (2,10-12)`` must not erase B8's unambiguous membership
            # in the first cluster.
            repeated_rids: set[str] = set()
            if clusters:
                rid_clusters: dict[str, set[int]] = {}
                for cluster in clusters:
                    for entry_index in cluster["entries"]:
                        for rid in entries[entry_index][1]:
                            rid_clusters.setdefault(rid, set()).add(
                                cluster["index"])
                repeated_rids = {
                    rid for rid, indexes in rid_clusters.items()
                    if len(indexes) > 1
                }
            clusters_by_span[span_i] = clusters
            cluster_of_entry = {e: c["index"] for c in clusters
                                for e in c["entries"]}
            cluster_of_pos = {entries[e][0]: k
                              for e, k in cluster_of_entry.items()}
            # (sort key, ref). ASSIGNMENT happens explicit-first so an explicit
            # link always beats an inference to a reference; the key restores
            # RENDERED order afterwards, placing each inferred interior right
            # after the endpoint that opened its range. "1-5" therefore lists
            # B1..B5, not B1, B5, B2, B3, B4.
            claims: list = []
            for e_i, (pos, rids, mtext) in enumerate(entries):
                for rid in rids:
                    ref = refs_by_id.get(rid)
                    if ref is None or ref.citance:    # first citance wins
                        continue
                    ref.citance = sentence
                    ref.citance_citation_style = style
                    ref.citance_marker_cluster_index = (
                        -1 if rid in repeated_rids
                        else cluster_of_entry.get(e_i, -1))
                    if not ref.cited_reference_marker:
                        ref.cited_reference_marker = mtext
                    claims.append(((pos, 0, 0), ref))
            for open_pos, number, rid in _inferred_interior(
                    text, entries, numbering, refs_by_id):
                ref = refs_by_id.get(rid)
                if ref is None or ref.citance:        # first citance still wins
                    continue
                ref.citance = sentence
                ref.citance_citation_style = style
                # A recovered interior belongs to the cluster of the endpoint
                # that OPENED its range: "16-18" renders as one adjacent run, so
                # 17 is cited exactly where 16 and 18 are.
                ref.citance_marker_cluster_index = cluster_of_pos.get(
                    open_pos, -1)
                ref.citance_marker_inferred = True
                if not ref.cited_reference_marker:
                    # The number the article renders. Accurate, and the
                    # inferred flag records that no xref asserted it.
                    ref.cited_reference_marker = str(number)
                claims.append(((open_pos, 1, number), ref))
                inferred_by_span.setdefault(span_i, []).append(ref)
            if claims:
                claims.sort(key=lambda kv: kv[0])
                claimed_by_span[span_i] = [ref for _k, ref in claims]

        # One group per sentence occurrence that claimed at least one reference.
        # Numbered in document order across the whole article so the id is stable
        # and reproducible from the XML alone.
        for span_i in sorted(claimed_by_span):
            members = claimed_by_span[span_i]
            group_seq += 1
            pmcid = members[0].source_pmcid or members[0].source_pmid or "doc"
            group_id = f"{pmcid}:g{group_seq:02d}"
            member_ids = list(dict.fromkeys(m.citation_id for m in members))
            inferred_ids = list(dict.fromkeys(
                m.citation_id for m in inferred_by_span.get(span_i, [])))
            # Cluster ids extend the group id rather than replacing it --
            # "<group>:c<NN>" -- so the sentence a cluster belongs to is readable
            # straight off the id. citation_id and citance_group_id are untouched:
            # Band 1's preband_contract joins on the first and the co-citation
            # record keys on the second.
            clusters = clusters_by_span.get(span_i) or []
            for cluster in clusters:
                cluster["id"] = f"{group_id}:c{cluster['index']:02d}"
                cluster["members"] = [
                    m.citation_id for m in members
                    if m.citance_marker_cluster_index == cluster["index"]]
            for ref in members:
                ref.citance_group_id = group_id
                ref.citance_group_members = list(member_ids)
                ref.citance_group_inferred_members = list(inferred_ids)
                if not clusters:
                    continue
                # Copied per reference: the list is provenance carried on a
                # durable record, and two references must never share one mutable
                # object across the band.
                ref.citance_marker_clusters = [
                    {k: (list(v) if isinstance(v, list) else v)
                     for k, v in cluster.items() if k != "entries"}
                    for cluster in clusters]
                own = ref.citance_marker_cluster_index
                ref.citance_marker_cluster_id = (
                    clusters[own]["id"] if 0 <= own < len(clusters) else "")


def parse_pmc_xml(path: str, source_pmcid: str = "") -> list[Reference]:
    """Return all parseable references from one PMC OA XML file."""
    tree = _PARSER(path)
    root = tree.getroot()

    # source (citing) paper metadata
    src_title = _text(_first(root, ".//article-title"))
    src_pmid = ""
    for aid in root.iter("article-id"):
        if aid.get("pub-id-type") == "pmid":
            src_pmid = _text(aid)
            break

    refs: list[Reference] = []
    refs_by_id: dict[str, Reference] = {}
    # Bibliography order -- the ordinal a numbered citation marker refers to.
    # EVERY <ref> counts, including one the loop below skips for carrying no
    # citation node, because a skipped entry still occupies a numbered slot on
    # the page. Dropping it would shift every later ordinal by one and silently
    # mis-resolve inferred range members; counting it keeps the sequence honest,
    # and _positional_numbering refuses the article if the markers disagree.
    ordered_ref_ids: list[str] = [
        ref.get("id") or f"ref{i}" for i, ref in enumerate(root.iter("ref"))]
    for i, ref in enumerate(root.iter("ref")):
        cit = _citation_node(ref)
        if cit is None:
            continue
        raw_title = _reference_title(cit)
        clean_title, excised = excise_leading_furniture(raw_title)
        claimed = ClaimedRef(
            title=clean_title,
            authors=_authors_from(cit),
            year=_year_from(cit),
            journal=_text(_first(cit, "source")),
            claimed_pmid=_pub_id(cit, "pmid"),
            claimed_doi=_pub_id(cit, "doi"),
            raw=_text(cit),
            volume=_direct_text(cit, "volume"),
            pages=_pages_from(cit),
            written_title_excised=excised,
            first_author_is_collab=_first_author_is_collab(cit),
        )
        ref_id = ref.get("id") or f"ref{i}"
        reference = Reference(
            citation_id=f"{source_pmcid or src_pmid or 'doc'}:{ref_id}",
            citance="",                       # filled by link_citances below
            claimed=claimed,
            source_pmcid=source_pmcid,
            source_pmid=src_pmid,
            source_title=src_title,
        )
        refs.append(reference)
        if ref.get("id"):
            refs_by_id[ref.get("id")] = reference

    try:
        link_citances(root, refs_by_id, ordered_ref_ids)
    except Exception as e:                            # noqa: BLE001 - best-effort
        print(f"[citance-skip] {source_pmcid or path}: {e}")
    return refs


def iter_pmc_dir(dirpath: str, *,
                 source_pmcids: set[str] | None = None) -> Iterator[Reference]:
    """Yield references across a PMC XML tree, optionally scoped by file stem.

    The filename-level filter runs before XML parsing.  This is load-bearing for
    held-out rebanding where several seeds share a large Drive directory: parsing
    every out-of-scope article before discarding it made a 1,225-file run scan the
    entire corpus and appear hung on cold network-mounted storage.
    """
    allow = set(source_pmcids) if source_pmcids is not None else None

    # The normal OA layout is flat (``<dir>/PMC123.xml``).  Resolve a scoped
    # run directly instead of first listing every entry in a cloud-mounted
    # directory; on Drive that directory walk can take minutes before the first
    # wanted article is opened.  Fall back to a recursive walk only for allowed
    # stems that were not present at the root, preserving support for nested
    # corpora.
    if allow is not None:
        missing = set(allow)
        for pmcid in sorted(allow):
            for suffix in (".xml", ".nxml"):
                path = os.path.join(dirpath, f"{pmcid}{suffix}")
                if not os.path.isfile(path):
                    continue
                missing.discard(pmcid)
                try:
                    yield from parse_pmc_xml(path, source_pmcid=pmcid)
                except Exception as e:                       # noqa: BLE001
                    print(f"[parse-skip] {pmcid}{suffix}: {e}", file=sys.stderr)
                break
        if not missing:
            return
        allow = missing

    for dp, _, files in os.walk(dirpath):
        for fn in files:
            if fn.endswith((".xml", ".nxml")):
                pmcid = re.sub(r"\.n?xml$", "", fn)
                if allow is not None and pmcid not in allow:
                    continue
                try:
                    yield from parse_pmc_xml(os.path.join(dp, fn), source_pmcid=pmcid)
                except Exception as e:                       # noqa: BLE001
                    print(f"[parse-skip] {fn}: {e}", file=sys.stderr)
