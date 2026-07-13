-- Add the free-lane search corpus, sort for row-group pruning, and write the terminal
-- Parquet the out-of-repo publish pipeline consumes. GRAIN: one row per (cik, ticker).
--
-- corpus: a lowered, accent-stripped concatenation of name + ticker + exchange,
-- powering the free-lane in-browser search (single-column ILIKE, pre-lowered).
-- Hidden from the grid; searched, not displayed.
COPY (
  SELECT
    cik, company_name, ticker, exchange,
    lower(strip_accents(concat_ws(' ', company_name, ticker, exchange))) AS corpus
  FROM edgar
  ORDER BY company_name
) TO 'build/edgar.parquet' (FORMAT parquet, COMPRESSION zstd);
