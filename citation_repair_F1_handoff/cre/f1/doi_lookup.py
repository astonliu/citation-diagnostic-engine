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
