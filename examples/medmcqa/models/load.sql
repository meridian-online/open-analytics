-- MedMCQA's `validation` split, with its map coordinates, into a shape a dashboard
-- can read.
--
-- The input is not the fetched file: it is what `umap_project` wrote, which is the
-- fetched file's columns plus `embedding`, `projection_x`, `projection_y` and
-- `projection_fit_id`. Two things happen here and nothing else.
--
--   • `embedding` is DROPPED. It is a FLOAT[256] per row — 4,183 x 256 floats, about
--     4 MB of vectors — and no chart in the analysis surface reads a vector column.
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
-- `topic_name` is NULL on 3,760 of these 4,183 rows and `exp` on 1,977. Both are
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
