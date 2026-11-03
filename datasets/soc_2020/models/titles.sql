-- Build the `ons/soc_2020_titles` job-title index from `ons_index` (load.sql) and the code
-- list `soc_2020_out` (code_list.sql). Run by the `titles` step.
--
-- One row per coded entry of ONS's coding index (cross-references are left out in
-- load.sql); `entry_id` is ONS's UNIQUE ID. `code_description` is the code's `description`
-- in the code list, so a lookup needs no second join. ONS codes a title by its qualifiers
-- where they decide the code, so `corpus` is the title with its additional and industry
-- qualifiers, lowered, accent-stripped and whitespace-collapsed. The columns after the
-- contract's core ones are ONS's. `release` is the index's own, not the code list's.
-- `ons_release` (one row) is joined rather than read in a scalar subquery, so arc records it
-- as an input of this step and a release-only change reruns it.
CREATE OR REPLACE TABLE soc_2020_titles_out AS
SELECT
  t.entry_id,
  t.title,
  t.code,
  c.description AS code_description,
  r.index_release AS release,
  'ons' AS text_source,
  lower(strip_accents(regexp_replace(concat_ws(' ', t.title, t.additional_qualifier, t.industry_qualifier), '\s+', ' ', 'g'))) AS corpus,
  t.title_natural,
  t.additional_qualifier,
  t.industry_qualifier,
  t.default_flag,
  t.soc_2020_ext,
  t.soc_2020_ext_title,
  t.soc_2010,
  t.soc_2000,
  t.soc_1990,
  t.isco_08,
  t.version_added_or_amended,
  t.change
FROM ons_index t
CROSS JOIN ons_release r
LEFT JOIN soc_2020_out c ON c.code = t.code
ORDER BY t.code, t.entry_id;
