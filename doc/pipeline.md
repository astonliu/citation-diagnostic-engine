# Pipeline

A citation goes through two bands. The first asks whether the reference is the
work it says it is; the second asks whether the citing sentence is a fair use of
that work. They are separate because they fail differently: Band 1 is answerable
from databases and is deterministic, Band 2 needs the text of both papers and a
model, and mixing them would let a lookup outage look like a judgment.

## Band 1 — reference identity

Input is PMC JATS XML. `refs/parser` extracts each reference and the sentence
that cites it. For every reference, `refs/lookup` resolves the claimed PMID and
`refs/confirm` searches PubMed, Crossref and OpenAlex for the claimed title.
`refs/decide` combines the two into one label.

What stops an item here: **F1** (no such work exists), **F2** (the identifier
names a different work), **F8** (the work was retracted more than 31 days before
the citing paper appeared), `same_work` (a variant of the same work — a
preprint, a translation), `unscoreable` (nothing to compare, e.g. no title), and
`unverifiable` (no identifier at all).

The important asymmetry: a search that did not *answer* is never evidence of
absence. An Unresolvable Reference finding is read by authors as an accusation
whether or not it is phrased as one, so it requires all three
databases to have replied and none to have found the title. A transport failure,
a rate limit, or a skipped search holds the reference instead. The same rule
appears again in Band 2 and is the single most consequential design decision in
the system.

## Pre-band disposition

`refs/preband_disposition` writes the Band 1 answer as an artifact keyed by
`citation_id`, and `refs/preband_contract` is what Band 2 reads it through. The
contract exists to prevent a specific silent failure: if the two bands are run
over different corpora, every pair is excluded fail-closed and the run completes
with a valid manifest and an empty result. A zero-overlap join aborts before any
output file is created.

Only `cleared` references reach Band 2.

## Band 2 — claims, coverage, discriminators

`claims/band` decomposes the citing sentence into atomic claims. `claims/abstracts`
or `claims/fulltext` retrieves the cited work's text, and a coverage judge scores
each claim against it as established / not established / cannot be determined.
`claims/cocitation` supplies the overlay for sentences citing several references
at once: a claim a sibling reference established is the group's coverage, not
this reference's gap.

`diagnose/engine` then applies the hierarchy — **F7** (wrong entity) > **F6**
(partial support) > **F4** (overstatement) > **F3** (misattribution) > **F5**
(superseded) — over typed assessments, with no I/O of its own. Only the
discriminators whose seams are actually wired can produce a finding; an unwired
one is silence, never a confident negative.

What stops an item here: no atomic claims, an unreadable citing sentence
(quarantined rather than judged), a coverage verdict of "cannot be determined",
an unjudgeable discriminator, or a contract violation — a non-verbatim span, a
content-hash mismatch, a malformed model reply — which raises rather than
degrading into a label.

## Output

One record per citation pair, hash-chained, with a manifest that pins the prompt
versions, the parser versions and the digests of every module that could govern
a number on that run's wiring.
