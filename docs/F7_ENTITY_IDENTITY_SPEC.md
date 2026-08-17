# F7 — entity identity, the authority lock, and the dropped label — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

**Context that frames every item below:** there is **no production `f7_evidence_builder` anywhere in
the repo**. `EvidenceContext` is constructed only in `test_f7_entity.py` and
`test_f7_orchestrator_wiring.py`. **F7 is test-only wiring today.** Fix the defects, but do not let
anyone read an F7 number as a measurement until a real builder exists.

---

## Defect 1 — the legacy path silently drops a confirmed F7

With `f7_seams` and `f7_evidence_builder` both wired but `discriminator_call_llm=None`,
`judge_pair_finish` returns at `judgment_run.py:744` — **before** the F7 branch at `:750`.
**Verified:**

```
findings: ['F7'] | label: None | disp: held_full_coverage_pending_F3_F5_F7
emitted_labels: {} | seam_status.F7: {wired: true, fired: 0}
```

A confirmed wrong-entity finding sits in `rec["findings"]`, and the instrumentation built to prevent
exactly this reports the exact lie it exists to prevent. **The disposition string literally claims F7
is *pending*.**

Not covered by any test — every case in `test_f7_orchestrator_wiring.py` sets
`discriminator_call_llm` (`kw.setdefault` at `:94`).

**Required:** F7 does not depend on the discriminator seam; the early return must not swallow it.
**Note this is the same early return that hides an F5 finding** — coordinate with the F5 spec; one fix
may serve both.

## Defect 2 — the default authority table makes F7 structurally unreachable, silently

`f7_entity.py:232`: `authorities_json: str = "{}"`. `_parse_authorities` (`:251-303`) rejects a blank
string but **accepts `"{}"`** — the loop at `:267` never runs and `{}` is returned. Then
`f7_entity.py:1017-1020` returns `UNJUDGEABLE` / `authority_not_locked` whenever either lock is
`None`. **Verified:**

```
default authorities_json = '{}'   _parse_authorities(default) = {}
state: UNJUDGEABLE  reason: authority_not_locked
LLM calls burned before the hold: 3   (tuples, attribution, evidence)
```

So a run can wire both seams, report `wired: true, fired: 0`, and have had F7 unreachable for every
claim. Nothing raises, nothing warns. `preband_contract.py:711-731` skips the F7 clause entirely when
`fired == 0`, so the run passes `assert_reportable_run`.

**Verified**, two 20-record runs — one structurally unreachable, one an honest all-`SAME_ENTITY` zero:

```
                        unreachable            honest zero
outcome_counts     {'UNJUDGEABLE': 20}    {'SAME_ENTITY': 20}
authorities_sha256  44136fa3...aff8a       3bc9860e...f888
```

`44136fa3...` is exactly `sha256("{}")` — **an unlabelled digest that nothing compares against**, and
an auditor must already know that constant to recognise it. `reportable: False` fires in **both**,
because `digest_present` (`judgment_run.py:951`) is False whenever nothing reached the relation stage.

**Required:** an up-front configuration check, mirroring the full-text XOR guard at
`judgment_run.py:1178-1182`, plus an explicit `authorities_locked_types` field in the `f7` block.
Also move the lock check **before** `_assess_tuple`'s three generator calls (`:1019`) — today the run
pays full model cost for a verdict that was structurally impossible.

Same shape for cross-type: `_CROSS_RELATIONS` (`:84-87`) omits `provably_distinct`, and
`policy.cross_ontology_lock` defaults `""` (`:233`), so **cross-type F7 is a second structurally
unreachable default**.

## Defect 3 — F7 will propose correcting KRAS to KRAS

The only identity guard is `f7_entity.py:1063` — a raw `.strip()` comparison of normalized ids.
**`canonical_label` is available on both sides and never compared**; it is used only for prompt text
(`:1088, 1092`) and for `proposed_corrected_label` (`:1117`). The engine's guard
(`judgment_engine.py:112`) is the same `.strip()` compare. **Verified:**

```
claimed id='HGNC:6407-OLD' label='KRAS' | evidence id='HGNC:6407' label='KRAS'
  --> DIFFERENT_ENTITY_SUPPORTED, proposed_corrected_label='KRAS'
claimed id='hgnc:6407'     label='KRAS' | evidence id='HGNC:6407' label='KRAS'
  --> DIFFERENT_ENTITY_SUPPORTED, proposed_corrected_label='KRAS'
```

A stale alias table or a case difference produces a wrong-entity **accusation** on the same gene, and
the packet proposes correcting a label to itself.

Preconditions are all reachable: `_confident_id` (`:583-599`) accepts `mapping_status: "exact"` at
face value with no cross-check. And **four of the five assessment stages run on the *generator*
callable** — the relation comparison at `:1069` passes `call_llm=self.call_llm`, so only the final
boolean is independent.

**Required:** hold (or raise) when
`claimed_norm["canonical_label"].strip().casefold() == evidence_norm["canonical_label"].strip().casefold()`,
and casefold the id comparison at `:1063`. Cheap, and it closes the whole class.

## Defect 4 — evidence outside four section labels vanishes without a record

`_SECTION_LABELS = {"results","methods","table","figure"}` (`f7_entity.py:94`). Three outcomes for a
claim whose evidence is in discussion or intro, **all verified**:

1. **Builder passes it** → `SectionText.__post_init__` (`:135-140`) raises → caught at
   `judgment_run.py:1501` → the pair becomes `DISP_QUARANTINE_PARSE`. **F7's section policy is
   recorded as a parse error.**
2. **Builder drops it** (the realistic case) → nothing records the drop. The evidence prompt
   (`:684-716`) has **no "not found" escape**; schema C (`:384-388`) requires spans
   unconditionally, and a digest for a dropped section raises (`:992-993`).
3. **Model picks an admitted-but-wrong-kind section** → correctly recorded as
   `entity_section_not_results_or_methods` / `no_valid_relation_span` (`:1005-1008`).

So cases 1 and 2 are uninstrumented: **a paper whose entity is named only in the Discussion is
indistinguishable from a paper with no evidence at all.** `_evidence_context_sha256` (`:804-824`)
hashes only what *was* supplied.

**Related:** the context gate at `f7_entity.py:889` is a **tautology** — `__post_init__` already
forbids anything outside `_SECTION_LABELS`, so it reduces to `if not ctx.body_sections`. It reads as a
section-kind filter and is not one. Same defect class as the tautological queue audit.

## Defect 5 — the manifest publishes states, not reasons

`_f7_manifest_block` (`:924-988`) publishes `records_emitted`, `outcome_counts` (keyed on the three
`EntityState` values), `relation_comparisons_attempted`, prompt digest and `reportable`. The
**17 distinct deterministic hold reasons** (`:99-117`) are never aggregated. They survive on disk in
`rec["f7_records"]` (`judgment_run.py:709`) — so the information exists and is simply absent from the
artifact anyone reads.

**F7 is nonetheless the best-instrumented discriminator in the package** — F3's block publishes zero
counters. Its gap is granularity; F3's is existence. Do not regress it while fixing it.

## Defect 6 — the `f7` block contradicts `seam_status`

`judgment_run.py:957` hardcodes `"wired": True`, and the block is emitted whenever `f7_seams is not
None` **alone** (`:1679-1680`; `_module_hashes` has the same one-sided condition at `:883`).
**Verified**: `_f7_manifest_block(None, [])` returns `{'wired': True, 'records_emitted': 0,
'outcome_counts': {}}`. Supplying `f7_seams` without the builder yields `f7.wired = true` beside
`seam_status.F7.wired = false` in one manifest.

Also worth stating in the block: `reportable: False` does **not** imply anything was wrong — an honest
all-`SAME_ENTITY` run gets it too.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| both F7 seams wired, `discriminator_call_llm=None`, confirmed mismatch | label | **F7**, not dropped |
| default `authorities_json="{}"` | run | **raises** at configuration, or `f7` block says unreachable |
| same | model calls | zero — the lock check precedes `_assess_tuple` |
| any run | `f7` block | `authorities_locked_types` present |
| same `canonical_label`, different id (stale alias) | verdict | **not** `DIFFERENT_ENTITY_SUPPORTED` |
| same id, different case | verdict | **not** `DIFFERENT_ENTITY_SUPPORTED` |
| evidence only in `discussion` | record | a named reason, not a quarantine and not silence |
| `f7_seams` without `f7_evidence_builder` | manifest | `f7.wired` agrees with `seam_status.F7.wired` |
| any run | `f7` block | hold-reason histogram over the 17 reasons |
| any run | manifest | states F7 has no production evidence builder, until it has |

## Guardrails — do NOT change

- **F7 is deliberately NOT gated on support state** (`judgment_run.py:702-709` sits outside the
  `all_supported` guard; `EntityAssessorRun.__call__` accepts `support` and never reads it). Verified
  behaviour: with `established: False`, F7 still fires and wins precedence. **Keep that.**
- **F7 leads the precedence chain** (`judgment_engine.py:491-493`, `:509`). Do not reorder.
- **`band_prompts.py` stays byte-identical** — blob OID
  `fa01126e2b9482d450065fd70cd0eb1fea816f5c`. The F7 prompt quartet is sealed; if a fix appears to
  need a prompt edit, **stop and report**.
- **Precision-first.** Ambiguity escalates; it never becomes an accusation. Defect 3 is that guardrail
  applied literally.
- **Claude never assigns semantic labels.**
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.
`test_f7_entity.py`, `test_f7_orchestrator_wiring.py`, `test_adversarial_f7_entity*.py` —
**148 passed, 11 xfailed** at audit time.

## Definition of done

- Legacy-path drop closed, with a test that omits `discriminator_call_llm`.
- Empty-authorities run refuses or declares itself unreachable; lock check precedes the model calls.
- `canonical_label` cross-check and case-insensitive id compare in place.
- Section exclusion instrumented.
- Hold-reason histogram published.
- `f7.wired` and `seam_status.F7.wired` cannot disagree.
- Manifest states F7 has no production evidence builder.
- `band_prompts.py` blob OID verified unchanged and stated.
- Suite green; count old → new, stating the environment.

## Out of scope

- **Writing a production `f7_evidence_builder`.** Cost it and report; ZD decides.
- Any corpus run.
- Extending `_ENTITY_TYPES` beyond `{drug, gene, variant, disease}`.
- Editing `band_prompts.py`.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
