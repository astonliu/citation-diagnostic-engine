# F2 branch merge — implementation spec

**Date:** 2026-08-17 · **Author of record:** ZD · **For:** Claude Code, in its own session
**Objective:** one tree that runs F1–F8, with F2's code carried over intact, **without invalidating
74/80 = 0.9250.**

---

## THE GATE — read this first, everything else serves it

**Zero verdict movement on the seed-47 frame, against `d90196a`.**

After the merge, re-run the frame and diff the verdicts row by row against the artifact produced at
`d90196a`. **Byte-identical, or the merge does not land.** Not "suite green" — verdicts.

Precedent that this is achievable: `8cf408f` → `d90196a` was verified at **zero verdict movement
across 59,038 rows**. This is that check, run once more.

**If even one verdict moves:** do not reconcile it away. Stop, name the file and the row, and report.
A moved verdict means the shipped matcher is no longer the measured matcher, and
**`RESERVE_SEEDS = (31, 37, 41, 43, 47)` is EXHAUSTED — there is no seed left to re-measure with.**

---

## Provenance being protected

| what | where |
|---|---|
| seed 47 drawn | `8cf408f` (2026-08-13) |
| rule set finalised, 82 HIGH rows adjudicated, figure measured | `d90196a` (2026-08-14) |
| branch carrying both | `feat/f2-matcher-revision` (HEAD `1473832`) |
| merge target | `feat/f3-f7-semantic-validator-v1` (`c892851`) |

`feat/f2-final-revision` (`a0c1060`, 2026-07-15) is a **frozen ancestor**, not a candidate. It is the
provenance pin for `~/cre-f2`. DEC-024: leave it alone.

**The figure is pinned to a commit, not a branch.** Merging does not erase it. Only a change to the F2
decision path does.

---

## Split — this is the whole job

### Group A — copy whole, do not edit (zero risk to the figure)

`biblio_match.py` · `work_identity.py` · `f2_run_v3.py` · `biblio_rerank.py`

Take the `feat/f2-matcher-revision` version **byte-for-byte**. Nothing in the F3–F7 band imports
these. Verify that claim before copying: grep the F3–F7 modules for each import. If an import exists,
stop and report — it is not in Group A.

Measured divergence (2026-08-17), which is why "just merge" will not work:

| module | `cre-f3f7` | F2 branch |
|---|---|---|
| `biblio_match.py` | 764 | **1594** |
| `work_identity.py` | 971 | **1569** |
| `f2_run_v3.py` | 433 | 642 |
| `unscoreable.py` | 231 | 286 |

### Group B — hand reconciliation, four files, and this is the entire risk

`lookup.py` · `confirm.py` · `decide.py` · `schema.py`

Both branches changed these, in different directions. **The 2026-08-16 F1 transport fix landed on the
F3–F7 side and is absent from the F2 branch**, so the F3–F7 version is larger for F1 reasons while the
F2 version is ahead on F2 reasons:

| module | `cre-f3f7` | F2 branch |
|---|---|---|
| `decide.py` | 222 | 179 |
| `confirm.py` | 220 | 133 |
| `schema.py` | 511 | 425 |
| `lookup.py` | 605 | 567 |

**Reconciliation rule, per hunk:**

- The hunk is F1-only (transport vocabulary, `FETCH_*`, `transport_status`, the quarantine, `f1_status`)
  → **take the F3–F7 side.**
- The hunk is F2-only (`compare_and_flag`'s verdict path, same-work, the override, the F2 denominator)
  → **take the F2 side.**
- The hunk is both → **stop and report.** Do not invent a reconciliation. `compare_and_flag` serves
  both labels and is where this will happen.

`unscoreable.py` sits in Group B in practice — it routes references out of the F2 denominator, so a
wrong take here moves the figure silently. Treat it as Group B, not Group A.

### Group C — do not touch

`band_prompts.py` and everything in `freeze/`. Blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c` is
sealed inside both frozen prompt packages, `semantic_validator_v1.py`, both universe fixtures and
`test_mint_v1.py`'s digest literals. **A prompt change is a re-freeze of at least five committed
artifacts, and it is a decision for ZD, not a merge step.**

---

## Order

1. Scratch branch off `feat/f3-f7-semantic-validator-v1`. Never merge into either source branch.
2. Group A across, whole. Suite must still collect.
3. Group B, one file at a time, committing each separately so a bad take is revertible alone.
4. **Run the gate.** Verdicts vs `d90196a`.
5. Only then, suite.

---

## Definition of done

- Zero verdict movement on the seed-47 frame vs `d90196a`, stated as a row count, not as "no diff".
- Group A files byte-identical to `feat/f2-matcher-revision`. Prove it with a hash per file.
- Every Group B hunk classified F1-only / F2-only / both, in the commit message. Every "both" hunk
  reported, not resolved.
- Suite green, old → new counts, environment stated (`anthropic` and `jsonschema` change the number).
- `band_prompts.py` blob OID unchanged. State it.

## Out of scope

- Changing any F2 rule, threshold or banding behaviour. **No Part B item.**
- Re-running or re-adjudicating seed 47.
- Extending `GOVERNING_MODULES`. **Note: `schema.py` IS governed, and CONTRADICTIONS 65 is already
  open because the F1 pass moved its digest. This merge moves it again — report, do not decide.**
- Any corpus run.
- Rewriting history on either source branch.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

**The suite is not the gate. The gate is the frame.**
