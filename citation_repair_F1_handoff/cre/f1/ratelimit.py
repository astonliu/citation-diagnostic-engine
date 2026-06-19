"""Shared HTTP throttling + retry for the live-network stages (lookup, confirm).

Why this lives in one place: NCBI EFetch / ESearch / ESummary all draw on a
single per-IP rate budget (~3 req/s without an API key, ~10 req/s with one), so
they MUST share one limiter or a scaled run gets 429'd. Crossref and OpenAlex
have their own polite-pool budgets and get their own limiters.

`request_with_retry` adds exponential backoff on 429/5xx and transient
connection errors. Backoff parameters are injectable so tests can drive it with
zero real sleeping.
"""
from __future__ import annotations
import threading
import time

import requests

# Statuses worth retrying: rate limit + transient server/gateway errors.
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


class RateLimiter:
    """Token-free fixed-interval limiter: blocks so calls are >= 1/rate apart.

    Thread-safe; a single instance can be shared across threads that all hit the
    same upstream budget.
    """

    def __init__(self, rate_per_sec: float):
        self._lock = threading.Lock()
        self._last = 0.0
        self.set_rate(rate_per_sec)

    def set_rate(self, rate_per_sec: float) -> None:
        self._min_interval = (1.0 / rate_per_sec) if rate_per_sec > 0 else 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._min_interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


# Shared limiters. NCBI defaults to the keyless rate; configure_ncbi() bumps it
# once a run knows whether an API key is in play.
NCBI = RateLimiter(3.0)
CROSSREF = RateLimiter(5.0)
OPENALEX = RateLimiter(5.0)


def configure_ncbi(has_api_key: bool) -> None:
    """Set the shared NCBI rate to the documented ceiling for the auth state."""
    NCBI.set_rate(9.0 if has_api_key else 3.0)


def _retry_after_seconds(resp) -> float | None:
    """Honor a numeric Retry-After header if the server sent one."""
    ra = resp.headers.get("Retry-After") if resp is not None else None
    if not ra:
        return None
    try:
        return max(0.0, float(ra))
    except (TypeError, ValueError):
        return None


def request_with_retry(session, url, params, *, limiter: RateLimiter | None = None,
                       timeout: float = 20, max_retries: int = 3,
                       base_backoff: float = 0.5, max_backoff: float = 8.0):
    """GET with throttle + exponential backoff.

    Retries on 429/5xx and on transient ``requests.RequestException``. Returns
    the final ``Response`` (the caller inspects ``status_code``); re-raises the
    last ``RequestException`` only if every attempt failed to connect.
    """
    s = session or requests
    resp = None
    for attempt in range(max_retries + 1):
        if limiter is not None:
            limiter.wait()
        try:
            resp = s.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            if attempt == max_retries:
                raise
            time.sleep(min(base_backoff * (2 ** attempt), max_backoff))
            continue
        if resp.status_code in _RETRY_STATUS and attempt < max_retries:
            delay = _retry_after_seconds(resp)
            if delay is None:
                delay = min(base_backoff * (2 ** attempt), max_backoff)
            time.sleep(delay)
            continue
        return resp
    return resp
