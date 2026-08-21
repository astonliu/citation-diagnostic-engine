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
                     HUMAN_REVIEW, F1, V_FORMATTING, V_UNCERTAIN,
                     FETCH_ANSWERED_ABSENT, FETCH_ANSWERED_RECORD,
                     FETCH_RESOLVER_ERROR, fetch_answered)
from .parser import iter_pmc_dir
from .lookup import fetch_pubmed, compare_and_flag
from .llm_filter import llm_filter
from .confirm import confirm
from .decide import decide
from .ncbi_meta import ncbi_pubtypes, is_retracted
from .ratelimit import configure_ncbi
from .doi_lookup import DOI_FOUND
from .f8_retraction import (F8_CLEAR, F8_NOT_APPLICABLE, F8_QUALIFIED,
                            F8_TIMING_VERSION, assess_f8_timing)
from . import eval_report

# Anthropic API errors worth retrying (transient); everything else fails fast.
_RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class NonRetryableProviderError(RuntimeError):
    """A permanent provider/configuration failure that must abort the run."""


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
                raise NonRetryableProviderError(str(exc)) from exc
            return _extract_text(msg)
        return ""
    complete.model_id = model
    complete.model_settings = {"max_tokens": max_tokens}
    return complete


def retraction_state(ref: Reference, *, ncbi_key: str = "", session=None,
                     cache: dict | None = None) -> "bool | None":
    """The F8 retraction TRI-STATE for a reference's resolved record.

    True = the resolved PMID's PubMed publication types include
    ``Retracted Publication``; False = the types were fetched and it is absent;
    ``None`` = UNKNOWN (no resolved PMID to look up, or the EFetch failed).

    The lookup lives here, in the existence layer, and NOT in ``decide`` -- that
    module is a pure function over accumulated evidence and must stay one.

    A network failure must never read as "not retracted", so ``ncbi_pubtypes``
    returning None yields None. An EMPTY type list is also unknown: every live
    MEDLINE record carries at least one ``PT`` line, so an empty parse is a
    failure to read the field, not a record that has none.

    ``cache`` (PMID -> state), when given, is read and written for KNOWN states
    only. The unknown state is deliberately NOT cached: caching it would freeze a
    transient outage into every later reference to the same PMID in the run.
    """
    if not ref.retrieved.resolved:
        return None
    pmid = (ref.retrieved.pmid or ref.claimed.claimed_pmid or "").strip()
    if not pmid:
        return None
    if cache is not None and pmid in cache:
        return cache[pmid]
    pubtypes = ncbi_pubtypes(pmid, ncbi_key, session=session)
    if not pubtypes:                     # None (failure) or [] (nothing parsed)
        return None
    state = is_retracted(pubtypes)
    if cache is not None:
        cache[pmid] = state
    return state


def process_reference(ref: Reference, complete, *, ncbi_key="",
                      crossref_mailto="", openalex_mailto="",
                      sim_threshold=85.0, match_threshold=85.0,
                      author_tripwire=True, session=None,
                      retraction_cache: dict | None = None,
                      f8_fetch_meta=None) -> Reference:
    # cheap path. With a claimed PMID: EFetch + metadata compare. Without one:
    # compare_and_flag runs the structured no-ID bibliographic lookup itself.
    if ref.claimed.claimed_pmid:
        ref.retrieved = fetch_pubmed(ref.claimed.claimed_pmid, ncbi_key,
                                     session=session)
    # F8 existence gate (§4.3): record the retraction tri-state BEFORE the
    # comparison runs, so it is on the log for every resolved reference no matter
    # which branch decide() ends up taking. The no-ID path never reaches here with
    # a PMID (fuzzy_biblio_lookup's record always has ``.pmid == ""``), so it
    # honestly records "unknown" rather than a lookup it could not perform.
    if f8_fetch_meta is not None:
        cited_pmid = (ref.retrieved.pmid or ref.claimed.claimed_pmid or "").strip()
        assessment = assess_f8_timing(
            cited_pmid, ref.source_pmid, fetch_meta=f8_fetch_meta)
        ref.log.f8_timing_status = assessment.status
        ref.log.f8_notice_date = assessment.notice_date
        ref.log.f8_citing_date_earliest = assessment.citing_date_earliest
        ref.log.f8_timing_gap_days = assessment.timing_gap_days
        ref.log.f8_timing_version = F8_TIMING_VERSION
        ref.log.retracted = (True if assessment.status == F8_QUALIFIED else
                             False if assessment.status in {
                                 F8_CLEAR, F8_NOT_APPLICABLE} else None)
    else:
        ref.log.retracted = retraction_state(
            ref, ncbi_key=ncbi_key, session=session, cache=retraction_cache)
    flagged = compare_and_flag(ref, sim_threshold,
                               author_tripwire=author_tripwire, session=session)

    # Not flagged -> cleared / unverifiable (both the PMID and no-ID paths).
    if not flagged:
        return decide(ref, flagged, None, None, match_threshold)

    # A retracted source is decided deterministically in the existence layer
    # (§4.3), so do not pay for an LLM call and three confirmation searches on a
    # row whose label they cannot change: decide()'s F8 branch precedes every use
    # of llm_verdict and db_hits, and decide() enforces the UNSCOREABLE-first
    # precedence itself. Mirrors the same-work short-circuit below.
    if ref.log.retracted is True:
        return decide(ref, flagged, None, None, match_threshold)

    # Identity-proven variants are audited, not accused.  Avoid paying for an
    # LLM and three confirmation searches when the deterministic layer has
    # already selected the dedicated human-review quarantine.
    if ref.log.same_work_reason:
        return decide(ref, flagged, None, None, match_threshold)

    # The claimed-PMID fetch never answered -> decide() will hold this row no
    # matter what the LLM and the searches say. Short-circuit for the same
    # reason as the branch above: do not buy evidence for a decision that has
    # already been made, and during an NCBI outage this is the hot path.
    if ref.log.pmid_present and not fetch_answered(ref.log.pmid_transport_status):
        return decide(ref, flagged, None, None, match_threshold)

    # No-PMID references carrying a printed DOI are deterministic identity
    # cases.  An exact positive feeds the existing metadata matcher and can
    # produce F2; an exact negative must be paired with the independent title
    # sweep before F1 is reachable.  Neither case needs an LLM opinion about
    # whether the identifier exists.
    if not ref.log.pmid_present and ref.claimed.claimed_doi:
        hits = None
        if ref.log.doi_lookup_status != DOI_FOUND:
            hits = confirm(ref, ncbi_key, crossref_mailto, openalex_mailto,
                           match_threshold, s=session or requests)
        return decide(ref, flagged, None, hits, match_threshold)

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
        refs: Iterable[Reference] | None = None,
        complete: Callable[[str], str] | None = None,
        f8_timing: bool = False, f8_fetch_meta=None) -> dict:
    """Run Band 1 over the exact corpus.

    ``complete`` is an injectable production seam so the full-system launcher
    can bind Band-1 model calls to the same adapter receipt as Band 2.  Direct
    callers retain the historical behaviour: when it is omitted, this function
    constructs the Anthropic transport itself.
    """
    complete = complete or make_completer(model, anthropic_key)
    configure_ncbi(bool(ncbi_key))            # bump NCBI rate when a key is present
    session = requests.Session()
    if f8_timing and f8_fetch_meta is None:
        from .f5_candidate_finder import PubMedCandidateFinder
        from .ncbi_meta import DEFAULT_EMAIL
        finder = PubMedCandidateFinder(
            api_key=ncbi_key, email=openalex_mailto or DEFAULT_EMAIL,
            session=session)
        f8_memory: dict[str, dict | None] = {}

        def f8_fetch_meta(work_id):
            key = str(work_id or "").strip()
            if key not in f8_memory:
                f8_memory[key] = finder.fetch_metadata(key)
            value = f8_memory[key]
            return dict(value) if isinstance(value, dict) else None
    stream = refs if refs is not None else iter_pmc_dir(pmc_dir)

    prediction_records, log_records = [], []
    # Seeded so a run that produced no F1 reports a ZERO rather than a missing
    # key -- see f1_status below. Other labels keep the observed-only behavior.
    counts: dict[str, int] = {F1: 0}
    quarantined = 0
    # PMID -> retraction state, shared across the run so a PMID cited by many
    # references costs one EFetch. Known states only (see ``retraction_state``).
    retraction_cache: dict = {}
    for ref in stream:
        try:
            process_reference(ref, complete, ncbi_key=ncbi_key,
                              crossref_mailto=crossref_mailto,
                              openalex_mailto=openalex_mailto,
                              sim_threshold=sim_threshold,
                              match_threshold=match_threshold,
                              author_tripwire=author_tripwire, session=session,
                              retraction_cache=retraction_cache,
                              f8_fetch_meta=f8_fetch_meta if f8_timing else None)
        except NonRetryableProviderError:
            raise
        except Exception as e:                # noqa: BLE001 - quarantine, never abort
            # ONE BAD ROW MUST NOT KILL THE RUN. A Crossref 200 whose `message`
            # is a string raised AttributeError out of confirm() and took the
            # whole batch with it. Same pattern as the strict-parser quarantine
            # in judgment_run.py: name the row, hold it, keep going.
            #
            # HUMAN_REVIEW, deliberately: a reference we failed to process is
            # unjudged, and unjudged must never be reported as a finding.
            quarantined += 1
            ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
            ref.rationale = ("Processing raised an unexpected error; the "
                             "reference was quarantined unjudged.")
            ref.log.decided_by = "quarantine_exception"
            ref.log.notes = f"{type(e).__name__}: {e}"
            print(f"[f1-run-quarantine] {ref.citation_id}: "
                  f"{type(e).__name__}: {e}")
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
    print(eval_report.format_report(eval_report.summarize(log_records)))
    return counts
