"""cre/f1/sentence_spans.py -- deterministic sentence segmentation and alignment.

WHY THIS EXISTS (ZD 2026-08-11, DEC-047)
----------------------------------------
``coverage_v3`` used to ask the model to REPRODUCE source text verbatim and then
reject the verdict when it could not. Three commits treated successive symptoms of
that as typos -- a label contradicting its own text, a two-passage span stitched
with an ellipsis, and finally run 4, where CR42 quarantined on "an engaged claim
needs at least one evidence span" and lost all six of its claims.

The literature says verbatim generation is the outlier. MultiVerS (Findings of NAACL
2022) selects rationales with a classification head over sentence-boundary tokens.
Sarol et al. (Bioinformatics 2024, PMID 38924508) retrieve candidate sentences with
BM25 + MonoT5 and hand those to the verifier. ReClaim (Findings of NAACL 2025) emits
sentence-level citations because passage-level attribution "falls short in
verifiability." FullCite (arXiv 2606.07130) tested prompt-based verbatim generation
against post-hoc alignment head to head and measured Snippet-F1 12.80% -> 61.87%
(ASQA) and 6.18% -> 24.23% (BioASQ) in alignment's favour.

So the judge SELECTS. This module cuts each section into addressable units, and
``coverage_prompts_v3`` shows the model their ids. A span becomes verbatim BY
CONSTRUCTION -- the text is read out of the section, so there is nothing left for
the model to get wrong -- and a table row stops being a special case, because
pointing at a row costs the same as pointing at a sentence.

WHY IT IS NOT IN fulltext_reader
--------------------------------
The spec allowed either. Here, because segmentation is a PURE function of a
section's text and putting it in the reader would change the reader's output
contract: every ``fulltext_pmid_*.json`` cache entry would be invalidated
(``_cache_is_usable`` checks a required-key set), and ``f7_entity.SectionText``
validates the section dicts it is handed. Neither cost buys anything, because purity
already gives the property that matters: the same section text yields the same ids
in the prompt and at resolve time, in this process or a later one. The judge
segments ONCE per reference and reuses it across that reference's claims.

The consequence to be aware of: a stored ``sentence_ids`` value only means something
relative to the segmenter that produced it. :func:`segmenter_provenance` is written
into the run manifest for exactly that reason, and the verdict record also stores the
RESOLVED TEXT so the artifact stays readable even if this module later changes.

WHAT SEGMENTATION IS NOT
------------------------
Not a model. Deterministic regex over the text, no clock, no randomness, no network
-- an id that moved between the prompt and the resolve would make a verdict cite a
sentence it never saw.

BOUNDARY POLICY, and it deliberately errs toward MERGING
-------------------------------------------------------
A boundary needs a terminator, whitespace, and a next character that looks like the
start of a sentence: an uppercase letter or an opening quote/bracket. A digit does
NOT open a sentence here, which is what keeps "Fig. 2", "e.g. 2 mm" and "Eq. 4"
whole; the cost is that a prose sentence genuinely beginning with a numeral is merged
into its predecessor. That trade is deliberate and one-directional: a MERGED unit
still contains the evidence verbatim and merely reads long, while a SPLIT unit can
cut a citation group or a p-value in half and hand back a fragment that misrepresents
the source. Abbreviations that are followed by a capital ("Fig. S1", "Dr. Smith") and
single-letter initials ("E. coli", "J. Smith") are protected explicitly.
"""
from __future__ import annotations

import re

#: Stamped into the run manifest. A stored sentence id is only interpretable
#: relative to the segmenter that produced it, so bump the version on ANY change to
#: boundary behaviour -- ids from two versions are not comparable.
SEGMENTER_NAME = "cre_regex_sentence_v1"
SEGMENTER_VERSION = 1

#: Labels whose text is ROW-STRUCTURED, one record per line, and must never be cut
#: on sentence punctuation. ``fulltext_reader`` renders a table as a caption block
#: plus one pipe-delimited line per row, and those rows are full of periods that end
#: no sentence ("3.14", "P < 0.05", abbreviated genera). One row is one unit: that is
#: the whole reason table content stopped being a special case.
ROW_UNIT_LABELS = frozenset({"table"})

#: Prefix used for every unit id, scoped to its section -- ``discussion:s2`` and
#: ``table:s2`` are different units, and the label always travels with the id.
ID_PREFIX = "s"

# Abbreviations that end in a period and can be followed by a capital, so the
# "next char is uppercase" rule cannot separate them from a real boundary. Stored
# lowercased, with the trailing period.
_PROTECTED_ABBREVIATIONS = frozenset({
    "fig.", "figs.", "eq.", "eqs.", "ref.", "refs.", "no.", "nos.", "ch.",
    "sec.", "vol.", "pp.", "al.", "vs.", "cf.", "approx.", "est.", "min.",
    "max.", "dr.", "prof.", "mr.", "mrs.", "ms.", "st.", "jr.", "sr.",
    "inc.", "ltd.", "co.", "dept.", "univ.", "i.e.", "e.g.", "etc.",
    "spp.", "sp.", "subsp.", "var.", "cv.", "syn.",
})

# A boundary candidate: a terminator (with any closing brackets/quotes that trail
# it), then whitespace, then something that opens a sentence. The next-character
# class excludes digits on purpose -- see BOUNDARY POLICY in the module docstring.
_BOUNDARY_RE = re.compile(r'([.!?][)\]"\'”’]*)\s+(?=[A-Z“"\'(\[])')

# The token immediately before a candidate terminator, for abbreviation checks.
_TRAILING_TOKEN_RE = re.compile(r'(\S+)$')

_WORD_RE = re.compile(r"[0-9a-z]+")


def segmenter_provenance() -> dict:
    """The segmenter identity to record in a run manifest."""
    return {"name": SEGMENTER_NAME, "version": SEGMENTER_VERSION}


def _is_protected(prefix: str) -> bool:
    """True when the text ending at a candidate boundary ends in an abbreviation.

    Two cases the uppercase-next rule cannot catch: a known abbreviation followed by
    a capital ("Fig. S1"), and a single-letter initial ("E. coli" reads as lowercase
    and is safe, but "J. Smith" is not)."""
    match = _TRAILING_TOKEN_RE.search(prefix)
    if not match:
        return False
    token = match.group(1).lower()
    if token in _PROTECTED_ABBREVIATIONS:
        return True
    # A single letter plus period is an initial, never a sentence end. Two letters
    # ("Ab.") are left alone: real words that short do occur.
    return bool(re.fullmatch(r"[a-z]\.", token))


def split_sentences(text: str) -> "list[str]":
    """Split one line of prose into sentences. Deterministic; see BOUNDARY POLICY.

    Never returns an empty string, and the concatenation of the results always
    recovers the input up to the whitespace at the boundaries -- so a unit is always
    a verbatim substring of the line it came from."""
    line = text.strip()
    if not line:
        return []
    out, start = [], 0
    for match in _BOUNDARY_RE.finditer(line):
        end = match.end(1)
        if _is_protected(line[start:end]):
            continue
        piece = line[start:end].strip()
        if piece:
            out.append(piece)
        start = match.end()
    tail = line[start:].strip()
    if tail:
        out.append(tail)
    return out or [line]


def segment_section(label: str, text: str) -> "list[dict]":
    """Cut one section into addressable units: ``[{"id": "s1", "text": ...}, ...]``.

    Ids are ``s1``..``sN`` in DOCUMENT ORDER, scoped to this section. Lines are the
    first cut -- which is what preserves ``fulltext_reader``'s block and row
    structure -- and prose lines are then cut into sentences. A
    :data:`ROW_UNIT_LABELS` section stops at lines, so one table row is one unit.

    PURE: same ``(label, text)`` in, same units out, always."""
    lines = [line.strip() for line in str(text or "").split("\n")]
    lines = [line for line in lines if line]
    pieces: list = []
    for line in lines:
        if label in ROW_UNIT_LABELS:
            pieces.append(line)
        else:
            pieces.extend(split_sentences(line))
    return [{"id": f"{ID_PREFIX}{index}", "text": piece}
            for index, piece in enumerate(pieces, start=1)]


def segment_sections(sections) -> "dict[str, list[dict]]":
    """``{label: units}`` for every section carrying text, in document order.

    Keyed on LABEL because that is what a span cites. A document with two sections
    under one label has them concatenated into a single id space, which matches how
    ``coverage_prompts_v3`` renders them -- one ``[label]`` block per label."""
    out: dict = {}
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        text = str(section.get("text") or "")
        if not text.strip():
            continue
        label = str(section.get("label") or "")
        units = out.setdefault(label, [])
        for unit in segment_section(label, text):
            units.append({"id": f"{ID_PREFIX}{len(units) + 1}",
                          "text": unit["text"]})
    return out


def _words(text: str) -> set:
    return set(_WORD_RE.findall(str(text or "").lower()))


def word_jaccard(left: str, right: str) -> float:
    """Word-level Jaccard similarity, the measure FullCite aligns with.

    Set-based, so it ignores word order and repetition; case- and
    punctuation-insensitive, because the drift being corrected for is a model
    retyping prose, not a different sentence. Either side empty is 0.0 -- an empty
    quote matches nothing, and returning 1.0 for two empties would align a span to
    an arbitrary sentence."""
    a, b = _words(left), _words(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def best_alignment(quoted: str, units) -> "tuple[dict | None, float]":
    """The unit most similar to ``quoted``, and its score. ``(None, 0.0)`` if none.

    BEST match, not first-over-threshold: two sentences in one section can both
    clear the floor while only one is the passage the judge meant, and taking the
    first would silently prefer document order over similarity. Ties resolve to the
    earlier unit, so the result stays deterministic.

    Applies no threshold itself -- the caller owns that policy and the score it
    records."""
    best, best_score = None, 0.0
    for unit in units or []:
        score = word_jaccard(quoted, unit.get("text", ""))
        if score > best_score:
            best, best_score = unit, score
    return best, best_score
