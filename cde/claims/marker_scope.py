"""Claim-to-marker attribution: the claims a reference was actually cited FOR.

THE DEFECT THIS EXISTS TO FIX
-----------------------------
``parser.link_citances`` builds one co-citation group per SENTENCE OCCURRENCE,
and the band asks every member of that group about every atomic claim in the
whole sentence -- including claims whose citation markers point at a different
reference.

Measured on the ``PMC13294812`` adjudication packet (2026-08-16, claude-opus-5,
effort high, full text): of 53 flagged claims over 20 flagged references, 17
(32%) were flagged with "the retrieved text never addresses this claim", 16 of
them on a co-cited reference, and all 17 emitted NO evidence span at all. An
adjudicator cannot audit those rows -- there is nothing to point at.

The clean case, verified end to end:

    Notably, such detailed cellular characterisations were not necessary for the
    successful clinical translation of fluorophore-labelled antibodies 52,53 and
    pH sensitive fluorescent micelles 54,55 for intraoperative FL imaging of oral
    SCC disease.

Two marker clusters -- ``52,53`` on *antibodies*, ``54,55`` on *micelles*. All
four references were asked all four claims, and ``B55`` was flagged F6 on
"fluorophore-labelled antibodies were successfully clinically translated". The
verdict was right. The QUESTION was wrong: ``B55`` was cited for micelles.

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is PURE: no network, no model call, no I/O, no clock. It takes a serialized
sentence plus its marker positions and returns clusters; it takes a sentence,
its clusters and a claim list and returns an attribution.

It is NOT allowed to narrow a reference's accountability on a guess. Narrowing
wrongly converts a real fault into a silent clear, and this project is
precision-first in the direction of escalation, not exculpation. Every uncertain
case therefore returns :data:`SCOPE_WHOLE_SENTENCE` -- byte-identically today's
behaviour -- and says why.

THREE PLACES THIS FAILS CLOSED, EACH DELIBERATE
-----------------------------------------------
1. **Style.** The positional cluster rule is not defined for author-year
   citations: the marker text is itself a name containing letters, so "a letter
   between two markers means a new clause" is meaningless. Measured over
   ``corpus_frozen_v1``, 17 of 20 documents are numeric and 3 are author-year
   (``PMC12967000``, ``PMC13219232``, ``PMC13295838``). Only a document whose
   markers are ALL numeric is clustered; anything else keeps whole-sentence
   behaviour and the record says which rule applied.
2. **Joining beats splitting.** Two markers are put in the SAME cluster whenever
   the text between them is only separator punctuation. Over-joining reproduces
   today's behaviour (both references keep being asked both clauses' claims);
   over-splitting stops asking a reference about a claim it was genuinely cited
   for. Only one of those two errors can manufacture a false clear, so the
   separator set is deliberately permissive.
3. **Attribution is all-or-nothing per sentence.** A sentence is scoped only when
   EVERY claim attributes to exactly one cluster AND every cluster receives at
   least one claim. One unattributable claim reverts the whole sentence.
   Per-claim fallback was considered and rejected: a claim shared by two clusters
   would then be aggregated inside each cluster separately, so a sibling that
   established it would no longer excuse the other cluster's members -- a silent
   narrowing of exculpation, dressed as a partial fix.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------
# Citation style. Detected per DOCUMENT, all-or-nothing, exactly like
# parser._positional_numbering: a marker model that is wrong anywhere is not
# trusted anywhere.
# --------------------------------------------------------------------------
CITATION_STYLE_NUMERIC = "numeric"
CITATION_STYLE_AUTHOR_YEAR = "author-year"
#: Markers exist but are neither wholly numeric nor letter-bearing, or there are
#: no markers at all. Never clustered.
CITATION_STYLE_UNKNOWN = "unknown"

# --------------------------------------------------------------------------
# Scope status + the reasons a sentence stayed whole.
# --------------------------------------------------------------------------
#: Claims were attributed to marker clusters; this reference was asked only its
#: own cluster's claims.
SCOPE_SCOPED = "scoped"
#: Today's behaviour: this reference was asked every claim in the sentence.
SCOPE_WHOLE_SENTENCE = "whole_sentence"

REASON_SINGLE_CLUSTER = "single_marker_cluster"
REASON_NOT_NUMERIC = "citation_style_not_numeric"
REASON_NO_CLAIMS = "no_atomic_claims"
#: At least one claim could not be attributed to exactly one cluster -- it spans
#: clusters, or nothing distinguishes them. The acceptance matrix names this one.
REASON_AMBIGUOUS = "ambiguous"
#: Every claim attributed, but some cluster came away with none. A reference
#: cited for a clause that yielded no claim would otherwise be judged against an
#: EMPTY claim list, i.e. silently held out of the coverage substrate entirely.
REASON_CLUSTER_WITHOUT_CLAIMS = "cluster_without_claims"

#: A (reference, claim) pair the reference was NEVER ASKED about. DEC-079's F3
#: gate and the tautological queue audit are the same class: a claim never put to
#: a reference and a claim the reference failed must never be indistinguishable.
DISPOSITION_NOT_ASKED = "not_asked"
DISPOSITION_ASKED = "asked"


# --------------------------------------------------------------------------
# 1. Style detection
# --------------------------------------------------------------------------
# Every dash Unicode actually uses for a citation range, so an en dash is not a
# miss. Mirrors parser._RANGE_DASH_RE's coverage.
_DASHES = r"\-‐‑‒–—―"
#: A numeric marker: one number, or several joined inside a single <xref>.
_NUMERIC_MARKER_RE = re.compile(rf"^\d+(?:\s*[,{_DASHES}]\s*\d+)*$")
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_BARE_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


def detect_citation_style(marker_texts) -> str:
    """The document's citation style from every marker it renders.

    ALL-OR-NOTHING. One letter-bearing marker refuses the whole document, because
    the cluster rule reads "a non-separator character between two markers starts a
    new clause" and an author-year marker IS non-separator characters. Refusing
    costs nothing -- the document keeps today's whole-sentence behaviour.

    Empty markers are ignored rather than counted against the document: an
    ``<xref>`` that renders nothing is evidence of neither style.
    """
    labels = [t.strip() for t in marker_texts
              if isinstance(t, str) and t.strip()]
    if not labels:
        return CITATION_STYLE_UNKNOWN
    # JATS commonly puts only the year inside xref and leaves the surname in
    # prose. Marker shape alone cannot distinguish that from a numeric style.
    if any(_BARE_YEAR_RE.match(label) for label in labels):
        return CITATION_STYLE_AUTHOR_YEAR
    if all(_NUMERIC_MARKER_RE.match(label) for label in labels):
        return CITATION_STYLE_NUMERIC
    if any(_HAS_LETTER_RE.search(label) for label in labels):
        return CITATION_STYLE_AUTHOR_YEAR
    return CITATION_STYLE_UNKNOWN


# --------------------------------------------------------------------------
# 2. Whitespace normalization WITH an offset map
# --------------------------------------------------------------------------
def normalize_with_offsets(segment: str) -> tuple:
    """``(normalized_text, raw_index -> normalized_index)``.

    The normalized text is exactly ``re.sub(r"\\s+", " ", segment).strip()`` --
    the transform ``parser._sentence_for`` applies to produce the citance. It is
    reproduced as a loop rather than reused because the OFFSET MAP is the point:
    marker positions are resolved against the raw serialized block, and every
    consumer downstream sees only the normalized citance. Without the map the two
    coordinate systems silently disagree by however much whitespace the XML had.
    """
    out: list[str] = []
    raw_to_norm = [0] * len(segment)
    pending_space = False
    for i, ch in enumerate(segment):
        if ch.isspace():
            # Deferred, so a whitespace RUN collapses to one space and leading /
            # trailing runs vanish -- the .strip() half of the transform.
            pending_space = bool(out)
            raw_to_norm[i] = len(out)
            continue
        if pending_space:
            out.append(" ")
            pending_space = False
        raw_to_norm[i] = len(out)
        out.append(ch)
    return "".join(out), raw_to_norm


# --------------------------------------------------------------------------
# 3. Marker clustering
# --------------------------------------------------------------------------
# What may sit BETWEEN two markers of one cluster: separator punctuation, and a
# bare conjunction ("12, 13 and 14" is one citation cluster in numeric style).
# Deliberately permissive -- see the module docstring, failure mode 2.
_SEPARATORS = rf"[\s,;\[\]\(\)\{{\}}{_DASHES}]*"
_CLUSTER_JOIN_RE = re.compile(
    rf"^{_SEPARATORS}(?:and|&)?{_SEPARATORS}$", re.IGNORECASE)


def cluster_markers(text: str, entries) -> list:
    """Group one sentence's markers into maximal adjacent runs.

    ``entries`` is ``[(char_offset, [rid...], marker_text), ...]`` in document
    order, from ``parser._serialize_with_markers`` -- the AUTHORITATIVE marker
    positions. Returns a list of lists of indices into ``entries``.

    Do NOT regex the rendered citance for this. Prototyped and measured
    2026-08-16: a digit regex over ``ref.citance`` matched years inside
    author-year citations (``Lang, 2024a``), ``COVID-19`` and ``10 + years``, and
    left 226 printed markers unlocatable in the citance string across the corpus.
    """
    clusters: list = []
    current: list = []
    prev_end = 0
    for i, entry in enumerate(entries):
        pos, _rids, mtext = entry
        between = text[prev_end:pos] if pos > prev_end else ""
        if current and _CLUSTER_JOIN_RE.match(between):
            current.append(i)
        else:
            if current:
                clusters.append(current)
            current = [i]
        prev_end = pos + len(mtext or "")
    if current:
        clusters.append(current)
    return clusters


# --------------------------------------------------------------------------
# 4. Claim -> cluster attribution (route (a), positional)
# --------------------------------------------------------------------------
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: Function words only. A content word is never removed here: the DISTINCTIVENESS
#: filter below does the real work, and stripping content would only make more
#: sentences ambiguous (which fails closed, but for the wrong reason).
_STOPWORDS = frozenset("""
a an and are as at be been being but by can could did do does for from had has
have having how however if in into is it its itself may might more most must no
nor not of off on once only or other our out over own same she should since so
some such than that the their them then there these they this those through thus
to too under until upon use used using was we were what when where which while
who whom why will with within without would yet you your his her him he
""".split())

# Longest-first so "iveness" is tried before "ness" and "ness" before "s".
_SUFFIXES = tuple(sorted({
    "ization", "isation", "ational", "iveness", "ously", "ation", "ition",
    "ities", "ically", "ingly", "ments", "ional", "ising", "izing", "ility",
    "ement", "ical", "ible", "able", "ness", "ment", "ions", "ives", "ists",
    "isms", "ing", "ies", "ied", "ive", "ily", "ers", "ent", "ant", "ate",
    "ous", "ful", "ish", "ism", "ist", "ity", "ion", "al", "ed", "es", "ly",
    "er", "ic", "s", "y",
}, key=len, reverse=True))

#: A stem shorter than this is too generic to carry an attribution.
_MIN_STEM = 3
#: Below this length a token is noise regardless of stopword status.
_MIN_TOKEN = 3


def _stem(token: str) -> str:
    """A deliberately CRUDE iterative suffix strip.

    Not a linguistic claim -- an alignment aid, so "antibodies" in a claim meets
    "antibody" in the sentence. It is applied to BOTH sides, so an over- or
    under-stemmed token simply fails to match, and a failed match makes the
    sentence ambiguous, which reverts it to today's behaviour. Every error this
    can make therefore lands on the fail-closed side.
    """
    for _round in range(3):
        for suffix in _SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 4:
                token = token[:-len(suffix)]
                break
        else:
            return token
    return token


def _content_tokens(text: str) -> set:
    """Lowercased content tokens plus their stems, as one set."""
    out: set = set()
    for token in _TOKEN_RE.findall(text.lower()):
        if len(token) < _MIN_TOKEN or token in _STOPWORDS:
            continue
        out.add(token)
        stem = _stem(token)
        if len(stem) >= _MIN_STEM:
            out.add(stem)
    return out


def cluster_regions(sentence: str, clusters) -> list:
    """The text each cluster OWNS, one string per cluster.

    A numeric marker follows the material it supports, so cluster ``k`` owns
    everything from the end of cluster ``k-1`` up to its own first marker. The
    tail after the last marker is owned by NOBODY: in the motivating sentence
    that tail is "for intraoperative FL imaging of oral SCC disease", which is
    true of the antibodies and the micelles alike, and letting either cluster
    claim it would attribute a shared predicate to one clause.
    """
    regions: list = []
    prev_end = 0
    for cluster in clusters:
        offset = int(cluster.get("offset", 0))
        regions.append(sentence[prev_end:offset] if offset > prev_end else "")
        prev_end = max(prev_end, int(cluster.get("end", offset)))
    return regions


def cluster_anchors(sentence: str, clusters) -> list:
    """The noun phrase each marker cluster ATTACHES TO, as a token set.

    A numeric marker attaches to the phrase immediately before it, not to the
    whole clause leading up to it, and that distinction is the entire difficulty.
    In the motivating sentence the lead-in "such detailed cellular
    characterisations were not necessary for the successful clinical translation
    of" is SHARED by both clauses, but it sits inside the first cluster's region,
    so a rule that scores a claim against everything in the region attributes
    "detailed cellular characterisations were not necessary for the clinical
    translation of pH SENSITIVE FLUORESCENT MICELLES" to the antibody cluster.
    Measured 2026-08-16 against the packet's own claim list: 1 of 4 claims wrong,
    and wrong in the direction that clears a real fault.

    The anchor is therefore the maximal run of content tokens immediately
    preceding the marker, STOPPING AT THE FIRST FUNCTION WORD -- "of" before
    "fluorophore-labelled antibodies", "and" before "pH sensitive fluorescent
    micelles". That boundary is a property of English, not a tuned window: a
    function word is where the preceding noun phrase ends.

    Tokens shorter than :data:`_MIN_TOKEN` are SKIPPED rather than treated as a
    boundary -- "pH" is content. Tokens that also occur in another cluster's
    region are dropped, since a token two clusters share cannot separate them.
    """
    regions = cluster_regions(sentence, clusters)
    token_sets = [_content_tokens(region) for region in regions]
    anchors: list = []
    for i, region in enumerate(regions):
        others: set = set()
        for j, tokens in enumerate(token_sets):
            if j != i:
                others |= tokens
        anchor: set = set()
        for match in reversed(list(_TOKEN_RE.finditer(region.lower()))):
            token = match.group()
            if token in _STOPWORDS:
                break
            if len(token) < _MIN_TOKEN:
                continue
            if token in others:
                continue
            anchor.add(token)
            stem = _stem(token)
            if len(stem) >= _MIN_STEM and stem not in others:
                anchor.add(stem)
        anchors.append(anchor)
    return anchors


def assign_claims(sentence: str, clusters, claims) -> list:
    """One cluster index per claim, or ``None`` where attribution is ambiguous.

    A claim belongs first to the cluster whose ANCHOR it names, and to no other.
    A claim naming two anchors spans clusters and returns ``None``.  When it
    names no anchor, it may still belong to exactly one cluster if it overlaps
    content found only in that cluster's owned region; overlap with zero or
    multiple regions remains ``None``.  One ``None`` reverts the whole sentence
    to today's behaviour -- narrowing on a guess is the one outcome this is not
    allowed to produce.
    """
    anchors = cluster_anchors(sentence, clusters)
    if not anchors or any(not anchor for anchor in anchors):
        # A cluster whose marker follows a function word directly ("as shown in
        # 12,13") has no anchor to name, so nothing here can separate it.
        return [None] * len(claims)
    regions = cluster_regions(sentence, clusters)
    region_tokens = [_content_tokens(region) for region in regions]
    # A token shared by two owned regions cannot distinguish their scopes.  The
    # exclusive remainder is used only when a claim names no immediate anchor;
    # an anchor match always wins, and multiple anchor matches remain ambiguous.
    exclusive_regions = []
    for i, tokens in enumerate(region_tokens):
        others: set = set()
        for j, other in enumerate(region_tokens):
            if i != j:
                others |= other
        exclusive_regions.append(tokens - others)

    out: list = []
    for claim in claims:
        claim_tokens = _content_tokens(claim if isinstance(claim, str) else "")
        matched = [i for i, anchor in enumerate(anchors)
                   if claim_tokens & anchor]
        if len(matched) == 1:
            out.append(matched[0])
            continue
        if matched:
            out.append(None)
            continue
        region_matched = [i for i, tokens in enumerate(exclusive_regions)
                          if claim_tokens & tokens]
        out.append(region_matched[0] if len(region_matched) == 1 else None)
    return out


# --------------------------------------------------------------------------
# 5. The per-item scope decision -- the one entry point the judging paths use
# --------------------------------------------------------------------------
def _cluster_of(clusters, index: int) -> dict:
    return clusters[index] if 0 <= index < len(clusters) else {}


def scope_item_claims(item: dict, claims) -> dict:
    """Decide which of ``claims`` THIS reference was actually cited for.

    Reads the marker-cluster provenance ``parser.link_citances`` recorded, which
    ``judgment_band.build_item`` carries onto the item. An item without that
    provenance -- a single-cluster sentence, an author-year document, a Reference
    built outside the parser -- yields :data:`SCOPE_WHOLE_SENTENCE` with the
    claims untouched, which is byte-identically today's behaviour.

    Returns a block carrying the decision, the scoped claim list, and one
    attribution row per ORIGINAL claim naming this reference's cluster, the
    claim's cluster and whether they matched. The caller stamps the block on the
    durable record and counts the ``not_asked`` rows.
    """
    claims = [c for c in (claims or [])]
    clusters = list(item.get("citance_marker_clusters") or [])
    # ABSENT MEANS NUMERIC, SINGLE CLUSTER. ``build_item`` carries the style key
    # for every document the positional rule does NOT apply to, and the cluster
    # list for every sentence that split -- so the only way to arrive here with
    # neither is the ordinary numeric sentence carrying one cluster. Defaulting
    # to "unknown" instead would report every such row as a style refusal and
    # bury the single-cluster case, which is the one this has to stay quiet on.
    style = item.get("citance_citation_style") or CITATION_STYLE_NUMERIC
    raw_index = item.get("citance_marker_cluster_index")
    index = raw_index if isinstance(raw_index, int) else -1
    sentence = item.get("citing_sentence") or ""
    own = _cluster_of(clusters, index)

    block = {
        "citation_style": style,
        "clusters": len(clusters),
        "cluster_index": index,
        "cluster_id": item.get("citance_marker_cluster_id") or "",
        "cluster_marker": own.get("marker_text", ""),
        "cluster_offset": own.get("offset"),
        "cluster_members": list(own.get("members") or []),
        "status": SCOPE_WHOLE_SENTENCE,
        "reason": "",
        "scope_id": "",
        "claims": list(claims),
        "claims_asked": len(claims),
        "claims_not_asked": 0,
        "attribution": [],
    }

    def _whole(reason: str) -> dict:
        block["reason"] = reason
        block["attribution"] = [
            {"claim": claim, "claim_index": i,
             "claim_cluster_index": None,
             "reference_cluster_index": index,
             "matched": True, "disposition": DISPOSITION_ASKED}
            for i, claim in enumerate(claims)]
        return block

    if style != CITATION_STYLE_NUMERIC:
        return _whole(REASON_NOT_NUMERIC)
    if len(clusters) < 2:
        return _whole(REASON_SINGLE_CLUSTER)
    if not claims:
        return _whole(REASON_NO_CLAIMS)
    if not own:
        # Clusters exist but this reference sits in none of them. Nothing to
        # narrow to, so nothing is narrowed.
        return _whole(REASON_AMBIGUOUS)

    assignments = assign_claims(sentence, clusters, claims)
    if any(a is None for a in assignments):
        return _whole(REASON_AMBIGUOUS)
    if any(k not in assignments for k in range(len(clusters))):
        # A cluster with no claim would leave its members judged against an EMPTY
        # claim list -- held out of the coverage substrate altogether rather than
        # judged narrowly. That is a scope reduction, not a precision gain.
        return _whole(REASON_CLUSTER_WITHOUT_CLAIMS)

    attribution = [
        {"claim": claim, "claim_index": i,
         "claim_cluster_index": assignments[i],
         "reference_cluster_index": index,
         "matched": assignments[i] == index,
         "disposition": (DISPOSITION_ASKED if assignments[i] == index
                         else DISPOSITION_NOT_ASKED)}
        for i, claim in enumerate(claims)]
    asked = [row["claim"] for row in attribution if row["matched"]]
    block.update({
        "status": SCOPE_SCOPED,
        "reason": "",
        "scope_id": block["cluster_id"],
        "claims": asked,
        "claims_asked": len(asked),
        "claims_not_asked": len(claims) - len(asked),
        "attribution": attribution,
    })
    return block


def should_record(block: dict) -> bool:
    """Is this block worth carrying on the durable record?

    Yes when the sentence actually split (the population this change touches,
    scoped or reverted alike), and yes for any document the positional rule was
    refused on, whose acceptance row requires the record to state which rule
    applied. A numeric single-cluster sentence -- the overwhelming majority, and
    the regression guard for the whole change -- records NOTHING, so its rows
    stay byte-identical.
    """
    return (int(block.get("clusters") or 0) >= 2
            or block.get("citation_style") != CITATION_STYLE_NUMERIC)


def stamp_verdicts(verdicts, block: dict) -> None:
    """Carry the cluster match onto each per-claim verdict, in place.

    Change 3 asks for the reference's cluster, the claim's cluster and whether
    they matched to be visible PER VERDICT, not only per record. Applied only on
    a scoped row, so a default-path verdict keeps exactly the keys it has always
    had.
    """
    if block.get("status") != SCOPE_SCOPED:
        return
    asked = [row for row in block.get("attribution", []) if row["matched"]]
    for verdict, row in zip(verdicts or [], asked):
        if isinstance(verdict, dict):
            verdict["reference_cluster_index"] = row["reference_cluster_index"]
            verdict["claim_cluster_index"] = row["claim_cluster_index"]
            verdict["cluster_matched"] = True


def new_counts() -> dict:
    """The manifest's marker-scope tally. Zeroed keys are load-bearing: a run
    that scoped nothing must be distinguishable from a run that never asked."""
    return {
        "scoped_pairs": 0,
        "whole_sentence_pairs": 0,
        "pairs_skipped_not_asked": 0,
        "pairs_skipped_by_document": {},
        "claims_asked": 0,
        "claims_not_asked": 0,
        "claims_assessed_negative": 0,
        "claims_model_assessed": 0,
        "fallback_reasons": {},
        "citation_style_documents": {},
        "multi_cluster_sentences": 0,
        "marker_bearing_sentences": 0,
        "multi_reference_sentences": 0,
        "positional_multi_reference_sentences": 0,
        "positional_multi_cluster_sentences": 0,
        "cluster_count_distribution": {},
    }


def manifest_block(counts: dict) -> dict:
    """The run's marker-attribution report, ready for a manifest.

    ``pairs_skipped_not_asked`` is the headline: how many (reference, claim)
    pairs the run declined to ask because the claim belonged to another marker
    cluster. It is published rather than absorbed, because narrowing the question
    changes what every downstream rate is a rate OF.
    """
    total = counts["scoped_pairs"] + counts["whole_sentence_pairs"]
    return {
        "scoped_pairs": counts["scoped_pairs"],
        "whole_sentence_pairs": counts["whole_sentence_pairs"],
        "pairs_skipped_not_asked": counts["pairs_skipped_not_asked"],
        "pairs_skipped_by_document": dict(sorted(
            counts["pairs_skipped_by_document"].items())),
        "claims_asked": counts["claims_asked"],
        "claims_not_asked": counts["claims_not_asked"],
        "claims_assessed_negative": counts["claims_assessed_negative"],
        "claims_model_assessed": counts["claims_model_assessed"],
        "fallback_reasons": dict(sorted(counts["fallback_reasons"].items())),
        "citation_style_documents": dict(sorted(
            counts["citation_style_documents"].items())),
        "marker_bearing_sentences": counts["marker_bearing_sentences"],
        "multi_reference_sentences": counts["multi_reference_sentences"],
        "multi_cluster_sentences": counts["multi_cluster_sentences"],
        "positional_multi_reference_sentences":
            counts["positional_multi_reference_sentences"],
        "positional_multi_cluster_sentences":
            counts["positional_multi_cluster_sentences"],
        "cluster_count_distribution": dict(sorted(
            counts["cluster_count_distribution"].items(),
            key=lambda kv: int(kv[0]))),
        "denominator_pairs": total,
        "note": (
            "A reference is judged only against the claims its OWN marker "
            "cluster was cited for. pairs_skipped_not_asked counts (reference, "
            "claim) pairs that were never put to the reference -- disposition "
            "not_asked, which is NOT assessed_negative and must never be read as "
            "one; claims_assessed_negative is the answered-and-failed count, "
            "printed beside it so the two cannot be conflated. Clustering "
            "applies only to numeric-marker documents; an "
            "author-year document and any sentence whose claims could not all be "
            "attributed keep whole-sentence behaviour, counted under "
            "fallback_reasons. The comparable numeric-only population is "
            "positional_multi_cluster_sentences over "
            "positional_multi_reference_sentences; the all-style counters are "
            "reported separately and must not be compared with the 76/274 "
            "numeric-only baseline."
        ),
    }


def tally_document(counts: dict, refs) -> None:
    """Fold ONE document's parsed references into the sizing counters, in place.

    This is the run's own version of the spec's sizing table, so the measurement
    that motivated the change and the measurement the change actually produces
    can be compared without re-deriving either. Measured over ``corpus_frozen_v1``
    on 2026-08-16, numeric-style documents only: 901 marker-bearing sentences,
    274 citing two or more distinct references, 76 of those (27.7%) splitting into
    two or more marker clusters, over 216 references.

    Counted from the PARSER's output, before any claim is extracted -- these are
    properties of the markup, not of an attribution that may later revert.
    """
    style = CITATION_STYLE_UNKNOWN
    sentences: dict = {}
    for ref in refs:
        gid = getattr(ref, "citance_group_id", "") or ""
        if not gid:
            continue
        style = getattr(ref, "citance_citation_style", "") or style
        clusters = len(getattr(ref, "citance_marker_clusters", None) or [])
        seen = sentences.setdefault(gid, {"members": 0, "clusters": 1})
        seen["members"] += 1
        seen["clusters"] = max(seen["clusters"], clusters or 1)
    counts["citation_style_documents"][style] = (
        counts["citation_style_documents"].get(style, 0) + 1)
    for seen in sentences.values():
        counts["marker_bearing_sentences"] += 1
        if seen["members"] < 2:
            continue
        counts["multi_reference_sentences"] += 1
        if style == CITATION_STYLE_NUMERIC:
            counts["positional_multi_reference_sentences"] += 1
        key = str(seen["clusters"])
        counts["cluster_count_distribution"][key] = (
            counts["cluster_count_distribution"].get(key, 0) + 1)
        if seen["clusters"] >= 2:
            counts["multi_cluster_sentences"] += 1
            if style == CITATION_STYLE_NUMERIC:
                counts["positional_multi_cluster_sentences"] += 1


def tally(counts: dict, block: dict, document: str = "") -> None:
    """Fold one item's scope decision into the run counters, in place.

    ``document`` is the citing PMCID, so the skipped pairs are countable PER
    DOCUMENT as well as per run -- a reader auditing one paper must be able to
    ask how much of it was never asked without re-deriving it from the rows.
    """
    if block.get("status") == SCOPE_SCOPED:
        skipped = int(block.get("claims_not_asked") or 0)
        counts["scoped_pairs"] += 1
        counts["pairs_skipped_not_asked"] += skipped
        if document and skipped:
            counts["pairs_skipped_by_document"][document] = (
                counts["pairs_skipped_by_document"].get(document, 0) + skipped)
    else:
        counts["whole_sentence_pairs"] += 1
        reason = block.get("reason") or ""
        if reason:
            counts["fallback_reasons"][reason] = (
                counts["fallback_reasons"].get(reason, 0) + 1)
    counts["claims_asked"] += int(block.get("claims_asked") or 0)
    counts["claims_not_asked"] += int(block.get("claims_not_asked") or 0)


#: Coverage buckets that are an ANSWER AGAINST the reference, as opposed to a
#: question never put to it. Imported from ``cocitation``'s vocabulary through
#: judgment_band's public names would be a circular import (this module is
#: imported by the parser); the two are string literals in a frozen manifest
#: contract, so they are restated here and pinned against ``cocitation``'s
#: definitions by a test rather than left to drift.
def tally_verdicts(counts: dict, verdicts) -> None:
    """Count the claims that were ASKED and came back negative, in place.

    Published beside ``claims_not_asked`` so the two are impossible to conflate.
    This is the same class as DEC-079's F3 gate and the tautological queue audit:
    a claim that was never put to a reference and a claim the reference failed
    must never be indistinguishable in the output, and the surest way to keep
    them distinguishable is to print both numbers next to each other.
    """
    for verdict in verdicts or []:
        if not isinstance(verdict, dict):
            continue
        if verdict.get("established") is False:
            counts["claims_assessed_negative"] += 1
        if verdict.get("scope") in {"abstract", "fulltext"}:
            counts["claims_model_assessed"] += 1
