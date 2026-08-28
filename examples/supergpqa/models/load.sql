-- SuperGPQA's corpus, with its map coordinates, into the shape that gets exported.
--
-- The input is what `umap_project` wrote: everything models/corpus.sql selected, plus
-- `embedding`, `projection_x`, `projection_y` and `projection_fit_id`. One thing
-- happens here.
--
-- `embedding` IS DROPPED. It is a FLOAT[256] per row — 26,529 x 256 floats, about
-- 27 MB of vectors — and no chart in the analysis surface reads a vector column. What
-- a map needs is the two coordinates the vectors were reduced to, and those survive.
-- An analyst who wants the vectors themselves runs the Protocol and reads
-- build/supergpqa.embedded.parquet, which is where they already are. That is also
-- where a nearest-neighbour question belongs: see README.md on why the two
-- coordinates cannot answer one.
--
-- Nothing is renamed and nothing is added. The shaping this Protocol does happens in
-- models/corpus.sql, before the embed, so that its gates run before the two expensive
-- steps rather than after them.
CREATE OR REPLACE TABLE supergpqa AS
SELECT * EXCLUDE (embedding)
FROM read_parquet('build/supergpqa.projected.parquet');

-- GATE — `uuid` IS UNIQUE, which is the premise behind this Protocol's
-- byte-reproducibility: it is what makes `ORDER BY ALL` on both exports a TOTAL order,
-- and the order of build/supergpqa.corpus.parquet is what the UMAP fit is pinned to.
-- ../medmcqa/models/load.sql carries the same gate for the same reason.
SELECT CASE WHEN v.repeated > 0 THEN error(
         'supergpqa: `uuid` is not unique — ' || v.repeated || ' value(s) appear more '
         || 'than once across ' || v.rows || ' row(s), so ORDER BY ALL is not a total '
         || 'order, the UMAP fit is not pinned to a row order and the export''s bytes '
         || 'are not pinned. e.g. ' || v.examples) END
FROM (
  SELECT (SELECT count(*) FROM supergpqa) AS rows,
         count(*) AS repeated,
         array_to_string(list_slice(list_sort(list(uuid)), 1, 5), ', ') AS examples
  FROM (SELECT uuid FROM supergpqa GROUP BY uuid HAVING count(*) > 1)
) v;
