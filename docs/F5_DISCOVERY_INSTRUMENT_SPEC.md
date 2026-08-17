# F5 — supersession, v1 discovery instrument — implementation spec

**Base:** tip of `feat/f3-f7-semantic-validator-v1`. **Rev 3, 2026-08-12 — RETROSPECTIVE. BOTH COMMITS
HAVE LANDED. There is no pending work in this document.**

- Commit 1 (items 1, 2, 7) = `7634b94`, pushed. Suite 1666 passed / 12 skipped / 25 xfailed.
- Commit 2 (items 3, 4, 5, 6) = `926cdca`, pushed, branch in sync with origin. New files
  `f5_seams.py` (364 lines), `f5_discovery_queue.py` (136), `test_f5_seams_and_queue.py` (250);
  `judgment_run.py` +77; `f5_contradiction_prompt.py` amended.
- **Item 4's two landmines were already handled independently before rev 3 was written.**
  `f5_discovery_queue.py:105` sets `QUEUE_FILENAME = "f5_discovery_queue.jsonl"`; `assert_blind` is
  recursive by design and its `BLIND_FIELDS` adds `discovery_confidence` beyond what this spec asked
  for; `test_queue_is_blind_at_every_depth` and
  `test_the_queue_has_its_own_filename_not_the_shared_annotation_queue` both exist. Rev 3 records
  those constraints for the next spec that touches the queue; it does not ask for them.

**Everything below is offline-verified only.** The live half — the F5 judge actually returning sentence
ids, and the seams reaching real NCBI — has not been run. **Worktree:** `/Users/kamachi/cre-f3f7`.
**Authority:** ZD 2026-08-12.
**Evidence:** a read-only audit of `f5_supersession.py` at `de3e040` (`F5 SOURCES`/`f5_code_audit.md`,
exact file:line throughout) and a four-part literature review (`F5 LITERATURE` in the vault;
`claude/F5_LITERATURE_REVIEW_2026-08-12.md` in the project). Vault `CONTRADICTIONS` 44, 45, 46, 47.

**Read this first.** `f5_supersession.py` **ALREADY EXISTS** — 1,243 lines of strict, fail-closed
detector, complete within its own boundary and never once executed against real data. So do
`test_f5_supersession.py` (72 tests) and `test_adversarial_f5_supersession.py`. The module contains
**zero** `NotImplementedError` markers and nothing in it needs rewriting. **Do not create it, do not
overwrite it, do not restructure it.** What is missing is entirely *outside* it: six injected
callables that only test fakes have ever satisfied, and a prompt that does not exist. Every item below
either adds a **new, separate** file, adds a field, or fixes a defect the module's own strict xfails
already record.

Existing public API you must not clobber: `EvidenceTier`, `F5Policy`, `validate_f5_policy`,
`CandidateWork`, `RetrievalResult`, `ComparabilitySource`, `NoticeStatus`, `Attestation`,
`ContradictionJudgment`, `derive_comparability_decision`, `record_sha256`, `TemporalAssessorRun`,
`make_temporal_assessor`, `decide_f5`, `validate_f5_record`, and ~18 frozenset vocabularies.

**Read this second.** The coverage verdict record shape moved in each of the last three commits, and
the two paths now diverge deliberately. On the **full-text** path a verdict carries `evidence_spans`
(entries of `label` / `sentence_ids` / `text` / `span_source`), `span_status`, and
`response_parser_version = "strict_coverage_spanids_v4"`; the manifest gained
`manifest["evidence_selection"]` and `counts["evidence_span_not_found"]`. On the **default abstract**
path a verdict still carries the old single `evidence_span` string and no parser-version key. If any
part of F5 reads a coverage verdict, it must handle both shapes and must not "normalise" them.

### Glossary (terms used below)

- **Seam** — a callable passed into `make_temporal_assessor` / `decide_f5`. The module calls it and
  type-checks the return; it supplies no implementation.
- **OCEBM tier** — Oxford Centre for Evidence-Based Medicine level of evidence. A ranking of study
  designs (systematic review > randomised trial > cohort > case series > expert opinion). `EvidenceTier`
  at `f5_supersession.py:88-96` holds eight of them.
- **Attestation** — a later document that says in its own words that an earlier finding was superseded
  (a guideline revision, a systematic review). Path A's gate.
- **Scope mismatch** — two findings look like they disagree, but they are about different populations,
  doses, species, endpoints, or settings, so both can be true. The literature's dominant failure mode.
- **Jaccard overlap** — words in common divided by words in either. Used at ≥0.7 as the span alignment
  threshold that landed in `324e430`.

---

## Objective

F5 today is wireable and never wired: `judgment_run.py:477-483` calls `decide_f5` only when
`f5_seams` is passed, no production caller passes it, and every non-test path holds `UNJUDGEABLE`.
This spec makes F5 **run for the first time**, as a *discovery instrument* — high-recall candidate
generation feeding a human annotation queue, exactly as the module docstring (34-39) already
describes — not as a scored detector.

Target behaviour: `decide_f5` runs end to end on a real citation with real PubMed data, emits per-claim
`F5Record` audit rows, and writes a blind annotation queue from `discovery_disposition`. Nothing
autonomous ships. `deploy_path_a` stays hard-gated off; `reportable` stays `False`.

**Why an instrument and not a detector.** No claim-level supersession ground truth exists anywhere in
the literature — the largest hand-built sets are 396, 146, and 49 items, each built with two
independent reviewers plus adjudication. There is no gold set to score against and no budget to build
one before the deadline. An instrument that produces a defensible annotation queue is a real
contribution; a detector reporting an unvalidated F1 is not.

---

## Change / defect

### 1. The `judge_contradiction` prompt does not exist — write it, and make it SELECT spans, not retype them

`f5_supersession.py` declares `contradiction_prompt_version = "f5_contradiction_v1"` (line 160) and
asserts it is nonblank (230, 234). **There is no prompt text anywhere in either repo.** F5 is the only
discriminator in the ladder with a named prompt version and zero prompt text; F3, F4, F7,
`band_prompts` and `coverage_prompts_v3` all ship theirs in-module.

**The read-across that matters.** `_assess_candidate` verifies both spans verbatim against
`_source_text` (`714-720`); a span that does not appear turns the candidate into `span_unverifiable` →
`_UNASSESSABLE`. **That is the identical design error `324e430` just removed from the coverage judge**,
for the identical reason: asking a model to reproduce source text verbatim is the outlier design, and
FullCite measured prompt-based verbatim generation against post-hoc alignment at Snippet-F1
12.80% → 61.87%. Do not ship F5 with the defect the previous commit removed.

**Required:** the F5 judge selects sentence ids from `ComparabilitySource`, the same way the coverage
judge now selects from a section. Reuse `sentence_spans.py` — segment each populated
`ComparabilitySource` field (`abstract`, `methods`, `results`, `protocol`, `registry_record`) into
`s1..sN` with the field name as the label, render ids in the prompt, and resolve ids back to text
before the module's verbatim check runs. Keep the Jaccard ≥ 0.7 alignment fallback for drift. A span
that cannot be resolved is a **recorded miss**, not a quarantine — same rule as DEC-047.

**Prompt structure: decomposed, not one verdict.** The best credible contradiction number in the
literature is Xie et al. 2024 (PMID `38758667`) at **F1 0.799 (R 0.903 / P 0.716)** on ManConCorpus's
1,040 real pairs, using four sequential steps: synthesise the research question → extract each paper's
assertion → summarise consensus/controversy → generate open questions. Single-prompt verdicts
underperform it. Mirror that decomposition inside the prompt; the seam signature stays one call
returning one JSON object.

**The abstain option is worth ~7 points of recall.** Same paper: ternary assertions score R 0.903 /
P 0.716; forcing a binary decision drops recall to 0.834. The `uncertain` values already present in
`_CLAIM_MATCH` and `_OUTCOME_RELATION`, and `unclear` in `_POPULATION_RELATION`, are that option —
the prompt must make using them a first-class instruction, not a fallback.

**Do not few-shot from synthetic negations.** SciFact's REFUTES is an expert flipping a true claim's
direction; COVID-Fact's is one word swapped by a masked LM, which MultiVerS's own inspection found
produces a genuinely refuted claim "roughly a third of the time"; SCitance uses GPT-3.5 to negate
"changing as few words as possible." All three teach lexical polarity flipping. Real supersession looks
like two different effect estimates in two different populations. Worked examples must come from real
paper pairs.

**Contract constraint.** `_parse_contradiction` (`487-537`) is strict: one bare JSON object, exactly
the ten keys in `_CONTRADICTION_KEYS` (`480-484`), no extras, no duplicates, enum values on-list,
`confidence` a number in [0,1], `directional_contradiction` a real JSON bool. Item 2 changes that key
set; nothing else about the parser's strictness may relax.

### 2. Record WHICH scope axis fired — the dominant failure mode is currently invisible

The module already routes scope mismatch correctly: `population_relation` →
`derive_comparability_decision` (`388-409`) → `not_comparable` → non-qualifying. **The outcome is
right; the explanation is missing.** Today a run cannot answer "why did this pair not qualify," which
is the single question the annotation queue exists to support.

The literature says this is not a minor field. Rosemblat et al. 2019 (PMID `31473364`): of 2,236
candidate contradictory pairs, 1,226 (54.8%) were extraction errors, 952 (42.6%) had generic subjects,
**58 (2.6%) survived as apparent contradictions, and 4 looked genuine** — annotator κ 0.92 on that
sort. Three independent taxonomies converge on the same short axis list, so this is a checklist, not
open-ended reasoning:

`species_or_strain`, `population_subgroup`, `dose_or_duration`, `route_or_administration`,
`endpoint_definition`, `assay_or_study_design`, `clinical_setting`, `time_period_new_knowledge`,
`endogenous_vs_exogenous`, `none`, `unclear`.

**Required:** every candidate assessment records which axis (or `none`) explains a non-`comparable`
decision. **Where that value comes from is [CLAUDE'S CALL]** — an eleventh key on the contradiction
contract, a separate derived field, or a second cheap call. The constraints are: it must be recorded
per candidate in `candidate_assessments`; it must not change any existing routing decision; and if it
becomes an eleventh key, `_CONTRADICTION_KEYS` and its strict tests move together and the prompt
version bumps.

### 3. Implement the six seams — at deliberately unequal depth

All six must be callable for `decide_f5` to run (`1119-1128` rejects a non-callable). They do **not**
all deserve equal effort. These go in a **new file (or files) that do not yet exist** — for example
`cre/f1/f5_seams.py`; the name is yours. **Nothing in this item edits `f5_supersession.py`.**

**3a. `check_formal_notice(work_id, *, as_of_date) -> NoticeStatus`** — the F5/F8 boundary. PubMed
publication-type and retraction lookup. `notice_kind=="retraction"` on the cited work already routes
to `UNJUDGEABLE cited_retracted_upstream_f8_inconsistency` (`899-903`); `correction`/`eoc` cap at
Path B (`904-906`). This is the only seam where mature machine-readable infrastructure exists. Cheap.
**`as_of_date` is load-bearing, not decorative** — Bakker et al. document papers being retracted while
reviews are in press, so status is a function of the date you check.

**3b. `classify_evidence_tier(meta) -> EvidenceTier | str`** — deterministic mapping from PubMed
publication types and MeSH to the eight `EvidenceTier` members. The module docstring and
`validate_f5_policy:213-216` are explicit that the mapping is deferred to this seam. **No model call.**
An unrecognised string raises in `_tier_from` (`547-556`), so the mapping must be total over what
PubMed actually emits — enumerate the publication types you handle and route everything else to the
lowest tier rather than raising.

**3c. `fetch_comparability_source(work_id, *, as_of_date) -> ComparabilitySource`** — reuse the
existing full-text path (`Data/fulltext_cache_v3/`, the `EFetch db=pmc` call in `ncbi_pmc_reflist`),
Drive-first. Its concatenated text is the ONLY thing span verification checks against (`540-544`,
`714-720`), so **a thin source silently turns every candidate into `UNASSESSABLE`** — that failure must
be visible in the record, not silent. Read the abstract at minimum: Rosemblat measured that for the
species axis the disambiguating fact was in the evidence sentence in 6 of 24 cases and required the
**full abstract** in 17 of 24. Follow DeepSciVerify's escalation — abstract first, full text only when
inconclusive, which resolved 67% of instances without full-text retrieval.

**3d. `retrieve_superseding_candidates(cited_meta, claim, *, after_date, as_of_date) -> RetrievalResult`**
— the expensive one, and the one with the most literature. v1 requirements:

- **Structural filter before semantic search.** Publication date strictly after `after_date`; MeSH
  overlap with `cited_meta`; the citation neighbourhood via E-utilities `elink` (papers citing the
  cited work, and papers citing those). RobotReviewer LIVE went from 23% to 55% precision at unchanged
  100% recall on structural narrowing alone.
- **Retrieve deep.** BM25 on SciFact-Open goes Recall@1 20.22% → Recall@50 66.09%; Sarol goes
  Recall@1 0.09 → Recall@20 0.55. Shallow retrieval is the most expensive mistake available. Set the
  candidate cap deliberately and **record it** — a silent cap reads as "we looked at everything."
- **No learned reranker in v1.** BM25 or E-utilities relevance only. This is a stated limitation, not
  an oversight: BM25 beats every pure dense retriever on SciFact in BEIR (nDCG@10 0.665 vs DPR 0.318),
  and monoT5 reranking is the known next gain (Recall@3 30.87% → 48.26%) but does not fit the
  deadline. Say so in the manifest.
- **`adequacy` and `status` must be honest.** They gate confident-negative-vs-hold at `936-938` and
  `978-980`, and `RetrievalResult.__post_init__` (`284-290`) already enforces empty ⟺ `adequacy=="empty"`.
  A transport failure must return `status="failure"`, never `adequacy="empty"` — that exact confusion
  (an outage wearing the same reason string as a real absence) cost calibration run 1 its entire yield.

**3e. `find_supersession_attestation(...) -> Attestation | None`** — **v1 returns `None`
unconditionally, and this is deliberate.** `deploy_path_a` is hard-gated off (`199-203`), so this seam
can only ever set an audit flag today; Path A is unreachable by construction (`_derive_f5_path`
`1039-1044`). Building attestation retrieval before Path A can deploy is effort spent on an
unreachable branch. **Required:** the no-op must be self-declaring — a named function whose docstring
says it is a declared stub, and a manifest field recording that attestation lookup was not performed,
so `path_a_eligible=False` can never be read as "no attestation exists in the world."

**3f. `judge_contradiction`** — item 1.

### 4. Emit the discovery queue — `discovery_disposition` currently has no consumer

`discovery_disposition` ∈ {`surface`, `do_not_surface`, `unassessable`} is computed, written to every
record and every candidate assessment, and **read by nobody in either repo**. That is the entire point
of a discovery build. No annotation-queue emitter exists.

**Required:** a queue artifact holding every `surface` row with the fields an annotator needs — claim
text, cited work, candidate work, both resolved spans, the scope axis from item 2, and the reason.
Blind: `proposed_route`, `temporal_state`, `confidence` and `discovery_disposition` must **not** appear
in the annotator-facing view. Counts by disposition go in the manifest.

**Two landmines here, both found on disk 2026-08-12 by the session that built `324e430`. Neither was
in rev 1 or rev 2 of this spec.**

**4a. The F5 queue must NOT write to `judgment_band_annotation_queue.jsonl`.** That filename is already
written by both `run_band` and `judgment_run`, and **24 assertions across 8 test files depend on its
exact contents — including 8 that assert it is empty in specific scenarios.** Appending F5 rows there
turns all of them red at once, and the failures present as F5 logic rather than a filename collision.
Use a distinct F5 artifact name.

**4b. A top-level blind filter is NOT sufficient — the disposition is nested.** Every entry of
`candidate_assessments` carries its own `discovery_disposition` (and its own `confidence`), so a nested
row smuggles both past any outer whitelist. **Reuse the existing recursive scrubber** —
`_ANNOTATION_BLIND_KEYS` and `_scrub_annotation_value` — rather than writing a second one. That code
already paid for this lesson once. Add a test that asserts the blind keys are absent at **every** depth
of a queue row, not only at the top level.

**DEC-045 read-across:** a recorded-but-not-queued state already has precedent — a `NO_CLAIMS`
reference is recorded and counted, never queued. `do_not_surface` and `unassessable` rows follow the
same rule: recorded and counted, never queued.

### 5. F5 may report absence only as "none found under this protocol"

The module already fails closed correctly here — inadequate, empty, failed and partial retrieval all
route to a hold via `_held_reason` (`1051-1062`), not to a confident negative. **The defect is
reporting, not routing.** `retrieval_query_hash` is a hash; a hash is not a protocol, and nobody can
audit what was searched from it.

The evidence base does not support an absence claim. SciFact-Open measured that **34.3% (251/732)** of
pooled candidates assumed to hold no evidence actually held it, and that **18% (38/209)** of known
evidence never entered a four-system pool at all. Recall on the disputing class has sat below 0.5 for
twenty years: Teufel 2006 CoCo− recall **0.19** → scite 2021 contrasting recall **0.451**. And no
dataset of verified negatives exists anywhere, so no abstention threshold can be validated.

**Required:** (a) the retrieval protocol is recorded in the manifest in readable form — sources
queried, date window, MeSH terms, candidate cap, reranker (`none` in v1) — not only as a hash; (b) a
negative carries a reason that distinguishes **no admissible later evidence was found** from
**retrieval failed**; (c) no artifact, field name or report line asserts that no superseding paper
exists.

### 6. F5 is invisible in the run manifest — fix the provenance gap

`judgment_run.py:784-794` builds `module_sha256` from `judgment_band`, `judgment_engine`,
`band_prompts`, `parser`, `schema`, `f4_strength`, `f3_provenance`, `judgment_run`. **`f5_supersession`
is not in that list.** The `prompt_sha256` map (`796-802`) covers F3 and F4 prompts only. There is an
`"f4"` policy block (`845-856`) with no `"f5"` counterpart. An F5 run today emits no module hash, no
prompt hash and no policy block.

**Required:** F5 module hash, F5 prompt hash, and an `"f5"` policy block carrying the `F5Policy`
fields, alongside the retrieval protocol from item 5. This is DEC-020's failure mode and
`CONTRADICTIONS` 41 repeating a third time: a number whose governing setting no artifact records.

**The landmine in this item, flagged by Claude Code 2026-08-12.**
`test_fulltext_coverage_wiring.py::test_default_path_manifest_counter_set_is_unchanged` pins the
**exact** `counts` key set for the default abstract path, because the opt-in guarantee is byte
identity. **Any new unconditional counter breaks it, even a zero-valued one.** Two patterns already
work in-tree: seed the key only when the relevant path is on (`counts["no_usable_fulltext"]`,
`counts["evidence_span_not_found"]`), or increment lazily with `counts.get` so the key appears only
when it fires (`ROUTE_NO_CLAIMS`). Every F5 manifest key added here must appear **only when F5 is
wired**. Default-path byte identity was proven by diff against `de3e040` for `324e430`; if this item
touches `run_band`'s manifest, redo that proof.

### 7. Clear the three strict xfails — they exist already, and they are `strict=True`

`test_adversarial_f5_supersession.py` records three live validation defects. **These tests are already
written; do not write them again.** Verified on disk 2026-08-12 at `:65`, `:72`, `:79`, each marked
`@pytest.mark.xfail(strict=True, ...)` — so the moment you fix a defect without removing its marker,
the test XPASSes and goes red. Remove the marker in the same commit as the fix. See Definition of
done.

- `date.fromisoformat` accepts `20240101` and `2024-W01-1`, so malformed dates pass the ISO gate.
- `NoticeStatus.date` is never parsed — a notice date is accepted unvalidated.
- `RetrievalResult` permits duplicate work IDs, so one candidate can be assessed twice and inflate
  agreement.

Also **`failed_replication_evidence`** (`652`) is initialised `False` and never written again — a dead
field. Either write it or remove it; do not leave it reading as a measured `False`.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| `ComparabilitySource` with 3 populated fields | prompt | each field rendered as a labelled block with `s1..sN` ids, document order, stable across runs |
| same source, two runs | ids | identical (deterministic segmenter, shared with `sentence_spans.py`) |
| judge reply | `cited_finding_span` / `candidate_contradiction_span` | resolved from ids, verbatim in `_source_text` by construction, passes the check at `714-720` |
| judge reply quoting prose, Jaccard ≥ 0.7 | span | aligned and resolved; `span_source` recorded |
| judge reply, span unresolvable | route | **recorded miss**, candidate retained in `candidate_assessments`; NOT a `ValueError`, NOT a quarantine |
| judge reply with 11th key while contract is 10 | `_parse_contradiction` | `ValueError` (strictness unchanged) |
| candidate with `population_relation="narrower"` | `comparability_decision` | `not_comparable` (unchanged) **and** a recorded scope axis |
| candidate with `population_relation="equivalent"`, no mismatch | scope axis | `none` |
| PubMed record, publication type not in the mapping | `classify_evidence_tier` | lowest tier, no raise |
| cited work with a retraction notice | `temporal_state` | `UNJUDGEABLE`, reason `cited_retracted_upstream_f8_inconsistency` (unchanged) |
| `as_of_date` before a retraction's date | `check_formal_notice` | notice not applied |
| E-utilities transport failure | `RetrievalResult` | `status="failure"`, **never** `adequacy="empty"` |
| retrieval returns zero candidates cleanly | `adequacy` / `temporal_state` | `"empty"` / `UNJUDGEABLE` hold, reason distinguishes no-admissible-evidence from failure |
| `find_supersession_attestation` | return | `None`; manifest records attestation lookup not performed |
| `path_a_eligible=True` in any record | `f5_path` / `path_a_deployed` | `"B"` / `False` (unchanged, `deploy_path_a=False`) |
| any record | `reportable` / `verifier_result` | `False` / `"not_run"` (unchanged) |
| run with ≥1 `surface` row | queue artifact | exists, under a name that is NOT `judgment_band_annotation_queue.jsonl`; contains claim, both works, both spans, scope axis, reason |
| existing `judgment_band_annotation_queue.jsonl` assertions | 24 assertions / 8 files | unchanged, including the 8 that assert it is empty |
| queue row with nested `candidate_assessments` | blind check at EVERY depth | no `proposed_route`, `temporal_state`, `confidence` or `discovery_disposition` at top level **or nested** |
| `do_not_surface` / `unassessable` rows | queue artifact | absent from the queue, present in the counts |
| manifest | new keys | `f5` module sha, F5 prompt sha, `"f5"` policy block, retrieval protocol, candidate cap, disposition tallies, attestation-not-performed flag |
| `date.fromisoformat("20240101")` path | validation | rejected |
| `RetrievalResult` with duplicate work ids | construction | rejected |
| F5 not wired (`f5_seams=None`) | `temporal` | `TemporalState.UNJUDGEABLE` (unchanged) |
| default abstract path, F5 not wired | every output byte | unchanged from `324e430` |
| `PMC10115774` end to end, live | run | completes; F5 records emitted; queue written. **Colab, ~25–40 min** for 26 references including E-utilities fetches — checkpoint to Drive |

---

## Guardrails (do NOT change)

- **`band_prompts.py` untouched** — blob `fa01126e2b9482d450065fd70cd0eb1fea816f5c` is the freeze-chain
  root. No freeze artifact, universe fixture, `test_mint_v1.py` literal or schema pin moves.
- **Do NOT run `git clean -fd` in this worktree.** Three untracked files live in `docs/`, and one is
  load-bearing: `docs/TAXONOMY_AMENDMENT_2026-08-11.md` is the governing taxonomy authority for
  `324e430` (its §D carries the DEC-047 rule that spans are recorded and reported and do not gate the
  verdict). The other two are `docs/F3F7_EVIDENCE_SPAN_SELECTION_SPEC.md` and this spec.
- **`f5_supersession.py` is not restructured.** Add fields and fix the three recorded defects. The
  detector contract, the fail-closed behaviour, `validate_f5_record`'s replay checks and
  `record_sha256` all stay.
- **`deploy_path_a` stays hard-gated off** (`199-203`). Path A stays unreachable. `reportable` stays
  `False` and `verifier_result` stays `"not_run"`.
- **Nothing may touch F2.** Seed 41 is drawn and scored at `c621a09`; any F2 rule, threshold, taxonomy
  or reason-code change spends it. See `CONTRADICTIONS` 42 — there is already an unresolved post-draw
  F2 change awaiting ZD.
- **The judge is never gold.** No F3–F8 label is machine-assigned. `proposed_route` stays blind to the
  annotator until after commit. Claude assigns no labels and curates no ground truth.
- **Naturally-occurring data only** for anything that could become gold. Calibration examples may be
  hunted; gold may not. No synthetic or perturbed supersession pairs.
- **Never use the detector's own flags as gold.**
- **`response_parser_version` bumps** (DEC-022, independent of prompt version); both stamped on every
  record. `contradiction_prompt_version` moves off `f5_contradiction_v1` only if item 2 changes the key
  set.
- Tri-state discipline (`is True` / `is False`, `None` means unknown); path-based module loading, no
  `__init__.py` in `cre/`; restart Colab after any push; Drive-first I/O; evaluation precision-only
  (DEC-005); `temperature=0` (DEC-046).
- **F2 is precision-only in evaluation, but recall is non-negotiable in the matcher** — nothing here
  touches it, and nothing here may tighten a gate that silently drops a population.

---

## Regression guards

Guard PMIDs `31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as
before. The two confirmed F3 cases must survive again — their evidence record shape has now changed in
four consecutive commits, and item 1 reuses the machinery that changed it in `324e430`.

The unresolved taxonomy note at `judgment_engine.py:408-411` (dated 2026-07-16 — whether an overstated
claim can be a valid F5 target) is **not** resolved here. Leave the comment and the behaviour as they
are, and do not silently decide it.

---

## Definition of done

- Collected and passing counts measured before and after, both reported. `324e430` self-reported
  **1648 passed / 12 skipped / 29 xfailed**. Do not predict; measure.
- Strict xfails written first for items 1, 2, 4 and 5 and observed failing at `324e430`.
- **The three xfails in item 7 are `strict=True`, so fixing a defect turns its test RED, not green.**
  A strict xfail that passes is an XPASS and pytest reports it as a failure. **Unmark each one in the
  same commit as its fix** — do not leave the marker on and do not "flip it to passing." Expect the
  xfail count to move 29 → 26; report the movement explicitly, and report any XPASS as a defect in this
  spec, not in the code.
  **Measured at `7634b94`: 3 markers removed, 4 xfail instances cleared, 29 → 25** — the ISO-date test
  is parametrized over `"20240101"` and `"2024-W01-1"`, so one marker collects as two instances. The
  "29 → 26" written in rev 2 counted markers and was wrong; **count instances, and when a marker sits
  on a parametrized test expect instances to exceed markers.** This is precisely the failure this
  section's own "do not predict; measure" rule exists to catch, and it caught it.
- Acceptance matrix verified. The live rows need `ANTHROPIC_API_KEY` and NCBI access, and run in Colab.
- Default path proven byte-identical to `324e430` by diff or hash with F5 unwired.
- Pushed; report the commit SHA and both counts.

**Land this in two commits, in this order.** Commit 1: items 1, 2, 7 — the prompt, the scope axis, the
three defects. All of it is testable offline and none of it needs a network. Commit 2: items 3, 4, 5, 6
— the seams, the queue, the absence language, the manifest. If the deadline bites, commit 1 alone is
still a real deliverable: it makes the judge honest and leaves the seams stubbed.

---

## Out of scope

- **An F5 verifier.** F4 and F7 have verifier prompts and passes; F5 has none, and `reportable` is
  unreachable because `validate_f5_record:1195` requires `verifier_result=="confirmed"`. That stays
  true. Do not build one; do not make `reportable` reachable.
- **Path A, attestation retrieval, deployment mode.** All hard-gated. `mode="deployment"` is
  constructible and passes validation (`CONTRADICTIONS` 44 item 1) — **do not use it, and do not "fix"
  it here**; whether `mode` should be pinned the way `deploy_path_a` is is ZD's call, not this spec's.
- **A learned reranker** (monoT5, MedCPT, dense retrieval). Named as the known next gain; does not fit
  the deadline. Record its absence, do not build it.
- **Any F5 scored result, F1, precision or recall.** No gold set exists. The output of this work is an
  annotation queue and a manifest, not a number.
- **`WEAKER_STRENGTH` claims** (module docstring 50-51) and the larger-n selection preference
  (`_select_representative` docstring 986-988) — both documented deferrals, both stay deferred.
- The batch prompt freeze (DEC-044, deferred not cancelled), CONFIG, trust-boundary modules,
  conformance regen, schema re-pin.
- Anything on the F2 branch. The corpus run and F6/F3 work.
- The missing `F5_SUPERSESSION_SPEC.md` / `F5_BLUEPRINT.md` — the module docstring pins
  `F5_BLUEPRINT.md` by sha256 `663edd69…` and **the file is not in either repo**
  (`CONTRADICTIONS` 44 item 7). A pinned document that is not in the repo cannot be checked. Do not
  reconstruct it; report it.

---

## Verification command

```
cd /Users/kamachi/cre-f3f7/citation_repair_F1_handoff
PYTHONPATH=. ../.venv_cre/bin/python -m pytest cre/f1 -q --ignore=cre/f1/.venv
```

---

## If something here is wrong

Five prescriptions from this side have now been wrong about interfaces they had not read
(`CONTRADICTIONS` 35, 39, 38, DEC-047, and the run-4 span framing). Three were caught because Claude
Code stopped and reported instead of reconciling. **That protocol is doing the work; keep it.**

This spec was written against a read-only audit with file:line citations, not against the running
code, and the device bridge dropped before it could be re-read live. Treat every line number as a
pointer to check, not a fact. **State the defect and the constraint, then choose the edit yourself. If
a prescribed change does not do what this spec claims, stop and report — do not reconcile silently.**

Two items are explicitly [CLAUDE'S CALL] and reversible on ZD's word: where the scope axis value comes
from (item 2), and the candidate cap and its structural filters (item 3d). Both must be recorded
wherever they land.

## Sources for the design decisions

- Rosemblat, Fiszman, Shin & Kilicoglu 2019, *J Biomed Inform* 98:103275, PMID `31473364` — the
  2,236 → 58 → 4 funnel, κ 0.92, and the abstract-vs-sentence context measurement (6 / 17 / 1 of 24).
- Xie et al. 2024, *JAMIA*, PMID `38758667` — four-step decomposition, F1 0.799 ternary vs 0.788 binary.
- Wadden et al. 2022, SciFact-Open, `2022.findings-emnlp.347` — pooling bias 34.3% and 18%.
- Nicholson et al. 2021, *QSS* 2(3):882–898 — scite "contrasting" 0.8%, precision 0.852, recall 0.451.
- Teufel, Siddharthan & Tidhar 2006, `W06-1613` — CoCo− F1 0.28, recall 0.19.
- Thakur et al. 2021, BEIR, `arXiv:2104.08663` — BM25 nDCG@10 0.665 on SciFact.
- Deng, Wang & Stevenson, ICTIR '25, doi `10.1145/3731120.3744614` — monoT5-3B Recall@3 30.87% → 48.26%.
- Sadeghi et al. 2026, DeepSciVerify, `arXiv:2605.27710` — abstract-first escalation, 67% resolved.
- Ioannidis 2005, JAMA — 24% unchallenged. Herrera-Perez et al. 2019, *eLife* — 48% unadjudicable.
- Full source register with verified/unverified split: `F5 SOURCES` (vault) /
  `claude/F5_SOURCES_2026-08-12.md` (project). **Numbers in this spec were transcribed by a fetch
  tool's summariser, not read off the PDF — spot-check before any of them enters the manuscript.**
