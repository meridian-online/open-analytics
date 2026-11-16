-- OurAirports' airports that carry an ICAO code: one row per ICAO code, from three of
-- OurAirports' files and the repository commit they were fetched at.
--
--   * airports.csv: id, ident, type, name, latitude_deg, longitude_deg, elevation_ft,
--     continent, iso_country, iso_region, municipality, scheduled_service, icao_code,
--     iata_code, gps_code, local_code, home_link, wikipedia_link, keywords.
--   * countries.csv: id, code, name, continent, wikipedia_link, keywords.
--   * regions.csv: id, code, local_code, name, continent, iso_country, wikipedia_link,
--     keywords.
--   * release_before.json and release_after.json: GitHub's answer for the latest commit
--     on the data repository's main branch, fetched before and after the three files.
--
-- Each CSV is read with every field as text and header = false, so the header is found
-- by its text, not by its position in the scan: exactly one row must be OurAirports'
-- header, and a header that has changed fails the run.
--
-- The release is the commit's UTC date. The two commit files must name the same commit,
-- so a repository that changed during the fetch is never published under one date.
--
-- IATA's codes are not republished: iata_code and keywords (free text in which IATA
-- codes appear) are read only so the header can be checked, and dropped here.
--
-- The run also fails on: an ICAO code that is not four capital letters, or that repeats;
-- a country not in countries.csv; a region not in regions.csv, or one of another country;
-- an airport type outside the five load knows; a scheduled_service other than yes or no;
-- a latitude or longitude that is not a number in range; an elevation that is not an
-- integer; an empty name; and a country or region code that countries.csv or regions.csv
-- repeats.

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
                header = false, delim = ',', quote = '"', escape = '"')
),
country_rows AS (
  SELECT *
  FROM read_csv('build/ourairports/countries.csv',
                columns = {'id': 'VARCHAR', 'code': 'VARCHAR', 'name': 'VARCHAR',
                           'continent': 'VARCHAR', 'wikipedia_link': 'VARCHAR',
                           'keywords': 'VARCHAR'},
                header = false, delim = ',', quote = '"', escape = '"')
),
region_rows AS (
  SELECT *
  FROM read_csv('build/ourairports/regions.csv',
                columns = {'id': 'VARCHAR', 'code': 'VARCHAR', 'local_code': 'VARCHAR',
                           'name': 'VARCHAR', 'continent': 'VARCHAR',
                           'iso_country': 'VARCHAR', 'wikipedia_link': 'VARCHAR',
                           'keywords': 'VARCHAR'},
                header = false, delim = ',', quote = '"', escape = '"')
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
-- The release: the commit both files name, and its UTC date.
release_before AS (
  SELECT sha, "commit".committer.date AS committed
  FROM read_json('build/ourairports/release_before.json',
                 columns = {'sha': 'VARCHAR', 'commit': 'STRUCT(committer STRUCT(date VARCHAR))'})
),
release_after AS (
  SELECT sha, "commit".committer.date AS committed
  FROM read_json('build/ourairports/release_after.json',
                 columns = {'sha': 'VARCHAR', 'commit': 'STRUCT(committer STRUCT(date VARCHAR))'})
),
airports_release AS (
  SELECT CASE
           WHEN (SELECT count(*) FROM release_before) <> 1
             OR (SELECT count(*) FROM release_after) <> 1
             THEN error('expected one commit in each OurAirports release file')
           WHEN (SELECT sha FROM release_before) IS DISTINCT FROM (SELECT sha FROM release_after)
             THEN error('OurAirports changed during the fetch: commit '
                        || coalesce((SELECT sha FROM release_before), 'NULL') || ' before, '
                        || coalesce((SELECT sha FROM release_after), 'NULL') || ' after')
           WHEN try_cast((SELECT committed FROM release_before) AS TIMESTAMPTZ) IS NULL
             THEN error('OurAirports commit date does not parse: '
                        || coalesce((SELECT committed FROM release_before), 'NULL'))
           ELSE strftime(try_cast((SELECT committed FROM release_before) AS TIMESTAMPTZ)
                         AT TIME ZONE 'UTC', '%Y-%m-%d')
         END AS release
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
-- Data rows: the airports with an ICAO code, each checked for shape and against the
-- countries and regions files, in that order.
airports AS (
  SELECT
    CASE WHEN NOT (SELECT ok FROM airports_header) THEN NULL
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
checks AS (
  SELECT l.ok AND u.ok AS ok FROM lookups_unique l, icao_unique u
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
