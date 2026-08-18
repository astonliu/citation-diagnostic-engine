# CRE taxonomy audit loop — state register

**Closed 2026-08-18 by ZD.** No further rounds will be run. This table is final. The last two lanes
(F2, F6) were stopped mid-round; both had a fully graded round in hand and nothing was discarded.

**Convergence rule as it stood at close:** a stratum reached `CLEAR` on **two consecutive rounds in
which the three checkers accepted nothing**. A round of only REJECT / DEFER / ASK-ZD counted as clear;
any single LAND reset the count. A dead agent never counted as a clear round. Saturation was advisory
and could not end a stratum. **LAND required unanimity from all three checkers, each at ≥95%
confidence.**

---

## Final status

| stratum | spec | rounds | status | landed |
|---|---|---|---|---|
| **F1** | `F1_FABRICATION_GUARD_SPEC.md` | 8 | **ROUND-CAP — not converged** | 9 (3 distinct) |
| **F2** | `F2_IDENTITY_EVIDENCE_SPEC.md` | 6 | **not converged** | 5 |
| **F3** | `F3_REACHABILITY_SPEC.md` | 3 | **NOT CLEAR — CLEAR withdrawn 2026-08-18** | 1 |
| **F4** | `F4_SCOPE_AND_VISIBILITY_SPEC.md` | 4 | **NOT CLEAR — clear-streak withdrawn 2026-08-18** | 3 |
| **F5** | `F5_HONESTY_SPEC.md` | 1 | **SKIPPED by ZD** — infrastructure failures | 3 |
| **F6** | `F6_SUPPRESSION_FIX_SPEC.md` | 4 | **not converged** | 4 + 1 ASK-ZD |
| **F7** | `F7_ENTITY_IDENTITY_SPEC.md` | 2 | **CLEAR** | 0 |
| **F8** | `F8_ATTESTATION_SPEC.md` | 2 | **CLEAR** | 0 |

**25 findings landed across the taxonomy**, every one unanimous at ≥95% from three checkers who each
re-opened the citation and re-ran the probe. F1's 9 collapse to **3 distinct defects** after dedup across
rounds — rounds 3, 4, 5 and 8 re-filed the same two.

**Two strata converged: F7 and F8.** Both had every round graded by a full triad. Neither can fire in
the production configuration, which is why neither landed anything — that fact is the headline of both
specs.

---

## The F3 correction, recorded because it matters more than the verdict

F3 was declared **CLEAR on 2026-08-17 and that was wrong.** Three round-2 findings had never been
graded — their checker triad died on a session limit — and F3 completed its clear-streak *around*
them. Graded on 2026-08-18: **1 LAND, 1 DEFER, 1 REJECT.** The LAND resets the streak, so F3 did not
converge and the verdict was withdrawn.

**The rule had a hole.** A dead checker round correctly failed to count as clear, but it did not stop
later rounds completing the count around it. Corrected in the driver:

```js
: unadj.length > 0 ? 'INCOMPLETE-UNADJUDICATED-FINDINGS'
```

**A stratum can no longer reach CLEAR while any finding is unadjudicated.** F3 was the only stratum
this affected; F7 and F8 had every round fully graded.

---

## What "not converged" means here, and what it does not

It means the loop was stopped by decision, not by exhaustion — **not** that those strata are unsound
or that their findings are provisional. Every landed finding in every spec cleared the same bar:
reproduced by execution, cited to a line the auditor opened, and unanimously accepted by three
independent checkers each at ≥95% confidence, each of whom re-opened the citation and re-ran the probe
themselves.

**What is genuinely unfinished** is coverage: F1, F2, F4 and F6 were still surfacing new defects when
the loop closed, at a declining rate. Their auditors were asked five times whether the surface was
worked out and refused to stop every time, each refusal citing code opened on that call and naming
specific files never read. Those files are named in the specs.

**The single largest open item is F6's ASK-ZD**, and it is not in any of the four "still surfacing"
strata's fix lists: `parser.py:190`'s sentence regex does not tile its input, measured to drop text in
**16.5% of paragraphs** of PMC13295119 — the article `cocitation.py:11` names as the source of the
`100/124 = 80.6%` F6 figure. Three checkers reproduced it; the cost checker declined to LAND it because
acting on it moves a published figure, changes what an F6 label refers to, and moves `parser.py`, which
is governed. **It is ZD's decision, and it is in `F6_SUPPRESSION_FIX_SPEC.md`.**

---

## Round log

_Historical. Statuses above supersede any status recorded here._

- **F1** r1: 12 → 4 LAND · r2: 3 → 1 LAND · r3: 4 → 3 LAND · r4: 3 → 2 LAND · r5: 3 → 1 LAND ·
  r6: 1 → 0 LAND · r7: unadjudicated · r8: 1 → 1 LAND, round cap
- **F2** r1: 4 → 2 LAND · r3: 2 → 0 LAND · r4: 2 → 1 LAND · r5: 2 → 1 LAND · r6: 1 → 1 LAND
- **F3** r1: 4 → 0 LAND · r2: 3 → 1 LAND (backfilled 2026-08-18) · r3: 2 → 0 LAND
- **F4** r1: 3 → 2 LAND · r2: 3 → 0 LAND · r3: 1 → 1 LAND (backfilled 2026-08-18) · r4: 1 → 0 LAND
- **F5** r1: 5 → 3 LAND · skipped thereafter
- **F6** r1: 7 → 2 LAND · r2: 4 → 2 LAND · r3: 1 → 0 LAND (split REJECT/LAND/DEFER) ·
  r4: 2 → 0 LAND, **1 ASK-ZD at 96/99/93** — `parser.py:190` sentence segmentation
- **F7** r1: 4 → 0 LAND · r2: 2 → 0 LAND → **CLEAR**
- **F8** r1: 4 → 0 LAND · r2: 2 → 0 LAND → **CLEAR**
