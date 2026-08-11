# F2 — title-normalization and reference-title extraction defects — implementation spec

**Written for:** an implementing model (Codex / GPT) working in `astonliu/citation-repair-engine`.
**Diagnosed by:** Claude (Opus), 2026-08-11, against `c621a09c8aca6b7bf756f802f74c58463513e529`
on `feat/f2-matcher-revision`, reading the source and the source JATS, not inferring from behaviour.
**Reported by:** ZD (Aston Liu), from live rows in the seed-41 flagged pool.

---

## READ THIS FIRST — the frozen-code cost

`c621a09` is the frozen `CODE_COMMIT` for seed 41. Seed 41 has already been **drawn and scored**:
1,449 papers, 49,423 rows, 67 flagged, denominator 46,560, provenance
`f2_seed41_seed41_01_PROVENANCE.json`. Per `docs/F2_SEED41_DRAW_SPEC.md`, **any rule, threshold,
taxonomy or reason-code change after the draw spends seed 41** and requires a further seed (43).

ZD has decided to fix. That decision is recorded, not re-litigated here. **The implementing model must
not treat "seed 41 is already spent" as licence to widen scope** — the cost is paid once and only for
the defects named below.

---

## Objective

Fix two **confirmed** reference-title extraction defects, and **first measure** why a set of rows with
an identical DOI and an already-passing title similarity failed to clear through the existing
`shared_doi_same_work` route.

### Census of all 67 seed-41 flagged rows (mechanical signature, no labels)

| signature | rows | identical DOI | median `title_sim` |
|---|---:|---:|---:|
| A punctuation/spacing-only title difference | 4 | 4 | **0.963** |
| B `<collab>` holds the title's leading clause | 1 | 1 | 1.000 |
| C resolved title present in `raw`, mis-extracted | 13 | 12 | 0.807 |
| D identical DOI, genuine title difference | 16 | 16 | 0.892 |
| E DOI present on both sides and **different** | 21 | 0 | 0.785 |
| F no written DOI at all | 12 | 0 | 0.881 |

Only **10 of 67** rows have `title_sim >= 0.92`. Classes E and F (33 rows) are untouched by anything in
this spec.

### The correction that matters — read before writing code

An earlier draft of this spec claimed a single cascade: that normalization defects pushed
`title_similarity` under `DOI_SAME_WORK_TITLE_MIN` (0.92) and so disabled RULE A
(`shared_doi_same_work`) in `work_identity.assess_same_work`. **The census refutes that for class A.**
Those 4 rows have `title_sim` 0.963 — already above the floor — with an identical DOI, matching first
author, matching year and matching first page. RULE A's stated conditions appear satisfied, and the row
still banded `review_wrong_paper`.

The call site is `cre/f1/biblio_match.py:748`:

```python
identity = assess_same_work(claimed, cand, title_similarity=m.title_sim)
...
if identity.same_work:          # line ~797, AFTER the clean-match early return
    return VERDICT_SAME_WORK_VARIANT, m
```

So `identity.same_work` was False. Candidate explanations, none verified:

1. `first_author_equivalent(claimed, resolved)` returns False on these pairs.
2. `_series_conflict(claimed.title, resolved.title)` fires.
3. `m.title_sim` at **call time** differs from the `title_sim` written into the artifact (reranking or
   title excision mutating `m` afterwards), so RULE A saw a lower value than the census reads.
4. `claimed.claimed_doi` is empty at match time even though the artifact records a `written_doi`.

**This was not resolved by reading.** It cannot be: this diagnosis was produced in a sandbox with no
network and no runnable `.venv_cre`, so no CRE code was executed. **Step 1 below is to measure it, not
to guess.** Do not implement a normalizer change on the strength of the refuted cascade story.

## Change / defect

### STEP 1 (do this first, change nothing) — instrument RULE A on the class-A rows

For each of the 4 class-A rows, print at the `biblio_match.py:748` call site: `claimed.claimed_doi`,
`cand.doi`, `doi_equivalent(...)`, `first_author_equivalent(...)`, `m.title_sim` **at call time**,
`_series_conflict(...)`, and `identity.blocked_by`. Report which conjunct is False.

**Report that before writing any fix.** If the blocker is a data or ordering problem rather than
normalization, Defects 1 and 2 below may be irrelevant to these rows — in which case say so and stop,
rather than changing the normalizer because this spec listed it.

Class-A rows are identifiable as: identical normalized DOI, and written/resolved titles that are equal
after removing every non-alphanumeric character but unequal after token normalization.


### Defect 1 (CONFIRMED asymmetry; relevance to the flagged rows UNPROVEN) — punctuation maps to a SPACE, so `I.v.` != `Iv`

`cre/f1/biblio_match.py`, `normalize_title` (lines 169-172) and the identical policy in
`cre/f1/work_identity.py`, `canonical_title` (lines 158-159):

```python
s = re.sub(r"(?<=\w)-(?=\w)", "", s)   # intra-token hyphen DELETED
s = re.sub(r"[^\w\s]", " ", s)         # every other punctuation -> SPACE
```

A period between letters becomes a space while a hyphen between letters is deleted. So:

| side | raw | normalized |
|---|---|---|
| resolved | `I.v. fentanyl decreases the clearance of midazolam.` | `i v fentanyl decreases the clearance of midazolam` |
| written | `Iv fentanyl decreases the clearance of midazolam` | `iv fentanyl decreases the clearance of midazolam` |

`i v` vs `iv` costs enough trigram overlap to fall under 0.92. Same mechanism on
`Pharmacokinetics of fentanyl during constant rate i.v. infusion` vs `... constant rate iv infusion`.

### Defect 2 (CONFIRMED asymmetry; relevance UNPROVEN) — the hyphen rule is asymmetric at a token boundary

Same two functions. `(?<=\w)-(?=\w)` requires word characters on **both** sides, so a trailing hyphen
before a space is left to the `[^\w\s]` rule and becomes a space:

| side | raw | normalized |
|---|---|---|
| written | `Nipple-and Areola-sparing Mastectomy...` | `nippleand areolasparing mastectomy...` |
| resolved | `Nipple- and areola-sparing mastectomy...` | `nipple and areolasparing mastectomy...` |

`nippleand` vs `nipple and`. Same work, sub-0.92 similarity.

**Required behaviour for Defects 1 and 2:** two titles that are equal after removing **all**
non-alphanumeric characters must not be separated by the normalizer. Add a punctuation-insensitive
comparison form and use it where title similarity is computed; do **not** silently redefine
`normalize_title`'s output for every other consumer without checking them — `normalize_title` feeds
`title_sim`, the 0.92 `SAME_WORK_TITLE_SIM_MIN` gate, `eval_report`, and the committed test suite.
**State which approach you chose and why, and report the measured effect on the full test suite.**

### Defect 3 (CONFIRMED from source JATS; 13 rows) — `parse_pmc_xml` takes the FIRST of several `<article-title>` elements

`cre/f1/parser.py` line ~296:

```python
raw_title = _text(_first(cit, "article-title", "part-title", "chapter-title"))
```

Publishers emit malformed citations with **two** `<article-title>` siblings. Verified source JATS,
`PMC10467347` ref `cit0002` (fetched from the pinned corpus):

```xml
<element-citation publication-type="journal">
  <person-group person-group-type="author"><collab>GBD</collab></person-group>
  <article-title>Tobacco Collaborators</article-title>
  <article-title>2015 Smoking prevalence and attributable disease burden in 195 countries
    and territories, 1990-2015: a systematic analysis from the Global Burden of Disease
    Study 2015</article-title>
  <source>Lancet</source><year>2017</year><volume>389</volume>
  <fpage>1885</fpage><lpage>1906</lpage>
  <pub-id pub-id-type="doi">10.1016/S0140-6736(17)30819-X</pub-id>
  <pub-id pub-id-type="pmid">28390697</pub-id>
</element-citation>
```

`_first` returns `Tobacco Collaborators`; the real title is discarded. Resolved title is
`Smoking prevalence and attributable disease burden in 195 countries and territories, 1990-2015: a
systematic analysis from the Global Burden of Disease Study 2015.` — an exact match to the **second**
element.

### Defect 4 (CONFIRMED from source JATS; 1 row) — a `<collab>` carrying the title's leading clause

Verified source JATS, `PMC10033911` ref `CR25`:

```xml
<person-group person-group-type="author">
  <collab>World Medical Association Declaration of Helsinki</collab></person-group>
<article-title>Ethical principles for medical research involving human subjects</article-title>
```

MEDLINE title: `World Medical Association Declaration of Helsinki: ethical principles for medical
research involving human subjects.` — exactly `collab` + `": "` + `article-title`. The publisher split
one title across two elements. Required behaviour: when a `<collab>` is present, the concatenation
`collab: article-title` must be available as an **additional** written-title candidate, and the best
candidate used for similarity. The existing `collab` behaviour in `_authors_from` must not change —
corporate-author identity rules depend on it.

---

## Acceptance matrix

Every row below is a **live seed-41 flagged row**; use each as a regression fixture. `HIGH` artifact:
`f2_seed41_seed41_01_HIGH.jsonl`, SHA-256
`198fe1fc9f59417316fa4248b2b3ed0d3dda28540bd588dd7b413ded620c557a`.

| Fixture | Defect | Field | Expected after fix |
|---|---|---|---|
| `PMC10467347:cit0002` (PMID 28390697) | 3 | extracted title | the `Smoking prevalence...` element, not `Tobacco Collaborators` |
| `PMC10467347:cit0002` | 3 | verdict | `review_same_work_variant`, reason `shared_doi_same_work` |
| `PMC10033911:CR25` (PMID 24141714) | 4 | title candidates | includes `World Medical Association Declaration of Helsinki: Ethical principles...` |
| `PMC10033911:CR25` | 4 | verdict | `review_same_work_variant`, reason `shared_doi_same_work` |
| `Iv fentanyl...` vs `I.v. fentanyl...` (PMID 9496205) | 1 | title_sim | >= 0.92 |
| `...constant rate iv infusion...` vs `...constant rate i.v. infusion...` (PMID 3756054) | 1 | verdict | `match` — CORRECTED 2026-08-11; see the note below |
| `Nipple-and Areola-sparing` vs `Nipple- and areola-sparing` (PMID 27898991) | 2 | title_sim | >= 0.92 |
| `normalize_title("I.v. fentanyl")` | 1 | == | `normalize_title("Iv fentanyl")` |
| `normalize_title("Nipple-and Areola")` | 2 | == | `normalize_title("Nipple- and areola")` |
| `canonical_title` on both pairs above | 1,2 | == | equal |
| `journal_equivalent('Blood','Blood Adv')` | — | result | still a documented FALSE match; unchanged |
| `canonical_title("Mn2+ transporter")` | — | vs `Mn2-` | still DIFFERENT (charge-preserving fold intact) |
| `normalize_title("Β1 receptor")` | — | vs `beta1 receptor` | still EQUAL |
| AAP vs AAP Committee on Nutrition, divergent titles | — | verdict | still `review_wrong_paper` |
| `International` vs `Interventional` | — | verdict | still `review_wrong_paper` |
| `Group A` vs `Group AB` | — | verdict | still `review_wrong_paper` |
| Part I vs Part II sharing a run-on DOI | — | verdict | still `review_wrong_paper` (`_series_conflict`) |

**Seed-37 rows that must keep banding `review_wrong_paper`:** `PMC8015328:ref011`,
`PMC11186016:ref55`, `PMC12359113:ref66`, `PMC9494430:ref68`.

**`PMC12733676:B29-jimaging-11-00445` must stay `review_wrong_paper` / `preprint_shape_unconfirmed`**
(DEC-F2-030: §14.3 governs over §15.2; no escape hatch).

---

## Guardrails (do NOT change)

- **Recall is non-negotiable in the matcher.** F2 is precision-only in *evaluation*; never tighten a
  gate so genuine F2 rows leave the measured population.
- **Do not move any threshold.** `SAME_WORK_TITLE_SIM_MIN` stays `0.92`; `DOI_SAME_WORK_TITLE_MIN`
  stays `0.92`. The fix is to compute the similarity correctly, not to lower the bar.
- **Do not add a reason code** and do not bump `REASON_REGISTRY_VERSION` (currently `"5.3"`, 25
  same-work reasons: 20 `work_identity` + 5 `biblio_match`). These rows must clear through the
  **existing** `shared_doi_same_work`.
- **Do not weaken** `reband_from_cache` or `index_claimed_from_xml_dir` scoping semantics. The
  evaluation frame comes from the hash-pinned `--selection-manifest`, never from `--xml-dir` contents.
- **Do not prescribe or accept line-level edits inside `flag_verdict`.** Three prior attempts to patch
  that function from outside were inert or made a new branch unreachable; the `:792`/`:806`/`:861`/
  `:889` return ladder, the `has_confident_disagreement` gate and `eval_report`'s exclusion list are
  too coupled. State the defect, then **simulate and report** before editing.
- **Never call `flag_verdict` directly on `unscoreable` rows** — production never routes them there.
- `author_match` / `first_author_match` are **tri-state**; test with `is False`, never a falsy check.
- `SAME_WORK_VARIANT` stays **audited and excluded from both sides** of the scoreable denominator.
- Gold labels are **naturally occurring only** — never hunted, never synthetic, never LLM-generated.
  **Detector output is never gold**, and no model may assign, alter or infer a semantic label.
- Path-based module loading: `cre/` has no `__init__.py` by design. After any push, **restart the
  Colab session** — `importlib.invalidate_caches()` alone does not evict `sys.modules`.
- No-rewrite discipline: targeted amendments only.

## Regression guards

Guard PMIDs that must keep banding as before: `31665581`, `16639420`, `18152150`, `27665045`,
`25750229`, `32355637`, `22926653`. The seven seed-7 PMIDs remain unverifiable and §18.1 already says
so — do not reopen. `journal_equivalent` must keep matching `Antioxidants`/`Antioxidants (Basel)`,
`Agric. Food Chem.`/`J Agric Food Chem`, `Angew. Chem. Int. Ed.`/`Angew Chem Int Ed Engl`.

## Definition of done

- Baseline test count established **before** the change and reported; all `cre/f1` tests pass after.
  (A sandbox without the optional `anthropic` module reports `N-5 passed, 5 failed` — same result as
  `N passed` where it is installed. Not a regression.)
- A strict `xfail` written **first** for each of the four defects, then flipped to passing.
- Acceptance matrix verified row by row, including every "still" row.
- `git diff --check` clean.
- A seed-37 reband at the new commit, reported as a **delta table against the frozen freeze_01 run**
  (23,370 rows / 27 flagged / denominator 22,314, artifact SHA-256
  `708f378238331bce4da18442c36a24c106ee582d806e62066863cfac6c330e5f`). Every moved row named, with
  its before/after verdict and reason. **No row may move silently.**
- Two consecutive rebands byte-identical at the new commit.
- The four defects and their fixtures written into the §24 limitations/register section of
  `docs/F2_MATCHER_REVISION_SPEC.md`.

## Out of scope

- Any threshold, taxonomy, or reason-code change.
- Seed-37 re-analysis or any attempt to rehabilitate it as evaluation data — it stays development data.
- Any precision, recall, sensitivity or F1 figure. Do not compute or quote one; seed 41's labels do not
  exist yet, and seed 37's are development-only.
- Pooling seed 37 and seed 41 into one fraction or denominator — different collection frames.
- `overwhelming_bibliographic_anchor`'s conjuncts (LR-1) and the preprint version rule (LR-4).
- The ~110-row quarantine backlog and `_is_supplement_locator`'s `^\s*[SP]\d` e-page gap.
- Redrawing a seed. That is ZD's call after this lands.

## Verification command

```bash
cd /Users/kamachi/citation-repair-engine/citation_repair_F1_handoff
../.venv_cre/bin/python -m pytest cre/f1 -q                     # baseline BEFORE, then after
git diff --check

# seed-37 reband delta, run from the same directory
../.venv_cre/bin/python -m cre.f1.f2_run_v3 --reband-from-cache \
  --resolved-cache "<DATA>/f2_resolved_cache_seed37_heldout_a0c1060.jsonl" \
  --xml-dir "<DATA>/pmc_oa_xml" \
  --selection-manifest "<DATA>/f2_prospective_selection_seed37.json" \
  --seed 37 --version postfix_01 --out-dir "<OUT>"
```

Exit codes: `2` missing corpus, `3` empty frame, `4` frame-integrity tripwire.

## What to report back

1. The STEP 1 instrumentation result: which conjunct of RULE A is False on the class-A rows.
2. Whether Defects 1 and 2 turned out to be relevant to any flagged row, or are a real but inert
   asymmetry. **An inert fix is a finding, not a failure — say so.**
3. The seed-37 delta table, every moved row named.
4. Whether any row still needs a route that does not exist. **If one does, stop and report. Do not add
   it.**

Classes E (21 rows, DOIs genuinely disagree) and F (12 rows, no DOI) are 33 of the 67 and are not
addressed here. Nothing in this spec should move them.

---

## Blocker attribution — ALL 67 rows, six defect families (added 2026-08-11, after STEP 1)

Produced by replicating the pure predicates from `work_identity.py` at `c621a09`
(`_ROMAN_RE`, `_TITLE_YEAR_RE`, `_EDITION_ORDINAL_RE`, `_DERIVATIVE_RE`, `canonical_title`,
`doi_equivalent`) against `f2_seed41_seed41_01_HIGH.jsonl`. **Replicated, not executed** — the sandbox
has no runnable `.venv_cre`. Confirm each family in-repo before acting on it.

| # | family | rows | identical DOI | `title_sim` >= 0.92 | action |
|---:|---|---:|---:|---:|---|
| 1 | `_series_conflict` roman-token mismatch | 5 | 4 | 5 | FIX — but see the exception below |
| 2 | resolved title present in `raw`, mis-extracted | 9 | 8 | 0 | FIX (Defect 3) |
| 3 | mis-extracted **and** corporate first-author containment | 5 | 5 | 2 | FIX (Defects 3 + 4) |
| 4 | corporate first-author containment only | 3 | 2 | 1 | INVESTIGATE (Defect 4 variant) |
| 5 | `_series_conflict` title-year share-none | 2 | 0 | 1 | DO NOT FIX without evidence |
| 6 | year written into the `volume` field | 1 | 1 | 0 | INVESTIGATE, do not move a gate |
| 7 | `_derivative_block` on the resolved title | 1 | 0 | 0 | DO NOT FIX |
| — | **no offline signature** | **41** | 13 | 1 | out of scope |

**26 of 67 rows carry an identifiable artifact signature. 41 do not** — and only 1 of those 41 has
`title_sim >= 0.92`. Nothing in this spec should move those 41 rows.

### Family 1 contains a row that MUST NOT be fixed away

Of the 5 roman-token rows, 4 are the `i.v.` family (PMIDs 3377942, 3756054, 8329252, 9496205, all
`PMC11860008`). The fifth is **`PMC10179947:B38-molecules-28-03831`**: written title
`...AM1-BCC model: I. Method` versus a resolved `II.` sibling — a **genuine** series conflict, DOIs do
NOT agree, and it must keep banding `review_wrong_paper`. A fix that clears it is wrong.

**Discriminator available without a label:** none of the 5 rows contains a series noun
(`part`, `chapter`, `volume`, `section`, `series`, `edition`, `no`) anywhere in either title. So a
"require a series noun" fix would clear all 5 including the genuine one. The `i.v.` rows are
distinguished instead by the roman tokens arising from **abbreviation punctuation**
(`i.v.` -> `{i, v}` vs `Iv` -> `{iv}`), whereas B38's `I.` and `II.` are section labels that survive
any punctuation policy. Prefer a fix keyed on that distinction and state which you chose.

### Families 5, 6 and 7 are NOT authorized here

Family 5 (title-year share-none) is the rule that correctly separates annual guideline editions —
`Statistics-2017 Update` vs `-2019`. Family 7 is the derivative-publication block. Both are working as
designed on the evidence available; neither has an identical DOI supporting a same-work claim. Family 6
is a single row whose citing paper wrote the year into `<volume>`; note that `volume_match` only feeds
`has_confident_disagreement` **below** `SAME_WORK_TITLE_SIM_MIN`, so it is inert above 0.92 and is not
what banded that row.

**Report any family you decline to fix and why. Declining is a result.**



---

## CORRECTION to the acceptance matrix (2026-08-11) — `match`, not `review_same_work_variant`

The matrix asked for `review_same_work_variant` on the `i.v.` rows. **That was wrong and the
implementer was right to refuse it.** In `flag_verdict` the clean-match early return sits *before* the
identity return:

```python
if not has_confident_disagreement and m.score >= accept and not (
        _physical_location_conjunction(f) and m.title_sim < accept):
    return VERDICT_MATCH, m          # <-- reached first
if identity.same_work:
    return VERDICT_SAME_WORK_VARIANT, m
```

With `m.score` 1.0 against `accept` 0.85 and the series conflict gone, these rows return `match`. The
code comment at that branch already states the intent: *"A GENUINE title match (title_sim >= accept)
with agreeing coordinates is an ordinary same_record and stays `match` (§5.4 step 3) ... Net HIGH
membership is unchanged either way."* Reaching `review_same_work_variant` would have required editing
the return ladder, which this spec prohibits. **Do not "fix" this.**

**Consequence for the seed-41 band:** `match` rows stay in the scoreable denominator, while
`review_same_work_variant` rows are excluded from both sides. So the expected effect is
**HIGH 67 -> 63 with denominator 46,560 unchanged** — not the denominator reduction a quarantine would
have produced.

## Definition of done is NOT yet met

The frozen-artifact footprint audit (zero dotted-`i.v.` candidates among the 23,370 seed-37 rows) is
sound evidence that this predicate cannot move a seed-37 row, and it is the right check to have run in
an environment without the pinned XML corpus. **It is not the reband the spec requires.** Still
outstanding:

1. A seed-37 reband **in Colab** at the new commit, against
   `f2_prospective_selection_seed37.json` / `f2_resolved_cache_seed37_heldout_a0c1060.jsonl`, compared
   to `freeze_01` (23,370 / 27 / 22,314, artifact SHA-256 `708f3782...`).
2. Two consecutive rebands **byte-identical at the new commit**.
3. A seed-41 reband at the new commit, with a new provenance file. The existing
   `f2_seed41_seed41_01_PROVENANCE.json` describes `c621a09` and must not be overwritten.
4. The new commit named and pushed; `CODE_COMMIT` for seed 41 is now superseded.

## Residual roman-token surface — register, do NOT fix

Masking dotted `i.v.`/`v.i.` closes the observed family. Two paths remain open by design, and neither
appears in the current 67:

- **Hyphen boundary.** `_ROMAN_RE` uses `\b`, and a hyphen is a word boundary, so `X-ray` yields
  `{x}` while `Xray` yields `{}`. A conflict needs both sides non-empty and unequal, which is why this
  has not fired — but it is the same class of accident as `i.v.`
- **Abbreviated genus and single-letter initials.** `V. cholerae` yields `{v}`; `i.m.`, `i.p.`,
  `i.c.v.`, `b.i.d.` all yield roman tokens once the dots create boundaries.

Record both as limitations. Do not widen the mask on speculation — no row in seed 41 demonstrates them.

---

## DEFECT 4 IS MISDIAGNOSED — STOP, do not implement it (corrected 2026-08-11)

Defect 4 above says the Helsinki family fails because nothing tries
`collab + ": " + article-title` as a title candidate. **That is wrong.** Measured by the implementer
and confirmed against the source: the matcher already de-prefixes the resolved title, `title_sim` is
already **1.0**, and the row is blocked by **`corporate_author_conflict`**
(`work_identity.py:557`), which `flag_verdict:759-761` turns into an unconditional
`VERDICT_WRONG_PAPER` before any title logic is reached.

The mechanism: the publisher put title words inside `<collab>`, so
`_corporate_names_conflict` sees `Declaration` and `Helsinki` as distinctive words the other
organization cannot account for. Per the comment at that rule, the block is deliberately **not** lifted
by a shared DOI alone — that is the design that keeps `National` vs `International Committee for
Pediatric Care` banding wrong-paper on a run-on DOI.

**Consequences:**

1. **A parser title candidate cannot fix this.** Adding one changes nothing on this path.
2. This is a **missing-route finding**, which the spec's own closing rule handles: *stop and report, do
   not add a route.* Implementation of Defect 4 is **suspended pending ZD's decision.**
3. The decision is not mechanical. Lifting the block requires distinguishing "a `<collab>` string that
   is partly a title" from "two genuinely different organizations", and the AAP-vs-AAP-Committee and
   National-vs-International counterexamples must keep banding wrong-paper.
4. Defect 3 (duplicate `<article-title>`) is unaffected and remains implementable.

Fixtures for all of this are in `cre/f1/test_f2_seed41_mirror.py` (revision 2): `test_3a` pins that the
parser is correct, `test_3b` is the strict xfail for the blocked row, and `test_3c` / `test_3d` are the
twins any future route must not break.

## Fixture-file revision log

Revision 1 of `test_f2_seed41_mirror.py` was reviewed by the implementer and had **four fixture bugs**,
all authored by Claude, none requiring a production change:

| case | revision-1 error | revision 2 |
|---|---|---|
| Defect 4 / `2a` | wrong root cause (extraction, not corporate conflict) | re-authored as `3b` with the corrected reason |
| volume case `4` | used titles at `title_sim` 0.9130, **below** the gate, so the volume clause was legitimately active and the case proved nothing | split into `5a` (identical titles, volume inert) and `5b` (strict xfail for the hyphen defect) |
| `6` | asserted wrong-paper because `title_sim` 0.8558 < 0.92; but that floor governs **RULE A only**, and the pair clears via `overwhelming_bibliographic_anchor` at score 1.0 | re-pinned as CLEARED so the gate is never "fixed" to force it |
| `8` | read `match.first_author_match`; the flags live on `match.fields` | corrected |
| `7b` | asserted the wrong answer as `True`, permanently codifying a known false match | asserts the CORRECT answer under strict xfail, so a future repair XPASSes |
| — | no parser-level test existed for Defect 3 at all | `2a` / `2b` / `2c` parse real JATS through `parse_pmc_xml` |

Claude also mis-stated the xfail count as five when revision 1 had two. Revision 2 has **six** strict
xfails and 30 collected cases; verify both rather than taking the number from prose.
