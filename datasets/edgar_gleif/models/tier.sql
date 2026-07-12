-- Build the tiered, multi-grain edge set: one row per asserted identity, keyed by a
-- typed SEC identifier (cik | series | class). Tiers, in precedence order:
--   authoritative  — deterministic, no guessing (SEC registration / Wikidata)
--   confirmed      — exact normalised-name Splink match (~95% precision)
--   candidate      — fuzzy name match, surfaced not asserted
--   ambiguous      — one exact name matched more than one entity
-- A deterministic edge always wins over a name-match for the same (key_type, key).
-- FIRST CUT — precedence + multi-grain shape are here; N-CEN registrant layer (§1c)
-- and cross-source dedup refinements are the next iteration (see README).

-- 1) Authoritative edges.
CREATE OR REPLACE TABLE authoritative AS
SELECT key_type, key, lei, category, 'sec-registration' AS method,
       'authoritative' AS tier, 1.0 AS confidence
FROM gleif_ra_sec
UNION ALL BY NAME
SELECT 'cik' AS key_type, key, lei, NULL AS category, 'wikidata' AS method,
       'authoritative' AS tier, 1.0 AS confidence
FROM wd_cik_lei;
-- 1c) TODO(backbone): UNION the N-CEN registrant CIK↔LEI here once fetched.

-- Dedup identical edges, preferring SEC registration over Wikidata.
CREATE OR REPLACE TABLE authoritative_d AS
SELECT DISTINCT ON (key_type, key, lei)
       key_type, key, lei, category, method, tier, confidence
FROM authoritative
ORDER BY key_type, key, lei, (method = 'sec-registration') DESC;

-- 2) Probabilistic name-match edges (operating-company tail), CIK grain. Drop any
--    CIK already covered by an authoritative edge — authoritative wins.
CREATE OR REPLACE TABLE probabilistic AS
SELECT 'cik' AS key_type, CAST(r.cik AS BIGINT)::VARCHAR AS key, upper(r.lei) AS lei,
       NULL AS category, r.method, r.status AS tier, r.match_probability AS confidence
FROM read_parquet('build/resolved.parquet') r
WHERE r.status IN ('confirmed', 'candidate', 'ambiguous')
  AND NOT EXISTS (
    SELECT 1 FROM authoritative_d a
    WHERE a.key_type = 'cik' AND a.key = CAST(r.cik AS BIGINT)::VARCHAR
  );

-- 3) The unified edge set (enriched from the sources in package.sql).
CREATE OR REPLACE TABLE crosswalk_edges AS
SELECT * FROM authoritative_d
UNION ALL BY NAME
SELECT * FROM probabilistic;
