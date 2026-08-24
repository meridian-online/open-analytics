-- ScienceQA's `test` split, into a shape a dashboard can read.
--
-- Two source quirks, each handled here rather than left for a reader to hit:
--   • `image` arrives as STRUCT(bytes BLOB, path VARCHAR) — the raw picture bytes for
--     2,017 of these 4,241 rows, both fields NULL for the other 2,224 (text-only
--     questions). Carrying a BLOB into the export would multiply this Protocol's
--     output for a column no chart in the analysis surface reads, and `path` is
--     always the generic literal 'image.png' when present — it names nothing about
--     the picture. So the struct becomes one boolean: whether a question shipped
--     with a figure.
--   • `choices` arrives as a VARCHAR[] of 2 to 5 options, and `answer` as a 0-based
--     index into it. A list column is not a shape the export/describe pair here has
--     handled before (see ../movies and ../census-income, both plain scalars), so it
--     is flattened to a single delimited string, and the index is joined back to the
--     option text it selects — a chart-building surface can read "answer_text" on
--     its own, where "answer_index" alone means nothing without also reading
--     "answer_choices".
--
-- 64 of these 4,241 rows are exact duplicates of another row across every column
-- this SELECT keeps. That is what makes `ORDER BY ALL` in ../arcform.yaml not a
-- total order — see the comment there.
CREATE OR REPLACE TABLE scienceqa AS
SELECT
  question                    AS question,
  array_to_string(choices, ' | ') AS answer_choices,
  answer                       AS answer_index,
  choices[answer + 1]          AS answer_text,
  hint                         AS hint,
  task                         AS task,
  grade                        AS grade,
  subject                      AS subject,
  topic                        AS topic,
  category                     AS category,
  skill                        AS skill,
  lecture                      AS lecture,
  solution                     AS solution,
  image.bytes IS NOT NULL      AS has_image
FROM read_parquet('build/scienceqa.src.parquet');
