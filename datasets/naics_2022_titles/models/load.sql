-- Stage Census's 2022 NAICS index file. Run by the `load` step (needs DuckDB's excel
-- extension); paths resolve from the Protocol directory.
--
-- The workbook is one sheet with a header row and no title block. Measured 2026-11-17:
-- row 1 reads NAICS22, INDEX ITEM DESCRIPTION, and the 20,398 rows below it are 20,373
-- index entries, each a six-digit code and a description, and then 25 cross-references
-- whose code cell is Census's '******' and whose text points elsewhere ("Dealers -- see
-- type"). A cross-reference names no code, so it is not an entry and is left out.
--
-- The sheet is read as text from an explicit range, with no header row, so a code
-- stored as a number reads as its digits. The header row's text is checked before any
-- data is read, so a sheet whose layout has moved fails the run instead of staging the
-- wrong cells. No step numbers the rows, so nothing here rests on the order the scan
-- delivers them.
--
-- Staging table: naics_index(code, title), one row per entry. `title` is Census's text
-- with spaces trimmed from its ends, as BLS's titles are; runs of spaces inside it are
-- kept as Census prints them and collapsed only in the corpus.
INSTALL excel; LOAD excel;

SELECT CASE WHEN (A, B) IS NOT DISTINCT FROM ('NAICS22', 'INDEX ITEM DESCRIPTION') THEN 1
       ELSE error('NAICS index header row has moved: expected NAICS22, INDEX ITEM DESCRIPTION in A1:B1, found '
                  || coalesce(A, 'NULL') || ', ' || coalesce(B, 'NULL')) END
FROM read_xlsx('build/census/2022_NAICS_Index_File.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A1:B1');

CREATE OR REPLACE TABLE naics_index AS
WITH src AS (
  SELECT trim(A) AS code, trim(B) AS title
  FROM read_xlsx('build/census/2022_NAICS_Index_File.xlsx', header = false, all_varchar = true,
                 stop_at_empty = false, range = 'A2:B')
  WHERE nullif(trim(A), '') IS NOT NULL OR nullif(trim(B), '') IS NOT NULL
)
SELECT
  CASE WHEN code IS NULL OR code = ''
       THEN error('NAICS index entry has no code: ' || title)
       WHEN NOT regexp_full_match(code, '[0-9]{6}')
       THEN error('NAICS index entry has a code that is not six digits and not a cross-reference: '
                  || code || ' on ' || coalesce(title, 'NULL'))
       ELSE code END AS code,
  CASE WHEN title IS NULL OR title = ''
       THEN error('NAICS index entry has no description: ' || code)
       ELSE title END AS title
FROM src
WHERE code IS DISTINCT FROM '******';

-- A cross-reference must still say where to look, so a code cell of '******' on an
-- ordinary entry is not dropped unseen.
SELECT error('NAICS index has a ****** row that is not a cross-reference: ' || coalesce(trim(B), 'NULL'))
FROM read_xlsx('build/census/2022_NAICS_Index_File.xlsx', header = false, all_varchar = true,
               stop_at_empty = false, range = 'A2:B')
WHERE trim(A) = '******' AND coalesce(trim(B), '') NOT LIKE '% -- see %'
LIMIT 1;
