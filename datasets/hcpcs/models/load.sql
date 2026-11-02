-- HCPCS Level II: one row per code, from CMS's quarterly alpha-numeric file.
--
-- CMS's file (HCPC<year>_<MON>_ANWEB_<MMDDYYYY>.txt) is fixed-width, 320 characters a
-- record by CMS's record layout, one record per line of description. Record type 3 is
-- the first line of a code and carries its data fields; type 4 continues its long
-- description. Types 7 and 8 are modifiers, which are not codes on this ladder and are
-- not read here. CMS breaks the long description between words, so its lines are
-- joined with one space.
--
-- The list is flat: every code is one level, `code`, at depth 0 with no parent. CMS
-- publishes no titles for the letter a code starts with, so the letter is not made a
-- level with text from elsewhere.
--
-- Licence. HCPCS Level II's codes and descriptors are a US Government work, except
-- two parts that are not:
--   * the American Dental Association's CDT codes, the D series, which this model
--     drops (CMS's file does not carry them today; the filter holds if it ever does);
--   * the American Medical Association's CPT (HCPCS Level I). CMS's file carries no CPT
--     code as a row, but it does carry CPT codes in two places, and both are removed:
--       - the cross-reference field, which for 42 codes in October 2026 points at a CPT
--         code: a cross-reference that is a CPT code or a D code is dropped;
--       - fifteen long descriptions that cite CPT codes by number ("list separately in
--         addition to cpt codes 99306, 99310"): each five-digit number, and each four
--         digits followed by F or T (CPT's category II and III forms), is replaced by
--         "[CPT code]", and text_source says so (`cms_cpt_redacted`).
-- The check_licence step proves both on the exported file.
--
-- release is CMS's quarter and the date in the file's name: HCPC2026_OCT_ANWEB_09232026
-- is `2026-10/2026-09-23`. The run fails if the extracted directory holds other than
-- one ANWEB file, if its name does not parse, if a record's code is not a letter and
-- four digits, if a code has no first record or more than one, or if a code has no
-- long description.

CREATE OR REPLACE TABLE hcpcs AS
WITH raw AS (
  SELECT line, filename
  FROM read_csv('build/hcpcs/HCPC*_ANWEB_*.txt',
                columns = {'line': 'VARCHAR'}, delim = chr(1), quote = '', escape = '',
                header = false, filename = true)
),
files AS (
  SELECT CASE WHEN count(DISTINCT filename) <> 1
              THEN error('expected one CMS ANWEB file in build/hcpcs, found '
                         || count(DISTINCT filename))
              ELSE any_value(filename) END AS filename
  FROM raw
),
release AS (
  -- regexp_extract's struct form gives empty strings, not NULL, when the name does not
  -- match, so emptiness is what is tested.
  SELECT CASE WHEN m['year'] = '' OR m['fy'] = '' OR month_number IS NULL
              THEN error('CMS file name does not parse as HCPC<year>_<MON>_ANWEB_<MMDDYYYY>: '
                         || filename)
              ELSE m['year'] || '-' || lpad(month_number::VARCHAR, 2, '0') || '/'
                   || m['fy'] || '-' || m['fm'] || '-' || m['fd'] END AS release
  FROM (
    SELECT filename,
           regexp_extract(filename,
             'HCPC([0-9]{4})_([A-Z]{3})_ANWEB_(?:[^/]*_)?([0-9]{2})([0-9]{2})([0-9]{4})\.txt$',
             ['year', 'mon', 'fm', 'fd', 'fy']) AS m,
           list_position(['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                          'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'],
             regexp_extract(filename, 'HCPC[0-9]{4}_([A-Z]{3})_ANWEB_', 1)) AS month_number
    FROM files
  )
),
records AS (
  SELECT rtrim(substr(line, 1, 5)) AS code,
         substr(line, 6, 5) AS seq,
         substr(line, 11, 1) AS ric,
         line
  FROM raw
  -- CMS's file ends with an empty line.
  WHERE trim(line) <> ''
),
procedure_records AS (
  SELECT CASE WHEN NOT regexp_full_match(code, '[A-Z][0-9]{4}')
              THEN error('CMS record is not a letter and four digits: ' || code)
              ELSE code END AS code,
         seq, ric, line
  FROM records
  WHERE ric IN ('3', '4')
),
level_ii AS (
  -- The ADA's D series is not a US Government work.
  SELECT * FROM procedure_records WHERE code NOT LIKE 'D%'
),
dates AS (
  SELECT code, strptime(nullif(trim(v), ''), '%Y%m%d')::DATE AS d, k
  FROM (
    SELECT code, substr(line, 269, 8) AS added, substr(line, 277, 8) AS effective,
           substr(line, 285, 8) AS terminated
    FROM level_ii WHERE ric = '3'
  ) UNPIVOT (v FOR k IN (added, effective, terminated))
),
first_records AS (
  -- Every code with any record, so a code whose first record is missing is refused
  -- rather than dropped.
  SELECT code,
         CASE WHEN count(*) FILTER (WHERE ric = '3') <> 1
              THEN error('CMS code has ' || count(*) FILTER (WHERE ric = '3')
                         || ' first records: ' || code)
              ELSE any_value(line) FILTER (WHERE ric = '3') END AS line
  FROM level_ii
  GROUP BY code
),
long_text AS (
  SELECT code, string_agg(trim(substr(line, 12, 80)), ' ' ORDER BY seq) AS long_description
  FROM level_ii
  GROUP BY code
),
fields AS (
  SELECT f.code,
         t.long_description,
         nullif(rtrim(substr(f.line, 92, 28)), '') AS short_description,
         list_filter([substr(f.line, 120, 2), substr(f.line, 122, 2),
                      substr(f.line, 124, 2), substr(f.line, 126, 2)], x -> trim(x) <> '')
           AS pricing_indicators,
         nullif(trim(substr(f.line, 128, 1)), '') AS multiple_pricing_indicator,
         list_filter([trim(substr(f.line, 181, 3)), trim(substr(f.line, 184, 3)),
                      trim(substr(f.line, 187, 3)), trim(substr(f.line, 190, 3)),
                      trim(substr(f.line, 193, 3)), trim(substr(f.line, 196, 3)),
                      trim(substr(f.line, 199, 3)), trim(substr(f.line, 202, 3))], x -> x <> '')
           AS lab_certification_codes,
         -- A cross-reference to a CPT code (starts with a digit) or a CDT code (D) is
         -- not carried.
         list_filter([trim(substr(f.line, 205, 5)), trim(substr(f.line, 210, 5)),
                      trim(substr(f.line, 215, 5)), trim(substr(f.line, 220, 5)),
                      trim(substr(f.line, 225, 5))],
                     x -> regexp_full_match(x, '[A-CE-Z][0-9]{4}')) AS cross_reference_codes,
         nullif(trim(substr(f.line, 230, 1)), '') AS coverage_code,
         nullif(trim(substr(f.line, 231, 2)), '') AS asc_payment_group,
         nullif(trim(substr(f.line, 257, 3)), '') AS betos,
         list_filter([substr(f.line, 261, 1), substr(f.line, 262, 1), substr(f.line, 263, 1),
                      substr(f.line, 264, 1), substr(f.line, 265, 1)], x -> trim(x) <> '')
           AS type_of_service,
         nullif(trim(substr(f.line, 293, 1)), '') AS action_code
  FROM first_records f
  JOIN long_text t USING (code)
),
redacted AS (
  SELECT *,
         regexp_replace(long_description, '\b([0-9]{5}|[0-9]{4}[FfTt])\b', '[CPT code]', 'g')
           AS clean_description
  FROM fields
)
SELECT
  r.code,
  r.code AS code_display,
  'code' AS level,
  0 AS depth,
  NULL::VARCHAR AS parent_code,
  CASE WHEN nullif(trim(r.clean_description), '') IS NULL
       THEN error('CMS code has no long description: ' || r.code)
       ELSE r.clean_description END AS description,
  r.clean_description AS description_full,
  (SELECT release FROM release) AS release,
  CASE WHEN r.clean_description <> r.long_description THEN 'cms_cpt_redacted' ELSE 'cms' END
    AS text_source,
  r.short_description,
  da.d AS added_date,
  de.d AS action_effective_date,
  dt.d AS termination_date,
  r.action_code,
  r.coverage_code,
  r.pricing_indicators,
  r.multiple_pricing_indicator,
  r.betos,
  r.type_of_service,
  r.lab_certification_codes,
  r.asc_payment_group,
  r.cross_reference_codes
FROM redacted r
LEFT JOIN dates da ON da.code = r.code AND da.k = 'added'
LEFT JOIN dates de ON de.code = r.code AND de.k = 'effective'
LEFT JOIN dates dt ON dt.code = r.code AND dt.k = 'terminated';
