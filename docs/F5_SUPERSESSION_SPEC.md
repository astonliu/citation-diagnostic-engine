# F5 — Supersession detector — implementation spec (for Claude Code)

## Objective
Build the **F5 (stale/superseded) detector** as a self-contained leaf that produces a frozen-engine
`TemporalAssessment`, in **development mode only** (offline, injected seams, `reportable=False`,
`deploy_path_a=False`). Current behavior: F5 is unbuilt; the engine already has the decision seam. Target
behavior: `cre/f1/f5_supersession.py` + `cre/f1/test_f5_supersession.py` exist, all offline, engine
untouched. **Design authority:** `F5_BLUEPRINT.md` (canonical sha256
`663edd690796155bb260e42117c93704b4c58698a0ef5d287cf931f45a04fdea`). Where this spec is terse, the
blueprint section cited is binding.

**Scope gate (read first).** This authorizes the **development-mode build**, not reportable/deployed use.
Advisor locks (comparability v1, independence AND/OR combinator, tier mapping, larger-`n` threshold,
confidence floor) are **NOT frozen** — treat every locked threshold as a policy parameter that, while
unfrozen, **fails closed** (yields `UNJUDGEABLE`, `reportable=False`). Do **not** derive a reportable
F5/Path-A verdict; do **not** deploy Path A. (Blueprint §13, §21.)

## Files
- **CREATE** `citation_repair_F1_handoff/cre/f1/f5_supersession.py` — the detector + `F5Policy` + typed
  records + injected-seam type aliases + `validate_f5_record`.
- **CREATE** `citation_repair_F1_handoff/cre/f1/test_f5_supersession.py` — offline unit tests, injected fakes.
- **DO NOT MODIFY** `cre/f1/judgment_engine.py`, `cre/f1/judgment_band.py`, `cre/f1/band_prompts.py`
  (`coverage_v2`), or anything F2.

## Change (file + function level)
In `cre/f1/f5_supersession.py`:

1. **`F5Policy` (frozen dataclass)** — fields per blueprint §13: `mode ∈ {discovery, deployment}` (=discovery),
   `path_a_rule ∈ {all_must_fire, any_sufficient}`, `date_gap_years` (=2), `tier_rule`,
   `require_attestation_for_path_a` (=True), `attestation_types` (={major_guideline_revision,
   systematic_review, meta_analysis}), `independence_rule`, `comparability_rule`, `confidence_floor`
   (low in discovery), `eoc_caps_at_path_b` (=True), `deploy_path_a` (=False), `policy_version`. Any
   field whose value depends on an unfrozen Roberts lock is `None`/unfrozen → the derivation using it
   **fails closed** to `UNJUDGEABLE`.

2. **Injected seam type aliases** (Callables; no network/paid I/O in module or tests) — signatures exactly
   per blueprint §5: `retrieve_superseding_candidates(cited_meta, claim, *, after_date, as_of_date) ->
   RetrievalResult`; `fetch_comparability_source(work_id, *, as_of_date) -> ComparabilitySource`;
   `check_formal_notice(work_id, *, as_of_date) -> NoticeStatus`; `classify_evidence_tier(work_meta) ->
   EvidenceTier`; `find_supersession_attestation(cited_meta, claim, replacement_work_id, *, as_of_date) ->
   Optional[Attestation]` (returns `attestation_conclusion_span`); `judge_contradiction(cited_source,
   candidate_source, claim) -> ContradictionJudgment` (model emits the three relation axes + two spans +
   two directions + magnitude + confidence; **code**, not the model, derives `comparability_decision` and
   the engine booleans per §18a.6).

3. **Typed records** — `RetrievalResult`, `CandidateWork`, `ComparabilitySource`, `NoticeStatus`
   (`notice_kind` + `notice_resolution` + `date`), `Attestation`, `ContradictionJudgment`,
   `CandidateAssessment`, and the two-level `F5Record` — fields exactly per blueprint §5, §10, §18a.5.
   Optional fields use real `Optional[...]` types (blueprint §10).

4. **`comparability_decision` derivation** — deterministic function of (`claim_match`, `outcome_relation`,
   `population_relation`), the exact §18a.6 order: (1) any hard mismatch ⇒ `not_comparable`; (2) else any
   uncertainty ⇒ `uncertain`; (3) else ⇒ `comparable`. The model never returns the combined decision.

5. **`decide_f5(...)` (main)** — for each `SUPPORTED` claim: run retrieval → per-candidate
   `check_formal_notice` + `judge_contradiction` + comparability → build `CandidateAssessment`s →
   apply the **detector contract** (§4) and **retrieval-adequacy rule** (§5, §9-1) → select the
   deterministic contradiction representative and (if eligible) the Path-A replacement (§9-11) → emit a
   **`TemporalAssessment`** for `decide_judgment`. Map: `newer_work_id = selected_contradiction_work_id`;
   `same_claim_or_outcome`/`comparable_population`/`evidence_spans`/`confidence` from the **selected
   contradiction** (null when none). Compute `discovery_disposition` and `f5_path` (stays `B` while
   `deploy_path_a=False`, even when `path_a_eligible=True`). Fail-closed strict-JSON parsing: malformed/
   off-enum → `ValueError` (orchestrator quarantines); well-formed-but-unknown → `UNJUDGEABLE`.

6. **`validate_f5_record(record)`** — replay guard: re-derive route + `comparability_decision` + engine
   booleans from stored facts + `policy_version`; raise on drift (mirror `validate_f7_record`).

## Acceptance matrix (offline; injected fakes; `deploy_path_a=False`, `mode=discovery`)
| Fixture | Expected `TemporalState` / field |
|---|---|
| SUPPORTED claim; independent newer paper, `outcome_relation=same`, `population=equivalent`, `f8_notice=False`, both spans verbatim, confidence≥floor | `QUALIFYING_CONTRADICTION` (engine → `F5`); `discovery_disposition=surface` |
| Same, but `outcome_relation=not_same` | `NO_QUALIFYING_CONTRADICTION` (candidate `do_not_surface`) |
| Same, but comparability `uncertain` | `UNJUDGEABLE`; may `surface` |
| Independence **false** (same-cohort re-analysis) | `not_F5` (never Path B) |
| Independence **unknown** | `UNJUDGEABLE` |
| Empty retrieval / `status=failure` / `adequacy=empty` | `UNJUDGEABLE` (never confident negative) |
| Candidate set with ≥1 unjudgeable and none qualifying | `UNJUDGEABLE` |
| Adequate + nonempty + fully judgeable, all candidates nonqualifying | `NO_QUALIFYING_CONTRADICTION` |
| Qualifying contradiction **+ bound SR/MA attestation + ≥2yr + equal-or-higher tier** | `QUALIFYING_CONTRADICTION`; `path_a_eligible=True`; **`f5_path=B`** (deploy off); `path_a_deployed=False` |
| Attestation present but **no** detected contradiction/replacement | not F5, not Path A |
| Candidate with `candidate_notice_kind≠none` | unjudgeable audit row; never a valid replacement |
| Cited claim not `SUPPORTED` | detector does not emit `QUALIFYING_CONTRADICTION` (would trip engine guard) |
| Two qualifying candidates | deterministic pick (tier → most-recent → `work_id`); larger-`n` step skipped (unfrozen) |
| Malformed model JSON / off-enum | `ValueError` (quarantine) |
| `validate_f5_record` on a tampered stored record | raises (drift) |

## Guardrails (do NOT change)
- **Frozen:** `judgment_engine.py`, `judgment_band.py` (full digests and their measurement
  commit in [Pinned digests](#pinned-digests) below), `coverage_v2`/`band_prompts.py`
  (presence-only lock), F2. F5 is a leaf that only *produces* a `TemporalAssessment`; it
  never edits the engine.
- **No network/paid I/O** in the module or its tests; all retrieval/model access is injected and faked.
- **SUPPORTED-only** F5 target; `WEAKER_STRENGTH` is a documented deferred limitation (engine L397). The
  additive pinning test for L397 is a **separate, later, authorized** change — not in this spec.
- **Fail-closed on unfrozen locks:** no reportable F5/Path-A verdict; `deploy_path_a=False`; discovery
  outputs are non-reportable candidate-F5 + hypothetical eligibility only.
- **Claude never assigns semantic labels / curates gold;** naturally-occurring data only; no synthetic
  calibration/evaluation data (synthetic *unit-test fixtures* are fine).
- **Path-based loading:** no `__init__.py` in `cre/`; after any push, restart the Colab session.
  `author_match` tri-state tested with `is False`. Targeted amendments only — no overwrite of registered
  content.

## Regression guards
- Full `cre/f1` stays green: `test_judgment_engine`, `test_judgment_band`, `test_band_prompts`,
  `test_f3_provenance`, `test_f4_strength`, `test_f7_entity`, `test_judgment_run` (683 with optional deps
  **before** the F5 additions), **plus** the new `test_f5_supersession`.
- F2 `WRONG_PAPER` guard PMIDs unaffected (they never enter the band): `31665581`, `16639420`, `18152150`,
  `27665045`, `25750229`, `32355637`, `22926653`.
- `judgment_engine.py` and `judgment_band.py` sha256 unchanged **relative to the measurement
  commit recorded in [Pinned digests](#pinned-digests)** — see the drift note there.

## Pinned digests

Both pins were written as eight hex characters, which is not collision-resistant and does not
identify a file. Completed 2026-08-14 during Round 1 remediation. **The digests are recorded
against the commit at which they were measured, not back-filled with today's value** — writing
the current digest under the original truncated pin would assert a freeze that never held.

| file | pinned sha256 | measured at |
|---|---|---|
| `cre/f1/judgment_engine.py` | `671de1e55dc62614d0e8e02a7dfe4fc846910929263a52471ede7b90e29587a1` | `ff940fc207359ba730df13f4c83105350736cf07` (2026-07-16) |
| `cre/f1/judgment_band.py` | `7d81e5e088b68c358e6c9b0f82d4025a431aef351508318d5a080adb99bfc8ee` | `ff940fc207359ba730df13f4c83105350736cf07` (2026-07-16) |

**DRIFT — the "unchanged" guardrail above no longer holds.** Measured 2026-08-14 at
`8e1737163b9a43cc0f445d238fe04406a659c6f6`, both files differ from their pins:

| file | current sha256 |
|---|---|
| `cre/f1/judgment_engine.py` | `b8567a14b7ea8233027fe61b729f6b2a101cad60520df7a073a6c8b1121eebd4` |
| `cre/f1/judgment_band.py` | `8f7c47b76e510e93c0d7f5b66fbfdd8ac496dfb0a380daa099ce7d43193fe0a8` |

Neither file was modified by the Round 1 remediation — the drift predates it and was
invisible while the pins were truncated. Whether the intervening edits were authorized, and
whether the pins should be re-frozen at the current tip, is **ZD's call and is not decided
here**; recorded in CONTRADICTIONS.

## Definition of done
- `python -m pytest cre/f1 -q` green (expect prior 683 with optional deps + the new F5 tests).
- The acceptance matrix above verified on fakes; `validate_f5_record` replay green.
- No diff to any frozen file (confirm the two sha256s); `deploy_path_a=False` and `reportable=False` by
  construction; module imports no network client.

## Out of scope
- Any engine / `judgment_band` / `coverage_v2` / F2 change.
- Live/paid retrieval; wiring into `judgment_run.py` (separate follow-up).
- Formal/reportable derivation, Path-A **deployment**, and the **values** of the advisor-locked thresholds
  (comparability v1, independence combinator, tier mapping, larger-`n`, confidence floor) — those wait for
  the Roberts freeze; the module carries them as unfrozen policy params that fail closed.
- The MeSH/scite/PubMed live backends (they are the injected seams' production wiring, built later in Colab).

## Verification command
```bash
cd citation_repair_F1_handoff && python -m pytest cre/f1 -q
```
