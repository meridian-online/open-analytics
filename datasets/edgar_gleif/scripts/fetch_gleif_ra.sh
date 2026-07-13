#!/usr/bin/env bash
# Fetch GLEIF entities registered under a given registration authority (RA), paging
# the public GLEIF v1 API. Emits CSV: lei,registered_as,category.
#   registered_as = the SEC identifier the entity registered under — a numeric CIK
#   for operating companies, or an S…/C… series/class ID for funds.
#   category      = GLEIF's authoritative entity type (FUND | GENERAL | BRANCH | …).
# Usage: fetch_gleif_ra.sh RA000665 out.csv
#
# GLEIF hard-caps OFFSET paging (page[number]) at 10,000 records — RA000665 has ~27k,
# so page[number] paging 400s the moment it crosses record 10,000. We use CURSOR paging
# instead (page[cursor]=*, then follow links.next), which has no offset cap. In cursor
# mode GLEIF omits the offset meta (from/to/lastPage are null), so we terminate on a
# null links.next or an empty data page, with a hard iteration cap as a loop guard.
set -euo pipefail
RA="${1:?registration authority id, e.g. RA000665}"
OUT="${2:?output csv path}"
UA="Meridian Protocol (open-analytics; research@meridian.online)"
API="https://api.gleif.org/api/v1/lei-records"

total=$(curl -fsS -H "User-Agent: $UA" "$API?filter%5Bentity.registeredAt%5D=$RA&page%5Bsize%5D=1" | jq -r '.meta.pagination.total')
echo "GLEIF $RA: $total records (cursor paging)" >&2
max_iters=$(( total / 200 + 10 ))  # loop guard: never spin beyond the known record count

# Atomic write: build into a .part temp and mv only on a verified-complete fetch. A
# failed or partial fetch must leave NO output file — otherwise arc's modified_after
# precondition treats the stale partial as "fresh" and SKIPS the re-fetch, silently
# building on truncated data (the 10k-offset failure this step replaced did exactly
# that: a partial CSV was mistaken for a complete one).
TMP="$OUT.part"
echo "lei,registered_as,category" > "$TMP"
url="$API?filter%5Bentity.registeredAt%5D=$RA&page%5Bsize%5D=200&page%5Bcursor%5D=*"
iters=0
while [ -n "$url" ] && [ "$url" != "null" ]; do
  if [ "$iters" -ge "$max_iters" ]; then
    echo "error: cursor paging exceeded $max_iters iterations for $RA — aborting" >&2
    rm -f "$TMP"; exit 1
  fi
  resp=$(curl -fsS -H "User-Agent: $UA" "$url")
  n=$(echo "$resp" | jq -r '.data | length')
  [ "$n" -eq 0 ] && break
  echo "$resp" | jq -r '.data[]? | [.attributes.lei, .attributes.entity.registeredAs, .attributes.entity.category] | @csv' >> "$TMP"
  url=$(echo "$resp" | jq -r '.links.next // ""')
  iters=$(( iters + 1 ))
  sleep 0.3
done
got=$(( $(wc -l < "$TMP") - 1 ))
# Refuse a materially short fetch (allow ~1 page of drift if GLEIF's count shifts mid-run).
if [ "$got" -lt "$(( total - 200 ))" ]; then
  echo "error: fetched only $got of $total records for $RA — refusing to write partial output" >&2
  rm -f "$TMP"; exit 1
fi
mv "$TMP" "$OUT"
echo "wrote $got rows → $OUT" >&2
