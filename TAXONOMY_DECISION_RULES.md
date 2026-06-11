# Taxonomy Decision Rules — The Two At-Risk Pairs (+ F5/F8 routing)

**Fold into `TAXONOMY.md`.** These are the deterministic collision rules that keep F1–F8 at 8 categories. They exist because an earlier pilot collapsed the taxonomy to 5 over inter-annotator confusion; these rules pre-empt the specific collisions that drove it. Each rule is a decision procedure an annotator applies *before* exercising judgment — the goal is to remove the judgment call entirely where possible.

Use alongside the full per-category definitions (1 paragraph + 2 positive + 2 negative examples each) already in `TAXONOMY.md`.

---

## Pair 1 — F3 (Misattribution) vs. F6 (Partial support)

**Why they collide.** For any multi-clause biomedical sentence, "the paper doesn't make the claimed finding" (F3) and "the paper supports part but not all of the claim" (F6) can describe the same abstract depending on how the annotator frames "the claim."

**Decision rule (count over atomic claims — deterministic given the atomic-claim labels the pipeline produces):**

1. Decompose the sentence into atomic claims (pipeline already does this).
2. For each atomic claim, label whether the cited paper supports it (yes/no).
3. Apply:
   - **Zero atomic claims supported → F3 (Misattribution).** The paper supports none of what was claimed.
   - **At least one supported AND at least one unsupported → F6 (Partial support).**
   - (All supported → not an error in this dimension; the citation is accurate for coverage.)

**Boundary note.** If the sentence has only one atomic claim and the paper doesn't support it, that is F3, not F6 — F6 requires a genuine split.

**Calibration note — "indirect support" edge case.** If the cited paper supports the general mechanism but the claim asserts a specific experimental context the paper doesn't confirm (e.g., claim says "ApoE-deficient mice" but abstract only discusses mice generally), this is F6 — the general claim is supported, the specific context is not addressed. Do NOT assign F3 unless zero atomic claims are supported. See Example 1 (claims_test.jsonl index 1) as a worked calibration case.

---

## Pair 2 — F4 (Overstatement) vs. F6 (Partial support)

**Why they collide.** Both are "the paper supports something *less* than what was claimed." A correlational paper cited for a causal claim reads as either a strength mismatch (F4) or a partial-support case (F6).

**Decision rule (axis of mismatch — strength vs. coverage):**

1. Identify whether the cited paper *addresses* the claim's subject at all.
   - If it addresses the subject but at a **weaker strength/modality** → **F4 (Overstatement).** This is a mismatch on *how strongly*: correlation cited as causation; "associated with" cited as "causes"; "may reduce" cited as "reduces"; observational cited as interventional.
   - If it **fully supports some atomic claims and is silent on / does not address others** → **F6 (Partial support).** This is a mismatch on *how many*.
2. Tie-breaker: F4 is about a single claim the paper *does* engage, at the wrong strength. F6 is about *multiple* claims where the paper covers a subset completely.

**One-line test.** "Wrong strength on a claim it addresses" = F4. "Right strength but only on some of the claims" = F6.

**F4 boundary — paper-level vs. literature-level uncertainty.** F4 requires the *cited paper itself* to make a weaker claim than what is being asserted. If the cited paper reports a finding as true while acknowledging uncertainty in the *broader literature* (e.g., "one study found X but other studies show variable results"), the citation is ACCURATE — the paper's own finding is being cited correctly. F4 is triggered only when the paper's own language is hedged relative to the claim (e.g., paper says "may be associated with," claim says "causes"). See Example 4 (claims_test.jsonl index 4) as a calibration case: Prevotella/meat-diet association labeled ACCURATE because the paper reports the finding as real despite noting broader uncertainty.

---

## F5/F8 routing rule — NOT a pre-pilot at-risk pair

*Per Preregistration Amendment 1 (June 4, 2026), F5/F8 was removed from the confusable-pairs list. F8 is resolved deterministically (database lookup) and never reaches the human annotator, so there is no annotator-level ambiguity to gate on. This section is retained as the deterministic routing rule, not as a judgment pair tested for κ.*

**Why the routing exists.** Both can read as "the cited paper is no longer valid evidence," so the pre-classifier must route them apart before any judgment.

**Decision rule (deterministic routing):**

1. **Check the retraction flag first** (PubMed retraction status / Retraction Watch). If the paper is formally retracted → **F8 (Retracted source).** This is a database lookup, handled in the existence-check layer with F1/F2 — it never reaches the human classifier.
2. If the paper is **not** retracted but a newer paper contradicts its finding on the same claim → **F5 (Stale citation).** Proceed to the two-path F5 protocol below.

**Consequence.** F8 is never a judgment call. F5 always is — but F5 has two distinct outputs depending on whether supersession is unambiguous.

---

## F5 Two-Path Protocol (annotation and repair)

F5 is detected when a newer paper contradicts the cited paper's finding on the same claim and same or comparable population. Detection alone is sufficient to classify F5 — the system does not need to adjudicate which paper is correct. However, the **repair output** depends on whether supersession is clear or disputed.

### Step 1 — Detect contradiction

A newer paper contradicts the cited finding if it reports a materially different effect size, direction, or conclusion on the same outcome measure and a comparable population. If no contradicting paper is found, the citation is not F5.

### Step 2 — Apply supersession criteria (in order)

Check the following against the contradicting paper. Each criterion is sufficient on its own to establish clear supersession:

1. **Study design tier.** Meta-analysis or systematic review > RCT > prospective cohort > retrospective cohort > case-control > cross-sectional > case report. If the newer paper is a higher tier on the same outcome, supersession is clear.
2. **Sample size.** Newer paper has substantially larger n on the same population and outcome measure.
3. **Pre-registration.** Newer paper is a pre-registered replication of the original finding that failed to replicate.
4. **Guideline revision.** A major governing body (FDA, WHO, relevant specialty society) revised its guidance after the cited paper, explicitly citing newer evidence.

### Step 3 — Two-path output

**Path A — Clear supersession (at least one criterion fires):**
- Classify F5.
- REPAIR mode proposes the contradicting paper as the replacement citation.
- Output includes: failure type, contradicting paper with support quotes, confidence = HIGH.

**Path B — Genuine dispute (contradiction exists but no criterion fires):**
- Classify F5.
- REPAIR mode does **not** propose a replacement.
- Output includes: failure type, both papers surfaced with their contradicting claims quoted, confidence = LOW, escalation flag = TRUE.
- The author adjudicates which paper is appropriate for their context.

**Key principle.** The system's job is to detect contradiction and apply the supersession criteria — not to resolve genuine scientific disputes. Path B is not a failure of the system; it is the correct behavior when the evidence is genuinely ambiguous. Both paths are reported separately in evaluation metrics.

### Reporting

- Report Path A and Path B F5 counts separately in the paper.
- The combined "successful repair" metric (correct diagnosis + correct replacement) applies only to Path A cases. Path B cases are excluded from that metric with an explicit note.
- The Path A / Path B split rate is itself a reportable finding characterizing F5 difficulty in the corpus.

---

## ACCURATE vs. F4 — the uncertainty boundary

**Decision rule.** The question is always: does the *cited paper itself* make a weaker claim than what is asserted?

- **Paper hedges, claim is strong → F4.** Example: paper says "TMAO may contribute to atherosclerosis," claim says "TMAO causes atherosclerosis."
- **Paper is confident, broader literature is uncertain → ACCURATE.** The citation is to the paper, not to the field consensus. If the paper stands behind its finding, the citation can too.
- **Paper reports a finding from one study, acknowledges variable results elsewhere → ACCURATE**, provided the claim doesn't overstate the paper's own conclusion.

**Key principle.** Annotators should assess the paper's own confidence level, not the field's consensus. A paper can be accurately cited even if its finding is contested elsewhere, as long as the claim accurately reflects what *that paper* asserts.

---

## ACCURATE vs. F6 — the specificity boundary

**Decision rule.** Check whether the claim adds specificity that the paper doesn't actually establish.

- **Claim adds a specific experimental context the abstract doesn't confirm → F6.** Example: claim says "in ApoE-deficient mice," abstract only discusses "mice" generally. The general finding is supported; the specific model is not confirmed.
- **Claim stays at the same level of specificity as the paper → ACCURATE.**

**Key principle.** "Accurate on the topic" ≠ "accurate on the claim." Read the claim at the level of its specific assertions, not its general subject. One word of unconfirmed specificity is enough to push a citation from ACCURATE to F6.

---

## Architecture summary (where each category is decided)

| Category | Decided by | Stage |
|---|---|---|
| F1 Fabricated | DOI/PMID/metadata fails to resolve | Existence check (pre-classifier) |
| F2 Wrong reference | Metadata mismatch after fuzzy match | Existence check (pre-classifier) |
| F8 Retracted | Retraction flag lookup | Existence check (pre-classifier) |
| F3 Misattribution | Atomic-claim count (zero supported) | F3–F7 judgment band |
| F4 Overstatement | Strength/modality mismatch | F3–F7 judgment band |
| F5 Stale | Contradiction detection + supersession criteria → Path A (autonomous repair) or Path B (escalation flag) | F3–F7 judgment band |
| F6 Partial support | Atomic-claim count (some supported) | F3–F7 judgment band |
| F7 Wrong entity | Entity mismatch (drug/gene/disease) | F3–F7 judgment band |

Human/LLM judgment is confined to F3–F7. F1, F2, F8 are database-resolvable.

---

## Calibration examples from Sarol et al. test set

These are real examples from claims_test.jsonl used to calibrate annotators on boundary cases.

| Index | Correct label | Sarol label | Key lesson |
|---|---|---|---|
| 0 | F6 | NOT_ACCURATE | General mechanism supported; ApoE-deficient model not confirmed — specificity boundary |
| 1 | ACCURATE | NOT_ACCURATE | All atomic claims directly supported; clean ACCURATE baseline |
| 2 | F6 | ACCURATE | **Sarol false negative.** First clause (cardiovascular correlation) unsupported; second clause (omnivore/vegan TMAO) supported. Best error analysis example. |
| 3 | ACCURATE | ACCURATE | Paper reports finding confidently despite broader literature uncertainty — paper-level vs. literature-level uncertainty rule |

---

## Pre-pilot gate (run before annotating at volume)

- **~20 examples (10 per pair), two annotators**, targeted at the two at-risk pairs only: **F3/F6 and F4/F6** (Pairs 1–2 above). F5/F8 is excluded — F8 is deterministic and never reaches the annotator (Amendment 1).
- **Proceed to full 1100–1200** if each pair holds **κ ≥ 0.60** on the pre-pilot. (κ ≥ 0.70 is "good"; 0.60 is the registered gate, not raised.)
- IAA is computed over the F3–F7 judgment band only (5 categories); F8 is excluded from κ as deterministic.
- **If F3/F6 or F4/F6 still collide** after these rules: merge the offending pair and report the pre-pilot as the methodological justification ("collapsed after a pilot revealed residual confusion" = methodological care, not retreat).
- The taxonomy stays **8** unless the pre-pilot says otherwise.
