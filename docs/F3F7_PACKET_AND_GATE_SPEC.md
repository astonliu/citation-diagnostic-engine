# F3–F7 — packet builder and gate instrumentation — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16 — run the F2 loop (adjudicate → fix → redraw) for F3–F7.
**Supersedes:** `F3F7_SCOPE_AND_F3_GATE_SPEC.md` Changes 2 and 3. Its Change 1 (structural coverage
pass) is **done** — measured 2026-08-16, `Data/coverage_audit_v1/coverage_report.json`, and closed by
DEC-081. Its adjudication precondition is **withdrawn**: ZD adjudicates inside the loop now.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## Objective

Nothing here changes a verdict. All three items make the loop **reproducible**, so that a precision
figure can be traced to the artifact it was adjudicated against.

---

## Change 1 — `adjudication_packet.py`, committed and tested

**The defect.** No module in either worktree builds
`docs/F3F7_ADJUDICATION_PACKET_PMC13294812.md`. Searched 2026-08-16 across `.py` and `.ipynb`: the
only string hits are prompt/schema text in `judgment_band.py` and `schema.py`. The packet was
throwaway Colab code and is gone, along with the run's own artifacts — the only
`judgment_run_manifest.json` on Drive is `stage4_smoke_PMC13295119`, an abstract-scope
`coverage_v2` run with no cocitation block.

**Consequence, already realised once.** `f4_strength` writes `derived: "F4"`, not the engine's
`WEAKER_STRENGTH`. The lost builder matched on the wrong token, so the document's only F4 never
reached the adjudicator. **A packet that silently omits a flag is worse than no packet — an
adjudicator cannot audit an absence.**

**Required:** a module that reads a run's predictions and manifest and emits the packet.

- Input: the run's predictions JSONL + manifest. **Not** a re-run, and **no model calls.**
- One row per **flagged claim**, carrying: `citation_id`, label, the citing sentence with markers as
  printed, the atomic claim and its index, the cited paper's PMID and title, the engine's reason, the
  evidence spans, and the co-cited siblings with inferred members marked as inferred.
- A **stable row id** per `(citation_id, claim_index)` so an adjudication can be joined back after a
  re-run.
- Header block recording model, effort, evidence scope, prompt versions, code commit, and the run
  manifest's chain tip — the packet must name the run it came from.
- **Every F-label the run emitted appears.** Derive the label set from the run's own
  `emitted_labels` / `seam_status` and **fail loudly** when a label present there produces zero packet
  rows. Do not hardcode a token list; that is the defect.
- Rows with **no evidence span** must be marked as such rather than rendered as an empty section. 17
  of 53 rows in the `PMC13294812` packet had no span (all of them `engages_subject=false`, which
  `coverage_prompts_v3.py:484` requires) and an adjudicator cannot judge those.

**Guardrail, from the existing spec and unchanged:** the packet **must not pre-score or rank rows by
likely correctness**, and must not show the adjudicator a confidence or a proposed answer. That biases
the label. Row order is document order.

## Change 2 — make the F3 gate observable (DEC-079)

**The defect.** F3 fired zero times on `PMC13294812` and **was never assessed** — the provenance
discriminator opens only under FULL support, and no reference reached full support. In the manifest,
"never asked" and "asked, found nothing" are the same absence. DEC-079 exists because that ambiguity
let a zero look like a rate.

**Do not change the gate.** Changing it to make F3 fire manufactures a rate rather than measuring one.
Instrument only.

**Required:**

- Per reference: **F3 not reached** distinct from **F3 assessed and negative**.
- Per document: a count of references that reached the provenance gate at all.
- When zero references reach it, the run says so in its own output rather than leaving a silent
  absence.
- Extend the same treatment to any other discriminator whose gate can go unexercised.

`marker_scope.py` already establishes the vocabulary for this — `not_asked` versus
`claims_assessed_negative`, with the manifest note that one must never be read as the other. **Reuse
that distinction and its wording** rather than inventing a second one.

**This is the third time this project has been bitten by a check that could not fail** — the
tautological queue audit, the `no_llm` branch, and now F3. A path that never ran and a path that ran
and found nothing must never be indistinguishable.

## Change 3 — regression tests for the two silent `c892851` fixes

Both landed. Both were silent. Neither has a guard.

1. **Range-expansion provenance.** `judgment_run.py:453-455` now carries
   `citance_group_inferred_members` and `citance_marker_inferred`. Before the fix the packet reported
   0 inferred where the corpus measurement said 7 — a deduced member presented as a publisher's link,
   which is exactly what the marking exists to prevent.
   **Test: a run whose corpus contains a known unlinked-interior range reports the inferred members,
   and they are distinguishable from asserted ones in the record and in the packet.**
2. **The dropped F4.** **Test: a fixture emitting each of F3/F4/F5/F6/F7 appears in the packet; none
   is silently dropped.** This is the same test Change 1's fail-loudly rule is built to satisfy.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| a completed run's predictions + manifest | packet | built with zero model calls, zero re-run |
| fixture emitting each of F3/F4/F5/F6/F7 | packet | every label appears |
| run whose `emitted_labels` names a label with no packet rows | behavior | **raises**, does not warn |
| flagged claim with `engages_subject=false` | row | marked as no-span, not an empty section |
| any packet | header | model, effort, scope, prompt versions, code commit, chain tip |
| any packet | rows | document order; no score, rank, or proposed answer |
| any packet row | id | stable on `(citation_id, claim_index)` across a re-run |
| corpus with a known unlinked-interior range | record + packet | inferred members present and marked inferred |
| document where no reference reaches provenance | manifest | F3 **not reached**, distinct from assessed-negative |
| document where some reference does reach it | manifest | count of references reaching the gate |
| document where zero reach it | run output | says so explicitly |

---

## Guardrails — do NOT change

- **Do not change the F3 gate.** Instrument it.
- **`band_prompts.py` stays byte-identical** — blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`,
  now pinned by `test_band_prompts_blob_oid_is_unchanged`. Verify and report.
- **Never `band_prompts.make_coverage_judge` on the band path** — `coverage_aggregate` only.
- **The strict parser stays strict.** Quarantine is 0.0% at effort high; relax nothing.
- **Claude never assigns semantic labels**, and the packet must not pre-score or rank rows.
- **Precision-first.** Ambiguity escalates; it never becomes an accusation.
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as before.
Plus the two confirmed F3 cases — compare on banding, not evidence-record shape.
All 14 rows of `test_cocitation_f6.py`, and the `marker_scope` suite added this session, still pass.

## Definition of done

- `adjudication_packet.py` committed, tested, and able to rebuild a packet from a run's artifacts.
- F3 gate observable: reached / not-reached distinguishable, with per-document counts.
- Both `c892851` regression tests present.
- `band_prompts.py` blob OID verified unchanged and stated.
- Suite green; count old → new. Baseline on a machine with `anthropic` and `jsonschema` installed was
  `1956 passed, 1 failed, 12 skipped, 24 xfailed` before the F6 change; without those packages the
  same tree gives `1934 passed, 23 failed`. **State which environment you measured in** — the two
  numbers are not comparable.

## Out of scope

- **Any corpus run.** The first iteration is one document.
- **Changing the F3 gate.**
- **The reporting unit** (per citation / per citation-group / per marker cluster). Surface all three;
  ZD decides.
- **The `et al.` sentence-fragmentation defect** — logged at `parser.py:181`, needs its own decision
  because it changes the citance text everything downstream is judged against.
- **F5 / F7 seam wiring.** F5's `reportable` is hardcoded `False` (`judgment_run.py:820`).
- **The F8 retraction gate.**
- **The replacement annotator codebook.** Gated on ZD's first adjudication pass.

## Verification command

Run from inside `citation_repair_F1_handoff/` with `PYTHONPATH` set to that directory — collecting
from the repo root fails with 46 collection errors, because `cre/` has no `__init__.py`.

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
