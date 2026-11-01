-- Materialise the terminal table the export and describe steps read. GRAIN: one row
-- per Schedule B code, chapters through ten-digit commodities. This model only adds the
-- search corpus, in the core columns' place after text_source, and hands off a typed
-- table.
--
-- corpus: the lowered, accent-stripped description_full, the field the in-browser
-- search reads. Hidden from the grid; searched, not displayed.
CREATE OR REPLACE TABLE schedule_b_out AS
SELECT
  code, code_display, level, depth, parent_code, description, description_full,
  release, text_source,
  lower(strip_accents(description_full)) AS corpus,
  description_short, unit_1, unit_2, sitc, end_use, agricultural, naics, hitech,
  section, section_title, hts_release
FROM schedule_b;
