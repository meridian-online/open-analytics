-- OurAirports' airports that carry an ICAO code: one row per ICAO code, from three of
-- OurAirports' files and the repository commit they were fetched at.
--
--   * airports.csv: id, ident, type, name, latitude_deg, longitude_deg, elevation_ft,
--     continent, iso_country, iso_region, municipality, scheduled_service, icao_code,
--     iata_code, gps_code, local_code, home_link, wikipedia_link, keywords.
--   * countries.csv: id, code, name, continent, wikipedia_link, keywords.
--   * regions.csv: id, code, local_code, name, continent, iso_country, wikipedia_link,
--     keywords.
--   * release.csv, written by verify: the commit the three files were checked against
--     and its UTC date, the table's release.
--
-- Each CSV is read with every field as text and header = false, so the header is found
-- by its text, not by its position in the scan: exactly one row must be OurAirports'
-- header, and a header that has changed fails the run.
--
-- A quoted empty field reads as NULL, as an unquoted one does.
--
-- IATA's codes are not republished: iata_code and keywords (free text in which IATA
-- codes appear) are read only so the header can be checked, and dropped here.
--
-- The run also fails on: an ICAO code that is not four capital letters, or that repeats;
-- a country not in countries.csv; a region not in regions.csv, or one of another country;
-- an airport type outside the five load knows; a scheduled_service other than yes or no;
-- a latitude or longitude that is not a number in range; an elevation that is not an
-- integer; an id that is not an integer; an empty name; fewer than 5% of the airports
-- file's rows carrying an ICAO code; and a country or region code that countries.csv or
-- regions.csv repeats, checked before the join so a repeated lookup row is not reported
-- as a repeated ICAO code.

CREATE OR REPLACE TABLE ourairports AS
WITH airport_rows AS (
  SELECT *
  FROM read_csv('build/ourairports/airports.csv',
                columns = {'id': 'VARCHAR', 'ident': 'VARCHAR', 'type': 'VARCHAR',
                           'name': 'VARCHAR', 'latitude_deg': 'VARCHAR',
                           'longitude_deg': 'VARCHAR', 'elevation_ft': 'VARCHAR',
                           'continent': 'VARCHAR', 'iso_country': 'VARCHAR',
                           'iso_region': 'VARCHAR', 'municipality': 'VARCHAR',
                           'scheduled_service': 'VARCHAR', 'icao_code': 'VARCHAR',
                           'iata_code': 'VARCHAR', 'gps_code': 'VARCHAR',
                           'local_code': 'VARCHAR', 'home_link': 'VARCHAR',
                           'wikipedia_link': 'VARCHAR', 'keywords': 'VARCHAR'},
                header = false, delim = ',', quote = '"', escape = '"',
                nullstr = '', allow_quoted_nulls = true)
),
country_rows AS (
  SELECT *
  FROM read_csv('build/ourairports/countries.csv',
                columns = {'id': 'VARCHAR', 'code': 'VARCHAR', 'name': 'VARCHAR',
                           'continent': 'VARCHAR', 'wikipedia_link': 'VARCHAR',
                           'keywords': 'VARCHAR'},
                header = false, delim = ',', quote = '"', escape = '"',
                nullstr = '', allow_quoted_nulls = true)
),
region_rows AS (
  SELECT *
  FROM read_csv('build/ourairports/regions.csv',
                columns = {'id': 'VARCHAR', 'code': 'VARCHAR', 'local_code': 'VARCHAR',
                           'name': 'VARCHAR', 'continent': 'VARCHAR',
                           'iso_country': 'VARCHAR', 'wikipedia_link': 'VARCHAR',
                           'keywords': 'VARCHAR'},
                header = false, delim = ',', quote = '"', escape = '"',
                nullstr = '', allow_quoted_nulls = true)
),
-- Each file's header, found by its text: exactly one row must be it. The data CTEs test
-- this first, so a changed header is reported as one, not as a malformed row.
airports_header AS (
  SELECT CASE WHEN count(*) <> 1
              THEN error('OurAirports airports header has changed: expected id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality,scheduled_service,icao_code,iata_code,gps_code,local_code,home_link,wikipedia_link,keywords')
              ELSE true END AS ok
  FROM airport_rows
  WHERE id = 'id' AND ident = 'ident' AND type = 'type' AND name = 'name'
    AND latitude_deg = 'latitude_deg' AND longitude_deg = 'longitude_deg'
    AND elevation_ft = 'elevation_ft' AND continent = 'continent'
    AND iso_country = 'iso_country' AND iso_region = 'iso_region'
    AND municipality = 'municipality' AND scheduled_service = 'scheduled_service'
    AND icao_code = 'icao_code' AND iata_code = 'iata_code' AND gps_code = 'gps_code'
    AND local_code = 'local_code' AND home_link = 'home_link'
    AND wikipedia_link = 'wikipedia_link' AND keywords = 'keywords'
),
countries_header AS (
  SELECT CASE WHEN count(*) <> 1
              THEN error('OurAirports countries header has changed: expected id,code,name,continent,wikipedia_link,keywords')
              ELSE true END AS ok
  FROM country_rows
  WHERE id = 'id' AND code = 'code' AND name = 'name' AND continent = 'continent'
    AND wikipedia_link = 'wikipedia_link' AND keywords = 'keywords'
),
regions_header AS (
  SELECT CASE WHEN count(*) <> 1
              THEN error('OurAirports regions header has changed: expected id,code,local_code,name,continent,iso_country,wikipedia_link,keywords')
              ELSE true END AS ok
  FROM region_rows
  WHERE id = 'id' AND code = 'code' AND local_code = 'local_code' AND name = 'name'
    AND continent = 'continent' AND iso_country = 'iso_country'
    AND wikipedia_link = 'wikipedia_link' AND keywords = 'keywords'
),
-- The release, from verify: the commit the files were checked against, and its date.
airports_release AS (
  SELECT CASE WHEN count(*) <> 1 THEN error('expected one row in release.csv')
              WHEN NOT regexp_full_match(any_value(release), '[0-9]{4}-[0-9]{2}-[0-9]{2}')
                THEN error('release.csv release is not a date: ' || coalesce(any_value(release), 'NULL'))
              ELSE any_value(release) END AS release
  FROM read_csv('build/ourairports/release.csv',
                columns = {'commit_sha': 'VARCHAR', 'release': 'VARCHAR'}, header = true)
),
countries AS (
  SELECT
    CASE WHEN NOT (SELECT ok FROM countries_header) THEN NULL ELSE code END AS code,
    name
  FROM country_rows
  WHERE NOT (id = 'id' AND code = 'code' AND name = 'name')
),
regions AS (
  SELECT
    CASE WHEN NOT (SELECT ok FROM regions_header) THEN NULL ELSE code END AS code,
    name, iso_country
  FROM region_rows
  WHERE NOT (id = 'id' AND code = 'code' AND name = 'name')
),
lookups_unique AS (
  SELECT CASE WHEN (SELECT count(*) - count(DISTINCT code) FROM countries) > 0
              THEN error('OurAirports countries file repeats a country code')
              WHEN (SELECT count(*) - count(DISTINCT code) FROM regions) > 0
              THEN error('OurAirports regions file repeats a region code')
              ELSE true END AS ok
),
-- A guard against OurAirports dropping most of its ICAO codes, which every per-row
-- check would pass: at least 5% of the airports file's rows must carry one (12.4% on
-- 2026-11-16, 10,734 of 86,368).
icao_share AS (
  SELECT CASE WHEN NOT (SELECT ok FROM airports_header) THEN NULL
              WHEN count(icao_code) * 20 < count(*)
              THEN error('only ' || count(icao_code) || ' of ' || count(*)
                         || ' airports have an ICAO code, below the 5% floor')
              ELSE true END AS ok
  FROM airport_rows
  WHERE NOT (id = 'id' AND ident = 'ident' AND icao_code = 'icao_code')
),
-- Data rows: the airports with an ICAO code, each checked for shape and against the
-- countries and regions files, in that order.
airports AS (
  SELECT
    CASE WHEN NOT (SELECT ok FROM airports_header) THEN NULL
         WHEN NOT (SELECT ok FROM lookups_unique) THEN NULL
         WHEN NOT (SELECT ok FROM icao_share) THEN NULL
         WHEN a.id IS NULL OR NOT regexp_full_match(a.id, '[0-9]{1,18}')
           THEN error('OurAirports id is not an integer: ' || coalesce(a.id, 'NULL') || ' (' || a.icao_code || ')')
         WHEN NOT regexp_full_match(a.icao_code, '[A-Z]{4}')
           THEN error('ICAO code is not four capital letters: ' || a.icao_code || ' (id ' || a.id || ')')
         WHEN a.name IS NULL OR trim(a.name) = ''
           THEN error('airport has no name: ' || a.icao_code)
         WHEN a.type IS NULL OR a.type NOT IN ('small_airport', 'medium_airport', 'large_airport', 'heliport', 'seaplane_base')
           THEN error('airport type is not one load knows: ' || coalesce(a.type, 'NULL') || ' (' || a.icao_code || ')')
         WHEN a.scheduled_service IS NULL OR a.scheduled_service NOT IN ('yes', 'no')
           THEN error('scheduled_service is not yes or no: ' || coalesce(a.scheduled_service, 'NULL') || ' (' || a.icao_code || ')')
         WHEN coalesce(try_cast(a.latitude_deg AS DOUBLE) NOT BETWEEN -90 AND 90, true)
           THEN error('latitude is not a number between -90 and 90: ' || coalesce(a.latitude_deg, 'NULL') || ' (' || a.icao_code || ')')
         WHEN coalesce(try_cast(a.longitude_deg AS DOUBLE) NOT BETWEEN -180 AND 180, true)
           THEN error('longitude is not a number between -180 and 180: ' || coalesce(a.longitude_deg, 'NULL') || ' (' || a.icao_code || ')')
         WHEN a.elevation_ft IS NOT NULL AND NOT regexp_full_match(a.elevation_ft, '-?[0-9]{1,6}')
           THEN error('elevation is not an integer: ' || a.elevation_ft || ' (' || a.icao_code || ')')
         WHEN c.code IS NULL
           THEN error('country not in OurAirports'' countries file: ' || coalesce(a.iso_country, 'NULL') || ' (' || a.icao_code || ')')
         WHEN r.code IS NULL
           THEN error('region not in OurAirports'' regions file: ' || coalesce(a.iso_region, 'NULL') || ' (' || a.icao_code || ')')
         WHEN r.iso_country IS DISTINCT FROM a.iso_country
           THEN error('region is not in the airport''s country: ' || a.iso_region || ' in ' || a.iso_country || ' (' || a.icao_code || ')')
         ELSE a.icao_code END AS code,
    a.id, a.ident, a.type, a.name, a.latitude_deg, a.longitude_deg, a.elevation_ft,
    a.continent, a.iso_country, c.name AS country_name, a.iso_region,
    r.name AS region_name, a.municipality, a.scheduled_service, a.gps_code,
    a.local_code, a.home_link, a.wikipedia_link
  FROM airport_rows a
  LEFT JOIN countries c ON c.code = a.iso_country
  LEFT JOIN regions r ON r.code = a.iso_region
  WHERE a.icao_code IS NOT NULL
    AND NOT (a.id = 'id' AND a.ident = 'ident' AND a.icao_code = 'icao_code')
),
icao_unique AS (
  SELECT CASE WHEN count(*) > 0
              THEN error('OurAirports repeats an ICAO code: ' || min(code))
              ELSE true END AS ok
  FROM (SELECT code FROM airports GROUP BY code HAVING count(*) > 1)
),
-- icao_share is here too, so an airports file whose rows all lack an ICAO code, which
-- leaves the airports CTE empty, is refused here. A file with a header and no rows at
-- all passes the floor (0 of 0) and builds an empty table, which check_code_list then
-- refuses.
checks AS (
  SELECT u.ok AND s.ok AS ok FROM icao_unique u, icao_share s
)
SELECT
  a.code, a.code AS code_display, 'airport' AS level, 0::INTEGER AS depth,
  NULL::VARCHAR AS parent_code, a.name AS description, a.name AS description_full,
  airports_release.release, 'ourairports' AS text_source,
  a.id::BIGINT AS ourairports_id, a.ident, a.type,
  a.latitude_deg::DOUBLE AS latitude_deg, a.longitude_deg::DOUBLE AS longitude_deg,
  a.elevation_ft::INTEGER AS elevation_ft, a.continent, a.iso_country, a.country_name,
  a.iso_region, a.region_name, a.municipality,
  (a.scheduled_service = 'yes') AS scheduled_service,
  a.gps_code, a.local_code, a.home_link, a.wikipedia_link
FROM airports a
CROSS JOIN airports_release
CROSS JOIN checks
WHERE checks.ok;
