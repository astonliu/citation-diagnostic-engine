# Estimated runtime: ~30-60 seconds (Drive listings only; no network, no cost).
# READ-ONLY. Safe on a fresh runtime. Run this FIRST after any reconnect.
import json, gzip
from pathlib import Path
from google.colab import drive
drive.mount("/content/drive", force_remount=False)

DRIVE_ROOT = Path("/content/drive/MyDrive/CitationRepairEngine")
print("=" * 68)

# 1. The sampling frame — the only expensive thing cell 8 produces (731 ESearch).
frames = sorted((DRIVE_ROOT / "sampling_frames").glob("*_manifest.json"))
if not frames:
    print("FRAME       : NOT on Drive. Cell 8 did not finish; it will re-run (~5-12 min).")
for f in frames:
    m = json.loads(f.read_text())
    gz = Path(m.get("frame_path", ""))
    ok = gz.exists()
    print("FRAME       : %s | %s ids | %d truncated days | gz present: %s"
          % (f.stem, m.get("ids_unique"), m.get("truncated_day_count", -1), ok))

# 2. Citing XML — every one of these is an NCBI fetch you don't have to repeat.
xml = list((DRIVE_ROOT / "cache" / "citing_xml").glob("*.xml"))
mb = sum(p.stat().st_size for p in xml) / 1e6 if xml else 0
print("CITING XML  : %d files on Drive (%.0f MB) — all reusable" % (len(xml), mb))

# 3. The MEDLINE store — written only after the whole prewarm loop finishes.
med = DRIVE_ROOT / "cache" / "medline_store.jsonl.gz"
if med.exists():
    n = sum(1 for line in gzip.open(med, "rt") if line.strip())
    print("MEDLINE     : %d records on Drive — prewarm completed at least once" % n)
else:
    print("MEDLINE     : not on Drive — the batched prewarm did not finish. "
          "Nothing lost except the requests; it re-runs in ~1-2 min.")

# 4. Per-paper Band-1 outputs — the ONLY thing that costs money to redo.
runs = sorted((DRIVE_ROOT / "runs").glob("f1f8_only_*"))
if not runs:
    print("BAND-1 WORK : none. No paper was processed, so nothing paid was lost.")
for r in runs:
    done = list((r / "papers").glob("*/status.json"))
    complete = [p for p in done
                if json.loads(p.read_text()).get("status") == "complete"]
    live = r / "f1_f8_findings_live.jsonl"
    hits = sum(1 for _ in live.open()) if live.exists() else 0
    state = r / "measurements" / "run_state.json"
    spend = json.loads(state.read_text()).get("spend_usd") if state.exists() else None
    print("BAND-1 WORK : %s | %d papers complete | %d F1/F8 rows | spend %s"
          % (r.name, len(complete), hits, "$%.2f" % spend if spend else "not recorded"))

print("=" * 68)
print("Anything listed above is SAFE and is skipped on the next run.")
