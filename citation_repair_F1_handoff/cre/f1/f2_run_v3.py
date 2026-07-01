"""v3 seed=7 F2 runner scaffold.

The heavy data-drawing for the seed=7 frame (random PMCID sample, EFetch) runs in
Colab (this environment can't reach NCBI/Crossref). This module is the
version-controlled, testable CORE the Colab runner should call:

  * the both-fixes-loaded guard (fail loud + HALT before writing anything -- the
    backstop against a stale sys.modules / stale-checkout run);
  * record assembly via ``build_f2_record`` (the re-bandable schema);
  * versioned output paths that PRESERVE v2 (writes ``*_seed7_v3.*``, refuses to
    target v2);
  * the HIGH-band metric with SAME_WORK_VARIANT quarantined.

Colab usage:
    from cre.f1.f2_run_v3 import run_f2_seed7_v3
    # items: iterable of (pmid, src_pmcid, ClaimedRef, RetrievedRecord) built from
    # your EFetch results for the seed=7 sample.
    summary = run_f2_seed7_v3(items, out_dir="/content/out")
    print(summary["flagged_f2_high"], summary["high_band_rate_of_scoreable"])
"""
from __future__ import annotations
import json
import os
from typing import Iterable, Tuple

from .schema import ClaimedRef, RetrievedRecord
from .eval_report import (build_f2_record, high_band_rate_of_scoreable,
                          assert_f2_fixes_loaded)

Item = Tuple[str, str, ClaimedRef, RetrievedRecord]


def run_f2_seed7_v3(items: Iterable[Item], *, out_dir: str = ".",
                    out_prefix: str = "f2_random_oa", version: str = "v3",
                    accept: float = 0.85) -> dict:
    """Assemble v3 records from ``items`` and write ``<prefix>_seed7_<version>.*``.

    ``items`` yields ``(pmid, src_pmcid, claimed, resolved)`` using the SAME
    objects passed to the scorer. Returns a summary dict (record count + the
    HIGH-band metric). Halts (RuntimeError) if either revision fix is not loaded,
    or if asked to write a v2 path (v2 is preserved, never overwritten)."""
    if version.lower() == "v2":
        raise RuntimeError("run_f2_seed7_v3 refuses to write v2 paths; v2 is "
                           "preserved. Use version='v3' (or later).")
    assert_f2_fixes_loaded()                      # fail loud BEFORE any write

    records = [build_f2_record(pmid, src_pmcid, claimed, resolved, accept=accept)
               for (pmid, src_pmcid, claimed, resolved) in items]

    os.makedirs(out_dir, exist_ok=True)
    records_path = os.path.join(out_dir, f"{out_prefix}_seed7_{version}.jsonl")
    summary_path = os.path.join(out_dir, f"{out_prefix}_seed7_{version}_summary.json")

    metric = high_band_rate_of_scoreable(records)
    summary = {
        "version": version,
        "seed": 7,
        "records_path": records_path,
        "n_records": len(records),
        **metric,
    }
    with open(records_path, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    return summary
