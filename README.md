# Citation Diagnosis Engine

Fine-grained diagnosis of citation faults in biomedical papers: not just whether
a citation is wrong, but *how*, with the evidence that says so.

Paper: preprint in preparation — this line will carry the link when there is one.

## What it does

Most citation checking answers one question: does this reference support this
sentence, yes or no. That answer is not actionable, because "no" covers a
fabricated reference, a transposed identifier, a real paper that says something
weaker than claimed, and a real paper that has since been overturned — and those
need four different fixes.

`cde` splits the question in two. **Band 1** asks whether the reference is the
work it claims to be, which databases can answer: fabricated (F1), wrong
reference (F2), retracted before the citing paper appeared (F8). **Band 2** asks
whether the citing sentence is a fair use of that work, which needs both texts:
misattribution (F3), overstatement (F4), superseded findings (F5), partial
support (F6), wrong entity (F7).

Every finding comes with the atomic claims the sentence was decomposed into and
the verbatim spans from the cited paper that the verdict rests on. The system
abstains rather than guessing: an unwired discriminator, an incomplete
retrieval, or a search that never answered produces a hold, never a confident
negative. This matters more than it sounds — F1 accuses an author of inventing a
reference, and the cost of one false accusation is not symmetric with the cost
of a miss.

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
- `cde/runtime/` — transports, rate limits, cost ledgers, the governed
  production launcher, and single-packet sandboxes.

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

These are real and current, not hypothetical.

**Evidence scope is PMC-only.** Band 2 reads abstracts, and full text only where
PMC has it. A claim supported in a paper's Results but not its abstract is
recorded as unestablished when only the abstract was available. The evidence
scope is stamped on every record, so this is visible per-pair — but it means F6
rates are an upper bound on abstract-scoped runs.

**F4 has a non-reportable development mode.** Its strength judgments are not
corpus-calibrated. The mode is recorded in the run manifest; numbers from it are
for engineering, not for a Results section.

**F5 ships escalation-only.** Contradiction detection and the three-criterion
supersession gate run, but autonomous replacement (Path A) is locked off in this
build, so a case can be computed Path-A eligible and still be emitted as Path B.
The "successful repair" metric therefore does not apply to F5.

**F7 is pending an advisor lock** on the entity authorities. It runs, and its
findings are not reportable until that lock.

**Several `freeze/` trust-boundary roles are specified but not instantiated**, so
`mint_v1 --config` is gated: the role→module manifest cannot be completed and
the bootstrap fails closed rather than minting a config it cannot verify.

**Twelve tests in the suite fail at HEAD.** About seven share one cause: the
annotation queue was deliberately narrowed to pairs carrying a finding, and
those tests still assert the older behaviour. They are stale expectations of
deliberate changes, not defects in the engine, and they are documented rather
than quietly deleted.

## Citation

```bibtex
@software{cde,
  author  = {Liu, Zhandong},
  title   = {Citation Diagnosis Engine: fine-grained diagnosis of
             biomedical citation faults},
  year    = {2026},
  url     = {https://github.com/}
}
```

## Contact

Zhandong Liu — zhandong.liu@bcm.edu
