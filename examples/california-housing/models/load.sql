-- California housing districts, into a shape a dashboard can read.
--
-- The source is StatLib's `cadata.txt`: 27 lines of prose and a regression table,
-- then 20,640 rows of nine space-padded numbers with CRLF line endings. Each line is
-- read whole and split on runs of whitespace, because the padding is not one
-- delimiter a CSV reader can be given.
--
-- The nine values are raw totals, in this order: median house value, median income,
-- housing median age, total rooms, total bedrooms, population, households, latitude,
-- longitude. The per-household averages are derived here the way scikit-learn derives
-- them, dividing by households, and house value is put in hundreds of thousands of
-- dollars, so the columns mean what they meant when this Protocol fetched a
-- scikit-learn-shaped re-upload: each of that file's 16,640 rows equals one of these
-- 20,640 exactly, compared when the source was changed.
--
-- Latitude and Longitude are kept as plain measures. They are what makes this the
-- one example in the reference gallery whose map needs no embedding behind it: two
-- numeric columns are a map already.
CREATE OR REPLACE TABLE california_housing AS
WITH lines AS (
  SELECT regexp_split_to_array(trim(line), '\s+') AS v
  FROM read_csv('build/statlib/cadata.txt', columns = {'line': 'VARCHAR'},
                header = false, delim = '\t', quote = '', escape = '', skip = 27)
  WHERE trim(line) <> ''
)
SELECT
  v[2]::DOUBLE                AS median_income,
  v[3]::DOUBLE                AS house_age,
  v[4]::DOUBLE / v[7]::DOUBLE AS avg_rooms,
  v[5]::DOUBLE / v[7]::DOUBLE AS avg_bedrooms,
  v[6]::DOUBLE                AS population,
  v[6]::DOUBLE / v[7]::DOUBLE AS avg_occupancy,
  v[8]::DOUBLE                AS latitude,
  v[9]::DOUBLE                AS longitude,
  v[1]::DOUBLE / 100000       AS median_house_value
FROM lines
WHERE len(v) = 9;

-- The split above drops a line that does not carry nine values, and the 27-line skip
-- assumes StatLib's preamble is still 27 lines. Either going wrong shows up as a row
-- count other than the 20,640 the authors published, so the run stops on that.
SELECT CASE WHEN v.rows <> 20640 THEN error(
         'california_housing: ' || v.rows || ' district(s) parsed from cadata.txt, '
         || 'not 20,640. Check whether StatLib''s preamble or row layout has changed.') END
FROM (SELECT count(*) AS rows FROM california_housing) v;
