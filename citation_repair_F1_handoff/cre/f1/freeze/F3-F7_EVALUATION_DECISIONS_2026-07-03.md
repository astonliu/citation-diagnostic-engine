# F3–F7 Evaluation Methodology — Decisions (2026-07-03)

## Project one-liner
CRE diagnoses and repairs faulty citations across an F1–F8 taxonomy. Novelty is
repair where prior systems (BibAgent, Sarol, Topaz/CITADEL) stop at detection.
Aston (ZD), Dr. Roberts's lab, arXiv preprint Aug 1.

## What this is
Closed decisions settling the F3–F7 evaluation design. These supersede the
denominatored naturally-occurring gold-set plan for F3–F7 (F3-DI2 and the
associated pre-pilot gate). F1/F2/F8 deterministic pre-classifier design is
unchanged. Do not relitigate.

---

## Closed decisions

### 1. Annotators are available, plural, to annotate
Dr. Roberts confirmed annotators are available to **annotate** the dataset, not
to construct it. This reverses the prior solo-annotation assumption. Reliability
is **inter-annotator agreement (Cohen's κ) on the adjudications**, computed on an
overlapping slice that two annotators both label. Confirm the count and the
double-annotated overlap before annotation starts, since that fixes the
reliability number in the paper.

### 2. Evaluation design: extend the F2 precision-on-flagged-set model to F3–F7
The finder (few-shot LLM + CRE) surfaces candidate faults. Annotators adjudicate
each candidate. Report **precision per stratum** on the flagged set. This is the
F2 audit model applied to the semantic strata. It is non-circular for the same
reason F2 is: humans decide true vs false positive independently of the system.

### 3. The LLM finds candidates; it does not label them
"Make the dataset" means **assemble unlabeled candidate items**, not sort them
into F3/F4/F5/... and hand the annotator a pre-sorted pile to confirm. The
annotator applies the taxonomy fresh and **blind to any proposed category**. If
the finder's label is retained at all, it is logged separately and revealed only
after the annotator commits, for disagreement analysis. Showing the annotator the
proposed label collapses the metric to the finder agreeing with itself (anchored
confirmation), which is circular against the system under test.

### 4. Precision-only; recall declined; sensitivity-on-positives as the bound
Population recall for F3–F7 is not measured. The candidate pool is enriched by
construction, so it has no honest denominator, the same wall F2 hit. This is not
a sacrificed number, it was never affordable at citation-fault base rates, and
the contribution is repair, not detection. Precedent: Topaz/CITADEL reports
precision on the flagged set, not recall against ground truth.

Cheap insurance instead of silence: for each stratum, run CRE over that stratum's
**human-confirmed positive set** and report the fraction caught. Label it
explicitly as **sensitivity on known positives, a lower bound, not population
recall**.

### 5. Justify precision-only by DESIGN, not by rarity-per-stratum
The load-bearing, stratum-agnostic argument is the evaluation design: auditing the
finder's flagged output rather than a random population sample yields precision by
construction, and recall is undefined because no labeled population draw was made.
Write this **once** as a unified rationale. Do **not** cite F2's ~0.1% / ~125k
figure as if it establishes rarity for F4 or F5; that is extrapolation, not a
measurement. Use F2's measured base rate as the exemplar of the regime, not a
universal constant.

### 6. Reporting honesty constraints
Report the number as **precision of the CRE-plus-finder pipeline, as configured**,
not "CRE precision" in the abstract, because it is conditional on the finder.
State scope plainly: precision-audited detection feeding repair, recall out of
scope for feasibility, sensitivity-on-positives as the bound.

### 7. Utility despite rarity
Rarity is not the axis of judgment. Rare per citation is not rare in count
(hundreds of thousands to millions absolute across the literature). Value is in
the tail (faulty citations under clinical or mechanistic claims propagate) and in
triage (at ~17% precision against a ~0.1% base rate, ~85× concentration of human
attention: an editor finds one real fault by checking ~6 candidates, not ~1000
references). The base rate itself is a contribution nobody has measured. Repair
raises value per catch above detection-only tools.

Scope it honestly: **precision carries the entire utility argument**, so report
precision per stratum and let the useful strata stand on their own rather than
claiming uniform utility. If a stratum lands at ~2% precision the triage value
erodes and it should not be pitched with the strong strata. Pitch: rare,
consequential, unquantified, now triageable and repairable. Not "citation errors
are a huge problem," which the base rate would contradict.

### 8. Venue and sequencing
JAMIA Open is comfortable with applied NLP plus disclosed limitations.
Bioinformatics scrutinizes evaluation completeness harder, so the repair result
must carry real weight there. **Get the F3–F7 repair numbers first, then
pressure-test the paper against them.** If repair lands, JAMIA Open is a
formality and Bioinformatics is live. If repair is thin, no framing rescues it,
and the fix is more confirmed repair cases, not better wording.

### 9. Prompt-conditionality: small batch, freeze prompt, item-keyed labels
The precision number is conditional on the exact finder prompt. Changing the
prompt is a different pipeline, so old flagged sets and their precision no longer
describe the current system. Annotation is the expensive resource; the prompt is
cheap to change. Therefore:

- **Start with a small candidate batch** to expose the prompt's failure modes and
  stabilize it. Freeze the prompt. Then annotate at volume against the frozen
  version.
- **Pin and version the prompt string with the dataset**, the way the
  preregistration pins model strings.
- **Annotate at the item level, keyed on pair identity** `(citing_sentence,
  cited_pmid)`, not on the prompt's output. Whether a pair is an F3 is a fact
  about the world, not about the prompt. A prompt change then costs only the
  newly surfaced delta; existing labels are reused. What changes is the
  denominator (the flagged set), not the individual truth labels.

### 10. Annotation must mirror the object the system scans (verdict stays blind)
Annotator and system must judge the **same object**, or the precision number
measures nothing. Three things must match:

- **Unit**: the annotator labels the same `(citing_sentence, cited_pmid)` pair
  the system flags, not the whole paragraph or the reference in general.
- **Evidence**: the annotator sees the same material the system reasoned over
  (extracted atomic claims plus the cited paper's relevant text). If the system
  works from the abstract and the annotator reads the full paper, a disagreement
  is ambiguous between a real fault and an evidence mismatch. This is silent bias
  baked into the metric, not noise that averages out.
- **Label space**: the same F3–F7 taxonomy at the same decision points.

Mirror the unit, evidence scope, and label space. Do **not** mirror the system's
reasoning steps, and never show the proposed category. Same object and same
evidence, independent judgment. This is the F2 audit discipline carried forward.

---

## Preregistration implications
This is a documented deviation from registered **F3-DI2** (the gold evaluation set
as a defined naturally-occurring sample with a denominator). Amend F3-DI2 to adopt
the precision-on-flagged-set audit for F3–F7, or frame the audit as a
supplementary precision metric alongside it. The synthetic-injection /
stratification / denominator machinery for F3–F7 is amended out. Fold this into
the preregistration amendment already queued (naturally-occurring-only alignment).
Everything else converts directly: taxonomy, decision rules, annotator and
adjudication protocol, and every confirmed positive already sourced.

## Superseded
- Solo-annotation assumption (reversed by decision 1).
- Denominatored naturally-occurring gold set for F3–F7 and its pre-pilot κ gate
  (replaced by decisions 2–5).

## Open items (priority order)
1. Confirm annotator count and the double-annotated overlap slice with Roberts.
2. Build/tune the finder prompt on a small candidate batch; freeze and version it.
3. Stand up item-keyed annotation on `(citing_sentence, cited_pmid)` with the
   system's evidence payload; keep the proposed category hidden until commit.
4. Draft the unified design-level precision-only rationale (one write-up, all
   strata) and the F3-DI2 amendment.
5. Get F3–F7 repair numbers before finalizing venue framing.

## Unchanged invariants (reference)
F2 regression-guard PMIDs stand: `31665581`, `16639420`, `18152150`, `27665045`,
`25750229`, `32355637`, `22926653`. Naturally-occurring-only still governs any
denominatored gold set; the precision audit is a distinct construct, explicitly
amended in. Claude never assigns semantic labels or curates ground truth.