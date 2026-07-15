# F2 state — identity redesign and prospective validation

Working-state note for the F2 (wrong-paper) precision pass. Branch:
`feat/f2-final-revision`. Module: `cre/f1/`.

## 2026-07-14 — seed-29 prospective result + wrong-paper-precision redesign

### Frozen prospective result (seed 29)

The first prospective seed (seed 29) produced **16 HIGH rows**. Human
adjudication: **9 genuine wrong-paper, 7 false positives**. The frozen
prospective HIGH precision was therefore **9/16 = 0.5625** — recorded here as the
honest held-out number for that frozen code.

- **Seed 29 is now BURNED DEVELOPMENT DATA.** Its 16 rows drove this redesign and
  are embedded as regression fixtures. No seed-29 replay after the fix may be
  described as held-out or prospective — any post-fix seed-29 score is
  **development-only**.
- **Seed 31 is the preserved BLINDED HOLDOUT.** It was drawn before this redesign
  (checkpoint `f2_seed31_blind_checkpoint_before_redesign.json`, selection hash
  `7fcb276d…`) and must not be inspected, generated, or scored until the revised
  code is frozen. Only after freezing may seed 31 test the revised engine, and
  the >0.8 precision target may be claimed only after seed 31 is completed and
  adjudicated.

### Root causes of the seven false positives

Each FP was a version-family / parser-artifact of the SAME work that the engine
lacked an evidence rule for, so a confident field disagreement (usually a year
drift) banded it `review_wrong_paper`:

1. `26297790` — historical republication ("Pillars Article:" reprint of a 1957
   paper in 2015, different journal/year).
2. `17261567` — conference/supplement abstract (`J Sci Med Sport` vol 12, page
   S59) resolving to the later full paper; **DOI matches exactly**.
3. `33624016` — effectively identical work with spelling typos (aoplipo/apolipo)
   and print-vs-online year drift; **DOI matches**; was additionally vetoed by
   the derivative-publication block because both titles legitimately say
   "meta-analysis".
4. `33551622`, `33244148` — supplement/poster abstracts (pages S39-S40 / P1025)
   resolving to the full publications of the same studies.
5. `12500577` — Russian translation: bracketed PubMed title, `language=rus`, and
   both the venue (Biophysics/Biofizika) and first author
   (Yurkevich/Iurkevich) differ only by transliteration, so the standard
   journal/author corroboration missed it.
6. `15129193` — shifted-field parser artifact: the article title leaked into the
   author slot and a tail fragment sat in the title slot; journal+volume+year all
   corroborate the same MMWR work.

### The fix — five general, auditable same-work rules (no PMID/title memorization)

Added to `work_identity.assess_same_work` (consumed by `biblio_match.flag_verdict`;
all route to `review_same_work_variant`, never an auto-clear). Each rule was then
**hardened by a two-round adversarial multi-agent review** that executed crafted
realistic citations against the real engine; the hardening guards below are the
ones that survived, and every confirmed break is pinned as a regression test:

- **RULE A `shared_doi_same_work`** — exact shared DOI + first-author POSITION
  match + `title_sim ≥ 0.92` + NOT a series/edition conflict. Runs BEFORE the
  derivative block so a DOI-proven same work overrides a *derivative-review* veto
  (`33624016` — both titles say "meta-analysis"). Hardening: the near-identical
  floor (0.92) rejects a common-surname run-on collision ("Wang L" sepsis vs
  "Wang Y" AKI sharing a mis-attached DOI, ts 0.90); `_series_conflict` now flags
  BOTH Roman-numeral (Part I vs Part II) AND embedded-arabic-year serial editions
  (AHA Statistics 2017 vs 2019; ADA Standards 2019 vs 2021) so a cross-edition DOI
  mis-attach stays wrong-paper. Excludes the DOI-matching genuine F2s `14741909`,
  `34249371`, `33036834` (author disagreement / empty author). Fixes `33624016`.
- **RULE B `conference_abstract_publication`** — supplement/poster page locator
  (`^[SP]\d`) + **≥6 distinctive claimed-title tokens** + **roster containment
  ≥ 0.75** + **abstract-content coverage ≥ 0.77** + `title_sim ≥ 0.87`. This is
  the hardest class (an abstract→full is metadata-near-identical to two sibling
  trials by an overlapping team), so it took three adversarial rounds: roster
  containment (not bare overlap) drops trials sharing only serial co-authors
  (rivaroxaban, DAPA-HF/CKD, overlap 0.25–0.60); content coverage drops sibling
  trials whose full title lacks the abstract's endpoint qualifier (round-2 DELIVER,
  cov 0.75); the ≥6-token specificity gate drops SHORT generic abstract titles
  ("Dapagliflozin in heart failure") that any sibling trial's title covers
  trivially (round-3 EMPEROR/DELIVER); the 0.87 floor keeps a different endpoint
  out (`33148016`, ts 0.85). Fixes `33551622`, `33244148`, `17261567`.
- **RULE C `historical_republication`** — reprint title prefix (Pillars/Classic/
  Landmark/Seminal Article, Reprinted from, Republished) OR MEDLINE reprint
  publication type, + claimed title CONTAINED in resolved + first-author POSITION
  match + **the resolved title recites the claimed original year AND
  (volume OR first-page)**. Hardening: the recites-original guard keeps a different
  longer-titled paper that merely carries a reprint word out ("Classic Article: X
  in human cancer", "Reprinted from Nature: X in chronic kidney disease"); the
  reprint marker keeps the Zimet containment F2 (`2280326`) out. Fixes `26297790`.
- **RULE D `translated_title_transliterated_author`** — resolved title
  PubMed-**bracketed** + same year + `title_sim ≥ 0.85` + transliterated
  first-author surname + matching volume. Hardening: requiring the bracket (not a
  bare non-English tag) keeps two different same-journal/volume/year Russian papers
  with transliteration-similar surnames out. Fixes `12500577`.
- **RULE E `shifted_author_title_artifact`** — claimed author-slot text is
  title-like (≥20 chars, ≥4 words, non-corporate) and contained in the resolved
  title + **resolved title ≥85% reconstructed from the claimed author-slot ∪
  title-slot tokens** + year within 1 + journal-or-volume agreement. Hardening: the
  coverage guard keeps consortium/cohort authors (ADNI/MESA/TCGA) whose group name
  appears in a *different* paper's title out. Fixes `15129193`.

### Known residual (irreducible, human-review-backed)

Distinguishing a conference abstract from its OWN full publication versus a
DIFFERENT trial by the same serial team is metadata-irreducible when the two
trials have specific, similar titles (e.g. a REDUCE-IT stroke sub-analysis vs a
REDUCE-IT revascularization full paper). RULE B's layered guards close every case
the adversarial review reproduced, but a genuinely ambiguous same-drug
sub-analysis cited as an abstract can still route to `review_same_work_variant`.
That is acceptable because SAME_WORK_VARIANT is **human-reviewed, never
auto-cleared** — a reviewer who sees "stroke abstract" resolving to
"revascularization paper" flags it. No same-work rule can route to
`match`/`cleared`/`correct` (structurally: `flag_verdict` returns those only for
clean no-disagreement pairs, before any same-work rule is consulted).

### Post-fix seed-29 development diagnostic (NOT held-out)

With the revised engine, all **7 label-0 FPs move to `review_same_work_variant`**
(each with its named reason) and all **9 label-1 genuine F2s stay
`review_wrong_paper`**. Development-only HIGH precision on the burned seed is
therefore 9/9 = 1.000 — this is a memorized-fixture diagnostic, **not** a
prospective or held-out number.

`SAME_WORK_TITLE_SIM_MIN` is unchanged at 0.92. The revised code must be frozen
before seed 31 is drawn/scored. Tests: `test_f2_wrong_paper_precision.py` pins all
16 seed-29 rows, PMID-free generic positives + adversarial negatives per rule, the
load-bearing wrong-paper guards (`33148016`, `35523811`, `36844755`, plus the
pre-existing `2280326`/`25750242`/`31643080` fixtures), and a parametrized set of
the adversarial-review recall breaks (series Part I/II, common-surname DOI
collision, sibling trials, reprint-word different paper, different foreign paper,
consortium authors, annual editions, generic-title sibling trials) that must stay
wrong-paper. Full `cre/f1` suite green (393 passed) + `git diff --check` clean.
Changed files: `work_identity.py`, `test_f2_wrong_paper_precision.py` (new),
`F2_STATE.md`.

## 2026-07-14 redesign status — development only

> **Seeds 19 and 23 are burned development data.** Their 31 original HIGH rows
> informed the identity redesign. No result from those seeds may be described as
> held-out or prospective in the paper.

Before independent ratification, the post-fix replay moved all 15 suspected
false positives to `review_same_work_variant` / human review and retained the 16
provisionally true-F2 rows as HIGH. The resulting **16/16 = 1.000 (Wilson 95% CI
[0.8064, 1.000])** was only a preliminary development diagnostic.

The full persisted-output rescore surfaced four additional HIGH rows:
`31643080`, `33148016`, `35523811`, and `36844755`. Therefore the operative
development denominator is **20, not 16**. After independent row review, all 20
are ratified as genuine wrong-paper cases. The post-fix development result is
therefore **20/20 = 1.000 (Wilson 95% CI [0.8389, 1.000])**. This remains a
burned-development result and must not be presented as held-out performance.

### Book versus edition-announcement policy (`31643080`)

`31643080` remains `review_wrong_paper` / HIGH. The proposed rule treating an
edition-announcement editorial as a same-target PubMed surrogate was rejected:

- [Cochrane's official citation instructions](https://training.cochrane.org/handbook)
  identify the 2019 Handbook as a separate Higgins/Thomas-edited,
  second-edition Wiley book.
- [PMID `31643080`](https://pubmed.ncbi.nlm.nih.gov/31643080/) is a
  Cumpston-led journal editorial with its own DOI, and its PubMed record cites
  the 2019 Handbook as a separate reference.
- The engine audits document identity, not topical or citation-family
  relatedness. An editorial that names, announces, reviews, or cites a book is
  not the book itself, even when the title, edition year, and some contributors
  overlap.

Accordingly, no book-to-edition-announcement quarantine rule is implemented.
`test_work_identity.py` carries both the exact PMID fixture and a generic guard.
The existing recall invariant is unchanged: genuine ambiguous same-work cases
still go to human review, never to `match` / `cleared` / `correct`.

### Recall-preservation invariant

All 15 rows moved out of HIGH go to a named human-review quarantine; **none may
route to `match`, `cleared`, or `correct`**. This is load-bearing: identity rules
may defer an ambiguous same-work family, but never auto-clear it from the F2
population. The live and offline paths now share this quarantine behavior.

### Prospective protocol

- Seed 29 is the next untouched seed. Because seeds 19/23 shaped the rules, seed
  29 is the first eligible source of a held-out precision number.
- Apply the pre-registered sampling rule unchanged: if pooled fresh HIGH is below
  20, add another untouched seed. At the observed base rate, plan operationally
  for seed 29 plus at least one additional fresh seed rather than assuming one
  seed will yield a defensible interval.
- Freeze code and adjudication instructions before drawing seed 29. Do not tune
  rules on seed 29 and still call it held out.
- Report the 19/23 result only as post-fix development performance and keep it
  separate from the prospective estimate.

### Overfitting stress point

Fresh data must specifically test whether the derivative-publication split
generalizes: `25750242` (Gompertz original versus a later commentary) is a
distinct-work HIGH, while `32187592` (ONETEP corrigendum) is a same-work review
quarantine. Commentary/review/series blockers and correction rules were tuned on
few examples, so success on 19/23 is not evidence of generalization.

### Current regression status

The isolated F2 freeze snapshot passes **313 tests** plus `git diff --check`. A focused
23-assertion guard run confirms:

- `2280326` (Zimet containment) and `25750242` (Gompertz commentary) remain
  `review_wrong_paper` / HIGH.
- `12199786`, `9802808`, and `35264587` remain
  `review_same_work_variant` / human review.
- `10233931`, `12187163`, `16205345`, `20713917`, `25480410`, `31035900`,
  `35737708`, and `36166919` remain `unresolved` with no fabricated scores.

The exact occurrence-aware XML re-band is still required when producing the
next durable run artifact. The 36,798-row check above was a rescore of persisted
records; it is broader than the original 31-row audit but is not a fresh seed.

## Historical v3.1 re-band notes

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
