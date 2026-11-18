-- How often a name match names the LEI GLEIF's own SEC registrations (RA000665) give the
-- same filer, by method and status: the figures descriptor.overrides.json, resolved.sql
-- and tier.sql state. Run from this folder after a full `arc run`:
--
--   duckdb -c ".read scripts/measure_precision.sql"
--
-- The population is the filers RA000665 registers by CIK. tier.sql replaces their name
-- matches with the registration, so no published name-matched row belongs to it: the
-- figure is the matcher's precision where the answer is known, not a count of published
-- rows. It counts edges, one per filer and LEI as the crosswalk publishes them, so a
-- filer with several tickers or former names matching one LEI counts once. In November
-- 2026: exact_name confirmed 0.983 on 4,484 edges, jaro_winkler candidate 0.198 on 835.
WITH ra AS (
  SELECT DISTINCT CAST(CAST(registered_as AS BIGINT) AS VARCHAR) AS cik, lei
  FROM (
    SELECT json_extract_string(json, '$.attributes.lei') AS lei,
           json_extract_string(json, '$.attributes.entity.registeredAs') AS registered_as
    FROM read_ndjson_objects('build/gleif_ra_sec.jsonl')
  )
  WHERE regexp_full_match(registered_as, '[0-9]+')
),
edges AS (
  SELECT DISTINCT cik, lei, method, status
  FROM read_parquet('build/resolved.parquet')
  WHERE cik IN (SELECT cik FROM ra)
)
SELECT e.method, e.status, count(*) AS edges,
       round(avg(CAST(EXISTS (SELECT 1 FROM ra x WHERE x.cik = e.cik AND x.lei = e.lei) AS INTEGER)), 3) AS share_registered_lei
FROM edges e
GROUP BY ALL
ORDER BY e.method, e.status;
