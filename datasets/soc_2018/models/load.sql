-- Stage BLS's three SOC 2018 workbooks. Run by the `load` step (needs DuckDB's excel
-- extension); paths resolve from the Protocol directory.
--
-- Each workbook has a title block above its table. Measured 2026-11-03: in the
-- definitions and the direct match title file the release line is cell A4, the column
-- headers are row 8 and the data starts at row 9; in the 2010 to 2018 crosswalk the
-- release line is A4, the headers are row 9 and the data starts at row 10. Every sheet is
-- read as text, with no header row, from an explicit range. The header row's text is
-- checked before any data is read, so a sheet whose layout has moved fails the run
-- instead of staging the wrong cells.
--
-- Staging tables: bls_definitions, bls_titles, bls_crosswalk, and bls_release, one row
-- holding the release `code_list.sql` stamps on every row ('2018/2020-04-15', the edition
-- and the date of the definitions file's last update) and the crosswalk's own
-- ('2017-11').
INSTALL excel; LOAD excel;

-- The header rows, each compared with the text BLS prints.
SELECT CASE WHEN (A, B, C, D) IS NOT DISTINCT FROM
         ('SOC Group', 'SOC Code', 'SOC Title', 'SOC Definition') THEN 1
       ELSE error('BLS definitions header row has moved: expected SOC Group, SOC Code, SOC Title, SOC Definition in A8:D8, found '
                  || coalesce(A, 'NULL') || ', ' || coalesce(B, 'NULL') || ', ' || coalesce(C, 'NULL') || ', ' || coalesce(D, 'NULL')) END
FROM read_xlsx('build/bls/soc_2018_definitions.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A8:D8');

SELECT CASE WHEN (A, B, C, D) IS NOT DISTINCT FROM
         ('2018 SOC Code', '2018 SOC Title', '2018 SOC Direct Match Title', 'Illustrative Example') THEN 1
       ELSE error('BLS direct match title header row has moved: expected 2018 SOC Code, 2018 SOC Title, 2018 SOC Direct Match Title, Illustrative Example in A8:D8, found '
                  || coalesce(A, 'NULL') || ', ' || coalesce(B, 'NULL') || ', ' || coalesce(C, 'NULL') || ', ' || coalesce(D, 'NULL')) END
FROM read_xlsx('build/bls/soc_2018_direct_match_title_file.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A8:D8');

SELECT CASE WHEN (A, B, C, D) IS NOT DISTINCT FROM
         ('2010 SOC Code', '2010 SOC Title', '2018 SOC Code', '2018 SOC Title') THEN 1
       ELSE error('BLS crosswalk header row has moved: expected 2010 SOC Code, 2010 SOC Title, 2018 SOC Code, 2018 SOC Title in A9:D9, found '
                  || coalesce(A, 'NULL') || ', ' || coalesce(B, 'NULL') || ', ' || coalesce(C, 'NULL') || ', ' || coalesce(D, 'NULL')) END
FROM read_xlsx('build/bls/soc_2010_to_2018_crosswalk.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A9:D9');

-- The releases. The definitions file reads "November 2017 (for reference year January
-- 2018 - Updated April 15, 2020)"; the release is the reference year and the update date.
-- The direct match title file must agree on the update date, so the two files cannot come
-- from different revisions. The crosswalk reads "November 2017".
CREATE OR REPLACE TABLE bls_release AS
WITH lines AS (
  SELECT
    (SELECT A FROM read_xlsx('build/bls/soc_2018_definitions.xlsx', header = false, all_varchar = true,
                             stop_at_empty = false, range = 'A4:A4')) AS definitions_line,
    (SELECT A FROM read_xlsx('build/bls/soc_2018_direct_match_title_file.xlsx', header = false, all_varchar = true,
                             stop_at_empty = false, range = 'A4:A4')) AS titles_line,
    (SELECT A FROM read_xlsx('build/bls/soc_2010_to_2018_crosswalk.xlsx', header = false, all_varchar = true,
                             stop_at_empty = false, range = 'A4:A4')) AS crosswalk_line
), parsed AS (
  SELECT
    definitions_line, titles_line, crosswalk_line,
    regexp_extract(definitions_line, 'reference year [A-Za-z]+ ([0-9]{4})', 1) AS reference_year,
    try_strptime(regexp_extract(definitions_line, 'Updated ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})', 1), '%B %d, %Y') AS updated,
    try_strptime(regexp_extract(titles_line, 'Updated ([A-Za-z]+ [0-9]{1,2}, [0-9]{4})', 1), '%B %d, %Y') AS titles_updated,
    try_strptime(trim(crosswalk_line), '%B %Y') AS crosswalk_month
  FROM lines
)
SELECT
  CASE WHEN reference_year = '' OR updated IS NULL
       THEN error('BLS definitions release line does not parse as "reference year <Month> <yyyy> ... Updated <Month> <d>, <yyyy>": ' || coalesce(definitions_line, 'NULL'))
       WHEN titles_updated IS DISTINCT FROM updated
       THEN error('BLS direct match title file release line "' || coalesce(titles_line, 'NULL') || '" does not give the update date of the definitions file: ' || definitions_line)
       ELSE reference_year || '/' || strftime(updated, '%Y-%m-%d') END AS release,
  CASE WHEN crosswalk_month IS NULL
       THEN error('BLS crosswalk release line is not "<Month> <yyyy>": ' || coalesce(crosswalk_line, 'NULL'))
       ELSE strftime(crosswalk_month, '%Y-%m') END AS crosswalk_release
FROM parsed;

-- `sheet_row` is the cell row in the workbook, taken in sheet order before blank rows are
-- dropped; `code_list.sql` uses it to check each parent against BLS's own ordering.
CREATE OR REPLACE TABLE bls_definitions AS
SELECT
  sheet_row, trim(A) AS soc_group, trim(B) AS code, trim(C) AS title,
  nullif(trim(regexp_replace(D, '\s+', ' ', 'g')), '') AS definition
FROM (
  SELECT 8 + row_number() OVER () AS sheet_row, *
  FROM read_xlsx('build/bls/soc_2018_definitions.xlsx', header = false, all_varchar = true,
                 stop_at_empty = false, range = 'A9:D')
)
WHERE coalesce(A, B, C, D) IS NOT NULL;

CREATE OR REPLACE TABLE bls_titles AS
SELECT
  trim(A) AS code, trim(B) AS title, trim(C) AS direct_match_title,
  nullif(trim(D), '') AS illustrative
FROM read_xlsx('build/bls/soc_2018_direct_match_title_file.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A9:D')
WHERE coalesce(A, B, C, D) IS NOT NULL;

CREATE OR REPLACE TABLE bls_crosswalk AS
SELECT
  trim(A) AS code_2010, trim(B) AS title_2010, trim(C) AS code_2018, trim(D) AS title_2018
FROM read_xlsx('build/bls/soc_2010_to_2018_crosswalk.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A10:D')
WHERE coalesce(A, B, C, D) IS NOT NULL;
