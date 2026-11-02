-- Materialise the terminal table the export and describe steps read. GRAIN: one row
-- per NDC labeler, product and package. This model only adds the search corpus, in the
-- core columns' place after text_source, and hands off a typed table.
--
-- corpus: the lowered, accent-stripped description_full, the field the in-browser
-- search reads. Hidden from the grid; searched, not displayed.
CREATE OR REPLACE TABLE ndc_out AS
SELECT
  code, code_display, level, depth, parent_code, description, description_full,
  release, text_source,
  lower(strip_accents(description_full)) AS corpus,
  * EXCLUDE (code, code_display, level, depth, parent_code, description, description_full,
             release, text_source)
FROM ndc;
