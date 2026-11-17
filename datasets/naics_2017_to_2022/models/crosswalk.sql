-- Build the `census/naics_2017_to_2022` edition crosswalk from `naics_concordance`
-- (load.sql). Run by the `crosswalk` step.
--
-- Census's own pairings of 2017 and 2022 national industries, unweighted, as published;
-- the titles are the ones Census prints in the concordance, so a split 2017 industry's
-- title names the piece that went to each 2022 industry. `release` is the edition the
-- crosswalk bridges to: Census's file states no release of its own (see the manifest's
-- header).
CREATE OR REPLACE TABLE naics_2017_to_2022_out AS
SELECT
  code_2017 AS from_code,
  title_2017 AS from_description,
  code_2022 AS to_code,
  title_2022 AS to_description,
  '2017' AS from_edition,
  '2022' AS to_edition,
  '2022' AS release
FROM naics_concordance
ORDER BY code_2017, code_2022;
