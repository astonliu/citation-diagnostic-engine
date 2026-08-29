#!/usr/bin/env bash
# Diagnose a directory of PMC documents: Band 1, then Band 2.
#
#   script/run-pipeline.sh <xml-dir> <out-dir> [model]
#
# Band 2 reads Band 1's disposition artifact and refuses to start without one:
# with no disposition every pair is excluded fail-closed and the run finishes
# valid, green and empty.
#
# This is the demonstration path, NOT the governed production launcher. It
# produces real records over an ungoverned population, so do not compute a
# reported rate from its output. See cde/runtime/cli.py.
set -euo pipefail

XML_DIR="${1:?usage: run-pipeline.sh <xml-dir> <out-dir> [model]}"
OUT_DIR="${2:?usage: run-pipeline.sh <xml-dir> <out-dir> [model]}"
MODEL="${3:-claude-haiku-4-5}"

: "${NCBI_API_KEY:?set NCBI_API_KEY -- see README, Setup}"
: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY -- see README, Setup}"

mkdir -p "$OUT_DIR"

PYTHONPATH=. python -m cde.runtime.cli band1 \
  --xml-dir "$XML_DIR" --out-dir "$OUT_DIR" --model "$MODEL"

PYTHONPATH=. python -m cde.runtime.cli band2 \
  --xml-dir "$XML_DIR" --out-dir "$OUT_DIR" --model "$MODEL"

echo "[run] records:  $OUT_DIR/judgment_predictions.jsonl"
echo "[run] manifest: $OUT_DIR/run_manifest.json"
