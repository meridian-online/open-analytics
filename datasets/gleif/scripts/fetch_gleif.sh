#!/usr/bin/env bash
# Resolve the latest GLEIF golden-copy full LEI CSV, download it (~470 MB zip) and
# unzip it into <out_dir> as the raw source the `load` step reads. The golden copy is
# republished daily; the publishes API returns the current full-file URLs.
# Emits <out_dir>/<name>.csv (the ~200-column golden copy).
# Usage: fetch_gleif.sh [out_dir]   (default: build/gleif_src)
set -euo pipefail
OUT="${1:-build/gleif_src}"
UA="Meridian Protocol (open-analytics; research@meridian.online)"
API="https://goldencopy.gleif.org/api/v2/golden-copies/publishes?format=json"

echo "resolving latest golden-copy full CSV URL" >&2
URL=$(curl -fsS -H "User-Agent: $UA" "$API" \
       | jq -r '(.data | if type == "array" then .[0] else . end) | .lei2.full_file.csv.url')
[ -n "$URL" ] && [ "$URL" != "null" ] || { echo "could not resolve golden-copy CSV URL" >&2; exit 1; }
echo "  $URL" >&2

rm -rf "$OUT"; mkdir -p "$OUT"
echo "downloading (~470 MB zip)" >&2
curl -fL --retry 3 -H "User-Agent: $UA" -o "$OUT/gleif.csv.zip" "$URL"
echo "unzipping" >&2
unzip -o -q "$OUT/gleif.csv.zip" -d "$OUT"
rm -f "$OUT/gleif.csv.zip"
CSV=$(ls "$OUT"/*.csv | head -1)
[ -n "$CSV" ] || { echo "no CSV found after unzip" >&2; exit 1; }
echo "  extracted $CSV" >&2
