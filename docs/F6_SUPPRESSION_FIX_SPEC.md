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
