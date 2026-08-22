# Spec — the candidate screen, prompt caching, and what to extend to the other taxonomies

**Written** 2026-08-22 · **Branch** `merge/f2-into-f3f7` (worktree `/Users/kamachi/cre-f3f7`, shared)
**Predecessor** `docs/F7_MULTI_ENTITY_HANDOFF.md` — read its §9 (Constraints in force); they all still apply.

---

## 0. What this session is for, in one paragraph

F5 now works end to end and finds real superseders from live PubMed, but it does so with the
**candidate screen turned off**, which makes retrieval depth cost linear in the candidate cap: every
admissible candidate goes straight to the expensive deep comparison. The cap is therefore pinned at
50, and at 50 it demonstrably misses landmark refuters (§2.3). The job here is to **build and wire
the screen**, then **turn on prompt caching at the one call site every taxonomy shares**, and then
**extend both to the taxonomies where the same shape applies**. Nothing in this spec is a semantic
change to any verdict; it is all cost, and cost is what currently bounds recall.

Everything runs from `/Users/kamachi/cre-f3f7/citation_repair_F1_handoff`. `cre` is a namespace
package with no `__init__.py` at the `cre/` level; **it will not import from the repo root.** Python
is `/Users/kamachi/citation-repair-engine/.venv_cre/bin/python3`.

---

## 1. Decisions already taken — do not re-litigate these

**The screen is the primary lever.** Raising the cap alone is ~8× cost. Raising it *with* the screen
is ~1.15× for 8× the retrieval depth. That is the whole reason this session exists.

**Prompt caching, not the Batch API.** The user left the choice open and this is the answer:

| | Batch API | Prompt caching |
|---|---|---|
| Discount | 50% flat | ~34% on the F5 deep comparison (measured, §2.2) |
| Latency | up to 24h, async submit/poll | *reduces* latency |
| Fit with `sandbox_server` | breaks it — the `/api/run` request/response model is synchronous | drop-in |
| Engineering surface | restructure every seam into submit + poll + reassemble | one function, §4 |
| Composes with the screen | yes | yes |

Batch remains a **later** option, and specifically for mass offline runs, where its latency is free
and it stacks multiplicatively with caching (§2.2 row E). It is out of scope here.

**The reranker stays out of scope.** `f5_seams.py:77` states plainly that v1 has no learned
reranker and names monoT5 as the known next gain. A bigger cap plus a screen gets the right paper
*into* the candidate set; nothing orders that set. That is a separate piece of work and a separate
decision.

---

## 2. The measured baseline — so it can be re-derived, not trusted

### 2.1 Observed call counts (live runs, real PubMed retrieval)

| run | retrieved | admissible | deep comparisons | paid calls |
|---|---|---|---|---|
| Stampfer 1991 → HRT | 50 | 49 | 42 | 47 |
| PROWESS 2001 → sepsis | 50 | 48 | 38 | 43 |

Deep comparisons land at **~0.8 × cap**. `abstract_screen_calls` was **0** in both, and every
`screen_decision` read `not_performed` — the screen has never run.

### 2.2 Cost model

Rendered the real contradiction prompt (`f5_contradiction_prompt.render_prompt`) against 20 actual
candidate abstracts from a live finder result:

```
total prompt    ~2,945 tok
  shared prefix ~1,890 tok   instructions + claim + CITED work    <- constant per claim
  varying tail  ~1,055 tok   CANDIDATE work                       <- changes per candidate
output          ~400 tok     (ESTIMATE -- see the caveat below)
```

Opus 5 at $5/1M in, $25/1M out; cache reads ~0.1x input, writes 1.25x (5-min TTL); Batch 50%.
→ **$0.0247** per deep comparison uncached, **$0.0162** with the prefix cached.

| scenario | deep calls | $/claim | $/1k claims |
|---|---:|---:|---:|
| A. today — cap 50, no screen, no cache | 40 | 0.99 | 989 |
| B. cap 400, nothing else changed | 320 | 7.91 | 7,912 |
| C. cap 400 + caching | 320 | 5.19 | 5,191 |
| **D. cap 400 + screen (20% pass) + caching** | **64** | **1.13** | **1,134** |
| E. D + Batch API | 64 | 0.57 | 567 |
| F. cap 50 + caching only | 40 | 0.65 | 649 |

**Two numbers in that table are assumptions, and the spec exists partly to replace them:**

- **Output tokens = 400** is a guess. `judgment_run.py:2357` records `input_tokens` /
  `output_tokens` / `cost_usd` as the literal string `"not_collected"`. Only the *input* sizes above
  are measured. See §5 — capturing usage is a prerequisite, not a nicety.
- **The 20% screen pass-rate** is a guess, and it is the single biggest lever in the table. Nothing
  has ever run the screen, so nobody knows this number. Measuring it is Work Item 1's real
  deliverable.
- Token counts are chars÷3.7, not `count_tokens`. Good to roughly ±10%.

### 2.3 Why the cap matters — the evidence that recall is actually being lost

Finder called directly on PROWESS (PMID 11236773), claim + cited paper only:

| cap | PROWESS-SHOCK (PMID 22616830) |
|---|---|
| 50 (`CANDIDATE_CAP`, `f5_seams.py:75`) | **MISSING** |
| 400 | **FOUND** |

The `pubmed_pubmed_citedin` stream returned **all 1,309** citing papers, complete and untruncated —
so the landmark refuter *was* retrieved and then discarded when the union was cut to the cap. The
HRT case survived only because Stampfer's citedin set is 276. The run still produced a correct F5
via a 2012 Cochrane review, so this is a **recall** loss, not a correctness loss — but it is the
exact shape of loss that makes a bench look like it has no yield.

---

## 3. Work Item 1 — build and wire the F5 candidate screen

### 3.1 What exists and what does not

`cre/f1/f5_candidate_screen.py` is **contract-only**: `CandidateScreenDecision`,
`CandidateScreenBatch`, `validate_candidate_screen_batch`, and
`CANDIDATE_SCREEN_VERSION = "f5_candidate_screen_v1"`. There is **no prompt and no model-calling
implementation.** This is a build, not a wiring job. Do not report it as "wiring the screen".

### 3.2 The exact contract

Called once per claim, over every structurally-admissible candidate
(`f5_supersession.py:1781`):

```python
screen_batch = self.screen(
    claim=claim,
    candidates=tuple(candidate for _, candidate, _ in screenable))
```

It must return a `CandidateScreenBatch(decisions=(...), prompt_sha256=..., response_sha256=...)`,
one `CandidateScreenDecision` per candidate:

| field | vocabulary |
|---|---|
| `candidate_work_id` | nonblank str, must match the candidate |
| `decision` | `plausible` \| `clear_mismatch` \| `uncertain` |
| `claim_relevance` | `match` \| `mismatch` \| `uncertain` |
| `possible_relation` | `opposes` \| `confirms` \| `mixed` \| `neutral` \| `uncertain` |
| `missing_facts` | tuple of nonblank str |

**One invariant is enforced in `__post_init__` and will reject a lazy prompt:** `clear_mismatch`
requires `claim_relevance == "mismatch"` **and** `possible_relation == "neutral"` **and** empty
`missing_facts`. A model that says "clear mismatch" while also reporting a possible relation is
refused. Put that rule in the prompt — the lesson from `f5_contradiction_v4`/`v5` is that this seam's
parsers enforce invariants the prompts never mention, and the model then trips them (§7).

**`counts["abstract_screen_calls"] = 1`** — it is ONE call for the whole batch. That is the entire
economic point; do not implement it per-candidate.

**It fails open, by design** (`f5_supersession.py:1784-1790`): any exception discards the screen
output, every candidate proceeds down the original path, and the record says
`candidate_screen_status = "failure_open_to_deep_comparison"`. So a broken screen degrades to
today's cost and today's answers — never to a wrong answer. This makes the change safe to land
before it is perfect.

**The policy flag must match the wiring or construction raises**
(`f5_supersession.py:2119`): `policy.candidate_screen_enabled` (default `False`, line 268) must be
`True` exactly when `screen_candidates is not None`.

### 3.3 What to build

1. A screen prompt + strict-JSON parser, in `f5_candidate_screen.py` alongside its contracts (that
   module is a leaf; keep it one). Mirror the strict-JSON discipline already used by
   `f5_contradiction_prompt` — duplicate-key rejection, exact key set, no fences, no coercion.
2. A `make_candidate_screen(...)` seam factory taking an injected `complete: Callable[[str], str]`,
   so the module still makes no network or model call of its own.
3. Wire it in `sandbox_wiring.build_f5`: construct the screen with its own
   `make_anthropic_call(...)`, pass `screen_candidates=` into `build_f5_seams`, and set
   `candidate_screen_enabled=True` on the `F5Policy`. Record the screen's presence in the returned
   provenance the way `f5_candidate_source` already is.
4. Bump `CANDIDATE_SCREEN_VERSION` off `_v1` if the shipped contract differs in any byte from what
   the frozen validator expects.

### 3.4 Acceptance

- The screen runs: `abstract_screen_calls == 1` per claim, `candidate_screen_status` is neither
  `not_performed` nor `failure_open_to_deep_comparison`.
- **Report the measured pass-rate** — `screen_plausible` / `screen_clear_mismatch` /
  `screen_uncertain` over a real 400-candidate retrieval, on at least the two fixtures in §8.2. This
  replaces the 20% assumption and is the headline deliverable.
- `deep_comparison_calls` falls to roughly `screen_plausible + screen_uncertain`.
- **Both §8.2 fixtures still reach `terminal_outcome: F5`.** A screen that saves money by screening
  out the true superseder is a regression, and `screen_clear_mismatch` on the known-correct
  superseder is the specific thing to check.
- Raise `CANDIDATE_CAP` (or thread a cap through the packet) only *after* the pass-rate is measured,
  and report the cost delta against §2.2 row A.

---

## 4. Work Item 2 — prompt caching at the shared chokepoint

### 4.1 The chokepoint

`band_prompts.make_anthropic_call` (`band_prompts.py:448`) is the **single** adapter every taxonomy
routes through: `sandbox_judge.py:398` (claim extraction + coverage), `sandbox_wiring.py:220/222`
(F7 generator/verifier), `sandbox_wiring.py:509/512` (F5 generator/verifier). One change there
reaches everything.

### 4.2 The obstacle, and the recommended way around it

The seam shape is `Callable[[str], str]` — a single prompt string in a single user content block.
`cache_control` has to sit on a **content-block boundary**, and there is currently no boundary to
put it on. Three options:

- **(a) Sentinel split — recommended.** Prompts embed a literal marker (e.g.
  `<<<CACHE_BREAK>>>`) at the end of their stable region. `make_anthropic_call` splits the string on
  it into two content blocks and puts `cache_control: {"type": "ephemeral"}` on the first. Every
  seam signature stays exactly as it is; a prompt with no marker behaves as today. This is the least
  invasive change that reaches all five call sites.
- (b) Change the seam type to accept a list of blocks. Correct but touches every seam and every
  test double.
- (c) Per-taxonomy adapters. Duplicates the adapter and loses the single-chokepoint property that
  makes this cheap.

Go with (a) unless something in review kills it. Whichever is chosen, the marker/boundary must be
*inside* the bytes that the prompt-version constant covers, so a breakpoint move is a version bump.

### 4.3 Per-taxonomy readiness — this is the real finding

Caching needs **stable content first, volatile content last**. Checked, and it is not uniform:

| prompt | order today | cacheable as-is? |
|---|---|---|
| `f5_contradiction_prompt.render_prompt` | instructions → claim → **CITED work** → candidate | **Yes.** ~1,890 of 2,945 tok (64%) is a clean prefix. |
| `F7_EVIDENCE_PROMPT` (`f7_entity.py`) | `CLAIM_TYPE`/`SURFACE`/`RELATION` (volatile) → `<<SECTIONS>>` (stable, bulky) | **No — inverted.** Needs reordering. |
| coverage (`coverage_prompts_v3.py`) | `<<ATOMIC_CLAIM>>` (volatile) → `<<EVIDENCE_SECTIONS>>` (stable, bulky) | **No — inverted.** Needs reordering. |

So F5 gets caching for free. F7 and coverage put the varying claim *before* the bulky cited-paper
text, which is exactly backwards, and fixing it means **moving prompt text** — which changes bytes,
which per this repo's own convention bumps the prompt-version constant, and which can change model
behavior. Reordering a prompt is not a refactor. Treat each as: reorder → bump version → re-run the
fixtures → **report whether the verdicts moved**, and do not fold a behavior change into a
"caching" commit.

### 4.4 Verification

`cache_read_input_tokens` is the only honest evidence caching is working. If it is zero across
repeated requests with an identical prefix, something is silently invalidating the prefix and the
change is buying nothing but the 1.25× write premium. This makes §5 a hard prerequisite: **do not
claim a caching win without the counters.**

Two caveats that will bite:

- **Parallel fan-out defeats the first write.** N concurrent requests with identical prefixes all
  pay full price — none can read what the others are still writing. The deep-comparison loop looks
  sequential today, which is fine; if anything parallelizes it, fire one request, await its first
  streamed token, then fan out the rest.
- **Minimum cacheable prefix is ~1024 tokens.** The F5 prefix (~1,890) clears it. Check before
  assuming a smaller prompt caches at all.

### 4.5 Acceptance

- `cache_read_input_tokens > 0` on the second and later deep comparisons within one claim.
- Measured $/claim against §2.2 row A, with the counters as evidence rather than the model in §2.2.
- No verdict changes on the §8.2 fixtures for F5 (which needs no reorder). For F7/coverage, verdict
  changes reported explicitly, not absorbed.

---

## 5. Work Item 3 — capture usage (prerequisite for §4)

`judgment_run.py:2354-2360` already declares the slots:

```python
"cost_counters": {
    "model_calls": judge_model_calls,
    "model_calls_avoided_by_cache": judge_cache_hits,
    "input_tokens": "not_collected",
    "output_tokens": "not_collected",
    "cost_usd": "not_collected",
},
```

Filling these **completes an existing contract**; it does not invent one. Capture
`usage.input_tokens`, `usage.output_tokens`, `usage.cache_creation_input_tokens` and
`usage.cache_read_input_tokens` from each response in `make_anthropic_call`, aggregate through
`AdapterReceipt.record` (`recording_adapter.py:73`) / `PaidCallMeter` (`:148`), and surface them
per-stage the way `paid_calls.by_stage` already is.

`cost_usd` needs a price table. Put the rates in one named constant with the date they were read,
and do not scatter them. Rates used for §2.2: Opus 5 $5.00/1M input, $25.00/1M output; cache read
0.1×, cache write 1.25× (5-min) / 2× (1h); Batch 50%.

---

## 6. Extending to the other taxonomies

The pattern that makes this work is: **a per-item fan-out where every item is compared against the
same large body of cited text.** Where that holds, caching pays; where an expensive stage can be
preceded by a cheap batched triage, a screen pays.

| taxonomy | fan-out | caching | screen-shaped triage |
|---|---|---|---|
| **F5** temporal | per candidate, over one cited work | **ready now** (§4.3) | **this spec** — the screen exists as a contract |
| **F7** entity | per claimed tuple, over the same `<<SECTIONS>>` | after reorder | no analogous contract; a tuple set is small (2 in the fixture) — **probably not worth it** |
| **coverage → F6** | per atomic claim, over the same `<<EVIDENCE_SECTIONS>>` | after reorder | no — one call per claim is already minimal |
| **F4** strength | — | — | **none: it makes no paid call.** `f4_strength.py` is offline/injected by design. Do not touch. |
| **F3** provenance | not characterised here | unknown | unknown |

**Do the taxonomies in this order:** F5 caching (free), then usage capture, then coverage and F7
reorders one at a time with verdicts reported. **Characterise F3 before touching it** — this spec
did not, and guessing its shape is how the four bench faults in the predecessor handoff happened.

Do **not** extend the screen concept to F7 or coverage on the strength of this spec. The F5 screen
earns its keep because 50 candidates collapse to a handful; a 2-tuple F7 claim has nothing to
triage. If a real corpus shows F7 tuple counts running high, that is a new measurement and a new
decision.

---

## 7. The failure mode to expect, stated in advance

Three times now, a change to this seam has been blocked by a **strict parser invariant that the
prompt never mentioned**:

- `neutral relation conflicts with different clear source directions` → prompt never stated the
  relation↔direction rule (fixed, `f5_contradiction_v4`).
- `comparable relation axes require scope_mismatch_axis='none'` → prompt never stated the
  axis↔relation-fields rule, and a v4 edit actively invited the rejected combination (fixed, `v5`).
- `candidate study cluster assignment drifted` → cluster/tier assigned after the early-exit gates
  (fixed, `d60fde5`).

Two of those quarantine the **entire pair** — every claim in it — and the stage raises before
writing `f5_records`, so the HTTP surface shows nothing useful. **When a new stage raises, diagnose
in-process, not through `/api/run`.** And before writing the screen prompt, read
`CandidateScreenDecision.__post_init__` and state every one of its invariants in the prompt text.

One process note that cost real time: Python's source-mtime check has 1-second granularity. Rewriting
a module twice inside the same second makes `pytest` reuse the stale `.pyc` and a fixed file will
look broken. Clear `__pycache__` when flipping a file back and forth to verify a test.

---

## 8. State at handoff

### 8.1 Commits

| sha | what | pushed |
|---|---|---|
| `ae8539e` | independence `contradiction_exempt_v1` + the four bench faults that kept F5 from ever running | **yes** |
| `64744cf` | F5 label on the legacy path | **no** |
| `d0d6207` | live PubMed discovery from the bench + `f5_contradiction_v5` | **no** |
| `d60fde5` | cluster/tier recorded before the early-exit gates + regression test | **no** |

`8ffa11b` (F7 entity-only scope) is pushed. **Four commits are unpushed on a shared branch** on top
of a peer's `07f3875`. Decide push before adding more.

### 8.2 Fixtures — both live-discovery, claim + cited paper only, no candidates supplied

Scratchpad:
`/private/tmp/claude-508/-Users-kamachi-cre-f3f7/c2fd1c5d-3470-4e32-8242-924bdeb75dcb/scratchpad`

| packet | claim | cited | verified outcome |
|---|---|---|---|
| `hrt_live.json` | postmenopausal HRT reduces CHD risk | Stampfer 1991, PMID 1870648 | `F5`, selected a 2025 systematic review, verifier confirmed, conf 0.72. **WHI 2002 (12117397) found unaided.** |
| `sepsis_live.json` | drotrecogin alfa reduces mortality in severe sepsis | PROWESS 2001, PMID 11236773 | `F5`, selected 23235609 (2012 Cochrane), verifier confirmed, conf 0.88. **PROWESS-SHOCK missed at cap 50** (§2.3). |

Bank-fed variants (`caps_cast_f5.json`, `hrt_f5.json`) and the runner scripts `runf5.py` /
`runpkt.py` are in the same directory. All abstracts and PMIDs are real, from PubMed; every verbatim
span was checked present before running.

**`caps_cast_f5.json` (CAPS 1988 → CAST 1991) does NOT reach F5, and that is correct** — CAST
confirmed arrhythmia suppression and found excess mortality, so `outcome_relation` is `not_same`.
Keep it as a negative fixture; a change that makes it fire is a precision regression.

### 8.3 Verification status — read this before believing anything

- F5 suites pass (98 in `test_f5_supersession`, 209–237 across the F5 set).
- **The full suite has NOT been run since the F7 work.** The user's standing instruction is to run it
  **last, and only when they say so.** Four commits of production changes are unverified against the
  wider suite. `test_f5_evidence_store`'s hash failure and `test_malformed_claim_output_holds_and_keeps_pair_reviewable`
  are pre-existing (the latter verified against `HEAD`).
- `ijson 3.5.1` and the hand-built 132K authority set from the predecessor handoff remain unreverted
  side effects.

---

## 9. Constraints in force

All of `docs/F7_MULTI_ENTITY_HANDOFF.md` §9, unchanged. The ones this work will touch:

- **Never synthesize a verdict, label, score, or example output.** Nothing produced by the bench is
  reportable. A measured pass-rate is a measurement; an assumed one is an assumption, and the
  difference must survive into the write-up.
- **Never weaken a strict parser to make a stage pass.** The three faults in §7 were all fixed by
  telling the model the rule or fixing the code — never by relaxing the check.
- **Never touch `main`.** Shared worktree: `git add` by explicit path only, never `-A` / `.` /
  `commit -a`. Never bare `git stash` / `git stash pop` — the stack is shared.
- Read the API key only as `"$(cat ~/.cre_bench_key)"` inside a command. Never echo it, never pass
  `--api-key` on a command line, never ask the user to paste one.
- Production files may be modified for the work in this spec — that is its point — but
  `f4_strength.py` is explicitly out of scope (§6) and the frozen `f5_supersession` blueprint digests
  in its module docstring must not be edited to make anything load.

---

## 10. Open decisions for the user

1. **Push the four commits** before this session starts, or carry them?
2. **New `CANDIDATE_CAP`** once the pass-rate is known. 400 is what §2.3 tested; the right number
   falls out of the measurement.
3. **Reranker** — out of scope here (§1). Worth its own session if recall still looks short after
   the cap rises.
4. **Batch API for mass offline runs** — §2.2 row E, $0.57/claim. Deferred, not rejected.
5. **F3** — characterise before extending, or leave alone?
