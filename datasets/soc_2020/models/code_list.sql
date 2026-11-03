-- Build the `ons/soc_2020` code list from the staging tables `load.sql` made.
-- Run by the `code_list` step.
--
-- Levels are ONS's own (major, sub-major, minor and unit groups), lowercased, with the
-- hyphen the column name needs: major, sub_major, minor, unit. Each code is a prefix of
-- the codes below it, so `parent_code` is the code with its last digit dropped. A row
-- below the top whose parent is not a group of the level above fails the run. Row order
-- in the sheet plays no part, as the prefix names the parent.
--
-- `description` is ONS's title as Volume 1's descriptions sheet writes it.
-- `description_full` is each ancestor's own title in order, then the row's, joined with
-- ' › '. Titles repeat where a minor group and its only unit group share one; the repeat
-- is kept, as ONS prints both.
CREATE OR REPLACE TABLE soc_2020_out AS
WITH shaped AS (
  SELECT *,
    CASE level WHEN 'major' THEN 0 WHEN 'sub_major' THEN 1 WHEN 'minor' THEN 2 ELSE 3 END AS depth
  FROM ons_groups
), parented AS (
  SELECT s.*,
    CASE WHEN s.depth = 0 THEN NULL
         ELSE coalesce(
           (SELECT p.code FROM shaped p
             WHERE p.code = substr(s.code, 1, length(s.code) - 1) AND p.depth = s.depth - 1),
           error('ONS SOC 2020 ' || s.level || ' group ' || s.code || ' has no parent at the level above')) END AS parent_code
  FROM shaped s
), titled AS (
  SELECT c.*,
    concat_ws(' › ', CASE WHEN c.depth >= 1 THEN ma.title END, CASE WHEN c.depth >= 2 THEN sm.title END,
              CASE WHEN c.depth >= 3 THEN mi.title END, c.title) AS full_title,
    CASE WHEN c.depth >= 1 THEN ma.code END AS major_code,
    CASE WHEN c.depth >= 2 THEN sm.code END AS sub_major_code,
    CASE WHEN c.depth >= 3 THEN mi.code END AS minor_code
  FROM parented c
  LEFT JOIN parented ma ON ma.code = substr(c.code, 1, 1)
  LEFT JOIN parented sm ON sm.code = substr(c.code, 1, 2)
  LEFT JOIN parented mi ON mi.code = substr(c.code, 1, 3)
)
SELECT
  code,
  code AS code_display,
  level,
  depth,
  parent_code,
  title AS description,
  full_title AS description_full,
  (SELECT code_list_release FROM ons_release) AS release,
  'ons' AS text_source,
  lower(strip_accents(full_title)) AS corpus,
  group_description,
  entry_routes,
  tasks,
  related_job_titles,
  major_code,
  sub_major_code,
  minor_code
FROM titled
ORDER BY code;
