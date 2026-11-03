-- Build the `bls/soc_2018_titles` job-title index from `bls_titles` (load.sql) and the code
-- list `soc_2018_out` (code_list.sql). Run by the `titles` step.
--
-- One row per BLS direct match title; `entry_id` is `<code>|<title>` because BLS gives no
-- id of its own. `code_description` is the code's `description` in the code list, so a
-- lookup needs no second join. `illustrative` is true where BLS marks the title with an
-- 'x' as an illustrative example, not a direct match. The extra column follows the
-- contract's core columns. `release` is the code list's: the definitions file and the
-- direct match title file are checked to share one update date in load.sql.
CREATE OR REPLACE TABLE soc_2018_titles_out AS
SELECT
  t.code || '|' || t.direct_match_title AS entry_id,
  t.direct_match_title AS title,
  t.code,
  c.description AS code_description,
  (SELECT release FROM bls_release) AS release,
  'bls' AS text_source,
  lower(strip_accents(t.direct_match_title)) AS corpus,
  coalesce(t.illustrative = 'x', false) AS illustrative
FROM bls_titles t
LEFT JOIN soc_2018_out c ON c.code = t.code
ORDER BY t.code, t.direct_match_title;
