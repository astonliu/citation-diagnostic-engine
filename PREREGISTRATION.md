# Pre-Registration — Citation Repair Engine

**Status:** Committed before annotation begins. The git commit timestamp is the registration date.
**Project:** Citation Repair Engine — fine-grained biomedical citation diagnosis, evidence-backed repair, and exploratory generation.
**Plan version:** v2.2 (May 29, 2026).
**Purpose of this document:** Fix the analysis plan in advance so that results cannot be reverse-justified. Referenced by commit hash in the manuscript.

---

## 1. Claims and their tests

The plan is organized as **one primary scientific claim plus two supporting system properties** (matching v2.2 §3), not as three co-equal claims — a focused contribution is one defense, not three rejection vectors.

**Primary claim (the contribution).** A fine-grained 8-category taxonomy (F1–F8) enables more accurate biomedical citation diagnosis than coarse 3-label (Sarol et al.) or 4-label (SemanticCite) schemes.
- **Test:** Map F1–F8 outputs down to 3 labels and 4 labels; compare diagnosis quality (precision, recall, macro-F1) and combined diagnosis+repair success rate under each scheme on the same examples.
- **Statistic:** Paired comparison (McNemar's test / paired bootstrap on per-example correctness). See §4 for power.
- **Controlled comparison:** Comparison A (our pipeline on Sarol's corpus, mapped to 3 labels) is the head-to-head that establishes the fine-down-to-coarse result is not a corpus artifact.

**Supporting system property 1 (not a standalone claim).** The pipeline autonomously proposes evidence-backed replacement citations using PubMed/Crossref retrieval.
- **Test:** Top-1 and top-3 accuracy of the replacement candidate against held-out gold PMIDs; reported as a system performance number, not pitched as a contribution.
- **Reference point (narrative only):** CiteAgent 35.3% on ML CiteME excerpts — not a threshold.

**Supporting system property 2 (exploratory).** The architecture extends to a GENERATION mode (citation discovery for claims without citations) without redesign.
- **Test (coverage):** On a held-out set of N real biomedical excerpts containing both citation-present and citation-absent cases, report the fraction each single-mode system fails vs. the unified pipeline. Measured from existing labels; no human-subjects protocol.
- **Quality (see §6):** objective top-k vs. gold PMIDs + a human spot-check anchor.
- GENERATION is exploratory until Phase 7; the contribution does not depend on it. If it fails to beat the in-domain recommender baseline (§2, baseline 4) by a meaningful margin, it is reported as a negative result and reframed as future work.
- **Optional appendix only:** human actionability rating (3–5 raters, Y/N, inter-rater agreement) — never a primary endpoint.

---

## 2. Baselines (fixed in advance)

1. Sarol et al.'s released pipeline (BM25 + MonoT5 + MultiVerS).
2. Zero-shot Claude, bare prompt (no retrieval, no taxonomy).
3. SemanticCite-style 4-class (F1–F8 mapped down).
4. CiteAgent-style zero-shot recommender (Generation Mode in-domain baseline).
5. Random / lexical-match retrieval floor.

**Ablations:** retrieval disabled; F1–F8 → 3 labels (Sarol); F1–F8 → 4 labels (SemanticCite); OpenAlex added (only if coverage-driven recall misses observed).

---

## 3. Two Sarol comparisons (not conflated)

- **Comparison A (controlled, established task):** our pipeline on Sarol's public corpus, mapped to their 3 labels. Supplies the Phase 7 ≥15-F1 figure.
- **Comparison B (task difficulty, new task):** Sarol's pipeline on CitationRepair-1000. Characterizes difficulty; not a superiority claim.

Reported separately; never averaged.

---

## 4. Sample size and power

- **Released dataset:** 1000 examples (annotate 1100–1200 to absorb IAA reconciliation losses), ~125/category across F1–F8.
- **Power (primary claim):** paired-proportion test; to detect a discordant-pair rate ≈ 0.10–0.15 at 80% power, α = 0.05, requires ~150–250 discordant pairs. At n = 1000, a 10–15% discordance rate yields ~100–150 discordant pairs — at/near threshold on the full dataset.
- **Conclusions fixed in advance:**
  - n = 1000 is sufficient for the primary claim at the full-dataset level; no expansion planned.
  - The 500-example August slice is underpowered for a *significance* claim on the primary comparison. Off the slice, the primary claim is reported as point estimate + CI with significance deferred to the full dataset.
- **Manuscript statement:** "the fine-vs-coarse comparison is powered to detect a discordant-pair rate ≥ 0.10 at n = 1000."

---

## 5. Evaluation protocol (fixed in advance)

- **Cross-validation:** 5-fold stratified CV.
- **Confidence intervals:** bootstrap, 1000 resamples. No single point estimates reported as results.
- **Contamination control:** a held-out experiment using citations from papers published after the model's training cutoff.
- **Model reporting:** exact Claude model strings and snapshot dates pinned.
- **UNVERIFIABLE exclusion:** examples flagged UNVERIFIABLE (theses, grey literature, conference papers with no stable identifier) are excluded from F1–F8 macro-F1 computation and from the Sarol comparison. Their count and fraction are reported in a separate coverage table. ABSTRACT_ONLY examples are included in primary metrics with the flag reported as a covariate. These rules are fixed; they are not adjusted based on observed results.

---

## 6. Generation-Mode evaluation (conflict-of-interest controls fixed in advance)

- **Primary:** objective top-1/top-3 vs. held-out gold PMIDs (top-3 headline; top-1 stricter lower bound).
- **Primary:** human spot-check, stratified ~50-example sample, agreement reported.
- **Secondary, disclosed:** LLM-as-judge using a **different model family** from the generator, reported with the CiteGuard 16–17% recall caveat inline; never overrides human/objective numbers.
- The Claude family that generates candidates is **never** the primary judge of those candidates.

---

## 7. Inter-annotator agreement (fixed in advance)

- **Target:** Cohen's κ ≥ 0.60 on the IAA subset (≥100 of the released examples double-annotated); κ ≥ 0.70 is "good".
- **Taxonomy pre-pilot:** 40 examples (5/category, two annotators) targeting the three at-risk pairs (see `TAXONOMY_DECISION_RULES.md`) *before* volume annotation. Proceed if each pair holds κ ≥ 0.60; otherwise merge the offending pair with the pilot as justification.
- **Fallback annotator qualification:** any second annotator must reach κ ≥ 0.60 against gold on a 20-example calibration set *before* paid annotation. Calibration κ reported in the manuscript.

---

## 8. Dataset construction (fixed in advance)

- **Real-error stratum is held out as a dedicated test partition** (Retraction Watch, PubPeer, Topaz et al. list when released). The primary claim reported separately on natural-only test examples.
- **Heterogeneous synthetic injection** per category to avoid a single artifact signature (e.g., F3: random PubMed paper / same-MeSH-wrong-finding / LLM-generated plausible-but-wrong). Injection-method mix documented in the dataset card.
- **Deterministic pre-classifier categories:** F1, F2, F8 resolved by database lookup before the classifier; human/LLM judgment confined to F3–F7.
- **Stratify across all 8 categories before train/test split.**

### 8a. Retrieval scope (fixed in advance)

CitationRepair-1000 is scoped to citations that are either (a) PMID-indexed in PubMed or (b) DOI-resolvable via Crossref. This operating envelope is declared here before annotation begins and reported explicitly in the manuscript's data statement. The following reference types fall outside the pipeline's stated scope and are handled as follows:

- **Theses and dissertations** (no PMID, DOI absent or unresolvable): flagged as UNVERIFIABLE at output, not assigned a failure label. Excluded from the primary F1–F8 evaluation metrics. Count reported separately in the dataset card.
- **Conference papers with no stable identifier** (e.g., workshop proceedings not indexed in PubMed or Crossref): same handling as theses — UNVERIFIABLE flag, excluded from primary metrics.
- **Paywalled papers where only title+abstract are accessible** (paper exists and identifier resolves, but full text is unavailable): existence check passes; verification proceeds on abstract only. Output confidence field is set to ABSTRACT_ONLY to signal reduced certainty. These examples are *included* in evaluation metrics but the ABSTRACT_ONLY flag is reported as a covariate.
- **Grey literature** (websites, reports, books): flagged UNVERIFIABLE; excluded from primary metrics.

**Rationale for fixed exclusion rather than silent filtering:** Topaz et al. (Lancet 2026) noted that their system excluded 23% of references lacking PMIDs, acknowledging this as a limitation. The Citation Repair Engine makes the same pragmatic choice but declares it in advance to prevent post-hoc scope adjustment. UNVERIFIABLE is not a failure mode — it is a coverage boundary.

**Scope is fixed as of this preregistration.** If the evaluation reveals an unexpectedly high UNVERIFIABLE rate (>15% of CitationRepair-1000), that is reported as a finding, not addressed by retroactively expanding the retrieval stack.

---

## 9. What would change the plan (decision rules)

- A 2026 biomedical citation *repair-with-replacement* paper appears before submission → re-pitch as comparison/ablation against it.
- Taxonomy pre-pilot κ < 0.60 on a pair after decision rules → merge that pair, report the pilot evidence.
- Zero-shot Claude beats Sarol by >10 F1 on the controlled comparison → that becomes a headline result alongside the taxonomy.
- Generation top-3 fails to beat the in-domain recommender baseline by ≥10 pts → Generation stays exploratory / demoted, not escalated.

---

## Amendment Log

**Amendment 1 — June 4, 2026 (pre-annotation)**

**Change:** Removed F5/F8 from the pre-pilot confusable pairs list.

**Rationale:** F8 (retracted paper) is resolved deterministically via PubMed retraction flag / Retraction Watch lookup and never reaches the human annotator. It is categorically not confusable with F5 (stale citation), which requires a substantive judgment call. Including F5/F8 as a confusable pair in the pre-pilot was an error — there is no annotator-level ambiguity to test. F8 belongs with F1 and F2 as a deterministic pre-classifier category (already stated in §8), not as a judgment category.

**Specific changes to §7:**
- Pre-pilot now targets **two pairs only**: F3/F6 and F4/F6.
- Pre-pilot size revised from 40 examples (5/category × 8) to ~20 examples (10 per pair), targeting boundary cases for the two judgment pairs only.
- IAA judgment categories clarified as F3–F7 (5 categories). F8 is excluded from IAA computation as it is deterministic; computing IAA over deterministic categories would artificially inflate κ.
- IAA pilot (100 examples) stratified across F3–F7 only (~20 examples/category).

**No annotation had occurred at time of this amendment.**
