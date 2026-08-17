# F8 — retraction: attest the check, or stop claiming the label — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## The finding: F8 does not exist in this package

An exhaustive AST scan of all 114 Python files found **exactly six syntactic occurrences** of `F8`:

| Site | What it is |
|---|---|
| `schema.py:26` | the constant's definition |
| `schema.py:27` | `TAXONOMY_LABELS` membership |
| `schema.py:229` | pass-through in `pipeline_state_to_taxonomy` |
| `preband_contract.py:47` | `FAULT_LABELS = {"F1","F2","F8"}` |
| `__init__.py:12, 48` | re-export |

**No module, no function, no test, no fixture, and no code path that assigns the string.**
`decide.py`'s full output alphabet is `{UNSCOREABLE, HUMAN_REVIEW, UNVERIFIABLE, CLEARED, F2, F1}` —
F8 is unreachable from it, and `decide` / `confirm` / `lookup` / `run` contain zero symbols matching
`retract` or `notice`.

**There is no timing gate and no constant of any kind.** The `>= 31 days` post-retraction rule exists
only in the vault. The comparison it needs has **no input**: `Reference` (`schema.py:328-335`) carries
`source_pmcid`, `source_pmid`, `source_title` and **no citing publication date**; `parser._year_from`
extracts only the *cited* work's year. The single `retraction_date` string in the repo is
`F1_BUILD_PLAN.md:70`, a JSON sketch with value `null`.

**The producer is not here.** `preband_contract.py:18, 38` name `preband_disposition.write_disposition`
as Band 1's writer; `from cre.f1 import preband_disposition` raises `ImportError`. F8, if decided at
all, is decided in a Band-1 codebase not shipped in this handoff.

Two naming tells that F8 was an afterthought: `judgment_run.py:139` documents `preband_label` as
carrying *"the F1/F2 label"*, and the mandatory upstream binding (`preband_contract.py:76, 151-155`)
is called **`f2_commit`**.

---

## Defect 1 — checked-and-clean is byte-identical to never-checked

`Disposition.provenance()` (`preband_contract.py:82-96`) records `source`, `canonical`, `schema`,
`path`, `artifact_sha256`, `manifest_sha256`, `f2_commit`, `corpus_manifest_sha256`, `row_count`,
`cleared_count`. **Verified**, two dispositions — one from a Band 1 that ran an F8 check and found
zero, one from a Band 1 where F8 was never implemented, both all-cleared:

```
provenance identical after dropping path/digests?  True
artifact bytes identical?                          True
F8-specific keys in provenance: []   per-check attestation keys: []
```

**The field that does not exist:** there is no `checks_performed`, no `f8_checked`, no
`retraction_source`, no `retraction_snapshot_date`. The only upstream binding is `f2_commit` — a
40-hex hash that records **which code ran, not which checks it ran**, and which is unresolvable to
anyone without that repo.

**Required:** a per-check attestation on the disposition — for each of F1, F2, F8: performed
yes/no, the source consulted, and the snapshot date. **This is the fix.** Everything else here is
downstream of it.

## Defect 2 — the guard built for exactly this failure skips the pre-band stage

`judgment_run.py:1750-1771` — the `seam_status` block, whose own note reads *"an unwired seam
reporting fired=0 has NOT found zero faults — it was never asked."* **Verified: it covers F3, F4, F5,
F6, F7 only. `"F8"`, `"F1"` and `"F2"` are absent.** The pre-band stage is exempt from the guard
written to stop this exact failure.

`preband_contract.reportability_report` iterates `("F3","F4","F5","F7")` (`:712`) — the string `"F8"`
does not appear in its source. **An emitted F8 needs no provenance block and blocks no reportability
clause.**

And `enforce_join_reached` (`:341-353`) deliberately does not fire on "the disposition cleared
nothing" — its docstring names an all-F1/F2/F8 corpus as legitimate. The **inverse** — an all-cleared
disposition, the exact signature of an unrun check — has no guard either.

## Defect 3 — the only F8 counter is biased against F8

`excluded_preband_by_label` (`judgment_run.py:1260, 1415-1417, 1775`) is the only per-label F8
counter. `judgment_run.py:1407-1410` applies `jb.exclusion_reason` (no citance / no cited PMID —
`judgment_band.py:147-156`) **before** the pre-band gate at `:1411`. So an F8-labelled reference
lacking a citing sentence or a claimed PMID is booked as `excluded_no_citance` /
`excluded_no_cited_pmid` and **its F8 label is never counted**.

`schema.py:68` states F1/F2/F8 are *"existence/metadata level and carry no atomic claims"* — so the
references F8 can legitimately fire on are **precisely the ones this counter drops**.

`eval_report.py` is F2-only; there is no F8 analogue and no F8 denominator anywhere.

## Defect 4 — the retraction code that exists is F5's, and its date handling is broken

The only retraction-flag lookup in the package is `f5_seams.make_check_formal_notice`
(`f5_seams.py:151-190`), one of F5's six seams. `f5_supersession.py:979-983` uses it to **refuse** to
judge a retracted work, returning `UNJUDGEABLE` with reason
`"cited_retracted_upstream_f8_inconsistency"` — its own comment calls this *"an F8 that should have
been removed upstream."* It never emits F8.

Five verified defects in that seam, all of which will matter the moment F8 is implemented on top of
it:

- **Dates are compared as raw strings** — `str(date) > str(as_of_date)` (`:184`), no parsing.
  `"2024/01/15" > "2024-06-01"` is **True** because `/` (0x2F) sorts above `-` (0x2D), so a real
  January retraction reads as **CLEAR** in June. `20240115` as an int does the same.
- **Asymmetric failure.** A non-ISO date that sorts *earlier* falls through to `NoticeStatus`, whose
  `__post_init__` (`f5_supersession.py:356-360`) **raises** — so `"15 Jan 2024"` crashes mid-run while
  `"2024/01/15"` silently clears. `f5_supersession.py:418-423` states the principle
  (*"a silently reinterpreted one is a correctness bug, not a formatting nicety"*) and the guard is
  not applied at the seam.
- **A missing `notice_date` disables the gate** (`:181-184`) — the retraction is treated as in force
  at every `as_of_date`. And **`notice_date` is populated by nothing in production**: it appears only
  at `f5_seams.py:181` and in two test fakes. `lookup.py:195-205` builds `RetrievedRecord` with
  `publication_types` and no notice date. So on any real wiring this is the default, not the edge.
- **A lookup failure is indistinguishable from a clean record** — `meta = fetch_meta(work_id) or {}`
  (`:179`); `None` and `{}` both yield `kind=none, resolution=resolved_clear`. The same file names and
  guards this exact defect class for retrieval at `:238-243`.
- **The flag conflates two publication types.** `_RETRACTION_TYPES` (`:151`) matches both
  *Retracted Publication* (the retracted article) and *Retraction of Publication* (the notice). So
  **citing a retraction notice — legitimate, and common in meta-research — reads as citing a retracted
  paper.** Source is PubMed `PT` alone (`lookup.py:203`), with no Retraction Watch or Crossref
  cross-check.

No timezone handling exists anywhere in the decision path, and no clock is read — every date is a
caller-supplied string. That is the right design; the parsing is what is missing.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| disposition from a Band 1 that ran an F8 check, zero retracted | provenance | **distinguishable** from one where F8 never ran |
| disposition with no F8 attestation | reportability | **fails** — a run cannot claim F8 coverage it cannot evidence |
| any run | `seam_status` | F1, F2, F8 entries present |
| F8-labelled reference with no citing sentence | counter | counted as F8, not only as `excluded_no_citance` |
| notice date `"2024/01/15"`, as-of `"2024-06-01"` | gate | retraction **in force** |
| notice date `"15 Jan 2024"` | run | parsed or rejected — never a mid-run crash |
| notice date absent | record | gate **not silently skipped**; the absence is named |
| `fetch_meta` returns `None` vs `{}` | record | distinguishable from a clean answer |
| cited work is a **retraction notice** | verdict | not treated as citing a retracted paper |
| any run | manifest | states F8 is not implemented in this package, until it is |

## Guardrails — do NOT change

- **Do not implement an F8 detector in this package.** F8 is decided at the pre-band stage
  (`preband_contract.py:47`, `FAULT_LABELS`), in a codebase not shipped here. This spec makes the
  *contract* honest. If ZD wants a detector, that is a separate decision and a separate repo.
- **Do not invent the 31-day constant here.** The rule is ZD's, the timing gate needs a citing
  publication date that `Reference` does not carry, and adding a threshold nobody adjudicated is how
  a number becomes unfalsifiable.
- **Precision-first.** A missing attestation holds; it never becomes an accusation, and it never
  becomes a silent clear.
- **Never use the detector's own flags as gold.**
- **Claude never assigns semantic labels.**
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92` at `biblio_match.py:120`.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.
The two retraction-adjacent tests pass today and must stay green.

## Definition of done

- Per-check attestation on the disposition contract, with a schema bump if required.
- Reportability fails when an attestation is absent.
- `seam_status` covers F1, F2, F8.
- The F8 counter no longer loses rows to the exclusion ordering.
- The five date defects in `f5_seams.make_check_formal_notice` fixed or, where they are policy,
  reported to ZD.
- The manifest states plainly that F8 is not implemented in this package.
- Suite green; count old → new, stating the environment.

## Out of scope

- Writing an F8 detector.
- The 31-day timing gate.
- Adding a citing publication date to `Reference` — that is a schema change with corpus-wide
  consequences; cost it and report.
- Any corpus run.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
