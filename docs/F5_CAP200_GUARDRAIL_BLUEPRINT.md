# Blueprint — raise the F5 candidate cap to 200 behind a deep-comparison guardrail

**Written** 2026-08-22 · **Branch** `merge/f2-into-f3f7` (worktree `/Users/kamachi/cre-f3f7`, shared)
**Predecessors** `docs/F5_SCREEN_AND_CACHE_SPEC.md` (the ask) and
`docs/F5_SCREEN_AND_CACHE_RESULTS.md` (what it measured). Read RESULTS §5 first; this turns its
open decision into an implementable change.

---

## 0. What this is for, in one paragraph

`CANDIDATE_CAP` is 50. At 50, F5 misses landmark refuters: PROWESS-SHOCK (`22616830`) is not
retrieved at all for the PROWESS claim, and landmark retrieval recall over a 6-paper probe is
**3/6**. Raising the cap to 200 lifts that to 4/6 and, at 400, 5/6 — but the cap alone multiplies
cost by **4.0x**, because every admissible candidate goes to a deep comparison. This blueprint
raises the cap to **200** and pairs it with **`max_deep_comparisons`** as a hard guardrail, so
retrieval depth quadruples while deep-comparison spend stays bounded. The ordering work that makes
the guardrail safe is already landed and tested. The remaining work is two default values, one
conditional, and a decision about which model runs the screen.

**The cap and the guardrail must move together.** Raising one without the other is the expensive
mistake this document exists to prevent.

---

## 1. The measured basis

All from live runs against real PubMed and the real API on `claude-opus-5`. Marked **measured** or
**projected**; nothing here is assumed.

| quantity | value | basis |
|---|---:|---|
| baseline: cap 50 + screen, no budget (sepsis) | **$1.380** | measured |
| cap 200 + screen + budget 25, opposers first | **$1.787** (1.30x) | measured |
| cap 200 + screen, **no budget** | ~$5.57 (4.0x) | projected from measured per-call cost |
| per deep comparison at cap 200 | **$0.0320** | measured |
| screen at cap 200, Opus 5 | **$0.874** | measured |
| deep-comparison output tokens | 741 / 793 / 836 | measured, 3 fixtures |
| prompt-cache saving on the generator | 26.8–27.6% | measured, 3 fixtures |

**The recall payoff, measured.** At cap 200 with budget 25, PROWESS-SHOCK sits at retrieval
position **189 of 200** — a budget of 25 walked in retrieval order would never reach it. The
screen's `possible_relation` signal moved it to **rank 9**; it was deep-compared and judged
`cited: decrease -> candidate: no_effect`, `relation: opposes`. The claim returned `F5`,
verifier confirmed, confidence **0.90**, with 5 qualifying contradictions against 2 at cap 50.

The first ten candidates the loop walked were all `plausible/opposes`.

---

## 2. Why a call budget is the right guardrail

Three properties, and the third is the one that makes this safe rather than merely cheap.

**It bounds the term that actually grows.** Retrieval is nearly free (cached eutils calls, seconds).
The screen grows linearly but is one call. The deep comparison is ~$0.032 each and is the only
term that scales with the cap. Capping *it* caps the cost.

**It is already implemented and already audited.** `F5Policy.max_deep_comparisons` exists,
`validate_f5_policy` accepts `None` or a non-negative int, skipped candidates are retained with
`reason: "deep_comparison_budget_exhausted"`, `record["budget_exhausted"]` is set, and
`validate_f5_record` enforces that no candidate is skipped before the budget is spent. No new
contract is needed.

**A budget can never manufacture a confident negative.** This is the load-bearing property.
From `f5_controversy_bundle._derive_evidence_profile`:

```python
search_complete = (retrieval_status == "ok"
                   and retrieval_adequacy in {"adequate", "empty"}
                   and budget_exhausted is not True
                   and no candidate was screened clear_mismatch)
```

and `pattern = "unassessable"` whenever `not search_complete`. So an exhausted budget forces the
claim to **hold for review**, never to assert "no later evidence exists". `source_complete` is
independently forced False by any budget-skipped candidate. The guardrail's failure direction is
*more human review*, which is the correct direction — it cannot produce a wrong answer, only an
abstention.

**What the guardrail cannot protect.** A positive finding still depends on a qualifying
contradiction landing inside the budget. That is exactly what the opposers-first ordering exists to
ensure, and it is why the two changes are one change (§3.2).

---

## 3. What is already landed

Both of these are on this branch, tested, with the full suite showing **zero regressions** against
clean HEAD (12 failures, identical set).

### 3.1 The candidate screen (`f5_candidate_screen.py`)

Prompt, strict parser and `make_candidate_screen()` seam factory. One model call for the whole
batch. **Its value is NOT cost.** Measured over 144 admissible candidates it screened out 11.1%
against the spec's assumed ~80%, making it roughly break-even at cap 50. Its value is that it emits
a `possible_relation` per candidate, which is the ranking signal §3.2 consumes. Read
`RESULTS.md` §3 before re-litigating the screen on cost grounds.

### 3.2 Opposers-first deep comparison (`f5_supersession.py`)

The deep loop sorts the screenable set by `possible_relation`
(`opposes` -> `mixed` -> `uncertain` -> `confirms` -> `neutral`), then `plausible` before
`uncertain`, then retrieval index — total, stable, deterministic.

At `max_deep_comparisons=None` this is a **provable no-op**: every candidate is compared whatever
the order, `candidate_assessments` is reassembled by `candidate_index`, and the selected candidate
is chosen order-independently. Asserted by
`test_without_a_budget_the_priority_sort_changes_nothing_observable`.

Every visited candidate now records `deep_comparison_rank`. This exists because
`validate_f5_record` replayed its budget-order invariant over `candidate_assessments` **in list
order** — silently assuming list order *was* spend order. The rank makes the walk explicit and the
validator replays the same strict invariant against it. The check was strengthened, not relaxed.

---

## 4. The work

### 4.1 Choose the guardrail value — this is the whole decision

`search_complete` already requires `adequacy in {adequate, empty}`, and hitting the cap makes
adequacy `inadequate`. **So a claim that fills the cap already cannot support a confident negative —
today, at cap 50.** The budget therefore costs nothing in decidability for capped claims. It costs
something only for claims small enough to be decidable:

| admissible candidates `n` | decidable at cap 50 today | decidable at cap 200 + budget `B` |
|---|---|---|
| `n <= B` | yes | yes — unchanged |
| `B < n <= 49` | **yes** | **no — REGRESSION** |
| `50 <= n < 200` | no (capped) | no (budget-capped) |
| `n >= 200` | no | no — unchanged |

**`B = 50` is the smallest guardrail that regresses nothing**, because it matches the old cap: any
claim decidable today had at most 49 admissible candidates, and a budget of 50 covers all of them.
It also bounds deep-comparison spend at *today's structural worst case* — cap 50 already permitted
up to 49 comparisons per claim.

| option | cost/claim | vs baseline | decidability |
|---|---:|---:|---|
| **B = 25**, Opus screen | **$1.79** measured | 1.30x | regresses the 26–49 band |
| B = 25, Haiku screen | $1.09 projected | **0.79x** | regresses the 26–49 band |
| **B = 50**, Opus screen | $2.59 projected | 1.88x | **no regression** |
| B = 50, Haiku screen | $1.89 projected | 1.37x | **no regression** |

**Recommendation: `B = 50` with the screen moved off Opus (§4.3).** That is 1.37x today's cost for
4x the retrieval depth and no loss of decidability anywhere. If the screen must stay on Opus, `B = 50`
at 1.88x is the honest price of not regressing; `B = 25` is only defensible if you accept that
claims with 26–49 candidates stop yielding confident negatives.

### 4.2 The edits

| file | change |
|---|---|
| `f5_seams.py:75` | `CANDIDATE_CAP = 50` -> `200`. Threads automatically into `retrieval_protocol`, `make_retrieve_superseding_candidates`, `build_f5_seams`, `build_pubmed_f5_runtime`. |
| `f5_supersession.py:271` | `max_deep_comparisons: Optional[int] = None` -> the chosen `B`. **Both, or neither.** |
| `f5_candidate_screen.py:139` | the comment naming `CANDIDATE_CAP` of 50 as the no-truncation case is now false at 200 (budget becomes 1,087 chars/candidate). Correct it. |
| `judgment_run.py:2100` | imports `CANDIDATE_CAP` for the manifest; verify the reported cap follows. |

The bench already exposes `f5_candidate_cap`, `f5_max_deep_comparisons` and `f5_candidate_screen`
per packet, so every option above is testable **without** touching a default.

### 4.3 Move the screen off Opus — the second-order saving

At cap 200 the screen is **$0.874 of $1.787** — it stopped being a saving and became the price of
the ranking. But it is a triage emitting five enum fields per candidate; it adjudicates nothing,
and `clear_mismatch` is the only decision that can avoid a comparison. Repricing the same measured
call (72,170 in / 21,266 out) on Claude Haiku 4.5 at $1/$5 per MTok gives **$0.175**.

This is a **governance decision, not a tuning knob**: `sandbox_wiring`'s "ONE MODEL, TWO
TRANSPORTS" note and the launch receipt's scope ruling both assume one model across the run. The
screen is arguably outside that ruling because it produces no verdict — but that argument has to be
made and recorded, not assumed. If accepted, add a `screen_model_id` and record it in provenance
beside `candidate_screen_version`.

**Validate before trusting it.** A weaker model may return a lower-quality `possible_relation`, and
the ranking is only as good as that signal. The acceptance test is §5's rank check, re-run on Haiku.

---

## 5. Acceptance criteria

1. **Cap and guardrail are both set**, and `validate_f5_policy` passes.
2. **Landmark recall improves, measured.** Re-run `cap_sweep` on the three fixtures: expect
   sepsis 2/2 (was 1/2), HRT 1/1, EGDT 1/3 -> total **4/6** (was 3/6).
3. **The true refuter lands inside the budget.** On the sepsis fixture, `22616830` must carry a
   `deep_comparison_rank` **< B** and a `reason` other than `deep_comparison_budget_exhausted`.
   Measured at B=25 it was rank 9.
4. **All three fixtures still reach `terminal_outcome: F5`** with the verifier confirming.
   Measured confidences to beat: HRT 0.72, sepsis 0.85/0.90, EGDT 0.90.
5. **`caps_cast_f5.json` still does NOT reach F5.** CAPS 1988 -> CAST 1991 is the negative
   fixture; a deeper cap that makes it fire is a precision regression.
6. **Cost per claim is within 10% of the §4.1 projection** for the chosen option, read off
   `token_usage.by_stage` — not modelled.
7. **`cache_read_input_tokens > 0`** on the generator stage; a cap raise must not silently break
   the prefix cache.
8. **Zero new test failures** against clean HEAD. The current set is 12; diff it, don't count it.

---

## 6. What must not change

- **`band_prompts.py` stays byte-identical.** Blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`,
  pinned by `mint_v1.derive_source_blob_oid` against `semantic_validator_v1.FROZEN_SOURCE_BLOB_OID`,
  sealed into both frozen prompt packages and asserted by
  `test_band_prompts_blob_oid_is_unchanged`. The transport lives in `anthropic_transport.py`
  precisely so this file never has to move.
- **Never weaken a strict parser or a record invariant to make a cap raise pass.** §3.2 is the
  worked example: the budget-order invariant was *strengthened* with `deep_comparison_rank` rather
  than relaxed.
- **`deploy_path_a` stays False.** Hard-gated in `validate_f5_policy`; a cap raise does not touch it.
- **`f4_strength.py` is out of scope.** It makes no paid call.
- **Nothing from the bench is reportable.** Every record carries `reportable: false`.
- Shared worktree: `git add` by explicit path only, never `-A` / `.` / `commit -a`; never bare
  `git stash`.

---

## 7. Risks, stated in advance

**The screen becomes a single point of failure for cost.** With the cap at 200, a screen that fails
or returns a malformed batch falls open to deep-comparing everything the budget allows — which the
guardrail bounds, so this degrades to cost, not correctness. But note the ordering is *lost* in
that case: the budget is then spent in retrieval order, which is where PROWESS-SHOCK sits at 189.
**A failed screen silently costs recall, not money.** `candidate_screen_status` records it
(`failure_open_to_deep_comparison` / `malformed_open_to_deep_comparison`); alert on it rather than
treating it as benign.

**The budget is per claim, not per reference.** `decide_f5` runs the detector once per SUPPORTED
claim, so a reference with 4 atomic claims costs up to `4 x B` deep comparisons. The distribution of
atomic claims per reference has never been measured, and it is the multiplier that decides what a
mass run actually costs. **Measure it before sizing a mass run** — it is a cheap offline count over
existing records.

**Retrieval order is not stable across caps.** Measured: WHI 2002 sits at position 8 at cap 25 and
197 at cap 300. So position-based reasoning transfers between caps only by measurement, never by
inference — a mistake made and corrected inside the predecessor session.

**Landmark recall is a 6-paper probe, hand-picked because the answers were already known.** It is
not a recall statistic and no confidence interval belongs on it. Note also that all three fixtures
reached the correct `F5` at cap 50 *despite* 3/6 landmark recall, because F5 needs one qualifying
contradiction rather than all of them. The case for the cap raise is robustness, not a fixed defect.

---

## 8. Rollback

Two constants. Revert `CANDIDATE_CAP` and `max_deep_comparisons` to `50` / `None` and the system is
byte-identical in behaviour to today — the ordering in §3.2 is a no-op at `None`, and the screen can
be turned off per packet with `f5_candidate_screen: false`. No record format changes, so records
written under the new defaults stay readable and `deep_comparison_rank` is simply present.

---

## 9. Open decisions

1. **`B = 25` or `B = 50`?** §4.1. 50 regresses nothing; 25 is cheaper and gives up confident
   negatives for claims with 26–49 candidates. This is the decision.
2. **Screen on Haiku 4.5?** §4.3. Makes `B = 50` cost 1.37x instead of 1.88x. Governance question.
3. **Cap 400 instead of 200?** Landmark recall 5/6 vs 4/6, and the screen roughly doubles again
   ($1.39 measured at 400). Deferred: 200 is the smallest cap that fixes the known miss.
4. **Measure atomic claims per reference** before sizing any mass run (§7).
5. **Batch API** for offline mass runs — 50% off, stacks with caching, up to 24h latency, breaks the
   synchronous `/api/run` model. Still deferred, not rejected.
6. **A learned reranker** (monoT5, named at `f5_seams.py:77`). The screen-as-ranker is a cheap
   proxy that measurably works; a real reranker would let the budget shrink further. Its own session.
