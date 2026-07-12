-- Add the free-lane search corpus, sort for row-group pruning, and write the terminal
-- Parquet the out-of-repo publish pipeline consumes. GRAIN: one row per LEI.
--
-- corpus: a lowered, accent-stripped concatenation of the key text fields, powering
-- the free-lane in-browser search (single-column ILIKE, pre-lowered) and the future
-- embedding field. Hidden from the grid; searched, not displayed.
COPY (
  SELECT
    lei, legal_name, country, city, jurisdiction, legal_form, entity_status,
    registration_status, initial_registration, next_renewal,
    lower(strip_accents(concat_ws(' ', legal_name, city, country))) AS corpus
  FROM gleif
  ORDER BY country, legal_name
) TO 'build/gleif.parquet' (FORMAT parquet, COMPRESSION zstd);
