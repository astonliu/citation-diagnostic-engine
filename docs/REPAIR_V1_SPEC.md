# Repair v1 — evidence-backed repair for F2, F8 and F1 — implementation spec

**Date:** 2026-08-17 · **Author of record:** ZD (Aston Liu) · **For:** Claude Code, in its own session
**Status:** proposed. Nothing here is implemented.

---

## The claim this exists to support

The locked novelty claim is **evidence-backed repair, where prior systems stop at detection or
recommendation** — BibAgent (detection-only), Sarol et al. 2024, Topaz/CITADEL, CiteGuard,
SemanticCite.

**Scope v1 deliberately and defensibly:** repair is implemented for the fault classes where **the
correct answer is uniquely determined by a retrievable structured record** — not by a model's
judgment. That boundary is principled, not a shortcut, and it is what separates F2/F8/F1 from
F3/F4/F5/F6, where the repair target is a generation or a corpus-wide retrieval problem.

**Wording for the manuscript:** *evidence-backed repair, demonstrated on the fault classes whose
correction is determined by a retrievable record — wrong reference, retracted reference, and
non-existent reference resolvable to a near-match; detection extends across the full F1–F8 taxonomy.*

---

## THE RULE THAT GOVERNS EVERY LINE BELOW

**Repair PROPOSES. Repair never applies.** Every output is a human-checkable proposal carrying the
evidence that produced it. This is precision-first carried into the repair layer, and it is also the
answer to the obvious reviewer attack — *"you cannot validate corrections at scale"*. We are not
asserting the fix; we are producing the receipt.

**A repair proposal is never generated for a reference whose fault label is itself held for human
review.** Ambiguity does not become an accusation, and it does not become a proposed edit either.

---

## What gets built

### One output record, all three labels

`RepairProposal`, written alongside the existing prediction record and joined on the existing
`citation_id` / `item_key` = `"<citing_pmcid>:<ref_id>"` — **do not introduce a new key.**

| field | meaning |
|---|---|
| `citation_id` | joins to the prediction record and to Band 1's disposition |
| `fault_label` | `F1` / `F2` / `F8` — the label this repair answers |
| `action` | `replace` · `flag_retracted` · `remove` |
| `proposed_reference` | the corrected record, or `None` for `remove` |
| `evidence` | list of evidence items, each naming its source and what it establishes |
| `confidence` | inherited from the detection, never re-derived |
| `requires_human` | **always `True` in v1** |

### F2 — `action: replace`

The correction is **already computed**. `biblio_match` retrieves and scores candidates; when the
claimed PMID resolves to one work and the claimed metadata identifies another, the proposal is the
matched record.

**Evidence:** the retrieved bibliographic record, plus the field-level agreement vector — `doi_match`,
`author_match` (**tri-state; `None` means unknown, test with `is False`**), title similarity, pages,
year, journal. **Emit the vector as-is. Do not summarise it into a score for the reader.**

### F8 — `action: flag_retracted`

**Evidence:** the PubMed publication type and the `RetractionIn` PMID.

**The data is already flowing.** The 2026-08-17 audit established that the retraction pubtype is
already fetched for every cited PMID on both Band-2 entry points. F8 has its inputs and no logic.
Build the logic; do not re-fetch.

### F1 — `action: remove`, or downgrade to an F2 `replace`

If the confirmation search surfaced a near-match, **the repair is an F2 `replace`, not a removal.**
Only a genuine no-match yields `remove`.

**Evidence:** the three-database search result **with the transport status attached** — an outage row
and a genuinely dead PMID must not produce the same proposal.

**`remove` is the only destructive action in v1 and it demands the highest precision.** It is gated on
the F1 fixes in `F1_FABRICATION_GUARD_SPEC.md` having landed. **Do not build F1 repair before those
land.**

---

## Evaluation — how to get a repair number without spending a seed

**For a confirmed F2, the repair target IS the record the matcher already matched.** So repair
accuracy is measurable on seed 47's adjudicated rows: *of the confirmed F2s, the engine proposed the
correct replacement in M of N.*

**Two conditions, both load-bearing:**

1. **Check first whether adjudication recorded the correct target, or only the F2 / not-F2 label.** If
   only the label, a light pass over the confirmed rows is needed to write down the right reference.
   That is an **annotation extension on an already-spent seed, not a new draw.**
2. **Measure on seed 47. Never tune on it.** `RESERVE_SEEDS = (31, 37, 41, 43, 47)` is **EXHAUSTED**,
   DEC-057A's post-adjudication clause has triggered, and 74/80 = 0.9250 has no replacement. Tuning
   against those rows retires the figure. **Do any tuning on non-seed documents.**

**F8:** repair accuracy is near-definitional — retraction status is objectively checkable — so the
number that matters is **coverage**: how many retracted references are caught.

**F1:** report proposals only. No accuracy claim in v1; the population is too small and the action is
destructive.

---

## Guardrails — do NOT change

- **No F2 banding change. No Part B item.** Repair reads the matcher's output; it does not move a
  verdict, a threshold or a band. If a repair need appears to require one, **stop and report.**
- **`band_prompts.py` stays byte-identical** — blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`,
  sealed inside both frozen prompt packages. New behaviour goes in a **new module**.
- **`citation_id` / `item_key` stay `"<citing_pmcid>:<ref_id>"`.**
- **Claude never assigns semantic labels and never curates ground truth.** Repair proposes a
  *reference*, never a *label*.
- **Never use the detector's own flags as gold.**
- **`schema.py` is a GOVERNED module** and CONTRADICTIONS 65 is OPEN because the F1 pass already moved
  its digest. If `RepairProposal` must live in `schema.py`, **report the digest consequence; do not
  decide it.** Prefer a new module.
- **No invented constants, thresholds or policies.** This spec proposes no number.
- **No `Co-Authored-By` trailers.**

## ⚠ Build the F2 surface against the F2 branch of record

Measured 2026-08-17: `biblio_match.py` is **764 lines** on `feat/f3-f7-semantic-validator-v1` and
**1594** on `feat/f2-matcher-revision`; `work_identity.py` is 971 vs 1569. **The F2 pipeline of record
is `feat/f2-matcher-revision`.** Building F2 repair against the F3–F7 worktree builds it against a
matcher that is not the one seed 47 measured. See `F2_BRANCH_MERGE_SPEC.md`.

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as before —
**repair must not move a single banding decision.** Prove it: run the F2 population before and after
and report the counts, not just a green suite.

## Definition of done

- `RepairProposal` emitted for F2 and F8, joined on the existing key, `requires_human: True` on every
  row.
- Every proposal carries evidence naming its source; no proposal is emitted without one.
- F2 repair accuracy measured on seed 47's confirmed F2 rows, reported as M of N, with a sentence
  stating that the set was used for measurement only.
- F8 coverage reported.
- F2 population unchanged, before vs after, as counts.
- Suite green, old → new counts, environment stated (`anthropic` and `jsonschema` change the number).
- `band_prompts.py` blob OID unchanged. State it.

## Out of scope

- **Repair for F3, F4, F5, F6.** Their targets are generation or corpus-wide retrieval, not lookup.
- **Applying any repair automatically.**
- Re-running or re-adjudicating seed 47.
- Any corpus run.
- Extending `GOVERNING_MODULES` — report, do not decide.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
