# Estimated runtime: ~2-5 minutes (copies ~300 small paper directories on Drive
# and reads two event logs). Read-mostly: it COPIES, never deletes.
import json, collections
from pathlib import Path

D = Path("/content/drive/MyDrive/CitationRepairEngine")
RUNS = D / "runs"
SAMPLE_SEED, SAMPLE_SIZE = 20260824, 2000
CANON = RUNS / ("f1f8_only_seed%d_n%d" % (SAMPLE_SEED, SAMPLE_SIZE))
PRICE = {"input_tokens": 5.00, "cache_creation_input_tokens": 6.25,
         "cache_read_input_tokens": 0.50, "output_tokens": 25.00}

orphans = sorted(p for p in RUNS.glob("f1f8_only_seed%d_2*" % SAMPLE_SEED)
                 if p != CANON)
print("canonical run :", CANON.name)
print("forked runs   :", [p.name for p in orphans] or "none")

# ---- 1. what was actually spent -------------------------------------------
tok, calls, results = collections.Counter(), 0, collections.Counter()
for run in orphans + [CANON]:
    ev = run / "events" / "model_events.jsonl"
    if not ev.exists():
        continue
    for line in ev.open(encoding="utf-8"):
        if not line.strip():
            continue
        e = json.loads(line)
        results[e.get("result", "?")] += 1
        if e.get("result") == "success":
            calls += 1
            for k in PRICE:
                tok[k] += int(e.get(k) or 0)
usd = sum(tok[k] / 1e6 * v for k, v in PRICE.items())
print("\nMODEL CALLS   : %d successful, outcomes %s" % (calls, dict(results)))
print("TOKENS        : %s" % dict(tok))
print("SPEND SO FAR  : $%.2f  (Opus 5 list prices, checked 2026-08-20)" % usd)

# ---- 2. merge the forked runs into the canonical one -----------------------
(CANON / "papers").mkdir(parents=True, exist_ok=True)
moved, collided = 0, 0
for run in orphans:
    for src in sorted((run / "papers").glob("*/status.json")):
        dst = CANON / "papers" / src.parent.name
        if (dst / "status.json").exists():
            collided += 1
            continue
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.parent.iterdir():
            if f.is_file():
                (dst / f.name).write_bytes(f.read_bytes())
        moved += 1
print("\nMERGED        : %d paper dirs copied in, %d already present" % (moved, collided))

# ---- 3. what the merged run holds ------------------------------------------
done = complete = skipped = refs = f1 = f8 = f2 = quar = 0
for st in (CANON / "papers").glob("*/status.json"):
    p = json.loads(st.read_text(encoding="utf-8"))
    done += 1
    if p.get("status") == "skipped":
        skipped += 1
        continue
    complete += 1
    refs += int(p.get("references") or 0)
    f1 += int(p.get("f1") or 0)
    f8 += int(p.get("f8") or 0)
    f2 += int(p.get("f2_computed_excluded") or 0)
    quar += int(p.get("quarantined") or 0)
print("\nMERGED RUN    : %d papers on disk (%d complete, %d skipped)"
      % (done, complete, skipped))
print("REFERENCES    : %d" % refs)
print("F1            : %d" % f1)
print("F8            : %d" % f8)
print("F2 (computed, excluded from the reported set): %d" % f2)
print("quarantined (unjudged, never a finding)      : %d" % quar)

# ---- 4. was F1 even reachable? ---------------------------------------------
probes = list(RUNS.glob("f1f8_only_*/measurements/provider_probe.json"))
if not probes:
    print("\nPROVIDER PROBE: NEVER RUN. F1 %d is UNINTERPRETABLE until Section 5B "
          "says whether PubMed, Crossref and OpenAlex all answer -- "
          "confirm.fully_answered() gates every F1." % f1)
for pr in probes:
    pp = json.loads(pr.read_text(encoding="utf-8"))
    print("\nPROVIDER PROBE: f1_reachable=%s | hits=%s"
          % (pp.get("f1_reachable"), pp.get("hits")))

print("\nNext run resumes into %s and skips those %d papers." % (CANON.name, done))
