-- Load the fetched sources into typed tables and normalise the join keys.
-- Run by the `load` step (depends on all fetches). CIK representations differ across
-- sources (EDGAR unpadded, GLEIF zero-padded) — normalise all to unpadded.

-- Source Datasets (already published, content-addressed).
CREATE OR REPLACE TABLE edgar AS
  SELECT CAST(cik AS BIGINT)::VARCHAR AS cik, company_name, ticker, exchange
  FROM read_parquet('build/edgar.parquet');

CREATE OR REPLACE TABLE gleif AS
  SELECT upper(trim(lei)) AS lei, legal_name, country, city, jurisdiction,
         registration_status AS reg_status
  FROM read_parquet('build/gleif.parquet');

-- Deterministic authoritative pairs — GLEIF SEC registrations. Type the SEC
-- identifier by its scheme (SEC-defined: S…=fund series, C…=share class, digits=CIK);
-- unpad CIKs to match EDGAR, keep series/class verbatim. `category` is GLEIF's own
-- authoritative entity type (FUND | GENERAL | BRANCH | …).
CREATE OR REPLACE TABLE gleif_ra_sec AS
  SELECT
    upper(trim(lei)) AS lei,
    CASE WHEN registered_as LIKE 'S%' THEN 'series'
         WHEN registered_as LIKE 'C%' THEN 'class'
         WHEN regexp_full_match(registered_as, '[0-9]+') THEN 'cik'
         ELSE 'other' END AS key_type,
    CASE WHEN regexp_full_match(registered_as, '[0-9]+')
         THEN CAST(registered_as AS BIGINT)::VARCHAR
         ELSE trim(registered_as) END AS key,
    category
  FROM read_csv('build/gleif_ra_sec.csv', header=true, all_varchar=true)
  WHERE lei IS NOT NULL AND registered_as IS NOT NULL AND registered_as <> '';
