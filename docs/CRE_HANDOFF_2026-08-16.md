# CRE — session handoff, 2026-08-16 (replaces the session that produced it)

**Test:** could a cold session, or a different model, resume from this alone? Everything below was
established or decided in this session unless marked otherwise.

---

## 0. Before you do anything

**Run the vault protocol.** `/Users/kamachi/cre-brain` → `00 START HERE.md`, then
`./refresh-state.sh --write`, then `CURRENT STATE.md`. **The generated block between the VOLATILE
markers is the only source of truth for commits, branches, test counts and pins. When prose disagrees
with it, the block wins and the prose is the bug.** Then `OPEN ITEMS.md`, `DECISIONS.md`,
`CONTRADICTIONS.md` (**entries 57–65 are this session**), `DEPENDENCY GRAPH.md`.

**Open the session in `~/cre-f3f7`.** A previous session searched `~/citation-repair-engine` for an
F3–F7 spec, found nothing, and correctly refused to proceed — wrong worktree. DEC-024: one worktree
per branch, no switching in a shared clone. Relative paths in the wrong tree can write to the F2
clone's `band_prompts.py`, the one file the guardrails protect.

---

## 1. Project one-liner

CRE diagnoses and repairs faulty citations across an **F1–F8 taxonomy**. Locked novelty claim:
**evidence-backed repair, where prior systems stop at detection or recommendation** — BibAgent
(detection-only), Sarol et al. 2024 (0.59 micro-F1, merged INDIRECT into ACCURATE so their models
cannot detect misattribution), Topaz/CITADEL, CiteGuard, SemanticCite. Solo author (ZD / Aston Liu),
advised by Dr. Kirk Roberts, UTHealth.

---

## 2. THE DEADLINE — this governs every other decision

**Everything, paper and system, by 2026-09-04** (`CURRENT STATE`:574; DECISIONS:1052). **18 days from
this handoff.** The AMIA 2026 High School Scholars abstract + deck (~September 1, DEC-035) is the
softer, nearer item and needs nothing from F3–F7.

### Four gates. Nothing else blocks a number.

1. **The two F6 silent clears** — until they close, no F6 result is defensible.
2. **The packet builder** — adjudication has no instrument without it.
3. **A replacement codebook** — two annotators (DEC-038) cannot label against a document that does not
   exist, and `TAXONOMY_DECISION_RULES.md` is VOID (DEC-080). **This is the item nobody has scheduled
   and it is on the critical path.**
4. **The corpus run** — ~10 h, ~$145, unattended. Then adjudication, ~1 day plus turnaround.

### Recommended scope cut — state it now, do not discover it on 2 September

| stratum | 2026-09-04 status |
|---|---|
| **F2** | **evaluated** — done and adjudicated |
| **F6** | **evaluated** — once the two silent clears close |
| F1 | implemented and described, not evaluated |
| F3 | two confirmed cases, gate documented |
| F5, F7, F8 | **specified and deferred** |

F5 has never touched real PubMed, F7 has no production evidence builder, F8 does not exist. **Three of
eight strata cannot produce a number in 18 days regardless of effort.** "We specify eight fault types
and evaluate the ones we could evaluate honestly" is a defensible paper.

**Methods and the F2 results are writable today** — do not leave them to the end.

---

## 3. Current state

**Branch `feat/f3-f7-semantic-validator-v1`, pushed and level with upstream:**

```
feec638  chore: ignore local per-project virtualenvs (.venv_cre)
8e90cef  fix(F1): transport failure must never become a fabrication accusation
```

`8e90cef` — 9 files, +1110/−56: `confirm.py`, `decide.py`, `eval_report.py`, `lookup.py`, `run.py`,
`schema.py`, `test_f1_fabrication_guard.py` (new), `test_live_paths.py`, `test_pipeline.py`.

**`schema.py` is a GOVERNED module and its digest moved** (CONTRADICTIONS 65):

| | sha256 |
|---|---|
| **committed — record this one** | `da42c4885b16c7095ae70c886d8d04dfba4bfd026b5bb5e84d06ca45f0499588` |
| working tree (incl. F6 hunk) | `f33e88a71c2ef92687d8c146c63e46dfe49b5480b79b4c84a20650045c73da15` |

They differ on purpose; the second keeps moving until the F6 work lands. **Any launch pinned to a
pre-2026-08-16 manifest refuses until the committed value is re-recorded.**

**Still dirty, deliberately excluded from `8e90cef`** — the in-flight F6 marker-attribution work:
`cocitation.py`, `judgment_band.py`, `judgment_run.py`, `parser.py`, `TAXONOMY_DECISION_RULES.md` (the
VOID banner), plus untracked `marker_scope.py` and `test_f6_marker_attribution.py`. `schema.py`
carried a fourth hunk from that work (`citance_marker_clusters`, +24 on `Reference`) which was
excluded via a filtered patch — **a plain `git add schema.py` would have committed a slice of a live
change under an F1 message.**

**Test counts — from DIFFERENT environments, do not pool them:**

| measurement | environment |
|---|---|
| `1998 passed, 12 skipped, 24 xfailed` — committed content only, verified in a throwaway worktree from a detached commit object | ZD's Mac venv |
| `2036` full working tree; gap of exactly **38** = `test_f6_marker_attribution.py` | ZD's Mac venv |
| `1956 passed, 1 failed, 12 skipped, 24 xfailed` | Linux/Colab **with** `anthropic` + `jsonschema` |
| `1934 passed, 23 failed` | Linux/Colab **without** them |

The single failure in the Linux run (`test_rerank_stage2_degrades_when_model_unavailable`) is
environment-dependent — it asserts graceful degradation when the cross-encoder cannot load, and a
machine with HuggingFace access loads it. **Not a defect.**

**Run the suite from inside the package dir**, or you get 46 collection errors (`cre/` has no
`__init__.py`):

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

---

## 4. Closed this session — do not relitigate

- **DEC-080 — `TAXONOMY_DECISION_RULES.md` is VOID in its entirety.** Its Pair 1 rule said *zero atomic
  claims supported → F3*, the exact inverse of DEC-017 (F3 is provenance-only, gated at FULL
  coverage). ZD voided the whole document rather than patch one section; a banner is on both copies.
  **Nothing replaces it as an annotator-facing codebook** — see gate 3.
- **DEC-081 — evidence scope is PMC full text only.** 539/1280 = **42.1%** over `corpus_frozen_v1`.
  Held 741: `no_pmcid` 390, no identifier at all 270, `no_body` 81. Distribution **unimodal**
  (18–61%, no gap), so the corpus mean is defensible and no per-document selection rule is needed.
  The Unpaywall lever is worth only **+69 references (+5.4 pp, to 47.5%)** once restricted to copies
  that are both version-of-record and openly licensed; the other apparent hits are 103 bronze (free to
  read, no licence) and 43 green of which 41 are **submittedVersion preprints** — a different document
  from the one cited. **208 of 1280 (16.3%) carry neither PMID nor DOI and belong in every
  denominator.**
- **F1 ESearch route: `field=title`**, chosen on measurement — 7/7 regression PMIDs self-retrieve
  versus 4/7 today. Record it as a **measured mitigation, not a solved defect** (n=7).
- **Audit-loop output policy:** docs committed by explicit path, one commit per round, never
  `git add -A`, never under `citation_repair_F1_handoff/`, and pushed.

---

## 5. The F1–F8 audit — the substance of this session

Eight independent read-only agents, one per label, every finding cited to `file:line` and most
reproduced by execution. Full report: `docs/F1_F8_AUDIT_2026-08-16.md`. **CONTRADICTIONS 63.**

### The cross-cutting pattern: `fired: 0` is a lie in five independent ways

`seam_status` (`judgment_run.py:1746-1770`) exists so a zero cannot be read as a rate. Five ways a
**wired** seam still reports `fired: 0`:

1. **F3** unreachable; `wired` reads `discriminator_call_llm` only.
2. **F4** confirmed but masked by F6/F7 precedence — `emitted_labels` counts **labels**, not findings.
3. **F5** seams wired + `discriminator_call_llm=None` → a finding with no label.
4. **F7** — the same legacy early return (`judgment_run.py:744` before `:750`) drops a confirmed F7.
5. **F7** — the **default** empty authority table makes it unreachable while reporting `wired: true`.

**`seam_status` covers F3–F7 only. F1, F2 and F8 have no entry.**

### Two precision-first violations in the unsafe direction — both blocking, neither tested

- **`marker_scope` turns a true fault into a clear.** A reference rendered in two marker clusters of
  one sentence is keyed into the first only (first-citance-wins, `parser.py:496`), the second
  cluster's claim is marked `not_asked`, and the reference lands on `FULL_COVERAGE` — **promoted into
  the F3 gate.** Verified with a contradicting abstract. This landed 2026-08-16.
- **The engine suppresses F6 on a per-reference CONTRADICTION.** `judgment_engine.py:437-440` filters
  `own_gaps` by the co-citation flags with no contradiction carve-out, though `cocitation.py:31-33`
  states contradiction survives grouping and `member_route` honours it. At abstract scope **every** F6
  is a contradiction, so every one is suppressible. Band layer and run layer publish opposite verdicts
  on identical evidence.

### One per label, in one line each

- **F1** — a partial NCBI outage makes a real, indexed paper come back `F1 / HIGH`, and confidence is
  HIGH *because* the fetch failed. **Mostly fixed in `8e90cef`**; see §7.
- **F2** — `doi_match` is computed and **read by no decision**; a contradicting DOI is not
  disagreement. Live and offline pipelines disagree on the same row.
- **F3** — unreachable for any input via two undocumented trace-seam gates.
- **F4** — judged against the **abstract** while coverage moved to full text, so a claim established in
  the body is structurally barred from F4 — and every such hold closes the F3 gate for the whole
  reference.
- **F5** — the reportability guard keys on `emitted_labels` and can be walked around; the attestation
  is a module constant, not an observation; retrieval outage and real absence are byte-identical in
  the manifest.
- **F6** — the two above, plus `claims_assessed_negative` wrong on both scopes.
- **F7** — will propose correcting **KRAS to KRAS**; only guard is a `.strip()` id compare and
  `canonical_label` is never compared.
- **F8** — **does not exist.** Six syntactic occurrences, all constant/membership/pass-through. Two
  dispositions, one that checked and one where F8 was never implemented, are **byte-identical**.

---

## 6. Corrections to the record — carry these, they contradict older documents

1. **My own F1 spec was wrong about ESearch, twice** (CONTRADICTIONS 64). The bracketed-title
   hypothesis was false — Entrez tolerates a leading `[`. The proposed remedy (quoting) would be
   **catastrophic** — full titles are not in PubMed's phrase index, so quoting would zero out nearly
   every search corpus-wide. **The real defect:** `f"{title}[Title]"` is not a title search; ATM binds
   `[Title]` to the trailing fragment and parsed *"in a"* as **`in a[Author]`**. Three of the seven
   regression PMIDs return 0 hits on their own exact titles: `16639420`, `18152150`, `27665045`.
2. **`F3F7_PACKET_AND_GATE_SPEC.md` Change 1 is known-wrong** where it says the packet builder should
   derive its label set from `emitted_labels` / `seam_status`. Three of the five `fired: 0` mechanisms
   are invisible to that rule. **Iterate `rec["findings"]` and `strength_records[*].derived`.**
3. **DEC-078's `PMC13294812` figures are wrong** — 27 evaluable / 52 held. It derived evaluable by
   subtraction, so two references carrying neither PMID nor DOI (`B1`, the SEER statistics page ZD
   spotted unaided, and `B9`) were counted as readable. **Correct: 25 / 54.** `no_pmcid` 49 and
   `no_body` 3 match exactly across both measurements.
4. **F5's `reportable` is at `judgment_run.py:1047`, not `:820`.** Line 820 is co-citation routing.
5. **F5 now has a real prompt** (`f5_contradiction_prompt.py:165`) — but unfrozen, version string
   unvalidated, parser version never published.
6. **`SAME_WORK_TITLE_SIM_MIN = 0.92` is at `biblio_match.py:120`.** Older specs cite `:139` and
   `:152`; both stale. It is **duplicated as a separate literal** at `work_identity.py:80`. Never
   restate a constant from memory.
7. **The corpus mixes citation styles** — 17 of 20 numeric, 3 author-year (`PMC12967000`,
   `PMC13219232`, `PMC13295838`). And `parser._sentence_spans` **fragments sentences on the period in
   `et al.`**, producing citances like `", 2006)."` — it protects `Fig.` and `Dr.` but not `et al.`
   (CONTRADICTIONS 61). That changes the citance text, which is the unit everything downstream is
   judged against.

---

## 7. Specs on disk — `cre-f3f7/docs/`

| File | State |
|---|---|
| `F1_FABRICATION_GUARD_SPEC.md` | **mostly implemented in `8e90cef`**; §Defect 4b rewritten with the real ESearch finding |
| `F2_IDENTITY_EVIDENCE_SPEC.md` | **split Part A / Part B** — see §8 |
| `F3_REACHABILITY_SPEC.md` | not started |
| `F4_SCOPE_AND_VISIBILITY_SPEC.md` | not started |
| `F5_HONESTY_SPEC.md` | not started |
| `F6_SUPPRESSION_FIX_SPEC.md` | **not started — the blocking item** |
| `F6_MARKER_ATTRIBUTION_SPEC.md` | implemented 2026-08-16, uncommitted |
| `F7_ENTITY_IDENTITY_SPEC.md` | not started |
| `F8_ATTESTATION_SPEC.md` | not started |
| `F3F7_PACKET_AND_GATE_SPEC.md` | not started; **Change 1 known-wrong** |
| `CRE_AUDIT_LOOP_SESSION_BRIEF.md` + `AUDIT_LOOP_AMENDMENT_01.md` | the loop running in another session |
| `F1_ESEARCH_TERM_FINDING_2026-08-16.md`, `F1_GOVERNANCE_GAP_2026-08-16.md` | Claude Code's write-ups |

**F1 defect status after `8e90cef`:** 1 transport vocabulary — landed. 2 all-three-answer rule —
landed, placed *after* `found_anywhere` so F2 recall is untouched. 3 skipped searches return `None`
— landed. 4 HTTP-200 fault envelopes — landed, with Entrez's nested `errorlist` deliberately excluded
because it appears on legitimate zero-hit searches. **4b ESearch term — NOT fixed**, route chosen
(`field=title`), not implemented. 5 false F2 rationale — landed. 6 per-reference quarantine — landed.
7 `f1_status` instrumentation — landed. 8 governance — **reported, deliberately not acted on**.

**Incidental, generalise it:** `test_pipeline.py` was monkeypatching `run` and `confirm` at collection
time and never restoring them, leaking into every later test. Pre-existing. **Check every suite for
the same pattern** — it makes downstream results untrustworthy silently.

---

## 8. Guardrails — violating one invalidates the work

- **`band_prompts.py` stays byte-identical.** Blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`, pinned by `test_band_prompts_blob_oid_is_unchanged`.
  If a fix appears to need a prompt edit, **stop and report** — that is a decision.
- **Seed 47 is adjudicated and `RESERVE_SEEDS = (31, 37, 41, 43, 47)` is EXHAUSTED.** DEC-058: 82 HIGH
  rows blind-labelled, TRUE_F2 74 · SAME_WORK 5 · CROSS_LANG 1 · AMBIG 2. Figure of record
  **74/80 = 0.9250 [0.8459, 0.9652]**; also 76/82 = 0.9268 and 74/82 = 0.9024 strict floor; all three
  clear the preregistered Wilson-LB > 0.8 gate. Flag rate 82 HIGH of 54,243 scoreable = **0.151%**.
  DEC-057A's clause has triggered: **after seed 47's HIGH rows are seen, no rule change, no threshold
  change.** **F2 Part B destroys this figure with no seed left to replace it.**
- **Precision-first.** Ambiguity escalates. It never becomes an accusation **and it never becomes a
  silent clear.** Both halves are load-bearing.
- **Claude never assigns semantic labels** and never curates ground truth.
- **Never use the detector's own flags as gold.** **Naturally-occurring data only for gold sets.**
- **`citation_id` / `item_key` stay `"<citing_pmcid>:<ref_id>"`.**
- **F2 is precision-only in evaluation; recall is non-negotiable in the matcher.**
- **`author_match` is tri-state**; `None` means unknown. Test with `is False`, never falsy.
- **No-rewrite discipline. No `Co-Authored-By` trailers.**

**Regression-guard PMIDs:** `31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`,
`22926653`, plus the two confirmed F3 cases (compare on banding, not evidence-record shape).

---

## 9. Key paths

| What | Where |
|---|---|
| Vault | `/Users/kamachi/cre-brain` — outside the repo, stays there |
| F3–F7 worktree | `/Users/kamachi/cre-f3f7` — **work here** |
| F2 clone | `/Users/kamachi/citation-repair-engine` — `feat/f2-matcher-revision` |
| F2 provenance pin | `/Users/kamachi/cre-f2` — leave alone |
| Package | `cre-f3f7/citation_repair_F1_handoff/cre/f1/` — 107 files, no `__init__.py` in `cre/` |
| Corpus | Drive `Citation-Integrity/Data/corpus_frozen_v1` — 20 XMLs + `frozen_manifest.json`, **not in any repo** |
| Coverage report | Drive `Data/coverage_audit_v1/coverage_report.json` |
| Colab | ZD's notebook — cells 1–15 from this session (coverage audit, F6 debug, marker-cluster prototypes, test baseline) |

**The adjudication packet has no builder** (CONTRADICTIONS 62) and the `PMC13294812` run artifacts are
gone — the only `judgment_run_manifest.json` on Drive is the old `stage4_smoke_PMC13295119`
abstract-scope run. That is why the F6 debug reconstructed co-citation groups from the frozen XML.

---

## 10. Next actions, in order

1. **Hand `F6_SUPPRESSION_FIX_SPEC.md` to Claude Code.** Everything else waits behind it. Verify on
   return: `band_prompts.py` blob OID unchanged, and all 14 rows of `F6_COCITATION_SPEC.md` passing
   **on the run path**, not only the band path.
2. **Packet builder** — `F3F7_PACKET_AND_GATE_SPEC.md` Change 1, corrected per §6.2.
3. **Start the codebook.** Draft from ZD's own adjudication pass; it gates the two annotators.
4. **Write Methods and the F2 results now**, in parallel. They are frozen and adjudicated.
5. **Land `field=title`** in `confirm.py`; drop the inline `[Title]` suffix at the same time.
6. **Corpus run** once gates 1–3 are closed. ~10 h, ~$145, overnight.
7. **Record the scope cut as a decision** (§2) so it is not discovered on 2 September.

**Triage rule for the audit loop:** it now generates more findings than can be acted on before the
4th. **Everything it produces is post-deadline work except items on the four gates.** Tell the
checkers, or it becomes a machine for growing the backlog during the worst possible fortnight.

**Colab startup discipline:** restart the session after any push (stale `sys.modules`; the restart is
what evicts them, `invalidate_caches()` alone will not), reinstall `rapidfuzz`, read from Drive first.
