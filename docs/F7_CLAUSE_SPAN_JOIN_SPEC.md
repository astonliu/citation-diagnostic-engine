# F7 — the clause-span join, and what a production evidence builder costs — implementation spec

**Date:** 2026-08-17 · **Tree of record:** `merge/f2-into-f3f7` at `/Users/kamachi/cre-f3f7`
**Status:** blocked on one design decision that is ZD's, not the implementer's.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## The blocker, stated once

`ClaimClauseRef.clause_span` must equal, **character for character**, the `clause_span` the schema-B
model returns — `f7_entity` joins on `==`.

`marker_scope.assign_claims` attributes **whole claims** to marker clusters. It has no intra-claim
clause spans. So a builder can only supply whole claim text, and **every tuple whose model-returned
span is a genuine sub-clause fails the join and holds at `clause_attribution_unavailable`.**

**Consequence: wire F7 today and it runs, spends money, and holds every tuple.** A 0% emission rate
that looks like "no entity faults found" rather than "the join never matched". That is the loop's
signature defect — a check that cannot fire — reintroduced at a new seam.

**Do not build the evidence builder until this is decided.** The builder is the cheap part.

---

## What already exists — do not rebuild it

`fulltext_reader.py:85-89` was built for this and says so: it emits *"a SUPERSET of
`f7_entity.SectionText`'s vocabulary … plus discussion, intro, other. The F7 evidence builder filters;
the reader never drops a section."*

- per-section `content_sha256` — already returned
- `resolved` / `pmcid` → `paper_resolved` / `resolved_work_id` — already returned
- `cocitation.py` supplies `bundled_reference_ids`

**Verify each of these against the tree before relying on it** — this spec's line numbers were read on
a pre-merge tree and `merge/f2-into-f3f7` may have moved them.

---

## The decision — ZD's, and it must be measured, not reasoned

Three candidate join strategies. **Do not pick one by argument.**

| option | what it does | what it risks |
|---|---|---|
| **A. Containment** | accept when the model's span is a contiguous substring of the supplied claim | silent mis-attribution when a claim contains two entity mentions |
| **B. Fuzzy join** | accept above a similarity floor | **needs a threshold nobody has adjudicated** — see guardrails |
| **C. Builder emits candidate clauses** | segment claims into clauses at build time; join stays `==` | segmentation becomes a new failure surface, and `parser.py`'s `et al.` fragmentation defect is precedent |

**Required before any option is chosen:** measure the hold rate. Run the existing F7 path over a
sample with the builder supplying whole-claim text, and report **what fraction of tuples hold at
`clause_attribution_unavailable`, and of those, how many the model's span is a substring of.** That
second number is the entire case for option A and it is cheap to get.

**Report the comparison to ZD before implementing a route.** Changing how a claim binds to evidence
changes what F7 measures.

---

## Out of scope, and it is the real cost

**The normalizer, cross-comparator and relation-comparator seams.** Live HGNC / ChEMBL / ClinVar /
MONDO lookups with a pinned version and `lookup_date` per authority. That is what `authorities_json`
is waiting for, it is a separate build an order of magnitude larger than this one, and **nothing in
this spec starts it.**

Also out of scope: the F5 half of the legacy-path early return (a marked insert point exists); the
`F7_EVIDENCE_PROMPT` "not found" escape; any corpus run.

---

## Guardrails

- **No invented threshold.** Option B needs a similarity floor. **Do not choose one** — measure the
  distribution, report it, and let ZD set it. An unadjudicated constant later gets cited as decided.
- **`band_prompts.py` byte-identical**, blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`. A
  `F7_EVIDENCE_PROMPT` change is a **prompt-version bump** and a re-freeze of at least five committed
  artifacts — route it, never take it.
- **`judgment_run.py`, `judgment_engine.py`, `parser.py`, `schema.py` are GOVERNED.** Four digests have
  already moved (`schema.py` from the F1 pass; `f7_entity.py`, `judgment_engine.py`, `judgment_run.py`
  from the F7 pass). **CONTRADICTIONS 65 is open on this class — report, do not decide.**
- **Precision-first, both halves.** A tuple that cannot be joined must be visibly held, never silently
  cleared and never emitted as a fault.
- **Do not report an F7 rate until the join is decided.** A hold-everything run produces no rate.

## Definition of done

- Hold-rate measured with whole-claim text, reported as a fraction, with the substring-containment
  sub-count.
- The comparison sent to ZD; a route chosen by him, not by the implementer.
- The chosen join implemented, and **the hold rate re-measured after** — old → new, as fractions.
- A tuple that fails the join is distinguishable in the artifact from a tuple that was assessed and
  cleared.
- `band_prompts.py` blob OID unchanged. State it.
- Suite: state old → new counts **and** state that the tree's pre-existing failures (the in-progress
  F2 merge: `resolve_a.py` and three test files carried over without the `ratelimit.py` defining
  `DATACITE`) are unrelated and unfixed.

## Prerequisite outside this spec

**`F2_BRANCH_MERGE_SPEC.md`'s gate has not been run** — zero verdict movement on the seed-47 frame
against `d90196a` — and cannot be while the tree does not import. Until it passes, the merged tree is
not known to preserve 74/80 = 0.9250. **That is a larger exposure than anything in this spec.**

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

---

# DECISION — ZD, 2026-08-17: **Option A (containment), with one binding condition**

**Option B is withdrawn, not declined.** `f7_entity.py:599-600` already raises on a non-substring
span at parse time, so the row quarantines before the join is reached. There is no continuum to put a
threshold on. This closes the "no invented constant" question by removing the constant.

**Option A is adopted.** The parser guarantees `model_span ⊆ claim` and the builder supplies the whole
claim, so containment matches unconditionally. That is correct: `ClaimClauseRef` models a finer
granularity than any producer in this repo can supply — `marker_scope.assign_claims` attributes whole
claims and has no intra-claim spans. **This is a contract that outran its inputs, not a join bug.**

## The condition — this is not optional

**Containment always matching means the clause-attribution check can no longer fail.** That is this
project's signature defect — a check that cannot fail — arriving at a new seam. It must not be left
reading as a passing verification.

**Required:** record in the artifact, **as a field and not a comment**, that the join was satisfied by
**containment** rather than **equality**. A reader must be able to tell "clause attribution verified"
from "clause attribution not contradicted".

**The honest alternative, if you would rather not add the field:** drop `clause_span` from the
contract until a producer exists. **What is not acceptable is leaving a check in place that reads as
passing.** Either is fine; doing neither is not.

## Accepted as sufficient, no further work

The downstream cover for Option A's stated risk was verified and holds:

- `assign_claims` returns `None` when a claim names two anchors and **falls back to whole-sentence
  scope rather than narrowing on a guess**
- schema A's `sibling_reference_possible` holds `multi_reference_attribution_ambiguous`
- sibling tuples in a multi-entity atomic claim **share one `clause_span`** (`"Metformin activates
  AMPK"` → 2 tuples, 1 distinct span), consistent with the frozen extractor's "ONE subject, ONE
  predicate, ONE finding"

## Withdrawn from this spec

**The hold-rate measurement is withdrawn as circular** — it required running the builder this spec
forbids building, plus a corpus run it puts out of scope. The substring sub-count it asked for is
**100% by invariant** (`f7_entity.py:599-600`), which is stronger evidence than a sample would have
been. Refusing to substitute an unsupported number was correct.

**Still owed once a builder exists:** the fraction of model-returned spans that are strict sub-clauses.
Under Option A that fraction no longer gates anything — it is a description of model behaviour, not a
blocker.
