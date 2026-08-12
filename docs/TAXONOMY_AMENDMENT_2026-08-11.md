# Governing amendment — 2026-08-11 (paste at the END of TAXONOMY.md, after the 2026-08-07 amendment)

> **This amendment wins on any conflict with everything above it**, including
> `TAXONOMY_AMENDMENT_2026-08-07_FINAL.md` and `TAXONOMY_DECISION_RULES.md` (TDR). The rule established
> on 2026-08-07 holds: the most recent governing amendment wins, and every taxonomy document carries its
> own date. Nothing above is deleted or rewritten — this amendment only overrides the sections it names.

**Provenance.** Written by Claude under ZD's explicit delegation, 2026-08-11: *"these small decisions ill
just let you make... fix ALL the amendments."* Every substantive call below is marked **[CLAUDE'S CALL]**
with its reasoning, so ZD can reverse any one of them in a single line. **Two of them are not small, and
they are flagged as such.** Claude assigns no labels and curates no gold here; these are rule-wording
decisions only.

**What triggered this.** Calibration run 2 (`calib_v3b_PMC10115774`, 2026-08-11) was the first live
exercise of `coverage_v3`. It produced six defects and exposed three places where the codebook has no
answer at all, so the code invented one. Vault entries `CONTRADICTIONS` 28–34.

---

## A. §H of the 2026-08-07 amendment is STALE — F6-from-silence is now reachable

The 2026-08-07 amendment §H reads: *"The retrieval-completeness signal that §B's third outcome depends on
does not exist yet; until the full-text reader reports what it obtained, every silence is CANNOT BE
DETERMINED and F6-from-silence is unreachable (DEC-032)."*

**That is no longer true and the sentence is revoked.** `cre/f1/fulltext_reader.py` reports
`retrieval_complete` with named `incomplete_reasons`, verified live on 2026-08-07 and again on
2026-08-11. In run 2, F6-from-silence fired twice — `PMC10115774:CR25` on unconfirmed specifics and
`PMC10115774:CR42` on a subject the cited paper never engages — with `coverage_contradicted` at zero.

§B's three outcomes are therefore fully operative:

- **ESTABLISHED** — the retrieved text states and supports the claim.
- **NOT ESTABLISHED** — retrieval was **complete** and the text does not state or support the claim.
  Silence against a complete retrieval is evidence of absence.
- **CANNOT BE DETERMINED** — retrieval was incomplete. A hold, not a finding.

**A retrieval failure is CANNOT BE DETERMINED, never NOT ESTABLISHED.** This is the direction DEC-032
already fails closed in, and it is now load-bearing rather than theoretical: on 2026-08-11 a PMID→PMCID
resolver outage returned "no PMC full text" for 25 of 25 references, and had the reasons been read as
absence rather than as a failed retrieval, run 1 would have flagged the entire document as F6. The reason
vocabulary an annotator may rely on is `no_pmcid`, `no_body`, `body_unparseable`, `body_too_small`, and
`resolver_error`. **Only `no_pmcid` and `no_body` are statements about the paper. The other three are
statements about the retrieval.**

## B. The specificity boundary is KEPT, and it is now written as a rule rather than implied by an example

**[CLAUDE'S CALL — and this one is not small. It is F6's operative definition.]**

ZD ruled "wider words count" on 2026-08-11. That ruling is **not adopted**, because it collides with two
documents he had already written and would revoke F6's working definition project-wide. The TDR's
"ACCURATE vs. F6 — the specificity boundary" says a claim adding specificity the paper does not confirm is
F6, with the worked case *claim says "in ApoE-deficient mice," paper only discusses "mice" generally*. The
2026-08-07 amendment §A deliberately preserved that verdict. Adopting "wider counts" would flip F6's
central example and every F6 example downstream of it.

Stated as a rule, replacing the implication carried by the worked example:

> **A claim is ESTABLISHED only when the retrieved text supports the claim's own terms.** A broader term
> in the source does not establish a narrower term in the claim. Where the claim names a specific
> substance, population, model, intervention, or outcome, the source must address that specific thing —
> not a category containing it.

**Worked negatives, both from run 2's `PMC10115774:CR4`.** Source says `recalcitrant substrates`; claim
says `lignin`. Source says `SOM decomposition`; claim says `litter decomposition`. Litter and soil organic
matter are different carbon pools, and lignin is one recalcitrant substrate among several. The judge marked
both ESTABLISHED and conceded the substitution in its own rationale — *"even though the evidence uses the
terms 'recalcitrant substrates' and 'SOM decomposition' rather than explicitly naming 'lignin' and 'litter
decomposition'"*. Under this rule both are **NOT ESTABLISHED**. Run 3 (`3e5261d`) confirmed the rule lands:
the judge flipped the first to NOT ESTABLISHED and cited the rule by number in its own rationale.

### B.1 Universal versus existential — a separate trap, and the reason "wider counts" could not be adopted flat

Two source statements look equally "broader" and are not:

- **Universal.** *"Recalcitrant substrates are rate-limiting in SOM decomposition."* A property asserted
  of the whole class. A member inherits it.
- **Existential.** *"Lignin degradation is caused by **certain** fungi."* A property asserted of some
  members. **A member inherits nothing.**

> **An existential statement about a class never establishes a claim about a specific member.**

**Worked negative, run 2 `PMC10115774:CR42`.** The cited paper says certain fungi degrade lignocellulose
and never mentions *Mycena*. "*Mycena* degrades lignocellulose" is **NOT ESTABLISHED**. Had "wider counts"
been adopted without this limit, it would have manufactured a clear here — and note that the judge itself
refused the inference, correctly.

## C. A reference with no extractable atomic claim is OUTSIDE the scoreable set

**[CLAUDE'S CALL]**

The codebook has no outcome for a reference from which no atomic claim could be extracted, so the code
supplied one by accident: `judgment_band.route` decides FULL_COVERAGE with `all()` over the verdict list,
and `all([])` is `True`. Its docstring says so outright — *"vacuously true when there are no claims."*
Measured: 3 of run 2's 6 `FULL_COVERAGE` references are this, and because the grading sheet pairs claims
with verdicts, they write **no row at all** and are invisible to the annotator.

> **A reference yielding zero atomic claims is `NO_CLAIMS`.** It is neither ACCURATE nor F6 nor HELD. It
> is excluded from the scoreable denominator and reported as its own count. ACCURATE continues to require
> every atomic claim ESTABLISHED — **of at least one claim.** Vacuous coverage is never coverage.

Excluded rather than counted, because a reference nothing could be extracted from carries no assertion to
be right or wrong about. Extraction failure is our failure, not the citation's. It must be *reported*, in
full, because a rising `NO_CLAIMS` count is a symptom of an extractor defect and must never be silent.

Confirmed stable: across 5 extraction draws of the same document, 3 of 21 citing sentences produced no
atomic claim **every time**, matching run 3's three `NO_CLAIMS` references exactly. The route records a
real property of those citances, not sampling noise. A `NO_CLAIMS` reference is recorded and counted but
**never queued for blind annotation** (DEC-045) — there is nothing for an annotator to judge.

## D. Evidence spans are RECORDED and REPORTED — they do not gate the verdict

**[CLAUDE'S CALL — REVISED 2026-08-11 evening, DEC-047. The original §D is superseded by the rule below.]**

> **The evidence spans recorded for a claim are the sentences the judge relied on, read together with
> their section context.** They are recorded in full and reported, and they are the basis for evidence
> selection metrics. **They do not gate the verdict.** A verdict is not downgraded because its spans are
> incomplete; incompleteness is measured as recall, not punished as error. Evidence selection is harder
> than the verdict it supports and has low human agreement — that is a property of the task, not a defect
> of a run.

**What the original §D said, and why it was wrong.** It required each span to justify its verdict standing
alone, and made a verdict whose justification needed outside text NOT ESTABLISHED. MultiVerS (Findings of
NAACL 2022) reports that many rationales are *"context-dependent"* and require surrounding document
context, *"making isolated sentence selection inherently problematic,"* and that experts disagree on
*"exactly which sentences contain the best evidence"* — to the point that *"systems already exceed human
agreement for sentence-level evaluation, but not abstract-level."* Sarol et al. (*Bioinformatics* 2024,
PMID 38924508) measured evidence-sentence agreement at kappa 0.20-0.37 and cite related datasets at
0.16-0.52. A gate on something humans cannot agree about produces noise, not rigour.

In practice the original rule also caused active harm: a missing span raised, which quarantined the whole
reference and destroyed every claim on it. Because quarantine is per reference,
`P(reference lost) = 1 - (1-p)^n_claims`, so the loss concentrated on the references carrying the most
claims — the ones most likely to contain a fault.

**What survives.** The reporting obligation stands: **record every passage you relied on.** In four of
run 2's twelve graded rows the rationale leaned on text absent from its own reported span, and that is
still a defect worth measuring — it is why the span audit passed both `CR4` rows as verbatim while both
verdicts were wrong under §B. But it is now a *reported* property, not a validity condition.

**A passing span audit is a surface metric.** "Cited but Not Verified" (arXiv 2605.06635) measures 14 LLMs
at 94%+ link validity and 80%+ topical relevance alongside 24-77% factual accuracy — *"a critical
disconnect between surface-level citation quality and factual reliability."* A verbatim span never
evidences a correct verdict, and must never be reported as though it does.

**For the human annotator**, the corresponding instruction is: read the recorded spans **with** their
section context, and record what you used. An annotator is not asked to justify a label from an isolated
sentence, because the literature says that is not a reasonable ask.

## E. `engages_subject` is a test on the claim's subject, not on its topic

**[CLAUDE'S CALL]**

> **`engages_subject` is False when the specific entity, population, intervention, or outcome named in the
> claim does not appear in the retrieved text.** A paper that discusses the surrounding topic does not
> engage a subject it never mentions.

Run 2's `PMC10115774:CR42` applied this inconsistently inside one reference: *Trichocladium* absent →
`engages_subject=False`; *Mycena* equally absent → `engages_subject=True`, with a span about primary
colonisers and, on a second row, a table row for *Armillaria cepistipes* — a different species. Both
absent genera are **False**. Consistency here matters beyond tidiness: `engages_subject` is what separates
an off-topic reference from a contradicted one.

## F. The judgment unit is the CITATION–REFERENCE PAIR

**[CLAUDE'S CALL — and this one is not small. It sets the denominator of every rate in the paper.]**

OPEN ITEMS item 6 has been open since 2026-08-06: the line *"of 42 atomic claims judged, 2 were
contradictions"* never said what the unit was. Extraction is cached per citing sentence but coverage runs
per reference, so a citance citing [5,6,7,8] puts the same claim text into a per-claim count four times.

> **The unit of judgment, of counting, and of annotation is the (citing sentence, cited reference) pair —
> one row per reference.** Atomic claims are the evidence structure *inside* a pair, reported as the
> established/unestablished split, which §C of the 2026-08-07 amendment already names as the F6 output.
> **Every rate, every denominator, and every precision figure is per reference, never per claim.**

Four reasons, in order of weight. The fault is a property of the citation, not of the claim — F6 means
*this reference does not support what it was cited for*. The repair is a property of the citation: you
replace a reference, not a claim. Each cited paper brings different evidence, so judging one claim against
four papers genuinely is four independent judgments, whereas counting one claim four times in a per-claim
denominator is not four independent observations and would silently correlate errors. And `judgment_band`
already implements it — every counter, item record, and annotation-queue row is per reference.

**A fifth reason, measured after this rule was written and stronger than any of the four.** Atomic claim
extraction is not reproducible. Same document, same frozen `claim_extract_v3`, same model: 5 draws over 21
citing sentences produced an identical claim set for only 12/21 at provider-default sampling and 18/21 at
`temperature=0` (DEC-046). Granularity moves — `CR39` went from one compound claim to six between two runs,
`CR52` from three to eight — and `CR34` changed route from `FULL_COVERAGE` to `F6_FLAGGED` because a single
claim was split in two, with no change in evidence and no verdict wrong. **A per-claim denominator is
therefore stochastic.** Per-reference is not. Per-claim figures may be reported as *descriptive
characterization* of what the extractor produced, clearly labelled, and never as a rate with the claim as
denominator. Pooling per-claim and per-reference counts into one fraction is forbidden, for the same reason
pooling two collection frames is.

## G. What is NOT changed here

- **Annotation stays two annotators.** DEC-036 and DEC-037 (2026-08-07) superseded DEC-006 on annotator
  count; `CONTRADICTIONS` entry 1 is RESOLVED and is not reopened. Two annotators label the flagged set
  independently and blind, adjudication produces gold, precision is the fraction gold confirms, no Cohen's
  kappa anywhere, agreement via Gwet's AC1 with no threshold registered in advance.
- **Stage order stays F4 before F3** (DEC-039, 2026-08-07 §D). F4's gate stays exactly ESTABLISHED (§E).
- **Evaluation stays precision-only** (DEC-005). No recall, sensitivity, or F1 figure for fault detection.
  (This does not forbid reporting *evidence-selection* recall, which is a different quantity about a
  different task — see §D.)
- **Gold is naturally occurring.** Never hunted, never synthetic, never LLM-generated. Calibration examples
  may be hunted; gold may not. The judge is the system under evaluation and is never gold; no F3–F7 label
  is machine-assigned.
- **`TAXONOMY_DECISION_RULES.md` is not edited.** §B above sharpens its specificity boundary into an
  explicit rule and keeps its verdict; the ApoE worked case stands unchanged.
- **The two confirmed F3 cases and their verdicts** are untouched.

## H. Schema-constant naming — recorded so it stops resurfacing

DEC-031 names the replacement scope constant `fulltext_snapshot`, and the 2026-08-07 amendment §A repeats
it. **Neither describes the tree today.** The frozen schema still reads
`"evidence_scope": {"const": "abstract_snapshot"}`, the run manifest emits
`"evidence_scope": "fulltext_sections"` (`judgment_band.py`, asserted by a committed test), and
`fulltext_snapshot` appears in zero files.

This is not a contradiction to fix, because ZD cut the prompt freeze from the critical path on 2026-08-11
(DEC-044): the schema is not being re-pinned, so no constant is changing. Recorded plainly instead —
**`fulltext_sections` is what a run manifest says; `fulltext_snapshot` is the name reserved for the schema
constant if and when the freeze happens.** `CONTRADICTIONS` 28 is downgraded from a blocker to a naming
note. Whichever survives, the freeze pass must make the two agree in one commit.

## I. Adapter parameters ride with every number (DEC-046, amending DEC-020)

`temperature=0` is pinned for extraction and judging. It rides in the run manifest beside `MODEL`
(`claude-sonnet-4-5`) and `assistant_prefill` (`{`). DEC-020 deliberately omitted `temperature` and
`top_p`; the omission was defensible when nothing depended on run-to-run stability, and the F6 route reads
the atomic-claim list, so it does. `top_p` stays omitted — untested, and one variable at a time.

**Not a determinism claim.** 3 of 21 citing sentences still vary at `temperature=0`; greedy decoding is not
bit-reproducible on batched inference. The residual is a documented limitation, and §F is what keeps it
from reaching any reported rate.
