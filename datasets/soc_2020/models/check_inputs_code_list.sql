-- Set up the code list both contract checks read, contracts/check_titles.sql and
-- check_crosswalk.sql. Run by the `check_inputs_code_list` step, after the export step has
-- written build/stage/soc_2020.parquet, so each check holds its table to the code list
-- exported in the same Run.
CREATE OR REPLACE VIEW contract_code_list AS
SELECT * FROM read_parquet('build/stage/soc_2020.parquet');
