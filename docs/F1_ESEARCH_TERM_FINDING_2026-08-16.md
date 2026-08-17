# F1 Defect 4 — live-NCBI finding on the ESearch term

**Date:** 2026-08-16 · **Reported to:** ZD · **Repo:** `cre-f3f7`, branch `feat/f3-f7-semantic-validator-v1`
**Spec:** `F1_FABRICATION_GUARD_SPEC.md`, Defect 4 — *"Verify against live NCBI before fixing, and
report what you find."*

**Bottom line:** the audit's hypothesis is **not confirmed**, the spec's prescribed remedy is
**actively dangerous**, and the probe found a **different and larger defect** in the same line of code.
Per the standing method, the query construction was **left unchanged** and is reported here instead.

---

## What was asked

The audit flagged, as *high-suspicion but NOT established*, that `confirm.py:52` builds the ESearch
term as `f"{title}[Title]"`, and that for a bracketed translated title the leading `[` is Entrez
field-tag syntax and so the query is *likely malformed* — with every plausible response landing on
`0.0`, i.e. counting as fabrication evidence.

## Method

Live `esearch.fcgi` / `esummary.fcgi` / `efetch.fcgi`, keyless, 2026-08-16. Bracketed titles were
taken from real PubMed records (`statins[Title] AND fre[Language]`) rather than invented, and each
query variant was compared against the record it should retrieve.

---

## Finding 1 — a leading `[` is NOT malformed. Hypothesis not confirmed.

Entrez tolerates the bracket. It reports it in `warninglist.outputmessages` as `['[', ']']`, drops
the bracket characters, and runs the query normally.

| term | result |
|---|---|
| `[Myalgia and statins: Separating the true from the false].[Title]` | **1 hit — its own PMID, 31473026** |
| `[Effect of statins on lipid levels].[Title]` (the spec's own example) | 113 hits, HTTP 200 |

A bracketed translated title is retrieved correctly today. **No fix is needed for this, and none was
made.**

## Finding 2 — the prescribed remedy would be catastrophic. Do not quote titles.

The acceptance matrix asks for the term to be `quoted/escaped`. Measured: **full article titles are
not in PubMed's phrase index**, so quoting turns working searches into zero-hit searches.

| term | hits |
|---|---|
| `Purple Urine after Catheterization[Title]` (unquoted, PMID 31665581) | **1 — correct** |
| `"Purple Urine after Catheterization"[Title]` | **0** |
| `Aspirin in the primary and secondary prevention of vascular disease[Title]` | 14 |
| `"Aspirin in the primary and secondary prevention of vascular disease"[Title]` | **0** |
| `"the berlin definition of ards"[Title]` | **0** |
| `"purple urine"[Title]` (short phrase — quoting works fine) | 214 |

Quoting is safe for short phrases and fails for whole titles. Had this been implemented as written,
**nearly every confirmation search would have returned `0.0`** — "searched, found nothing" — across
the entire corpus. That is the exact failure mode this spec exists to prevent, applied to every
reference at once rather than only during an outage.

The term is therefore **deliberately left unquoted**, and that decision is pinned by
`test_matrix_bracketed_translated_title_is_deliberately_not_quoted` so it is not "fixed" back.

## Finding 3 — the real defect: `f"{title}[Title]"` is not a title search

Entrez Automatic Term Mapping splits the string. **Only the trailing fragment binds to `[Title]`**;
everything before it becomes an AND-ed chain of All-Fields / MeSH expansions. From the live
`querytranslation` for the aspirin control:

```
(("aspirin"[Supplementary Concept] OR "aspirin"[All Fields] OR ...)
  AND ("primaries"[All Fields] OR "primary"[All Fields])
  AND ("secondary prevention"[MeSH Terms] OR ...))
AND "vascular disease"[Title]          <-- the ONLY title-bound clause
```

That AND-chain gets brittle as titles lengthen. Replaying `search_pubmed` against **the seven
regression PMIDs named in this spec**, using each paper's own exact title:

| PMID | hits | scored |
|---|---|---|
| 31665581 | 1 | 100.0 found |
| **16639420** | **0** | **0.0 — scores as NOT FOUND** |
| **18152150** | **0** | **0.0 — scores as NOT FOUND** |
| **27665045** | **0** | **0.0 — scores as NOT FOUND** |
| 25750229 | 1 | 100.0 found |
| 32355637 | 1 | 100.0 found |
| 22926653 | 1 | 100.0 found |

**Three of seven real, indexed papers return zero hits when searched by their own exact titles.**
Truncating to the first 8 words recovers 16639420 and 27665045 exactly (1 hit, self found);
18152150 returns 65 hits with itself outside the top 3. Sorting by relevance does not help — the
0-hit cases are 0 hits, not mis-ranked ones.

### Why this matters for F1

The PubMed leg of the confirmation search **fails toward accusation** on long titles: a real paper
scores `0.0`, which `decide()` reads as "searched, answered, found nothing" — genuine fabrication
evidence under the corrected semantics. It is a **recall** defect, not an honesty defect: the search
*was* issued and *did* answer, so it does not violate the "0.0 must mean searched, found nothing"
rule this spec establishes. But it is the largest remaining route to a false F1.

**Partially mitigated as of this pass.** Crossref (`query.bibliographic`) and OpenAlex
(`title.search`) use real title queries and do not share this flaw, and F1 now requires **all three**
searches to answer and come back empty (ZD, 2026-08-16). A long-titled real paper therefore needs
Crossref *and* OpenAlex to also miss it before F1 can fire.

## What was NOT changed, and why

The query construction feeds `confirm()`, which serves the **F2** population as well as F1. Changing
what it finds moves F2, and the spec's guardrails are explicit: *"F2 recall is non-negotiable in the
matcher"* and *"No-rewrite discipline: targeted amendments only."* Reworking it belongs in its own
pass with its own regression measurement, not inside the F1 fabrication guard.

**ZD's call, 2026-08-16: report only this pass.** The brief below is the handoff.

---

## Copy-and-paste brief for the next session

> **Task: fix the PubMed ESearch term construction in `cre/f1/confirm.py` (CRE, branch
> `feat/f3-f7-semantic-validator-v1`, repo `/Users/kamachi/cre-f3f7`).**
>
> **Context.** `search_pubmed` builds its query as `f"{title}[Title]"` (`confirm.py:52`). This is not
> a title search. Entrez Automatic Term Mapping splits the string and binds `[Title]` to the
> **trailing fragment only**; everything before it becomes an AND-ed chain of All-Fields/MeSH
> expansions. Confirmed live against NCBI on 2026-08-16 by reading `querytranslation`.
>
> **The defect.** The AND-chain becomes unsatisfiable as titles lengthen. Replaying `search_pubmed`
> on the seven F1 regression PMIDs using each paper's own exact title, **three return zero hits**:
> `16639420`, `18152150`, `27665045`. (`31665581`, `25750229`, `32355637`, `22926653` return 1 hit
> and score 100.0.) A real, indexed paper scoring `0.0` is read by `decide()` as evidence that the
> work does not exist, so this is a live route to a false F1 — the one label whose false positive is
> a public accusation.
>
> **Two things that are already settled — do not redo them.**
> 1. A leading `[` on a bracketed translated title is **not** malformed. Entrez tolerates it, warns
>    `outputmessages: ['[', ']']`, and returns correct hits. Not a defect.
> 2. **Do not quote the title.** Full article titles are not in PubMed's phrase index.
>    `"Purple Urine after Catheterization"[Title]` returns **0** hits while the unquoted form returns
>    the correct 1. Quoting whole titles would zero out nearly every confirmation search corpus-wide.
>    There is a test pinning the unquoted form —
>    `test_f1_fabrication_guard.py::test_matrix_bracketed_translated_title_is_deliberately_not_quoted`.
>    If you change the term, update that test's rationale rather than deleting the reasoning.
>
> **Constraints.**
> - `confirm()` feeds **F2** as well as F1. **F2 recall is non-negotiable** — measure the F2
>   population before and after, do not just check the suite is green.
> - `0.0` must keep meaning "searched, answered, found nothing"; `None` must keep meaning "no answer".
>   Do not make a weak query return `None` — that would suppress true positives.
> - Targeted amendment only; no rewrite of `confirm.py`.
>
> **Definition of done.** All seven regression PMIDs retrieve themselves from their own exact titles
> and score >= the 85.0 match threshold; the F2 population is measured old vs new and reported; suite
> green with the old→new count and the environment stated (`anthropic` and `jsonschema` change the
> number).
>
> **Starting data.** Truncating to the first 8 title words recovers `16639420` and `27665045` exactly
> (1 hit, self found) but leaves `18152150` at 65 hits with itself outside the top 3 — so word-count
> truncation alone is not sufficient, and it is a heuristic rather than a principled fix. Note also
> that ESearch's default sort is **most-recent, not relevance**, while the code reads only
> `retmax=3` — a broad query can therefore return the three newest hits rather than the three best
> matches. Consider `sort=relevance` and/or a larger `retmax` as part of the fix.
