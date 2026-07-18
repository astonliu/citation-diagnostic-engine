"""Reconcile a conformance-review workflow's run journal against its summary.

Why this exists
---------------
The F5 conformance-review workflow fanned out review agents (one per dimension,
each returning ``{"findings": [...]}``) and verify agents (one per finding, each
returning ``{"verdict": "CONFIRMED"|"REFUTED"|"UNCERTAIN", ...}``), then aggregated
a top-level summary ``{"confirmed_count": N, "confirmed": [...]}``.

On the F5 review the summary came back ``confirmed_count: 0`` even though the run
journal recorded 11 findings -> 9 CONFIRMED + 2 REFUTED. A shape mismatch in the
in-script aggregation (a pipeline stage returning a bare array for dimensions WITH
findings but ``{key, verified: []}`` for dimensions WITHOUT) silently dropped every
verdict. A silent empty summary from a non-empty journal is the exact failure this
module refuses to let pass.

What it does
------------
``reconcile_review_journal(journal, summary=None)`` reads the journal (the ground
truth: what the agents actually returned), tallies findings raised and the verify
verdicts, and enforces fail-loud invariants:

  * INV-A (completeness): raised == confirmed + refuted + unresolved. A shortfall
    (a finding whose verify agent produced no verdict) never passes silently.
  * INV-B (non-empty journal, empty tally): >= 1 finding raised but ZERO verdicts
    recorded -> raise (the journal itself is broken/incomplete).
  * INV-C (summary agrees with journal): when a ``summary`` is supplied, its
    ``confirmed_count`` MUST equal the journal's CONFIRMED tally (and refuted /
    unresolved when present). A summary that disagrees with the journal -- the F5
    ``confirmed: []`` bug -- raises.

It returns the reconciled counts and prints a one-line reconciliation report, so
every run leaves a visible journal-vs-summary reconciliation trail.

Pure stdlib; no project imports; safe to run anywhere.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable, Mapping, Optional, Union

# Verdict vocabulary -> reconciliation bucket. Case-insensitive; UNRESOLVED is an
# accepted alias for UNCERTAIN.
_VERDICT_BUCKET = {
    "CONFIRMED": "confirmed",
    "REFUTED": "refuted",
    "UNCERTAIN": "unresolved",
    "UNRESOLVED": "unresolved",
}


class ReconciliationError(AssertionError):
    """Journal and summary do not reconcile -- raised fail-loud, never swallowed."""


def _unwrap(entry: Any) -> Any:
    """Return the payload of a journal entry, unwrapping the common envelopes.

    A workflow journal line is typically ``{"type": "result", "result": {...}}``;
    some encoders use ``"value"``. A fixture may store the bare payload. Anything
    that is not a mapping is returned as-is (and ignored downstream)."""
    if isinstance(entry, Mapping):
        if entry.get("type") in (None, "result") and isinstance(entry.get("result"), Mapping):
            return entry["result"]
        if isinstance(entry.get("value"), Mapping):
            return entry["value"]
    return entry


def _iter_entries(journal: Union[str, Iterable[Any]]) -> list:
    """Accept a path to a .jsonl journal, a single json string, or an iterable of
    already-decoded entries; return the list of decoded entries."""
    if isinstance(journal, str):
        with open(journal, "r", encoding="utf-8") as fh:
            out = []
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
            return out
    return list(journal)


def classify_journal(journal: Union[str, Iterable[Any]]) -> dict:
    """Tally the ground-truth counts from the journal WITHOUT enforcing anything.

    Returns ``{"raised", "verdicts" (Counter over buckets), "review_entries",
    "verify_entries", "unknown_verdicts" (list of off-vocabulary verdict strings)}``.
    """
    raised = 0
    review_entries = 0
    verify_entries = 0
    verdicts: Counter = Counter()
    unknown_verdicts: list = []
    for raw in _iter_entries(journal):
        payload = _unwrap(raw)
        if not isinstance(payload, Mapping):
            continue
        if "findings" in payload and isinstance(payload["findings"], list):
            review_entries += 1
            raised += len(payload["findings"])
        elif "verdict" in payload:
            verify_entries += 1
            key = str(payload["verdict"]).strip().upper()
            bucket = _VERDICT_BUCKET.get(key)
            if bucket is None:
                unknown_verdicts.append(payload["verdict"])
            else:
                verdicts[bucket] += 1
    return {
        "raised": raised,
        "verdicts": verdicts,
        "review_entries": review_entries,
        "verify_entries": verify_entries,
        "unknown_verdicts": unknown_verdicts,
    }


def reconcile_review_journal(
    journal: Union[str, Iterable[Any]],
    summary: Optional[Mapping] = None,
    *,
    verbose: bool = True,
) -> dict:
    """Reconcile the journal (ground truth) against the aggregated ``summary``.

    Raises ``ReconciliationError`` on any invariant violation (INV-A/B/C above);
    otherwise returns the reconciled counts. Always prints a one-line report
    (unless ``verbose=False``) so every run leaves a reconciliation trail.
    """
    tally = classify_journal(journal)
    raised = tally["raised"]
    v = tally["verdicts"]
    confirmed = v.get("confirmed", 0)
    refuted = v.get("refuted", 0)
    unresolved = v.get("unresolved", 0)
    verified_total = confirmed + refuted + unresolved
    unverified = raised - verified_total

    result = {
        "raised": raised,
        "confirmed": confirmed,
        "refuted": refuted,
        "unresolved": unresolved,
        "verified_total": verified_total,
        "unverified": unverified,
        "review_entries": tally["review_entries"],
        "verify_entries": tally["verify_entries"],
        "summary_confirmed_count": None,
        "reconciled": False,
    }

    report = (
        f"[review-reconcile] raised={raised} "
        f"confirmed={confirmed} refuted={refuted} unresolved={unresolved} "
        f"unverified={unverified} (review_entries={tally['review_entries']}, "
        f"verify_entries={tally['verify_entries']})"
    )

    # An off-vocabulary verdict is a schema drift -> fail loud (never silently
    # bucketed away).
    if tally["unknown_verdicts"]:
        if verbose:
            print(report + f" -> FAIL (unknown verdicts {tally['unknown_verdicts']})")
        raise ReconciliationError(
            f"journal contains off-vocabulary verdict(s): {tally['unknown_verdicts']}; "
            f"expected one of {sorted(set(_VERDICT_BUCKET))}")

    # INV-B: findings raised but zero verdicts recorded at all -> the journal is
    # broken/incomplete; never report a silent empty tally.
    if raised >= 1 and verified_total == 0:
        if verbose:
            print(report + " -> FAIL (INV-B: findings raised but no verdicts)")
        raise ReconciliationError(
            f"journal raised {raised} finding(s) but recorded 0 verdicts: "
            "the summary would be silently empty")

    # INV-A: every raised finding must have exactly one verdict.
    if unverified != 0:
        if verbose:
            print(report + " -> FAIL (INV-A: counts do not reconcile)")
        raise ReconciliationError(
            f"counts do not reconcile: raised={raised} but "
            f"confirmed+refuted+unresolved={verified_total} "
            f"(unverified={unverified}); every finding must carry one verdict")

    # INV-C: a supplied summary must agree with the journal exactly.
    if summary is not None:
        s_confirmed = _summary_confirmed_count(summary)
        result["summary_confirmed_count"] = s_confirmed
        if s_confirmed != confirmed:
            if verbose:
                print(report + f" -> FAIL (INV-C: summary confirmed={s_confirmed} "
                      f"!= journal confirmed={confirmed})")
            raise ReconciliationError(
                f"summary disagrees with journal: summary confirmed_count="
                f"{s_confirmed} but journal recorded {confirmed} CONFIRMED "
                "verdict(s) -- the aggregation dropped verdicts")
        for bucket, s_val in (("refuted", _summary_int(summary, "refuted_count")),
                              ("unresolved", _summary_int(summary, "unresolved_count"))):
            if s_val is not None and s_val != result[bucket]:
                if verbose:
                    print(report + f" -> FAIL (INV-C: summary {bucket}={s_val} "
                          f"!= journal {bucket}={result[bucket]})")
                raise ReconciliationError(
                    f"summary disagrees with journal on {bucket}: "
                    f"summary={s_val} journal={result[bucket]}")

    result["reconciled"] = True
    if verbose:
        print(report + " -> OK")
    return result


def _summary_confirmed_count(summary: Mapping) -> int:
    """Extract the confirmed count from a summary that may carry an explicit
    ``confirmed_count`` and/or a ``confirmed`` list."""
    if "confirmed_count" in summary and summary["confirmed_count"] is not None:
        return int(summary["confirmed_count"])
    confirmed = summary.get("confirmed")
    if isinstance(confirmed, list):
        return len(confirmed)
    raise ReconciliationError(
        "summary carries neither a 'confirmed_count' nor a 'confirmed' list")


def _summary_int(summary: Mapping, key: str) -> Optional[int]:
    value = summary.get(key)
    return int(value) if value is not None else None


if __name__ == "__main__":  # pragma: no cover - CLI convenience
    import sys
    if len(sys.argv) < 2:
        print("usage: workflow_review_reconcile.py <journal.jsonl> [summary.json]")
        raise SystemExit(2)
    summary_arg = None
    if len(sys.argv) >= 3:
        with open(sys.argv[2], "r", encoding="utf-8") as fh:
            summary_arg = json.load(fh)
    reconcile_review_journal(sys.argv[1], summary_arg)
