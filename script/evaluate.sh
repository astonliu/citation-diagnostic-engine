#!/usr/bin/env bash
# The counts every reported rate divides by: routes, dispositions, outcomes.
#
#   script/evaluate.sh <out-dir>
#
# Counts, not rates. A rate needs a governed population and this cannot tell you
# that you have one -- see doc/evaluation.md on what belongs in a denominator.
set -euo pipefail

OUT_DIR="${1:?usage: evaluate.sh <out-dir>}"
PREDICTIONS="$OUT_DIR/judgment_predictions.jsonl"

if [ ! -f "$PREDICTIONS" ]; then
  echo "no predictions at $PREDICTIONS -- has the pipeline run?" >&2
  exit 1
fi

PYTHONPATH=. python -m cde.runtime.cli report "$PREDICTIONS"
