-- Enrich every edge from the sources (by LEI → GLEIF; by CIK → EDGAR), stamp the
-- as_of, build a search corpus, and write the terminal Parquet the publish seam
-- (the out-of-repo publish pipeline) consumes. Funds (key_type=series) don't join to EDGAR, so
-- their display name comes from GLEIF's legal_name via coalesce.
--
-- GRAIN: one row per (key_type, key, lei) edge. A CIK can carry several tickers, so
-- EDGAR is deduped to one row per CIK with the tickers aggregated (a comma list) —
-- otherwise the join fans an edge out into one row per ticker. (Ticker-grain vs
-- CIK-grain is a deliberate modelling choice; see README.)
COPY (
  SELECT
    x.key_type,
    x.key,
    e.ticker,
    coalesce(e.company_name, g.legal_name) AS company_name,
    x.lei,
    g.legal_name,
    coalesce(x.category, 'GENERAL')        AS category,
    g.jurisdiction,
    g.reg_status,
    x.method,
    x.tier,
    round(x.confidence, 6)                 AS confidence,
    CAST(nullif(getenv('ARC_PARAM_AS_OF'), '') AS DATE) AS as_of,
    lower(strip_accents(concat_ws(' ',
      coalesce(e.company_name, g.legal_name), g.legal_name, e.ticker, x.lei))) AS corpus
  FROM crosswalk_edges x
  LEFT JOIN gleif g ON g.lei = x.lei
  LEFT JOIN (
    SELECT cik,
           string_agg(DISTINCT ticker, ',' ORDER BY ticker) AS ticker,
           any_value(company_name)                          AS company_name
    FROM edgar GROUP BY cik
  ) e ON x.key_type = 'cik' AND e.cik = x.key
  ORDER BY company_name
) TO 'build/edgar_gleif.parquet'
  (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE 50000);
