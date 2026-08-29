# Preregistration

The analysis plan was fixed before annotation began so that results could not be
reverse-justified. The registered plan is v2.2 (29 May 2026); the git commit
timestamp is the registration date, and the manuscript cites it by commit hash.
Amendments are additive and dated — the original is never edited — and the
material ones are folded in below.

## The claim

One primary claim, two supporting properties. A focused contribution is one
defence, not three rejection vectors.

**Primary.** A fine-grained eight-category taxonomy (F1–F8) enables more
accurate biomedical citation diagnosis than coarse three-label (Sarol et al.) or
four-label (SemanticCite) schemes. Tested by mapping F1–F8 down to each and
comparing precision, recall, macro-F1 and combined diagnosis+repair success on
the *same* examples, with a paired test (McNemar / paired bootstrap) on
per-example correctness.

**Supporting property 1.** The pipeline proposes evidence-backed replacement
citations, reported as top-1 and top-3 accuracy against held-out gold PMIDs. A
system number, not pitched as a contribution.

**Supporting property 2 — withdrawn.** Generation mode (finding citations for
uncited claims) was registered as exploratory with the contribution explicitly
not depending on it. Amendment 1 withdrew it from the submission: the
diagnosis→repair loop is the uncontested contribution, citation recommendation
is a mature field, and a lightly-evaluated generation section is a net negative.
It survives as a Future Work paragraph, not a result. Baseline 4 and the whole
of the original §6 went with it.

## Baselines, fixed in advance

Sarol et al.'s released pipeline (BM25 + MonoT5 + MultiVerS); zero-shot Claude
with a bare prompt; a SemanticCite-style four-class collapse; a random/lexical
retrieval floor. Ablations: retrieval disabled, F1–F8 collapsed to three labels
and to four.

Two Sarol comparisons, reported separately and never averaged. **A**: our
pipeline on Sarol's corpus mapped to their three labels — the controlled
head-to-head that shows a fine-to-coarse result is not a corpus artifact. **B**:
Sarol's pipeline on CitationRepair-1000 — characterises task difficulty, and is
not a superiority claim.

## Power

1000 released examples (annotate 1100–1200 to absorb reconciliation losses),
about 125 per category. The paired-proportion test needs roughly 150–250
discordant pairs for 80% power at α = 0.05; at n = 1000 a 10–15% discordance
rate yields 100–150, which is at or near threshold. Fixed in advance: n = 1000
is sufficient at the full-dataset level and no expansion is planned, and the
500-example August slice is **underpowered for a significance claim** — off the
slice the primary claim is a point estimate with a confidence interval, with
significance deferred.

## Protocol

Five-fold stratified cross-validation. Bootstrap confidence intervals over 1000
resamples; no point estimate reported alone. A contamination control using
citations from papers published after the model's training cutoff. Exact model
strings and snapshot dates pinned.

**Verifiers.** Amendment 2 added a third: two frontier models from different
families plus Med-V1, a 3B biomedical specialist. Two frontier RLHF models may
share systematic biases, so their agreement alone does not answer the
LLM-as-judge circularity objection; a different architecture and training
paradigm does. Three-way agreement is a standalone robustness metric.
Disagreements are adjudicated by the human gold subset, never by any model.

This supersedes the original §6 note about using a different model family as a
secondary judge — cross-family judgment is now structural rather than a
safeguard layered on top. The launcher still enforces it: a run whose judge is
the same family as its generator is refused unless a dated amendment or a
recorded scope ruling permits it, and a same-family judge never runs silently.

**Validity anchor.** Amendment 4: a 100-example human-adjudicated gold subset,
stratified across F1–F8, is the primary anchor, with three-way LLM agreement
secondary. The fallback ladder was fixed in advance to avoid a post-hoc choice:
if the full 100 cannot be adjudicated, adjudicate only the three-way
disagreements and report it as a disagreement-focused check; if neither is
feasible, validity rests on cross-family agreement plus held-out natural-stratum
generalization without human adjudication.

## Agreement

**What the paper reports, and it is not an agreement statistic.** Gold-standard
annotation was performed principally by one adjudicator, who was involved in
developing the taxonomy. No inter-annotator agreement estimate is reported,
because there is no second independent pass to compute one over.

That supersedes everything this section previously said about a reliability
statistic. Earlier drafts specified Cohen's κ gates — a ≥ 0.60 IAA threshold, a
κ ≥ 0.70 "good" band, an F3/F6 and F4/F6 pre-pilot, and a second-annotator
calibration gate — and a later revision replaced them with Gwet's AC1. Neither is
in force. No κ gate is applied, and no AC1 figure is reported, because neither
statistic has a double-annotated overlap to be computed on.

Several category boundaries are interpretive and are the ones a second annotator
would be needed to test: same versus related work, historical reporting versus
current endorsement, and plausible versus incorrect entity scope. Written decision
rules and recorded rationales improve auditability; they do not replace
independent duplicate annotation.

The highest-priority item in future work is independent, category-specific
annotation with disagreement adjudication and explicit reporting of reliability.

## Dataset

The real-error stratum (Retraction Watch, PubPeer) is held out as a dedicated
test partition, and the primary claim is reported separately on natural-only
test examples. F1, F2 and F8 are resolved by database lookup before any
classifier. Stratified across all eight categories before the split.

**On synthetic data, which is settled.** The registered §8 says
naturally-occurring errors only; the 9 June research-plan delta appeared to
reinstate a synthetic stratum. The closed F3–F7 evaluation decisions (3 July
2026) resolve it: the synthetic-injection, stratification and denominator
machinery **is amended out for F3–F7**, which are audited for precision on a
flagged set rather than sampled against a denominator, and
naturally-occurring-only continues to govern any denominatored gold set that
does exist. No synthetic example is generated anywhere in the code, and none
should be.

**A documented deviation from registered F3-DI2.** That registration defined the
F3–F7 gold set as a naturally-occurring sample with a denominator. It is amended
to the precision-on-flagged-set audit described in `evaluation.md`. Everything
else converts unchanged: the taxonomy, the decision rules, the annotator and
adjudication protocol, and every confirmed positive already sourced. The F3–F7
pre-pilot κ gate goes with it — see Agreement above.

**One item is genuinely open and is not settled here.** The κ half of the queued
amendment is moot: κ is no longer reported on any basis, adjudications or
independent labels. What remains open is the framing of sensitivity on known
positives — whether it is reported as a diagnostic on the confirmed set or as a
lower bound on population recall. `evaluation.md` currently states the diagnostic
reading: sensitivity on known positives, never population recall. ZD supplies the
final wording; it is explicitly not to be drafted by an implementer. It is recorded here because this is the only place it is written
down.

**An invariant, not a preference.** No model assigns a semantic label or curates
ground truth at any point in this project.

## What would change the plan

Fixed in advance: a 2026 biomedical citation repair-with-replacement paper
appearing before submission means re-pitching as a comparison against it; a
pre-pilot κ below 0.60 on a pair after the decision rules means merging that
pair and reporting the pilot as evidence; zero-shot Claude beating Sarol by more
than 10 F1 on the controlled comparison makes that a headline result alongside
the taxonomy.
