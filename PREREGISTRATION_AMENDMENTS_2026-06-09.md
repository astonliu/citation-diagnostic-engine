# Pre-Registration Amendments — Citation Repair Engine
**Amendment date:** June 9, 2026
**Appended to:** PREREGISTRATION.md (v2.2, original commit preserved unchanged)
**Rule:** These amendments are additive and dated. The original registered plan is not edited or deleted; deviations are logged here with reasoning, per standard preregistration practice. Each amendment cites the section of the original it modifies.

---

## Amendment 1 — GENERATION mode withdrawn from the contribution (modifies §1 Supporting Property 2, §2 Baseline 4, §6 entirely)

**Decision:** GENERATION mode is withdrawn from the October 2026 submission. It is reframed as future work.

**Reasoning:** (1) GENERATION was registered as *exploratory*, with the contribution explicitly not depending on it (original §1). Withdrawing it therefore does not weaken any registered claim. (2) The June 2026 novelty search established that the diagnosis→repair closed loop is the uncontested contribution, while citation generation/recommendation is a mature field (CiteGuard already "identifies alternative valid citations"). (3) GENERATION requires a separate retrieval slice, separate dataset examples, a separate metric, and separate results; on the August 1 / October timeline this scope is not deliverable at a rigor that would survive review. A lightly-evaluated GENERATION section is a net negative.

**Consequences fixed now:**
- §2 Baseline 4 (CiteAgent-style recommender) is withdrawn — it existed only to benchmark GENERATION.
- §6 (Generation-mode evaluation) is withdrawn in full.
- The architecture's reusability for GENERATION (same Stages 2–4, different input) is noted in the manuscript's Future Work section as a forward-looking extension, not a result.
- The primary claim and Supporting Property 1 (evidence-backed replacement) are unaffected.

---

## Amendment 2 — Third verifier added: Med-V1 (modifies §4.4 LLM backbone, §6 secondary-judge note)

**Decision:** A third verifier, Med-V1 (Jin et al., arXiv 2603.05308; 3B-parameter biomedical model from the NLM/NCBI group), is added alongside the two co-equal frontier verifiers (Claude Opus, pinned; GPT-5, pinned).

**Reasoning:** Claude Opus and GPT-5 are both frontier-scale RLHF-trained models and may share systematic biases, so their agreement does not fully address the LLM-as-judge circularity concern (original limitation). Med-V1 is a different architecture and training paradigm (small, biomedically specialized), giving genuine cross-family diversity. Three verifiers spanning two frontier models plus one domain specialist handle the bias-family critique better than two frontier models alone, and add biomedical grounding.

**Reporting fixed now:**
- Three-way agreement rate reported as a standalone robustness metric.
- Disagreements adjudicated by the human gold subset (Amendment 4), never by any of the three models.
- Med-V1's structured-verdict output is parsed into the unified Stage-4 representation before mapping; parser documented in the repo.
- Med-V1 is also reportable as a cost/latency baseline against the frontier verifiers.

**§6 secondary-judge note superseded:** The original §6 language ("LLM-as-judge using a different model family from the generator") was written when Claude was the sole verifier and cross-family diversity was a secondary safeguard. It is now superseded by the three-verifier design — cross-family judgment is structural (two frontier families + one biomedical specialist), not a secondary layer. The §6 note is retained in the original for timestamp integrity but is inactive.

---

## Amendment 3 — F5 supersession operationalized as a three-criterion gate (modifies §8 deterministic-category handling; detailed in TAXONOMY.md)

**Decision:** F5 (Stale/superseded) is operationalized with a deterministic three-criterion gate. All three criteria must fire for Path A (autonomous repair); any failure routes to Path B (escalate/flag, no autonomous repair).

**The three criteria:**
1. **Directional contradiction** — the superseding paper contradicts the cited paper's central finding (reversal), not merely refines magnitude or hedging.
2. **Publication-date gap ≥ 2 years** — superseding paper postdates the cited paper by ≥ 2 years.
3. **Evidence-hierarchy upgrade** — superseding paper is at equal-or-higher evidence tier (systematic review > RCT > cohort > case study).

**F5/F8 boundary rule (fixed):** Any formal publisher notice (retraction, correction, expression of concern) routes to F8 via the pre-classifier database lookup, regardless of the F5 criteria. F5 is reserved for supersession by independent subsequent work with no formal notice.

**Reasoning:** F5 was previously registered only as requiring "a substantive judgment that newer work supersedes," which is not annotatable at acceptable κ. The three-criterion gate converts F5 into a deterministic function of annotatable facts, placing it cleanly in Stage 4 alongside the other rule-based gates and protecting F5's contribution to inter-annotator agreement.

---

## Amendment 4 — Human-adjudicated gold subset as the primary validity anchor (modifies §5 evaluation protocol, §7 IAA)

**Decision (conditional on advisor availability):** A 100-example human-adjudicated gold subset, stratified across F1–F8 (~12–13/category, spanning high-confidence and borderline cases), is the primary ground-truth anchor for diagnosis validity. Three-way LLM agreement (Amendment 2) is reported as a secondary robustness metric, not as the primary validity argument.

**Fallback ladder (fixed in advance, to avoid post-hoc choice):**
- If full 100-example adjudication is feasible before results are finalized → it is the primary anchor.
- If not → adjudicate only the cases where the three verifiers disagree (smaller subset), reported as a disagreement-focused human check.
- If neither is feasible → validity rests on three-way cross-family agreement plus held-out natural-stratum generalization, with human adjudication named as a limitation/future extension.

**Reasoning:** Cross-model agreement alone cannot establish correctness (shared-bias critique). A human anchor drawn and adjudicated *before* results are finalized prevents the appearance of post-hoc damage control. The fallback ladder is registered now so the choice among options is not made after seeing results.

**Note:** advisor (Dr. Roberts) availability to be confirmed; this amendment records the design, not a completed commitment.

---

## Amendment 5 — Dataset category sizing: equal synthetic, as-available natural (clarifies §4, §8)

**Decision:**
- **Synthetic stratum:** balanced at ~125/category across F1–F8, to enable unbiased macro-averaged comparison (the primary metric).
- **Natural stratum:** unequally distributed across categories according to real-world discoverable-error availability; reported in aggregate rather than per-category, with per-category natural results treated as descriptive (not inferential) given small per-cell n.

**Reasoning:** Equal synthetic sizing forecloses any "category sizes engineered to inflate macro-F1" critique. Unequal natural sizing is self-justifying (real errors occur at different frequencies; all available examples collected). Difficulty-based sizing was rejected because "harder = more data" is subjective and reads as padding the easy categories. No power-analysis-based unequal synthetic sizing is adopted (it would reintroduce the macro-F1-gaming perception).

---

## Amendment 6 — Abstract-only retrieval decision threshold (clarifies §8a retrieval scope)

**Decision:** A 20–30 example accessibility sampling exercise determines retrieval policy. Threshold fixed in advance at **70% PMC Open Access full-text accessibility**:
- **≥ 70% accessible** → abstract-only is the primary retrieval path; full-text via PMC OA used where available; full-text-vs-abstract reported as an ablation, not required as a core result.
- **< 70% accessible** → a full-text retrieval ablation is required before submission to quantify the degradation.

**Reasoning:** CiteGuard already documented abstract-only degradation, so this is defensive engineering, not a novel contribution. Fixing the threshold in advance prevents a post-hoc accessibility-cutoff choice. The sampling can be run on independently drawn recent PubMed citations before CitationRepair-1000 is complete, to de-risk scheduling.

---

## Amendment 7 — Dense retrieval ablation (clarifies §2 ablations, §4.4 retrieval)

**Decision:** A dense-retrieval ablation (biomedical encoder, e.g., MedCPT, vs. the BM25+MonoT5 primary) is run on a 20–30 example slice. Outcome-driven reporting, fixed in advance:
- **Gap ≤ 5%** → reported as future work; BM25+MonoT5 retained as primary on BEIR-strength grounds.
- **Gap 5–15%** → reported as an ablation; BM25+MonoT5 defended as primary.
- **Gap > 15%** → reconsider primary retrieval method.

**Reasoning:** Given the 2026 competitor landscape (BibAgent, CiteTracer, Med-V1), a reviewer may ask why no dense baseline. A small ablation answers this empirically rather than rhetorically. Thresholds fixed now so the interpretation is not chosen after seeing the number. Dense retrieval is a standard IR technique (cite DPR/ColBERT), not an idea borrowed from any specific competitor.

---

## Amendment 8 — Related-work and benchmark additions (modifies §2 baselines / comparisons)

**Decision:** The following 2026 systems are added to related work and, where applicable, to the comparison set, per the novelty-search finding that the headline must move from "fine-grained taxonomy" to "closed-loop biomedical diagnosis→repair":
- **BibAgent / MisciteBench** (arXiv 2601.16993) — primary taxonomy baseline; map F1–F8 against its 5-category scheme.
- **CiteTracer** (arXiv 2605.08583) — taxonomy + deterministic-rules-plus-judge twin; distinguish on semantic vs. bibliographic focus.
- **Med-V1** (arXiv 2603.05308) — strongest biomedical verifier baseline (also now a verifier per Amendment 2).
- Sarol, Schneider & Kilicoglu 2025 ASIS&T case study — direct follow-up to the cited 2024 baseline.

**Headline reframing (fixed):** The contribution is stated as the first biomedical pipeline combining fine-grained *semantic* failure diagnosis with *automated evidence-backed replacement retrieval*. The bare "fine-grained taxonomy" novelty is not claimed in isolation; "evidence-grounded transparency" is demoted from a novelty claim to a design property. The 8-category scheme's value is defended empirically via the collapse-to-5 / collapse-to-4 / collapse-to-3 ablation, not asserted.

---

## Unchanged (explicitly retained from v2.2)
- Primary claim and its paired-comparison test (§1, §4 power).
- κ targets: ≥ 0.60 registered, 0.70 "good" (§7) — **not** raised; raising a registered threshold post-hoc is avoided.
- Pre-pilot gate: 40 examples, three at-risk pairs, κ ≥ 0.60 to proceed (§7, TAXONOMY_DECISION_RULES.md).
- Held-out natural stratum; deterministic F1/F2/F8 pre-classifier (§8).
- 70/30 stratified split; n = 1000 power justification (§4).
- Two distinct Sarol comparisons, never conflated (§3).
