# F6 — stop the two silent clears — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Severity: CRITICAL.** Two paths turn a **true fault into a clear**. That is precision-first
violated in the one direction the project has always said is unacceptable, and one of the two landed
today in `marker_scope.py`.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

**Do not run a corpus run until Defects 1 and 2 are closed.** Neither is covered by a test.

---

## Defect 1 — the overlay suppresses F6 on a per-reference CONTRADICTION

`cocitation.py:31-33` states, in capitals, *"CONTRADICTION IS PER-REFERENCE AND SURVIVES GROUPING"*,
and `member_route` honours it (`cocitation.py:294-295`).

**The engine does not.** `judgment_engine.py:437-440` filters `own_gaps` by `covered[claim_index]`
with **no contradiction carve-out**. At abstract scope `UNESTABLISHED ⟺ contradicts`
(`coverage_aggregate.py:74-75`), so **every abstract-scope F6 is a contradiction and every one is
suppressible**.

**Verified**, unit and end-to-end:

```
engine, NO overlay  : ('F6',)
engine, WITH overlay: ()   + 'claim coverage attributed to a co-cited reference'

PMC1000:B2 | route: F6_FLAGGED | member_route: F6_FLAGGED
           | disposition: held_cocitation_covered | label: None | emitted_labels: {}
```

The band layer and the run layer now publish **opposite verdicts on identical evidence**.
`judgment_band.py:1464-1466` asserts in prose that contradiction still routes `F6_FLAGGED`; the
manifest a reader actually counts says otherwise. Existing coverage
(`test_cocitation_f6.py:659-673`) exercises the `run_band` route only.

**Required:** the engine must carry the same carve-out the co-citation module already documents. A
sibling covering a claim does not make *this* reference's contradiction of it disappear.

## Defect 2 — `marker_scope` clears a true fault and promotes it into the F3 gate

`parser.link_citances` is first-citance-wins (`parser.py:496`). A reference rendered in **two** marker
clusters of one sentence is recorded in the first only (`:500-501`) and omitted from the second
cluster's `members` (`:547-549`). `marker_scope.scope_item_claims` then marks the second cluster's
claims `not_asked` for it (`marker_scope.py:448-465`).

**Verified**, sentence *"Antibody probes were validated in mice ¹ and micelle probes were validated in
humans ¹,² ."*, where B1's abstract **contradicts** the "humans" claim:

```
SPLIT   : asked ['antibody probes...mice'] | route FULL_COVERAGE | label None
          disposition held_full_coverage_pending_F3_F5_F7
          pairs_skipped_not_asked: 2 | claims_assessed_negative: 0
CONTROL : same claims, one cluster -> route F6_FLAGGED
```

The contradiction is **never put to the model**, and the reference lands on `FULL_COVERAGE` — which
`judgment_band.py:430-431` calls *"the gate into the F3 provenance discriminator"*. **A true fault
becomes a clear and is promoted.**

Partial mitigation that exists: the dropped claim survives in `marker_scope.attribution` with
`disposition: "not_asked"`. Nothing counts it as a risk, and `cluster_members` under-reports the
cluster's real membership.

**Required:** a reference rendered in more than one cluster of a sentence must be a member of **every**
cluster it appears in — or, if that conflicts with first-citance-wins, the sentence must fail closed
to whole-sentence scope for that reference. **Never silently narrow.** Fail-closed is the standing
rule in this module and it was not applied to this case.

## Defect 3 — the freeloader fault is erased on the run path

`cocitation.member_route` returns `ROUTE_UNSUPPORTED_MEMBER` — *"a fault, and deliberately distinct
from F6"* (`cocitation.py:88-90, 296-297`). But `_cocitation_overlay` marks its flags usable
(`judgment_run.py:845-846`), so the engine suppresses F6 and the pair publishes as
`held_cocitation_covered`, **identical to a genuine contributor**. **Verified:**

```
PMC7000:B1 | route: F6_FLAGGED | label: None | disp: held_cocitation_covered
           | member_route: UNSUPPORTED_MEMBER
```

`member_route` survives only inside `rec["cocitation"]`; the disposition, the label and
`manifest["cocitation"]["held_cocitation_covered"]` all conflate the two. **This is acceptance row 4
of `F6_COCITATION_SPEC.md`** — one of the four rows that spec said were the ones that matter — passing
at the band layer and failing at the run layer.

## Defect 4 — cluster-keyed partition deletes co-citation groups

**Verified**, one sentence citing 4 references in 2 clusters:

| manifest field | split | same refs, one cluster |
|---|---|---|
| `denominator_per_citation_group` | **2** | 1 |
| `cocitation_groups` | **2** | 1 |
| `group_size_distribution` | `{"2": 2}` | `{"4": 1}` |
| `denominator_per_citation` | 4 | 4 |

Both group records carry the **same** `citance_group_id`, distinguished only by `marker_scope_id`
(`cocitation.py:344-350`) — so any downstream join or `nunique` on `citance_group_id` is now
ambiguous.

Worse, a 2-reference sentence splitting into 2 clusters becomes two size-1 partitions: **no group
record, no `cocitation` block on either row, overlay does nothing** — while
`rec["citance_group_members"]` (`judgment_run.py:447`) still advertises a 2-member group that no
artifact describes.

This is directionally intended, but it silently shrinks the population the co-citation fix protects.
**Required:** reconcile the record's advertised membership with the artifacts, and make the
before/after populations comparable in the manifest.

## Defect 5 — `claims_assessed_negative` is wrong on both scopes

`_NEGATIVE_BUCKETS = ("coverage_contradicted","coverage_off_topic")` (`marker_scope.py:634`) keys on
bucket **names**, while F6 is driven by `established`. **Verified:**

| case | bucket | abstract est. | fulltext est. | counted negative? |
|---|---|---|---|---|
| contradicts | `coverage_contradicted` | False | False | 1 |
| engaged, specific unconfirmed | `coverage_unconfirmed_specific` | None | **False** | **0 — missed** |
| off topic | `coverage_off_topic` | None | False | **1 — spurious at abstract scope** |

Over-counts at abstract scope, under-counts at full text. The docstring (`:638-644`) promises *"the
answered-and-failed count, printed beside it so the two cannot be conflated"* — it does not deliver
that on either scope. Separately, `claims_asked` (`:624`) counts **scope**, not answers: it includes
rows that took the deterministic no-usable-evidence branch and never reached a model.

## Defect 6 — "F6 = partial support" fires when nothing is supported

`judgment_band.py:420-421` routes `F6_FLAGGED` on **any** `established is False`, and
`judgment_engine.py:441-442` appends `"F6"` on **any** `own_gaps` — neither requires that some claim
*is* supported. `schema.check_f6_invariant` only forbids the opposite direction (`schema.py:75-76`).
**Verified:** all-unestablished → `('F6',)`; a single scoped claim unestablished → `('F6',)`.

`marker_scope` makes this materially more common: a scoped list of **one** claim means every F6 on
that row is really *"supported none of what it was asked"*. `cocitation` already has the right
vocabulary (`ROUTE_UNSUPPORTED_MEMBER`) but only inside groups of >1.

**This is a taxonomy question, not an implementation detail — surface it, do not resolve it.**

## Defect 7 — `EXCLUDED_CLAIMS_DIFFER` is now unreachable

Both production paths cache extraction per sentence (`judgment_band.py:1110-1118`,
`judgment_run.py:474-482`), and `scope_item_claims` is deterministic, so members of one partition
always share a claim list. The guard at `cocitation.py:179-182` can no longer fire.

The cache is correct and wanted. But the flip side is that **a single bad extraction now poisons every
member of the sentence, with no per-member disagreement signal left to detect it.** Record the risk;
do not remove the cache.

---

## What is confirmed working — do not regress it

All **verified** this session: the claim-extraction cache makes exactly **1** extractor call for a
4-reference sentence on **both** `run_band` and `run_natural_judgment`; marker offsets are exact under
tabs, blank lines and multi-space runs; style detection is fail-closed on mixed, empty, symbol-only
and bracketed markers; the scoped claim list is never empty; anchor attribution gets the motivating
`PMC13294812` sentence right and returns ambiguous on a shared predicate; inferred range interiors
land in the right cluster; and `12,13` / `12 and 13` / `[12][13]` all stay one cluster.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| co-cited pair, this reference **contradicts** a claim a sibling covers | label | **F6** — contradiction survives grouping |
| same fixture | band layer vs run layer | agree |
| reference rendered in two clusters of one sentence, contradicting the second cluster's claim | route | **F6_FLAGGED**, not `FULL_COVERAGE` |
| same fixture | F3 gate | **not** reached |
| freeloader (supports nothing, has siblings) | disposition | distinguishable from a genuine contributor |
| 2-reference sentence splitting into 2 clusters | record vs artifacts | `citance_group_members` consistent with the group records |
| engaged claim, unconfirmed specific, full text | `claims_assessed_negative` | counted |
| off-topic claim, abstract scope | `claims_assessed_negative` | **not** counted |
| all claims unestablished | label | per ZD's taxonomy ruling; surfaced either way |
| all 14 rows of `F6_COCITATION_SPEC.md` | run path | pass — **rows 4, 5, 6, 14 called out individually** |

## Guardrails — do NOT change

- **Fail closed.** Any ambiguity reverts to today's whole-sentence, whole-claim-list behaviour.
  Narrowing a reference's accountability on a guess is how Defect 2 happened.
- **Precision-first.** Ambiguity escalates; it never becomes an accusation **and it never becomes a
  silent clear.** The second half is the whole of this spec.
- **`citation_id` / `item_key` stay `"<citing_pmcid>:<ref_id>"`** — Band 1's disposition joins on it.
- **`band_prompts.py` stays byte-identical** — blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`, pinned by `test_band_prompts_blob_oid_is_unchanged`.
- **Keep the per-sentence claim cache.** It is correct.
- **F4 and F7 are per-reference and must be unaffected** — acceptance rows 12 and 13.
- **Claude never assigns semantic labels.**
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.
`test_f6_marker_attribution.py`, `test_cocitation_f6.py`, `test_judgment_engine.py`,
`test_coverage_aggregate.py` — **122 passed** at audit time. The single-marker-cluster fixture stays
byte-identical.

## Definition of done

- Defects 1, 2 and 3 closed, each with a test that fails on today's code.
- Defect 4 reconciled; Defect 5 corrected on both scopes.
- Defect 6 surfaced to ZD, not resolved unilaterally.
- Defect 7 recorded.
- All 14 `F6_COCITATION_SPEC.md` rows verified **on the run path**, not only the band path.
- `band_prompts.py` blob OID verified unchanged and stated.
- Suite green; count old → new, stating the environment.

## Out of scope

- Any corpus run — blocked until Defects 1 and 2 land.
- Resolving the reporting unit (per citation / per group / per cluster). Surface all three.
- The `et al.` sentence-fragmentation defect (`parser.py:181`) — needs its own decision.
- Editing `band_prompts.py`.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```


---

---

# Audit loop — F6, rounds 1–4 (2026-08-18) · **not converged, stopped by decision**

4 rounds. **4 landed** (2 in round 1, 2 in rounds 2–4), 2 deferred, **1 ASK-ZD that is the
most consequential finding in the stratum**. Bar for LAND: all three checkers ≥95%.

## ⚠ READ FIRST

**The ASK-ZD below is not a nuisance item.** It says the string every F6 verdict is a verdict *about* is
cut by a regex that does not tile its input, and it was measured to lose text in **16.5% of paragraphs**
of the very article the module cites for its 80.6% figure. **Read it before costing anything else here.**

---

## L-0a · `ROUTE_UNSUPPORTED_MEMBER` reaches no disposition, no label and no counter — and the standing spec's published symptom is FALSE

**Cite:** `judgment_run.py:775-780` · **Verdict:** LAND · 95/95/96 (round 1) · REPRODUCED

The freeloader route — declared *"a fault"* — lands in the same bucket as a reference whose abstract
could not be retrieved. **What is new is the correction, not the omission.** `F6_SUPPRESSION_FIX_SPEC`
Defect 3 states the freeloader *"publishes as `held_cocitation_covered`, identical to a genuine
contributor"* and prints a trace showing it. **Under the production tri-state judge it publishes as
`held_insufficient_evidence` and never touches that branch at all** — off-topic maps to
`established is None` (`coverage_aggregate.py:76-77`), so `jb.route` returns `ROUTE_HELD` and the
`elif r == jb.ROUTE_F6_FLAGGED and cogroup_covered` guard at `judgment_run.py:758` cannot fire.

**Fix the spec's stated symptom before fixing the code** — an implementer working from Defect 3 is
looking for the wrong disposition.

## L-0b · Every fixture in the F6 acceptance matrix uses a coverage mapping the band path forbids

**Cite:** `test_cocitation_f6.py:75-87` · **Verdict:** LAND · 96/96/97 (round 1) · REPRODUCED

The fixtures are built on the Boolean coverage mapping that DEC-076 and `coverage_aggregate.py:32-36`
forbid on the band path. **Acceptance row 2 — the guarantee that F6 still fires on a solo citation — is
false under production wiring.** The matrix passes 122/122 and proves nothing about the shipped path.

---

## L-1 · `detect_citation_style` fails OPEN on the commonest JATS author-year markup

**Cite:** `marker_scope.py:110-135` (detector) · `:112` (`_NUMERIC_MARKER_RE`) · `:40-48` (the
fail-closed guarantee it breaks) · `:469-480` (`should_record`) · `parser.py:453-455`
**Verdict:** LAND · 96/96/96 · REPRODUCED unit and end-to-end

The whole refusal rests on one premise, stated at `marker_scope.py:42-44`: *"the marker text is itself a
name containing letters."* That is true of one author-year convention. JATS routinely encodes
`Smith et al. (2020)` with the surname in plain prose and **only the year inside `<xref ref-type="bibr">`**.
`parser.py:193-216` records `mtext = _text(child)` — the xref's own text and nothing else — so the
detector sees a bare four-digit year. `_NUMERIC_MARKER_RE` is `^\d+(?:\s*[,-]\s*\d+)*$`; `"2020"` matches;
`all(...)` returns `CITATION_STYLE_NUMERIC` and the letter test at `:133-134` is never reached.

```
'author-year, name IN xref'    -> author-year
'author-year, YEAR-ONLY xref'  -> numeric          <- FAIL OPEN
'author-year, year+letter'     -> author-year      ("2020a")
```

`parser.py:455` then sets `clustering = True` for the whole document and the positional rule — which the
module's own header says is **undefined** for author-year — runs. On a document citing
`(Smith 2020; Jones 2021)` and `(Lee 2022; Park 2023)`:

```
style: numeric      clusters produced: 4      true structure: 2 collective citations
manifest: citation_style_documents={'numeric': 1}  multi_cluster_sentences=1  fallback_reasons={}
```

**Why it matters:** `marker_scope.py:555-558` tells the manifest's reader that author-year documents are
*"counted under `fallback_reasons`"*. `fallback_reasons["citation_style_not_numeric"]` **cannot be
non-zero for the year-only subset** — the named defect class, on a published counter. The durable row and
the manifest both assert the numeric positional rule applied to an author-year paper.

**Scope note, recorded because a checker measured it and it narrows the filed claim:** the counter is
incapable only for the **year-only subset**; it fires correctly for the name-in-xref rendering
(`test_f6_marker_attribution.py:280` asserts this). And reachability on `corpus_frozen_v1` is
**unestablished** — the one live retrieval that succeeded (PMC13295838, named in the module header as
author-year) renders plain numeric superscripts, which points **against** the corpus being affected;
PMC13219232 returned HTTP 404, which is a failed test, not a zero. **The code defect is certain; the
corpus impact is not. Do not re-report the 17/3 style split on the strength of this.**

**Fix direction:** the detector must not conclude "numeric" from marker shape alone. A year-shaped marker
in a document whose prose carries the surnames is the ambiguous case — **fail closed on it**, as the
header promises, rather than falling through to the positional rule.

---

## L-2 · The published `multi_cluster_sentences` rate is biased down by construction

**Cite:** `marker_scope.py:549-562` (the note) · `:566-601` (`tally_document`) · `:586-589` and `:600-601`
(the arithmetic) · `judgment_run.py:1397`
**Verdict:** LAND · 95/96/— · REPRODUCED

`parser.py:455` sets `clustering = style == CITATION_STYLE_NUMERIC`, so for any style-refused document
**every** reference has an empty `citance_marker_clusters`. In `tally_document`, `:586-589` computes
`seen["clusters"] = max(1, clusters or 1)` — **it can never exceed 1** for a refused document — while
`:596` still increments `multi_reference_sentences` for every multi-reference sentence in it.

**A style-refused document can only ever add to the denominator.** `judgment_run.py:1397` calls
`tally_document` once per document with no style filter.

The note at `:549-562` asserts the ratio is *"this run's equivalent of the 76/274 (27.7%)"* baseline —
**but that baseline was numeric-only.** A run whose style detection got *worse* moves this rate in the
same direction as a run where genuinely fewer sentences split. The two causes are indistinguishable.

**Fix direction:** restrict `tally_document`'s denominator to documents the positional rule actually ran
on, or publish the two populations separately. Then the note's comparison is either true or removed.
**Do not re-report 27.7% against the current number.**

---

## ⚠ ASK-ZD · The citing sentence itself is cut by a regex that is not a partition of its input

**Cite:** `parser.py:190` (`_SENT_RE`) · `:219-225` (`_sentence_spans`) · `:227-236` (`_sentence_for`) ·
`:384-394` (`_span_index`) · consequences at `cocitation.py:87-90`, `:196-203`
**Confidence:** LAND 96 · LAND 99 · **ASK-ZD 93** — the cost checker wrote: *"the strongest-mechanism
finding I have reviewed in this stratum … I am ~99% confident the defect is real — I am declining to LAND
it because landing it inside this loop would spend exactly the things my charter exists to protect."*

`_SENT_RE` is `[^.!?]*[.!?]+(?:\s+|$)|[^.!?]+$`. `[^.!?]*` cannot cross a `.`, and after `[.!?]+` the
pattern **requires** whitespace or end-of-string. **Any period not followed by whitespace** — a decimal
(`0.5`), `et al.,`, `e.g.,` — fails the match at every start position at or before it. `_sentence_spans`
collects whatever `finditer` returns with **no assertion that the spans tile the input and no fallback
that reclaims the unmatched head**, so the text before the offending period is *deleted*, not merged. A
marker inside the deleted region is silently re-homed: `_sentence_for` and `_span_index` both fall
through their containment loop and **return the last span**.

Measured on PMC13295119 — the document `cocitation.py:11` names as the source of the
`100/124 = 80.6%` F6 measurement, full text fetched live:

```
paragraphs >= 120 chars: 121
paragraphs where _sentence_spans loses text: 20/121  (16.5%)
characters dropped: 3696/84007 (4.4%)

DROPPED RUN (183 chars) handed to NO reference:
 'SNA with a Ccore and 12 radially arranged DNA strands show 400-1000-fold higher cellular
  uptake efficiency by MCF-7 cells than free DNA after 6 h of treatment at a concentration of 0.'
the span the parser DOES keep:
 '5 μM, as measured by flow cytometry. …'
```

End to end through the real parser, the real tri-state judge and the real co-citation aggregation, on a
**numeric-marker** fixture whose only offence is the decimal:

```
citance -> '5 μM, as measured by flow cytometry 12,13.'
coverage_bucket = coverage_off_topic
PMC…:B1  solo=HELD_LOW_CONFIDENCE  GROUP ROUTE = UNSUPPORTED_MEMBER
PMC…:B2  solo=HELD_LOW_CONFIDENCE  GROUP ROUTE = UNSUPPORTED_MEMBER
```

`cocitation.py:87-90` defines that route as **"The freeloader. A fault."** Two real papers are accused
because the citing sentence contained `0.5`. The worse variant merges references from **two different
sentences** into one co-citation group, so a sibling's coverage of the wrong sentence's claim can set
`cogroup_covered` and suppress a genuine F6.

**Disclosure the checker required, and it is material:** `parser.py:181-189` — the ten lines immediately
above the regex — **already log this defect**, in the package's own words, as *"KNOWN DEFECT, logged not
fixed (found while measuring marker clusters, 2026-08-16)"*, scoped to `et al.` and to the three
author-year documents of `corpus_frozen_v1`. **What is new is the scope**: the same regex fails on
decimals and `e.g.,` in **numeric-marker** documents, which the logged note explicitly excludes, and the
16.5% measurement above is on a numeric-marker corpus article.

### Why this is ZD's call, on four counts

1. **It moves published figures.** `docs/F6_MARKER_ATTRIBUTION_SPEC.md:135` publishes `76 (27.7%)`;
   `cocitation.py:12-14` publishes `100/124 = 80.6%` and `44/98 = 44.9%`. All four numerators and
   denominators are computed over co-citation groups, and the segmenter was **proved by execution** to
   change group membership. Re-deriving them needs a corpus run, which is locked out.
2. **It changes what an F6 label means.** F6 is a relation between a reference and a citing sentence.
   Changing which string *is* the citing sentence changes the referent of every F6 label already
   reported.
3. **Its fix moves `parser.py`, which is in `GOVERNING_MODULES`** (`production_launcher.py:65-70`).
   CONTRADICTIONS 65 is already OPEN because the F1 pass moved `schema.py`'s digest.
4. `sentence_spans.py:84-89` protects `Fig.`, `Dr.` and single-letter initials and guards neither
   `et al.` nor decimals — so the fix belongs there, in a second module, not in the regex alone.

**Recommendation:** treat the diagnostic half as separable and cheap — **assert that `_sentence_spans`
tiles its input and record when it does not.** That moves no verdict, changes no rate, and converts a
silent deletion into a counted one. The segmentation fix itself waits for a decision.

---

## Deferred

| cite | claim | why deferred |
|---|---|---|
| `judgment_run.py:859-894`, `production_launcher.py:64-70` | `cocitation.py` — the module that decides whether F6 is raised — appears in **no** digest block under any wiring, so editing the F6 suppression rule leaves the manifest byte-identical. `coverage_aggregate.py` has the same hole on the abstract path | 82/55/70/(96/96 on backfill) — not unanimous. **Re-raise before any corpus run**: the file itself names this defect class at `judgment_run.py:880-882` and then omits the F6 module |
| `cocitation.py:296` | the freeloader guard is keyed on the OFF_TOPIC **bucket**, not on the tri-state `established`, so an all-`UNCONFIRMED_SPECIFIC` member routes `GROUP_COVERED` — "contributed, NOT a fault" | REJECT 93 / LAND 96 / DEFER 93. **The asymmetry is real and all three checkers reproduced it** (one bucket flip moves the route). It was not landed because **the proposed fix makes the accusation half worse**: keying on "no bucket equal to `BUCKET_ESTABLISHED`" routes abstract-scope `established=None` — *"unknown, NEVER a coverage gap"* — to a fault, over a strictly larger population. A correct fix needs a scope parameter on `member_route`, which changes its signature at two call sites in **two governed modules** |

## Guardrails

- `judgment_run.py`, `judgment_band.py`, `judgment_engine.py`, `parser.py`, `schema.py` are **GOVERNED**.
  CONTRADICTIONS 65 is OPEN. **Report the digest consequence; do not decide it.**
- `band_prompts.py` byte-identical — blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
- Precision-first, both halves — this stratum broke it in **both** directions in one line, twice.
- No invented constants. Specs only — no corpus run.

## Definition of done

- A year-only author-year document is refused, counted under `citation_style_not_numeric`, and says so in
  the durable row and the manifest.
- The `multi_cluster_sentences` rate is either comparable to its stated baseline or the note goes.
- `_sentence_spans` either tiles its input or **records that it did not** — a dropped run is never silent.
- No route named "a fault" is reachable from a segmentation artifact.
- Suite: old → new counts, environment stated.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
