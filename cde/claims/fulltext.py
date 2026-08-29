"""cde/claims/fulltext.py -- the band's cited-paper FULL-TEXT reader.

The companion to ``evidence_reader`` under DEC-030 (evidence scope is full text,
not the abstract). ``evidence_reader.fetch_abstract`` stops at EFetch
``db=pubmed, rettype=abstract``; this module fetches the cited paper's PMC JATS
body and returns its sections, plus -- the part DEC-032 actually needs -- a
statement of WHAT WAS RETRIEVED.

Why the completeness signal exists (DEC-032): the band may treat SILENCE as
evidence of absence ("the cited paper never says this") only when retrieval was
complete. Without a retrieved-vs-missing statement, an empty result is
indistinguishable between "the paper does not support the claim" and "we failed
to read the paper", and the second silently manufactures an F6. So every result
carries ``retrieval_complete`` and, when False, a named reason.

CONTRACT (the injected-callable shape a consumer will expect):
    fetch_fulltext(pmid, *, api_key="", email=DEFAULT_EMAIL, session=None,
                   cache_dir=None, resolve_pmcid=None, fetch_xml=None)
        -> dict | None

``None`` means ONLY "this PMID cannot be attempted at all" (malformed / empty
PMID) -- it is not a failure channel. Every attemptable PMID returns a dict:

    {
      "pmid": str,
      "pmcid": str | None,          # None = no PMC full text for this PMID
      "resolved": bool,             # PMCID found AND EFetch db=pmc returned a
                                    #   parseable document
      "sections": [ {"label", "title", "text", "content_sha256"}, ... ],
      "sections_present": [str],    # sorted distinct labels emitted; reported,
                                    #   never gated on (DEC-041)
      "retrieval_complete": bool,   # the DEC-032 predicate
      "incomplete_reasons": [str],  # nonempty EXACTLY when complete is False
      "sanitized_paths": [str],     # see UNICODE below; normally empty
      "source": "cache" | "live",
    }

``sanitized_paths`` is the one field beyond the bare contract: "reject-or-
sanitize and RECORD it" needs somewhere to record, and it cannot be
``incomplete_reasons`` (that field's invariant is tied to
``retrieval_complete``, and an escaped code point is not a retrieval failure).
It follows the ``judgment_band._safe_json`` ``touched`` convention: one path per
rewrite, so a reader never has to guess which text is verbatim and which is ours.

COMPLETENESS -- FAIL-CLOSED. It answers "did we get the paper's BODY", not "is
this paper shaped like a trial report" (DEC-041). ``retrieval_complete`` is True
only when ALL hold:
  * ``resolved`` is True;
  * the JATS ``<body>`` element was present and parsed;
  * at least one section was emitted, and their combined text clears
    ``NONTRIVIAL_BODY_CHARS``;
  * no parse error was swallowed -- a ``ParseError`` (including the DOCTYPE-level
    "undefined entity" that JATS's external DTD provokes) yields NO sections and
    ``body_unparseable``, never a silently truncated section list.
Anything else is False with a named reason (``no_pmcid``, ``resolver_error``,
``no_body``, ``body_unparseable``, ``body_too_small``). When in doubt, False:
DEC-032 makes False the safe direction, because it HOLDS an item instead of
flagging it.

``no_pmcid`` vs ``resolver_error`` -- THE ONE DISTINCTION THAT CORRUPTED A NUMBER.
``no_pmcid`` means the resolver ANSWERED and this article has no PMC full text.
``resolver_error`` means the resolver did not answer: transport failure, non-200,
a body that is not JSON, or a payload whose status is not ``"ok"``. Both hold
under DEC-032 -- an incomplete retrieval holds rather than flags, and that did not
change -- but they are not the same fact and must never share a label. Measured
2026-08-11: an ELink server-side fault returned HTTP 200 with a raw newline inside
its ``ERROR`` value, every ``r.json()`` raised, and ALL 25 distinct cited PMIDs in
calibration run 1 came back ``no_pmcid``. Three of them had PMCIDs
(``30140736`` -> ``PMC6105232``, ``32382079`` -> ``PMC7206102``, ``26372954`` ->
``PMC4586821``). The run produced zero coverage judgments and the outage was
indistinguishable from an OA-subset ceiling. This label is what makes it
distinguishable.

An earlier rule also required a ``results`` or ``methods`` section. That made
completeness STRUCTURALLY UNREACHABLE for every non-IMRAD paper -- reviews and
perspectives included -- so under DEC-032 every claim judged against a review
would hold forever, and reviews are F3's central case. What sections a document
actually yielded is now REPORTED, in ``sections_present``, and gates nothing.

Content that can never be retrieved -- supplementary files, results that exist
only inside a figure image -- is NOT a completeness failure. Counting it as one
would make ``retrieval_complete`` unreachable for ordinary papers and hold the
whole corpus. It is the documented limit of scope (taxonomy amendment SecA).

SECTION LABELS. This module emits a SUPERSET of ``f7_entity.SectionText``'s
vocabulary: ``results``, ``methods``, ``table``, ``figure`` (the four F7 accepts)
plus ``discussion``, ``intro``, ``other``. The F7 evidence builder filters; the
reader never drops a section, because a dropped section is unrecoverable
downstream while an extra one is free to ignore.

NAMESPACES. Namespaces are stripped on load, so a default-namespaced JATS
document and an un-namespaced one produce byte-identical output. This is
deliberate: ``parser.py`` matches with namespace-naive ``find()`` paths and
therefore extracts nothing from a default-namespaced document. That defect must
not be reproduced here, and it is covered by a paired fixture in the tests.

NONE-VS-EMPTY. A section's ``text`` is never blank; a section with no text of
its own is not emitted at all (a pure container ``<sec>`` contributes only
through its children). No sentinel strings, ever.

CACHE. One JSON file per PMID under ``cache_dir``, read before any HTTP request
and written ONLY when ``resolved`` is True. ``no_body`` and ``body_too_small``
are cached: they are stable properties of the fetched document, not transient
failures. ``no_pmcid``, ``resolver_error`` and ``body_unparseable`` are NOT
cached, so a later run can succeed -- and ``resolver_error`` least of all, since
caching an outage would serve it back as a settled fact about the article on every
later run. A cache entry that is corrupt, malformed, missing a required key,
or whose section hashes no longer match its text is ignored and refetched -- F7
checks ``content_sha256`` at construction, so a cache that violates the
invariant must never be handed to it. Requiring ``sections_present`` is what
retires every entry written under the pre-DEC-041 completeness rule.

UNICODE. Fetched XML can carry a lone surrogate, which UTF-8 cannot encode.
``ET.fromstring`` raises ``UnicodeEncodeError`` (not ``ParseError``) on one, and
a surrogate that reached a section would kill a later JSONL write far from here.
Both are closed at the boundary: surrogates in the fetched XML are escaped ONLY
at the offending code points -- valid non-ASCII (accents, CJK, emoji) is
preserved verbatim, since a blanket ASCII fold would silently narrow the corpus
-- and every extracted string is re-checked before it is hashed.

SEAMS. ``resolve_pmcid`` and ``fetch_xml`` are injected callables; this module
supplies live defaults built on the shared NCBI limiter (``ratelimit.NCBI``, one
per-IP budget across every caller) and ``ncbi_meta``'s PMC ID Converter. Both are
injected in tests, so the whole module is exercised offline with no network.

The resolver seam is the one place in this module that lets an exception THROUGH
rather than mapping it to a value: a ``ResolverError`` (or a bare
``RequestException``) out of ``resolve_pmcid`` becomes ``resolver_error``, and an
empty return becomes ``no_pmcid``. An injected resolver that swallows its own
failures and returns ``""`` will therefore be reported as ``no_pmcid`` -- it has
told us nothing else -- so a resolver that wants the distinction must raise.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Callable, Optional

import requests

from ..refs.ncbi_meta import (DEFAULT_EMAIL, EFETCH, NCBI, TOOL, ResolverError,
                        ncbi_pmids_to_pmcids, request_with_retry)

# ==========================================================================
# 1. Vocabulary -- labels, reasons, sources
# ==========================================================================
LABEL_RESULTS = "results"
LABEL_METHODS = "methods"
LABEL_TABLE = "table"
LABEL_FIGURE = "figure"
LABEL_DISCUSSION = "discussion"
LABEL_INTRO = "intro"
LABEL_OTHER = "other"

#: Every label this reader can emit. A superset of ``f7_entity._SECTION_LABELS``.
SECTION_LABELS = frozenset({
    LABEL_RESULTS, LABEL_METHODS, LABEL_TABLE, LABEL_FIGURE,
    LABEL_DISCUSSION, LABEL_INTRO, LABEL_OTHER,
})

#: Minimum combined section text, in characters, for a parsed body to count as
#: retrieved. A CHOSEN FLOOR, not a derived one: it separates a real body from a
#: stub or a front-matter-only record, and nothing about 1000 is principled.
#: Tunable -- raise it if stubs slip through, lower it if short correspondence
#: and letters are being held. Tests that are not about the floor override it.
NONTRIVIAL_BODY_CHARS = 1000

#: The resolver ANSWERED, and this article has no PMC full text. That is the only
#: thing it means. See REASON_RESOLVER_ERROR for what used to be folded in here.
REASON_NO_PMCID = "no_pmcid"
REASON_NO_BODY = "no_body"
REASON_BODY_UNPARSEABLE = "body_unparseable"
REASON_BODY_TOO_SMALL = "body_too_small"
#: The resolver did not answer at all -- transport failure, non-200, unparseable
#: body, or a bad payload status. A DIFFERENT FACT from ``no_pmcid``: one is about
#: the article, the other is about the wire. Conflating them cost calibration run
#: 1 its entire coverage yield and read as an OA-subset ceiling (ZD 2026-08-11).
REASON_RESOLVER_ERROR = "resolver_error"

INCOMPLETE_REASONS = frozenset({
    REASON_NO_PMCID, REASON_NO_BODY, REASON_BODY_UNPARSEABLE,
    REASON_BODY_TOO_SMALL, REASON_RESOLVER_ERROR,
})

# --- WHY the body is missing, split by WHAT A CONSUMER SHOULD DO ABOUT IT ----
# The same distinction this module already draws between `no_pmcid` and
# `resolver_error` -- "one is about the article, the other is about the wire" --
# generalized, because a downstream scope decision turns on it.

#: THE ARTICLE HAS NO RETRIEVABLE BODY. The resolver answered and there is no PMC
#: full text, or there is a record with no body. Retrying returns the same answer
#: forever, so the abstract is not a downgrade -- it is the whole of the evidence
#: that will ever exist for this work, and judging at abstract scope is the right
#: and only answer.
BODY_ABSENT_REASONS = frozenset({REASON_NO_PMCID, REASON_NO_BODY})

#: WE FAILED TO GET OR READ A BODY THAT MAY WELL EXIST. A transport failure, XML
#: we could not parse, or a body that came back too small to be the real thing.
#: A retry may return it. Falling back to abstract scope here would SILENTLY
#: DOWNGRADE THE EVIDENCE SCOPE of a row that was entitled to full text, and the
#: record would carry an honest-looking abstract-scope verdict for a paper whose
#: body we simply failed to fetch. Hold instead, and let the retry happen.
BODY_RETRIEVAL_FAILED_REASONS = frozenset({
    REASON_RESOLVER_ERROR, REASON_BODY_UNPARSEABLE, REASON_BODY_TOO_SMALL})


def body_is_permanently_absent(incomplete_reasons) -> bool:
    """True only when EVERY reason says the article itself has no body.

    Fails closed in both directions that matter. An empty or unrecognised reason
    list is NOT "permanently absent": we do not know why the body is missing, and
    guessing "the article has none" is exactly the downgrade this function exists
    to prevent. A mixed list is treated as a retrieval failure, because one
    unfetched body is enough to make full text the scope the row was entitled to.
    """
    reasons = [str(r) for r in (incomplete_reasons or [])]
    if not reasons:
        return False
    return all(r in BODY_ABSENT_REASONS for r in reasons)

SOURCE_CACHE = "cache"
SOURCE_LIVE = "live"

#: Recorded in ``sanitized_paths`` when the fetched XML carried a code point
#: UTF-8 cannot encode. Not a section path: the rewrite happens before parsing,
#: because the parser itself cannot survive the character.
SANITIZED_XML_TEXT = "xml_text"

# A PMID is ASCII digits. Anything else cannot be attempted, so it is the one
# input that returns None rather than a dict. Explicitly [0-9] rather than \d,
# which also matches Arabic-Indic and other Unicode digits -- those are not
# PMIDs, and admitting one would spend a live NCBI request to learn so.
_PMID_RE = re.compile(r"[0-9]+")

# Elements that become sections of their own. They are excluded from an
# ancestor's own text so table and figure content is not emitted twice.
_STRUCTURAL_TAGS = frozenset({"sec", "table-wrap", "fig"})

# Containers whose children are themselves block-level, so they are expanded
# into blocks rather than flattened into one. EFetch routinely emits
# ``<caption><title>X</title><p>Y</p></caption>`` with no whitespace between the
# two, and flattening that yields "X.Y" -- two sentences fused into a non-word.
_BLOCK_CONTAINERS = frozenset({"caption"})


# ==========================================================================
# 2. Unicode boundary -- the same discipline as judgment_band._safe_json
# ==========================================================================
# Deliberately duplicated rather than imported from judgment_band: the band
# INJECTS this reader, so the reader must not import the band, or wiring it into
# assemble_evidence later would close an import cycle. The transform is byte-for-
# byte the one judgment_band._safe_text applies.
def _safe_text(s: str) -> str:
    """Force a string into text that always survives the JSONL write."""
    return s.encode("utf-8", "backslashreplace").decode("ascii", "replace")


_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _desurrogate(text: str) -> "tuple[str, bool]":
    """Escape ONLY the code points UTF-8 cannot encode; report whether any were.

    A whole-string ``_safe_text`` here would ASCII-fold an entire paper --
    accents, CJK, Greek letters in the results -- to remove one bad character.
    That silently narrows the corpus, which is a worse defect than the crash this
    guards against, so the rewrite is confined to the offending characters."""
    if not _SURROGATE_RE.search(text):
        return text, False
    return _SURROGATE_RE.sub(lambda m: _safe_text(m.group()), text), True


def _sanitize(value: str, path: str, touched: list) -> str:
    """Return ``value`` unchanged, or its escaped form with ``path`` recorded."""
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        touched.append(path)
        return _safe_text(value)
    return value


def _sha256_text(text: str) -> str:
    """The F7 ``SectionText.content_sha256`` convention, kept identical."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_section_text(value: str) -> str:
    """THE one representation of a section's text. Producer and consumer share it.

    A section is addressed downstream BY ITS HASH: F5 and F7 bind every evidence
    span to ``content_sha256``, and that is what makes a span checkable against
    the paragraph it claims to come from. So there may be exactly one byte
    sequence per section. There were two. This module emitted the text as JATS
    rendered it; ``f5_evidence_store`` canonicalized before hashing, and the two
    digests disagreed whenever the rendering left a trailing space on a line or a
    decomposed accent -- 13 references on the natural run died as
    ``quarantine_parse`` for it (e.g. PMC11624350:bib31, "fulltext section 8
    content_sha256 does not match stored text").

    The fix is to AGREE, never to relax: the comparison downstream stays strict
    and keeps failing on mismatch, because a loosened comparison would let an
    evidence span bind to the wrong paragraph and produce a confident wrong label
    with no error anywhere. Applying this at the producer makes the consumer's
    normalization a no-op, so one hash addresses one representation.

    NFC, LF line endings, no trailing whitespace on any line, stripped ends --
    byte-for-byte ``f5_evidence_store._normalize_text``, which is the consumer
    this has to match.
    """
    text = unicodedata.normalize("NFC", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).strip()


# ==========================================================================
# 3. Namespace-agnostic XML helpers
# ==========================================================================
def _local(tag) -> str:
    """Local name of a tag; '' for comments / processing instructions."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _strip_namespaces(root):
    """Rewrite every tag and attribute name to its local name, in place.

    Stripping on load (rather than matching ``{*}sec`` at each call site) is what
    makes a default-namespaced document and an un-namespaced one produce the same
    tree, and therefore the same output -- there is no second code path that can
    drift."""
    for elem in root.iter():
        if isinstance(elem.tag, str) and elem.tag.startswith("{"):
            elem.tag = elem.tag.rsplit("}", 1)[1]
        for key in [k for k in elem.attrib if k.startswith("{")]:
            elem.attrib[key.rsplit("}", 1)[1]] = elem.attrib.pop(key)
    return root


def _ws(text: str) -> str:
    """Collapse every whitespace run to a single space and strip.

    JATS is pretty-printed, so raw text carries the indentation of the file it
    came from. Collapsing here is also what makes the namespaced and
    un-namespaced fixtures compare equal."""
    return " ".join(text.split())


def _all_text(elem) -> str:
    return "".join(elem.itertext()) if elem is not None else ""


def _first_child(elem, name: str):
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _text_excluding(elem, skip) -> str:
    """All of ``elem``'s text except ``skip`` and every structural subtree.

    Structural descendants are skipped at any depth -- JATS allows a ``<fig>``
    inside a ``<p>`` -- so a figure caption is not duplicated into its enclosing
    section's text as well as its own."""
    parts = [elem.text or ""]
    for child in elem:
        if child is not skip and _local(child.tag) not in _STRUCTURAL_TAGS:
            parts.append(_text_excluding(child, skip))
        parts.append(child.tail or "")
    return "".join(parts)


def _blocks_excluding(elem, skip) -> list:
    """``elem``'s own content as blocks: one per direct non-structural child.

    Blocks are joined with a blank line, the same paragraph convention
    ``evidence_reader`` uses to join a structured abstract, so a section reads
    the way the paper's paragraphs do rather than as one collapsed line."""
    blocks = []
    if (elem.text or "").strip():
        blocks.append(_ws(elem.text))
    for child in elem:
        if child is not skip and _local(child.tag) not in _STRUCTURAL_TAGS:
            if _local(child.tag) in _BLOCK_CONTAINERS:
                blocks.extend(_blocks_excluding(child, skip))
            else:
                text = _ws(_text_excluding(child, skip))
                if text:
                    blocks.append(text)
        if (child.tail or "").strip():
            blocks.append(_ws(child.tail))
    return blocks


# ==========================================================================
# 4. Labelling
# ==========================================================================
# Leading numbering on a real heading ("3.1. Materials and Methods", "2 Results").
# Digits only, and a separator is required, so it can never eat a leading word.
#: Leading enumeration: an arabic number ("2", "2.1") or a roman numeral ("ii",
#: "iv"), optionally bracketed, and REQUIRING a trailing separator or whitespace.
#:
#: That trailing requirement is load-bearing, not defensive. Roman numerals are
#: spelled with letters that ordinary headings start with -- ``d`` in
#: "Discussion", ``m`` in "Methods", ``i`` in "Introduction``, ``c`` in
#: "Conclusion" -- so a numeral alternative that could match without a following
#: separator would eat that first character and silently mislabel the section.
#: Matching is done on the case-folded heading, hence lowercase numeral letters.
_HEADING_ENUM_RE = re.compile(
    r"^[(\[]?\s*(?:\d+(?:\.\d+)*|[ivxlcdm]+)\s*(?:[).\]:]+|\s)\s*")

# Keyword -> label, checked IN ORDER against the heading's tokens, so position
# encodes precedence: results > methods > discussion > intro. A heading naming
# several takes the first, which is why "Results and Discussion" is results.
#
# Containment rather than equality: real headings qualify their section name
# ("Research Design and Methods", "Methodology", "Summary of findings") and a
# phrase list can only ever chase those variants one at a time. Keywords are
# stems, so plurals and compounds fall out for free.
#
# "concluding" is listed beside "conclusion" because it is not a superstring of
# it -- concludING vs concluSION -- so "Concluding remarks" would otherwise stop
# matching. Essay-shaped papers close with these instead of Discussion; without
# them a review's closing argument lands in ``other``.
_HEADING_KEYWORDS = (
    ("result", LABEL_RESULTS),
    ("method", LABEL_METHODS),
    ("materials", LABEL_METHODS),
    ("discussion", LABEL_DISCUSSION),
    ("conclusion", LABEL_DISCUSSION),
    ("concluding", LABEL_DISCUSSION),
    ("summary", LABEL_DISCUSSION),
    ("introduction", LABEL_INTRO),
    ("background", LABEL_INTRO),
)

# JATS sec-type is a controlled-ish vocabulary of pipe-joined tokens
# ("materials|methods", "intro", "results"). Token precedence, most specific
# evidence first.
_SEC_TYPE_TOKENS = (
    ("results", LABEL_RESULTS),
    ("methods", LABEL_METHODS),
    ("method", LABEL_METHODS),
    ("discussion", LABEL_DISCUSSION),
    ("intro", LABEL_INTRO),
    ("introduction", LABEL_INTRO),
    ("background", LABEL_INTRO),
)


def _normalize_heading(title: str) -> str:
    """Case-fold a heading and drop its enumeration and trailing punctuation."""
    text = _ws(title).casefold()
    text = _HEADING_ENUM_RE.sub("", text)
    return text.strip(" .:;-").strip()


def _label_from_heading(title: str) -> "str | None":
    """The label a heading names, or None when it names none of them.

    Token containment on the enumeration-stripped, case-folded heading, in
    :data:`_HEADING_KEYWORDS` precedence order. Splitting into tokens first keeps
    a keyword from matching across a word boundary, so only a whole word that
    CONTAINS the stem counts."""
    norm = _normalize_heading(title)
    if not norm:
        return None
    tokens = [token for token in re.split(r"[^a-z0-9]+", norm) if token]
    for keyword, label in _HEADING_KEYWORDS:
        if any(keyword in token for token in tokens):
            return label
    return None


def _label_from_sec_type(sec_type: str) -> "str | None":
    """The label a JATS ``sec-type`` names, or None."""
    tokens = set(re.split(r"[^a-z0-9]+", _ws(sec_type).casefold()))
    tokens.discard("")
    if not tokens:
        return None
    for token, label in _SEC_TYPE_TOKENS:
        if token in tokens:
            return label
    return None


def _label_for_sec(sec, title: str, inherited: "str | None") -> str:
    """``sec-type`` first, then the heading, then the enclosing section's label.

    Inheritance is what keeps nested sections usefully labelled: JATS routinely
    nests "Primary outcome" under ``<sec sec-type="results">``, and without it
    every such subsection would land in ``other``, hiding real results from the
    F7 evidence filter. A subsection of Results IS results.

    The child's OWN evidence wins when it has any -- ``sec-type`` then heading --
    and the parent's label applies only to a subsection that names nothing. So a
    Discussion-titled subsection inside Results is ``discussion``, and cannot
    inherit its way into evidence F7 should not see."""
    label = _label_from_sec_type(sec.get("sec-type") or "")
    if label is None:
        label = _label_from_heading(title)
    if label is None:
        label = inherited
    return label or LABEL_OTHER


# ==========================================================================
# 5. Section extraction
# ==========================================================================
def _table_rows(table) -> str:
    """Render a ``<table>`` as one line per row, cells separated by ' | '.

    Cell boundaries carry meaning a flat ``itertext()`` destroys: without them a
    row reads as "12 3.4 0.02" and no reader -- human or model -- can tell which
    number belongs to which column. Empty cells are kept so columns stay aligned.
    """
    rows = []
    for elem in table.iter():
        if _local(elem.tag) != "tr":
            continue
        cells = [_ws(_all_text(c)) for c in elem
                 if _local(c.tag) in ("th", "td")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _table_wrap_text(table_wrap, label_elem) -> str:
    """Caption text plus rendered table body, as blocks."""
    blocks = []
    for child in table_wrap:
        if child is label_elem:
            continue
        tables = ([child] if _local(child.tag) == "table"
                  else [t for t in child.iter() if _local(t.tag) == "table"])
        if tables:
            for table in tables:
                rows = _table_rows(table)
                if rows:
                    blocks.append(rows)
        else:
            text = _ws(_all_text(child))
            if text:
                blocks.append(text)
    return "\n\n".join(blocks)


def _make_section(label: str, title: str, text: str) -> "dict | None":
    """A section, or None when it has no text of its own (never emit a blank)."""
    if not text.strip():
        return None
    return {"label": label, "title": title, "text": text}


def _walk(elem, inherited: "str | None", out: list) -> None:
    """Emit ``elem``'s structural descendants as sections, in document order.

    An element's own text is emitted before its structural children are visited,
    which is the reading order of every JATS body in practice (paragraphs precede
    subsections)."""
    for child in elem:
        tag = _local(child.tag)
        if tag == "sec":
            title_elem = _first_child(child, "title")
            title = _ws(_all_text(title_elem)) if title_elem is not None else ""
            label = _label_for_sec(child, title, inherited)
            section = _make_section(
                label, title,
                "\n\n".join(_blocks_excluding(child, title_elem)))
            if section is not None:
                out.append(section)
            # A subsection with no label of its own inherits this one's, but
            # never inherits "other" over a real ancestor label.
            _walk(child, label if label != LABEL_OTHER else inherited, out)
        elif tag == "table-wrap":
            label_elem = _first_child(child, "label")
            title = _ws(_all_text(label_elem)) if label_elem is not None else ""
            section = _make_section(LABEL_TABLE, title,
                                    _table_wrap_text(child, label_elem))
            if section is not None:
                out.append(section)
        elif tag == "fig":
            label_elem = _first_child(child, "label")
            title = _ws(_all_text(label_elem)) if label_elem is not None else ""
            # The caption's own <title> stays in the text: for a figure the
            # caption IS the retrievable content, unlike a section heading.
            section = _make_section(
                LABEL_FIGURE, title,
                "\n\n".join(_blocks_excluding(child, label_elem)))
            if section is not None:
                out.append(section)
        else:
            # Not structural itself, but JATS allows <fig>/<table-wrap> inside a
            # <p>; descend so those are still reached.
            _walk(child, inherited, out)


def _extract_sections(body) -> list:
    """Every section of a parsed JATS ``<body>``, in document order."""
    out = []
    # Loose text directly under <body>, ahead of the first <sec>. Unlabelled, but
    # never dropped -- a dropped section is unrecoverable downstream.
    lead = "\n\n".join(_blocks_excluding(body, None))
    section = _make_section(LABEL_OTHER, "", lead)
    if section is not None:
        out.append(section)
    _walk(body, None, out)
    return out


def _find_body(root):
    """The article's OWN ``<body>``, or None.

    Only a direct child of an ``<article>`` counts. A ``<sub-article>`` (peer
    review, author response) carries its own body, and reading one of those as
    the cited paper's results would put another document's text under this
    paper's PMCID."""
    articles = [root] if _local(root.tag) == "article" else [
        e for e in root.iter() if _local(e.tag) == "article"]
    for article in articles:
        body = _first_child(article, "body")
        if body is not None:
            return body
    if _local(root.tag) == "body":
        return root
    return None


# ==========================================================================
# 6. Result construction -- the completeness invariant lives here
# ==========================================================================
def _completeness_reasons(sections: list) -> list:
    """Why a parsed body still is not a retrieved body -- or ``[]`` if it is.

    Completeness answers "did we get the paper's body", NOT "is this paper shaped
    like a trial report". The former rule required a ``results`` or ``methods``
    section, which made completeness STRUCTURALLY UNREACHABLE for every
    non-IMRAD paper -- reviews and perspectives included. Reviews are F3's
    central case, so under DEC-032 every claim judged against one would have
    held forever. Recorded as DEC-041.

    A body is retrieved when it yielded at least one section and their combined
    text clears :data:`NONTRIVIAL_BODY_CHARS`. The section check is not redundant
    with the character check: a caller that lowers the floor to zero must still
    not get "complete" out of a body that produced nothing."""
    total = sum(len(section["text"]) for section in sections)
    if sections and total >= NONTRIVIAL_BODY_CHARS:
        return []
    return [REASON_BODY_TOO_SMALL]


def _finalize(pmid: str, pmcid: "str | None", resolved: bool, sections: list,
              reasons: list, sanitized: list, source: str) -> dict:
    """Build the result and enforce the ``incomplete_reasons`` invariant.

    ``retrieval_complete`` is derived, never passed in, so there is no path that
    can report complete-with-a-reason or incomplete-with-none. ``sections_present``
    reports what WAS retrieved rather than gating on it: downstream code that
    genuinely needs results or methods (F7's evidence builder, via
    ``SectionText``'s own label filter) reads this field or filters the sections
    itself. The reader no longer decides that on anyone's behalf."""
    complete = resolved is True and not reasons
    return {
        "pmid": pmid,
        "pmcid": pmcid,
        "resolved": resolved is True,
        "sections": sections,
        "sections_present": sorted({s["label"] for s in sections}),
        "retrieval_complete": complete,
        "incomplete_reasons": [] if complete else list(reasons),
        "sanitized_paths": list(sanitized),
        "source": source,
    }


def _emit_sections(raw_sections: list, sanitized: list) -> list:
    """Sanitize, hash, and freeze the extracted sections.

    Hashing follows sanitization AND canonicalization, in that order:
    ``content_sha256`` must be the hash of the text actually emitted, or F7's
    ``SectionText`` rejects it at construction -- and the text actually emitted
    must already be in the one canonical representation every consumer hashes,
    or F5's strict binding check rejects it instead. See
    :func:`canonical_section_text` for why there is exactly one."""
    out = []
    for index, section in enumerate(raw_sections):
        title = canonical_section_text(
            _sanitize(section["title"], f"sections[{index}].title", sanitized))
        text = canonical_section_text(
            _sanitize(section["text"], f"sections[{index}].text", sanitized))
        out.append({
            "label": section["label"],
            "title": title,
            "text": text,
            "content_sha256": _sha256_text(text),
        })
    return out


# ==========================================================================
# 7. Cache -- drive-first, and never trusted blindly
# ==========================================================================
def _cache_path(cache_dir: str, pmid: str) -> str:
    return os.path.join(cache_dir, f"fulltext_pmid_{pmid}.json")


# ``sections_present`` is load-bearing here beyond its own contract: no entry
# written before DEC-041 has it, so requiring it invalidates every cache entry
# whose ``retrieval_complete`` was computed under the results-or-methods rule.
# Without that, a cache hit would keep serving the old False -- on exactly the
# non-IMRAD papers this change exists to unblock -- and the reasons it carries
# (``no_results_or_methods``) are no longer members of INCOMPLETE_REASONS. This
# reuses the module's existing "unusable entry is ignored and refetched" rule
# rather than adding a versioning mechanism.
_REQUIRED_KEYS = ("pmid", "pmcid", "resolved", "sections", "sections_present",
                  "retrieval_complete", "incomplete_reasons", "sanitized_paths")


def _cache_is_usable(data, pmid: str) -> bool:
    """True only for an entry this module could have written for THIS pmid.

    Only ``resolved`` results are ever written, and F7 re-checks every section
    hash at construction, so an entry that fails either test is treated exactly
    like a corrupt file: ignored and refetched."""
    if not isinstance(data, dict):
        return False
    if any(key not in data for key in _REQUIRED_KEYS):
        return False
    if data["pmid"] != pmid or data["resolved"] is not True:
        return False
    if not isinstance(data["sections"], list):
        return False
    for section in data["sections"]:
        if not isinstance(section, dict):
            return False
        if set(section) != {"label", "title", "text", "content_sha256"}:
            return False
        if not isinstance(section["text"], str) or not section["text"].strip():
            return False
        if section["label"] not in SECTION_LABELS:
            return False
        try:
            # BOTH conditions, and the second is the new one: the stored text
            # must already BE the canonical representation, not merely hash to
            # itself. An entry written before the producer canonicalized is
            # self-consistent and still unusable downstream -- F5 would refuse
            # its binding and the reference would die UNJUDGEABLE on a cache
            # artifact. Treated like any other corrupt entry: ignored, refetched,
            # rewritten canonical.
            if section["content_sha256"] != _sha256_text(section["text"]):
                return False
            if section["text"] != canonical_section_text(section["text"]):
                return False
        except (AttributeError, TypeError, UnicodeEncodeError):
            return False
    return True


def _read_cache(cache_dir: str, pmid: str) -> "dict | None":
    path = _cache_path(cache_dir, pmid)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not _cache_is_usable(data, pmid):
        return None
    data["source"] = SOURCE_CACHE
    return data


def _write_cache(cache_dir: str, pmid: str, result: dict) -> None:
    """Persist a resolved result. Best-effort: a cache failure is never fatal."""
    payload = {k: v for k, v in result.items() if k != "source"}
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir, pmid), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except (OSError, ValueError, UnicodeEncodeError):
        pass


# ==========================================================================
# 8. Live seams
# ==========================================================================
def _live_resolve_pmcid(pmid: str, api_key: str, email: str, session) -> str:
    """PMID -> PMCID via the PMC ID Converter (``ncbi_meta.ncbi_pmids_to_pmcids``).

    PROPAGATES ``ResolverError`` rather than returning ``""``. It deliberately
    does NOT go through ``ncbi_meta.ncbi_pmid_to_pmcid``, whose contract is to
    swallow: swallowing here is exactly the defect: it makes an outage
    indistinguishable from "this article has no PMC full text", and
    :func:`fetch_fulltext` is the one caller that can record the difference.

    A bare ``RequestException`` is normalized to ``ResolverError`` so the seam has
    ONE failure type to propagate, whatever an injected session raises."""
    try:
        return ncbi_pmids_to_pmcids(
            [pmid], api_key, email, session=session).get(str(pmid).strip(), "")
    except requests.RequestException as exc:
        raise ResolverError(f"resolver transport failure: {exc}") from exc


def _live_fetch_xml(pmcid: str, api_key: str, email: str,
                    session) -> "str | None":
    """EFetch ``db=pmc, retmode=xml`` on the shared NCBI limiter."""
    digits = re.sub(r"\D", "", pmcid or "")
    if not digits:
        return None
    params = {"db": "pmc", "id": digits, "retmode": "xml",
              "tool": TOOL, "email": email}
    if api_key:
        params["api_key"] = api_key
    try:
        r = request_with_retry(session, EFETCH, params, limiter=NCBI, timeout=30)
    except requests.RequestException:
        return None
    if r is None or r.status_code != 200 or not r.text.strip():
        return None
    return r.text


# ==========================================================================
# 9. Public entry point
# ==========================================================================
def fetch_fulltext(pmid, *, api_key: str = "", email: str = DEFAULT_EMAIL,
                   session=None, cache_dir: "str | None" = None,
                   resolve_pmcid: "Callable | None" = None,
                   fetch_xml: "Callable | None" = None) -> Optional[dict]:
    """Fetch ``pmid``'s PMC full text plus its retrieval-completeness signal.

    See the module docstring for the contract, the fail-closed completeness
    rules, the cache policy, and the unicode boundary. Returns None ONLY for a
    PMID that cannot be attempted at all."""
    pmid = str(pmid).strip() if pmid is not None else ""
    if not _PMID_RE.fullmatch(pmid):
        return None

    if cache_dir:
        cached = _read_cache(cache_dir, pmid)
        if cached is not None:
            return cached

    resolver = resolve_pmcid or (
        lambda p: _live_resolve_pmcid(p, api_key, email, session))
    fetcher = fetch_xml or (
        lambda c: _live_fetch_xml(c, api_key, email, session))

    sanitized: list = []

    # A resolver that FAILED and a resolver that ANSWERED "no PMC full text" are
    # two different facts, and they used to share the no_pmcid label -- which is
    # how an ELink outage read as an OA-subset ceiling across all 25 cited PMIDs
    # of calibration run 1. Neither result is cached (both return before any
    # _write_cache below), so a later run can still succeed.
    try:
        raw_pmcid = resolver(pmid)
    except (ResolverError, requests.RequestException):
        return _finalize(pmid, None, False, [], [REASON_RESOLVER_ERROR],
                         sanitized, SOURCE_LIVE)
    digits = re.sub(r"\D", "", str(raw_pmcid or ""))
    if not digits:
        return _finalize(pmid, None, False, [], [REASON_NO_PMCID],
                         sanitized, SOURCE_LIVE)
    pmcid = "PMC" + digits

    try:
        xml_text = fetcher(pmcid)
    except requests.RequestException:
        xml_text = None
    if not str(xml_text or "").strip():
        return _finalize(pmid, pmcid, False, [], [REASON_BODY_UNPARSEABLE],
                         sanitized, SOURCE_LIVE)

    # Before parsing, not after: ET.fromstring raises UnicodeEncodeError -- not
    # ParseError -- on a lone surrogate, so an unescaped one is a crash here
    # rather than a bad section later.
    xml_text, was_escaped = _desurrogate(str(xml_text))
    if was_escaped:
        sanitized.append(SANITIZED_XML_TEXT)

    try:
        root = _strip_namespaces(ET.fromstring(xml_text))
    except (ET.ParseError, UnicodeEncodeError, ValueError):
        return _finalize(pmid, pmcid, False, [], [REASON_BODY_UNPARSEABLE],
                         sanitized, SOURCE_LIVE)

    body = _find_body(root)
    if body is None:
        result = _finalize(pmid, pmcid, True, [], [REASON_NO_BODY],
                           sanitized, SOURCE_LIVE)
        if cache_dir:
            _write_cache(cache_dir, pmid, result)
        return result

    sections = _emit_sections(_extract_sections(body), sanitized)
    reasons = _completeness_reasons(sections)
    result = _finalize(pmid, pmcid, True, sections, reasons, sanitized,
                       SOURCE_LIVE)
    if cache_dir:
        _write_cache(cache_dir, pmid, result)
    return result
