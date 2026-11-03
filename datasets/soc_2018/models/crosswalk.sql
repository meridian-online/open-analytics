-- Build the `bls/soc_2010_to_2018` edition crosswalk from `bls_crosswalk` (load.sql).
-- Run by the `crosswalk` step.
--
-- BLS's own pairings at the detailed level, unweighted, as published; the titles are the
-- ones BLS prints in the crosswalk file. `release` is the crosswalk's own, not the code
-- list's.
-- `bls_release` (one row) is joined rather than read in a scalar subquery, so arc records it
-- as an input of this step and a release-only change reruns it.
CREATE OR REPLACE TABLE soc_2010_to_2018_out AS
SELECT
  code_2010 AS from_code,
  title_2010 AS from_description,
  code_2018 AS to_code,
  title_2018 AS to_description,
  '2010' AS from_edition,
  '2018' AS to_edition,
  r.crosswalk_release AS release
FROM bls_crosswalk
CROSS JOIN bls_release r
ORDER BY code_2010, code_2018;
