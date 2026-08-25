# F1 — fabrication guard: transport failure must never become an accusation — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Severity: CRITICAL.** This is the only defect in the taxonomy that produces a **false public
accusation** — that a real, indexed paper does not exist.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

---

> ### AMENDMENT, 2026-08-25 — the route this spec hardens no longer produces `F1`
>
> **This document is a historical record of the 2026-08-16 work and has not been rewritten.**
> Every acceptance row, table, and worked example below that shows
> `label=F1 … decided_by=confirm_not_found_f1` now reads
> `label=human_review … decided_by=confirm_not_found_human_review`.
>
> **What changed.** That route — claimed PMID answered-and-absent (or resolving to
> another paper), survives the LLM filter, claimed title found in none of the three
> databases that all answered — was disconnected from `F1` **and** from `F2`:
>
> * not `F1`, because the sweep is three **title** searches over databases that do
>   not span the literature, run with the claimed title, which is the very field a
>   misprinted reference gets wrong. "We could not match it" is a statement about our
>   matching, not about the world, and `F1` asserts that no such work exists.
> * not `F2` either, because unlike every other `F2` route (`confirm_found_f2`,
>   `exact_doi_metadata_mismatch_f2`) this one identifies **no work at all** — there
>   is nothing to call the printed metadata wrong *about*, and nothing to point a
>   repair at.
>
> The row is now **held for human adjudication**, the same call the no-PMID branch
> already made on the same evidence (`noid_confirm_not_found_human_review`).
>
> **`F1` remains reachable**, on the exact-DOI route
> (`exact_doi_absent_confirm_not_found_f1`), where the DOI system itself reports
> ANSWERED-ABSENT on a registered identifier — an authority that can actually report
> a non-existence. The other `F2` routes are untouched.
>
> **Nothing in this spec is retracted.** Every guard it specifies still stands and is
> still tested; they now govern which rows reach a *hold* rather than which reach an
> *accusation*. The one guard whose wording it changes is the acceptance matrix's
> "the true positive must survive" row, which now asserts that complete, healthy,
> empty evidence reaches the terminal branch on its merits rather than being
> short-circuited by a guard.
>
> Code of record: `cre/f1/decide.py` (terminal branch), `tools/F1_CALIBRATION_PROBE.py`
> (probe A, amended the same day).

---

## Objective

**Current behavior.** A partial NCBI outage causes a real, PubMed-indexed paper to be labelled `F1`
at `HIGH` confidence.

**Target behavior.** F1 is reachable only from evidence that was actually gathered. Every retrieval
failure is recorded as a failure and holds the reference; it never becomes evidence of non-existence.

---

## Defect 1 — every fetch failure collapses into "did not resolve"

`lookup.fetch_pubmed` (`lookup.py:70-86`) returns the same `RetrievedRecord(resolved=False)` for all
of: non-200, a 429 that survived every retry (`ratelimit.py:90-97` returns the final 429 response),
a connection error (`:85-86`), and a 200 with an empty body (`:82`).

`compare_and_flag` then writes `log.notes = "claimed PMID did not resolve"` (`lookup.py:479`), and
**`StageLog` has no transport-status field** (`schema.py:287-324`). A dead PMID and a failed fetch are
byte-identical in the durable record.

**Verified by execution.** Keyless run, NCBI 429 throughout, paper real and indexed, Crossref and
OpenAlex healthy but not indexing it:

```
label='F1' confidence='HIGH' decided_by='confirm_not_found_f1'
db_hits={'pubmed': None, 'crossref': 0.0, 'openalex': 0.0}
rationale=Claimed title not found in PubMed, Crossref, or OpenAlex; claimed PMID did not resolve.
```

**`confidence` is HIGH *because* `pmid_resolved` is False** (`decide.py:149`). The transport failure
raises the stated confidence.

**Required:** `fetch_pubmed` must distinguish *answered-and-absent* from *did-not-answer*. Add a
transport-status field to `RetrievedRecord` and/or `StageLog` and propagate it. `decide` must hold —
never accuse — when the PMID fetch did not answer. **`resolved=False` must stop meaning two things.**

This is the same distinction `fulltext_reader` already draws between `no_pmcid` and `resolver_error`
(`fulltext_reader.py:64-75`), and the docstring there records that conflating them corrupted a number
once already. **Reuse that vocabulary.**

## Defect 2 — the confirmation guard is too weak

`confirm.all_errored` (`confirm.py:127-129`) holds only when **all three** searches returned `None`.
One search that returned successfully-but-empty licenses the accusation, and this is codified by
`test_live_paths.py:471-479` (`test_decide_partial_error_still_decides_f1`).

**Required:** decide whether one healthy empty search is sufficient evidence of non-existence when
another search **errored**. It is ZD's call, not yours. Implement whichever he picks, and **update or
delete that test rather than working around it** — a test asserting the current behavior is part of
the defect.

## Defect 3 — F1 is reachable with zero searches issued

`confirm.search_pubmed` / `search_crossref` / `search_openalex` return `0.0` for an empty title
**without issuing a request** (`confirm.py:48-49, 74-75, 92-93`). The unscoreable gate on the PMID
path sits *after* the unresolved-PMID early return (`lookup.py:486` after `:477-480`), so a reference
with **no claimed title and a dead PMID** reaches F1 with `db_hits={0.0, 0.0, 0.0}`.

**Verified:** `label='F1' confidence='HIGH'`, `http calls = ['efetch']` — zero searches were issued,
and three fabricated zeros were presented as evidence. Control (same reference, PMID resolves) →
`unscoreable`, correctly.

**Required:** a score of `0.0` must mean "searched, found nothing". A skipped search must be `None`,
not `0.0`. And the unscoreable classification must be reachable on the PMID path regardless of
whether the PMID resolved.

## Defect 4 — HTTP-200 error payloads become "found nothing"

`_json_or_none` (`confirm.py:36-44`) maps only non-200 and unparseable JSON to `None`. **Verified**:

```
pubmed, Entrez 200 + ERROR body       -> 0.0
pubmed, 200 + unexpected JSON shape   -> 0.0
crossref, 200 + {'status':'error'}    -> 0.0
openalex, 200 + {'error': ...}        -> 0.0
```

### ⚠️ Defect 4b — the bracketed-title hypothesis was WRONG, and the real defect is larger

**This section replaces the original remedy. Do not implement the original.** The first draft of this
spec guessed that a leading `[` in a translated title made the ESearch query malformed, and proposed
quoting the term. Both halves were wrong, established against live NCBI 2026-08-16 by Claude Code and
independently re-confirmed here.

- **Entrez tolerates the leading `[`** and returns the right paper. There was no bracket defect.
- **Quoting would be catastrophic.** Full titles are not in PubMed's phrase index. Confirmed:
  `"The heat of activation and the heat of shortening in a muscle twitch"[Title]` →
  `count: 0`, with `warninglist.phrasesnotfound` naming the whole phrase. Quoting every title would
  zero out nearly every search corpus-wide and turn the entire corpus into apparent fabrications.

**The actual defect.** `f"{title}[Title]"` (`confirm.py:52`) **is not a title search.** Automatic Term
Mapping binds `[Title]` to the trailing fragment only, and maps the rest as free text. Confirmed on
`18152150`'s own exact title — `count: 0` — with this `querytranslation`:

```
(("hot temperature"[MeSH Terms] OR ... OR "heat"[All Fields])
 AND ("activable"[All Fields] OR ... OR "activity"[All Fields])
 AND (... "heat"[All Fields])
 AND ("shorten"[All Fields] OR ... OR "shortens"[All Fields])
 AND in a[Author])
AND "muscle twitch"[Title]
```

Two things there, and the second is worse than the reported one:

1. `[Title]` applies to **`"muscle twitch"` alone** — the last two words.
2. **`in a[Author]`** — ATM parsed the words *"in a"* as an **author surname search**. The middle of
   the title silently became an author query, ANDed against MeSH-exploded All Fields terms.

**Three of the seven regression PMIDs in this spec return 0 hits on their own exact titles**:
`16639420`, `18152150`, `27665045`.

**Classification.** This is a **recall** defect, not an honesty defect — but it is the largest
remaining route to a false F1, because a zero-hit search on a real paper is exactly the input Defect 1
turns into an accusation. The all-three-must-answer rule (Defect 2) partly masks it: all three
databases now have to answer, so a PubMed miss no longer suffices alone. **It does not fix it.**

**Required, and it needs measuring before it is chosen:** neither the tag-suffix form nor the quoted
form works. Candidate routes, in the order worth testing —

- `field=title` as a **request parameter** rather than an inline tag, so the field applies to the whole
  term. *(Untested — a rate limit blocked the confirming call. Test it first.)*
- `ecitmatch.cgi`, NCBI's purpose-built **citation matcher**, which takes structured
  journal/year/volume/page/author and is designed for exactly this question.
- Dropping to Crossref/OpenAlex bibliographic query as the primary existence check, with PubMed as
  corroboration rather than the lead.

**Do not pick one from reasoning.** Measure hit rate against a set of references known to exist — the
seven regression PMIDs are the obvious starting sample — and report the numbers. **Report the measured
comparison to ZD before landing a route**; changing how existence is established changes what F1
means.

## Defect 5 — the mirror-image error produces a false F2

**Verified:** EFetch transport-dead while the confirmation search *finds* the title yields **`F2`**
with the rationale *"claimed PMID resolves to a different paper"*. Nothing resolved. The same
transport fix closes this; the rationale string must not assert a resolution that did not happen.

## Defect 6 — an uncaught exception aborts the whole batch

`run.py:131-143` has no try/except around `process_reference`. **Verified:** a Crossref 200 whose
`message` is a string raises `AttributeError` from `confirm.py:83`; the except clause at
`confirm.py:87` catches `RequestException, ValueError, KeyError` but not `AttributeError`.

**Required:** a per-reference guard that quarantines the row rather than killing the run, matching the
pattern already used at `judgment_run.py:1501-1507`.

## Defect 7 — no F1 instrumentation exists

- `run.run()` returns `counts[ref.label]` (`run.py:138`) — keys exist only for observed labels, so a
  zero F1 is the **absence of the key**.
- `eval_report.summarize`'s counts block (`eval_report.py:146-157`) has `f2_count` and **no
  `f1_count`**.
- `manifest["seam_status"]` (`judgment_run.py:1746-1770`) — the block written specifically so a zero
  cannot be read as a rate — **covers F3–F7 only. F1, F2 and F8 have no entry.**

**Required:** an F1 entry in `seam_status` (or the pre-band equivalent) carrying *attempted*,
*answered*, *transport-failed* and *fired*, so "zero fabrications" is distinguishable from "the check
could not run".

## Defect 8 — no F1-producing module is byte-governed

`production_launcher.GOVERNING_MODULES` (`production_launcher.py:66-72`) hashes 13 modules.
`decide.py`, `lookup.py`, `confirm.py`, `run.py`, `llm_filter.py`, `unscoreable.py` and
`biblio_match.py` are **all absent**. **Report this to ZD with your fix; do not extend the list
yourself** — what is governed is a freeze decision.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| EFetch 429 exhausted, title found in Crossref | label | **not F1** — held, transport failure named |
| EFetch connection error, one search errored, one empty | label | **not F1** — held |
| EFetch 200 empty body (genuine dead PMID), all three searches healthy and empty | label | F1 — the true positive must survive |
| no claimed title + dead PMID | label | unscoreable — **not** F1 |
| no claimed title | `db_hits` per skipped search | `None`, never `0.0` |
| Entrez 200 with an `ERROR` body | search score | `None` |
| Crossref 200 `{"status":"error"}` | search score | `None` |
| bracketed translated title | ESearch term | quoted/escaped; report the live-NCBI finding |
| EFetch dead, title found by search | label + rationale | held; no "resolves to a different paper" |
| Crossref 200 with a string `message` | run | that reference quarantines; the batch completes |
| run with zero F1 | manifest | F1 attempted/answered/transport-failed/fired all present |
| run where the F1 check could not run | manifest | distinguishable from zero |

## Guardrails — do NOT change

- **Precision-first.** Ambiguity escalates to human review; it never becomes an accusation. This spec
  is that guardrail applied literally.
- **Never use the detector's own flags as gold.** Fixing F1 does not license relabelling any existing
  row.
- **Claude never assigns semantic labels** and never curates ground truth.
- **F2 recall is non-negotiable in the matcher** — do not tighten a gate in a way that drops the F2
  population while fixing F1. The two share `compare_and_flag`.
- **`SAME_WORK_TITLE_SIM_MIN = 0.92`** at `biblio_match.py:120` — untouched by this spec.
- **No-rewrite discipline:** targeted amendments only.
- **No `Co-Authored-By` trailers.**

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` band as before.
Plus: the genuine-dead-PMID fixture above is the guard that this spec does not simply disable F1.

## Definition of done

- Transport failure distinguishable from absence, in the record and in the manifest.
- No path to F1 that did not gather the evidence it cites.
- Per-reference exception guard in `run.py`.
- F1 instrumentation present.
- Live-NCBI finding on the bracketed-title query reported to ZD.
- Suite green; count old → new, stating the environment (see the note in
  `F3F7_PACKET_AND_GATE_SPEC.md` — `anthropic` and `jsonschema` change the number).

## Out of scope

- Changing what F1 *means*.
- Re-running any corpus.
- Extending `GOVERNING_MODULES` — report, do not decide.
- The `all_errored` policy question — ZD decides; implement his answer.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```

---

# Audit loop — F1 stratum, round 1 (2026-08-17)

**Method.** Three read-only finders over disjoint F1 surfaces (transport+decision, search+orchestration,
instrumentation+artifacts), then three independent checkers — Reality, Blast radius, Cost. Every
citation below was opened by the finder **and re-opened by all three checkers**; every checker
recorded `citation_verified: true` on all twelve candidates, so no auditor is carrying a bad-citation
signal this round. **12 candidates → 4 LAND · 4 DEFER · 2 ASK-ZD · 2 REJECT.** Unanimous LAND was
required; the two REJECTs are duplicates and are logged in `AUDIT_LOOP_REJECTIONS.md`.

**Baseline the findings were measured against:** `test_f1_fabrication_guard.py` + `test_schema.py` +
`test_eval_report.py` + `test_recording_adapter.py` + `test_production_launcher.py` +
`test_preband_contract.py` → **179 passed**, in a Linux container with `anthropic`, `jsonschema` and
`rapidfuzz` installed. **Every finding below is a gap the green suite does not cover.**

**The shape of the round, stated plainly.** Defects 1–7 landed on 2026-08-16 and the fixes hold on the
paths they were written for. What round 1 found is a second generation of the same defect class:
**the transport fix taught the pipeline a vocabulary, and three separate seams either mint the wrong
word, discard the word, or read a missing word as a good one.** Findings 1, 3 and 6 are all
"answered-but-unusable is recorded as answered-and-absent" at three different layers.

---

## Landed findings

### L-1 · A PubMed fetch that answers HTTP 200 with a non-MEDLINE body is recorded as `FETCH_ANSWERED_ABSENT`

**Cite:** `cre/f1/lookup.py:103-110`. **Status: REPRODUCED.** **Severity: CRITICAL — this is a false
public accusation.**

```python
    if not r.text.strip():
        # Answered, and there is no such record. The one case that is evidence.
        return RetrievedRecord(resolved=False, pmid=pmid,
                               transport_status=FETCH_ANSWERED_ABSENT)
    rec = _parse_medline(r.text, pmid)
    rec.transport_status = (FETCH_ANSWERED_RECORD if rec.resolved
                            else FETCH_ANSWERED_ABSENT)
    return rec
```

**Mechanism.** `lookup.py:100-102` routes only non-200 and no-response to `FETCH_RESOLVER_ERROR`; a
fault served **under** 200 passes that guard. `lookup.py:107` hands the body to `_parse_medline`,
which finds no `^[A-Z]{2,4}-` tags (`lookup.py:181`), so `lookup.py:204-205` returns
`resolved=False`. `lookup.py:108-109` then **forces** `FETCH_ANSWERED_ABSENT`, because the ternary's
only input is `rec.resolved` — **there is no third arm for "answered with something we could not
read."** The docstring at `lookup.py:76-85` enumerates the resolver-error cases as non-200,
connection error and missing response and never contemplates a parse failure; code and docstring
disagree and the code is what runs. Downstream: `lookup.py:504` copies it into the durable log,
`lookup.py:523` gets `fetch_answered → True` and writes "claimed PMID did not resolve"
(`lookup.py:531`), `run.py:114` does not short-circuit, `decide.py:74` does not hold, and
`decide.py:211-221` assigns F1 at `HIGH`.

**The codebase itself establishes the shape is real.** `confirm.py:45-49`: *"Entrez, Crossref and
OpenAlex all serve faults with HTTP 200 and say so only in the body."* `ncbi_meta.py:19-24` records a
measured eutils outage that answered 200 with a non-JSON `ERROR` body. **The sibling seams draw the
distinction correctly and this one does not** — `confirm.py:69-72` returns `None` for an unparseable
200; `ncbi_meta.py:186-189` raises `ResolverError`. `lookup.py` is the only one of the three whose
output is an accusation.

**Reproduction** (stubbed session, no network):

```
[entrez fault under 200] resolved=False transport_status='answered_absent' fetch_answered=True
[gateway HTML under 200] resolved=False transport_status='answered_absent' fetch_answered=True

label            : F1
confidence       : HIGH
decided_by       : confirm_not_found_f1
log.notes        : claimed PMID did not resolve | invented
rationale        : Claimed title not found in PubMed, Crossref, or OpenAlex; claimed PMID did not resolve.
evidence.pmid_transport_status: answered_absent
```

The **F2 mirror survives too** — same fault body, Crossref finds the title:

```
label     : F2 MED confirm_found_f2
rationale : Claimed work found in a database but the claimed PMID has no PubMed record: wrong reference.
IDENTICAL rationale to the genuinely-dead control?  True
```

**Reachability.** Production hot path, single fault. **Blast radius.** Narrow: the genuine-dead shape
(200 + **empty** body, verified live 2026-08-16) is caught at `lookup.py:103` *before*
`_parse_melline` runs, so a third arm added at `:108-109` cannot touch the true positive.
**Cost.** `lookup.py` is **not** in `GOVERNING_MODULES` (read at `production_launcher.py:65-70`), not
pinned, off the freeze chain, not imported by `f2_run_v3` — no published figure and no seed moves.

**Expected behaviour.** A 200 whose body cannot be parsed as MEDLINE is **not evidence of absence.**
It needs a status distinct from both `answered_absent` and `resolver_error`, and `decide()` must hold
on it. The `confidence = HIGH` comment at `decide.py:212-216` states the precondition this path
fakes: *"HIGH is only sound because the guard above has already established that the fetch ANSWERED."*
**Do not extend `FETCH_NO_EVIDENCE` without reading D-4 below — `schema.py` is governed and
CONTRADICTIONS 65 is open.**

---

### L-6 · A provider that ANSWERED but whose records could not be read scores `0.0` — "searched, found nothing"

**Cite:** `cre/f1/confirm.py:114-115`. **Status: REPRODUCED.** **Severity: CRITICAL.** *(Reality
checker: "the strongest finding in the round.")*

```python
        return max((_score(title, (res[i] or {}).get("title", ""))
                    for i in ids if isinstance(res.get(i), dict)), default=0.0)
```

**Mechanism.** The module's central rule (`confirm.py:10-15`) is that `0.0` means **searched,
answered, found nothing** and `None` means **no answer**. Defect 4's fix hardened the **envelope**
only — `_json_or_none` (`:58-80`) and the `esearchresult` guard (`:97-102`) correctly return `None` —
and stopped there. The **scoring hop below them has no such guard.** ESearch returns a **non-empty
idlist** (positive proof PubMed's index contains a title match, `:100-104`); ESummary answers 200 with
a well-formed envelope so `_json_or_none` passes it (`:105-113`); every requested uid carries an
Entrez per-uid `{"error": "cannot get document summary"}` stanza, so `.get("title","")` is `""`,
`_score` short-circuits to `0.0` (`:40-41`), and `max()` returns `0.0`. `confirm.py:185` writes that
`0.0` into the durable log. `decide.py:144` `all_errored=False`; `decide.py:199` `fully_answered=True`
— **the all-three-must-answer bar is satisfied by a manufactured answer.** `decide.py:211-221` then
assigns F1 at HIGH.

**Same hole at the other two providers:** `confirm.py:143` returns `0.0` for a Crossref item with no
`title` key (a real Crossref shape); `confirm.py:165-166` returns `0.0` for an OpenAlex result whose
`title` **and** `display_name` are both null.

**Reproduction.** Defect 4's own six payload shapes now all return `None` — **Defect 4 is genuinely
closed.** The new shapes are one layer down:

```
=== B. Entrez ESummary PER-UID error (documented Entrez shape) ===
ESearch idlist non-empty + ESummary per-uid errors -> pubmed score = 0.0
=== C. ESummary answers but omits the requested uids entirely ===  -> 0.0
=== D. ESummary record present but title key missing/None ===      -> 0.0
crossref items=[non-dict] -> 0.0 · crossref item title=[] -> 0.0
openalex results=[title None] -> 0.0 · openalex results=[non-dict] -> 0.0

hits = {'pubmed': 0.0, 'crossref': 0.0, 'openalex': 0.0}
all_errored = False · fully_answered = True · found_anywhere = False
```

End-to-end, with the **only** difference being whether ESummary could build the record:

```
label F1  HIGH  confirm_not_found_f1   db_hits {'pubmed': 0.0, ...}
--- CONTROL: same run, ESummary healthy ---
label F2        confirm_found_f2       db_hits {'pubmed': 100.0, ...}
```

**Reachability.** Production. **Blast radius.** Survivable **only because the fix can be scoped
precisely** to the record-extraction hop; widening it risks turning genuine zero-hit searches into
`None` and suppressing true positives — the mirror defect, and R-003's reason for existing.
**Cost.** `confirm.py` ungoverned, unpinned, off the freeze chain, not imported by `f2_run_v3`, so
the seed-47 **0.9250** figure cannot move. **Not a re-litigation of CONTRADICTIONS 64** — that is
about how the *query* is built; this is about how a *returned record* is read.

**Expected behaviour.** A provider that answered but produced no usable record must score `None`, not
`0.0`. A non-empty ESearch idlist that ESummary then fails to resolve is **evidence the paper exists**,
and it is currently discarded.

---

### L-7 · The quarantine swallows the non-retryable auth error `make_completer` promises to fail fast on

**Cite:** `cre/f1/run.py:151-166`. **Status: REPRODUCED.** *(Cost checker: the one finding in the
round with **negative** cost — landing it saves money.)*

```python
        except Exception as e:                # noqa: BLE001 - quarantine, never abort
            quarantined += 1
            ref.label, ref.confidence = HUMAN_REVIEW, "LOW"
```
versus the contract it overrides, `run.py:62-64`:
```
    - Re-raises non-retryable errors (auth / bad request) immediately so a
      misconfigured run fails fast instead of silently labelling everything
      uncertain.
```

**Mechanism.** `make_completer` classifies at `run.py:76-82` and re-raises a non-retryable 401/400/
unknown-model at `:82` **specifically so the run dies loudly**. That raise passes through
`llm_filter.py:77` and `run.py:119` into the Defect-7 quarantine at `run.py:151`, which catches bare
`Exception`. Because the error is per-call and permanent, **every** row takes the same path, the whole
batch is quarantined one row at a time, and the run reaches `:174-175`, writes both JSONL files,
prints the report at `:180` and returns counts at `:183`. **Two fixes composed into a regression:
Defect 7's guard silently repealed `make_completer`'s documented fail-fast contract.**

**Reproduction.** A 401 on every call: `run()` returns `{'F1': 0, 'human_review': 3}`, both JSONL
files written, report prints `F2 labelled: 0` and `base rate: 0.0`, `decided_by` =
`quarantine_exception`, no non-zero exit.

**Expected behaviour.** The quarantine must not catch the exception class `make_completer` raises to
abort. A misconfigured run must fail before it writes an artifact.

---

### L-3 · `f1_status` counts a MISSING transport status as "answered", and publishes a note asserting those fetches replied

**Cite:** `cre/f1/eval_report.py:127-132`. **Status: REPRODUCED.**

```python
        if lg.get("pmid_present"):
            f1_attempted += 1
            if fetch_answered(lg.get("pmid_transport_status") or ""):
                f1_answered += 1
            else:
                f1_transport_failed += 1
```

**Mechanism.** `lg.get(...) or ""` collapses a **missing key** and a **null** into `""`;
`fetch_answered("")` is `True` (`schema.py:77`, `status not in FETCH_NO_EVIDENCE`); the row is counted
as answered and `transport_failed` stays 0. `eval_report.py:188-205` then publishes the note
*"'answered' is those whose PubMed fetch actually replied"* — for a row with no recorded status the
code established only that **nobody wrote down whether it did**. `eval_report.py:279-282` gates the
loud warning on `if f1s["transport_failed"] or f1s["confirm_incomplete"]`, so the clean line prints
with no caveat. The header comment at `eval_report.py:182-187` says this block exists to prevent
exactly this.

**Scope, stated honestly** (the finder scoped it and all three checkers confirmed): a **current**
`run.py` always populates the field on PMID-bearing rows (`lookup.py:504`), so a fresh run is not
miscounted. **The live exposure is replay** — and replay is a documented production path:
`run.py:176-178` names `eval_report.summarize(log_records, gold=...)` as the offline re-scoring entry
point, and **every log written before 2026-08-16 has no such key.**

**Reproduction** — `summarize()` over one pre-transport-status log record:

```
{"attempted": 1, "answered": 1, "transport_failed": 0,
 "confirm_complete": 1, "confirm_incomplete": 0, "fired": 1,
 "note": "... 'answered' is those whose PubMed fetch actually replied; ..."}
```
`format_report` printed no "part of this corpus was NOT checked" line.

**Blast radius.** Cheapest safe change in the round. **Cost.** `eval_report.py` ungoverned, unpinned,
off the freeze chain.

**Expected behaviour.** A **three-state** read — `unrecorded` / `answered` / `failed`. The two-state
read cannot fail. **The fix must stay inside `eval_report`** and must not touch `fetch_answered`'s
decide-side permissiveness, which is a deliberate documented decision at `schema.py:73-76`.

**Fold in from D-9 and D-12** (deferred as twins, same code block — spec the edit once):

- The four routes that skip `confirm()` entirely — `run.py:102` (not flagged), `:108` (same-work
  variant), `:115` (PMID fetch did not answer), `:120-121` (LLM verdict formatting/uncertain). The new
  counter must separate **"nothing needed confirming"** (`:102`, `:108` — benign) from
  **"confirmation was prevented"** (`:120` — the dangerous one, the route an Anthropic outage drives
  when `make_completer` returns `""` after exhausted retries). `:115` is already covered by
  `transport_failed`.
- A `decided_by == "quarantine_exception"` tally in `f1_status`. Additive, read-only layer.
- **Test gap, verified:** `test_f1_fabrication_guard.py:613-633` is the acceptance-matrix row-12 test
  and it exercises only the non-empty-`db_hits` path.

---

### L-0 · Defect 4b resolved by measurement — the ESearch term takes `field=title` (ZD, 2026-08-17)

**This closes the route question left open by CONTRADICTIONS 64 and Amendment 01 §B.** Measured live
against NCBI E-utilities and the PubMed search API, 2026-08-17.

Current form re-confirmed broken, with the author-misparse visible in both cases:

| PMID | `term={title}[Title]` | damning fragment of `querytranslation` |
|---|---|---|
| 18152150 | **count 0** | `... AND in a[Author]) AND "muscle twitch"[Title]` |
| 16639420 | **count 0** | `... AND (a, at[Author] OR at a[Author]) ...) AND "boundary"[Title]` |

Title-bound term — **all seven regression PMIDs self-retrieve at count 1, up from 4/7, no control
regressed**: `18152150`, `16639420`, `27665045`, `31665581`, `25750229`, `32355637`, `22926653`.

Why it works, from the live `querytranslation` on 18152150 with the real `field=title` parameter:

```
"heat"[Title] AND "activation"[Title] AND "heat"[Title] AND "shortening"[Title]
  AND "muscle"[Title] AND "twitch"[Title]
warninglist.phrasesignored: ["The","of","and","in","a"]
```

Every content word binds to `[Title]`; stopwords are **dropped rather than re-parsed**. No phrase
index is consulted, which is why this succeeds where R-002 (quoting) fails: **title length stops
mattering.**

**Honesty caveat.** Verified end-to-end with the real `field=title` request parameter on **1 of 7**;
the other six used the equivalent expanded query — the exact translation `field=title` produced in the
one case where both were observed. The remaining calls were rate-limited (HTTP 429, then 403), which
per Amendment 01 §A.5 is a **failed test, not a negative result.**

**Conditions attached to this decision, all three binding:**

1. **Re-run the other six through the real `field=title` parameter** before the fix lands.
2. **Measure the F2 population before and after.** `confirm()` serves both labels; F2 recall is
   non-negotiable in the matcher. A green suite is not sufficient evidence.
3. **Do not touch `0.0` / `None` semantics.** A weak query must keep returning `0.0`. Making it return
   `None` would suppress true positives.

**Separately measurable, not part of this decision:** ESearch's default sort is **most-recent, not
relevance**, and the code reads only `retmax=3`, so a broad query can return the three newest hits
rather than the three best.

---

## Deferred

### D-2 · The published F2 v3 run record drops `transport_status` entirely

**Cite:** `cre/f1/eval_report.py:303-319`. **REPRODUCED.** Two `RetrievedRecord`s differing **only** in
`transport_status` produce **byte-identical** published records, both landing in
`resolved_unresolved_excluded=2`. The field survives cache reconstruction (`f2_run_v3.py:59,148`) and
is then discarded by `_raw_fields` (`eval_report.py:326-358`), which copies ~20 other attributes.

**Deferred by Cost, and the reason is worth recording.** The fix buys nothing on any artifact that
will actually be produced, while perturbing the schema of the artifact carrying the 0.9250 provenance.
**Retroactively:** `transport_status` was added 2026-08-16; the frozen resolution cache DEC-057A pins
byte-identically predates the field, so every rebanded historical row carries the empty-string default
and lands in the same undifferentiated bucket. **Prospectively:** `f2_run_v3` is the seed-sampling F2
harness, `RESERVE_SEEDS` is **exhausted**, seed 47 is adjudicated and DEC-057A's post-adjudication
clause has triggered — **there is no future F2 seed run for the new key to serve.**

**Re-raise when:** ZD authorises a new F2 measurement run, or a non-seed consumer of the F2 record
schema appears.

### D-4 · `fetch_answered()` is a one-element denylist — the `FETCH_*` vocabulary fails OPEN

**Cite:** `cre/f1/schema.py:66-77`. **REPRODUCED** — 96-row `decide()` truth table:

```
transport_status       resolved db_hits   label         conf  decided_by
''                     False    all_zero  F1            HIGH  confirm_not_found_f1
'not_attempted'        False    all_zero  F1            HIGH  confirm_not_found_f1
'answered_record'      False    all_zero  F1            HIGH  confirm_not_found_f1
'answered_absent'      False    all_zero  F1            HIGH  confirm_not_found_f1
'resolver_error'       False    all_zero  human_review  LOW   pmid_fetch_no_answer
'ANSWERED_ABSENT'      False    all_zero  F1            HIGH  confirm_not_found_f1
```

The predicate asks *"is this the one known-bad literal?"* rather than *"is this one of the known-good
literals?"* Every unrecognised value — a case variant, a typo, a future producer's status, a value off
a foreign cache — answers `True` and routes to the accuse-eligible branch. **No consumer anywhere
branches on `FETCH_NOT_ATTEMPTED` / `FETCH_ANSWERED_RECORD` / `FETCH_ANSWERED_ABSENT` individually**
(grep: the four consumers are `decide.py:74`, `lookup.py:523`, `run.py:114`, `eval_report.py:129`), so
the four-value vocabulary described in the 20-line comment at `schema.py:41-64` collapses to one bit
at every read site, defaulting to "safe to accuse".

**Deferred, and the reasoning is the useful part.** Today the **only** production route into the
fail-open is L-1 — all five `fetch_pubmed` return paths emit valid literals — and **L-1's fix closes
it at source.** Against zero live benefit sits a real cost: `fetch_answered` lives in `schema.py`,
which **is** in `GOVERNING_MODULES`, and **CONTRADICTIONS 65 is OPEN precisely because the last F1
pass moved that digest and ZD has not ruled on re-recording it.** Editing it again for a hypothetical
deepens an unresolved governance item.

**Re-raise when:** CONTRADICTIONS 65 is closed, **or** if L-1's fix introduces a new `FETCH_*`
constant — in which case flipping the denylist to an allowlist should ride **inside** that same edit,
not as a separate change.

### D-9 · `f1_status` has no state for "the confirmation search never ran"

**Cite:** `cre/f1/eval_report.py:133-138`. **REPRODUCED.** Deferred **as a twin of L-3, not as a
rejection** — same code block, same fix. Its substance is folded into L-3 above. F1 is only reachable
through the confirmation search (`decide.py:210-221` is the sole `ref.label = F1` site, below the
`db_hits is None` guard at `:134-138`), and on all four skip routes `ref.log.db_hits` stays at its
`field(default_factory=dict)` default (`schema.py:367`), so `hits` at `eval_report.py:133` is falsy and
**neither** counter increments.

### D-12 · `run()` computes a quarantine count and throws it away

**Cite:** `cre/f1/run.py:142`. **REPRODUCED.** `quarantined` is initialised at `:142`, incremented at
`:159`, and never read — `run()`'s only return at `:183` is `counts`, and `counts[ref.label]` at `:167`
folds every quarantined row into the ordinary `human_review` bucket. `eval_report.summarize` has no
counter keyed on `decided_by == "quarantine_exception"`. The sole surviving trace is a `print` to
stdout that no artifact captures.

**Second-order observation, verified and worth carrying:** `run.py:94-96` catches only
`requests.RequestException` (`lookup.py:97`), so a `UnicodeDecodeError` out of the transport
quarantines the row with `pmid_present` **still False**, making it invisible to `f1_attempted` too. A
systematic non-`RequestException` transport failure therefore publishes
`attempted 0, answered 0, transport-failed 0, fired 0` — every F1 instrument reading zero on a corpus
where nothing was checked.

**Deferred as a split.** The **safe half** — a `quarantine_exception` tally in `f1_status` — is
additive on the read-only reporting layer and belongs **inside L-3's spec item**. The **unsafe half** —
changing `run()`'s return shape — is not worth its own item.

---

## Blocked on ZD

### Z-5 · `request_with_retry` obeys a server-supplied `Retry-After` without bounding it by `max_backoff`

**Cite:** `cre/f1/ratelimit.py:90-95`. **REPRODUCED:** `max_backoff=8.0` was passed; actual sleeps
`[3600.0, 3600.0, 3600.0]` → **10800 s** on one reference. `:58-66` parses the header and returns
`max(0.0, float(ra))` with **no ceiling**; `:91-94` skips the `min(..., max_backoff)` clamp entirely
when the header parses — the clamp is on the fallback arm only.

**Why it is a question and not a finding.** Nothing in the code declares `max_backoff` as a ceiling on
`Retry-After`; the docstring promises "exponential backoff" and the clamp is written only on the
fallback branch. **Choosing a ceiling is a NEW policy, and the loop may not invent one.** Blast radius
is the widest in the round: `request_with_retry` is the single retry helper for `lookup.py:96`,
`confirm.py:89/105/126/157`, `biblio_match.py:703/720`, `ncbi_meta.py:115/177/253`,
`evidence_reader.py:134` and `fulltext_reader.py:743` — every network path in the package.

**Question for ZD:** may a run truncate an upstream-supplied `Retry-After`, and if so at what bound —
or should it instead surface the wait and abandon the reference? The outcome is eventually correct
(the 429 does become `FETCH_RESOLVER_ERROR` at `lookup.py:100-102`), so **this corrupts no number**;
it is an availability defect on the F1 hot path, since a rate-limited NCBI is exactly the condition
under which every PMID-bearing reference takes this branch.

### Z-11 · The adapter receipt records the launch **declaration**, not the invocation

**Cite:** `cre/f1/recording_adapter.py:90-95`. **REPRODUCED.** `recorded()` takes `*args, **kwargs`
and **reads none of them**; `record()` builds every entry from `_base()` (`:71-77`), which reads
`self.model` and `self.temperature` — both fixed at `__init__` (`:52-58`) from what the **caller
declared**. So `verify_receipt`'s unauthorized-model clause (`production_launcher.py:546-551`) and
temperature clause (`:553-569`) **compare the declaration to a copy of itself and cannot fail**, yet
`launch_receipt` publishes them as verification.

```
what the callable ACTUALLY sent : {"model": "gpt-4o-mini", "temperature": 0.9, "assistant_prefill": "{"}
what the receipt RECORDED       : {"model": "claude-opus-5", "seam": "extractor"}
verify_receipt -> PASSED        : {"calls": 3, "models": ["claude-opus-5"], "temperature": "unsupported"}
```

**This is the project's signature defect on a governance artifact.** It bears directly on **DEC-065**
(model authorization) and **DEC-070** — whose recorded code consequence is *"verify_receipt refuses any
call not recorded at the declared temperature"*, a clause that cannot refuse. On the pinned production
model it is airtight-by-construction: `claude-opus-5` is in `TEMPERATURE_REJECTING_MODELS`
(`production_launcher.py:183-185`), resolves to `'unsupported'` (`:243-245`), and `_base()` at
`recording_adapter.py:64` structurally never writes a temperature key in that state.

**Why the obvious fix was refused by Blast radius, and this is the crux of the question.** Real seam
callables take model and temperature from a **closure**, not from kwargs — `f4_strength.py:765` calls
`call_llm(generator_prompt)` with a single positional argument, and every seam in
`test_recording_adapter.py:64-80` has the same shape. **Making `wrap()` record observed kwargs would
write an "unobserved" model on every real call**, and `verify_receipt`'s clause at
`production_launcher.py:546-551` would then raise `LaunchRefused` on every launch.

**Question for ZD:** how should this be closed? Options: (a) change the seam-callable contract so the
model and temperature actually sent are observable at the wrap point — largest change, real
verification; (b) leave the mechanism and **correct the published `limitation` string**
(`production_launcher.py:641-646`), which currently discloses a **weaker** gap than the one that
exists — cheapest, honest, verifies nothing; (c) remove the two clauses from `verify_receipt` so the
receipt stops claiming what it cannot check. **Recommendation: (b) now, (a) specced separately** — the
false claim in the shipped artifact is the part that makes a reader act wrongly, and it is fixable
without touching a single seam.

---

## Round 1 verdict

**4 LAND → the F1 clear-streak resets to 0.** F1 is **not** clear and **not** saturated: three of the
four landed findings are severity-CRITICAL false-accusation routes, none is instrumentation-only or
cosmetic, so the diminishing-returns test is not met. **Round 2 is warranted.**

**Round 2 should look hardest at:** every other place the transport vocabulary is minted or read
(L-1/L-3/L-6 are one defect at three layers, and there may be a fourth); the `preband_contract`
join accounting, which no finder reached in depth; and `biblio_match`'s two `RetrievedRecord`
construction sites (`:655`, `:680`), which this round confirmed exist but did not trace.


---

# Audit loop — F1 stratum, round 2 (2026-08-17)

**Label:** F1 — fabricated / non-existent reference.

**Method.** One auditor over the F1 surface, then three independent checkers — Reality, Blast radius, Cost. Every checker opened every cited line itself and re-ran the auditor's probe rather than trusting the pasted output. **A finding lands only on unanimous LAND with all three checkers at ≥95% confidence**; a unanimous LAND carrying any vote below 95 is demoted to DEFER automatically.

**Result: 3 findings → 1 LAND · 2 DEFER · 0 ASK-ZD · 0 REJECT.**

**Stratum status: `INCOMPLETE-CHECKERS-FAILED`. Clear-streak 0/3.** The stratum has not converged. Saturation is advisory only and cannot end a stratum (ZD, 2026-08-17): the sole exit is three consecutive rounds in which the checkers accept nothing.

**⚠ 4 further findings from the next round are UNADJUDICATED** — the auditor raised them and the checkers died on a session limit before grading them. They are recorded as unadjudicated, **not** as rejected, and **not** as a clear round.

---

## Landed findings

### L-1 · The adjudication harness — the only thing in the package that mints gold — drops the transport word, so an NCBI outage is handed to the human adjudicator as "(claimed PMID did not resolve)"

**Cite:** `cre/f1/adjudicate.py:70-72 (evidence_view) and cre/f1/adjudicate.py:150-152 (write_worklist); gold minted at cre/f1/adjudicate.py:84` · **REPRODUCED** · **verdict: LAND** · checker confidence 95–96

**Decision conflict:** Direct conflict with the CLOSED 2026-08-16 transport decision, recorded in code in three places I read this session.

(1) `cre/f1/schema.py:333-336`: "One of the FETCH_* statuses. THE FIELD THAT KEEPS AN OUTAGE OUT OF THE RECORD AS AN ACCUSATION: without it, ``pmid_resolved=False`` in a durable log is unreadable -- a dead PMID and a 429 that survived every retry look identical forever after." `adjudicate.py:72` renders the RESOLVED line from precisely that unreadable boolean.

(2) `cre/f1/decide.py:163-166`, the rationale wording that was deliberately changed by the same fix: "The rationale states only what was observed: a PMID that ANSWERED and had no record did not 'resolve to a different paper', and saying so asserted a resolution that never happened." `adjudicate.py:72` asserts a NON-resolution that was never observed — the same error the machine rationale was rewritten to stop making.

(3) `/home/claude/work/cre-f3f7/docs/F1_FABRICATION_GUARD_SPEC.md:196`, acceptance row: "run where the F1 check could not run | manifest | distinguishable from zero". Satisfied at the manifest layer (`eval_report.py:186-203`, `f1_status`), NOT at the adjudication layer — which is where the label that reaches the dataset is actually decided. And spec `:199-200`: "Precision-first. Ambiguity escalates to human review; it never becomes an accusation." The row IS escalated. The harness then removes the evidence that it was ambiguous.

Unadjudicated gap, not a policy I am inventing: `adjudicate.py` appears nowhere in `F1_FABRICATION_GUARD_SPEC.md` (I grepped it), and appears in none of round 1's LANDED, DEFERRED, REJECTED or BLOCKED-ON-ZD items. I am naming no threshold and no constant — the fix is to carry fields that already exist (`pmid_transport_status`, `decided_by`, `rationale`) into the two views, which is a display decision for ZD, not a numeric one.

```
cre/f1/adjudicate.py:70-72 --
            "  RESOLVED  : " + (_fmt(retr.get("title"), retr.get("authors"),
                                     retr.get("year"), retr.get("pmid"))
                                if retr.get("resolved") else "(claimed PMID did not resolve)"),

cre/f1/adjudicate.py:150-152 --
    def write_worklist(self, path: str) -> None:
        cols = ["citation_id", "predicted_label", "title_similarity",
                "llm_verdict", "claimed_title", "resolved_title",
                "db_hits", "verdict", "final_label", "note"]
```

**Mechanism.** Hop 1 — the status is minted correctly. `cre/f1/lookup.py:98-99` stamps `FETCH_RESOLVER_ERROR` on a non-200 EFetch. Hop 2 — it is copied correctly. `cre/f1/lookup.py:504` (`log.pmid_transport_status = ref.retrieved.transport_status`). Hop 3 — the guard fires correctly. `cre/f1/run.py:114` short-circuits before `llm_filter` and `confirm`, and `cre/f1/decide.py:74-81` labels the row `HUMAN_REVIEW`/`LOW` with `decided_by="pmid_fetch_no_answer"`. Hop 4 — it survives serialisation correctly. `cre/f1/schema.py:477` puts it in `PredictionRecord.evidence["pmid_transport_status"]`; `cre/f1/schema.py:503-504` puts it in the log JSONL under both `retrieved.transport_status` and `log.pmid_transport_status`. Hop 5 — IT IS DROPPED. `cre/f1/adjudicate.py:122` selects candidates whose label is in `REVIEW_LABELS = {F1, F2, "human_review"}` (`:37`), so the rows the transport guard just created are exactly the rows this harness reviews. `cre/f1/adjudicate.py:72` then renders the RESOLVED line from `retr.get("resolved")` alone — the boolean `cre/f1/schema.py:333-336` says is unreadable — and prints the affirmative sentence "(claimed PMID did not resolve)" for a fetch that never answered. `cre/f1/adjudicate.py:150-152` is worse: the headless worklist, the path the module's own docstring at `:16-21` names as the way to produce gold at scale, emits ten columns and not one of them is `pmid_transport_status`, `transport_status`, `decided_by`, `pmid_resolved` or even `rationale` — all four of which are sitting in `c.log` and `c.pred["evidence"]`, which the method already has in hand. Hop 6 — the human's verdict becomes gold. `cre/f1/adjudicate.py:190-192` (`apply_worklist`) accepts any `final_label` in `TAXONOMY_LABELS`, `:196-199` (`_collect`) calls `to_gold()` on every `confirm`, and `cre/f1/adjudicate.py:83` resolves the label as `self.final_label or self.predicted_label`. `cre/f1/adjudicate.py:84` is the only `GoldRecord(` construction site in the entire package (verified by `grep -rn "GoldRecord(" --include=*.py cre/ | grep -v /test_`). The gold that comes out is what `cre/f1/eval_report.py:225` (`_precision_on_band`) measures published precision against. I also swept for any other unqualified non-resolution assertion (`grep -rn "did not resolve|no PubMed record" --include=*.py cre/ | grep -v /test_`): the only other two are `cre/f1/decide.py:219` and `cre/f1/lookup.py:531`, and both are correctly guarded — `decide.py:219` sits after the `:74` transport guard (the comment at `:213` says so explicitly) and `lookup.py:531` is the `else` arm of `if not fetch_answered(...)` at `:523`. `adjudicate.py:72` is the only unguarded one, and it is the human-facing one.

**Reproduction.**

```
REPRODUCED. `/tmp/p2/probe_adj.py` — two references identical in every claimed field, differing only in what NCBI does (503 vs 200-with-empty-body). Real `run.process_reference`, real `Adjudicator`; only the injected `session` object is stubbed.

```
=== Adjudicator.evidence_view() (interactive path) ===
[PMC1:r1]  predicted: human_review  (decided_by=pmid_fetch_no_answer)
  rationale : The claimed PMID could not be checked: the PubMed fetch did not answer (resolver_error). Whether it resolves is unknown; held for human review rather than reported as a finding.
  CLAIMED   : 'A totally real indexed paper about widgets.'  | Smith | 2020 | id=99999999
  RESOLVED  : (claimed PMID did not resolve)
  similarity: None   author_match=None   year_match=None
  llm       : None
  db_hits   : {}

[PMC1:r2]  predicted: F1  (decided_by=confirm_not_found_f1)
  rationale : Claimed title not found in PubMed, Crossref, or OpenAlex; claimed PMID did not resolve.
  CLAIMED   : 'A totally real indexed paper about widgets.'  | Smith | 2020 | id=99999999
  RESOLVED  : (claimed PMID did not resolve)
  similarity: None   author_match=None   year_match=None
  llm       : fabrication
  db_hits   : {'pubmed': 0.0, 'crossref': 0.0, 'openalex': 0.0}
```

The outage row and the genuine-F1 row print the SAME RESOLVED line. Note the row's own rationale directly contradicts the line printed two lines below it — code vs. the text the same function emits.

The headless path, which has no rationale column at all:

```
=== write_worklist() CSV (headless path -> gold) ===
citation_id,predicted_label,title_similarity,llm_verdict,claimed_title,resolved_title,db_hits,verdict,final_label,note
PMC1:r1,human_review,,,A totally real indexed paper about widgets.,,{},,,
PMC1:r2,F1,,fabrication,A totally real indexed paper about widgets.,,"{""pubmed"": 0.0, ""crossref"": 0.0, ""openalex"": 0.0}",,,
```

Everything a human could use to tell an outage from an absence is blank. Closing the loop — I filled `verdict=confirm, final_label=F1` on the OUTAGE row and ran `apply_worklist` + `save_gold`:

```
=== gold minted from the OUTAGE row: 1 record(s) ===
  citation_id= PMC1:r1  label= F1  source= adjudicated_from_f1_detector
  any transport key in gold record?  NONE
```

A gold F1 record for a reference whose existence was never checked, carrying no trace that the check never ran.
```

**Why it matters.** F1's false positive is a public accusation that a real, indexed paper does not exist. Every machine-side guard against that accusation is intact — the fetch layer names the status, the decide layer holds on it, the manifest layer reports `transport_failed`. But the accusation is not ultimately made by the machine: `adjudicate.py` is the harness that takes the machine's HUMAN_REVIEW holds and asks a person to convert them into gold, and it is the one layer that throws the word away. So the guard does not fail loudly — it succeeds, routes the row to a human exactly as designed, and then hands that human a view in which the outage row is indistinguishable from a fabrication. The escalation the whole precision-first design rests on terminates in a blank CSV cell.

It is also self-reinforcing in the worst direction. Gold from `adjudicate.py:84` is the denominator `eval_report._precision_on_band` (`eval_report.py:225`) measures F1/F2 precision against. A transport-failure row confirmed as F1 becomes gold F1, which then makes the detector's precision look BETTER, not worse — the error is invisible to the very instrument built to catch it. And the corruption scales with outage severity: the worse NCBI is behaving during a run, the more all-blank rows land in the worklist, and the more of them look like fabrications.

This is the defect class named in the brief, one layer further out than round 1 reached. A path that never ran and a path that ran and found nothing are indistinguishable in the output — except the "output" here is not a manifest counter, it is a human being's screen.

**Live-service check.** FAILED TEST, not a negative result — and I did not route around it. All three provider domains are refused by this session's egress policy at the CONNECT stage:

```
$ curl -sS 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=31665581&rettype=medline&retmode=text' --cacert /root/.ccr/ca-bundle.crt
curl: (56) CONNECT tunnel failed, response 403
HTTP 000
$ curl -sS "$HTTPS_PROXY/__agentproxy/status" | jq -r '[.recentRelayFailures[].host]|unique'
['api.crossref.org:443', 'api.openalex.org:443', 'eutils.ncbi.nlm.nih.gov:443']
  "detail": "gateway answered 403 to CONNECT (policy denial or upstream failure)"
```

So I could NOT verify EFetch's HTTP status codes, `querytranslation` or `warninglist` myself this session, and I make no claim about them. Per the proxy README, a 403 is an organization policy denial and must be reported, not retried or bypassed.

What I could establish through the sanctioned PubMed connector: PMID 31665581 is a real, indexed paper — "Purple Urine after Catheterization", N Engl J Med 2019;381(18):e33, DOI [10.1056/NEJMicm1905446](https://doi.org/10.1056/NEJMicm1905446) (according to PubMed) — while PMID 99999999 returns nothing (`count: 1` for two requested ids). That is the shape of the harm: a real indexed paper on one side, a nonexistent id on the other, and this harness renders them identically to the human whenever the fetch fails.

Critically, the finding does not depend on any live measurement. `FETCH_RESOLVER_ERROR` is produced by `cre/f1/lookup.py:97-102` on ANY `requests.RequestException`, on `r is None`, and on ANY non-200 — including, per `cre/f1/lookup.py:196-199`, a 429 that survived every retry in `ratelimit.request_with_retry`. It is a state the code mints from its own control flow, and I reproduced it end to end above.

**Checkers.**

- **reality** — LAND (95%) · citation verified: yes
  Real, reproduced byte-for-byte by me from a rewritten probe, and reachable in the only gold-minting path the package has. I did not inherit the auditor's probe: I wrote /tmp/rc/f1.py from the description, drove the real cre.f1.run.process_reference with two stubbed sessions (EFetch 503 vs EFetch 200-empty-body), serialised through the real write_jsonl, and loaded the real Adjudicator. Output matched the dossier exactly, including the gold record. Reachability is the crux and it holds: adjudicate.py:84 is the sole GoldRecord construction site in the whole package, eval_report.py:96 names Adjudicator as where `gold` comes from, and eval_report.py:225-247 makes that gold the denominator of the published wrong-paper precision. The outage row is guaranteed to arrive here because decide.py:74-81 labels it HUMAN_REVIEW and adjudicate.py:37 puts "human_review" in REVIEW_LABELS. So the machine's
- **blast-radius** — LAND (96%) · citation verified: yes
  BLAST RADIUS IS NEAR-ZERO AND THE BUG IS AT THE GOLD-MINTING LAYER. Fix risk: adjudicate.py is NOT in GOVERNING_MODULES (production_launcher.py:65-69) — no governed digest moves, CONTRADICTIONS 65 is not deepened. test_mint_v1.py pins only synthetic cre/f1/freeze/mod_<role>.py modules, so no acceptance-record digest literal moves. band_prompts.py is untouched. adjudicate.py is imported by exactly one file in the package (test_adj.py) and by nothing in the pipeline; `grep -rn 'evidence_view|write_worklist|apply_worklist|Adjudicator('` returns only adjudicate.py's own docstring and test_adj.py. test_adj.py has no test functions (`pytest cre/f1/test_adj.py -q` -> "no tests ran") but its module-level asserts execute at collection; I ran it as a script and it passes. Its only wording-sensitive assert is line 40, `"did not resolve" not in v`, evaluated on candidate c1 whose retrieved.resolved
- **cost** — LAND (96%) · citation verified: yes
  COST VERDICT: this is the cheapest finding in the stratum to act on and the most expensive to leave. FIX TOUCHES: `cre/f1/adjudicate.py` only (plus `cre/f1/test_adj.py`). GOVERNED MODULES MOVED: NONE. `adjudicate.py` is absent from GOVERNING_MODULES, which I read from code at `cre/f1/production_launcher.py:65-70`, and the digest loop at `cre/f1/production_launcher.py:126` iterates only that tuple, so no launcher digest moves and CONTRADICTIONS 65 is not deepened. Not covered by FROZEN_SOURCE_BLOB_OID (`cre/f1/freeze/semantic_validator_v1.py:66`) or PINNED_SCHEMA_SHA256/BYTES (`cre/f1/freeze/schema_gate.py:20-21`). PUBLISHED FIGURE MOVED: NONE, and I checked rather than assumed — `find /home/claude/work/cre-f3f7 -name '*gold*'` returns nothing, so no gold artifact exists in this tree, and the seed-47 adjudication of record ran through a SEPARATE Colab instrument (`f2_seed47_labels.csv` ->

---

## Deferred

### D-1 · The author trip-wire note tells the log the claimed first author "appears later in the resolved author list" when it appears nowhere in it — it reads a substring-tolerant roster-wide flag as positional evidence, on F1 and F2 rows

**Cite:** `cre/f1/lookup.py:581-583 (relation ternary), emitted at cre/f1/lookup.py:584-585; the flag that drives it is set at cre/f1/biblio_match.py:347 via _surname_present at cre/f1/biblio_match.py:324-326` · **REPRODUCED** · **verdict: DEFER** · checker confidence 80–95

**Decision conflict:** `cre/f1/lookup.py:598-601` records a closed decision in this file: the trip-wire must "compare position zero to position zero", because "the older anywhere-in-roster check mislabeled a claimed first author found only as a coauthor as a clean trip-wire pass." That fix was applied to the *signal* (`_record_author_tripwire`, `cre/f1/lookup.py:391-395`, keys on `first_author_match` only). The note at `cre/f1/lookup.py:581-583` re-introduces the abandoned reading in the *description*, and does so through a flag that is strictly weaker than the roster check the comment rejected — `_surname_present` (`cre/f1/biblio_match.py:324-326`) will report True on a mere substring, so `author_match is True` does not even establish roster membership, let alone position.

```
cre/f1/lookup.py:579-585
    flagged = _flag_decision(m, accept, author_tripwire=author_tripwire)
    if flagged:
        if author_tripwire and m.fields.first_author_match is False:
            relation = ("appears later in the resolved author list"
                        if m.fields.author_match is True
                        else "does not match the resolved first author")
            log.notes = (f"claimed first author {ref.claimed.authors[0]!r} "
                         f"{relation}; positional author trip-wire fired.")

cre/f1/biblio_match.py:321-327
        ctoks = c.split()
        if last and last in ctoks:                  # surname token appears
            return True
        if len(c) >= 4 and len(claimed_surname) >= 4 and \
                (c in claimed_surname or claimed_surname in c):
            return True
    return False

cre/f1/lookup.py:598-602 (the comment that settles the position-vs-roster question)
    # Trip-wire audit signal: compare position zero to position zero.  The older
    # anywhere-in-roster check mislabeled a claimed first author found only as a
    # coauthor as a clean trip-wire pass, even though `_flag_decision` correctly
    # flagged that positional mismatch.
    _record_author_tripwire(log, m, enabled=author_tripwire)
```

**Mechanism.** Hop 1 — the claimed side is a bare surname in production. `cre/f1/parser.py:87-99` (`_surnames_under`) collects only the text of `<surname>` elements, and `cre/f1/parser.py:102-136` (`_authors_from`) returns that list, so `ClaimedRef.authors` is e.g. `['Wang']`, not `'Wang T'`. Hop 2 — the resolved side is also a bare surname: `cre/f1/lookup.py:206` does `authors = [_au_surname(a) for a in fields.get("AU", [])]`, so MEDLINE `AU  - Wangler MF` becomes `'Wangler'`. Hop 3 — `cre/f1/biblio_match.py:345-347` computes `fa.author_match = _surname_present(claimed_sn, cand.authors)`. `_surname_present` first tries token membership (`cre/f1/biblio_match.py:322`), which fails for `wang` against `['wangler']`, then falls through to the **substring** branch at `cre/f1/biblio_match.py:324-326`, where `claimed_surname in c` -> `'wang' in 'wangler'` -> True. `author_match` is now True although the claimed surname is not any author of the record. Hop 4 — `cre/f1/biblio_match.py:348` computes `fa.first_author_match = first_author_equivalent(...)`, which at `cre/f1/work_identity.py:253-258` intersects position-zero alias sets built by `cre/f1/work_identity.py:179-222`: `{'wang'}` vs `{'wangler'}`, disjoint -> False. Hop 5 — `cre/f1/lookup.py:578` flags the row via `_flag_decision`'s third disjunct (`cre/f1/lookup.py:387`). Hop 6 — `cre/f1/lookup.py:580` enters the trip-wire note branch, and `cre/f1/lookup.py:581-583` selects the `"appears later in the resolved author list"` arm **solely because `author_match is True`** — but `author_match` is roster-wide AND substring-tolerant, so True does not establish either "appears" or "later". Hop 7 — the row proceeds through `cre/f1/run.py:119-125` to `cre/f1/decide.py`, reaching F1 at `decide.py:211-221` when the three searches answer empty, or F2 at `decide.py:167-173` when one finds the title. Hop 8 — the sentence is persisted: `cre/f1/schema.py:496-505` (`to_log_record`) serialises `asdict(self.log)` including `notes`, written by `cre/f1/run.py:175` to `out_logs`. This is exactly the conflation `cre/f1/lookup.py:598-601` says was already fixed once in the *signal*; it survives in the *sentence*.

**Reproduction.**

```
Executed `/tmp/p/live2.py` (real MEDLINE record shape for PMID 38746221, whose first author is Wangler MF — confirmed live this session; claimed authors in the production bare-surname shape):

```
resolved authors parsed from real MEDLINE AU: ['Wangler', 'Yamamoto']
flagged=True author_match=True first_author_match=False
NOTE -> claimed first author 'Wang' appears later in the resolved author list; positional author trip-wire fired.
'Wang' present anywhere in roster ['Wangler', 'Yamamoto'] ? False
label='F1' conf='MED' decided_by='confirm_not_found_f1'
rationale: Claimed title not found in PubMed, Crossref, or OpenAlex; claimed PMID resolves to an unrelated paper.
```

And on an F2 row, `/tmp/p/note.py` (claimed 'Smith' vs resolved first author 'Smithson B'):

```
field_agreement: author_match=True first_author_match=False year=True journal=True
flagged      = True
log.author_match       = True
log.first_author_match = False
log.author_tripwire    = True
NOTES -> claimed first author 'Smith' appears later in the resolved author list; positional author trip-wire fired.

resolved authors[0] = Smithson B | claimed authors = ['Smith']
is 'Smith' anywhere LATER than position 0 in the resolved roster?  False

FINAL label='F2' conf='MED' decided_by='confirm_found_f2'
rationale: Claimed work found in a database but the claimed PMID resolves to a different paper: wrong reference.
notes shipped in log record: claimed first author 'Smith' appears later in the resolved author list; positional author trip-wire fired.
```

The second run also shows the mirror case: the substring branch is not needed for the note to be wrong in general, but it is what makes `author_match` True while the surname is absent from the roster.
```

**Why it matters.** This is the mandated defect class applied to a sentence: the code writes an assertion it has not established, into the durable per-reference log, on rows that carry the F1 accusation. Its reach is bounded and I state that plainly — `log.notes` is written to `out_logs` by `cre/f1/run.py:175` and is available to `eval_report.summarize` (`cre/f1/run.py:180`), but it is NOT in `to_prediction()`'s evidence dict (`cre/f1/schema.py:471-493` has no `notes` key) and NOT in the adjudication packet (`cre/f1/adjudicate.py:64-77` prints rationale, claimed, resolved, similarity, author_match, llm_verdict, db_hits — no notes). So it does not enter the dataset. What it does corrupt is the human-readable audit trail of exactly the label whose false positive is a public accusation: an auditor reading the log for an F1 row sees "the claimed first author appears later in the resolved author list", which reads as *coauthor overlap on the same paper* — mild, formatting-ish, low-alarm evidence — when the truth is that the claimed surname is absent from the record entirely, which is the strongest wrong-paper signal available. The note systematically understates the evidence on the most consequential rows, and it does so precisely on the fuzzy-surname pairs where a human most needs an accurate description.

**Live-service check.** Direct HTTPS to NCBI is blocked by org egress policy in this session and I did not route around it: `curl -sS -G https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi ...` returned `curl: (56) CONNECT tunnel failed, response 403 / HTTP=000`, and `curl -sS "$HTTPS_PROXY/__agentproxy/status"` recorded `{"kind":"connect_rejected","detail":"gateway answered 403 to CONNECT (policy denial or upstream failure)","host":"eutils.ncbi.nlm.nih.gov:443"}`. **Blocked host reported, not bypassed.** I grounded the surname shape with the sanctioned PubMed connector instead. According to PubMed: `search_articles(query="Wangler MF[1au]")` returned `{"pmids":["40498764","38746221"],"total_count":14,"query_translation":"wangler mf[Author - First]"}` — i.e. 14 real, indexed records whose FIRST author surname is `Wangler`, a strict superstring of the extremely common claimed surname `Wang`. `get_article_metadata(['31616000'])` returned a record with `"last_name":"Wangler","fore_name":"Michael F"` in the author list, confirming MEDLINE stores this surname exactly as the code's `_au_surname` would reduce it (DOI: https://doi.org/10.1038/s41467-019-12435-8). The same generator exists for Lee/Leeming, Park/Parkin, Hall/Halliday, Chan/Chandra and any truncated-vs-full MEDLINE surname pair, so this is not a contrived shape.

**Checkers.**

- **reality** — DEFER (80%) · citation verified: yes
  The mechanism is real and I reproduced it, but it fails the 'worth acting on' half of the 95% bar, and I am stating the doubt rather than landing with a caveat. Three things hold it under the bar. First, ZERO downstream reach, which I checked rather than took on trust: `grep -rn '\bnotes\b' --include=*.py cre/ | grep -v /test_ | grep -v 'log.notes ='` returns exactly two lines — schema.py:369, the field declaration, and eval_report.py:211, an unrelated static list of report caveats. Nothing in the package reads log.notes. I also opened adjudicate.py:64-77 myself and confirmed the adjudication packet has no notes key, and schema.py's to_prediction evidence dict has none either. So the false sentence changes no label, no confidence, no rationale, no metric, and never reaches the dataset or the human adjudicator — it lands only in the out_logs JSONL that run.py:175 writes. Second, the class
- **blast-radius** — LAND (95%) · citation verified: yes
  REAL, REPRODUCED, AND THE FIX COSTS NOTHING — BUT ONLY IF IT IS SCOPED TO THE STRING. Fix risk, measured: lookup.py is NOT in GOVERNING_MODULES, so no governed digest moves and CONTRADICTIONS 65 is not deepened; test_mint_v1.py pins only synthetic freeze/ modules. `log.notes` has ZERO programmatic readers on EITHER tree — I grepped both cre-f3f7 and the F2 branch of record for `["notes"]` / `.get("notes"` / `.get('notes'` and got nothing; every hit is a write. It is not in to_prediction()'s evidence (schema.py:471-493 has no notes key) and not in the adjudication packet (adjudicate.py:64-78). The string exists in exactly one place: `grep -rn "appears later in the resolved"` returns only lookup.py:581. THE HARD CONSTRAINT: the fix must change ONLY the note wording. `_surname_present` (biblio_match.py:301-326) is byte-identical to the F2 branch of record (same function at biblio_match.py:4
- **cost** — LAND (95%) · citation verified: yes
  COST VERDICT: zero-cost to fix, with ONE hard guard the spec must carry in writing. FIX TOUCHES: the `relation` ternary at `cre/f1/lookup.py:581-583` — three lines of string selection — and nothing else. GOVERNED MODULES MOVED: NONE. `lookup.py` and `biblio_match.py` are absent from GOVERNING_MODULES (`cre/f1/production_launcher.py:65-70`, read from code). `parser.py` and `work_identity.py` appear only as HOPS in the mechanism; no fix goes near them, so the governed `parser.py` is not touched and CONTRADICTIONS 65 is not deepened. REPORTED NUMBER MOVED: NONE, and I verified the containment rather than accepting the auditor's bounding. `log.notes` has no numeric consumer anywhere: `to_prediction`'s evidence dict (`cre/f1/schema.py:471-493`) has no `notes` key; `Candidate.evidence_view` (`cre/f1/adjudicate.py:64-77`) never prints notes; `write_worklist`'s ten columns (`cre/f1/adjudicate.py

### D-2 · parse_verdict's degrade-to-uncertain except clause misses TypeError: five JSON-valid model responses crash out of llm_filter instead of falling to `uncertain`, converting a would-be CLEARED into an unattributed quarantine

**Cite:** `cre/f1/llm_filter.py:70-72 (the `v in _VALID` membership test and the except clause) and cre/f1/llm_filter.py:80 (the notes concatenation)` · **REPRODUCED** · **verdict: DEFER** · checker confidence 82–95

**Decision conflict:** `cre/f1/llm_filter.py:71` establishes the module's contract by construction: malformed model output must degrade to `V_UNCERTAIN`, never escape. `cre/f1/run.py:60-65` states the same contract for the caller — "Empty / refusal responses yield "" (parse_verdict -> uncertain), no crash." Five JSON-valid response shapes violate both. It also sits against `cre/f1/run.py:151-158`, whose comment scopes the quarantine to "a reference we failed to process" — a model that answered in an unexpected JSON shape is a reference the pipeline *could* have judged (`uncertain` -> `decide.py:127` -> human_review, or `formatting_discrepancy` -> `decide.py:120` -> cleared), so routing it to the quarantine mislabels a handled case as an unhandled one.

```
cre/f1/llm_filter.py:65-72
def parse_verdict(raw: str) -> tuple[str, str]:
    raw = raw.strip().replace("```json", "").replace("```", "").strip()
    try:
        obj = json.loads(raw)
        v = obj.get("verdict", V_UNCERTAIN)
        return (v if v in _VALID else V_UNCERTAIN), obj.get("reason", "")
    except (json.JSONDecodeError, AttributeError):
        return V_UNCERTAIN, "unparseable LLM output"

cre/f1/llm_filter.py:75-81
def llm_filter(ref: Reference, complete: Callable[[str], str]) -> str:
    """Run the filter; record verdict on the log; return the verdict."""
    verdict, reason = parse_verdict(complete(build_prompt(ref)))
    ref.log.llm_verdict = verdict
    if reason:
        ref.log.notes = (ref.log.notes + " | " if ref.log.notes else "") + reason
    return verdict
```

**Mechanism.** Hop 1 — `_VALID` is a `set` (`cre/f1/llm_filter.py:21`), so `v in _VALID` hashes `v`. When the model returns valid JSON whose `verdict` value is a dict or a list, `obj.get("verdict", ...)` yields an unhashable object and `cre/f1/llm_filter.py:70` raises `TypeError: unhashable type: 'dict'`. Hop 2 — the except clause at `cre/f1/llm_filter.py:71` lists only `json.JSONDecodeError` and `AttributeError`, so the TypeError escapes `parse_verdict` entirely, bypassing the `V_UNCERTAIN` fallback that clause exists to provide. Hop 3 — a second, independent TypeError: when `verdict` is a valid token but `reason` is a dict, list or int, `parse_verdict` returns normally, `cre/f1/llm_filter.py:78` writes `ref.log.llm_verdict = verdict`, and then `cre/f1/llm_filter.py:80` does `... + reason` -> `TypeError: can only concatenate str (not "list") to str`. The log now carries a verdict for a reference that was never decided. Hop 4 — either TypeError propagates out of `llm_filter` through `cre/f1/run.py:119` and out of `process_reference`, and is caught by the bare-`Exception` quarantine at `cre/f1/run.py:151-166`, which sets `HUMAN_REVIEW` / `decided_by="quarantine_exception"`. `decide()` is never called for that reference. Hop 5 — the label consequence is real for one of the shapes: `{"verdict":"formatting_discrepancy","reason":[...]}` would have taken `cre/f1/run.py:120-121` -> `decide.py:120-124` -> **CLEARED / MED**, and instead becomes HUMAN_REVIEW. For `verdict: <dict>` and `verdict: <list>` the final label is HUMAN_REVIEW either way, but the reason code changes from `llm_uncertain` to `quarantine_exception`. Hop 6 — the substitution is invisible in any tally: `cre/f1/run.py:142` increments `quarantined` and `cre/f1/run.py:183` returns only `counts`, so the number is computed and discarded (the condition D-12 already names), and no `quarantine_exception` bucket is reported.

**Reproduction.**

```
Executed `/tmp/p/llm.py`. `parse_verdict` over 20 shapes (abridged to the relevant rows plus the safe controls):

```
empty                parse_verdict -> 'uncertain'                  reason='unparseable LLM output'
prose                parse_verdict -> 'uncertain'                  reason='unparseable LLM output'
list                 parse_verdict -> 'uncertain'                  reason='unparseable LLM output'
missing_key          parse_verdict -> 'uncertain'                  reason='x'
trailing_content     parse_verdict -> 'uncertain'                  reason='unparseable LLM output'
nested_json          parse_verdict -> 'uncertain'                  reason=''
verdict_is_dict      parse_verdict -> RAISED TypeError: unhashable type: 'dict'
verdict_is_list      parse_verdict -> RAISED TypeError: unhashable type: 'list'
verdict_is_null      parse_verdict -> 'uncertain'                  reason=''
verdict_true         parse_verdict -> 'uncertain'                  reason=''
reason_is_dict       parse_verdict -> 'fabrication'                reason={'a': 1}
reason_is_list       parse_verdict -> 'formatting_discrepancy'     reason=['a']
reason_is_int        parse_verdict -> 'uncertain'                  reason=7
dup_keys             parse_verdict -> 'fabrication'                reason=''
very_long            parse_verdict -> 'fabrication'                reason='xxxx...'   (200000 chars, no error)
fenced               parse_verdict -> 'fabrication'                reason='r'
whitespace_token     parse_verdict -> 'uncertain'                  reason=''
case                 parse_verdict -> 'uncertain'                  reason=''
top_level_str        parse_verdict -> 'uncertain'                  reason='unparseable LLM output'
nan                  parse_verdict -> 'uncertain'                  reason=''
```

End to end through `llm_filter` (stub `complete`, no monkeypatching):

```
--- llm_filter() end to end ---
verdict_is_dict      -> RAISED TypeError: unhashable type: 'dict'
verdict_is_list      -> RAISED TypeError: unhashable type: 'list'
reason_is_dict       -> RAISED TypeError: can only concatenate str (not "dict") to str
reason_is_list       -> RAISED TypeError: can only concatenate str (not "list") to str
reason_is_int        -> RAISED TypeError: can only concatenate str (not "int") to str
very_long            -> verdict='fabrication' notes_len=200000
```

Five shapes crash; the other fifteen degrade to `uncertain` exactly as intended.
```

**Why it matters.** The direction of failure is precision-safe — nothing here can produce an F1 or F2, and I am not claiming otherwise. What it costs is honesty of the record and one real label flip. (a) A reference the model correctly triaged as a benign formatting discrepancy is not cleared; it becomes an unjudged HUMAN_REVIEW because the *reason* field had the wrong JSON type, which is a recall loss on the clear side driven by nothing the reference did. (b) The three `verdict`-shape crashes land on the same final label as the intended `uncertain` path but under a different reason code, so the population of "the model answered in a shape we could not read" is filed under "processing raised an unexpected error" — the two are then indistinguishable in the log, which is the same not-attempted-vs-attempted-and-empty confusion this whole audit exists to remove. (c) Because `cre/f1/run.py:142`'s `quarantined` counter is discarded at `cre/f1/run.py:183`, a run in which the model started emitting nested verdicts would silently drain the cleared and uncertain buckets with no number anywhere showing it. This is a distinct code path from L-7 (which is about `make_completer`'s non-retryable auth error being swallowed): here the exception originates inside `parse_verdict`/`llm_filter` from well-formed JSON, and it is the module's own except clause — written to guarantee "malformed model output becomes `uncertain`" — that fails to hold.

**Live-service check.** Not applicable — this finding makes no claim about any external service. It is entirely local control flow over a stubbed `complete` callable, and the reproduction above is the executed evidence. (For completeness, the only external service I attempted this session was NCBI eutils, which the org egress proxy refused with 403 on CONNECT; reported above, not bypassed.)

**Checkers.**

- **reality** — LAND (95%) · citation verified: yes
  Reproduced from scratch by me, and the path is genuinely live in the production configuration. I wrote /tmp/rc/f3.py from the description without reading the auditor's script and got the identical five crashes. Reachability is the question I own and it holds cleanly: production `complete` is built by run.py:66-85, a plain `client.messages.create` whose return is run through `_extract_text` and handed back as a raw string — there is no tool schema, no structured-output enforcement, nothing but the prompt text at llm_filter.py:50-51 asking for the shape. `parse_verdict` is therefore the sole sanitizer standing between untrusted model output and the pipeline, and its except tuple omits the one exception type its own membership test can raise. Nothing upstream gates the path dead: run.py:119 is on the flagged-survivor hot path and is reached whenever compare_and_flag flags, same_work_reason
- **blast-radius** — DEFER (82%) · citation verified: yes
  THE CRASH IS 100% REAL AND I REPRODUCED ALL FIVE SHAPES — BUT IT FAILS THE 95% BAR ON TWO LEGS, AND THE OBVIOUS FIX TRADES A LOUD DEFECT FOR A QUIET ONE, WHICH IS MY REJECT CRITERION. Leg 1, reachability is unmeasured. The prompt at llm_filter.py:23-51 ends with an explicit schema whose values are both strings: `Respond with ONLY a JSON object, no prose: {"verdict": "<one of the four>", "reason": "<one sentence>"}`. Whether a pinned Claude Opus ever returns a dict- or list-valued `verdict` or `reason` against that instruction is a claim about model behaviour I cannot test from this container and the auditor did not measure. Per Rule 0 EXTENDED an untested external claim is a HYPOTHESIS, and the auditor honestly labels the local control flow REPRODUCED without asserting any frequency. I put reachability at roughly 75-85%, which caps the finding below 95. Leg 2, and this is the blast-radiu
- **cost** — LAND (95%) · citation verified: yes
  COST VERDICT: the cheapest fix in the stratum, with one coordination requirement against an already-landed round-1 item. FIX TOUCHES: `cre/f1/llm_filter.py` only — broaden the except tuple at `:71` to catch `TypeError`, and coerce a non-string `reason` before the concatenation at `:80`. GOVERNED MODULES MOVED: NONE. `llm_filter.py` is absent from GOVERNING_MODULES (`cre/f1/production_launcher.py:65-70`, read from code); no launcher digest moves; CONTRADICTIONS 65 is not deepened. Not pinned by FROZEN_SOURCE_BLOB_OID or PINNED_SCHEMA_SHA256. PUBLISHED FIGURE MOVED: NONE — every affected row currently lands in the bare-`Exception` quarantine as HUMAN_REVIEW, so no existing precision numerator or denominator depends on the current behaviour; nothing is retroactively restated. NO SEED, NO CORPUS RUN, NO INVENTED CONSTANT, NO LABEL-MEANING CHANGE — the fix restores the vocabulary already decl
---

# Audit loop — F1, rounds 3–8 (2026-08-18) · **ROUND-CAP, not converged**

**Stopped by decision, not by exhaustion.** 8 rounds. Rounds 1–2 are already appended above.
Rounds 3–8 raised 13 findings; after dedup across rounds, **3 distinct defects landed**, plus 4 deferred
and 1 ASK-ZD. Bar for LAND: all three checkers, each ≥95% confidence, each having re-opened the cited
line and re-run the probe.

**Rounds 3, 4, 5 and 8 re-filed the same two defects under different anchors.** They are merged below.
The count in the state register (9) counts re-files; **3 is the number of distinct things to fix.**

## ⚠ READ FIRST — L-1 blocks gold minting entirely

`adjudicate.py` is the only module in the package that mints a `GoldRecord`. **A human cannot use it to
say a flagged reference is fine.** Every other landed finding is downstream of that.

---

## L-1 · A human cannot clear a flagged reference — `accurate` is the one label the gold-minting harness cannot assign

**Cite:** `adjudicate.py:138-145` (interactive) · `adjudicate.py:180-182` (headless) · `adjudicate.py:83`
(label resolution) · `adjudicate.py:84` (the only `GoldRecord(` site) · `schema.py:25-27` (the taxonomy)
**Verdict:** LAND · 96/96/96 (r4) and 95/96/96 (r8) · REPRODUCED both paths

**The taxonomy is mixed-case by construction.** `schema.py:25` is `ACCURATE = "accurate"` — lower case.
`schema.py:26` is `F1`…`F8` — upper case. `schema.py:27` puts all nine in one `set`, so every membership
test is exact and case-sensitive.

- **Interactive path.** `adjudicate.py:139` does `lbl = v.split(maxsplit=1)[1].upper()` before the
  membership test at `:140`. `.upper()` is a no-op for `F1`…`F8` and breaks exactly one member of the
  set. `relabel accurate` is refused as "not in taxonomy". **The human can relabel a candidate to any of
  the eight defect classes and to nothing else.**
- **Headless path.** `adjudicate.py:177` reads the verdict as `.strip().lower()`; `:180` reads
  `final_label` with **no normalisation at all**. A mis-cased or mistyped label is silently discarded
  while `verdict=confirm` is still honoured — so the detector's own F1 accusation is minted as gold,
  annotated `human_1 … confidence 1.0`, with the human's correction attached as a note nothing reads.

**Why it matters:** this is the silent-clear half and the false-accusation half in one function. A human
saying "this reference is fine" produces a gold **F1** record.

**Fix direction:** normalise the label on both paths the way the verdict is already normalised, and
**refuse an unrecognised `final_label` loudly** rather than falling through to the detector's label. Do
not change the literals in `schema.py` — that is a governed module and the mixed case is the taxonomy of
record.

---

## L-2 · `to_gold` reads four keys neither serialiser writes — every gold record loses its provenance, invisibly

**Cite:** `adjudicate.py:86-98` (`to_gold`) · `schema.py:496-505` (`to_log_record`) · `schema.py:457-495`
(`to_prediction`) · the fields exist and are populated at `schema.py:378-383` and `parser.py:612-613`
**Verdict:** LAND · 96/96/95 · REPRODUCED

`Reference` declares `citance`, `cited_reference_marker`, `source_pmid`, `source_title` as REQUIRED
(`schema.py:378-383`) and `parser.py:612-613` populates them. **Both durable serialisers drop all four.**
`to_log_record` emits seven keys; none is one of these. `to_gold` then reads them off the serialised row
and gets nothing.

**Why it matters:** every gold record the package can mint has an empty citance, an empty marker and a
wholly empty `source_paper` — and **empty is a legitimate value for all three**, so total provenance loss
is indistinguishable from a reference that genuinely had none. This is the named defect class applied to
the ground-truth store.

**Fix direction:** carry the four fields through whichever serialiser `to_gold` actually consumes, and
make an absent — as opposed to empty — value distinguishable in the gold record.

---

## L-3 · The launch receipt claims a different-family judge on runs that wired a same-family one

**Cite:** `production_launcher.py:492-501` (the `compliance_note` ternary) · `:444-453` (path list) ·
`:471-482` (residual-risk guard it contradicts) · `:609` and `:637` (route into the run manifest)
**Verdict:** LAND · 96/96/96 (r5) and 72/96/96 (r8 re-file) · REPRODUCED

`verify_judge_governance`'s own docstring (`:420-424`) commits to **three** accepted answers:
different-family judge, dated amendment, DECISION-recorded scope ruling. The path list at `:447-453`
builds all three correctly. **The compliance sentence has only two arms.** The ternary at `:498` is
guarded on `ruling is not None`, i.e. on the scope-ruling path only; the amendment path leaves
`ruling = None`, the condition is False, and control falls to the `else` at `:499` — which asserts
*"The preregistered different-family judge arrangement was used"* **regardless of what judge is wired**.

**Why it matters:** the launcher refuses false provenance everywhere else and mints it here, into the
published run manifest.

**Fix direction:** give the sentence a third arm keyed on which path actually held, built from `paths`
rather than from `ruling`. `production_launcher.py` is not in `GOVERNING_MODULES` — confirm that before
editing.

---

## Deferred — real, reproduced, not worth spending this loop's budget on

| cite | claim | why deferred |
|---|---|---|
| `production_launcher.py:126-129` | `verify_tree` silently `continue`s past a governing module missing from `pkg_dir`, so a run that verified **zero of thirteen** returns success and publishes `"tree_clean": True` with an empty digest map | 95/62/95 — the mirror config error (mis-pointed `repo_dir`) has a hard refusal at `:111-121`; blast-radius put the reachability of the unguarded case below the bar. **Re-raise if `pkg_dir` is ever caller-supplied.** |
| `production_launcher.py:272-280` | the temperature note publishes *"deprecated from Claude Opus 4.7 onward"* — the exact claim DEC-070's table 100 lines above records as never measured and **WITHDRAWN** | 97/82/96 — a false string in a manifest, no number moves |
| `ncbi_meta.py:53-61`, `:131-138`, `:172-178`; `ratelimit.py:80-84` | `IDCONV` now 302-redirects to a different NCBI host, so `idconv_request_count` under-reports by half and the shared per-IP limiter throttles one of every two | 55/70/70 — measured live and real, but the count is advisory and the fix is a URL change ZD should make deliberately |

## ASK-ZD

**`recording_adapter.py:119-124` — six of the eleven `RUN_SEAMS` are NCBI HTTP fetchers, not provider
callables.** The adapter receipt stamps the launched model and temperature 0 onto them, so the
launcher's *"EVERY call used the authorized model at temperature 0"* (`production_launcher.py:32-34`,
published at `:640`) is asserted over events that carried neither, and `adapter_receipt.calls` is **not a
count of model calls**. Confidence 80/70/96. **Decide whether the receipt should count only provider
seams, or whether the sentence should be narrowed.** Either is a change to a published attestation.

## Guardrails

- `schema.py` is **GOVERNED** and its digest has already moved once in this pass — **CONTRADICTIONS 65 is
  OPEN on this class. Report the digest consequence; do not decide it.**
- `band_prompts.py` byte-identical, blob OID `fa01126e2b9482d450065fd70cd0eb1fea816f5c`.
- Precision-first, both halves. No invented constants. Specs only — no corpus run.

## Definition of done

- A human can mint a gold `accurate` record on **both** paths, and an unrecognised `final_label` is
  refused rather than dropped.
- A gold record with no provenance is distinguishable from one whose provenance was lost.
- The compliance sentence names the path that actually held, on all three paths.
- Suite: old → new counts, environment stated.

## Verification command

```
cd citation_repair_F1_handoff && PYTHONPATH=. python -m pytest cre/f1 -q
```
