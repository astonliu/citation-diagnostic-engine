# F2 — identity evidence — implementation spec

**Date:** 2026-08-16 (revised same day) · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

# ⛔ STOP — READ THIS BEFORE ANY EDIT

**This spec is in two parts, and they are not equally safe.**

- **PART A — LAND NOW.** Four items. Every one is behaviour-identical or purely additive. The
  reported F2 precision figure survives untouched.
- **PART B — DO NOT IMPLEMENT.** Four items. Each one changes verdicts on rows that have **already
  been blind-adjudicated**, and there is **no reserve seed left** to re-measure with. Landing any of
  them destroys the only adjudicated precision figure this project has.

**If you find yourself editing a decision predicate, a threshold, a comparator, or a verdict path,
you are in Part B. Stop and report.**

## Why Part B is locked

**DEC-058, 2026-08-14.** Seed 47's HIGH band was adjudicated blind in one pass: 82 rows,
`f2_prospective_seed47_d90196a_high.jsonl`, `sha256 565903c513884736…`. Counts: **TRUE_F2 74 ·
SAME_WORK 5 · CROSS_LANG 1 · AMBIG 2**, all 82 labelled, no blanks. Three conventions from the same
82 labels:

| convention | n | precision | Wilson 95% | prereg gate (LB > 0.8) |
|---|---|---|---|---|
| citation-error rows counted as correct flags | 76/82 | 0.9268 | [0.8494, 0.9660] | clears |
| **figure of record** | **74/80** | **0.9250** | **[0.8459, 0.9652]** | clears |
| strict floor | 74/82 | 0.9024 | [0.8191, 0.9497] | clears |

Denominator basis: 82 HIGH of 54,243 scoreable = **0.151%** flag rate.

**DEC-057A's binding clause has already triggered:** *after seed 47's HIGH rows are seen, no rule
change, no threshold change.* They have been seen and labelled.

**`RESERVE_SEEDS = (31, 37, 41, 43, 47)` is exhausted.** There is no further preregistered seed. A
banding change is not "spend a seed and redraw" — it is "spend the last one and have nothing."

**Context that should not make this feel safer than it is:** 0.9250 is the **only** adjudicated
precision figure in the project. F3 is unreachable, F5 has never run on real data, F7 has no
production evidence builder, F8 is unimplemented. There is no other number to fall back on.

---

# PART A — land now (safe)

## A1 — import the threshold instead of re-declaring it

`SAME_WORK_TITLE_SIM_MIN = 0.92` at **`biblio_match.py:120`** (audited this session; older specs cite
`:139` and `:152` — those line numbers are stale). Operator `>=`, inclusive; because the compared
value is `round(ts, 4)` (`:461`) the effective gate is `>= 0.91995`.

**`work_identity.py:80` declares `DOI_SAME_WORK_TITLE_MIN = 0.92` as a separate literal**, not an
import. Changing one will never move the other — a latent trap for whoever eventually does change it.

**Required:** import the constant. **The value must not change**, so this is behaviour-identical.
Sibling rule-local floors stay exactly as they are: `0.85` (`:84`), `0.87` (`:85`), `0.85` (`:86`),
`0.78` (`:115`), and inline `0.80` (`:767, 916`), `0.82` (`:784`), `0.70` (`:952, 958`), `0.65`
(`:931`).

**Verify byte-identical banding** on the seed 47 HIGH frame before and after. If a single verdict
moves, you have changed a value — revert and report.

## A2 — make the four tri-state boost reads explicit

`biblio_match.py:430` (`if f.author_match:`), `:432`, `:434`, `:436` use falsy checks on tri-state
fields.

**These are behaviour-identical to `is True`.** For a value in `{True, False, None}`, `if x:` is true
exactly when `x is True`. This is a readability fix that removes a foot-gun, not a semantic change.

**Do not change the boost values or the penalty.** The −0.15 author penalty is already correctly gated
on `is False` (`:439`), which is what keeps absence from being read as mismatch. Verified this
session:

```
author agrees    author_match=True   score=0.8314  verdict=review_formatting
author UNKNOWN   author_match=None   score=0.7814  verdict=review_formatting
author DISAGREES author_match=False  score=0.6314  verdict=review_wrong_paper
```

**Record, do not fix:** unknown forfeits the +0.05 corroboration, so a sparse-author row sits 0.05
nearer the accept cliff than an identical corroborated row — a real, undocumented bias against exactly
the population `eval_report.py:56` calls "F2-prone". **Changing that is Part B.** Add a comment
stating it and leave the arithmetic alone.

## A3 — fix the docstring that describes a gate the code does not run

`flag_verdict`'s docstring (`biblio_match.py:526`) says *"Call `is_scoreable_title` on both titles
before calling this"*. Both real callers use `classify_unscoreable` instead (`lookup.py:486`,
`eval_report.py:337`), and the two are **not** equivalent: `is_scoreable_title` (`:201-227`) catches
journal-as-title by trigram containment ≥ 0.92 and checks the *resolved* title too;
`classify_unscoreable` uses exact normalized equality plus a 6-entry masthead list.

**Required:** correct the docstring to describe what actually runs. **Do not wire
`is_scoreable_title`** — that changes the unscoreable population, which changes the denominator, which
is Part B.

## A4 — instrumentation (purely additive)

`manifest["seam_status"]` (`judgment_run.py:1746-1770`) — the block written specifically so a zero
cannot be read as a rate — covers **F3–F7 only. F1, F2 and F8 have no entry.**

Offline, `denominator_scoreable == 0` plus a `null` rate does distinguish "nothing scoreable" from
"scored, none HIGH" (verified). But there is no `f2_check_ran`, no attempted count, and **no
transport-error counter** — so `resolved_unresolved_excluded` conflates a genuinely dead PMID with an
EFetch failure. A run where NCBI was down for an hour reports a large exclusion count and a
healthy-looking `high_band_rate_of_scoreable` over the survivors, with nothing marking the outage.

Live, a zero F2 is the **absence of a dict key** (`run.py:138`), and `summarize` is `print`ed inside a
bare `except` that swallows any failure into `[eval-report-skip]` while the run still returns success
(`run.py:151-153`).

**Required, all additive — no existing field changes meaning:**

- `seam_status` entries for F1, F2 and F8 carrying attempted / answered / transport-failed / fired.
- A transport-failure counter distinct from `resolved_unresolved_excluded`.
- Remove the bare `except` swallow, or make it log loudly. A silent `[eval-report-skip]` on a
  successful run is the same defect class as everything else in this audit.

**This one also serves the F1 spec** — coordinate, do not duplicate.

## A5 — write down which path owns which number (documentation only)

There are two independent F2 pipelines and they do not agree:

| | LIVE | OFFLINE v3 |
|---|---|---|
| entry | `run.process_reference` (`run.py:86`) | `f2_run_v3.run_f2_seed7_v3` / `reband_from_cache` |
| scorer | `lookup.compare_and_flag` (`lookup.py:388`) | `eval_report.build_f2_record` (`eval_report.py:299`) |
| candidate predicate | `lookup._flag_decision` (`lookup.py:344`) | `flag = verdict != VERDICT_MATCH` (`eval_report.py:401`) |
| headline | `f2_count` / `base_rate_per_pmid_bearing` | `flagged_f2_high` / `high_band_rate_of_scoreable` |

**The reported 0.9250 comes from the OFFLINE path.** Verified divergences, for the record:

- `_flag_decision` has a fourth disjunct (`lookup.py:361-364`) that `flag_verdict` has no counterpart
  for; one probe row was `flagged=True` live and a plain `match` offline.
- `band_of` (`eval_report.py:60-80`) and `flag_verdict` are different band spaces, so `summarize`'s
  `wrong_paper_precision` is computed over a **different population** than `flagged_f2_high`.
- `StageLog` has **no `verdict` field** (`schema.py:287-324`), so the live path cannot record which
  band a row landed in — `flag_verdict`'s return at `lookup.py:497` is consumed by
  `_live_quarantines_variant` and discarded.

**Required:** a docstring or module-header note in `eval_report.py` stating which path is authoritative
for which reported figure, and that the two are not interchangeable. **Do not unify the predicates —
that is Part B.**

---

# PART B — DO NOT IMPLEMENT

Recorded here so the findings are not lost and are not re-discovered. **Each requires ZD's explicit
written sign-off, and landing any one of them retires 0.9250 with no seed left to replace it.**

## B1 — a contradicting DOI is not evidence of anything

`fa.doi_match` is written at `biblio_match.py:385` and **read by no decision**. Absent from the
`disagree` predicate (`:531-538`) and from `any_agree` (`:603-604`). Repo-wide search outside tests
finds only assignments. **Verified:**

```
claimed DOI == resolved DOI            doi_match=True   score=1.0 verdict=match
claimed DOI CONTRADICTS resolved DOI   doi_match=False  score=1.0 verdict=match
title_sim=0.6623 doi_match=False override_fired=True score=0.85 verdict=match
```

Meanwhile `doi_equivalent(...) is True` is load-bearing **for** same-work rescue at eight sites in
`work_identity.py`. The highest-entropy identifier is exculpatory only.

**Precedent pointing one way:** DEC-F2-030 already ruled that a confident DOI disagreement refutes a
version relation (§14.3 governs §15.2). **Precedent pointing the other:** seed 47 was adjudicated
against the current predicate, and 74 of its 80 rows were judged TRUE_F2 without it.

## B2 — a cosmetic page abbreviation flips the verdict

`_digits` (`biblio_match.py:390-391`) strips non-numerics and concatenates, so the standard
bibliographic short form `100-10` (meaning 100–110) ≠ `100-110`. **Verified**, identical pair, only
the page string differing:

```
pages='100-110' pages_match=True   score=0.9938 -> match
pages='100-10'  pages_match=False  score=0.9938 -> review_wrong_paper
```

`_digits("S39-S40") == _digits("39-40")` → `pages_match=True` for a supplement abstract vs the
co-numbered full article. `work_identity._first_pages_agree` (`:480-493`) **already** has a
supplement-parity guard and a first-page-only comparison; `biblio_match.field_agreement` does not.
Two page comparators with different semantics feed one decision.

## B3 — `mixed_identity_citation` returns `same_work=True`

`work_identity._mixed_identity_citation` (`:496-515`) is documented as *"a citation assembled from two
different works"* — the textbook F2 — and returns `same_work=True` at `:712`, which
`biblio_match.py:558` converts to `VERDICT_SAME_WORK_VARIANT`. **Verified**: removed from **both**
numerator and denominator. It still reaches a human (`decide.py:40-46` → `HUMAN_REVIEW`), so it is not
silently cleared — but it is silently **uncounted**, and the field name asserts the opposite of the
rule's own docstring.

## B4 — `resolved` is a tri-state on one path and a boolean on the other

Declared `bool = False` (`schema.py:258`), but `eval_report.py:328, 334, 367-369, 423` implement a
tri-state (`is False` only; `None` proceeds to scoring) while the live path uses falsy checks
(`lookup.py:477, 420`; `unscoreable.py:178`). **Verified:**

```
resolved=None  live: flagged=True 'claimed PMID did not resolve' | offline: verdict=match
```

Opposite readings of one value. Note the F1 spec's transport fix touches this same field — **the two
specs collide here, and F1's fix is the one that must not be blocked.** Flag it to ZD rather than
resolving it inside either spec.

---

## Acceptance matrix — PART A ONLY

| Input / fixture | Field | Expected |
|---|---|---|
| seed 47 HIGH frame (82 rows), before vs after | every verdict | **byte-identical** |
| seed 47 HIGH frame | `flagged_f2_high` | **82**, unchanged |
| `work_identity` | threshold | imported from `biblio_match`, value `0.92` |
| `biblio_match.py:430-436` | reads | explicit `is True`; scores unchanged |
| sparse-author row | score | unchanged, with the bias documented in a comment |
| `flag_verdict` docstring | text | describes `classify_unscoreable`, the gate that runs |
| unscoreable population | count | **unchanged** — `is_scoreable_title` stays unwired |
| any run | `seam_status` | F1, F2, F8 entries present |
| NCBI outage fixture | counters | transport failures counted separately from dead PMIDs |
| `summarize` raising | run | logs loudly; no silent `[eval-report-skip]` on a successful run |
| `eval_report.py` header | note | states which path owns which reported figure |

## Guardrails — do NOT change

- **The seed 47 figure of record is `74/80 = 0.9250` [0.8459, 0.9652] (DEC-058).** No Part A item may
  move a single verdict on that frame. Prove it, do not assume it.
- **`SAME_WORK_TITLE_SIM_MIN` stays `0.92`.** `eval_report.py:469` hard-aborts otherwise. Import it;
  never re-declare or re-derive it.
- **`RESERVE_SEEDS` is exhausted.** There is no redraw. Treat every verdict on the adjudicated frame
  as irreplaceable.
- **F2 is precision-only in evaluation, but recall is non-negotiable in the matcher.** No gate may be
  tightened in a way that silently drops the F2 population.
- **Never use the detector's own flags as gold.**
- **Claude never assigns semantic labels** and never curates ground truth.
- **No-rewrite discipline:** targeted amendments only.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.
Suites that must stay green: `test_f2_v3_1`, `test_f2_v3_5`, `test_f2_revision`,
`test_f2_recall_guard`, `test_biblio_match`, `test_unscoreable`, `test_work_identity`,
`test_eval_report` — **215 passed** at audit time.

**The load-bearing guard is the seed 47 HIGH frame itself.** Re-band it before and after and diff. Any
movement means a Part B item leaked into Part A.

## Definition of done

- A1–A5 landed.
- **Seed 47 HIGH frame re-banded and diffed: zero verdict changes, stated explicitly in your report.**
- Part B untouched, and your report says so.
- Suite green; count old → new, stating the environment (`anthropic` and `jsonschema` change the
  number — see `F3F7_PACKET_AND_GATE_SPEC.md`).

## Out of scope

- **Everything in Part B.**
- Changing any threshold **value**.
- Re-running or redrawing any seed.
- Unifying the live and offline predicates.
- Wiring `is_scoreable_title`.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```


---

---

# Audit loop — F2, rounds 2–6 (2026-08-18) · **not converged, stopped by decision**

6 rounds. **5 landed**, all unanimous at ≥95%. Every finding below was reproduced on **both trees** —
`cre-f3f7` and the `feat/f2-matcher-revision` branch of record — and each entry says so.

## ⚠ READ FIRST — none of this changes F2 banding

**Seed 47 is adjudicated. `RESERVE_SEEDS = (31,37,41,43,47)` is EXHAUSTED. 74/80 = 0.9250 has no
replacement.** Three of the five findings below move verdicts on the frame that produced that figure.
**Do not fix them and re-report the rate.** Fix them, then run the zero-verdict-movement gate
(`F2_MERGE_FOURFILE_CARRY_SPEC.md`) and report what moved. If anything moves, stop and report.

---

## L-1 · `mixed_identity_citation` — the rule that concludes "two different works" — returns `same_work=True`

**Cite:** `work_identity.py:496-515` (the rule) · `:712-714` (evidence constructed) · `decide.py:53-58`
(the published sentence) · `eval_report.py:504-511` (the frame exit)
**Both trees:** F2-branch `work_identity.py:1310-1314`, `decide.py:65-71` — identical source.
**Verdict:** LAND · 96/96/96 · REPRODUCED end to end on the live path and the offline path.

`_mixed_identity_citation` fires only on a five-part conjunction: exact DOI, venue, volume and first page
agree, **and** the cited-work identity conflicts — title similarity strictly **below 0.85**, a year gap
≥2, and ≥2 claimed surnames absent from the resolved roster. Its docstring at `:498` states the
conclusion in one sentence: *"a citation assembled from two different works."* That is textbook F2.

**The conclusion is written into a field named `same_work`, set `True`** (`:712`). `flag_verdict`
(`biblio_match.py:558-561`) reads only the boolean, so it cannot tell this rule from
`authoritative_title_alias`. The human adjudicator is then handed
*"Resolved identifier appears to represent the same work or a work variant"* (`decide.py:53-58`), and
offline the row is dropped from **both** the numerator and the denominator
(`eval_report.py:504-511`) as `same_work_variant_excluded` — whose own docstring justifies the exclusion
by *"an (near-)identical title means the identifier resolves to the same work"*. **The rule's own
precondition caps title similarity below 0.85.**

Measured on one row (exact shared DOI, same journal/volume/first page, 2016 vs 2018, four claimed
surnames none of which are in the three-name resolved roster):

```
title_sim = 0.7019
assess_same_work -> same_work=True reason='mixed_identity_citation'
VERDICT = review_same_work_variant
fields: author_match=False first_author_match=False year_match=False doi_match=True
metric: {'flagged_f2_high': 0, 'denominator_scoreable': 0, 'same_work_variant_excluded': 1}
```

**Fix direction:** this rule's evidence is not same-work evidence. Give it its own disposition, and at
minimum stop `decide.py` publishing a sentence the rule contradicts. **Deciding whether the row belongs
in the F2 frame is a verdict-movement question — route it, do not take it.**

---

## L-2 · `_derivative_block` fires on a genre phrase in the resolved record's **own** subtitle, and pre-empts PubMed's own alternate-title proof

**Cite:** `work_identity.py:371-374` (the block) · `:30-36` (`_DERIVATIVE_RE`) · `:693-695` (the
unconditional early return) · `:729-732` (the authority it pre-empts) · `biblio_match.py:566`
(`not identity.blocked_by`), `:600-601` (the HIGH-band fall-through) · `lookup.py:225` (MEDLINE `TT`)
**Both trees:** F2-branch `work_identity.py:821-824`, `:1244-1257`, `:1331`.
**Verdict:** LAND (r5, 96/96/96) · ASK-ZD on a narrower re-file (r4) · REPRODUCED against **live PubMed
records**, not invented strings.

`_DERIVATIVE_RE` needs only a genre noun preceded by start-of-string or `[.:;] ` and followed by
punctuation or `on|to|of|for`. The ordinary biomedical subtitle `"<Topic>: A Systematic Review of …"`
satisfies it. The block is raised from **the resolved record's own title**, and the only other condition
is `ct != rt` — true for essentially every real citation pair. So the code raises a derivative block
having established only that **the resolved paper is a review**.

The return at `:693-695` is unconditional and sits **above** `authoritative_title_alias`,
`canonical_title_exact`, `malformed_title_wrapper` and all four translation rules. It also clears
`biblio_match.py`'s 0.92 near-identical-title quarantine, so the row falls to
`if disagree: return VERDICT_WRONG_PAPER` — the HIGH band, counted into `flagged_f2_high`.

Matched case/control, four words changed inside the resolved record's own subtitle and nothing else:

```
E'  resolved: [Perioperative management of meningioma: a systematic review of the available evidence]
    blocked_by='derivative_publication' -> review_wrong_paper      flagged_f2_high: 1
F'  resolved: [Perioperative management of meningioma: an appraisal of the available evidence]
    blocked_by=''  reason='authoritative_title_alias' -> review_same_work_variant
```

In F′ MEDLINE's `TT` field proves the claimed string is an alternate title of that very record. In E′
that proof is never consulted. Live-measured population: 51,121 PubMed records with "systematic review
of" in the title, 65,748 with "meta-analysis of", 54,648 with a further genre form.

**Fix direction:** the block must establish a *relationship* between the pair, not a property of one
record. At minimum, move the block **below** `authoritative_title_alias` so an authority-attested
alternate title cannot be pre-empted by a genre keyword.

---

## L-3 · `_roster_containment` deletes every romanized surname shorter than four characters

**Cite:** `work_identity.py:612-619` (`_surname_set`) · `:622-630` (`_roster_containment`) · consumed at
`:889` (RULE B) and `:838-840` (RULE F low tier)
**Both trees:** F2-branch `work_identity.py:1079-1086`, `:1089-1097`, `:1489`, `:1437-1440` — byte-identical.
**Verdict:** LAND · 96/96/96 · REPRODUCED, including on **live PubMed rosters**.

`:617` keeps a token only when `len(t) >= 4`. The docstring one line above says the function *"drops
given-name initials"* — initials are one character. The four-character floor additionally deletes Li, Wu,
Xu, Ma, Hu, Lu, Yu, Sun, Guo, Han, Gao, Kim, Lee, Cho, Tan, Zhu, Liu, Ito, Abe, Roy, Das, Rao and the
rest of that class. (The second conjunct `not re.fullmatch(r"[a-z]{1,3}", t)` can never be False once
`len(t) >= 4` has passed — **it is itself a check that cannot fail**, and it is the fossil of the intent
the docstring states.)

Two failures, opposite directions:

```
identical CJK-romanized roster   -> _roster_containment = 0.00
identical Latin roster           -> _roster_containment = 1.00
   (RULE B floor 0.75, RULE F floor 0.60 — both unreachable for the first)

PMID 38340178 first 5 AU ['Yu Z','Wu X','Zhu J','Yan H','Li Y']
   _surname_set = set()   containment(roster, THE SAME roster) = 0.00
PMID 37797632 first 5 AU ['Gao X','Xu N','Li Z','Shen L','Ji K']
   _surname_set = {'shen'} containment = 1.00   <- measured over ONE of five authors
```

Band flip on two rows identical but for the surnames:

```
Latin surnames : roster_containment=1.00 -> same_work='conference_abstract_publication' -> review_same_work_variant
CJK surnames   : roster_containment=0.00 -> same_work=False                             -> review_wrong_paper
```

The over-permissive half defeats the guard's stated purpose. The comment at `:874-877` says containment
(≥0.75, "not mere overlap") exists to stop sibling trials that share only serial co-authors —
DAPA-HF vs DELIVER, the rivaroxaban family. With one long surname surviving, two different trials
sharing one serial co-author score **1.00**, and `identity_signals` publishes the token
`"roster_containment"` to the adjudicator as corroboration measured over one name.

**And `0.0` is unreadable** — a roster that could not be compared and a roster with genuinely zero
overlap return the same float.

**The F2 branch already knows.** `work_identity.py:1100-1111` on that branch describes this in its own
words, names a concrete case (PMC12733676:B29 — Mao/Li/Xie/Lau), calls the blindness *"systematic for
CJK-romanized names"*, and states why it was not fixed: **RULE B's thresholds were calibrated against
`_surname_set`'s output.** That is a real cost, not a dismissal — but it means the defect was left live
in a same-work proof rule and its size was never measured.

**Fix direction:** two separable steps. (a) Make an unmeasurable roster distinguishable from a
zero-overlap roster — that alone is diagnostic and moves no verdict. (b) Fix the filter to drop
initials rather than short surnames. **(b) moves verdicts and recalibrates RULE B — route it.**

---

## L-4 · `n_ambiguous_dropped` is structurally incapable of being non-zero, and three tests assert `== 0`

**Cite:** `f2_run_v3.py:307` · **Verdict:** LAND · 96/96/96 · REPRODUCED

Published in every reband `summary.json`. A run that dropped nothing and a run whose drop path never
executed are the same output. The three tests that assert `== 0` cannot fail.

## L-5 · `same_work_newly_quarantined` over-reports rows that never moved

**Cite:** `f2_run_v3.py:382-388` · **Verdict:** LAND · 96/97/96 · REPRODUCED

It counts proof-rule quarantines in `[0.92, 0.95)` as rows the 0.92 threshold move created. **The audit
list that exists to prove no row moved reports rows that did not move.**

---

## ASK-ZD

**`_series_conflict`'s year branch reads a wrapped citation's publication year as a serial-edition
ordinal** (`work_identity.py:355-358`; F2-branch `:805-808`, byte-identical — this one survives branch
divergence). It runs **before** `malformed_title_wrapper`, so a same-work row with an exact DOI is forced
into the HIGH band by the very token the wrapper rule treats as same-work evidence. Confidence that the
defect is real: ~97. Confidence it should be actioned inside this loop: **below 95** — the year branch is
load-bearing for the AHA *"Statistics-2017 Update"* / *"-2019 Update"* family, where romans and edition
ordinals are empty on both sides and the year branch is the only thing catching it. **Narrowing it wrong
converts a visible false accusation into an invisible false clear.** DEC-047A is not cover: its "Bounds
honoured" clause authorises exactly two fixes, and this is neither.

## Guardrails

- **No F2 banding change. No Part B item.** Seed 47 is adjudicated and the reserve is exhausted.
- `band_prompts.py` byte-identical — blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
- `schema.py` is GOVERNED; CONTRADICTIONS 65 is OPEN. Report the digest consequence, do not decide it.
- **Both trees.** Every fix must be applied on whichever tree ships; each finding above names its
  F2-branch anchors. Do not assume `cre-f3f7` line numbers hold there.
- Precision-first, both halves. No invented constants. Specs only — no corpus run.

## Definition of done

- `mixed_identity_citation` no longer publishes a same-work sentence, and its frame treatment is a
  decision on the record rather than a side effect of a boolean.
- An authority-attested alternate title cannot be pre-empted by a genre keyword.
- An unmeasurable roster is distinguishable from a zero-overlap roster in the published record.
- `n_ambiguous_dropped` can be non-zero, or it goes.
- **The zero-verdict-movement gate run after all of the above, result stated as a row count.**
- Suite: old → new counts, environment stated.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
