"""Phase 1a -- parse PMC Open Access XML into structured References.

Handles both <element-citation> and <mixed-citation>. Extracts the claimed
bibliographic fields, the claimed PMID/DOI, the raw citation string, and links
each reference to its citance (the sentence in the body carrying the in-text
<xref ref-type="bibr"> marker that points at it).

Dependencies: lxml. Falls back to stdlib ElementTree if lxml is absent.
"""
from __future__ import annotations
from typing import Iterator
import os
import re

try:
    from lxml import etree
    _PARSER = lambda p: etree.parse(p)            # noqa: E731
except ImportError:                               # pragma: no cover
    import xml.etree.ElementTree as etree         # type: ignore
    _PARSER = lambda p: etree.parse(p)            # noqa: E731

from .schema import Reference, ClaimedRef


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

    De-duped by the <surname> element's identity so a contributor wrapped as
    <string-name><name><surname> (rare/malformed nesting) is counted once; order
    follows the surname's document position. For a pure <name><surname> ref this
    yields exactly the previous result (same surnames, same order)."""
    out: list[str] = []
    seen: set[int] = set()
    for node in el.iter():
        if _localname(node.tag) not in ("name", "string-name"):
            continue
        sn = node.find("surname")           # direct-child surname only
        if sn is None or id(sn) in seen:
            continue
        txt = _text(sn)
        if txt:
            seen.add(id(sn))
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


def _sentence_spans(text: str):
    spans = []
    for m in _SENT_RE.finditer(text):
        if m.group().strip():
            spans.append((m.start(), m.end(), m.group()))
    return spans


def _sentence_for(pos: int, spans) -> str:
    for start, end, seg in spans:
        if start <= pos < end:
            return re.sub(r"\s+", " ", seg).strip()
    # marker past the last boundary: fall back to the final sentence
    if spans:
        return re.sub(r"\s+", " ", spans[-1][2]).strip()
    return ""


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


def link_citances(root, refs_by_id: dict) -> None:
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

    Best-effort: any failure here must never break reference extraction, so the
    caller wraps this in try/except.
    """
    group_seq = 0
    for block in root.iter():
        if _localname(block.tag) not in _BLOCK_TAGS:
            continue
        # only the innermost block (no nested block-level descendant)
        nested = 0
        for d in block.iter():
            if _localname(d.tag) in _BLOCK_TAGS:
                nested += 1
                if nested > 1:
                    break
        if nested > 1:
            continue

        text, markers = _serialize_with_markers(block)
        if not markers:
            continue
        spans = _sentence_spans(text)
        # Sentence occurrence -> the references it newly gave its citance to, in
        # document order. Built as the assignments happen so it records exactly
        # the population a group verdict may be aggregated over.
        claimed_by_span: dict = {}
        for pos, rids, mtext in markers:
            sentence = _sentence_for(pos, spans)
            span_i = _span_index(pos, spans)
            for rid in rids:
                ref = refs_by_id.get(rid)
                if ref is None or ref.citance:    # first citance wins
                    continue
                ref.citance = sentence
                if not ref.cited_reference_marker:
                    ref.cited_reference_marker = mtext
                claimed_by_span.setdefault(span_i, []).append(ref)

        # One group per sentence occurrence that claimed at least one reference.
        # Numbered in document order across the whole article so the id is stable
        # and reproducible from the XML alone.
        for span_i in sorted(claimed_by_span):
            members = claimed_by_span[span_i]
            group_seq += 1
            pmcid = members[0].source_pmcid or members[0].source_pmid or "doc"
            group_id = f"{pmcid}:g{group_seq:02d}"
            member_ids = list(dict.fromkeys(m.citation_id for m in members))
            for ref in members:
                ref.citance_group_id = group_id
                ref.citance_group_members = list(member_ids)


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
    for i, ref in enumerate(root.iter("ref")):
        cit = _citation_node(ref)
        if cit is None:
            continue
        claimed = ClaimedRef(
            title=_text(_first(cit, "article-title","part-title", "chapter-title")),
            authors=_authors_from(cit),
            year=_year_from(cit),
            journal=_text(_first(cit, "source")),
            claimed_pmid=_pub_id(cit, "pmid"),
            claimed_doi=_pub_id(cit, "doi"),
            raw=_text(cit),
            volume=_direct_text(cit, "volume"),
            pages=_pages_from(cit),
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
        link_citances(root, refs_by_id)
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
                    print(f"[parse-skip] {pmcid}{suffix}: {e}")
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
                    print(f"[parse-skip] {fn}: {e}")
