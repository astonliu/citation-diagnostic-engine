# What the 200-paper live run is for

**Written to settle a scope disagreement.** A review of `CRE_MASS_ERROR_HUNT.ipynb` assessed it as an
attempt to produce *"10–20 defensible examples per category"* and correctly concluded it fails at that.
**That was never the goal.** This document states the actual goal so the notebook can be judged against
it. Eight of the review's findings are still true and are addressed at the end.

---

## 1. The purpose in one sentence

**Find the engineering failures that only appear when the pipeline meets real, heterogeneous literature
at volume — failures no amount of code reading can surface.**

This is a **shakedown**, not a measurement. The word for it in software is a soak test: run the real
system on real input, at real scale, and watch what breaks.

---

## 2. Why code reading cannot substitute for it

The taxonomy audit that closed on 2026-08-18 ran 30 rounds and landed 25 findings. Every one came from
reading code and reproducing it with a hand-built probe. That method is powerful and it has a hard
ceiling: **it can only find defects someone thought to look for.**

Three classes of defect are invisible to it and visible only here:

**a. Input-shape failures.** The corpus is not uniform. Real PMC articles include author-year citation
markup, non-English and bracketed titles, corporate authors, reference lists with no PMIDs, chapters,
retracted works, and JATS variants no fixture covers. The audit already found two defects of exactly
this shape by accident — `detect_citation_style` failing open on year-only `<xref>` markup, and
`_surname_set` deleting every romanized surname shorter than four characters. **Both were found by
reasoning about input the fixtures did not contain.** A 200-paper run supplies that input by the
thousand instead of by imagination.

**b. Rates, which no probe can produce.** A probe answers *can this fail?* Only a volume run answers
*how often, and on what?* The rates that matter operationally:

- what fraction of model calls end in parse quarantine, and in which of the three failure modes
- what fraction of references reach judgment at all
- how often `_sentence_spans` fails to tile its input, and how many characters that deletes
- how often NCBI rate-limits, 404s, or returns a record with no usable abstract
- how often a document parses to zero references

Doc 1 already produced one such number — **six quarantines out of one document** — and that single
observation is more actionable than several audit rounds. That is the argument for volume in miniature.

**c. Endurance and interaction.** Does the engine survive ~6,000 references without a memory leak, a
session drift, an unhandled provider exception, a chain-hash break, or a manifest that silently goes
`in_progress` and stays there? Those failures are not in any one function. They only appear in the long
run.

---

## 3. What the deliverable actually is

**The deliverable is a failure census, not a findings list.**

The run succeeds if it produces:

1. A **quarantine census** — count and rate, broken down by failure mode, per paper and overall.
2. A **true disposition census** — every one of the thirteen dispositions counted, so *judged*,
   *excluded before judgment*, *held*, and *quarantined* are four distinct numbers.
3. A **reachability attestation** — for each of F1–F8, whether the check could fire at all, printed
   before any count.
4. A **transport-failure census** — NCBI failures separated from genuine absence.
5. A **crash and exception log** — anything that killed a chunk, with the paper that caused it.
6. Whatever positives happen to appear, as a by-product.

**A run that finds zero findings but produces a complete census has succeeded.** A run that finds
forty findings and cannot say what fraction of references reached judgment has failed. That inversion is
the whole point and it is what the review's framing missed.

---

## 4. Why the numbers are smaller than assumed

The cost figure carried in the specs is *~$7.22 and ~29 min per document*. **The unit is wrong.** Cost
and time scale with **references**, not papers, and the figure appears to derive from a large-bibliography
article. Most papers carry **~30 references, not ~300**.

Re-expressed per reference, and treating the spec figure as ~289 references:

```
~$0.025 and ~6 s per reference
200 papers x ~30 refs = ~6,000 references
                      = roughly $150 and roughly 10 hours
```

**This must be measured, not assumed** — chunk 0 gives the real number. But the order of magnitude is
~$150, not ~$1,444. That changes the decision entirely, and it means the objection "too expensive to
recover from failure" shrinks with it: a 10-paper chunk is roughly **$7.50 and 30 minutes**, not $72 and
4.8 hours.

---

## 5. Sampling: the review is right that it is not random, and random is not what this needs

For a **reported** measurement, an adjudicated seed is mandatory and none of this applies —
`RESERVE_SEEDS` is exhausted and untouched.

For a **shakedown**, randomness is the wrong criterion. **Diversity is the criterion**, and the current
draw fails it in a way the review did not name: sorting by publication date returns 200 *recent* papers,
which are the most homogeneous slice available — same deposit conventions, same era of JATS tooling, few
retractions.

The right sampling for this purpose is **deliberately adversarial**: over-sample the shapes known to
break things — author-year documents, non-English and bracketed titles, very short and very long
bibliographies, papers citing retracted work, papers with reference lists lacking PMIDs. **A soak test
should be biased toward the ugly cases on purpose**, and stamped so it can never be mistaken for a seed.

---

## 6. The review's eight findings under this purpose

All eight are true. Under the actual goal they sort into three groups.

**Must fix before running — they break the deliverable itself:**

- **The denominator is wrong.** `judged = rows − quarantine_parse` subtracts one disposition out of
  thirteen. It counts `excluded_no_citance`, `excluded_no_cited_pmid`,
  `excluded_preband_disposition_missing`, `excluded_preband` and `held_no_atomic_claims` as *judged*.
  Since the census **is** the deliverable, this is fatal, not cosmetic.
- **`max_retries=0` with no resume.** One transient provider error ends a chunk that cannot restart.
  Retries with backoff, and smaller chunks.
- **Sampling is homogeneous.** See §5.

**Must decide, because it changes what the run tests:**

- **Abstract scope vs full text.** No `fetch_fulltext` and no `coverage_judge_v3` are supplied, so the
  full-text path is never exercised. The review's stated mechanism is backwards and the truth is worse:
  `coverage_aggregate.py:76-80` maps abstract silence to `None` — *"unknown, NEVER a coverage gap"* — so
  missing evidence produces a **HELD, not a false F6**. Abstract-scope F6 requires the abstract to
  *contradict* the claim. The consequence is **under-detection**: the entire full-text branch, including
  the `established=False` path that DEC-032 governs, goes untested. **For a shakedown of the whole
  system, wire full text.** That is a real decision with a real cost.

**True, and not obstacles to this purpose:**

- **F5, F7 and F8 cannot fire.** Correct, and the run is not trying to measure them. What it *does* test
  is that they are honestly reported as unreachable at volume rather than silently printing zero — which
  is the audit's central concern, and the reachability report is the check.
- **`max_tokens` does not fix malformed JSON.** Agreed, and stated in the triage doc: it addresses one
  of six observed quarantines. The remaining five are a read-side decision, and **measuring their rate
  is precisely what this run is for.**
- **`positives.jsonl` is thin.** True, and cheap to enrich. It is a by-product here, not the deliverable
  — but there is no reason to keep it thin.
- **The F3 header contradicts the wiring.** True. The header markdown overstates; the reachability cell
  is correct. The header must be fixed.

---

## 7. What this run is explicitly not

- **Not the reported run.** `production=False`, `require_reportable=False`, `production_launcher` unused.
- **Not a rate, a precision figure, or a base rate.** No number from it may be quoted, including F6.
- **Not a seed.** Seed 47 and `74/80 = 0.9250` are untouched. The draw is stamped
  `ERROR_HUNT_NOT_A_SEED`.
- **Not an example set.** Adjudicable examples need evidence spans, atomic claims, provenance chains and
  F4 strength records. That is a different artifact with a different design.
- **Not a change to any frozen or governed thing.** `band_prompts.py` is asserted byte-identical at
  blob `fa01126e2b9482d450065fd70cd0eb1fea816f5c` before anything runs.

## 8. The one-line version, for the reviewer

**We are not sampling to measure. We are stress-testing to break.** The output is a failure census with
an honest denominator; findings are incidental. Judge the notebook on whether it can tell *"the check ran
and found nothing"* apart from *"the check never ran"* — six thousand times, without lying once.
