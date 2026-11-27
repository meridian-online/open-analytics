-- Natural Earth's countries at 1:110m (admin_0_countries): one row per country or
-- territory Natural Earth draws, 177 in release 5.1.1, read from the shapefile with the
-- spatial extension's ST_Read.
--
-- The release is read from the archive's VERSION.txt and must be the one the descriptor
-- names (5.1.1), so a new release fails the run until a person updates the URL, the
-- descriptor and this check together, as a new Census vintage does for FIPS.
--
-- iso_a3 is the key. Natural Earth sets ISO_A3 to -99 where ISO's code is disputed or
-- missing, and gives the code a map should use in ISO_A3_EH ("either here"): France and
-- Norway carry -99 in ISO_A3 and FRA and NOR in ISO_A3_EH. Three countries have no ISO
-- code at all, Northern Cyprus, Somaliland and Kosovo, and for those the key is Natural
-- Earth's own ADM0_A3 (CYN, SOL, KOS), so every country has a key and none is dropped.
-- iso_a2 takes ISO_A2_EH the same way, and is NULL where Natural Earth has no code.
--
-- The run fails on a missing or repeated key, an iso_a2 that is not two capitals, or a
-- shape that is missing or empty. Shapes are not tested for validity: Natural Earth's
-- 1:110m outlines are drawn as published.

INSTALL spatial;
LOAD spatial;

CREATE OR REPLACE TABLE ne_release AS
WITH v AS (
  SELECT trim(line) AS release
  FROM read_csv('build/naturalearth/countries/ne_110m_admin_0_countries.VERSION.txt',
                columns = {'line': 'VARCHAR'}, delim = chr(1), quote = '', escape = '',
                header = false)
  WHERE trim(line) <> ''
)
SELECT CASE WHEN count(*) <> 1 OR min(release) <> '5.1.1'
            THEN error('Natural Earth release is not 5.1.1: VERSION.txt says ' || coalesce(string_agg(release, ' | '), 'nothing') || '; update the fetch URL, the descriptors and this check together')
            ELSE min(release) END AS release
FROM v;

CREATE OR REPLACE TABLE ne_countries AS
WITH raw AS (
  SELECT * FROM ST_Read('build/naturalearth/countries/ne_110m_admin_0_countries.shp')
),
keyed AS (
  SELECT
    CASE WHEN ISO_A3_EH IS NOT NULL AND ISO_A3_EH <> '-99' THEN ISO_A3_EH ELSE ADM0_A3 END AS iso_a3,
    CASE WHEN ISO_A2_EH IS NOT NULL AND ISO_A2_EH <> '-99' THEN ISO_A2_EH END AS iso_a2,
    -- Natural Earth's own ISO_A3 and ISO_A2 (-99 for France) are left out: DuckDB's
    -- names ignore case, so they would stand beside the keys above under the same names.
    * EXCLUDE (ISO_A3, ISO_A2)
  FROM raw
),
checked AS (
  SELECT
    CASE
      WHEN iso_a3 IS NULL OR NOT regexp_full_match(iso_a3, '[A-Z]{3}')
        THEN error('Natural Earth country without a three-letter key: ' || coalesce(ADMIN, '?') || ' (ISO_A3_EH ' || coalesce(ISO_A3_EH, 'NULL') || ', ADM0_A3 ' || coalesce(ADM0_A3, 'NULL') || ')')
      WHEN iso_a2 IS NOT NULL AND NOT regexp_full_match(iso_a2, '[A-Z]{2}')
        THEN error('Natural Earth country with a malformed ISO_A2_EH: ' || ADMIN || ' (' || iso_a2 || ')')
      WHEN geom IS NULL OR ST_IsEmpty(geom)
        THEN error('Natural Earth country with no shape: ' || ADMIN)
      WHEN count(*) OVER (PARTITION BY iso_a3) > 1
        THEN error('Natural Earth key repeated: ' || iso_a3)
      ELSE true
    END AS ok,
    *
  FROM keyed
)
SELECT * EXCLUDE (ok) FROM checked WHERE ok;
