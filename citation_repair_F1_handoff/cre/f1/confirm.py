"""Phase 1g -- multi-database confirmation.

Searches for the CLAIMED title + first author (NOT the claimed PMID) across
PubMed, Crossref, and OpenAlex. The question is: does the claimed work exist
ANYWHERE? Google Scholar is intentionally omitted (no API, not reproducible).

Returns a dict {db: best_score|None}. A db "finds" the work if its best title
match clears `match_threshold`.

0.0 AND None ARE NOT INTERCHANGEABLE, and this is the module's central rule:

  * ``0.0`` means SEARCHED, ANSWERED, FOUND NOTHING. It is evidence.
  * ``None`` means NO ANSWER -- the search errored, returned a fault envelope
    under an HTTP 200, or was never issued at all (no claimed title). It is not
    evidence of anything, in either direction.

decide.py requires EVERY search to have answered before F1 is reachable (see
``fully_answered``): an accusation that a work exists nowhere has to be backed
by having actually looked everywhere.

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


#: Keys that mean "this 200 is an error report, not a result set". Entrez,
#: Crossref and OpenAlex all serve faults with HTTP 200 and say so only in the
#: body; taking such a body at face value yielded an empty item list, which
#: scored 0.0, which decide() read as "searched, found nothing" -- fabrication
#: evidence manufactured out of an upstream fault.
#: NOTE: ``errorlist`` is deliberately absent. Entrez nests a benign
#: ``errorlist`` (phrasesnotfound / fieldsnotfound) inside ``esearchresult`` on
#: perfectly successful zero-hit searches; treating it as a fault would turn
#: every honest "found nothing" into "could not look", which is the mirror of
#: the defect being fixed.
_ERROR_KEYS = ("error", "ERROR", "error-message")


def _json_or_none(resp):
    """Return parsed JSON for a healthy 200 response, else None (treated as an
    errored search, not 'found nothing').

    None is returned for a non-200, an unparseable body, a body that is not a
    JSON object, an object carrying an error key, and a Crossref-style
    ``{"status": "error"}`` envelope. A search that could not be answered must
    never be scoreable.
    """
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if any(data.get(k) for k in _ERROR_KEYS):
        return None
    status = data.get("status")
    if isinstance(status, str) and status.lower() not in ("ok", "success"):
        return None
    return data


def search_pubmed(title: str, api_key: str = "", s=requests) -> float | None:
    # No title -> no request is issued, so there is NO result to report. Scoring
    # a search that never happened as 0.0 fabricated evidence of non-existence.
    if not title:
        return None
    try:
        esearch = _json_or_none(request_with_retry(s, PUBMED_ESEARCH, {
            "db": "pubmed", "term": title, "field": "title", "retmode": "json",
            "retmax": 3, **({"api_key": api_key} if api_key else {})},
            limiter=NCBI, timeout=20))
        if esearch is None:
            return None
        # Entrez reports faults INSIDE esearchresult with an outer HTTP 200; an
        # absent/!dict esearchresult is an unexpected shape, not an empty index.
        result = esearch.get("esearchresult")
        if not isinstance(result, dict) or result.get("ERROR") or result.get("error"):
            return None
        ids = result.get("idlist")
        if not isinstance(ids, list):
            return None
        if not ids:
            return 0.0                    # searched, answered, found nothing
        summary = _json_or_none(request_with_retry(s, PUBMED_ESUMMARY, {
            "db": "pubmed", "id": ",".join(ids), "retmode": "json",
            **({"api_key": api_key} if api_key else {})},
            limiter=NCBI, timeout=20))
        if summary is None:
            return None
        res = summary.get("result")
        if not isinstance(res, dict):
            return None
        usable = [res[i].get("title") for i in ids
                  if isinstance(res.get(i), dict)
                  and isinstance(res[i].get("title"), str)
                  and res[i].get("title").strip()]
        return max((_score(title, value) for value in usable), default=None)
    except (requests.RequestException, ValueError, KeyError,
            AttributeError, TypeError):
        return None


def search_crossref(title: str, mailto: str = "", s=requests) -> float | None:
    # See search_pubmed: a search that was never issued has no score.
    if not title:
        return None
    try:
        data = _json_or_none(request_with_retry(s, CROSSREF_URL, {
            "query.bibliographic": title, "rows": 3,
            **({"mailto": mailto} if mailto else {})},
            limiter=CROSSREF, timeout=20))
        if data is None:
            return None
        # ``message`` is an object on a healthy response. A 200 whose message is
        # a STRING is a fault envelope -- it used to raise AttributeError out of
        # .get() and kill the entire batch (nothing caught it here or in run.py).
        message = data.get("message")
        if not isinstance(message, dict):
            return None
        items = message.get("items")
        if not isinstance(items, list):
            return None
        # Crossref title is a LIST of strings (often one element, sometimes more).
        usable = [" ".join(v for v in it.get("title") if isinstance(v, str))
                  for it in items
                  if isinstance(it, dict) and isinstance(it.get("title"), list)
                  and any(isinstance(v, str) and v.strip()
                          for v in it.get("title"))]
        return (0.0 if not items else
                max((_score(title, value) for value in usable), default=None))
    except (requests.RequestException, ValueError, KeyError,
            AttributeError, TypeError):
        return None


def search_openalex(title: str, mailto: str = "", s=requests) -> float | None:
    # See search_pubmed: a search that was never issued has no score.
    if not title:
        return None
    try:
        params = {"filter": f"title.search:{title}", "per-page": 3}
        if mailto:
            params["mailto"] = mailto
        data = _json_or_none(request_with_retry(s, OPENALEX_URL, params,
                                                limiter=OPENALEX, timeout=20))
        if data is None:
            return None
        items = data.get("results")
        if not isinstance(items, list):
            return None
        # OpenAlex title may be null; fall back to display_name, then "".
        usable = [it.get("title") or it.get("display_name") for it in items
                  if isinstance(it, dict)
                  and isinstance(it.get("title") or it.get("display_name"), str)
                  and (it.get("title") or it.get("display_name")).strip()]
        return (0.0 if not items else
                max((_score(title, value) for value in usable), default=None))
    except (requests.RequestException, ValueError, KeyError,
            AttributeError, TypeError):
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


def unanswered(hits: dict) -> list[str]:
    """Names of the searches that did not answer (None), sorted.

    None covers both "errored" and "never issued" -- neither produced a result,
    and the distinction does not matter to a caller deciding whether it has
    enough evidence to accuse.
    """
    return sorted(k for k, v in hits.items() if v is None)


def fully_answered(hits: dict) -> bool:
    """True when EVERY confirmation search returned a real score.

    THE EVIDENCE BAR FOR F1 (ZD, 2026-08-16). An accusation of fabrication
    asserts that the work is in no database; that assertion requires every
    database to have actually been consulted. One healthy-but-empty search
    alongside an errored one used to license F1, so a single-provider outage
    could carry a real, indexed paper to a public accusation.

    Note this is strictly stronger than ``not all_errored(hits)`` -- it is the
    difference between "we looked somewhere" and "we looked everywhere".
    """
    return bool(hits) and all(v is not None for v in hits.values())


def found_anywhere(hits: dict, match_threshold: float = 85.0) -> bool:
    return any((v is not None and v >= match_threshold) for v in hits.values())
