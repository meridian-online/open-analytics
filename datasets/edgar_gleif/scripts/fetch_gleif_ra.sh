#!/usr/bin/env bash
# Fetch GLEIF entities registered under a given registration authority (RA), paging
# the public GLEIF v1 API. Emits CSV: lei,registered_as,category.
#   registered_as = the SEC identifier the entity registered under — a numeric CIK
#   for operating companies, or an S…/C… series/class ID for funds.
#   category      = GLEIF's authoritative entity type (FUND | GENERAL | BRANCH | …).
# Usage: fetch_gleif_ra.sh RA000665 out.csv
set -euo pipefail
RA="${1:?registration authority id, e.g. RA000665}"
OUT="${2:?output csv path}"
UA="Meridian Protocol (open-analytics; research@meridian.online)"
API="https://api.gleif.org/api/v1/lei-records"

total=$(curl -fsS -H "User-Agent: $UA" "$API?filter%5Bentity.registeredAt%5D=$RA&page%5Bsize%5D=1" | jq -r '.meta.pagination.total')
pages=$(( (total + 199) / 200 ))
echo "GLEIF $RA: $total records, $pages pages" >&2

echo "lei,registered_as,category" > "$OUT"
for p in $(seq 1 "$pages"); do
  curl -fsS -H "User-Agent: $UA" "$API?filter%5Bentity.registeredAt%5D=$RA&page%5Bsize%5D=200&page%5Bnumber%5D=$p" \
    | jq -r '.data[]? | [.attributes.lei, .attributes.entity.registeredAs, .attributes.entity.category] | @csv' >> "$OUT"
  sleep 0.3
done
echo "wrote $(( $(wc -l < "$OUT") - 1 )) rows → $OUT" >&2
