# F7 debugging handoff — the multi-entity locator bug

**Written** 2026-08-22 · **Branch** `merge/f2-into-f3f7` (worktree `/Users/kamachi/cre-f3f7`, shared)
**State** one uncommitted production edit in `cre/f1/f7_entity.py`, **confirmed through the real
module 2026-08-22** (§10). Still uncommitted, pending the user's word.

---

## 0. Orientation, in one paragraph

F7 asks: *the citing sentence attributes a relation to entity X; does the cited paper's own results
actually concern a different entity Y?* It fires only when it can name Y from an authority snapshot
and show the paper reports the relation about Y, not X. It is the most expensive and most
precision-critical seam in Band 2, and it was **structurally unable to fire on any sentence naming
more than one entity** — which is nearly every real sentence. This document is the debugging record
of that bug and its fix.

Everything below runs from `/Users/kamachi/cre-f3f7/citation_repair_F1_handoff`. `cre` is a
namespace package with no `__init__.py` at the `cre/` level; **it will not import from the repo
root.** Python is `/Users/kamachi/citation-repair-engine/.venv_cre/bin/python3`.

---

## 1. The test case

A synthetic pair, chosen by the user because the fault is unambiguous — the citing sentence names
the wrong drug:

| | |
|---|---|
| **Claim** (citing sentence) | `Aspirin increases recovery in patients with lung cancer to up to 30%.` |
| **Evidence** (cited paper, results) | `Metformin increases recovery in patients with lung cancer to up to 30%.` |

The correct verdict is **F7**: the paper is about metformin, the citation says aspirin, and the
proposed correction is `metformin` / `RxNorm:6809`.

The user's **original phrasing** was `Aspirin is attributed to a 30% recovery rate in lung cancer
patients`. That wording never got as far as the bug — see §5, gate 2. The packet was reworded across
`aspirin_f7.json` → `f7b` → `f7c` precisely to walk past each earlier gate. **Re-running the
original phrasing and reporting honestly what it does now is an outstanding task** (§8).

Packets live in the scratchpad (§3). The claim names **two** entities — a drug *and* a disease —
and that is the whole point of the case.

---

## 2. The bug

`F7_EVIDENCE_PROMPT` (`cre/f1/f7_entity.py`, ~line 845) is the schema-C locator: given ONE claimed
entity, find the counterpart entity the cited paper's results concern. It was given the claimed
**surface** but never the claimed **type**.

So on a two-entity claim the engine issued two locator calls and got the paper's *main subject* back
both times:

```
tuple 1  claimed drug    "Aspirin"      ->  evidence drug    "metformin"     ✓ correct pairing
tuple 2  claimed disease "lung cancer"  ->  evidence drug    "metformin"     ✗ cross-ontology
```

Tuple 2's comparator was asked to relate `MONDO:0008903` to `RxNorm:6809`. Nothing can:
`f7_seams.py:403` will not call two ids from different authorities distinct, and rightly so. The
tuple returned `UNJUDGEABLE / cross_comparator_unavailable`.

Then the roll-up at `f7_entity.py:1475` — **no cherry-picking**, deliberate and correct — holds the
entire claim if *any* tuple is unjudgeable:

```python
unjudgeable_tuples = [o for o in outcomes if o[1] == "UNJUDGEABLE"]
if unjudgeable_tuples:
    reason = sorted(unjudgeable_tuples, key=lambda o: o[0])[0][2]
    return unjudgeable(reason)
```

Net effect, from `run_f7d.json`: tuple 1 reached `CONFIRMED_MISMATCH` with
`proposed_corrected_label: "metformin"`, `proposed_corrected_id: "RxNorm:6809"`, and a verifier
answering **true on all five checks** — and the claim was still held. `terminal_outcome: F6`,
`findings: ['F6']`. The engine found the fault, proved it, and threw it away.

**This is a precision-shaped bug that reads as a recall bug.** Nothing was ever labelled wrong; F7
simply could never speak. On the judge bench that looks like "F7 has no yield," which is exactly the
wrong conclusion to draw.

---

## 3. Where things are

**Repo** `/Users/kamachi/cre-f3f7/citation_repair_F1_handoff`

| Path | Role |
|---|---|
| `cre/f1/f7_entity.py` | the seam. **Modified, uncommitted.** |
| `cre/f1/f7_seams.py:403` | why distinct RxNorm ids are not proof of distinctness |
| `cre/f1/sandbox_server.py` | local runner, `/api/run` |
| `cre/f1/sandbox_wiring.py:87` | `load_authorities` — re-hashes every snapshot **and** index on every run |
| `cre/f1/sandbox_judge.py` | `judge()` — what `/api/run` calls |

**Scratchpad**
`/private/tmp/claude-508/-Users-kamachi-cre-f3f7/c2fd1c5d-3470-4e32-8242-924bdeb75dcb/scratchpad`

| File | What it is |
|---|---|
| `f7_authorities/` | **hand-built 132K authority set** — see §4 |
| `aspirin.json` | band-only packet, original phrasing, no F7 |
| `aspirin_f7.json` `…f7b` `…f7c` | successive rewordings; **`f7c` is the live one** |
| `run_f7.json` … `run_f7d.json` | the four pre-fix gate results (§5) |
| `run_f7_patched.json` | the monkeypatched proof the fix works (§6) |
| `server.log` | access log of the still-running server |

---

## 4. The authority set — read this before you touch F7

F7 refuses to run without four frozen authorities: `gene`/HGNC, `variant`/ClinVar, `drug`/RxNorm,
`disease`/MONDO. Each is a JSON snapshot **plus** a SQLite index, every one sha256-pinned in
`manifest.json`. The real set is ~16.6 GB and is re-hashed on **every** run (~12s), with no cache,
because **the hash is the gate** — caching it would be caching away the thing being checked.

The scratchpad set is a **minimal hand-built stand-in**, 132K, four records total. It exists so this
one pair can be run at all. Its `sqlite_index_builder_commit` is the string
`"bench-minimal-rxnorm"` — a deliberate tell that this is not a real build.

Two things about it that matter:

- `drug.json` carries an **explicit relation** `RxNorm:1191 (aspirin) —provably_distinct→
  RxNorm:6809 (metformin)`. Without it, gate 1 fires: RxNorm has hierarchy, so two distinct ids are
  *not* self-evidently distinct entities. Writing that relation in by hand is a curation act; it is
  fine for a mechanism test and **is not ground truth**.
- `ijson` was missing from the venv and no requirements file pins it anywhere.
  `build_frozen_sqlite_authority_index` needs it. `ijson 3.5.1` was installed. **That install is
  also unreverted state** — it fixed the six `test_f7_sqlite_authority.py` failures.

---

## 5. The four gates, in order

Each rejection was correct given its input, and each one taught something. Recorded so nobody
re-walks them:

| # | Result file | Held on | Why |
|---|---|---|---|
| 1 | `run_f7.json` | `relation_unknown` | distinct RxNorm ids ≠ distinct entities. Fixed by writing the explicit relation into `drug.json`. |
| 2 | `run_f7b.json` | `relation_mismatch` (`predicate`) | *"is attributed to"* vs *"increases recovery rate"*. F7 requires **all four** of predicate/object/direction/population to match. Fixed by rewording the claim. |
| 3 | `run_f7c.json` | `relation_mismatch` (`object`) | *"30%"* vs *"30% vs 18% control"*. Fixed by simplifying the results text. |
| 4 | `run_f7d.json` | `cross_comparator_unavailable` | **the actual bug** (§2). |

Gates 1–3 are the engine being strict, not broken. Gate 4 is the defect.

---

## 6. The fix

Three edits, all in `cre/f1/f7_entity.py`, all uncommitted. `git diff cre/f1/f7_entity.py` shows
+17/−2.

1. **`:316`** `evidence_prompt_version: "f7_evidence_v1"` → **`"f7_evidence_v2"`**. This field is
   covered by `policy_sha256(policy)`, so every `f7_record` written from here on records that the
   prompt changed. Bumping it is not optional.
2. **`F7_EVIDENCE_PROMPT` (~`:845`)** — header now says locate the entity **of the same type**, and a
   new first rule states the type constraint and *why*: a real claim names several entities and each
   is located separately in its own request. New placeholder line `CLAIMED ENTITY TYPE: <<CLAIM_TYPE>>`
   above `CLAIMED ENTITY SURFACE:`.
3. **`:1235`** call site supplies `"<<CLAIM_TYPE>>": claimed_type`, with a comment recording the
   failure it prevents.

**Deliberately not changed, and do not change without deciding it explicitly:**

- the roll-up at `:1475` (no cherry-picking). Relaxing it is a **precision decision**, not a bug fix.
- `R_CROSS_UNAVAILABLE = "cross_comparator_unavailable"` — pinned by `test_f7_entity.py:484`.

### Proof so far — monkeypatch only

`run_f7_patched.json`, in-process with the prompt patched in:

```
tuple 1  drug/Aspirin      -> drug/metformin      [RxNorm:1191 vs RxNorm:6809]
         compare_relation provably_distinct  ->  CONFIRMED_MISMATCH
         proposed fix: metformin / RxNorm:6809 ; verifier all five checks true
tuple 2  disease/lung cancer -> disease/lung cancer [MONDO:0008903 vs MONDO:0008903]
         compare_relation equivalent  ->  SAME_ENTITY
claim    derived DIFFERENT_ENTITY_SUPPORTED / different_entity_supported
record   terminal_outcome F7 · findings ['F7','F6'] · label F7
paid_calls {total: 9, retries: 0, by_stage {claim_extraction:1, coverage:1, F7:6, F7_verifier:1}}
```

Identical cost to the failing run — the fix buys a verdict, not calls.

### Test state

Full suite after the edit: **12 failed, 2738 passed, 12 skipped, 34 xfailed**. Was 18 before
installing `ijson`. **No new failures.** The 12 are pre-existing and unrelated:
`test_adversarial_judgment_run`, `test_cocitation_f6`, `test_f5_evidence_store`,
2× `test_f7_orchestrator_wiring`, `test_f8_retraction_gate`, `test_judgment_run`,
3× `test_judgment_run_fulltext_wiring`, 2× `test_live_paths`.

---

## 7. Reproducing

The server at PID 38819 on `http://127.0.0.1:8781/` **was started before the edit and holds the
pre-fix module.** Kill it first — this is why the fix is still unproven through real code.

```bash
kill 38819
cd /Users/kamachi/cre-f3f7/citation_repair_F1_handoff
SCRATCH=/private/tmp/claude-508/-Users-kamachi-cre-f3f7/c2fd1c5d-3470-4e32-8242-924bdeb75dcb/scratchpad
ANTHROPIC_API_KEY="$(cat ~/.cre_bench_key)" \
  /Users/kamachi/citation-repair-engine/.venv_cre/bin/python3 -m cre.f1.sandbox_server \
  --no-open --mailto zhandong.liu@bcm.edu >> "$SCRATCH/server.log" 2>&1 &
```

`--authorities` need not be passed at launch: `run_packet` reads `body["authorities"]` per request
(`sandbox_server.py:175`), which is how every F7 run above was done.

```bash
python3 -c "
import json,urllib.request
S='$SCRATCH'
body={'packet':json.load(open(S+'/aspirin_f7c.json')),'authorities':S+'/f7_authorities','verify':'sqlite'}
r=urllib.request.urlopen('http://127.0.0.1:8781/api/run',
    json.dumps(body).encode(),timeout=900)
json.dump(json.load(r),open(S+'/run_f7_real.json','w'),indent=1)
"
```

Then read `result.record.terminal_outcome`, `.findings`, `.label`, and both
`f7_records[0].tuple_records[*].derived`. **Expect `F7` / `['F7','F6']` / `F7`.** If it still holds
on `cross_comparator_unavailable`, the prompt change did not take and the module is stale — check
`f7_records[0].policy_sha256` changed and that `evidence_prompt_version` reads `f7_evidence_v2`.

A live run costs 9 model calls against `claude-opus-5`. Read the key only as
`"$(cat ~/.cre_bench_key)"` inside a command — **never** echo it, never pass `--api-key` on a command
line (visible in `ps`), never ask the user to paste one.

---

## 8. Outstanding

1. ~~**Confirm the fix through real code**~~ — **DONE**, §10.1. `F7` / `['F7','F6']` / `F7`.
2. ~~**Re-run the original phrasing**~~ — **DONE**, §10.2. It does **not** fire. It holds at
   `relation_mismatch`, driven by the **`object`** component, not `predicate` as gate 2 suggested.
3. **Commit `f7_entity.py`** — held back pending #1, and pending the user's word. Earlier in this
   work the user pushed back hard on scope creep; ask before committing.
4. **Report the residual gap (below).** It is not fixed.
5. **Decide the scratchpad's fate** — keep or bin the hand-built authority set; keep or uninstall
   `ijson`. Both are unreverted side effects.

### The residual gap — unfixed, must be reported

If a cited paper genuinely contains **no entity of the claimed type**, the locator must still answer
something: the schema requires a nonblank surface. It will return an off-type entity, the comparator
will fail, and the claim will be held with `cross_comparator_unavailable` — a reason that, after this
fix, is now **under-descriptive**: it will read as "cross-ontology pair" when the truth is "the
paper has no entity of that type at all."

Fixing that means either letting the locator return *nothing* (a schema change) or letting the
roll-up ignore that one tuple (relaxing no-cherry-picking). **The second trades precision for recall
and is the user's call, not Claude's.** It was deliberately left alone.

---

## 9. Constraints in force

- **Never** synthesize a verdict, label, score, or example output. Nothing produced by the bench is
  reportable. Claude assigns no semantic labels and curates no ground truth. The hand-written
  `provably_distinct` relation in §4 is a mechanism fixture and must never be described otherwise.
- **Never** weaken a hash comparison or edit a hash to make a load pass.
- **Never** touch `main`.
- No production file may be modified — **superseded for `f7_entity.py` only**, by the user's explicit
  *"Fix it please."* That authorization does not extend to any other file.
- **Shared worktree.** Only ever `git add` by explicit path; never `git add -A` / `.` / `commit -a`.
  Never bare `git stash` / `git stash pop` — the stack is shared with other sessions. If a stash is
  unavoidable: `git stash push -u -m "<unique-tag>"`, capture the SHA via
  `git stash list --format='%H %gs'`, restore with `git stash apply <sha>`, then drop by re-finding
  the tag.
- Two commits sit unpushed on the shared branch on top of a peer's `07f3875`:
  `31e61a0` (Band-1 gate) and `aa83ea6` (join to the judge bench).

---

## 10. Confirmation run — 2026-08-22, real module

Server PID 38819 (stale) killed; relaunched as PID 52287 on the edited module. Authority set is the
same 132K hand-built stand-in of §4, passed per-request. Verified before reading any verdict:
`F7Policy().evidence_prompt_version == "f7_evidence_v2"`, `<<CLAIM_TYPE>>` present in the live
prompt, and `policy_sha256` moved `4183485468a60ef2…` → `1e5cbbc5b26d3222…`.

Note the monkeypatch run `run_f7_patched.json` carries the **pre-fix** `policy_sha256` — patching the
prompt string in-process did not bump the version field. That is precisely the audit hole edit #1
closes, and it is why the bump was not optional.

### 10.1 The fix is real — `aspirin_f7c.json` → `run_f7_real.json`

```
tuple 0  drug/Aspirin        -> drug/metformin        [RxNorm:1191 vs RxNorm:6809]  CONFIRMED_MISMATCH
tuple 1  disease/lung cancer -> disease/lung cancer   [MONDO:0008903 same]          SAME_ENTITY
record   terminal_outcome F7 · findings ['F7','F6'] · label F7
paid_calls {total: 9, retries: 0, by_stage {claim_extraction:1, coverage:1, F7:6, F7_verifier:1}}
```

Matches the monkeypatch prediction exactly. **Both tuples are on-type in every run below, including
the ones that hold** — the cross-ontology pairing of §2 is gone. That was the bug, and it is fixed.

### 10.2 The user's own sentence still does not reach F7 — `aspirin_user.json` → `run_user.json`

New packet, the user's pair verbatim: claim *"Aspirin is attributed to a 30% recovery rate in lung
cancer patients"*, results section = *"Metformin was shown to increase recovery in patients with lung
cancer to up to 30%."* and nothing else (no invented methods section).

```
tuple 0  drug/Aspirin -> drug/Metformin  provably_distinct  ->  UNJUDGEABLE / relation_mismatch
tuple 1  disease/lung cancer -> disease/lung cancer         ->  SAME_ENTITY
record   terminal_outcome F6 · findings ['F6'] · label F6
paid_calls {total: 8, ... F7:6}   # no F7_verifier call: nothing reached CONFIRMED_MISMATCH
```

The **entity** half of F7 is fully satisfied — `RxNorm:1191` vs `RxNorm:6809`, `provably_distinct`.
The **relation** gate is what holds it:

| component | verdict | claim | evidence |
|---|---|---|---|
| predicate | *unstable, see below* | `is attributed to` | `increases recovery in` |
| **object** | **mismatch** | `30% recovery rate` | `up to 30% recovery` |
| direction | match | positive/beneficial | positive/beneficial |
| population | match | lung cancer patients | patients with lung cancer |

Comparator's own rationale: *"the object differs in strength: the evidence reports 'up to 30%
recovery' (an upper bound), whereas the claim asserts a flat '30% recovery rate', overstating the
supported figure."* F7 requires all four to match, so one mismatch holds the tuple, and
no-cherry-picking holds the claim.

### 10.3 The comparator is not stable on this phrasing

`aspirin_user.json` run twice, byte-identical input:

| run | predicate | object | direction | population | outcome |
|---|---|---|---|---|---|
| `run_user.json` | match | **mismatch** | match | match | F6 |
| `run_user_rep2.json` | **mismatch** | **mismatch** | match | match | F6 |

And a one-variable diagnostic (`aspirin_user_nohedge.json`, evidence `to up to 30%` → `to 30%`,
claim untouched) flipped the blame the other way — `object: match`, `predicate: mismatch`, still F6.

So: **`object` mismatch is the consistent blocker for the user's sentence; `predicate` attribution
flips run to run.** The verdict is stable (F6 / `relation_mismatch` in all three runs); the component
the comparator blames is not. Gates 2 and 3 of §5 were probably always the same borderline, sampled
twice. This is a **new finding, not diagnosed further, and not a licence to relax anything.**

### 10.4 What this means

*"Aspirin is attributed to a 30% recovery rate"* against *"up to 30%"* is a real defect — the citation
drops the paper's upper-bound hedge — but it is **not the defect F7 detects.** F7 asks whether the
paper is about a different entity, and it cannot answer until claim and paper are agreed to be
discussing the same relation. A hedge-stripping misreport is a different taxonomy, and F7 declining
it is the seam being strict, not broken.

Files: `run_f7_real.json`, `run_user.json`, `run_user_rep2.json`, `run_user_nohedge.json`,
packets `aspirin_user.json`, `aspirin_user_nohedge.json`, runner `runpkt.py` — all in the §3
scratchpad. Server PID 52287 left running.
