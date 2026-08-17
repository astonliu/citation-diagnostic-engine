# CRE taxonomy audit loop — new-session bootstrap brief

**Written:** 2026-08-16 · **For:** a fresh session running an unattended multi-agent audit loop
**Author of record:** ZD (Aston Liu). **No human is in the loop** except through the Relay agent.

---

## 0. Read this section first, in this order

You are starting cold. Do not state a single CRE fact from memory.

1. **Run the vault protocol.** `/Users/kamachi/cre-brain` — read `00 START HERE.md`, then run
   `./refresh-state.sh --write` and read `CURRENT STATE.md`. **The generated block between the
   VOLATILE markers is the only source of truth for commits, branches, test counts and pins. When
   prose disagrees with the block, the block wins and the prose is the bug.**
   In a Linux sandbox set `CRE_REPO` and `CRE_F3F7` to the mounted paths; a loud
   `TEST SUITE NOT RUN` banner is expected and is not an error.
2. `OPEN ITEMS.md` — priority-ordered. `DECISIONS.md` — closed means closed.
   `CONTRADICTIONS.md` — **read entry 63 in full; it is the audit this loop continues.**
   `DEPENDENCY GRAPH.md` — before proposing any edit to a frozen or pinned file.
3. `cre-f3f7/docs/F1_F8_AUDIT_2026-08-16.md` — the completed first-pass audit. Every finding is
   cited to `file:line`; most were reproduced by execution. **Do not re-derive what is already in
   here. Extend it.**
4. The ten specs in `cre-f3f7/docs/` (§3 below).

---

---

## RULE 0 — every finding comes from the code, read this session

**This overrides everything else in this brief. An agent that breaks it has produced nothing.**

1. **No finding may originate from a document.** Not from a spec, not from the vault, not from
   `F1_F8_AUDIT_2026-08-16.md`, not from a prior agent's summary, not from memory. **Open the file,
   read the lines, and cite them.** Documents tell you *where to look* and *what was supposed to be
   true*. Only the code tells you what is.
2. **Every finding carries a `file:line` citation that the agent read this session.** Not inherited,
   not copied from a spec. Line numbers in this project move — `SAME_WORK_TITLE_SIM_MIN` has moved
   twice and three separate documents cite it wrongly. **A citation you did not open is a guess.**
3. **Default is reproduce by execution.** Write a probe in `/tmp` that imports the real function with
   stubbed seams, run it, and paste what it printed. A finding you could not reproduce is labelled
   **HYPOTHESIS** and says why it could not be run. It may still be worth recording — but it may not
   be presented as established, and the Reality checker must downgrade or reject it on that basis.
4. **Follow the control flow, not the docstring.** This codebase's comments are wrong in at least
   eight places found so far: `f4_strength.py:698-700` calls a branch unreachable that is reachable;
   `judgment_run.py:1599` publishes a false claim about the F4 verifier *in a shipped artifact*;
   `cocitation.py:258-260` describes a padding mechanism that does not exist;
   `biblio_match.py:526` names a gate the code does not run. **When code and comment disagree, that is
   itself a finding — but you must have read both to know.**
5. **Checkers verify citations independently.** A checker that accepts a finding without opening the
   cited file has not checked it. **Reality opens every citation. Blast radius reads the call sites
   and the tests. Cost reads the pins and the frozen artifacts.** Trusting the auditor's quote is the
   one way this loop degrades into agents agreeing with each other about a codebase none of them read.
6. **Code-versus-decision mismatches are the highest-value class**, and they require reading both
   sides: the closed decision in `DECISIONS.md`, and the lines that are supposed to implement it. A
   mismatch means a number somewhere is measuring something other than what it claims.

---

## 1. What CRE is, in three sentences

The Citation Repair Engine diagnoses and repairs faulty citations in the scientific literature across
an **F1–F8 fault taxonomy**. The locked novelty claim is **evidence-backed repair, where prior systems
stop at detection or recommendation** — BibAgent (detection-only), Sarol et al. 2024 (0.59 micro-F1;
merged INDIRECT into ACCURATE so their models cannot detect misattribution), Topaz/CITADEL, CiteGuard,
SemanticCite. Solo author (ZD / Aston Liu), advised by Dr. Kirk Roberts at UTHealth.

**The taxonomy:**

| | fault | decided where |
|---|---|---|
| F1 | fabricated / non-existent reference | pre-band existence check |
| F2 | wrong reference (metadata mismatch) | pre-band, `biblio_match` |
| F3 | misattribution — **provenance only**, right claims wrong source, at FULL coverage (DEC-017) | F3–F7 judgment band |
| F4 | overstatement — wrong strength on a claim the paper does engage | judgment band |
| F5 | temporal supersession / contradiction | judgment band |
| F6 | partial support — backs some atomic claims, silent on others | judgment band |
| F7 | wrong entity | judgment band |
| F8 | retracted reference | pre-band |

---

## 2. Where everything lives

| What | Path | Note |
|---|---|---|
| Vault | `/Users/kamachi/cre-brain` | outside the repo, stays there. Refresh script is read-only against the repo. |
| F3–F7 worktree | `/Users/kamachi/cre-f3f7` | branch `feat/f3-f7-semantic-validator-v1`. **Work here.** |
| F2 clone | `/Users/kamachi/citation-repair-engine` | branch `feat/f2-matcher-revision`. A previous session searched here for an F3–F7 spec and found nothing — wrong tree. |
| F2 provenance pin | `/Users/kamachi/cre-f2` | `feat/f2-final-revision`. Leave alone. |
| Package | `cre-f3f7/citation_repair_F1_handoff/cre/f1/` | 107 files. **No `__init__.py` in `cre/`** — path-based loading. |
| Corpus | Google Drive `Citation-Integrity/Data/corpus_frozen_v1` | 20 XMLs + `frozen_manifest.json`. **Not in any repo** — a session that greps for it finds nothing. |
| Coverage report | Drive `Data/coverage_audit_v1/coverage_report.json` | DEC-081. |

**DEC-024: one worktree per branch, no branch switching in a shared clone.** Open the session with
`~/cre-f3f7` as the working directory. Relative paths and bare `git` in the wrong tree can write to
the F2 clone's `band_prompts.py`, which is the one file the guardrails protect.

**Running the tests** — from *inside* the package dir, or you get 46 collection errors:

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

**Baseline, and state which one you measured against:**
`1956 passed, 1 failed, 12 skipped, 24 xfailed` **with** `anthropic` and `jsonschema` installed;
`1934 passed, 23 failed` **without**. The single failure with them installed
(`test_rerank_stage2_degrades_when_model_unavailable`) is environment-dependent — it asserts graceful
degradation when the cross-encoder cannot load, and a machine with HuggingFace access loads it. **Not
a defect.**

---

## 3. What is already done — do not redo it

**Ten specs exist in `cre-f3f7/docs/`. All untracked (`??`) — a `git clean` deletes them.**

| Spec | Covers |
|---|---|
| `F1_FABRICATION_GUARD_SPEC.md` | transport failure becomes a fabrication accusation |
| `F2_IDENTITY_EVIDENCE_SPEC.md` | **split Part A / Part B — see §5** |
| `F3_REACHABILITY_SPEC.md` | F3 unreachable; gate instrumentation |
| `F4_SCOPE_AND_VISIBILITY_SPEC.md` | abstract-vs-fulltext asymmetry; masked labels |
| `F5_HONESTY_SPEC.md` | reportability hatch; false attestations |
| `F6_SUPPRESSION_FIX_SPEC.md` | **two silent clears — the blocking item** |
| `F6_MARKER_ATTRIBUTION_SPEC.md` | claim-to-marker attribution (implemented 2026-08-16) |
| `F7_ENTITY_IDENTITY_SPEC.md` | dropped label; authority lock; alias misfire |
| `F8_ATTESTATION_SPEC.md` | F8 unimplemented; no per-check attestation |
| `F3F7_PACKET_AND_GATE_SPEC.md` | packet builder + F3 gate. **Its Change 1 is known-wrong — see §5** |

**Landed in code 2026-08-16:** `marker_scope.py` (claim-to-marker attribution, +38 tests), and the
per-sentence claim-extraction cache on both run paths.

**Settled by measurement 2026-08-16:** DEC-080 (`TAXONOMY_DECISION_RULES.md` is VOID in its entirety),
DEC-081 (evidence scope is PMC full text only; 539/1280 = 42.1%; distribution unimodal; 208 of 1280
references carry neither PMID nor DOI and belong in every denominator).

---

## 4. The recurring defect class — this is what you are hunting

**A check that cannot fail.** The project has been bitten by it at least six times: the tautological
queue audit, the `no_llm` branch, the F3 gate (DEC-079), the F7 context gate, the F3 reportability
clause, and the F5 reportability clause.

Its signature: **a path that never ran and a path that ran and found nothing are indistinguishable in
the output.**

The first-pass audit found `fired: 0` is a lie in **five independent ways** — and `seam_status` covers
F3–F7 only, so F1, F2 and F8 have no entry at all. **Assume there are more. That is the loop's
highest-value target.**

---

## 5. Live corrections — carry these, they contradict older documents

1. **`F3F7_PACKET_AND_GATE_SPEC.md` Change 1 is wrong** where it says the packet builder should derive
   its label set from `emitted_labels` / `seam_status`. Three of the five `fired: 0` mechanisms are
   invisible to that rule by construction. The builder must iterate `rec["findings"]` and
   `strength_records[*].derived`.
2. **F5's `reportable` is at `judgment_run.py:1047`, not `:820`.** Line 820 is co-citation routing.
3. **F5 now has a real prompt** (`f5_contradiction_prompt.py:165`) — but unfrozen, version string
   unvalidated, parser version never published.
4. **`SAME_WORK_TITLE_SIM_MIN = 0.92` is at `biblio_match.py:120`.** Older specs cite `:139` and
   `:152`. Both stale. **Re-read before quoting; never restate a constant from memory.**
5. **`TAXONOMY_DECISION_RULES.md` is VOID** (DEC-080). Its "zero atomic claims supported → F3" rule is
   the inverse of DEC-017. **Nothing currently replaces it as an annotator-facing codebook.**
6. **DEC-078's `PMC13294812` figures (27 evaluable / 52 held) are wrong** — it derived evaluable by
   subtraction, so two unreachable references were counted as readable. Correct: 25 / 54.

---

## 6. The locked constraints — violating one invalidates the work

- **`band_prompts.py` stays byte-identical.** Blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`, pinned by `test_band_prompts_blob_oid_is_unchanged`.
  The frozen prompt packages seal it by whole blob OID. **If a fix appears to require a prompt change,
  that is a decision, not an implementation detail — route it to the Relay agent.**
- **Seed 47 is adjudicated and `RESERVE_SEEDS = (31, 37, 41, 43, 47)` is EXHAUSTED.** DEC-058: 82 HIGH
  rows labelled blind, figure of record **74/80 = 0.9250 [0.8459, 0.9652]** (also 76/82 = 0.9268 and
  74/82 = 0.9024 strict floor; all three clear the preregistered Wilson-LB > 0.8 gate). DEC-057A's
  clause has triggered: **after seed 47's HIGH rows are seen, no rule change, no threshold change.**
  **Any F2 banding change destroys the only adjudicated precision figure the project has, with no seed
  left to replace it.** This is why the F2 spec is split.
- **Precision-first.** Ambiguity escalates to human review. It never becomes an accusation **and it
  never becomes a silent clear.** Both halves are load-bearing — the two blocking F6 defects are
  violations of the second half.
- **Claude never assigns semantic labels** (F3 provenance etc.) and never curates ground truth.
- **Never use the detector's own flags as gold.** Circular.
- **Naturally-occurring data only for gold sets.** Calibration examples may be hunted; gold may not.
- **`citation_id` / `item_key` stay `"<citing_pmcid>:<ref_id>"`** — Band 1's disposition joins on it.
- **F2 is precision-only in evaluation, but recall is non-negotiable in the matcher.**
- **`author_match` is tri-state**; `None` means unknown. Test with `is False`, never a falsy check.
- **No-rewrite discipline.** Targeted amendments; never overwrite registered content.
- **No `Co-Authored-By` trailers.**

**Regression-guard PMIDs** — must keep banding correctly:
`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`,
plus the two confirmed F3 cases (compare on banding, not evidence-record shape).

---

## 7. The agent architecture

**13 agents: 8 auditors, 3 checkers, 1 relay, 1 orchestrator (you).**

### 7.1 The eight Auditors — one per label

Each owns exactly one taxonomy label and exactly one spec file. **They append findings; they never
edit code.**

Each round, an auditor:

1. Re-reads its label's spec and the corresponding section of `F1_F8_AUDIT_2026-08-16.md` — **to
   learn where to look, not to source findings from** (RULE 0.1).
2. **Opens the code.** Traces every path that can produce the label, from construction site to
   published artifact, following control flow rather than docstrings. Cites `file:line` for each step,
   read this session.
3. Cross-checks what the code does against the **authoritative documents** — `DECISIONS.md`, the
   surviving taxonomy documents, the frozen specs. **Both sides must be read.** A mismatch between
   code and a closed decision is the highest-value finding class.
4. **Reproduces by execution.** Probe in `/tmp`, real functions, stubbed seams, output pasted into the
   finding. Anything unreproduced is labelled **HYPOTHESIS** with the reason.
5. Submits findings to the Checkers. **It does not write them into the spec itself.**

**Auditors may spawn their own subagents** to parallelise a large surface.

### 7.2 The three Checkers — the regulator

Three, not one, because the project's failure modes are three different questions and one agent
answering all three tends to wave through the easy ones:

| Checker | Asks | Must read, itself |
|---|---|---|
| **Reality** | Is the finding real, reproduced, and **reachable in the production configuration**? Many findings sit on paths that cannot execute as configured. | **Every cited line**, plus the gates upstream of it. Re-runs the probe. |
| **Blast radius** | Does the fix risk more than the bug? What does it touch, what tests move, what could it break? A fix that trades a visible defect for an invisible one is a rejection. | **Every call site** of the function, and the tests that cover it. |
| **Cost** | Does it move a reported number, spend a seed, or require a decision ZD has not made? Does it touch a frozen or pinned file? | The **pins**, `DEPENDENCY GRAPH.md`, and the relevant `DECISIONS.md` entries. |

**A checker that has not opened the file has not checked the finding** (RULE 0.5). If a citation does
not say what the auditor claims, that is an automatic REJECT **and** it goes in the rejection register
with the discrepancy noted — a bad citation is a signal about that auditor's whole round.

**Verdict vocabulary — every finding gets exactly one:**

- **LAND** — real, reachable, worth it, safe. Appended to the spec.
- **DEFER** — real but not worth it now. Appended to a `## Deferred` section **with the reason**, so it
  is not re-found.
- **REJECT** — not real, not reachable, or the cure is worse. **Not appended.** Logged with the reason
  in a rejection register, so no auditor re-raises it.
- **ASK-ZD** — needs a decision. Goes to the Relay agent. **The auditor does not block on it** — it
  records the item as `BLOCKED-ON-ZD` and continues.

**A finding lands only on unanimous LAND.** Any single REJECT kills it. This asymmetry is deliberate:
a wrongly-rejected finding costs one re-discovery; a wrongly-landed fix costs a regression in code
nobody was watching.

**The anti-pollution rubric.** REJECT when any of these is true:

- It cannot fire in the production configuration, and instrumenting it is already covered elsewhere.
- It is a restatement of an existing item at a different line number.
- It is a style, naming or typing preference with no behavioural consequence.
- The fix's blast radius exceeds the defect's reach.
- It proposes a threshold, constant or policy that nobody has adjudicated. **Inventing a number is
  worse than leaving a gap**, because an unadjudicated constant later gets cited as though it were
  decided.
- It is a comment-only fix on a comment nobody reads. *(Exception: a comment that makes a **false
  factual claim in a published artifact** — like the F4 `manifest["warning"]` — is a real defect,
  because a reader acts on it.)*

**The diminishing-returns test — the Checkers own the loop's end.** If the last three accepted
findings for a stratum were all instrumentation-only or cosmetic, the Checkers declare that stratum
**SATURATED** and instruct its auditor to stop, regardless of how many rounds it has run. **Say this
out loud in the report.** Do not keep listing fixes that no longer change anything.

### 7.3 The Relay agent — the only channel to ZD

Collects `ASK-ZD` items from all agents. **Deduplicates and batches them** — ZD gets one consolidated
list, not thirteen streams. Each question must state: what is blocked, the options, what each option
costs, and the agent's recommendation. **A question with no recommendation is not ready to send.**

The Relay never answers on ZD's behalf and never guesses. Unanswered questions stay `BLOCKED-ON-ZD`
and the loop continues around them.

### 7.4 You, the orchestrator

Run the rounds, keep the registers, enforce the stopping condition, and write the vault's end-of-session
half. **Do not adjudicate findings yourself** — that is what the Checkers are for.

---

## 8. The stopping condition

Per stratum, **three consecutive rounds** in which the auditor surfaces **zero findings that the
Checkers accept as genuine**. "Genuine" is the Checkers' verdict, never the auditor's.

- A round producing only REJECTs counts as a clear round.
- A round producing a DEFER counts as a clear round.
- A round producing **one LAND resets the counter to zero.**
- **SATURATED** (§7.2) also ends the stratum, immediately.

An auditor that reports clear three times running is **done**. It reports `CLEAR` and stops. Do not
restart it.

**The loop ends when all eight strata are CLEAR or SATURATED.** Then write the vault's end half:
`CURRENT STATE` narrative, any decision ZD closed, any contradiction discovered, and re-ordered
`OPEN ITEMS`.

---

## 9. Output contract

Per stratum, one spec file in `cre-f3f7/docs/`, appended to — never rewritten:

- `## Landed findings` — LAND verdicts, each with `file:line`, reproduction, and expected behaviour.
- `## Deferred` — DEFER verdicts with the reason.
- `## Blocked on ZD` — ASK-ZD items with the question as sent to the Relay.
- Each finding carries: **cite / reproduction / reachability / blast radius / cost / verdict.**

Plus two registers:

- `AUDIT_LOOP_REJECTIONS.md` — every REJECT with its reason. **Consult it before raising anything**;
  re-raising a rejected finding is the loop's main pollution risk.
- `AUDIT_LOOP_STATE.md` — per stratum: round number, clear-streak, status.

**Every spec keeps the standing structure**: objective, defect at file+function level, acceptance
matrix, guardrails, regression guards, definition of done, out of scope, verification command. Carry
the §6 constraints into every one.

---

## 10. Known open questions — seed the Relay with these

1. **The reporting unit.** Per citation / per citation-group / per marker cluster. `marker_scope` added
   the third. Group counts move. Surface all three; ZD decides.
2. **The `et al.` sentence-fragmentation defect** (`parser.py:181`). It changes the citance text, which
   is the unit everything downstream is judged against. Affects `PMC12967000`, `PMC13219232`,
   `PMC13295838`.
3. **F2 Part B** — four banding changes, each of which retires 0.9250.
4. **F6 "supports nothing" labelled "partial support"** — a taxonomy question, not an implementation
   detail.
5. **Whether to wire F5 to real PubMed and write a production `f7_evidence_builder`** — both are
   currently test-only, so neither can produce a number.
6. **The replacement annotator codebook**, gated on ZD's first adjudication pass.

---

## 11. What this loop must NOT do

- **Do not implement anything.** Specs only. Implementation is Claude Code's, in a separate session.
- **Do not run a corpus run.** ~$7.22 and ~29 min per document, and blocked on the two F6 silent
  clears.
- **Do not edit `band_prompts.py`.**
- **Do not land any F2 Part B item.**
- **Do not adjudicate rows or assign semantic labels.**
- **Do not invent constants, thresholds or policies.**
- **Do not rewrite an existing spec.** Append.
- **Do not report a rate from a seam that cannot fire.** Say it cannot fire.
- **Do not raise a finding you have not read in the code this session** (RULE 0). No finding sourced
  from a spec, a summary, the vault, or another agent. No inherited line numbers.
- **Do not accept a finding whose citation you have not opened.** Applies to every checker.
