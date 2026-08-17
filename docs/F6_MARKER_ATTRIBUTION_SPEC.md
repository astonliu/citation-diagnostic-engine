# F6 — claim-to-marker attribution — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F6 debug pass over `PMC13294812` and `corpus_frozen_v1`.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

## Objective

**Current behavior.** `parser.link_citances` builds one co-citation group per *sentence occurrence*,
and `judgment_run.judge_pair_coverage` asks **every member of that group about every atomic claim in
the whole sentence** — including claims whose citation markers point at a different reference.

**Target behavior.** A reference is judged only against the claims its own marker position was cited
for. A claim a reference was never cited for must not be able to produce a fault against it.

---

## The measurement that motivates this

Adjudication packet `PMC13294812`, 53 flagged claims over 20 flagged references,
`claude-opus-5`, effort high, full text, tri-state aggregation:

| engine reason | claims | share |
|---|---|---|
| unconfirmed specific | 32 | 60% |
| **the retrieved text never addresses this claim** | **17** | **32%** |
| contradicts | 3 | 6% |
| F4 weaker strength | 1 | 2% |

**16 of those 17 are on a co-cited reference**, and all 17 are exactly the rows that emitted **no
evidence span at all** (`coverage_prompts_v3.py:484` — `engages_subject=false` requires
`evidence_spans=[]`). An adjudicator cannot audit those rows; there is nothing to point at.

### The clean case, verified end to end

Sentence (`PMC13294812`, discussion):

> Notably, such detailed cellular characterisations were not necessary for the successful clinical
> translation of fluorophore-labelled antibodies **52,53** and pH sensitive fluorescent micelles
> **54,55** for intraoperative FL imaging of oral SCC disease.

The sentence has **two marker clusters**: `52,53` attached to *antibodies*, `54,55` attached to
*micelles*. `link_citances` puts all four in one group. Claim extraction returns 4 claims — two about
antibodies, two about micelles — and all four claims were asked of all four references. Verified from
the packet: the `atomic_claims` lists of `B52`, `B53`, `B54`, `B55` are **byte-identical**, all four
claims each.

Result, packet row 11: **`B55`** (*PET imaging of occult tumours by temporal integration of
tumour-acidosis signals from pH-sensitive 64Cu-labelled polymers*) is flagged **F6** on the claim
*"Fluorophore-labelled antibodies were successfully clinically translated…"* with reason **"the
retrieved text never addresses this claim."**

That verdict is correct. The question was wrong. `B55` was cited for micelles.

The same shape holds for `B44`/`B45` (cited for *"underexplored"*) judged against the
*"dominant interactions"* claims belonging to `46-50`, and for `B20`/`B21`/`B22`
(markers `19,20` = margins, `21,22` = lymph nodes).

---

## What this is NOT — three hypotheses tested and eliminated

Record these so they are not re-investigated.

1. **The co-citation overlay is not unwired.** `_cocitation_overlay` is built
   (`judgment_run.py:741`) and reaches the engine as `cogroup_covered`
   (`judgment_run.py:1418`). Verified by reading the call path.
2. **Claim-extraction divergence did not fire.** `cocitation.aggregate` excludes a member whose
   `atomic_claims` differ from the group's (`EXCLUDED_CLAIMS_DIFFER`, `cocitation.py:158`). Measured:
   the claim lists are byte-identical across all members of all four groups examined. This also
   provides partial, single-document evidence toward the open extraction-stability question.
3. **Coverage is a real but secondary cause.** A group only clears a member through
   `contributing_members`, so a sibling with no full text contributes nothing. Measured over
   `corpus_frozen_v1`: 246 co-citation groups, 170 with at least one evaluable member, **79 of those
   170 (46.5%) have exactly one** — group credit is structurally impossible there. On `PMC13294812`
   this accounts for **3 of 20** flagged references; **12 of 20** sat in groups with two or more
   evaluable members and were flagged anyway. Coverage is not the main driver.

---

## Change 1 — attribute claims to marker clusters

`parser.link_citances` (`parser.py:345`) already resolves each marker's position when it walks the
sentence. What it discards is **which markers sit together**.

**Required:**

- Record, per reference, the **marker cluster** it belongs to: the maximal run of citation markers
  that are adjacent in the serialized sentence (separated only by commas, dashes, whitespace or
  bracket punctuation). `B52,B53` is one cluster; `B54,B55` is another; the two are separated by the
  words *"and pH sensitive fluorescent micelles"*.
- Record each cluster's **character offset** in the sentence.
- A sentence with exactly one cluster behaves **exactly as today** — no path divergence. This is the
  regression guard for the whole change.

**Use `_serialize_with_markers`' xref offsets. Do NOT regex the rendered citance.**
Prototyped 2026-08-16 and measured, so this is not a preference:

- A digit regex over `ref.citance` matched **years inside author-year citations** (`Lang, 2024a`),
  **`COVID-19`**, and **`10 + years`**, and left **226 printed markers unlocatable** in the citance
  string across the corpus. Numbers derived that way are unusable.
- `parser._serialize_with_markers` (`parser.py:183`) already returns
  `(text, [(char_offset, [rid...], marker_text), ...])` for every `<xref ref-type="bibr">` in
  document order. That is the authoritative marker position. Cluster on those offsets.

### The corpus is not all one citation style — measured 2026-08-16

| style | documents |
|---|---|
| numeric marker | **17** of 20 |
| author-year | **3** — `PMC12967000`, `PMC13219232`, `PMC13295838` |

The positional cluster rule **is not defined for author-year citations**: the marker text is itself a
name containing letters, so "a letter between two markers means a new clause" is meaningless there.
**Detect the style and apply clustering only to numeric-marker documents**; author-year documents keep
today's whole-sentence behavior and the record says which rule applied. `PMC13294812` — the
adjudication packet's document — is numeric, so the packet's findings are unaffected.

**Separate defect found while measuring this, not in scope here but log it:** in author-year documents
`parser._sentence_spans` fragments sentences on the period in `et al.`, yielding sentence fragments
like `", 2006)."`. `sentence_spans.py` protects `Fig.`, `Dr.` and single-letter initials but not
`et al.`. That affects the citance every reference in those three documents is judged against.

### Sizing — the acceptance target

Numeric-style documents only, measured over `corpus_frozen_v1`:

| | |
|---|---|
| marker-bearing sentences | 901 |
| sentences citing ≥2 distinct references | 274 |
| **of those, splitting into 2+ marker clusters** | **76 (27.7%)** |
| references sitting in such a sentence | **216** |
| clusters per multi-reference sentence | `{1: 198, 2: 68, 3: 4, 4: 2, 5: 1, 7: 1}` |

So roughly **one multi-reference sentence in four** currently asks at least one reference about
another clause's claims. That 216 is the population Change 2 de-scopes, and the implementation should
report its own equivalent count so the two can be compared.

**Do not change `citance_group_id` semantics or `citation_id`.** The cluster is additional
provenance, not a replacement. `citation_id` stays `"<citing_pmcid>:<ref_id>"` — Band 1's
`preband_contract` join keys on it.

## Change 2 — scope each claim to a cluster

Claim extraction returns an ordered list for the whole sentence. Each claim must carry the marker
cluster it belongs to, and each reference must be judged only against claims whose cluster is its own.

**How to determine the mapping is yours.** Two defensible routes:

(a) **Positional.** Assign each claim to the nearest preceding-or-following cluster by character
offset in the sentence. Deterministic, no model call, and it gets the `52,53` / `54,55` case right.

(b) **Ask the extractor.** Extend the claim-extraction reply with a marker attribution per claim.
**This requires a `band_prompts.py` change, which is a decision, not an implementation detail —
stop and report rather than doing it.** `band_prompts.py` is pinned by whole blob OID
`fa01126e2b9482d450065fd70cd0eb1fea816f5c` and the frozen prompt packages seal that OID.

**Start with (a).** It is testable offline against fixtures, costs nothing, and if its accuracy is
insufficient that is a measurement ZD can act on.

**Fail closed.** When the mapping is ambiguous — a claim that spans clusters, or a sentence whose
clusters cannot be ordered — the reference is judged against **the whole sentence, exactly as today**,
and the record says so. Ambiguity must never silently narrow what a reference is accountable for;
narrowing wrongly converts a real fault into a clear, and this project is precision-first in the
direction of escalation, not exculpation.

## Change 3 — make the unattributed rows visible

Whatever the mapping decides, the record must let a reader see it:

- per verdict: the reference's marker cluster, the claim's assigned cluster, and whether they matched
- per document: a count of `(reference, claim)` pairs that were **skipped as not-this-reference's-claim**
- a `not_asked` disposition distinct from `assessed_negative`. **This is the same class as DEC-079's
  F3 gate and the tautological queue audit** — a claim that was never put to a reference and a claim
  the reference failed must never be indistinguishable in the output.

## Change 4 — cache claim extraction on the orchestrator path

**Separate defect, found while debugging this one.**
`judgment_band.run_band` caches extraction per citing sentence (`judgment_band.py:1044-1052`,
comment: *"the cached list is shared by every reference on this citance"*).
`judgment_run.judge_pair_coverage` (`judgment_run.py:454`) calls
`jb.extract_atomic_claims(item["citing_sentence"], extractor=extractor)` with **no cache**, and
`run_natural_judgment` is the production path.

So the `B52`-`B55` group paid **four identical extraction calls for one sentence**. At 1.30 billed
calls/reference and $7.22/document this is pure waste, and it is also a latent correctness risk: any
extraction drift silently triggers `EXCLUDED_CLAIMS_DIFFER` and the group's coverage credit vanishes
with nothing in the output saying so.

**Required:** mirror `run_band`'s per-sentence cache in the orchestrator, copying the list per item so
each record still owns its own claims. **Test: a document with one sentence citing N references makes
exactly one extraction call.**

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| sentence with ONE marker cluster citing 4 refs | behavior | byte-identical to today — regression guard |
| author-year document (`PMC12967000`) | behavior | whole-sentence, as today; record says `style=author-year` |
| `corpus_frozen_v1`, numeric documents | de-scoped sentences | ≈76 of 274 multi-reference sentences (compare to the measured 27.7%) |
| marker positions | source | `_serialize_with_markers` xref offsets, never a regex over rendered text |
| `"…antibodies 52,53 and micelles 54,55…"`, 4 claims | `B55` judged against | the micelle claims only |
| same fixture | `B55` verdict on the antibody claim | **not emitted** — recorded as `not_asked` |
| same fixture | `B52` verdict on the antibody claim | emitted, unchanged from today |
| claim spanning two clusters | scope | whole sentence (fail-closed), and the record says `ambiguous` |
| any run | record | reference cluster, claim cluster, matched yes/no |
| any document | manifest | count of `(reference, claim)` pairs skipped as not-this-reference's |
| manifest | dispositions | `not_asked` distinct from `assessed_negative` |
| one sentence citing N refs | extraction calls | **exactly 1** (Change 4) |
| group with exactly one evaluable member | behavior | unchanged; still no group credit — that is coverage, not this bug |
| fixture rows 1-14 of `F6_COCITATION_SPEC.md` | all | still pass |

---

## Guardrails — do NOT change

- **`band_prompts.py` stays byte-identical** — blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
  Verify and report. If route (b) above looks necessary, **stop and report**.
- **`citation_id` / `item_key` stay `"<citing_pmcid>:<ref_id>"`.** Band 1's disposition joins on it.
- **`citance_group_id` semantics unchanged.** Marker clusters are additional provenance.
- **Fail closed to today's behavior** on any ambiguity. Never narrow a reference's accountability on a
  guess.
- **F4 and F7 are per-reference and must be unaffected** — acceptance rows 12 and 13 of
  `F6_COCITATION_SPEC.md`.
- **The strict parser stays strict.** Quarantine is 0.0% at effort high.
- **Claude never assigns semantic labels**, and the packet must not pre-score or rank rows.
- **Precision-first.** Ambiguity escalates; it never becomes an accusation, and it never becomes a
  silent clear.
- **F2 untouched.** `SAME_WORK_TITLE_SIM_MIN = 0.92`, read this session at `biblio_match.py:120`
  (note: the older specs cite `:152` and `:139`; the constant has moved — cite `:120` and re-read
  before quoting it again).
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as before.
Plus the two confirmed F3 cases — compare on banding, not evidence-record shape.

The single-cluster fixture is the load-bearing guard: most sentences have one cluster, and those must
not move at all.

## Definition of done

**Baseline suite, measured on Linux 2026-08-16 (first real count this project has had — the vault's
narrative still carried 968 from 2026-07-27):**

```
1956 passed, 1 failed, 12 skipped, 24 xfailed in 47s
```

Run from inside `citation_repair_F1_handoff/` with `PYTHONPATH` set to that directory — collecting
from the repo root fails with 46 collection errors, because `cre/` has no `__init__.py`.
Requires `pip install anthropic` (13 tests fail without it) and `rapidfuzz`.

The one failure is **environment-dependent, not a defect**:
`test_biblio_match.py::test_rerank_stage2_degrades_when_model_unavailable` asserts graceful
degradation when the Stage-2 cross-encoder cannot load. Colab can reach HuggingFace, so the model
loads and the degradation branch never runs. Expect it to pass on a machine with no HF access.

**So the target after this change is 1956 + (new tests) passing, same 1 environment failure.**

- Marker clusters recorded per reference, with offsets.
- Claims scoped to clusters by the positional rule, failing closed to whole-sentence on ambiguity.
- `not_asked` distinguishable from `assessed_negative` in the manifest, with counts.
- Orchestrator extraction cached per sentence; one-call test passing.
- All 14 rows of `F6_COCITATION_SPEC.md` still pass.
- `band_prompts.py` blob OID verified unchanged and stated.
- Suite green; count old → new.

## Out of scope

- **Any corpus run.** Still gated on ZD's adjudication split.
- **Changing the F3 gate** (DEC-079) — instrument only.
- **The evidence-scope levers.** ZD accepted PMC-only scope, 42.1% coverage, 2026-08-16.
- **F5 / F7 seam wiring.** F5's `reportable` is hardcoded `False` (`judgment_run.py:820`).
- **Re-running the `PMC13294812` packet.** The measurement above is sufficient to build against.
- **Any `band_prompts.py` edit.**

## Verification command

```
python -m pytest citation_repair_F1_handoff/cre/f1 -q
```
