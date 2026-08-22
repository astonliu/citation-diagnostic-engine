"""Exact DOI existence lookup for the no-PMID F1/F2 scope.

The written DOI is treated as an identifier, never as a fuzzy search seed.  The
only normalization removes presentation wrappers/case/trailing citation
punctuation; no character is inserted, deleted, or substituted.

The DOI Foundation resolver, Crossref, DataCite, and OpenAlex are queried
concurrently.  A positive exact record proves existence.  A negative is usable
by F1 only when every provider answered and none carried the DOI.  Any provider
outage leaves the negative incomplete, so it can never become fabrication
evidence.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import requests

from .biblio_match import _coerce_year, _crossref_record, _openalex_record
from .ratelimit import CROSSREF, DATACITE, OPENALEX, request_with_retry
from .schema import RetrievedRecord
from .work_identity import _norm_doi, doi_equivalent

CROSSREF_WORK = "https://api.crossref.org/works"
DATACITE_DOI = "https://api.datacite.org/dois"
OPENALEX_WORKS = "https://api.openalex.org/works"
DOI_HANDLE_API = "https://doi.org/api/handles"

DOI_FOUND = "found"
DOI_FOUND_NO_METADATA = "found_no_metadata"
DOI_ANSWERED_ABSENT = "answered_absent"
DOI_INCOMPLETE = "incomplete"
DOI_CONFLICT = "conflict"
DOI_NOT_ATTEMPTED = "not_attempted"

PROVIDER_FOUND = "found"
PROVIDER_ABSENT = "absent"
PROVIDER_ERROR = "error"
PROVIDER_CONFLICT = "conflict"
_REQUESTS_SESSION_TYPE = requests.sessions.Session


@dataclass(frozen=True)
class ExactDoiResult:
    normalized_doi: str
    status: str
    record: Optional[RetrievedRecord] = None
    source: str = ""
    providers: dict[str, str] = field(default_factory=dict)


def _parallel_transport(s):
    """Avoid sharing a live ``requests.Session`` across worker threads.

    The requests module creates an independent connection for each call.  Test
    transports remain injectable and are intentionally passed through.
    """
    return requests if isinstance(s, _REQUESTS_SESSION_TYPE) else s


def _json(resp):
    if resp is None or resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _crossref(doi: str, s) -> tuple[str, Optional[RetrievedRecord]]:
    try:
        resp = request_with_retry(
            s, f"{CROSSREF_WORK}/{quote(doi, safe='')}", {},
            limiter=CROSSREF, timeout=20)
    except requests.RequestException:
        return PROVIDER_ERROR, None
    if resp.status_code == 404:
        return PROVIDER_ABSENT, None
    data = _json(resp)
    if data is None or not isinstance(data.get("message"), dict):
        return PROVIDER_ERROR, None
    rec = _crossref_record(data["message"])
    rec.resolved = True
    rec.doi = rec.doi or doi
    if not doi_equivalent(doi, rec.doi):
        return PROVIDER_CONFLICT, None
    return PROVIDER_FOUND, rec


def _doi_proxy(doi: str, s) -> tuple[str, Optional[RetrievedRecord]]:
    """Authoritative DOI Handle existence, independent of registration agency."""
    try:
        resp = request_with_retry(
            s, f"{DOI_HANDLE_API}/{quote(doi, safe='')}", {}, timeout=20)
    except requests.RequestException:
        return PROVIDER_ERROR, None
    data = _json(resp)
    if data is None:
        return PROVIDER_ERROR, None
    code = data.get("responseCode")
    if code == 1:
        return PROVIDER_FOUND, None
    if code == 100:
        return PROVIDER_ABSENT, None
    return PROVIDER_ERROR, None


def _datacite_record(attrs: dict) -> RetrievedRecord:
    titles = attrs.get("titles") or []
    title = " ".join(
        item.get("title", "") for item in titles
        if isinstance(item, dict) and item.get("title")).strip()
    authors = []
    for creator in attrs.get("creators") or []:
        if not isinstance(creator, dict):
            continue
        name = creator.get("familyName") or creator.get("name") or ""
        if name:
            authors.append(name)
    journal = ((attrs.get("container") or {}).get("title") or "")
    return RetrievedRecord(
        resolved=True, title=title, authors=authors,
        year=_coerce_year(attrs.get("publicationYear")), journal=journal,
        doi=(attrs.get("doi") or "").lower())


def _datacite(doi: str, s) -> tuple[str, Optional[RetrievedRecord]]:
    try:
        resp = request_with_retry(
            s, f"{DATACITE_DOI}/{quote(doi, safe='')}", {},
            limiter=DATACITE, timeout=20)
    except requests.RequestException:
        return PROVIDER_ERROR, None
    if resp.status_code == 404:
        return PROVIDER_ABSENT, None
    data = _json(resp)
    payload = data.get("data") if data else None
    if not isinstance(payload, dict):
        return PROVIDER_ERROR, None
    rec = _datacite_record(payload.get("attributes") or {})
    rec.resolved = True
    rec.doi = rec.doi or doi
    if not doi_equivalent(doi, rec.doi):
        return PROVIDER_CONFLICT, None
    return PROVIDER_FOUND, rec


def _openalex(doi: str, s) -> tuple[str, Optional[RetrievedRecord]]:
    try:
        resp = request_with_retry(
            s, OPENALEX_WORKS, {"filter": f"doi:{doi}", "per-page": 1},
            limiter=OPENALEX, timeout=20)
    except requests.RequestException:
        return PROVIDER_ERROR, None
    data = _json(resp)
    if data is None:
        return PROVIDER_ERROR, None
    results = data.get("results")
    if not isinstance(results, list):
        return PROVIDER_ERROR, None
    if not results:
        return PROVIDER_ABSENT, None
    for item in results:
        if not isinstance(item, dict):
            continue
        rec = _openalex_record(item)
        if doi_equivalent(doi, rec.doi):
            rec.resolved = True
            return PROVIDER_FOUND, rec
    return PROVIDER_CONFLICT, None


#: The longest inverted index we will reconstruct. An abstract is a paragraph;
#: anything past this is a malformed record or a full text pasted into the field,
#: and reconstructing it would put an unbounded string into every evidence dict.
OPENALEX_ABSTRACT_MAX_TOKENS = 20000

#: The abstract's provenance value, written into ``evidence["cited_abstract_source"]``.
#: An OpenAlex abstract is a third-party RECONSTRUCTION of publisher metadata, not
#: a PubMed abstract, and the two must never be summed in a report as one thing.
ABSTRACT_SOURCE_OPENALEX = "openalex_doi"
ABSTRACT_SOURCE_PUBMED = "pubmed"


def reconstruct_inverted_abstract(index) -> str:
    """Rebuild an abstract from OpenAlex's ``abstract_inverted_index``.

    The field maps each token to the list of positions it occupies. Sorting every
    (position, token) pair and joining by ascending position is the whole of the
    transform.

    ALL OR NOTHING. A malformed index -- a non-list position list, a non-integer
    position, a token that is not a string -- returns ``""`` rather than a partial
    reconstruction. A half-rebuilt abstract is worse than none: it reads as
    ordinary prose, so a judge would weigh it as evidence and a reader would never
    know a gap had been silently dropped out of the middle of it.
    """
    if not isinstance(index, dict) or not index:
        return ""
    pairs: list = []
    for token, positions in index.items():
        if not isinstance(token, str) or not isinstance(positions, (list, tuple)):
            return ""
        for position in positions:
            # bool is an int subclass and would sort as 0/1, silently reordering
            # the sentence; excluded explicitly rather than by luck.
            if not isinstance(position, int) or isinstance(position, bool):
                return ""
            pairs.append((position, token))
            if len(pairs) > OPENALEX_ABSTRACT_MAX_TOKENS:
                return ""
    if not pairs:
        return ""
    pairs.sort(key=lambda pair: pair[0])
    return " ".join(token for _position, token in pairs).strip()


def fetch_openalex_abstract(doi: str, *, s=requests, mailto: str = "") -> str:
    """The cited work's abstract from OpenAlex, by DOI. ``""`` when there is none.

    WHY THIS EXISTS. 80 of the natural run's 562 references were Band-1 cleared
    with no PMID -- IEEE proceedings and non-indexed regional journals that have no
    PubMed record and never will. Crossref does not carry abstracts for them
    (``10.1109/icra.2016.7487344`` has title, container-title and type, and no
    ``abstract`` field). OpenAlex does, as an inverted index.

    ``mailto`` joins OpenAlex's polite pool. It is not decoration: the anonymous
    pool 429s under load, and a rate-limited fetch here reads downstream as "this
    paper has no abstract", which is a wrong terminal answer produced by a
    throttle.

    Returns ``""`` on absence, on any transport or decode failure, and on a
    malformed index. The caller treats an empty result as "no abstract" and
    terminates the reference UNJUDGEABLE -- never as a licence to guess.
    """
    doi = _norm_doi(doi)
    if not doi:
        return ""
    params = {"filter": f"doi:{doi}", "per-page": 1,
              "select": "id,doi,abstract_inverted_index"}
    if mailto:
        params["mailto"] = mailto
    try:
        resp = request_with_retry(s, OPENALEX_WORKS, params,
                                  limiter=OPENALEX, timeout=20)
    except requests.RequestException:
        return ""
    data = _json(resp)
    if data is None:
        return ""
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return ""
    item = results[0]
    if not isinstance(item, dict):
        return ""
    # The DOI must match. OpenAlex's filter is authoritative, but a record for a
    # DIFFERENT work would put another paper's abstract under this citation.
    if not doi_equivalent(doi, item.get("doi") or ""):
        return ""
    return reconstruct_inverted_abstract(item.get("abstract_inverted_index"))


def lookup_exact_doi(value: str, *, s=requests) -> ExactDoiResult:
    """Check one mechanically-normalized DOI against all configured providers."""
    doi = _norm_doi(value)
    if not doi:
        return ExactDoiResult(doi, DOI_NOT_ATTEMPTED)

    transport = _parallel_transport(s)
    functions = {
        "doi_proxy": _doi_proxy,
        "crossref": _crossref,
        "datacite": _datacite,
        "openalex": _openalex,
    }
    with ThreadPoolExecutor(max_workers=len(functions)) as pool:
        futures = {name: pool.submit(fn, doi, transport)
                   for name, fn in functions.items()}
        # Consume in fixed provider order so records and provenance are stable.
        answers = {name: futures[name].result() for name in functions}

    statuses = {name: answer[0] for name, answer in answers.items()}
    metadata_found = any(
        statuses[name] == PROVIDER_FOUND
        for name in ("crossref", "datacite", "openalex"))
    if (any(status == PROVIDER_CONFLICT for status in statuses.values())
            or (statuses["doi_proxy"] == PROVIDER_ABSENT and metadata_found)):
        return ExactDoiResult(doi, DOI_CONFLICT, providers=statuses)

    # Deterministic authority preference affects only which metadata record feeds
    # F2; every exact positive proves DOI existence.
    for name in ("crossref", "datacite", "openalex"):
        status, rec = answers[name]
        if status == PROVIDER_FOUND and rec is not None:
            return ExactDoiResult(doi, DOI_FOUND, rec, name, statuses)

    if statuses["doi_proxy"] == PROVIDER_FOUND:
        return ExactDoiResult(doi, DOI_FOUND_NO_METADATA, providers=statuses)

    if all(status == PROVIDER_ABSENT for status in statuses.values()):
        return ExactDoiResult(doi, DOI_ANSWERED_ABSENT, providers=statuses)
    return ExactDoiResult(doi, DOI_INCOMPLETE, providers=statuses)
