# Acceptance check — commit `25f5890`, branch `merge/f2-into-f3f7`

**Why this exists.** The F1–F8 implementation was reported complete, and one landed item —
RULE G's signal tuple at `work_identity.py:723-726` — had silently not landed. A green suite and a
passing gate did not catch it, because **a fix that landed and a fix that never landed produced the same
report.** That is the exact defect class this audit was hunting, reproduced in the implementation
process itself.

**How to use it.** Answer every box with the evidence, not with a yes. Each `[ ]` gets either a
`file:line` you opened plus the test that fails on the pre-fix code, or the word **NOT LANDED**. Do not
mark a stratum done; mark lines done.

**Known state going in — do not re-derive these:**

- **NOT LANDED:** RULE G still emits a constant `"journal"` signal when only
  `_abbreviated_journal_anchor(...)` succeeded (`work_identity.py:723-726`). Decided: fix it. Zero
  verdict risk — both live rows in the frame have `journal_match: true`.
- **CORRECTLY EXCLUDED:** F2 L-1's frame-changing half. `same_work=True` and the historical verdict
  stand; only the separate conflict disposition and the corrected reviewer wording were added.
- **CORRECTLY EXCLUDED:** F2 L-3(b). `_surname_set` byte-identical to `d90196a`, four-character floor
  intact; only the measurability diagnostic was added.
- **GATE PASSED:** full seed-47 machine frame, 57,459 rows in and out, 0 identity mismatches,
  0 verdict movements, verdict-stream SHA-256 identical on both sides
  (`bdbb0a13…bfe60f7c`). Two rows changed same-band *reason* only, both still
  `review_same_work_variant`.
- **HELD:** `band_prompts.py` blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
- **NOT ENACTED, correctly:** `REPAIR_V1_SPEC.md` — still proposed.

---

## F1

- [ ] Transport failure distinguishable from absence, in the record and in the manifest.
- [ ] No path to F1 that did not gather the evidence it cites.
- [ ] Per-reference exception guard in `run.py`.
- [ ] F1 instrumentation present.
- [ ] Live-NCBI finding on the bracketed-title query reported to ZD.
- [ ] Suite green; count old → new, stating the environment (see the note in

## F2

- [ ] A1–A5 landed.
- [ ] **Seed 47 HIGH frame re-banded and diffed: zero verdict changes, stated explicitly in your report.**
- [ ] Part B untouched, and your report says so.
- [ ] Suite green; count old → new, stating the environment (`anthropic` and `jsonschema` change the

## F3

- [ ] F3 reachable under a stated configuration, **or** the run refuses to run without it.
- [ ] `seam_status.F3` names every gate and cannot report `wired: true` while blocked.
- [ ] Counters present; not-reached distinguishable from assessed-negative.
- [ ] Five distinct block reasons.
- [ ] Reportability check no longer conditional on an F3 having fired.
- [ ] `band_prompts.py` blob OID verified unchanged and stated.
- [ ] Suite green; count old → new, stating the environment.

## F4

- [ ] F4's evidence basis matches the run, or is recorded per verdict and never pooled.
- [ ] Findings-level F4 count published.
- [ ] Consumer contract documented; fixture test keyed on `findings`.
- [ ] `manifest["warning"]` corrected; stale comments fixed.
- [ ] Verifier record honest.
- [ ] `f4` outcome distribution published.
- [ ] Queue can record F4, or the manifest says no F4 figure is obtainable.
- [ ] Suite green; count old → new, stating the environment.

## F5

- [ ] Reportability guard keyed on findings, not `emitted_labels`.
- [ ] XOR guard on the seam pair.
- [ ] Outage distinguishable from absence in the manifest; `negative_reason()` live.
- [ ] Attestation declaration derived from the injected seam.
- [ ] Prompt version validated or reconciled; digest stamped only when rendered; parser version published.
- [ ] Gate string complete.
- [ ] Manifest states F5 has never run on real data.
- [ ] Cosmetic: unused `import os` (`f5_seams.py:30`); the write-only `self.reportable`
- [ ] Suite green and still fast; count old → new, stating the environment.

## F6

- [ ] Defects 1, 2 and 3 closed, each with a test that fails on today's code.
- [ ] Defect 4 reconciled; Defect 5 corrected on both scopes.
- [ ] Defect 6 surfaced to ZD, not resolved unilaterally.
- [ ] Defect 7 recorded.
- [ ] All 14 `F6_COCITATION_SPEC.md` rows verified **on the run path**, not only the band path.
- [ ] `band_prompts.py` blob OID verified unchanged and stated.
- [ ] Suite green; count old → new, stating the environment.

## F7

- [ ] Legacy-path drop closed, with a test that omits `discriminator_call_llm`.
- [ ] Empty-authorities run refuses or declares itself unreachable; lock check precedes the model calls.
- [ ] `canonical_label` cross-check and case-insensitive id compare in place.
- [ ] Section exclusion instrumented.
- [ ] Hold-reason histogram published.
- [ ] `f7.wired` and `seam_status.F7.wired` cannot disagree.
- [ ] Manifest states F7 has no production evidence builder.
- [ ] `band_prompts.py` blob OID verified unchanged and stated.
- [ ] Suite green; count old → new, stating the environment.

## F8

- [ ] Per-check attestation on the disposition contract, with a schema bump if required.
- [ ] Reportability fails when an attestation is absent.
- [ ] `seam_status` covers F1, F2, F8.
- [ ] The F8 counter no longer loses rows to the exclusion ordering.
- [ ] The five date defects in `f5_seams.make_check_formal_notice` fixed or, where they are policy,
- [ ] The manifest states plainly that F8 is not implemented in this package.
- [ ] Suite green; count old → new, stating the environment.

---

## After the boxes

- [ ] Every `NOT LANDED` line has a one-line reason: not implemented, implemented and reverted, or
      out of scope on a constraint. Name which.
- [ ] For each stratum, one test that **fails on the pre-fix code** — a test that passes both before and
      after proves nothing landed.
- [ ] Suite count old → new, environment named (`anthropic` and `jsonschema` change the number).
- [ ] `band_prompts.py` blob OID restated after all edits.
- [ ] No governed-module digest moved without being reported. CONTRADICTIONS 65 is OPEN on
      `schema.py` from the F1 pass — a second move deepens it, and that is ZD's call.

## Do not implement — these are decisions, not tasks

- F2 L-1's frame half — which side of the F2 rate `mixed_identity_citation` rows belong on.
- F2 L-3(b) — `_surname_set`'s four-character floor. Moves verdicts, recalibrates RULE B.
- F2 `_series_conflict`'s year branch — load-bearing for the AHA "Statistics-20XX Update" family.
  Narrowing it wrong converts a visible false accusation into an invisible false clear.
- F6 `parser.py:190` segmentation — changes what an F6 label refers to; needs a corpus run.
- F1 `recording_adapter.py:119-124` — whether the adapter receipt counts non-provider seams.

## Not audited to the same depth — do not present as equivalent

F3 and F4 are **NOT CLEAR** (both clear-streaks withdrawn after backfilled findings landed).
F5 received **one round**. F6's `80.6%` and `27.7%` figures remain **unre-derived** and must not be
quoted.
