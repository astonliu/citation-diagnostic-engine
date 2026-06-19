"""Phase 1g -- multi-database confirmation.

Searches for the CLAIMED title + first author (NOT the claimed PMID) across
PubMed, Crossref, and OpenAlex. The question is: does the claimed work exist
ANYWHERE? Google Scholar is intentionally omitted (no API, not reproducible).

Returns a dict {db: best_score|None}. A db "finds" the work if its best title
match clears `match_threshold`. None means the search itself errored (network /
parse) and is distinct from 0.0 (searched, found nothing) -- decide.py treats an
all-errored result as "cannot rule out existence", not as fabrication evidence.

All three searches go through the shared rate limiters + retry helper so a
scaled run respects NCBI / Crossref / OpenAlex budgets and survives transient
429/5xx.
"""
from __future__ import annotations
import requests
from rapidfuzz import fuzz

from .schema import Reference
from .lookup import _normalize
from .ratelimit import NCBI, CROSSREF, OPENALEX, request_with_retry

PUBMED_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
CROSSREF_URL = "https://api.crossref.org/works"
OPENALEX_URL = "https://api.openalex.org/works"


def _score(claimed_title: str, cand_title: str) -> float:
    if not claimed_title or not cand_title:
        return 0.0
    return float(fuzz.token_sort_ratio(_normalize(claimed_title), _normalize(cand_title)))


def _json_or_none(resp):
    """Return parsed JSON for a healthy 200 response, else None (treated as an
    errored search, not 'found nothing')."""
    if resp is None or resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def search_pubmed(title: str, api_key: str = "", s=requests) -> float | None:
    if not title:
        return 0.0
    try:
        esearch = _json_or_none(request_with_retry(s, PUBMED_ESEARCH, {
            "db": "pubmed", "term": f"{title}[Title]", "retmode": "json",
            "retmax": 3, **({"api_key": api_key} if api_key else {})},
            limiter=NCBI, timeout=20))
        if esearch is None:
            return None
        ids = esearch.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return 0.0
        summary = _json_or_none(request_with_retry(s, PUBMED_ESUMMARY, {
            "db": "pubmed", "id": ",".join(ids), "retmode": "json",
            **({"api_key": api_key} if api_key else {})},
            limiter=NCBI, timeout=20))
        if summary is None:
            return None
        res = summary.get("result", {})
        return max((_score(title, res[i].get("title", "")) for i in ids if i in res),
                   default=0.0)
    except (requests.RequestException, ValueError, KeyError):
        return None


def search_crossref(title: str, mailto: str = "", s=requests) -> float | None:
    if not title:
        return 0.0
    try:
        data = _json_or_none(request_with_retry(s, CROSSREF_URL, {
            "query.bibliographic": title, "rows": 3,
            **({"mailto": mailto} if mailto else {})},
            limiter=CROSSREF, timeout=20))
        if data is None:
            return None
        items = data.get("message", {}).get("items", [])
        # Crossref title is a LIST of strings (often one element, sometimes more).
        return max((_score(title, " ".join(it.get("title") or [])) for it in items),
                   default=0.0)
    except (requests.RequestException, ValueError, KeyError):
        return None


def search_openalex(title: str, mailto: str = "", s=requests) -> float | None:
    if not title:
        return 0.0
    try:
        params = {"filter": f"title.search:{title}", "per-page": 3}
        if mailto:
            params["mailto"] = mailto
        data = _json_or_none(request_with_retry(s, OPENALEX_URL, params,
                                                limiter=OPENALEX, timeout=20))
        if data is None:
            return None
        items = data.get("results", [])
        # OpenAlex title may be null; fall back to display_name, then "".
        return max((_score(title, it.get("title") or it.get("display_name") or "")
                    for it in items), default=0.0)
    except (requests.RequestException, ValueError, KeyError):
        return None


def confirm(ref: Reference, api_key="", crossref_mailto="", openalex_mailto="",
            match_threshold: float = 85.0, s=requests) -> dict:
    """Search all three; record per-db best scores; return the dict.

    `match_threshold` is accepted for call-site symmetry but applied later in
    found_anywhere(); confirm() only gathers raw best-match scores.
    """
    title = ref.claimed.title
    hits = {
        "pubmed": search_pubmed(title, api_key, s),
        "crossref": search_crossref(title, crossref_mailto, s),
        "openalex": search_openalex(title, openalex_mailto, s),
    }
    ref.log.db_hits = hits
    return hits


def all_errored(hits: dict) -> bool:
    """True when every search errored (all None) -- no evidence either way."""
    return bool(hits) and all(v is None for v in hits.values())


def found_anywhere(hits: dict, match_threshold: float = 85.0) -> bool:
    return any((v is not None and v >= match_threshold) for v in hits.values())
