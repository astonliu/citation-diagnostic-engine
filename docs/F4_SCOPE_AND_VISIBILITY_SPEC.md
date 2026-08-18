# F4 — evidence scope, visibility, and the verifier record — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## Defect 1 — F4 is judged against the ABSTRACT while coverage moved to full text

`f4_strength.py:720` reads `evidence.get("cited_abstract")` and **never** `cited_fulltext` — zero
occurrences in the file. That one string is: the presence gate (`:735`), the generator prompt's
`<<ABSTRACT>>` (`:763`), the verifier prompt's `<<ABSTRACT>>` (`:781`), and — decisively — the
substring corpus for `cited_strength_span` (`:640`).

Coverage on the DEC-030/032 path is judged against the **body** (`judgment_run.py:499-507`), and the
record is stamped `evidence_scope: "fulltext_sections"` (`:527`). The asymmetry survives because
`assemble_evidence` fetches the abstract unconditionally on both paths
(`judgment_band.py:302-308`), so F4 silently keeps running at abstract scope.

**Verified by execution**, coverage v3 over a body whose weaker language exists only in the body:

```
manifest evidence_scope_effective: fulltext_sections
coverage established             : [True]
F4 record: assessed=True derived='UNJUDGEABLE' reason='span_unverifiable'
provenance block (F3 ran?): None
```

**Four consequences.**

1. **A claim established by a Results paragraph is structurally barred from F4**, because
   `cited_strength_span not in cited_abstract` (`:640`) holds by construction. F4's reach shrinks in
   exact proportion to how much full text widens coverage.
2. **The loss is invisible.** It lands in `span_unverifiable`, indistinguishable from a model that
   fabricated a span.
3. **The generator is fed a false premise.** `F4_STRENGTH_PROMPT` (`:280-283`) asserts *"The claim is
   already established as supported by that abstract"*. On the v3 path it was not.
4. **It closes the F3 gate.** Every such hold flips the claim to `UNJUDGEABLE`, collapsing
   `all_supported` (`judgment_run.py:655-656`). This is a direct mechanical contributor to DEC-079:
   an abstract-scoped F4 hold on a full-text run removes the reference from F3's denominator before
   F3 is consulted. **Wiring `discriminator_call_llm` to enable F3 also enables the mechanism that
   most often prevents it running.**

**Also verified:** the docstring at `f4_strength.py:698-700` claims the no-abstract branch *"should be
unreachable"*. It is reachable — `evidence_is_usable` (`band_prompts.py:228-244`) gates only the v2
path, and `judgment_run.py:499-507` applies no abstract check to v3.

**Required:** F4's evidence basis must match the run's. Either give F4 the full text, or record
per-verdict that F4 was judged at abstract scope while coverage was not — and never pool the two.
**Whichever ZD picks, the evidence basis is recorded per verdict**; that guardrail predates this spec.

## Defect 2 — a confirmed F4 masked by F6/F7 reads as `fired: 0`

F4 sits third in the precedence chain (`judgment_run.py:750-774`). **Verified**, one reference, claim
0 a confirmed F4, claim 1 a coverage gap:

```
claim-level derived: ['F4', None]
rec findings       : ['F6', 'F4']
rec label          : 'F6'
manifest emitted_labels: {'F6': 1}
manifest seam_status F4: {"wired": true, "fired": 0, ...}
```

The F4 is real, verified and recorded — and the run-level artifacts say zero.

**This corrects `F3F7_PACKET_AND_GATE_SPEC.md` Change 1**, written earlier the same day: its
fail-loudly rule derives the label set from `emitted_labels` / `seam_status` and is **blind to this
case by construction**. A packet builder must iterate `rec["findings"]` and
`strength_records[*].derived`.

**Required:** publish a findings-level count alongside the label-level count, and say which is which.

## Defect 3 — the token mismatch, and what a consumer must match on

`WEAKER_STRENGTH` exists **only in memory** — `_new_record` (`judgment_run.py:351-378`) has no field
for support state, so refined states never reach `judgment_predictions.jsonl`. **Verified:**
`'WEAKER_STRENGTH' in json(record) → False`.

A consumer must match on: `rec["strength_records"][i]["derived"] == "F4"`, `"F4" in rec["findings"]`,
`rec["label"] == "F4"`, `manifest["emitted_labels"]["F4"]`. Matching on `"WEAKER_STRENGTH"` finds
**zero rows in every artifact**.

Two shape traps to guard: pass-through claims produce a record with only
`{claim_index, assessed: False}` — no `derived` key at all (`:732`), so `sr["derived"]` raises
`KeyError`; and `rec["strength_records"]` is `[]` unless `discriminator_call_llm` is wired (`:653`).

**Required:** document the contract in one place, and add the fixture test from
`F3F7_PACKET_AND_GATE_SPEC.md` Change 3 item 2 keyed on `findings`, not `emitted_labels`.

## Defect 4 — `manifest["warning"]` publishes a false statement

`judgment_run.py:1599` writes: *"F4 results are reportable ONLY in formal mode (distinct verifier)."*
DEC-072 retired the distinct-verifier requirement, and the same manifest's
`f4.self_verification` block says so correctly. **Verified** on a formal, self-verified run: the
warning and the block contradict each other in one artifact.

**This is a false provenance statement in a published artifact, not a stale code comment.** Same claim
also at `judgment_run.py:32-34` and `:1094-1096`; stale comments at `f4_strength.py:151` and
`:721-722`.

**Required:** correct the warning string. Fix the comments in the same pass.

## Defect 5 — `independent_verifier: true` is forgeable

`judgment_run.py:1572-1577` computes self-verification from Python object identity plus two
caller-supplied strings. **Verified:** a one-line lambda delegating to the same function, plus a
different id string, yields `{"self_verified": false, "independent_verifier": true}` — same model,
same code path.

**Required:** either stop asserting independence, or verify something that cannot be faked by a
wrapper. Recording the two model ids as *claimed* rather than *verified* would be honest and cheap.

## Defect 6 — a reportable F4 can carry an empty `verifier_model_id`

`validate_f4_config` (`f4_strength.py:196-199`) demands `verifier_model_id` only when a verifier
**callable** is wired. Passing a formal policy with no verifier callable is accepted, and
`f4_strength.py:723` then falls back to the generator. **Verified:**

```
derived='F4' reportable=True mode='formal' gen_id='test-model' ver_id=''
manifest f4: {"reportable": true, "verifier_model_id": "", "verifier_calls": 1}
```

A verifier call happened, was hashed, is reportable, and no model id was recorded — precisely what the
guard's own error message calls *"a call nothing can reconstruct."*

## Defect 7 — default wiring routes a one-model run to development mode

`judgment_run.py:1158-1164`: formal mode is selected only if an explicit `f4_policy` is passed **or**
`f4_verifier_call_llm is not None`. Under DEC-063 (one model), an operator wiring only
`discriminator_call_llm` gets development mode and a non-reportable F4 — the state DEC-072 was written
to end. **Required:** make the default match DEC-072, or raise rather than silently degrade.

## Defect 8 — the `f4` block publishes no outcome distribution

`f7` publishes `outcome_counts` (`judgment_run.py:952-955, 981`); `f5` publishes
`disposition_counts` (`:1054`). **`f4` publishes none.** From the manifest you cannot tell how many
assessed claims returned `NOT_F4` versus were held, nor for which of the eleven reasons.

Given Defect 1, that is the single most important missing number: the `span_unverifiable` and
`no_usable_abstract` counts are what would expose the evidence-scope asymmetry as a **recall problem**
rather than a null result.

## Defect 9 — the annotation queue cannot record an F4

`judgment_band.py:105`: `LABEL_SPACE_F3 = ["F6","F3","ACCURATE"]`, with an F3-only worksheet
(`:628-635`). **Verified**: `'F4' anywhere in queue row? False`. F4-labelled pairs *are* queued, but
an annotator cannot record the label and there is no strength question.

DEC-072 makes a human-adjudicated F4 sample a precondition of any F4 precision figure. **That sample
has no landing site.** Fix the queue, or state plainly that no F4 figure is obtainable.

## Defect 10 — the orchestrator ignores `DecisionStatus`

`judgment_run.py:749` takes `decision.findings` and ignores `decision.status` and
`decision.primary_label`. **Verified**: the engine returned `HELD_UNJUDGEABLE` with
`primary_label=None`; the orchestrator published `disposition: "predicted"` with a label, while
writing the engine's hold reasons onto the same row. The record contradicts itself on its face.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| v3 run, claim established only in the body | F4 | per ZD's scope ruling; **not** a silent `span_unverifiable` |
| any run | per-verdict record | F4's evidence basis recorded, never pooled with coverage's |
| reference with a confirmed F4 and an F6 | run-level | F4 visible in a findings-level count |
| pass-through claim | `strength_records[i]` | no `derived` key; consumers use `.get` |
| formal mode, no verifier callable | config | **raises**, or `verifier_model_id` required |
| lambda wrapper + different id string | `independent_verifier` | not asserted true |
| one-model run, default wiring | mode | formal per DEC-072, or an explicit raise |
| any run | `f4` block | outcome distribution over all eleven hold reasons |
| annotation queue | label space | can record F4, or the manifest states no F4 figure is obtainable |
| engine returns `HELD_UNJUDGEABLE` | record | disposition and status do not contradict |
| any manifest | `warning` | no distinct-verifier claim |

## Guardrails — do NOT change

- **`band_prompts.py` stays byte-identical** — blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`, pinned by `test_band_prompts_blob_oid_is_unchanged`.
  The F4 prompt pair is sealed; if a fix appears to need a prompt edit, **stop and report**.
- **Evidence basis is recorded per verdict** and full-text and abstract verdicts are never pooled.
- **DEC-039 stands:** at full coverage, strength is judged before provenance. Do not reorder.
- **Precision-first.** Ambiguity escalates; it never becomes an accusation.
- **Claude never assigns semantic labels**, and no F4 precision figure may be quoted without a
  human-adjudicated sample (DEC-072).
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.
`test_f4_strength.py`, `test_adversarial_f4_strength*.py`, `test_judgment_engine.py` — 174 passed at
audit time; `test_judgment_run.py`, `test_run_provenance.py` — 106 passed.

## Definition of done

- F4's evidence basis matches the run, or is recorded per verdict and never pooled.
- Findings-level F4 count published.
- Consumer contract documented; fixture test keyed on `findings`.
- `manifest["warning"]` corrected; stale comments fixed.
- Verifier record honest.
- `f4` outcome distribution published.
- Queue can record F4, or the manifest says no F4 figure is obtainable.
- Suite green; count old → new, stating the environment.

## Out of scope

- Any corpus run.
- Editing `band_prompts.py`.
- The F3 gate itself — separate spec. Note this spec's Defect 1 is the largest upstream contributor to
  F3's empty denominator, so land them in an order ZD chooses.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

---

# Audit loop — F4, rounds 1–4 (2026-08-18) · **NOT CLEAR**

**Status: NOT CONVERGED.** 10 findings adjudicated → **3 LAND · 1 DEFER · 2 ASK-ZD · 1 REJECT**
(plus 3 superseded). Three checkers per round, each opening every cited line and re-running every
probe. LAND required unanimity at **≥95% confidence**.

> **F4 reached a clear-streak at round 4 and the verdict was withdrawn before it was published.** One
> round-3 finding had never been graded — its checker triad died on a spend limit — and F4 completed
> the count around it. Graded on backfill: **LAND at 96/96/96.** The streak resets. This is the same
> hole that invalidated F3's CLEAR; the driver rule now blocks CLEAR while any finding is
> unadjudicated.

**No further rounds will be run** (ZD, 2026-08-18).

---

## Landed findings

### L-1 · F4's citing-side span gate is anchored to the model's own output, not to the paper
**`cre/f1/f4_strength.py:638`** · REPRODUCED · unanimous LAND at **96 / 96 / 96** · **most severe F4 item**

```python
    if not citing_span or citing_span not in claim:
```

`claim` is the **atomic claim emitted by the LLM extractor** (`band_prompts.CLAIM_EXTRACT_PROMPT` via
`judgment_band.extract_atomic_claims:268-278`). So the string the accusation is anchored to and the
string it is checked against **are the same machine-produced artifact.** The cited side is anchored to
fetched text (`cited_span not in cited_abstract`, `:640`); **only the citing side validates the model
against itself.**

**This is structural, not a slip.** `refine_support_strength` takes only `(claims, support,
evidence)`, and `evidence` is built solely by `judgment_band.assemble_evidence:306-312` —
`cited_pmid`, `cited_abstract`, `cited_is_review`, `review_reflist`, `review_fulltext_available`,
plus `cited_fulltext` at `:318`. **`citing_sentence` never reaches `f4_strength.py`.** F4 has no
argument through which to consult the citing paper's words.

**Reproduced on both evidence scopes.** Hedged citing sentence, extractor drops the hedge:

```
citing_sentence : Drug X was associated with a reduction in disease Y in adults [1].
atomic_claims   : ['Drug X causes a reduction in disease Y in adults']
derived         : F4  weaker_strength  reportable=True
published span  : 'causes a reduction in disease Y'
span in CITING SENTENCE? False      span in ATOMIC CLAIM? True
'causes' in citing sentence?  False
```

Identical result on the **DEC-081 full-text path**, with the row stamped `evidence_scope:
fulltext_sections` while the citing anchor came from neither the full text nor the citing paper.

**The trigger is measured, not hypothetical.** CONTRADICTIONS 36b/36c record first-party N=5 runs:
*"most instability is claim TEXT, not granularity"*, and only **18/21** sentences reproduce their
claim set at `temperature=0`. Claim text is exactly the axis this rides on. **The bias has a
direction:** dropping a hedge yields a claim stronger than the source — the only direction that fires
F4. Adding one merely holds.

**The in-package contrast settles "omission, not design":** `f7_entity.py:174/811/845/1132` — F7, the
sibling accuser of the citing side — **hashes the citing sentence into its record and refuses a record
whose hash does not replay.**

**Nothing locks the current behaviour as intended.** `grep citing_sentence` across all three F4 test
files returns zero hits; the suite is 148 passed with the defect present, because the API has nowhere
to put a citing sentence.

**Required, and the remedy is ZD's:** binding F4 to the citing sentence is a contract change to
`refine_support_strength` and to the record shape. **The spec proposes no rule or constant.**

**Two overstatements in the auditor's argument, corrected by the checkers and not load-bearing:**
the blind queue genuinely cannot catch this (`judgment_band.py:655-666` whitelists seven keys, no
`strength_records`; `LABEL_SPACE_F3 = ['F6','F3','ACCURATE']` has no F4), **but** the durable
prediction rows carry both `citing_sentence` (`judgment_run.py:358`) and `strength_records` (`:653`),
so a DEC-072 manual sample *could* catch it — nothing in code does. And the "recall half" argument is
unsound: the generator is filled with `<<CLAIM>>` only (`:761-764`).

### L-2 · The run-level reportability gate skips its F4 clause exactly when F4 is masked
**`cre/f1/preband_contract.py:711-731`** · REPRODUCED

The gate omits its F4 clause whenever the F4 is masked by F6/F7 precedence — so the one case where a
confirmed F4 disappears from `emitted_labels` is the case the gate declines to check.

### L-3 · `seam_status["F4"]["assessed_claims"]` counts claims F4 *refused* to assess
**`cre/f1/judgment_run.py:1754-1757`** · REPRODUCED

On the DEC-081 full-text path the counter includes held claims, so the denominator of any F4 rate
overstates what was actually assessed.

---

## Blocked on ZD

### Z-1 · The accusation path applies none of the ambiguity gates the clear path applies
**`cre/f1/f4_strength.py:569-582` (clear) against `:617-646` (accusation)** · REPRODUCED

`_none_consistent` refuses to clear on `pop != "equivalent"` or any `unknown` on any ladder. The
accusation branch reads `pop` **only** on the `population_generality` branch and examines **only** the
load-bearing dimension. Measured, holding everything else fixed:

```
population_relation=equivalent    -> F4 weaker_strength reportable=True
population_relation=unknown       -> F4 weaker_strength reportable=True
population_relation=incomparable  -> F4 weaker_strength reportable=True
```

The module's own posture at `:96-99` claims *"Any unknown / conflict ... holds as UNJUDGEABLE — never
a fabricated F4."* **This is on your desk and not an implementer's because three of the project's own
seven F4 regression PMIDs are papers with no comparable population at all** — symmetrising the gates
may move them.

### Z-2 · A cited-side level of `"none"` on the load-bearing ladder
**`cre/f1/f4_strength.py:617-625`** · REPRODUCED · the model's token for *"the cited paper does not
address this dimension"* is ranked as the weakest rung rather than treated as absence.

---

## Deferred

### D-1 · Phase-2 quarantine rebuilds the record and loses the F4 strict-parse failure
**`cre/f1/judgment_run.py:1501-1507`** · REPRODUCED · `_new_record` rebuilds, so an F4 strict-parser
failure on the DEC-081 path does not survive into the artifact. **Re-raise with the L-1 contract
change**, which touches the same record shape.

## Rejected — do not re-raise

| cite | claim | why |
|---|---|---|
| `f4_strength.py:627-632` | an F4 raised on `population_generality` is certified by a verifier never asked about population | True, but subsumed by Z-1, which covers the same gate asymmetry at the branch above it. |

---

## Guardrails

- **`f4_strength.py` is NOT governed; `judgment_run.py`, `judgment_engine.py`, `parser.py`,
  `schema.py`, `coverage_aggregate.py` ARE.** L-2, L-3 and D-1 move `judgment_run.py` /
  `preband_contract.py` — **report the digest consequence, do not decide it.** CONTRADICTIONS 65 is
  open on that class.
- **`band_prompts.py` byte-identical**, blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`. L-1's
  remedy must not reach for a prompt change — that is a re-freeze and a decision.
- **DEC-081 is the governing scope decision**: PMC full text only, 539/1280 = 42.1%.
- Precision-first, both halves. No invented constants. Specs only — no corpus run.

## Definition of done

- L-1: F4 sees the citing sentence, or the record states plainly that its citing anchor is
  model-generated. **A verbatim gate against the model's own output is not a verbatim gate.**
- L-2 and L-3 fixed; the F4 denominator counts only claims actually assessed.
- Z-1 and Z-2 routed to ZD before any gate is symmetrised.
- Suite green, old → new counts, environment stated (`anthropic` and `jsonschema` change the number).
- `band_prompts.py` blob OID unchanged. State it.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
