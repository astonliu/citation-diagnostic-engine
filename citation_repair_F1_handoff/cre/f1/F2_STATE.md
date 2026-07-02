# F2 state — v3.1 re-band

Working-state note for the F2 (wrong-paper) precision pass. Branch:
`feat/f2-final-revision`. Module: `cre/f1/`.

## What v3.1 fixes (and why)

The v3 live run (seed=7 random PMC-OA frame, 23,351 refs) produced **331
`review_wrong_paper` (HIGH)** — a 25× jump from v2's 13, and ~1.43% vs the
established 0.1–0.2% F2 base rate. That magnitude was a bug signal, **not** a real
F2 surge. Defect A (the v3 fix that newly parses `<string-name>` authors, ~5,125
previously-null authors) is correct; it *exposed* two banding bugs upstream of the
metric. v3.1 fixes both as targeted amendments (no module rewrites) and re-bands
from cache (no re-fetch).

Decomposition of the 331, and where each goes after v3.1:

| Sub-population | Count | v3 band (wrong) | v3.1 band (fixed) |
|---|---|---|---|
| empty `written_title` (`title_sim=0.0`, `author_match=None`) | 303 | `review_wrong_paper` | `unscoreable` (excluded from HIGH + denominator) |
| same first author after strong Unicode normalization (U+2010 hyphen, diacritics, case) | ~15 | `review_wrong_paper` | `match` / `review_same_work_variant` (author now agrees) |
| `title_sim ≥ 0.95` only after strong normalization | ~6 | `review_wrong_paper` | `review_same_work_variant` (quarantined) or `match` |
| genuine HIGH after both fixes | ~28 | `review_wrong_paper` | `review_wrong_paper` (unchanged) |

Expected v3.1 headline: **`flagged_f2_high` ≈ 28**, `denominator_scoreable` ≈
22,612, `high_band_rate_of_scoreable` ≈ **0.124%** — back in base-rate territory.
(Exact numbers come from the Colab re-band; the repo tests prove the *mechanisms*.)

### Bug 1 — UNSCOREABLE gate in `build_f2_record` (the 303-row leak)

The live path (`lookup.compare_and_flag`) routes non-title / placeholder /
book-container / empty-title pairs through `classify_unscoreable` **before**
scoring, into a counted UNSCOREABLE bucket. `build_f2_record` (the v3 banding
core) called `match_score` + `flag_verdict` directly and skipped that gate, so an
empty claimed title scored `title_sim=0.0` and banded WRONG_PAPER.

Fix: `build_f2_record` now applies the same `classify_unscoreable(claimed,
resolved)` gate first. A gated pair is emitted with `verdict=VERDICT_UNSCOREABLE`
(`"unscoreable"`, matching `schema.UNSCOREABLE`) and its bucket in
`unscoreable_reason`; `match_score`/`title_sim`/field verdicts are left `None`
(never fabricated). `high_band_rate_of_scoreable` drops UNSCOREABLE rows from
**both** the HIGH numerator and the scoreable denominator, reporting
`unscoreable_excluded` — mirroring how `decide()` drops UNSCOREABLE live.

### Bug 2 — insufficient Unicode dash folding

`biblio_match.normalize_title` collapses intra-token ASCII hyphens (`t-rna` →
`trna`) but let Unicode dash variants survive to the punctuation-strip step, where
they became a word-splitting space. So `Topka‐Bielecka` (U+2010) normalized to
`topka bielecka` while `Topka-Bielecka` (ASCII) normalized to `topkabielecka` —
the same surname/title mis-comparing (false `author_match=False`; `title_sim`
deflated below the 0.95 SAME_WORK gate).

Fix: fold U+2010–U+2015 + U+2212 to ASCII `-` **before** the intra-token collapse,
in `normalize_title` (the site the v3 banding path uses for both author-surname
comparison and title similarity, keeping them in agreement). The same fold is
mirrored into `lookup._normalize` for consistency (a no-op there — that normalizer
already word-splits every hyphen). Punctuation/diacritic/case folding only; no
fuzzy surname matching, no token reordering.

## Bug 3 — mixed-citation coverage (scoping decision, NOT a code change)

**727 refs (3.1%)** of the frame parse no structured title: free-text
`<mixed-citation>`/`<citation>` refs where the parser returns only `raw` (author,
title, and source run together, e.g. PMID 28146066:
`"Norris EJ, Coats JR. Current and future repellent technologies…"` with
`claimed.title=''`). The Bug 1 UNSCOREABLE gate correctly quarantines these from
false F2 (`no_claimed_title`), but that also makes them **invisible to F2
detection** — a recall hole, not a precision artifact.

**Decision (this pass): F2 is structured-citation-only** (`<element-citation>` and
mixed-citations that carry a discrete `<article-title>`). Mixed-citation free-text
title parsing is **deferred**. Coverage figure to record in methods: **3.1%** of
references are outside the F2-scoreable frame for this reason. Revisit for the
journal submission if the mixed-citation population is material to the recall
claim (F2 recall is separately unmeasurable now — see the P(fail|real) plan).

## Re-band from cache (no re-fetch)

`f2_run_v3.reband_from_cache(xml_dir, resolved_cache_path, out_dir=…,
version="v3_1")` rebuilds the frame offline from the two Drive caches and re-bands
with the currently-loaded fixes:

- **Claimed side:** parse every `{DATA}/pmc_oa_xml/{src_pmcid}.xml` with the fixed
  parser; index each PMID-bearing ref's `ClaimedRef` by `(src_pmcid,
  claimed_pmid)` (`index_claimed_from_xml_dir`).
- **Resolved side:** load `{DATA}/f2_resolved_cache_seed7_v3.jsonl`. Each line is
  an envelope `{"pmid": ..., "rec": {resolved, title, authors, year, journal, doi,
  volume, pages, is_container, year_from_dep}}`; the `RetrievedRecord` is
  reconstructed from the **nested `"rec"`** (descend into it — reading the top
  level yields `resolved=False`/empty-title on every row). Un-enveloped flat lines
  fall back to the top level (`load_resolved_cache` / `_retrieved_from_cache`).
- **Join** on `(src_pmcid, claimed_pmid)`. A cache line with no `src_pmcid` falls
  back to a PMID-only join, accepted only when that PMID is unique across the
  frame; an ambiguous PMID-only line is dropped and counted, never mis-joined. A
  line that *does* carry a `src_pmcid` joins ONLY on its exact key — a
  present-but-unmatched `src_pmcid` is dropped as unmatched, never re-joined to a
  different paper.
- **Operational note:** the current cache envelope carries **no `src_pmcid`**, so
  every line takes the PMID-only path. Any target PMID cited by >1 sampled source
  paper is dropped as ambiguous (precision-safe, never mis-banded) — **watch
  `n_ambiguous_dropped`** in the summary. If it is material, add `src_pmcid` to the
  cache envelope (exact join) or fan out one banded record per (src_pmcid, PMID);
  this pass does neither, to avoid a silent mis-join.
- **Pre-write guard:** aborts if >50% of scoreable rows have an empty
  `resolved_title` — the signature of a broken reconstruction (wrong-level read);
  a corrupt v3_1 is never written.
- Writes `*_seed7_v3_1.*`; **refuses** to target a frozen version (v2/v3 preserved)
  and calls `assert_f2_fixes_loaded()` (fail-loud stale-module guard) before any
  read/write. Summary carries join diagnostics (`n_resolved_cache`, `n_joined`,
  `n_pmid_only_join`, `n_ambiguous_dropped`, `n_unmatched_dropped`).

## After the re-band — audit

Hand-adjudicate the ~28 `review_wrong_paper` rows; Wilson CI on HIGH only. Apply
the near-0.95 `title_sim` lens (a residual formatting variant can still sit just
under the gate). Confirm the six regression guards stay HIGH with `title_sim <
0.95` and the ANOMALY trio diverts to `review_same_work_variant`. **Do not merge
to `main` until the ~28 HIGH rows are hand-audited.**

## Tests

`cre/f1/test_f2_v3_1.py` (27 tests): UNSCOREABLE gate + schema uniformity + metric
exclusion; the 28146066 mixed-citation shape; Unicode-dash author/title folding;
the SAME_WORK threshold reached via dash-only difference; three regression guards
staying HIGH; and the full `reband_from_cache` path (join, both fixes, v3
preserved, v2/v3 refused, PMID-only fallback, ambiguous/unmatched drops, the
present-but-unmatched-`src_pmcid` never-mis-join guarantee, nested-`rec`
reconstruction, and the >50%-empty-resolved-title pre-write abort). Full `cre.f1`
suite green except the 5 pre-existing `anthropic`-SDK import failures in
`test_live_paths.py` (environment-only; unrelated).

An adversarial multi-agent review of this diff surfaced one real defect — the
PMID-only join fallback fired on a present-but-unmatched `src_pmcid`, which could
silently re-join a definitely-sourced cache line to a *different* source paper.
Fixed (`reband_from_cache` now gates the fallback on `not src_pmcid`) and covered
by the two never-mis-join tests above.
