-- Epoch AI's notable models as they arrive, leaving out the Authors and Abstract
-- columns, which no table here needs.
CREATE OR REPLACE TABLE raw_models AS
SELECT * EXCLUDE ("Authors", "Abstract") FROM read_csv('build/notable_ai_models.csv');

-- The file has no pin, so these are what stands between a changed file and a wrong
-- dashboard. A column the next model reads that is gone, or renamed, already stops
-- the run there. What this adds: every model has a name and a publication date, which
-- every chart is drawn against, and the file has not shrunk to a fraction of the 1,082
-- rows it held on 2026-10-26. The floor sits below that count rather than at it, so
-- Epoch AI removing or merging a few entries does not stop a run.
SELECT CASE
  WHEN v.rows < 1000 THEN error(
    'raw_models: ' || v.rows || ' model(s) in notable_ai_models.csv, against 1,082 on '
    || '2026-10-26. Check whether Epoch AI has moved or truncated the file.')
  WHEN v.unnamed > 0 OR v.undated > 0 THEN error(
    'raw_models: ' || v.unnamed || ' model(s) with no name and ' || v.undated
    || ' with no publication date. Check whether Epoch AI has changed the file''s columns.') END
FROM (
  SELECT count(*) AS rows,
         count(*) FILTER (WHERE "Model" IS NULL) AS unnamed,
         count(*) FILTER (WHERE "Publication date" IS NULL) AS undated
  FROM raw_models
) v;
