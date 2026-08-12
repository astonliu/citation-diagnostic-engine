# F5 (Stale / Superseded) — design blueprint

Design-level rationale for the F5 discriminator, upstream of the implementation spec
(`F5_SUPERSESSION_SPEC.md`, to be written). Purpose: fix the architecture and the decision rules
**before** code, and give a concrete surface to pressure-test. Branch `feat/f3-f7-typed-judgment-v2`.

> **STATUS: CODEX + CLAUDE-OPUS CONSENSUS REACHED (2026-07-17) — relay gate cleared. PENDING ROBERTS ADVISOR LOCKS.**
> F5 is the last unbuilt F3–F7 discriminator. This blueprint proposes resolutions to every open
> decision but **derives nothing advisor-locked** until Roberts freezes the rules and they are
> preregistered. It was **hardened by a 5-member LLM council (2026-07-16, §20)** and then **ratified
> through the codex-relay two-reviewer loop (3 turns; final-turn hash
> `35a8f2d3af7a092881d10d2cf73f7339334b827f3851e3eed04bbc286062dc96`)**; the agreed item-12 conformance
> edits are applied (see §21). It is **non-reportable** until the Roberts locks clear. Nothing here
> modifies the frozen engine.

---

## 0. Two owner corrections + the council verdict (read first)

**Owner corrections (ZD, 2026-07-16), baked in:**
1. **F5 and F8 are operationally separated (not a statistical-correlation claim). There is no "F5 vs F8 boundary."** The original preregistration
   *excluded* an F5-vs-F8 judgment pair; a later doc reinstated it (protocol §18-2) — that
   reinstatement is withdrawn. F8 (formal retraction) is **deterministic and decided upstream** in the
   existence-check pre-classifier. F5 is a judgment discriminator over the notice-clear population.
   They do not compete or share a boundary task. The only residual is **mechanical, not a
   correlation**: the frozen engine requires `f8_notice=False`, i.e. a retracted paper was already
   removed upstream. **Consequence:** the `f5_f8_boundary` measurement construct in
   `measurement/measure.py` and the "corrections/EoC routing" question are **out of F5 scope**. The
   **authoritative protocol was amended 2026-07-17** (dated amendment at its head + §9.3) to withdraw the
   F5-vs-F8 boundary task, so protocol and blueprint now agree.
2. **Ground the design in the literature** (§1 + References).

**Council verdict (full detail §20), binding on this blueprint:**
- A single qualifying contradiction is a **CANDIDATE** signal, **not** a supersession diagnosis.
  Supersession is a field-level/consensus event.
- **Path A (autonomous replacement) is deferred for the Aug-1 preprint** and, when used, is **gated by
  a separate, temporally-bounded (on-or-after; `replacement_date ≤ attestation_date ≤ as_of_date`, §6-I),
  citable field-level attestation that explicitly concludes the finding was reversed/superseded** — a major-guideline revision **or** a systematic review/meta-analysis, never one
  recent primary study. A **pre-registered failed replication is contradiction/supporting evidence, not a
  standalone attestation** (it can still be a single study); it gates Path A only when incorporated into a
  qualifying field-level attestation. The preprint ships **detection-first + human escalation (Path B)**.
- Do **not** report an F5/Path-A precision number for the preprint (the positive class is too sparse —
  "n≈2, 100% precision" is unpublishable). Report **existence + mechanism + N worked repairs**.
- **Quality-hierarchy fix:** superseding evidence must be at an **equal-or-higher OCEBM tier**; a larger
  n counts only *within or above* the cited tier (a big cohort does **not** supersede a small RCT).
- **Source the worked set by inverting from published medical-reversal catalogs** → forward citations
  (existence/calibration only, never a detection denominator — honors the no-circularity lock).
- Add a **prior-art/novelty defense** (Trialstreamer, RobotReviewer, living systematic reviews, scite
  contrasting/disputing citations).

---

## 1. What F5 is

F5 = **stale / superseded citation**: the cited paper is real, had no formal retraction, and **once
supported the claim**, but its central finding **has since been directionally contradicted by more
recent, independent work** (of *any* evidence tier — tier governs the Path-A repair gate, **not**
detection). F5 is about *scientific progress*, not an error
in the original citation and not an integrity failure. (`TAXONOMY_F5_section.md`;
`TAXONOMY_DECISION_RULES.md`; Prereg Amendment 3.)

**Grounding (why F5 is real).** F5 is the citation-level instance of *medical reversal* — an
established finding overturned by a later, superior study. Prasad, Gall & Cifu coined the term (2011);
a decade-scale audit found 146 reversals in *NEJM* (Prasad et al., 2013); a three-journal meta-research
review catalogued 396 (Herrera-Perez et al., 2019). Canonical reversals — CAST (antiarrhythmics
post-MI), COURAGE (routine stenting in stable CAD), WHI (HRT/CV risk) — are exactly the events that
make a once-correct citation stale.

**Novelty — and the prior art it must clear [council-added].** Automated detection of *contradictory
claims between biomedical abstracts* is an established NLP task (Alamri & Stevenson, 2016; Rosemblat et
al., 2019; BioDivergence, 2026); *evidence-surveillance* systems exist (Trialstreamer, RobotReviewer,
living systematic reviews); and scite already productizes **contrasting/disputing** Smart Citations.
None of these is tied to a **specific citation's validity** or to **repair**. **F5's novelty is the
join:** detect that a *particular citation* now rests on a since-reversed finding, inside an F1–F8
citation-repair taxonomy, and — where a field-level verdict exists — propose an evidence-backed
replacement. Claim the join, not contradiction detection per se. Run an explicit novelty search against
the systems above before the preprint (mirroring the June-2026 search that reframed the GENERATION
withdrawal).

**Three contrast boundaries define F5 (F8 is *not* one):**
- **F5 vs F3/F4/F6:** those ask whether the cited paper supports the claim *as written today*. F5
  **presumes it did**; the field moved. F5 only ever targets a claim already judged `SUPPORTED`.
- **F5 vs ACCURATE:** ACCURATE = supported and no qualifying contradiction in later literature. F5
  fires only on a *directional reversal*, never a magnitude refinement or a tightened hedge.
- **F5 vs "no error":** a newer paper that merely refines or extends is not F5.

---

## 2. Where F5 sits in the engine (already built and FROZEN; do not modify)

`judgment_engine.py` (sha256 `671de1e5…`, frozen) already contains the F5 **decision** seam. Building
F5 adds a **detector + repair layer + measurement wiring** and touches the engine **zero** times.

- **Types (frozen):** `TemporalState ∈ {NO_QUALIFYING_CONTRADICTION, QUALIFYING_CONTRADICTION,
  UNJUDGEABLE}`; `TemporalAssessment(state, claim_index, newer_work_id, same_claim_or_outcome,
  comparable_population, f8_notice, evidence_spans, rationale)`.
- **Contract for `QUALIFYING_CONTRADICTION` (frozen):** requires `claim_index` (int ≥ 0), nonblank
  `newer_work_id`, `same_claim_or_outcome is True`, `comparable_population is True`, `f8_notice is
  False`, nonempty `evidence_spans`; else `DiscriminatorContractError`.
- **Decision (frozen, L390–406):** on `QUALIFYING_CONTRADICTION` the engine asserts `claim_index` in
  range **and `claim_support[claim_index].state is SUPPORTED`** (else contract error "F5 contradiction
  must target a claim the cited paper supported"), then appends `"F5"`.
- **Precedence (frozen):** order `("F7","F6","F4","F3","F5")` — **F5 lowest**; it rides along other
  faults (`test_f5_does_not_erase_an_evidence_fault` → `("F6","F5")`).
- **Pinned tests (stay green):** `test_qualifying_temporal_evidence_derives_f5`,
  `test_f5_does_not_erase_an_evidence_fault`, `test_f5_cannot_target_an_unsupported_claim`,
  `test_temporal_assessor_none_is_a_contract_error_not_a_silent_hold`.

**Reconciling the engine with the council's "CANDIDATE-F5" point.** The frozen engine emits `F5` on a
single qualifying contradiction — I cannot and will not change that. The council's concern (one paper ≠
supersession) is honored **in the detector and the write-up, not the engine**: a lone qualifying
contradiction is emitted as F5 = **detection/CANDIDATE**, and the **repair layer** (outside the engine)
decides whether it is a Path-A autonomous replacement (requires the §6 attestation) or Path-B
escalation. The engine records detection; the detector governs when to emit it and what to do next.

---

## 3. The architectural constraint that shapes everything

F5 is **the only discriminator that must retrieve a *second* paper.** Per the v2.2 architecture note:
**MeSH-tag candidate generation filtered by publication date, then direct LLM contradiction judgment on
the `ComparabilitySource` bundle (abstract + methods/results/protocol/registry, §5) — NOT MonoT5
reranking** (topical relevance ≠ directional contradiction). To stay offline,
fail-closed, and reproducible like F3/F4/F7, **all retrieval and model calls are injected `Callable`
seams**; the module and its tests do no network/paid I/O.

```
SUPPORTED claim ─▶ retrieve_superseding_candidates(cited_meta, claim, after=cited_date, as_of=assessment_cutoff)
                       │  (MeSH + pubdate>cited  ∪  scite "contrasting" citations; NOT MonoT5)
                       ▼
             candidates ─▶ check_formal_notice(cited + candidate)
                       │   candidate flagged→audit row (§9-12) · cited retraction→upstream-F8 inconsistency (§9-20) · cited correction/EoC→Path-B cap (§9-21)
                       ▼
             judge_contradiction(cited_source, candidate_source, claim)  (LLM, temp=0, strict-JSON)
                       ▼
             F5Record (rich) ─▶ map to TemporalAssessment (minimal) ─▶ decide_judgment ─▶ F5 (or not)
                       └─▶ Path routing: attestation? → Path A (deferred) ; else → Path B (escalate)
```

---

## 4. Two layers: DETECTION (engine) vs REPAIR ROUTING (Path A / Path B) [council-hardened]

- **Detection = `QUALIFYING_CONTRADICTION` = the `F5` finding = CANDIDATE.** A newer, **independent**
  paper **directionally contradicts** the cited finding on the **same outcome** and a **comparable
  population**, with a verified verbatim span, and the **cited work is retraction-clear**
  (`f8_notice=False`). **"Notice-clear" throughout means *no retraction*, not "no notice of any kind":** a
  *correction/EoC* on the cited work is detection-eligible but **caps routing at Path B**. Independence
  **false ⇒ `not_F5`**; independence/comparability **unknown**, an unresolved formal notice on the
  replacement, or an unverifiable span ⇒ the candidate **cannot qualify** (→ `UNJUDGEABLE`).
  Independence, recency (date), contradiction *direction*, and *confidence* are **detector-layer
  preconditions**, not fields the frozen `TemporalAssessment` enforces; they **map into the narrower
  frozen engine contract**, which itself checks only `same_claim_or_outcome=True`,
  `comparable_population=True`, `f8_notice=False`, a nonblank `newer_work_id`, nonempty verbatim
  `evidence_spans`, and an in-range `claim_index` on a `SUPPORTED` claim.
- **Repair routing (outside the engine), for a detected F5:**
  - **Path B — escalate (DEFAULT, and the *only* path shipped in the Aug-1 preprint).** Surface both
    papers with quoted contradicting spans; escalation flag TRUE; **no autonomous replacement**. Path B is
    driven by **absent attestation, not low confidence** — a *high-confidence* contradiction still routes to
    Path B when no field-level attestation exists. The author adjudicates. Path B is **correct behavior**, not a failure.
  - **Path A — autonomous replacement (DEFERRED; attestation-gated).** Proposed only when a **qualifying field-level supersession attestation** — a *separate evidentiary role,
    not necessarily a separate document* (§6-I) — exists *and* the quality/date gate holds. Presented as
    **N worked repairs**, never as a precision rate, until a labeled corpus exists. **Eligibility ≠
    deployment:** passing the hypothetical Path-A gates sets `path_a_eligible=True`, but while
    `deploy_path_a=False` the emitted **`f5_path` stays `B`** and `path_a_deployed=False`; Path A becomes
    the live route only when Roberts unlocks and `deploy_path_a=True`.
- Both paths are F5. Path A/B is recorded in the F5 record + measurement schema, **never** in the frozen
  engine. Locked: report Path A and Path B **separately**; Path-A **coverage** is mandatory so an
  "escalate everything" policy cannot look strong.

---

## 5. Retrieval infrastructure (injected seams + concrete live backends)

**Injected seam contracts (faked in tests):**

| Seam | Signature (conceptual) | Returns |
|---|---|---|
| `retrieve_superseding_candidates` | `(cited_meta, claim, *, after_date, as_of_date) -> RetrievalResult` | `after_date` = cited pub date; `as_of_date` = the assessment cutoff (**distinct** from `cited_date`). Enforce `cited_date < candidate_date ≤ as_of_date`. `RetrievalResult{candidates: Seq[CandidateWork]; adequacy ∈ {adequate,inadequate,empty}; status ∈ {ok,failure,partial}; query_hash (includes after_date + as_of_date); rationale}` — `adequacy`/`status` drive the UNJUDGEABLE-vs-confident-negative rule (§8, §9-1). Each `CandidateWork`: id, title, abstract, pub_date, authors, mesh, tier_hint |
| `fetch_comparability_source` | `(work_id, *, as_of_date) -> ComparabilitySource` | `ComparabilitySource{abstract, methods, results, protocol, registry_record, publication_type}` **pinned to the source version available at `as_of_date`** (no post-cutoff abstract/registry/protocol/correction content may leak into a historical assessment) — the materials needed to judge primary-outcome status, eligibility, composite definitions, and subgroup prespecification (§18a); the abstract/full-text within it also serves the verbatim-span check. All fields are **optional** (a study need not have a registered protocol/registry — e.g. observational work). **Sufficiency is fact-based: if the available authoritative materials cannot establish a required fact (primary-outcome status, eligibility, composite definition, subgroup prespecification), that axis is `uncertain`** — present-and-sufficient materials suffice even when others are absent. |
| `check_formal_notice` | `(work_id, *, as_of_date) -> NoticeStatus` | `NoticeStatus{notice_kind ∈ {none, retraction, correction, eoc}; notice_resolution ∈ {resolved_clear, flagged, unresolved}; date}` — run on cited **and** candidate, evaluated **as of `as_of_date`** (reproducible historical assessment); populates the `*_notice_kind` / `*_notice_resolution` packet fields |
| `classify_evidence_tier` | `(work_meta) -> EvidenceTier` | ordinal OCEBM tier (below) |
| `find_supersession_attestation` | `(cited_meta, claim, replacement_work_id, *, as_of_date) -> Optional[Attestation]` | a separate citable field-level attestation — **major-guideline revision or systematic review/meta-analysis only** — that explicitly concludes reversal/supersession **and is bound to the proposed replacement** (`Attestation.replacement_work_id`), with `replacement_date ≤ attestation_date ≤ as_of_date`. Returns `Attestation.attestation_conclusion_span` — the verbatim reversal/supersession conclusion, **validated against the attestation source version fetched as of `as_of_date`** (this is the field §10 requires). A guideline/review alone, absent an already-detected qualifying F5 contradiction and an identified replacement, **cannot create Path A**. A pre-registered failed replication is returned as contradiction/supporting evidence, **not** an attestation. |
| `judge_contradiction` | `(cited_source: ComparabilitySource, candidate_source: ComparabilitySource, claim) -> ContradictionJudgment` | strict-JSON the **model emits**: `directional_contradiction, claim_match ∈ {match,mismatch,uncertain}, outcome_relation ∈ {same,not_same,uncertain}, population_relation ∈ {equivalent,encompassing_direct,encompassing_without_qualifying_direct_evidence,narrower,disjoint,unclear}, cited_direction, candidate_direction, magnitude, cited_finding_span (⊂ cited_source), candidate_contradiction_span (⊂ candidate_source), confidence` (each span **separately validated** against its own source). **`comparability_decision` and the frozen-engine booleans `same_claim_or_outcome` / `comparable_population` are NOT model-chosen** — code derives them deterministically from (`claim_match`, `outcome_relation`, `population_relation`) per §18a.6. |

**Evidence tiers (OCEBM 2011):** `systematic_review_or_meta_analysis > rct > prospective_cohort >
retrospective_cohort > case_control > cross_sectional > case_series_or_report > preprint_unreviewed`.
Ties break conservatively downward.

**Concrete live backends (Colab/production only — never imported by module/tests):**
- **PubMed / NCBI E-utilities** — primary candidate generation: `esearch` with the cited PMID's MeSH
  major topics + `datetype=pdat&mindate=<cited year>&maxdate=<as_of_date>`; `efetch` for abstract + publication type. (Email
  `aston.hliu@gmail.com`; Colab secret `NCBI_API_KEY` — *from prior memory; confirm the exact values before the live run.*)
- **scite `search_literature`** — (a) `retraction_notices` for `check_formal_notice`; (b) Smart Citations
  classified **`contrasting`** as a **precision-oriented second candidate stream** — a curated pointer to
  papers disputing the cited finding. **Union** it with MeSH (contrasting-citation recall is low: it only
  finds superseders that *cite* the target). Note: whether a superseder cites the original is **orthogonal
  to independence** (§6-D), which is authorship/cohort-based — reversal papers routinely cite what they
  overturn.
- **bioRxiv / medRxiv** — recency; preprints are tier `preprint_unreviewed` → **Path B only**.
- **Attestation-search stream [council/consensus]:** a *distinct* retrieval pass for the Path-A gate —
  systematic reviews/meta-analyses and major-guideline revisions on the claim's topic (PubMed
  `Publication Type` = Guideline / Meta-Analysis / Systematic Review; guideline repositories). Kept
  separate from the contradiction streams; it supplies the **field-level attestation**, not a mere contradiction.
- **Recall is not assumed complete.** MeSH ∪ contrasting-citations is the *baseline* contradiction
  channel only; add **citation chaining** (forward/backward from the cited work and from known
  contradictions) and claim/outcome-oriented retrieval to catch **terminology drift** and superseders that
  neither share major MeSH headings nor cite the original.
- **Explicitly NOT MonoT5** for F5 candidate ranking.

**Determinism / reproducibility:** pin the MeSH query + date window (`after_date`, `as_of_date`); record + cache the candidate ID
list; LLM `temperature=0`, pinned model id; store prompt/response **hashes**; `validate_f5_record`
**replay guard** (mirrors `validate_f7_record`) re-derives the route from stored facts + policy, fails
closed on drift. **Retrieval-adequacy criteria must be frozen and preregistered before any confident
negative:** search failure, empty retrieval, or a candidate set with no judgeable full evidence spans
yields `UNJUDGEABLE`, never `NO_QUALIFYING_CONTRADICTION`.

**Valid `RetrievalResult` combinations.** Only `status=ok ∧ adequacy=adequate` (nonempty, every candidate
fetched and judgeable) can license a confident negative — and only when every candidate is nonqualifying.
`status=ok ∧ adequacy=empty` (ran cleanly, zero candidates) → `UNJUDGEABLE`. `status=partial` (some
sources/candidates failed or truncated) → `UNJUDGEABLE`, unless a judgeable candidate already qualifies
(→ F5). `status=failure` → `UNJUDGEABLE`. `adequacy=adequate` with an empty candidate list is **not** a
valid combination (empty ⇒ `adequacy=empty`); `adequacy=inadequate` never licenses a confident negative.

---

## 6. The supersession model and the open decisions (recommendations, not derivations)

Project docs conflict; per protocol §9.3/§18 the software **stores every fact and must not derive a
*reportable* F5/Path-A verdict — nor deploy Path A — until Roberts freezes the rules.** Non-reportable
**development** derivation *is* allowed (offline, `deploy_path_a=False`; see §13). Recommendations below
are for Codex + Roberts ratification.

### Conflict A — "all three" vs "any one" for Path A
Amendment 3 / `TAXONOMY_F5_section.md`: all three. `TAXONOMY_DECISION_RULES.md`: any one. (§18-4 lock.)
The two docs also enumerate *different* criteria (see Conflict B): `TAXONOMY_F5_section.md` = {directional
contradiction, ≥2-yr gap, tier upgrade}; `TAXONOMY_DECISION_RULES.md` = {higher tier, larger n, failed
pre-registered replication, guideline revision}, with directional contradiction split out as detection
and **no date-gap criterion at all**. Rec B resolves both at once.
> **REC A — conjunctive (all must fire), plus the Rec I attestation.** Path A is the highest-risk action;
> §8.5 gives it "the strictest repair threshold"; it is the preregistered position. Any failure → Path B
> (recall not lost, only autonomy).

### Conflict B — *which* criteria, and the quality-hierarchy fix [council-hardened]
> **REC B — layered gate.** Directional contradiction is **detection** (Conflict C). The Path-A gate is
> **both**: **(i)** publication-date gap ≥ 2 years; **(ii)** superseding evidence at an
> **equal-or-higher OCEBM tier** than the cited paper. A *substantially larger n* is **a non-required selection preference, not an eligibility gate** (§9-11 order step 2, Roberts-deferred) — it applies **only at equal-or-higher
> tier**: it never promotes a lower-tier design, substitutes for the attestation, or permits
> cohort-beats-RCT (fixing the disjunction-in-a-conjunction bug the council caught). The
> **attestation (Rec I) and the replacement are recorded separately** — a guideline is *not* coerced into
> an invented OCEBM tier. Roberts must freeze the **tier mapping (question-appropriate), the conservative
> tie rule, the date-gap calculation, and the larger-`n` selection-preference threshold** before any derivation.

### Conflict C — is "directional contradiction" detection or a Path-A criterion?
> **REC C — detection.** It defines `QUALIFYING_CONTRADICTION` and matches the frozen engine seam.
> Treating it as a Path-A criterion double-counts it.

### Conflict D — `judgment_engine.py:397` — may a `WEAKER_STRENGTH` (F4) claim be an F5 target?
> **REC D — keep SUPPORTED-only (no) for v1.** Preserves the **frozen** engine; precision-first; the
> "cited paper supported the claim" premise is ambiguous for a weaker-strength claim. Detector assesses
> temporal contradiction **only on `SUPPORTED` claims**; otherwise emits `NO_QUALIFYING_CONTRADICTION`.
> Document the superseded-weak-finding case as a **known limitation**; add **one additive pinning test**
> at build (no engine-module change).

### Rec I — Path-A attestation gate [council-added; the core hardening]
> A single more-recent primary study is a **candidate**, not supersession. **Path A (autonomous
> replacement) requires a separate, temporally-bounded (on-or-after; `replacement_date ≤ attestation_date ≤ as_of_date`), citable field-level attestation that explicitly concludes
> the finding was reversed/superseded**: (a) a major-guideline revision, or (b) a systematic
> review/meta-analysis. A **pre-registered failed replication does NOT by itself qualify** — it can still
> be one primary study; it is recorded as contradiction/supporting evidence and gates Path A **only when
> incorporated into** a qualifying attestation (a)/(b). Absent such an attestation, every detected case is
> **Path B**, regardless of how compelling a single newer study looks. This keeps recency-of-one-study
> from triggering an irreversible swap. **Path A is order-dependent:** a qualifying F5 contradiction must
> be detected *first* and a replacement identified; the attestation must then **bind to that replacement**
> (`replacement_work_id`). An attestation alone (e.g. a guideline) with no detected qualifying
> contradiction and no replacement is **not F5 and not Path A**. **Temporal bounds** (`as_of_date` = the
> assessment cutoff, **distinct from** `cited_date`): every candidate satisfies
> `cited_date < candidate_date ≤ as_of_date`, and the Path-A attestation satisfies
> `replacement_date ≤ attestation_date ≤ as_of_date` (same-day permitted; `≤` is intentional, **not**
> "post-dated"). **Non-circularity:** the attestation must be an admissible *field-level* type
> (guideline / SR / meta-analysis) — a single primary-study contradiction is never its own attestation
> (Rec I). It **may** coincide with the replacement (`attestation_source_id = selected_replacement_work_id`)
> **only when all hold**: (i) the attestation is a **systematic review / meta-analysis** (never a
> guideline); (ii) that same work **independently satisfies both the contradiction and the replacement
> gates**; and (iii) a **separately-validated `attestation_conclusion_span`** explicitly concludes
> reversal/supersession. Principle: **separate evidentiary role, not necessarily separate document.**
> **Every** attestation (same- **or** cross-document) must carry a separately-validated
> `attestation_conclusion_span` that explicitly concludes reversal/supersession — the reversal conclusion
> must be auditable regardless of whether attestation and replacement are the same work.

### Rec J — Preprint scope [council-added]
> **Ship detection-first + Path B for Aug 1.** Path A is present in the design and demonstrated as **N
> worked repairs** (existence proof), but **no Path-A precision number is reported** until a labeled
> corpus exists. F5's preprint contribution = *the category exists, occurs naturally, the frozen engine
> detects and routes it, and here are N end-to-end repairs.*

---

## 7. Decision table (per claim; detector output → engine result)

| Claim support state | Temporal finding | `TemporalState` | Engine result | Repair route |
|---|---|---|---|---|
| `SUPPORTED`, directional contradiction, same outcome, comparable pop, independent, notice-clear, verbatim span | detected (CANDIDATE) | `QUALIFYING_CONTRADICTION` | `F5` | `path_a_eligible` **iff** attestation + tier/date gate — but emitted **`f5_path` = B until `deploy_path_a=True`**; otherwise Path B |
| `SUPPORTED`, retrieval **adequate + nonempty + fully judgeable**, every candidate nonqualifying | none | `NO_QUALIFYING_CONTRADICTION` | no F5 | — |
| `SUPPORTED`, **empty / inadequate / partial retrieval**, or **no judgeable candidate qualifies while ≥1 candidate stays unjudgeable**, or ambiguous / low-confidence / span unverifiable / comparability uncertain | undecidable | `UNJUDGEABLE` | held | — |
| `WEAKER_STRENGTH` (F4) | out of scope (Rec D) | `NO_QUALIFYING_CONTRADICTION` | F4 stands | — |
| `UNESTABLISHED` (F6) / `UNJUDGEABLE` support | N/A | `NO_QUALIFYING_CONTRADICTION` | F6 / held | — |

---

## 8. Precision-first posture (deliberate)

- Confident negative (`NO_QUALIFYING_CONTRADICTION`) only when retrieval was **adequate, nonempty, and
  fully judgeable with every candidate nonqualifying**. Otherwise — empty / failed / inadequate / partial
  retrieval, **or** no judgeable candidate qualifies while ≥1 candidate stays unjudgeable → `UNJUDGEABLE`
  (held), never "no F5". A judgeable candidate that *does* qualify → F5 regardless of other unjudgeable
  candidates.
- Any ambiguity in direction, outcome-match, comparability, independence, or span verification →
  `UNJUDGEABLE`. Unknown ≠ absent.
- Path A requires the full conjunctive gate **plus** attestation; any doubt drops to Path B. Path B never
  autonomously replaces.
- Never a confident negative on an unbuilt/ungated path.

### 8a. Operating mode — discovery (high-recall) vs deployment (precision-first) [ZD 2026-07-17]

F5 runs in one of two modes, set by `F5Policy.mode`. **The §8 / §9 precision-first "any doubt →
`UNJUDGEABLE`" holds govern *deployment* mode; *discovery* mode overrides them as below.**

- **Discovery mode (the preprint's mode; `deploy_path_a=False`).** F5's job here is to **generate the
  dataset**: a **high-recall candidate generator** feeding a human **annotation queue**. Two axes are
  computed **independently**:
  - **`TemporalState`** (the engine verdict) derives from the **complete detector contract** — claim is
    `SUPPORTED`, directional contradiction, `same_claim_or_outcome`, comparable population, independence,
    retraction-clear (`f8_notice=False`), verified verbatim span, confidence ≥ floor — **aggregated over
    retrieval** per the §5 `RetrievalResult`/adequacy rule. **`comparable` is necessary but not
    sufficient:** *every* contract element must pass for `QUALIFYING_CONTRADICTION`.
  - **`discovery_disposition ∈ {surface, do_not_surface, unassessable}`** derives from the **recall policy**
    (the low `confidence_floor`), independently of the engine verdict.

  Mapping:
  - **Fully qualifying** (whole contract passes) → `QUALIFYING_CONTRADICTION` **+ `surface`** (engine `F5`).
  - **Ordinary uncertainty** (a contract element unknown/borderline) → **`UNJUDGEABLE`** + *optionally*
    **`surface`** (recall policy decides) — **not** an emitted F5; Path B only **after a human confirms**.
  - **Unassessable** (retrieval failure/empty, insufficient source material / unverifiable span) →
    **`UNJUDGEABLE` + `unassessable`**.
  - **Hard-nonqualifying candidate** (clear mismatch — `not_same` outcome, `not_comparable` population,
    confirmed non-independence, below floor) → **`do_not_surface`** *for that candidate*. This is **not** a
    claim-level negative: the claim becomes a confident **`NO_QUALIFYING_CONTRADICTION`** **only after the
    entire candidate set satisfies the §5 retrieval-adequacy rule** (adequate, nonempty, fully judgeable,
    every candidate nonqualifying).

  This avoids answering "can't tell" to everything (ZD Q8) **without** forcing the engine to fire F5 on
  uncertainty. **Precision** (and, if feasible, **recall**/**prevalence**) is measured by the **two hired
  annotators** on the surfaced set — the detector never self-scores.
- **Deployment mode (future; requires the Roberts locks + `deploy_path_a=True`).** The full §8/§9
  precision-first rules apply: independence-unknown/borderline → `UNJUDGEABLE`, attestation-gated Path A,
  reportable verdicts. Nothing autonomous ships until then.

This **scopes — does not weaken —** the consensus rules: no *autonomous/reportable* F5 is emitted on
unknown independence; discovery merely **queues** such cases for a human. **Empty/failed retrieval → `UNJUDGEABLE`** holds in
**both** modes (you cannot surface what you never retrieved).

**Project-wide posture (ZD 2026-07-17):** the whole project should **start high-recall and gradually
narrow to high-precision**. F5's discovery→deployment path is one instance of that trajectory.

---

## 9. Edge cases (exhaustive)

1. **Retrieval → confident negative vs held.** A confident negative (`NO_QUALIFYING_CONTRADICTION`) requires an **adequate, nonempty, fully judgeable candidate set in which every candidate is nonqualifying**. If **no judgeable candidate qualifies** but retrieval is empty / failed / unavailable / inadequate / partial, **or** the set is not fully judgeable (≥1 residual unjudgeable candidate) → **`UNJUDGEABLE`**. Empty retrieval is *never* a confident negative. A judgeable candidate that *does* qualify → **F5**, regardless of other unjudgeable candidates. (Adequacy criteria are Roberts-frozen and preregistered — §5, §14.)
2. **Contradiction on a different outcome/endpoint:** `outcome_relation = not_same` → not qualifying (§18a).
3. **Different/narrower/disjoint population** (species, stage, age): `population_relation ∈ {narrower, disjoint}` → not qualifying; if uncertain → `UNJUDGEABLE` (§18a).
4. **Refinement, not reversal** (1.5%→1.1%): not directional → not qualifying. Primary precision guard.
5. **Tightened hedge / added caveat:** not a reversal → not F5.
6. **Author overlap between cited and superseding work:** weighs against independence, but whether author overlap *alone* fails (AND rule) or can be rescued by a genuinely independent cohort/data source (OR rule) is the **OPEN Roberts combinator (Lock D)** — do not hard-code it; until it is frozen, an author-overlap-but-independent-data case is `UNJUDGEABLE`. A **confirmed** non-independent case (e.g. same-cohort re-analysis, §9-26) → **`not_F5`** (Path B only follows a valid detection). Independence *unknown* → `UNJUDGEABLE`.
7. **Evidence-tier *downgrade*** (RCT "contradicted" by case series/cohort): fails (ii) → Path B. (The bug the council caught: a bigger cohort does not beat a smaller RCT.)
8. **Date gap < 2 years / same-work preprint→journal drift:** fails gap → Path B; version drift is not independent → not even detection.
9. **Preprint contradiction:** tier `preprint_unreviewed` → detection possible, **Path B only**.
10. **Guideline revision:** a guideline is an **attestation, not a detection and not a replacement**. It gates Path A *only* when a qualifying F5 contradiction has **already** been detected and a `selected_replacement_work_id` identified, with the guideline bound to that replacement. A guideline with **no** detected qualifying contradiction/replacement ⇒ **no Path A and no F5-by-detection** (the engine's F5 requires a `QUALIFYING_CONTRADICTION` from a superseding paper on the same outcome).
11. **Multiple qualifying contradictions — two deterministic selections.** **(a) Detection representative:** the engine `newer_work_id` = `selected_contradiction_work_id`, chosen among **all** candidates satisfying the full detector contract (for Path A *and* Path B) by the stable order highest tier → most-recent → stable `work_id`. **(b) Replacement:** compute Path-A eligibility **per candidate first** (each needs its *own* bound attestation + tier/date gate), **then** pick `selected_replacement_work_id` among the *eligible* candidates in the deterministic order (1) highest OCEBM tier; (2) **[Roberts-deferred lock]** a substantially-larger-`n` **preference** at equal tier (skipped until Roberts ratifies the threshold); (3) most-recent; (4) stable `work_id` (final determinism tie-break). The larger-`n` step is a *substantive quality preference*, **not** a determinism device — `work_id` only breaks residual ties. Never pick a candidate lacking a bound attestation; `selected_replacement_work_id` is **null** when none is eligible (→ Path B). Record all candidates in `candidate_assessments` (§10). Candidates disagree (some confirm) → genuine dispute → **Path B**.
12. **Superseding candidate itself retracted/flagged:** a candidate with `candidate_notice_kind ≠ none` (retraction/correction/eoc) or `candidate_notice_resolution ∈ {flagged, unresolved}` is **never a valid replacement** and is retained as an **unjudgeable audit row** (not silently dropped). If **no other candidate qualifies**, the overall result is **`UNJUDGEABLE`** — a flagged candidate can never license a confident negative. (Same `notice_kind` / `notice_resolution` split applies to the cited work.)
13. **Claim not `SUPPORTED`:** never emit `QUALIFYING_CONTRADICTION` (Rec D) — would trip the engine guard.
14. **Contradiction targets a peripheral finding, not the claim:** tie `judge_contradiction` to the specific `claim_index`; contradicting some other finding is not F5 for this claim.
15. **Multi-claim: one superseded + another `UNESTABLISHED`:** primary `F6`, findings `("F6","F5")` (engine-verified). F5 never erases the evidence fault.
16. **`claim_index` out of range/`None`:** produce a valid index or fail closed to `UNJUDGEABLE`.
17. **Spans not verbatim in their sources:** the **two** spans are validated **separately** — `cited_finding_span` must be a substring of the **cited** work's `ComparabilitySource`, and `candidate_contradiction_span` a substring of the **candidate's** — either failing → `UNJUDGEABLE` (`span_unverifiable`). Never assert an ungrounded F5.
18. **Candidate lacks sufficient source material or a verifiable span:** skip that candidate; but a confident negative needs a *fully judgeable* set, so if **any** retained candidate stays unjudgeable (and no judgeable one qualifies) → **`UNJUDGEABLE`**, never `NO_QUALIFYING_CONTRADICTION` — you did not actually clear the claim.
19. **Candidate predates the cited paper:** reject; newer must post-date.
20. **Formal *retraction* on the cited paper:** an F8 handled upstream; should never reach F5. Defensive check sets `f8_notice=True`, refuses F5, flags a routing inconsistency. **(Not an F5/F8 boundary — a data precondition.)**
21. **Correction/EoC on the cited paper (no retraction):** not F8, not F5's routing problem; v1 conservative — still F5-detection-eligible but **cap at Path B** (do not autonomously replace a paper under an active EoC).
22. **Low model confidence:** `UNJUDGEABLE`. Threshold is a calibration lock (§14).
23. **Retrieval nondeterminism:** pinned query + cached candidate IDs + `temp=0` + hashes + `validate_f5_record`.
24. **Cross-type pair** (the citing claim and cited work concern different entity *types* — e.g. the claim is about a drug but the cited paper is about a gene): this is a wrong-entity (F7) concern, not F5. F5 stays **`UNJUDGEABLE`** (never a confident F5 or negative) and defers to the F7 discriminator.
25. **Over-escalation gaming:** caught by the **Path-A coverage** metric (§14).
26. **Newer paper re-analyzes the same cohort/data:** fails independence → not a new-evidence supersession.
27. **F5 sole finding:** primary `F5`, `findings == ("F5",)` (engine-verified).
28. **Attestation but no detected contradiction/replacement:** an attestation alone (guideline or review) **cannot manufacture F5 or Path A** — detection is prerequisite. No `QUALIFYING_CONTRADICTION` + no `replacement_work_id` ⇒ **not F5, not Path A** (record as a manual-review lead only, never an emitted F5). This closes the guideline-only path that would otherwise conflict with §5 and Rec I. **[consensus edge]**
29. **Reversal later re-reversed** (the superseder is itself overturned): non-stationarity — record `as_of_date`; a Path-A proposal is timestamped and may itself become stale. Prefer attestation (guideline/meta-analysis) which lags and is more stable. **[council edge]**
30. **Newer result merely nonsignificant (no opposite direction):** directional contradiction is **not** established — *absence of evidence is not reversal* → not qualifying. **[lock-E v1]** (Outcome/population comparability is governed in full by the **§18a** rule; its edge-case table extends cases 2–4, and a preliminary **claim-match gate** on intervention/comparator precedes it.)

---

## 10. Typed evidence packet (`F5Record`, two levels)

**Two-level** so it retains every candidate's audit state (multiple contradictions, flagged audit rows,
per-candidate Path-A eligibility — §9-11, §9-12).

**Claim-level `F5Record`:** `claim_index, claim_text, claim_population_text, intervention_or_exposure,
comparator, cited_work_id, cited_date, cited_tier, cited_notice_kind∈{none,retraction,correction,eoc},
cited_notice_resolution∈{resolved_clear,flagged,unresolved}, as_of_date,
retrieval_adequacy∈{adequate,inadequate,empty}, retrieval_status∈{ok,failure,partial},
retrieval_query_hash (includes after_date + as_of_date), candidate_assessments: Seq[CandidateAssessment],
selected_contradiction_work_id (deterministic representative qualifying contradiction → engine `newer_work_id`),
selected_replacement_work_id (**nullable**; chosen only among Path-A-eligible candidates),
same_claim_or_outcome: Optional[bool] (from the selected *contradiction*; null when none selected), comparable_population: Optional[bool] (from the selected *contradiction*; null when none selected),
cited_finding_span: Optional[str], candidate_contradiction_span: Optional[str] (verbatim; from the selected *contradiction*; null when none selected),
selected_surfaced_candidate_work_id (**deterministic**: highest surfacing-confidence surfaced candidate, tie-break stable `work_id`; null if none surfaces),
discovery_confidence (= that surfaced candidate's confidence; non-null whenever any candidate surfaces, else null),
path_a_eligible(bool; ∃ an eligible candidate),
path_a_deployed(bool; requires deploy_path_a=True), discovery_disposition (claim-level rollup),
f5_path∈{A,B,not_F5,unknown}, temporal_state∈{QUALIFYING_CONTRADICTION,NO_QUALIFYING_CONTRADICTION,UNJUDGEABLE},
confidence: Optional[float] (= the selected contradiction's confidence; null when none selected), reason, model_version, f5_policy_version, comparability_policy_version, verifier_result∈{confirmed,rejected,unjudgeable,failure,not_run}, verifier_model_version, verifier_evidence_hash, reportable`.
Coverage/F4 evidence is **appended to, never overwritten**.

**Claim-level rollups.** `discovery_disposition` = `surface` if **any** candidate surfaces; else
`unassessable` if retrieval is unassessable **or** any unresolved candidate is unassessable; else
`do_not_surface`. Claim-level `confidence` = the **selected contradiction's** confidence, or **null** when
no contradiction is selected (confident negative or all-`UNJUDGEABLE`).

**Per-candidate `CandidateAssessment` (one per retrieved candidate):** `candidate_work_id, candidate_date,
candidate_tier, candidate_notice_kind∈{none,retraction,correction,eoc},
candidate_notice_resolution∈{resolved_clear,flagged,unresolved}, claim_match∈{match,mismatch,uncertain},
outcome_relation∈{same,not_same,uncertain},
population_relation∈{equivalent,encompassing_direct,encompassing_without_qualifying_direct_evidence,narrower,disjoint,unclear},
comparability_decision∈{comparable,not_comparable,uncertain}, independent(+basis), directional_contradiction,
cited_direction, candidate_direction, contradiction_magnitude, date_gap_years, tier_relation, criteria_fired, confidence,
cited_finding_span(verbatim; ⊂ cited_source), candidate_contradiction_span(verbatim; ⊂ candidate_source), discovery_disposition∈{surface,do_not_surface,unassessable},
attestation∈{major_guideline_revision,systematic_review,meta_analysis,none}, attestation_source_id,
attestation_date, attestation_replacement_work_id (binds attestation↔this candidate), attestation_conclusion_span(verbatim; ⊂ attestation source; explicitly concludes reversal/supersession — **required for *every* attestation**, same- or cross-document),
failed_replication_evidence(bool; supporting only — not an attestation), path_a_eligible(bool), reason`.

**Level assignment.** Retrieval aggregation, the selected replacement, the final `TemporalState`, and the
route are **claim-level**; notice, comparability, independence, contradiction, disposition, attestation,
and eligibility are **per-candidate**. The §18a.5 signatures attach accordingly: the **cited-work**
outcome/population signature once at claim level, each **candidate-work** signature + relation fields
inside its `CandidateAssessment`.

**Engine mapping (claim level).** The engine's `newer_work_id` = `selected_contradiction_work_id`;
`same_claim_or_outcome=True` / `comparable_population=True`, the `evidence_spans` (= `(cited_finding_span, candidate_contradiction_span)`), and `confidence` derive
**from that selected contradiction — not the replacement** — so a `QUALIFYING_CONTRADICTION` needs only a
qualifying *contradiction*, not a Path-A replacement (§18a.6). `selected_replacement_work_id` is set only
for Path A and is **null on Path B**. `as_of_date` and all `candidate_assessments` are stored for replay
(`validate_f5_record`).

**Map to `measurement/measure.py` (F5/F8 operationally separated):** keep `contradiction_exists`, `f5_path ∈
{A,B,not_F5,unknown}`; **drop** `formal_retraction_notice`/`timing_gap_days`/`timing_gate_met` from the
F5 schema (they belong to F8). An `F5` label requires `contradiction_exists is True AND f5_path ∈ {A,B}`.

---

## 11. Aggregation (already in the engine)

Per-claim assessments roll up through the frozen `decide_judgment`. F5 is lowest precedence; it surfaces
in `findings` without displacing F7/F6/F4/F3. Sole finding → primary `F5`.

---

## 12. Fail-closed & offline

Strict-JSON output (replicated leaf parser); malformed/off-enum → `ValueError` → orchestrator
quarantines. Well-formed but unknown/unverifiable/low-confidence → `UNJUDGEABLE` (held). All I/O
injected; **no network/paid call in module or tests.**

---

## 13. `F5Policy` (advisor-locked, parameterized) + development vs formal mode

One frozen-at-runtime `F5Policy`: `mode ∈ {discovery, deployment}`(=discovery); `path_a_rule ∈ {all_must_fire, any_sufficient}`; `date_gap_years`(=2);
`tier_rule`; `require_attestation_for_path_a`(=True); `attestation_types`(={major_guideline_revision, systematic_review, meta_analysis}; a failed
replication is supporting evidence, not an attestation); `independence_rule`;
`comparability_rule`; `confidence_floor`(low in discovery; Roberts-set in deployment); `eoc_caps_at_path_b`(=True); `deploy_path_a`(=False);
`policy_version`. Ratifying a
different rule is a **config change, not a rewrite**. Mirroring F4: F5 runs **development mode
(`reportable=False`)** until Roberts freezes the locks, they are preregistered, and calibration
completes.

**What "gated" means (single rule).** The module and its injected offline unit tests **may be implemented
now** in **development mode**: they may emit **non-reportable candidate-F5 and *hypothetical* Path-A
eligibility** in offline tests, under a hard **`deploy_path_a=False`** — no autonomous replacement is ever
deployed and nothing is `reportable`. **Roberts's locks gate formal/reportable derivation and any live
Path-A deployment**, *not* whether code and offline tests may exist. So `F5_SUPERSESSION_SPEC.md` and the
offline implementation may proceed now; only reportable/deployed use waits for the locks. This one rule
supersedes any earlier "emits no F5 at all" phrasing.

---

## 14. Calibration, sourcing & reporting (non-reportable until locked) [council-hardened]

- **Sourcing the worked/calibration set (inverted search):** start from **published medical-reversal
  catalogs** (Prasad & Cifu, *Ending Medical Reversal*; Ioannidis; JAMA/BMJ reversal lists), take each
  reversed finding's **original** paper, and walk its **forward citations** to find documents that still
  cite it as support. This builds F5 examples deterministically. **Per the no-circularity lock these are
  existence proofs + codebook calibration + worked repairs only — never a detection-precision
  denominator, never hunted with the detector's own flags.**
- **Metrics (never pooled; Path A/B separate):** `f5_random_prevalence`, `f5_detection_precision`,
  `f5_path_a_repair_precision`, `f5_path_a_coverage`, `f5_path_b_escalation_appropriateness`,
  `f5_path_b_missed_clear_supersession_rate`, routing confusion matrix. **Path-A coverage mandatory.**
  For the **preprint, none of these is reported as a headline number** (Rec J) — existence + N worked
  repairs instead. **Do not report confident-negative (`NO_QUALIFYING_CONTRADICTION`) performance until
  the retrieval-adequacy criteria (§5) are frozen, preregistered, and validated.**
- **Evaluation model [ZD 2026-07-17 — corrects a prior memory-derived error].** The **LLM/detector
  produces the answers** (candidate-F5s, high-recall in discovery mode); **two hired human annotators then
  independently check precision** on the surfaced set. This two-annotator precision check is **crucial and
  required**. **Recall** and **prevalence** are reported **if** the annotated set supports them. The
  detector never self-scores.
- **Retracted claim (memory error) + the correct model.** Earlier text asserted a *solo-annotation*
  architecture with "no κ / no pre-pilot." That was **stale `memory.md`** and is **wrong**: the registered
  docs (`PREREGISTRATION.md`, `TAXONOMY_DECISION_RULES.md`, `docs/F3_F7_MEASUREMENT_PROTOCOL.md`) specify a
  **two-annotator model** — two independent annotators double-annotate an IAA subset, reported via **Cohen's
  κ + Gwet's AC1** (κ≥0.60 gate, incl. the F3/F6 & F4/F6 pre-pilot), with the author's re-label as a
  *secondary* test-retest. So κ, AC1, and test-retest are all real — they were merely mis-framed as "solo."
  Roberts's role here is the **advisor who freezes the §18 locks**. F5's positive class is sparse, so the
  preprint leads with **existence + N worked repairs**; recall/prevalence follow if the annotated set
  supports them.
- **Independent positive-only verifier (formal/reportable only).** A *formal/reportable* F5 positive —
  detection **or** a Path-A repair — additionally requires an **independent positive-only verifier** (a
  distinct model/pass that only confirms positives), mirroring F4 formal-mode and F7. Development/
  discovery-mode outputs are non-reportable and exempt. Store `verifier_result`, `verifier_model_version`,
  and a `verifier_evidence_hash` (evidence/response hash) in the record — **invariant:
  `reportable=True ⇒ verifier_result=confirmed`.** (`rejected` = active disagreement; `unjudgeable`/`failure`
  = could not verify; all non-`confirmed` states are non-reportable.)
- **Dropped from F5 scope:** the `f5_f8_boundary` task/metric (F5/F8 operationally separated, §0).

---

## 15. Regression guards & frozen constraints

- **Frozen, do not touch:** `judgment_engine.py` (`671de1e5…`), `judgment_band.py` (`7d81e5e0…`),
  `coverage_v2`/`band_prompts.py` (presence-only lock), F2.
- **F2 regression-guard PMIDs unaffected:** `31665581`, `16639420`, `18152150`, `27665045`,
  `25750229`, `32355637`, `22926653` are F2 `WRONG_PAPER` cases decided in the **existence-check
  pre-classifier** (they never enter the F3–F7 band). F5 does not touch F2, so each must keep producing
  its expected F2 verdict.
- **Suites stay green:** `test_judgment_engine`, `test_judgment_band`, `test_band_prompts`,
  `test_f3_provenance`, `test_f4_strength`, `test_f7_entity`, `test_judgment_run`, + new `test_f5_*`
  (full `cre/f1` = 683 with optional deps before F5 additions).
- **Discipline:** path-based loading (no `__init__.py` in `cre/`; restart Colab after push); `author_match`
  tri-state tested with `is False`; naturally-occurring data only; targeted amendments only.

---

## 16. Non-goals / out of scope

No change to engine, `judgment_band`, `coverage_v2`/`band_prompts`, F2. No MonoT5 for F5. **No Path-A
autonomous replacement in the preprint beyond N worked repairs.** No F5-vs-F8 boundary task. **No synthetic calibration/evaluation data** — synthetic *offline unit-test
fixtures* are allowed. **Non-reportable development derivation is allowed; only *formal/reportable*
derivation and *live Path-A deployment* are locked** until Roberts freezes the rules — implementation and
injected offline tests may exist, and runtime derivation fails closed (non-reportable, `deploy_path_a=False`)
until then (see §13). Live/paid
retrieval is a later, authorization-gated step.

---

## 17. Open questions to pressure-test (for the relay) — [HISTORICAL]

> **[HISTORICAL — these were the pre-deliberation open questions. All are resolved by the 2026-07-16
> council (§20) and the 2026-07-17 Codex + Claude-Opus consensus (§21). Retained for provenance; not
> open.]**

1. Is the layered detection/repair seam right, or should Path A/B enter the engine? (Rec: keep out of the frozen engine.)
2. Is **conjunctive + attestation-gated Path A** the right autonomy bar, or still too permissive/too strict?
3. Is **SUPPORTED-only** (Rec D) acceptable given the deferred weak-finding case?
4. Is the **equal-or-higher-tier** quality rule (larger-n only within/above tier) correctly specified?
5. Is **contrasting Smart Citations ∪ MeSH** the right retrieval union, and is the independence/citation orthogonality stated correctly?
6. Is **detection-first + Path B for the preprint** (Rec J), with Path A as N worked repairs, the right scope?

---

## 18. Advisor locks to freeze (Roberts)

- **A — Path-A rule (§18-4):** conjunctive. *Adopt.*
- **B — Criteria + hierarchy:** {≥2-yr gap} ∧ {equal-or-higher tier}. A larger `n` is **not an eligibility gate** and never promotes a lower tier, but it **is a substantive selection *preference*** among equal-tier eligible replacements — a **Roberts advisor-lock (deferred)**, not a cleanup. *Adopt (gap stays 2 yr — ZD 2026-07-17); Roberts ratifies the **tier mapping, conservative tie rule, date-gap calculation, and larger-`n` selection-preference threshold** ("decide when we see it").*
- **C — Detection rule:** directional contradiction, same outcome, comparable population, independent, notice-clear, verbatim span. *Adopt.*
- **D — Independence (§9.3-a):** built from two conditions — (a) superseding authors not substantially overlapping cited authors (no shared first/last/corresponding), and (b) an independent cohort/data source. **Whether (a) and (b) combine as AND or OR is an OPEN Roberts lock** (§9-6 defers to it); same-cohort re-analysis is never independent; independence is **orthogonal to whether the superseder cites the original.** *OPEN — Roberts freezes the AND/OR combinator and the overlap threshold.*
- **E — Comparability (§9.3-b):** outcome + population judged on **separate relation axes** (`outcome_relation`, `population_relation`) → combined `comparability_decision ∈ {comparable,not_comparable,uncertain}`; deployment sets the engine booleans only for `comparable`, `uncertain`→`UNJUDGEABLE`. *Recommended v1 (ZD via ChatGPT, 2026-07-17) — full rule in §18a; pending Roberts freeze.*
- **F — Confidence floor / calibration (§18-6):** `judge_contradiction` confidence threshold + Path-A repair-precision gate. Reliability follows the **registered two-annotator model** — Cohen's κ + Gwet's AC1 on the double-annotated IAA subset (κ≥0.60 gate, incl. the F3/F6 & F4/F6 pre-pilot; `PREREGISTRATION.md`), with the author test-retest as a *secondary* creator-stability check. *Roberts sets thresholds post-calibration.*
- **G — EoC/correction:** cap at Path B in v1. *Adopt.*
- **H — engine L397:** SUPPORTED-only; additive pinning test at build. *Adopt.*
- **I — Path-A attestation:** require a separate, **temporally-bounded** (`replacement_date ≤ attestation_date ≤ as_of_date`; same-day OK), citable field-level attestation — enum `{major_guideline_revision, systematic_review, meta_analysis}`; a pre-registered failed replication is supporting evidence, **not** a standalone attestation. *Adopt.* **[council/consensus]**
- **Withdrawn:** F5-vs-F8 boundary pair (§18-2) and `f5_f8_boundary` metric.

**Advisor-lock status after ZD review (2026-07-17).** A — **agreed**. B — established; the **2-year threshold is agreed**, while the **tier mapping, conservative tie rule, exact date-gap calculation, and larger-`n` selection-preference threshold remain deferred** ("decide when we see it"; larger-`n` retained as a deferred *preference* per ZD 2026-07-17 owner decision — earlier over-prune corrected). C — **agreed**. D (independence) — agreed in principle; overlap
**numbers deferred**, **start high-recall** in discovery. E (comparability) — **recommended v1** (full
rule §18a; pending Roberts freeze). F (confidence floor) — **start low / high-recall**; numbers "we'll see";
precision measured by annotation (§8a, §14). G — **agreed**. H — ZD's taxonomy call, already
SUPPORTED-only. I — **agreed**. **Evaluation model corrected:** the LLM/detector produces answers; **two
hired annotators** check precision (a prior 'solo-annotation' claim was a memory-derived error — §14, §22).
**Project-wide posture (ZD):** start high-recall, narrow to high-precision. **Pending Roberts freeze (one canonical set — mirrored in §6-B and §18-B):** comparability v1 (§18a); the
independence AND/OR combinator + overlap threshold (Lock D); the **quality/date-gate locks** — tier
mapping, conservative tie rule, date-gap calculation, and the larger-`n` selection-preference threshold
(§6-B, §18-B); and the confidence floor (Lock F).

---

## 18a. Advisor-lock E resolved — F5 comparability rule (v1)

*ZD via ChatGPT, 2026-07-17. Recommended v1; **pending Roberts freeze.** Resolves lock E; governs the
`comparable_population` / `same_claim_or_outcome` detector preconditions (§4) and extends §9 cases 2–4.*

A newer work is **comparable** to the cited work only when: **(1)** it evaluates the same claim-linked
primary outcome or estimand; **and (2)** its target population is equivalent to the cited claim
population, or encompasses that population and reports evidence directly applicable to it. **Outcome and
population are judged on two separate relation axes** — `outcome_relation ∈ {same, not_same, uncertain}`
and `population_relation ∈ {equivalent, encompassing_direct, encompassing_without_qualifying_direct_evidence, narrower,
disjoint, unclear}` — which **combine into a single** `comparability_decision ∈ {comparable,
not_comparable, uncertain}`. (Terminology: only the *combined* result uses
`comparable`/`not_comparable`/`uncertain`; below, an outcome hard-exclusion ⇒ `outcome_relation=not_same`,
and a missing/ambiguous outcome fact ⇒ `outcome_relation=uncertain`.) In
**discovery** mode `comparable` and `uncertain` are **eligible to surface** per `discovery_disposition` (recall policy; §8a); in **deployment** mode
only `comparable` may set `same_outcome=True` and `comparable_population=True`, and `uncertain` produces
`UNJUDGEABLE` (§8a).

**Preliminary claim-match gate.** Before applying this rule, confirm that the intervention/exposure, the
comparator/reference condition, and the causal contrast match the citation-dependent claim. Matching
outcome and population alone cannot make different treatments, doses, comparators, or exposure definitions
contradictory.

### 18a.1 Same primary outcome

Define the reference outcome from the specific claim and the cited paper — not from the title or whichever
result appears first. For trials, "primary" means prespecified primary, co-primary, or a
multiplicity-controlled confirmatory endpoint. For observational studies or reviews, use the prospectively
designated main outcome for the main objective. If primary status is absent or conflicts between article
and protocol/registry → `outcome_relation = uncertain`. **If the cited outcome is itself a known
secondary / exploratory / post-hoc outcome** (not the paper's prespecified primary/main), the pair is
**`not_comparable` — outside v1**: v1 only adjudicates a contradiction of a *prespecified primary/main*
cited outcome.

The newer result has the same primary outcome only if **all** hold:

1. **Same construct or event** (e.g. all-cause mortality, stroke, symptom severity, virologic suppression).
2. **Primary-result status** — the contradiction comes from the newer work's primary/main confirmatory outcome, not exploratory/post-hoc/ordinary-secondary.
3. **Same endpoint class** — clinical vs clinical, surrogate vs the same surrogate, biomarker vs the same biomarker. A surrogate and the clinical outcome it predicts are **not** the same endpoint.
4. **Compatible definition** — event definitions, thresholds, composite components, and competing/intercurrent-event handling materially aligned.
5. **Compatible measurement** — same instrument passes; different validated instruments pass only when they measure the same construct in the relevant population and benefit/harm direction can be unambiguously harmonized.
6. **Compatible metric** — different effect measures may pass when they concern the same endpoint and direction relative to the null can be deterministically harmonized. Continuous score vs responder threshold ⇒ `outcome_relation = uncertain` absent a prespecified mapping.
7. **Compatible time horizon** — same time point/window, or a newer estimate explicitly reported for the cited window, passes. Overlapping/adjacent windows without a matched estimate → `outcome_relation = uncertain`. Clinically distinct non-overlapping horizons → `outcome_relation = not_same`.

This follows the estimand principle that treatment, population, endpoint, intercurrent events, and
population-level summary jointly define the effect estimated — not the endpoint label alone
([ICH E9(R1)](https://www.ema.europa.eu/en/documents/scientific-guideline/ich-e9-r1-addendum-estimands-and-sensitivity-analysis-clinical-trials-guideline-statistical-principles-clinical-trials-step-5_en.pdf)).
CONSORT likewise defines an outcome by measurement variable, analysis metric, aggregation method, and
time point ([CONSORT 2025](https://www.bmj.com/content/389/bmj-2024-081124)).

**Outcome hard exclusions — set `outcome_relation = not_same` (⇒ combined `not_comparable`) when any is clear:** whole composite vs one
component; materially different composite definitions; surrogate vs clinical outcome (even a validated
surrogate); all-cause vs cause-specific mortality; acute vs long-term without a matched time point;
candidate contradiction only on a secondary/exploratory/post-hoc endpoint; different constructs despite
similar labels. (FDA distinguishes surrogate endpoints from direct clinical outcomes —
[FDA endpoint guidance](https://www.fda.gov/about-fda/innovation-fda/fda-facts-biomarkers-and-surrogate-endpoints).)

### 18a.2 Comparable population

First define the **reference population** = the population to which the citation-dependent claim and cited
primary estimand apply. **Do not** automatically use the cited paper's entire enrollment. Compare these
material dimensions: species/model; disease/condition + diagnostic definition; etiology/phenotype/
genotype/biomarker restriction; stage/severity; age/developmental stage; sex/reproductive status; material
comorbidities; prior treatment/line; care setting/context. A dimension is **material** when it is explicit
in the claim, in either study's eligibility, defining the indication, or a prespecified effect modifier
under the F5 policy.

Assign one **population relation:** `equivalent` (aligns on every material dimension); `encompassing_direct`
(newer contains the complete reference population **and** reports a **prespecified subgroup result on the same primary outcome** directly applicable to that population);
`encompassing_without_qualifying_direct_evidence` (contains the reference population but lacks a *qualifying* prespecified-subgroup result on the same primary outcome — e.g. only a mixed-population aggregate, a post-hoc subgroup, or a nonsignificant interaction); `narrower`
(proper subset on a material dimension); `disjoint` (no overlap on a material dimension); `unclear`.
Set `comparable_population=True` **only** for `equivalent` or `encompassing_direct`.

A broad-population average does not establish what happened in the cited subgroup, so
`encompassing_without_qualifying_direct_evidence` is `uncertain`, not comparable (Cochrane treats population/setting
differences as applicability questions —
[Cochrane Handbook ch.15](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-15)).
A reported subgroup result is direct support (`encompassing_direct`) only when it is **prespecified**,
corresponds to the reference population, and supplies an interpretable effect estimate on the **same
primary outcome**. A **post-hoc** subgroup, or a **nonsignificant interaction test alone**, does **not**
qualify → `population_relation = encompassing_without_qualifying_direct_evidence` (⇒ combined `uncertain`).

### 18a.3 Annotator decision procedure

Answer in order: (1) intervention/exposure + comparator match the claim? (2) cited claim-linked outcome
identifiable as a prespecified primary/main? (3) newer contradiction from a prespecified primary/main
confirmatory outcome? (4) both measure the same construct/event? (5) endpoint class + composite definition
compatible? (6) instrument, metric, polarity, time horizon compatible or unambiguously harmonizable?
(7) reference population identifiable? (8) populations equivalent on every material dimension? (9) if
broader, a result directly applicable to the complete reference population? (10) any material dimension
narrower/disjoint/clearly different?

**Output:** `comparable` = Q1–7 pass and Q8 or Q9 yes, no hard mismatch. `not_comparable` = any clear
intervention/comparator mismatch, outcome hard exclusion, narrower or disjoint population. `uncertain` =
no hard mismatch but a required fact is missing/ambiguous or supported only by a broader aggregate.
**Routing:** discovery → `comparable`|`uncertain` **eligible to surface** per `discovery_disposition`;
deployment → `comparable` may qualify, `uncertain` → `UNJUDGEABLE`, `not_comparable` → does not qualify.

### 18a.4 Edge cases

| Pair | Call |
|---|---|
| MACE composite vs cardiovascular death alone | `not_comparable` |
| Same composite label, different components | `uncertain`; `not_comparable` if clearly material |
| HbA1c vs mortality/CV events | `not_comparable` |
| PHQ-9 vs HAM-D, same horizon | `comparable` only if both validly measure the claim's construct + direction harmonizable; else `uncertain` |
| 12-week vs 8-week symptom outcome | `uncertain` unless newer reports the 12-week result |
| 30-day vs 5-year mortality | `not_comparable` |
| Older women vs all-adult mixed-sex aggregate | `uncertain`; `comparable` only with directly applicable older-women evidence |
| Adults vs children, humans vs animals | `not_comparable` |
| Stable outpatient vs acute ICU disease | `not_comparable` when stage/setting material |
| Same endpoint, contradiction only in a secondary analysis | `not_comparable` under v1 |
| Same endpoint + population, newer merely nonsignificant | outcome/population may be comparable, but directional contradiction is **not** established (absence of evidence ≠ reversal) |
| Newer appears narrower but exactly matches the citation's expressly limited population | compare against the **claim-defined** reference population — may be `equivalent`, not narrower |

### 18a.5 Auditable structured fields (rich F5 record; frozen engine unchanged)

- **Comparison unit:** `claim_index`, `claim_text`, `claim_population_text`, `intervention_or_exposure`, `comparator`, `claim_match`, `claim_match_reason`.
- **Outcome signature (per work):** `outcome_name`, `outcome_construct`, `outcome_role`, `prespecified`, `endpoint_class`, `measurement_variable`, `instrument`, `analysis_metric`, `aggregation_method`, `effect_measure`, `benefit_direction`, `time_point_or_window`, `composite_components`, `event_definition`, `intercurrent_event_strategy`, `outcome_evidence_span`.
- **Outcome comparison:** `construct_relation`, `endpoint_class_relation`, `composite_relation`, `instrument_relation`, `metric_relation`, `time_relation`, `outcome_relation ∈ {same, not_same, uncertain}`, `outcome_reason_codes`.
- **Population signature (per work):** `species`, `condition`, `diagnostic_criteria`, `stage_severity`, `age_range`, `sex_reproductive_status`, `phenotype_genotype_biomarker`, `comorbidities`, `prior_treatment`, `care_setting`, `eligibility_summary`, `population_evidence_span`.
- **Population comparison + decision:** `material_effect_modifier_dimensions`, `population_relation ∈ {equivalent, encompassing_direct, encompassing_without_qualifying_direct_evidence, narrower, disjoint, unclear}`, `direct_reference_population_result`, `population_reason_codes`, `comparability_decision ∈ {comparable, not_comparable, uncertain}`, `comparability_rationale`, `annotator_id`, `annotation_timestamp`, `comparability_policy_version`.

### 18a.6 Deterministic combination (machine + annotator)

`claim_match ∈ {match, mismatch, uncertain}` (from the preliminary claim-match gate). The combined
`comparability_decision` is **derived deterministically** from (`claim_match`, `outcome_relation`,
`population_relation`) — the model/annotator supplies the three axis values and does **not** independently
choose the combined result:

1. **Any hard mismatch ⇒ `not_comparable`:** `claim_match = mismatch`, **or** `outcome_relation = not_same`, **or** `population_relation ∈ {narrower, disjoint}`.
2. **Otherwise, any uncertainty ⇒ `uncertain`:** `claim_match = uncertain`, `outcome_relation = uncertain`, or `population_relation ∈ {encompassing_without_qualifying_direct_evidence, unclear}`.
3. **Otherwise ⇒ `comparable`:** `claim_match = match` **and** `outcome_relation = same` **and** `population_relation ∈ {equivalent, encompassing_direct}`.

A hard `not_comparable` therefore **dominates** uncertainty (step 1 is checked before step 2). This is the
authoritative combination; the §18a.3 annotator output follows the same logic.

**Engine mapping.** Set `same_claim_or_outcome=True` and `comparable_population=True` **only** when the
corresponding rich-record decisions are definitively positive — the frozen `TemporalAssessment` contract
(§2) is unchanged.

---

## 19. RELAY-READY DELIBERATION BLOCK (for real Codex) — [HISTORICAL RELAY INPUT]

> **[HISTORICAL — the 11-item CURRENT PROPOSAL below is the *pre-consensus* draft that was submitted to
> the relay. It was superseded by the ratified consensus; the binding refinements are in §21 (and ZD's
> post-consensus corrections in §0/§4–§14). Do not treat the 11 items as current.]**

> Self-contained so Codex can run the codex-relay exchange directly. Loop policy: **re-run until
> CONSENSUS** (do not settle for `NO_CONSENSUS`), per ZD. Model usage counts against existing billing.
> The standalone task text also lives in `F5_RELAY_TASK.md`.

**Decision:** Ratify the council-hardened F5 blueprint's seam + advisor-lock **recommendations**
(§18 A–I) + the F5/F8 decorrelation, OR return precise revisions. Scope: design + decision rules only;
no code, no engine change; nothing advisor-locked is *derived* until Roberts also signs.

**CURRENT PROPOSAL (accept = endorse all exactly):**
1. **Layered seam:** detection == frozen engine `QUALIFYING_CONTRADICTION` ⇒ `F5` (a **candidate**);
   Path A/B lives in the repair record + measurement, never in the frozen engine.
2. **Detection rule (C):** directional contradiction, same outcome, comparable population, independent
   source, notice-clear, verbatim span.
3. **Path-A rule (A+I):** conjunctive **and** requires a separate, post-dated, citable field-level
   **attestation** that explicitly concludes reversal/supersession — a major-guideline revision **or** a
   systematic review/meta-analysis **only**. A pre-registered failed replication is supporting evidence,
   not a standalone attestation (it gates Path A only when folded into a qualifying attestation). A single
   recent primary study never triggers Path A.
4. **Path-A quality/date gate (B):** publication-date gap ≥ 2 years **and** superseding evidence at an
   **equal-or-higher OCEBM tier**; larger n counts only within/above tier (no cohort-beats-RCT).
5. **Independence (D)** authorship/cohort-based, orthogonal to citation; **comparability (E)** same
   primary outcome + equivalent-or-encompassing population.
6. **Engine L397 (H):** F5 target must be `SUPPORTED`; `WEAKER_STRENGTH` deferred (documented limitation);
   detector assesses temporal only on `SUPPORTED` claims; one additive pinning test at build; **no engine
   modification.**
7. **F5/F8 decorrelation:** no boundary pair; drop `f5_f8_boundary`; EoC/correction cap at Path B; the
   only residual is `f8_notice=False` as a data precondition.
8. **Retrieval:** MeSH + pub-date candidate generation (NOT MonoT5) **∪** scite `contrasting` citations;
   injected seams; offline/fail-closed; `temp=0`; cached candidates; `validate_f5_record` replay.
9. **Preprint scope (J):** ship detection-first + Path B; Path A demonstrated as **N worked repairs**, no
   Path-A precision number reported until a labeled corpus exists. Source worked/calibration examples by
   inverting from published reversal catalogs (existence/calibration only — no-circularity lock).
10. **Novelty:** claim the *join* (citation-specific supersession + repair) against Trialstreamer /
    RobotReviewer / living reviews / scite contrasting citations; run a novelty search before the preprint.
11. **Reportability:** development mode (`reportable=False`) until Roberts freezes the locks, preregisters,
    and calibration completes; Path A/B reported separately; Path-A coverage mandatory.

**Reviewer instructions:** `action=accept` returns `sha256:<proposal-hash>` only on full agreement; else
`action=revise` with the complete revised proposal, or `action=block` with the missing evidence/decision.
Keep scope narrow; do not invent rules Roberts has not frozen.

**Ready-to-run (machine with `codex` + `claude` authenticated):**
```bash
WS=/Users/kamachi/citation-repair-engine
python3 <codex-relay-skill-dir>/scripts/relay.py \
  --task "$(cat /Users/kamachi/Documents/CitationRepairEngine/F5_RELAY_TASK.md)" \
  --workspace "$WS"
# On NO_CONSENSUS.md: feed the latest revised proposal back as --task and re-run until CONSENSUS.md.
# See F5_RELAY_TASK.md for the loop wrapper.
```

---

## 20. Council verdict (5-advisor LLM council, 2026-07-16)

Full transcript: `council-transcript-2026-07-16-F5.md`; report: `council-report-2026-07-16-F5.html`.

**Where the council agreed (high-confidence):**
- A single qualifying contradiction is a **signal (CANDIDATE-F5)**, not a supersession **diagnosis**;
  supersession is a field-level/consensus event.
- **Autonomous replacement (Path A) is the weakest, most-attackable, sparsest-to-validate component** →
  defer it; ship detection + escalation (Path B). Gate any Path A behind a field-level attestation.
- The positive class is **sparse**; a Path-A precision number would be ~n=2 → report **existence +
  mechanism + N worked repairs** instead.
- **Source examples by inverting from published reversal catalogs** (forward-citation walk).
- Precision-first hold-as-`UNJUDGEABLE` is correct.

**Where it clashed:** the Expansionist wanted F5 (prevalence + "reversal surveillance") as the **headline**;
the other four (and the peer review) held that leading with the least-validated component maximizes review
attack surface. **Resolution:** keep the surveillance/prevalence framing for Discussion/Future Work as
vision, not as a validity claim.

**Blind spots the peer review caught (folded in):**
1. **Quality-hierarchy bug** — "higher tier OR larger n" let a cohort supersede an RCT. **Fixed** to
   equal-or-higher tier (§6-B, §9-7).
2. **Ground-truth circularity** — reconciled by the **registered two-annotator model**: two independent
   (hired) annotators double-annotate the IAA subset → Cohen's κ + Gwet's AC1; the author's own re-label is
   a *secondary* test-retest (creator stability), never the primary figure. Sparse positive class → the
   preprint leads with worked-examples, not a precision claim (§14). *(Corrects an earlier solo-annotation
   misstatement — §22.)*
3. **Prior art** — Trialstreamer, RobotReviewer, living systematic reviews, scite contrasting citations;
   claim the *join*, run a novelty search (§1).
4. **Retrieval/independence** — contrasting citations are precision-oriented/low-recall (∪ MeSH);
   independence is authorship-based, orthogonal to citation (§5, §6-D).

**One thing to do first (council):** pull ~15 documented reversals from published catalogs, pin each
reversed original + its superseding source, and walk forward citations to find live citations of the
reversed original — the single move that creates the F5 existence proof + calibration set + worked
repairs; in parallel, send Roberts the decision-rule doc for the lock.

---

## 21. Codex + Claude-Opus consensus conformance (2026-07-17)

Ratified via the codex-relay two-reviewer loop — 3 turns, final-turn hash
`35a8f2d3af7a092881d10d2cf73f7339334b827f3851e3eed04bbc286062dc96`. The agreed proposal refined this
blueprint; the **item-12 conformance edits are applied above**:
- **(a)** self-correction / failed independence → `not_F5`, **never Path B** (§9-6); independence *unknown* → `UNJUDGEABLE`.
- **(b)** an all-unjudgeable candidate set → `UNJUDGEABLE`, not `NO_QUALIFYING_CONTRADICTION` (§9-18).
- **(c)** a pre-registered failed replication is **contradiction/supporting evidence, not a standalone
  Path-A attestation** — corrected in §0, §5 (seam row), §6 (Rec B, Rec I), §10 (enum), §13
  (`attestation_types`), §18-I, and §19. It gates Path A only when incorporated into a qualifying
  field-level attestation (guideline revision **or** systematic review/meta-analysis).
- **(d)** **"notice-clear" = no retraction** (`f8_notice=False`); a correction/EoC is detection-eligible
  but caps routing at Path B (§4, §9-21).

Also folded in from the agreed proposal: the distinct **attestation-search retrieval stream** +
citation-chaining/claim-outcome retrieval and the **retrieval-adequacy-before-any-confident-negative**
rule (§5, §14); attestation and replacement recorded **separately**, with Roberts freezing the tier
mapping / tie rule / date calculation / larger-`n` selection preference (§6).

These conformance **edits** are design/spec only and themselves touched no code, engine, or repo
(development-mode implementation is separately governed by §13).
**Remaining gate: Roberts advisor locks (§18); `reportable=False` until every lock is frozen,
preregistered, and calibration completes.** Next artifact: `F5_SUPERSESSION_SPEC.md` (implementation spec →
Claude Code), written for the development-mode build now (`deploy_path_a=False`, non-reportable) per §13; the Roberts
locks gate only formal/reportable derivation and live Path-A deployment.

## 22. Claim-provenance audit (2026-07-17)

Prompted by ZD, separating what is **verified against files this session** from what came from **prior
memory** (which can be stale) or **my inference** — so nothing memory-derived silently drives the build.

- **Verified against the actual repo/protocol this session (safe):** frozen `judgment_engine.py` sha256
  `671de1e5…` and `judgment_band.py` `7d81e5e0…`; the `TemporalAssessment` / `TemporalState` contract, the
  `SUPPORTED`-only F5 guard, and the `L397` comment; the finding order `("F7","F6","F4","F3","F5")`; the
  four pinned F5 tests; the seven F2 `WRONG_PAPER` guard PMIDs; protocol §9.3 / §18 advisor-lock text; the
  `TAXONOMY_F5_section.md` "all-three" vs `TAXONOMY_DECISION_RULES.md` "any-one" conflict; scite/PubMed tool
  capabilities. (An independent read-only audit confirmed these.)
- **Corrected memory error (important — and its blast radius).** The **annotation / reliability model.**
  Earlier F5 text asserted *solo annotation + Roberts-as-sole-spot-checker + "no κ / no pre-pilot."* This
  came from `memory.md`, which **directly contradicts the registered project docs**: `PREREGISTRATION.md`
  (double-annotated IAA subset, κ≥0.60), `TAXONOMY_DECISION_RULES.md` ("40 examples, 5/category, **two
  annotators**"), and `docs/F3_F7_MEASUREMENT_PROTOCOL.md` ("the **two annotators** independently
  re-label…"; Cohen's κ + Gwet's AC1). The registered **two-annotator** model is authoritative and matches
  ZD's "LLM produces answers; two hired annotators check precision." **Blast radius: the error lived only in
  `memory.md` and this F5 blueprint (now fixed) — the F4/F7 specs, the protocol, and the preregistration
  already use the correct two-annotator model and are unaffected.** The stale `memory.md` entry should be
  corrected so it doesn't re-poison future sessions (it is read-only from here).
- **From prior memory — confirm before relying on them:** the NCBI contact email / `NCBI_API_KEY` (§5);
  the **Aug-1** preprint date; and competitor / prior-art names (BibAgent, Sarol, Trialstreamer,
  RobotReviewer) — used only as *novelty-search targets to verify* (§1), never asserted as results.
- **My inference (design proposals, not facts):** the layered detection/repair seam, the
  discovery/deployment modes, and the edge-case rulings — proposals, ratified via the relay + your review,
  still advisor-lockable.

If any "confirm" item is wrong, it is isolated to that line and does **not** touch the verified
engine/taxonomy core.

## References (grounding)

- Prasad V, Gall V, Cifu A. (2011). The frequency of medical reversal. *Arch Intern Med* 171(18):1675–6.
- Prasad V, et al. (2013). A decade of reversal: 146 contradicted medical practices. *Mayo Clin Proc*
  88(8):790–8. PMID 23871230.
- Herrera-Perez D, et al. (2019). A comprehensive review of RCTs in three medical journals reveals 396
  medical reversals. *eLife* 8:e45183.
- Alamri A, Stevenson M. (2016). A corpus of potentially contradictory research claims from cardiovascular
  research abstracts. *J Biomed Semantics* 7:36. PMID 27267226.
- Rosemblat G, Fiszman M, Shin D, Kilicoglu H. (2019). Towards a characterization of apparent
  contradictions in the biomedical literature using context analysis. *J Biomed Inform* (PMC7001095).
- BioDivergence (2026). Benchmark for hidden contextual contradictions in biomedical abstracts.
  arXiv:2606.11208.
- OCEBM Levels of Evidence Working Group (2011). *The Oxford 2011 Levels of Evidence.* CEBM, Oxford.
- Prasad V, Cifu A. (2015). *Ending Medical Reversal.* Johns Hopkins University Press. *(reversal catalog
  for inverted-search sourcing.)*
- Teixeira da Silva JA. (2025). The citation of retracted papers and impact on the integrity of the
  scientific biomedical literature. *Learned Publishing* 38:e1667. *(F8/retraction context — contrast.)*
- Marshall IJ, et al. Trialstreamer / RobotReviewer. *(prior-art positioning: evidence surveillance.)*

---

**Freeze canonicalization.** Canonical form = **UTF-8**, **LF** line endings, **exactly one trailing
newline**. Compute the freeze hash on a file in that form: `sha256sum F5_BLUEPRINT.md`. A copy with the
final newline stripped differs by exactly that one byte (this was the earlier `79e3f027…` / `109ccd…`
vs `0994e0…` mismatch — same content, different trailing byte, not a divergent file). The frozen hash is
recorded in the freeze log / handoff, not inside this file (a file cannot contain its own hash).
