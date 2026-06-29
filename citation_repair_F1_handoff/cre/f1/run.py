"""Phase 1b + 1i orchestration -- run the F1 pipeline over a PMC slice.

Cheap path first (parse -> PMID lookup -> compare), then expensive path only on
flagged survivors (LLM filter -> multi-DB confirm -> decide). Writes two JSONL
outputs: the dataset records and the full per-reference logs.

Wire the Anthropic SDK into `make_completer` and pass your keys via env/config.
This sandbox can't reach NCBI/Crossref, so run it in Colab.
"""
from __future__ import annotations
import os
import time
import requests
from typing import Callable, Iterable

from .schema import (Reference, write_jsonl, UNVERIFIABLE, UNSCOREABLE,
                     V_FORMATTING, V_UNCERTAIN)
from .parser import iter_pmc_dir
from .lookup import fetch_pubmed, compare_and_flag
from .llm_filter import llm_filter
from .confirm import confirm
from .decide import decide
from .ratelimit import configure_ncbi
from . import eval_report

# Anthropic API errors worth retrying (transient); everything else fails fast.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


def _extract_text(msg) -> str:
    """Concatenate the text blocks of a Messages response. Empty string for a
    refusal or an otherwise text-free response (no crash)."""
    parts = []
    for b in getattr(msg, "content", None) or []:
        if getattr(b, "type", None) == "text":
            parts.append(getattr(b, "text", "") or "")
    return "".join(parts)


def _is_retryable(exc) -> bool:
    try:
        import anthropic
    except ImportError:                       # pragma: no cover
        return False
    conn = tuple(t for t in (getattr(anthropic, "APIConnectionError", None),
                             getattr(anthropic, "APITimeoutError", None)) if t)
    if conn and isinstance(exc, conn):
        return True
    return getattr(exc, "status_code", None) in _RETRYABLE_STATUS


def make_completer(model: str, api_key: str = "", *, max_tokens: int = 400,
                   max_retries: int = 4, base_backoff: float = 1.0,
                   max_backoff: float = 30.0) -> Callable[[str], str]:
    """Return a complete(prompt)->str backed by the Anthropic SDK.

    - Retries transient API errors (429/5xx/overloaded/connection) with backoff;
      after exhausting retries it returns "" so the reference falls to
      'uncertain' -> human_review and the run survives.
    - Re-raises non-retryable errors (auth / bad request) immediately so a
      misconfigured run fails fast instead of silently labelling everything
      uncertain.
    - Empty / refusal responses yield "" (parse_verdict -> uncertain), no crash.
    """
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    def complete(prompt: str) -> str:
        for attempt in range(max_retries + 1):
            try:
                msg = client.messages.create(
                    model=model, max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}])
            except Exception as exc:          # noqa: BLE001 - classify below
                if _is_retryable(exc) and attempt < max_retries:
                    time.sleep(min(base_backoff * (2 ** attempt), max_backoff))
                    continue
                if _is_retryable(exc):        # retries exhausted -> skip this ref
                    return ""
                raise                          # non-retryable -> fail fast
            return _extract_text(msg)
        return ""
    return complete


def process_reference(ref: Reference, complete, *, ncbi_key="",
                      crossref_mailto="", openalex_mailto="",
                      sim_threshold=85.0, match_threshold=85.0,
                      author_tripwire=True, session=None) -> Reference:
    # cheap path. With a claimed PMID: EFetch + metadata compare. Without one:
    # compare_and_flag runs the structured no-ID bibliographic lookup itself.
    if ref.claimed.claimed_pmid:
        ref.retrieved = fetch_pubmed(ref.claimed.claimed_pmid, ncbi_key,
                                     session=session)
    flagged = compare_and_flag(ref, sim_threshold,
                               author_tripwire=author_tripwire, session=session)

    # Not flagged -> cleared / unverifiable (both the PMID and no-ID paths).
    if not flagged:
        return decide(ref, flagged, None, None, match_threshold)

    # expensive path (flagged survivors only -- PMID candidates and no-ID
    # references whose cheap lookup found a poor match or nothing)
    verdict = llm_filter(ref, complete)
    if verdict in (V_FORMATTING, V_UNCERTAIN):
        return decide(ref, flagged, verdict, None, match_threshold)

    hits = confirm(ref, ncbi_key, crossref_mailto, openalex_mailto,
                   match_threshold, s=session or requests)
    return decide(ref, flagged, verdict, hits, match_threshold)


def run(pmc_dir: str, out_dataset: str, out_logs: str, *,
        model: str, anthropic_key="", ncbi_key="",
        crossref_mailto="", openalex_mailto="",
        sim_threshold=85.0, match_threshold=85.0, author_tripwire=True,
        refs: Iterable[Reference] | None = None) -> dict:
    complete = make_completer(model, anthropic_key)
    configure_ncbi(bool(ncbi_key))            # bump NCBI rate when a key is present
    session = requests.Session()
    stream = refs if refs is not None else iter_pmc_dir(pmc_dir)

    prediction_records, log_records = [], []
    counts: dict[str, int] = {}
    for ref in stream:
        process_reference(ref, complete, ncbi_key=ncbi_key,
                          crossref_mailto=crossref_mailto,
                          openalex_mailto=openalex_mailto,
                          sim_threshold=sim_threshold,
                          match_threshold=match_threshold,
                          author_tripwire=author_tripwire, session=session)
        counts[ref.label] = counts.get(ref.label, 0) + 1
        log_records.append(ref.to_log_record())
        # unverifiable AND unscoreable refs are dropped from the prediction set
        # (no taxonomy label); unscoreable is still counted + reported below.
        if ref.label not in (UNVERIFIABLE, UNSCOREABLE):
            prediction_records.append(ref.to_prediction().to_dict())

    write_jsonl(prediction_records, out_dataset)
    write_jsonl(log_records, out_logs)
    # F2 measurement layer: UNSCOREABLE buckets, evidence bands, base rate.
    # Read-only; precision-vs-human is computed separately once adjudications
    # exist (eval_report.summarize(log_records, gold=...)).
    try:
        print(eval_report.format_report(eval_report.summarize(log_records)))
    except Exception as e:                            # noqa: BLE001 - reporting must never break a run
        print(f"[eval-report-skip] {e}")
    return counts
