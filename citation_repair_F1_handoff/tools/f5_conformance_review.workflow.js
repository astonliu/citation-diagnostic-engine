// Canonical, corrected F5 conformance-review workflow.
//
// Fixes the aggregation SHAPE BUG that made the first F5 run report
// `confirmed: []` while its journal actually held 9 CONFIRMED + 2 REFUTED
// verdicts: the verify pipeline stage returned a BARE ARRAY for dimensions WITH
// findings but a `{key, verified: []}` OBJECT for dimensions WITHOUT, so the
// downstream `.flat()/.flatMap(r => r.verified)` silently dropped every verdict.
//
// The stage now returns a UNIFORM `{key, findings, verified}` object, the summary
// is built from that uniform shape, and a fail-loud reconciliation guard throws if
// the journal's raised/confirmed/refuted/unresolved counts do not reconcile
// (raised === confirmed + refuted + unresolved, and never a non-empty journal with
// an empty tally). Run tools/workflow_review_reconcile.py on the run journal for an
// independent, regression-tested cross-check of the returned summary.
export const meta = {
  name: 'f5-conformance-review',
  description: 'Adversarially review the F5 supersession detector; reconcile confirmed/refuted fail-loud',
  phases: [
    { title: 'Review' },
    { title: 'Verify' },
  ],
}

const MODULE = '/Users/kamachi/citation-repair-engine/citation_repair_F1_handoff/cre/f1/f5_supersession.py'
const TESTS = '/Users/kamachi/citation-repair-engine/citation_repair_F1_handoff/cre/f1/test_f5_supersession.py'
const ENGINE = '/Users/kamachi/citation-repair-engine/citation_repair_F1_handoff/cre/f1/judgment_engine.py'
const SPEC = '/Users/kamachi/Documents/CitationRepairEngine/F5_SUPERSESSION_SPEC.md'
const BLUEPRINT = '/Users/kamachi/Documents/CitationRepairEngine/F5_BLUEPRINT.md'

const COMMON = `You are auditing the citation-repair-engine F5 (stale/superseded) discriminator.

READ in full before judging:
- Implementation: ${MODULE}
- Its tests: ${TESTS}
- Frozen decision engine (MUST NOT be modified; the module only PRODUCES a TemporalAssessment for it): ${ENGINE}
- Implementation spec (binding): ${SPEC}
- Design blueprint (binding where the spec is terse): ${BLUEPRINT}

DEVELOPMENT-MODE, offline, injected-seam build. Nothing reportable; Path A never deployed (deploy_path_a hard-gated off). Report ONLY genuine in-dimension defects with a concrete file:line and a failing input->wrong output scenario; empty array if clean.`

const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false,
        required: ['severity', 'file', 'line', 'summary', 'failure_scenario'],
        properties: {
          severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
          file: { type: 'string' }, line: { type: 'integer' },
          summary: { type: 'string' }, failure_scenario: { type: 'string' },
          spec_reference: { type: 'string' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['verdict', 'reasoning'],
  properties: {
    verdict: { type: 'string', enum: ['CONFIRMED', 'REFUTED', 'UNCERTAIN'] },
    reasoning: { type: 'string' },
    corrected_severity: { type: 'string', enum: ['blocker', 'major', 'minor'] },
  },
}

const DIMENSIONS = [
  { key: 'engine-contract', prompt: `${COMMON}\n\nDIMENSION: the produced TemporalAssessment can NEVER trip the frozen engine contract (QUALIFYING only for a SUPPORTED claim, nonblank newer_work_id, both engine booleans True, nonempty NONBLANK verbatim evidence_spans, f8_notice False).` },
  { key: 'comparability-18a6', prompt: `${COMMON}\n\nDIMENSION: derive_comparability_decision implements Sec 18a.6 order exactly (hard-mismatch > uncertainty > comparable); code (not the model) derives it; engine booleans map correctly.` },
  { key: 'retrieval-adequacy', prompt: `${COMMON}\n\nDIMENSION: confident NO_QUALIFYING only when status=ok AND adequacy=adequate AND nonempty AND all candidates judgeable-nonqualifying; empty/failed/partial/inadequate/any-unjudgeable hold UNJUDGEABLE; a qualifier wins under partial.` },
  { key: 'path-a-gate', prompt: `${COMMON}\n\nDIMENSION: Path A hard-gated off (deploy_path_a rejected); conjunctive gate (bound admissible attestation with source-validated span, equal-or-higher tier, >=date_gap); EoC caps at B; deterministic selection; attestation only for qualifying candidates.` },
  { key: 'independence', prompt: `${COMMON}\n\nDIMENSION: _assess_independence fails closed at the unfrozen Lock-D combinator; same-cohort (any iterable) -> not_independent -> not_F5 never Path B; unknown -> held; disjoint -> independent.` },
  { key: 'failclosed-replay', prompt: `${COMMON}\n\nDIMENSION: no network import; malformed/off-enum JSON raises; validate_f5_record re-derives comparability + engine booleans + f5_path + policy versions and recomputes sha last; no tamperable un-re-derived field.` },
  { key: 'policy-enforcement', prompt: `${COMMON}\n\nDIMENSION: validate_f5_policy rejects deploy_path_a=True and any non-implemented value of path_a_rule / tier_rule / independence_rule / comparability_rule, so a record can never claim a config the code did not apply.` },
  { key: 'matrix-coverage', prompt: `${COMMON}\n\nDIMENSION: every spec acceptance-matrix row and blueprint Sec 9 edge case is covered by a test whose assertion actually establishes the requirement; flag weak/misnamed tests and uncovered rows.` },
]

const reviewed = await pipeline(
  DIMENSIONS,
  d => agent(d.prompt, { label: `review:${d.key}`, phase: 'Review', schema: FINDINGS_SCHEMA })
        .then(r => ({ key: d.key, findings: (r && r.findings) || [] })),
  // SHAPE FIX: this stage ALWAYS returns the same {key, findings, verified} object
  // shape -- for zero-finding dimensions AND for dimensions whose findings were
  // verified -- so the downstream aggregation reads one uniform shape.
  res => {
    const items = res.findings.map((f, i) => ({ ...f, dim: res.key, idx: i }))
    if (!items.length) return { key: res.key, findings: res.findings, verified: [] }
    return parallel(items.map(f => () =>
      agent(`${COMMON}

A "${f.dim}" reviewer filed against ${f.file}:${f.line}:
SUMMARY: ${f.summary}
FAILURE SCENARIO: ${f.failure_scenario}
${f.spec_reference ? 'SPEC REF: ' + f.spec_reference : ''}

Adversarially VERIFY. Read the code + cited spec/blueprint, construct the concrete input, trace execution. Default REFUTED if you cannot reproduce the exact failure or the behavior is spec-correct (intentional fail-closed hold, deferred/unfrozen-lock, or development-mode-by-design). CONFIRMED only if the failure genuinely occurs and violates the spec/blueprint/engine contract.`,
        { label: `verify:${f.dim}:${f.idx}`, phase: 'Verify', schema: VERDICT_SCHEMA })
      .then(v => ({ finding: f, verdict: v }))
    )).then(verified => ({ key: res.key, findings: res.findings, verified: verified.filter(Boolean) }))
  }
)

// --- Aggregate from the UNIFORM shape --------------------------------------
const raised = reviewed.reduce((n, r) => n + r.findings.length, 0)
const pairs = reviewed.flatMap(r => r.verified)          // [{finding, verdict}, ...]
const tally = { CONFIRMED: 0, REFUTED: 0, UNCERTAIN: 0 }
let offVocab = 0
for (const p of pairs) {
  const k = p && p.verdict && p.verdict.verdict
  if (k in tally) tally[k]++
  else offVocab++
}
const confirmedCount = tally.CONFIRMED
const refuted = tally.REFUTED
const unresolved = tally.UNCERTAIN
const verifiedTotal = confirmedCount + refuted + unresolved

// --- Reconciliation logging + fail-loud invariants -------------------------
log(`[review-reconcile] raised=${raised} confirmed=${confirmedCount} refuted=${refuted} ` +
    `unresolved=${unresolved} off_vocab=${offVocab} verify_pairs=${pairs.length}`)
if (offVocab > 0) {
  throw new Error(`review aggregation: ${offVocab} off-vocabulary verdict(s) (schema drift)`)
}
if (raised >= 1 && verifiedTotal === 0) {
  throw new Error(`review aggregation: ${raised} finding(s) raised but 0 verdicts tallied ` +
                  `-- refusing to return a silently empty summary`)
}
if (verifiedTotal !== raised) {
  throw new Error(`review aggregation does not reconcile: raised=${raised} but ` +
                  `confirmed+refuted+unresolved=${verifiedTotal}`)
}

const order = { blocker: 0, major: 1, minor: 2 }
const confirmed = pairs
  .filter(p => p.verdict && p.verdict.verdict === 'CONFIRMED')
  .map(p => ({
    dimension: p.finding.dim,
    severity: p.verdict.corrected_severity || p.finding.severity,
    location: `${p.finding.file}:${p.finding.line}`,
    summary: p.finding.summary,
    failure_scenario: p.finding.failure_scenario,
    spec_reference: p.finding.spec_reference || '',
    verify_reasoning: p.verdict.reasoning,
  }))
  .sort((a, b) => (order[a.severity] ?? 3) - (order[b.severity] ?? 3))

return {
  confirmed_count: confirmedCount,
  refuted_count: refuted,
  unresolved_count: unresolved,
  raised,
  confirmed,
  reconciliation: { raised, confirmed: confirmedCount, refuted, unresolved },
}
