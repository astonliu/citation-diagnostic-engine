# F1 Defect 8 — no F1-producing module is byte-governed

**Date:** 2026-08-16 · **Reported to:** ZD · **Decision required.**
**Spec:** `F1_FABRICATION_GUARD_SPEC.md`, Defect 8 — *"Report this to ZD with your fix; do not extend
the list yourself — what is governed is a freeze decision."*

Reported, not acted on. `GOVERNING_MODULES` is **unchanged**.

---

## The gap

`production_launcher.GOVERNING_MODULES` (`production_launcher.py:65-71`) hashes 13 modules and
compares each against its committed bytes at HEAD before a production launch is permitted:

```
judgment_run.py   judgment_band.py   judgment_engine.py   band_prompts.py
parser.py         schema.py          f3_provenance.py     f4_strength.py
f7_entity.py      preband_contract.py  parser_versions.py
coverage_prompts_v3.py                coverage_aggregate.py
```

Every module on that list serves the F3–F7 band. **Every module that can emit an F1 or F2 label is
absent:**

| module | role in the F1/F2 verdict | governed |
|---|---|---|
| `decide.py` | assigns the F1 / F2 label and its confidence | **no** |
| `lookup.py` | claimed-PMID fetch; sets `pmid_resolved` | **no** |
| `confirm.py` | the three-database existence search | **no** |
| `run.py` | orchestration; per-reference quarantine | **no** |
| `llm_filter.py` | the survivor filter F1 is conditioned on | **no** |
| `unscoreable.py` | routes references out of the F2 numerator | **no** |
| `biblio_match.py` | `SAME_WORK_TITLE_SIM_MIN`, the F2 gate | **no** |
| `schema.py` | record shapes | yes |

A production run can therefore be launched with locally modified `decide.py` bytes and the launcher
will report clean. The label whose false positive is a **public accusation that a real paper does not
exist** is produced entirely outside the integrity boundary.

Note the loop `continue`s on a missing file (`production_launcher.py:127-128`), so a name that is
absent from the package is silently skipped rather than refused — worth considering if the list is
extended, since a typo would read as "governed" and hash nothing.

## What this pass changed inside the boundary

**`schema.py` is governed and this pass modified it** — the `FETCH_*` transport vocabulary,
`RetrievedRecord.transport_status`, `StageLog.pmid_transport_status`, and the
`pmid_transport_status` key on the prediction evidence. Its SHA-256 changes. That is expected and
correct, but it means **any frozen digest recorded for `schema.py` before 2026-08-16 no longer
matches**, and a launch pinned to an older manifest will refuse until the digest is re-recorded.

The other six modified modules (`decide.py`, `lookup.py`, `confirm.py`, `run.py`, `eval_report.py`,
plus the two test files) are outside the boundary and change no digest at all — which is precisely
the gap being reported.

## The decision for ZD

Whether to extend `GOVERNING_MODULES` to cover the F1/F2 production path, and if so which of the
seven modules above. This is a freeze decision and was left untouched.

If the answer is yes, the natural set is `decide.py`, `lookup.py`, `confirm.py`, `llm_filter.py`,
`unscoreable.py`, `biblio_match.py` and `run.py` — that closes the path from claimed reference to
emitted label. `eval_report.py` is a reporting layer and arguably belongs too, since `f1_status` is
now the artifact that distinguishes "zero fabrications" from "the check never ran".
