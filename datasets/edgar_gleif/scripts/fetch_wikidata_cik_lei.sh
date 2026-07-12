#!/usr/bin/env bash
# Fetch Wikidata (CC0) items carrying BOTH an SEC Central Index Key (P5531) and an
# LEI (P1278) — an independent, license-clean CIK↔LEI seed. Emits CSV: cik,lei.
# Usage: fetch_wikidata_cik_lei.sh out.csv
set -euo pipefail
OUT="${1:?output csv path}"
UA="Meridian Protocol (open-analytics; research@meridian.online)"
curl -fsS -G "https://query.wikidata.org/sparql" -H "User-Agent: $UA" -H "Accept: text/csv" \
  --data-urlencode 'query=SELECT ?cik ?lei WHERE { ?item wdt:P5531 ?cik ; wdt:P1278 ?lei }' \
  -o "$OUT"
echo "wrote $(( $(wc -l < "$OUT") - 1 )) rows → $OUT" >&2
