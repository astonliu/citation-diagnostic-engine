# F1 — thread an OpenAlex API key through every OpenAlex leg — implementation spec

**Date:** 2026-08-24
**Branch:** `merge/f2-into-f3f7`
**Engine commit this was measured against:** `5602a3b46ba7785a5ae88210486020b985a3b3a0`

---

## Objective

`confirm.fully_answered` requires PubMed **and** Crossref **and** OpenAlex to each return a real score
before `decide()` may license an F1. OpenAlex now requires an API key; without one an account gets
**$0.10 of usage per day** and then `409`. `confirm.search_openalex` can only send `mailto`, so it has no
way to authenticate, and once the allowance is spent it returns `None` for every reference — which makes
**F1 structurally unreachable for the whole corpus**.

**Current behavior:** OpenAlex answers for the first handful of references in a run, then stops. F1 = 0
regardless of what the corpus contains.

**Target behavior:** every OpenAlex request carries an optional `api_key` query parameter, threaded from
the caller. With a key configured, OpenAlex answers for the whole run and F1 becomes reachable. With no
key configured, behavior is byte-identical to today.

### The measurement that motivates this — do not re-derive it

Two `provider_probe.json` files from 2026-08-24, same probe title, same run:

| probe | pubmed | crossref | openalex | `fully_answered` |
|---|---|---|---|---|
| earlier | 36.22 | 74.07 | **100.0** | **True** |
| later | 36.22 | 74.07 | **None** | **False** |

PubMed and Crossref returned identical scores in both, so the probe is deterministic and OpenAlex is the
only moving part. The partial run's **F1 = 0 over 8,009 references is an artifact of API access, not a
base rate.**

Pricing, read 2026-08-24 from `developers.openalex.org/guides/authentication` and
`help.openalex.org/access/example-costs/`: no key = **$0.10/day**; free key = **$1/day**. A
`list + filter` call costs **$0.0001**; a `search` call costs **$0.001**. The key is a query parameter:
`api_key=YOUR_KEY`.

---

## Change / defect

Three distinct OpenAlex legs exist and they are **not** equivalent. Change all three, but understand
which one gates F1.

### Leg A — `cre/f1/confirm.py:159` `search_openalex` — THE F1 GATE

```python
params = {"filter": f"title.search:{title}", "per-page": 3}
if mailto:
    params["mailto"] = mailto
```

- Add keyword-only `api_key: str = ""`; set `params["api_key"] = api_key` when non-empty.
- `confirm.confirm` (`confirm.py:186`) gains keyword-only `openalex_api_key: str = ""` and passes it in
  the `calls` dict at `confirm.py:203-204`.
- **Volume and cost:** fires only on flagged survivors. Measured 530 `llm_filter` calls over 8,009
  references, so roughly 6,000 calls for a 92,000-reference run at the **$0.0001 filter tier ≈ $0.60**.
  A free key covers this leg comfortably.

### Leg B — `cre/f1/biblio_match.py:1553` `_openalex_candidates` — HIGH VOLUME, EXPENSIVE TIER

```python
resp = request_with_retry(session, OPENALEX_URL,
                          {"search": claimed.title, "per-page": n},
                          limiter=OPENALEX, timeout=20)
```

- Sends **no `mailto` at all** today, so it is fully anonymous.
- Add keyword-only `api_key: str = ""` (and `mailto: str = ""`); thread through
  `retrieve_candidates` (`biblio_match.py:1575`) → `lookup.fuzzy_biblio_lookup` (`lookup.py:334`) →
  `lookup.compare_and_flag` (`lookup.py:452`) → `run.process_reference` → `run.run`.
- **It uses `search=`, the $0.001 tier, and fires on every PMID-less reference.** At roughly half of
  92,000 references that is ~46,000 calls ≈ **$46**, well past a $1/day free allowance.
- **Do NOT "fix" this by switching `search=` to `filter=title.search:` to save money.** That changes
  which candidates are retrieved, which changes F2 and F1 outcomes. It is a judgment change, not a
  billing change. If cost forces it, it is a separate spec with its own before/after diff.

### Leg C — `cre/f1/doi_lookup.py:178` `_openalex` and `:248` `fetch_openalex_abstract`

- `_openalex(doi, s)` is part of the `lookup_exact_doi` provider set (`doi_lookup.py:305`); add
  `api_key` and thread from `lookup_exact_doi`'s caller in `run.process_reference`.
- `fetch_openalex_abstract` already takes `mailto`; add `api_key` and thread from
  `production_launcher._openalex_abstract_seam` (`production_launcher.py:819-826`). This is a Band-2
  seam and is not on the Band-1 path, but leaving it unkeyed leaves a silent "no abstract" failure mode.

### Leg D — the entrypoints

- `run.run` and `run.process_reference` gain `openalex_api_key: str = ""`.
- `production_launcher.launch_full` and `launch` gain `openalex_api_key: str = ""` beside the existing
  `openalex_mailto`, and pass it to `run_band1` (`production_launcher.py:982-987`) and to
  `_openalex_abstract_seam` (`:1003`).

### The correctness requirement that outranks all of the above

**A spent allowance must never read as "found nothing."**

- `confirm._json_or_none` (`confirm.py:60`) already returns `None` for any non-200, so a `409` becomes
  an unanswered search and `fully_answered` correctly refuses F1. **Do not add `409` to
  `ratelimit._RETRY_STATUS`** — a spent daily quota is not transient and retrying it burns the run.
- `_openalex_candidates` returns `[]` **and** appends `"openalex_candidates"` to `errors` on a fault.
  **Verify that `errors` is actually propagated** from `retrieve_candidates` → `fuzzy_biblio_lookup` →
  the reference's log, and report whether it is. If a quota-blocked OpenAlex can reach `decide()`
  indistinguishable from a healthy empty result, say so and stop — that is the same class of defect as
  [[CONTRADICTIONS]] entry 63 and it must not be fixed silently.

### Telemetry required by this change

Log OpenAlex calls per leg with their HTTP status, so the daily allowance is observable rather than
inferred after a run dies. One counter per leg (`confirm`, `candidates`, `doi`, `abstract`) recorded into
the run manifest is sufficient.

---

## Acceptance matrix

| Input / fixture | Field | Expected |
|---|---|---|
| `search_openalex(title, mailto="m", api_key="")` | request params | no `api_key` key present |
| `search_openalex(title, mailto="m", api_key="K")` | request params | `api_key == "K"`, `mailto == "m"` |
| `search_openalex`, recorded 200 fixture, key set | return | same float as the pre-change code |
| `search_openalex`, recorded **409** fixture | return | `None` |
| `confirm(...)` with a 409 OpenAlex fixture | `fully_answered(hits)` | `False` |
| `confirm(...)` with a 409 OpenAlex fixture | `decide()` label | **never `F1`** |
| `_openalex_candidates(..., api_key="K")` | request params | `api_key == "K"` |
| `_openalex_candidates`, 409 fixture | return, `errors` | `[]` **and** `"openalex_candidates"` appended |
| `fuzzy_biblio_lookup`, 409 OpenAlex fixture | reference log | carries the `openalex_candidates` error tag |
| `run.run(..., openalex_api_key="K")`, recorded transports | every OpenAlex request | carries `api_key` |
| `run.run(...)` with `openalex_api_key=""` | every OpenAlex request | byte-identical to pre-change |
| `launch_full(..., openalex_api_key="K")` | `_openalex_abstract_seam` request | carries `api_key` |
| Any completed run | manifest | per-leg OpenAlex call counts and status tallies present |

---

## Guardrails (do NOT change)

- **No judgment change.** No prompt, threshold, matcher rule, parser or decision path moves. With
  `openalex_api_key=""` every request must be byte-identical to today — the key is a billing and
  authentication parameter, nothing else.
- **Leg B stays on `search=`.** Changing it to `filter=title.search:` is a retrieval change and is out
  of scope, however tempting the 10× cost difference is.
- **A provider fault stays a fault.** Never let a `409`, `429` or non-200 become evidence of absence.
  `fully_answered`'s all-three bar is not to be relaxed to two-of-three to work around cost.
- `band_prompts.py` not edited; its blob OID is sealed into both frozen prompt packages.
- **No-rewrite discipline**: targeted amendments only.
- Path-based module loading; no `__init__.py` in `cre/`; **after pushing, restart the Colab session** —
  `importlib.invalidate_caches()` does not evict already-imported modules from `sys.modules`.
- `author_match` tri-state: `None` is unknown; test with `is False`.
- **F2 recall is non-negotiable in the matcher.** Leg B feeds F2 candidate retrieval — do not let a cost
  concern narrow that population.
- F2 evaluation stays precision-only.
- Claude assigns no semantic labels and curates no ground truth.
- Never use the detector's own flags as gold.

---

## Regression guards

These must band exactly as before, with `openalex_api_key=""` and with a key set:
`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653`.

Re-run one completed Band-1 batch from cache with `openalex_api_key=""` and diff per-reference verdicts
against the stored `band1_lossless_log.jsonl`. **Any difference fails.**

---

## Definition of done

- Full test suite passes; state the count as old → new.
- Every acceptance-matrix row verified on fixtures, including the 409 rows.
- The `errors`-propagation question above answered explicitly in the commit message: does a
  quota-blocked OpenAlex reach `decide()` distinguishably from a healthy empty result, yes or no.
- A cached-batch re-run with no key produces byte-identical per-reference verdicts.
- Pushed to `merge/f2-into-f3f7`. `main` untouched at `d090ab7`.

---

## Out of scope

- Switching leg B from `search=` to `filter=title.search:` (a retrieval change; separate spec).
- Any Crossref, DataCite, bioRxiv or NCBI change. None of them needs a key — Crossref's polite pool is
  `mailto`-only and the engine already sends it.
- Adding a paid OpenAlex plan, or any budget-throttling logic. Measure first; throttle in a later spec
  if the counters show it is needed.
- Any F3–F8 decision logic, prompt wording, or claim-extraction behavior.
- Re-running the corpus. ZD does that after this lands.

---

## Verification command

```
cd citation_repair_F1_handoff
PYTHONPATH=. python -m pytest cre/f1 -q
```

Then, live, with the key in the environment:

```python
from cre.f1 import confirm as c
h = {"pubmed":   c.search_pubmed("The Hallmarks of Cancer", NCBI_KEY),
     "crossref": c.search_crossref("The Hallmarks of Cancer", EMAIL),
     "openalex": c.search_openalex("The Hallmarks of Cancer", EMAIL, api_key=OA_KEY)}
print(h, c.fully_answered(h))   # must be True, and must STAY True late in a run
```
