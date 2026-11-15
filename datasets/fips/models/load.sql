-- FIPS state and county codes: one row per state or state-equivalent and per county or
-- county-equivalent, from three Census Bureau files.
--
--   * national_state<vintage>.txt, the ANSI state codes: STATE|STATEFP|STATENS|STATE_NAME.
--   * national_county<vintage>.txt, the ANSI county codes:
--     STATE|STATEFP|COUNTYFP|COUNTYNS|COUNTYNAME|CLASSFP|FUNCSTAT.
--   * <vintage>_Gaz_counties_national.txt, the Gazetteer's counties of the 50 states, DC
--     and Puerto Rico in Census's current vintage:
--     USPS|GEOID|GEOIDFQ|ANSICODE|NAME|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG.
--
-- The ANSI lists are the base: they are the code list Census publishes, and they carry
-- the Island Areas and Census's class and functional-status codes. Census's ANSI page
-- serves the 2020 vintage, which predates Connecticut's change of 2022, when its nine
-- planning regions (09110 to 09190) replaced its eight counties (09001 to 09015) as
-- county-equivalents. The Gazetteer adds a county ANSI does not list, and decides
-- `current`: true for a code it lists (a county, or a state with a county in it), false
-- for an ANSI county in a state it covers that it no longer lists, NULL where it does not
-- cover the state (the Island Areas and the Minor Outlying Islands).
--
-- Each file is read as whole lines and split on '|', so the header is found by its text,
-- not by its position in the scan, and a header that has changed fails the run. The run
-- also fails on: a file name whose vintage does not parse, two ANSI vintages that differ,
-- a line with the wrong number of fields or a code of the wrong shape, a repeated code,
-- a county whose state is not in the state file, and a county both files list under a
-- different name or GNIS code (a rename is decided by a person, not published under the
-- older name).

CREATE OR REPLACE TABLE fips AS
WITH state_lines AS (
  SELECT line, filename
  FROM read_csv('build/census/national_state*.txt',
                columns = {'line': 'VARCHAR'}, delim = chr(1), quote = '', escape = '',
                header = false, filename = true)
  WHERE trim(line) <> ''
),
county_lines AS (
  SELECT line, filename
  FROM read_csv('build/census/national_county*.txt',
                columns = {'line': 'VARCHAR'}, delim = chr(1), quote = '', escape = '',
                header = false, filename = true)
  WHERE trim(line) <> ''
),
gazetteer_lines AS (
  SELECT rtrim(line) AS line, filename
  FROM read_csv('build/census/gazetteer/*_Gaz_counties_national.txt',
                columns = {'line': 'VARCHAR'}, delim = chr(1), quote = '', escape = '',
                header = false, filename = true)
  WHERE trim(line) <> ''
),
-- One file of each, its vintage from its name, and its header where Census prints it.
vintages AS (
  SELECT
    CASE WHEN (SELECT count(DISTINCT filename) FROM state_lines) <> 1
         THEN error('expected one ANSI state file, found '
                    || (SELECT count(DISTINCT filename) FROM state_lines))
         ELSE regexp_extract((SELECT any_value(filename) FROM state_lines),
                             'national_state([0-9]{4})\.txt$', 1) END AS state_vintage,
    CASE WHEN (SELECT count(DISTINCT filename) FROM county_lines) <> 1
         THEN error('expected one ANSI county file, found '
                    || (SELECT count(DISTINCT filename) FROM county_lines))
         ELSE regexp_extract((SELECT any_value(filename) FROM county_lines),
                             'national_county([0-9]{4})\.txt$', 1) END AS county_vintage,
    CASE WHEN (SELECT count(DISTINCT filename) FROM gazetteer_lines) <> 1
         THEN error('expected one Gazetteer county file, found '
                    || (SELECT count(DISTINCT filename) FROM gazetteer_lines))
         ELSE regexp_extract((SELECT any_value(filename) FROM gazetteer_lines),
                             '([0-9]{4})_Gaz_counties_national\.txt$', 1) END
      AS gazetteer_vintage
),
fips_release AS (
  SELECT CASE
           WHEN state_vintage = '' THEN error('ANSI state file name does not parse as national_state<year>.txt')
           WHEN county_vintage = '' THEN error('ANSI county file name does not parse as national_county<year>.txt')
           WHEN gazetteer_vintage = '' THEN error('Gazetteer file name does not parse as <year>_Gaz_counties_national.txt')
           WHEN state_vintage <> county_vintage
             THEN error('ANSI state and county files are of different vintages: '
                        || state_vintage || ' and ' || county_vintage)
           WHEN (SELECT count(*) FROM state_lines
                 WHERE line = 'STATE|STATEFP|STATENS|STATE_NAME') <> 1
             THEN error('ANSI state header has changed: expected STATE|STATEFP|STATENS|STATE_NAME')
           WHEN (SELECT count(*) FROM county_lines
                 WHERE line = 'STATE|STATEFP|COUNTYFP|COUNTYNS|COUNTYNAME|CLASSFP|FUNCSTAT') <> 1
             THEN error('ANSI county header has changed: expected STATE|STATEFP|COUNTYFP|COUNTYNS|COUNTYNAME|CLASSFP|FUNCSTAT')
           WHEN (SELECT count(*) FROM gazetteer_lines
                 WHERE line = 'USPS|GEOID|GEOIDFQ|ANSICODE|NAME|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG') <> 1
             THEN error('Gazetteer header has changed: expected USPS|GEOID|GEOIDFQ|ANSICODE|NAME|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG')
           ELSE 'ANSI ' || state_vintage || ', Gazetteer ' || gazetteer_vintage
         END AS release
  FROM vintages
),
-- Data lines: every line but the header, split into its fields, each checked for shape.
ansi_states AS (
  SELECT
    CASE WHEN len(f) <> 4 THEN error('ANSI state line does not have 4 fields: ' || line)
         WHEN NOT regexp_full_match(f[1], '[A-Z]{2}') THEN error('ANSI state abbreviation is not two letters: ' || line)
         WHEN NOT regexp_full_match(f[2], '[0-9]{2}') THEN error('ANSI state code is not two digits: ' || line)
         WHEN NOT regexp_full_match(f[3], '[0-9]{8}') THEN error('ANSI state GNIS code is not eight digits: ' || line)
         WHEN trim(f[4]) = '' THEN error('ANSI state has no name: ' || line)
         ELSE f[2] END AS state_code,
    f[1] AS state_abbr, f[3] AS gnis_code, f[4] AS name
  FROM (SELECT line, string_split(line, '|') AS f FROM state_lines
        WHERE line <> 'STATE|STATEFP|STATENS|STATE_NAME')
),
ansi_counties AS (
  SELECT
    CASE WHEN len(f) <> 7 THEN error('ANSI county line does not have 7 fields: ' || line)
         WHEN NOT regexp_full_match(f[2], '[0-9]{2}') THEN error('ANSI county state code is not two digits: ' || line)
         WHEN NOT regexp_full_match(f[3], '[0-9]{3}') THEN error('ANSI county code is not three digits: ' || line)
         WHEN NOT regexp_full_match(f[4], '[0-9]{8}') THEN error('ANSI county GNIS code is not eight digits: ' || line)
         WHEN trim(f[5]) = '' THEN error('ANSI county has no name: ' || line)
         WHEN NOT regexp_full_match(f[6], '[A-Z][0-9]') THEN error('ANSI county class code is not a letter and a digit: ' || line)
         WHEN NOT regexp_full_match(f[7], '[A-Z]') THEN error('ANSI county functional status is not one letter: ' || line)
         ELSE f[2] || f[3] END AS code,
    f[1] AS state_abbr, f[2] AS state_code, f[3] AS county_code, f[4] AS gnis_code,
    f[5] AS name, f[6] AS class_code, f[7] AS functional_status
  FROM (SELECT line, string_split(line, '|') AS f FROM county_lines
        WHERE line <> 'STATE|STATEFP|COUNTYFP|COUNTYNS|COUNTYNAME|CLASSFP|FUNCSTAT')
),
gazetteer AS (
  SELECT
    CASE WHEN len(f) <> 11 THEN error('Gazetteer line does not have 11 fields: ' || line)
         WHEN NOT regexp_full_match(f[1], '[A-Z]{2}') THEN error('Gazetteer state abbreviation is not two letters: ' || line)
         WHEN NOT regexp_full_match(f[2], '[0-9]{5}') THEN error('Gazetteer county code is not five digits: ' || line)
         WHEN NOT regexp_full_match(f[4], '[0-9]{8}') THEN error('Gazetteer GNIS code is not eight digits: ' || line)
         WHEN trim(f[5]) = '' THEN error('Gazetteer county has no name: ' || line)
         ELSE f[2] END AS code,
    f[1] AS state_abbr, f[4] AS gnis_code, f[5] AS name
  FROM (SELECT line, string_split(line, '|') AS f FROM gazetteer_lines
        WHERE line <> 'USPS|GEOID|GEOIDFQ|ANSICODE|NAME|ALAND|AWATER|ALAND_SQMI|AWATER_SQMI|INTPTLAT|INTPTLONG')
),
-- The states the Gazetteer covers, by the counties it lists in them.
covered AS (
  SELECT DISTINCT substr(code, 1, 2) AS state_code FROM gazetteer
),
-- Cross-file checks, each a row that fails the run if any offending row exists.
county_states_known AS (
  SELECT CASE WHEN count(*) > 0
              THEN error('county whose state is not in the ANSI state file: ' || min(code))
              ELSE true END AS ok
  FROM (SELECT code FROM ansi_counties WHERE state_code NOT IN (SELECT state_code FROM ansi_states)
        UNION ALL
        SELECT code FROM gazetteer WHERE substr(code, 1, 2) NOT IN (SELECT state_code FROM ansi_states))
),
gazetteer_states_agree AS (
  SELECT CASE WHEN count(*) > 0
              THEN error('Gazetteer state abbreviation differs from the ANSI state file: ' || min(g.code))
              ELSE true END AS ok
  FROM gazetteer g JOIN ansi_states s ON substr(g.code, 1, 2) = s.state_code
  WHERE g.state_abbr <> s.state_abbr
),
names_agree AS (
  SELECT CASE WHEN count(*) FILTER (WHERE a.name <> g.name) > 0
              THEN error('county name differs between the ANSI and Gazetteer files: '
                         || min(a.code) FILTER (WHERE a.name <> g.name))
              WHEN count(*) FILTER (WHERE a.gnis_code <> g.gnis_code) > 0
              THEN error('county GNIS code differs between the ANSI and Gazetteer files: '
                         || min(a.code) FILTER (WHERE a.gnis_code <> g.gnis_code))
              ELSE true END AS ok
  FROM ansi_counties a JOIN gazetteer g ON a.code = g.code
),
unique_codes AS (
  SELECT CASE WHEN (SELECT count(*) - count(DISTINCT state_code) FROM ansi_states) > 0
              THEN error('ANSI state file repeats a state code')
              WHEN (SELECT count(*) - count(DISTINCT code) FROM ansi_counties) > 0
              THEN error('ANSI county file repeats a county code')
              WHEN (SELECT count(*) - count(DISTINCT code) FROM gazetteer) > 0
              THEN error('Gazetteer repeats a county code')
              ELSE true END AS ok
),
checks AS (
  SELECT c.ok AND s.ok AND n.ok AND u.ok AS ok
  FROM county_states_known c, gazetteer_states_agree s, names_agree n, unique_codes u
),
states AS (
  SELECT
    s.state_code AS code, 'state' AS level, 0 AS depth, NULL::VARCHAR AS parent_code,
    s.name AS description, s.name AS description_full, 'census_ansi' AS text_source,
    s.state_code, NULL::VARCHAR AS county_code, s.state_abbr, s.gnis_code,
    NULL::VARCHAR AS class_code, NULL::VARCHAR AS functional_status,
    CASE WHEN s.state_code IN (SELECT state_code FROM covered) THEN true END AS current
  FROM ansi_states s
),
counties AS (
  SELECT
    a.code, 'county' AS level, 1 AS depth, a.state_code AS parent_code,
    a.name AS description, s.name || ' › ' || a.name AS description_full,
    'census_ansi' AS text_source,
    a.state_code, a.county_code, a.state_abbr, a.gnis_code, a.class_code,
    a.functional_status,
    CASE WHEN a.code IN (SELECT code FROM gazetteer) THEN true
         WHEN a.state_code IN (SELECT state_code FROM covered) THEN false
    END AS current
  FROM ansi_counties a JOIN ansi_states s USING (state_code)
  UNION ALL
  SELECT
    g.code, 'county', 1, substr(g.code, 1, 2),
    g.name, s.name || ' › ' || g.name, 'census_gazetteer',
    substr(g.code, 1, 2), substr(g.code, 3, 3), g.state_abbr, g.gnis_code,
    NULL, NULL, true
  FROM gazetteer g JOIN ansi_states s ON substr(g.code, 1, 2) = s.state_code
  WHERE g.code NOT IN (SELECT code FROM ansi_counties)
)
SELECT
  r.code, r.code AS code_display, r.level, r.depth, r.parent_code, r.description,
  r.description_full, fips_release.release, r.text_source,
  r.state_code, r.county_code, r.state_abbr, r.gnis_code, r.class_code,
  r.functional_status, r.current
FROM (SELECT * FROM states UNION ALL SELECT * FROM counties) r
CROSS JOIN fips_release
CROSS JOIN checks
WHERE checks.ok;
