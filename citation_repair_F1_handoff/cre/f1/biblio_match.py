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
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

import requests
from rapidfuzz.distance import JaroWinkler

from .schema import ClaimedRef, RetrievedRecord
from .ratelimit import CROSSREF, OPENALEX, request_with_retry

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
    year_match: Optional[bool] = None
    journal_match: Optional[bool] = None
    volume_match: Optional[bool] = None
    pages_match: Optional[bool] = None


@dataclass
class MatchResult:
    score: float                       # 0..1 composite
    title_sim: float                   # 0..1 title-only similarity
    fields: FieldAgreement
    record: Optional[RetrievedRecord] = None   # the candidate this scores
    override_fired: bool = False       # strong-corroboration override floored the score


@dataclass
class BestMatch:
    found: bool
    best: Optional[MatchResult] = None
    confident: bool = False
    ambiguous: bool = False
    runners_up: list = field(default_factory=list)


# =====================================================================
# Title scoring (containment-aware, so truncation doesn't tank the score)
# =====================================================================
# PubMed brackets translated (non-English) titles: "[Results of ...]".
# Corrigendum/erratum/correction notices decorate the original title.
_TITLE_PREFIX_RE = re.compile(
    r"^\s*(erratum|corrigendum|correction|retraction)\b[:\-\s]*", re.I)


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
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
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


def jaro_winkler(a: str, b: str) -> float:
    """Prefix-weighted edit similarity in 0..1 (truncation-robust)."""
    return float(JaroWinkler.similarity(a, b))


def title_sim(claimed: str, candidate: str) -> float:
    """0..1. Robust to truncation and dropped subtitles.

    ``max`` of (a) Jaro-Winkler (prefix-weighted, good for truncated prefixes)
    and (b) the S2ORC-style harmonic mean of trigram Jaccard and trigram
    containment (containment rescues a short-but-correct title)."""
    a, b = normalize_title(claimed), normalize_title(candidate)
    if not a or not b:
        return 0.0
    jw = jaro_winkler(a, b)
    tri_j = trigram_jaccard(a, b)
    cont = trigram_containment(a, b)
    hm = 0.0 if (tri_j + cont) == 0 else 2 * tri_j * cont / (tri_j + cont)
    return max(jw, hm)


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
    for cand in cand_authors:
        c = _norm(cand)
        if not c:
            continue
        if claimed_surname == c:
            return True
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

    # journal (bidirectional normalized containment) -- computed before year so
    # the preprint year-tolerance below can require journal corroboration.
    cj, rj = _norm(claimed.journal), _norm(cand.journal)
    if cj and rj:
        fa.journal_match = (cj in rj) or (rj in cj)

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

    # volume / pages (digits only)
    cv, rv = _digits(claimed.volume), _digits(cand.volume)
    if cv and rv:
        fa.volume_match = cv == rv
    cp, rp = _digits(claimed.pages), _digits(cand.pages)
    if cp and rp:
        fa.pages_match = cp == rp

    return fa


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


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
    override_fired = (f.author_match is True and f.journal_match is True
                      and disagree == 0 and score < accept)
    if override_fired:
        score = accept
    # -----------------------------------------------------------------------

    score = round(max(0.0, min(1.0, score)), 4)   # round: avoid float knife-edges
    return MatchResult(score=score, title_sim=round(ts, 4), fields=f, record=cand,
                       override_fired=override_fired)


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