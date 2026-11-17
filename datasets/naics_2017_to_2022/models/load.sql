-- Stage Census's 2017 to 2022 NAICS concordance. Run by the `load` step (needs DuckDB's
-- excel extension); paths resolve from the Protocol directory.
--
-- The workbook is one sheet. Measured 2026-11-17: A1 is the title "2017 NAICS U.S.
-- Matched to 2022 NAICS U.S. (Full Concordance)", A2 a note on what Census's bold and
-- italics mean, A3:D3 the header, and rows 4 to 1153 are 1,150 pairings of a 2017 code
-- and title with a 2022 code and title, codes stored as numbers. Columns E to I hold 19
-- cells of whitespace and nothing else.
--
-- The sheet is read as text from explicit ranges, with no header row, so a code stored
-- as a number reads as its digits. The title and header are checked before any data is
-- read, so a sheet whose layout has moved fails the run instead of staging the wrong
-- cells. No step numbers the rows, so nothing here rests on the order the scan delivers
-- them.
--
-- Staging table: naics_concordance(code_2017, title_2017, code_2022, title_2022), one row
-- per pairing. Titles are Census's text with spaces trimmed from their ends (two end in
-- spaces); everything else, Census's typo "excpet" included, is kept as printed.
INSTALL excel; LOAD excel;

SELECT CASE WHEN A IS NOT DISTINCT FROM '2017 NAICS U.S. Matched to 2022 NAICS U.S. (Full Concordance)' THEN 1
       ELSE error('NAICS concordance title has moved: expected 2017 NAICS U.S. Matched to 2022 NAICS U.S. (Full Concordance) in A1, found '
                  || coalesce(A, 'NULL')) END
FROM read_xlsx('build/census/2017_to_2022_NAICS.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A1:A1');

-- Census's second header cell runs over two lines; only its first line is checked.
SELECT CASE WHEN (A, split_part(B, chr(10), 1), C, D)
                 IS NOT DISTINCT FROM ('2017 NAICS Code', '2017 NAICS Title', '2022 NAICS Code', '2022 NAICS Title') THEN 1
       ELSE error('NAICS concordance header row has moved: expected 2017 NAICS Code, 2017 NAICS Title, 2022 NAICS Code, 2022 NAICS Title in A3:D3, found '
                  || coalesce(A, 'NULL') || ', ' || coalesce(split_part(B, chr(10), 1), 'NULL') || ', '
                  || coalesce(C, 'NULL') || ', ' || coalesce(D, 'NULL')) END
FROM read_xlsx('build/census/2017_to_2022_NAICS.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A3:D3');

-- A value beyond column D is a column Census added (a weight, a note), which would
-- otherwise be dropped unseen. Census's file has whitespace in E to I and nothing else.
-- Every cell from E to Z is read; UNPIVOT drops the empty ones.
SELECT error('NAICS concordance has a value beyond column D, in ' || col || ': ' || v)
FROM (
  UNPIVOT (SELECT * FROM read_xlsx('build/census/2017_to_2022_NAICS.xlsx', header = false, all_varchar = true,
                                   stop_at_empty = false, range = 'E1:Z'))
  ON COLUMNS(*) INTO NAME col VALUE v
)
WHERE nullif(trim(v), '') IS NOT NULL
LIMIT 1;

CREATE OR REPLACE TABLE naics_concordance AS
WITH src AS (
  SELECT nullif(trim(A), '') AS code_2017, nullif(trim(B), '') AS title_2017,
         nullif(trim(C), '') AS code_2022, nullif(trim(D), '') AS title_2022
  FROM read_xlsx('build/census/2017_to_2022_NAICS.xlsx', header = false, all_varchar = true,
                 stop_at_empty = false, range = 'A4:D')
)
SELECT
  CASE WHEN code_2017 IS NULL
       THEN error('NAICS concordance pairing has no 2017 code: ' || coalesce(code_2022, 'NULL') || ' ' || coalesce(title_2017, 'NULL'))
       WHEN NOT regexp_full_match(code_2017, '[0-9]{6}')
       THEN error('NAICS concordance has a 2017 code that is not six digits: ' || code_2017)
       ELSE code_2017 END AS code_2017,
  CASE WHEN title_2017 IS NULL
       THEN error('NAICS concordance pairing has no 2017 title: ' || code_2017)
       ELSE title_2017 END AS title_2017,
  CASE WHEN code_2022 IS NULL
       THEN error('NAICS concordance pairing has no 2022 code: ' || coalesce(code_2017, 'NULL') || ' ' || coalesce(title_2017, 'NULL'))
       WHEN NOT regexp_full_match(code_2022, '[0-9]{6}')
       THEN error('NAICS concordance has a 2022 code that is not six digits: ' || code_2022)
       ELSE code_2022 END AS code_2022,
  CASE WHEN title_2022 IS NULL
       THEN error('NAICS concordance pairing has no 2022 title: ' || code_2022)
       ELSE title_2022 END AS title_2022
FROM src
WHERE coalesce(code_2017, title_2017, code_2022, title_2022) IS NOT NULL;
