-- Set up what contracts/check_crosswalk.sql reads besides the code list, which
-- check_inputs_code_list.sql sets up. Run by the `check_inputs_crosswalk` step, after the
-- export step has written build/stage/soc_2010_to_2020.parquet. ONS pairs at the unit-group
-- level, from 2010 codes of four digits.
CREATE OR REPLACE VIEW contract_crosswalk AS
SELECT * FROM read_parquet('build/stage/soc_2010_to_2020.parquet');

CREATE OR REPLACE TABLE contract_crosswalk_shape AS
SELECT '^[0-9]{4}$' AS from_pattern, 'unit' AS level;
