# Data

Every file the repo reads or writes is JSON Lines: one JSON object per line, no
enclosing array. `citation_id` is the join key throughout and has the form
`PMC<digits>:<reference id>`.

## Inputs

**`data/corpus.jsonl`** — the cited-document store for the Sarol et al.
comparison.

| field | type | meaning |
|---|---|---|
| `doc_id` | string | document identifier referenced by `cited_doc_ids` |
| `title` | string | may be empty; an empty title makes a reference unscoreable |
| `abstract` | list of strings | one entry per abstract sentence |

**`data/claims_test.jsonl`** — claims with their gold coverage labels.

| field | type | meaning |
|---|---|---|
| `id` | string | example identifier |
| `claim` | string | the citing sentence |
| `evidence` | object | `doc_id` → list of `{sentences: [int], label: str}` |
| `cited_doc_ids` | list | documents the claim cites |

**PMC JATS XML** — a directory of `PMC*.xml`, fetched by
`script/download-data.sh`. Not committed; the corpus is large and the source is
authoritative.

## Band 1 outputs

**`band1_predictions.jsonl`** — the label, one line per reference:
`citation_id`, `label`, `confidence` (HIGH / MED / LOW), `rationale`,
`evidence` (with `decided_by` and the per-database `db_hits`), and
`annotations`.

**`band1_lossless_log.jsonl`** — the same references with everything the
decision was made from, so a label can be re-derived without re-running the
lookups: `claimed` (title, authors, year, claimed PMID/DOI), `retrieved` (what
the identifier actually resolved to), and `log` (title similarity, author match,
per-database hits, transport status per leg, the LLM filter verdict,
`same_work_reason`, `unscoreable_reason`).

`db_hits` distinguishes `0` from `null`, and this is load-bearing: `0` means the
database answered and had nothing, `null` means it never answered. Only the
first is evidence.

## The join artifact

**`preband_disposition_v2.jsonl`** — `citation_id` → one of `cleared`,
`same_work`, `F1`, `F2`, `F8`, `unverifiable`, `unscoreable`, `human_review`,
plus the resolved identifier and its status. A sidecar `.manifest.json` carries
the file digest, the Band 1 commit, and the corpus manifest it was built
against. Band 2 verifies all three before reading a line.

## Band 2 outputs

**`judgment_predictions.jsonl`** — one line per citation pair:

| field | meaning |
|---|---|
| `citation_id`, `citing_pmcid`, `citing_sentence`, `cited_pmid` | identity |
| `atomic_claims` | the decomposition the coverage judge was asked about |
| `evidence` | the cited text used, and where it came from |
| `route` | `FULL_COVERAGE`, `F6_FLAGGED`, `HELD_LOW_CONFIDENCE`, `NO_CLAIMS`, `PARSE_QUARANTINE` |
| `disposition` | the finer bucket, including every `excluded_*` reason |
| `findings` | the F-numbers raised, in hierarchy order |
| `label` | the reported finding, or empty when the pair was held |
| `terminal_outcome` / `terminal_reason` | the closed-vocabulary answer and why |
| `human_review_required` | whether the record is a work item for a person |
| `record_sha256` / `prev_sha256` | the hash chain over whole records |

**`judgment_band_annotation_queue.jsonl`** — the blind payload for gold
labelling. It holds only pairs that carry a finding: a reviewer confirming "no
claim here" for a citance reading `5,8,10,19` is not a judgment anyone can make.
Pairs where the engine itself failed go to a separate human-review file.

**`run_manifest.json`** — the prompt and parser versions, the module digests
captured before execution, the token and paid-call ledgers, the join accounting,
and the chain tip.
