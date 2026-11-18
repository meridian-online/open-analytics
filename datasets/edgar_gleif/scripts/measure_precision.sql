-- How often a name match names the LEI GLEIF's own SEC registrations (RA000665) give the
-- same filer, by method and status: the figures descriptor.overrides.json, resolved.sql
-- and tier.sql state. Run from this folder after a full `arc run`:
--
--   duckdb -c ".read scripts/measure_precision.sql"
--
-- The population is the filers RA000665 registers by CIK. tier.sql replaces their name
-- matches with the registration, so no published name-matched row belongs to it: the
-- figure is the matcher's precision where the answer is known, not a count of published
-- rows. In November 2026: exact_name confirmed 0.982 on 4,719 rows, jaro_winkler
-- candidate 0.198 on 891.
WITH ra AS (
  SELECT DISTINCT CAST(CAST(registered_as AS BIGINT) AS VARCHAR) AS cik, lei
  FROM read_csv('build/gleif_ra_sec.csv', header = true, all_varchar = true)
  WHERE regexp_full_match(registered_as, '[0-9]+')
)
SELECT r.method, r.status, count(*) AS rows_with_registration,
       round(avg(CAST(EXISTS (SELECT 1 FROM ra x WHERE x.cik = r.cik AND x.lei = r.lei) AS INTEGER)), 3) AS share_registered_lei
FROM read_parquet('build/resolved.parquet') r
WHERE r.cik IN (SELECT cik FROM ra)
GROUP BY ALL
ORDER BY r.method, r.status;
