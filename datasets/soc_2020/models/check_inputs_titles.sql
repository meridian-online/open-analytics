-- Set up what contracts/check_titles.sql reads besides the code list, which
-- check_inputs_code_list.sql sets up. Run by the `check_inputs_titles` step, after the
-- export step has written build/stage/soc_2020_titles.parquet. ONS codes its titles to
-- unit groups.
CREATE OR REPLACE VIEW contract_titles AS
SELECT * FROM read_parquet('build/stage/soc_2020_titles.parquet');

CREATE OR REPLACE TABLE contract_title_level AS SELECT 'unit' AS level;
