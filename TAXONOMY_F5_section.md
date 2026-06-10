# TAXONOMY.md — F5 Section (Stale / Superseded Citation)

*Drop-in section for TAXONOMY.md. Operationalizes F5 per Preregistration Amendment 3 (2026-06-09). Assumes the F1/F2/F8 deterministic pre-classifier and the F3/F6, F4/F6 decision rules from TAXONOMY_DECISION_RULES.md are already in force.*

---

## F5 — Stale / Superseded

**Definition.** The cited paper is real and originally supported the claim, but its central finding has since been contradicted or overturned by more recent, higher-or-equal-quality independent work. F5 is about *scientific progress*, not error in the original citation and not integrity failure.

**What F5 is NOT:**
- Not F8. If any formal publisher notice exists (retraction, correction, expression of concern), the example routes to **F8** via the pre-classifier database lookup, regardless of the criteria below. F5 never overrides a formal notice.
- Not F3/F4/F6. Those concern whether the cited paper supports the claim *as written today*. F5 presumes the cited paper *did* support the claim; the issue is that the field has moved.

### The three-criterion supersession gate

All three must fire for **Path A** (autonomous repair: retrieve and propose the superseding paper). Any single failure routes to **Path B** (flag/escalate; no autonomous replacement).

1. **Directional contradiction.** The superseding paper contradicts the cited paper's central finding — a reversal of direction or conclusion, not a refinement of magnitude or a tightening of hedging.
2. **Publication-date gap ≥ 2 years.** The superseding paper postdates the cited paper by at least two years (filters rapid replication disputes and preprint-to-journal version drift).
3. **Evidence-hierarchy upgrade.** The superseding paper sits at an equal-or-higher evidence tier than the cited paper (systematic review > RCT > cohort/observational > case series/report).

### Worked examples

**Criterion 1 — directional contradiction**
- *Fires:* Cited "HRT reduces cardiovascular risk in postmenopausal women" (Smith 1998); superseding WHI 2002 finds HRT *increases* that risk. Reversal → fires.
- *Does not fire:* Cited "metformin reduces HbA1c ~1.5%" (2010); superseding 2022 meta-analysis finds ~1.1%. Same direction, smaller effect → refinement, not contradiction → Path B.

**Criterion 2 — date gap**
- *Fires:* cited Jan 2018, superseding Mar 2021 (3 yr).
- *Does not fire:* cited Sep 2023, superseding Nov 2024 (14 mo) → Path B.

**Criterion 3 — evidence-hierarchy upgrade**
- *Fires:* cited cohort study (n=340) on aspirin and colorectal cancer; superseding Cochrane review of 12 RCTs finds no effect. Review > cohort → fires.
- *Does not fire:* cited large RCT (n=12,000) on statins and stroke; superseding case series (n=8). Case series < RCT → Path B.

### F5 / F8 boundary

- *Classify F8 (not F5):* cited Stapel 2011; formal retraction issued → F8, criteria irrelevant.
- *Classify F8 (not F5):* cited 2019 biomarker study; journal issued a 2023 Expression of Concern → formal notice → F8.
- *Classify F5 (not F8):* cited 2015 observational Mediterranean-diet/dementia finding; 2022 RCT finds no cognitive benefit; no notice of any kind → all three criteria fire → F5, Path A.

### Architecture note

F5 is the only category whose verification requires retrieving a *second* (superseding) paper. Retrieval for F5 uses MeSH-tag candidate generation filtered by publication date, then direct LLM contradiction judgment on abstracts — **not** MonoT5 reranking (topical relevance does not capture directional contradiction). This is unchanged from the v2.2 architecture; the three-criterion gate is the labeling/decision layer on top of it.

### Annotation instruction

Annotators label F5 by answering three yes/no questions (the three criteria), plus the F8 notice check. They do **not** make a holistic "feels superseded" judgment. If the F8 notice check is positive, stop and label F8.
