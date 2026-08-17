# F3–F7 — evidence spans by SENTENCE SELECTION, not generation — implementation spec

**Base:** tip of `feat/f3-f7-semantic-validator-v1`, `de3e040`. **Worktree:** `/Users/kamachi/cre-f3f7`.
**Authority:** ZD 2026-08-11. Taxonomy: `TAXONOMY_AMENDMENT_2026-08-11.md`, whose **§D is amended by item 4
below** (DEC-047).
**Evidence:** calibration runs 2–4 (`calib_v3b/c/d_PMC10115774`), plus a literature review run 2026-08-11.
Vault `CONTRADICTIONS` 37a, 38, 43. Supersedes the "one prompt line" framing of the `CR42` fix — that was
treating the third instance of a design error as a typo.

## Objective

`coverage_v3` currently asks the model to reproduce source text verbatim, then rejects the verdict when it
cannot. Run 4 proved that fails even after the fix: `CR42` still quarantines, now with
`an engaged claim needs at least one evidence span`, losing all six of its claims.

**The literature says this design is the outlier.** Three independent lines select evidence by sentence
index and never generate it: MultiVerS uses a binary classification head over sentence-boundary tokens;
Sarol et al. retrieve candidate sentences with BM25 + MonoT5 and pass those to the verifier; ReClaim emits
sentence-level citations because passage-level attribution "falls short in verifiability." A fourth,
FullCite, tested prompt-based verbatim generation head to head against post-hoc alignment and measured
Snippet-F1 **12.80% → 61.87%** (ASQA) and **6.18% → 24.23%** (BioASQ) in alignment's favour.

Target behaviour: the judge **selects** evidence; it never retypes it. Spans become verbatim by
construction, tables stop being a special case, and a failure to find evidence becomes a measured recall
miss instead of a discarded reference.

## Change / defect

### 1. Sentence-segmented sections with stable ids; the judge returns ids

`cre/f1/fulltext_reader.py` (or a new sibling — implementer's choice) and
`cre/f1/coverage_prompts_v3.py`.

Segment each section's `text` into sentences once, at read time, and give each a stable id scoped to the
section: `s1`, `s2`, … in document order. Render them into the prompt with the id visible:

```
[discussion]
  s1  Lignin-modifying enzymes are predominantly associated with ...
  s2  Thus, our finding of N-induced increases in recalcitrant soil C ...
[table]
  s1  Fungal species used in the microcosm experiments.
  s2  Species name | Code | Phyllum | Decay type | NBRC | DDBJ | Source
  s3  Armillaria cepistipes | Armi | Basidio | White | 110165 | AB907593 | W
```

A table row is one unit. That is the whole point: the model never retypes it.

Output contract replaces `evidence_spans: [{label, text}]` with:

```
"evidence_spans": [ {"label": "<reader label>", "sentence_ids": ["s2", "s3"]}, ... ]
```

The code resolves ids to text and stores **both** the ids and the resolved text on the verdict record, so
the artifact stays human-readable while provenance stays machine-checkable. An id that does not exist in
the named section is a parse error; a *wrong but existing* id is a recall/precision miss, not an error.

Segmentation must be deterministic and recorded — the segmenter's name and version go in the manifest.
Do not use a model to segment. A table section segments on row boundaries, not on `.`.

### 2. Post-hoc alignment fallback

When a reply carries quoted text instead of ids (older prompt, model drift, any reason), align it to the
section rather than rejecting it: word-level Jaccard ≥ 0.7 against each candidate sentence, take the best
match, and record `span_source: "aligned"` versus `"selected"`. Below threshold, record
`span_source: "unaligned"` and treat as item 3.

This is FullCite's measured-best strategy and it costs nothing at inference time.

### 3. A missing or unresolvable span is a RECORDED MISS, never a quarantine

Current behaviour raises, which propagates out of `coverage_judge` and quarantines the whole reference —
`P(reference lost) = 1 − (1−p)^n_claims`, so the loss concentrates on the references with the most claims,
which are the ones most likely to carry a fault. Biased deletion presenting as a quarantine rate.

Every system in the literature treats evidence-selection failure as a **recall miss**: counted, reported
as Recall@k, item retained. Sarol reports Recall@20 = 0.54 and keeps going.

**Required:** an engaged claim with no resolvable span records `evidence_spans: []` plus
`span_status: "not_found"`, and the verdict, the item and the reference all survive. Manifest gains a
counter. `PARSE_QUARANTINE` returns to meaning only "the reply was not parseable."

### 4. Amend §D — spans are RECORDED and REPORTED, they do not GATE the verdict

`TAXONOMY_AMENDMENT_2026-08-11.md` §D currently requires each span to justify its verdict standing alone,
and makes a verdict whose justification needs outside text NOT ESTABLISHED. **That is wrong, and it was my
call.** MultiVerS reports that many rationales are "context-dependent" and require surrounding document
context, "making isolated sentence selection inherently problematic," and that experts disagree on "exactly
which sentences contain the best evidence" — to the point that "systems already exceed human agreement for
sentence-level evaluation." Sarol's annotators reached κ 0.20–0.37 on evidence sentences.

A gate on something humans cannot agree about produces noise, not rigour.

**Replacement text for §D (paste over the existing §D):**

> **The evidence spans recorded for a claim are the sentences the judge relied on, read together with
> their section context.** They are recorded in full and reported, and they are the basis for evidence
> selection metrics. **They do not gate the verdict.** A verdict is not downgraded because its spans are
> incomplete; incompleteness is measured as recall, not punished as error. Evidence selection is harder
> than the verdict it supports and has low human agreement — that is a property of the task, not a defect
> of a run.

The judging rule from `3e5261d` item 4 — a rationale must not rely on text the judge did not record —
**survives as a reporting obligation**: record everything you used. It is no longer a validity condition.

### 5. Emit the metrics the redesign makes available

With ids, evidence selection becomes measurable without new annotation. Emit per run: number of engaged
claims, number with ≥1 resolved span, span count distribution, `span_status` and `span_source` tallies.
These support Recall@k and sentence-selection F1 against Sarol's Recall@20 = 0.54 and MultiVerS's SciFact
67.2 once gold spans exist.

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| section with 3 sentences | prompt | ids `s1`–`s3` rendered, document order, stable across runs |
| table section | segmentation | one id per row; no split on `.` inside a row |
| same section, two runs | ids | identical (deterministic segmenter) |
| verdict on an engaged claim | `evidence_spans` | `[{label, sentence_ids}]`, ids resolvable in that section |
| resolved span | stored record | ids **and** resolved text both present |
| id absent from the named section | route | `PARSE_QUARANTINE` (this one IS a parse error) |
| reply carrying quoted prose, Jaccard ≥ 0.7 | `span_source` | `"aligned"`, span resolved to the matched sentence |
| reply carrying quoted prose, Jaccard < 0.7 | `span_status` | `"unaligned"`, verdict and reference retained |
| engaged claim, no span found | route / counters | **not** quarantine; `span_status="not_found"`, counter incremented |
| `PMC10115774:CR42` (6 claims), live | route | any route except `PARSE_QUARANTINE`; 6 verdicts present |
| `engages_subject` False | `evidence_spans` | `[]` |
| manifest | new keys | segmenter name+version, `span_status` and `span_source` tallies |
| default (abstract) path, no `fetch_fulltext` | every output byte | unchanged from `de3e040` |
| `route([])` | return | `NO_CLAIMS` (unchanged) |

## Guardrails (do NOT change)

- **`band_prompts.py` untouched** — blob `fa01126e2b9482d450065fd70cd0eb1fea816f5c` is the freeze-chain
  root. No freeze artifact, universe fixture, `test_mint_v1.py` literal or schema pin moves. Nothing deleted.
- **Nothing may touch F2.** Seed 41 is drawn and scored at `c621a09`; any F2 rule, threshold, taxonomy or
  reason-code change spends it. This spec is F3–F7 branch only. See `CONTRADICTIONS` 42 — there is already
  an unresolved post-draw F2 rule change awaiting ZD.
- **The parser stays strict on reply shape.** One bare JSON object per reply; concatenated objects
  quarantine. Item 3 narrows what counts as a parse error; it does not relax the JSON contract.
- **`response_parser_version` bumps** (DEC-022 — independent of prompt version). Both stamped on every verdict.
- **The judge is never gold.** No F3–F7 label is machine-assigned; `proposed_route` stays blind to the
  annotator until after commit. Claude assigns no labels and curates no ground truth.
- Tri-state discipline (`is True` / `is False`); path-based module loading; restart Colab after any push;
  Drive-first I/O; evaluation precision-only (DEC-005); gold naturally occurring only.
- **`temperature=0` (DEC-046)** stays pinned, and `CONTRADICTIONS` 41 — adding a `temperature` parameter to
  `run_band` so it reaches `manifest["params"]` — should land with this commit if it has not already.

## Regression guards

Guard PMIDs `31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as
before. The two confirmed F3 cases must survive the record-shape change — this is the **third** consecutive
commit to alter the field their evidence is stored in.

## Definition of done

- Collected and passing counts measured before and after, both reported. `de3e040` self-reported
  1615 passed / 10 skipped / 29 xfailed. Do not predict; measure.
- Strict xfails first for items 1 and 3, observed failing at `de3e040`.
- Acceptance matrix verified; the `CR42` live row needs a model call and runs in Colab.
- Default abstract path proven unchanged by diff or hash.
- Pushed; report the commit SHA and both counts.

## Out of scope

- The batch prompt freeze (deferred, not cancelled — DEC-044), CONFIG, trust-boundary modules, conformance
  regen, schema re-pin.
- Extraction nondeterminism (`CONTRADICTIONS` 36/36a/36b) — measured, DEC-046 pins `temperature=0`, residual
  is a documented limitation. Not a code change.
- F5's six unimplemented seams — next spec, gated on this landing.
- Anything on the F2 branch.
- Retrieval-side changes (BM25/MonoT5 pre-selection of candidate sentences). Worth considering later; this
  spec keeps the whole section in the prompt and changes only how the judge *points* at it.

## Verification command

```
cd /Users/kamachi/cre-f3f7/citation_repair_F1_handoff
PYTHONPATH=. ../.venv_cre/bin/python -m pytest cre/f1 -q --ignore=cre/f1/.venv
```

## If something here is wrong

Four times this session a prescription of mine has been wrong about an interface I had not read: a raise
rule against an API shape (`CONTRADICTIONS` 35), a JSON array against a one-call-per-claim judge
(`CONTRADICTIONS` 39), a single-string span field against a multi-passage need (38), and a self-sufficiency
gate against a task with κ 0.20–0.37 human agreement (this spec, item 4). **State the defect and the
constraint, then choose the edit yourself. If a prescribed change does not do what this spec claims, stop
and report — do not reconcile silently.**

## Sources for the design change

- Wadden et al., *MULTIVERS*, Findings of NAACL 2022 — sentence-level rationale selection via a
  classification head; evidence F1 SciFact 67.2 / HealthVer 69.1 / COVIDFact 43.7 against label F1
  72.5 / 77.6 / 77.3; "context-dependent" rationales; systems exceed human agreement at sentence level.
- Sarol et al., *Bioinformatics* 2024, doi:10.1093/bioinformatics/btae420 (PMID 38924508) — BM25 + MonoT5
  sentence retrieval; Recall@20 = 0.54; evidence-sentence IAA κ 0.20–0.37.
- *FullCite*, arXiv 2606.07130 — prompt-based vs constrained decoding vs post-hoc alignment; Snippet-F1
  12.80% → 61.87% (ASQA), 6.18% → 24.23% (BioASQ); word-level Jaccard ≥ 0.7.
- *Cited but Not Verified*, arXiv 2605.06635 — 94%+ link validity and 80%+ relevance alongside 24–77%
  factual accuracy; surface citation metrics mask factual failure.
- ReClaim, Findings of NAACL 2025 — sentence-level citations; passage-level attribution "falls short in
  verifiability."
