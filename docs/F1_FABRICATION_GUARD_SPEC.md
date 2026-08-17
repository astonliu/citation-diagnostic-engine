# F1 — fabrication guard: transport failure must never become an accusation — implementation spec

**Date:** 2026-08-16 · **Branch:** `feat/f3-f7-semantic-validator-v1`
**Authority:** ZD, 2026-08-16, on the F1–F8 audit (`F1_F8_AUDIT_2026-08-16.md`, CONTRADICTIONS 63).
**Severity: CRITICAL.** This is the only defect in the taxonomy that produces a **false public
accusation** — that a real, indexed paper does not exist.
**Standing method:** the defect and the constraint are stated; the edit is yours. If a constraint here
is wrong, stop and report rather than reconciling silently.

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
