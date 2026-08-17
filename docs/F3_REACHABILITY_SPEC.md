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
