# CRE F1 Stage — Handoff to Claude Code

This package is the F1 (fabricated citation) detection stage of the Citation Repair Engine — a reimplementation of the Topaz et al. (Lancet 2026) verification pipeline, with a Claude Opus LLM filter replacing their Haiku step. Files are in `cre/f1/`.

## Context you need

**What F1 means (locked design rule).** F1 is a *conjunction*, not a single test:
`(claimed-PMID metadata mismatch OR dead PMID) AND survives the LLM formatting filter AND claimed content not found in PubMed/Crossref/OpenAlex`.
A reference with no claimed PMID is `unverifiable` and must **never** be labeled F1. Required by the base-rate math — raw "couldn't find it" is precision-poor.

**Pipeline order (cost matters).** Cheap path on every reference: parse → EFetch claimed PMID → title-similarity compare. Only *flagged* mismatches go down the expensive path: LLM filter → three-database confirmation → decide. Do not reorder so expensive calls run on everything.

**Two label spaces — keep them separate.** The detector emits *pipeline states* (`cleared`, `unverifiable`, `human_review`) plus taxonomy codes. The *dataset/eval vocabulary* is taxonomy only: `F1`–`F8` and `accurate`. `schema.pipeline_state_to_taxonomy` maps states to labels (`cleared`→`accurate`; F-codes pass through; `unverifiable`/`human_review`→`None`, dropped). Do not merge these vocabularies.

**Precision-first.** Anything ambiguous → `human_review` or `cleared`, never F1. A false F1 is a false accusation; that is the expensive error.

**Files:**
- `schema.py` — three record types (`GoldRecord`, `PredictionRecord`, `EvalRecord`), the F6 invariant, label spaces, the detector working object (`Reference`)
- `parser.py` — PMC OA XML → structured references
- `lookup.py` — EFetch + cheap mismatch flag (candidate filter)
- `llm_filter.py` — Opus triage of flagged mismatches
- `confirm.py` — PubMed/Crossref/OpenAlex confirmation search
- `decide.py` — pure decision logic over accumulated evidence
- `run.py` — orchestration + Anthropic completer; writes prediction JSONL + per-reference log JSONL
- `test_schema.py`, `test_pipeline.py` — passing offline tests; protect them

## Schema rules you must not break
- **Label space:** dataset `label` ∈ `{accurate, F1..F8}`. Pipeline states never appear there.
- **F6 invariant** (`check_f6_invariant`, enforced by `GoldRecord.validate`): all atomic claims supported → label ≠ F6; any unsupported → label ≠ accurate. F4/F5/F7 are not claim-derivable and are not constrained; F1/F2/F8 carry no atomic claims.
- **No `schema_version` in records.** Versioning lives in the dataset manifest / filename.
- `PredictionRecord` must keep its `evidence` dict (db_hits, similarity, decided_by) — error analysis depends on it.
- `EvalRecord.score` must keep `repair_correct` — it measures the project's actual contribution.

## Already tested (offline, passing)
Parser (both citation element types), MEDLINE parsing (continuation joins, `Surname Initials` author extraction), similarity flagging, LLM verdict parsing, all decision branches, mocked end-to-end emitting a `PredictionRecord`, F6 invariant both directions, `validate()`, state→taxonomy mapping, eval scoring with repair. Two bugs already fixed: MEDLINE author field is space-separated (not comma), and a package-level submodule/function name shadow.

## NOT tested (your job) — needs live network
- `lookup.fetch_pubmed` (EFetch + `_parse_medline` on real MEDLINE)
- `confirm.search_pubmed` / `search_crossref` / `search_openalex`
- `run.make_completer` (Anthropic SDK call + response extraction)

## Your tasks, Claude Code
1. **Review every file and fix bugs.** Focus:
   - `_parse_medline`: real MEDLINE edge cases — multi-line `TI`/`AU`/`DP`, corporate authors (`CN`), missing `TI`, electronic-only dates. Verify against real EFetch output.
   - `confirm.py`: Crossref `message.items[].title` is a *list*; OpenAlex `results[].title` may be `null`. Confirm parsing and `match_threshold` semantics.
   - `make_completer`: confirm block extraction for the current SDK; handle empty/refusal responses without crashing.
   - Rate limiting: EFetch + ESearch share NCBI limits (~10/s with key). Add throttle + retry-with-backoff in `lookup.py` and `confirm.py` so a scaled run isn't 429'd.
2. **Trip-wire (only if Aston approves — see his Step 4).** Currently the LLM filter runs only on title mismatches, so a fabricated ref whose invented PMID resolves to a similar-titled real paper slips through (recombination false-negative). If approved, add a second flag in `lookup.compare_and_flag`: titles similar but claimed first-author surname absent from the resolved record.
3. **Link citances (real task, not a stub).** `parser.py` currently sets `citance=""` — it captures the reference list but does not link each reference to the in-text sentence that cites it. Implement it: walk the body, find in-text `<xref ref-type="bibr">` markers, and attach the enclosing sentence to the matching `Reference`. Survivable for F1/F2 but essential for F3–F7, where the citance *is* the claim being verified. Populate `Reference.citance` and `cited_reference_marker`.
4. **Write pytest tests for the live-path functions** using recorded fixtures (capture a few real EFetch/ESearch/Crossref/OpenAlex responses, replay them) so network paths are covered without hitting APIs each run.
5. **Do not change** the F1 conjunction, the unverifiable exclusion, the cheap-then-expensive ordering, the two-label-space separation, the F6 invariant, or `repair_correct`/`evidence`. If you think one should change, stop and ask Aston.

After fixing, run `test_schema.py`, `test_pipeline.py`, and `test_adj.py` plus your new fixture tests; report what changed and why.

---

## Next steps for Aston (in order)

**Step 1 — Environment.** `pip install rapidfuzz lxml anthropic requests`. Put `cre/f1/` on the path. Set `ANTHROPIC_API_KEY`, NCBI key, and a `mailto` for Crossref + OpenAlex.

**Step 2 — Smoke test ~10 references by hand.** Build `Reference` objects: two known-real (correct PMID+title), one wrong PMID, one plausible-but-invented title. Run `process_reference` on each. Confirm reals → `cleared`, wrong PMID → `F2`/`F1` correctly, no crashes on live EFetch/search/LLM calls. Catch live-API bugs here, before scaling.

**Step 3 — Calibrate the similarity threshold.** ~50 known-clean references, cheap path only. Tune `sim_threshold` to **zero** F1 calls on the clean set while still flagging genuine mismatches. Record the value + rationale in `config.yaml`. This is your false-positive gate.

**Step 4 — Decide the trip-wire (task 2 above).** Catches more recombination fabrications, raises false positives. Your call. Tell Claude Code yes/no before the scaled run.

**Step 5 — Candidate-generation run.** Full pipeline over a 2024–2026 PMC slice (few thousand papers; scale overnight if yield is low). Outputs: prediction JSONL + per-reference log JSONL. The log gives the stage-resolution distribution.

**Step 6 — Adjudicate.** Use `adjudicate.py` (the notebook scaffold). It loads the prediction + log JSONL, shows claimed-vs-resolved metadata and the three DB search results per candidate, you mark confirm/reject/uncertain, and it writes validated `GoldRecord`s (the F6 invariant is checked on write). Report the count you find — no target.

**Step 7 — Metrics + commit.** Build `EvalRecord`s from gold vs prediction; report precision on adjudicated F1 (X/N — your Topaz-comparable number) and the stage-resolution distribution. Do **not** report recall (no ground-truth positive set). Commit module + plan + schema + calibrated config; save outputs to Drive.

## Honest expectations
- F1 is not the contribution; it must work and be characterized, not be large.
- Fabrication ≈ 4 per 100,000 references — expect a handful to a couple dozen gold positives.
- F2 falls out of the same run for free — capture it; it's a hard-to-source category.
- Recall is unmeasurable now; defer to the planned P(fail|real) experiment.
- `secondary_label` exists in all record types but is unpopulated and unvalidated — define its meaning and its own invariant before using it.
