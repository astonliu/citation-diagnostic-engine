# Proposals pending ZD approval — semantic_validator_v1 build (2026-07-24)

> **Queued, awaiting ZD's exact text (2026-07-25 — do not draft):** an
> amendment to F3-F7_EVALUATION_DECISIONS_2026-07-03.md correcting (a) the
> kappa computation basis (independent pre-adjudication labels, not the
> adjudications) and (b) the "sensitivity on known positives" framing (a
> diagnostic on the confirmed set, not a lower bound on population recall).
> ZD supplies the wording; the edit changes the 263c6e2e... pin and requires
> re-pinning in the spec's Normative references in the same commit.

Per the build spec's residuals #3 and #5: the implementer PROPOSES, ZD
approves. Nothing below is decided; the current implementation makes the
pending state explicit (fail-closed or interim, named in every violation
message) and is a one-line change to flip once ZD rules.

## 1. Residual #3 — SV-033 `coverage_targets` needs per-item strata

**Problem the residual identified:** `coverage_targets` is unevaluable — the
schema gave no way to know which target an item counts toward, so SV-033's
"every coverage_targets entry met" clause had no trusted input.

**Proposal (implemented as a proposal):**
- Schema delta (applied in the residuals commit): selection items carry an
  OPTIONAL `stratum` field (nonempty string; must name a `coverage_targets`
  key — enforced semantically, since the key set is instance-specific).
- SV-033, when `coverage_targets` is non-empty:
  - all items carry `stratum` → enforce `stratum ∈ targets` and per-target
    counts (`count(items with stratum=k) >= targets[k]`);
  - any item lacks `stratum` → FAIL CLOSED with a violation naming this
    residual ("unevaluable pending ZD decision").
- ZD's alternative per the residual: demote `coverage_targets` to
  informational. If chosen, SV-033 drops the coverage clause (sort,
  uniqueness, min_size stay) and `stratum` stays optional provenance.

**Decision needed with:** the selection-rule input (supplied alongside the
canonical six inputs — see the spec's Inputs section).

## 2. Residual #5 — SV-024 pinned response schemas

**Problem:** SV-024's "pinned response schema" had no pinned input.

**Proposal (implemented as an interim):**
- Schema delta (applied): each stage config carries REQUIRED-NULLABLE
  `response_schema_sha256`. `null` = response schemas not yet supplied.
- While null, SV-024 validates SHAPE PRESENCE ONLY (an `ok` call must carry
  a non-null `parsed`) and SAYS SO in its violation message, plus the
  mechanical cardinality/claim-index correspondence checks which need no
  schema.
- Once ZD supplies/approves the two response-schema files (with input #1),
  they are committed, their SHA-256s go into CONFIG, and SV-024 validates
  `parsed` against them (already implemented: supply them via
  `artifacts["response_schemas"]`).

**Decision needed with:** the model-snapshot input (ZD input #1).

## Operational setup required (residual #9 — documented, not a predicate)

SV-042 validates ancestry continuity between successive OBSERVED
canonical-ref states; force-push and branch-deletion protection is hosting
policy and cannot be a validator predicate. Before the first release
validation, ZD must enable on `github.com/astonliu/citation-repair-engine`
for the canonical ref (`refs/heads/main`, per `bootstrap.TRUSTED_CANONICAL_REF`):
branch protection with **force pushes disallowed** and **deletion disallowed**
(GitHub: Settings → Branches → Branch protection rules, or a push ruleset
with "Block force pushes" + "Restrict deletions"). This is the fast-forward-
only / non-deletable requirement of the freeze spec's Threat model.

## 3. Note — SV-043 known-prohibited set is vacuous until ZD input #5

`bootstrap.KNOWN_PROHIBITED_CITING_PMCIDS` is empty: the two prohibited F3
cases (Seeman/DNA-nanotech, idelalisib) are known by PMID; their citing
PMCIDs must be SUPPLIED, not guessed (freeze spec, Inputs #5 of the
canonical six; the lockfile is #6). While empty,
SV-043's superset check is vacuous; uniqueness-by-`citing_pmcid` and the
selection/candidate exclusion checks bind regardless. Supplying the two
PMCIDs is a one-line bootstrap change on the canonical ref.
