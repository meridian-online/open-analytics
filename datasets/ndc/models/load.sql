-- The FDA's National Drug Code directory: one row per labeler, product and package,
-- from openFDA's NDC download.
--
-- openFDA's file (drug-ndc-0001-of-0001.json) is one JSON object: `meta`, which carries
-- the date openFDA last updated it and the number of records in it, and `results`, one
-- record per product listing. A listing carries its product NDC, the FDA's product
-- fields and a `packaging` list with one entry per package NDC. The download holds
-- finished drugs and unfinished ones (bulk ingredients and drugs for further
-- processing, `finished = false`); the NDC directory is the finished drugs, so the
-- unfinished listings are not read.
--
-- The ladder. An NDC is ten digits in three segments, labeler-product-package, in one of
-- three layouts the FDA prints with hyphens: 4-4-2, 5-3-2 or 5-4-1. Claims and most other
-- data carry it as eleven digits, each segment padded with zeros to 5-4-2, so `code` is
-- that form at every level: five digits for a labeler, nine for a product, eleven for a
-- package. Each level's code is a prefix of the codes below it, so a package's parent is
-- its first nine digits and a product's is its first five. `code_display` is the FDA's
-- own hyphenated form (`0002-0152-01`; a product `0002-0152`; a labeler `0002`).
--
-- One product NDC can be listed more than once (903 product NDCs on 2026-10-30), once
-- per Structured Product Labeling document. The product row takes its fields from one
-- listing: the one whose listing certification runs latest, then the latest marketing
-- start, then the lowest product_id, and `listing_count` says how many there were. A
-- package row takes its product fields from its own listing. A package NDC listed twice
-- (four on 2026-10-30, each twice in one listing with two start dates) keeps the entry
-- from the first listing by the same order, with the earlier start date.
--
-- A labeler's row is named by the name its listings give most often, the first
-- alphabetically on a tie. Labelers submit their names with each listing and they vary
-- ("Willow Birch Pharma, Inc." and "Willow Birch Pharma, LLC"), so product and package
-- rows carry the name their own listing gives in `labeler_name`.
--
-- The text is the FDA's: a labeler's name, a product's brand name (openFDA's brand_name,
-- which already ends with any suffix) and, where it differs, its generic name in brackets,
-- then its dosage form; a package's description. All of it is submitted by the labelers, and the FDA does not verify it.
--
-- release is the date openFDA's `meta` gives as last updated: `2026-10-30`.
--
-- The run fails if the file's record count is not the count `meta` states, if openFDA's
-- date is missing or not a date, if a product or package NDC is not in one of the three
-- layouts, if a package NDC does not start with its listing's product NDC, if two
-- different NDCs pad to the same eleven digits, if a product has no name, if a labeler
-- code has no name on any of its listings, or if a date field is not a date.

CREATE OR REPLACE TABLE ndc AS
WITH raw AS (
  SELECT meta, results
  FROM read_json('build/ndc/drug-ndc-*.json',
                 format = 'auto', maximum_object_size = 1073741824,
                 columns = {
                   'meta': 'STRUCT(last_updated VARCHAR, results STRUCT(total BIGINT))',
                   'results': 'STRUCT(
                      product_ndc VARCHAR, product_id VARCHAR, spl_id VARCHAR,
                      finished BOOLEAN, labeler_name VARCHAR, brand_name VARCHAR,
                      brand_name_suffix VARCHAR, generic_name VARCHAR, dosage_form VARCHAR,
                      route VARCHAR[], product_type VARCHAR, marketing_category VARCHAR,
                      application_number VARCHAR,
                      active_ingredients STRUCT("name" VARCHAR, strength VARCHAR)[],
                      pharm_class VARCHAR[], dea_schedule VARCHAR,
                      marketing_start_date VARCHAR, marketing_end_date VARCHAR,
                      listing_expiration_date VARCHAR,
                      packaging STRUCT(package_ndc VARCHAR, description VARCHAR,
                                       marketing_start_date VARCHAR,
                                       marketing_end_date VARCHAR, "sample" BOOLEAN)[]
                   )[]'
                 })
),
release AS (
  SELECT CASE WHEN count(*) <> 1
              THEN error('expected one openFDA NDC file in build/ndc, found ' || count(*))
              WHEN any_value(meta.results.total) IS DISTINCT FROM any_value(len(results))
              THEN error('openFDA states ' || coalesce(any_value(meta.results.total)::VARCHAR, 'no')
                         || ' records but the file holds ' || any_value(len(results)))
              WHEN try_strptime(any_value(meta.last_updated), '%Y-%m-%d') IS NULL
              THEN error('openFDA last_updated is not a date: '
                         || coalesce(any_value(meta.last_updated), 'NULL'))
              ELSE any_value(meta.last_updated) END AS release
  FROM raw
),
listings AS (
  SELECT unnest(results, max_depth := 2) FROM raw
),
finished AS (
  SELECT *,
         CASE WHEN product_ndc IS NULL
                   OR NOT regexp_full_match(product_ndc, '[0-9]{4}-[0-9]{4}|[0-9]{5}-[0-9]{3,4}')
              THEN error('openFDA product NDC is not 4-4, 5-3 or 5-4: '
                         || coalesce(product_ndc, 'NULL') || ' in ' || coalesce(product_id, 'a listing'))
              ELSE lpad(split_part(product_ndc, '-', 1), 5, '0')
                   || lpad(split_part(product_ndc, '-', 2), 4, '0') END AS product_code,
         -- The order that picks a product's listing, and a package's when it is listed twice.
         row_number() OVER (PARTITION BY product_ndc
                            ORDER BY listing_expiration_date DESC NULLS LAST,
                                     marketing_start_date DESC NULLS LAST, product_id)
           AS listing_rank
  FROM listings
  WHERE finished
),
dated AS (
  -- openFDA prints dates as YYYYMMDD. A value that does not parse fails the run.
  SELECT *,
         CASE WHEN marketing_start_date IS NOT NULL
                   AND try_strptime(marketing_start_date, '%Y%m%d') IS NULL
              THEN error('openFDA marketing start date is not a date: ' || product_id)
              ELSE strptime(marketing_start_date, '%Y%m%d')::DATE END AS product_start,
         CASE WHEN marketing_end_date IS NOT NULL
                   AND try_strptime(marketing_end_date, '%Y%m%d') IS NULL
              THEN error('openFDA marketing end date is not a date: ' || product_id)
              ELSE strptime(marketing_end_date, '%Y%m%d')::DATE END AS product_end,
         CASE WHEN listing_expiration_date IS NOT NULL
                   AND try_strptime(listing_expiration_date, '%Y%m%d') IS NULL
              THEN error('openFDA listing expiration date is not a date: ' || product_id)
              ELSE strptime(listing_expiration_date, '%Y%m%d')::DATE END AS listing_expires,
         CASE WHEN coalesce(nullif(trim(brand_name), ''), nullif(trim(generic_name), '')) IS NULL
              THEN error('openFDA product has no brand or generic name: ' || product_id)
              ELSE trim(coalesce(nullif(trim(brand_name), ''), generic_name))
                   || CASE WHEN nullif(trim(brand_name), '') IS NOT NULL
                                AND nullif(trim(generic_name), '') IS NOT NULL
                                AND lower(trim(generic_name)) <> lower(trim(brand_name))
                           THEN ' (' || trim(generic_name) || ')' ELSE '' END
                   || coalesce(', ' || nullif(trim(dosage_form), ''), '') END AS product_text
  FROM finished
),
packages AS (
  SELECT product_code, product_ndc, listing_rank, unnest(packaging, max_depth := 2)
  FROM dated
),
package_rows AS (
  SELECT *,
         CASE WHEN package_ndc IS NULL
                   OR NOT regexp_full_match(package_ndc,
                         '[0-9]{4}-[0-9]{4}-[0-9]{2}|[0-9]{5}-[0-9]{3}-[0-9]{2}|[0-9]{5}-[0-9]{4}-[0-9]')
              THEN error('openFDA package NDC is not 4-4-2, 5-3-2 or 5-4-1: '
                         || coalesce(package_ndc, 'NULL') || ' under ' || product_ndc)
              WHEN NOT starts_with(package_ndc, product_ndc || '-')
              THEN error('openFDA package NDC ' || package_ndc || ' is not under its product '
                         || product_ndc)
              ELSE product_code || lpad(split_part(package_ndc, '-', 3), 2, '0') END AS code,
         CASE WHEN marketing_start_date IS NOT NULL
                   AND try_strptime(marketing_start_date, '%Y%m%d') IS NULL
              THEN error('openFDA package marketing start date is not a date: ' || package_ndc)
              ELSE strptime(marketing_start_date, '%Y%m%d')::DATE END AS package_start,
         CASE WHEN marketing_end_date IS NOT NULL
                   AND try_strptime(marketing_end_date, '%Y%m%d') IS NULL
              THEN error('openFDA package marketing end date is not a date: ' || package_ndc)
              ELSE strptime(marketing_end_date, '%Y%m%d')::DATE END AS package_end
  FROM packages
),
package_pick AS (
  SELECT *,
         row_number() OVER (PARTITION BY package_ndc
                            ORDER BY listing_rank, package_start NULLS LAST, description,
                                     package_end NULLS LAST, "sample" NULLS LAST)
           AS package_rank
  FROM package_rows
),
labeler_names AS (
  SELECT lpad(split_part(product_ndc, '-', 1), 5, '0') AS code,
         split_part(product_ndc, '-', 1) AS code_display,
         trim(labeler_name) AS labeler_name,
         count(*) AS n
  FROM dated
  WHERE nullif(trim(labeler_name), '') IS NOT NULL
  GROUP BY ALL
),
labelers AS (
  SELECT code,
         CASE WHEN count(DISTINCT code_display) <> 1
              THEN error('two labeler codes pad to ' || code || ': '
                         || string_agg(DISTINCT code_display, ', '))
              ELSE any_value(code_display) END AS code_display,
         first(labeler_name ORDER BY n DESC, labeler_name) AS description
  FROM labeler_names
  GROUP BY code
),
products AS (
  SELECT product_code AS code,
         CASE WHEN count(DISTINCT product_ndc) <> 1
              THEN error('two product NDCs pad to ' || product_code || ': '
                         || string_agg(DISTINCT product_ndc, ', '))
              ELSE any_value(product_ndc) END AS code_display,
         count(*) AS listing_count
  FROM dated
  GROUP BY product_code
),
packages_one AS (
  SELECT code,
         CASE WHEN count(DISTINCT package_ndc) <> 1
              THEN error('two package NDCs pad to ' || code || ': '
                         || string_agg(DISTINCT package_ndc, ', '))
              ELSE any_value(package_ndc) END AS code_display
  FROM package_rows
  GROUP BY code
),
product_fields AS (
  -- The FDA's product fields, as a listing gives them, for a product row and the
  -- package rows under it.
  SELECT product_id, product_code, listing_rank, product_text,
         trim(labeler_name) AS labeler_name, product_type,
         nullif(trim(brand_name), '') AS brand_name,
         nullif(trim(brand_name_suffix), '') AS brand_name_suffix,
         nullif(trim(generic_name), '') AS generic_name,
         nullif(trim(dosage_form), '') AS dosage_form,
         CASE WHEN len(route) = 0 THEN NULL ELSE route END AS routes,
         CASE WHEN len(active_ingredients) = 0 THEN NULL ELSE active_ingredients END
           AS active_ingredients,
         CASE WHEN len(pharm_class) = 0 THEN NULL ELSE pharm_class END AS pharm_classes,
         nullif(trim(dea_schedule), '') AS dea_schedule,
         marketing_category,
         nullif(trim(application_number), '') AS application_number,
         product_start, product_end, listing_expires, spl_id
  FROM dated
),
ladder AS (
  SELECT l.code, l.code_display, 'labeler' AS level, 0 AS depth, NULL::VARCHAR AS parent_code,
         l.description, l.description AS labeler_name,
         NULL::VARCHAR AS product_text, NULL::VARCHAR AS product_id,
         NULL::BIGINT AS listing_count, NULL::VARCHAR AS package_description,
         NULL::DATE AS package_start, NULL::DATE AS package_end, NULL::BOOLEAN AS sample
  FROM labelers l
  UNION ALL
  SELECT p.code, p.code_display, 'product', 1, left(p.code, 5),
         f.product_text, f.labeler_name, f.product_text, f.product_id,
         p.listing_count, NULL, NULL, NULL, NULL
  FROM products p
  JOIN product_fields f ON f.product_code = p.code AND f.listing_rank = 1
  UNION ALL
  SELECT k.code, o.code_display, 'package', 2, left(k.code, 9),
         trim(k.description), f.labeler_name, f.product_text, f.product_id,
         NULL, trim(k.description), k.package_start, k.package_end, k."sample"
  FROM package_pick k
  JOIN packages_one o ON o.code = k.code
  JOIN product_fields f ON f.product_code = k.product_code AND f.listing_rank = k.listing_rank
  WHERE k.package_rank = 1
)
SELECT
  x.code,
  x.code_display,
  x.level,
  x.depth,
  x.parent_code,
  CASE WHEN lab.code IS NULL
       THEN error('NDC labeler ' || left(x.code_display, strpos(x.code_display || '-', '-') - 1)
                  || ' has no name on any of its listings')
       WHEN nullif(x.description, '') IS NULL
       THEN error('NDC ' || x.code_display || ' has no text')
       ELSE x.description END AS description,
  -- The labeler, then the product, then the package, each level's text once.
  concat_ws(' › ', lab.description,
            CASE WHEN x.level IN ('product', 'package') THEN x.product_text END,
            CASE WHEN x.level = 'package' THEN x.description END) AS description_full,
  (SELECT release FROM release) AS release,
  'fda' AS text_source,
  x.labeler_name,
  f.product_type,
  f.brand_name,
  f.brand_name_suffix,
  f.generic_name,
  f.dosage_form,
  f.routes,
  f.active_ingredients,
  f.pharm_classes,
  f.dea_schedule,
  f.marketing_category,
  f.application_number,
  f.product_start AS product_marketing_start_date,
  f.product_end AS product_marketing_end_date,
  f.listing_expires AS listing_expiration_date,
  x.listing_count,
  x.package_description,
  x.package_start AS package_marketing_start_date,
  x.package_end AS package_marketing_end_date,
  x."sample",
  f.spl_id,
  x.product_id
FROM ladder x
LEFT JOIN labelers lab ON lab.code = left(x.code, 5)
LEFT JOIN product_fields f ON f.product_id = x.product_id;
