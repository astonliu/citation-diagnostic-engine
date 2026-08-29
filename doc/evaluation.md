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

5-fold stratified cross-validation; bootstrap confidence intervals over 1000
resamples, with no point estimate reported alone; exact model strings pinned in
the run manifest. The fine-vs-coarse comparison is a paired test on per-example
correctness, powered to detect a discordant-pair rate ≥ 0.10 at n = 1000. See
`preregistration.md`.

The judge is a different model family from the generator, and the launcher
refuses to start a run where it is not — see §6 of the preregistration. The
number that check produces is recorded beside the result, not asserted in prose.
