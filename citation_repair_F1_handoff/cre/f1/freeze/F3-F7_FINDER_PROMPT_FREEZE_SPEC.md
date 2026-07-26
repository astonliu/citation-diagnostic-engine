# F3–F7 — finder freeze: config / batch / promotion lifecycle — implementation spec (v17, self-contained)

> **v17 changelog (unrestricted Codex review, adjudicated by Claude, 2026-07-24).** ZD granted
> Codex an unrestricted pass (anything challengeable, findings labeled DEFECT vs DISAGREEMENT).
> Codex returned 13 defects + 5 disagreements; adjudication: **12 defects valid and fixed here**,
> 1 (D12, the "sensitivity as lower bound" wording) valid but located in ZD's pinned decisions
> doc — routed to ZD as a one-line correction alongside the κ fix, since that doc is not editable
> from this spec. Disagreements routed to ZD undecided. The four blockers were all
> promised-but-unimplemented validator obligations:
> **D1** SV-002 never recomputed the template digest from `template_utf8`, so a drifted template
> carrying the pinned digest passed — the freeze's core guarantee. SV-002 now recomputes
> template AND render-contract digests (recompute, never trust).
> **D2** No SV rule implemented the selection-artifact invariants or candidate==selection
> binding both the schema and §9 promised. Added SV-033 (selection: sort/uniqueness/min_size/
> coverage) and SV-034 (candidate manifest exactly equals the committed selection's ordered id
> set; prohibited-case check applies to both).
> **D3** `host_allowlisted` was a self-assertion with no allowlist anywhere. Added SV-091: host
> ∈ out-of-band pinned allowlist constant in the bootstrap; the flag is recomputed, never
> trusted.
> **D4** Four SV predicates had no trusted input: SV-031's precision-undefined clause moved to
> the annotation-freeze spec; SV-044's UI-gate clause replaced by the checkable commit-ancestry
> proxy (exposure_plan commit strictly precedes PROMOTION commit) with the UI gate named a
> runner enforcement point; SV-050 reworded as auditing the derivation function (values are
> computed, never persisted — as §10 already said); SV-110 reclassified as a RUNTIME GATE
> evidenced by bootstrap subprocess fixtures.
> Majors: **D6** `observed_runtime_sha256` recompute added to SV-001; **D7** retry-policy
> cross-field equations added as SV-101; **D8** §8b now names the real promotion field
> (`post_exposure_exclusion_checkpoint_sha256`); **D11** §1 corrected — CONFIG binds
> `render_contract_sha256` transitively via `package_sha256`, and SV-002 recomputes it;
> **D13** CONFIG package-version references now `minimum 1`. **D5** (build-spec overclaim),
> **D9** (build spec citing two revisions), **D10** (stale pins in the old handoff and Codex
> instructions) fixed in those files: build spec re-aimed at v17 + honest objective; the two
> session documents carry SUPERSEDED headers.
> Schema identity **v13 → v14** (package-version tightening is breaking). Re-pinned
> `b42fae7463387a3d2ba8d625cc2513af4c882824129f41cc350599955d15960d` / 67,897 bytes; conformance
> 18/18 positives, **29/29 negatives**. The SV-table defects (D1–D4, D6, D7) were exactly the
> class the validator build was already scheduled to shake out; they are now specified precisely
> enough to build against. Loop status unchanged: CLOSED — next step is the build.

> **Changelog governance.** Changelog blocks older than the current revision are audit trail:
> where an older changelog conflicts with the current normative sections, the schema, or the §12
> SV table, the current text governs. Conflicting statements in old changelogs carry an inline
> *[SUPERSEDED …]* annotation.
>
> **v16 changelog (Codex fix-verification round, adjudicated by Claude, 2026-07-24).** Codex
> verified its 8 prior findings against v15/v12: **7 FIXED confirmed, 1 NOT-FIXED (Q2.2)** — the
> v10 changelog line still asserted the normalization-contract hash is part of the
> occurrence-identity contract, contradicting settled SV-061, and it sits above the historical
> marker. Fixed two ways: the line now carries an inline *[SUPERSEDED]* annotation, and the
> changelog-governance rule above makes old changelogs non-normative categorically. Two new
> admissible findings, both verified by execution and fixed:
> **B-Q1.1 (MAJOR):** slot `encoding` accepted any nonempty string (`"utf-16"` validated) despite
> the vocabulary rule "every stored string declares strict-UTF-8" — now `const "utf-8"` in all
> three slot definitions (negative case added: utf-16 rejects).
> **B-Q3.1 (MINOR):** the acceptance-matrix PROMOTION row overstated `payload_sha256` coverage
> ("covers it" for any altered field) — envelope-level fields (`signed_tag_oid`) are outside the
> hash by design and are anchored by the introducing commit; row now says exactly that,
> consistent with SV-001.
> Schema identity bumped **v12 → v13** (the encoding tightening is breaking). Re-pinned
> `9880d1f3cb9fe2553c848172fb94c8d774aad09f56866d8f3b96e109fecf6c8f` / 67,869 bytes. Conformance:
> 18/18 positives, **28/28 negatives**. The review loop remains CLOSED per the pre-agreed rule —
> these fixes do not buy another round; next step is the `semantic_validator_v1` build.
>
> **v15 changelog (final bounded Codex pass, adjudicated by Claude, 2026-07-24) — LOOP CLOSED.**
> ZD authorized exactly one final Codex round, bounded to three questions with concrete-instance
> admissibility rules. Codex returned 8 findings; Claude re-checked every one by execution;
> **all 8 were valid.** (Two Claude passes had missed all of them — recorded plainly.) Fixes:
> **Q1.1 (MAJOR):** `run_state_manifest` lacked the execution-mode matrix that BATCH has — a
> release-mode run-state with `calibration` purpose and null promotion validated. Matrix added
> (candidate→calibration+null promo; release→formal+promo; development run-state stays permitted
> since a development run has state but never a BATCH). **Q1.2 (MAJOR):** `http_status` was
> unbounded in the `provider_error`-family, `model_mismatch`, and `empty` review-call branches
> (600 validated) despite the "bounded 100–599" constraint. Bounded in all branches.
> **Q1.3 (MINOR):** frozen template text versions weren't schema-enforced — `claim_extract` now
> `const 3`, `coverage` `const 2`. **Q2.1 (BLOCKER):** SV-001 gave the wrong PROMOTION/REVOCATION
> preimage (envelope-minus-field vs the envelope rule's payload-only hash). SV-001 now states the
> per-artifact declared preimage: envelopes hash `payload` only; all other self-hashed artifacts
> hash `obj ∖ field`. **Q2.2 (BLOCKER):** SV-061's preimage used different member names than the
> schema (`occ_id_v1`/`key_type` vs `occurrence_identity_version`/`normalized_ref_key_type`) —
> under `canon_v1` different keys are a different hash. SV-061 now matches the schema verbatim,
> and the `normalization_contract_sha256` question is settled explicitly: it is NOT in the
> preimage (stored alongside for recomputability; the contract is versioned via
> `occurrence_identity_version`). **Q3.1 (MAJOR):** §8 still said timeout "distinguishes
> connect/read/total/ambiguous-post-send" — stale v8-era prose contradicting the schema and
> matrix (terminal timeout = `connect` only). Fixed. **Q3.2 (MAJOR):** §6 claimed
> `response_persisted` preserves a transport-exception class its closed schema branch forbids —
> that field lives on `transport_failed`/`indeterminate`. Fixed. **Q3.3 (MAJOR):** §9 keyed
> selection uniqueness on `source_occurrence_fingerprint` (snapshot-dependent) while the schema
> and SV-031 key on `occurrence_identity`. §9 now matches the schema/SV-031.
> Schema identity bumped **v11 → v12** (the run-state matrix, http_status bounds, and text-version
> pins are tightenings = breaking). Re-pinned
> `415b331934531e9f43b2f7a032bab4153985808d460c64c0d32f97d009eee43e` / 67,920 bytes. Conformance
> suite extended with a regression case per finding: 18/18 positives (+ release BATCH and
> release run-state), **27/27 negatives reject**. Per the pre-agreed adjudication rule, surviving
> findings were fixed and **the review loop is now permanently closed** — next step is the
> `semantic_validator_v1` build, where SV-001/SV-061 get executable fixtures.
>
> **v14 changelog (Claude second pass, 2026-07-24).** Re-review of my own v13. Found and fixed:
> (1) **Dead `$defs` encoding the prohibited pattern** — `temperature_param` and `top_p_param`
> (plus unused `int_param`) survived in the schema as unreferenced definitions that *permit*
> `state:"supplied"` with decimal values, exactly what the live `no_decimal_param` forbids. An
> implementer wiring the wrong def would silently legalize JSON floats in the hashed request.
> Removed; the conformance suite now has a dead-def audit (every `$defs` member must be
> referenced). (2) **Definition of done still required "the DOM test"** — annotation-UI scope,
> removed with the v11/v12 annotation cut. Removed. (3) **Inputs item 6 still demanded exact
> `citation_id`s + source-occurrence fingerprints** for the two prohibited F3 cases, contradicting
> the v13 schema (required key = `citing_pmcid` only, which is also all ZD can supply). Fixed.
> (4) **Conformance suite had no case for the locked-slot acceptance claims** (extras/dupes/
> reorder rejected) — added: extraction-with-extra-slot and coverage-slots-reordered both reject;
> also added empty-`ref_id` `citation_id` rejection. Suite is now 18 positives (+release batch) /
> 21 negatives, all green. Schema stays format **v11** (removing unreferenced defs changes no
> instance's validity — patch, not format change; matches the v8-patch precedent), re-pinned
> `f95ccc17f3f5b53cd4b04df0c361867edaf0b7d25a752b293ded8a4b0c85f6de` / 66,618 bytes.
> Checked and left alone: `stage_base_props` (referenced via sub-pointer `$ref`s — legal and
> resolvable, verified 0 dangling), the SV-table row ordering (IDs are stable and referenced;
> cosmetic), the "dataset manifest" name in the Vocabulary artifact list (historical name for
> `annotation_release_manifest`; harmless).
>
> **v13 changelog (Claude review — Codex loop TERMINATED by ZD, 2026-07-24).** The mediation loop
> is over; this revision is a single adversarial Claude pass over the v12 artifacts, every claim
> re-checked by execution. Defects found in v12 and fixed here:
> (1) **v12's own changelog lied about the schema, again** — it claimed
> `candidate_protocol.prohibited_cases` was `minItems 2`; the file said `minItems 0`. This is the
> third straight revision where prose described a fix absent from the artifact. Fixed
> (`minItems 2`, negative case added to the conformance suite) and `required` relaxed to
> `citing_pmcid` only — the durable contamination key, and the only field ZD can actually supply
> today (the two cases are known by PMID; `citation_id`/`occurrence_identity` are optional
> provenance).
> (2) **Internal contradiction on `label_record`:** the scope-cut paragraph and Out-of-scope
> section said it "stays as the interface stub" while the v12 changelog said it was removed from
> the schema (it was). Text now matches the artifact: removed.
> (3) **Stale acceptance row contradicting a closed decision:** one matrix row still credited
> `occurrence_identity` with blocking excluded-case reintroduction; the closed decision is that
> exclusion is citing-paper-wide on `citing_pmcid` and `occurrence_identity` is provenance only.
> Row fixed; the matching stale `$comment` in `selection_artifact` fixed.
> (4) **`release_attestation` `$comment` said "descendant"** where SV-041 and §9 require
> immediate-parent-equality. Aligned.
> (5) **Stale exposure_plan `$comment`** still referenced the removed `predeclared_exposure_shas`.
> Fixed.
> (6) **`canonical_ref_commit` typed as generic `git_oid`** in `exclusion_checkpoint` and
> `release_attestation` while BATCH used `git_commit_oid`. Now `git_commit_oid` everywhere a
> commit is meant.
> (7) **`model_snapshot` pattern `.*-[0-9]{8}$` baked in one provider's dated-snapshot naming** and
> would reject valid pinned ids (e.g. `claude-opus-5`). Loosened syntactically; pinning is
> enforced semantically (must equal the ZD-supplied snapshot string; provider-returned id must
> match at run time).
> (8) **Guardrail contradicted the project's locked annotation model:** the "two annotators + κ"
> guardrail conflicts with the current locked memory state (solo annotation; reliability via
> Gwet's AC1 / blind test-retest / advisor spot-checks) — and the annotation subsystem was already
> cut from this spec's scope in v11. The guardrail now defers entirely to the separate
> annotation-freeze spec, where ZD must resolve the methodology-doc conflict (Decision 1 of the
> 2026-07-03 doc vs. the locked solo model) before annotator instructions.
> (9) **Stale "19 artifacts"** in the Status section (18 after the `label_record` removal). Fixed.
> Schema identity bumped **v10 → v11** (the `prohibited_cases` tightening is breaking; breaking
> changes get a new identity). Schema re-pinned `3cc1a338a7533bf0a270da90376828372bdf3a0fee133ec42477b6a8c370c0f3`
> / 67,978 bytes; conformance re-run by execution: 18/18 root artifacts validate, 17/17 negative
> cases reject (suite extended: candidate+promotion, development-BATCH, 1-prohibited-case,
> omitted+text system message, supplied decimal temperature, call_made+not_run, excluded-with-
> stimulus). Note: the byte-exact v10 schema (`3475a7bd…`) was lost in a paste transfer between
> sessions; the v11 bytes derive from a structurally verified reconstruction (306 `$ref`
> occurrences / 50 unique targets / 18 roots / 0 duplicate keys — all equal to the machine
> report of the original) plus the fixes above. Nothing had consumed the old pin (no CONFIG,
> no commit), so re-pinning is clean.
>
> **v12 changelog (Codex v11 review) — finishing what v11 only announced.** Codex was right that
> v11 half-did things. Now actually done and execution-verified: (1) schema identity **bumped to
> v10** (`$id` + all `schema_version`) — a breaking change gets a new identity. (2) PROMOTION now
> **references** `candidate_protocol_sha256` + `exposure_plan_sha256` + `post_exposure_exclusion_checkpoint_sha256`;
> the opaque `predeclared_exposure_shas` array and the duplicated freeze-criterion string are
> **removed** (verified: old shape rejected). (3) The **annotation scope cut is real** — `label_record`
> removed from the schema (root + `$def`); SV-080, the label/DOM/κ acceptance rows, and precision-
> readiness language removed from this spec (below). (4) The **execution-mode matrix is enforced in
> the schema** (candidate→calibration+null promotion; release→formal+promotion; development→no
> BATCH) — verified with 5 negative cases. (5) `candidate_protocol.prohibited_cases` now `minItems 2`
> and keyed on `citing_pmcid` (the durable key). (6) WAL edges: `response_persisted` provider
> metadata is **required-nullable** (no byte-distinct equivalents); terminal `timeout` is **`connect`
> only** (read/total → `indeterminate`); `ok`/error `http_status` bounded 100–599. (7) The
> occurrence-identity **contradiction is purged** from architecture/SV/acceptance, not just the one
> comment. Schema re-pinned `3475a7bd…` / 66,941 bytes. Still flagged, not fixed by me (ZD's docs):
> the methodology κ line (pre-adjudication) + superseded pair identity — a required pre-annotation
> edit, out of this spec's scope.
>
> **v11 changelog (Codex v10 review).** Owning it plainly: my v10 changelog claimed three schema
> fixes I had not actually made in the file — the WAL "closed `oneOf`", `response_persisted`
> nullable metadata, and the worksheet-choice rules were described in prose but absent from the
> schema (and SV-023 was therefore unsatisfiable). Same failure mode as an earlier "verified"
> overstatement. This round every fix was applied **and checked by executing the validator with
> positive and negative instances** (results in the Refs line). Real fixes now in the file: WAL
> is a genuine closed `oneOf` (5 event branches, each `additionalProperties:false`), `prepared`
> carries the `idempotency_preimage` (SV-023 now satisfiable), `response_persisted` metadata is
> nullable, `timeout` no longer carries the ambiguous post-send phase (→ `indeterminate`),
> worksheet choice/free-text constraints enforced. Added the two pre-commit artifacts Codex v9 #2
> wanted — `candidate_protocol` (freeze criterion + prohibited set + schema/validator contract,
> bound by CONFIG *before* the run) and `exposure_plan` (exact exposed review records, committed
> after finalization but before UI reveal). Fixed the occurrence-identity contradiction (#4): it
> is **not** claimed stable across resolution — a key change yields a new identity, which is fine
> because exclusion is citing-paper-wide on `citing_pmcid` and never depended on it. Schema
> re-pinned `3475a7bd…` / 67,502 bytes.
>
> **Scope cut (Codex v10 #7/#15, accepted).** The annotation / adjudication / evaluation-result
> subsystem (κ computation, label-ledger concurrency, assignment/overlap artifacts, the precision
> result artifact) is **removed from this spec and moved to a separate annotation-freeze spec** —
> it was never in the original task (freeze the finder front-end) and is a distinct system. This
> spec no longer claims annotation readiness; it certifies the finder that *produces* the
> annotation queue. `label_record` was removed from the schema entirely in v12 (the earlier
> "interface stub" language was stale); its shape and ledger rules are that separate spec's job.
> This is the honest boundary and it resolves a whole cluster of
> Codex's findings by scoping, not by building a second system inside this one.
>
> **v10 changelog (Codex v9 review).** Fixed a self-contradiction I'm not proud of: the v9
> schema had **two duplicate `$comment` keys**, which its own strict-loader rule ("reject
> duplicate keys") must reject — so it was invalid under its own contract. Merged them and this
> round added a **byte-level duplicate-key check** to validation (0 dups now). Also: bumped `$id`
> + every `schema_version` to **`v9`** (v9 is breaking vs v8 — it gets its own identity);
> PROMOTION now **pins post-exposure checkpoint N+1** + the predeclared exposure set; WAL got a
> terminal **`transport_error`** review-call state and `timeout` no longer carries
> `ambiguous_post_send` (that maps to `indeterminate`); `response_persisted` no longer forces
> provider metadata a 4xx body lacks; `runtime_observation` is a shared `$ref` (embedded in
> BATCH *and* run-state, not duplicated); the undefined **sidecar was removed** (the
> review-record chain is the recovery structure); `occurrence_identity` got a **versioned
> contract** (`occ_id_v1`, key-type + normalization-contract hash + alias rule) *[SUPERSEDED by
> SV-061: the normalization-contract hash is stored alongside the item but is deliberately NOT in
> the `occurrence_identity` preimage; the contract is versioned via `occurrence_identity_version`]*. Schema re-pinned
> `3475a7bd…` / 60,059 bytes. **κ flag (Codex v9, important):** the methodology doc's "κ on the
> adjudications" is a genuine methods error — κ must be the two *independent, pre-adjudication*
> annotations; flagged for ZD to fix in the methodology doc before annotator instructions.
> Pushback held: semantic-validator *fixtures* are a build artifact (the spec gives each rule an
> ID/equation/failure-code, §12), not pre-enumerated prose.
>
> **v9 changelog (Codex v8 review).** The submitted v8 schema had **16 dangling `$ref`s**
> (`stage_base_props/properties/…` with no `properties` member) — the config path never
> resolved, so the schema was **not executable** and my "verified" claim was overstated. Fixed
> and this time validated end-to-end: **0 dangling refs; every artifact type instantiates and
> validates.** Also fixed real bugs: decimal params (`temperature`/`top_p`) can't be *supplied*
> (a JSON float would break exact-request hashing) → must be omitted/provider_default; the
> exclusion ledger's **self-exclusion chronology** (candidate run consults checkpoint N,
> exposure → N+1, PROMOTION pins N+1); the annotation manifest is now **bound to its producing
> formal run** (source run/dump/selection hashes + per-row `review_record_sha256`); BATCH &
> run-state bind **execution_mode + promotion/checkpoint/commit at start** (resume can't switch
> mode or dataset); review records carry a `seq`/`prev`/`record_sha256` **chain**; WAL gains
> `transport_failed` + global & per-attempt sequencing + a **send-boundary** retry rule;
> `idempotency` has a **literal preimage object**; the stimulus **embeds codebook content** and
> a closed worksheet schema; `label_record` identity includes `annotator_id` + an adjudication
> kind; a stdlib-only **fresh-interpreter bootstrap** is added to the trust boundary. Schema
> re-pinned `64673f33…` (`schema_version` const stays `v8` — patch, not format change). Stale
> acceptance rows corrected. Pushback held: prereg amendment stays a publication dependency;
> runtime import sandboxing stays out.
>
> **v8 changelog (Codex v7 review).** Fixed real blockers: the `review_call`/`coverage`
> intersection was unsatisfiable → now a `{claim_idx, call}` **coverage_item wrapper**
> (verified: an `ok` coverage record validates, and `call_made:true + not_run` is rejected);
> WAL is now **immutable event-sourced** (`wal_event`) not a mutable-state object; prompt slots
> are **locked** to the verified target placeholders (`<<CITING_SENTENCE>>`; `<<ATOMIC_CLAIM>>`,
> `<<EVIDENCE>>`) — no "provisional"; BATCH carries `run_id` + a **structured** genesis preimage;
> `run_state_manifest` binds every immutable input + checkpoint (resume can't switch datasets);
> numeric params bounded; `system_message:"omitted"` forbids text/hash; the dataset manifest is
> **split** into an `annotation_release_manifest` (flagged + derived-eligible only). Normative
> hygiene: our Decisions 1–2 are marked an **explicit supersession** of methodology §9, not
> "faithful incorporation." Pushed back (out of scope under the threat model): the F3-DI2
> prereg amendment is a **publication** dependency, not a finder-freeze prerequisite; runtime
> import sandboxing stays out (fresh-interpreter + pre-import byte verification is the in-scope
> fix). Both normative files re-pinned to their **canonical no-trailing-newline** bytes.

## Threat model (governs scope — read first)

**Single-author integrity.** The adversary is honest error and silent drift, **not** a
malicious or compromised signer. Trust root is the canonical Git ref of the project repo plus
one author. Everything load-bearing is **content-addressed, immutable, and append-only** so a
mistake cannot pass unnoticed and a number always maps to exactly one pipeline. Explicitly
**out of scope** (do not build): a PKI `TRUST_POLICY` artifact, signer/revoker registries, key
rotation, external signing-time anchors, retroactive revocation, and **runtime import
sandboxing** (constraining `sys.path` / loaded-module origins against a hostile import — that
defends a threat we've excluded; pre-import **byte verification** against the pinned module
manifest is what's in scope, and it is exactly what catches the project's real stale-checkout
bug). Revocation blocks **future** releases only; past runs stay reproducible. Signed
annotated tags are optional corroboration, never the trust root.

**Author identity (resolves Codex's recurring ZD-vs-Aston flag).** "ZD" and "Aston" are the
**same person** (ZD is Aston Liu's handle); there is one author who is also the sole promoter
and revoker. Where the annotation model calls Aston a possible "blind third rater," that means
the same individual rating independently of the two hired annotators — not a second authority.
All `recorded_by` fields are `ZD`.

**Explicit supersession of methodology §9 (Codex v7 #12, conceded).** Methodology §9 literally
keys labels on `(citing_sentence, cited_pmid)` and reuses them across prompt changes. Decisions
1–2 below deliberately **supersede** that: the unit is the reference-level first-citance
`citation_id`, and reuse is keyed on `(citation_id, stimulus_sha256, codebook_sha256)` with
re-annotation when the stimulus changes. This is a **normative amendment**, not "faithful
incorporation" — the methodology doc must be amended and re-pinned to reflect it (tracked as a
doc-hygiene task; it does not block infrastructure implementation).

**Trust root is out-of-band, not self-nominated (Codex v7 #11).** `repo_identity` and
`canonical_ref` appear in CONFIG but the loader compares them to **independently configured
trusted constants** (`TRUSTED_REPO_IDENTITY`, `TRUSTED_CANONICAL_REF`); a CONFIG cannot nominate
its own trust root. The canonical ref must be fast-forward-only and non-deletable.

**Git is not automatically monotonic (Codex v6 #10, accepted — non-PKI).** `commit --amend`,
rebase, branch deletion, force-push, and gc can rewrite private history, so "committed" alone
is not an integrity or time source. Therefore CONFIG pins a **canonical repository identity**
and **canonical release ref** which must be **fast-forward-only and non-deletable**; validation
requires a fresh remote fetch, a clean worktree, and that every artifact (CONFIG, PROMOTION,
REVOCATION, exclusion-ledger tip, dataset manifest, release attestation) be tracked at a commit
**reachable from the canonical ref**; the release attestation pins the exact observed
canonical-ref commit; and **revocation ordering uses commit ancestry, not author/committer
timestamps**.

## Normative references (supplied AND pinned by content hash)

- `F3-F7_EVALUATION_DECISIONS_2026-07-03.md` — **canonical form = no trailing newline**,
  SHA-256 `263c6e2eb31da104c7d53d379ede919e1f270ba8d4af35dc77cb9c15971c1927` (8,938 bytes).
  **Supplied with this package**; commit it into the pinned source tree. (The
  Claude.ai-project copy has a trailing LF → `b6d03c…`/8,939 bytes; the committed canonical form
  drops it, exactly as with the schema file — Codex v7 #1.) §9/§10 incorporated by reference, as
  amended by Decisions 1–2 (see supersession note below).
- `F3-F7_FINDER_FREEZE_SCHEMAS.json` — **canonical form = no trailing newline**, SHA-256
  `b42fae7463387a3d2ba8d625cc2513af4c882824129f41cc350599955d15960d` (67,897 bytes; `$id` and
  every `schema_version` const at **`v14`** — the v17 package-version tightening is breaking, as
  were the v15/v16 tightenings before it).
  **Re-verified by execution (python-jsonschema 4.26.0):** 0 duplicate keys,
  0 dangling `$ref`s (304 occurrences / 50 unique targets), 0 unreferenced `$defs`,
  meta-validates against draft 2020-12; **18/18 root artifact types validate positively** (plus
  release/formal/promo BATCH and run-state) and **29/29 negative cases reject**, including the
  **mode matrix on BOTH batch and run-state** (candidate+formal, candidate+promo,
  release+no-promo, release+calibration, development-BATCH, release-run-state+calibration+null,
  candidate-run-state+promo all rejected), the **PROMOTION shape** (old opaque shape rejected),
  the **locked prompt slots** (extra slot, reordered coverage slots, and non-UTF-8 `encoding`
  all rejected), **bounded `http_status`** (600 rejected in `provider_error` and
  `model_mismatch`), **frozen text versions** (claim_extract ≠ 3 and coverage ≠ 2 rejected), and
  `prohibited_cases` <2 entries; `system_message` omitted+text; supplied decimal temperature;
  `call_made:true + not_run`; excluded record with non-null stimulus; empty-`ref_id`
  `citation_id`.
  `label_record` removed (annotation scope-out). Normative for structure, enums, null-vs-omitted,
  self-hash exclusions, grammars. **A strict byte loader** (reject duplicate keys and any float
  token such as `1.0` *before* parsing) and **a versioned semantic validator** (§12) are REQUIRED.

## Vocabulary — four named hash rules, never mixed

1. **git blob/tree/commit OID** — Git's own object id (algorithm-prefixed: `sha1:` / `sha256:`).
   Source provenance only; **never** equated with a content hash (Codex v5 #1: the
   `band_prompts.py` blob OID `fa01126e…` is not any SHA-256 of its content).
2. **content hash** — SHA-256 over an artifact's exact stored bytes. For a prompt template,
   over the **decoded UTF-8 text** of the template string (not the `.py` file, not base64).
   Every stored string declares strict-UTF-8; no JSON field ever claims to hold raw bytes.
3. **canonical model-request hash** — SHA-256 over the **canonical request bytes the runtime
   controls** (JCS), which are also the bytes handed to the transport. Auth headers/secrets
   are added by the transport **after** hashing and are never persisted or hashed.
4. **canonical-object hash** — `SHA256(canon_v1(object))` for structured objects.

`canon_v1` = RFC 8785 / JCS; **floats prohibited** (real quantities as decimal strings);
duplicate keys / `NaN` / `Infinity` rejected; native JSON booleans (`true`, not `"true"`).

**Envelope pattern (fixes v5's three self-referential digests, Codex v5 #1).** A digest is
never a member of the object it covers. Filename digests: `config_hash =
SHA256(canon_v1(CONFIG))`, `run_hash = SHA256(canon_v1(BATCH without run_hash))` — carried in
the filename, not as a field. Signed/tamper-evident envelopes: `package =
{payload_fields…, package_sha256=H(canon_v1(package_without_package_sha256))}`; `promotion =
{payload, payload_sha256=H(canon_v1(payload))}`; `revocation` likewise. Self-hash exclusions
are stated per artifact in the schema file.

**stimulus** — the complete exported object the annotator acts on: sentence; ordered atomic
claims; evidence; label space; worksheet schema; display instructions; codebook content;
truncation/redaction/ordering settings. The UI renders **solely** from it. `stimulus_sha256 =
SHA256(canon_v1(stimulus_object))`.

**Artifact set (v5's "four" was inaccurate).** PROMPT package · CONFIG · BATCH · PROMOTION ·
REVOCATION · selection artifact · exclusion ledger · dataset manifest · evidence snapshots ·
codebook. Schemas for each in the schema file, except evidence snapshots and the codebook —
hash-governed opaque byte artifacts whose schemas are deliberately NOT in the schema file
(residual #11).

---

## Decisions (Codex-approved — do not reopen)

1. **Label reuse (rec A):** labels append-only keyed `(citation_id, stimulus_sha256,
   codebook_sha256)`; reuse only on exact match. Coverage-prompt change → stimulus unchanged →
   reuse (§9); extraction/displayed-content change → re-surface (§10).
2. **Multi-sentence references (rec B):** unit = reference-level first-citance; key
   `citation_id = <citing_pmcid>:<ref_id>` with a validated grammar (percent-encoded
   components; reject unescaped `:`); no ordinal, no parser change (F2 recall guard protected).
3. **Freeze scope (finder-only):** certifies atomic-claim extraction, evidence presentation,
   coverage assessment, **F6 candidate membership**, and the shared input substrate to the
   downstream F3/F4/F5 discriminators — **not** F3/F4/F5 membership. No valid "F3–F7 flagged
   set" exists after this promotion alone.
4. **Coverage terminal-failure policy:** `continue_after_coverage_terminal_failure: true`.
   Any exhausted terminal coverage failure → remaining claim calls still run, item
   quarantined, run continues. Extraction failure is distinct → 0 coverage calls, item held.

---

**Status:** gate to volume annotation. No F6 candidate set, annotation queue, or finder
precision number is valid until: the finder front-end is frozen as an immutable CONFIG; a
candidate BATCH runs under it in `candidate` mode via internally-constructed requests; and a
committed PROMOTION (envelope-hashed; trust root = the repo) marks it reportable. Supersedes
v5; self-contained (full acceptance matrix and guardrails below, schemas by hash). Claude
never assigns semantic labels or curates ground truth; the promotion decision is ZD's.

## Repository target

Branch cut from `feat/f3-f7-typed-judgment-v2` @ `5cc2bf1`. Prompts authoritative in
`band_prompts.py`. Acceptance constants (Codex-verified; full values, not abbreviated — Codex
v6 #8): blob OID `sha1:fa01126e2b9482d450065fd70cd0eb1fea816f5c`; template **content** hashes
claim `25f7de6267de4d638d1a5fc0c778b852d3efba4865c35c40ff1a0f980a6a4507`, coverage
`1a24d13be0e817a757c8fc5ea1ab40f059c11c580990b09bd5c2fe2d1125421a`. These are different object
kinds and are never equated.

## Inputs ZD supplies before candidate-configuration creation (infra is built now)

The canonical SIX inputs — this list is the numbering authority for every
"ZD input #N" reference in code, proposals, and reports (renumbered
2026-07-25; an earlier revision interleaved two extra items and shifted the
numbers):

1. Model snapshot string(s).
2. Codebook content (content-hashed).
3. Coverage evidence retrieval/snapshot policy + snapshot storage/access/release policy.
4. Freeze criterion.
5. **The two prohibited F3 cases' `citing_pmcid`** (Seeman/DNA-nanotech, idelalisib — known by
   PMID in memory; the citing PMCIDs must be supplied, not guessed; `citation_id`/
   `occurrence_identity` are optional provenance, per the v13 schema fix).
6. **A dependency lockfile with per-package hashes** — none exists in the target branch
   (Codex v5 #5); create one (e.g. `uv.lock` / hash-pinned `requirements`) at repo root and
   record its SHA-256 in `runtime_profile.dependency_lock_sha256`.

Supplied alongside, not numbered inputs: the **selection rule + min size + coverage targets +
frozen selection artifact** (the selection-rule input — ZD decides residual #3 with it), and
the local interpreter command `../.venv_cre/bin/python` (Codex-verified; recorded in BATCH,
**not** pinned in CONFIG). Colab records its own environment identity + lock hash.

---

## Architecture (intent + invariants; exact structure in the schema file)

### 1. Prompt package (`PROMPT_<name>_pkg_v<m>.json`)

Template text keeps its historical version (`claim_extract` text v3, `coverage` text v2), but
the **structured renderer is a different request contract** than the historical sequential
`.replace()`, so the package carries a **new render-contract `package_version`** (Codex v5 #2).
CONFIG binds the full `package_sha256` — which itself covers `render_contract_sha256` and every
other package field, so the render contract is bound transitively (the CONFIG reference carries
`package_version` + `package_sha256` only; SV-002 recomputes both the template digest and
`render_contract_sha256` from package content) — not template hash + number. **Slots are LOCKED, not provisional (Codex v7 #8):** `claim_extract` = exactly one
slot `CITING_SENTENCE` (role user, order 0); `coverage` = exactly two, `ATOMIC_CLAIM` (order 0)
then `EVIDENCE` (order 1) — the schema enforces exact count/name/role/order via
`prefixItems`+`items:false`. Rendering builds role-tagged message blocks (no `.replace()`);
structured blocks prevent **slot collision** — not a claim that cited text can never be read as
instructions. Imported-baseline check asserts only `runtime_constant_utf8_sha256 ==
template_utf8_sha256`; the blob OID is stored, never equated.

### 2. CONFIG (`CONFIG_<config_hash>.json`)

`config_hash` is the filename digest, not a field. Request/parser/tool/params are
**stage-specific** (extract vs coverage have different response schemas). `runtime_profile`
pins a reproducible identity — python implementation/version, platform constraint,
`dependency_lock_sha256`, `distribution_inventory_sha256`, transport + JCS library versions
(and optionally a container image digest) — and contains **no invocation path** (Codex v5 #5).
`source` uses **ordinary** commit + tree OIDs from a commit predating CONFIG/BATCH that
contains code + prompt packages (no custom filtered tree, Codex v5 #6).

### 3. BATCH (`BATCH_<run_hash>.json`)

Runs under a CONFIG via internally-constructed requests. Pins the **selection artifact
sha256 bound into `chain.genesis`**, full chain state, and `chain_hash_version="canon_v1"`
with a **new JCS record canonicalizer** (the existing `_canonical_sha256` is `json.dumps`,
not JCS, and permits floats — legacy chains are rejected or bridged via `legacy_tip_anchor`,
Codex v5 #6). Records the **exact environment command actually used**. Funnel field renamed
`runner_accepted`; `refused`/`truncated` are separate item-vs-attempt diagnostics.

### 3′. Run-state manifest vs BATCH (Codex v6 #9)

BATCH is **final-only** (`status:"complete"`, `run_hash` over the final object). In-progress
state — mutable, recoverable, changing as the run proceeds — lives in a separate
`run_state_manifest`, which becomes immutable when finalized into the BATCH. A run's changing
tip never mutates an immutable filename-hashed artifact.

### 4. PROMOTION (`PROMOTION_<config_hash>.json`)

Envelope `{payload, payload_sha256}`, committed on the canonical ref — the commit that
introduces it is the integrity anchor. `payload_sha256` covers **every** load-bearing field.
`recorded_by` (= `ZD`) is ordinary identity metadata, **not** a verified fingerprint (Codex v6
#11 — nothing is called a fingerprint without verification); a `signed_tag_oid` may corroborate
but is not required. **A revoked CONFIG is never re-promoted** (one PROMOTION per `config_hash`).

### 5. REVOCATION (`REVOCATION_<config_hash>_<fs_safe_id>.json`)

Envelope-hashed, committed. Filesystem-safe id (not a raw ISO timestamp). Blocks **future**
releases as of the introducing commit; **no retroactive invalidation**; reproduction of past
runs remains permitted and labeled `historically_reproducible`. **Rechecked at
release/publish time, not only at run startup.**

### 6. Execution modes, request + evidence construction

`execution_mode ∈ {candidate, release, development}`, plus a **finder-only entrypoint
`run_finder_frontend`** (Codex v5 #8): under finder `release`, downstream unfrozen F3/F4/F5
stages are **skipped/held**, never inheriting the finder's mode; they run only under their own
explicit non-reportable `development` mode.

- **candidate:** CONFIG required, PROMOTION not required (ineligible); constructed requests.
- **release:** CONFIG + valid unrevoked PROMOTION required.
- **development:** explicit opt-in; injected transport/clock/snapshot/renderer/callables
  permitted; unattested, nonreportable.
- Gate **fails closed before any side effect** (`FreezeConfigInvalid`, zero side effects),
  after **on-disk module-byte verification**.
- **Requests:** in candidate/release the runtime **internally constructs a source-pinned
  provider adapter** and serializes canonical request bytes, hashes them, and sends bytes; an
  **injected transport is rejected** (a fake transport could return fabricated responses with
  perfect request hashes — Codex v5 #7; negative test required). `auth_context` is
  **credentials only** — cannot alter endpoint/model/params. **The WAL is immutable
  event-sourced (Codex v7 #3, #4):** one `wal_event` record per transport-attempt state
  transition (`prepared → sent → response_persisted | indeterminate`), each linked by
  `(attempt_id, event_seq, prev_event_sha256)` and self-hashed; a `response_persisted` event
  preserves HTTP status, response-body hash+ref, allowlisted headers,
  returned model/response ids (required-nullable), and finish reason — a persisted HTTP response
  has no transport exception; `transport_exception_class` lives on the `transport_failed` and
  `indeterminate` events, which is exactly what the schema's closed branches enforce. `review_call.retry_wal_event_shas` are the
  **ordered, unique WAL event hashes** for the attempts (not opaque ids). Each carries the
  request hash, previous-attempt link, and
  `idempotency_key = H(config_hash, run_id, citation_id, stage, claim_idx, request_bytes_sha256)`
  — **domain-separated so two distinct calls with identical bodies never collide** (Codex v6
  #6). **Crash policy (Codex v6 #6, accepted with pushback):** true "no duplicate call" is
  impossible unless the provider offers idempotent replay/lookup, which is *not* assumed. An
  attempt left `sent`-but-unpersisted after a crash becomes `indeterminate` and **quarantines
  the item** — it is never silently retried. (A rare duplicate paid call is a cost annoyance,
  not a validity threat; building provider-specific recovery is out of scope.) Persist **decoded
  HTTP-body bytes** (content-addressed, not compressed wire bytes), status, an **allowlist of
  behavior-relevant non-secret headers** (secrets never stored), and one record per attempt.
  Verify the **provider-returned model id equals the configured snapshot**; mismatch is a
  distinct terminal `model_mismatch` status (`provider_model_id` is absent for
  timeout/empty/no-response states).
- **Evidence:** candidate/release accept **only** a CONFIG-governed content-addressed snapshot
  reader; injected fetchers/evidence-builders/renderers and alternate import paths are
  rejected; snapshot bytes verified before any call.

### 7. Item key and evidence

`item_key = citation_id` (validated grammar). `resolved_work_id` recorded, never keyed, and
**bound to its evidence snapshot** via the candidate manifest, which also carries upstream
F1/F2 resolution provenance. Fail-closed on duplicate PMCID files, duplicate `ref_id`,
duplicate `citation_id`, or conflicting source snapshots.

### 8. Batch runner — one terminal record per claim

Mechanical only. Extraction = 1 `review_call`; coverage = an array of **`coverage_item`
wrappers** `{claim_idx, call}`, **exactly one per extracted claim**, indices contiguous
`0..n-1`, **even when no call fires** (evidence unusable → each `call` is `{call_made:false,
status:"not_run", reason:"evidence_unavailable", retry_wal_event_shas:[]}`). The wrapper is what
fixes v7's unsatisfiable intersection (Codex v7 #2). Terminal `review_call` states are per-state
typed (terminal `timeout` is pre-send `connect` ONLY — read/total timeouts are post-send-boundary
and become `indeterminate`, never a terminal timeout; empty distinguishes
zero-byte vs valid-empty; `model_mismatch` requires the returned model id; `provider_error`
preserves status/body/headers; `indeterminate` names the WAL event proving the ambiguous send). Durability reuses the existing judgment-run hash-chain
lifecycle under the new `canon_v1` record canonicalizer; torn tail preserved and replayed;
resume under a different `config_hash` rejected before any write; completed output immutable.
Artifact writes: **regular-file check, ownership + link-count==1, same-directory temp file →
fsync → no-replace atomic install → directory fsync**, defending symlink/hardlink and
validate/use (TOCTOU) races (`O_EXCL` alone can leave a corrupt partial after a crash).

### 8b. Candidate-batch integrity + exclusion chronology (Codex v8 #9, fixed)

v8 had a self-exclusion bug: if every candidate item must be in the exclusion ledger *before*
the run, and input-exclusion → `excluded`, the candidate batch excludes itself. Correct
chronology, now pinned:

1. Select candidate items against **exclusion checkpoint N** (which does **not** yet contain
   them); the run records `exclusion_checkpoint_sha256_at_start = N`.
2. Run the candidate batch (mode `candidate`, purpose `calibration`) — items are *not* excluded
   at start because they aren't in N.
3. **Before any human inspects the output**, append exposure entries for the inspected items
   (`reason: human_inspected/calibration`, `scope: citing_paper`) → **checkpoint N+1**.
4. **PROMOTION pins the post-exposure checkpoint N+1** (its payload's
   `post_exposure_exclusion_checkpoint_sha256`), so no promotion can predate the exposure record.
5. Every later **formal** selection consults **N+1 or later** and thus excludes those citing
   papers.

The runner still refuses to proceed unless a **committed selection artifact** exists with its
sha256 **bound into `chain.genesis`**, every item carries natural-source provenance (corpus
source id + retrieval-record hash) + the author's `not_detector_sourced` attestation, and the
**two prohibited F3 cases are absent** from the batch.

**Exclusion (Codex v7 #9 / v10 #5, corrected — no false stability claim).** The durable
contamination key is **`citing_pmcid`, applied citing-paper-wide** for every human-inspected
calibration item (not only the two confirmed F3 positives). `occurrence_identity` is computed
from the *current* normalized key and is **not** claimed stable across a later resolution — a key
change may change it — and `source_occurrence_fingerprint` is snapshot-dependent; **neither is the
contamination key.** Both are retained only as provenance / dedup aids. This is why a changed
`ref_id`/parser/snapshot cannot reintroduce a case: the citing paper is excluded regardless of
key drift.

### 9. Label reuse, dispositions, funnel, release validation

- Labels append-only keyed `(citation_id, stimulus_sha256, codebook_sha256)`.
- **Final finder disposition precedence (exact, Codex v5 #10):** input exclusion → `excluded`;
  extraction failure / no claims / evidence unavailable → `held`; **any** coverage terminal
  error → `quarantined` (even if another claim is unsupported); otherwise any unsupported claim
  → `flagged`; otherwise → `clear`.
- **Funnel invariants:** `input_total = excluded + runner_accepted`;
  `runner_accepted = flagged + clear + held + quarantined`. `refused`/`truncated` are separate
  diagnostics with explicit item-vs-attempt units.
- `finder_evaluation_eligible` is **derived by the release validator**, never trusted from an
  input row.
- **Leakage audit (test-enforced):** exported stimulus carries no
  route/category/confidence/coverage-verdict/source-frame/calibration-status/sampling-stratum/
  raw-output/internal-ID.
- **Exclusion ledger is verifiable (Codex v6 #14):** each entry carries `seq` (contiguous
  from 0), `prev_entry_sha256`, and `entry_sha256 = SHA256(canon_v1(entry∖entry_sha256))`; a
  separate `exclusion_checkpoint` pins the tip + entry count + observed canonical-ref commit. A
  release proves its `exclusion_checkpoint` is the **latest reachable from the canonical ref**;
  a missing/forked/rewritten/stale ledger fails the release closed.
- **Selection & inventory cannot corrupt the denominator (Codex v6 #15):** the semantic
  validator enforces canonical sort by `citation_id`, semantic uniqueness by **both**
  `citation_id` and `occurrence_identity` (the snapshot-independent dedup aid — matches the
  schema and SV-031; `source_occurrence_fingerprint` is snapshot-dependent and is NOT a
  uniqueness key), `len(items) ≥ min_size`, and
  `coverage_targets` met — so no duplicate/unsorted/repeated row double-counts.
- **`source_occurrence_fingerprint` (Codex v6 #16)** = `SHA256(canon_v1({source_xml_sha256,
  ref_content_utf8, extraction_contract_version}))`; the selection item stores all three
  components so it is recomputable.
- **Manifest split (Codex v7 #14, #16).** The release artifact is an
  `annotation_release_manifest` = the annotation **denominator**: **`flagged` + validator-eligible
  rows only** (not a full-run inventory, so no held/quarantined item with a null stimulus can
  appear). Eligibility is **derived by the validator, never stored** (no byte-distinct manifests
  with identical semantics). It is content-addressed and anchored by a `release_attestation`;
  each row binds `citation_id`, `occurrence_identity`, `source_xml_sha256`, `resolved_work_id`,
  `resolution_provenance_sha256`, `stimulus_sha256`, `evidence_snapshot_sha256`. `"latest"`
  prohibited. Release validation re-checks revocation by commit ancestry and asserts every row
  matches. (A separate full-run inventory, if needed, lives in the review dump with nullable
  fields — it is not the denominator.)
- **Two-phase git anchoring (Codex v7 #15).** Checkpoints/attestations cannot name their own
  introducing commit. Rule: (1) commit all referenced inputs; (2) build the
  checkpoint/attestation referencing that parent commit; (3) commit the artifact; (4) the
  semantic validator requires the introducing commit's **immediate parent to equal the
  validated-state commit** (not merely a descendant — Codex v10, so no revocation or ledger change
  can slip in between), and reachable from the canonical ref. Matches SV-041.

### 10. Reportability — three layers, not one immutable blob (Codex v6 #12, #13)

An immutable record must not carry a value that goes stale. So reportability lives in three
layers:

- **Run record (immutable, run-time facts only):** `finder_configuration_attested`,
  `finder_freeze_promoted_at_run`, `config_hash_used`, `promotion_payload_sha256_at_run`. No
  `valid_at_release` / `currently_unrevoked` here — a candidate record cannot know them.
- **Release attestation (immutable, validity at ONE canonical-ref commit):** `valid_at_release`
  bound to that commit + manifest digest.
- **Current validity (computed live from the canonical ref, never persisted as timeless
  truth):** `finder_result_reportable`, `discriminator_result_reportable`,
  `composite_result_reportable`. On a downstream F3/F4/F5 row the **finder result stays
  reportable**; only the composite is false. A **semantic validator** enforces the equations
  (e.g. a calibration item can never be `evaluation_eligible`; `composite ⇒ finder ∧
  discriminator`; `finder_result_reportable ⇒ promoted ∧ eligible ∧ unrevoked-at-release`) with
  **negative fixtures** — JSON Schema alone cannot express these.

### 11. Module manifest + pre-import verification (Codex v6 #17 / v7 #10, scoped)

CONFIG pins `module_manifest_sha256`; the manifest lists every trust-boundary module
(validator, canonicalizer, renderer, parser, provider adapter, evidence reader, runner,
package_init, strict_loader, semantic_validator) by repo path + git blob OID + content hash;
the semantic validator requires **exhaustive role coverage** and unique `(role, repo_path)`.
Candidate/release **run in a fresh interpreter process** and verify those files' **bytes on disk
before importing them**; if any trust-boundary module is already present in `sys.modules` before
verification, the run **fails closed** (Codex v7 #10 — byte-verifying disk does not prove Python
is executing those bytes; this is exactly the project's stale-`sys.modules` bug, and `fresh_interpreter`
is recorded `true` in BATCH). **Bootstrap (Codex v8 #11).** Verification-before-import is
circular unless something trusted does the first check, so a **stdlib-only `bootstrap` launcher**
(its own `module_manifest` role) is **byte-verified by the parent** before it spawns the fresh
child; the child then verifies `strict_loader`/`semantic_validator`/the rest before importing
them. The out-of-band trusted repo/ref constants live in that bootstrap. **Runtime `sys.path` /
loaded-module-origin sandboxing stays out of scope** — that defends a hostile import, which the
threat model excludes.

### 12. Semantic validator (`semantic_validator_v1`; required beyond JSON Schema)

Each rule has a stable ID, an exact predicate, and a failure code (`SV-nnn`). The validator
ships a positive and a negative **fixture per rule** — but fixtures are produced *with the
validator implementation* (they are test code), not enumerated in this prose (Codex v9 pushback).
The rule contract:

| ID | Predicate (must hold) | Fail code |
|---|---|---|
| SV-001 | Every self-hash equals its artifact's DECLARED preimage: envelope artifacts (promotion, revocation) → `payload_sha256 == SHA256(canon_v1(payload))` (payload only — envelope-level fields incl. `signed_tag_oid` are outside the hash by design); all other self-hashed artifacts (package/entry/attestation/record/wal_event) → `SHA256(canon_v1(obj ∖ that field))`; embedded-object field hashes recomputed likewise: `run_state_manifest.observed_runtime_sha256 == canon_sha256(observed_runtime)` | E_SELF_HASH |
| SV-002 | **Recompute, never trust:** `SHA256(UTF8(template_utf8)) == template_utf8_sha256` (the embedded text hashes to the stored digest — a drifted template with a pasted-in pinned digest FAILS here); `canon_sha256(render_contract) == render_contract_sha256`; `runtime_constant_utf8_sha256 == template_utf8_sha256`; blob OID never compared to a content hash | E_TEMPLATE |
| SV-003 | `config_hash == SHA256(canon_v1(CONFIG))`; `run_hash == SHA256(canon_v1(BATCH∖run_hash))` | E_FILENAME_HASH |
| SV-005 | Every `observed_runtime` field with a CONFIG `runtime_profile` counterpart equals it (python implementation/version, `dependency_lock_sha256`, `distribution_inventory_sha256`, transport + JCS library versions); `matches_config_runtime_profile` is RECOMPUTED from that comparison, never trusted from the artifact *(added post-v17 by the phantom audit; resolution direction in the build spec's residuals #2)* | E_RUNTIME_MATCH |
| SV-010 | `input_total = excluded + runner_accepted`; `runner_accepted = flagged+clear+held+quarantined` | E_FUNNEL |
| SV-011 | `finder_disposition` derived by precedence excluded→held→quarantined→flagged→clear | E_DISPOSITION |
| SV-020 | Genesis = `SHA256(canon_v1(genesis_preimage))`; review chain `seq` contiguous from 0, `prev_record_sha256` links, seq 0 → genesis; tip recomputable from dump | E_CHAIN |
| SV-021 | WAL: global `wal_seq` and per-attempt `attempt_event_seq` contiguous + linked; `indeterminate` has a prior durable `sent` in the same attempt | E_WAL_CHAIN |
| SV-022 | Retry only when NOT `sent_boundary_crossed`; any read-timeout/reset after boundary → `indeterminate`, item quarantined | E_SEND_BOUNDARY |
| SV-023 | `idempotency_key == SHA256(canon_v1(idempotency_preimage))`; preimage persisted in the `prepared` WAL event | E_IDEMPOTENCY |
| SV-030 | Manifest rows ⊆ the release-mode/formal BATCH's review chain (row `review_record_sha256`/`seq` present); `source_run_hash`/`source_review_dump_sha256`/`source_selection_hash` match that BATCH | E_MANIFEST_RUN |
| SV-031 | Manifest sorted by `citation_id`; unique by `citation_id` AND `occurrence_identity` (the empty-inventory ⇒ precision-undefined rule moved to the annotation-freeze spec — the precision-result artifact lives there, so this package cannot check it) | E_MANIFEST_UNIQ |
| SV-032 | `finder_evaluation_eligible` recomputed (calibration ⇒ ineligible); never trusted from input | E_ELIGIBLE |
| SV-033 | Selection artifact internal invariants: items canonically sorted by `citation_id`; unique by `citation_id` AND `occurrence_identity`; `len(items) >= min_size`; every `coverage_targets` entry met by the item set | E_SELECTION |
| SV-034 | `candidate_manifest.items` exactly equal the selection artifact's ordered `citation_id` set (same ids, same order, same count), resolved via `selection_artifact_sha256` → actual committed selection bytes; the SV-043 prohibited-`citing_pmcid` check applies to BOTH the selection and the candidate manifest | E_CANDIDATE_BIND |
| SV-024 | `review_call.ok.parsed` conforms to the stage's pinned response schema (extraction vs coverage); coverage cardinality + claim-index correspondence | E_PARSED |
| SV-025 | Stimulus `evidence.text` derives from `evidence_snapshot_sha256` under the pinned policy; `citing_sentence` derives from the selected source-XML occurrence | E_DERIVATION |
| SV-026 | Each `review_call`'s response fields equal its terminal WAL event; `retry_wal_event_shas` = every WAL event of the logical call, globally ordered; per-attempt request hash/key/logical-call id identical; ordinals contiguous; extraction preimage `claim_idx:null`, coverage integer | E_WAL_MATCH |
| SV-040 | Exclusion consulted citing-paper-wide on `citing_pmcid`; PROMOTION's N+1 strictly newer than candidate BATCH's N and covers every `exposure_plan` row citing-paper-wide | E_EXCLUSION |
| SV-041 | Revocation ordered by commit ancestry; checkpoint/attestation introducing commit's **immediate parent == validated-state commit** (not merely descendant); all artifacts reachable from the trusted canonical ref | E_GIT_ANCHOR |
| SV-042 | `repo_identity`/`canonical_ref` == out-of-band trusted constants; canonical ref ff-only, non-deletable | E_TRUST_ROOT |
| SV-043 | `candidate_protocol_sha256` == CONFIG's; protocol's `schema_sha256` == the schema actually used; protocol committed before CONFIG + candidate run; `prohibited_cases` ⊇ the two known citing papers, unique by `citing_pmcid`; selection excludes every prohibited `citing_pmcid` | E_PROTOCOL |
| SV-044 | PROMOTION's `exposure_plan.run_hash == run_hash`; every exposure row ∈ the run's review chain; unique by `citation_id` AND `review_record_sha256`; the exposure_plan's introducing commit is a strict ancestor of PROMOTION's introducing commit (the checkable proxy for "committed before reveal" — the UI-side gate that refuses to render output without a committed exposure_plan is a RUNNER enforcement point, built and tested with the runner, not a post-hoc artifact predicate); PROMOTION targets a candidate/calibration BATCH under the same CONFIG | E_EXPOSURE |
| SV-045 | Mode matrix: candidate⇒calibration∧null-promotion; release⇒formal∧valid-matching-promotion; development⇒never eligible/promotable/reportable | E_MODE |
| SV-050 | Reportability, COMPUTED at validation time from canonical-ref state + artifacts (per §10 these values are never persisted, so the rule audits the derivation function, not stored fields): `composite ⇒ finder ∧ discriminator`; `finder_result_reportable ⇒ promoted ∧ eligible ∧ unrevoked-at-release ∧ in-scope`; fixtures feed synthetic ref-states + artifacts and assert the derived flags | E_REPORTABLE |
| SV-060 | `citation_id` canonical percent decode/re-encode equality; `item_key == citing_pmcid+':'+pctencode(ref_id)` | E_CITATION_ID |
| SV-061 | `occurrence_identity == SHA256(canon_v1({occurrence_identity_version, citing_pmcid, normalized_ref_key_type, normalized_ref_key}))` — these four members exactly, keyed by these field names (matches the schema verbatim); `normalization_contract_sha256` is deliberately NOT in the preimage (stored alongside; contract versioned via `occurrence_identity_version`); recomputable for the **current** key; a key change **may** change it; contamination protection depends **only** on citing-paper-wide `citing_pmcid` exclusion, never on this identity | E_OCCURRENCE |
| SV-070 | Recursive leakage scan of the entire stimulus (incl. nested `worksheet_schema`) for forbidden provenance fields | E_LEAKAGE |
| SV-071 | `codebook_sha256 == SHA256(codebook_content)`; atomic-claim idx contiguous+unique; `label_space` unique; worksheet question ids unique | E_STIMULUS |
| SV-072 | `review_record.stimulus_sha256` RECOMPUTED as `SHA256(canon_v1(stimulus_object))` from the actual stimulus bytes (never trusted from the record); `stimulus_object.codebook_sha256 == CONFIG.codebook_sha256` — an unrecomputed stimulus hash silently breaks label reuse (Decision 1) *(added post-v17 by the phantom audit; resolution direction in the build spec's residuals #7)* | E_STIMULUS_HASH |
| SV-090 | Header names ∈ pinned case-normalized allowlist; credential-bearing headers (authorization/cookie/api-key) rejected | E_HEADER |
| SV-091 | `endpoint.base_url` host ∈ the out-of-band pinned host allowlist (a trusted constant in the bootstrap, alongside `TRUSTED_REPO_IDENTITY`/`TRUSTED_CANONICAL_REF` — e.g. `api.anthropic.com`); `host_allowlisted` is RECOMPUTED from that list, never trusted from the CONFIG | E_HOST |
| SV-101 | Retry-policy cross-field equations (promised in the schema `$comment`, enforced here): decimal-string compare `total_timeout_seconds >= max(connect_timeout_seconds, read_timeout_seconds)`; `backoff_cap_seconds >= backoff_base_seconds`; `retry_after_cap_seconds` participates only when `respect_retry_after` | E_RETRY_POLICY |
| SV-100 | RFC 3339 semantic validity; all persisted integers within I-JSON safe range; CAS reference grammar + storage-root confinement (no absolute/`..`/symlink) | E_FORMAT |
| SV-110 | RUNTIME GATE, not a post-hoc artifact predicate (no artifact carries a pre-import transcript to audit retroactively): the bootstrap itself enforces fail-closed — fresh interpreter; no trust-boundary module in `sys.modules` before byte verification; module manifest role coverage exhaustive; module `repo_path` normalized repo-relative and inside the pinned tree. Evidence = the bootstrap's subprocess test fixtures (violations must abort before import) + `observed_runtime.fresh_interpreter` recorded in BATCH | E_BOOTSTRAP |

(SV-080 label/adjudication rules **removed** — the annotation subsystem is out of scope, §Out of scope.)

### 12b. Review-round residuals — canonical numbered list (numbering authority)

The 2026-07-24 unrestricted round's items, as resolved by the validator build.
The round's prose said "ten valid defects"; this list of ELEVEN is canonical
(#8 is a build-scope join and #11 a prose correction, not schema/validator
defects). Every "residual #N" reference in code, fixtures, proposals, and the
conformance report resolves against THIS list. Status as built:

1. SV-002 additionally asserts the frozen acceptance constants (claim
   `25f7de62…`, coverage `1a24d13b…`, `source_blob_oid` `sha1:fa01126e…`) —
   internal consistency alone is not a freeze. **DONE** (validator constants +
   dedicated negative fixture).
2. New rule SV-005: every `observed_runtime` field with a CONFIG
   `runtime_profile` counterpart equals it; `matches_config_runtime_profile`
   recomputed, never trusted. **DONE.**
3. SV-033 `coverage_targets` need per-item strata to be evaluable — schema
   delta applied as a **PROPOSAL** (optional `stratum`, enum-of-target-keys
   enforced semantically); fail-closed when unevaluable; ZD decides with the
   selection-rule input (alternative: demote `coverage_targets` to
   informational). **PENDING ZD.**
4. SV-034 binds FULL rows (per-`citation_id` `source_xml_sha256` equality,
   fail-closed on conflicting snapshots) and binds
   `resolved_work_id`/`resolution_provenance_sha256`/`evidence_snapshot_sha256`
   forward into the review record. **DONE.**
5. Stage configs carry required-nullable `response_schema_sha256` (schema
   delta); SV-024 validates shape presence only while null and says so; ZD
   supplies/approves the response schemas with input #1. **DELTA APPLIED,
   interim; PENDING ZD.**
6. `TRUSTED_RESPONSE_HEADERS`, `CAS_ROOT`, `CAS_REF_GRAMMAR` become bootstrap
   trusted constants alongside repo identity / canonical ref / endpoint
   hosts. **DONE.**
7. New rule SV-072: `stimulus_sha256` recomputed from the stimulus object;
   `stimulus_object.codebook_sha256 == CONFIG.codebook_sha256`. **DONE.**
8. `cre/f1/freeze/bootstrap.py` (stdlib-only launcher, parent
   byte-verification, fresh child) joins this build; SV-110's evidence = its
   subprocess fixtures. **DONE.**
9. SV-042 rescoped to ancestry continuity between successive observed
   canonical-ref states; force-push/deletion protection is hosting policy —
   an operational setup step, documented (PROPOSALS_PENDING_ZD.md), not a
   validator predicate. **DONE.**
10. Schema delta: `config.source.source_commit_oid` → `git_commit_oid` type;
    semantic check (SV-041) that `source_tree_oid` IS `source_commit_oid`'s
    tree. **DONE.**
11. Vocabulary prose (one line): evidence snapshots and the codebook are
    hash-governed opaque byte artifacts — their schemas are deliberately NOT
    in the schema file. **APPLIED.**

---

## Acceptance matrix (complete)

| Input / fixture | Field | Expected |
|---|---|---|
| Prompt provenance | equality | `runtime_constant_utf8_sha256 == template_utf8_sha256`; blob OID stored, never equated |
| Structured renderer vs historical `.replace()` | package | new render-contract `package_version`; text version stays 3/2 |
| Module verification | order | on-disk bytes checked **before** import; mismatch → fail-closed, zero side effects |
| Slot value `<<EVIDENCE>>` | rendering | no slot collision |
| `config_hash` recompute | value | reproducible; not a field; `source_tree_oid` is an ordinary Git tree OID |
| `runtime_profile` | contents | no invocation path; dep-lock + distribution-inventory hashes present |
| `package_sha256` / `promotion.payload_sha256` | self-hash | excludes itself/auth per schema; envelope validates |
| PROMOTION payload field altered post-commit | validity | fails (`payload_sha256` covers every payload field); envelope-level fields (`signed_tag_oid`) are OUTSIDE the hash by design — their alteration is caught by the introducing-commit anchor, not by `payload_sha256` |
| Revoked CONFIG re-promotion attempt | result | rejected (one PROMOTION per config; revoked stays revoked) |
| Revocation appearing after run, before release | release | blocked at release-time recheck; past run stays `historically_reproducible` |
| `execution_mode=candidate` | states | attested, not promoted, not reportable; constructed requests |
| `execution_mode=release`, missing/revoked PROMOTION | side effects | none; `FreezeConfigInvalid` before any dir/file/model call |
| Injected fake `send_preconstructed_request` in release | result | rejected (adapter constructed internally); negative test passes |
| `auth_context` altering endpoint/model | result | rejected (credentials-only) |
| Crash after provider-accept, before persist | resume | **no automatic retry**; item `indeterminate` → quarantined (provider replay not assumed) |
| Provider model id ≠ configured snapshot | status | terminal `model_mismatch` (returned model id required) |
| Read-timeout/connection-reset after `sent_boundary_crossed` | retry | **not retried** (send-boundary governs, not exception class) |
| 7-claim item, evidence unusable | coverage | 7 `coverage_item`s, each `call.call_made:false / not_run`, `retry_wal_event_shas:[]` |
| Claim 3 terminal failure | run | claim 3 error record; 4–6 run; item `quarantined`; run continues |
| Extraction fails | coverage | 0 coverage calls; item `held` |
| Disposition precedence | derive | excluded→held→quarantined→flagged→clear applied in order |
| Funnel | equations | `input_total=excluded+runner_accepted`; `runner_accepted=flagged+clear+held+quarantined`; refused/truncated separate |
| Real JATS XML, one `rid` in 3 sentences | `item_key` | single `citation_id`; parser untouched |
| Same stimulus bytes + codebook | reuse | reused |
| Extraction re-frozen | reuse | re-surfaced |
| Coverage-only re-freeze | reuse | reused; denominator recomputed |
| Selection artifact missing / not in genesis | runner | refuses |
| Item lacking natural-source provenance | runner | refuses |
| Changed `ref_id`/parser/snapshot reintroducing an excluded case | release | blocked via **citing-paper-wide `citing_pmcid`** exclusion (`occurrence_identity` and `source_occurrence_fingerprint` are provenance only — neither is the contamination key) |
| Candidate batch vs its own exclusion | chronology | run consults checkpoint N (items absent → not self-excluded); exposure → N+1; PROMOTION pins N+1 |
| Prohibited F3 `citation_id` in batch | runner | refuses |
| Resume under a different `config_hash` / `execution_mode` / checkpoint | result | rejected before write (run-state binds all) |
| Candidate run resumed as release, or release resumed on newer checkpoint | result | rejected (mode + checkpoint bound in run-state & BATCH) |
| Chain hash | version | `canon_v1`; review records carry `seq`/`prev_record_sha256`/`record_sha256`; tip recomputable |
| Exported stimulus | leakage audit | recursively free of route/category/confidence/stratum/raw-output/internal-ID (incl. inside `worksheet_schema`) |
| `annotation_release_manifest` (finder's queue output) | binding | pins `source_run_hash`+`source_review_dump_sha256`+`source_selection_hash`; each row cites `review_record_sha256`; validator proves row ∈ that release-mode/formal BATCH |
| PROMOTION | binding | references `candidate_protocol_sha256`+`exposure_plan_sha256`+`post_exposure_exclusion_checkpoint_sha256`; old opaque shape rejected |
| Mode matrix | validation | candidate/calibration/null ✓, release/formal/promo ✓; candidate+formal, candidate+promo, release+no-promo, release+calibration, development-BATCH all rejected |
| WAL retryability | rule | governed by `sent_boundary_crossed`; terminal `timeout` = `connect` only (read/total → `indeterminate`); `transport_failed` = safe pre-send failure |
| `idempotency_key` | preimage | `SHA256(canon_v1(idempotency_preimage))` with literal `CRE_FINDER_IDEMPOTENCY_V1` object; `claim_idx` null for extraction |
| Supplied decimal `temperature`/`top_p` | validation | rejected (must be omitted/provider_default/unsupported — no JSON-float in the hashed request) |
| Stimulus | completeness | embeds `codebook_content`; closed `worksheet_schema`; `label_space` unique; atomic-claim idx contiguous |
| Fresh-interpreter bootstrap | trust | stdlib-only launcher byte-verified by parent before spawn; in the module manifest |
| Downstream F3 row | reportability | `finder_result_reportable=true`, `composite_result_reportable=false` |
| Artifact write crash | integrity | no corrupt partial (temp→fsync→atomic install→dir fsync) |
| `continue_after_coverage_terminal_failure` | type | native boolean `true` |
| `finder_evaluation_eligible` | trust | derived by validator; not a manifest field |
| Schema root given `{}` / `null` / `7` | validation | rejected (discriminated `oneOf` on `artifact_type`) |
| Dangling `$ref` audit | schema | 0 dangling; every artifact type instantiates and validates |
| Every artifact + review record | schema | validates against `F3-F7_FINDER_FREEZE_SCHEMAS.json` (`b42fae74…`) at `#/$defs/<artifact_type>` |
| Drifted `template_utf8` carrying the pinned digest | SV-002 | E_TEMPLATE (digest recomputed from embedded text, never trusted) |
| Candidate manifest items ≠ committed selection's ordered id set | SV-034 | E_CANDIDATE_BIND |
| Selection with `len(items) < min_size` or unmet `coverage_targets` | SV-033 | E_SELECTION |
| `base_url` host not in the pinned out-of-band allowlist | SV-091 | E_HOST (`host_allowlisted` recomputed) |
| `total_timeout < max(connect, read)` or `backoff_cap < backoff_base` | SV-101 | E_RETRY_POLICY |
| `observed_runtime_sha256` ≠ recomputed hash of embedded `observed_runtime` | SV-001 | E_SELF_HASH |
| `coverage_item` wrapping an `ok` call | validation | **passes** (v7 unsatisfiable-intersection fixed) |
| Locked prompt slots | validation | extraction exactly `[CITING_SENTENCE]`; coverage exactly `[ATOMIC_CLAIM, EVIDENCE]`; extras/dupes/reorder rejected |
| `system_message.state:"omitted"` with `text_utf8` | validation | rejected |
| `temperature.value:"3"` / `top_p:"1.5"` / `max_tokens:0` | validation | rejected (bounds) |
| WAL | shape | immutable `wal_event` per transition; `retry_wal_event_shas` are ordered unique event hashes |
| Resume | binding | `run_state_manifest` binds selection+candidate+genesis+WAL tip+checkpoints; mismatch rejected |
| `annotation_release_manifest` | rows | `flagged`-only, eligibility derived not stored |
| Changed `ref_id`/parser/snapshot | exclusion | still blocked via citing-paper-wide `citing_pmcid` (not via the mutable `occurrence_identity`) |
| Fresh interpreter | BATCH | `observed_runtime.fresh_interpreter=true`, `matches_config_runtime_profile=true` |
| `repo_identity`/`canonical_ref` ≠ trusted constants | validation | rejected (CONFIG can't self-nominate trust root) |
| `review_call` with `call_made:true, status:"not_run"` | validation | rejected (discriminated oneOf) |
| `1.0` float token anywhere | strict loader | rejected before parse |
| Duplicate JSON key | strict loader | rejected before parse |
| BATCH with `status:"in_progress"` | validation | rejected (BATCH is final-only; in-progress → run_state_manifest) |
| Two distinct calls, identical bodies | idempotency_key | differ (domain-separated) |
| Sent-but-unpersisted attempt after crash | resume | **no automatic retry; item quarantined as `indeterminate`** (provider idempotent-replay not assumed) |
| Immutable run record | fields | carries no `valid_at_release`/`currently_unrevoked` (those are attestation / computed) |
| Exclusion checkpoint not latest-reachable from canonical ref | release | fails closed |
| Artifact not reachable from canonical ref | validation | fails closed |
| Module bytes ≠ manifest before import | gate | fails closed before import-time code runs |

Fixtures use real JATS XML with a multi-sentence citation.

## Guardrails (complete — do NOT change)

- Claude never assigns semantic labels or curates ground truth; the runner is mechanical.
- Detector flags never become gold; exported stimulus carries no category/verdict (leakage
  audit enforces this).
- **Naturally-occurring only** in any reportable/gold/validation figure; synthetic for model
  training only, documented and disjoint.
- **Annotation reliability protocol is OUT of this spec** (separate annotation-freeze spec).
  This spec takes no position on annotator count or the agreement statistic: the 2026-07-03
  methodology doc (Decision 1) says two annotators + Cohen's κ, while the project's current
  locked state is solo annotation with reliability via Gwet's AC1 / blind test-retest / advisor
  spot-checks. ZD resolves that conflict in the annotation spec before annotator instructions.
  What binds HERE regardless: annotators judge, never construct; no committed agreement value or
  gate — this freeze is the gate; the finder's proposed labels are never shown pre-commit.
- Path-based module loading: no `__init__.py` in `cre/`; `cre/f1/` keeps its own; restart
  Colab after any push (stale `sys.modules`); reinstall `rapidfuzz` after a runtime reset;
  Drive-first I/O.
- `author_match` / resolved tri-state `None` → test `is None` / `is False`, never falsy.
- No-rewrite: all artifacts immutable; changes create new versions/configs; defects handled by
  additive committed REVOCATION; do not edit `band_prompts.py` text, PREREGISTRATION, or
  TAXONOMY docs.
- **Do not touch the F2 path** (`parser.py`, `biblio_match.py`, `lookup.py`); the item-key
  decision exists to avoid the parser.

## Regression guards

Recall guard first: `cd citation_repair_F1_handoff && PYTHONPATH=. ../.venv_cre/bin/python -m
pytest cre/f1/test_f2_recall_guard.py -q`. F2 guard PMIDs must still band: `31665581`,
`16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` (`16639420` is the
recall-guard shape case). Existing 28 judgment-band tests run in `development` mode; assertions
not silently changed.

## Definition of done

Recall guard green; full `cre/f1` suite passes (branch baseline collects **759** — state the
new total after artifact/gate/runner/reuse/funnel/leakage/schema tests). Every artifact + review
record validates against the pinned schema file. Acceptance matrix verified incl. real-XML
multi-sentence fixture, fail-closed bypass tests through `run_finder_frontend`,
`run_natural_judgment`, `run_band`, `judge_pair`, `annotation_payload`, the fake-transport
negative test, and the crash/WAL reconciliation test (the DOM/render-diff test moved to the
annotation-freeze spec with the rest of the annotation UI). Candidate batch runs in
`candidate` mode via the internal adapter + content-addressed reader. Release validator green
with revocation recheck; funnel reconciles; exclusion ledger consulted. `git diff --stat`
reviewed — broad by necessity.

## Out of scope

Prompt-text authoring/tuning; the promotion decision + volume run; the F3/F4/F5 discriminator
freezes (separate specs); **the annotation / adjudication / evaluation-result subsystem** — κ
computation and its pre-adjudication rule, the label ledger's concurrency/checkpoint/genesis
rules, annotator assignment + overlap artifacts, and the precision-result artifact that binds
denominator/numerator/undefined-empty behavior — all moved to a **separate annotation-freeze
spec** (Codex v10 #7/#15); this spec certifies only the finder that produces the queue
(`label_record` was removed from the schema in v12); F2 held-out generalization; case-control
control-matching; git history cleanup; recall computation; PKI/key-rotation/time-anchor/
retroactive-revocation and runtime import sandboxing (per the threat model); and the **F3-DI2
preregistration amendment**
— it is a **dataset-publication** dependency, referenced by `release_attestation.prereg_amendment_sha256`
and required before a dataset is *published*, but it is **not** a prerequisite for building the
freeze infrastructure or running the candidate batch (Codex v7 #13 — pushed back on folding
prereg governance into this spec).

## Verification command

```
cd citation_repair_F1_handoff
PYTHONPATH=. ../.venv_cre/bin/python -m pytest cre/f1/test_f2_recall_guard.py -q
PYTHONPATH=. ../.venv_cre/bin/python -m pytest cre/f1 -q
```

Colab records its own environment identity + dependency-lock hash separately. The candidate
batch runs in Colab in `candidate` mode under the CONFIG; that cell is generated under the
Colab-cell standard (runtime estimate, Drive-first, restart after push, reinstall `rapidfuzz`).

---
---

> **HISTORICAL — NON-NORMATIVE (do not implement from below).** The disposition sections that
> follow are a per-round audit trail (Codex v5–v10). They contain **superseded hashes, sizes, and
> field names** from earlier revisions (e.g. old schema hashes paired with old byte counts). The
> **only** normative pins are in "Normative references" at the top (`b42fae74…` schema, `263c6e2e…`
> methodology); the only normative structure is the schema file + the §12 SV table. Read the
> history for context, not for values.

## Codex v5 NO-GO — dispositions (scoped to single-author integrity)

**#1** self-referential digests → envelope pattern, filename digests not fields (Vocab, all
artifacts). **#2** promotion policy contradiction → one policy: committed envelope in trusted
repo, signed tag optional (§4; threat model). **#3** trust-store binding → **dropped** (no PKI
under this threat model; single recorded fingerprint, repo is the trust root). **#4** signing
time/backdating → **dropped**; revocation blocks future releases only, git history is the
monotonic source (§5; threat model). **#5** runtime identity → invocation path removed from
CONFIG; `runtime_profile` with dep-lock + inventory hashes; BATCH records the command; lockfile
to be created (Input 7, §2). **#6** chain canonicalizer → `chain_hash_version="canon_v1"`, new
JCS record canonicalizer, legacy rejected/bridged (§3, §8). **#7** injected-transport bypass →
internal source-pinned adapter, injected transport development-only, negative test (§6). **#8**
one mode for whole pipeline → `run_finder_frontend` + per-component modes; downstream held under
finder release (§6). **#9** contamination → committed selection artifact in genesis, natural-
provenance fields, append-only ledger every release consults, dual-key
(citation_id + source_occurrence_fingerprint) + citing-paper-wide exclusion (§8b). **#10**
funnel overload → `runner_accepted` rename, exact disposition precedence, validator-derived
eligibility (§9). **#11** self-containment → full matrix + guardrails inline; decisions doc
supplied + pinned (canonical no-newline `263c6e2e…`); schema file pinned `eab89727…`. **#12**
illustrative schemas → machine-valid JSON Schema file (draft 2020-12), no comments/ellipses,
explicit states/enums/self-hash exclusions/git-OID prefixes/param states/retry+idempotency
grammar. **#13** manifest unprotected → content-addressed + committed attestation binding six
per-row hashes (§9). Secondary: four-rule vocab label; full artifact-set list; decoded-body
bytes + header allowlist + secret redaction; write-ahead attempt id + idempotency key; provider
model-id verification; pre-import module verification; temp-file/ownership/link-count/TOCTOU
rules; as-of reportability fields; revoked-config-never-repromoted; `citation_id` grammar;
pinned renderer + DOM-diff ignoring nondeterministic attributes — all folded into §1–§10 and
the schema file. Q1–Q24 answered inline; the adversarial-only questions (trust policy, key
rotation, signing-time anchor, retroactive revocation) are out of scope per the chosen threat
model.

## Codex v6 NO-GO — dispositions

**#1** schema hash wrong → re-pinned to `cdfcfb13…` over the canonical no-trailing-newline
bytes; meta-validated draft 2020-12. **#2** root enforced nothing → artifacts moved under
`$defs`, root is discriminated `oneOf` on `artifact_type`; verified it rejects `{}`/`null`/`7`
and accepts a valid artifact (Refs, schema). **#3** missing artifact schemas → added
candidate_manifest, run_state_manifest, wal_entry, exclusion_ledger_entry, exclusion_checkpoint,
release_attestation, module_manifest, plus review_call state machine; evidence snapshot/codebook
declared opaque byte artifacts governed by content hash, not JSON Schema. **#4** review_call
impossible states → discriminated `oneOf` with `additionalProperties:false`, typed per state
(§6, schema). **#5** WAL → one typed `wal_entry` per transport attempt (schema). **#6**
idempotency + crash → domain-separated key; `indeterminate` quarantines, no silent retry;
provider-recovery integration declined as out of scope (§6). **#7** stage cross-contamination →
split `claim_extract_stage_config`/`coverage_stage_config`, per-param typed values, timeouts +
backoff caps, tool-schema/hash consistency, dated-snapshot pattern, base_url pinned (schema).
**#8** prompt contract → name-dependent slot sets (`citing_sentence` required for extraction;
coverage slot set to CONFIRM against `band_prompts.py`), full template hashes as acceptance
constants (schema, Repo target). **#9** BATCH in-progress → BATCH final-only; separate
`run_state_manifest` (§3′). **#10** git not monotonic → canonical repo identity + ff-only
non-deletable release ref + fetch/clean-worktree/reachability + ancestry-ordered revocation
(Threat model). **#11** unverifiable fingerprints → renamed `recorded_by` identity metadata
(§4, schema). **#12** stale current-state in immutable records → three layers: run facts /
release attestation / computed current (§10). **#13** reportability relationships → semantic
validator with equations + negative fixtures (§10, §12). **#14** ledger integrity → seq +
prev + entry digest + checkpoint + latest-reachable proof + fail-closed (§9). **#15**
denominator corruption → canonical order + dual-key uniqueness + min_size/coverage checks (§9).
**#16** fingerprint formula → `SHA256(canon_v1({source_xml_sha256, ref_content_utf8,
extraction_contract_version}))` with components stored (§9, schema). **#17** module manifest →
added; pre-import byte verification; **runtime sandboxing declined** as out of scope (§11,
Threat model). **#18** observed runtime → BATCH records observed profile + validator match +
structured argv/cwd (schema). **#19** decisions doc → **supplied** (`b6d03c…`) and to be
committed. **#20** release attestation → schema added binding manifest digest + canonical-ref
commit (§9, schema). Secondary schema defects: strict byte loader (dup-key/float-token),
semantic RFC 3339, excluded-item null hashes, item_key/pmcid/ref_id agreement, `%ZZ` rejection,
`provider_model_id` optionality + `model_mismatch`, typed retry attempts, inventory disposition +
eligibility, selection source fields, non-empty `cluster_id`, genesis preimage formula — all in
the schema + §12. ZD=Aston clarified (Threat model). Q1–Q23 answered inline.

## Two places I pushed back on Codex (per ZD)

1. **Runtime import sandboxing (#17 tail).** Declined — it defends against a hostile import, a
   threat excluded by single-author integrity. Kept the in-scope, well-motivated part
   (pre-import byte verification against the module manifest, which catches the real
   stale-checkout bug).
2. **Provider idempotent-replay/lookup integration (#6 tail).** Declined building it. Adopted
   the honest fallback: an ambiguous attempt is `indeterminate` → quarantine. A rare duplicate
   paid call is a cost annoyance, not a validity threat.

## Codex v7 NO-GO — dispositions

Fixed (real): **#1** doc hash → canonical no-newline `263c6e2e…` (8,938 B) supplied; schema
re-pinned `eab89727…`. **#2** unsatisfiable coverage intersection → `{claim_idx, call}`
`coverage_item` wrapper; **verified** an `ok` coverage record validates and `call_made:true +
not_run` is rejected. **#3/#4/#7** WAL → immutable event-sourced `wal_event` with per-attempt
status/body/headers/exception/model/finish + linkage; `retry_wal_event_shas` ordered unique
hashes; terminal states per-state typed. **#5** BATCH → `run_id` + structured `genesis_preimage`.
**#6** resume → `run_state_manifest` binds selection/candidate/genesis/WAL-tip/checkpoints/runtime.
**#8** prompt slots → **locked** (`CITING_SENTENCE`; `ATOMIC_CLAIM`,`EVIDENCE`) via
`prefixItems`+`items:false`; no "provisional". **#9** contamination → snapshot-independent
`occurrence_identity` + citing-paper-wide exclusion for **every** inspected calibration item.
**#10** stale imports → fresh-interpreter run + fail-closed if a trust-boundary module is already
loaded; `fresh_interpreter=true` recorded. **#11** trust root → compared to out-of-band trusted
constants. **#12** methodology conflict → explicit **supersession + amendment** note. **#14**
manifest → split into `annotation_release_manifest` (flagged + derived-eligible only). **#15**
git anchoring → explicit two-phase commit rule + descendant/reachability check. Additional:
`citation_id` regex fixed + round-trip; `system_message:"omitted"` forbids text/hash; numeric
bounds (temp 0..2, top_p ≤1, positive tokens); timeout/backoff cross-field checks;
`base_url` host allowlist; `retryable_exceptions` enum; `idempotency` = versioned
`CRE_FINDER_IDEMPOTENCY_V1` preimage; `run_id` uniqueness; `matches_config_runtime_profile` +
`valid_at_release` = const true; observed runtime records lock/lib identity; candidate==selection
correspondence; empty-ledger state; module role/path uniqueness; stimulus + label_record schemas
added; stale `ab1de8fc…` reference removed. **Pushed back:** #13 prereg amendment = publication
dependency, not a freeze prerequisite (Out of scope); runtime import sandboxing stays out (#10
tail). Q1–Q28 answered inline.

## Where I pushed back on Codex (per ZD — "it's not always right")

1. **Runtime import sandboxing** (v6 #17 / v7 #10 tail) — declined; defends a hostile import
   (excluded threat). Kept fresh-interpreter + pre-import byte verification (the real
   stale-checkout fix).
2. **Provider idempotent-replay integration** (v6 #6) — declined; ambiguous attempt →
   `indeterminate` → quarantine. A rare duplicate paid call is cost, not a validity threat.
3. **F3-DI2 prereg amendment as a freeze prerequisite** (v7 #13) — declined; it gates dataset
   *publication*, referenced by the release attestation, not the finder-freeze build.

## Codex v8 NO-GO — dispositions

Fixed (real): **#1** 16 dangling `$ref`s → `stage_base_props` given a real `properties` member;
schema re-pinned `64673f33…`; **validated end-to-end** — 0 dangling refs, every artifact type
instantiates (config-with-stage-configs, the blocker, now passes). **#2** manifest not tied to
its run → `source_run_hash` + `source_review_dump_sha256` + `source_selection_hash` + per-row
`review_record_sha256`/`seq`; validator proves each row ∈ that release-mode/formal BATCH. **#3**
mode/promotion state → BATCH + run-state bind `execution_mode`, `promotion_payload_sha256_at_start`,
`exclusion_checkpoint_sha256_at_start`, `canonical_ref_commit_observed`. **#4** review chain →
`seq`/`prev_record_sha256`/`record_sha256` in each review record (tip recomputable). **#5** WAL →
`transport_failed` event, global `wal_seq` + per-attempt sequencing + terminal linkage,
per-event-type required fields, and a **send-boundary** retry rule (no retry once request bytes
may have left). **#6** idempotency → literal `idempotency_preimage` object hashed by
`canon_v1`. **#7** decimal-vs-JSON-float → supplied `temperature`/`top_p` prohibited
(omitted/provider_default only), safe-int bounds. **#8** stimulus → embeds `codebook_content`,
closed `worksheet_schema`, unique `label_space`, contiguous atomic-claim idx; recursive leakage
audit. **#9** self-exclusion chronology → checkpoint N → run → exposure → N+1 → PROMOTION pins
N+1 (§8b). **#10** `normalized_ref_key` → versioned `occurrence_identity` contract (over-exclusion
via shared-work identity is conservative/safe). **#11** bootstrap → stdlib-only launcher,
parent-verified, in the module manifest. **#12** `observed_runtime_sha256` → reusable
`runtime_observation` $defs embedded in both run-state and BATCH. **#13** stale acceptance rows →
corrected (retry_attempts→`retry_wal_event_shas`, source_occurrence_fingerprint→`occurrence_identity`,
"Dataset manifest"→`annotation_release_manifest`, "no duplicate call"→indeterminate/quarantine,
removed the input-eligibility row). P1s: `label_record` identity includes `annotator_id` +
adjudication kind; label ∈ label_space (semantic); header allowlist enum; git-OID type-specific
(`git_commit_oid`); empty-denominator + empty-ledger states defined; loader/validator versioned
via module hashes. **Pushed back (unchanged):** #13 prereg = publication dependency; runtime
import sandboxing out of scope.

## Where I pushed back on Codex (per ZD — "it's not always right")

1. **Runtime import sandboxing** — declined; defends a hostile import (excluded threat). Kept
   fresh-interpreter + pre-import byte verification (the real stale-checkout fix).
2. **Provider idempotent-replay integration** — declined; ambiguous attempt → `indeterminate`
   → quarantine. A rare duplicate paid call is cost, not a validity threat.
3. **F3-DI2 prereg amendment as a freeze prerequisite** — declined; it gates dataset
   *publication* (referenced by the release attestation), not the finder-freeze build.
4. **Over-exclusion is a defect (v8 #10 subtext)** — declined; two bibliography entries for the
   same work sharing an `occurrence_identity` *over*-excludes, which is the safe direction for a
   contamination guard. The denominator still keys on distinct `citation_id`s.

## Codex v9 NO-GO — dispositions

Fixed (real): **#1** duplicate `$comment` keys (violated own dup-key rule) → merged; byte-level
dup-key check added to validation (0 dups). **#2** stale byte size → recomputed (60,059 B,
`3475a7bd…`). **#3** `v8` identity on a breaking change → `$id` + all `schema_version` → `v9`.
**#4** PROMOTION now pins `post_exposure_exclusion_checkpoint_sha256` (N+1) + `predeclared_exposure_shas`
(exposure set fixed before the UI reveals it). **#5** WAL → closed per-event-type branches; a
`sent` requires `sent_boundary_crossed:true`, `prepared`/`transport_failed` require false,
`indeterminate` requires a prior durable `sent`. **#6** terminal `transport_error` review-call
state added; `ambiguous_post_send` removed from `timeout` (maps to `indeterminate`). **#7**
`response_persisted` provider metadata now nullable (a 4xx body has none); the `ok` review-call
carries the model audit. **#8** `runtime_observation` is a shared `$ref` in BATCH and run-state.
**#9** `occurrence_identity` versioned (`occ_id_v1` + key-type + `normalization_contract_sha256`
+ alias rule). **#10** sidecar removed (review-record chain is the recovery structure; empty-chain
+ seq0→genesis defined). **#11** semantic validator → rule table with IDs/predicates/failure
codes (§12). Additional: git-OID type-specific (`git_commit_oid`), two-phase **immediate-parent**
rule, header allowlist + credential rejection (SV-090), `idempotency_preimage` persisted in
`prepared`, empty-denominator → precision undefined, `label_record` `record_kind` + adjudication
≥2 distinct annotators, worksheet choice rules. **κ (Codex v9 P1):** flagged for the methodology
doc — κ must be pre-adjudication, not on reconciled labels. **Pushed back:** semantic-validator
*fixtures* are a build artifact; prereg + import sandboxing unchanged.

## Where I pushed back on Codex (per ZD — "it's not always right")

1. **Runtime import sandboxing** — declined; defends a hostile import (excluded threat). Kept
   fresh-interpreter + pre-import byte verification (the real stale-checkout fix).
2. **Provider idempotent-replay integration** — declined; ambiguous attempt → `indeterminate`
   → quarantine. A rare duplicate paid call is cost, not a validity threat.
3. **F3-DI2 prereg amendment as a freeze prerequisite** — declined; gates *publication*, not the
   build.
4. **Over-exclusion is a defect** — declined; shared-work `occurrence_identity` *over*-excludes,
   the safe direction for a contamination guard; the denominator still keys on distinct `citation_id`.
5. **Enumerating validator fixtures in the spec (v9 #11)** — declined; the spec fixes each rule's
   ID/predicate/failure code (§12); the fixtures are test code written with the validator.

## Status (honest read for ZD — no convergence claim)

Eleven rounds. I'm not going to call this "ready to build" — the times I did, the artifact didn't
match the claim. What I *can* say precisely, because I executed the checks this round:
the schema loads, meta-validates, has no duplicate keys and no dangling refs, and its
failure-mode branches (WAL, worksheet) reject the bad instances and accept the good ones. That is
verified, not asserted.

Two things are now clear and I owe them to you straight:

1. **My execution matters more than Codex's.** Three rounds running I introduced or mis-claimed
   real defects (dangling refs, dup keys, prose-only "fixes"). The fix wasn't more review — it
   was running a validator every round, which I now do. Any future revision must ship with a
   machine-generated conformance report, or the claims are worth nothing.
2. **The loop is not converging by adversarial review, and it won't.** Codex is correct on the
   mechanics but is also steadily annexing adjacent systems (it's now specifying the entire
   annotation/evaluation subsystem). I cut that back to scope this round. But an open-ended
   formal-verification adversary on a solo research tool has no fixed point.

**Recommendation, firmly:** stop the mediation loop. Hand v11 to Claude Code and build the
`semantic_validator_v1` + a conformance test suite; the remaining Codex items are either
out-of-scope (the annotation subsystem, now a separate spec) or exactly the class of thing a
test suite closes during implementation. If you want a final Codex touch, bound it to two
mechanical questions — "does the schema load and validate all 19 artifacts?" (19 was the v11
count; 18 after the v12 `label_record` removal) and "does the
SV-table map 1:1 to validator rules with a fixture each?" — not another open review. Still yours
to supply at candidate-config time: model snapshot, codebook, evidence policy, freeze criterion,
the two F3 `citation_id`s, the lockfile. And the methodology doc's κ line needs the one-line fix
(pre-adjudication, not on the reconciled labels) before annotator instructions.