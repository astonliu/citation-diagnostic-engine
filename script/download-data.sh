#!/usr/bin/env bash
# Fetch PMC Open Access documents for a list of PMCIDs.
#
#   script/download-data.sh <pmcid-file> [out-dir]
#
# The corpus is not committed: it is large, it is versioned upstream, and a
# stale copy in git is worse than no copy.
set -euo pipefail

IDS="${1:?usage: download-data.sh <pmcid-file> [out-dir]}"
OUT="${2:-data/raw}"
: "${NCBI_API_KEY:?set NCBI_API_KEY -- see README, Setup}"

mkdir -p "$OUT"
BASE="https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

while read -r pmcid; do
  [ -z "$pmcid" ] && continue
  out="$OUT/${pmcid}.xml"
  [ -f "$out" ] && { echo "[skip] $pmcid"; continue; }
  echo "[fetch] $pmcid"
  curl -sf --get "$BASE" \
    --data-urlencode "db=pmc" \
    --data-urlencode "id=${pmcid#PMC}" \
    --data-urlencode "retmode=xml" \
    --data-urlencode "api_key=${NCBI_API_KEY}" \
    -o "$out" || { echo "[fail] $pmcid" >&2; rm -f "$out"; }
  # NCBI allows 10 requests/second with a key. Stay well under it.
  sleep 0.2
done < "$IDS"

echo "[download] $(find "$OUT" -name '*.xml' | wc -l | tr -d ' ') documents in $OUT"
