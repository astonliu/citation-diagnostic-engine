# CRE taxonomy audit loop — state register

**Purpose.** Per stratum: round number, clear-streak, status. The loop's stopping condition
(`CRE_AUDIT_LOOP_SESSION_BRIEF.md` §8) is read off this table, not off any agent's summary.

**Stopping condition.** A stratum is `CLEAR` after **three consecutive rounds** in which the auditor
surfaces **zero findings the Checkers accept as genuine**. A round of only REJECTs counts as clear. A
round producing a DEFER counts as clear. **One LAND resets the streak to zero.** A stratum declared
`SATURATED` by the Checkers (§7.2 diminishing-returns test) ends immediately regardless of streak.

**"Genuine" is the Checkers' verdict, never the auditor's.**

---

## Status table

| stratum | spec file | rounds run | clear streak | status |
|---|---|---|---|---|
| **F1** | `F1_FABRICATION_GUARD_SPEC.md` | **1** | **0** (reset by 4 LANDs) | **OPEN — round 2 warranted** |
| F2 | `F2_IDENTITY_EVIDENCE_SPEC.md` | 1 in flight | — | IN PROGRESS |
| F3 | `F3_REACHABILITY_SPEC.md` | 1 in flight | — | IN PROGRESS |
| F4 | `F4_SCOPE_AND_VISIBILITY_SPEC.md` | 1 in flight | — | IN PROGRESS |
| F5 | `F5_HONESTY_SPEC.md` | 1 in flight | — | IN PROGRESS |
| F6 | `F6_SUPPRESSION_FIX_SPEC.md` | 1 in flight | — | IN PROGRESS |
| F7 | `F7_ENTITY_IDENTITY_SPEC.md` | 1 in flight | — | IN PROGRESS |
| F8 | `F8_ATTESTATION_SPEC.md` | 1 in flight | — | IN PROGRESS |

---

## Round log

_Appended, never rewritten. One block per stratum-round._

### F1 · round 1 · 2026-08-17

**Agents:** 3 finders over disjoint surfaces (transport+decision · search+orchestration ·
instrumentation+artifacts), then 3 checkers (Reality · Blast radius · Cost). Unanimous LAND required.

**Result: 12 candidates → 4 LAND · 4 DEFER · 2 ASK-ZD · 2 REJECT.**

| | finding | cite |
|---|---|---|
| LAND | L-1 non-MEDLINE 200 recorded as `answered_absent` | `cre/f1/lookup.py:103-110` |
| LAND | L-6 unreadable provider record scores `0.0` | `cre/f1/confirm.py:114-115` |
| LAND | L-7 quarantine swallows the fail-fast auth error | `cre/f1/run.py:151-166` |
| LAND | L-3 missing transport status counted as "answered" | `cre/f1/eval_report.py:127-132` |
| LAND | L-0 Defect 4b route resolved by measurement (ZD, 2026-08-17) | `cre/f1/confirm.py:52` |
| DEFER | D-2 F2 v3 record drops `transport_status` | `cre/f1/eval_report.py:303-319` |
| DEFER | D-4 `fetch_answered()` fails OPEN | `cre/f1/schema.py:66-77` |
| DEFER | D-9 no `f1_status` state for "confirm never ran" (twin of L-3) | `cre/f1/eval_report.py:133-138` |
| DEFER | D-12 quarantine count computed and discarded | `cre/f1/run.py:142` |
| ASK-ZD | Z-5 unbounded `Retry-After` | `cre/f1/ratelimit.py:90-95` |
| ASK-ZD | Z-11 adapter receipt records the declaration, not the invocation | `cre/f1/recording_adapter.py:90-95` |
| REJECT | ×2 duplicates — see `AUDIT_LOOP_REJECTIONS.md` R-005, R-006 | |

**Citation integrity: clean.** All three checkers recorded `citation_verified: true` on all twelve
candidates. No auditor carries a bad-citation signal out of this round.

**Not saturated.** Three of the four landed findings are severity-CRITICAL false-accusation routes;
none is instrumentation-only or cosmetic, so the §7.2 diminishing-returns test is not met.

**Round 2 targets, carried forward:** every other site where the transport vocabulary is minted or
read (L-1/L-3/L-6 are one defect at three layers — look for a fourth); `preband_contract` join
accounting, which no finder reached in depth; `biblio_match`'s two `RetrievedRecord` construction
sites (`:655`, `:680`), confirmed to exist but not traced.
