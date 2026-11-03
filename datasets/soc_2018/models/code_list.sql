-- Build the `bls/soc_2018` code list from the staging tables `load.sql` made.
-- Run by the `code_list` step.
--
-- Levels are BLS's own (Major, Minor, Broad, Detailed), lowercased. SOC codes are not
-- plain prefixes: a broad group 11-3110 sits under the minor group 11-3000 but 15-1210
-- under 15-1200, and the detailed occupations 29-1211 to 29-1229 all sit under the broad
-- group 29-1210, not under the 29-1220 their last digit would name. `parent_code` is the
-- code one level up that shares the most leading digits and is the nearest at or below
-- the row's own code:
--   minor    -> its major group, XX-0000;
--   broad    -> the highest minor group at or below it that shares its first four
--               characters (NN-N);
--   detailed -> the highest broad group at or below it that shares its first five
--               characters (NN-NN).
-- A row below the top that finds no such parent fails the run.
--
-- `description_full` is each ancestor's own title in order, then the row's, joined with
-- ' › '. Titles repeat where a broad group and its only detailed occupation share one;
-- the repeat is kept, as BLS prints both.
CREATE OR REPLACE TABLE soc_2018_out AS
WITH src AS (
  SELECT
    CASE WHEN regexp_matches(code, '^[0-9]{2}-[0-9]{4}$') THEN code
         ELSE error('BLS SOC code is not NN-NNNN: ' || coalesce(code, 'NULL')) END AS code,
    title, definition,
    CASE soc_group WHEN 'Major' THEN 'major' WHEN 'Minor' THEN 'minor'
                   WHEN 'Broad' THEN 'broad' WHEN 'Detailed' THEN 'detailed'
         ELSE error('BLS SOC group is not Major, Minor, Broad or Detailed: '
                    || coalesce(soc_group, 'NULL') || ' for ' || coalesce(code, 'NULL')) END AS level
  FROM bls_definitions
), shaped AS (
  SELECT *,
    CASE level WHEN 'major' THEN 0 WHEN 'minor' THEN 1 WHEN 'broad' THEN 2 ELSE 3 END AS depth,
    CASE level
      WHEN 'major' THEN NULL
      WHEN 'minor' THEN substr(code, 1, 2) || '-0000'
      WHEN 'broad' THEN (SELECT max(q.code) FROM src q WHERE q.level = 'minor'
                           AND q.code <= src.code AND substr(q.code, 1, 4) = substr(src.code, 1, 4))
      ELSE (SELECT max(q.code) FROM src q WHERE q.level = 'broad'
              AND q.code <= src.code AND substr(q.code, 1, 5) = substr(src.code, 1, 5))
    END AS want_parent
  FROM src
), parented AS (
  SELECT s.* EXCLUDE (want_parent),
    CASE WHEN s.level = 'major' THEN NULL
         ELSE coalesce(
           (SELECT p.code FROM src p
             WHERE p.code = s.want_parent
               AND p.level = CASE s.level WHEN 'minor' THEN 'major' WHEN 'broad' THEN 'minor' ELSE 'broad' END),
           error('BLS SOC ' || s.level || ' ' || s.code || ' has no parent at the level above')) END AS parent_code
  FROM shaped s
)
SELECT
  c.code,
  c.code AS code_display,
  c.level,
  c.depth,
  c.parent_code,
  c.title AS description,
  concat_ws(' › ', ma.title, mi.title, br.title, c.title) AS description_full,
  (SELECT release FROM bls_release) AS release,
  'bls' AS text_source,
  lower(strip_accents(concat_ws(' › ', ma.title, mi.title, br.title, c.title))) AS corpus,
  CASE WHEN c.level = 'detailed' THEN c.definition END AS definition,
  ma.code AS major_code,
  mi.code AS minor_code,
  br.code AS broad_code
FROM parented c
LEFT JOIN parented par ON par.code = c.parent_code
LEFT JOIN parented br ON c.depth = 3 AND br.code = c.parent_code
LEFT JOIN parented mi ON mi.code = CASE c.depth WHEN 2 THEN c.parent_code WHEN 3 THEN par.parent_code END
LEFT JOIN parented ma ON ma.code = CASE WHEN c.depth >= 1 THEN substr(c.code, 1, 2) || '-0000' END
ORDER BY c.code;
