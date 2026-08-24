# DEC-084 — ready-to-paste entry for `DECISIONS.md`

`DECISIONS.md` lives in the `cre-brain` vault, not in this repository, so this
entry could not be appended by the implementation. **Paste the block below into
`DECISIONS.md` as DEC-084** (084 is the next free number: the highest referenced
anywhere in this repo is DEC-083). Everything else in
`docs/F1_BAND1_HAIKU_SWITCH_SPEC.md`'s definition of done that lives in this repo
is implemented; the two items that do not live here are this entry and the
manuscript's Methods F1 paragraph.

---

## DEC-084 — Band-1 `llm_filter` runs on Claude Haiku 4.5, not Claude Opus 5

**Date:** 2026-08-24 · **Authority:** ZD, direct instruction · **Status:** CLOSED

**Decision.** The Band-1 `llm_filter` model pin is `claude-haiku-4-5`; the
`llm_filter` prompt, the `decide()` conjunction, and every other stage are
unchanged.

**Rationale — cost, not judgment.** Opus 5 measured **$4.5988 for 327 complete
papers** (464 calls, $0.0141 per complete paper) on
`f1f8_only_seed20260824_n1000`. A multi-thousand-paper run at that rate is not
affordable. Haiku 4.5 lists at $1.00 / $5.00 per MTok against Opus 5's $5.00 /
$25.00 (checked 2026-08-24, https://platform.claude.com/docs/en/about-claude/pricing).

**What it supersedes.** The Opus-lever framing in `F1_BUILD_PLAN.md:18` — *"LLM
filter = Claude Opus (your stronger-than-Topaz's-Haiku lever; the one citable F1
improvement)"* — and the matching sentence at `HANDOFF.md:3`. **The F1 novelty
claim is spent.** Topaz's CITADEL used Claude 3.5 Haiku, zero-shot; Haiku 4.5 is a
later model in the same tier, so the F1 stage is now a faithful reimplementation
with no model-side improvement. "A later Haiku, same zero-shot posture" is
defensible; "a stronger model than theirs" is not. Both documents are amended;
the Methods F1 paragraph must be amended in the manuscript.

**Consequences.**
1. Opus-run and Haiku-run papers are **separate strata** and are never pooled in
   one F1 or F2 denominator (DEC-083). The 327 existing complete papers ran on
   Opus. Re-running them under Haiku is a separate decision, not part of this one.
2. `PRICE_USD_PER_MTOK` in the notebook is now Haiku 4.5 rates. Every `spend_usd`
   figure printed before this switch is an **Opus** figure and must be labelled as
   one.
3. Throughput may move in either direction and is **unmeasured**. A higher
   `uncertain` rate routes more references into `confirm()` — three searches each
   — and can eat the saving in OpenAlex credit. Record the `uncertain` rate.
4. F8 is unaffected: it is decided from PubMed publication types and never reaches
   `llm_filter` or `confirm()`.
5. `production_launcher.launch_full`'s `authorized_models` allowlist still pins
   `["claude-opus-5"]` (DEC-068), and its `TEMPERATURE_REJECTING_MODELS` set still
   contains only `claude-opus-5`. This notebook does not use the launcher, so
   neither is consulted; **if the full F1–F8 launcher is ever run, it still pins
   Opus** and this decision does not reach it.

**Gate.** `tools/F1_CALIBRATION_PROBE.py --model claude-haiku-4-5` must pass
before any paid batch. Probe A is the row that matters: a dead PMID whose claimed
work is absent from all three databases must come out F1. If a weaker model calls
it `formatting_discrepancy`, `decide()` clears it and F1 stops firing silently.
