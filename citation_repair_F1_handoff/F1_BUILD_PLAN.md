# F1 System + Dataset Build Plan — CitationRepair-1000

**Goal:** Build the F1 (fabricated citation) detection stage of CRE — a faithful reimplementation of the Topaz et al. (Lancet 2026) verification pipeline — and start the F1 portion of CitationRepair-1000.

**Scope honesty.** Two coupled deliverables with different completion horizons:
- **F1 *system*** → finishable today. This is the part that must be correct and reportable.
- **F1 *dataset portion*** → scaffolded + candidate run started today; populated from your own detector on a normal recent-papers PMC slice. Fabrication is ~4 per 100,000 references, so F1 will be a *small* category — a handful to a couple dozen gold positives is a realistic and acceptable outcome.

**F1 is not the contribution.** F1 detection is the reimplemented Topaz method — "we apply the established approach." It must exist, work, and be honestly characterized. It does **not** need to be large. The objective is "enough to demonstrate the stage functions and populate the category," not "maximize positives." A small F1 count is a documented dataset limitation (per source-constrained maximization), not a hole. No external data request is part of this plan.

**Core design rule (locked):** F1 is *not* raw "couldn't find it." F1 = (claimed-PMID metadata mismatch OR dead PMID) **AND** (survives the LLM formatting-discrepancy filter) **AND** (claimed content not found in any of PubMed / Crossref / OpenAlex). It is a conjunction. The base-rate math requires the corroborating signal — a single chain-failure test would be precision-poor.

---

## Assumptions (state, adjust if wrong)
- PMC Open Access XML is the input substrate; you can pull a slice via the OA service or an existing local subset.
- NCBI API key in hand (≈10 req/s). Anthropic API in hand. Add a Crossref polite-pool `mailto` and an OpenAlex `mailto`.
- LLM filter = Claude Opus (your stronger-than-Topaz's-Haiku lever; the one citable F1 improvement).
- Three databases, not four: PubMed, Crossref, OpenAlex. **Skip Google Scholar** (no API, rate-limit fragile, not reproducible). Note the omission in the paper.
- F2 (wrong reference) falls out of the same pipeline for free — capture it, but F1 is the focus.

---

## Phase 0 — Setup & locked decisions (~30 min)
- [ ] Create module dir: `cre/f1/` with `parser.py`, `lookup.py`, `compare.py`, `llm_filter.py`, `confirm.py`, `decide.py`, `run.py`, `schema.py`.
- [ ] `config.yaml`: NCBI key, Anthropic key, Crossref mailto, OpenAlex mailto, rate limits, similarity threshold, Opus model pin string.
- [ ] Lock the **unverifiable** decision: references without a claimed PMID route to a separate `unverifiable` bucket and are **never** labeled F1. (Precedent: Topaz excluded the same ~23%.)
- [ ] Lock the **precision-first** stance: when in doubt the system clears or sends to human review; it does not flag. A false F1 is an accusation; that error is the expensive one.
- [ ] Decide output: per-reference JSON log with every stage's outcome (needed later for recall characterization + stage-resolution distribution).

## Phase 1 — F1 detector build (~3 hr)
- [ ] **1a Parser** (`parser.py`): PMC OA XML → structured refs. Handle both `<element-citation>` and `<mixed-citation>`. Extract: `article-title`, author surnames, `source` (journal), `year`, `pub-id[pub-id-type=pmid]`, `pub-id[pub-id-type=doi]`, raw citation string, and the citing sentence (citance) around the in-text marker.
- [ ] **1b PMID filter + routing** (`run.py`): PMID present → verification path; absent → `unverifiable` bucket.
- [ ] **1c PMID lookup** (`lookup.py`): EFetch PubMed for the *claimed* PMID. Capture retrieved title/authors/journal/year. Record dead/invalid PMIDs explicitly (strong fabrication signal).
- [ ] **1d Metadata comparison** (`compare.py`): normalize titles (lowercase, strip punctuation, collapse whitespace), `rapidfuzz.fuzz.token_sort_ratio`. Add corroborating checks: first-author surname match, year match. Output a mismatch flag + score.
- [ ] **1e Mismatch flagging + artifact filter**: flag refs below threshold. Pattern-strip obvious parsing artifacts (truncated strings, encoding noise) before they reach the LLM.
- [ ] **1f LLM filter** (`llm_filter.py`): Opus, structured JSON output, runs **only on the flagged subset**. Input = claimed metadata + retrieved record. Output ∈ {`fabrication`, `formatting_discrepancy`, `reference_error`, `uncertain`}. Prompt anchored on Topaz's own example (abbreviated/informal title that resolves to a real indexed paper = formatting discrepancy, not fabrication).
- [ ] **1g Multi-DB confirmation** (`confirm.py`): for LLM survivors, search **claimed title + first author** (not the PMID) across:
  - PubMed ESearch `…[Title]`
  - Crossref `works?query.bibliographic=` (with mailto)
  - OpenAlex `works?filter=title.search:` (with mailto)
  Each returns a best-match score; "found" = above a match threshold.
- [ ] **1h Decision logic** (`decide.py`):
  - no claimed PMID → `unverifiable`
  - PMID resolves + metadata matches → `cleared`
  - PMID resolves + mismatch → LLM filter → if `formatting_discrepancy` → `cleared`; else confirm search → found under different ID → **F2**; not found anywhere → **F1**
  - PMID dead/invalid → confirm search by claimed title → not found → **F1**; found → **F2**
  - `uncertain` from LLM → `human_review`
- [ ] **1i Logging**: write per-reference record (claimed vs retrieved, mismatch score, LLM verdict, per-DB search hits, final label, confidence, which stage decided).

## Phase 2 — Calibration (~1 hr)
- [ ] Pull ~50 known-clean references from recent PMC OA papers (assume real).
- [ ] Sweep the similarity threshold; pick the precision-first knee (tolerate abbreviated/translated titles without flagging). Record the chosen value + rationale in config.
- [ ] Eyeball the LLM filter on the boundary cases it produced — confirm it isn't flagging legitimate abbreviations.
- [ ] Confirm zero F1 calls on the clean set (or understand every exception). This is your false-positive sanity gate.

## Phase 3 — F1 dataset generation (~2 hr; small category, fine)
- [ ] **3a Schema** (`schema.py`): one record per atomic claim —
  ```json
  {
    "citation_id": "",
    "citance": "",
    "cited_paper": {"title":"","authors":[],"year":null,"journal":"","claimed_pmid":"","claimed_doi":""},
    "source_paper": {"pmcid":"","pmid":"","title":""},
    "label": "F1",
    "secondary_label": null,
    "atomic_claims": [],
    "rationale": "",        // verification trail: claimed title not in PubMed/Crossref/OpenAlex; claimed PMID resolves to unrelated paper
    "confidence": "HIGH",
    "retraction_date": null
  }
  ```
- [ ] **3b Cheap candidate filter first:** the expensive multi-DB confirmation is for *confirming*, not *finding*. The cheap first pass runs on EFetch alone — claimed PMID that doesn't resolve, or resolves to a low-similarity title. Run this over a recent-papers PMC slice (2024–2026) to concentrate the candidate stream before spending any Crossref/OpenAlex calls.
- [ ] **3c Confirmation run:** multi-DB confirm only on what survives 3b → candidate F1 list.
- [ ] **3d Adjudication harness:** notebook review loop — show claimed vs retrieved + the three DB search results, mark confirm / reject / uncertain, export confirmed → gold JSONL. (Streamlit later if volume warrants; notebook is fastest today.)
- [ ] **3e Adjudicate candidates** into gold F1 records. Report the count you find — no target.
- [ ] **3f Controls:** sample `cleared` references as F1-negatives for the dataset (the detector gives these for free; they matter for evaluation).

## Phase 4 — (removed)
No external data request. F1 positives come entirely from your own detector. If the category ends up small, that is the honest source-constrained result and gets documented as a limitation.

## Phase 5 — Validation & metrics framing (~30 min)
- [ ] Compute precision on the adjudicated F1 flags (X / N confirmed). This is your Topaz-comparable number (their 91%).
- [ ] Tabulate the stage-resolution distribution from the logs.
- [ ] Write the honest metric statement: precision reported; recall unestimated without a ground-truth positive set (same posture as Topaz), with the P(fail|real) experiment noted as the planned recall characterization.

## Phase 6 — Commit & document (~30 min)
- [ ] Commit `cre/f1/` + this plan + schema to `astonliu/citation-repair-engine` (rebase if needed).
- [ ] Update `TAXONOMY_DECISION_RULES.md`: F1 operational definition = the Phase-1 conjunction; add the unverifiable-vs-F1 boundary and cite Topaz's exclusion precedent.
- [ ] Save all artifacts to Drive (survive Colab resets).

---

## Today's time blocks (full day)
- **Morning:** Phase 0 + 1a–1e (parser, lookup, comparison, flagging).
- **Midday:** Phase 1f–1i (LLM filter, multi-DB confirm, decision logic, logging) + Phase 2 calibration.
- **Afternoon:** Phase 3 (schema, cheap filter, confirmation run, harness).
- **Evening:** Phase 3e–3f adjudication + Phase 5 metrics + Phase 6 commit.

## Honest constraints (read before banking on outcomes)
1. **F1 will be a small category** and that's acceptable — self-detection on a normal slice yields few positives per thousand papers. Report what you find; document it as a source-constrained limitation.
2. **False positives are the dangerous error.** Threshold and prompt are tuned precision-first on purpose.
3. **Recall is unmeasurable today** (no ground-truth positive set). Don't report a recall number; report the stage-resolution distribution and defer recall to the P(fail|real) experiment.
4. **The detector is the deliverable that must be correct.** Its precision and logging are reportable regardless of how many positives it surfaces.

## Definition of done (today)
- F1 detector runs end-to-end on a PMC slice, produces labeled output + per-reference logs.
- Similarity threshold calibrated; clean-set false-positive gate passed.
- JSONL schema finalized; adjudication harness working; candidates adjudicated into gold F1 records (whatever the count).
- Module + plan + schema committed and on Drive.
