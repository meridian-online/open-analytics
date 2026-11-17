-- Set up what contracts/check_crosswalk.sql reads. Run by the `check_inputs` step, after
-- the export step has written build/stage/naics_2017_to_2022.parquet.
--
-- The code list is `naics`, built in this Run from Census's descriptions workbook by
-- census/naics_2022's own model, so the crosswalk's 2022 codes are held to the codes of
-- that list and never to an earlier publish. Census pairs six-digit national industries;
-- the 2017 edition is not hosted, so its codes are checked by their shape alone.
CREATE OR REPLACE VIEW contract_code_list AS
SELECT code, level FROM naics;

CREATE OR REPLACE VIEW contract_crosswalk AS
SELECT * FROM read_parquet('build/stage/naics_2017_to_2022.parquet');

CREATE OR REPLACE TABLE contract_crosswalk_shape AS
SELECT '^[0-9]{6}$' AS from_pattern, 'national_industry' AS level;
