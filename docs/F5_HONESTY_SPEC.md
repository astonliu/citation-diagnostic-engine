# F5 — close the reportability hatch and stop the false attestations — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

**Corrections to the vault, all verified — carry these forward:**

| Prior belief | Actual |
|---|---|
| `reportable` hardcoded False at `judgment_run.py:820` | **Wrong line.** `:820` is co-citation routing. The site is **`judgment_run.py:1047`**. |
| F5 alone has no prompt in the repo | **False now.** `f5_contradiction_prompt.py:165`, 4,899 chars, sha256 `47ce9f21f3f693270a90cee8ae6c544e6ead1d9d74d79785b823cf3de4739f2a`, genuinely rendered at `f5_seams.py:325`. |
| F5 declares itself not-performed | **Narrower than believed.** The stub covers *attestation lookup* only, not F5 as a whole. |

---

## Defect 1 — the reportability guard can be walked around

`preband_contract.py:711-731` keys on `emitted_labels`. **Verified**: with F5 seams wired and
`discriminator_call_llm=None`, `judge_pair_finish` takes the legacy early return
(`judgment_run.py:721-744`) and never reaches the label branch at `:772`. The result:

```
reportability.reportable = True | failures = [] | F5-related checks present = []
emitted_labels = {} | predictions row findings = ['F5'] | label = None
f5_records[0].temporal_state = QUALIFYING_CONTRADICTION | f5.queued_rows = 1
```

A `QUALIFYING_CONTRADICTION` rides out inside a run stamped `reportable: True` — **exactly what
`preband_contract.py:709-710` says the clause exists to prevent.**

**Required:** key the clause on `findings` in the predictions, or on
`f5.disposition_counts["surface"] > 0`. Not on `emitted_labels`.

**Note this is the same early return that drops a confirmed F7** (`judgment_run.py:744` before
`:750`). One fix may serve both; coordinate with the F7 spec.

## Defect 2 — `reportable: False` is a literal, and one of three

`judgment_run.py:1047` — `"reportable": False,  # unreachable by construction; stated, not implied` —
reads no state, no policy, no records. `f5_supersession.py:672` sets `self.reportable = False` and
**nothing ever reads it**. `f5_supersession.py:958` writes `"reportable": False` into every per-claim
record; no path writes `True`, though `validate_f5_record` (`:1275`) guards the `True` case.

That is defensible as an honest declaration — **but it means the F5 pipeline can produce a
`QUALIFYING_CONTRADICTION`, queue it, hash it into the chain, and count it in `emitted_labels`, while
three separate places assert it is not reportable.** Decide with ZD whether F5 should be *unreachable*
(gate it off) or *reachable and non-reportable* (current), and make the three sites agree with one
source of truth.

## Defect 3 — an outage and a real absence are byte-identical in the manifest

**Verified**: two runs, one with every retrieval `status="ok", adequacy="empty"` (real absence), one
with `status="failure"` (total outage):

```
MANIFEST F5 BLOCKS IDENTICAL? True
per-record retrieval_status: absence='ok'  outage='failure'
per-record reason:          absence='retrieval_empty' outage='retrieval_failure'
```

The distinction survives **only** per-record in the predictions JSONL. And
`f5_discovery_queue.negative_reason()` (`:44-54`) — the function written to preserve exactly this
distinction — is **dead code**, called only by `test_f5_seams_and_queue.py:92-93`.

`f5_discovery_queue.py:37-41` records that conflating these *"cost calibration run 1 its entire
yield."* **Required:** surface retrieval status in the `f5` manifest block, and call the function that
exists for it.

## Defect 4 — `attestation_lookup_performed` is a constant, not an observation

`judgment_run.py:1038` imports `ATTESTATION_LOOKUP_PERFORMED` from `f5_seams` and publishes it at
`:1057-1058`. It **never inspects the injected `find_supersession_attestation`**. **Verified** with a
real attestation seam injected:

```
manifest attestation_lookup_performed = False
record.path_a_eligible = True | f5_path = B
```

The manifest declares no lookup was performed while the record shows one succeeded. The declaration is
a hardcoded claim about a swappable seam, and it can be wrong in **both** directions.

## Defect 5 — no XOR guard on the F5 seam pair

The full-text pair raises when half-wired (`judgment_run.py:1178-1182`). F5 has no equivalent.
**Verified**: supplying `f5_seams` alone writes an `f5` manifest block, hashes the F5 modules and the
prompt, and creates `f5_discovery_queue.jsonl` — while `seam_status.F5.wired` reads `False`. **Two
contradictory statements in one manifest.**

## Defect 6 — the prompt is unfrozen and its version string unvalidated

`cre/f1/freeze/` holds packages for `claim_extract` and `coverage` only. No F5 package, no pinned
sha256 in any test. `validate_f5_policy` (`f5_supersession.py:252-259`) checks only that
`contradiction_prompt_version` is a nonblank string. **Verified:**

```
validate_f5_policy accepted contradiction_prompt_version='totally_made_up_v99': YES
manifest f5.contradiction_prompt_version = totally_made_up_v99
manifest prompt_sha256['F5_CONTRADICTION_PROMPT'] = 47ce9f21...   (the real prompt)
```

A lie in the version field sits beside a truthful digest with nothing reconciling them. Worse, the
digest is stamped on `f5_seams is not None`, **not** on whether the prompt was rendered — a probe
injected a judge that never touched it and the manifest still published its hash.

**And `RESPONSE_PARSER_VERSION = "strict_f5_contradiction_spanids_v1"`
(`f5_contradiction_prompt.py:55`) is never written to any record or manifest.** DEC-022 requires both
axes; F5 stamps only one. **Freezing the prompt is a decision — report, do not seal it yourself.**

## Defect 7 — the gate string is incomplete

`seam_status.F5.gate` says `"f5_seams AND f5_evidence_builder"` (`judgment_run.py:1758`). **Verified**
there is a third precondition: `discriminator_call_llm is not None`, without which the legacy return
fires. Three further preconditions are undocumented too: non-empty claims (`:628-636`), a `SUPPORTED`
target claim (`f5_supersession.py:1082-1085`; the engine raises otherwise,
`judgment_engine.py:485-488`), and no F7/F6/F4/F3 finding, since F5 rides lowest in the chain.

## Defect 8 — F5 has never run on real data, and `build_f5_seams` has no callers

`build_f5_seams` (`f5_seams.py:349`) has **zero callers anywhere in the repo**.
`production_launcher.py:617` passes `**run_kwargs` and never names F5. No F5 module imports anything
network-capable. **Verified:** 142 F5 tests pass in **0.30 s**. `f5_seams.py:3-5` says so itself —
*"only test fakes have ever satisfied them, so `decide_f5` has never run on real data"* — and that
statement is still accurate.

**Required:** this is not a bug to fix silently. State it in the manifest so no reader can mistake an
F5 zero for a measurement, and give ZD the wiring cost.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| F5 seams wired, `discriminator_call_llm=None`, qualifying contradiction | reportability | **fails**; the finding is not silently reportable |
| `f5_seams` supplied without `f5_evidence_builder` | run | raises, mirroring `judgment_run.py:1178-1182` |
| retrieval outage vs real absence | `f5` manifest block | **distinguishable**; `negative_reason()` actually called |
| real attestation seam injected | `attestation_lookup_performed` | reflects the injected seam, not a module constant |
| fabricated `contradiction_prompt_version` | config | rejected, or reconciled against the digest |
| F5 prompt rendered vs not rendered | `prompt_sha256` | stamped only when rendered |
| any F5 record | parser version | published, per DEC-022 |
| any run | `seam_status.F5.gate` | names all preconditions, incl. `discriminator_call_llm` |
| any run | manifest | states F5 has never run on real data, until it has |

## Guardrails — do NOT change

- **Do not make F5 reportable.** F5's `reportable` is False by design; this spec makes the machinery
  honest, not permissive. Flipping it is ZD's decision and requires a wired, measured seam.
- **Do not freeze the F5 prompt yourself.** Report; sealing is a freeze decision.
- **`band_prompts.py` stays byte-identical** — blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`. Verify and report.
- **Precision-first.** Ambiguity escalates; it never becomes an accusation.
- **Claude never assigns semantic labels.**
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.
The five F5 suites — 142 passed in 0.30 s at audit time — must stay green, and **must stay fast**: if
the runtime rises materially, something reached the network and that is a finding, not a pass.

## Definition of done

- Reportability guard keyed on findings, not `emitted_labels`.
- XOR guard on the seam pair.
- Outage distinguishable from absence in the manifest; `negative_reason()` live.
- Attestation declaration derived from the injected seam.
- Prompt version validated or reconciled; digest stamped only when rendered; parser version published.
- Gate string complete.
- Manifest states F5 has never run on real data.
- Cosmetic: unused `import os` (`f5_seams.py:30`); the write-only `self.reportable`
  (`f5_supersession.py:672`).
- Suite green and still fast; count old → new, stating the environment.

## Out of scope

- Wiring F5 to real PubMed. Cost it and report; ZD decides.
- Any corpus run.
- Freezing the prompt.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
