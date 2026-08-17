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
from .biblio_match import (VERDICT_UNSCOREABLE, VERDICT_SAME_WORK_VARIANT,
                           SAME_WORK_TITLE_SIM_MIN)
from .eval_report import (build_f2_record, high_band_rate_of_scoreable,
                          assert_f2_fixes_loaded)

Item = (Tuple[str, str, ClaimedRef, RetrievedRecord]
        | Tuple[str, str, str, ClaimedRef, RetrievedRecord])

# Output versions that are FROZEN and must never be overwritten by a re-band.
_PRESERVED_VERSIONS = {"v2", "v3"}

# F2_V3_3 audit: the SAME_WORK gate before v3.3 (0.95). A row that bands
# review_same_work_variant now but sits in [SAME_WORK_TITLE_SIM_MIN, this) is one
# that was review_wrong_paper (HIGH) at the old gate -- i.e. NEWLY quarantined by
# the 0.95 -> 0.92 move. Surfaced in the reband summary so no row moves silently.
_PRIOR_SAME_WORK_TITLE_SIM_MIN = 0.95

# RetrievedRecord constructor field names -- used to reconstruct a record from a
# cache line while ignoring envelope keys (src_pmcid, claimed_pmid, ...) that are
# not RetrievedRecord fields, so ``RetrievedRecord(**line)`` never TypeErrors.
_RETRIEVED_FIELDS = {f.name for f in dataclasses.fields(RetrievedRecord)}


def run_f2_seed7_v3(items: Iterable[Item], *, out_dir: str = ".",
                    out_prefix: str = "f2_random_oa", version: str = "v3",
                    accept: float = 0.85, seed: int = 7,
                    refuse_empty: bool = True) -> dict:
    """Assemble v3 records from ``items`` and write ``<prefix>_seed<seed>_<version>.*``.

    ``items`` preferably yields ``(pmid, src_pmcid, citation_id, claimed,
    resolved)`` using the SAME objects passed to the scorer. The legacy 4-tuple
    form remains accepted and receives a deterministic occurrence ID instead of
    the old blank value. Returns a summary dict (record count + the
    HIGH-band metric). Halts (RuntimeError) if either revision fix is not loaded.

    ``seed`` labels the run (default ``7``) so a fresh held-out draw (e.g. seed 11)
    can share this runner and write its own ``*_seed11_*`` files. This runner is
    what PRODUCES seed-7 v3, so its preserved-version guard is seed-aware and
    narrow: it blocks the frozen seed-7 v2 path only (v2 was written by an earlier
    runner), while still emitting seed-7 v3 by default. Held-out seeds are never
    blocked."""
    if seed == 7 and version.lower() == "v2":
        raise RuntimeError("run_f2_seed7_v3 refuses to write the frozen seed-7 v2 "
                           "path; v2 is preserved. Use version='v3' (or later).")
    assert_f2_fixes_loaded()                      # fail loud BEFORE any write

    records = []
    for occurrence_index, item in enumerate(items, start=1):
        if len(item) == 5:
            pmid, src_pmcid, citation_id, claimed, resolved = item
        elif len(item) == 4:
            pmid, src_pmcid, claimed, resolved = item
            citation_id = f"{src_pmcid or 'doc'}:f2occ{occurrence_index}"
        else:
            raise ValueError("F2 items must be 4-tuples or 5-tuples with a "
                             "citation_id")
        records.append(build_f2_record(
            pmid, src_pmcid, claimed, resolved, accept=accept,
            citation_id=citation_id))
    return _write_run(records, out_dir=out_dir, out_prefix=out_prefix,
                      version=version, seed=seed, refuse_empty=refuse_empty)


def _write_run(records: list, *, out_dir: str, out_prefix: str, version: str,
               extra: Optional[dict] = None, seed: int = 7,
               refuse_empty: bool = True) -> dict:
    """Write ``<prefix>_seed<seed>_<version>.jsonl`` + ``..._summary.json`` and
    return the summary. Shared by the fresh-draw runner and the re-band path so the
    output schema and the HIGH-band metric cannot drift between them. ``seed``
    (default ``7``) parameterizes both the filenames and the summary ``"seed"``
    field; ``seed=7`` reproduces the frozen seed-7 paths and summary byte-for-byte.

    EMPTY-FRAME GUARD (``refuse_empty``, default on): a run that produced ZERO
    records is never a valid artifact, so raise ``EmptyFrameError`` BEFORE creating
    any file -- a zero-row ``*_summary.json`` under a real-looking name is a trap
    for a later glob / hash-pin / session. Living here (not in each caller) means
    both entry points inherit it and no future one can skip it. A caller that
    genuinely wants an empty frame (a join-logic unit test) passes
    ``refuse_empty=False``."""
    records_path = os.path.join(out_dir, f"{out_prefix}_seed{seed}_{version}.jsonl")
    summary_path = os.path.join(out_dir, f"{out_prefix}_seed{seed}_{version}_summary.json")
    if refuse_empty and not records:
        raise EmptyFrameError(
            f"refusing to write an EMPTY frame (0 records) to {records_path!r}: a "
            f"zero-row artifact under a legitimate name is a trap for a later "
            f"glob / hash-pin / session. This is especially load-bearing for a "
            f"fresh held-out draw (§16.3), which cannot be re-run. Pass "
            f"refuse_empty=False only to deliberately materialize an empty frame.")

    os.makedirs(out_dir, exist_ok=True)
    metric = high_band_rate_of_scoreable(records)
    route_reason_counts: dict[str, int] = {}
    for record in records:
        reason = record.get("same_work_reason") or ""
        if reason:
            route_reason_counts[reason] = route_reason_counts.get(reason, 0) + 1
    summary = {
        "version": version,
        "seed": seed,
        "records_path": records_path,
        "n_records": len(records),
        # Named counts make every review-band rescue visible in the run artifact;
        # Rule L2 is reported separately by ``cross_language_excluded`` below via
        # the shared metric because it intentionally has no same-work reason.
        "route_reason_counts": dict(sorted(route_reason_counts.items())),
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


def index_claimed_occurrences_from_xml_dir(
        xml_dir: str, *, src_pmcids: Optional[Iterable[str]] = None) -> dict:
    """Index every PMID-bearing citation occurrence without collapsing repeats.

    Values are XML-ordered ``[(citation_id, ClaimedRef), ...]`` lists.  A source
    article may cite the same PMID more than once, including once correctly and
    once incorrectly; those are distinct audit units and must both survive.
    """
    allow = set(src_pmcids) if src_pmcids is not None else None
    index: dict = {}
    for ref in iter_pmc_dir(xml_dir, source_pmcids=allow):
        pmid = (ref.claimed.claimed_pmid or "").strip()
        if not pmid:
            continue
        key = (ref.source_pmcid or "", pmid)
        index.setdefault(key, []).append((ref.citation_id, ref.claimed))
    return index


def index_claimed_from_xml_dir(xml_dir: str, *,
                               src_pmcids: Optional[Iterable[str]] = None) -> dict:
    """Parse every .xml/.nxml under ``xml_dir`` with the FIXED parser and index
    each PMID-bearing reference's ``ClaimedRef`` by ``(src_pmcid, claimed_pmid)``.

    ``src_pmcid`` is the file stem (the PMCID), matching ``{DATA}/pmc_oa_xml/
    {src_pmcid}.xml``. Only references carrying a claimed PMID are indexed -- the
    resolved cache is keyed by the PMID that was looked up, so a no-PMID ref can
    never join to it. Returns ``{(src_pmcid, claimed_pmid): ClaimedRef}``; on a
    duplicate key the FIRST occurrence wins (deterministic).

    ``src_pmcids`` scopes the index to a held-out seed's source papers when many
    seeds share one XML dir: when it is not ``None`` (built into a set once), any
    ref whose ``source_pmcid`` is not in the allow-list is skipped, so the
    resulting frame -- and thus the reband denominator -- is not contaminated by
    other seeds' articles. ``None`` indexes the whole dir (original behavior)."""
    occurrences = index_claimed_occurrences_from_xml_dir(
        xml_dir, src_pmcids=src_pmcids)
    return {key: values[0][1] for key, values in occurrences.items()}


def load_resolved_cache(resolved_cache_path: str, *, src_pmcid_key: str = "src_pmcid",
                        pmid_key: str = "pmid") -> list:
    """Read the resolved-cache JSONL. Each line yields ``(src_pmcid, pmid,
    RetrievedRecord)``: the RetrievedRecord is reconstructed from the line's nested
    ``"rec"`` sub-object (see ``_retrieved_from_cache``); ``pmid`` comes from the
    top-level ``pmid_key`` (falling back to the reconstructed record's ``.pmid``);
    ``src_pmcid`` from ``src_pmcid_key`` when present, else ``""`` (the join then
    degrades to PMID-only)."""
    out = []
    seen: dict[tuple[str, str], RetrievedRecord] = {}
    with open(resolved_cache_path) as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            env = json.loads(raw)                 # the whole line envelope
            resolved = _retrieved_from_cache(env)  # descends into env["rec"]
            envelope_pmid = str(env.get(pmid_key) or "").strip()
            resolved_pmid = str(resolved.pmid or "").strip()
            if (envelope_pmid and resolved_pmid
                    and envelope_pmid != resolved_pmid):
                raise RuntimeError(
                    "Resolved-cache PMID mismatch: envelope "
                    f"{envelope_pmid!r} != nested record {resolved_pmid!r}; "
                    "refusing to join one work under another identifier.")
            pmid = envelope_pmid or resolved_pmid
            src_pmcid = str(env.get(src_pmcid_key) or "").strip()
            key = (src_pmcid, pmid)
            if pmid and key in seen:
                if seen[key] != resolved:
                    raise RuntimeError(
                        "Conflicting resolved-cache rows for "
                        f"src_pmcid={src_pmcid!r}, pmid={pmid!r}; refusing to "
                        "multiply an ambiguous record across citation occurrences.")
                continue
            if pmid:
                seen[key] = resolved
            out.append((src_pmcid, pmid, resolved))
    return out


class EmptyFrameError(RuntimeError):
    """A run produced ZERO records. Raised by ``_write_run`` (which BOTH entry
    points funnel through) BEFORE any artifact is written when ``refuse_empty`` is
    set, so an empty frame never leaves a zero-row ``*_summary.json`` / ``*.jsonl``
    on disk under a legitimate name -- a later session, a model, or a
    `glob('*_summary.json')` would otherwise pick it up as a real run (the
    stale-artifact / branch-drift failure class this project keeps hitting).

    Guarding this in ``_write_run`` rather than in each caller means no future
    entry point can write an empty frame without opting out, and it protects the
    higher-stakes case: the fresh-draw runner emits the SINGLE-USE held-out
    artifact (§16.3), which -- unlike a reband -- cannot simply be re-run."""


# Backward-compatible alias (the guard first shipped reband-only under this name).
EmptyRebandError = EmptyFrameError


def reband_from_cache(xml_dir: str, resolved_cache_path: str, *,
                      out_dir: str = ".", out_prefix: str = "f2_random_oa",
                      version: str = "v3_1", accept: float = 0.85,
                      src_pmcid_key: str = "src_pmcid",
                      pmid_key: str = "pmid", seed: int = 7,
                      src_pmcids: Optional[Iterable[str]] = None,
                      refuse_empty: bool = True,
                      extra_manifest: Optional[dict] = None) -> dict:
    """Rebuild the F2 frame from the two Drive caches and re-band it with the
    CURRENTLY-LOADED fixes -- NO NCBI/Crossref call. Writes
    ``<prefix>_seed<seed>_<version>.*`` (default ``seed=7``, ``version="v3_1"``).

    ``seed`` labels the run and its output files. The preserved-version guard is
    seed-aware: it refuses only when ``seed == 7`` and the version is frozen
    (v2/v3), keeping the audited seed-7 files untouchable while held-out seeds may
    reband at any new version.

    ``src_pmcids`` scopes the claimed-index to this seed's source papers when many
    seeds share one XML dir (see ``index_claimed_from_xml_dir``). It is required
    when cache rows lack their own source-PMCID field, preventing a PMID from
    leaking across runs that share an XML directory. ``n_src_pmcids`` is reported
    in the summary for auditability.

    Cache format: each resolved-cache line is an envelope
    ``{"pmid": ..., "rec": {resolved, title, authors, ...}}``; the RetrievedRecord
    is reconstructed from the nested ``"rec"`` (see ``_retrieved_from_cache``).

    Join: the resolved cache is joined to citation OCCURRENCES on
    ``(src_pmcid, claimed_pmid)``. Because a cache row is the official record for
    one PMID, it fans out to every in-scope citation occurrence carrying that
    PMID. When a cache line has no ``src_pmcid``, fanout may span source papers;
    each occurrence is still compared with the same resolved work. A line that DOES carry
    a ``src_pmcid`` joins ONLY on its exact key -- a present-but-unmatched
    ``src_pmcid`` is dropped as unmatched, never re-joined to another paper. Both
    fixes ride through ``build_f2_record``: the UNSCOREABLE gate (Bug 1) and the
    strengthened Unicode normalization (Bug 2). Before writing, a guard ABORTS if
    >50% of scoreable rows have an empty resolved_title (a broken reconstruction).

    Returns the run summary plus join diagnostics (``n_resolved_cache``,
    ``n_joined``, ``n_cache_rows_joined``, ``n_occurrence_fanout``,
    ``n_occurrence_duplicates_deduped``,
    ``n_pmid_only_join``, ``n_ambiguous_dropped``, ``n_unmatched_dropped``)
    and the F2_V3_3 audit list
    ``same_work_newly_quarantined`` -- the PMIDs whose band changed from
    review_wrong_paper (at the old 0.95 gate) to review_same_work_variant (at the
    new 0.92 gate), so the threshold move can be audited row-by-row."""
    if seed == 7 and version.lower() in _PRESERVED_VERSIONS:
        raise RuntimeError(
            f"reband_from_cache refuses to write a preserved seed-7 version "
            f"({sorted(_PRESERVED_VERSIONS)}); those runs are frozen. Use "
            f"version='v3_1' (or later), or a held-out seed.")
    assert_f2_fixes_loaded()                      # fail loud BEFORE any read/write

    # Materialize the allow-list once so len() is stable and the set can be passed
    # to the indexer without re-consuming a generator.
    src_pmcid_set = set(src_pmcids) if src_pmcids is not None else None
    cache = load_resolved_cache(resolved_cache_path, src_pmcid_key=src_pmcid_key,
                                pmid_key=pmid_key)
    if src_pmcid_set is None and any(not src for src, _pmid, _rec in cache):
        raise RuntimeError(
            "reband_from_cache requires src_pmcids when cache rows do not carry "
            "src_pmcid. PMID fanout over an unscoped shared XML directory can "
            "silently mix seeds; pass the run's explicit source-PMCID frame.")

    claimed_by_full = index_claimed_occurrences_from_xml_dir(
        xml_dir, src_pmcids=src_pmcid_set)
    # PMID-only fallback index: pmid -> every (source, citation ID, ClaimedRef).
    claimed_by_pmid: dict = {}
    for (src_pmcid, pmid), occurrences in claimed_by_full.items():
        for citation_id, claimed in occurrences:
            claimed_by_pmid.setdefault(pmid, []).append(
                (src_pmcid, citation_id, claimed))

    items: list = []
    item_by_occurrence: dict = {}
    n_raw_occurrence_joins = n_occurrence_duplicates_deduped = 0
    n_pmid_only = n_ambiguous = n_unmatched = n_cache_rows_joined = 0
    for src_pmcid, pmid, resolved in cache:
        if not pmid:
            n_unmatched += 1
            continue
        occurrences = []
        if src_pmcid:
            # A definitely-sourced cache line joins ONLY on its exact
            # (src_pmcid, claimed_pmid) key. If that key misses (a stale PMCID, or
            # a source paper absent from this XML dir), the line is UNMATCHED --
            # never re-joined to a DIFFERENT source paper via the PMID-only
            # fallback. That fallback is reserved for lines with NO src_pmcid; a
            # present-but-unmatched src_pmcid must never be silently rewritten.
            occurrences = [
                (src_pmcid, citation_id, claimed)
                for citation_id, claimed in claimed_by_full.get((src_pmcid, pmid), [])
            ]
        else:
            # The cache stores one resolved record per PMID, so that record is
            # the correct comparator for every in-scope occurrence of the PMID.
            occurrences = claimed_by_pmid.get(pmid, [])
            if occurrences:
                n_pmid_only += 1
        if not occurrences:
            n_unmatched += 1
            continue
        n_cache_rows_joined += 1
        for joined_src, citation_id, claimed in occurrences:
            n_raw_occurrence_joins += 1
            occurrence_key = (joined_src, citation_id, pmid)
            prior = item_by_occurrence.get(occurrence_key)
            item = (pmid, joined_src, citation_id, claimed, resolved)
            if prior is not None:
                if prior[4] != resolved:
                    raise RuntimeError(
                        "Conflicting resolved-cache rows joined to citation "
                        f"occurrence {occurrence_key!r}; refusing to score the "
                        "same citation against two works.")
                n_occurrence_duplicates_deduped += 1
                continue
            item_by_occurrence[occurrence_key] = item
            items.append(item)

    records = [build_f2_record(pmid, s, c, r, accept=accept,
                               citation_id=citation_id)
               for (pmid, s, citation_id, c, r) in items]

    # Pre-write reconstruction guard. If the resolved records were read from the
    # wrong level (top-level instead of the nested "rec"), every row reconstructs
    # to resolved=False + empty title -- which now bands VERDICT_UNRESOLVED (the
    # F2_V3_2 resolved-side gate), so the whole frame would be spurious "unresolved"
    # rather than spurious wrong-paper. Either way it is corrupt. UNRESOLVED rows
    # are DELIBERATELY still counted here (they are non-UNSCOREABLE), so the
    # wrong-level read still trips the guard; a handful of genuinely-unresolved rows
    # is a negligible fraction on real data, while a whole-frame flood is not. When
    # >50% of the SCOREABLE (non-UNSCOREABLE) rows carry an empty resolved_title,
    # the reconstruction/join is broken -- ABORT before writing a corrupt run.
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

    # The empty-frame guard now lives in _write_run (both entry points inherit
    # it), so ``refuse_empty`` is simply threaded through below.

    # F2_V3_3 audit visibility: enumerate the rows that CHANGED band from
    # review_wrong_paper (at the old 0.95 gate) to review_same_work_variant (at the
    # new 0.92 gate) -- i.e. SAME_WORK_VARIANT rows whose title_sim sits in
    # [SAME_WORK_TITLE_SIM_MIN, 0.95). Emitting the PMIDs lets the v3_3 output be
    # diffed against v3.2 so no row is silently quarantined; these are surfaced for
    # human audit, not assumed correct.
    same_work_newly_quarantined = sorted(
        str(r.get("pmid") or "")
        for r in records
        if r.get("verdict") == VERDICT_SAME_WORK_VARIANT
        and r.get("title_sim") is not None
        and SAME_WORK_TITLE_SIM_MIN <= r["title_sim"] < _PRIOR_SAME_WORK_TITLE_SIM_MIN
    )
    same_work_newly_quarantined_occurrences = sorted(
        ({"citation_id": r.get("citation_id") or "",
          "src_pmcid": r.get("src_pmcid") or "",
          "pmid": str(r.get("pmid") or "")}
         for r in records
         if r.get("verdict") == VERDICT_SAME_WORK_VARIANT
         and r.get("title_sim") is not None
         and SAME_WORK_TITLE_SIM_MIN <= r["title_sim"] < _PRIOR_SAME_WORK_TITLE_SIM_MIN),
        key=lambda row: (row["citation_id"], row["pmid"]),
    )
    proof_rule_quarantined_below_gate = sorted(
        ({"citation_id": r.get("citation_id") or "",
          "src_pmcid": r.get("src_pmcid") or "",
          "pmid": str(r.get("pmid") or ""),
          "reason": r.get("same_work_reason") or ""}
         for r in records
         if r.get("verdict") == VERDICT_SAME_WORK_VARIANT
         and r.get("same_work_reason")
         and r.get("same_work_reason") != "near_identical_title"
         and r.get("title_sim") is not None
         and r["title_sim"] < SAME_WORK_TITLE_SIM_MIN),
        key=lambda row: (row["citation_id"], row["pmid"]),
    )

    # F2-G residual census (spec §8.3): the distinct (written_journal,
    # resolved_journal) pairs matched ONLY by the containment heuristic -- the
    # review artifact from which ZD builds versioned manual aliases. Recomputed
    # from the frame's journal pairs, ranked by frequency.
    from .journal_identity import containment_only_census
    journal_containment_census = containment_only_census(
        (r.get("written_journal") or "", r.get("resolved_journal") or "")
        for r in records)

    diag = {
        "n_resolved_cache": len(cache),
        "n_joined": len(items),
        "journal_containment_census": journal_containment_census,
        "n_journal_containment_only": len(journal_containment_census),
        "n_cache_rows_joined": n_cache_rows_joined,
        "n_occurrence_fanout": n_raw_occurrence_joins - n_cache_rows_joined,
        "n_occurrence_duplicates_deduped": n_occurrence_duplicates_deduped,
        "n_pmid_only_join": n_pmid_only,
        "n_ambiguous_dropped": n_ambiguous,
        "n_unmatched_dropped": n_unmatched,
        "n_src_pmcids": len(src_pmcid_set) if src_pmcid_set is not None else None,
        "rebanded_from_cache": True,
        "same_work_newly_quarantined": same_work_newly_quarantined,
        "n_same_work_newly_quarantined": len(same_work_newly_quarantined),
        "same_work_newly_quarantined_occurrences":
            same_work_newly_quarantined_occurrences,
        "proof_rule_quarantined_below_gate": proof_rule_quarantined_below_gate,
        "n_proof_rule_quarantined_below_gate":
            len(proof_rule_quarantined_below_gate),
    }
    # Frame-provenance metadata from the caller (e.g. the selection-manifest path
    # and hash). Recorded in the summary + artifact so a reader can see WHICH
    # allow-list defined the frame; does not touch scoping semantics.
    if extra_manifest:
        diag.update(extra_manifest)
    return _write_run(records, out_dir=out_dir, out_prefix=out_prefix,
                      version=version, extra=diag, seed=seed,
                      refuse_empty=refuse_empty)


# =====================================================================
# CLI entry point for the offline reband (spec §20 verification step)
# =====================================================================
def _parse_selection_ids(raw: bytes) -> list:
    """Source-PMCID allow-list from a selection manifest: a JSON object with
    ``selected_pmcids`` (the seed-37 selection manifest), a JSON list, or a plain
    one-ID-per-line file (``#`` comments allowed). Returns the ids in file order."""
    text = raw.decode("utf-8", "replace")
    head = text.lstrip()[:1]
    if head in ("{", "["):
        obj = json.loads(text)
        if isinstance(obj, dict):
            ids = (obj.get("selected_pmcids") or obj.get("src_pmcids")
                   or obj.get("pmcids") or [])
        elif isinstance(obj, list):
            ids = obj
        else:
            ids = []
    else:
        ids = [ln.split("#", 1)[0].strip() for ln in text.splitlines()]
    return [str(i).strip() for i in ids if str(i).strip()]


def _glob_xml_stems(xml_dir: str) -> list:
    """File stems of every ``*.xml`` / ``*.nxml`` under ``xml_dir`` (the corpus,
    which MAY be a superset of the frame)."""
    import glob
    return [os.path.splitext(os.path.basename(x))[0]
            for x in (glob.glob(os.path.join(xml_dir, "*.xml"))
                      + glob.glob(os.path.join(xml_dir, "*.nxml")))]


def _cli(argv: "Optional[list[str]]" = None) -> int:
    """``python -m cre.f1.f2_run_v3 --reband-from-cache ...`` -- the runnable form
    of §20's "reband the frozen seed-37 frame offline" step. Wraps
    ``reband_from_cache``.

    The evaluation FRAME is defined by a HASH-PINNED selection manifest
    (``--selection-manifest``), NEVER by ``--xml-dir`` contents. ``--xml-dir`` is
    only the corpus location and MAY be a superset of the frame (a shared XML dir);
    stems outside the manifest are ignored and counted, not admitted. This closes
    the frame-scoping defect where directory contents silently defined the frame
    and let out-of-seed papers contaminate the denominator.

    Fails loud rather than produce a misleading run:
      * ``--selection-manifest`` / ``--xml-dir`` / ``--resolved-cache`` are all
        required (argparse exit 2 if any is absent);
      * no ``.xml``/``.nxml`` in ``--xml-dir`` at all, or any manifest PMCID with
        NO XML present -> **exit 2** (absent / missing corpus; a silently smaller
        frame is the same defect in the other direction);
      * an empty frame (``n_records == 0``) -> **exit 3**, no artifact written;
      * a realized ``n_src_pmcids`` != the manifest size -> **exit 4** (frame
        integrity; should be unreachable, kept as a control).
    The manifest path + SHA-256, ``n_src_pmcids``, and the ignored-stem count are
    recorded in the summary and the written artifact."""
    import argparse
    import hashlib
    import sys

    p = argparse.ArgumentParser(
        prog="python -m cre.f1.f2_run_v3",
        description="Offline reband of a frozen F2 frame from the two caches "
                    "(source XML + resolved records); no network.")
    p.add_argument("--reband-from-cache", dest="reband", action="store_true",
                   required=True, help="run the offline reband (the only mode).")
    p.add_argument("--resolved-cache", required=True,
                   help="path to the resolved-record cache JSONL.")
    p.add_argument("--xml-dir", required=True,
                   help="directory of source PMC XML (the CORPUS location; MAY be "
                        "a superset of the frame -- it does NOT define the frame).")
    p.add_argument("--selection-manifest", required=True,
                   help="hash-pinned allow-list of source PMCIDs that DEFINE the "
                        "frame (the seed selection manifest with 'selected_pmcids', "
                        "a JSON list, or one PMCID per line). The frame comes from "
                        "THIS, never from --xml-dir contents.")
    p.add_argument("--out-dir", default=".")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--version", default="v3_1")
    args = p.parse_args(argv)

    # Frame = the hash-pinned manifest allow-list (never the directory contents).
    try:
        manifest_raw = open(args.selection_manifest, "rb").read()
    except OSError as e:
        p.error(f"--selection-manifest {args.selection_manifest!r}: {e}")
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    frame = sorted(set(_parse_selection_ids(manifest_raw)))
    if not frame:
        p.error(f"--selection-manifest {args.selection_manifest!r} lists no source "
                f"PMCIDs (expected 'selected_pmcids', a JSON list, or one per line).")

    stems = set(_glob_xml_stems(args.xml_dir))
    if not stems:
        p.error(f"--xml-dir {args.xml_dir!r} contains no .xml/.nxml files; the "
                f"pinned source corpus must be present for a reband.")
    # A manifest PMCID with no XML is a MISSING corpus -- a silently smaller frame
    # is the same defect as a contaminated one; refuse (exit 2).
    missing = [pid for pid in frame if pid not in stems]
    if missing:
        print(f"ERROR: {len(missing)} of {len(frame)} manifest source PMCIDs have "
              f"NO XML in --xml-dir {args.xml_dir!r} -- missing corpus; a silently "
              f"smaller frame is refused. examples: {missing[:5]}", file=sys.stderr)
        return 2
    n_ignored = len(stems - set(frame))      # superset is normal; count, don't admit

    manifest_meta = {
        "selection_manifest": args.selection_manifest,
        "selection_manifest_sha256": manifest_sha,
        "n_manifest_src_pmcids": len(frame),
        "n_xml_dir_stems": len(stems),
        "n_ignored_stems": n_ignored,
    }

    # An EMPTY frame is fatal and is refused INSIDE reband_from_cache (refuse_empty
    # defaults on), so NO zero-row artifact is written. Turn it into a clean exit 3.
    try:
        summary = reband_from_cache(
            xml_dir=args.xml_dir, resolved_cache_path=args.resolved_cache,
            out_dir=args.out_dir, version=args.version, seed=args.seed,
            src_pmcids=frame, extra_manifest=manifest_meta)
    except EmptyFrameError as e:
        print(f"ERROR: {e} This is NOT a valid verification; no artifact written.",
              file=sys.stderr)
        return 3

    # Assert the realized frame IS the manifest -- a control, not a mere report
    # (the audit field n_src_pmcids was printed and never checked on the
    # contaminated run). Unreachable in normal operation; kept as a tripwire.
    if summary.get("n_src_pmcids") != len(frame):
        print(f"ERROR: realized n_src_pmcids={summary.get('n_src_pmcids')} != "
              f"manifest size {len(frame)}; frame scoping is not what the manifest "
              f"specifies.", file=sys.stderr)
        return 4

    scalar = {k: v for k, v in summary.items() if not isinstance(v, list)}
    print(json.dumps(scalar, indent=2))
    return 0


if __name__ == "__main__":               # pragma: no cover
    raise SystemExit(_cli())
