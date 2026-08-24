# F1 — switch the Band-1 LLM filter to Haiku 4.5 — implementation spec

**Date:** 2026-08-24 · **Authority:** ZD, direct instruction
**Branch:** `merge/f2-into-f3f7` · **Engine commit:** `8f28bda330226910c817bd4127d7e2ec31d50714`

---

## Objective

Band 1's `llm_filter` currently runs on `claude-opus-5`. Switch it to **Haiku 4.5**.
Measured cost was **$4.5988 / 327 complete papers** (464 calls) on Opus; the purpose of the
change is to make a multi-thousand-paper run affordable.

**This is a NOTEBOOK change, not a repo change.** `cre/f1/llm_filter.py` is transport-agnostic
by design — its own docstring: *"pass any callable `complete(prompt:str)->str`"*. No engine
file is edited.

---

## Change

### 1. `CRE_F1_F8_ONLY_RANDOM_PMCID_COLAB.ipynb`, Section 2

```python
MODEL = "claude-haiku-4-5"      # was "claude-opus-5"
```

`MODEL` feeds `make_band1_transport()` (Section 3) and is recorded in every `model_events.jsonl`
row, so the switch is self-documenting in the telemetry. `MODEL_MAX_TOKENS = 400` is unchanged —
it matches `run.make_completer`'s default and is ample for the four-way verdict.

**VERIFY THE STRING BEFORE THE RUN.** The exact identifier must be confirmed against the provider,
not assumed. Acceptance row 1 below is a live call that fails loudly on a wrong string rather than
silently falling back.

### 2. Nothing else changes

- No `temperature`, no `assistant_prefill` — the notebook sends neither (DEC-070, DEC-071).
- `PRICE_USD_PER_MTOK` in Section 2 is **Opus pricing** and will now be wrong. Either update it to
  Haiku 4.5's published rates or treat every `spend_usd` figure as void. Do not leave it stale and
  quote it.
- `production_launcher.launch_full` is not used by this notebook, so its `authorized_models`
  allowlist (DEC-068: `["claude-opus-5"]`) is not consulted. **If the full F1–F8 launcher is ever
  run, that allowlist still pins Opus** and this change does not reach it.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| One live call with `MODEL` set | HTTP result | 200, non-empty text — **not** `not_found_error` |
| `model_events.jsonl`, any row | `model` | `claude-haiku-4-5` |
| `F1_CALIBRATION_PROBE.py`, Probe A (dead PMID + work absent everywhere) | label | **F1** |
| `F1_CALIBRATION_PROBE.py`, Probe B (PMID 31665581, real title) | label | **cleared**, never F1 |
| `llm_filter` output over 50 flagged rows | verdict values | all within `{fabrication, formatting_discrepancy, reference_error, uncertain}` |
| Same 50 rows | JSON parse failures | 0 |
| Rate of `uncertain` vs the Opus run | — | **record it**; a rise routes more rows to `confirm()` and raises OpenAlex/NCBI volume |
| One 100-paper batch | `spend_usd` per complete paper | recorded, compared to Opus's $0.0141 |
| Same batch | `f1_reportable_this_batch` | True (else the batch says nothing about F1 either way) |

**Probe A and Probe B are the gate.** If a weaker model calls a fabricated reference
`formatting_discrepancy`, `decide()` clears it and F1 silently stops firing. Probe A catches exactly
that. Run it under Haiku before any paid batch.

---

## Consequences to record, not to argue about

1. **The F1 novelty claim is spent.** `F1_BUILD_PLAN.md:18` and `HANDOFF.md:3` describe the Opus
   filter as *"your stronger-than-Topaz's-Haiku lever; the one citable F1 improvement"*, and the
   Lancet paper confirms CITADEL used **Claude 3.5 Haiku, zero-shot**. Under this change the F1
   stage is a faithful reimplementation with no model-side improvement. **Update both documents and
   the Methods F1 paragraph** so the manuscript does not claim a lever it no longer has. Haiku 4.5
   is a later model than Claude 3.5 Haiku — that is a defensible sentence, and it is a different
   sentence from the one currently written.
2. **Opus and Haiku runs are NOT poolable for F1 or F2.** The 327 complete papers already in
   `f1f8_only_seed20260824_n1000` ran on Opus. Any Haiku papers form a separate stratum. Either
   re-run those 327 under Haiku or report the two separately and never in one denominator (DEC-083).
3. **F8 is unaffected.** It is decided deterministically from PubMed publication types and never
   reaches `llm_filter` or `confirm()`.
4. **Throughput may move in either direction, and it is unmeasured.** A higher `uncertain` rate
   sends more references into `confirm()`, which is three searches each and consumes OpenAlex budget
   — the acceptance matrix records this precisely because it could eat the saving.

---

## Guardrails

- **No engine file is edited.** `llm_filter.py`, `decide.py`, `confirm.py`, `run.py` untouched.
- The `decide()` conjunction is unchanged, so precision is still protected deterministically: a
  model verdict alone cannot produce F1 — it still requires `FETCH_ANSWERED_ABSENT` **and**
  `fully_answered(db_hits)` **and** `not found_anywhere`.
- `band_prompts.py` not edited; the `llm_filter` PROMPT text is not edited. **Model swap only** —
  do not "tune the prompt for Haiku" in the same change, or nothing is attributable.
- Path-based module loading; restart Colab after any push.
- Claude assigns no semantic labels and curates no ground truth.
- Never use the detector's own flags as gold.

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` must band exactly
as before. `31665581` is Probe B and must clear.

---

## Definition of done

- Acceptance matrix verified, including both calibration probes under Haiku.
- `PRICE_USD_PER_MTOK` updated to Haiku 4.5 rates, with the source URL and check date recorded, or
  every cost figure explicitly marked void.
- Measured $/complete-paper under Haiku recorded next to Opus's $0.0141.
- `F1_BUILD_PLAN.md`, `HANDOFF.md` and the Methods F1 paragraph amended per consequence 1.
- A decision recorded in `DECISIONS.md`: next `DEC-###`, date, one-sentence decision, rationale
  (cost), what it supersedes (the Opus-lever framing in `F1_BUILD_PLAN.md:18`).

## Out of scope

- Any prompt change. Model swap only.
- Any Band 2 / F3–F7 model change. DEC-068 stands for the judgment band.
- Re-running the existing 327 Opus papers. Separate decision.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

Then, in the notebook, after setting `MODEL`:

```python
# Estimated runtime: ~10 seconds. One live call; fails loudly on a bad model string.
print(BAND1_COMPLETE("Reply with the single word: ok")[:80])
print("model recorded as:", BAND1_COMPLETE.model_id)
```
