# CRE audit loop — Relay queue (the only channel to ZD)

Every item states: **what is blocked · the options · what each option costs · the recommendation.**
An item with no recommendation is not ready to send. The Relay never answers on ZD's behalf and never
guesses. Unanswered items stay `BLOCKED-ON-ZD` and the loop continues around them.

---

## RELAY-001 — ✅ ANSWERED by ZD, 2026-08-17: **take Option 1, `field=title`**, with all three conditions.

Recorded in `F1_FABRICATION_GUARD_SPEC.md` as landed finding **L-0**. Closes the route question left
open by CONTRADICTIONS 64 and Amendment 01 §B. **The three conditions are binding and are part of the
decision, not commentary** — see the Recommendation block below.

**Status:** closed. **Blocked:** CONTRADICTIONS 64 · `F1_FABRICATION_GUARD_SPEC.md` Defect 4b.
**Measured:** 2026-08-17, live against NCBI E-utilities and the PubMed search API.

### What was blocked

Amendment 01 §B: `confirm.py`'s `f"{title}[Title]"` is not a title search — Automatic Term Mapping
binds `[Title]` to the trailing fragment only. Three of the seven regression-guard PMIDs returned
**0 hits on their own exact titles**. Three candidate routes were listed; **candidate 1
(`field=title` as a request parameter) was UNTESTED because a rate limit blocked the confirming
call**, and §B says the route must be chosen on measured hit rate, never on reasoning.

### The measurement

**Current form — re-confirmed broken live, and the author-misparse is visible in both cases.**

| PMID | `term={title}[Title]` | the damning fragment of `querytranslation` |
|---|---|---|
| 18152150 | **count 0** | `... AND in a[Author]) AND "muscle twitch"[Title]` |
| 16639420 | **count 0** | `... AND (a, at[Author] OR at a[Author]) ... ) AND "boundary"[Title]` |

In both, only the **last one or two words** are title-bound, and a mid-title stopword sequence
(*"in a"*, *"at a"*) was parsed as an **author surname search**.

**Candidate route 1 — title-bound term. All seven regression PMIDs self-retrieve at count 1.**

| PMID | hits | idlist | how tested |
|---|---|---|---|
| 18152150 | **1** | `["18152150"]` | live `esearch.fcgi`, real `field=title` request parameter |
| 16639420 | **1** | `["16639420"]` | equivalent title-bound query |
| 27665045 | **1** | `["27665045"]` | equivalent title-bound query |
| 31665581 | **1** | `["31665581"]` | equivalent title-bound query (control) |
| 25750229 | **1** | `["25750229"]` | equivalent title-bound query (control) |
| 32355637 | **1** | `["32355637"]` | equivalent title-bound query (control) |
| 22926653 | **1** | `["22926653"]` | equivalent title-bound query (control) |

**7 / 7, up from 4 / 7. No control regressed.**

**Why it works**, from the live `querytranslation` on 18152150 with `field=title`:

```
"heat"[Title] AND "activation"[Title] AND "heat"[Title] AND "shortening"[Title]
  AND "muscle"[Title] AND "twitch"[Title]
warninglist.phrasesignored: ["The","of","and","in","a"]
```

Every content word binds to `[Title]`; stopwords are dropped rather than re-parsed. The MeSH
explosion disappears and so does the author misfire. This is also why R-002 (quoting) fails and this
does not: no phrase index is consulted, so title length stops mattering.

### The honesty caveat on this measurement

**Route 1 was verified end-to-end with the real `field=title` request parameter on 1 of 7 PMIDs.**
The other six were verified with the **equivalent expanded query** — the exact translation
`field=title` produced in the one case where both were observed. The NCBI proxy rate-limited the
remaining `field=title` calls (HTTP 429, then HTTP 403); per Amendment 01 §A.5 those are **failed
tests, not negative results.** Re-running the other six through the real parameter is cheap now that
a key exists and should be done before the fix lands.

### The options

| option | cost | what it buys |
|---|---|---|
| **1. `field=title` request parameter** | one-line change in `confirm.py`; **F2 population must be measured before/after** because `confirm()` feeds F2 too | 7/7 measured. Cheapest. Stays inside PubMed. |
| 2. `ecitmatch.cgi` | new code path; needs structured journal/year/volume/page/author, which the reference record may not carry | purpose-built citation matcher; unmeasured |
| 3. demote PubMed to corroboration; Crossref/OpenAlex lead | changes **what F1 means** — a decision, not an implementation detail | removes the single-database dependency; unmeasured |

### Recommendation

**Take option 1**, with three conditions:

1. **Re-run the other six PMIDs through the real `field=title` parameter first** — close the caveat
   above before anything lands.
2. **Measure the F2 population before and after.** `confirm()` serves both labels, and F2 recall is
   non-negotiable in the matcher. A green suite is not sufficient evidence here.
3. **Do not touch `0.0` / `None` semantics.** A weak query must keep returning `0.0` — "searched,
   answered, found nothing". Making it return `None` would suppress true positives, which is the
   mirror defect.

Also worth carrying into the fix, separately measured by whoever implements: ESearch's default sort is
**most-recent, not relevance**, and the code reads only `retmax=3`. A broad query can therefore return
the three newest hits rather than the three best. That is a distinct defect from this one.

**This does not decide it.** Changing how existence is established changes what F1 means, and that is
ZD's call.

---

## RELAY-002 — may a run truncate an upstream-supplied `Retry-After`?

**Status:** BLOCKED-ON-ZD. **From:** F1 round 1, finding Z-5. **Cite:** `cre/f1/ratelimit.py:90-95`.

**What is blocked.** `request_with_retry` obeys a server-supplied `Retry-After` with no ceiling.
`:58-66` returns `max(0.0, float(ra))`; `:91-94` skips the `min(..., max_backoff)` clamp entirely when
the header parses — **the clamp is on the fallback arm only.** Reproduced: `max_backoff=8.0` was
passed, actual sleeps were `[3600.0, 3600.0, 3600.0]` → **10800 s on one reference.**

**Why the loop cannot decide it.** Nothing in the code declares `max_backoff` as a ceiling on
`Retry-After` — the docstring promises "exponential backoff" and the clamp is written only on the
fallback branch. **Picking a ceiling is a new policy, and the loop may not invent one.**

**Blast radius — the widest in the round.** `request_with_retry` is the single retry helper for
`lookup.py:96`, `confirm.py:89/105/126/157`, `biblio_match.py:703/720`, `ncbi_meta.py:115/177/253`,
`evidence_reader.py:134` and `fulltext_reader.py:743` — **every network path in the package.**

| option | cost |
|---|---|
| **A. Clamp `Retry-After` to `max_backoff`** | one line; makes the declared parameter mean what it reads as. Risks hammering a service that asked for a long wait. |
| **B. Clamp to a separate, larger explicit ceiling** | needs a number nobody has adjudicated — the loop will not propose one. |
| **C. Do not clamp; abandon the reference and record the wait** | no arbitrary number; converts a stall into a visible `resolver_error` hold. Changes how many references a rate-limited run completes. |
| **D. Leave as-is; document it** | zero risk, zero benefit. The stall stays silent — there is no log line today. |

**Recommendation: C, with A as the fallback if you want the run to keep trying.** C invents no
constant, and it turns a silent multi-hour stall into the outcome the transport vocabulary already
has a word for. **This corrupts no number** — the 429 does eventually become `FETCH_RESOLVER_ERROR`
at `lookup.py:100-102` — so it is an availability defect, not an honesty one. But it lands on the F1
hot path, because a rate-limited NCBI is exactly the condition under which *every* PMID-bearing
reference takes this branch.

---

## RELAY-003 — the adapter receipt verifies the launch declaration against itself. How should it be closed?

**Status:** BLOCKED-ON-ZD. **From:** F1 round 1, finding Z-11.
**Cite:** `cre/f1/recording_adapter.py:90-95`; `production_launcher.py:546-569`.
**This one touches two closed decisions — DEC-065 and DEC-070 — so it is the highest-priority item in
this queue.**

**What is blocked.** `recorded()` takes `*args, **kwargs` and **reads none of them**. `record()` builds
every entry from `_base()` (`:71-77`), which reads `self.model` and `self.temperature`, both fixed at
`__init__` from what the **caller declared**. So `verify_receipt`'s unauthorized-model clause
(`production_launcher.py:546-551`) and temperature clause (`:553-569`) **compare the declaration to a
copy of itself and cannot fail** — while `launch_receipt` publishes them as verification.

Reproduced:

```
what the callable ACTUALLY sent : {"model": "gpt-4o-mini", "temperature": 0.9, "assistant_prefill": "{"}
what the receipt RECORDED       : {"model": "claude-opus-5", "seam": "extractor"}
verify_receipt -> PASSED        : {"calls": 3, "models": ["claude-opus-5"], "temperature": "unsupported"}
```

**Why it matters to a closed decision.** DEC-070's recorded code consequence is *"verify_receipt
refuses any call not recorded at the declared temperature."* **That clause cannot refuse.** On the
pinned production model it is airtight by construction: `claude-opus-5` is in
`TEMPERATURE_REJECTING_MODELS` (`production_launcher.py:183-185`), resolves to `'unsupported'`
(`:243-245`), and `_base()` at `recording_adapter.py:64` structurally never writes a temperature key
in that state. The receipt's only real measurement is call **count** — and even that counts attempts,
not completions.

**Why the obvious fix was refused, and this is the crux.** Real seam callables take model and
temperature from a **closure**, not from kwargs — `f4_strength.py:765` calls
`call_llm(generator_prompt)` with a single positional argument, and every seam in
`test_recording_adapter.py:64-80` has the same shape. **Making `wrap()` record observed kwargs would
write an "unobserved" model on every real call**, and `verify_receipt` would then raise
`LaunchRefused` on every launch.

| option | cost |
|---|---|
| **A. Change the seam-callable contract** so model and temperature are observable at the wrap point | largest change; touches every seam. Buys real verification. |
| **B. Correct the published `limitation` string** (`production_launcher.py:641-646`), which today discloses a **weaker** gap than the one that exists | cheapest; honest; verifies nothing |
| **C. Remove the two clauses from `verify_receipt`** so the receipt stops claiming what it cannot check | small; removes a false claim; also removes the appearance of a guarantee |

**Recommendation: B now, A specced separately.** The false claim in the shipped artifact is the part
that makes a reader act wrongly, and it is fixable without touching a single seam. A is the real fix
and deserves its own spec with its own regression measurement — it is not an F1 item.

---

## RELAY-004 … — appended as the F2–F8 Checkers produce ASK-ZD verdicts.
