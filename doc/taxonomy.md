# Taxonomy

Eight categories. The taxonomy stayed at eight only because the collisions that
collapsed an earlier pilot to five were pre-empted with decision procedures an
annotator applies *before* exercising judgment. Those procedures are the point
of this document; the category names on their own are not enough to annotate
with.

| | category | decided by | stage |
|---|---|---|---|
| F1 | Fabricated | identifier and metadata fail to resolve anywhere | Band 1 |
| F2 | Wrong reference | metadata resolves, to a different work | Band 1 |
| F3 | Misattribution | full claim coverage, wrong origin | Band 2 |
| F4 | Overstatement | strength/modality mismatch | Band 2 |
| F5 | Stale / superseded | contradiction + supersession gate | Band 2 |
| F6 | Partial support | any unestablished claim after complete retrieval | Band 2 |
| F7 | Wrong entity | drug / gene / disease mismatch | Band 2 |
| F8 | Retracted | retraction notice + timing gate | Band 1 |

F1, F2 and F8 are database-resolvable and never reach a human classifier. Human
and model judgment is confined to F3–F7.

## The three collisions

**F3 vs F6.** Coverage first, provenance second. Decompose the sentence into
atomic claims; label each as established or not by the cited paper. *Any*
unestablished claim, given complete retrieval, is F6 — including zero coverage.
F3 applies only when every claim is established and the cited work is
nevertheless not the origin of them. F3 is never inferred from a lack of
support, and incomplete retrieval holds rather than choosing either.

The subtle case is added specificity: a claim saying "in ApoE-deficient mice"
against a paper that discusses mice generally is F6. The general finding is
supported; the specific model is not. One word of unconfirmed specificity is
enough.

**F4 vs F6.** The axis is strength versus coverage. If the paper engages the
claim's subject but at a weaker modality — correlation cited as causation, "may
reduce" cited as "reduces", observational cited as interventional — that is F4.
If complete evidence leaves a claim unestablished, that is F6. In one line:
"wrong strength on a claim it addresses" is F4; "a claim not established" is F6.

F4 turns on the *cited paper's own* hedging, not the field's. A paper that
reports its finding confidently while noting that the broader literature is
mixed is being cited accurately when the claim states that finding. The citation
is to the paper, not to the consensus.

**F5 vs F8.** Check for a formal notice first. Any retraction, correction or
expression of concern routes to F8 regardless of anything else — F5 never
overrides a publisher notice. F8 additionally requires the citing paper to have
appeared at least 31 days after the notice; a smaller gap is excluded as
indeterminate rather than labelled, because publication lags submission and a
short positive gap cannot show the authors could have known. The 31 days is a
confidence threshold, not part of the definition. `retraction_date` always means
the notice date, never the retracted paper's own publication date.

## F5 and the supersession gate

F5 presumes the cited paper *did* support the claim and that the field has since
moved. Detection is a directional contradiction on the same outcome in a
comparable population.

Three criteria, and **all three** must fire for Path A:

1. **Directional contradiction** — a reversal of direction or conclusion, not a
   refinement of magnitude. Metformin at 1.5% revised to 1.1% is a refinement.
2. **Date gap ≥ 2 years**, which filters rapid replication disputes and
   preprint-to-journal drift.
3. **Evidence-tier upgrade** — the superseding work sits at an equal or higher
   OCEBM tier. A Cochrane review over a cohort study fires; a case series over a
   large RCT does not.

**Path A** proposes the superseding paper as a replacement. **Path B** surfaces
both papers with their contradicting claims quoted, sets an escalation flag, and
proposes nothing — the author adjudicates. Path B is not a failure; it is the
correct behaviour when the evidence is genuinely ambiguous.

*This build ships Path B only.* `deploy_path_a` is locked off, so a case may be
computed as Path-A eligible and still be emitted as Path B. The two counts are
reported separately, and the split rate is itself a finding about how hard F5 is
in this corpus.

An older four-criterion formulation, in which each criterion was independently
sufficient, is superseded by the gate above. The code keeps it reachable as
`path_a_rule="any_sufficient"`; `"all_must_fire"` is the default and the
operative rule.

## ACCURATE

Not a positive prediction. It is what remains when every wired discriminator has
answered and none raised a finding. A pair nothing was asked about is held, not
cleared.
