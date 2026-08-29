# Citation Diagnostic Engine

Fine-grained diagnosis of citation faults in biomedical papers: not just whether
a citation is wrong, but *how*, with the evidence that says so.

Paper: Aston Liu and Kirk Roberts, "Diagnosing Biomedical Citation Faults: An
Eight-Category Taxonomy and Multi-Design Evaluation." This line will carry the
link on publication.

## What it does

Most citation checking answers one question: does this reference support this
sentence, yes or no. That answer is not actionable, because "no" covers a
reference that will not resolve, a transposed identifier, a real paper that says something
weaker than claimed, and a real paper that has since been overturned — and those
need four different fixes.

`cde` splits the question in two. **Band 1** asks whether the reference is the
work it claims to be, which databases can answer: Unresolvable Reference, Wrong
Reference, and Retracted Source. **Band 2** asks whether the citing sentence is a
fair use of that work, which needs both texts: Wrong Entity, Insufficient
Support, Overstatement, Misattribution, and Supersession.

The full precedence order is Unresolvable Reference → Wrong Reference →
Retracted Source → Wrong Entity → Insufficient Support → Overstatement →
Misattribution → Supersession. Each citation receives exactly one primary label,
naming its first point of failure.

Every finding comes with the atomic claims the sentence was decomposed into and
the verbatim spans from the cited paper that the verdict rests on. The system
abstains rather than guessing: an unwired discriminator, an incomplete
retrieval, or a search that never answered produces a hold, never a confident
negative. This matters more than it sounds: an Unresolvable Reference finding
sits one careless sentence away from accusing an author of inventing a source, so
the output says "could not be resolved in the sources consulted" and stops there.
Index coverage is incomplete, and regional, non-English, dataset and supplement
citations are the ones it misses.

## Setup

```
conda create --name cde python=3.11
conda activate cde
pip install -r requirements.txt
```

Keys go in a `.env` (never committed):

```
NCBI_API_KEY=...          # PubMed / PMC. Raises the rate limit from 3/s to 10/s.
ANTHROPIC_API_KEY=...     # claim extraction and coverage judging
OPENAI_API_KEY=...        # the judge, deliberately a different model family
OPENALEX_MAILTO=you@...   # OpenAlex polite pool
```

## Quickstart

Diagnose one PMC document:

```
export NCBI_API_KEY=... ANTHROPIC_API_KEY=...
echo PMC7000001 > /tmp/ids.txt
script/download-data.sh /tmp/ids.txt data/raw
script/run-pipeline.sh data/raw results/demo
script/evaluate.sh results/demo
```

`evaluate.sh` prints the route distribution, the disposition breakdown and the
terminal outcomes — the counts every reported rate divides by:

```
route
  F6_FLAGGED                                        3
  FULL_COVERAGE                                     1
  HELD_LOW_CONFIDENCE                               1
  NO_CLAIMS                                         2
```

Per-pair records land in `results/demo/judgment_predictions.jsonl`, one line per
citation, with the claims, the evidence spans and the hash chain.

## Code structure

- `cde/refs/` — Band 1. Identifier resolution, title search across PubMed,
  Crossref and OpenAlex, the same-work rule, and the retraction timing gate.
- `cde/claims/` — Band 2 front end. Atomic-claim decomposition, abstract and
  full-text retrieval, sentence spans, coverage judging, co-citation grouping.
- `cde/diagnose/` — the typed decision engine and the five discriminators. The
  engine does no I/O: injected assessors produce typed evidence and it applies
  the F7 > F6 > F4 > F3 > F5 hierarchy deterministically.
- `cde/freeze/` — artifact integrity. Canonical serialisation, the frozen prompt
  seals, the trust-boundary bootstrap.
- `cde/runtime/` — transports, rate limits, cost ledgers, and the governed
  production launcher.

## Data

`data/corpus.jsonl` and `data/claims_test.jsonl` are the Sarol et al. comparison
set — cited abstracts and claims with gold coverage labels — and are small
enough to commit. `script/download-data.sh` fetches PMC Open Access XML for a
list of PMCIDs into `data/raw/`, which is gitignored: the corpus is large and
the upstream copy is authoritative. Field-by-field schemas for every file the
repo reads or writes are in `doc/data.md`.

## Documentation

- [`doc/pipeline.md`](doc/pipeline.md) — what each stage does and what stops an item.
- [`doc/taxonomy.md`](doc/taxonomy.md) — F1–F8 and the decision rules for the three confusable pairs.
- [`doc/data.md`](doc/data.md) — the schema of every jsonl, field by field.
- [`doc/evaluation.md`](doc/evaluation.md) — the label space, and what belongs in a denominator.
- [`doc/preregistration.md`](doc/preregistration.md) — the analysis plan, fixed before annotation.

## Limitations

These are real and current, not hypothetical. They follow the paper's own
limitations section; where this file and the paper differ, the paper governs.

**No design here estimates population prevalence, population recall, repair
accuracy, or end-to-end eight-class performance.** The four evaluation designs
estimate different quantities and must not be pooled: route and selected-flag
audits estimate confirmation among reviewed flags; the constructed panels
estimate detection on hunted positives and false alarms on selected controls.

**No baseline.** Nothing here demonstrates that the full architecture beats
simpler metadata rules, string matching, or a general-purpose claim-verification
prompt. That comparison is future work.

**Annotation was principally by one adjudicator**, who was involved in
developing the taxonomy. Several boundaries are interpretive — same versus
related work, historical reporting versus current endorsement, plausible versus
incorrect entity scope. Written rules and recorded rationales improve
auditability, but they are not a substitute for independent duplicate annotation,
and no agreement estimate is reported.

**Model assistance entered both candidate discovery and diagnostic judgment.**
Different model families and human confirmation reduce direct self-scoring but do
not establish independence: models may share training data and interpretive
priors.

**A citation-marker parsing defect affects the claim-support results.** In the
52-article run it affected 114 of 373 references carrying a claim-support
finding, 100 of them where a marker followed terminal punctuation. The
Insufficient Support and Overstatement numbers are therefore an exploratory
selected-flag audit over rows with confirmed marker attribution, not category-wide
precision.

**The semantic panels carry one-sided cues.** All five multi-reference Wrong
Entity items are positives; two no-PMID Misattribution references and one
statistics/methods example occur only among positives. Only the cited-title cue
was ablated — repeating the 40-item Wrong Entity run without titles gave the same
37/40 and identical item-level verdicts. The others were not.

**The Supersession arm is short and clustered.** It contributed 16 positives
against a declared 20, and those 16 rows represent only eight distinct superseded
sources, with two sources accounting for nine rows. One Supersession control
remained source-linkage-indeterminate and is excluded from the false-alarm
denominator, leaving 55 fully verified controls rather than 56. Row-level
confidence intervals treat rows as independent where sources, citing articles and
faulty identifiers recur.

**Evidence scope is PMC-only.** Band 2 reads abstracts, and full text only where
PMC has it. The scope is stamped on every record, so it is visible per-pair.

**Repair is not evaluated.** Each diagnosis maps to a corrective action, and F5
ships escalation-only: contradiction detection and the supersession gate run, but
autonomous replacement is locked off in this build. Whether a proposed repair
identifies the right work, retrieves an appropriate replacement, and preserves
the claim without overstatement is untested.

**The freeze subsystem is deferred, not live.** Several trust-boundary roles are
specified but not instantiated, and `MINT_INPUTS.json` does not exist in this
tree, so `mint_v1 --config` cannot run. What it still carries is the two sealed
prompt packages — the evidence that the prompts were frozen before the graded
run.

**Overstatement has no worked example** in the per-category case set
(`tests/characterization/test_taxonomy_cases.py`). Its decision rule is specified
and unit-tested; what is missing is a published citing/cited pair to point at. The
case is a strict `xfail` rather than an invented example, so the gap fails loudly
instead of looking complete.

## Citation

```bibtex
@inproceedings{liu2026cde,
  author    = {Liu, Aston and Roberts, Kirk},
  title     = {Diagnosing Biomedical Citation Faults: An Eight-Category
               Taxonomy and Multi-Design Evaluation},
  year      = {2026},
  note      = {Venue and DOI to be added on publication}
}
```

## Contact

Aston Liu<sup>1,2</sup>, Kirk Roberts, PhD<sup>2</sup>

1. Kinkaid School, Houston, TX, USA
2. McWilliams School of Biomedical Informatics, UTHealth Houston, Houston, TX, USA
