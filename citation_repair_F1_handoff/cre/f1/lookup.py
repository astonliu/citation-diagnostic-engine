"""Phases 1c-1e (cheap path) -- claimed-PMID lookup + metadata comparison.

This is the CHEAP candidate filter from plan Phase 3b: it uses EFetch only,
no Crossref/OpenAlex. Run this over a large slice to concentrate the candidate
stream before spending the expensive multi-DB confirmation.

A reference is "flagged" (a candidate) when:
    - it has a claimed PMID, AND
    - the PMID is dead (no record), OR resolves to a low-similarity title, OR
      (trip-wire, opt-in) resolves to a similar title whose author list does
      NOT contain the claimed first author -- the recombination case where an
      invented PMID lands on a real, similarly-titled paper by other authors.

Set NCBI_API_KEY in config for ~10 req/s; EFetch shares the NCBI rate budget
with the ESearch/ESummary calls in confirm.py via the shared limiter.
"""
from __future__ import annotations
import html
import re
import unicodedata

import requests
from rapidfuzz import fuzz

from .schema import Reference, RetrievedRecord
from .ratelimit import NCBI, request_with_retry
from .biblio_match import match_score, retrieve_candidates, best_match
from .unscoreable import classify_unscoreable

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


# Greek letter -> English name. Maps both lowercase and uppercase forms.
# Needed because PMC/JATS titles carry literal Greek (β-glucans) while
# PubMed/Crossref records spell them out (beta-glucans); without this the SAME
# paper scores as a mismatch.
_GREEK = {
    "\u03b1": "alpha", "\u0391": "alpha",
    "\u03b2": "beta",  "\u0392": "beta",
    "\u03b3": "gamma", "\u0393": "gamma",
    "\u03b4": "delta", "\u0394": "delta",
    "\u03b5": "epsilon", "\u0395": "epsilon",
    "\u03b6": "zeta",  "\u0396": "zeta",
    "\u03b7": "eta",   "\u0397": "eta",
    "\u03b8": "theta", "\u0398": "theta",
    "\u03b9": "iota",  "\u0399": "iota",
    "\u03ba": "kappa", "\u039a": "kappa",
    "\u03bb": "lambda", "\u039b": "lambda",
    "\u03bc": "mu",    "\u039c": "mu",
    "\u03bd": "nu",    "\u039d": "nu",
    "\u03be": "xi",    "\u039e": "xi",
    "\u03bf": "omicron", "\u039f": "omicron",
    "\u03c0": "pi",    "\u03a0": "pi",
    "\u03c1": "rho",   "\u03a1": "rho",
    "\u03c3": "sigma", "\u03c2": "sigma", "\u03a3": "sigma",
    "\u03c4": "tau",   "\u03a4": "tau",
    "\u03c5": "upsilon", "\u03a5": "upsilon",
    "\u03c6": "phi",   "\u03a6": "phi",
    "\u03c7": "chi",   "\u03a7": "chi",
    "\u03c8": "psi",   "\u03a8": "psi",
    "\u03c9": "omega", "\u03a9": "omega",
    "\u00b5": "mu",    # MICRO SIGN (distinct codepoint from Greek mu)
}

_TAG_RE = re.compile(r"<[^>]+>")
_GREEK_RE = re.compile("|".join(map(re.escape, _GREEK)))


def _normalize(t: str) -> str:
    """Normalize a title/name for fuzzy comparison.

    Steps run IN ORDER. Each exists to stop a specific formatting difference
    from making the SAME work look like a different one (observed in the F2
    base-rate test):

      1. Unescape HTML entities (&amp;, &lt;, &#x2014; ...) -- JATS/Crossref
         carry entity-encoded characters.
      2. Strip HTML/MathML tags (<sub>, </sub>, <i>, <sup> ...) -- e.g.
         CHA<sub>2</sub>DS<sub>2</sub> vs CHA2DS2.
      3. Map Greek letters to English names (beta-glucans vs beta-glucans).
      4. NFKD-fold and drop combining marks to fold diacritics to ASCII
         (AlZu'bi vs AlZubi; also normalizes sub/superscript digit forms).
      5. Lowercase, replace remaining non-word/space chars with a space,
         collapse whitespace, strip.

    Do NOT "simplify" this back to a single non-alnum strip -- that is the bug
    this function fixes.
    """
    if not t:
        return ""
    # 1. HTML entities
    t = html.unescape(t)
    # 2. HTML/MathML tags
    t = _TAG_RE.sub(" ", t)
    # 3. Greek letters -> names
    t = _GREEK_RE.sub(lambda m: _GREEK[m.group()], t)
    # 4. fold diacritics / compatibility forms to ASCII
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    # 5. existing behavior
    t = t.lower()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def title_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return float(fuzz.token_sort_ratio(_normalize(a), _normalize(b)))


def fetch_pubmed(pmid: str, api_key: str = "", email: str = "",
                 session: requests.Session | None = None) -> RetrievedRecord:
    """Retrieve the record the claimed PMID actually points to."""
    if not pmid:
        return RetrievedRecord(resolved=False)
    params = {"db": "pubmed", "id": pmid, "rettype": "medline", "retmode": "text"}
    if api_key:
        params["api_key"] = api_key
    if email:
        params["email"] = email
    try:
        r = request_with_retry(session, EFETCH, params, limiter=NCBI, timeout=20)
        if r is None or r.status_code != 200 or not r.text.strip():
            return RetrievedRecord(resolved=False, pmid=pmid)
        return _parse_medline(r.text, pmid)
    except requests.RequestException:
        return RetrievedRecord(resolved=False, pmid=pmid)


def _au_surname(au: str) -> str:
    """MEDLINE AU is 'Surname Initials' (e.g. 'Smith JA'). Strip the trailing
    initials token if it looks like initials; surnames may contain spaces."""
    au = au.strip()
    if "," in au:                      # some sources use 'Surname, I'
        return au.split(",")[0].strip()
    parts = au.split()
    if len(parts) >= 2 and re.fullmatch(r"[A-Z]{1,3}", parts[-1]):
        return " ".join(parts[:-1])
    return au


def _first_nonempty(fields: dict, *tags: str) -> str:
    for t in tags:
        vals = fields.get(t)
        if vals and vals[0]:
            return vals[0]
    return ""


def _year_from_medline(fields: dict) -> tuple[int | None, bool]:
    """(publication year, came_from_DEP). DP is canonical; DEP (epub date, often
    YYYYMMDD) is the electronic-only fallback. ``came_from_DEP`` is True when DP
    was absent and the year came from DEP -- an epub-ahead-of-print signal the
    field matcher uses to widen its year tolerance for a preprint->publication
    gap on the SAME work."""
    for v in fields.get("DP", []):
        m = re.search(r"(?:19|20)\d{2}", v)
        if m:
            return int(m.group()), False
    for v in fields.get("DEP", []):
        m = re.search(r"(?:19|20)\d{2}", v)
        if m:
            return int(m.group()), True
    return None, False


def _parse_medline(text: str, pmid: str) -> RetrievedRecord:
    # MEDLINE: each field begins with a 2-4 letter tag + '-'; continuation
    # lines are indented. Join continuations onto their field first. Skip blank
    # lines so trailing whitespace between records doesn't get glued on.
    joined: list[str] = []
    for line in text.splitlines():
        if re.match(r"^[A-Z]{2,4}\s*-", line):
            joined.append(line)
        elif joined and line.strip():
            joined[-1] += " " + line.strip()

    fields: dict[str, list[str]] = {}
    for line in joined:
        m = re.match(r"^([A-Z]{2,4})\s*-\s*(.*)$", line)
        if not m:
            continue
        tag, val = m.group(1), m.group(2).strip()
        if val:
            fields.setdefault(tag, []).append(val)

    # A real record always carries a PMID; some carry a book title (BTI) or
    # transliterated title (TT) instead of TI. No PMID and no title => junk.
    ti = _first_nonempty(fields, "TI")
    bti = _first_nonempty(fields, "BTI")
    title = ti or bti or _first_nonempty(fields, "TT")
    # A record whose only title is a BOOK title (BTI, no article-level TI) is a
    # container, not the cited chapter -- title-matching a chapter claim against
    # it is meaningless, so flag it for the UNSCOREABLE gate.
    is_container = bool(bti) and not ti
    if not fields.get("PMID") and not title:
        return RetrievedRecord(resolved=False, pmid=pmid)

    authors = [_au_surname(a) for a in fields.get("AU", [])]
    if not authors and fields.get("FAU"):       # fall back to full author names
        authors = [a.split(",")[0].strip() for a in fields["FAU"]]
    authors += fields.get("CN", [])             # corporate/collective authors, raw

    year, year_from_dep = _year_from_medline(fields)
    return RetrievedRecord(
        resolved=True,
        title=title,
        authors=[a for a in authors if a],
        year=year,
        journal=_first_nonempty(fields, "TA", "JT"),
        pmid=(fields.get("PMID") or [pmid])[0],
        is_container=is_container,
        year_from_dep=year_from_dep,
    )


# --------------------------------------------------------------------------
# Author-mismatch trip-wire (HANDOFF task 2)
# --------------------------------------------------------------------------
def _norm_name(s: str) -> str:
    return _normalize(s)


def _claimed_first_author_present(claimed_authors: list[str],
                                  resolved_authors: list[str]) -> bool | None:
    """Is the claimed FIRST author's surname present in the resolved record?

    Returns True/False, or None when we lack the data to judge (no claimed first
    author, or the resolved record has no authors). None => do NOT trip
    (precision-first: never flag on absence of evidence).
    """
    if not claimed_authors or not resolved_authors:
        return None
    claimed = _norm_name(claimed_authors[0])
    if not claimed:
        return None
    resolved = {n for n in (_norm_name(a) for a in resolved_authors) if n}
    if not resolved:
        return None
    if claimed in resolved:
        return True
    claimed_tokens = [t for t in claimed.split() if len(t) >= 3]
    for r in resolved:
        if claimed == r:
            return True
        # distinctive token match handles particles ('van der Berg' ~ 'Berg')
        if claimed_tokens and claimed_tokens[-1] in r.split():
            return True
        # containment only when both are long enough to be unambiguous
        if len(r) >= 4 and len(claimed) >= 4 and (r in claimed or claimed in r):
            return True
    return False


# --------------------------------------------------------------------------
# No-ID branch: structured bibliographic lookup
# --------------------------------------------------------------------------
# HANDOFF_BIBLIO_MATCH supersedes the old single-token_sort_ratio judging here:
# candidate retrieval + parsing now live in biblio_match.py, and the confident
# match decision is made by the structured matcher (title similarity + field
# agreement), not a bare title threshold. The ROUTING is unchanged -- a no-PMID
# reference still goes to a lookup whose only outcomes are CLEARED or escalation
# (-> human_review), never straight to F1.
def _maybe_rerank(claimed, candidates, accept, bm):
    """Stage-2 tie-break: when the top two candidates are within ``margin``,
    re-rank with the MedCPT cross-encoder. Degrades to the Stage-1 ``bm`` when
    the optional model/dependency is unavailable (the common case)."""
    try:
        from .biblio_rerank import rerank_stage2
    except Exception:                         # noqa: BLE001 - module optional
        return bm
    try:
        reranked = rerank_stage2(claimed, candidates, accept=accept)
    except Exception:                         # noqa: BLE001 - model load/runtime
        return bm
    return reranked if reranked is not None else bm


def fuzzy_biblio_lookup(ref: Reference, threshold: float = 85.0,
                        session: requests.Session | None = None
                        ) -> RetrievedRecord:
    """Structured bibliographic lookup for references with no claimed PMID.

    Retrieves candidates from Crossref bibliographic search + OpenAlex title
    search (:func:`biblio_match.retrieve_candidates`) and picks the best with the
    structured matcher (:func:`biblio_match.best_match`): normalized title
    similarity plus author/year/journal/volume/pages agreement. When the top two
    candidates are within ``margin`` the optional Stage-2 cross-encoder re-ranks
    (and degrades to Stage 1 if unavailable).

    Returns a ``RetrievedRecord`` with ``resolved=True`` and the winning hit's
    metadata only on a CONFIDENT match (score >= ``threshold``/100 with a clear
    margin over the runner-up); otherwise ``resolved=False``. ``.pmid`` is always
    empty (there is none on this path).

    If both databases errored or returned nothing, ``retrieve_candidates`` yields
    an empty list and this returns ``resolved=False`` -- a network failure is NOT
    treated as "found nothing"; the caller escalates such cases through the
    confirmation path (its own all-errored guard), never straight to F1. Uses the
    shared CROSSREF / OPENALEX rate limiters.
    """
    candidates = retrieve_candidates(ref.claimed, session=session)
    if not candidates:                       # both DBs errored or found nothing
        return RetrievedRecord(resolved=False)
    accept = threshold / 100.0
    bm = best_match(ref.claimed, candidates, accept=accept)
    if bm.found and bm.ambiguous:
        bm = _maybe_rerank(ref.claimed, candidates, accept, bm)
    if bm.found and bm.confident and bm.best is not None and bm.best.record:
        rec = bm.best.record
        rec.resolved = True
        return rec
    return RetrievedRecord(resolved=False)


def _year_disagreement(fields) -> bool:
    """True when the years CONFIDENTLY disagree (year_match is False).

    A year disagreement is direct wrong-reference evidence, but the confirmatory
    boosts in ``match_score`` can lift the composite back over ``accept`` and bury
    it: fixing the parser's author extraction adds +0.05, pushing a genuine
    wrong-reference whose year disagrees (e.g. the 16639420 paper-series case,
    0.8339) up to 0.8839 -- silently UN-flagged. Flagging on a year disagreement
    regardless of the boosted composite closes that recall hole WITHOUT raising
    ``accept`` (C1) and WITHOUT auto-clearing on agreement (C2). It only ever ADDS
    a flag, so it cannot drop a genuine F2; None (can't-judge) never trips it.

    Author disagreement is deliberately NOT handled here -- it is owned by the
    opt-in author trip-wire below (with its own ``log.author_tripwire`` signal),
    so that ``author_tripwire=False`` still fully opts out of author-based
    flagging. Both penalty-bearing fields (author, year) are thus covered at a
    boosted-over-accept score: author by the trip-wire, year here.
    """
    return fields.year_match is False


def _override_quality(fields) -> bool:
    """The strong-corroboration condition (mirrors biblio_match.match_score's
    override gate): the two HIGH-ENTROPY fields -- first-author surname AND
    journal -- both agree, and no field disagrees. This is the only corroboration
    strong enough to let a sub-accept title be CLEARED; author-only or
    year-only agreement is not (low entropy, collides across unrelated works)."""
    if not (fields.author_match is True and fields.journal_match is True):
        return False
    return not any(v is False for v in (fields.author_match, fields.year_match,
                                        fields.journal_match, fields.volume_match,
                                        fields.pages_match))


def _flag_decision(m, accept: float) -> bool:
    """The F2-screen flag predicate, shared by the PMID and no-PMID paths.

    Flag when ANY holds:
      * the composite is below accept;
      * the years confidently disagree (a boost may have buried it -- the 16639420
        paper-series case after the parser author fix);
      * the title is below accept and is NOT rescued by override-quality
        corroboration -- so confirmatory boosts ALONE (e.g. the lone +0.05 author
        boost the parser fix now adds on a sparse ref whose year+journal are
        unparsed) cannot carry a sub-accept title over accept and silently clear a
        genuine wrong-reference.
    Every disjunct only ADDS a flag (recall-first, C1); none ever clears (C2). It
    does not raise ``accept``: a title at/above accept still clears on its own.
    """
    return (m.score < accept
            or _year_disagreement(m.fields)
            or (m.title_sim < accept and not _override_quality(m.fields)))


def compare_and_flag(ref: Reference, threshold: float = 85.0,
                     author_tripwire: bool = True,
                     session: requests.Session | None = None) -> bool:
    """Populate the log and return True if this reference is a CANDIDATE
    (dead PMID, claimed PMID resolves to a low-similarity title, or -- with the
    trip-wire on -- a similar title whose authors lack the claimed first author).

    No-ID path (no claimed PMID): instead of giving up, run a structured
    bibliographic lookup. A confident, well-matching hit clears the reference; a
    poor match or no match escalates to the LLM + confirmation path -- never
    straight to F1 (see decide.py for the precision-first no-ID outcome).
    """
    log = ref.log
    log.pmid_present = bool(ref.claimed.claimed_pmid)
    accept = threshold / 100.0             # match_score is 0..1; threshold is 0..100
    if not log.pmid_present:
        if not ref.claimed.title:
            # Nothing to search on -> genuinely unverifiable.
            log.notes = "No claimed PMID and no title; cannot attempt lookup."
            return False                   # decide() will set UNVERIFIABLE
        # Claimed-side UNSCOREABLE (journal name / regulatory code in the title
        # slot): a non-title carries no wrong-reference evidence, and there is
        # nothing meaningful to search on. Route to the counted bucket.
        bucket, reason = classify_unscoreable(ref.claimed, None)
        if bucket:
            log.unscoreable_reason = bucket
            log.notes = f"UNSCOREABLE ({bucket}): {reason}"
            return False                   # decide() -> UNSCOREABLE (dropped)
        retrieved = fuzzy_biblio_lookup(ref, threshold=threshold, session=session)
        ref.retrieved = retrieved
        log.pmid_present = False            # stays False; downstream = no-ID path
        log.noid_lookup_attempted = True
        if retrieved.resolved:
            # Resolved-side UNSCOREABLE (placeholder / book-container record).
            bucket, reason = classify_unscoreable(ref.claimed, retrieved)
            if bucket:
                log.unscoreable_reason = bucket
                log.notes = f"UNSCOREABLE ({bucket}): {reason}"
                return False
            # Re-score claimed vs the chosen record with the structured matcher
            # (truncation-robust title + field agreement). title_similarity is
            # logged on the established 0..100 scale; match_score on 0..1.
            m = match_score(ref.claimed, retrieved)
            log.title_similarity = round(m.title_sim * 100, 1)
            log.match_score = m.score
            log.author_match = m.fields.author_match
            log.year_match = m.fields.year_match
            log.journal_match = m.fields.journal_match
            log.volume_match = m.fields.volume_match
            log.pages_match = m.fields.pages_match
            log.override_fired = m.override_fired
            # Same screen predicate as the PMID path (a confident year
            # disagreement or a boost-only sub-accept clear escalates rather than
            # clearing). No-PMID can never become F2, so a flag here only routes
            # to human_review -- but keeping the two paths consistent avoids
            # auto-clearing a year-mismatched pair on one path and flagging it on
            # the other.
            if not _flag_decision(m, accept):
                # Reference exists and points to the right work as far as we can
                # tell -> cleared (was_flagged=False in decide()).
                log.mismatch_flagged = False
                log.notes = (f"No PMID; bibliographic match found "
                             f"(match_score {m.score:.2f}).")
                return False
            # Found a candidate but it doesn't match well -> possible wrong ref.
            log.mismatch_flagged = True
            log.notes = (f"No PMID; bibliographic lookup found a candidate but "
                         f"it did not cleanly match (match_score {m.score:.2f}, "
                         f"title_sim {m.title_sim:.2f}).")
            return True                    # continue to LLM filter + confirm path
        # Not found confidently -> do NOT label F1; escalate.
        log.mismatch_flagged = True
        log.noid_not_found = True
        log.notes = "No PMID; bibliographic lookup found no confident match."
        return True                        # continue to LLM filter + confirm path

    log.pmid_resolved = ref.retrieved.resolved
    if not ref.retrieved.resolved:
        log.mismatch_flagged = True        # dead PMID is a strong candidate
        log.notes = "claimed PMID did not resolve"
        return True

    # UNSCOREABLE gate: a non-title / placeholder / book-container pair carries
    # no evidence about whether the PMID points to the wrong paper. Route it to
    # the counted bucket BEFORE scoring, so the strong-corroboration override
    # cannot silently floor it to ``accept`` and clear it (the 30539090 path).
    bucket, reason = classify_unscoreable(ref.claimed, ref.retrieved)
    if bucket:
        log.unscoreable_reason = bucket
        log.notes = f"UNSCOREABLE ({bucket}): {reason}"
        return False                       # decide() -> UNSCOREABLE (dropped)

    # Structured match: containment-aware title similarity + field agreement.
    # A truncated-but-correct title whose author/year/journal agree now scores
    # HIGH (field boosts compensate) and is not flagged; a PMID resolving to an
    # unrelated paper scores LOW on title AND fields -> flagged (Dr. Roberts'
    # concern). title_similarity stays on 0..100; match_score is the 0..1 verdict.
    m = match_score(ref.claimed, ref.retrieved)
    log.title_similarity = round(m.title_sim * 100, 1)
    log.match_score = m.score
    log.author_match = m.fields.author_match
    log.year_match = m.fields.year_match
    log.journal_match = m.fields.journal_match
    log.volume_match = m.fields.volume_match
    log.pages_match = m.fields.pages_match
    log.override_fired = m.override_fired

    # Flag via the shared screen predicate (low composite, buried year
    # disagreement, or a sub-accept title not rescued by override-quality
    # corroboration). Recall-first; never raises accept, never auto-clears.
    flagged = _flag_decision(m, accept)
    if flagged:
        if m.score < accept:
            log.notes = (f"match_score {m.score:.2f} < {accept:.2f} "
                         f"(title_sim {m.title_sim:.2f})")
        elif _year_disagreement(m.fields):
            log.notes = (f"match_score {m.score:.2f} >= {accept:.2f} but the "
                         f"years confidently disagree (year_match=False); "
                         f"wrong-reference evidence the boosts masked.")
        else:
            log.notes = (f"title_sim {m.title_sim:.2f} < {accept:.2f}; composite "
                         f"{m.score:.2f} reached accept on confirmatory boosts "
                         f"alone without author+journal corroboration.")

    # Trip-wire: title is similar enough to pass, but the claimed first author
    # is absent from the record the PMID resolves to -> recombination candidate.
    if not flagged and author_tripwire:
        present = _claimed_first_author_present(ref.claimed.authors,
                                                ref.retrieved.authors)
        if present is not None:               # None = unjudgeable; leave as None
            log.author_tripwire = (present is False)
        if present is False:
            flagged = True
            log.notes = (f"title similar (match_score {m.score:.2f}) but claimed "
                         f"first author {ref.claimed.authors[0]!r} absent from "
                         f"resolved record")

    log.mismatch_flagged = flagged
    return flagged