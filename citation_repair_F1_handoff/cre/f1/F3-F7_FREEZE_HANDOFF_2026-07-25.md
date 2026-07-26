# F3–F7 finder freeze — handoff / state brief (2026-07-25)

Written for a cold session. This is the **freeze / semantic-validator workstream**, not F2. Separate branch, separate tree, separate test count. Do not merge the two mentally: F2 lives on `feat/f2-final-revision` in the `~/cre-f2` worktree at 409 tests; this lives on `feat/f3-f7-semantic-validator-v1` in the main clone at 908.

## What this workstream is

A conformance and integrity layer for the F3–F7 finder pipeline. It freezes prompts, schemas and evaluation decisions behind content hashes, then enforces 35 semantic rules (`SV-001` … `SV-110`, each with a distinct `E_*` fail code) over 18 root artifact types — `prompt_package`, `config`, `batch`, `run_state_manifest`, `promotion`, `revocation`, `selection_artifact`, `candidate_manifest`, `exclusion_ledger_entry`, `exclusion_checkpoint`, `review_record`, `annotation_release_manifest`, `release_attestation`, `module_manifest`, `wal_event`, `stimulus_object`, `candidate_protocol`, `exposure_plan`. The threat model it exists for is stale checkouts, drifted copies, force-pushed history and fail-open validators — i.e. the ways a frozen artifact silently stops being frozen.

## Current state — VERIFIED this session

**Branch** `feat/f3-f7-semantic-validator-v1` @ **`ee27139`**, working tree clean. Main clone `/Users/kamachi/citation-repair-engine`.

```
5cc2bf1  chore(F5 tools): vendor the conformance-review reconciler   <- base
7a59b8a  feat: normative inputs + review-round residual schema deltas
7171e74  feat: strict_loader + canon_v1 + bootstrap + schema_gate + semantic_validator_v1
ee27139  fix: adversarial-audit round — close fail-open paths, tighten SV predicates, bootstrap hardening
```

`git diff --stat 5cc2bf1..HEAD` = **15 files, 9,391 insertions, 0 deletions**, entirely within `cre/f1/freeze/`, `cre/f1/fixtures/freeze/` and the new test file. `parser.py`, `biblio_match.py`, `lookup.py`, `band_prompts.py`, `PREREGISTRATION.md` and `TAXONOMY_DECISION_RULES.md` untouched — confirmed by diffstat.

**Tests:** 908 passed (759 baseline unmodified + 149 new). F2 recall guard green before and after (22 passed, all seven banding PMIDs still band).

**All three pins re-verified independently this session:**

| artifact | bytes | SHA-256 |
|---|---|---|
| `F3-F7_FINDER_FREEZE_SCHEMAS.json` | 69,138 | `3241cbcce6189cf19f278b452b01ed41fb46ec079a8f2588fd110e50409b53b1` |
| `F3-F7_EVALUATION_DECISIONS_2026-07-03.md` | 8,938 | `263c6e2eb31da104c7d53d379ede919e1f270ba8d4af35dc77cb9c15971c1927` |
| `F3-F7_SCHEMA_CONFORMANCE_REPORT.txt` (no trailing newline) | 6,259 | `f4333a27ead9da5fa3a7b181f34d7cb6f8d0c37544d4b4c6b2ae77890e62e7e7` |

The report file's raw hash equals its no-trailing-newline hash, confirming the file genuinely has no terminal newline. Report format **v14**, generated `2026-07-25T21:40:53Z`, python-jsonschema 4.26.0 Draft202012, interpreter 3.14.6. `duplicate_keys: NONE`, `meta_schema_draft202012: PASS`, internal refs 307 occurrences / 50 unique targets / **dangling NONE**, all 18 roots PASS positive, `all_rules_have_both_fixtures_and_pass: True`.

## 🔴 The one urgent item

**`feat/f3-f7-semantic-validator-v1` is not on origin.** Remote branches are `origin/main`, `origin/feat/f2-final-revision`, `origin/feat/f3-f7-typed-judgment-v2` — this branch has no counterpart. Three commits and 9,391 lines of freeze infrastructure exist single-copy on local disk. That is exactly the durability failure this subsystem was built to prevent. **Push before anything else.**

## Blocked on ZD — four decisions

Source: `cre/f1/freeze/PROPOSALS_PENDING_ZD.md`. Per the build spec's residuals, the implementer proposes and ZD approves; nothing below is decided, and each is a one-line flip.

1. **Residual #3 — SV-033 `coverage_targets`.** Schema now carries an OPTIONAL per-item `stratum` naming a `coverage_targets` key. When `coverage_targets` is non-empty: all items carry `stratum` → enforce membership and per-target counts; any item lacks it → **FAIL CLOSED**, violation names the residual. ZD's alternative: demote `coverage_targets` to informational, drop the coverage clause, keep sort/uniqueness/min_size. **Decide with ZD input #2 (selection rule).**
2. **Residual #5 — SV-024 pinned response schemas.** Each stage config carries REQUIRED-NULLABLE `response_schema_sha256`, currently `null`. While null, SV-024 validates **shape presence only** (an `ok` call must carry non-null `parsed`) and says so in the violation, plus the cardinality / claim-index checks that need no schema. Once ZD supplies the two response-schema files they're committed, hashes go into CONFIG, and full validation is already implemented via `artifacts["response_schemas"]`. **Decide with ZD input #1 (model snapshot).**
3. **Residual #9 — branch protection**, documented not predicated. SV-042 validates ancestry continuity between OBSERVED canonical-ref states; force-push and deletion protection is hosting policy. Before first release validation, enable on `github.com/astonliu/citation-repair-engine` for `refs/heads/main` (per `bootstrap.TRUSTED_CANONICAL_REF`): **force pushes disallowed** and **deletion disallowed**.
4. **Input #6 — the two prohibited citing PMCIDs.** `bootstrap.KNOWN_PROHIBITED_CITING_PMCIDS` is empty. The two prohibited F3 cases (Seeman/DNA-nanotech, idelalisib) are known by PMID; their **citing PMCIDs must be supplied, not guessed**. While empty, SV-043's known-prohibited superset check is **vacuous** — uniqueness-by-`citing_pmcid` and the selection/candidate exclusion checks still bind. One-line bootstrap change.

## Closed decisions — do not relitigate

- **SV-002 frozen constants** were verified against the live repo before being frozen into the validator: `CLAIM_EXTRACT_PROMPT` → `25f7de62…`, `COVERAGE_PROMPT` → `1a24d13b…`, blob OID `fa01126e…` at HEAD.
- **Retry after a persisted HTTP response is legal.** Otherwise `retryable_status` is meaningless. Rationale lives in SV-022's code comment, not a changelog.
- **SV-101's `retry_after_cap` "participation" clause has no artifact-level predicate** — it is runner behaviour. Stated in the rule comment and the report NOTES.
- **Zero rules skipped; none proved unimplementable.** SV-110's evidence is bootstrap subprocess fixtures (fresh `-I` child, byte-mismatch abort-before-import, role-gap abort, pre-imported-module fail-closed, stray-drifted-copy refusal) plus artifact-side manifest checks.

## Open risk — the audit was single-reviewer

The adversarial pass ran a 7-lens workflow producing **28 raw findings**, but **its verify fan-out hit the session usage limit**, so the implementer adjudicated each finding against the v17 text alone. ~17 were real and are fixed in `ee27139`, including **three genuine fail-open paths**: the SV-034 canon-error skip, an unhandled `CanonV1Error` escaping `validate()`, and a dead-symlink check in bootstrap. Also fixed: missing SV-026 branches, SV-025/SV-030 binding gaps, SV-022 boundary semantics being simultaneously too strict (pre-send-crash retry) and too weak (boundary history ignored), and a bootstrap gap for stale same-named modules from other trees — the exact stale-checkout class this gate exists for.

**This is a single-reviewer pass on fixes to fail-open paths.** It does not invalidate the work, but it is not the two-reviewer result 28 findings implies. **Record it in the report's NOTES** so the next reviewer knows the fan-out did not complete, and consider re-running the fan-out on a fresh session budget before release validation.

## Key paths

All under `/Users/kamachi/citation-repair-engine/citation_repair_F1_handoff/cre/f1/freeze/`:

| file | bytes | what |
|---|---|---|
| `semantic_validator_v1.py` | 95,682 | the 35 SV rules |
| `F3-F7_FINDER_PROMPT_FREEZE_SPEC.md` | 92,287 | the normative spec (§12 = the rule list) |
| `F3-F7_FINDER_FREEZE_SCHEMAS.json` | 69,138 | pinned schemas, 18 roots |
| `fixtures_v1.py` | 40,143 | positive/negative fixtures |
| `gen_conformance.py` | 26,470 | regenerates the report |
| `bootstrap.py` | 14,262 | `TRUSTED_CANONICAL_REF`, `KNOWN_PROHIBITED_CITING_PMCIDS` |
| `F3-F7_EVALUATION_DECISIONS_2026-07-03.md` | 8,938 | committed byte-identical to its pin |
| `F3-F7_SCHEMA_CONFORMANCE_REPORT.txt` | 6,259 | generated; hash recorded in `ee27139` |
| `canon_v1.py` | 3,871 | canonicalisation |
| `PROPOSALS_PENDING_ZD.md` | 3,589 | **the four blocking decisions** |
| `strict_loader.py` | 3,373 | fail-closed module loading |
| `schema_gate.py` | 2,730 | schema entry point |

Tests: `cre/f1/test_semantic_validator_v1.py` (1,253 lines). Fixtures: `cre/f1/fixtures/freeze/{candidate_universe,release_universe}.json`. Vendored reconciler: `citation_repair_F1_handoff/tools/f5_conformance_review.workflow.js`.

**Sibling workstream, do not confuse:** F2 state is `/Users/kamachi/cre-work/F2_HANDOFF_2026-07-25.md`; F2 spec is `/Users/kamachi/cre-work/F2_DISPLACEMENT_SPEC_2026-07-24.md`.

## Next actions

1. **Push `feat/f3-f7-semantic-validator-v1` to origin.** Nothing else until this is done.
2. Answer the four `PROPOSALS_PENDING_ZD.md` items. #3 and #4 (branch protection, the two PMCIDs) need no other input and can land today.
3. Append the incomplete-fan-out note to the report NOTES and regenerate; the report SHA will change, so record the new one in the commit message as before.
4. Re-run the adversarial verify fan-out on a fresh budget before release validation.
5. Regenerate and re-verify:
   ```
   cd citation_repair_F1_handoff/cre/f1/freeze && python gen_conformance.py
   printf '%s' "$(cat F3-F7_SCHEMA_CONFORMANCE_REPORT.txt)" | sha256sum
   cd ../../.. && PYTHONPATH=. python3 -m pytest cre/f1 -q --ignore=cre/f1/.venv
   PYTHONPATH=. python3 -m pytest cre/f1/test_f2_recall_guard.py -q --ignore=cre/f1/.venv
   ```

## Constraint reminders

Propose, don't decide — the four pending items are ZD's. Never overwrite registered or committed content without explicit instruction; targeted amendments only. Interpretation calls go **in-code with rationale**, not in a changelog. Fail closed, never fail open — that is the whole point of this subsystem, and three fail-open paths already slipped through one review round. Tri-state discipline: `is False` / `is True`, never falsy.
