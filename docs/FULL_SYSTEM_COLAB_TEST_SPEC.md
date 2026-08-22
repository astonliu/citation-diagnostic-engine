# CRE full-system test notebook — build spec

**Date:** 2026-08-18 · **Tree of record:** `merge/f2-into-f3f7` @ `0a8e663b926af94f7da8d67964118775ab580268`
**Audience:** whoever generates the notebook (Claude Code). **This spec is the contract; the cells are yours.**
**Standing method:** the requirement and the constraint are stated. If a constraint here is wrong, stop
and report rather than reconciling silently.

---

## Objective

Build one Colab notebook that answers a single question end to end: **for each of F1–F8, did the check
run, and if it ran, what did it find?** Those are two different questions and the system currently
answers them with the same number.

**Not** a demo. **Not** a rate. A conformance harness — *conformance* here means: the run did what the
manifest says it did.

---

## ⚠ THE ONE RULE — a zero is never a pass

This project's entire audit was about one defect shape: **a check that never ran and a check that ran
and found nothing produce identical output.** Three strata currently cannot fire at all in the
production configuration, so a naive full-system run prints `F3: 0, F7: 0, F8: 0` and looks clean.

**Every stage of this notebook must print, per stratum, a REACHABILITY verdict before it prints a
count.** A count with no reachability verdict beside it is a defect in the notebook, not a result.

Three vocabulary items used throughout:

- **Seam** — a function the pipeline calls out to, injected by the caller rather than written inside
  the module. `run_natural_judgment` makes **no model call itself**; every one goes through an injected
  callable. An unwired seam means that stratum is silently off.
- **Reachable** — a wired seam plus every gate open, so the stratum *could* produce a finding.
- **Governed module** — a file whose bytes are hashed into the run manifest, so a number cannot move
  without an artifact moving. There are **17** of them in `production_launcher.GOVERNING_MODULES`.

---

## Environment facts (assume these; assert them, do not trust them)

- Repo: `github.com/astonliu/citation-repair-engine`. **`/Users/kamachi/cre-f3f7` is a git *worktree* of
  that same repo** — a second working folder on a different branch, not a separate project. Colab clones
  the repo and checks out `merge/f2-into-f3f7`.
- Colab checkout: `/content/citation-repair-engine`; package root
  `/content/citation-repair-engine/citation_repair_F1_handoff`; modules under `cre/f1/`.
- **`cre/` has no `__init__.py`** — modules load by path. A stale checkout silently runs old code.
  **After any push, Runtime > Restart session.** `importlib.invalidate_caches()` alone does not evict
  already-imported modules from `sys.modules`.
- Durable I/O on Drive: `DATA = "/content/drive/MyDrive/Citation-Integrity/Data"`. Treat `/content` as
  scratch that vanishes on reset.
- `rapidfuzz` is required and not preinstalled — reinstall after every runtime reset.
- NCBI: `EMAIL = "aston.hliu@gmail.com"`, key from Colab Secrets (`NCBI_API_KEY`) via
  `google.colab.userdata`. With a key, ~9 req/s ceiling; ~3 rec/s effective including parse.
- Pinned test environment at `0a8e663`: `anthropic==0.122.0`, `jsonschema==4.26.0`, `pytest==9.1.1`.
  **These change the suite count — state the environment with any count you report.**

## Known state the notebook must NOT rediscover

| fact | consequence for the notebook |
|---|---|
| Suite at `0a8e663`: **2480 passed, 12 skipped, 39 xfailed** in ~51 s | Stage 1 asserts this exact triple |
| `band_prompts.py` blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c` | asserted in Stage 0 and restated at the end |
| **F3 is NOT LANDED** — five acceptance lines deferred; `seam_status.F3` still reduces wiring to the discriminator gate (`judgment_run.py:2063`); reportability still conditional on an observed F3 (`preband_contract.py:745`) | **F3 cannot fire. `F3: 0` is expected and is not a result.** Print `UNREACHABLE — gate-opening deferred` |
| **F7 has no production evidence builder** (`judgment_run.py:1088` discloses this) | **F7 cannot fire.** Print `UNREACHABLE — no evidence builder` |
| **F8 has no local detector in this Band-2 package** — it consumes upstream attestation only (`judgment_run.py:2098`) | **F8 cannot fire here.** Print `NOT IMPLEMENTED IN THIS PACKAGE` |
| **F5 has never run on real data** (`judgment_run.py:1240`) | Print `NEVER RUN ON REAL DATA` beside any F5 number |
| **`cocitation.py` is absent from `GOVERNING_MODULES`** (verified: grep count 0) — it is the module that decides whether F6 is raised | Print an explicit warning: **the F6 suppression module's bytes are not hashed into the manifest.** Editing it moves no digest |
| F6's `80.6%` / `27.7%` figures are **unre-derived** | The notebook must never print them |
| `parser.py` semantics deliberately unchanged; only the tiling diagnostic exists (`parser.py:258`) | Stage 3 reads the diagnostic counter; it is expected to be **non-zero** and that is correct behaviour, not a failure |

---

## Cell-by-cell blueprint

Nine cells. **Every cell opens with `# Estimated runtime: ~X` and its basis.** Cells 0–5 spend **no
money and make no model call**. Cell 6 is the first that does, and Cell 5 exists to stop you reaching it
by accident.

### Cell 0 — Bootstrap + provenance assert · ~45–90 s (pip 15–30 s + clone 10–30 s + fetch)

Follow the bootstrap template. Set `BRANCH = "merge/f2-into-f3f7"`. `git fetch` + `git reset --hard
origin/BRANCH` — **never a plain `git pull`**, which can leave a dirty tree.

Then assert, and **fail loudly on each**:

- `git rev-parse HEAD` equals the commit under test; print it.
- `git status --porcelain` is empty.
- `git rev-parse HEAD:citation_repair_F1_handoff/cre/f1/band_prompts.py` ==
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
- `len(production_launcher.GOVERNING_MODULES) == 17`.
- The RULE G fix is loaded — the check tied to the most recent push, so a stale runtime is caught here
  and not four cells later.

**Prose to send with this cell:** restart the session first; the restart is what evicts stale modules.

### Cell 1 — Offline suite · ~60 s (measured 51.14 s at `0a8e663`)

`PYTHONPATH=. python -m pytest cre/f1 -q`. Parse the summary line and assert the exact triple
**2480 / 12 / 39**. Print the three pinned package versions alongside it — a count without its
environment is not comparable.

**A drift in any of the three numbers stops the notebook.** Do not continue and interpret later stages
against an unknown tree.

### Cell 2 — Governance preflight, no run · ~5 s

Call the launcher's verifiers **without** launching:
`verify_tree`, `verify_temperature_governance`, `verify_prefill_governance`, `verify_judge_governance`,
`assert_receipt_shape`.

`production_launcher.launch` raises `LaunchRefused` **before** starting a run if any precondition fails.
This cell proves the refusals work while nothing is at stake.

Assert two negatives explicitly — both are landed audit findings and both must now hold:

1. `verify_tree` against a directory missing the governing modules **refuses**; it does not return
   success with an empty digest map.
2. `verify_judge_governance` on the **dated-amendment path with a same-family judge** does **not**
   publish *"The preregistered different-family judge arrangement was used."*

### Cell 3 — Deterministic replay, offline · ~10–20 s (378 rows, pure local)

`adjudication_packet.build_packet` over the historical artifact. **No network, no model, no corpus.**
This is the cheapest full-shape exercise of the record schema that exists.

Print and assert:

- row count `378`
- `_assert_unbiased` passes — the packet carries no detector verdict into the annotator's view
- an unmappable finding **fails loudly** rather than being dropped
- `parser.py`'s tiling-diagnostic counter, with an explicit label: *"spans that did not tile the input —
  non-zero is expected; the segmentation fix is deliberately not enacted."*

### Cell 4 — Live NCBI, no model · ~10 s (7 PMIDs at ~3 rec/s + overhead)

Pre-flight first: print the signature of every function the cell is about to call, then do **exactly one**
live round-trip to confirm credentials and response shape before anything batched.

Then resolve the seven regression-guard PMIDs and assert each bands as before:
`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.

Assert the F1 transport distinction — a landed finding: **a fetch that failed is distinguishable from a
reference that is genuinely absent**, in the record and in the manifest. Force one deliberate transport
failure (bad host or forced timeout) and show the two produce different records. **If they produce the
same record, that is the audit's core defect alive in production; stop the notebook.**

### Cell 5 — COST GATE · ~0 s (prints and halts)

Prints the cost and **raises** unless a flag set in the cell above it is explicitly `True`. Nothing
below runs by accident.

```
one document  : ~29 min, ~$7.22
corpus_frozen_v1 (20 docs): ~10 h, ~$144
```

State plainly: everything above this line is free and repeatable; everything below spends money and
takes hours.

### Cell 6 — One document, end to end · ~29 min, ~$7.22

`production_launcher.launch(...)` — the only production entry point. Not `run_natural_judgment`
directly; the launcher is what enforces the governance the manifest then claims.

`max_docs=1`. Write outputs under `DATA`, never `/content`.

Print progress on the per-pair loop — a silent 29-minute cell is indistinguishable from a hang.

### Cell 7 — The reachability report · ~5 s · **this is the notebook's headline output**

Read the run manifest and print one row per stratum. **Reachability comes first; the count comes second
and is meaningless without it.**

| stratum | seam wired? | gates open? | reachable? | findings | what a 0 means here |
|---|---|---|---|---|---|
| F1 | | | | | |
| F2 | | | | | |
| F3 | | | **NO** | 0 | gate-opening deferred — not a result |
| F4 | | | | | |
| F5 | | | **NO** | 0 | never run on real data |
| F6 | | | | | ⚠ `cocitation.py` not in `GOVERNING_MODULES` |
| F7 | | | **NO** | 0 | no production evidence builder |
| F8 | | | **N/A** | — | not implemented in this Band-2 package |

Derive every cell from the manifest, **not from this table** — this table is what the manifest is
expected to say, and the notebook's job is to catch it saying something else.

Assert `f7.wired` and `seam_status.F7.wired` cannot disagree. Assert `seam_status` covers F1, F2 and F8.
Print the hold-reason histogram and the F4 outcome distribution.

### Cell 8 — Corpus run, checkpointed · ~10 h, ~$144 · **behind a second gate**

Only after Cell 7 reads correctly on one document. Write incremental progress to a JSONL on Drive and
skip completed documents on restart, so a dropped runtime costs one checkpoint and not the whole run.

**No anti-idle or keep-alive script.** The answer to a dropped runtime is idempotent resume.

---

## Acceptance matrix

| cell | check | expected |
|---|---|---|
| 0 | `band_prompts.py` blob OID | `fa01126e2b9482d450065fd70cd0eb1fea816f5c` |
| 0 | `len(GOVERNING_MODULES)` | `17` |
| 0 | `git status --porcelain` | empty |
| 1 | suite triple | `2480 passed, 12 skipped, 39 xfailed` |
| 2 | `verify_tree` on a dir missing the modules | **refuses**, no empty-digest success |
| 2 | amendment path + same-family judge | compliance note does **not** claim a different-family judge |
| 3 | packet rows | `378` |
| 3 | detector verdict in annotator view | **absent** (`_assert_unbiased` passes) |
| 3 | tiling-diagnostic counter | present; non-zero expected and labelled as expected |
| 4 | 7 regression PMIDs | band as before |
| 4 | transport failure vs genuine absence | **two distinct records** |
| 7 | F3 / F5 / F7 rows | `UNREACHABLE` printed **before** the count |
| 7 | F6 row | carries the `cocitation.py` not-governed warning |
| 7 | `f7.wired` vs `seam_status.F7.wired` | cannot disagree |

---

## Guardrails

- **`band_prompts.py` byte-identical.** Asserted in Cell 0, restated at the end.
- **No F2 banding change and no re-banding of any seed.** Seed 47 is adjudicated,
  `RESERVE_SEEDS = (31,37,41,43,47)` is EXHAUSTED, `74/80 = 0.9250` has no replacement.
- **Do not print or derive an F2 precision figure from this notebook.** It runs a different population.
- **Do not print `80.6%` or `27.7%`.** Unre-derived.
- **No invented constants.** If a threshold is needed, read it from the code and cite the line.
- **`author_match` is tri-state** — `None` means unknown. Test with `is False`, never a falsy check.
- **`citation_id` / `item_key` stay `"<citing_pmcid>:<ref_id>"`.**
- **Claude never assigns semantic labels or curates ground truth**, and the detector's own flags are
  never used as gold.
- **The notebook reads; it does not fix.** A failing assert stops the notebook and reports. It does not
  patch the tree and continue.

## Regression guards

`31665581` · `16639420` · `18152150` · `27665045` · `25750229` · `32355637` · `22926653` — band as
before, asserted in Cell 4.

## Definition of done

- Cells 0–5 run start to finish with **zero** network cost beyond NCBI and **zero** model calls, and
  every assert passes.
- The Cell 7 table prints reachability **before** every count, and the three unreachable strata say so
  in words rather than showing a bare `0`.
- A deliberately induced transport failure produces a different record from a genuine absence, shown
  side by side in the output.
- Suite triple and the three pinned package versions printed together.
- `band_prompts.py` blob OID printed unchanged at the end of the run.
- Cell 8 resumes correctly after a forced interrupt — demonstrate it, do not assert it in prose.

## Out of scope

- **Fixing anything.** This notebook measures; the four routed decisions stay routed.
- Re-deriving F6's published figures.
- Re-banding, redrawing, or re-reporting any seed.
- Wiring an F7 evidence builder, an F3 gate, or an F8 detector — each is a separate build.
- `REPAIR_V1` — still proposed, not enacted.

## Verification command (local, before any Colab run)

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
