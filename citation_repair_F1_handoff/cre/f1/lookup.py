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
import re

import requests
from rapidfuzz import fuzz

from .schema import Reference, RetrievedRecord
from .ratelimit import NCBI, request_with_retry

EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


def _normalize(t: str) -> str:
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


def _year_from_medline(fields: dict) -> int | None:
    """Publication year. DP is canonical; DEP (epub date, often YYYYMMDD) is the
    electronic-only fallback."""
    for tag in ("DP", "DEP"):
        for v in fields.get(tag, []):
            m = re.search(r"(?:19|20)\d{2}", v)
            if m:
                return int(m.group())
    return None


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
    title = _first_nonempty(fields, "TI", "BTI", "TT")
    if not fields.get("PMID") and not title:
        return RetrievedRecord(resolved=False, pmid=pmid)

    authors = [_au_surname(a) for a in fields.get("AU", [])]
    if not authors and fields.get("FAU"):       # fall back to full author names
        authors = [a.split(",")[0].strip() for a in fields["FAU"]]
    authors += fields.get("CN", [])             # corporate/collective authors, raw

    return RetrievedRecord(
        resolved=True,
        title=title,
        authors=[a for a in authors if a],
        year=_year_from_medline(fields),
        journal=_first_nonempty(fields, "TA", "JT"),
        pmid=(fields.get("PMID") or [pmid])[0],
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


def compare_and_flag(ref: Reference, threshold: float = 85.0,
                     author_tripwire: bool = True) -> bool:
    """Populate the log and return True if this reference is a CANDIDATE
    (dead PMID, claimed PMID resolves to a low-similarity title, or -- with the
    trip-wire on -- a similar title whose authors lack the claimed first author).
    """
    log = ref.log
    log.pmid_present = bool(ref.claimed.claimed_pmid)
    if not log.pmid_present:
        return False                       # -> unverifiable, handled in decide()

    log.pmid_resolved = ref.retrieved.resolved
    if not ref.retrieved.resolved:
        log.mismatch_flagged = True        # dead PMID is a strong candidate
        log.notes = "claimed PMID did not resolve"
        return True

    sim = title_similarity(ref.claimed.title, ref.retrieved.title)
    log.title_similarity = sim
    cl = {a.lower() for a in ref.claimed.authors}
    rt = {a.lower() for a in ref.retrieved.authors}
    log.author_match = bool(cl & rt) if cl and rt else None
    log.year_match = (ref.claimed.year == ref.retrieved.year) \
        if ref.claimed.year and ref.retrieved.year else None

    flagged = sim < threshold
    if flagged:
        log.notes = f"title similarity {sim:.0f} < {threshold:.0f}"

    # Trip-wire: title is similar enough to pass, but the claimed first author
    # is absent from the record the PMID resolves to -> recombination candidate.
    if not flagged and author_tripwire:
        present = _claimed_first_author_present(ref.claimed.authors,
                                                ref.retrieved.authors)
        if present is not None:               # None = unjudgeable; leave as None
            log.author_tripwire = (present is False)
        if present is False:
            flagged = True
            log.notes = (f"title similar (sim {sim:.0f}) but claimed first "
                         f"author {ref.claimed.authors[0]!r} absent from "
                         f"resolved record")

    log.mismatch_flagged = flagged
    return flagged
