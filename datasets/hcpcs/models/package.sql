-- Materialise the terminal table the export and describe steps read. GRAIN: one row
-- per HCPCS Level II code. This model only adds the search corpus, in the core
-- columns' place after text_source, and hands off a typed table.
--
-- corpus: the lowered, accent-stripped description_full, the field the in-browser
-- search reads. Hidden from the grid; searched, not displayed.
CREATE OR REPLACE TABLE hcpcs_out AS
SELECT
  code, code_display, level, depth, parent_code, description, description_full,
  release, text_source,
  lower(strip_accents(description_full)) AS corpus,
  short_description, added_date, action_effective_date, termination_date, action_code,
  coverage_code,
  -- CMS's repeated fields, as lists of the values printed; NULL where none is.
  CASE WHEN len(pricing_indicators) = 0 THEN NULL ELSE pricing_indicators END AS pricing_indicators,
  multiple_pricing_indicator, betos,
  CASE WHEN len(type_of_service) = 0 THEN NULL ELSE type_of_service END AS type_of_service,
  CASE WHEN len(lab_certification_codes) = 0 THEN NULL ELSE lab_certification_codes END
    AS lab_certification_codes,
  asc_payment_group,
  CASE WHEN len(cross_reference_codes) = 0 THEN NULL ELSE cross_reference_codes END
    AS cross_reference_codes
FROM hcpcs;
