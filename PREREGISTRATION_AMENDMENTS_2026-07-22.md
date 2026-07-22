# Pre-Registration Amendments — Citation Repair Engine
**Amendment date:** July 22, 2026
**Appended to:** PREREGISTRATION.md (v2.2, original commit preserved unchanged)
**Follows:** PREREGISTRATION_AMENDMENTS_2026-06-09.md (Amendments 1–2)
**Rule:** Additive and dated. The original registered plan is not edited or deleted;
deviations are logged here with reasoning, per standard preregistration practice. Each
amendment cites the section of the original it modifies. Because the project is not yet
cited anywhere and the registration's proof-of-timing rests on the original commit and
`CITATION.cff` (date-released 2026-06-02), history is not rewritten to record these.

---

## Amendment 3 — annotator model and IAA gating (modifies §7, and §9 decision rule 1)

**Decision.** Two annotators are hired to **annotate** the released dataset — to label
items, not to construct the dataset and not to author items. Reliability is inter-annotator
agreement (Cohen's κ) on a double-annotated overlap slice (≥100 examples target), annotators
labeling independently and blind to each other and to any system-proposed category. Aston may
serve as an independent blind third rater and never pre-labels items for others to confirm.

**No κ value is committed in advance.** No κ threshold, gate, or target is registered ahead of
data, and there is no κ-gated pre-pilot. κ is **reported when computed** as a measured
reliability figure — not a bar the project claims to have cleared.

- The ~20-example κ≥0.60 pre-pilot gate (§7) is retired as a gate.
- The fallback-annotator κ≥0.60 qualification (§7) is retired.
- The κ<0.60 merge trigger (§9 decision rule 1) is retired as a pre-registered trigger.
- The gate to volume annotation is now **finder-prompt stabilization** on a small candidate
  batch: run the finder, expose failure modes, then freeze and version the finder prompt with
  the dataset. Annotate at volume only against the frozen prompt.

**Reasoning.** (1) A pre-committed κ threshold the project then reports having met invites the
suspicion it was chosen to be clearable; measuring κ and reporting it as found is the more
honest and more defensible posture, especially for a solo-led project. (2) The precision
number is conditional on the exact finder prompt, so the real risk to validity is an unstable
prompt, not an unmet κ — the gate belongs there. (3) Merging a confusable pair remains available
as a *post-hoc* response to measured disagreement, reported as methodological care, which is
strictly weaker (and more truthful) than a pre-registered pass/fail.

**Supersedes.** This reverses the "κ targets: ≥0.60 registered … not raised" and "Pre-pilot
gate … κ ≥ 0.60 to proceed" lines under *Unchanged* in Amendments 1–2 (2026-06-09). Those
lines committed a κ gate; Amendment 3 removes the gate while keeping κ as a reported metric.

---

## Amendment 4 — recall / precision scope (modifies §5; refines the §1 primary-claim metric set)

**Decision.** Evaluation is **precision-first**.

- **Primary:** precision per stratum on the finder's flagged set, justified by the evaluation
  design — auditing the finder's flagged output, with no labeled population draw, yields
  precision by construction and leaves population recall undefined.
- **Added:** a **case-control sensitivity lower bound** for any stratum that has an independent
  confirmed-positive set at write time (F2 and F3 today). Cases = human-confirmed positives
  sourced independently of the detector (F2: seed-audit TRUE_F2 rows, seeds 7/19/23/29; F3:
  confirmed provenance cases). Controls = matched non-fault citations. The result is reported
  as **case-control sensitivity, a lower bound, not population recall**, with a Wilson CI.
- **Circularity guard (non-negotiable).** Cases feeding the case-control estimate come only
  from independently confirmed positives — **never from detector output**. A case admitted
  because the detector flagged it makes the estimate the detector agreeing with itself.
- **Out of scope:** population recall against the corpus, declined for feasibility (~0.1–0.2%
  base rate; ~125k adjudicated references). Case-control sidesteps that wall without claiming
  to have climbed it.

**Reasoning.** Dr. Roberts proposed a case-control recall design (2026-07-17). It is adopted in
the scoped form above: it delivers a defensible sensitivity lower bound where confirmed positives
exist, while the design-level precision argument (unchanged) carries the strata without such a set.
The primary scientific claim and its paired-comparison test (§1, §4) are unaffected; diagnosis
quality is still reported with precision, and — where a confirmed-positive set exists — a
case-control sensitivity lower bound.

**Supersedes.** Any prior blanket "precision-only / recall off the table" posture for F2 and the
semantic strata. See the standalone decision note `RECALL_DECISION_2026-07-22.md` (project-internal;
not part of the public repo) for the full rationale and the circularity guard.

---

## Amendment 5 — synthetic data restricted to training (reaffirms and generalizes §8)

**Decision.** No synthetic, injected, perturbed, or LLM-generated item appears in any validation
set, gold set, or reported number. Naturally-occurring-only governs everything measured. Synthetic
data **may** be used for model training / augmentation only, documented in the dataset card and kept
strictly disjoint from any evaluated data.

**Reasoning.** The §8 "naturally-occurring errors only" note (added pre-annotation per Dr. Roberts)
already removed synthetic data from the dataset. Amendment 5 states the boundary explicitly for the
training/evaluation split so there is no ambiguity: synthetic is a training-side aid at most, never a
source of any reported figure.

---

## Unchanged (explicitly retained)

- Primary claim and its paired-comparison test (§1, §4 power).
- Held-out natural stratum; deterministic F1/F2/F8 pre-classifier (§8).
- Stratified split and n = 1000 power justification (§4).
- Two distinct Sarol comparisons, never conflated (§3).
- IAA judgment categories are F3–F7; F8 deterministic and excluded from κ.
- Naturally-occurring-only data (from Amendments 1–2 / §8), now generalized by Amendment 5.
