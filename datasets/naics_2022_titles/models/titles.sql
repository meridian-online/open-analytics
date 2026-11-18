-- Build the `census/naics_2022_titles` index from `naics_index` (load.sql) and the NAICS
-- code list `naics`, which the `load_naics` step builds in this Run from Census's
-- descriptions workbook with census/naics_2022's own model (../naics/models/load.sql).
-- Run by the `titles` step.
--
-- One row per index entry; `entry_id` is `<code>|<title>` because Census gives no id of
-- its own, as for BLS's titles. `code_description` is the code's `title` in
-- census/naics_2022, Census's name for the industry (that list's `description` holds
-- the same title, and its `definition` Census's paragraph), so a lookup needs no second
-- join. `release` is the edition:
-- Census's index file states no date of its own (see the manifest's header).
CREATE OR REPLACE TABLE naics_2022_titles_out AS
SELECT
  i.code || '|' || i.title AS entry_id,
  i.title,
  i.code,
  c.title AS code_description,
  '2022' AS release,
  'census' AS text_source,
  lower(strip_accents(regexp_replace(i.title, '\s+', ' ', 'g'))) AS corpus
FROM naics_index i
LEFT JOIN naics c ON c.code = i.code
ORDER BY i.code, i.title;
