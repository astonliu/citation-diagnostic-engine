# First-light triage — answers, live-positive watcher, and the 200-paper question

**Run under review:** Band 2, doc 1 `PMC13294766`, notebook `docs/FULL_SYSTEM_COLAB_TEST.ipynb`
**Tree:** `merge/f2-into-f3f7` @ `0a8e663`

---

## 1. F6 is not confused. The report you read is stale.

The string **"check ran and found none" does not exist anywhere in the notebook on disk.** Cell 8's
current code is:

```python
interpretation = (f"{findings} finding(s) emitted" if findings else
                  "reachable; zero findings emitted" if reachable else
                  "check did not run")
```

Your header row also differs — you have `| what zero means`, the file has `| interpretation`. **You ran
an older Cell 8.** Re-run it against the same manifest (~3 s, free, no model calls) and that row becomes:

```
F6 | True | always live (coverage) | YES | 4 | 4 finding(s) emitted; WARNING: cocitation.py is not in GOVERNING_MODULES
```

**So F6 found 4 real findings on that document, and nothing is printing a number for no reason.** Go
look at those 4 — Cell 9 shows them.

Why only F6 shows a count: `judgment_run.py:365` says it outright — `"label": None, # taxonomy label;
only F6 is emitted live`. F6 is the one stratum this path emits as a live label. The others surface
through `findings` / `strength_records`, which is why the watcher below reads both.

---

## 2. The parse errors matter more than anything else in that output

Six of the document's references were quarantined. **A quarantined pair produces no verdict at all.**
So every `0` in your reachability table was measured over the judged remainder, not over the document.

Reconstructing your six errors, they are **three distinct failures, not one**:

| n | error | what it means | does raising `max_tokens` fix it? |
|---|---|---|---|
| 3 | `Expecting ',' delimiter` | malformed inside the object | **no** |
| 2 | `Extra data: line 2/3 column 1` | the model wrote **more after** the object — prose or a second object | **no** |
| 1 | `Unterminated string starting at` | output cut off mid-string | **yes** |

Cell 7 (notebook index 14) sets `band_prompts.make_anthropic_call(client, MODEL, max_tokens=1024)`.
Raising it addresses **one of six**. Do not expect it to clear the rate.

The other five are read-side: the strict parser demands **one bare JSON object**, and the model is
returning something else. `band_prompts.py` is frozen (blob `fa01126e…`), so the prompt cannot change.
Changing the reader to extract the first bare object **changes which references get judged** — that is a
decision, not a fix. Route it.

### ⚠ And `reportable: true` does not mean what it looks like

`preband_contract.py:682`:

```python
need("no_parse_failures", not (pb.get("preband_parse_failures") or {}), ...)
```

That checks **preflight parse failures of the F1/F2 disposition file** — not model-output quarantines.
So `no_parse_failures: true` and `reportable: true` are both technically correct **while 60% of the
document was quarantined.** The block is not lying; it is answering a different question than the one
you were reading it to answer. Add the census below beside it.

---

## 3. 200 papers — the arithmetic first

The figure carried in the specs is **~$7.22 and ~29 min per document**.

```
200 documents  ->  ~$1,444  and  ~97 hours of wall clock (~4 days)
```

**Re-measure it from doc 1 before trusting it** — you now have a real datapoint. Read
`BAND2_RECEIPT_SUMMARY` and the printed elapsed time and divide.

Two things make 200 the wrong number today:

1. **At a 60% quarantine rate you would pay full price for ~40% coverage**, and the errors you are
   hunting would be hidden behind pairs that never got judged.
2. **The notebook cannot resume a partial Band-2 run.** Cell 7 raises on partial output —
   *"Choose a new RUN_ID; automatic resume can duplicate reference rows in this engine."* Over ~97
   hours, one Colab disconnect loses the whole run. **If you do scale, chunk it: 10 documents per
   `RUN_ID`, one chunk per cell invocation.** A drop then costs one chunk.

**Do 3–5 papers first**, run the census, and decide the scale from a measured quarantine rate.

---

## Cell A — live positives · paste and run **before** the Band-2 cell

`judgment_run` writes one record per pair and calls `flush()` on every write
(`judgment_run.py:1573-1574`), so tailing the predictions file sees each verdict the instant the engine
commits it. **No engine change, no governed module touched.** Tested against a synthetic stream and
against a reconstruction of your doc-1 output.

```python
# Estimated runtime: ~0 s to start (a daemon thread; it lives as long as the run does)
# RUN THIS **BEFORE** THE BAND-2 CELL.
import json, os, threading, time
from collections import Counter

LIVE = {"stop": False, "pairs": 0, "positive": 0, "labels": Counter(),
        "docs": set(), "t0": time.time()}

def _fmt(rec):
    cid  = rec.get("citation_id", "?")
    labs = list(rec.get("findings") or [])
    if any((s or {}).get("derived") == "F4" for s in (rec.get("strength_records") or [])):
        if "F4" not in labs:
            labs.append("F4")
    lab = rec.get("label")
    if lab and lab not in labs:
        labs.append(lab)
    return cid, sorted(set(labs))

def _watch(pred_path, poll=1.0, heartbeat=30.0):
    pos = 0
    last_hb = time.time()
    while not LIVE["stop"]:
        if not os.path.exists(pred_path):
            time.sleep(poll); continue
        with open(pred_path, "r", encoding="utf-8") as fh:
            fh.seek(pos)
            for line in fh:
                if not line.endswith("\n"):        # partial line; re-read next tick
                    break
                pos += len(line.encode("utf-8"))
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                LIVE["pairs"] += 1
                if rec.get("citing_pmcid"):
                    LIVE["docs"].add(rec["citing_pmcid"])
                cid, labs = _fmt(rec)
                if not labs:
                    continue
                LIVE["positive"] += 1
                for l in labs:
                    LIVE["labels"][l] += 1
                sent = (rec.get("citing_sentence") or "").replace("\n", " ")
                print(f"\n>>> POSITIVE [{'+'.join(labs)}]  {cid}"
                      f"  cited_pmid={rec.get('cited_pmid') or '-'}"
                      f"  disposition={rec.get('disposition') or '-'}", flush=True)
                print(f"    citance: {sent[:220]}{'…' if len(sent) > 220 else ''}", flush=True)
                for hr in (rec.get("hold_reasons") or [])[:3]:
                    print(f"    hold: {hr}", flush=True)
        now = time.time()
        if now - last_hb >= heartbeat:
            last_hb = now
            el = now - LIVE["t0"]
            print(f"[live] docs={len(LIVE['docs'])} pairs={LIVE['pairs']} "
                  f"positives={LIVE['positive']} {dict(LIVE['labels'])} "
                  f"| elapsed {el/60:.1f} min", flush=True)
        time.sleep(poll)

def start_live_watch(band2_out):
    pred = os.path.join(str(band2_out), "judgment_predictions.jsonl")
    LIVE.update(stop=False, pairs=0, positive=0, labels=Counter(), docs=set(), t0=time.time())
    t = threading.Thread(target=_watch, args=(pred,), daemon=True)
    t.start()
    print(f"[live] watching {pred}", flush=True)
    return t

def stop_live_watch():
    LIVE["stop"] = True
    time.sleep(1.5)
    print(f"[live] FINAL docs={len(LIVE['docs'])} pairs={LIVE['pairs']} "
          f"positives={LIVE['positive']} {dict(LIVE['labels'])}", flush=True)

BAND2_OUT = RUN_ROOT / "band2"
_live_thread = start_live_watch(BAND2_OUT)
```

Add `stop_live_watch()` as the last line of the Band-2 cell.

**It reads `strength_records` as well as `findings`** — F4 is derived there and a watcher reading only
`findings` would miss every F4. Verified: the test stream emitted one F4 that way and it was caught.

---

## Cell B — the denominator · run after any Band-2 run, before believing a zero

```python
# Estimated runtime: ~2 s (reads the predictions JSONL already on disk; no network, no model)
import json, collections
from pathlib import Path

PRED = Path(BAND2_MANIFEST["predictions_path"])
rows = [json.loads(l) for l in PRED.read_text(encoding="utf-8").splitlines() if l.strip()]

disp = collections.Counter(r.get("disposition") or "(none)" for r in rows)
docs = collections.Counter(r.get("citing_pmcid") or "?" for r in rows)
quar = [r for r in rows if r.get("disposition") == "quarantine_parse"]

def _mode(err: str) -> str:
    e = err or ""
    if "Unterminated string"      in e: return "TRUNCATED (output cut mid-string -> raise max_tokens)"
    if "Extra data"               in e: return "MULTI-OBJECT (model wrote more after the object)"
    if "Expecting ',' delimiter"  in e: return "MALFORMED (bad delimiter inside the object)"
    if "Expecting value"          in e: return "EMPTY/NON-JSON (no object at all)"
    return "OTHER"

modes = collections.Counter(_mode(r.get("parse_error", "")) for r in quar)
judged = len(rows) - len(quar)
pct    = 100.0 * len(quar) / len(rows) if rows else 0.0
positive = sum(1 for r in rows if (r.get("findings") or []) or r.get("label")
               or any((s or {}).get("derived") == "F4" for s in (r.get("strength_records") or [])))

print("=" * 72)
print("DENOMINATOR — what the zeros were measured over")
print("=" * 72)
print(f"documents            : {len(docs)}")
print(f"reference pairs seen : {len(rows)}")
print(f"  judged             : {judged}")
print(f"  QUARANTINED (parse): {len(quar)}   <-- {pct:.1f}% NEVER REACHED JUDGMENT")
print(f"  positives          : {positive}")
print()
print("A finding count of 0 for any stratum means 0 out of", judged,
      "judged pairs — NOT 0 out of", len(rows))
print()
print("quarantine failure modes:")
for m, n in modes.most_common():
    print(f"  {n:4d}  {m}")
print()
print("dispositions:")
for d, n in disp.most_common():
    print(f"  {n:4d}  {d}")

per_doc = collections.Counter(r["citing_pmcid"] for r in quar if r.get("citing_pmcid"))
print("\nper-document quarantine rate:")
for pmcid, total in docs.most_common():
    q = per_doc.get(pmcid, 0)
    flag = "  <-- unusable" if total and q / total > 0.5 else ""
    print(f"  {pmcid}: {q}/{total} ({100.0*q/total if total else 0:.0f}%){flag}")

print("\nfirst 3 quarantine errors, verbatim:")
for r in quar[:3]:
    print(f"  {r['citation_id']}: {r.get('parse_error','')[:140]}")

if pct > 20:
    print("\n" + "!" * 72)
    print(f"STOP. {pct:.0f}% of pairs were quarantined. Do not scale this run and do not")
    print("read any stratum's zero as a result until this rate is characterised.")
    print("!" * 72)
```

Output on a reconstruction of your doc-1 numbers:

```
reference pairs seen : 10
  judged             : 4
  QUARANTINED (parse): 6   <-- 60.0% NEVER REACHED JUDGMENT

quarantine failure modes:
     3  MALFORMED (bad delimiter inside the object)
     2  MULTI-OBJECT (model wrote more after the object)
     1  TRUNCATED (output cut mid-string -> raise max_tokens)
```

---

## Constraints this triage respects

- **`band_prompts.py` byte-identical** — blob `fa01126e2b9482d450065fd70cd0eb1fea816f5c`. Neither cell
  touches it. `max_tokens` is passed by the notebook, not baked into the frozen package.
- **No governed module edited.** Both cells read artifacts the engine already writes.
- **This is not the reported run.** `production=False`, `require_reportable=False`. Do not quote any
  number from it, including the four F6 findings, as a rate.
- Changing the strict parser to accept non-bare-object output **changes which references get judged** —
  decision, not task.

## Next action

Run **Cell B against the artifact you already have.** No re-run, no cost. It tells you what the six
quarantines did to your denominator, and whether 200 papers is worth $1,444.
