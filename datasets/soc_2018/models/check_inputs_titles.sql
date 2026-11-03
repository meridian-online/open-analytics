-- Set up what contracts/check_titles.sql reads besides the code list, which
-- check_inputs_code_list.sql sets up. Run by the `check_inputs_titles` step, after the
-- export step has written build/stage/soc_2018_titles.parquet. BLS codes its titles to
-- detailed occupations.
CREATE OR REPLACE VIEW contract_titles AS
SELECT * FROM read_parquet('build/stage/soc_2018_titles.parquet');

CREATE OR REPLACE TABLE contract_title_level AS SELECT 'detailed' AS level;
