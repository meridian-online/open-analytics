-- Set up what contracts/check_crosswalk.sql reads besides the code list, which
-- check_inputs_code_list.sql sets up. Run by the `check_inputs_crosswalk` step, after the
-- export step has written build/stage/soc_2010_to_2018.parquet. BLS pairs at the detailed
-- level, from 2010 codes of the form NN-NNNN.
CREATE OR REPLACE VIEW contract_crosswalk AS
SELECT * FROM read_parquet('build/stage/soc_2010_to_2018.parquet');

CREATE OR REPLACE TABLE contract_crosswalk_shape AS
SELECT '^[0-9]{2}-[0-9]{4}$' AS from_pattern, 'detailed' AS level;
