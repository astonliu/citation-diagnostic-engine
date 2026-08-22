# How to test a case yourself

You hand the engine **one JSON packet** describing one citation. Two ways to submit it: a CLI
command, or a local web UI. Both run the same production code path.

Everything runs from `citation_repair_F1_handoff/`, with `PYTHONPATH=.`:

```bash
cd /Users/kamachi/cre-f3f7/citation_repair_F1_handoff
export PYTHONPATH=.
PY=/Users/kamachi/citation-repair-engine/.venv_cre/bin/python3
export ANTHROPIC_API_KEY="$(cat ~/.cre_bench_key)"
```

---

## 1. Validate for free first — always do this

```bash
$PY -m cre.f1.sandbox_judge mycase.json --dry-run
```

Zero model calls, no cost. It resolves the wiring and prints the plan plus `wired_seams`. If a
field is missing or malformed you get a `[sandbox-packet-error]` here instead of after paying.
**A packet that dry-runs clean is the only one worth spending money on.**

## 2. Run it

```bash
$PY -m cre.f1.sandbox_judge mycase.json                      # F3 / F4 / F5 / F6
$PY -m cre.f1.sandbox_judge mycase.json --authorities DIR    # F7 also needs this
$PY -m cre.f1.sandbox_judge mycase.json --taxonomies F5      # override the packet
```

Cost, measured on the three sample cases: **F3 ~$0.10, F7 ~$0.12, F5 $1.10–1.80.**
F5 is the expensive one because it deep-compares every admissible candidate.

## 3. Or use the web UI

```bash
$PY -m cre.f1.sandbox_server --port 8781
```

Opens a browser page where you paste the packet and see the record. Same engine, same cost.

---

## Writing the packet

Start from **`bench/TEMPLATE_packet.json`** — it is annotated per taxonomy and every `_comment_*`
key is ignored by the loader. Or copy a working sample:

| taxonomy | sample | what it tests |
|---|---|---|
| F3 provenance | `bench/packet_f3_icuaw_provenance.json` | cited a review for a finding from a primary trial in its own reference list |
| F5 supersession | `bench/packet_f5_egdt_supersession.json` | EGDT: later trials nulled the effect (`decrease` -> `no_effect`) |
| F7 wrong entity | `bench/packet_f7_metformin_wrong_drug.json` | claim says aspirin, paper studied metformin |

### The four fields every packet needs

`citation_id`, `citing_sentence`, `cited_claimed` (with `claimed_pmid`), `cited_abstract`.

### Per-taxonomy additions

- **F5** — `cited_pub_date`, `cited_mesh_terms`, `f5_as_of_date`, and `f5_live_discovery: true`
  to search live PubMed. Supply `f5_candidates` instead if you want to hand-feed the candidate set.
- **F7** — `cited_pmcid`, `cited_sections` labelled only `methods` / `results` / `table` /
  `figure`, and `--authorities` pointing at a folder with `manifest.json`, the four snapshots and
  `sqlite_indexes/`. **The claimed and evidence entities must both exist in that authority set**
  or F7 abstains as `normalization_ambiguous` — that is the single most common way a hand-written
  F7 case fails.
- **F3** — `cited_pmcid`, `cited_is_review`, `cited_reference_list` (the paper's REAL
  bibliography), and an `abstracts` map from PMID to abstract. Without abstracts every candidate
  origin holds and no F3 can be reached.

### Two rules that will bite you

1. **Spans are checked verbatim.** F5 and F7 verify quoted text character-for-character against
   `cited_sections` / `cited_abstract`. A paraphrase, a smart quote, or an en-dash turned into a
   hyphen makes the span unresolvable and the candidate unassessable — for a reason that has
   nothing to do with the science. Copy/paste from PubMed or PMC, never retype.
2. **Write the sentence at the right strength.** The ladder reports the highest-ranking fault, so
   an overstated claim returns F4 even when the provenance fault you meant to test is also found.
   `bench/packet_f3_icuaw_provenance_plus_strength.json` is the same pair as the F3 sample with
   "prevents" instead of "is associated with": it returns `findings: ['F4','F3']` and
   `terminal_outcome: F4`. Check `findings`, not just `terminal_outcome`.

---

## Reading the result

```jsonc
{
  "record": {
    "terminal_outcome": "F5",          // highest-ranking fault
    "findings": ["F5"],                // EVERYTHING that fired -- read this too
    "coverage_verdicts": [ ... ],      // if established:false, F6 fired and may mask others
    "f5_records": [ ... ],             // per-claim audit: candidates, screen, deep comparisons
    "human_review_required": false
  },
  "token_usage": {                     // what it actually cost, from response.usage
    "total": { "cost_usd": 1.38, "cache_read_input_tokens": 86868 },
    "by_stage": { "f5_generator": {...}, "f5_candidate_screen": {...} }
  }
}
```

For F5, the useful sub-fields inside `f5_records[0]`:

- `cost_stage_counts` — candidates retrieved / admissible / screened / deep-compared
- `candidate_assessments[]` — one row per candidate, each with `screen_decision`,
  `deep_comparison_rank` (the order the budget was spent), `reason`, and the judged directions
- `discovery_disposition`, `verifier_result`, `confidence`

## Cost knobs for F5

| key | default | effect |
|---|---|---|
| `f5_candidate_cap` | 50 | how deep retrieval goes. 200 recovers refuters cap 50 misses. |
| `f5_max_deep_comparisons` | none | budget. **Set this whenever you raise the cap** — cap 200 unbudgeted is ~4x cost; cap 200 with budget 25 is +30%. |
| `f5_candidate_screen` | true | the batched triage. Its real value is ordering: the budget is spent on the candidates it flagged `opposes` first. Set `false` to compare screened vs unscreened cost. |

## Nothing from this is reportable

Every record carries `reportable: false` and the provenance block says why. A hand-authored packet
is a population of one; use it to interrogate behaviour, never to produce a figure.
