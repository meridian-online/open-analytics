-- Add the free-lane search corpus, sort by code, and write the terminal Parquet the
-- out-of-repo publish pipeline consumes. GRAIN: one row per NAICS code.
--
-- corpus: a lowered, accent-stripped concatenation of title + description, powering
-- the free-lane in-browser search (concat_ws skips a NULL description, so no stray
-- 'null' text leaks in). Hidden from the grid; searched, not displayed.
COPY (
  SELECT
    code, title, level, description,
    lower(strip_accents(concat_ws(' ', title, description))) AS corpus
  FROM naics
  ORDER BY code
) TO 'build/naics.parquet' (FORMAT parquet, COMPRESSION zstd);
