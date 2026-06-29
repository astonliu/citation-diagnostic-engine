"""F2 measurement layer -- buckets, evidence bands, base rate, precision + CI.

Read-only. Consumes the per-reference log records that ``run.py`` already writes
(``Reference.to_log_record()``) and emits the numbers the F2 write-up needs. It
NEVER re-runs the network, NEVER mutates ``ref.label``, and NEVER assigns an F2
label -- it only counts and reports what the deterministic pipeline + the human
adjudications produced.

Three things the pipeline did not measure before (spec §5/§6):

  1. UNSCOREABLE is a NAMED, COUNTED coverage bucket (never silently dropped),
     reported by reason and excluded from the F2 numerator and the base rate.
  2. The flagged pool is BANDED by strength of wrong-reference evidence, so the
     same-author/same-year ambiguous cases are surfaced for review rather than
     auto-decided. Banding reads the raw (author/year/journal/volume/pages)
     verdict tuple directly -- NEVER the difference ``score - title_sim`` (a
     confirmatory boost can mask a field disagreement, so that difference is not
     an invertible read-out of the field verdicts).
  3. Precision on the wrong-paper band is reported with a Wilson interval, scored
     ONLY against human adjudications (never the detector's own labels), and the
     two precision samples are kept separate (never pooled).
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Optional

from .schema import F1, F2, UNVERIFIABLE, UNSCOREABLE


# =====================================================================
# Wilson score interval (pure arithmetic; small-n honest, unlike normal approx)
# =====================================================================
def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% (default z) Wilson score interval for k successes in n trials."""
    if n <= 0:
        return (0.0, 0.0)
    phat = k / n
    denom = 1.0 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    half = (z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))) / denom
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


# =====================================================================
# Evidence banding (on the verdict TUPLE, never on score - title_sim)
# =====================================================================
# Bands, in priority order:
BAND_STRONG_WRONG = "STRONG_WRONG"            # a field confidently disagrees
BAND_SAME_AUTHOR_SAME_YEAR = "SAME_AUTHOR_SAME_YEAR"   # HARD: surface for review
BAND_SPARSE = "SPARSE"                         # author can't be judged (F2-prone)
BAND_OTHER = "OTHER"


def band_of(log: dict) -> str:
    """Classify a flagged reference by strength of wrong-reference evidence,
    using the structured field verdicts (True / False / None).

    * STRONG_WRONG -- author or year CONFIDENTLY disagrees (the clearest
      wrong-reference signal).
    * SAME_AUTHOR_SAME_YEAR -- author AND year both agree yet the title differs:
      metadata-indistinguishable from a same-paper cosmetic variant, so it is
      surfaced for human review, never auto-decided.
    * SPARSE -- the claimed author could not be judged (absent on a side): the
      sparse-field population most prone to a missed wrong-reference.
    * OTHER -- everything else (partial corroboration).
    """
    a, y = log.get("author_match"), log.get("year_match")
    if a is False or y is False:
        return BAND_STRONG_WRONG
    if a is None:
        return BAND_SPARSE
    if a is True and y is True:
        return BAND_SAME_AUTHOR_SAME_YEAR
    return BAND_OTHER


# =====================================================================
# Report
# =====================================================================
def _lg(rec: dict) -> dict:
    return rec.get("log", {}) or {}


def summarize(log_records: list[dict],
              gold: Optional[dict] = None) -> dict:
    """Build the F2 measurement report from per-reference log records.

    ``gold`` (optional): ``{citation_id: human_taxonomy_label}`` from human
    adjudication (e.g. via ``adjudicate.Adjudicator``). When provided, precision
    on the wrong-paper band is computed against it. When absent, band sizes are
    reported but precision is left ``None`` (it is NOT estimated from the
    detector's own labels -- that would be circular, spec §6/C5).
    """
    total = len(log_records)
    unscoreable_by_reason: Counter = Counter()
    unverifiable = 0
    pmid_bearing = 0
    pmid_resolved = 0
    flagged_total = 0
    f2_count = 0
    override_cleared = 0          # cleared by the strong-corroboration override
    band_counts: Counter = Counter()
    flagged_ids_by_band: dict[str, list[str]] = {}

    for rec in log_records:
        lg = _lg(rec)
        label = rec.get("label")
        cid = rec.get("citation_id", "")
        if label == UNSCOREABLE or lg.get("unscoreable_reason"):
            unscoreable_by_reason[lg.get("unscoreable_reason") or "unspecified"] += 1
            continue                       # excluded from numerator AND denominator
        if label == UNVERIFIABLE:
            unverifiable += 1
            continue
        if lg.get("pmid_present"):
            pmid_bearing += 1
            if lg.get("pmid_resolved"):
                pmid_resolved += 1
        if label == F2:
            f2_count += 1
        # The override-cleared population is the known same-author/same-journal
        # residual: a low-title-similarity pair floored to accept and NOT flagged.
        # Counted so its size is MEASURED on real data, never assumed away.
        if lg.get("override_fired") and not lg.get("mismatch_flagged"):
            override_cleared += 1
        if lg.get("mismatch_flagged"):
            flagged_total += 1
            b = band_of(lg)
            band_counts[b] += 1
            flagged_ids_by_band.setdefault(b, []).append(cid)

    unscoreable_total = sum(unscoreable_by_reason.values())
    scoreable = total - unscoreable_total - unverifiable

    # Base rate headline: ID-bearing F2 as a fraction of PMID-bearing references
    # (the spec's ~0.1-0.2%); NOT a precision figure.
    base_rate = (f2_count / pmid_bearing) if pmid_bearing else None

    report = {
        "counts": {
            "total": total,
            "scoreable": scoreable,
            "unverifiable": unverifiable,
            "unscoreable_total": unscoreable_total,
            "pmid_bearing": pmid_bearing,
            "pmid_resolved": pmid_resolved,
            "flagged_total": flagged_total,
            "f2_count": f2_count,
            "override_cleared": override_cleared,
        },
        "unscoreable_by_reason": dict(unscoreable_by_reason),
        "flagged_band_counts": dict(band_counts),
        "base_rate_per_pmid_bearing": base_rate,
        "wrong_paper_precision": _precision_on_band(
            flagged_ids_by_band.get(BAND_STRONG_WRONG, []), gold),
        "notes": [
            "UNSCOREABLE is excluded from both the flagged pool and the F2 "
            "numerator; reported by reason above.",
            "Bands are read from the (author/year/journal/volume/pages) verdict "
            "tuple, NOT from score - title_sim (non-invertible).",
            "SAME_AUTHOR_SAME_YEAR is surfaced for human review, never "
            "auto-decided.",
            "base_rate is the headline F2 finding, not precision; the two "
            "precision samples (recent-block, random seed=7) do NOT pool.",
        ],
    }
    return report


def _precision_on_band(band_ids: list[str], gold: Optional[dict]) -> dict:
    """Precision over a flagged band vs HUMAN adjudications (gold). Returns the
    band size always; the point estimate + Wilson CI only when gold is given for
    at least one band member (never estimated from detector self-labels)."""
    out = {"band_size": len(band_ids), "n_adjudicated": 0, "k_true_f2": 0,
           "point": None, "ci95": None,
           "source": "human_adjudication" if gold else None}
    if not gold:
        out["note"] = ("no human adjudications supplied; precision NOT estimated "
                       "from detector labels (would be circular, §6/C5).")
        return out
    n = k = 0
    for cid in band_ids:
        human = gold.get(cid)
        if human is None:
            continue
        n += 1
        if human == F2:
            k += 1
    out["n_adjudicated"], out["k_true_f2"] = n, k
    if n:
        out["point"] = round(k / n, 4)
        out["ci95"] = wilson_ci(k, n)
    return out


def format_report(report: dict) -> str:
    """Human-readable one-screen summary."""
    c = report["counts"]
    lines = [
        "===== F2 measurement report =====",
        f"  references total      : {c['total']}",
        f"  scoreable             : {c['scoreable']}",
        f"  unverifiable          : {c['unverifiable']}",
        f"  UNSCOREABLE (excluded): {c['unscoreable_total']}  "
        f"{report['unscoreable_by_reason']}",
        f"  PMID-bearing          : {c['pmid_bearing']} "
        f"(resolved {c['pmid_resolved']})",
        f"  flagged pool          : {c['flagged_total']}  "
        f"bands={report['flagged_band_counts']}",
        f"  override-cleared      : {c['override_cleared']}  "
        f"(same-author/journal residual; measure against gold)",
        f"  F2 labelled           : {c['f2_count']}",
        f"  base rate (F2/PMID-bearing): {report['base_rate_per_pmid_bearing']}",
    ]
    wp = report["wrong_paper_precision"]
    if wp.get("point") is not None:
        lines.append(f"  wrong-paper precision : {wp['point']} "
                     f"(k={wp['k_true_f2']}/n={wp['n_adjudicated']}, "
                     f"95% CI {wp['ci95']}, {wp['source']})")
    else:
        lines.append(f"  wrong-paper band size : {wp['band_size']} "
                     f"({wp.get('note','')})")
    return "\n".join(lines)
