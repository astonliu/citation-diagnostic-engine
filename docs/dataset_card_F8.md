# CitationRepair-1000 — F8 (Retracted Source) Stratum

## Definition

An F8 instance is a citation to a work that **was already retracted at the time the citing paper was published**. The category is timing-gated: a citation to a paper that is later retracted is not F8 if the citing work predates the retraction notice.

For inclusion in the gold set we operationalize this as: the citing paper was published **≥ 31 days after the retraction notice date**. The 31-day floor is an *annotation-confidence threshold*, not part of the definition — publication date lags submission, so a smaller positive gap cannot reliably establish that the authors could have known of the retraction. The definition is "retracted at citation time"; the 31-day band is how we operationalize confidence in that ordering.

`retraction_date` throughout this stratum refers to the **retraction notice date**, not the original publication date of the retracted paper.

## Labeling procedure (deterministic)

F8 requires no biomedical judgment. Each record is labeled by three mechanical checks:

1. **Retraction status** — the cited paper is flagged retracted via the Retraction Watch database (accessed through Crossref).
2. **Reference-list membership** — the retracted paper's PMID appears in the JATS reference list of the citing paper's PMC full text. This linkage is ground truth; the in-text marker is resolved from the `xref` → `ref` mapping.
3. **Post-retraction ordering** — the citing paper was published ≥ 31 days after the retraction notice.

A record is included in the gold set only if all three hold. The pipeline (`f8_verify_enrich.py`) resolves the citing paper's best PMC version (preferring the version whose reference list cites the retracted PMID), verifies linkage, extracts and clips the citance to the citing sentence, computes the date gap, and auto-assigns one of four dispositions.

## Disposition counts (n = 158 candidate pairs)

| Disposition | n | Meaning |
|---|---|---|
| **accept (gold)** | 140 | Linked, citance confirmed, ordering clears the 31-day floor |
| **exclude (timing)** | 18 | Genuinely cites a retracted paper, but gap < 31 days — ordering indeterminate / plausibly good-faith pre-retraction |
| **reject (label error)** | 0 | Retracted PMID absent from the reference list |
| **review → accept** | (4, included in 140) | Low citance–paragraph overlap; hand-confirmed |

Linkage held at **158/158** — every candidate's retracted PMID was present in the citing paper's reference list — so there were **zero label errors**. The 18 excludes are reported separately from the (empty) reject set: an exclude is a real F8 citation that is not includable on timing grounds, not a labeling mistake. Keeping the two counts distinct is what makes "0 label errors" an honest statement.

## Targeted low-overlap audit

Auto-accepted records whose citance overlapped its host paragraph weakly (sim < 40; n = 5: records 0011, 0056, 0089, 0098, 0109) were hand-audited as the subset where citance extraction could plausibly have grabbed the wrong paragraph. All five were correct F8 labels: the extracted marker resolved to the retracted reference and appeared in the stored citance in every case. Low overlap traced to citance **form**, not to misattribution — one bare-marker citance (`[129]`) and three bulk-list citations. (One of the five, 0056, was independently excluded on timing.) This audit verifies that the deterministic pipeline executed its rules correctly; it is not an independent semantic re-annotation.

## Corpus statistics

Across all 158 processed records, citance quality was tagged to bound how many F8 instances support *substantive* repair versus mere presence-of-citation:

- **Bulk / incidental** (marker appears in a list of ≥ 5 references): 10
- **Thin** (bare-marker citance, < 50 characters of surrounding text): 1

These figures matter because a bulk or thin citance carries little semantic content to repair against — the retracted source is acknowledged but not leaned on argumentatively.

## Known limitations

- **Marker formatting is imperfect.** Extracted markers sometimes retain trailing punctuation ("27.") or surface as author–year strings ("Zhang et al., 2021") rather than a bare number. This is a display/extraction-formatting issue, not a label issue — linkage is established by PMID, not by the marker string.
- **Citances are paragraph-level** for harvest reasons; `cited_reference_marker` was null at harvest and is recovered at verification time. The low-overlap audit bounds the risk this introduces.
- **Year-only citing dates** are handled with worst-case (earliest-possible) ordering: the gap is computed as if the citing paper published on January 1 of its year. A year-only record is accepted only if even that earliest date clears the 31-day floor; all year-only cases in this stratum cleared (minimum 35 days).
