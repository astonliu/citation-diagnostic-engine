# Evaluation

## The label space

Band 1 produces `F1`, `F2`, `F8`, `same_work`, `cleared`, `unscoreable`,
`unverifiable`, `human_review`. Band 2 produces `F3`, `F4`, `F5`, `F6`, `F7`, or
holds. `accurate` is the absence of a finding after every wired discriminator
has answered — it is not a positive prediction, and nothing is labelled
`accurate` that was merely never asked.

The backticked strings above are literal record values: they are what a
prediction file actually contains, and they are what a `grep` over one must
match. `doc/taxonomy.md` maps each code to the category name this document and
the paper use in prose — `F6` is Insufficient Support — and
`cde.taxonomy.CATEGORY_NAMES` is that same table in code. The names are for
reading; the codes are the data, and only the codes are ever written to a
record.

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

The four evaluation designs estimate **different quantities and must never be
pooled into one number**. Route and selected-flag audits estimate confirmation
among reviewed flags. The constructed panels estimate detection on hunted
positives and false alarms on selected controls. No design estimates population
prevalence, population recall, repair accuracy, or end-to-end eight-class
performance.

- **Unresolvable Reference and Retracted Source** — exhaustive adjudication of
  locked route-specific flagged strata. Flag precision for the audited route
  only; not recall, not a per-paper rate.
- **Wrong Reference** — held-out seed 47, after earlier development seeds were
  spent. Precision among flags, characterising the seed 47 configuration rather
  than every later pipeline version.
- **Insufficient Support and Overstatement** — an *exploratory selected-flag
  audit*, not end-to-end precision. A citation-marker parsing defect affected 114
  of 373 references carrying a claim-support finding, so the audit selected rows
  with confirmed marker attribution and, for Insufficient Support, available full
  text. Report determinate precision and the worst-case convention that counts
  every ambiguous judgment as incorrect, side by side; never the determinate
  figure alone.
- **Misattribution, Supersession, Wrong Entity** — constructed matched panels.
  Positives were located by targeted search and human-confirmed; controls were
  built to resemble faults on specified surface dimensions. Detection is
  conditional on hunted positives, and the control result describes the panels,
  not deployment false-alarm rates. Report per stratum; no pooled estimate,
  because the strata were assembled differently.

**Denominator care in the panels.** Two control counts are both correct and are
easy to swap by accident. The control arms as *built* are 20 Misattribution,
**16** Supersession, 20 Wrong Entity — 56 candidate controls. The false-alarm
denominators as *scored* are 20, **15**, 20 — 55 fully verified controls. The
difference is one Supersession control that remained source-linkage-indeterminate
and is excluded from the false-alarm denominator, not from the panel. Use 16 when
describing how the arm was constructed; use 15 in any rate whose denominator is
controls the system was scored against. Row-level intervals assume more
independence than the data provide: the 16 Supersession positive rows represent
eight distinct superseded sources, and two sources account for nine of them.
Supersession is most honestly read by distinct source.

**One-sided cues are a property of the panels and belong in every report of
them.** Only the Wrong Entity cited-title cue was ablated; the run without titles
returned the same 37/40 and identical item-level verdicts. The others stand
unablated and must be disclosed rather than left unmentioned.

## Protocol

**Unresolvable Reference, Wrong Reference and Retracted Source** are
database-resolvable and are evaluated against a
naturally-occurring gold set with a real denominator: precision and recall,
five-fold stratified cross-validation, bootstrap confidence intervals over 1000
resamples, and no point estimate reported alone.

**The five semantic categories are evaluated differently, and the difference is
not a detail.** There
is no population sample and there is no recall number. A finder surfaces
candidate faults, annotators adjudicate each one, and what is reported is
**precision per stratum on the flagged set** — the same audit model Wrong
Reference uses,
carried into the semantic strata. It is non-circular for the same reason: humans
decide true from false positive independently of the system.

Recall for the five semantic categories is **declined, not missing**. The candidate pool is enriched by
construction, so it has no honest denominator — auditing a finder's flagged
output yields precision by construction and leaves recall undefined, because no
labelled population draw was ever made. This is a design consequence, not a
sacrifice to rarity, and it must be argued that way: do not cite Wrong
Reference's measured base rate as if it established rarity for Overstatement or
Supersession.

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
  pair is a Misattribution is a fact about the world; a prompt change then
  moves the denominator, not the individual truth labels.

Report it as **precision of the pipeline as configured**, not as "the engine's
precision" in the abstract.

**Agreement.** None is reported. Annotation was principally by one adjudicator,
so there is no double-annotated overlap to compute a statistic over — neither
Cohen's κ nor Gwet's AC1. Independent duplicate annotation is the first item
of future work. See `preregistration.md`.

**Verifiers.** The judge is a different model family from the generator, and the
launcher refuses to start a run where it is not — see `preregistration.md`. No
model assigns a semantic label or curates ground truth; that is an invariant, not
a preference.
