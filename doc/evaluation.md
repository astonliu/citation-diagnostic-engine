# Evaluation

## The label space

Band 1 produces `F1`, `F2`, `F8`, `same_work`, `cleared`, `unscoreable`,
`unverifiable`, `human_review`. Band 2 produces `F3`, `F4`, `F5`, `F6`, `F7`, or
holds. `accurate` is the absence of a finding after every wired discriminator
has answered — it is not a positive prediction, and nothing is labelled
`accurate` that was merely never asked.

## What counts in a denominator

The denominator is not "every reference in the corpus". It is the population the
band was actually asked about, and three groups leave before it:

- **Excluded.** Band 1 answered `F1`/`F2`/`F8`/`same_work`, or the reference has
  no citing sentence or no resolvable cited identifier. These are not Band 2
  misses. Counting them would dilute every Band 2 rate.
- **Quarantined.** The citing sentence could not be parsed into anything
  judgeable — a bare marker run like `[5]`, or a contract violation. Reported,
  never scored.
- **Held.** The band declined to answer. See below.

`UNJUDGEABLE` and `NO_CLAIMS` are the two that need care.

**`UNJUDGEABLE`** means the evidence did not settle the question: retrieval was
incomplete, a discriminator was not wired, a verifier disagreed with itself, or
the coverage judge returned "cannot be determined". It is not a negative. Put it
in the denominator of a *recall* figure and you understate recall by counting
abstentions as misses; leave it out of a *precision* figure and you flatter the
system. Report it as its own count, always, and say which figures it was
excluded from.

**`NO_CLAIMS`** means the citing sentence asserted nothing empirical — a pointer
sentence, "see also". There is no claim to support or fail to support, so the
pair belongs to no accuracy figure in either direction. It is a property of the
corpus, not of the system, and its rate is worth reporting for that reason.

The route distribution over a run is therefore the first thing to read: every
rate in the paper is a ratio over those counts, and a change to it invalidates
all of them at once. `tests/characterization/` freezes that distribution over a
fixed corpus for exactly this reason.

## What is reportable today

Reportable: Band 1's F1/F2/F8 dispositions; Band 2's F6 route and the coverage
decomposition behind it; the hold and exclusion rates; the Path A / Path B split
for F5, which characterises F5 difficulty in the corpus.

**Not reportable as a headline number:**

- **F4** has a development mode that is not corpus-calibrated. Its numbers are
  for engineering, not for the Results section, and the manifest records which
  mode produced them.
- **F5** ships escalation-only (Path B). Contradiction detection runs; autonomous
  replacement (Path A) is deferred, so the "successful repair" metric does not
  apply to F5 cases.
- **F7** is pending an advisor lock on the entity authorities.

## Protocol

**F1, F2 and F8** are database-resolvable and are evaluated against a
naturally-occurring gold set with a real denominator: precision and recall,
five-fold stratified cross-validation, bootstrap confidence intervals over 1000
resamples, and no point estimate reported alone.

**F3–F7 are evaluated differently, and the difference is not a detail.** There
is no population sample and there is no recall number. A finder surfaces
candidate faults, annotators adjudicate each one, and what is reported is
**precision per stratum on the flagged set** — the same audit model F2 uses,
carried into the semantic strata. It is non-circular for the same reason: humans
decide true from false positive independently of the system.

Recall for F3–F7 is **declined, not missing**. The candidate pool is enriched by
construction, so it has no honest denominator — auditing a finder's flagged
output yields precision by construction and leaves recall undefined, because no
labelled population draw was ever made. This is a design consequence, not a
sacrifice to rarity, and it must be argued that way: do not cite F2's measured
base rate as if it established rarity for F4 or F5.

In its place, for each stratum, the pipeline is run over that stratum's
human-confirmed positive set and the fraction caught is reported, labelled
explicitly as **sensitivity on known positives** and never as population recall.

Three constraints keep that precision number meaning something:

- **The finder proposes; it never labels.** Annotators apply the taxonomy fresh
  and blind to any proposed category. Showing the proposed label collapses the
  metric into the finder agreeing with itself.
- **Annotator and system judge the same object.** The same
  `(citing_sentence, cited_pmid)` unit, the same evidence scope, the same label
  space. If the system reads an abstract and the annotator reads the full paper,
  a disagreement is ambiguous between a real fault and an evidence mismatch —
  silent bias baked into the metric, not noise that averages out.
- **Precision is conditional on the finder prompt.** A prompt change is a
  different pipeline, so the prompt is frozen and versioned with the dataset, and
  labels are keyed on pair identity rather than on the prompt's output. Whether a
  pair is an F3 is a fact about the world; a prompt change then moves the
  denominator, not the individual truth labels.

Report it as **precision of the pipeline as configured**, not as "the engine's
precision" in the abstract.

**Verifiers.** The judge is a different model family from the generator, and the
launcher refuses to start a run where it is not — see `preregistration.md`. No
model assigns a semantic label or curates ground truth; that is an invariant, not
a preference.
