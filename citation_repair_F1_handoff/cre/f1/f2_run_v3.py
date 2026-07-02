"""v3 seed=7 F2 runner scaffold.

The heavy data-drawing for the seed=7 frame (random PMCID sample, EFetch) runs in
Colab (this environment can't reach NCBI/Crossref). This module is the
version-controlled, testable CORE the Colab runner should call:

  * the both-fixes-loaded guard (fail loud + HALT before writing anything -- the
    backstop against a stale sys.modules / stale-checkout run);
  * record assembly via ``build_f2_record`` (the re-bandable schema);
  * versioned output paths that PRESERVE v2 (writes ``*_seed7_v3.*``, refuses to
    target v2);
  * the HIGH-band metric with SAME_WORK_VARIANT quarantined;
  * an OFFLINE RE-BAND entry point (``reband_from_cache``) that rebuilds the frame
    from the two Drive caches -- source XML + resolved records -- with NO re-fetch,
    for applying banding fixes (F2_V3_1 Bug 1 / Bug 2) to an existing run.

Colab usage (fresh draw):
    from cre.f1.f2_run_v3 import run_f2_seed7_v3
    # items: iterable of (pmid, src_pmcid, ClaimedRef, RetrievedRecord) built from
    # your EFetch results for the seed=7 sample.
    summary = run_f2_seed7_v3(items, out_dir="/content/out")
    print(summary["flagged_f2_high"], summary["high_band_rate_of_scoreable"])

Colab usage (re-band an existing run from cache, no NCBI call):
    from cre.f1.f2_run_v3 import reband_from_cache
    summary = reband_from_cache(
        xml_dir=f"{DATA}/pmc_oa_xml",
        resolved_cache_path=f"{DATA}/f2_resolved_cache_seed7_v3.jsonl",
        out_dir="/content/out", version="v3_1")   # writes *_seed7_v3_1.*, keeps v3
"""
from __future__ import annotations
import dataclasses
import json
import os
from typing import Iterable, Optional, Tuple

from .schema import ClaimedRef, RetrievedRecord
from .parser import iter_pmc_dir
from .biblio_match import VERDICT_UNSCOREABLE
from .eval_report import (build_f2_record, high_band_rate_of_scoreable,
                          assert_f2_fixes_loaded)

Item = Tuple[str, str, ClaimedRef, RetrievedRecord]

# Output versions that are FROZEN and must never be overwritten by a re-band.
_PRESERVED_VERSIONS = {"v2", "v3"}

# RetrievedRecord constructor field names -- used to reconstruct a record from a
# cache line while ignoring envelope keys (src_pmcid, claimed_pmid, ...) that are
# not RetrievedRecord fields, so ``RetrievedRecord(**line)`` never TypeErrors.
_RETRIEVED_FIELDS = {f.name for f in dataclasses.fields(RetrievedRecord)}


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
    return _write_run(records, out_dir=out_dir, out_prefix=out_prefix,
                      version=version)


def _write_run(records: list, *, out_dir: str, out_prefix: str, version: str,
               extra: Optional[dict] = None) -> dict:
    """Write ``<prefix>_seed7_<version>.jsonl`` + ``..._summary.json`` and return
    the summary. Shared by the fresh-draw runner and the re-band path so the
    output schema and the HIGH-band metric cannot drift between them."""
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
        **(extra or {}),
    }
    with open(records_path, "w") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    return summary


# =====================================================================
# Offline re-band from cache (F2_V3_1 -- no re-fetch)
# =====================================================================
def _retrieved_from_cache(line: dict) -> RetrievedRecord:
    """Reconstruct a ``RetrievedRecord`` from one resolved-cache line.

    The RetrievedRecord fields live in a NESTED ``"rec"`` sub-object -- the cache
    envelope is ``{"pmid": ..., "rec": {resolved, title, authors, year, journal,
    doi, volume, pages, is_container, year_from_dep}}`` -- so descend into it.
    Fall back to the top-level object when ``rec`` is absent, so an un-enveloped
    (flat) line still reconstructs. Keeps only real RetrievedRecord fields, so
    envelope keys (pmid, src_pmcid, ...) are ignored and never TypeError.

    Reading the wrong level would silently yield ``resolved=False`` + empty title
    on every row (they carry no RetrievedRecord fields), so this descent is
    load-bearing; ``reband_from_cache`` also guards against it before writing."""
    fields = line.get("rec")
    if not isinstance(fields, dict):
        fields = line
    return RetrievedRecord(**{k: v for k, v in fields.items()
                              if k in _RETRIEVED_FIELDS})


def index_claimed_from_xml_dir(xml_dir: str) -> dict:
    """Parse every .xml/.nxml under ``xml_dir`` with the FIXED parser and index
    each PMID-bearing reference's ``ClaimedRef`` by ``(src_pmcid, claimed_pmid)``.

    ``src_pmcid`` is the file stem (the PMCID), matching ``{DATA}/pmc_oa_xml/
    {src_pmcid}.xml``. Only references carrying a claimed PMID are indexed -- the
    resolved cache is keyed by the PMID that was looked up, so a no-PMID ref can
    never join to it. Returns ``{(src_pmcid, claimed_pmid): ClaimedRef}``; on a
    duplicate key the FIRST occurrence wins (deterministic)."""
    index: dict = {}
    for ref in iter_pmc_dir(xml_dir):
        pmid = (ref.claimed.claimed_pmid or "").strip()
        if not pmid:
            continue
        key = (ref.source_pmcid or "", pmid)
        index.setdefault(key, ref.claimed)
    return index


def load_resolved_cache(resolved_cache_path: str, *, src_pmcid_key: str = "src_pmcid",
                        pmid_key: str = "pmid") -> list:
    """Read the resolved-cache JSONL. Each line yields ``(src_pmcid, pmid,
    RetrievedRecord)``: the RetrievedRecord is reconstructed from the line's nested
    ``"rec"`` sub-object (see ``_retrieved_from_cache``); ``pmid`` comes from the
    top-level ``pmid_key`` (falling back to the reconstructed record's ``.pmid``);
    ``src_pmcid`` from ``src_pmcid_key`` when present, else ``""`` (the join then
    degrades to PMID-only)."""
    out = []
    with open(resolved_cache_path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            env = json.loads(raw)                 # the whole line envelope
            resolved = _retrieved_from_cache(env)  # descends into env["rec"]
            pmid = str(env.get(pmid_key) or resolved.pmid or "").strip()
            src_pmcid = str(env.get(src_pmcid_key) or "").strip()
            out.append((src_pmcid, pmid, resolved))
    return out


def reband_from_cache(xml_dir: str, resolved_cache_path: str, *,
                      out_dir: str = ".", out_prefix: str = "f2_random_oa",
                      version: str = "v3_1", accept: float = 0.85,
                      src_pmcid_key: str = "src_pmcid",
                      pmid_key: str = "pmid") -> dict:
    """Rebuild the seed=7 F2 frame from the two Drive caches and re-band it with
    the CURRENTLY-LOADED fixes -- NO NCBI/Crossref call. Writes
    ``<prefix>_seed7_<version>.*`` (default ``v3_1``); refuses to target a frozen
    version (v2/v3 are preserved, never overwritten).

    Cache format: each resolved-cache line is an envelope
    ``{"pmid": ..., "rec": {resolved, title, authors, ...}}``; the RetrievedRecord
    is reconstructed from the nested ``"rec"`` (see ``_retrieved_from_cache``).

    Join: the resolved cache is joined to the parsed claimed refs on
    ``(src_pmcid, claimed_pmid)``. When a cache line has no ``src_pmcid``, the join
    falls back to PMID-only and is accepted ONLY when that PMID is unique across
    the parsed frame; an ambiguous PMID-only line (same PMID in >1 source paper)
    is dropped and counted, never silently mis-joined. A cache line that DOES carry
    a ``src_pmcid`` joins ONLY on its exact key -- a present-but-unmatched
    ``src_pmcid`` is dropped as unmatched, never re-joined to another paper. Both
    fixes ride through ``build_f2_record``: the UNSCOREABLE gate (Bug 1) and the
    strengthened Unicode normalization (Bug 2). Before writing, a guard ABORTS if
    >50% of scoreable rows have an empty resolved_title (a broken reconstruction).

    Returns the run summary plus join diagnostics (``n_resolved_cache``,
    ``n_joined``, ``n_pmid_only_join``, ``n_ambiguous_dropped``,
    ``n_unmatched_dropped``)."""
    if version.lower() in _PRESERVED_VERSIONS:
        raise RuntimeError(
            f"reband_from_cache refuses to write a preserved version "
            f"({sorted(_PRESERVED_VERSIONS)}); those runs are frozen. Use "
            f"version='v3_1' (or later).")
    assert_f2_fixes_loaded()                      # fail loud BEFORE any read/write

    claimed_by_full = index_claimed_from_xml_dir(xml_dir)
    # PMID-only fallback index: pmid -> list of (src_pmcid, ClaimedRef).
    claimed_by_pmid: dict = {}
    for (src_pmcid, pmid), claimed in claimed_by_full.items():
        claimed_by_pmid.setdefault(pmid, []).append((src_pmcid, claimed))

    cache = load_resolved_cache(resolved_cache_path, src_pmcid_key=src_pmcid_key,
                                pmid_key=pmid_key)

    items: list = []
    n_pmid_only = n_ambiguous = n_unmatched = 0
    for src_pmcid, pmid, resolved in cache:
        if not pmid:
            n_unmatched += 1
            continue
        claimed = None
        joined_src = src_pmcid
        if src_pmcid:
            # A definitely-sourced cache line joins ONLY on its exact
            # (src_pmcid, claimed_pmid) key. If that key misses (a stale PMCID, or
            # a source paper absent from this XML dir), the line is UNMATCHED --
            # never re-joined to a DIFFERENT source paper via the PMID-only
            # fallback. That fallback is reserved for lines with NO src_pmcid; a
            # present-but-unmatched src_pmcid must never be silently rewritten.
            claimed = claimed_by_full.get((src_pmcid, pmid))
        else:
            # No src_pmcid: fall back to a PMID-only join, accepted only when the
            # PMID is unique across the parsed frame (else ambiguous -> drop).
            candidates = claimed_by_pmid.get(pmid, [])
            if len(candidates) == 1:              # unique PMID -> safe join
                joined_src, claimed = candidates[0]
                n_pmid_only += 1
            elif len(candidates) > 1:             # ambiguous -> never guess
                n_ambiguous += 1
                continue
        if claimed is None:
            n_unmatched += 1
            continue
        items.append((pmid, joined_src, claimed, resolved))

    records = [build_f2_record(pmid, s, c, r, accept=accept)
               for (pmid, s, c, r) in items]

    # Pre-write reconstruction guard. If the resolved records were read from the
    # wrong level (top-level instead of the nested "rec"), every row reconstructs
    # to resolved=False + empty title and then bands as spurious wrong-paper. When
    # >50% of the SCOREABLE (non-UNSCOREABLE) rows carry an empty resolved_title,
    # the reconstruction/join is broken -- ABORT before writing a corrupt v3_1.
    scoreable_recs = [r for r in records
                      if r.get("verdict") != VERDICT_UNSCOREABLE]
    n_empty_resolved = sum(1 for r in scoreable_recs
                           if not (r.get("resolved_title") or "").strip())
    if scoreable_recs and n_empty_resolved / len(scoreable_recs) > 0.5:
        raise RuntimeError(
            f"reband_from_cache: {n_empty_resolved}/{len(scoreable_recs)} scoreable "
            f"rows have an EMPTY resolved_title (> 50%). The resolved cache almost "
            f"certainly reconstructed from the wrong level -- RetrievedRecord fields "
            f"live in the nested 'rec' sub-object of each line. Aborting before any "
            f"write so a corrupt v3_1 is never emitted.")

    diag = {
        "n_resolved_cache": len(cache),
        "n_joined": len(items),
        "n_pmid_only_join": n_pmid_only,
        "n_ambiguous_dropped": n_ambiguous,
        "n_unmatched_dropped": n_unmatched,
        "rebanded_from_cache": True,
    }
    return _write_run(records, out_dir=out_dir, out_prefix=out_prefix,
                      version=version, extra=diag)
