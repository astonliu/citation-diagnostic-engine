# Complete the four-file carry, then run the gate — implementation spec

**Date:** 2026-08-17 · **Tree:** `merge/f2-into-f3f7` at `/Users/kamachi/cre-f3f7`
**Why this is first:** it converts "the tree does not import" into a four-file merge, and it is what
unblocks the zero-verdict-movement gate on **74/80 = 0.9250**. That gate is the largest open exposure
in the project.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## The diagnosis (established 2026-08-17, during the F8 pass)

The merge carried these across:

`test_f8_retraction_gate.py` · `preband_disposition.py` · `reason_registry.py` · `f8_seed47_retraction_check.json`

and left behind the four modules they depend on:

`run.py` · `ncbi_meta.py` · `decide.py` · `ratelimit.py`

`ratelimit.py` is why `DATACITE` is undefined. Symptom: **15 failures and 8 collection errors**, none
in an F7 or `judgment_*` file.

---

## ⚠ THE TRAP — do not carry these four wholesale

**`decide.py` and `run.py` hold the 2026-08-16 F1 transport fix**, which exists only on the F3–F7
side. The F2 branch's versions predate it. **Taking the F2 version of either silently reverts landed
F1 work** — the `FETCH_*` vocabulary, `transport_status` propagation, `decide()`'s hold on an
unanswered fetch, the per-reference quarantine, and `f1_status`.

Measured line counts, which is why a wholesale take is visibly wrong:

| module | `cre-f3f7` | F2 branch |
|---|---|---|
| `decide.py` | 222 | 179 |
| `run.py` | 183 | 211 |
| `ncbi_meta.py` | — | — |
| `ratelimit.py` | — | — |

**Per-hunk rule, the same one the merge spec sets:**

- hunk is **F1-only** — transport vocabulary, `FETCH_*`, `transport_status`, the quarantine,
  `f1_status` → **take the F3–F7 side**
- hunk is **F2-only** — `compare_and_flag`'s verdict path, same-work, the override, the F2
  denominator → **take the F2 side**
- hunk is **both** → **stop and report.** Do not invent a reconciliation.

`ratelimit.py` and `ncbi_meta.py` are expected to be additive rather than conflicting — **verify that
rather than assuming it**, and say which you found.

---

## Order

1. Carry `ratelimit.py` and `ncbi_meta.py` first — if they are additive, the tree may import at this
   point and the failure count should drop on its own. Report the count.
2. Carry `decide.py` and `run.py` under the per-hunk rule, **one file per commit**, so a bad take is
   revertible alone.
3. Confirm the F1 fixes survived: `FETCH_*` present in `schema.py`, `transport_status` set on every
   `RetrievedRecord` construction site in `lookup.py`, `decide()` holding on `resolver_error`, the
   quarantine in `run.py`, `f1_status` in `eval_report.py`. **Name each one you checked.**
4. **Then run the gate.**

---

## The gate — the point of all of this

**Zero verdict movement on the seed-47 frame, against `d90196a`.** Diff the verdicts row by row
against the artifact produced at that commit. **Byte-identical, or the merge does not land.**

Precedent that this is achievable: `8cf408f` → `d90196a` was verified at **zero verdict movement
across 59,038 rows**.

**If even one verdict moves:** stop, name the file and the row, and report. Do not reconcile it away.
A moved verdict means the shipped matcher is no longer the matcher that produced 74/80 = 0.9250, and
`RESERVE_SEEDS = (31, 37, 41, 43, 47)` is **EXHAUSTED** — there is no seed left to re-measure with.

---

## Guardrails

- **No F2 banding change. No Part B item.** This is a carry, not a revision. If completing it appears
  to require a rule or threshold change, **stop and report.**
- **`band_prompts.py` byte-identical** — blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`. Confirm
  it after, and state it.
- **`decide.py` and `run.py` are not governed; `schema.py` is.** If the carry touches `schema.py`,
  report the digest consequence — CONTRADICTIONS 65 is already open on four modules.
- **No invented constants.** No corpus run beyond the gate itself.
- **Do not rewrite history on either source branch.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as before.

## Definition of done

- The tree imports; collection errors **0**.
- Failure count reported after step 1 and after step 2, separately.
- Each F1 fix from step 3 named and confirmed present.
- **Gate run, result stated as a row count** — not as "no diff".
- Every "both" hunk reported, not resolved.
- `band_prompts.py` blob OID unchanged. State it.
- Suite: old → new counts, environment stated.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
