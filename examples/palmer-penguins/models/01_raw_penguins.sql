-- The palmerpenguins CSV as it arrives. It writes a missing value as the string NA,
-- so every column is read as text and the next model types it.
CREATE OR REPLACE TABLE raw_penguins AS
SELECT * FROM read_csv('build/penguins.csv', all_varchar = true);

-- The package documents 344 penguins. A different count means the file is not the one
-- this recipe was written against.
SELECT CASE WHEN v.rows <> 344 THEN error(
         'raw_penguins: ' || v.rows || ' penguin(s) read from penguins.csv, not 344.') END
FROM (SELECT count(*) AS rows FROM raw_penguins) v;
