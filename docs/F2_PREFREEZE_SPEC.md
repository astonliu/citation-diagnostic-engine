# F2 — pre-freeze blockers — implementation spec (v3)
**Base:** `feat/f2-matcher-revision` @ `858a22f` (rev 5.2), spec
`aa7a0fb8257ecdcf48be8146cf131226a9fbf4919e55c84d909760d6d81b3417`, 1,492 lines.
**Baseline:** 544 tests collected, 539 pass, 5 `test_live_paths.py` env failures (missing
`anthropic`). Band 33 `review_wrong_paper` / 11 `review_same_work_variant` / 7 `match`.
## Why v3 is written differently
v1 and v2 prescribed exact code edits. Two independent adversarial passes simulated them and found
that **the prescriptions did not do what the acceptance tables claimed**:
- v2's item 3 (corporate-author suppression) is **inert**. Verified: all 5 target rows have
  `first_author_match is False`, so `has_confident_disagreement` is True and they hit
  `if has_confident_disagreement: return VERDICT_WRONG_PAPER` regardless of the block. Additionally
  the `near_identical_title` branch (`biblio_match.py:806`) is gated on `not identity.blocked_by`,
  which v2 explicitly kept set.
- v2's item 2 (title floor) **silently moves a published rate by 0.18** through a denominator v2
  never mentioned. See Decision D1.
- v2's item 2 also **retires F2-C entirely** — measured 0 firings after the floor, because a row with
  `title_sim ≥ 0.85` plus journal (+0.03) and volume/pages (+0.02) reaches `score ≥ 0.90` and returns
  at the `:792` MATCH short-circuit before F2-C is consulted.
- v2's item 4b remedy breaks **23 committed tests** and its own figures are wrong.
- v2's item 4a corrected a number (`771`) that **appears nowhere in the committed spec**.
The coupling in `flag_verdict` — the `:792` / `:806` / `:861` / `:889` return ladder, the
`has_confident_disagreement` gate, `eval_report`'s exclusion list, and 544 committed tests — is too
tight to prescribe blind. So v3 states **verified defects, hard constraints, required measurements,
and the decisions that are ZD's rather than the implementer's.** The exact edit is the implementer's
to determine and to prove by simulation.
---
## Step 0 — concurrency, then branch discipline
**A second Claude Code session is live in this repository.** Observed at spec-writing time:
`.git/index.lock` present with a fresh timestamp, and a third worktree registered at
`/Users/kamachi/cre-f3f7` holding `feat/f3-f7-semantic-validator-v1` @ `0eb7a41`.
Two consequences:
- **Do not force-remove `.git/index.lock`.** If a git write fails with *"Unable to create
  index.lock: File exists"*, another session holds the index. Wait and retry. Deleting it while a
  session is mid-write corrupts the index.
- **The drift hazard is now structurally fixed and the old warning is obsolete.** F3–F7 work has its
  own worktree, so `feat/f3-f7-semantic-validator-v1` can advance without moving this checkout's
  HEAD. Earlier revisions of this spec warned that the repo drifted onto that branch twice; the cause
  was a shared working tree and it no longer applies. Do not switch branches in this directory to do
  F3–F7 work — use `/Users/kamachi/cre-f3f7`.
**Verify the base at read time. Do not trust `858a22f`.** It was HEAD when this spec was written and
a concurrent session may have advanced it.
```bash
cd /Users/kamachi/citation-repair-engine
ls .git/index.lock 2>/dev/null && echo "ANOTHER SESSION IS WRITING -- wait"
git status --porcelain                                     # expect empty
git rev-parse --abbrev-ref HEAD                            # expect feat/f2-matcher-revision
git log --oneline -1                                       # spec assumes 858a22f
sha256sum docs/F2_MATCHER_REVISION_SPEC.md                 # spec assumes aa7a0fb8…
wc -l citation_repair_F1_handoff/cre/f1/biblio_match.py    # spec assumes 1064
python -m pytest cre/f1 -q --collect-only | tail -1        # spec assumes 544 collected
```
**If HEAD, the hash, the line count or the test total differs from the assumptions above, stop and
report the delta before editing anything.** Every measured figure in this spec — the band, the 11
title_sim values, the 5 corporate rows, the `journal_equivalent` results — was taken at `858a22f`.
A moved base does not invalidate the defects, but it does invalidate the numbers, and they are
acceptance criteria.
**Every line reference in this spec is a convenience, not an address.** Anchor edits to content:
| site | content anchor |
|---|---|
| `_physical_location_conjunction` | `def _physical_location_conjunction` |
| corporate early return | `if identity.blocked_by in ("corporate_author_conflict",` |
| MATCH short-circuit | `if not disagree and m.score >= accept and not (` |
| `near_identical_title` gate | the branch testing `not identity.blocked_by` |
| F2-C branch | `m.same_work_reason = "physical_location_same_work"` |
| tail score-accept return | the final `if m.score >= accept:` before `VERDICT_FORMATTING` |
---
## Decisions for ZD — these are not the implementer's to make
### D1 — Does `review_mixed_identity` count in the F2 denominator?  **Blocks everything else.**
**Verified:** `eval_report.py:426 high_band_rate_of_scoreable` excludes `review_same_work_variant`,
`unscoreable` and `unresolved` from **both** numerator and denominator. `review_mixed_identity` is a
new route and appears in no exclusion list, so it would fall into the denominator.
Measured consequence, **on the 51-row band only** — this is not the frame-wide shift:
| configuration | HIGH | denominator | `high_band_rate_of_scoreable` |
|---|---|---|---|
| base | 33 | 40 | **0.8250** |
| after the title floor, mixed_identity **in** denominator | 33 | 51 | **0.6471** |
| after the title floor, mixed_identity **excluded** | 33 | 40 | 0.8250 |
**A pure routing rename moves the band-local rate by 0.18 with zero change in the HIGH count.**
Three qualifications, all verified, so ZD decides on accurate numbers:
- **Band-local, not frame-wide.** `high_band_rate_of_scoreable` is called at `f2_run_v3.py:129`
  over the whole run frame, whose denominator includes every `match` and `review_formatting` row
  across ~23,370 occurrences. The 40-versus-51 denominator exists only on the 51-row artifact. The
  frame-wide shift is **unmeasured** pending the Colab reband — but it is the same sign, and it is
  the number that gets published.
- **Which published figure this governs is inferred, not stated.** `high_band_rate_of_scoreable`
  appears **0 times** in the committed spec (verified by grep). ZD's decision should name the
  reported quantity it feeds, so the choice is recorded against something concrete.
- **The same trap applies to the other two new routes.** `review_related_work` and
  `retrieval_incomplete` are also absent from the exclusion list. Both are non-firing this revision
  (§15.2 unapplied, no live retrieval), so their denominator status is deferred **deliberately** —
  record that, rather than leaving it unnoticed until one of them fires.
The substantive question: a row where pages, volume and journal agree but the title is too weak to
prove same-work is *not established* as either the same work or a different one. §5.1 calls it
"fields or identifiers conflict across possible works." That is an undetermined row, which argues for
exclusion from both sides, like `unscoreable`. But excluding it also means the rule can never
contribute to a reported rate, which is what `review_same_work_variant` already does.
**Required:** ZD chooses in/out, `eval_report.py`'s exclusion list is amended to say so explicitly,
and §16 records the choice with this table.
### D2 — Is F2-C retired, and is that acceptable?
**Verified:** after the title floor, F2-C fires on **0** of 321 readable rows, and
`physical_location_same_work` becomes unemittable in the frozen configuration.
**The traceability instrument is degraded, not extinguished.** Frame-scoped — which is how
`f2_run_v3.py:438` computes it, over all `records` — `proof_rule_quarantined_below_gate` goes from
**29 to 18**, not 11 to 0. The 11 F2-C rows are one of **seven** contributing reasons
(`overwhelming_bibliographic_anchor` 7, `title_stem_same_issue` 5, `authoritative_title_alias` 2,
`preprint_published_version` 2, `translated_title_shared_anchors` 1, `historical_republication` 1).
The 11 → 0 figure holds only on the 51-row artifact. So the question for ZD is whether losing 11 of
29 entries is acceptable, not whether the instrument goes dark.
So enforcing §10 does not "harden" F2-C; it removes it. That may be the correct reading of §10, but
it is a different outcome from what the gap table's "harden" implies, and it leaves the frozen
configuration with no rule producing an auditable physical-location same-work reason.
**Required:** ZD decides whether a retired F2-C is acceptable for the freeze. If yes, §10, §11-style
disabled-rule bookkeeping, and the §5.6 registry's not-emitted list must all record it. If no, the
floor cannot be a hard gate and §10 needs amending instead of the code.
---
## Defect 1 — three of nine §5.1 routes are unimplemented
**Verified:** §5.1 enumerates 9 routes and states *"The matcher emits exactly one route."*
`grep -n "^VERDICT_"` returns 6. Missing: `review_mixed_identity` (7 spec mentions, 0 in code),
`review_related_work`, `retrieval_incomplete`.
### Constraints
Reason codes are named here rather than left to the implementer, because unilateral naming is exactly
what the §5.6 registry test exists to prevent. Verified: none collides with the current registry.
| route | reason code |
|---|---|
| `review_mixed_identity` | `coordinate_agreement_below_title_floor` |
| `review_related_work` | `conference_abstract_related_output` |
| `retrieval_incomplete` | `provider_retrieval_incomplete` |
**§15.2 is not applied this revision.** The 3 conference-abstract rows (`PMC8097933:CR9`,
`PMC9829249:R20`, `PMC12864399:B12` — verified in the band) stay `review_wrong_paper`. Add a §15.2
note that the taxonomy policy is unresolved.
**Registry test impact, corrected.** `test_registry_partitions_cleanly` **fails unconditionally** —
it hardcodes three route buckets and a fourth is required. Extend it, and extend `REASON_ROUTE`
(derived from the buckets) so `test_route_mapping_is_total_and_correct` keeps passing.
`test_registry_equals_emitted_literals_exactly` is **approach-dependent, not doomed**:
`_emitted_reason_literals` scans `same_work_reason = "…"` source literals, so writing a gated-off
emitter — the pattern the repo already uses for `strict_prefix_title` — keeps it green with the code
listed in `NOT_EMITTED_IN_FROZEN_CONFIG`. Prefer that over weakening the equality guarantee.
**`REASON_REGISTRY_VERSION` is `"5.2"` and must be resolved explicitly.** Verified: bumping it breaks
`test_manifest_frozen_config_validates`, which hardcodes `"5.2"`, and makes `validate_manifest`
reject every existing artifact manifest. Not bumping leaves the version lying about its contents.
State the choice and its migration.
### Required measurement
Band unchanged at 33 / 11 / 7. Full suite green after the test amendments.
---
## Defect 2 — F2-C runs without 2 of the 9 conjuncts §10 requires
**File:** `biblio_match.py:686` `_physical_location_conjunction`, consumed at `:861`.
**Verified — §10 requires nine conjuncts.** Implemented: `pages_match ∧ volume_match ∧ journal_match
∧ doi_match is not False`, with `first_author_match` / `year_match` not-False enforced indirectly by
`and not has_confident_disagreement`.
**Verified — two are absent.** `COORDINATE_REVIEW_TITLE_MIN` is `0.85` at spec line 346 and `grep`
finds it nowhere in `cre/f1/`. The code uses `journal_match`, not `journal_match_authoritative`.
**Verified — all 11 firings are below the floor**, and all 11 have `score ≥ 0.85`:
| citation_id | title_sim | score | override |
|---|---|---|---|
| `PMC8683971:jia225853-bib-0031` | 0.8267 | 0.9767 | — |
| `PMC9025524:B9-biomedicines-10-00850` | 0.7991 | 0.9491 | — |
| `PMC10227918:r18` | 0.7912 | 0.8912 | — |
| `PMC10424567:R20` | 0.7891 | 0.9391 | — |
| `PMC8093414:B38` | 0.7292 | 0.8792 | — |
| `PMC10227918:r98` | 0.7273 | 0.8773 | — |
| `PMC10227918:r171` | 0.7136 | 0.8636 | — |
| `PMC10227918:r48` | 0.7019 | 0.8519 | — |
| `PMC11280655:B22-sensors-24-04615` | 0.6963 | 0.8500 | fired |
| `PMC10227918:r138` | 0.6221 | 0.8500 | fired |
| `PMC10424567:R78` | 0.5668 | 0.8500 | fired |
### Constraints
Because every row already clears `accept`, **a floor added to F2-C alone sends all 11 to the tail
`if m.score >= accept: return VERDICT_MATCH`** — verified. That includes `PMC10424567:R78` at
`title_sim` 0.5668, which §10.1 names as a mandatory fixture that *"coordinate normalization must not
silently convert to `match`."*
Any implementation must therefore satisfy:
1. Define `COORDINATE_REVIEW_TITLE_MIN` in code as the single source of truth; §10 references the
   code, not the reverse. (There is no `MATCH_ACCEPT_SCORE` constant in code — verified, it exists
   only in a comment at `:782` and in the spec — so do not site it "beside" one.)
2. **The shared helper `_physical_location_conjunction` is not changed** — the `:792`
   MATCH-short-circuit exception also calls it and its docstring requires the two call sites stay
   identical. **But the F2-C *branch* at `:861` must change.** Verified: leaving that branch as-is
   makes any later mixed-identity branch unreachable, because `:861` returns
   `review_same_work_variant` first — simulated, **0 rows** reach the new route, band stays
   33 / 11 / 7, and `R78` stays quarantined. So the floor goes on the `:861` branch condition, or
   the new branch precedes `:861`. Changing the *function* is forbidden; changing the *branch* is
   required. This is the same failure mode that made v1 and v2 wrong; do not repeat it.
3. `PMC10424567:R78` must end at `review_mixed_identity`, not `match` and not
   `review_same_work_variant`. Regression test required.
4. All 11 rows carry `coordinate_agreement_below_title_floor`.
5. The four guards stay `review_wrong_paper`.
**[ADV]** A branch of the form `plc(f) ∧ ¬has_confident_disagreement ∧ title_sim <
COORDINATE_REVIEW_TITLE_MIN → review_mixed_identity`, combined with the `:861` change in constraint
2, was simulated and satisfies all five: band 33 / 0 / 11 / 7, and the test delta is **exactly** the
two named policy tests with no hidden third break. Confirm rather than assume.
**Two committed tests reverse policy and must be amended, not deleted:**
`test_f2c_same_pages_volume_journal_quarantines_title_divergence` and
`test_item1_physical_location_low_title_is_traced_not_silently_matched`. Both now expect
`review_mixed_identity`. Amending them is a policy change and belongs in the commit message.
### The journal-authority conjunct is already decided — do not reopen
**Verified** committed §8: *"**Implement before F2-C is accepted** — retained as the activation gate;
not required while F2-G is frozen inert, since F2-C is not re-gated on
`journal_match_authoritative` in this revision."*
The defect is narrower than v1 and v2 claimed: **§10's conjunct list and the gap table still state
the requirement unconditionally, contradicting §8 within rev 5.2.** Amend §10 and the gap table to
match §8, and name the exposure in §10's body. Verified against the committed comparator:
```
journal_equivalent('Blood',         'Blood Adv')    -> True     # the spec's own named false match
journal_equivalent('ACS Nano',      'Nanoscale')    -> True
journal_equivalent('Acta Biomater', 'Biomaterials') -> True
journal_equivalent('Acc Chem Res',  'Chemosphere')  -> True
journal_equivalent('Acta Anaesthesiol Scand', 'Anesthesiology') -> True
journal_equivalent('JAMA', 'Journal of the American Medical Association') -> False
```
Cause (`work_identity.py:280`): the `token_match` fallback iterates the **smaller** token list, so a
single-token journal name need only prefix-match any token on the other side. **UNVERIFIED:** the
frame-wide count of zero-shared-token True results — an adversarial pass reported 683 of 896 distinct
strings; six pairs confirmed individually, the total not. Measure before quoting.
### Required measurement
Report the band, the `high_band_rate_of_scoreable` under both D1 options, F2-C's firing count, and
`proof_rule_quarantined_below_gate`. **[ADV]** predicts 33 / 0 / 11 / 7 and F2-C at 0.
---
## Defect 3 — `corporate_author_conflict` forces 5 of 51 HIGH rows
**Files:** `work_identity.py:381` sets `blocked_by`; `biblio_match.py:759` early-returns
`VERDICT_WRONG_PAPER`.
**Verified** by recomputing `assess_same_work` over the band:
| citation_id | title_sim | all five fields True | DOI=T **or** jr+vol+pg+yr all T | first_author_match | written `authors[0]` → resolved |
|---|---|---|---|---|---|
| `PMC13163525:B26-healthcare-14-01146` | 1.0000 | **yes** | yes | False | `World Medical Association (WMA)` → `World Medical Association` |
| `PMC8887078:R1` | 1.0000 | **yes** | yes | False | `Coronaviridae Study Group … Taxonomy of, V` → `… Taxonomy of Viruses` |
| `PMC11291866:eph13581-bib-0004` | 1.0000 | no (`journal_match` False) | yes | False | `Association` → `World Medical Association` |
| `PMC12337699:B18` | 1.0000 | no (`doi_match` None) | yes | False | NCEP panel, `.` for `,`, ± `(NCEP)` |
| `PMC12168542:B1` | 0.6006 | no | yes | False | author slot holds the *title* |
Only **2 of 5** have all five fields True; what holds for all 5 is exact DOI agreement **or**
journal+volume+pages+year all agreeing.
**The logical error:** absence of format-key equality is treated as *affirmative* evidence of two
different organizations. A parenthetical acronym, a truncated trailing token (`Taxonomy of, V`), or
periods where commas belong are not two organizations.
**Verified — the mechanism is not what v1 said.** RULE A (shared DOI) is evaluated **before** the
block (`work_identity.py:667-695`), so the block cannot pre-empt it. RULE A fails on its own terms
because `first_author_equivalent` fails on the corporate strings. Unless normalization is also applied
there, the DOI proof stays permanently unreachable for corporate-author works.
### Constraints — v2's prescription was inert; these are the traps
**Verified: all 5 rows have `first_author_match is False`, so `has_confident_disagreement` is True.**
Suppressing the `:759` early return therefore changes nothing — the rows fall to
`if has_confident_disagreement: return VERDICT_WRONG_PAPER`. Additionally the `near_identical_title`
branch at `:806` is gated on `not identity.blocked_by`.
So a working fix must address the disagreement signal or the `blocked_by` gate, not just the early
return. Either is a design choice with consequences:
- Clearing `blocked_by` for suppressed rows changes `assess_same_work`'s contract and its telemetry.
- Relaxing the `:806` gate widens `near_identical_title` for every row, not just these.
- Making corporate normalization flip `first_author_match` to True has a **verified side effect**:
  **[ADV]** `PMC12337699:B18` then clears to `match`, leaving the review pool and entering the
  denominator. That is a row moving *out* of human review, which needs stating.
**Normalization, corrected.** *"Fold `.` as `,` is folded"* is a **no-op** — verified,
`_corporate_author_format_key` already maps `[^\w\s]` to space. And the prescribed rules do not cover
`PMC11291866:eph13581-bib-0004`: `Association` versus `World Medical Association` is a truncated
**leading** portion, which no stated rule handles. **[ADV]** measured effect of the three prescribed
rules on format-key equality: `B18` ✓, `B26` ✓, `R1` ✓ (needs the trailing-token rule), `bib-0004` ✗.
**Remove the `_CORPORATE_RE` exclusion from `_author_field_holds_title`** (`work_identity.py:588`,
verified by reading it), or document in §13 why a corporate name in the author slot cannot be a
transposed title. **Necessary but not sufficient for `PMC12168542:B1`.** Verified: with the exclusion
removed the function still returns False, because it is an exact substring test and the two strings
differ in spelling and articles — written `…guidelines for the management of the difficult
airway…anaesthesiologists…` against resolved `…guidelines for management of…anesthesiologists…`;
`a0 in canonical_title(resolved.title)` is False. Recognising that row additionally requires
spelling- and article-tolerant containment. The consumer is `shifted_author_title_artifact`
(`work_identity.py:906`), not F2-I, so the prescription is incomplete rather than inert.
**Two committed tests will fail and one is load-bearing:**
`test_corporate_formatting_is_exact_but_different_groups_stay_high` and
`test_corporate_abbreviation_is_a_token_change_and_stays_high`. **[ADV]** the first encodes a genuine
two-organization case — National versus International Committee for Pediatric Care sharing a DOI —
which the change would route out of `review_wrong_paper`. **That is the naturally-occurring
counterexample v2 deferred to Colab as unobtainable. It is obtainable, it is already in the test
suite, and it must not be broken.** Any fix has to keep it `review_wrong_paper`.
### Required measurement
Report the band and, **per citation_id**, the destination and the emitted reason for all 5 rows.
**The field is `same_work_reason`** (`eval_report.py:256`, `:417`), not `route_reason` — verified:
`route_reason` exists in spec §19.1 and in `reason_registry.py` docstrings but is emitted nowhere and
appears in neither readable artifact. If §19.1's `route_reason` is to be introduced, that is its own
change; do not report against a field that does not yet exist.
The reason matters because the candidate remedies produce **different** ones for the same rows.
**[ADV]** the variant simulated yielded `canonical_title_exact` for `bib-0004` and
`overwhelming_bibliographic_anchor` for `B26` and `R1` — not the `shared_doi_same_work` /
`near_identical_title` pair an earlier draft of this spec predicted. Report what actually fires.
Also report whether any row leaves the review pool, and the frame-wide firing count if the Colab
reband has run. **If `PMC12337699:B18` carries a confirmed label, its move to `match` is an
auto-clear, which committed §16.2 makes a merge veto — escalate to ZD rather than reporting it as a
deviation.**
**Do not claim a band target this spec cannot derive.** v2 asserted 29 / 4 / 11 / 7; **[ADV]**
simulation found that unreachable as specified — it requires the `:806` relaxation the spec omits
*and* omitting the normalization the spec requires. Measure and report; do not target.
---
## Defect 4 — stale cached artifacts
**Verified** by recomputing `flag_verdict` at HEAD:
- **HIGH (51 rows): 18 of 51 stored verdicts differ.** Stored are all `review_wrong_paper`, so a
  consumer reading stored verdicts sees 51 / 0 / 0.
- **quarantine (270 rows): 35 flip `review_same_work_variant` → `match`**, losing
  `same_work_reason`; `PMC12733676:B29-jimaging-11-00445` flips to `review_wrong_paper`, a 52nd HIGH
  row. `overwhelming_bibliographic_anchor` recomputes to **128**.
Mechanism: F2-A turns `pages_match` False→True, removing the disagreement and adding +0.02, pushing
rows past `accept` into the `:792` MATCH short-circuit — which precedes `if identity.same_work` and
discards an identity proof already computed. **36 rows are re-routed with no rule named**, violating
rev-5's traceability guarantee via the boost path, which §10.1's exception does not cover.
### Constraint — v2's chosen remedy is not viable as described
**[ADV]** moving the MATCH short-circuit after the identity check breaks **23 further tests**,
including `test_pmid_exact_match_not_flagged`, `test_clean_exact_match_remains_match` and
`test_flag_verdict_match_band`: a cleanly-cited reference satisfies an identity rule and gets
reclassified `review_same_work_variant`, dissolving the `match` class. Its figures were also wrong —
measured quarantine is 269 / 0 / 1, not 270 / 0, and it restores
`overwhelming_bibliographic_anchor` to 158, contradicting the 128 that would otherwise be published.
So the traceability fix is a real design problem, not a one-line move. Options, both needing
measurement: record the discarded reason at the `:792` return without changing the route, or gate the
short-circuit narrowly enough to preserve the `match` class. **Report the test delta and both
quarantine bands before choosing.**
Re-band **both** artifacts before any band figure is quoted anywhere.
---
## Defect 5 — §2.2's unscoreable text (mine, and v2's correction was also wrong)
Committed §2.2 says *"345 rows have no journal, no authors and no title — the parser extracted nothing
at all."* I tested three fields only; the sentence is wrong.
**v2's correction was itself defective.** Verified: `771` appears **nowhere** in the committed spec —
§2.2 reads 752 `no_claimed_title` + 24 + 2 = 778, internally consistent. v2's instruction to "fix
both numbers together" had no referent and risked breaking a valid partition. And 26 + 351 = 377 is
not a partition of 752 or of 778; v2's "~375 residual" mixed denominators.
**Required:** recount from the 778-row artifact on the Drive copy (it is not in the repo and not
readable from the spec author's environment, so this is the implementer's or ZD's measurement).
Report, as a genuine partition with a stated denominator: rows with no fields at all; rows with every
field except the title; rows with some but not all. Then replace the §2.2 sentence with the measured
figures and drop "extracted nothing at all." **[ADV]** reported 26 and 351 for the first two
categories; treat as indicative.
---
## Deferred — not freeze blockers
Line numbers verified where noted. Rows marked **[source unconfirmed]** cite rows absent from both
readable artifacts; locate the source or drop the claim.
- **`_norm_doi` does not fold Unicode dashes** (`work_identity.py:139`) while `_canonical_pages` does.
  **Verified:** `PMC11426526:ppat.1012555.ref016` carries `…4575–4581…`, read as `doi_match is
  False`, verdict `match`, `title_sim` 0.8402. `doi_match is not False` is used with **opposite
  polarity** in two places — protective in F2-C, permissive in the `:792` exception. Note: defect 4's
  remedy would incidentally resolve this symptom.
- **Two F2-A regressions True→False**, missed because the 17-case suite tests only relaxation:
  `1-276` vs `S1-S276` and `p. 32` vs `32`. **[ADV]**
- **`_surname_present` substring rule** (`biblio_match.py:337`, not 360) matches `rovin` inside
  `improving`. **[ADV]**
- **`_title_variants` de-prefixing** admits non-distinctive remainders. **[ADV, source unconfirmed]**
- **The unscoreable bucket contains decidable F2 evidence** — 7 rows with both DOIs present and
  disagreeing. Excluding the population also removes true negatives from the denominator, biasing
  prevalence upward by a parser artifact. **[ADV, source unconfirmed]**
- **Non-200 convention differs** between `resolve_a.py:150` and `biblio_match.py:997`/`:1002` (not
  999), so bucket counts are non-deterministic under rate-limiting. **[ADV]**
- **`mixed_identity_citation` returns `same_work=True`**, routing the canonical mixed-identity F2 out
  of the F2 count. 0 firings. Note the naming collision with the new `review_mixed_identity` route.
- **`has_confident_disagreement` omits `doi_match`; `match_score` penalises only author and year.**
  **[ADV, source unconfirmed]**
## Null results — do not re-litigate
- ~~`overwhelming_bibliographic_anchor` survives adversarial review: exact DOI ∧ year ∧ journal ∧
  volume ∧ first page ∧ `title_sim ≥ 0.85` cannot be satisfied by two different physical articles.~~
  **WITHDRAWN 2026-08-07 (ZD):** the physical-sufficiency argument holds only if the identifiers are
  correctly attached, which is the very F2 fault class the detector exists to find; and in the
  sibling-work regime a high `title_sim` is not protective. See `F2_MATCHER_REVISION_SPEC.md` §24
  (LR-1) for the mechanism, the three executed counterexamples, and the deferred logic change.
- Tri-state discipline is clean; the four falsy checks (`:540-546`) are behaviourally identical to
  `is True` for `Optional[bool]`.
- The empty-frame guard is exactly as claimed — `_write_run:120` raises before any file handle opens.
- `year_from_dep` widening is dead code on all readable data (0 firings, `resolved_year_from_dep`
  False on all 1,099 rows).
- **[ADV]** F2-C fires on 0 of 270 quarantine rows, so defect 2 touches nothing there; no `match` row
  moves into a review route under defects 2–3; all four guards are unaffected by both.
---
## Order
```
D1, D2 (ZD)  ->  Defect 1 (routes)  ->  Defect 2 (floor)  ->  Defect 3 (corporate)  ->  Defect 4 (re-band)  ->  Defect 5 (§2.2 text)
```
D1 first: the denominator decision changes what every subsequent measurement means. Defect 1 before 2:
the 11 rows have nowhere to route without `review_mixed_identity`. Defect 5 in the same commit so the
spec hash moves once.
Guards after each: `PMC8015328:ref011`, `PMC11186016:ref55`, `PMC12359113:ref66`,
`PMC9494430:ref68` stay `review_wrong_paper`.
**The 7 seed-7 guard PMIDs remain unverifiable, and the spec already says so** — committed §18.1
states verbatim that until a seed-7 artifact exists *"the real seed-7 guard is **not verified**"*, and
§17 item 16 carries it as a release requirement. No action needed here; do not re-open it.
The frame-wide reband runs in neither implementation environment. All figures here are measured on 51
of 23,370 rows — 0.2% — as disclosed in §2.2. Closing that is the Colab step, which also owns
defect 3's frame-wide firing count.
## Working alongside the other session
The concurrent session is on F3–F7 in its own worktree, so the branches do not collide. Three
practical rules while both are live:
- **One commit per defect, and push nothing.** Prior F2 work was deliberately left unpushed; keep it
  that way so the other session's state is never affected by a fetch.
- **Do not run `git gc`, `git rebase`, or anything that rewrites history.** The object store is
  shared across all three worktrees. The outstanding task to strip `Co-Authored-By` trailers by
  interactive rebase must wait until no other session is live.
- **Do not touch `/Users/kamachi/cre-f2`.** It pins `a0c1060`, the artifact provenance commit for
  every seed-37 file. It reads as `prunable` from some vantage points because the path is not visible
  there; a host-side `git worktree prune` correctly leaves it alone.
## Report back before landing anything
For each defect: the exact edit made, the band before and after, the `high_band_rate_of_scoreable`
under both D1 options, the test delta with every amended test named and justified, and any row that
changed class in a way this spec did not predict. **A deviation reported is a finding; a deviation
absorbed is a defect.**
