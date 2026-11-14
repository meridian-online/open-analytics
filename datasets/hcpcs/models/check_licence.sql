-- The licence check, on the exported file: it carries no ADA dental (CDT) code and no
-- AMA CPT content. Fails the run, before the file is described, validated or stamped,
-- if any of these holds:
--   * a code that is not a letter and four digits, or one in the D series;
--   * a cross-reference that is not a letter and four digits, or one in the D series;
--   * a description, short description or search corpus holding a CPT-shaped code: a
--     five-digit number, or four digits followed by F or T, standing as a word;
--   * any text naming a D-series code (D and four digits, as a word).
-- The table it makes, `licence_check`, records what was counted, so a passing run
-- shows the counts it passed on.
CREATE OR REPLACE TABLE licence_check AS
WITH f AS (SELECT * FROM read_parquet('build/stage/hcpcs.parquet')),
counts AS (
  SELECT
    count(*) AS codes,
    count(*) FILTER (WHERE NOT regexp_full_match(code, '[A-Z][0-9]{4}')) AS bad_shape,
    count(*) FILTER (WHERE code LIKE 'D%') AS dental_codes,
    count(*) FILTER (WHERE len(list_filter(coalesce(cross_reference_codes, []),
                                           lambda x: NOT regexp_full_match(x, '[A-CE-Z][0-9]{4}'))) > 0)
      AS bad_cross_references,
    count(*) FILTER (WHERE regexp_matches(concat_ws(' ', description, description_full,
                                                    short_description, corpus),
                                          '(?i)\b([0-9]{5}|[0-9]{4}[ft])\b'))
      AS cpt_shaped_text,
    count(*) FILTER (WHERE regexp_matches(concat_ws(' ', description, description_full,
                                                    short_description, corpus),
                                          '(?i)\bd[0-9]{4}\b'))
      AS dental_text,
    count(*) FILTER (WHERE text_source = 'cms_cpt_redacted') AS redacted
  FROM f
)
SELECT CASE WHEN codes = 0 THEN error('licence check: the file has no rows')
            WHEN bad_shape + dental_codes + bad_cross_references + cpt_shaped_text + dental_text > 0
            THEN error('licence check failed: ' || bad_shape || ' codes not a letter and four digits, '
                       || dental_codes || ' D codes, ' || bad_cross_references
                       || ' rows with a CPT or D cross-reference, ' || cpt_shaped_text
                       || ' rows with CPT-shaped text, ' || dental_text || ' rows naming a D code')
            ELSE 'pass' END AS result,
       *
FROM counts;
