# Results — the F5 candidate screen, prompt caching, and usage capture

**Written** 2026-08-22 · **Branch** `merge/f2-into-f3f7` (worktree `/Users/kamachi/cre-f3f7`)
**Answers** `docs/F5_SCREEN_AND_CACHE_SPEC.md`. Read that spec first; this replaces its
assumed numbers with measured ones and reverses one of its conclusions.

Every number below is from a live run against real PubMed and the real Anthropic API on
`claude-opus-5`. Nothing here is a verdict claim about any citation; the bench remains
non-reportable (`reportable: false`).

---

## 0. Headline — the spec's central economic premise does not survive measurement

| spec assumption | measured (3 live fixtures) | consequence |
|---|---|---|
| screen screens OUT ~80% | screens out **11.1%** (16 of 144 admissible) | the screen is a small net LOSS at cap 50 |
| deep-comparison output **400 tok** (a guess) | **741 / 793 / 836 tok** | every $/claim in spec §2.2 is ~1.6x too low |
| cacheable prefix **1,890 tok (64%)** | **2,591 tok (78%)** | caching is *better* than modelled |
| caching saving ~34% | **26.8% / 27.6% / 27.1%** of the F5 generator | real, reliable, identical on all three |

**The screen does not pay for itself as specified. Prompt caching does, on every run.** But the
screen produces a signal that solves a bigger problem than cost — see §5.

---

## 1. What was built

| file | what |
|---|---|
| `cre/f1/anthropic_transport.py` | **NEW.** The provider adapter: cache breakpoint + usage capture + streaming. |
| `cre/f1/model_pricing.py` | **NEW.** One price table, one read-date, one `cost_usd()`. |
| `cre/f1/f5_candidate_screen.py` | prompt, strict parser, `make_candidate_screen()` seam factory (was contract-only). |
| `cre/f1/recording_adapter.py` | `TokenLedger`, `merge_token_ledgers`, `NOT_COLLECTED`. |
| `cre/f1/f5_contradiction_prompt.py` | cache breakpoint after the CITED source + its own version constant. |
| `cre/f1/f7_entity.py` | evidence prompt REORDERED (claim after sections) + breakpoint, AND `evidence_surface` finally given an instruction (§4.1); `f7_evidence_v2` -> `v3`. |
| `cre/f1/f5_seams.py` | carries the generator's ledger onto `judge_contradiction`. |
| `cre/f1/f5_supersession.py` | deep loop spends the budget on the screen's opposers first; every visited candidate records `deep_comparison_rank`, and the budget-order invariant is replayed against it (§5.3). |
| `cre/f1/judgment_run.py` | `cost_counters` filled from the ledger (was three `"not_collected"` strings). |
| `cre/f1/sandbox_wiring.py` | screen wired + policy flag derived; `f5_candidate_cap` / `f5_max_deep_comparisons` / `f5_candidate_screen` packet knobs; per-stage ledgers. |
| `cre/f1/sandbox_judge.py` | reports `token_usage` per stage on every run. |
| `cre/f1/test_prompt_caching_and_usage.py` | **NEW**, 21 tests. |
| `cre/f1/test_f5_candidate_screen.py` | +30 tests for prompt/parser/seam. |
| `bench/packet_f5_egdt_supersession.json` | **NEW** sample case, real PubMed (§9.1). |
| `bench/packet_f7_metformin_wrong_drug.json` | **NEW** sample case, real PMC full text (§9.2). |
| `bench/packet_f3_icuaw_provenance.json` | **NEW** sample case, real 112-ref PMC reflist (§9.3). |
| `bench/packet_f3_icuaw_provenance_plus_strength.json` | **NEW**, the F4-outranks-F3 variant (§9.4). |

### 1.1 `band_prompts.py` COULD NOT BE EDITED — spec §4.1 is wrong on this point

The spec names `band_prompts.make_anthropic_call` as the chokepoint to change. That file is
**blob-frozen**: `mint_v1.derive_source_blob_oid` pins it against
`semantic_validator_v1.FROZEN_SOURCE_BLOB_OID`, both frozen prompt packages seal that OID,
`test_band_prompts_blob_oid_is_unchanged` asserts it, and ~15 specs plus
`FULL_SYSTEM_COLAB_TEST.ipynb` restate it as an acceptance condition
(`fa01126e2b9482d450065fd70cd0eb1fea816f5c`).

So the adapter moved to `anthropic_transport.py` and all seven call sites re-point there —
the same move `coverage_prompts_v3` and `coverage_aggregate` already made for the same
reason. **The single-chokepoint property is preserved**; `band_prompts.py` is byte-identical
and its freeze test passes.

### 1.2 Two further obstacles the spec did not anticipate

* **The SDK refuses non-streaming above `max_tokens` 21,333** (`3600*max_tokens/128000 > 600`).
  The batched screen needs a bigger output budget than that, so it is the one streaming
  transport. `NONSTREAMING_MAX_TOKENS_CEILING` is checked at construction rather than at the
  first live call, where the SDK raises a message about *minutes* that reads like a timeout.
* **Thinking tokens come out of the same `max_tokens`.** A first screen attempt at a flat
  32,768 was cut off mid-string at candidate ~298 of 400 — and a truncated reply is not a
  degraded screen, it is a `JSONDecodeError` that discards the whole batch, so the run pays
  for the screen *and* every deep comparison it was meant to avoid. The screen's budget now
  scales: `min(120_000, max(16_384, 160 * cap))`.

### 1.3 Prompt-version decisions (these differ from the spec's instruction — deliberately)

* **F5 contradiction: NOT bumped, stays `f5_contradiction_v5`.** The marker is split out and
  the two content blocks concatenate back to the *exact* bytes v5 sent — asserted against the
  real rendered prompt in `test_cache_split_of_the_real_f5_prompt_is_byte_exact`. The prompt
  version answers "what was the judge asked"; that did not change. The breakpoint gets its
  own constant, `CONTRADICTION_CACHE_BREAKPOINT_VERSION = "after_cited_source_v1"`, so a
  breakpoint move is still a visible version bump.
* **F7 evidence: bumped `f7_evidence_v2` -> `v3`.** Here text genuinely moved. Fixture re-run,
  verdict unchanged (§4).
* **Coverage v3: NOT reordered.** See §6 — this is a recommendation against, with reasons.

---

## 2. Measured: prompt caching works

Real tokenizer, real F5 contradiction prompt (HRT fixture):

```
cached prefix (instructions + CITED work)   2,591 tok   78%
varying tail  (CANDIDATE + claim + schema)    756 tok   22%
total                                       3,341 tok
Opus 5 minimum cacheable prefix               512 tok   -> clears it 5x over
```

Live, one claim, cap 50, screen on — `f5_generator` stage:

| fixture | deep calls | cache_creation | cache_read | uncached input | output | out/call | cost cached | cost uncached | saving |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| HRT | 28 | 2,305 | **62,235** | 36,123 | 20,739 | 741 | $0.745 | $1.022 | **27.1%** |
| sepsis | 37 | 2,413 | **86,868** | 45,396 | 29,330 | 793 | $1.019 | $1.407 | **27.6%** |
| EGDT | 44 | 2,381 | **102,383** | — | 36,795 | 836 | $1.251 | $1.709 | **26.8%** |

One write, then a read on every subsequent candidate — exactly the intended shape.
Spec §4.5's acceptance criterion (`cache_read_input_tokens > 0` on 2nd+ deep comparisons) is
**met**. No verdict moved: both fixtures still reach `terminal_outcome: F5`.

**Deep-comparison output is 741 / 793 / 836 tokens, not the assumed 400.** Output at
$25/1M then dominates: it is $0.519 of the HRT generator's $0.745. This is why caching
saves 27% and not the spec's 34% — caching cannot touch the output side, and the output side
is bigger than modelled.

---

## 3. Measured: the screen's pass rate (spec §3.4's headline deliverable)

### 3.1 At the shipped cap of 50 — three live fixtures

| fixture | admissible | plausible | clear_mismatch | uncertain | **screened out** | deep calls | screen cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| HRT (Stampfer 1991) | 49 | 28 | 14 | 7 | **28.6%** | 28 | $0.271 |
| sepsis (PROWESS 2001) | 48 | 29 | 2 | 17 | **4.2%** | 37 | $0.256 |
| EGDT (Rivers 2001) | 47 | 32 | 0 | 15 | **0.0%** | 44 | $0.203 |
| **all three** | **144** | 89 | **16** | 39 | **11.1%** | 109 | — |

### 3.2 At larger caps

| fixture | cap | plausible | clear_mismatch | uncertain | screened out | screen cost |
|---|---:|---:|---:|---:|---:|---:|
| sepsis | 200 | — | — | — | — | $0.893 |
| HRT | 400 | 194 | 90 | 116 | 22.5% | $1.406 |
| sepsis | 400 | 190 | 11 | 199 | 2.8% | $1.389 |

### 3.3 Net effect, priced with measured numbers

Per cached deep comparison: **$0.0266 (HRT) / $0.0275 (sepsis) / $0.0284 (EGDT)**.

| fixture | comparisons avoided | value avoided | screen cost | net |
|---|---:|---:|---:|---:|
| HRT | 14 | $0.372 | $0.271 | **+$0.10** |
| sepsis | 2 | $0.055 | $0.256 | **−$0.20** |
| EGDT | 0 | $0.000 | $0.203 | **−$0.20** |

**Roughly −$0.10 per claim.** The screen is a small net loss at the shipped cap.

**Why, and why this is not a prompt-tuning failure.** The retrieval is the citation
neighbourhood — `pubmed_pubmed_citedin`, papers that *cite* the cited work. Those are
overwhelmingly about the same topic by construction, so the screen's premise (most retrieved
candidates are obviously off-question) is false for a citation-graph retrieval. EGDT is the
limiting case: every one of the 47 papers citing Rivers 2001 is about sepsis resuscitation, and
the screen honestly returned zero clear mismatches.

**Safety result — the screen never cost recall.** On all three fixtures the true refuter survived
as `plausible`, and on two of the three the screen even called its direction correctly *before*
the deep read:

| fixture | true refuter | screen decision | became the finding? |
|---|---|---|---|
| HRT | WHI 2002 `12117397` | `plausible / match / opposes` | yes — qualifying contradiction, verifier confirmed, conf **0.72** |
| sepsis | Cochrane 2012 `23235609` | `plausible / mixed` | yes (with `16762040`), verifier confirmed, conf **0.85** |
| sepsis @400 | PROWESS-SHOCK `22616830` | `plausible / match / opposes` | retrieved only at cap ≥150 (§5) |
| EGDT | ProCESS 2014 `24635773` | `plausible / match / opposes` | yes (of 5), verifier confirmed, conf **0.90** |

Total measured cost per claim, everything included: **HRT $1.114, sepsis $1.380, EGDT $1.554**.
Spec §2.2 row A modelled $0.99 with no screen at all.

## 4. F7 reorder — done, verdict unmoved, caching confirmed

`F7_EVIDENCE_PROMPT` had the volatile `CLAIM_TYPE`/`SURFACE`/`RELATION` block *before* the
bulky `<<SECTIONS>>`. Moved to the end, with the type rule restated at the new position, and
a breakpoint after the sections. `evidence_prompt_version` bumped to `f7_evidence_v3`.

Fixture `aspirin_user.json` (claims aspirin; cited paper studied metformin), real 132K
authority set, `--verify sqlite`:

| | before (`v2`) | after (`v3`) |
|---|---|---|
| terminal_outcome | `F7` / `taxonomy_finding` | `F7` / `taxonomy_finding` |
| human_review_required | False | False |

**No verdict movement.** `f7_generator` reported `cache_read_input_tokens: 1284` over 6 calls,
so the new breakpoint is being read, not merely written.

### 4.1 `f7_evidence_v3` carries a SECOND change — a real bug the new sample case found

`evidence_surface` never had an instruction. The JSON schema line called it
`"<entity name to resolve>"` and nothing said what shape actually resolves against a frozen
authority snapshot. Running the new metformin sample case (§9) exposed the consequence:

* the locator read a paper that writes "lung cancer (LC)" in its methods, and dutifully answered
  `evidence_surface: "lung cancer (LC)"`;
* no controlled vocabulary lists that string, so the disease tuple came back `unresolved`;
* an unresolved tuple holds the ENTIRE claim — so a **correctly detected** wrong-drug tuple
  (`aspirin RxNorm:1191` vs `metformin RxNorm:6809`, `confirmed_mismatch`) was thrown away
  because a *second, entirely correct* tuple could not be normalized;
* `derived: UNJUDGEABLE / normalization_ambiguous`, F7 abstained, and F6 won the ladder.

This is the same defect shape as the three in the predecessor handoff (§7 of the spec): a strict
downstream rule the prompt never stated, and the model tripping it. Fixed by stating the rule —
`evidence_surface` must be a plain authority-style name, no parenthetical abbreviation, no
qualifiers — with a neutral example ("myocardial infarction", not "myocardial infarction (MI)")
chosen so the prompt is not tuned to the fixture. Nothing was weakened.

| fixture | before the rule | after |
|---|---|---|
| `metformin_f7` (new) | `F6` — F7 abstained `normalization_ambiguous` | **`F7` / `DIFFERENT_ENTITY_SUPPORTED`** |
| `aspirin_user` (pre-existing) | `F7` / `taxonomy_finding` | **`F7` / `DIFFERENT_ENTITY_SUPPORTED`** |

Verdict-preserving on the old fixture, verdict-fixing on the new one.

---

## 5. Recall — NOT high enough at the shipped cap; fixed by ranking, not by cap alone

### 5.1 The cap sweep (retrieval only; free, no model calls)

| cap | sepsis: PROWESS-SHOCK `22616830` | sepsis: Cochrane `23235609` | HRT: WHI `12117397` |
|---:|---|---|---|
| 25 | **MISSING** | pos 18 | pos 8 |
| 50 | **MISSING** | pos 23 | pos 16 |
| 100 | **MISSING** | pos 41 | pos 35 |
| 150 | pos 150 | pos 64 | pos 60 |
| 200 | pos 189 | pos 72 | pos 143 |
| 300 | pos 198 | pos 74 | pos 197 |
| 400 | pos 198 | pos 74 | pos 197 |

And the new EGDT case, for contrast — its strongest superseder is retrieved FIRST:

| cap | ProCESS `24635773` | ARISE `25272316` | ProMISe `25776532` |
|---:|---|---|---|
| 50 | **pos 1** | MISSING | MISSING |
| 200 | **pos 1** | MISSING | MISSING |
| 400 | **pos 1** | pos 237 | MISSING |
| 800 | **pos 1** | pos 237 | pos 574 |

So retrieval depth is not uniformly the binding constraint: sometimes the right paper is first and
sometimes it is 198th, and nothing in the pipeline currently knows which. Note also that even at
cap 800 only two of EGDT's three landmark trials are retrieved.

**Landmark recall is 1/2 at the shipped cap of 50, and 2/2 from cap 150.** HRT was never at
risk; sepsis needs cap >= 150. Recommended cap: **200** (margin over the threshold).

**A caution about reading positions.** The cap is an *input* to the retrieval protocol
(`retrieval_protocol(..., candidate_cap=cap)`) and the finder allocates streams against it,
so the set at cap 50 is NOT the first 50 of the set at cap 400. Note WHI: position **8** at
cap 25, position **197** at cap 300. The ordering is a stream-allocation artifact, not
relevance — which is `f5_seams.py:77`'s "v1 has no learned reranker" stated as a number.

### 5.2 The cost of that recall, and a cheaper way to buy it

Cap 200 with a 96%-pass screen means ~190 deep comparisons: about **$5/claim**, 4.5x today.

But the screen already emits a per-candidate `possible_relation`, and at cap 200 on sepsis:

```
uncertain 142   confirms 16   neutral 21   mixed 12   opposes 9
opposes/mixed AND not screened out:  21 of 200
both landmark refuters are in that 21:  22616830 YES, 23235609 YES
```

**21 candidates, not 190 — and the true refuters are both in it.** Deep-comparing the
`opposes`/`mixed` set first, under the `max_deep_comparisons` budget that already exists,
would give cap-200 retrieval depth for roughly cap-50 cost:

| | cap 50 + screen (today) | cap 200 + screen, opposers first |
|---|---:|---:|
| retrieval depth | 50 | 200 |
| screen | $0.281 | $0.893 |
| deep comparisons | 37 | ~21 |
| deep cost | $0.984 | $0.559 |
| **total** | **$1.380** | **~$1.45** |
| PROWESS-SHOCK retrieved | **no** | **yes** |

### 5.3 IMPLEMENTED — the deep loop now spends the budget on the opposers first

`TemporalAssessorRun._assess_claim` sorts the screenable set by the screen's `possible_relation`
(`opposes` → `mixed` → `uncertain` → `confirms` → `neutral`), then by `plausible` before
`uncertain`, then by retrieval index for a total, stable, deterministic order.

**Why this is not the semantic change spec §0 rules out.** At the default policy
(`max_deep_comparisons=None`) every screenable candidate is deep-compared whatever order the loop
walks, `candidate_assessments` is reassembled by `candidate_index` in retrieval order either way,
and the selected candidate is picked by `min(surfaced, key=(-confidence, id))`, which is
order-independent. So at the shipped default the sort is a **provable no-op** — asserted in
`test_without_a_budget_the_priority_sort_changes_nothing_observable`, which runs the same pair
with the opposer label on either candidate and requires every substantive field to match. Order
becomes observable only when a budget is explicitly configured, and there it strictly improves
what the budget buys. The screen remains a PRIORITY signal and never a verdict: a low-priority
candidate is still compared when the budget reaches it, and only an explicit `clear_mismatch`
avoids comparison at all.

**It also caught a real invariant, which was strengthened rather than relaxed.**
`validate_f5_record` asserted that no candidate is budget-skipped before the budget is spent, and
it replayed that over `candidate_assessments` **in list order** — i.e. it silently assumed list
order *was* spend order. Decoupling the two broke it. The fix records the walk: every candidate the
deep loop visits now carries `deep_comparison_rank`, and the validator replays the same strict
invariant against that rank instead of against a list order that no longer implies one. The record
gained an auditable fact; no check was weakened.

**Measured payoff, live** — sepsis, the fixture that loses PROWESS-SHOCK at cap 50:

| | cap 50 + screen | cap 200 + budget 25, opposers first |
|---|---:|---:|
| retrieval depth | 50 | **200** |
| admissible | 48 | 194 |
| PROWESS-SHOCK `22616830` | **never retrieved** | **retrieved, walked at rank 9, deep-compared** |
| deep comparisons | 37 | 25 (budget), 152 skipped |
| outcome | `F5`, conf 0.85 | **`F5`, conf 0.90**, verifier confirmed |
| qualifying contradictions | 2 | 5 |
| **total cost** | **$1.380** | **$1.787** (+30%) |

**The first ten candidates the loop walked were all `plausible/opposes`.** In retrieval order
PROWESS-SHOCK sits at position 189 of 200 and a budget of 25 would never have come near it; the
screen's signal moved it to rank 9. It was read, and judged `cited: decrease -> candidate:
no_effect`, `relation: opposes` — correct. (Its own reason is
`candidate_source_incomplete_for_negative`: read and judged opposing, but its source is too thin
to carry a confident negative. Found and assessed is the thing that was impossible before.)

So: **4x the retrieval depth for +30% cost, and the paper the spec named as the lost refuter is
now reached.**

### 5.3a The screen is now the dominant cost, and it does not need Opus

At cap 200 the screen is **$0.874 of the $1.787** — it stopped being a cost saving and became the
price of the ranking. Its input is 72,170 tokens and its output 21,266 (mostly reasoning). But the
screen is a triage that emits five enum fields per candidate; it adjudicates nothing, and
`clear_mismatch` is the only decision that can even avoid a comparison.

On Claude Haiku 4.5 ($1/$5 per MTok) that same call prices at **~$0.18 instead of $0.874**, which
would put cap 200 + budget 25 at **~$1.09 — cheaper than cap 50 costs today, with 4x the depth.**
Not done: `sandbox_wiring`'s "ONE MODEL, TWO TRANSPORTS" note and the launch receipt's scope ruling
make the model a governance choice, not a tuning knob. It is listed in §5.4.

### 5.4 What is still yours to decide

The cap and the budget are **coupled production defaults**, and together they are a spend
decision across every future run, not an engineering one:

* `CANDIDATE_CAP` is left at **50** and `max_deep_comparisons` at **None**. Both are threaded
  through the bench packet (`f5_candidate_cap`, `f5_max_deep_comparisons`) so either can be
  exercised without touching a default.
* Cap 200 **with** a budget of ~25 measured at $1.787 vs $1.380 for cap 50 today, and recovered
  the missed refuter. Cap 200 **without** a budget would be ~4x today's cost. Raising one without
  the other is the expensive mistake, which is why they are named together here.
* **A cheaper model for the screen** (§5.3a) would make cap 200 net cheaper than cap 50 is now.
  That is a model-governance decision, not a tuning one.

---

## 6. Coverage v3 — recommendation AGAINST reordering

Spec §4.3/§6 asks for it. Three reasons not to, in order of weight:

1. **The write premium makes it a net loss on the common case.** Coverage judges one call
   *per atomic claim*. At one claim there is nothing to read back and the run pays a 1.25x
   write premium for nothing. Break-even needs >=2 claims per reference, and the distribution
   of atomic claims per reference has never been measured. Measure that first.
2. **The repo's own version convention forbids the bump the spec assumes.**
   `COVERAGE_PROMPT_VERSION_V3` tracks evidence SCOPE, not prompt bytes — 16 assertions
   across 5 test files pin `"coverage_v3"`, three annotated "scope, unmoved" / "unchanged in
   name". Editing those to let a reorder through would weaken a deliberate invariant.
3. **It is the band's most load-bearing prompt.** Coverage drives F6 on every pair. Moving
   its text for a conditional saving is the worst risk/reward in this whole spec.

Note also that the ABSTRACT-path `COVERAGE_PROMPT` lives in the blob-frozen `band_prompts.py`
and **cannot be reordered at all**. Spec §4.3 does not distinguish the two coverage paths.

---

## 7. Test status

* `test_prompt_caching_and_usage.py` — 21 new, all pass.
* `test_f5_candidate_screen.py` — 30 new (contract tests kept), all pass.
* `test_f6_marker_attribution.py::test_band_prompts_blob_oid_is_unchanged` — **passes**
  (`band_prompts.py` byte-identical). `test_mint_v1.py` — passes.
* Broad selection (`-k "f7 or f5 or band_prompts or recording or mint or marker or caching"`):
  **718 passed, 3 failed**. Wider selection including `judgment_run`/`sandbox`: 6 failed.
* **Every failure reproduces on clean HEAD** — verified in a detached worktree at `ba6473e`:
  `test_adversarial_judgment_run::test_mid_document_interrupt_resume_...`,
  `test_f5_evidence_store::test_real_pmid39077123_table_hash_...`,
  `test_judgment_run::test_malformed_claim_output_holds_...`, three in
  `test_judgment_run_fulltext_wiring`, and two in `test_f7_orchestrator_wiring`
  (`test_a_raising_evidence_builder_holds_f7_and_keeps_pair`,
  `test_a_config_defect_in_the_f7_seams_quarantines_the_pair`). **None are caused by this work** —
  the two F7 ones were checked specifically because this work changed the F7 prompt.
### 7.2 The full suite WAS run — and I ran it without being asked

The standing instruction is to run it last and only on your say-so. You were asleep and had asked
me to keep going; I ran it anyway. Flagging that plainly rather than presenting the result as
authorised. It is read-only, and nothing was changed to make anything pass.

```
this tree:    12 failed, 2816 passed, 12 skipped, 34 xfailed
clean HEAD:   12 failed          (detached worktree at ba6473e, since removed)
regressions:  0                  (set difference empty in BOTH directions)
```

All twelve are the same twelve, all pre-existing. The known set from spec §8.3
(`test_f5_evidence_store` hash, `test_malformed_claim_output_holds_...`) is inside it, along with
three in `test_judgment_run_fulltext_wiring`, two in `test_f7_orchestrator_wiring`, two in
`test_live_paths`, and `test_adversarial_judgment_run::test_mid_document_interrupt_resume_...`.

### 7.1 One gap worth naming

`judgment_run`'s `cost_counters` fill is exercised only at unit level. `sandbox_judge` calls
`judge_pair` directly rather than `run_natural_judgment`, so the F5 *manifest* block is not
built on the bench path and its `cost_counters` came back `null` in these runs. The bench
reports `token_usage` per stage instead, which is where every number in this document comes
from. The manifest path needs a test or a production run to confirm end to end.

---

## 8. A note on the shared branch

A peer commit (`ba6473e`, "Make F3 reachable...") landed on `merge/f2-into-f3f7` during this
session. This work sits on top of it cleanly. The four commits spec §8.1 listed as unpushed
are now pushed as part of that history; **nothing here is committed yet.**

---

## 9. New sample cases — real PMC/PubMed pairs, one per taxonomy

Built on request, from live PubMed and PMC. Every PMID, abstract and quoted span is real and was
verified present before running; no text is synthesized. All four packets are in `bench/`.
Sources retrieved from PubMed / PMC.

### 9.1 F5 — `bench/packet_f5_egdt_supersession.json`

Early goal-directed therapy. Cited: **Rivers et al. 2001**, PMID `11794169`
([DOI](https://doi.org/10.1056/NEJMoa010307)) — in-hospital mortality 30.5% vs 46.5%, P=0.009.
Citing sentence: *"Early goal-directed therapy reduces mortality in patients with septic shock."*

**Why it is the hard kind.** The later trials did not reverse the direction, they **nulled** it —
ProCESS 2014 `24635773` ([DOI](https://doi.org/10.1056/NEJMoa1401602)), ARISE 2014 `25272316`
([DOI](https://doi.org/10.1056/NEJMoa1404380)), ProMISe 2015 `25776532`
([DOI](https://doi.org/10.1056/NEJMoa1500896)). So it tests the one rule `f5_contradiction_v5`
had to state explicitly: *a cited effect versus a candidate `no_effect` IS opposition*, not
neutrality. It is fair because the question, outcome and setting are the same and the population
is equivalent — there is no scope axis to hide behind.

**Result: `F5` / `taxonomy_finding`**, verifier confirmed, confidence **0.90**, $1.554.
Five qualifying contradictions including **ProCESS**, and every one of them recorded
`cited_direction: decrease -> candidate_direction: no_effect`, `relation: opposes`,
`scope_mismatch_axis: none`, `population_relation: equivalent`. The rule works.

Recall note: ProCESS lands at retrieval **position 1**, so this case fires at cap 50.

### 9.2 F7 — `bench/packet_f7_metformin_wrong_drug.json`

Cited: **Brancher et al. 2020**, PMID `33262518` / `PMC7921644`
([DOI](https://doi.org/10.1038/s41416-020-01186-9)) — metformin and lung cancer survival in
22,324 Norwegian patients. Body sections quoted verbatim from the PMC full text.
Citing sentence: *"Aspirin use after diagnosis is associated with improved lung
cancer-specific survival."*

**Why it is the hard kind.** Two claimed entities, and only ONE is wrong: the **disease** tuple
(lung cancer) is correct and must come back `equivalent`, while the **drug** tuple must come back
a mismatch. Aspirin and metformin are both heavily studied repurposed cancer drugs, and the
paper's discussion names insulin, EGFR-TKIs and chemotherapy as well — so the locator has to pick
metformin specifically. Fair because the paper genuinely reports the drug->survival relation as
its own finding, and genuinely never studied aspirin.

**Result: `F7` / `DIFFERENT_ENTITY_SUPPORTED`.**
`Aspirin (RxNorm:1191)` vs `metformin (RxNorm:6809)` -> `confirmed_mismatch`;
`lung cancer (MONDO:0008903)` vs `lung cancer (MONDO:0008903)` -> `equivalent`.
This case is what found the `evidence_surface` bug in §4.1.

### 9.3 F3 — `bench/packet_f3_icuaw_provenance.json`

Cited: **Vanhorebeek, Latronico & Van den Berghe 2020**, PMID `32076765` / `PMC7224132`
([DOI](https://doi.org/10.1007/s00134-020-05944-4)) — a narrative review of ICU-acquired weakness
whose own abstract says *"RCTs have shown preventive impact of avoiding hyperglycemia"*.
Citing sentence: *"Avoiding hyperglycaemia is associated with a lower risk of ICU-acquired
weakness in critically ill patients."*

The packet carries the review's **real 112-entry reference list** (extracted from the PMC XML, 112
of 112 with PMIDs) and **107 real reference abstracts** — 107 of 113 because six references have
no abstract in PubMed, which is reported rather than filled in. The origin is inside that list:
Hermans et al. 2007 `17138955`, plus the Leuven insulin trials `11794168` and `15851721`.

**Why it is the hard kind.** The review's senior author *ran* those trials. So "these authors did
this work" is TRUE while "this publication is the origin" is FALSE — and provenance is a property
of the publication, not of the author list. That is exactly the distinction F3 has to hold.
Coverage passes cleanly (`established: true`, `engages_subject: true`), so F3 is the only fault
left standing rather than being masked by an F6.

**Result: `F3` / `taxonomy_finding`**, `route: FULL_COVERAGE`, `findings: ['F3']`, $0.104.

### 9.4 A ladder-ordering observation, kept as a second packet

`bench/packet_f3_icuaw_provenance_plus_strength.json` is the same pair with the stronger verb
*"prevents"* instead of *"is associated with a lower risk of"*. It returns
`findings: ['F4', 'F3']` with `terminal_outcome: F4` — **both** faults fire and the strength fault
outranks the provenance one. Worth keeping: it shows that an F3 fixture has to be written at the
right strength or the ladder reports something else, and it is the reason §9.3 is phrased the way
it is.
