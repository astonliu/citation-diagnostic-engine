# F3–F7 judgment bench — implementation spec

**Date:** 2026-08-22 · **Repo:** `/Users/kamachi/cre-f3f7` · **Branch:** `merge/f2-into-f3f7`
**Never touch `main`.** Two new files are already on disk, uncommitted and not yet in git:

```
citation_repair_F1_handoff/cre/f1/sandbox_judge.py    # packet -> real engine -> record
citation_repair_F1_handoff/cre/f1/sandbox_wiring.py   # F5/F7 seam construction
```

They were written by the review session, not by you. Read both before changing either.
`sandbox_judge.py` is believed correct; `sandbox_wiring.py` has one known defect (Change 1).

---

## Objective

A bench that runs ONE hand-authored citation pair through the real F3–F7 engine, so a
question like "what does the band do with this claim against this evidence" is answered by
the engine rather than by reading the prompts. It is not a launcher and produces nothing
reportable: no frozen-corpus verification, no pre-band disposition, no hash chain, no join
accounting, no reportability gate. A packet is authored by hand, so its population is one
and no rate can be computed from it.

---

## Change 1 — fix the F7 builder import (known defect, blocks all F7)

`sandbox_wiring.py:176` in `build_f7`:

```python
from .f7_entity import ProductionF7EvidenceBuilder      # WRONG — ImportError
from .f7_evidence_builder import ProductionF7EvidenceBuilder   # correct
```

`f7_entity.py` exports the capability FLAG `PRODUCTION_F7_EVIDENCE_BUILDER` and the note;
the CLASS is `f7_evidence_builder.py:154`. Two different things with near-identical names.
Verified: with this fixed, `--taxonomies F7 --dry-run` proceeds past the import and reaches
the authority hash check.

---

## Change 2 — verify the F7 path against the real authorities

Nobody has run it. The review session had no access to the 5.5 GB ClinVar index.

`FrozenSQLiteAuthorityNormalizer.__init__` opens each `.sqlite` and compares its `metadata`
table against `expected_metadata` built from the snapshot row — `index_schema`,
`entity_type`, `authority`, `version`, `lookup_date`, `accept_synonym_as_equivalent`,
`source_snapshot_sha256`, plus `release_date`. The values `load_authorities` feeds it come
from the authority `manifest.json`. If those two disagree on any field the load raises
`"authority SQLite index does not match its lock"`.

Run this and report the exact error if it raises:

```bash
cd citation_repair_F1_handoff
python3 -m cre.f1.sandbox_judge ../bench/packet_f7_example.json \
  --taxonomies F7 --dry-run \
  --authorities "/Users/kamachi/Library/CloudStorage/GoogleDrive-aston.hliu@gmail.com/My Drive/CitationRepairEngine/f7_authorities"
```

Do not "fix" a mismatch by loosening the comparison or editing a hash. If the manifest and
the index metadata disagree, that is a real finding about the frozen authority build —
report it.

### The authority set, verified present

`My Drive/CitationRepairEngine/f7_authorities/` holds `manifest.json` (schema
`cre_f7_authority_manifest_v2`, `complete: true`, `sqlite_index_builder_commit
84ebb07f`, `lookup_date 2026-08-20`), `build_receipt.json`, four snapshots, and
`sqlite_indexes/` with four indexes:

| entity | authority | snapshot | JSON | index | SQLite |
|---|---|---|---|---|---|
| gene | HGNC | `gene_2026-08-07.json` | 43.6 MB | `gene_2026-08-07.sqlite` | 35.0 MB |
| disease | MONDO | `disease_v2026-07-06.json` | 54.6 MB | `disease_v2026-07-06.sqlite` | 87.7 MB |
| drug | RxNorm | `drug_2026-08-03.json` | 129.5 MB | `drug_2026-08-03.sqlite` | 172.8 MB |
| variant | ClinVar | `variant_2026-08.json` | 4797.2 MB | `variant_2026-08.sqlite` | 5510.6 MB |

`load_authorities` reads every hash from the manifest. Nothing is hardcoded; a re-freeze
changes one file.

---

## Change 3 — the `--verify` default is a claim; confirm it holds

`load_authorities(verify="sqlite")` hashes the four indexes and **does not read the four
JSON snapshots** (5.0 GB, 4.8 of it ClinVar). The argument: each index carries
`metadata.source_snapshot_sha256`, and `FrozenSQLiteAuthorityNormalizer` already compares
it to `source.sha256`, so the snapshot binding is attested by the index. Re-reading the
snapshot re-derives a fact the index states in one row.

Confirm that argument against the code before relying on it. If the metadata comparison
turns out NOT to bind the snapshot, say so and make `all` the default. `verify="none"` must
stay reachable for wiring iteration but must never be the default.

---

## Change 4 — F5 stays bench-mode, and the record must say so

`validate_production_f5_configuration` (`f5_seams.py:800`) gates on
`evidence_builder.production_f5_evidence_builder is True`, a flag whose meaning is
"candidates came from PubMed". On a synthetic case they cannot: `build_pubmed_f5_runtime`
searches live PubMed, which will never return a paper that exists only in a hand-authored
bank — so F5 would report "no supersession" for a reason unrelated to the citation.

`build_f5` therefore wires `build_f5_seams` directly with a bank-backed `search_candidates`
and `fetch_meta`, and **deliberately does not call the production validator**. Everything
else is production's: same `judge_contradiction`, same `verify_contradiction`,
`F5Policy(mode="deployment", deploy_path_a=False)`.

**Do not set the flag to make the validator pass.** That would be a false attestation. Keep
the provenance block the wiring already emits:

```
f5_candidate_source: "bench_paper_bank"
production_validator_called: false
reportable: false
```

F7 is the opposite case and `validate_production_f7_configuration` IS called: nothing about
F7 is synthetic except the body text, which is the same kind of input PMC supplies.

---

## Change 5 — the F7 fulltext contract

`ProductionF7EvidenceBuilder.__call__` (`f7_evidence_builder.py:160`) enforces four things
and returns an EMPTY evidence map — holding `evidence_source_insufficient` — if any fails:

1. `fulltext["pmid"] == item["cited_pmid"]` (raises if bound to a different PMID)
2. `resolved is True` and a non-blank `pmcid`, else `UNRESOLVED:` work id
3. `retrieval_complete is True` — a partial body yields an empty map on purpose, because
   F7 must never infer absence from partial evidence
4. each section's `content_sha256 == sha256(text)`

`sandbox_wiring.fulltext_from_packet` builds that dict and computes the hashes itself. It
must keep computing them — a hand-entered digest silently disables F7 for that packet.
Section labels are restricted to `methods`, `results`, `table`, `figure`; `SectionText`
rejects abstract/intro/discussion.

---

## Change 6 — commit, with the bench kept out of the production surface

Both new modules import from the package but nothing in the package imports them, so the
production path is unaffected. Verify that: grep for any production module importing
`sandbox_judge` or `sandbox_wiring` and confirm zero. Then commit both files plus the two
example packets under `bench/`.

---

## Adapter identity — settled, do not re-decide

- **Model:** `claude-opus-5`, generator and verifier both. Read from batch003's
  `judgment_run_manifest.json`: `f4`, `f5` and `f7` each record
  `generator_model_id == verifier_model_id == claude-opus-5`.
  `test_judgment_run.py:1090` pins the same on the reportable path.
- **The validators test OBJECT identity, not model identity** — `if generator is verifier:
  raise` (`f5_seams.py:828`, `f7_seams.py:1044`). One model, two separately constructed
  callables. The wiring builds two `Anthropic` clients so the roles do not share connection
  state.
- **Temperature is not sent.** `band_prompts.make_anthropic_call` omits the parameter
  because the pinned model rejects it provider-side (DEC-070, first-party HTTP 400,
  request id `req_011Ce3qbp97tLCSVL2rRZtYP`). The bench records
  `TEMPERATURE_UNSUPPORTED`. Do not pin `temperature=0` — that would make the bench answer
  a question about a different adapter than production runs.
- Self-verification on one model is a documented limitation of the production design, named
  in the launch receipt's scope ruling. The bench inherits it; it does not introduce it.

---

## Acceptance matrix

| # | Input | Field | Expected |
|---|---|---|---|
| 1 | `--taxonomies F5 --dry-run`, no API key | exit | 0, plan printed, zero model calls |
| 2 | same | `plan.temperature_recorded` | `"unsupported"` |
| 3 | same | `plan.wiring_provenance.f5` | carries `bench_paper_bank`, `reportable: false` |
| 4 | `--taxonomies F7 --dry-run`, real authorities | exit | 0, `wired_seams` lists the three F7 keys plus `fetch_fulltext` |
| 5 | manifest hash edited to a wrong value | load | refused, message names entity + both hashes |
| 6 | packet with `cited_sections[].label = "discussion"` | load | refused, message names the four legal labels |
| 7 | F7 packet with no `cited_pmcid` | load | refused — the builder treats an unresolved work as no evidence |
| 8 | F3 selected, no `cited_reference_list` | load | refused — provenance is unanswerable without it |
| 9 | F5 selected, no `f5_as_of_date` | load | refused |
| 10 | `f5_candidate_ids` naming an id absent from the bank | load | refused, not silently dropped |
| 11 | any successful run | `record.terminal_outcome` | in the closed vocabulary; `tox.assert_valid` passes |
| 12 | any successful run | receipt summary | every call counted, retries included |

Rows 5–10 are the fail-closed guards. A packet that cannot be judged must be refused, never
answered — an empty record reads as a verdict.

---

## Guardrails (do NOT change)

- **Never synthesize a verdict, label, score or example output.** If a real value cannot be
  produced by running real code, print an explicit not-run state. This is the whole point of
  the bench: a fabricated sample verdict would make it worse than useless.
- Nothing from the bench is reportable. Do not add a reportability path.
- **Claude assigns no semantic labels** and curates no ground truth.
- **Path-based module loading:** no `__init__.py` in `cre/`; restart the Colab session after
  any push. `python3 -m cre.f1.sandbox_judge` from `citation_repair_F1_handoff` is verified
  working.
- **No production file may be modified by this work.** The bench is additive only.
- Do not weaken any hash comparison, and do not edit a hash to make a load pass.
- `rapidfuzz` is required and not preinstalled; reinstall after any runtime reset.

## Regression guards

`31665581`, `16639420`, `18152150`, `27665045`, `25750229`, `32355637`, `22926653` must band
exactly as before — **and print how many of the seven were present in the file under test.**
You established last session that 0 of 7 appear in any code, fixture or corpus artifact, so
treat this as the vacuous guard it is and report the count rather than a green check.

The load-bearing guard here is different: the bench must not change any production behavior.
Suite before and after must have the **identical failure set** — currently 35 failing / 2697
passing, 12 intended contract changes against the true `f8ef323` baseline of 23.

---

## Definition of done

- Change 1 applied; `--taxonomies F7 --dry-run` runs against the real authorities and either
  prints a plan or reports a specific, diagnosed mismatch.
- All 12 acceptance rows verified, or the ones you could not run named explicitly with why.
- Suite failure set identical to before, stated old → new.
- Zero production modules import the bench.
- Committed and pushed to `origin/merge/f2-into-f3f7`; remote SHA equals local.
- Report: what you ran, what you could not run, and the F7 dry-run's actual output.

## Out of scope

- The 408 paid rerun and its deterministic freeze.
- Any change to the parser, the terminal-outcome router, the queue split, or the citation
  selection.
- The six evidence-recovery gates in `F3F7_EVIDENCE_RECOVERY_SPEC.md`.
- Live PubMed candidate retrieval for F5. Named and deferred, not dismissed.
- Making the bench reportable. It is not, by construction.

## Verification command

```bash
python3 -m pytest citation_repair_F1_handoff/cre/f1 -q
```
