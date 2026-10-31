-- ScienceQA's `test` split, into a shape a dashboard can read.
--
-- The authors publish every split in one file, `problems.json`: a single JSON object
-- keyed by problem id, each value one question carrying a `split` field of `train`,
-- `val` or `test`. The column types are declared rather than inferred, so the shape
-- this SELECT reads cannot move with DuckDB's sampling; `answer` is read as TINYINT,
-- the type the recipe exported when it read a Parquet copy of these rows, so the
-- output schema is unchanged. The `split` field agrees with the authors' own
-- `pid_splits.json`: its 4,241 `test` ids are exactly the 4,241 rows kept here.
--
-- Two source quirks, each handled here rather than left for a reader to hit:
--   • `image` is the file name of the question's figure — always the generic literal
--     'image.png', for 2,017 of these 4,241 rows — or NULL for the other 2,224
--     (text-only questions). The figures themselves ship separately, and the name
--     says nothing about the picture. So the field becomes one boolean: whether a
--     question shipped with a figure.
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
WITH problems AS (
  SELECT unnest(map_values(json)) AS p
  FROM read_json(
    'build/scienceqa.src.json',
    format = 'unstructured',
    maximum_object_size = 100000000,
    columns = {json: 'MAP(VARCHAR, STRUCT(question VARCHAR, choices VARCHAR[], answer TINYINT, hint VARCHAR, image VARCHAR, task VARCHAR, grade VARCHAR, subject VARCHAR, topic VARCHAR, category VARCHAR, skill VARCHAR, lecture VARCHAR, solution VARCHAR, split VARCHAR))'}
  )
)
SELECT
  p.question                        AS question,
  array_to_string(p.choices, ' | ') AS answer_choices,
  p.answer                          AS answer_index,
  p.choices[p.answer + 1]           AS answer_text,
  p.hint                            AS hint,
  p.task                            AS task,
  p.grade                           AS grade,
  p.subject                         AS subject,
  p.topic                           AS topic,
  p.category                        AS category,
  p.skill                           AS skill,
  p.lecture                         AS lecture,
  p.solution                        AS solution,
  p.image IS NOT NULL               AS has_image
FROM problems
WHERE p.split = 'test';
