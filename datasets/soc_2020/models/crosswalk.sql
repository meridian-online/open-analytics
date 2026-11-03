-- Build the `ons/soc_2010_to_2020` edition crosswalk from `ons_relationship` (load.sql).
-- Run by the `crosswalk` step.
--
-- ONS's own pairings at the unit-group level, with the titles ONS prints in the tables.
-- Each pairing carries its four shares (men and women, Labour Force Survey and Census) and
-- the four base counts of its 2010 group, from that group's "Total sample" row; a pairing
-- whose shares are all 0 is kept, as ONS prints it. `release` is the tables' own, not the
-- code list's.
CREATE OR REPLACE TABLE soc_2010_to_2020_out AS
SELECT
  code_2010 AS from_code,
  title_2010 AS from_description,
  code_2020 AS to_code,
  title_2020 AS to_description,
  '2010' AS from_edition,
  '2020' AS to_edition,
  (SELECT crosswalk_release FROM ons_release) AS release,
  men_lfs_base,
  women_lfs_base,
  men_census_base,
  women_census_base,
  men_lfs_share,
  women_lfs_share,
  men_census_share,
  women_census_share
FROM ons_relationship
ORDER BY code_2010, code_2020;
