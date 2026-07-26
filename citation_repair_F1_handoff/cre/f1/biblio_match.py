"""Deterministic bibliographic matcher (HANDOFF_BIBLIO_MATCH task, Stage 1).

Supersedes the single ``token_sort_ratio`` title threshold that previously
decided whether a claimed reference matched a retrieved record. Dr. Roberts
flagged that approach as fragile: authors truncate references and alter titles,
so a bare lexical title-similarity score flags legitimate references as
mismatches.

This is the standard, reproducible approach used by Crossref's matcher
(Tkaczyk 2018) and Semantic Scholar's S2ORC/S2APLER: **normalized string
similarity over titles + structured field agreement (author, year, volume,
pages, journal)**. No LLM call; no embedding cosine; no closed APIs. The scoring
core depends only on :mod:`rapidfuzz` (already in the project) and the existing
``schema`` dataclasses. ``retrieve_candidates`` additionally uses the shared
rate limiters in :mod:`ratelimit` for its two HTTP queries.

Scale: ``title_sim`` and ``match_score`` are on **0..1**. The legacy
``token_sort_ratio`` path (``lookup.title_similarity``) stays on 0..100 and is
unaffected; the integration boundary keeps ``log.title_similarity`` on the
established 0..100 scale and records the new 0..1 composite in ``log.match_score``.

Optional Stage 2 (``biblio_rerank.py``, a MedCPT cross-encoder) is invoked ONLY
when :func:`best_match` returns ``ambiguous=True``; it degrades to Stage 1 when
the model can't load. Stage 1 ships independently and has no such dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import requests
from rapidfuzz.distance import JaroWinkler

from .schema import ClaimedRef, RetrievedRecord
from .ratelimit import CROSSREF, OPENALEX, request_with_retry
from .textnorm import fold_bibliographic_text, fold_chemical_charges
from .work_identity import (assess_same_work, first_author_equivalent,
                            doi_equivalent, journal_equivalent,
                            is_distinctive_title)

# ``Claimed`` is the handoff's name for the claimed-reference metadata object.
Claimed = ClaimedRef

CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"


# =====================================================================
# Result objects
# =====================================================================
@dataclass
class FieldAgreement:
    """Per-field verdicts. True/False/None where None = can't judge (the field
    is missing on at least one side, so absence is never read as a mismatch)."""
    author_match: Optional[bool] = None
    # Unlike author_match (written first author appears anywhere in the resolved
    # roster), this compares first-author position.  Keep both: coauthor overlap
    # is useful evidence but must not masquerade as high-entropy first-author ID.
    first_author_match: Optional[bool] = None
    year_match: Optional[bool] = None
    journal_match: Optional[bool] = None
    volume_match: Optional[bool] = None
    pages_match: Optional[bool] = None
    doi_match: Optional[bool] = None


@dataclass
class MatchResult:
    score: float                       # 0..1 composite
    title_sim: float                   # 0..1 title-only similarity
    fields: FieldAgreement
    record: Optional[RetrievedRecord] = None   # the candidate this scores
    override_fired: bool = False       # strong-corroboration override floored the score
    same_work_reason: str = ""          # auditable identity rule used by flag_verdict
    identity_signals: tuple[str, ...] = ()
    # F2-B (second defect): the claimed PMID resolved to a PREPRINT record while
    # the citation itself reads as an ordinary article. Evidence TOWARD a fault,
    # surfaced under its own reason (never folded into the same-work quarantine).
    resolved_preprint: bool = False


@dataclass
class BestMatch:
    found: bool
    best: Optional[MatchResult] = None
    confident: bool = False
    ambiguous: bool = False
    runners_up: list = field(default_factory=list)


# Flag verdicts (priority bands for the human/LLM audit).
VERDICT_MATCH        = "match"               # score >= accept: not flagged
VERDICT_WRONG_PAPER  = "review_wrong_paper"  # flagged, HIGH priority (real-F2 signal)
VERDICT_FORMATTING   = "review_formatting"   # flagged, LOW priority (likely same paper)
VERDICT_SAME_WORK_VARIANT = "review_same_work_variant"  # (near-)identical title,
#   author/year drift: the PMID points at the SAME work (revision / citing-side
#   metadata drift), NOT a wrong reference. Audited, but excluded from the F2 count.
VERDICT_UNSCOREABLE  = "unscoreable"         # not a scoreable title comparison
#   (empty/placeholder title, journal-as-title, regulatory code, book-container):
#   carries ZERO wrong-paper evidence. Named + counted, excluded from BOTH the HIGH
#   count and the scoreable denominator -- mirrors decide()'s live-path treatment.
#   Value matches schema.UNSCOREABLE on purpose (same concept, two label spaces).
VERDICT_UNRESOLVED   = "unresolved"          # the claimed PMID did NOT resolve
#   (RetrievedRecord.resolved is False): there is NO resolved work to mismatch
#   against, so the row cannot be an F2 -- scoring it against an empty resolved
#   title spuriously lands it in WRONG_PAPER. Routed OUT of BOTH the HIGH count and
#   the scoreable denominator (like UNSCOREABLE), but kept a DISTINCT bucket so the
#   rows stay recoverable as F1 (fabrication) candidates later. This is a
#   RESOLVED-side gate, separate from the claimed-side classify_unscoreable gate.
#   Tri-state: only explicit ``resolved is False`` -- resolved=None (unknown) is
#   NOT swept in.

# An (near-)identical title means the identifier resolves to the SAME work, so a
# field disagreement on it is a same-work variant, not a wrong paper. Title
# similarity alone gates this -- source-agnostic (no StatPearls / journal
# allowlist).
# F2_V3_3: lowered 0.95 -> 0.92. Two confirmed same-work formatting variants
# (seam rows 12199786 / 9802808) normalize to title_sim ~0.944-0.947 -- just under
# the old gate -- and were mis-banding review_wrong_paper (false HIGH). 0.92 admits
# both as review_same_work_variant while staying clear of the genuine-F2 guards
# (all title_sim < 0.92). The gate expression is unchanged and stays source-agnostic;
# newly-crossed rows are surfaced (never silently moved) via reband audit.
SAME_WORK_TITLE_SIM_MIN = 0.92


# =====================================================================
# Title scoring (containment-aware, so truncation doesn't tank the score)
# =====================================================================
# PubMed brackets translated (non-English) titles: "[Results of ...]".
# Corrigendum/erratum/correction notices decorate the original title.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(erratum|corrigendum|correction|retraction)\b[:\-\s]*", re.I)

# A leading section / society / consortium prefix that PubMed prepends to an
# article title and the citing reference often omits, e.g. "Biochemistry.
# Metamorphic proteins.", "Clinical practice. Celiac disease.", "American College
# of Sports Medicine position stand. Progression models...". A short (<= 80 char)
# run ending in the first '.' or ':' followed by whitespace and more text. Used
# only to OFFER a de-prefixed title variant; never the sole representation.
_LEADING_PREFIX_RE = re.compile(r"^[^.:]{1,80}?[.:]\s+(?=\S)")

def normalize_title(t: str) -> str:
    """Lowercase, Unicode-fold (strip accents), drop punctuation, collapse
    whitespace. Also strips PubMed translated-title brackets and
    erratum/corrigendum prefixes so the SAME work normalizes consistently.
    Applied to both sides before any string comparison."""
    if not t:
        return ""
    # strip a single pair of square brackets PubMed wraps around translated
    # titles: "[Results of ...]" -> "Results of ..."
    s = t.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    elif s.startswith("[") and s.endswith("]."):
        s = s[1:-2]
    # drop erratum/corrigendum/correction/retraction decoration
    s = _TITLE_PREFIX_RE.sub("", s)
    s = fold_bibliographic_text(s)
    s = fold_chemical_charges(s)
    s = s.lower()
    # collapse intra-token hyphens in alphanumeric tokens so "t-rna" == "trna",
    # "pd-l2" == "pdl2" (chemical / gene / variant name formatting)
    s = re.sub(r"(?<=\w)-(?=\w)", "", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _trigrams(s: str) -> set[str]:
    """Character 3-grams of an already-normalized string (spaces kept, so word
    boundaries still count). Empty for strings shorter than 3 characters."""
    return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else set()


def trigram_jaccard(a: str, b: str) -> float:
    """|shared 3-grams| / |union of 3-grams|. Symmetric overlap."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


def trigram_containment(a: str, b: str) -> float:
    """|shared 3-grams| / |smaller 3-gram set|. Asymmetric coverage: this is
    what rescues a truncated-but-correct title (its trigrams are a near-subset
    of the full title's)."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    smaller = min(len(ta), len(tb))
    return inter / smaller if smaller else 0.0


# Resolved-side placeholders PubMed emits when a record has no usable title.
_NONTITLE_PHRASES = {
    "not available", "no title available", "no title",
    "title not available", "untitled",
}


def is_scoreable_title(title: str, journal: str = "") -> bool:
    """Whether ``title`` is usable for an F2 title comparison.

    Returns False (caller buckets UNSCOREABLE, never auto-flagged) when the
    title is not really a title:
      * empty / whitespace-only;
      * a PubMed '[Not Available]' placeholder;
      * the journal name parsed into the title slot (normalized title equals or
        is near-identical to the journal field by trigram containment >= 0.92).

    This is NOT precision-by-suppression: a journal name or missing title
    carries no evidence about whether the cited PMID is the wrong paper.
    Excluded references are reported as a named coverage bucket, not hidden.
    Apply symmetrically to both written and resolved titles before scoring."""
    nt = normalize_title(title)
    if not nt:
        return False
    if nt in _NONTITLE_PHRASES:
        return False
    if journal:
        nj = normalize_title(journal)
        if nj:
            if nt == nj:
                return False
            if min(len(nt), len(nj)) >= 8 and trigram_containment(nt, nj) >= 0.92:
                return False
    return True


def _strict_title_prefix(a: str, b: str) -> bool:
    """True iff one NORMALIZED title is a strict prefix of the other at a word
    boundary (the shorter is a truncation of the longer). NOT general
    containment: 'either title inside the other' costs true positives (F2-D), so
    a title embedded mid-string (the deliberately-untouched containment class,
    e.g. 2280326 'Psychometric characteristics of the <claimed>') does not match.
    Both sides must be distinctive so a trivially short fragment never prefixes an
    unrelated title."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb or na == nb:
        return False
    shorter, longer = (na, nb) if len(na) < len(nb) else (nb, na)
    if not (is_distinctive_title(shorter) and is_distinctive_title(longer)):
        return False
    # Word-boundary prefix: the char in ``longer`` right after ``shorter`` must be
    # a space, so 'metal' is not read as a prefix of 'metallurgy'.
    return longer.startswith(shorter) and longer[len(shorter):len(shorter) + 1] == " "


def jaro_winkler(a: str, b: str) -> float:
    """Prefix-weighted edit similarity in 0..1 (truncation-robust)."""
    return float(JaroWinkler.similarity(a, b))


def _pair_sim(a: str, b: str) -> float:
    """0..1 similarity of two ALREADY-NORMALIZED strings: ``max`` of (a)
    Jaro-Winkler (prefix-weighted, good for truncated prefixes) and (b) the
    S2ORC-style harmonic mean of trigram Jaccard and trigram containment
    (containment rescues a short-but-correct title)."""
    if not a or not b:
        return 0.0
    jw = jaro_winkler(a, b)
    tri_j = trigram_jaccard(a, b)
    cont = trigram_containment(a, b)
    hm = 0.0 if (tri_j + cont) == 0 else 2 * tri_j * cont / (tri_j + cont)
    return max(jw, hm)


def _title_variants(title: str) -> list[str]:
    """Normalized title, plus a de-prefixed variant when a leading section/
    society/consortium prefix is present (e.g. 'Biochemistry. Metamorphic
    proteins.' -> also 'metamorphic proteins'). The de-prefixed remainder is
    offered ONLY when it is still a substantial title (>= 2 words and >= 10
    chars), so a real short title is never stripped to a fragment. It is an
    ADDITIONAL variant, never a replacement -- title_sim takes the MAX, so
    de-prefixing can only RAISE similarity for a genuinely same work."""
    base = normalize_title(title)
    if not base:
        return []
    variants = [base]
    m = _LEADING_PREFIX_RE.match(title or "")
    if m:
        nr = normalize_title((title or "")[m.end():])
        if nr and nr != base and len(nr.split()) >= 2 and len(nr) >= 10:
            variants.append(nr)
    return variants


def title_sim(claimed: str, candidate: str) -> float:
    """0..1. Robust to truncation, dropped subtitles, AND a leading section/
    society prefix on either side. ``max`` of ``_pair_sim`` over the cross-product
    of each side's title variants (original + de-prefixed). Because the
    original-vs-original pair is always included, this can only RAISE the score
    relative to the bare comparison, never lower it -- a prefix can no longer tank
    a genuinely same work, and two different works gain nothing (their de-prefixed
    forms still do not match)."""
    va, vb = _title_variants(claimed), _title_variants(candidate)
    if not va or not vb:
        return 0.0
    return max(_pair_sim(a, b) for a in va for b in vb)


# =====================================================================
# Field agreement
# =====================================================================
def _norm(s: str) -> str:
    return normalize_title(s)


def _first_author_surname(authors: list[str]) -> str:
    """Best-effort surname of the first listed author. Crossref gives bare
    family names; OpenAlex/free-text give 'Given Family' or 'Family, Given'."""
    if not authors:
        return ""
    a = authors[0].strip()
    if "," in a:                          # 'Surname, Given'
        return _norm(a.split(",")[0])
    return _norm(a)                       # normalized full string; matched token-wise


def _surname_present(claimed_surname: str, cand_authors: list[str]) -> bool:
    """Is the claimed first-author surname present among the candidate authors?
    Token-aware so 'van der Berg' ~ 'Berg' and 'Okafor' ~ 'A. Okafor' match."""
    if not claimed_surname:
        return False
    target_tokens = [t for t in claimed_surname.split() if len(t) >= 3]
    last = target_tokens[-1] if target_tokens else claimed_surname
    corporate_tokens = {
        "association", "society", "organization", "organisation", "committee",
        "council", "consortium", "collaboration", "group", "agency", "college",
    }
    claimed_is_corporate = bool(set(claimed_surname.split()) & corporate_tokens)
    for cand in cand_authors:
        c = _norm(cand)
        if not c:
            continue
        if claimed_surname == c:
            return True
        if claimed_is_corporate:
            continue
        ctoks = c.split()
        if last and last in ctoks:                  # surname token appears
            return True
        if len(c) >= 4 and len(claimed_surname) >= 4 and \
                (c in claimed_surname or claimed_surname in c):
            return True
    return False


def field_agreement(claimed: Claimed, cand: RetrievedRecord) -> FieldAgreement:
    """Each field -> True / False / None (None = can't judge, missing on a side).

    * author : claimed first-author surname present in candidate authors
    * year   : equal within +/- 1
    * journal: normalized containment either direction (handles ISO-4 abbrevs*)
    * volume / pages : exact match after stripping to digits

    \\* Full ISO-4 normalization needs a journal-abbreviation authority list; for
    v1 this is lowercase + punctuation-strip + bidirectional containment, a
    documented approximation (see methods/limitations).
    """
    fa = FieldAgreement()

    # author
    claimed_sn = _first_author_surname(claimed.authors)
    if claimed_sn and cand.authors:
        fa.author_match = _surname_present(claimed_sn, cand.authors)
        fa.first_author_match = first_author_equivalent(claimed, cand)

    # journal (conservative ISO-abbreviation approximation) -- computed before year so
    # the preprint year-tolerance below can require journal corroboration.
    cj, rj = _norm(claimed.journal), _norm(cand.journal)
    if cj and rj:
        fa.journal_match = journal_equivalent(claimed.journal, cand.journal)

    # year: agree within +/-1. A 2-year gap is read as CAN'T-JUDGE (None, never a
    # penalty) ONLY when the resolved year is epub/preprint-derived (year_from_dep)
    # AND BOTH high-entropy fields -- author AND journal -- corroborate: the same
    # work cited from its preprint and indexed at its later print year. Every other
    # >1 gap stays a confident disagreement (False). Requiring author AND journal
    # (not author alone) means this demotion only ever lets the strong-corroboration
    # OVERRIDE fire (author+journal+no-disagreement), so its recall cost is a SUBSET
    # of the already-documented, instrumented override residual -- it cannot touch a
    # large-gap paper-series F2 (19-yr gap), a sparse ref (author not True), or a
    # same-author-but-different-journal wrong paper.
    if claimed.year and cand.year:
        gap = abs(int(claimed.year) - int(cand.year))
        if gap <= 1:
            fa.year_match = True
        elif (gap <= 2 and getattr(cand, "year_from_dep", False)
              and fa.author_match is True and fa.journal_match is True):
            fa.year_match = None
        else:
            fa.year_match = False

    # volume (digits only) / pages (F2-A: canonicalize elided end pages + dashes)
    cv, rv = _digits(claimed.volume), _digits(cand.volume)
    if cv and rv:
        fa.volume_match = cv == rv
    cp, rp = _canonical_pages(claimed.pages), _canonical_pages(cand.pages)
    if cp and rp:
        fa.pages_match = cp == rp

    if claimed.claimed_doi and cand.doi:
        fa.doi_match = doi_equivalent(claimed.claimed_doi, cand.doi)

    return fa


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


# F2-A: dash characters PubMed / citing sources use in a page range. Folded to a
# plain hyphen before the elided-end-page expansion so '117–32' and '117-32'
# canonicalize identically.
_PAGE_DASHES = "‐‑‒–—―−"   # ‐ ‑ ‒ – — ― −
_PAGE_RANGE_RE = re.compile(r"^([a-z]*)(\d+)-(\d+)(.*)$")


def _canonical_pages(s: str) -> str:
    """Canonicalize a page range so an elided end page compares equal to its
    written-out form (F2-A). PubMed elides the shared leading digits of the end
    page (``141-4``, ``1083-91``, ``3143-421``) and uses hyphens where citations
    use en/em dashes (``117–32``, ``925–8.e4``).

    1. Fold every dash in ``_PAGE_DASHES`` to ``-``; strip internal whitespace;
       lowercase.
    2. When the value is ``<prefix><start>-<end><suffix>`` and the end-page digit
       string is SHORTER than the start-page digit string, left-pad the end from
       the start (``925-8`` -> ``925-928``, ``3143-421`` -> ``3143-3421``).
    3. A non-range value (``S100``, ``e0224455``, ``CD010442``) matches no range
       and is returned folded but otherwise unchanged.

    Returns ``""`` for a missing value so the caller keeps ``pages_match``
    tri-state (``None`` when either side is absent)."""
    if not s:
        return ""
    t = s.strip().lower()
    for d in _PAGE_DASHES:
        t = t.replace(d, "-")
    t = re.sub(r"\s+", "", t)
    m = _PAGE_RANGE_RE.match(t)
    if not m:
        return t
    prefix, start, end, suffix = m.groups()
    if len(end) < len(start):
        end = start[:len(start) - len(end)] + end
    return f"{prefix}{start}-{end}{suffix}"


# =====================================================================
# Composite score + best match
# =====================================================================
def match_score(claimed: Claimed, cand: RetrievedRecord,
                accept: float = 0.85) -> MatchResult:
    """Title similarity, nudged by confirmatory field agreement and pulled down
    by confident field DISagreement, with a STRONG-CORROBORATION OVERRIDE.

    The penalties are what separate a same-title-DIFFERENT-paper (survey vs.
    update from the same group) from a true match: titles can look alike, but a
    confident author/year disagreement is strong evidence of a different work.

    The additive nudges, however, cannot lift a near-zero cross-language title
    over ``accept`` even when the work is plainly the same. The override floors
    the score at ``accept`` ONLY when the two high-entropy fields -- first-author
    surname AND journal -- both agree and NO field disagrees.

    Why author+journal, not ``agree >= 2`` / ``agree >= 3``: year (+/-1 window)
    and volume/pages are low-entropy and collide across unrelated works, and
    missing fields read as None (uncountable). Counting them lets the override
    fire on author+year alone whenever journal is unparsed -- i.e. on sparse,
    malformed references, the population most likely to be a real wrong-reference
    (F2). Requiring the two discriminating fields, both present and agreeing, is
    the narrowest gate that still rescues the cross-language case (author + year
    + journal agree) while refusing to fire when journal is unknown.

    KNOWN RESIDUAL (no metadata gate can close it): a wrong paper by the SAME
    author in the SAME journal in the SAME year is metadata-identical to a
    cross-language same-paper cite. The override fires on it. This is an
    irreducible precision/recall trade; its size is measured on the held-out F2
    recall set, not assumed away here.
    """
    ts = title_sim(claimed.title, cand.title)
    f = field_agreement(claimed, cand)
    score = ts
    # confirmatory boosts
    if f.author_match:
        score += 0.05
    if f.year_match:
        score += 0.05
    if f.journal_match:
        score += 0.03
    if f.volume_match or f.pages_match:
        score += 0.02
    # disqualifying penalties
    if f.author_match is False:
        score -= 0.15
    if f.year_match is False:
        score -= 0.10

    # --- strong-corroboration override -------------------------------------
    disagree = sum(1 for v in (f.author_match, f.year_match, f.journal_match,
                               f.volume_match, f.pages_match) if v is False)
    # Fire ONLY when both high-entropy fields agree and nothing contradicts.
    # author_match is True AND journal_match is True already implies neither of
    # those disagrees; ``disagree == 0`` additionally blocks a contradicting
    # year/volume/pages (same author+journal but year off by 5 -> likely a
    # different work, do not rescue).
    first_author_ok = (f.first_author_match is True or
                       (f.first_author_match is None and f.author_match is True))
    override_fired = (first_author_ok and f.journal_match is True
                      and disagree == 0 and score < accept)
    if override_fired:
        score = accept
    # -----------------------------------------------------------------------

    score = round(max(0.0, min(1.0, score)), 4)   # round: avoid float knife-edges
    return MatchResult(score=score, title_sim=round(ts, 4), fields=f, record=cand,
                       override_fired=override_fired)


# =====================================================================
# Preprint-source detector (F2_V3_5)
# =====================================================================
# A claimed citation whose venue is a preprint server, resolving via its own
# identifier to the published record, is the SAME work under a revised title --
# NOT a wrong paper. The signal lives entirely on the CLAIMED (citing-side)
# metadata: the venue string names a preprint server, or the claimed DOI carries
# a preprint-registrant prefix. Orthogonal to title_sim, so the 0.92 same-work
# gate is untouched.
_PREPRINT_VENUE_TOKENS = ("arxiv", "biorxiv", "biorvix", "medrxiv", "chemrxiv",
                          "ssrn", "research square", "researchsquare",
                          "preprints.org", "preprint", "osf",
                          "psyarxiv", "techrxiv", "authorea")
# Preprint-ONLY registrants: a bare prefix is decisive.
#   10.48550 arXiv, 10.21203 Research Square, 10.26434 ChemRxiv,
#   10.31234 PsyArXiv, 10.31219 OSF.
_PREPRINT_DOI_PREFIXES = ("10.48550/", "10.21203/",
                          "10.26434/", "10.31234/", "10.31219/")
# F2-B: 10.1101 is Cold Spring Harbor Laboratory Press, which registers
# bioRxiv/medRxiv AND real CSHL journals (Genome Res, Genes Dev, Learn Mem, CSH
# Perspect Biol/Med, CSH Protoc, CSH Symp). Only the date-stamped DOI form
# (10.1101/2020.02.08.939660) is a preprint; 10.1101/gr.209601.116 and
# 10.1101/gad.1255404 are journal articles. A pattern, not a bare prefix, so the
# 79 seed-37 CSHL-journal rows stop reading as a preprint signal.
_PREPRINT_DOI_DATESTAMP_RE = re.compile(r"^10\.1101/\d{4}\.\d{2}\.\d{2}\.")


def _is_preprint_doi(doi: str) -> bool:
    """True iff ``doi`` is a preprint DOI: a preprint-only registrant prefix, or
    the date-stamped 10.1101 form (a bare 10.1101 prefix is NOT enough -- it is
    shared with CSHL journals)."""
    doi = (doi or "").lower()
    if not doi:
        return False
    if any(doi.startswith(p) for p in _PREPRINT_DOI_PREFIXES):
        return True
    return bool(_PREPRINT_DOI_DATESTAMP_RE.match(doi))


def is_preprint_source(claimed) -> bool:
    """True iff the CLAIMED citation names a preprint venue (journal string)
    or carries a preprint-registrant DOI. Signal is on the claimed (citing-side)
    metadata only -- the resolved record is the published work."""
    j = (claimed.journal or "").lower()
    if any(tok in j for tok in _PREPRINT_VENUE_TOKENS):
        return True
    return _is_preprint_doi(claimed.claimed_doi)


def is_preprint_resolved(resolved) -> bool:
    """True iff the RESOLVED record is itself a preprint -- a preprint-server
    journal, a date-stamped/preprint-registrant DOI, or a MEDLINE 'Preprint'
    publication type (F2-B, second defect).

    This is the INVERSE of :func:`is_preprint_source`. A citation that reads as
    an ordinary journal article whose claimed PMID resolves to a preprint is a
    genuine F2 subtype and was previously undetectable (the detector only ever
    inspected the claimed side). A preprint on the RESOLVED side is evidence
    TOWARD a fault -- the opposite direction from a preprint on the claimed side
    -- so the caller surfaces it under its own reason, never folding it into the
    same-work quarantine."""
    j = (resolved.journal or "").lower()
    if any(tok in j for tok in _PREPRINT_VENUE_TOKENS):
        return True
    if _is_preprint_doi(resolved.doi):
        return True
    return "preprint" in {p.lower() for p in (resolved.publication_types or [])}


def flag_verdict(claimed: Claimed, cand: RetrievedRecord,
                 accept: float = 0.85) -> tuple[str, MatchResult]:
    """Classify a (claimed, resolved-candidate) pair into a priority band.

    Returns (verdict, MatchResult). Does NOT change the flagging decision --
    everything below ``accept`` is still surfaced for review (recall unchanged).
    It only ranks the flagged pool so the audit reaches genuine F2 candidates
    first:

      VERDICT_MATCH               score >= accept.
      VERDICT_SAME_WORK_VARIANT   below accept, title (near-)identical
                             (title_sim >= SAME_WORK_TITLE_SIM_MIN) yet author or
                             year CONFIDENTLY disagrees: the identifier points at
                             the SAME work, so this is a revision / citing-side
                             metadata-drift signature, NOT a wrong reference.
                             Checked BEFORE the wrong-paper branch; audited but
                             excluded from the F2 count. Also reached (F2_V3_5)
                             when the claimed venue is a preprint server, the
                             first author agrees (author_match is True), AND a
                             field confidently disagrees -- a preprint->published
                             retitle of the same work, keyed on the preprint
                             signal not title_sim. A cleanly-matching preprint
                             cite (no disagreement) stays MATCH in the
                             denominator, not SAME_WORK_VARIANT.
      VERDICT_WRONG_PAPER    below accept AND (author or year disagrees, or no
                             field agrees at all): the wrong-paper signal, the
                             audit's high-precision band.
      VERDICT_FORMATTING     below accept but at least one field agrees and none
                             disagrees: low title similarity is most likely a
                             formatting/translation variant of the SAME paper.
                             Low priority, never auto-cleared.

    Compute F2 precision primarily over VERDICT_WRONG_PAPER.
    Call is_scoreable_title on both titles before calling this."""
    m = match_score(claimed, cand, accept=accept)
    f = m.fields
    # Tri-state: only a REAL disagreement (is False) counts; None (unparsed) does
    # not. A confident author/year disagreement.
    disagree = ((f.first_author_match is False)
                or (f.author_match is False) or (f.year_match is False)
                # A coordinate mismatch below the near-identical-title gate is
                # an adjacent-record signal, not harmless pagination formatting.
                # This prevents a same-journal/volume neighbour from being
                # auto-cleared solely by the composite's other boosts.
                or ((f.volume_match is False or f.pages_match is False)
                    and m.title_sim < SAME_WORK_TITLE_SIM_MIN))
    identity = assess_same_work(claimed, cand, title_similarity=m.title_sim)
    # A corporate-author or series/edition conflict is AFFIRMATIVE evidence that
    # these are distinct records (two organizations, or two editions of a
    # serial): never let a high composite assembled from shared bibliographic
    # fields auto-clear one. Deliberately NOT extended to the genre-heuristic
    # blocks (derivative_publication, uncorroborated_title_wrapper): both titles
    # of ONE review-genre work carry the same marker, so a clean high-scoring
    # pair with a one-token drift would be forced HIGH by a blanket early
    # return (adversarial review, 2026-07-15). Those blocks keep their original
    # semantics -- suppress same-work rescue, let the score/disagreement path
    # decide.
    if identity.blocked_by in ("corporate_author_conflict",
                               "series_ordinal_conflict"):
        return VERDICT_WRONG_PAPER, m
    # F2-B (second defect): the citation reads as an ordinary journal article but
    # its claimed PMID resolved to a PREPRINT record. Evidence TOWARD a fault --
    # surfaced under its own reason (``resolved_preprint_target``), NOT folded
    # into the same-work quarantine. A same-work PROOF pre-empts it (a preprint
    # that is provably the same work -- shared DOI / overwhelming anchor -- is a
    # version, not a wrong paper); otherwise the row is routed to the HIGH review
    # band so this previously-undetectable subtype is seen (audited, never
    # auto-labelled F2). Checked BEFORE the clean-MATCH early return so a
    # confirmatory boost cannot silently clear a preprint-resolved row.
    m.resolved_preprint = (is_preprint_resolved(cand)
                           and not is_preprint_source(claimed))
    if m.resolved_preprint and not identity.same_work:
        m.same_work_reason = "resolved_preprint_target"
        m.identity_signals = ("resolved_preprint",)
        return VERDICT_WRONG_PAPER, m
    # Clean accepted pairs are ordinary matches, not review variants.  For a
    # pair that would otherwise be reviewed, prefer a specific identity proof
    # over the generic near-title gate so the live path can route safely.
    if not disagree and m.score >= accept:
        return VERDICT_MATCH, m
    if identity.same_work:
        m.same_work_reason = identity.reason
        m.identity_signals = identity.signals
        return VERDICT_SAME_WORK_VARIANT, m
    # SAME_WORK_VARIANT quarantine -- checked FIRST. A near-identical title means
    # the PMID resolves to the same work, so author/year drift on it is a
    # revision / metadata-drift signature, not wrong-paper evidence. Requires a
    # real disagreement (so an unparsed-field ``None`` never diverts).
    if (m.title_sim >= SAME_WORK_TITLE_SIM_MIN and disagree
            and is_distinctive_title(claimed.title)
            and is_distinctive_title(cand.title)
            and not identity.blocked_by):
        m.same_work_reason = "near_identical_title"
        m.identity_signals = ("title_sim>=0.92",)
        return VERDICT_SAME_WORK_VARIANT, m
    # PREPRINT-SOURCE same-work quarantine (F2_V3_5): a citation whose CLAIMED
    # venue is a preprint server, resolving via its own identifier to a published
    # record, is the SAME work under a revised title -- not a wrong paper. Keyed
    # on the preprint signal (orthogonal to title_sim, so the 0.92 gate is
    # untouched). Requires author_match is True AND a real disagreement (the same
    # ``disagree`` the 0.92 branch uses) so it fires ONLY on preprint rows that
    # would otherwise be misflagged (author matches, year drifts by the preprint
    # lag = 35264587). A genuinely wrong preprint cite by a DIFFERENT author
    # (author_match is False/None) still lands in WRONG_PAPER; a CORRECTLY-cited
    # preprint that matches cleanly (no disagreement) stays MATCH and remains in
    # the denominator -- it is a true negative, not an ambiguous same-work row.
    # SAME_WORK_VARIANT is audited (not auto-cleared), so any rare misfire is
    # still seen by a human.
    first_author_ok = (f.first_author_match is True or
                       (f.first_author_match is None and f.author_match is True))
    if is_preprint_source(claimed) and first_author_ok and disagree:
        m.same_work_reason = "preprint_published_version"
        m.identity_signals = ("preprint_source", "first_author")
        return VERDICT_SAME_WORK_VARIANT, m
    # PHYSICAL-LOCATION same-work quarantine (F2-C; depends on F2-A). Two distinct
    # articles cannot occupy the same page range of the same volume of the same
    # journal. When the CANONICALIZED pages agree AND volume agrees AND journal
    # agrees, the claimed identifier points at the correct physical article, so a
    # TITLE divergence is a description defect, not a wrong paper.
    #
    # The CONJUNCTION is load-bearing: bare page agreement is weak (ranges like
    # 1-12 are ubiquitous), and it correctly does NOT fire on PMC8015328:ref011 (a
    # TRUE_F2 where the DOI agrees but volume and pages do not). first_author_match
    # is deliberately NOT part of the conjunction -- written_authors is corrupted by
    # the same defect F2-E fixes.
    #
    # Two AFFIRMATIVE distinct-work signals defer to WRONG_PAPER, because a shared
    # page range then reads as a mis-assembled / coincident citation, not one work:
    #   * doi_match is False -- both DOIs present and DISAGREE (seed-29 37192094:
    #     two real BMJ Leader papers, different DOIs, coincident vol 7 pp 266-272);
    #   * a confident field disagreement (author/year) -- the run-on-DOI adversarial
    #     rows (shared DOI, unrelated title, different author) stay HIGH.
    # A physical-location match with neither signal is a description defect on the
    # same work -> quarantine (audited), never silently cleared.
    if (f.pages_match is True and f.volume_match is True
            and f.journal_match is True
            and f.doi_match is not False and not disagree):
        m.same_work_reason = "physical_location_same_work"
        m.identity_signals = ("pages", "volume", "journal")
        return VERDICT_SAME_WORK_VARIANT, m
    # STRICT-PREFIX same-work quarantine (F2-D). When one normalized title is a
    # strict prefix of the other at a word boundary, the shorter is a truncation
    # of the longer -- the same work under a dropped subtitle. Strict prefix
    # appears in 11/34 HIGH false positives and 0/17 true positives -- the cleanest
    # single discriminator in the AUDITED set.
    #
    # But strict prefix alone is NOT sufficient in general: the extra tail can be a
    # DISCRIMINATING qualifier that names a different work in a family --
    # "Empagliflozin in heart failure" (EMPEROR-Reduced) is a strict prefix of
    # "...with a preserved ejection fraction" (EMPEROR-Preserved); a same-author
    # sequel appends "...a multicenter randomized trial...". In every such case a
    # field CONFIDENTLY disagrees (first-author position or year), so the same
    # ``not disagree`` deferral F2-C uses -- plus the DOI-disagreement deferral --
    # keeps those genuine wrong papers HIGH while still quarantining a clean
    # dropped-subtitle truncation.
    if (f.doi_match is not False and not disagree
            and _strict_title_prefix(claimed.title, cand.title)):
        m.same_work_reason = "strict_prefix_title"
        m.identity_signals = ("strict_prefix",)
        return VERDICT_SAME_WORK_VARIANT, m
    # A confident disagreement on a NON-identical title is wrong-paper evidence
    # and MUST stay in the HIGH band even when confirmatory field boosts lifted
    # the composite over ``accept`` -- the Defect-A author fix can do exactly that
    # to a genuine F2 (16639420: +0.05 for the now-parsed matching author pushes
    # its year-mismatched score past accept). Mirrors lookup.compare_and_flag's
    # flag rule; guarantees C1 (never drop a genuine wrong-paper from HIGH).
    if disagree:
        return VERDICT_WRONG_PAPER, m
    # No confident disagreement below the same-work threshold.
    if m.score >= accept:
        return VERDICT_MATCH, m
    any_agree = ((f.author_match is True) or (f.year_match is True)
                 or (f.journal_match is True))
    if not any_agree:
        return VERDICT_WRONG_PAPER, m
    return VERDICT_FORMATTING, m


def best_match(claimed: Claimed, candidates: list[RetrievedRecord],
               accept: float = 0.85, margin: float = 0.05) -> BestMatch:
    """Pick the highest-scoring candidate. ``confident`` requires both a score
    at/above ``accept`` AND a clear ``margin`` over the runner-up (a near-tie is
    ambiguous, never confident -- precision-first).

    ``accept`` and ``margin`` are calibration targets; defaults favor precision.
    ``accept`` is threaded into ``match_score`` so the strong-corroboration
    override floors at the SAME threshold a non-default ``accept`` sets here.
    """
    scored = sorted((match_score(claimed, c, accept=accept) for c in candidates),
                    key=lambda m: m.score, reverse=True)
    if not scored:
        return BestMatch(found=False)
    top = scored[0]
    ambiguous = len(scored) > 1 and (top.score - scored[1].score) < margin
    confident = top.score >= accept and not ambiguous
    return BestMatch(found=True, best=top, confident=confident,
                     ambiguous=ambiguous, runners_up=scored[1:])


# =====================================================================
# Candidate retrieval (Crossref + OpenAlex -> RetrievedRecord)
# =====================================================================
def _coerce_year(y) -> Optional[int]:
    if isinstance(y, int):
        return y
    return int(y) if isinstance(y, str) and y.strip().isdigit() else None


def _crossref_record(item: dict) -> RetrievedRecord:
    title = " ".join(t for t in (item.get("title") or []) if t)
    authors = []
    for a in item.get("author") or []:
        if not isinstance(a, dict):          # the API can emit null array entries
            continue
        name = a.get("family") or a.get("name") or ""
        if name:
            authors.append(name)
    year = None
    parts = (item.get("issued") or {}).get("date-parts") or []
    if parts and parts[0]:
        year = _coerce_year(parts[0][0])
    journal = " ".join(t for t in (item.get("container-title") or []) if t)
    pages = item.get("page") or ""
    return RetrievedRecord(
        resolved=False, title=title, authors=authors, year=year,
        journal=journal, pmid="", doi=(item.get("DOI") or "").lower(),
        volume=str(item.get("volume") or ""), pages=str(pages))


def _openalex_record(result: dict) -> RetrievedRecord:
    title = result.get("title") or result.get("display_name") or ""
    authors = []
    for au in result.get("authorships") or []:
        if not isinstance(au, dict):
            continue
        name = (au.get("author") or {}).get("display_name") or ""
        if name:
            authors.append(name)
    src = (result.get("primary_location") or {}).get("source") or {}
    journal = src.get("display_name") or \
        (result.get("host_venue") or {}).get("display_name") or ""
    biblio = result.get("biblio") or {}
    pages = ""
    if biblio.get("first_page"):
        pages = str(biblio["first_page"])
        if biblio.get("last_page"):
            pages += f"-{biblio['last_page']}"
    doi = (result.get("doi") or "").lower().replace("https://doi.org/", "")
    return RetrievedRecord(
        resolved=False, title=title, authors=authors,
        year=_coerce_year(result.get("publication_year")),
        journal=journal, pmid="", doi=doi,
        volume=str(biblio.get("volume") or ""), pages=pages)


def _json_or_none(resp):
    """Parsed JSON for a healthy 200, else None (errored search, distinct from a
    200 that found nothing)."""
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _crossref_candidates(claimed: Claimed, n: int, session) -> list[RetrievedRecord]:
    query = " ".join(str(p) for p in (
        claimed.title, claimed.authors[0] if claimed.authors else "",
        claimed.year or "", claimed.journal) if p)
    try:
        resp = request_with_retry(session, CROSSREF_URL,
                                  {"query.bibliographic": query, "rows": n},
                                  limiter=CROSSREF, timeout=20)
    except requests.RequestException:
        return []
    data = _json_or_none(resp)
    if data is None:
        return []
    out = []
    for it in data.get("message", {}).get("items", []) or []:
        if isinstance(it, dict):
            out.append(_crossref_record(it))
    return out


def _openalex_candidates(claimed: Claimed, n: int, session) -> list[RetrievedRecord]:
    try:
        resp = request_with_retry(session, OPENALEX_URL,
                                  {"search": claimed.title, "per-page": n},
                                  limiter=OPENALEX, timeout=20)
    except requests.RequestException:
        return []
    data = _json_or_none(resp)
    if data is None:
        return []
    out = []
    for it in data.get("results", []) or []:
        if isinstance(it, dict):
            out.append(_openalex_record(it))
    return out


def retrieve_candidates(claimed: Claimed, n: int = 5,
                        session: requests.Session | None = None
                        ) -> list[RetrievedRecord]:
    """Query Crossref ``query.bibliographic`` and OpenAlex ``search`` and parse
    the top-n of each into ``RetrievedRecord``. Dedup by DOI, then by normalized
    title. Reuses the shared CROSSREF / OPENALEX rate limiters.

    Returns an empty list when both searches error or find nothing -- a network
    failure is indistinguishable here from a true no-find, and the caller treats
    "no candidates" as "not confidently matched" (escalate, never F1)."""
    cands = _crossref_candidates(claimed, n, session) + \
        _openalex_candidates(claimed, n, session)

    deduped: list[RetrievedRecord] = []
    seen_doi: set[str] = set()
    seen_title: set[str] = set()
    for c in cands:
        if not (c.title or c.doi):
            continue
        if c.doi and c.doi in seen_doi:
            continue
        nt = normalize_title(c.title)
        if nt and nt in seen_title:
            continue
        if c.doi:
            seen_doi.add(c.doi)
        if nt:
            seen_title.add(nt)
        deduped.append(c)
    return deduped
