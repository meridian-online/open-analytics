-- Set up what contracts/check_titles.sql reads. Run by the `check_inputs` step, after
-- the export step has written build/stage/naics_2022_titles.parquet.
--
-- The code list is `naics`, built in this Run from Census's descriptions workbook by
-- census/naics_2022's own model, so the index is held to the codes of that list and
-- never to an earlier publish. Census codes its index entries to six-digit national
-- industries.
CREATE OR REPLACE VIEW contract_code_list AS
SELECT code, level FROM naics;

CREATE OR REPLACE VIEW contract_titles AS
SELECT * FROM read_parquet('build/stage/naics_2022_titles.parquet');

CREATE OR REPLACE TABLE contract_title_level AS SELECT 'national_industry' AS level;
