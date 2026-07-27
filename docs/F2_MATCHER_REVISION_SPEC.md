# F2 matcher revision — implementation specification

**Spec date:** 2026-07-26

**Revision:** 5.1 (supersedes revisions 1–5)

**Amendments in 5.1 (2026-07-27):** two defects surfaced during implementation and
fixed here, both documentation-level, neither changing any normative rule:
(1) §20 described an offline reband step with no runnable command, and
`reband_from_cache` was a Python-only function — a CLI now makes it executable,
§20 gives the concrete command, and the CLI FAILS LOUD (non-zero exit) on an
absent/empty corpus instead of emitting an exit-0 all-zeros summary that reads as
a pass; (2) §2.2 now discloses that no change since `a0c1060` has been verified
frame-wide (§7.2), only on unit tests + the 51-row band, because neither working
environment holds a readable pinned corpus. Editing this file changes its
SHA-256; the `91c2f45…` pin refers to revision 5 and is superseded by the 5.1
hash recorded in the next manifest.

**Frozen scoring provenance:** `a0c1060e85d74a57de0f1c0bb8c9060c87a5caa4`

**Current prototype baseline:** `778d0cc853aa693d2710be3f560a050683290ec4` on
`feat/f2-matcher-revision`

This is the normative build and release contract. If code, an older handoff, a notebook,
or a comment conflicts with this document, this document wins until it is replaced by a
dated revision.

Normative words are used deliberately:

- **MUST / MUST NOT**: required for merge and release.
- **SHOULD / SHOULD NOT**: expected unless the implementation records a specific,
  reviewed exception.
- **MAY**: optional.
- **DEFERRED**: must not affect production routing in this revision.

---

## 1. Purpose and identity model

For one reference:

- **A** is the bibliographic work the written citation appears to intend.
- **B** is the record reached by the identifier currently attached to the citation,
  usually the claimed PMID.
- **C** is a proposed replacement record when A can be resolved confidently and C is
  demonstrably distinct from B.

F2 is a bibliographic-identity fault: the attached identifier resolves to a different
bibliographic work from the one the citation intends.

The following are not interchangeable:

- same bibliographic record;
- declared version family, such as preprint and version of record;
- same underlying study represented by distinct outputs, such as a conference abstract
  and a full paper;
- related work, such as a comment, reply, correction, or retraction;
- corrupt or transposed citation text;
- mixed identity, where the citation combines fields or identifiers from more than one
  work.

The detector emits evidence and review routes. It **never creates a semantic gold label**.

---

## 2. Frozen inputs and provenance

### 2.1 Verified local artifacts

| Artifact | Required row count | SHA-256 |
|---|---:|---|
| `f2_random_oa_seed37_freeze_a0c1060.jsonl` | 23,370 | `6e83b555a05f4b2a45421126395ce6dfbd888d67f4d1760af0cfe55da6568985` |
| `f2_prospective_seed37_a0c1060_high.jsonl` | 51 | `3a9402e7913165d4e27342812db8cea92bfbc57149687933187002f84247cf60` |
| `f2_resolved_cache_seed37_heldout_a0c1060.jsonl` | 23,011 physical lines | `d000f2aec772ee4ee40e68f70a613e4c5d4f1cb1575971c93985c080978fa92e` |
| `f2_seed37_HIGH_labels_51.tsv` | 51 data rows | `81926bbe7521f895a80e162381bd8f567f5bdfba7d2da7166c7c735f5458351a` |

The freeze contains citation occurrences. Counts described as unique PMIDs, unique
source/PMID pairs, cache rows, joined rows, or occurrences MUST retain those exact names;
they MUST NOT be substituted for one another.

The small `f2_heldout_frame_seed37.jsonl` is an index and does not contain the complete
`written_*` evidence needed to re-score the matcher. It MUST NOT be used as the scoring
frame.

### 2.2 Audit-label status — release blocker

The seed-37 labels are development evidence, not clean release gold:

- The connected Google Sheet
  `1VrHiv7GUUDM70p4v5e-bll_EEuejAHKyyUtgv47zd6o` contains all 51 cases but, in the
  currently visible version, only two rows have populated audit labels. Those two are
  `NOT_A_TITLE`, while the local derivative TSV calls the same rows `SAME_WORK`.
- The local annotation workbook is an unfilled template.
- The local 51-row TSV is a derivative that provides a complete label column, but its
  claimed filled-CSV source is not available locally and several labels are explicitly
  pending or internally inconsistent.
- The current `f2_seed37_HIGH_labeling_worksheet_FILLED.csv` label `SAME_WORK` for
  `PMC8015328:ref011` is wrong. ZD reconfirmed on 2026-07-26 that this case is
  `TRUE_F2`: the written citation describes
  *Paurodontella persica* in *Nematology* 19:57–68 (2016), while the attached
  PMID/DOI resolves to *Paurodontella composticola* in *Journal of Nematology*
  51:1–12 (2019). The next frozen export MUST contain this correction and its dated
  rationale.

Therefore:

1. Seed-37 MAY be used for error analysis, rule discovery, regression fixtures, and
   descriptive before/after counts.
2. Seed-37 MUST be called **development / adaptively reused**. It is no longer held out.
3. No seed-37 precision, sensitivity, F1, or Wilson interval is a reportable estimate of
   future performance.
4. Before any label-dependent acceptance claim, ZD MUST export one complete human
   adjudication table containing all 51 labels and rationales, freeze it as a non-native
   file, record its SHA-256, and resolve every item in §17.
5. The detector, Claude, GPT-Sol, and retrieval providers MUST NOT fill, alter, or infer
   those labels.

Until that blocker is closed, the historical counts are reported as:

- current derivative labels, including the `PMC8015328:ref011` correction:
  17 `TRUE_F2`, 34 other;
- provisional scenario after the three originally proposed relabels: 16 `TRUE_F2`,
  35 other.

Neither scenario is “known truth.”

**Frame-wide verification status (amended 2026-07-27).** Every number produced for
any change since `a0c1060` — F2-A/B/C/D/E/F/G/I, the routing fixes, and the
resolver work — comes from unit tests plus the 51-row HIGH band. The frame-wide
reband that §7.2 requires (“frame-wide movement MUST be reproduced from the pinned
freeze”) has **not** been run for any of those changes, in either working
environment: the primary checkout carries 0-byte source-XML stubs and the Drive
copy is an unreadable ~42 MB placeholder, so the offline reband produces an empty
frame in both — the CLI now treats that (`n_records == 0`) as a FATAL error with a
non-zero exit, rather than the exit-0 all-zeros summary it emitted before this
amendment (see §20). Only a Colab run with the real pinned corpus can close this. Until it does, all
before/after counts in this program are **development-scale (≤51 rows), not
frame-wide**, and must be reported as such alongside the base rate.

### 2.3 Reproducibility manifest

Every candidate run MUST write a manifest containing:

- code commit and dirty-tree status;
- hash of this specification;
- hashes and row counts of every input;
- label snapshot hash, or `null` with `labels_unfrozen: true`;
- all thresholds and feature flags;
- NLM serial-authority snapshot name, publication date, and hash;
- provider names, query versions, cache hashes, and retrieval timestamps;
- Python version and dependency lock hash;
- run start/end UTC timestamps;
- output hashes and row counts.

A result without this manifest is diagnostic only.

---

## 3. Current prototype baseline

Do not recreate the branch from `a0c1060`; it already exists. As inspected on
2026-07-26, the current branch is `feat/f2-matcher-revision` at `778d0cc`, and
`a0c1060` remains checked out separately at `/Users/kamachi/cre-f2`.

`origin/main` is now `d090ab783919645c4d37dd13f871a6b4f1c7818f` and does contain the
F1 package. Revision 4's claim that main is still `b56997a` and lacks
`biblio_match.py` is stale.

The prototype is **not accepted** merely because its tests pass. Its status is:

| Change | Prototype status | Rev-5 disposition |
|---|---|---|
| F2-A page canonicalization | implemented, but lacks rev-5 prefix and rollover cases | harden |
| F2-B preprint discrimination | implemented | retain with routing changes |
| F2-C physical-location rule | implemented with some vetoes, but no title floor or authoritative-journal method | harden |
| F2-D strict-prefix rule | implemented | disable; deferred |
| F2-E title-furniture excision | partly implemented | harden and complete provenance/author recovery |
| F2-F A-side resolver | prototype implemented | redesign before any live batch |
| F2-G journal authority | absent | implement before F2-C is accepted |
| F2-H broad collective-author matching | absent | deferred |
| F2-I transposition recovery | absent | implement only as a non-destructive search hypothesis |

The focused prototype tests currently pass (`61 passed`), and the full `cre/f1` suite
currently passes (`470 passed`). Those facts establish mechanical compatibility only; they
do not close the semantic gaps listed above.

---

## 4. Non-negotiable safety invariants

1. **Raw evidence is immutable.** Every normalization stores the original value, selected
   normalized value, rule name, rule version, and before/after values.
2. **No silent disappearance.** A row that changes route MUST appear in a churn artifact.
3. **No heuristic auto-clear.** Page, journal, author, prefix, or relation heuristics may
   route a row to an auditable review queue; they may not silently convert a sub-accept
   title comparison to `match`.
4. **Hard contradiction dominates heuristic agreement.** A confident DOI, first-author,
   year, version, or locator conflict cannot be erased by weaker similarities.
5. **Tri-state remains tri-state.** `author_match`, `first_author_match`,
   `journal_match`, `volume_match`, `pages_match`, `doi_match`, and
   `abstract_overlap` use `True`, `False`, or `None`. Code tests them with `is True` and
   `is False`.
6. **Absence is not evidence.** Missing relation metadata, a provider 404, no search hit,
   no abstract, or a disabled provider never proves that two works are distinct or
   identical.
7. **Retrieval failure is not unscoreable.** Network failure, quota exhaustion, parse
   failure, and incomplete source coverage route to `retrieval_incomplete`.
8. **Stable identifiers are not infallible citation evidence.** A shared DOI or PMID is
   strong evidence about a record, but a mixed-identity citation can attach that identifier
   to text describing another work.
9. **Same study is not automatically same work.** Abstract/full-paper and
   protocol/results pairs require a related-work review route.
10. **Detector output is not gold.** Resolver results and proposed repairs remain hidden
    from annotators until their labels are committed.
11. **Development and validation stay separate.** Seed-37 rules and labels never support a
    held-out claim. A fresh sealed sample is required for final PPV.
12. **Every threshold is single-sourced.** A constant is defined once, serialized into the
    manifest, and tested. Comments and notebooks must not carry competing values.

---

## 5. Output and routing model

### 5.1 Matcher route

The matcher emits exactly one route:

| Route | Meaning |
|---|---|
| `match` | sufficient non-contradictory evidence for an ordinary bibliographic match |
| `review_wrong_paper` | evidence that A and B may be distinct |
| `review_same_work_variant` | evidence for one work or a version family, still auditable |
| `review_related_work` | A and B are distinct outputs of one study or another explicitly related-work class whose F2 policy is not settled |
| `review_formatting` | likely text/formatting defect; still auditable |
| `review_mixed_identity` | citation fields or identifiers conflict across possible works |
| `unscoreable` | no usable bibliographic handle after deterministic repair hypotheses |
| `unresolved` | B did not resolve |
| `retrieval_incomplete` | required provider work did not complete |

All `review_*` routes are first-class outputs, not “suppressed data.”

### 5.2 A-resolution status

The resolver emits one status independent of matcher route:

- `resolved_unique`
- `ambiguous_multiple`
- `not_found_complete`
- `retrieval_incomplete`
- `conflicting_evidence`
- `no_searchable_handle`

### 5.3 A-versus-B identity status

When A is `resolved_unique`, comparison to B emits:

- `same_record`
- `declared_version_family`
- `same_study_related_work`
- `distinct_record`
- `undetermined_identity`

Only `distinct_record` may carry a proposed replacement C. It is still a proposal, not a
human F2 label.

### 5.4 Decision precedence

After deterministic repairs, required retrieval, relation classification, and A-resolution
are complete, apply the following order once in one shared function used by online scoring,
offline rebanding, reporting, and tests:

1. B unresolved -> `unresolved`; a required retrieval failure -> `retrieval_incomplete`.
2. No searchable handle after all deterministic hypotheses -> `unscoreable`.
3. A is `resolved_unique` and A-versus-B is:
   - `distinct_record` -> `review_wrong_paper`;
   - `same_study_related_work` -> `review_related_work`;
   - `declared_version_family` -> `review_same_work_variant`;
   - `same_record` with no hard conflict -> continue to formatting or ordinary-match
     evaluation;
   - `undetermined_identity` -> continue to the next rule.
4. A is ambiguous/conflicting, or unresolved mixed-identity/hard-conflict evidence remains
   -> `review_mixed_identity`.
5. A guarded high-precision same-work rule fires -> `review_same_work_variant`.
6. Guarded deterministic wrong-paper evidence fires -> `review_wrong_paper`.
7. A formatting-repair rule fires without identity conflict -> `review_formatting`.
8. The ordinary non-contradictory acceptance rule passes -> `match`.
9. Otherwise -> `review_mixed_identity` with reason `insufficient_identity_evidence`.

No other call path may reimplement this precedence.

### 5.5 Threshold registry

These names have different semantics even when two currently share the same numeric value:

```text
MATCH_ACCEPT_SCORE = 0.85
SAME_WORK_TITLE_SIM_MIN = 0.92
COORDINATE_REVIEW_TITLE_MIN = 0.85
A_TITLE_MIN = 0.85
A_TITLE_STRONG = 0.95
```

They live in one versioned configuration object and are serialized into every manifest.
`SAME_WORK_TITLE_SIM_MIN=0.92` remains unchanged in revision 5. A code default, notebook
copy, or older comment with another value is not authoritative.

---

## 6. F2-E — recover leading title furniture first

**Files:** parser, title-furniture helper, schema, run-record builder.

F2-E runs before page/journal identity decisions because it repairs the title and author
evidence those rules consume.

### 6.1 Allowed written-side signatures

Detection MUST use the written citation only. It MUST NOT inspect B's title, authors, or
journal to decide where to cut.

Allowed head-anchored signatures:

1. `author_run`: at least two semicolon-delimited name groups, each a surname/initial form
   or a capitalized mononym, followed by a period and a distinctive title remainder.
2. `chapter_label`: `Chapter`, `Section`, `Part`, or `Appendix`, followed by an Arabic
   number, Roman numeral, or spelled number, then a dash, colon, or period.
3. `article_type_label`: a bounded leading phrase ending in a controlled genre noun such
   as `Report`, `Editorial`, `Commentary`, `Erratum`, `Correction`, `Statement`,
   `Position Paper`, `Guideline`, or `Consensus Statement`, followed by period-space and
   a distinctive remainder. Straight and typographic apostrophes are equivalent.

Forbidden:

- a generic “period-space-capital” rule;
- trailing-title excision;
- use of `resolved_authors` or `resolved_title` to find the boundary;
- destructive overwrite of the original title.

### 6.2 Stored fields

For every firing, store:

```text
written_title_original
written_title_selected
written_title_excised
written_title_excision_rule
written_title_excision_version
excised_author_tokens
written_authors_original
written_authors_selected
```

`written_title_selected` is the scored title. `written_title_original` remains available
for audit and alternative search.

For `author_run`, parse the excised names, append them after the already structured author
sequence, preserve source order, and deduplicate only exact normalized duplicates. Never
replace a structured first author with a leaked title author.

### 6.3 Acceptance cases

The rule MUST cover the known `PMC10227918` author-overflow rows, including mononym starts
such as `Gopichand` and `Zaheer-ul-Haq`, and the chapter-label cases
`PMC11244905:R55` and `PMC9340374:B8`.

It MUST be a no-op on:

- `U.S. trends in dementia prevalence`;
- a legitimate title ending in `Allicin`, `Dimethyl Sulfoxide`, or a species name;
- a prefix whose removal leaves no distinctive handle;
- a normal title containing a semicolon-separated list after the first title word.

Every firing enters `rule_events.jsonl`; no firing alone can produce `match`.

---

## 7. F2-A — canonical page ranges

**File:** `biblio_match.py`, the comparator feeding `FieldAgreement.pages_match`.

F2-A changes only the page-comparison evidence. It does not itself choose a route.

### 7.1 Canonicalization

1. `None`, empty, and whitespace-only values become absent and preserve
   `pages_match=None` when either side is absent.
2. Fold `‐ ‑ ‒ – — ― −` to ASCII `-`, remove internal whitespace, and lowercase.
3. Canonicalize each comma-separated segment independently; preserve segment order.
4. Parse a segment as:

   ```text
   optional-alpha-prefix + start-digits + "-" +
   optional-alpha-prefix + end-digits + suffix
   ```

5. If both prefixes are present and differ, leave the segment unexpanded.
6. If the end has fewer digits than the start, let `width = 10 ** len(end)`,
   compute:

   ```text
   candidate = start - (start % width) + end
   while candidate < start:
       candidate += width
   ```

   This gives `1199-8 -> 1199-1208`, `3143-421 -> 3143-3421`, and
   `925-8.e4 -> 925-928.e4`. When expansion is required, format the end with at
   least the start's original digit width so leading zeroes are preserved
   (`001-9 -> 001-009`); allow a longer result when a boundary is crossed.
7. Preserve a shared alpha prefix once: `S141-S144` and `S141-4` both canonicalize
   to `s141-144`.
8. Non-ranges such as `S100`, `e0224455`, `CD010442`, and `xii-xv` remain folded but
   unexpanded.
9. A bare start page does not equal a range. Start-page-only similarity MAY be measured
   but is not implemented as agreement.

### 7.2 Required tests

Tests MUST include at least:

```text
141-144 == 141-4
1083-1091 == 1083-91
3143-3421 == 3143-421
117-132 == 117–32
925-928.e4 == 925–8.e4
S141-S144 == S141-4
1199-1208 == 1199-8
1-12 == 1-12
9-11 != 9-12
A12-B15 remains unexpanded
empty vs any value -> None
comma-separated segments canonicalize independently
```

Frame-wide movement MUST be reproduced from the pinned freeze, not copied from an earlier
report.

---

## 8. F2-G — authoritative journal identity

**Implement before F2-C is accepted.**

The existing containment comparator is useful as a weak similarity feature but is not
strong enough to prove physical-location identity.

### 8.1 Evidence strength

Journal comparison returns:

```text
journal_match: True | False | None
journal_match_method:
  exact_text
  authority_alias_unique
  issn_intersection
  nlm_unique_id
  containment_heuristic
  manual_alias_unique
  ambiguous_alias
  unavailable
journal_match_authoritative: bool
```

Only `authority_alias_unique`, `issn_intersection`, `nlm_unique_id`, and a reviewed
`manual_alias_unique` mapping to one stable canonical journal ID are authoritative enough
for F2-C. Identical normalized text alone is `exact_text`; it remains non-authoritative
unless the pinned authority snapshot proves that the alias maps to exactly one record.

Containment MAY contribute to the composite score, but it MUST NOT satisfy F2-C's
authoritative-journal gate.

### 8.2 Authority snapshot

Use a pinned NLM serials snapshot, not an unversioned live lookup. NLM's current
downloadable serials source is Serfile; the older List of Serials Indexed for Online Users
is no longer maintained after its 2024 edition. Record snapshot date, format, license/terms,
and SHA-256 in the run manifest.

Build alias mappings from:

- NLM unique ID;
- full and variant titles;
- NLM title abbreviation;
- ISO abbreviation when present;
- every print, electronic, linking, or other ISSN attached to the same catalog record.

An alias mapping to more than one NLM record is ambiguous and yields `None`, not `True`.
An exact string shared by multiple authority records is also ambiguous.

ISSN equality is available only when both compared sides actually carry parsed or
authoritatively resolved ISSN sets. The fact that a retrieval provider can return an ISSN
does not create a written-side ISSN.

### 8.3 Residual aliases

The distinct containment-only pair census is a review artifact. A human-approved alias:

- lives in a versioned table;
- cites its evidence and reviewer;
- maps to a stable canonical journal ID;
- has positive and collision tests;
- is never inferred merely because it improves the 51-row development result.

The first four rev-4 examples should be covered by authority data if unambiguous. The
corrupt `Europ Moll Biology Organ Rep` and translated `Health Research` examples remain
review-only unless a stable authority mapping is proved. Rev 4's blanket “six rows fixed”
claim is withdrawn.

---

## 9. F2-B — preprint and version evidence

The `10.1101` DOI prefix is shared by preprints and Cold Spring Harbor Laboratory Press
journals. A DOI beginning `10.1101/` is a preprint signal only when it matches the
date-stamped preprint form or provider metadata explicitly types the record as posted
content/preprint.

Claimed-side and resolved-side signals remain separate:

- claimed-side preprint evidence may support a version-family review;
- resolved-side preprint evidence on an ordinary-looking citation is a possible
  wrong-target signal, not a label.

A resolved-side preprint signal alone MUST NOT force `review_wrong_paper`. It is combined
with relation, citing-date, title, author, and identifier evidence. When those are
incomplete, route `review_same_work_variant` or `review_mixed_identity`.

Crossref relation absence is `None`. Depositors do not supply complete relationship
metadata.

---

## 10. F2-C — physical-location evidence

The phrase “two articles cannot share a physical location” is too strong and is withdrawn.
Pagination can restart by issue or supplement, article locators can be reused across
containers, and mixed citations can carry a coherent wrong coordinate bundle.

F2-C is an auditable same-work-review rule, not an auto-clear.

It may fire only when:

```text
pages_match is True
volume_match is True
journal_match_authoritative is True
doi_match is not False
first_author_match is not False
year_match is not False
title_sim >= COORDINATE_REVIEW_TITLE_MIN
no mixed-identity signal
no explicit inter-work relation
```

Additional requirements:

- If issue data exists on both sides, issue disagreement vetoes the rule.
- If issue is absent and the canonical start page is at most 20, tag
  `short_range_issue_unknown`; the row stays manual review and is not counted as a
  high-confidence same-work proof.
- A DOI match does not override a materially contradictory title/locator bundle.
- A title below `COORDINATE_REVIEW_TITLE_MIN` routes `review_mixed_identity`, even if all three
  coordinates agree.
- The output records all evidence methods, not only booleans.

F2-C may move a row from `review_wrong_paper` to `review_same_work_variant`. It may not
move it to `match`.

### 10.1 Strong-corroboration override

The existing `_override_quality` path is a cross-cutting recall risk. Page or journal
normalization can remove the last recorded disagreement and accidentally let
author-plus-journal boosts lift a low-title pair to `match`.

Revision 5 requires:

- `title_sim < MATCH_ACCEPT_SCORE` cannot become `match` through the generic
  corroboration override;
- `doi_match is False`, `first_author_match is False`, or any mixed-identity signal
  vetoes the override;
- exact-record evidence that is strong enough to justify an ordinary match must use the
  shared precedence function in §5.4, not a separate score floor;
- every override firing records its input evidence and before/after route;
- changing `pages_match` or `journal_match` requires tracing and testing
  `match_score`, `_override_quality`, `_flag_decision`, `flag_verdict`,
  `assess_same_work`, offline rebanding, and reporting consumers.

The low-title coordinate counterexample `PMC10424567:R78|25836306` is a mandatory
adversarial fixture: coordinate normalization must not silently convert it to `match`.

---

## 11. F2-D — strict-prefix title rule

**DEFERRED and disabled in revision 5.**

The current prototype contains an active strict-prefix branch. It MUST be disabled before
the next candidate reband.

Reasons:

- it has zero independent development gain after the safer rules;
- prefix shape alone cannot distinguish truncation from a sequel, part, update, subgroup,
  or related publication;
- the relevant seed-37 rows have unresolved or contradictory human labels;
- revision 4 simultaneously called one conference abstract/full-paper pair “genuinely
  different papers” and a “same-work variant.”

If revived in a future spec, it must be review-only and require:

```text
strict prefix at a word boundary
both titles distinctive
doi_match is not False
volume_match is not False
first_author_match is not False
year_match is not False
no serial/part/update/edition marker conflict
no multiple eligible A candidates
```

Its frame-wide firings and independently adjudicated benefit must be frozen before
activation.

---

## 12. F2-H — collective authors

The existing narrow corporate-author formatting equivalence may remain.

The proposed broad rule that strips acronyms, converts punctuation, and accepts arbitrary
initialism matches is **DEFERRED**. It has zero independent gain on the current development
band, and acronym collisions can merge distinct organizations.

Allowed in revision 5:

- punctuation and whitespace folding;
- parenthesized acronym removal only when the remaining full names are exactly equal after
  normalization;
- parser repair supported by explicit JATS structure, such as a collective name split into
  a surname plus a one-letter given-name artifact;
- exact, versioned aliases with evidence and collision tests.

Forbidden:

- accepting a bare acronym or word-initial string as sufficient identity;
- roster-wide author matching as a substitute for first-author identity;
- counting an unadjudicated band movement as “false positives removed.”

The 19 flagged collective-author rows may be reported as **affected rows**, not false
positives, until adjudicated.

---

## 13. F2-I — field-transposition hypothesis

Do not swap fields destructively, and do not use B's journal to decide that A's fields are
transposed.

Create an alternative written-side search hypothesis when both conditions hold:

1. `written_title` is independently recognized as a journal/container name by the pinned
   authority data or is a single generic container term; and
2. `written_authors[0]` is a distinctive, sentence-like title candidate with at least six
   lexical tokens.

Store:

```text
field_hypothesis = "title_first_author_transposed"
hypothesis_title
hypothesis_container
original_title
original_authors
hypothesis_evidence
```

Run the original and transposed hypotheses through F2-F. Do not select the transposed
hypothesis merely because it resembles B. If both resolve to different eligible records,
the status is `conflicting_evidence`.

`PMC12168542:B1` is the required positive fixture. At least two no-op fixtures involving a
long collective author and a genuine one-word title are required.

### 13.1 Scoreability after repair hypotheses

Remove the prototype's blanket “single long word” unscoreable gate. A one-word or short
title can be a legitimate article title, and a DOI or other metadata may still provide a
searchable handle.

Classify `no_searchable_handle` only after F2-E, F2-I, and all non-conflicting identifier
hypotheses are considered. Structural no-handle cases include:

- empty or placeholder title with no usable identifier or metadata;
- numeric/year/locator-only title with no other handle;
- a title slot authoritatively identified as a container, with no recoverable title
  elsewhere;
- an author residue with no recoverable title.

`Commentary`, `Editorial`, `Introduction`, or `Anaesthesiology` alone may trigger a
container/generic-title hypothesis, but does not by itself prove unscoreability when a DOI,
PMID, author, or locator can still resolve A.

---

## 14. F2-F — A-side resolver and proposed repair

The current three-way prototype is not production-safe. In particular:

- shared DOI currently implies A equals B, which fails the
  `PMC8015328:ref011` mixed-identity guard;
- the cascade stops at the first hit, making source order decide identity;
- provider failure can collapse into `unscoreable`;
- declared relations and citing-date policy are missing;
- the implemented title floor (`0.90`) conflicts with revision 4's proposed `0.85`;
- OpenAlex access requirements have changed and now require an API key and usage budget.

### 14.1 DOI normalization

Normalize scheme/prefix, case, surrounding whitespace, and trailing citation punctuation.
Do not strip an arbitrary terminal `vN` from every DOI.

A preprint URL version suffix may be treated as URL decoration only when:

- it matches a registered, provider-specific preprint pattern;
- the base DOI resolves;
- the transformation is recorded; and
- no distinct version DOI is being collapsed.

### 14.2 No shared-DOI early exit

If written DOI equals B's DOI:

- with no hard metadata conflict, record strong `same_record` evidence;
- with hard title/author/year/journal/volume/page conflict, assign the provisional route
  `review_mixed_identity` and search A using the non-DOI written hypothesis;
- if that search uniquely resolves a coherent A distinct from B, replace the provisional
  route with `review_wrong_paper`, retain `mixed_identity=true`, and record which written
  identifiers were treated as contaminated fields.

The known *Paurodontella persica* / *composticola* case MUST resolve through that last
branch to `review_wrong_paper`; it MUST never return `match`, `same_record`, or a non-F2
semantic conclusion solely because the DOI and PMID agree.

### 14.3 Relation handling

Gather Crossref relations and PubMed `CommentsCorrections` links before inferring identity.

Classify relation types:

- **intra-work/version evidence:** preprint, manuscript, expression, format, version,
  translation, manifestation, or identical;
- **inter-work evidence:** comment, reply, correction, retraction, review, derivation,
  continuation, or part.

An intra-work relation routes to `declared_version_family`, not automatically `not_f2`.
Record both publication dates and the citing paper date. A later version unavailable at the
citing date cannot be assumed to be the intended citation.

An inter-work relation does not prove same work. Comment/reply links may identify a repair
candidate for the original work.

### 14.4 Candidate collection

Build hypotheses from:

1. original written metadata;
2. F2-E cleaned title/author metadata;
3. F2-I transposition hypothesis, when present;
4. DOI-based lookup, unless the DOI is marked mixed/conflicting.

Collect candidates from all enabled sources rather than stopping at the first hit:

- DOI resolver/registration-agency lookup;
- PubMed ESearch/EFetch;
- Crossref bibliographic query;
- OpenAlex search.

Do not call a DOI dead because `api.crossref.org/works/{doi}` returns 404. Resolve or
identify the DOI registration agency and use the appropriate metadata path. Record
withdrawal, replacement, and `update-to` metadata separately from ordinary lookup failure.

Deduplicate candidates into identity clusters using authoritative IDs and explicit
relations. Provider rank and Crossref's search score are recorded but never used as an
acceptance threshold.

### 14.5 Candidate eligibility and uniqueness

Use the existing title normalizer and similarity implementation with the A-resolution
thresholds from §5.5.

A candidate is eligible only if its title is distinctive and either:

1. `title_sim >= A_TITLE_MIN` plus at least two non-title corroborators among
   first author, year, authoritative journal, volume/pages, or a non-conflicting written
   DOI; or
2. `title_sim >= A_TITLE_STRONG` plus one non-title corroborator; or
3. an authoritative written DOI resolves to it and the written metadata contains no hard
   mixed-identity conflict.

A confident DOI disagreement, first-author disagreement, or incompatible version/part
marker is a hard conflict unless that field is explicitly excluded by a recorded
mixed-field hypothesis.

Resolution is `resolved_unique` only when exactly one identity cluster is eligible. If
multiple clusters are eligible, return `ambiguous_multiple`; do not choose by provider
order or a small score margin.

Year is a corroborator, not a universal veto. Preprint/version relations use exact dates
and citing-date policy rather than a global ±1 rule.

### 14.6 No-hit and failure semantics

- Every enabled source completed with no eligible candidate:
  `not_found_complete`.
- Any required source timed out, returned a terminal quota/authentication error, produced
  an invalid response, or was disabled for missing credentials:
  `retrieval_incomplete`.
- No distinctive title, identifier, or structural hypothesis:
  `no_searchable_handle`.
- Conflicting eligible hypotheses:
  `conflicting_evidence`.

Only `no_searchable_handle` maps to `unscoreable`. The others stay separate.

Resolver failure or ambiguity never downgrades the deterministic matcher:

- `not_found_complete` retains the pre-resolver matcher route and adds the resolver status;
- `ambiguous_multiple` or `conflicting_evidence` routes `review_mixed_identity`;
- `retrieval_incomplete` routes `retrieval_incomplete` when resolver evidence is required
  for the decision; it never becomes `match` or `unscoreable`.

### 14.7 A versus B

When A is unique:

- shared authoritative identity cluster with no mixed conflict -> `same_record`;
- explicit intra-work relation -> `declared_version_family`;
- explicit same-study but distinct-output evidence -> `same_study_related_work`;
- distinct authoritative identity clusters, no intra-work relation, and coherent distinct
  metadata -> `distinct_record`; this includes a recorded mixed-field hypothesis in which
  contaminated written identifiers point to B but the remaining written fields uniquely
  resolve A;
- anything else -> `undetermined_identity`.

Only `distinct_record` receives `proposed_repair=C`.

If A resolves to B, use `bibliographic_text_variant` unless a specific parser corruption is
proved. Do not call all ordinary variants `corrupt_bibliography`.

### 14.8 Abstract evidence

Abstract overlap is **DEFERRED** from decision-making in revision 5. It may be emitted as
unthresholded auxiliary evidence only.

Before it affects routing, a later spec must name the exact text source, preprocessing,
scorer, threshold, calibration set, missing-data semantics, and tests. Missing abstract
remains `None`.

### 14.9 Provider operation

- NCBI: include `tool` and `email`, cache responses, and obey the current E-utilities
  policy (currently 3 requests/second without a key and 10 with a key by default).
- Crossref: use the polite pool, identify the client, cache responses, honor
  `x-rate-limit-*` and `x-concurrency-limit`, and back off on 429/5xx.
- OpenAlex: require an API key and enforce the account's current usage budget. Missing or
  exhausted access makes that source unavailable; it is never a negative result.
- Rate limits are per provider. No shared limiter may accidentally let one source's policy
  govern another.
- Raw provider responses or content-addressed cache entries MUST be retained for replay.
- API keys and secrets MUST NOT appear in manifests, logs, or artifacts.

---

## 15. Special relation classes

### 15.1 Comment or reply versus original

These are distinct bibliographic works. If the written citation describes the original but
B is a commentary/reply, an explicit relation may establish a `distinct_record` and supply
the original as C. Required fixtures include `PMC9252976:CR36` and `PMC9705589:B8`.

### 15.2 Conference abstract versus full paper

These are treated as `same_study_related_work`, not automatically same record and not
automatically F2. Required development fixtures include `PMC8097933:CR9`,
`PMC12864399:B12`, and `PMC9829249:R20`.

The route is `review_related_work` until the taxonomy policy explicitly decides when citing
one output with the other's identifier is F2.

### 15.3 Cochrane volume/issue representation

The year-as-volume behavior is a venue-specific representation difference. No general
year-shaped-volume normalization lands in revision 5. A Cochrane-specific rule is deferred
until its firing population and collisions are measured.

---

## 16. Measurement and adoption

### 16.1 Development accounting

For seed-37, report:

- route counts under the current derivative labels;
- route counts under any separately named provisional relabel scenario;
- paired per-row route movement;
- rule firing counts;
- confirmed and pending label counts;
- all new entrants without semantic claims.

The evaluation population is the exact final `review_wrong_paper` route. A reporting band
such as `BAND_STRONG_WRONG`, a score threshold, or a log label is not a substitute; route,
record count, and joined label IDs must reconcile exactly.

Use the term **development PPV** only after a complete immutable label snapshot exists.
Because rules were selected using these 51 cases, its confidence interval does not repair
the adaptive reuse and MUST NOT be presented as prospective precision.

### 16.2 Recall accounting

Distinguish:

- **review recall:** a confirmed F2 remains in any explicit review route;
- **HIGH-band sensitivity:** a confirmed F2 remains in `review_wrong_paper`;
- **auto-clear loss:** a confirmed F2 moves to `match`, `unscoreable`, or an omitted state.

Any confirmed F2 auto-clear is a merge veto.

A confirmed F2 may move from HIGH to another auditable review route only with a row-level
report and explicit ZD sign-off. The vague rev-4 rule “gain more than it loses” is
withdrawn because it compared unlike quantities without a loss weight.

### 16.3 Fresh validation

After code, configuration, authority snapshots, and prompts are frozen:

1. select one new natural citation corpus that was not used for rule discovery,
   calibration, or threshold choice, and freeze its occurrence-level frame;
2. run the frozen detector once and freeze the complete output before adjudication.
   Because this draw is single-use, the runner refuses to write a zero-row artifact
   (`_write_run` raises `EmptyFrameError` when the frame is empty; see §20) — an
   empty output here must fail loud, never leave a legitimately-named empty file;
3. adjudicate either every `review_wrong_paper` occurrence or a probability sample drawn
   only from that exact route, with the selection rule, seed, ordered frame hash, sample
   hash, and inclusion probability recorded before labels are viewed;
4. keep detector internals, resolver proposals, scores, and route reasons hidden until
   adjudication is committed;
5. report PPV for the exact `review_wrong_paper` route. Use an interval appropriate to the
   sampling design; a Wilson interval is allowed only for an unclustered equal-probability
   occurrence sample. Account for paper-level clustering or unequal weights when present;
6. report the route denominator, adjudicated numerator, missing adjudications, route sizes,
   and coverage buckets;
7. do not tune, relabel selectively, or amend the route on this sample and still call its
   estimate held out.

This audit estimates route PPV only. It does not estimate corpus recall or sensitivity.

### 16.4 Mechanism panel

A separately constructed case-control panel may measure mechanism coverage:

- naturally occurring confirmed positives;
- independently confirmed negatives;
- prespecified mechanism cells;
- blinded, shuffled review;
- exact selection and randomization hashes.

Report panel detection rate and per-cell counts. Do not report panel precision, corpus
specificity, corpus F1, prevalence, or corpus PPV.

---

## 17. Human adjudications required

One correction is already decided and is not pending:

| Citation ID | Incorrect current-CSV label | Required frozen label | Rationale |
|---|---|---|---|
| `PMC8015328:ref011` | `SAME_WORK` | `TRUE_F2` | written *P. persica* record and attached *P. composticola* record are distinct works despite the shared contaminated DOI/PMID |

An export that retains `SAME_WORK` for this row is invalid and MUST NOT be used for any
metric or acceptance decision.

Before label-dependent acceptance, resolve at least:

| Citation ID | Current derivative label | Required decision |
|---|---|---|
| `PMC10227918:r138` | `SAME_WORK` | reconcile with connected-sheet `NOT_A_TITLE` |
| `PMC10227918:r171` | `SAME_WORK` | reconcile with connected-sheet `NOT_A_TITLE` |
| `PMC10424567:R20` | `TRUE_F2` | same record with paraphrased title, or F2 |
| `PMC9340374:B41` | `TRUE_F2` | same record with spliced abbreviation, or F2 |
| `PMC9494430:ref68` | `SAME_WORK` | reconcile with the two identical Kresse citations |
| `PMC8839767:B10-polymers-14-00407` | `SAME_WORK` | Part 1 versus Part 2 bibliographic identity |
| `PMC8887078:R27` | `SAME_WORK` | two different Hastie/Tibshirani publications |
| `PMC9829249:R20` | `SAME_WORK` | abstract/full-paper taxonomy policy |

Group consistency check:

- group rows by a versioned normalized-written-citation signature containing at least
  normalized title, claimed PMID, and normalized written DOI when present;
- identical signatures with different labels are a blocking error unless a human addendum
  explains a citation-context difference.

Label corrections are dated addenda that produce a new artifact hash. They are never silent
edits.

---

## 18. Regression and adversarial fixtures

### 18.1 Existing guards

The seven historical seed-7 PMIDs are:

```text
31665581
16639420
18152150
27665045
25750229
32355637
22926653
```

They do not occur in seed-37. Constructed “shape” tests do not prove that the real seed-7
records still behave correctly.

Release requires either:

1. a pinned seed-7 artifact with exact path, hash, and real-record replay; or
2. reviewed immutable fixtures faithfully copied from those records, with provenance.

Until one exists, the real seed-7 guard is **not verified**.

### 18.2 Mandatory seed-37 guards

- `PMC8015328:ref011`: must route `review_wrong_paper`, retain its mixed-identity evidence,
  and preserve the human `TRUE_F2` label; it must never auto-clear on the shared
  contaminated DOI/PMID.
- `PMC11186016:ref55`, `PMC12359113:ref66`, `PMC9494430:ref68`: identical Kresse
  mechanism and consistent routing.
- `PMC10296898:B34-foods-12-02325`: related-title wrong-paper control.
- `PMC9252976:CR36` and `PMC9705589:B8`: comment/reply target relations.
- `PMC12168542:B1`: transposition hypothesis without destructive mutation.
- the F2-D risk rows in §17: no strict-prefix auto-demotion.

### 18.3 Mutation tests

For every same-work rule, mutate one load-bearing field at a time:

- DOI match -> mismatch;
- first author match -> mismatch;
- year match -> mismatch;
- authoritative journal match -> containment-only;
- page/volume match -> mismatch;
- unique A candidate -> two eligible candidates;
- relation present -> absent;
- source complete -> one required source failed.

The route must fail safe exactly as specified.

---

## 19. Required output artifacts

Each candidate run emits:

1. `run_manifest.json`
2. `all_rows.jsonl`
3. `rule_events.jsonl`
4. `retained_original_high.jsonl`
5. `moved_from_original_high.jsonl`
6. `new_high_entrants.jsonl`
7. `resolver_candidates.jsonl`
8. `resolver_outcomes.jsonl`
9. `guard_report.json`
10. `summary.json`

`all_rows.jsonl` and every derived JSONL MUST validate against a versioned JSON Schema.
Each row contains, at minimum:

- occurrence identity (`citation_id`, source PMCID, claimed PMID);
- input artifact and code provenance;
- original and selected written fields;
- B metadata;
- every tri-state field agreement plus comparison method;
- title similarity, composite score, and threshold configuration;
- route before and after each rule;
- ordered rule events;
- scoreability and retrieval-completeness states;
- A candidates, provider provenance, eligibility failures, and identity clusters;
- relation type, direction, source, and dates;
- A-resolution and A-versus-B statuses;
- proposed C only when allowed by §14.7.

Gold labels and hidden adjudications are not part of resolver or detector row schemas.
Evaluation joins them later by immutable occurrence ID.

### 19.1 Machine contract

Schemas use JSON Schema draft 2020-12, set `additionalProperties: false` at every
controlled object, and require a top-level `schema_version`. Null is data, not an omitted
key. At minimum, `all_rows.jsonl` enforces:

| Field | Type / constraint |
|---|---|
| `schema_version` | non-empty string fixed by the run manifest |
| `run_id` | non-empty string |
| `citation_id`, `src_pmcid`, `claimed_pmid` | strings; `claimed_pmid` may be null only when the source truly lacks it |
| `written_original`, `written_selected`, `b_record` | objects with explicit nullable bibliographic fields |
| `agreements` | object containing every named comparison as `boolean \| null` plus its method |
| `title_sim`, `match_score` | number in `[0,1]` or null; null requires a reason |
| `route` | enum of every route in §5.1 |
| `route_reason` | non-empty, versioned reason code |
| `rule_events` | ordered array; each event has rule ID/version, before route, after route, and evidence |
| `a_resolution_status` | enum from §5.2 |
| `a_b_identity_status` | enum from §5.3 or null when A is not uniquely resolved |
| `retrieval` | per-provider status, timestamp, cache key, and response hash; never a secret |
| `a_candidates` | array of candidates with source, identity cluster, evidence, and every failed eligibility condition |
| `proposed_c` | object or null; non-null only with `distinct_record` |

Per-provider retrieval status is exactly one of:

```text
not_required
completed_hit
completed_no_hit
timeout
quota_exhausted
authentication_failed
invalid_response
disabled_missing_credentials
```

The schema MUST reject NaN/infinity, unknown route/status values, missing tri-state keys,
gold-label fields, known secret-bearing field names, and a non-null `proposed_c` under any
identity status other than `distinct_record`. A separate preflight secret scan MUST test
serialized artifacts and logs against configured credential values before publication.

`rule_events.jsonl` uses the same event object as `all_rows.jsonl`; tests compare the
embedded and standalone event sequences byte-for-byte after canonical JSON serialization.
Derived churn artifacts preserve the full source row and add only their schema-defined
membership fields.

The three churn sets are disjoint and exhaustive relative to original HIGH membership:

- retained original HIGH;
- moved from original HIGH, with before/after route and rule;
- new HIGH entrants.

Every `moved_from_original_high` row includes the full ordered rule trace. “Suppressed” is
not used as a synonym for deletion.

If new entrants are sampled for adjudication, the sampling rule, seed, ordered frame hash,
sample hash, and inclusion probabilities are fixed before labels are viewed.

---

## 20. Verification sequence

Work from the existing prototype branch. Do not prune the active worktree, recreate the
branch, or run a destructive reset as part of this specification.

Before edits:

```bash
git -C /Users/kamachi/citation-repair-engine status --short --branch
git -C /Users/kamachi/citation-repair-engine rev-parse HEAD
git -C /Users/kamachi/citation-repair-engine merge-base --is-ancestor \
  a0c1060e85d74a57de0f1c0bb8c9060c87a5caa4 HEAD
```

The Python package and tests live under:

```text
/Users/kamachi/citation-repair-engine/citation_repair_F1_handoff
```

Run tests from that directory so `cre.f1` resolves from the intended checkout. The
reproducible baseline commands are:

```bash
cd /Users/kamachi/citation-repair-engine/citation_repair_F1_handoff
../.venv_cre/bin/python -m pytest -q -p no:cacheprovider \
  cre/f1/test_f2_matcher_revision.py cre/f1/test_resolve_a.py
../.venv_cre/bin/python -m pytest -q -p no:cacheprovider cre/f1
```

At the 2026-07-26 prototype baseline these produce `61 passed` and `470 passed`,
respectively. A later run is accepted by zero failures and complete discovery, not by
matching those historical counts.

Implementation order:

1. freeze the complete label snapshot or mark all label metrics blocked;
2. centralize schema, routes, precedence, constants, and manifest;
3. F2-E;
4. F2-A;
5. F2-G;
6. F2-B routing hardening;
7. F2-C;
8. disable F2-D;
9. F2-I hypothesis;
10. F2-F redesign;
11. churn/reporting artifacts;
12. real guard replay and fresh validation preparation.

After each step:

- run the affected unit and mutation tests;
- run all `cre/f1` tests;
- run `git diff --check`;
- reband the frozen seed-37 frame offline when the step affects deterministic scoring;
- compare route movement and verify all original HIGH rows are accounted for;
- do not run the network resolver until all provider preflights pass.

The offline reband is run with (added 2026-07-27 — previously described but not
runnable; `reband_from_cache` was a Python-only function and no CLI existed):

```bash
cd /Users/kamachi/citation-repair-engine/citation_repair_F1_handoff
../.venv_cre/bin/python -m cre.f1.f2_run_v3 --reband-from-cache \
  --resolved-cache <resolved-cache.jsonl> --xml-dir <source-xml-dir> \
  --seed 37 --version candidate_02 --out-dir <out-dir>
```

The resolved cache is keyed by PMID and carries no source PMCID, so the
`--xml-dir` file stems define the source-PMCID frame; the **real pinned source-XML
corpus must therefore be present**. The command FAILS LOUD when it is not, rather
than emitting an all-zeros summary at exit 0 (which would read as a pass): no
`.xml`/`.nxml` files at all → argparse error, exit 2; a frame that comes back
empty → **exit 3, diagnostic on stderr, nothing on stdout**. Critically, the
empty-frame guard lives in the shared `_write_run` (`refuse_empty=True` by
default) that BOTH entry points funnel through — the reband AND the fresh-draw
runner `run_f2_seed7_v3` — and raises `EmptyFrameError` **before writing any
file**, so a refused run leaves **no** zero-row `*_summary.json` / `*.jsonl` on
disk under a real-looking name (which a later `glob('*_summary.json')`, hash-pin,
or another session/model would otherwise pick up as a real run). Placing it in
`_write_run` rather than one caller also protects the higher-stakes path: the
fresh-draw runner emits the SINGLE-USE held-out artifact (§16.3), which cannot be
re-run. The equivalent `python -c "from cre.f1.f2_run_v3 import reband_from_cache;
..."` snippet inherits the same guard; a caller that genuinely wants an empty
frame (e.g. a join-logic unit test) must pass `refuse_empty=False` explicitly.
This corpus gap is why the step has not closed frame-wide in either local
environment (§2.2).

Do not use fixed pass counts as acceptance criteria. The criterion is zero failures in the
complete discovered suite plus every required semantic fixture in this spec.

Network preflight MUST separately prove:

- credentials/configuration;
- one successful request and parse per enabled provider;
- rate-limit header handling where supplied;
- cache write/read replay;
- 404/no-hit semantics;
- timeout/quota/authentication failure semantics;
- no secret leakage.

For every Colab or other long-lived Python runtime:

- start a fresh interpreter after changing the checkout;
- record `Path(module.__file__).resolve()` for the matcher, resolver, and schema modules;
- assert the loaded source-tree hash/commit equals the manifest commit before processing;
- make the destination directory explicit and persistent before the first row;
- write checkpoints atomically and verify their hashes before resume;
- never treat `/content`, `/tmp`, or an in-memory dataframe as the only copy of a
  production artifact.

---

## 21. Definition of done

Revision 5 is complete only when all of the following are true:

1. Frozen inputs and every output are hash-pinned in a manifest.
2. The complete 51-row human label source is immutable, fully populated, rationalized, and
   hashed, or all label-dependent metrics remain explicitly blocked.
3. All §17 adjudications are resolved or excluded from label-dependent acceptance.
4. Raw citation evidence survives every normalization.
5. F2-A passes the complete page acceptance matrix.
6. F2-G distinguishes authoritative identity from containment heuristics.
7. F2-C cannot auto-clear a sub-accept or hard-conflict pair.
8. The generic corroboration override cannot lift a sub-accept title pair to `match`.
9. F2-D and broad F2-H are disabled.
10. F2-E records rule provenance and recovers leaked authors without changing first-author
   order.
11. F2-I is a non-destructive alternative hypothesis.
12. F2-F has no shared-DOI early exit, no first-hit source bias, explicit relation/citing-date
    handling, unique-candidate semantics, and separate failure states.
13. OpenAlex is either successfully authenticated or explicitly unavailable; its absence
    is never treated as a negative search result.
14. Every original HIGH row appears in exactly one churn outcome.
15. Every same-work rule has one-field mutation tests.
16. The real seed-7 guard is replayed from a pinned artifact or provenance-faithful
    immutable fixtures; constructed shapes are reported separately.
17. All required seed-37 guards remain reviewable and none auto-clear.
18. Focused tests, full `cre/f1` tests, and `git diff --check` pass.
19. Network preflights and cache replay pass before any live batch.
20. Detector outputs and resolver proposals never write or expose gold labels.
21. Any final performance claim comes from one fresh sealed sample, not seed-37.
22. Every A-versus-B identity status maps through the shared precedence function to one
    tested route, including `same_study_related_work -> review_related_work`.

---

## 22. Deferred and out of scope

- strict-prefix auto-routing (F2-D);
- broad collective-author acronym/initialism matching (F2-H);
- abstract-overlap decision thresholds;
- start-page-only equality;
- generic Cochrane/year-volume normalization;
- trailing-title excision;
- general title-hyphen or threshold changes;
- corpus recall inferred from unlabeled frame rows;
- corpus precision, specificity, F1, or prevalence inferred from the mechanism panel;
- silent edits to seed-37, seed-7, audited seed-7 outputs, or frozen `v3_5` artifacts;
- any semantic label assigned by an LLM, retrieval provider, or detector output.

---

## 23. External operational references

- [NCBI E-utilities usage policy and API-key rate limits](https://www.ncbi.nlm.nih.gov/sites/books/NBK25497/)
- [NLM downloadable catalog and Serfile data](https://www.nlm.nih.gov/databases/download/catalog.html)
- [NLM Catalog journal fields](https://www.ncbi.nlm.nih.gov/books/NBK3799/)
- [Crossref REST API access, headers, and current limits](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)
- [Crossref relationship types](https://www.crossref.org/documentation/schema-library/markup-guide-metadata-segments/relationships/)
- [OpenAlex current API access requirements](https://developers.openalex.org/)

These links are operational references, not substitutes for pinned run-time snapshots and
manifests.
