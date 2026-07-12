#!/usr/bin/env bash
# Download recent SEC Form N-CEN datasets and extract the two tables that carry
# authoritative fund LEIs straight from the filing:
#   REGISTRANT.tsv          → registrant CIK ↔ LEI  (the fund company/trust)
#   FUND_REPORTED_INFO.tsv  → series SERIES_ID ↔ LEI (each fund series)
# N-CEN is filed annually per fund, staggered across quarters, so ~4 recent quarters
# cover the registered-fund universe. LEI fill is ~100%.
# Usage: fetch_ncen.sh [num_quarters] [out_dir]   (defaults: 4, build/ncen)
set -euo pipefail
N="${1:-4}"
OUT="${2:-build/ncen}"
UA="Meridian Protocol (open-analytics; research@meridian.online)"
BASE="https://www.sec.gov"
PAGE="$BASE/data-research/sec-markets-data/form-n-cen-data-sets"

rm -rf "$OUT"; mkdir -p "$OUT"
urls=$(curl -fsS -H "User-Agent: $UA" "$PAGE" \
        | grep -oiE 'href="[^"]*ncen[^"]*\.zip"' | sed 's/href="//;s/"$//' | head -n "$N")
[ -n "$urls" ] || { echo "no N-CEN zip links found on $PAGE" >&2; exit 1; }

for path in $urls; do
  label=$(basename "$path" | grep -oE '[0-9]{4}q[0-9]')
  echo "N-CEN $label" >&2
  tmp="$OUT/$label.zip"
  curl -fsS -H "User-Agent: $UA" "$BASE$path" -o "$tmp"
  mkdir -p "$OUT/$label"
  unzip -o -q "$tmp" REGISTRANT.tsv FUND_REPORTED_INFO.tsv -d "$OUT/$label"
  rm -f "$tmp"
  sleep 0.3
done
echo "extracted $N quarter(s) into $OUT" >&2
