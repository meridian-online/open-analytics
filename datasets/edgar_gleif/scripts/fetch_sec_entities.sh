#!/usr/bin/env bash
# Build the SEC entity resolution universe — the left side of the crosswalk at full
# scale. Every named SEC filer (CIK + name) from the full cik-lookup list (~1.05M
# name-variant rows, INCLUDING SEC former names → extra match surface), with
# ticker/exchange joined where the filer has one. Individuals (insider Form 3/4/5
# filers) are left in: they carry no LEI, so they can never match GLEIF and the
# crosswalk OUTPUT is entity-only by construction. Reads build/edgar.parquet (the
# ticker product, fetched by the fetch_edgar step) for the ticker join.
# Usage: fetch_sec_entities.sh out.parquet
set -euo pipefail
OUT="${1:?output parquet path}"
UA="Meridian Protocol (open-analytics; research@meridian.online)"
mkdir -p build
curl -fsSL -H "User-Agent: $UA" "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt" -o build/cik_lookup.txt
duckdb -c "
COPY (
  WITH ck AS (
    -- CIK is the trailing :NNNNNNNNNN: ; names may themselves contain colons, so
    -- parse the CIK off the end by regex rather than splitting on ':'.
    SELECT CAST(regexp_extract(line, ':([0-9]{10}):\$', 1) AS BIGINT)::VARCHAR AS cik,
           regexp_replace(line, ':[0-9]{10}:\$', '') AS company_name
    FROM read_csv('build/cik_lookup.txt', delim='\x1e', header=false, all_varchar=true,
                  columns={'line':'VARCHAR'}, ignore_errors=true)
    WHERE line SIMILAR TO '.*:[0-9]{10}:' AND regexp_replace(line, ':[0-9]{10}:\$', '') <> ''
  ),
  tk AS (
    SELECT CAST(cik AS BIGINT)::VARCHAR AS cik, any_value(ticker) AS ticker, any_value(exchange) AS exchange
    FROM read_parquet('build/edgar.parquet') GROUP BY cik
  )
  SELECT c.cik, c.company_name, t.ticker, t.exchange
  FROM ck c LEFT JOIN tk t USING (cik)
) TO '$OUT' (FORMAT parquet, COMPRESSION zstd);
"
echo "wrote $OUT" >&2
