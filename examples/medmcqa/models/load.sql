-- MedMCQA's `validation` split, with its map coordinates, into a shape a dashboard
-- can read.
--
-- The input is not the fetched file: it is what `umap_project` wrote, which is the
-- fetched file's columns plus `embedding`, `projection_x`, `projection_y` and
-- `projection_fit_id`. Two things happen here and nothing else.
--
--   • `embedding` is DROPPED. It is a FLOAT[256] per row — 182,822 x 256 floats, about
--     187 MB of vectors — and no chart in the analysis surface reads a vector column.
--     What a map needs is the two coordinates the vectors were reduced to, and those
--     survive. An analyst who wants the vectors themselves runs the Protocol and
--     reads build/medmcqa.embedded.parquet, which is where they already are.
--   • `answer_text` is ADDED, joined from `cop`. The source records the correct
--     answer as an integer 0-3 indexing the four option columns, and an index alone
--     means nothing to a chart-building surface without also reading four other
--     columns. ../scienceqa/models/load.sql makes the same join for the same reason.
--
-- EVERY SOURCE COLUMN KEEPS ITS SOURCE NAME, including the four `opa`/`opb`/`opc`/
-- `opd` and the abbreviated `cop`. They are not names anybody would choose, and
-- renaming them here would put this Protocol's export and the file a reader fetches
-- from the source under different vocabularies for the same data. What the names mean
-- is written down in descriptor.overrides.json, which is the file that exists for it.
--
-- `topic_name` is NULL on 95,613 of these 182,822 rows and `exp` on 21,953. Both are
-- carried through as the source has them rather than defaulted to a blank string: a
-- missing explanation and an empty explanation are different facts.
CREATE OR REPLACE TABLE medmcqa AS
SELECT
  * EXCLUDE (embedding),
  CASE cop
    WHEN 0 THEN opa
    WHEN 1 THEN opb
    WHEN 2 THEN opc
    WHEN 3 THEN opd
  END AS answer_text
FROM read_parquet('build/medmcqa.projected.parquet');

-- GATE the table on the two facts the rest of this Protocol asserts about it, in the
-- shape ../../datasets/edgar_gleif/models/package.sql uses: stop the Run rather than
-- export something whose description is not true of it.
--
-- 1. `cop` IS AN ANSWER. The source's `test` split withholds answers — all 6,150 of
--    its rows carry `cop = -1` — so pointing the fetch at it, which is one URL and one
--    hash away in arcform.yaml, would export 6,150 rows with `answer_text` NULL on
--    every one and a descriptor still claiming the column joins the correct option in.
--    That is the mistake this gate exists for; it is not hypothetical, it is the other
--    file in the same directory of the same repository.
SELECT CASE WHEN v.rows > 0 THEN error(
         'medmcqa: ' || v.rows || ' row(s) carry a `cop` outside 0-3, so `answer_text` '
         || 'is NULL on them and this is not a split with answers in it. The source''s '
         || '`test` split sets cop = -1 on every row; check which file arcform.yaml '
         || 'fetches. Distinct out-of-range value(s): ' || v.values) END
FROM (
  SELECT count(*) AS rows,
         array_to_string(list_sort(list(DISTINCT cop)), ', ') AS values
  FROM medmcqa
  WHERE cop IS NULL OR cop < 0 OR cop > 3
) v;

-- 2. `id` IS UNIQUE, which is what makes `ORDER BY ALL` in arcform.yaml a TOTAL order
--    and lets this Protocol claim byte-reproducibility without the weaker
--    tie-between-identical-rows argument ../scienceqa/arcform.yaml has to make. If a
--    future split or revision repeats an id, the claim in the README stops being true
--    and the export's row order stops being pinned by anything.
SELECT CASE WHEN v.repeated > 0 THEN error(
         'medmcqa: `id` is not unique — ' || v.repeated || ' value(s) appear more than '
         || 'once across ' || v.rows || ' row(s), so ORDER BY ALL is not a total order '
         || 'and the export''s bytes are not pinned. e.g. ' || v.examples) END
FROM (
  SELECT (SELECT count(*) FROM medmcqa) AS rows,
         count(*) AS repeated,
         array_to_string(list_slice(list_sort(list(id)), 1, 5), ', ') AS examples
  FROM (SELECT id FROM medmcqa GROUP BY id HAVING count(*) > 1)
) v;
