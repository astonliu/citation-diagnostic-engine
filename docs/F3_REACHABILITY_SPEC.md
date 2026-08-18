# F3 — reachability and instrumentation — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
Extends DEC-079. **Supersedes** `F3F7_PACKET_AND_GATE_SPEC.md` Change 2, which was written before the
audit and named an incomplete gate.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## Objective

F3 fired zero times on `PMC13294812` and DEC-079 recorded that it was never assessed. The audit
establishes something stronger: **under the production configuration F3 is unreachable for any input,
any model output, and any document** — and the manifest asserts it was wired.

Nothing here changes the F3 gate. It makes reachability true and observable.

---

## Finding — F3 is blocked three times, and only one gate is documented

**Gate A, support state.** `judgment_run.py:655-661`: `all_supported` must hold, else `provenance`
stays `None`. Enforced independently and hard by the engine — `judgment_engine.py:453-466` raises
`DiscriminatorContractError` if a non-`None` provenance arrives without full support.

**Gate B, discriminator wiring.** `judgment_run.py:662-674`. With `discriminator_call_llm is None`,
provenance is a flat `UNJUDGEABLE` with no model call. **This is the only gate the manifest names**
(`judgment_run.py:1751-1753`).

**Gate C, the trace seams — undocumented, and each alone is sufficient to block.**
`f3_provenance.py:438-448`:

```
438  if "cited_reflist" not in policy.trace_sources:   return None
440  if policy.max_hop_count < 1:                      return None
442  if not (cited_pmcid and str(cited_pmcid).strip()): return None
446  candidates = _assemble_candidates(fetch_reflist(str(cited_pmcid).strip()))
447  if not candidates:                                 return None
```

- `f3_resolve_pmcid=None` ⇒ `cited_pmcid=None` (`judgment_run.py:663-664`) ⇒ blocked at `:442-445`.
  **`cited_pmcid` has exactly this one source.**
- `f3_fetch_reflist=None` ⇒ the stub `lambda _p: ([], False)` (`judgment_run.py:667`) ⇒ `[]` ⇒ blocked
  at `:447-448`.

`MISATTRIBUTED_CONFIRMED` has exactly one construction site (`f3_provenance.py:466-478`), downstream
of both.

**Verified by execution** with the most F3-favourable possible model stub
(`restatement → select → confirmed`):

| wiring | result |
|---|---|
| both seams unwired (production) | `UNJUDGEABLE` |
| reflist wired, `f3_resolve_pmcid=None` | `UNJUDGEABLE` |
| pmcid resolved, `f3_fetch_reflist=None` | `UNJUDGEABLE` |
| **both wired** | `MISATTRIBUTED_CONFIRMED`, chain `('PMID:111','PMID:999')` |

The suite corroborates: the only test where F3 fires supplies **both** seams
(`test_judgment_run.py:674-675`).

---

## Change 1 — wire the trace seams, or make the run refuse to claim F3

Two acceptable outcomes. **ZD picks; you implement.**

(a) **Wire them.** `f3_resolve_pmcid` and `f3_fetch_reflist` get real implementations and are passed
by the production launcher. `ncbi_meta.ncbi_pmc_reflist` (`ncbi_meta.py:235`) already exists.

(b) **Refuse.** If either seam is absent, the run raises at configuration time rather than producing
records that silently cannot contain F3 — mirroring the full-text XOR guard at
`judgment_run.py:1178-1182`, which is the precedent in this file.

**Do not change the support-state gate (Gate A) either way.** Making F3 fire by loosening its gate
manufactures a rate. DEC-079 stands.

**Note the ordering trap:** `production_launcher.launch` forwards `**run_kwargs` blind
(`production_launcher.py:625`) and **nothing in the repo defaults these seams**. Whichever option ZD
picks must be enforced somewhere the launcher cannot bypass.

## Change 2 — `seam_status.F3` must name every gate

`judgment_run.py:1751-1753` publishes `"gate": "discriminator_call_llm"`. That is **incomplete to the
point of being false**: with the discriminator wired it emits `wired: true, fired: 0`, and the
block's own note (`:1766-1770`) instructs the reader to read that as "asked, found zero".

**Required:** `wired` is true only when **every** gate F3 depends on is satisfied — discriminator,
`f3_resolve_pmcid`, `f3_fetch_reflist`, and a `trace_sources` / `max_hop_count` policy that admits the
path. Record each seam separately. Note `params.fetch_reflist_wired` (`judgment_run.py:1726`) refers
to the **evidence-level review reflist** (`judgment_band.py:313-316`), a different seam that a reader
can easily mistake for this one — disambiguate the names.

## Change 3 — counters, and not-reached vs assessed-negative

`_f3_manifest_block` (`judgment_run.py:897-921`) carries policy and `policy_sha256` and **no counters
at all**. Compare: `f4` publishes `eligible_claims` (`:1688-1690`), `f7` publishes
`relation_comparisons_attempted` / `records_emitted` / `outcome_counts` (`:979-981`), `f5` publishes
`disposition_counts` / `records_emitted` (`:1054-1055`).

**Required:**

- per document, a count of references that **reached** the provenance gate;
- **F3 not reached** distinguishable from **F3 assessed and negative**, per reference and in
  aggregate;
- when zero references reach it, the run says so in its own output.

`marker_scope.py:637-646` already coined this vocabulary — `not_asked` versus
`claims_assessed_negative`, with a manifest note that one must never be read as the other, and it
explicitly names DEC-079's F3 gate as the same defect class. **Reuse it; do not invent a second
vocabulary.**

Today the only recovery is per-record — `rec["provenance"] is None` (`judgment_run.py:374`) versus a
state dict (`:675-680`) — and `manifest["counts"]`'s `held_pending_F5_F7` +
`held_provenance_unjudgeable` is a **lower bound only**, because a fully-supported pair that also
draws F7 or F5 gets that label instead while still having had provenance assessed.

## Change 4 — five causes, one rationale string

`f3_provenance.py:535` appends the same string — `"restatement not confirmed within hop budget"` —
for **all five** distinct outcomes: PMCID unresolved (`:445`), no reflist candidates (`:448`), V3
selected nothing (`:454`), primary abstract unfetchable (`:459`), V4 said no (`:462`). **Verified**:
the first three produce byte-identical rationales.

"The seam was absent" and "we traced it and the primary did not contain the finding" must not be the
same string. **Required:** a distinct machine-readable reason per cause.

## Change 5 — `PROPER_ORIGIN` without a trace

When the gate opens and the seams are absent, `rec["provenance"]` **is** written
(`judgment_run.py:675-680`). If V2 returns all `originates` / `not_origin_sensitive`, the state is
`PROPER_ORIGIN` (`f3_provenance.py:545-548`) → disposition `held_pending_F5_F7`.

**That branch never consults the reflist.** So a run whose provenance-trace seams were never wired can
publish "provenance is fine". Internally consistent; externally misleading. **Required:** record the
evidence basis on the verdict, so a `PROPER_ORIGIN` reached without a trace is distinguishable from
one reached with it.

## Change 6 — the reportability gate for F3 is vacuous

`preband_contract.py:712-727` checks the f3 block **only if F3 labels were emitted** (`:713-714`).
Since F3 cannot fire, the check can never run. **This is the fourth instance of the project's
recurring defect class** — the tautological queue audit, the `no_llm` branch, the F3 gate, and now the
gate's own guard. Make the check unconditional on the *configuration*, not on the outcome.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| both trace seams wired, model says restatement→select→confirmed | verdict | `MISATTRIBUTED_CONFIRMED` |
| `f3_resolve_pmcid=None` | run | per ZD: seam wired, **or** configuration raises |
| `f3_fetch_reflist=None` | run | same |
| any run | `seam_status.F3.wired` | true only when **all** gates are satisfied |
| any run | `seam_status.F3.gate` | names every gate, not just the discriminator |
| document where no reference reaches provenance | manifest | **not reached**, distinct from assessed-negative, with counts |
| document where some reference reaches it | manifest | count of references reaching the gate |
| each of the five block causes | rationale | five distinct machine-readable reasons |
| `PROPER_ORIGIN` reached with seams absent | record | evidence basis says no trace was performed |
| F3 configured but unreachable | reportability | fails, without requiring an F3 label to have fired |

## Guardrails — do NOT change

- **Do not change the F3 support-state gate.** Instrument and wire; never loosen. Changing it
  manufactures a rate.
- **`band_prompts.py` stays byte-identical** — blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`, now pinned by
  `test_band_prompts_blob_oid_is_unchanged`. Verify and report.
- **The F3 prompt trio is sealed.** Wiring seams must not edit prompts. If it appears to require one,
  **stop and report**.
- **Claude never assigns semantic labels.** F3 is provenance-only per DEC-017 — right claims, wrong
  source, at FULL coverage. Any code or comment implying "zero claims supported → F3" is stale
  (`TAXONOMY_DECISION_RULES.md` is VOID, DEC-080).
- **Precision-first.** Ambiguity escalates; it never becomes an accusation.
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as before.
**Plus the two confirmed F3 cases — compare on banding, not evidence-record shape.** They are the only
positive evidence the project has for this label; if wiring the seams changes their banding, stop.

## Definition of done

- F3 reachable under a stated configuration, **or** the run refuses to run without it.
- `seam_status.F3` names every gate and cannot report `wired: true` while blocked.
- Counters present; not-reached distinguishable from assessed-negative.
- Five distinct block reasons.
- Reportability check no longer conditional on an F3 having fired.
- `band_prompts.py` blob OID verified unchanged and stated.
- Suite green; count old → new, stating the environment.

## Out of scope

- **Changing the support-state gate.**
- Any corpus run.
- F4's abstract-scope defect, which is the largest *upstream* contributor to F3's empty denominator —
  separate spec, and it must land for an F3 rate to mean anything.
- The reporting unit.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

---

# Audit loop — F3, rounds 1–3 (2026-08-17) · **NOT CLEAR — CLEAR WITHDRAWN 2026-08-18**

> **⚠ F3 WAS DECLARED CLEAR ON 2026-08-17 AND THAT VERDICT IS WITHDRAWN.**
>
> Three round-2 findings had never been graded — their checker triad died on a session limit — and F3
> completed its clear-streak around them. They were graded on 2026-08-18: **1 LAND, 1 DEFER, 1 REJECT.**
> **A LAND resets the streak. F3 did not converge.**
>
> The driver rule has been corrected so this cannot recur: a stratum can no longer reach CLEAR while
> any finding is unadjudicated (`INCOMPLETE-UNADJUDICATED-FINDINGS`). F3 is the only stratum this
> affected — F7 and F8 had every round fully graded.

**Status: NOT CONVERGED.** 9 findings adjudicated across rounds 1–3 →
**1 LAND · 5 DEFER · 1 ASK-ZD · 2 REJECT**. Three checkers per round, each opening every cited line
and re-running every probe. Bar for LAND: unanimous, all three ≥95% confidence.

**No further rounds will be run** (ZD, 2026-08-18). This spec is the record as it stands.

## ⚠ READ FIRST — two things qualify this CLEAR

**1. F3 cannot fire in the production configuration.** That is why nothing landed. Every item below is
**latent** — it becomes live the moment the F3 gate opens. Fix them as part of opening it, not before.
**Do not report an F3 rate.**

**2. The three round-2 findings are now graded** (2026-08-18 backfill). One landed — see L-1 below.
The other two are recorded under Deferred and Rejected.

---

## Landed findings

### L-1 · The F3 candidate collector turns an NCBI outage into "not a review", silently
**`cre/f1/f3_candidate_collect.py:318-330` and `:342-349`, with `cre/f1/ncbi_meta.py:103-121`, `:123-128`, `:212-232`** · REPRODUCED · unanimous LAND at **96 / 96 / 97**

**This is the one item in F3 that is not behind the unopened gate.** `f3_candidate_collect.py` is a
standalone program with its own CLI (`:395-451`, `main` at `:424`), it does not touch `judgment_run`
or `judgment_engine`, and **it fires on its own default configuration** (`p.set_defaults(require_review_oa=True)` at `:420`).

**Mechanism.** `ncbi_meta.is_review` is tri-state — `None` means "could not ask". The collector tests
it with bare truthiness (`if review:` at `:324`, `if rec["cited_is_review"] and rec["cited_pmcid"]` at
`:344`), so **`None` and `False` take the same branch** and both increment the single
`counts["filtered_out"]` at `:349`. The same collapse happens at `:329` on `cited_pmcid`, where
`ncbi_meta.py:229-232` returns `""` on `ResolverError` — against its own contract at `:224-225`, which
names this caller and tells it to use the batch helper precisely because the single helper
*"CANNOT distinguish an outage from an absence."*

**Reproduction** (Reality checker's own run of the real `collect()`):

```
A EFetch ANSWERED review    : cited_is_review=1 review_has_pmcid=1 emitted=1 filtered_out=0  rows=1
B EFetch ANSWERED not-review: cited_is_review=0 review_has_pmcid=0 emitted=0 filtered_out=1  rows=0
C EFetch FAILED -> None     : cited_is_review=0 review_has_pmcid=0 emitted=0 filtered_out=1  rows=0
B counts == C counts ? True
```

Written manifests for B and C differ **only in the `out_dir` string.**

**Why it matters.** This collector is **the project's only source of F3 calibration examples**, and
F3 starts from zero confirmed instances. A network outage during collection is indistinguishable from
a corpus that genuinely contains no reviews — and the bias runs one way, toward *"F3 does not occur"*,
which is the reading DEC-079 exists to forbid.

**It also publishes a false claim in a shipped artifact.** The module's own invariant at
**`f3_candidate_collect.py:29-31`** — *"NO SUPPRESSION BY HIDDEN FUNNEL. Every stage that drops a
candidate is counted in the manifest, so the funnel is auditable"* — is untrue: a stage drops
candidates and no counter names the drop. **Cite `:29-31`, not `:24-26`** — the auditor's anchor was
off by five lines and all three checkers caught it.

**Required.** Test the tri-state with `is True` / `is None`, not truthiness, and give "could not ask"
its own counter in the manifest. **The fix is provably behaviour-neutral** — the Blast-radius checker
executed all three values and confirmed `if review:` and `if review is True:` are equivalent for every
reachable value, so the emitted candidate stream is byte-identical. It is purely additive
instrumentation.

**Cost: none.** Neither `f3_candidate_collect.py` nor `ncbi_meta.py` is in `GOVERNING_MODULES`, so no
governed digest moves and CONTRADICTIONS 65 is not deepened. The collector has **zero production
importers** (`f3_phase1_frame.py`'s import at `:68-74` is entirely commented out). No constant is
proposed.

**Precedent it fails to honour:** `ncbi_meta.py:15-30` records ZD's own ruling of 2026-08-11 —
*"An outage must never again be readable as an absence of full text."* It was applied to
`ncbi_pmids_to_pmcids` and never carried to this caller.

**Test gap, confirmed:** `test_f3_candidate_collect.py`'s `patched_ncbi` fixture always returns a
list; there is no `None` stub anywhere in the file, and no `test_ncbi_meta.py` exists.

---

## Deferred — implement when the F3 gate opens

### D-1 · Evidence spans are published without checking they appear in the source
**`cre/f1/f3_provenance.py:460-472`** · REPRODUCED

A confirmed F3 publishes both of its evidence spans with no verification that either occurs in the
text it cites. **Required:** verify before publishing, or mark the span unverified in the record.

### D-2 · The origin chain names a PMID the model never saw
**`cre/f1/f3_provenance.py:452-471`** · REPRODUCED

V3 selects by title; the origin chain then publishes a PMID the code never verified and the model was
never shown. **Required:** the chain must name only identifiers the trace actually resolved.

### D-3 · The band feeds a PMID to a PMCID-keyed seam
**`cre/f1/judgment_band.py:313-315`** · REPRODUCED

Wrong identifier type at a seam boundary — the annotator's F3-V3 rightful-primary candidate path.
**Required:** convert or refuse; do not pass a PMID where a PMCID is the key.

### D-4 · The collector's funnel manifest is overwritten by each resume
**`cre/f1/f3_candidate_collect.py:354-392`** · REPRODUCED

Each resume overwrites the manifest and it never counts the candidates a prior pass saw. **Required:**
accumulate across resumes, or state in the artifact that the counts describe the last pass only.

---

## Blocked on ZD

### Z-1 · The blind annotation queue offers every scoreable record the F3 slice's three-label space
**`cre/f1/judgment_band.py:103-105`** · REPRODUCED

Every scoreable record is offered the F3 slice's label space, including records the F3 gate never
considered. **The question is what the annotator is being asked**, which is a taxonomy and instrument
decision, not an implementation detail. Bears on the replacement annotator codebook, which does not
exist since `TAXONOMY_DECISION_RULES.md` was voided by DEC-080.

---

### D-5 · Seven F3 trace outcomes collapse into one recorded rationale
**`cre/f1/f3_provenance.py:535`** · REPRODUCED · DEFER (88 / 95 / 92)

Seven distinct outcomes — including *"the trace never ran"* and *"the trace ran and the primary
refuted the finding"* — are written as one string, `"restatement not confirmed within hop budget"`,
which is **affirmatively false** for several of them. Deferred because it has never executed in any
real run and unlocks no work an adopted artifact does not already order. **Re-raise when the F3 gate
opens** — the string occurs exactly once in the package, so the fix is contained.

---

## Rejected — do not re-raise

| cite | claim | why |
|---|---|---|
| `preband_contract.py:721-727` | F3's only governance gate cannot fail — both conjuncts are implied by an F3 label having been emitted | **Mechanically true and independently confirmed by all three checkers**, but not reachable in the production configuration, and already ordered at the same lines by this spec's own Change section. The Reality checker tried to construct an input that should fail the gate and could not. |
| `judgment_run.py:660-672` | the F3 V2 discriminator spends one LLM call per claim on every fully-supported reference | Real cost observation, but not a correctness defect, and it sits on a path the production configuration does not reach. |

---

## Guardrails

- **`TAXONOMY_DECISION_RULES.md` is VOID in its entirety (DEC-080).** Its *"zero atomic claims
  supported → F3"* rule is the **inverse** of DEC-017, which defines F3 as **provenance only, at FULL
  coverage**. The file is still in the repo. **If any code path implements the void rule, that is a
  finding, not a design choice.**
- **`F3F7_PACKET_AND_GATE_SPEC.md` Change 1 is KNOWN-WRONG** where it derives the label set from
  `emitted_labels` / `seam_status` — three of the five `fired: 0` mechanisms are invisible to that
  rule. The builder must iterate `rec["findings"]` and `strength_records[*].derived`.
- **`judgment_run.py`, `judgment_band.py`, `parser.py`, `schema.py` are GOVERNED.** Four digests have
  already moved. CONTRADICTIONS 65 is open on this class — **report, do not decide.**
- **`band_prompts.py` byte-identical**, blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
- Precision-first, both halves. No invented constants. Specs only — no corpus run.

## Definition of done

- The three unadjudicated round-2 findings graded by a full checker triad **before any F3 work lands**.
- Each deferred item carried into the gate-opening spec.
- **`seam_status` no longer reports a wired F3 that cannot execute** — or the gate is opened.
- No code path implements the voided zero-support rule; prove it with a grep and a cite.
- Suite green, old → new counts, environment stated.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
