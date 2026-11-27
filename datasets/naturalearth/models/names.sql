-- The country_names table: one row per spelling of a country's name or code, folded to
-- a key (lower-cased, trimmed and accent-stripped, `strip_accents(lower(trim(x)))`), and
-- the iso_a3 it means. A reader folds its own column the same way and joins on name_key.
--
-- Each spelling comes from a source, and the sources are ranked:
--   0  aliases.csv, the spellings Meridian adds with a reason (usa, uk, rsa, eire);
--   1  the country's own codes and English names: iso_a3, iso_a2, NAME, NAME_LONG,
--      FORMAL_EN, ADMIN, NAME_ALT;
--   2  its name in each of Natural Earth's languages, NAME_AR to NAME_ZHT;
--   3  SOVEREIGNT, the country it belongs to, which Natural Earth gives a territory too.
-- A key resolves to the country that carries it at the best rank it appears at, so
-- "france" is France, though New Caledonia's SOVEREIGNT is France too, and "ao" is
-- Angola's code, though it is also Austria's name in Vietnamese. A key two countries
-- carry at the same best rank means either, and is dropped: country_names_dropped lists
-- each, and the step prints them. The run fails on an alias whose iso_a3 is not a
-- country here, or an alias repeated.

CREATE OR REPLACE TABLE ne_aliases AS
WITH a AS (
  SELECT alias, iso_a3, reason
  FROM read_csv('aliases.csv', header = true,
                columns = {'alias': 'VARCHAR', 'iso_a3': 'VARCHAR', 'reason': 'VARCHAR'})
)
SELECT
  CASE
    WHEN a.iso_a3 NOT IN (SELECT iso_a3 FROM ne_countries)
      THEN error('aliases.csv: ' || a.alias || ' names ' || a.iso_a3 || ', which is not a Natural Earth country')
    WHEN coalesce(trim(a.reason), '') = ''
      THEN error('aliases.csv: ' || a.alias || ' has no reason')
    WHEN count(*) OVER (PARTITION BY strip_accents(lower(trim(a.alias)))) > 1
      THEN error('aliases.csv: ' || a.alias || ' is repeated')
    ELSE a.alias
  END AS alias,
  a.iso_a3
FROM a;

CREATE OR REPLACE TABLE ne_spellings AS
SELECT alias AS spelling, iso_a3, 0 AS rank, 'aliases.csv' AS source FROM ne_aliases
UNION ALL
SELECT s.spelling, c.iso_a3, s.rank, s.source
FROM ne_countries AS c,
LATERAL (VALUES
  (c.iso_a3, 1, 'iso_a3'), (c.iso_a2, 1, 'iso_a2'),
  (c.NAME, 1, 'NAME'), (c.NAME_LONG, 1, 'NAME_LONG'), (c.FORMAL_EN, 1, 'FORMAL_EN'),
  (c.ADMIN, 1, 'ADMIN'), (c.NAME_ALT, 1, 'NAME_ALT'),
  (c.NAME_AR, 2, 'NAME_AR'), (c.NAME_BN, 2, 'NAME_BN'), (c.NAME_DE, 2, 'NAME_DE'),
  (c.NAME_EN, 2, 'NAME_EN'), (c.NAME_ES, 2, 'NAME_ES'), (c.NAME_FA, 2, 'NAME_FA'),
  (c.NAME_FR, 2, 'NAME_FR'), (c.NAME_EL, 2, 'NAME_EL'), (c.NAME_HE, 2, 'NAME_HE'),
  (c.NAME_HI, 2, 'NAME_HI'), (c.NAME_HU, 2, 'NAME_HU'), (c.NAME_ID, 2, 'NAME_ID'),
  (c.NAME_IT, 2, 'NAME_IT'), (c.NAME_JA, 2, 'NAME_JA'), (c.NAME_KO, 2, 'NAME_KO'),
  (c.NAME_NL, 2, 'NAME_NL'), (c.NAME_PL, 2, 'NAME_PL'), (c.NAME_PT, 2, 'NAME_PT'),
  (c.NAME_RU, 2, 'NAME_RU'), (c.NAME_SV, 2, 'NAME_SV'), (c.NAME_TR, 2, 'NAME_TR'),
  (c.NAME_UK, 2, 'NAME_UK'), (c.NAME_UR, 2, 'NAME_UR'), (c.NAME_VI, 2, 'NAME_VI'),
  (c.NAME_ZH, 2, 'NAME_ZH'), (c.NAME_ZHT, 2, 'NAME_ZHT'),
  (c.SOVEREIGNT, 3, 'SOVEREIGNT')
) AS s(spelling, rank, source)
WHERE s.spelling IS NOT NULL AND trim(s.spelling) <> '' AND s.spelling <> '-99';

-- Each key's best rank, and the countries that carry it there.
CREATE OR REPLACE TABLE ne_name_claims AS
WITH keyed AS (
  SELECT strip_accents(lower(trim(spelling))) AS name_key, iso_a3, rank, source
  FROM ne_spellings
),
best AS (
  SELECT name_key, min(rank) AS rank FROM keyed GROUP BY name_key
)
SELECT k.name_key, k.iso_a3, b.rank,
       string_agg(DISTINCT k.source, ', ' ORDER BY k.source) AS sources
FROM keyed AS k JOIN best AS b USING (name_key)
WHERE k.rank = b.rank
GROUP BY k.name_key, k.iso_a3, b.rank;

CREATE OR REPLACE TABLE country_names_dropped AS
SELECT name_key, rank,
       string_agg(iso_a3 || ' (' || sources || ')', '; ' ORDER BY iso_a3) AS countries
FROM ne_name_claims
GROUP BY name_key, rank
HAVING count(*) > 1;

CREATE OR REPLACE TABLE country_names_out AS
SELECT name_key, iso_a3
FROM ne_name_claims
WHERE name_key NOT IN (SELECT name_key FROM country_names_dropped);

-- Printed in the step's log: each key dropped because two countries carry it.
SELECT 'dropped, two countries carry it' AS note, name_key, rank, countries
FROM country_names_dropped ORDER BY name_key;
