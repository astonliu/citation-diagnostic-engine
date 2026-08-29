"""Per-leg accounting for every OpenAlex call the engine makes.

WHY THIS EXISTS. OpenAlex metered its API in 2026: an anonymous caller gets
$0.10 of usage per day and then 409s, a free key gets $1. The engine spends that
allowance from four independent places, each with a different volume and a
different unit price, and none of them announced itself. The failure mode this
produced is the reason the module is here: on 2026-08-24 two ``provider_probe``
records from the SAME run, same probe title, reported ``openalex: 100.0`` and
then ``openalex: None`` while PubMed and Crossref returned byte-identical
scores. F1 went structurally unreachable mid-run and the run's own artifacts
said nothing about it -- the allowance had to be INFERRED from a dead run.

So each leg reports the HTTP status of every call it makes, tallied by leg:

  * ``confirm``    -- confirm.search_openalex, the F1 evidence gate. Fires on
    flagged survivors only; ``filter=`` tier ($0.0001/call).
  * ``candidates`` -- biblio_match._openalex_candidates, F2 candidate retrieval.
    Fires on every PMID-less reference; ``search=`` tier ($0.001/call), so this
    is the leg that empties the allowance.
  * ``doi``        -- doi_lookup._openalex, the exact-DOI provider set.
  * ``abstract``   -- doi_lookup.fetch_openalex_abstract, the Band-2 seam.

Counters are process-cumulative and never reset by production code. Callers
report a WINDOW by taking a :func:`snapshot` before the work and a
:func:`delta` after it, which is exact whether or not anything else in the
process is also calling OpenAlex. ``reset()`` exists for tests only.

This module records status codes. It never records the API key, and no counter
value can be used to reconstruct one.
"""
from __future__ import annotations
import threading

import requests

from .ratelimit import request_with_retry

LEG_CONFIRM = "confirm"
LEG_CANDIDATES = "candidates"
LEG_DOI = "doi"
LEG_ABSTRACT = "abstract"

#: Fixed leg order, so a manifest diff between two runs lines up.
LEGS = (LEG_CONFIRM, LEG_CANDIDATES, LEG_DOI, LEG_ABSTRACT)

#: The status label for a call that never reached a status line. Distinct from
#: any HTTP code for the same reason 0.0 and None are distinct in confirm.py: a
#: request that did not complete is not a request that was answered.
TRANSPORT_ERROR = "transport_error"
#: request_with_retry can return None if it exhausts its loop without a response.
NO_RESPONSE = "no_response"

#: OpenAlex's spent-allowance status. Named here because it is the one code
#: whose tally answers "did this run run out of money", and because it is
#: deliberately ABSENT from ratelimit._RETRY_STATUS -- a spent daily quota is
#: not transient, and retrying it only burns the run faster.
QUOTA_EXHAUSTED = "409"

_lock = threading.Lock()
_counts: dict[str, dict[str, int]] = {leg: {} for leg in LEGS}


def record(leg: str, resp=None, *, error: bool = False) -> None:
    """Tally one OpenAlex call for ``leg`` under its HTTP status label."""
    if error:
        label = TRANSPORT_ERROR
    elif resp is None:
        label = NO_RESPONSE
    else:
        label = str(getattr(resp, "status_code", NO_RESPONSE))
    with _lock:
        bucket = _counts.setdefault(leg, {})
        bucket[label] = bucket.get(label, 0) + 1


def request(leg: str, session, url, params, **kwargs):
    """``request_with_retry`` plus exactly one tally for ``leg``.

    The legs call this instead of tallying for themselves so that a call cannot
    be made without being counted. It changes no request bytes: ``url``,
    ``params`` and every retry parameter pass straight through.
    """
    try:
        resp = request_with_retry(session, url, params, **kwargs)
    except requests.RequestException:
        record(leg, error=True)
        raise
    record(leg, resp)
    return resp


def snapshot() -> dict[str, dict[str, int]]:
    """Cumulative per-leg status tallies at this instant."""
    with _lock:
        return {leg: dict(_counts.get(leg, {})) for leg in
                (*LEGS, *(k for k in _counts if k not in LEGS))}


def delta(before: dict, after: dict | None = None) -> dict:
    """The calls made between two snapshots, plus per-leg and run totals.

    ``{"legs": {leg: {status: n}}, "leg_totals": {leg: n}, "total": n,
    "quota_exhausted": n}``. Legs with no calls in the window are still present
    with ``{}`` and ``0`` -- a leg that made no call and a leg that was never
    wired must not look the same in a manifest.
    """
    after = snapshot() if after is None else after
    legs: dict[str, dict[str, int]] = {}
    for leg in {*before, *after, *LEGS}:
        was, now = before.get(leg, {}), after.get(leg, {})
        diff = {status: now.get(status, 0) - was.get(status, 0)
                for status in {*was, *now}}
        legs[leg] = {status: n for status, n in sorted(diff.items()) if n}
    ordered = {leg: legs[leg] for leg in
               (*LEGS, *sorted(k for k in legs if k not in LEGS))}
    leg_totals = {leg: sum(statuses.values())
                  for leg, statuses in ordered.items()}
    return {
        "legs": ordered,
        "leg_totals": leg_totals,
        "total": sum(leg_totals.values()),
        # Hoisted out of `legs` because this is the number a reader is looking
        # for: a nonzero value means the run was answering "not found" with
        # "could not pay" for however long it kept going.
        "quota_exhausted": sum(statuses.get(QUOTA_EXHAUSTED, 0)
                               for statuses in ordered.values()),
    }


def openalex_key_kwarg(api_key: str) -> dict:
    """``{"openalex_api_key": key}`` when a key is configured, else ``{}``.

    HOW THE KEY CROSSES AN INJECTION SEAM. The no-PMID retrieval chain
    (``lookup.retrieve_candidates``, ``lookup.fuzzy_biblio_lookup``) and
    ``compare_and_flag`` are documented injection seams: offline tests, replays
    and the launcher all substitute their own callables. Passing
    ``openalex_api_key=`` unconditionally would break every one of those
    substitutions that predates this parameter, including on runs that
    configured no key at all -- turning a billing parameter into an incompatible
    call signature.

    Splatting this instead means a keyless run makes literally the same calls it
    made before, argument for argument. A run WITH a key does pass it, and a
    stale injected seam then fails loudly with a TypeError -- which is the right
    outcome: a seam that silently swallowed the key would send unauthenticated
    requests and spend the anonymous $0.10/day allowance while the manifest
    recorded an authenticated run.
    """
    return {"openalex_api_key": api_key} if api_key else {}


def reset() -> None:
    """Zero every counter. Tests only; production reports windows via delta()."""
    with _lock:
        _counts.clear()
        _counts.update({leg: {} for leg in LEGS})
