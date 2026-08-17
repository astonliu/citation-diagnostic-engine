# Audit loop — Amendment 01 (2026-08-16, after the brief was issued)

Paste this into the running audit-loop session as a separate prompt. It amends
`CRE_AUDIT_LOOP_SESSION_BRIEF.md`; everything not contradicted here still stands.

---

## A. All agents now have WEB ACCESS. New rules that come with it.

**RULE 0 is extended, not relaxed.** Web access adds a second class of ground truth; it does not
replace the first.

1. **Code is still the only source for what the code does.** Never use the web to infer a function's
   behaviour. RULE 0 is unchanged for anything inside the repo.
2. **Any claim about an EXTERNAL service must be tested live, never reasoned.** NCBI E-utilities,
   Crossref, OpenAlex, Unpaywall, PMC. If a finding says *"this query is malformed"* or *"this API
   returns X"*, **issue the call and paste the response.** An untested claim about an external service
   is a HYPOTHESIS and the Reality checker must treat it as one.
3. **This rule exists because the first draft of the F1 spec broke it.** See §B — a plausible-sounding
   guess about Entrez syntax was wrong in both directions, and its proposed remedy would have been
   catastrophic corpus-wide. That spec was written by reasoning about an API instead of calling it.
4. **Read the `querytranslation`, not just the count.** A zero-hit result tells you nothing about why.
   Entrez returns how it parsed your query; that field is where the actual defect lives.
5. **Respect rate limits and record them.** NCBI without a key is ~3 req/s. A rate-limited call is a
   FAILED test, not a negative result — say "untested, blocked by rate limit" rather than reporting a
   zero.
6. **Never fetch to circumvent a blocked domain.** If a fetch tool refuses a domain, that is the
   answer; do not route around it with a shell or a library.
7. **Live-service checks belong in the finding**, quoted verbatim: the URL called, the count, the
   `querytranslation`, any `warninglist`. A citation to a doc page is weaker evidence than a call.

---

## B. The F1 ESearch finding — established, do not re-derive

Confirmed against live NCBI on 2026-08-16, twice, independently. Recorded as **CONTRADICTIONS 64**.

**Two things that were WRONG in the original `F1_FABRICATION_GUARD_SPEC.md` and are now corrected in
it:**

- The spec guessed a leading `[` in a translated title made the query malformed. **Entrez tolerates it
  and returns the right paper. There is no bracket defect.**
- The spec proposed quoting the term. **Quoting is catastrophic.** Full titles are not in PubMed's
  phrase index:
  `"The heat of activation and the heat of shortening in a muscle twitch"[Title]` → `count: 0`,
  `warninglist.phrasesnotfound` naming the whole phrase. Quoting every title would zero out nearly
  every search corpus-wide and turn the corpus into apparent fabrications.

**The real defect.** `confirm.py:52` builds `f"{title}[Title]"`. That **is not a title search.**
Automatic Term Mapping binds `[Title]` to the trailing fragment only. Live `querytranslation` for
PMID `18152150` searched on its own exact title, `count: 0`:

```
(("hot temperature"[MeSH Terms] OR ... OR "heat"[All Fields])
 AND ("activable"[All Fields] OR ... OR "activity"[All Fields])
 AND ("shorten"[All Fields] OR ... OR "shortens"[All Fields])
 AND in a[Author])
AND "muscle twitch"[Title]
```

- `[Title]` applied to **`"muscle twitch"` alone** — the last two words.
- **`in a[Author]`** — ATM read the words *"in a"* as an **author surname search**, ANDed against
  MeSH-exploded All Fields terms. The middle of the title silently became an author query.

**Three of the seven regression-guard PMIDs return 0 hits on their own exact titles:**
`16639420`, `18152150`, `27665045`.

**Classification: a RECALL defect, not an honesty defect** — but it is the largest remaining route to a
false F1, because a zero-hit search on a real paper is exactly the input the transport defect turns
into a HIGH-confidence accusation. The new all-three-must-answer rule partly masks it. It does not fix
it.

**No replacement route is chosen, and the loop must not choose one by reasoning.** Candidates, in the
order worth testing:

1. `field=title` as a **request parameter** rather than an inline tag. **UNTESTED** — a rate limit
   blocked the confirming call. **Test this first; it is the cheapest.**
2. `ecitmatch.cgi`, NCBI's purpose-built **citation matcher**, which takes structured
   journal/year/volume/page/author.
3. Crossref/OpenAlex bibliographic query as the primary existence check, with PubMed demoted to
   corroboration.

**Choose on measured hit rate against references known to exist** — the seven regression PMIDs are the
obvious starting sample — **and route the comparison to ZD before landing anything.** Changing how
existence is established changes what F1 means.

---

## C. F1 has been PARTIALLY IMPLEMENTED. Audit the new code, not the spec.

Claude Code implemented most of `F1_FABRICATION_GUARD_SPEC.md` on 2026-08-16, in `~/cre-f3f7`.
**The F1 auditor must read the current code, not the spec's description of the old code.** Several
findings in `F1_F8_AUDIT_2026-08-16.md` are now stale for F1 — verify each before re-raising.

| Defect | State |
|---|---|
| 1 — transport ≠ absence | **Landed.** `FETCH_*` vocabulary in `schema.py`, reusing `fulltext_reader`'s `resolver_error`; carried on `RetrievedRecord.transport_status` and `StageLog.pmid_transport_status`. `decide()` holds on an unanswered fetch. |
| 2 — all-three rule | **Landed**, per ZD. Placed *after* `found_anywhere` so a positive finding needs no complete sweep and F2 recall is untouched. `test_decide_partial_error_still_decides_f1` deleted and replaced. |
| 3 — skipped searches | **Landed.** Skipped searches return `None`, never `0.0`. UNSCOREABLE gate reachable on the dead-PMID branch, scoped to that branch only. |
| 4 — 200-with-error payloads | **Landed**, with per-search shape validation. Entrez's nested `errorlist` deliberately **excluded** — it appears on legitimate zero-hit searches, so treating it as a fault would be the mirror defect. |
| 4b — ESearch term | **NOT fixed.** See §B. |
| 5 — false F2 rationale | **Landed**, closed by the transport fix. |
| 6 — batch abort | **Landed.** Per-reference quarantine in `run.py` matching `judgment_run.py:1501`. |
| 7 — instrumentation | **Landed.** `f1_status` block: attempted / answered / transport_failed / confirm_complete / confirm_incomplete / fired, plus `f1_count`; `counts` seeded so zero F1 is a zero, not a missing key. |
| 8 — governance | **Reported, deliberately NOT acted on.** `GOVERNING_MODULES` unchanged. |

**Nothing was committed**, and no change was made in the `citation-repair-engine` clone.

**Incidental, and worth generalising:** `test_pipeline.py` was monkeypatching `run` and `confirm` at
collection time and never restoring them, leaking into every later test. Pre-existing. **Every auditor
should check its own suite for the same pattern** — a leaking monkeypatch makes every downstream test
result untrustworthy without anyone noticing.

---

## D. New Cost-checker input: `schema.py` is governed

Recorded as **CONTRADICTIONS 65**. `schema.py` **is** in `production_launcher.GOVERNING_MODULES`, and
the F1 fix modified it. Its digest therefore changes, and **any launch pinned to a pre-2026-08-16
manifest will refuse until the digest is re-recorded.**

Note the shape of it: the seven modules that can actually *produce* an F1 — `decide.py`, `lookup.py`,
`confirm.py`, `run.py`, `llm_filter.py`, `unscoreable.py`, `biblio_match.py` — are **ungoverned**. The
one that merely carries the vocabulary is the one that trips the pin.

**The Cost checker must now flag any finding whose fix touches a governed module.** Extending
`GOVERNING_MODULES` is a freeze decision and goes to the Relay, never to an auditor.

---

## E. Seed the rejection register with these, before round 1

So no auditor spends a round re-finding them:

- **REJECTED — "the leading `[` in a translated title makes the ESearch query malformed."** Tested
  live; Entrez tolerates it and returns the right paper.
- **REJECTED — "quote the ESearch term."** Tested live; full titles are not in the phrase index, and
  quoting would zero out the corpus.
- **REJECTED — "treat Entrez's nested `errorlist` as a fault envelope."** It appears on legitimate
  zero-hit searches; treating it as a fault is the mirror defect.
- **DEFERRED, not rejected — `GOVERNING_MODULES` does not cover the F1-producing modules.** Real, and
  deliberately left to ZD.

---

## F. Unchanged, and still binding

Everything in the brief that this amendment does not contradict, in particular: RULE 0; the unanimous-
LAND rule; the SATURATED verdict; the three-clear-rounds stopping condition; no implementation; no
corpus run; no `band_prompts.py` edit; no F2 Part B item; seed 47 is adjudicated at
**74/80 = 0.9250** and `RESERVE_SEEDS` is exhausted.

**Still true and getting riskier:** nothing in `cre-f3f7` is committed. Ten specs, two finding
documents, and now working code, all untracked.
