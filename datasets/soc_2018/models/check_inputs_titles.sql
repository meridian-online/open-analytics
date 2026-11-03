-- Set up what contracts/check_titles.sql reads. Run by the `check_titles` step, after the
-- export steps have written build/stage/soc_2018_titles.parquet and soc_2018.parquet.
-- BLS codes its titles to detailed occupations.
CREATE OR REPLACE VIEW contract_titles AS
SELECT * FROM read_parquet('build/stage/soc_2018_titles.parquet');

CREATE OR REPLACE VIEW contract_code_list AS
SELECT * FROM read_parquet('build/stage/soc_2018.parquet');

CREATE OR REPLACE TABLE contract_title_level AS SELECT 'detailed' AS level;
